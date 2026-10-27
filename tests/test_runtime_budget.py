"""Offline tests for the portable shared-ledger runtime transport."""
import copy
import json
import os
import subprocess
import sys
import time

import pytest
from fastapi import HTTPException

import app.runtime_budget as rb
from app.main import get_investigation_client
from app.meta_client import MODEL, MetaClient
from app.runtime_budget import RuntimeBudgetError, RuntimeBudgetTransport, RuntimeLockBusy

NOW = 1_700_000_000.0
KEY = "DUMMY_RUNTIME_KEY"
MARKER = "SYNTHETIC_PROMPT_MARKER"


def payload(**overrides):
    data = {
        "model": MODEL,
        "input": [{"role": "user", "content": [{"type": "input_text", "text": MARKER}]}],
        "store": False,
        "reasoning": {"effort": "low"},
        "max_output_tokens": 64,
    }
    data.update(overrides)
    return data


def write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")


def budget(charged=10.0):
    return {
        "version": 1, "basis": "owner reported project ledger", "total_cap_usd": 100,
        "owner_note": "keep-me",
        "requests": [{
            "id": "coding-1", "run_id": "coding-run", "reserved_usd": charged,
            "charged_usd": charged, "status": "settled", "model": "muse-wonder",
            "rate_in": 9e-7, "rate_out": 3e-6,
            "usage": {"input_tokens": 100, "output_tokens": 10, "total_tokens": 110, "legacy": {"kept": True}},
        }],
        "runs": [{"id": "coding-run", "cap_usd": 25, "started_at": int(NOW) - 10, "status": "completed", "extra": "kept"}],
    }


def credit(total=10.0, available=150.0, verified_at=NOW):
    return {
        "source": "owner_report", "available_usd": available,
        "verified_at": verified_at, "tracked_at_verification_usd": total,
        "note": "synthetic test only",
    }


def make_runtime(tmp_path, budget_value=None, credit_value=None):
    directory = tmp_path / "runtime"
    directory.mkdir()
    (directory / "runner.lock").write_bytes(b"\x00")
    b = budget() if budget_value is None else budget_value
    total = sum(r["charged_usd"] for r in b.get("requests", []) if isinstance(r, dict) and isinstance(r.get("charged_usd"), (int, float)) and not isinstance(r.get("charged_usd"), bool))
    write_json(directory / "budget-state.json", b)
    write_json(directory / "credit-state.json", credit(total) if credit_value is None else credit_value)
    return directory


def read_budget(directory):
    return json.loads((directory / "budget-state.json").read_text(encoding="utf-8"))


class Response:
    def __init__(self, body=None, status=200):
        self.status = status
        self._body = json.dumps(body if body is not None else {
            "id": "resp-test", "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120, "nested": {"untrusted": "drop"}, "cached_tokens": "not-an-int"}
        }).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self, limit=-1):
        return self._body if limit < 0 else self._body[:limit]


class Opener:
    def __init__(self, directory=None, response=None, error=None, responses=None):
        self.directory = directory
        self.response = response or Response()
        self.responses = list(responses) if responses is not None else None
        self.error = error
        self.requests = []
        self.during = None

    def __call__(self, request, timeout=None):
        self.requests.append(request)
        if self.directory is not None:
            self.during = read_budget(self.directory)
        if self.error:
            raise self.error
        if self.responses is not None:
            return self.responses.pop(0)
        return self.response


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    return make_runtime(tmp_path)


def transport(directory, opener=None, clock=None):
    return RuntimeBudgetTransport(str(directory), opener=opener or Opener(directory), clock=clock or (lambda: NOW))


def test_preexisting_charge_preserved_counted_and_clean_exit(env):
    opener = Opener(env)
    with transport(env, opener) as active:
        result = active(payload())
    assert result["usage"]["input_tokens"] == 100
    state = read_budget(env)
    assert state["owner_note"] == "keep-me"
    old = state["requests"][0]
    assert old["charged_usd"] == 10.0 and old["usage"]["legacy"] == {"kept": True} and old["model"] == "muse-wonder"
    new = state["requests"][-1]
    assert set(new) == {"id", "run_id", "reserved_usd", "charged_usd", "status", "model", "rate_in", "rate_out", "usage"}
    assert new["status"] == "settled" and new["model"] == MODEL
    assert new["charged_usd"] == pytest.approx(100 * 1.25e-6 + 20 * 4.25e-6)
    assert new["usage"] == {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}
    run = state["runs"][-1]
    assert run["id"].startswith("runtime-") and run["cap_usd"] == 0.25
    assert run["status"] == "completed" and run["calls"] == 1 and "finished_at" in run


def test_happy_three_call_loop_under_own_running_run_record(env):
    # Regression: __enter__ appends this run with status 'running'; calls in
    # the same context must not reject themselves because of that label.
    opener = Opener(env)
    with transport(env, opener) as active:
        during = read_budget(env)
        assert during["runs"][-1]["status"] == "running"
        for _ in range(3):
            result = active(payload())
            assert result["id"] == "resp-test"
    assert len(opener.requests) == 3
    state = read_budget(env)
    settled = [r for r in state["requests"] if r["run_id"] == state["runs"][-1]["id"]]
    assert len(settled) == 3 and all(r["status"] == "settled" for r in settled)
    assert state["runs"][-1]["calls"] == 3 and state["runs"][-1]["status"] == "completed"


def test_mixed_format_history_preserved_and_summed_verbatim(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", KEY)
    legacy = []
    for i in range(6):
        # Exactly the legacy shared-ledger shape: no model/rate fields.
        legacy.append({
            "id": "legacy-%d" % i, "run_id": "legacy-run",
            "reserved_usd": 0.01, "charged_usd": 0.01, "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        })
    newer = [
        {"id": "new-1", "run_id": "run-a", "reserved_usd": 1.0, "charged_usd": 0.5,
         "status": "settled", "model": "model-a", "rate_in": 1e-6, "rate_out": 2e-6,
         "usage": {"input_tokens": 5, "output_tokens": 5}, "unknown_meta": {"keep": [1, 2]}},
        {"id": "new-2", "run_id": "run-b", "reserved_usd": 2.0, "charged_usd": 1.5,
         "status": "completed", "model": "model-b", "rate_in": 9e-6, "rate_out": 8e-6,
         "usage": {"input_tokens": 7, "output_tokens": 3}},
    ]
    total = 6 * 0.01 + 0.5 + 1.5
    b = {
        "version": 1, "basis": "mixed historical ledger", "total_cap_usd": 100,
        "requests": legacy + newer,
        "runs": [
            {"id": "legacy-run", "cap_usd": 5, "started_at": int(NOW) - 100, "status": "starting"},
            {"id": "run-a", "cap_usd": 5, "started_at": int(NOW) - 90, "status": "needs_review", "calls": 1},
            {"id": "run-b", "cap_usd": 5, "started_at": int(NOW) - 80, "status": "staged_for_review"},
            {"id": "run-c", "cap_usd": 5, "started_at": int(NOW) - 70, "status": "live_evaluation_finished", "finished_at": int(NOW) - 60},
            {"id": "run-d", "cap_usd": 5, "started_at": int(NOW) - 50, "status": "generation_failed"},
        ],
    }
    directory = make_runtime(tmp_path, b, credit(total=total, available=150.0))
    opener = Opener(directory)
    with transport(directory, opener) as active:
        assert active(payload())["id"] == "resp-test"
    state = read_budget(directory)
    # Legacy records are preserved verbatim: no guessed model/rates added.
    for i in range(6):
        assert state["requests"][i] == legacy[i]
    assert state["requests"][6]["unknown_meta"] == {"keep": [1, 2]}
    assert state["requests"][6]["model"] == "model-a" and state["requests"][7]["model"] == "model-b"
    assert state["requests"][-1]["model"] == MODEL


def test_reservation_visible_before_network_and_no_secrets_or_payload_in_ledger(env):
    opener = Opener(env)
    with transport(env, opener) as active:
        active(payload())
    during = opener.during
    reserved = during["requests"][-1]
    assert reserved["status"] == "reserved" and reserved["charged_usd"] == reserved["reserved_usd"] > 0
    text = (env / "budget-state.json").read_text(encoding="utf-8")
    assert KEY not in text and MARKER not in text
    request = opener.requests[0]
    assert request.full_url == "https://api.meta.ai/v1/responses"
    assert request.headers["Authorization"] == "Bearer " + KEY


def test_same_process_lock_busy_and_cross_process_lock_busy(env, tmp_path):
    with transport(env):
        with pytest.raises(RuntimeLockBusy):
            transport(env).__enter__()
    ready = tmp_path / "ready"
    code = (
        "import os,sys,time; p=sys.argv[1]; f=open(p,'r+b'); f.seek(0); "
        "( __import__('msvcrt').locking(f.fileno(), __import__('msvcrt').LK_NBLCK, 1) if os.name=='nt' else __import__('fcntl').lockf(f.fileno(), __import__('fcntl').LOCK_EX|__import__('fcntl').LOCK_NB) ); "
        "open(sys.argv[2],'w').write('x'); time.sleep(10)"
    )
    child = subprocess.Popen([sys.executable, "-c", code, str(env / "runner.lock"), str(ready)])
    try:
        for _ in range(100):
            if ready.exists():
                break
            if child.poll() is not None:
                raise AssertionError("lock child exited early")
            time.sleep(0.05)
        assert ready.exists()
        with pytest.raises(RuntimeLockBusy):
            transport(env).__enter__()
    finally:
        child.terminate()
        child.wait(timeout=5)


@pytest.mark.parametrize("age", [-1, 86401])
def test_stale_or_future_credit_rejected(env, age):
    write_json(env / "credit-state.json", credit(verified_at=NOW - age))
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()


def test_genuine_reserve_violation_blocks_before_network(env):
    # tracked (10) equals the current charged total (10), so available-now is
    # exactly 50; any positive bound must breach the $50 reserve.
    write_json(env / "credit-state.json", credit(total=10.0, available=50.0))
    opener = Opener(env)
    with transport(env, opener) as active:
        with pytest.raises(RuntimeBudgetError):
            active(payload())
    assert opener.requests == []
    assert len(read_budget(env)["requests"]) == 1


@pytest.mark.parametrize("mutate", [
    lambda b, c: b["requests"][0].update(charged_usd=100.0) or c.update(tracked_at_verification_usd=100.0, available_usd=200.0),
    lambda b, c: c.update(tracked_at_verification_usd=11.0),  # tracked above current total is suspect
])
def test_total_cap_and_tracked_gates(env, mutate):
    b, c = budget(), credit()
    mutate(b, c)
    write_json(env / "budget-state.json", b)
    write_json(env / "credit-state.json", c)
    opener = Opener(env)
    with pytest.raises(RuntimeBudgetError):
        with transport(env, opener) as active:
            active(payload())
    assert opener.requests == []


def test_credit_and_time_revalidated_before_each_call(env):
    now = [NOW]
    opener = Opener(env)
    with transport(env, opener, clock=lambda: now[0]) as active:
        first = active(payload())
        assert first["id"] == "resp-test"
        # Time crosses the 24h freshness window and the credit source state
        # changes to a lower available amount before the second call.
        now[0] = NOW + 86401
        write_json(env / "credit-state.json", credit(total=10.0, available=40.0, verified_at=NOW))
        with pytest.raises(RuntimeBudgetError):
            active(payload())
    assert len(opener.requests) == 1
    state = read_budget(env)
    assert len(state["requests"]) == 2  # pre-existing + first settled only
    settled = state["requests"][-1]
    assert settled["status"] == "settled"
    assert settled["charged_usd"] == pytest.approx(100 * 1.25e-6 + 20 * 4.25e-6)


def test_run_cap_rejected_without_network(env):
    opener = Opener(env)
    big = payload(input="x" * 220000)
    serialized_bytes = len(json.dumps(big).encode("utf-8"))
    # Independently bound the cost from the serialized size at the model's
    # input rate plus the full requested output allowance. This payload is
    # large enough for that bound to exceed the $0.25 run cap, while staying
    # under the serialized-size ceiling so the run cap is what rejects it.
    estimated_bound_usd = serialized_bytes * 1.25e-6 + big["max_output_tokens"] * 4.25e-6
    assert serialized_bytes <= rb.MAX_SERIALIZED_BYTES
    assert estimated_bound_usd > 0.25
    with transport(env, opener) as active:
        with pytest.raises(RuntimeBudgetError):
            active(big)
    assert opener.requests == []


@pytest.mark.parametrize("kind", ["bool", "nan", "negative", "malformed", "missing", "version", "truncated", "oversized", "credit_bool", "unknown_status"])
def test_invalid_state_rejected_and_entry_failure_unlocks(env, kind):
    bpath, cpath = env / "budget-state.json", env / "credit-state.json"
    good_budget, good_credit = budget(), credit()
    if kind == "bool":
        b = copy.deepcopy(good_budget); b["requests"][0]["charged_usd"] = True; write_json(bpath, b)
    elif kind == "nan":
        bpath.write_text(json.dumps(good_budget).replace("10.0", "NaN", 1), encoding="utf-8")
    elif kind == "negative":
        b = copy.deepcopy(good_budget); b["total_cap_usd"] = -1; write_json(bpath, b)
    elif kind == "malformed":
        bpath.write_text("{not-json", encoding="utf-8")
    elif kind == "missing":
        bpath.unlink()
    elif kind == "version":
        b = copy.deepcopy(good_budget); b["version"] = 2; write_json(bpath, b)
    elif kind == "truncated":
        bpath.write_text(json.dumps(good_budget)[:20], encoding="utf-8")
    elif kind == "oversized":
        bpath.write_bytes(b" " * (rb.MAX_STATE_BYTES + 1))
    elif kind == "credit_bool":
        c = copy.deepcopy(good_credit); c["available_usd"] = False; write_json(cpath, c)
    else:
        b = copy.deepcopy(good_budget); b["requests"][0]["status"] = "mystery"; write_json(bpath, b)
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()
    write_json(bpath, good_budget); write_json(cpath, good_credit)
    with transport(env):  # released process/file locks permit a corrected entry
        pass


@pytest.mark.parametrize("mutate", [
    lambda b: b["runs"][0].update(cap_usd=-1),
    lambda b: b["runs"][0].update(cap_usd=True),
    lambda b: b["runs"][0].update(started_at=float("nan")),
    lambda b: b["runs"][0].update(finished_at=-5),
    lambda b: b["runs"][0].update(calls=True),
    lambda b: b["runs"][0].update(calls=1.5),
    lambda b: b["requests"][0].update(usage={"input_tokens": True, "output_tokens": 1}),
    lambda b: b["requests"][0].update(usage={"input_tokens": 1.5, "output_tokens": 1}),
    lambda b: b["requests"][0].update(reserved_usd=float("inf")),
    lambda b: b["requests"][0].update(rate_in=-1),
])
def test_strict_numeric_validation_of_runs_requests_and_usage(env, mutate):
    b = budget(); mutate(b)
    raw = json.dumps(b)  # NaN/Infinity serialize as bare tokens Python json accepts
    (env / "budget-state.json").write_text(raw, encoding="utf-8")
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()


def test_large_int_fields_do_not_raise_overflow(env):
    b = budget()
    b["runs"][0]["started_at"] = 10 ** 400  # valid huge int: no float() overflow
    write_json(env / "budget-state.json", b)
    with transport(env):
        pass
    # A huge verified_at is a clean sanitized rejection, not OverflowError.
    write_json(env / "budget-state.json", budget())
    write_json(env / "credit-state.json", credit(verified_at=10 ** 400))
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()


@pytest.mark.parametrize("response,error", [
    (Response({"usage": {"input_tokens": "100", "output_tokens": 1}}), None),
    (Response({"usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 1}}), None),
    (Response({}, status=503), None),
    (Response({}, status=302), None),
    (None, OSError("synthetic")),
])
def test_uncertain_outcome_retains_full_bound_and_blocks(env, response, error):
    opener = Opener(env, response=response, error=error)
    with transport(env, opener) as active:
        with pytest.raises(RuntimeBudgetError):
            active(payload())
        with pytest.raises(RuntimeBudgetError):
            active(payload())
    assert len(opener.requests) == 1
    record = read_budget(env)["requests"][-1]
    assert record["status"] == "unknown" and record["charged_usd"] == record["reserved_usd"]
    assert "usage" not in record


def test_extremely_large_usage_treated_as_unknown(env):
    huge = Response({"usage": {"input_tokens": 10 ** 400, "output_tokens": 1}})
    opener = Opener(env, response=huge)
    with transport(env, opener) as active:
        with pytest.raises(RuntimeBudgetError):
            active(payload())
    record = read_budget(env)["requests"][-1]
    assert record["status"] == "unknown" and record["charged_usd"] == record["reserved_usd"]


def test_usage_exceeding_bound_is_durable_needs_review_stop(env):
    # Output 20 against a requested cap of 16 breaches the bound.
    opener = Opener(env, response=Response({"usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}}))
    with transport(env, opener) as active:
        with pytest.raises(RuntimeBudgetError):
            active(payload(max_output_tokens=16))
    record = read_budget(env)["requests"][-1]
    assert record["status"] == "needs_review"
    assert record["charged_usd"] == pytest.approx(100 * 1.25e-6 + 20 * 4.25e-6)
    # A later context cannot resume: the nonterminal request status blocks.
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()


def test_interrupted_reserved_state_blocks_restart(env):
    b = budget(); b["requests"].append({
        "id": "stuck", "run_id": "old", "reserved_usd": 0.01, "charged_usd": 0.01,
        "status": "reserved", "model": MODEL, "rate_in": 1.25e-6, "rate_out": 4.25e-6,
    })
    write_json(env / "budget-state.json", b)
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()


def test_reservation_save_failure_prevents_network(env):
    opener = Opener(env)
    with transport(env, opener) as active:
        original = active._save_budget
        def boom():
            raise OSError("synthetic disk failure")
        active._save_budget = boom
        with pytest.raises(RuntimeBudgetError):
            active(payload())
        assert opener.requests == []
        # The failed reservation was never persisted: only the pre-existing
        # settled request remains in the on-disk ledger.
        assert len(read_budget(env)["requests"]) == 1
        # Restore the real saver before leaving the context so context exit
        # does not (correctly) raise its own sanitized save failure after
        # pytest.raises has swallowed the inner one.
        active._save_budget = original
    assert opener.requests == []
    state = read_budget(env)
    assert len(state["requests"]) == 2
    assert state["requests"][-1]["status"] == "reserved"
    assert state["requests"][-1]["charged_usd"] == state["requests"][-1]["reserved_usd"]
    with pytest.raises(RuntimeBudgetError):
        transport(env).__enter__()


def test_settlement_save_failure_retains_reservation_and_stops(env):
    opener = Opener(env)
    with transport(env, opener) as active:
        original = active._save_budget
        calls = {"n": 0}
        def flaky():
            calls["n"] += 1
            if calls["n"] == 2:  # 1 = reservation (ok), 2 = settlement (fails)
                raise OSError("synthetic disk failure")
            return original()
        active._save_budget = flaky
        with pytest.raises(RuntimeBudgetError):
            active(payload())
        with pytest.raises(RuntimeBudgetError):
            active(payload())
    assert len(opener.requests) == 1
    record = read_budget(env)["requests"][-1]
    assert record["status"] == "reserved" and record["charged_usd"] == record["reserved_usd"]


def test_exit_failure_never_masks_original_and_lock_reacquirable(env):
    state = {"fail_clock": False}
    def clock():
        if state["fail_clock"]:
            raise RuntimeError("synthetic clock failure at /secret/path with KEY")
        return NOW
    active = transport(env, Opener(env), clock=clock)
    with pytest.raises(ValueError, match="original route failure"):
        with active:
            state["fail_clock"] = True
            raise ValueError("original route failure")
    state["fail_clock"] = False
    # Locks were released deterministically; the leftover 'running' run label
    # (exit save never happened) is historical metadata, not unresolved spend.
    with transport(env, Opener(env), clock=clock):
        pass


def test_exit_save_failure_without_original_is_sanitized(env):
    active = transport(env, Opener(env))
    with pytest.raises(RuntimeBudgetError) as caught:
        with active:
            def boom():
                raise OSError("synthetic failure at /secret/path")
            active._save_budget = boom
    assert "/secret/path" not in str(caught.value) and KEY not in str(caught.value)
    with transport(env):  # lock still reacquirable after failed exit
        pass


@pytest.mark.parametrize("mutate", [
    lambda p: p.update(model="other-model"),
    lambda p: p.update(store=True),
    lambda p: p.update(extra="x"),
    lambda p: p.update(max_output_tokens=True),
    lambda p: p.update(input=[{"type": "input_image", "image_url": "x"}]),
    lambda p: p.update(previous_response_id="x"),
    lambda p: p.update(tools=[{"type": "function", "name": "apply_repair", "parameters": {"type": "object", "properties": {}}}]),
    lambda p: p.update(tools=[{"type": "web_search", "name": "get_patch"}]),
    # Prohibited keys hidden outside `input`: in tools, text and tool_choice.
    lambda p: p.update(tools=[{"type": "function", "name": "get_patch", "parameters": {"type": "object", "properties": {}}, "previous_response_id": "x"}]),
    lambda p: p.update(tools=[{"type": "function", "name": "get_patch", "parameters": {"type": "object", "properties": {"file_id": "x"}}}]),
    lambda p: p.update(text={"format": {"type": "json_schema", "strict": True, "schema": {}, "file": "x"}}),
    lambda p: p.update(tool_choice={"type": "function", "name": "get_patch", "previous_response_id": "x"}, tools=[{"type": "function", "name": "get_patch", "parameters": {"type": "object", "properties": {}}}], parallel_tool_calls=False),
    # Arbitrary text options are not the strict MetaClient structure.
    lambda p: p.update(text={"format": {"type": "text"}}),
    lambda p: p.update(text={"format": {"type": "json_schema", "strict": False, "schema": {}}}),
    lambda p: p.update(text={"format": {"type": "json_schema", "schema": {}}, "verbosity": "high"}),
    lambda p: p.update(text="plain"),
])
def test_invalid_payload_rejected_without_upstream(env, mutate):
    opener = Opener(env)
    bad = payload(); mutate(bad)
    with transport(env, opener) as active:
        with pytest.raises(RuntimeBudgetError):
            active(bad)
    assert opener.requests == []
    assert len(read_budget(env)["requests"]) == 1


def test_strict_text_structure_accepted(env):
    opener = Opener(env)
    good = payload(text={"format": {"type": "json_schema", "name": "proposal", "strict": True, "schema": {"type": "object"}}})
    with transport(env, opener) as active:
        assert active(good)["id"] == "resp-test"
    assert len(opener.requests) == 1


def test_redirect_handler_and_fixed_url_cannot_carry_token_elsewhere():
    handler = rb._NoRedirect()
    assert handler.redirect_request(None, None, 302, "", {}, "https://example.invalid/") is None


def test_default_opener_is_no_redirect_through_mocked_urllib(env, monkeypatch):
    seen = {}
    class FakeDirector:
        def open(self, request, timeout=None):
            seen["url"] = request.full_url
            seen["timeout"] = timeout
            return Response()
    def fake_build_opener(*handlers):
        seen["handlers"] = handlers
        return FakeDirector()
    monkeypatch.setattr(rb.urllib.request, "build_opener", fake_build_opener)
    active = RuntimeBudgetTransport(str(env), clock=lambda: NOW)  # default opener
    with active:
        assert active(payload())["id"] == "resp-test"
    assert seen["url"] == "https://api.meta.ai/v1/responses"
    assert any(isinstance(h, rb._NoRedirect) for h in seen["handlers"])


def test_meta_client_integration_with_valid_response_within_cap(env):
    opener = Opener(env)  # default fake returns output 20
    with transport(env, opener) as active:
        client = MetaClient(transport=active)
        for _ in range(3):
            client.create_response("hello", max_output_tokens=32)  # 20 <= 32: valid
        summary = client.usage_summary()
        assert summary["requests"] == 3
        assert summary["estimated_cost_usd"] == pytest.approx(3 * (100 * 1.25e-6 + 20 * 4.25e-6))
    assert len(opener.requests) == 3


def test_transport_six_call_ceiling_directly(env):
    opener = Opener(env)
    with transport(env, opener) as active:
        for _ in range(6):
            assert active(payload(max_output_tokens=32))["id"] == "resp-test"
        with pytest.raises(RuntimeBudgetError):
            active(payload(max_output_tokens=32))
    assert len(opener.requests) == 6
    assert read_budget(env)["runs"][-1]["calls"] == 6


def test_main_dependency_yields_client_and_finishes_run(env, monkeypatch):
    # This dependency uses the real clock, unlike the fake-clock transports
    # above, so refresh only this temporary fixture's credit verification
    # (same ledger total) to be fresh relative to that clock.
    write_json(env / "credit-state.json", credit(total=10.0, verified_at=time.time()))
    monkeypatch.setenv("AFTERFIX_RUNTIME_DIR", str(env))
    monkeypatch.delenv("AFTERFIX_META_BASE_URL", raising=False)
    dependency = get_investigation_client()
    client = next(dependency)
    assert isinstance(client, MetaClient)
    with pytest.raises(StopIteration):
        next(dependency)
    assert read_budget(env)["runs"][-1]["status"] == "completed"


@pytest.mark.parametrize("runtime,gateway", [("relative/path", None), (str(env), "http://127.0.0.1:9/v1"), ("", None)])
def test_main_dependency_ambiguous_or_relative_fails_closed(env, monkeypatch, runtime, gateway):
    monkeypatch.setenv("AFTERFIX_RUNTIME_DIR", runtime)
    if gateway is None:
        monkeypatch.delenv("AFTERFIX_META_BASE_URL", raising=False)
    else:
        monkeypatch.setenv("AFTERFIX_META_BASE_URL", gateway)
    with pytest.raises(HTTPException) as caught:
        next(get_investigation_client())
    assert caught.value.status_code == 503
    assert KEY not in str(caught.value.detail)
