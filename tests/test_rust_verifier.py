import io
import json
import os
import sqlite3
import threading
import time

import pytest

from app import domain, recovery, rust_verifier, store


@pytest.fixture()
def conn(tmp_path):
    c = store.connect(str(tmp_path / "t.db"))
    store.reset_and_seed(c)
    yield c
    c.close()


def widget_events(conn):
    return store.list_product_events(conn, "WIDGET-1")


def make_proposal(conn):
    ids = [r["id"] for r in widget_events(conn) if r["kind"] == "order_deduction"]
    p = recovery.build_proposal(conn, "INC-001", "WIDGET-1", ids)
    assert p["status"] == "proposed", p
    return p


def counts(conn):
    return (conn.execute("SELECT COUNT(*) c FROM approvals").fetchone()["c"], conn.execute("SELECT COUNT(*) c FROM audit_log").fetchone()["c"], conn.execute("SELECT COUNT(*) c FROM events WHERE kind='repair_credit'").fetchone()["c"], conn.execute("SELECT COUNT(*) c FROM idempotency_keys").fetchone()["c"], store.get_product(conn, "WIDGET-1")["stock"])


def test_real_canonical_preview_approve_apply(conn):
    assert rust_verifier.executable_path().is_file(), "build release verifier first"
    p = make_proposal(conn)
    prev = recovery.preview_proposal(conn, p["proposal_id"])
    rust = prev["rust_verification"]
    assert rust["status"] == "verified" and rust["verifier_version"] == "0.1.0"
    assert rust["snapshot_hash"] == p["snapshot_hash"]
    assert rust["outstanding_credit"] == 10 and rust["expected_stock"] == 110
    assert prev["simulation_pass"] is True
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    assert appr["rust_verification"]["status"] == "verified"
    out = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-real-1", actor_id="operator")
    assert out["status"] == "applied" and out["after_stock"] == 110
    assert out["rust_verification"]["proposal_matches"] is True
    assert recovery.verify_product(conn, "WIDGET-1", p["proposal_id"])["pass"] is True
    post = recovery.preview_proposal(conn, p["proposal_id"])
    assert post["status"] == "applied" and post["diff"]["events_added"] == 0
    assert post["rust_verification"]["proposal_matches"] is None


def test_real_repeated_calls_and_env_filter(conn, monkeypatch):
    assert rust_verifier.executable_path().is_file(), "build release verifier first"
    monkeypatch.setenv("MODEL_API_KEY", "synthetic-secret")
    monkeypatch.setenv("UNRELATED_SECRET", "synthetic-secret")
    p = make_proposal(conn)
    cand = {"credit_qty": p["credit_qty"], "reverse_event_ids": p["reverse_event_ids"]}
    for _ in range(3):
        out = rust_verifier.verify_current(conn, "WIDGET-1", cand)
        assert out["status"] == "verified" and out["proposal_matches"] is True
    env = rust_verifier._minimal_env()
    assert "MODEL_API_KEY" not in env and "UNRELATED_SECRET" not in env
    assert set(env) <= {"SYSTEMROOT", "WINDIR"}


def test_real_varied_flow(conn):
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-2000", 7)
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-2000", 7)
    domain.process_restock(conn, "WIDGET-1", 3)
    ids = [r["id"] for r in widget_events(conn) if r["kind"] == "order_deduction"]
    p = recovery.build_proposal(conn, "INC-001", "WIDGET-1", ids)
    assert p["status"] == "proposed" and p["credit_qty"] == 17
    prev = recovery.preview_proposal(conn, p["proposal_id"])
    assert prev["rust_verification"]["expected_stock"] == prev["after_stock"]
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    out = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-varied-1", actor_id="operator")
    assert out["after_stock"] == prev["after_stock"]
    assert store.get_product(conn, "GADGET-9")["stock"] == 45


def test_real_analysis_only_and_invalid_candidate_rejected_before_child(conn):
    p = make_proposal(conn)
    ok = rust_verifier.verify_current(conn, "WIDGET-1", None)
    assert ok["proposal_matches"] is None and ok["outstanding_credit"] == 10
    assert all(c["name"] != "proposal_matches" for c in ok["checks"])
    calls = []
    def forbidden(*a, **k):
        calls.append(1)
        raise AssertionError("child launched")
    for cand in ({"credit_qty": 9, "reverse_event_ids": [4]}, {"credit_qty": 10, "reverse_event_ids": [999]}, {"credit_qty": 10.0, "reverse_event_ids": p["reverse_event_ids"]}, {"credit_qty": True, "reverse_event_ids": p["reverse_event_ids"]}):
        with pytest.raises(rust_verifier.RustVerificationError):
            rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=forbidden)
    assert calls == []


def test_forged_ok_true_false_candidate_never_verified(conn):
    # Otherwise-valid seeded ledger/candidate; the forged body exactly matches
    # the Python ledger oracle except it claims the valid candidate is false.
    p = make_proposal(conn)
    cand = {"credit_qty": p["credit_qty"], "reverse_event_ids": list(p["reverse_event_ids"])}
    req, expected = rust_verifier.build_request(conn, "WIDGET-1", cand)
    assert expected == {"ledger_actual": 100, "outstanding_credit": 10, "expected_stock": 110, "reverse_event_ids": cand["reverse_event_ids"], "proposal_matches": True}
    forged = {"protocol_version": 1, "verifier_version": "0.1.0", "ok": True, "errors": [], "ledger_actual": 100, "expected_stock": 110, "outstanding_credit": 10, "reverse_event_ids": cand["reverse_event_ids"], "proposal_matches": False}
    payload = (json.dumps(forged) + "\n").encode()
    factory, _holder = canned_factory(payload)
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert ei.value.code == "candidate_mismatch" and ei.value.status == "failed"
    # The reproduced shape from review: credit 9 / first duplicate id, with a
    # child claiming expected values while marking the candidate false.
    bad_cand = {"credit_qty": 9, "reverse_event_ids": [cand["reverse_event_ids"][0]]}
    forged2 = dict(forged, reverse_event_ids=bad_cand["reverse_event_ids"])
    factory2, _holder2 = canned_factory((json.dumps(forged2) + "\n").encode())
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", bad_cand, _process_factory=factory2)


class FakeProc:
    def __init__(self, stdout=b"", stderr=b"", rc=0, hang=False, stdin=None, poll_error=False):
        self.stdin = stdin if stdin is not None else io.BytesIO()
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self._rc, self._hang, self._poll_error = rc, hang, poll_error
        self.returncode = None
        self.killed = False
        self.waited = False
    def poll(self):
        if self._poll_error:
            raise OSError("poll failed")
        if self._hang and self.returncode is None:
            return None
        return self._rc if self.returncode is None else self.returncode
    def kill(self):
        self.killed = True
        self.returncode = -9
        self._hang = False
    def wait(self, timeout=None):
        self.waited = True
        return self.poll() if not self._poll_error else -9


def good_response(request, extra=None, **over):
    merged = {}
    if extra:
        merged.update(extra)
    merged.update(over)
    obj = {"protocol_version": 1, "verifier_version": "0.1.0", "ok": True, "errors": [], "ledger_actual": 100, "expected_stock": 110, "outstanding_credit": 10, "reverse_event_ids": [3], "proposal_matches": True if request.get("proposal") else None}
    obj.update(merged)
    return (json.dumps(obj) + "\n").encode()


def factory_returning(make):
    def factory(argv, **kw):
        assert kw.get("shell") is False
        assert argv == [str(rust_verifier.executable_path())]
        assert "MODEL_API_KEY" not in (kw.get("env") or {})
        return make(argv, kw)
    return factory


def canned_factory(payload, rc=0, stderr=b"", hang=False, **proc_kw):
    holder = {}
    def make(argv, kw):
        holder["proc"] = FakeProc(payload, stderr, rc, hang, **proc_kw)
        return holder["proc"]
    return factory_returning(make), holder


def seeded_candidate(conn):
    p = make_proposal(conn)
    return p, {"credit_qty": p["credit_qty"], "reverse_event_ids": p["reverse_event_ids"]}


def test_fake_child_valid_shape_but_false_claims_fail(conn):
    p, cand = seeded_candidate(conn)
    real_ref = cand["reverse_event_ids"]
    factory, _ = canned_factory(good_response({"proposal": cand}, {"reverse_event_ids": real_ref}))
    out = rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert out["status"] == "verified"
    for over in ({"ledger_actual": 99}, {"outstanding_credit": 11}, {"expected_stock": 111}, {"reverse_event_ids": [999]}, {"proposal_matches": False}, {"proposal_matches": None}):
        merged = {"reverse_event_ids": real_ref}
        merged.update(over)
        factory, _ = canned_factory(good_response({"proposal": cand}, merged))
        with pytest.raises(rust_verifier.RustVerificationError) as ei:
            rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
        assert "999" not in str(ei.value) and "/" not in str(ei.value)


BASE_OK = {"protocol_version": 1, "verifier_version": "0.1.0", "ok": True, "errors": [], "ledger_actual": 100, "expected_stock": 110, "outstanding_credit": 10, "reverse_event_ids": [4], "proposal_matches": True}


def _enc(obj):
    return (json.dumps(obj) + "\n").encode()


@pytest.mark.parametrize("payload", [
    b"not json\n", b'{"protocol_version":1,"protocol_version":1}\n', b'{"protocol_version":1} trailing\n',
    b'{"protocol_version":NaN}\n', b'[1,2,3]\n', b"\xff\xfe bad\n",
    _enc(dict(BASE_OK, verifier_version="9.9.9")), _enc(dict(BASE_OK, extra=1)),
    _enc(dict(BASE_OK, ok="true")), _enc(dict(BASE_OK, ledger_actual="100")),
    _enc(dict(BASE_OK, ledger_actual=100.0)), _enc(dict(BASE_OK, ledger_actual=True)),
    _enc(dict(BASE_OK, outstanding_credit=10.5)), _enc(dict(BASE_OK, reverse_event_ids=[4.0])),
    _enc(dict(BASE_OK, reverse_event_ids=[True])), _enc(dict(BASE_OK, reverse_event_ids="4")),
    _enc(dict(BASE_OK, protocol_version=1.0)), _enc(dict(BASE_OK, protocol_version=True)),
    _enc(dict(BASE_OK, errors=[] , proposal_matches=None)), _enc(dict(BASE_OK, proposal_matches=False)),
    _enc({"protocol_version": 1, "verifier_version": "0.1.0", "ok": False, "errors": [], "ledger_actual": None, "expected_stock": None, "outstanding_credit": None, "reverse_event_ids": [], "proposal_matches": None}),
    _enc({"protocol_version": 1, "verifier_version": "0.1.0", "ok": False, "errors": ["ledger_mismatch"], "ledger_actual": 100, "expected_stock": None, "outstanding_credit": None, "reverse_event_ids": [], "proposal_matches": False}),
    _enc({"protocol_version": 1, "verifier_version": "0.1.0", "ok": False, "errors": ["evil/path"], "ledger_actual": None, "expected_stock": None, "outstanding_credit": None, "reverse_event_ids": [], "proposal_matches": None}),
    _enc({"protocol_version": 1, "verifier_version": "0.1.0", "ok": False, "errors": ["rust_evil"], "ledger_actual": None, "expected_stock": None, "outstanding_credit": None, "reverse_event_ids": [], "proposal_matches": False}),
    _enc({"protocol_version": 1, "verifier_version": "0.1.0", "ok": True, "errors": ["ledger_mismatch"], "ledger_actual": 100, "expected_stock": 110, "outstanding_credit": 10, "reverse_event_ids": [4], "proposal_matches": True}),
])
def test_fake_child_strict_response_failures(conn, payload):
    _p, cand = seeded_candidate(conn)
    factory, _ = canned_factory(payload)
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert str(ei.value) == "rust verification failed"
    assert ei.value.code in rust_verifier._KNOWN_CODES


def test_failed_response_null_match_is_failure_not_verified(conn):
    _p, cand = seeded_candidate(conn)
    payload = _enc({"protocol_version": 1, "verifier_version": "0.1.0", "ok": False, "errors": ["candidate_mismatch"], "ledger_actual": None, "expected_stock": None, "outstanding_credit": None, "reverse_event_ids": [], "proposal_matches": None})
    factory, _ = canned_factory(payload)
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert ei.value.code == "candidate_mismatch" and ei.value.status == "failed"


def test_child_exit_stderr_timeout_overflow_and_cleanup(conn, monkeypatch):
    _p, cand = seeded_candidate(conn)
    payload = good_response({"proposal": cand}, {"reverse_event_ids": cand["reverse_event_ids"]})
    for factory, holder in (canned_factory(payload, rc=2), canned_factory(b"x" * (rust_verifier.MAX_OUTPUT + 1)), canned_factory(payload, stderr=b"e" * (rust_verifier.MAX_STDERR + 1))):
        with pytest.raises(rust_verifier.RustVerificationError):
            rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
        assert holder["proc"].waited
    monkeypatch.setattr(rust_verifier, "TIMEOUT_SECONDS", 0.2)
    factory, holder = canned_factory(b"", hang=True)
    start = time.monotonic()
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert time.monotonic() - start < rust_verifier.TIMEOUT_SECONDS + rust_verifier.CLEANUP_SECONDS + 2
    assert ei.value.code == "rust_timeout"
    proc = holder["proc"]
    assert proc.killed and proc.waited and proc.stdout.closed and proc.stderr.closed and proc.stdin.closed
    assert not [t for t in threading.enumerate() if t.is_alive() and t is not threading.current_thread() and not t.daemon is False]


class BadStdin:
    def write(self, data):
        raise OSError("write failed secret path")
    def flush(self):
        pass
    def close(self):
        self.closed = True


class BadStdout:
    def read(self, n):
        raise OSError("read failed")
    def close(self):
        self.closed = True


def test_child_writer_reader_poll_and_start_failures_bounded(conn):
    _p, cand = seeded_candidate(conn)
    payload = good_response({"proposal": cand}, {"reverse_event_ids": cand["reverse_event_ids"]})
    cases = []
    f, h = canned_factory(payload, stdin=BadStdin()); cases.append((f, h))
    f, h = canned_factory(payload, poll_error=True); cases.append((f, h))
    def bad_reader(argv, kw):
        proc = FakeProc(payload)
        proc.stdout = BadStdout()
        h["proc"] = proc
        return proc
    cases.append((factory_returning(bad_reader), h))
    for factory, holder in cases:
        start = time.monotonic()
        with pytest.raises(rust_verifier.RustVerificationError) as ei:
            rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
        assert time.monotonic() - start < 5 and "secret" not in str(ei.value)
    def bad_start(argv, **kw):
        raise OSError("no binary at /secret/path")
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=bad_start)
    assert "secret" not in str(ei.value)


def test_missing_executable_and_forged_snapshot_hash(conn, monkeypatch, tmp_path):
    monkeypatch.setattr(rust_verifier, "executable_path", lambda: tmp_path / "missing-verifier")
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", None)
    assert str(tmp_path) not in str(ei.value)
    monkeypatch.undo()
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", None, snapshot_hash="forged-hash")


def test_malformed_history_and_independent_expected_validation(conn):
    conn.execute("INSERT INTO events(product_id,kind,qty,order_id,ts,note,reversed_ids,repair_proposal_id) VALUES ('WIDGET-1','repair_credit',10,NULL,1,'x','[true]','PROP-X')")
    conn.commit()
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", None)
    conn.rollback()


@pytest.mark.parametrize("sql", [
    "UPDATE products SET stock=999 WHERE product_id='WIDGET-1'",
    "INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('WIDGET-1','order_deduction',5,'ORD-1001',1,'inconsistent')",
    "INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('WIDGET-1','order_deduction',5,NULL,1,'missing order')",
    "INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('WIDGET-1','restock',5,'ORD-X',1,'bad field')",
    "INSERT INTO events(product_id,kind,qty,order_id,ts,note,reversed_ids) VALUES ('WIDGET-1','restock',5,NULL,1,'bad refs','[1]')",
])
def test_invalid_history_fails_before_child(conn, sql):
    called = []
    conn.execute(sql); conn.commit()
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", None, _process_factory=lambda *a, **k: called.append(1))
    assert called == []


def test_oversized_raw_refs_rejected_and_history_bound(conn):
    conn.execute("INSERT INTO events(product_id,kind,qty,order_id,ts,note,reversed_ids,repair_proposal_id) VALUES ('WIDGET-1','repair_credit',1,NULL,1,'x',?,'PROP-X')", ("[" + ",".join(["1"] * 500) + "]",))
    conn.commit()
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.build_request(conn, "WIDGET-1", None)
    conn.rollback()
    for i in range(250):
        conn.execute("INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('WIDGET-1','restock',1,NULL,1,'x')")
    conn.commit()
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.build_request(conn, "WIDGET-1", None)


def test_missing_init_fail_closed():
    c2 = sqlite3.connect(":memory:"); c2.row_factory = sqlite3.Row
    store.init_db(c2)
    c2.execute("INSERT INTO products(product_id,name,stock) VALUES ('P','p',0)"); c2.commit()
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(c2, "P", None)
    c2.close()


def break_rust(monkeypatch):
    monkeypatch.setattr(rust_verifier, "executable_path", lambda: rust_verifier.Path("/nonexistent/afterfix-verifier"))


def test_preview_rust_failure_not_simulation_pass(conn, monkeypatch):
    p = make_proposal(conn); break_rust(monkeypatch)
    prev = recovery.preview_proposal(conn, p["proposal_id"])
    assert prev["rust_verification"]["status"] in ("failed", "unavailable")
    assert prev["rust_verification"]["enabled"] is True and prev["simulation_pass"] is False
    assert prev["simulation_checks"]
    assert {c["name"]: c["ok"] for c in prev["invariants"]}["rust_agreement"] is False


def test_preview_inner_needs_review_not_simulated_as_proposed(conn, monkeypatch):
    p = make_proposal(conn)
    orig_copy = recovery._copy_conn
    def racy(src):
        dst = orig_copy(src)
        dst.execute("UPDATE proposals SET status='needs_review', credit_qty=0, reverse_event_ids='[]', reason='raced' WHERE id=?", (p["proposal_id"],)); dst.commit()
        return dst
    monkeypatch.setattr(recovery, "_copy_conn", racy)
    out = recovery.preview_proposal(conn, p["proposal_id"])
    assert out["status"] == "needs_review" and "simulation_checks" not in out


@pytest.mark.parametrize("raw", ['[true]', '["4"]', '[4.0]', 'not-json', '[4,4]', '[999999999999999999999999]'])
def test_malformed_raw_stored_proposal_blocks_gates(conn, raw):
    p = make_proposal(conn)
    conn.execute("UPDATE proposals SET reverse_event_ids=? WHERE id=?", (raw, p["proposal_id"])); conn.commit()
    with pytest.raises(recovery.ApprovalError):
        recovery.preview_proposal(conn, p["proposal_id"])
    with pytest.raises(recovery.ApprovalError):
        recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")


def test_approve_apply_rust_inside_transaction_recording_wrapper(conn, monkeypatch):
    p = make_proposal(conn)
    real_verify = rust_verifier.verify_current
    calls = []
    def recording(c, product_id, candidate=None, **kw):
        calls.append({"in_transaction": bool(c.in_transaction), "counts": counts(c), "fingerprint": store.fingerprint(c, product_id), "candidate": candidate})
        return real_verify(c, product_id, candidate, **kw)
    monkeypatch.setattr(rust_verifier, "verify_current", recording)
    before = counts(conn)
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    assert calls and calls[-1]["in_transaction"] is True
    assert calls[-1]["counts"][:4] == before[:4] and calls[-1]["fingerprint"] == p["snapshot_hash"]
    assert calls[-1]["candidate"] == {"credit_qty": p["credit_qty"], "reverse_event_ids": p["reverse_event_ids"]}
    calls.clear(); mid = counts(conn)
    out = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-txn-1", actor_id="operator")
    assert out["status"] == "applied" and calls and calls[-1]["in_transaction"] is True
    assert calls[-1]["counts"][2] == mid[2] and calls[-1]["counts"][4] == mid[4]


def test_approve_rust_failure_writes_nothing(conn, monkeypatch):
    p = make_proposal(conn); before = counts(conn); break_rust(monkeypatch)
    with pytest.raises(recovery.ApprovalError) as ei:
        recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    assert "nonexistent" not in str(ei.value) and counts(conn) == before
    assert recovery._get_proposal(conn, p["proposal_id"])["status"] == "proposed"


def test_apply_rust_failure_writes_nothing_and_actor_replay(conn, monkeypatch):
    p = make_proposal(conn)
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-actor-1", actor_id="viewer")
    before = counts(conn); break_rust(monkeypatch)
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-apply-1", actor_id="operator")
    assert counts(conn) == before
    monkeypatch.undo()
    out = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-apply-1", actor_id="operator")
    assert out["status"] == "applied"
    break_rust(monkeypatch)
    again = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-apply-1", actor_id="operator")
    assert again == out
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-apply-1", actor_id="viewer")
    with pytest.raises(recovery.ApprovalError):
        recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-apply-1", actor_id="other")


@pytest.mark.parametrize("bad_match", [[], {}, 1, "true"])
def test_malformed_match_values_sanitized(conn, bad_match):
    _p, cand = seeded_candidate(conn)
    failed = {"protocol_version": 1, "verifier_version": "0.1.0", "ok": False, "errors": ["candidate_mismatch"], "ledger_actual": None, "expected_stock": None, "outstanding_credit": None, "reverse_event_ids": [], "proposal_matches": bad_match}
    factory, _h = canned_factory(_enc(failed))
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert str(ei.value) == "rust verification failed" and ei.value.code in rust_verifier._KNOWN_CODES
    factory, _h = canned_factory(good_response({"proposal": cand}, {"reverse_event_ids": cand["reverse_event_ids"], "proposal_matches": bad_match}))
    with pytest.raises(rust_verifier.RustVerificationError) as ei:
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    assert str(ei.value) == "rust verification failed"


@pytest.mark.parametrize("raw", ['[9223372036854775808]', '[true]', '["4"]', '[4.0]', '[4,4]'])
def test_strict_stored_ids_reject_malformed(raw):
    with pytest.raises(ValueError):
        recovery._strict_stored_ids(raw)


def test_strict_stored_ids_max_and_unsorted_normalize():
    assert recovery._strict_stored_ids('[9223372036854775807]') == [2**63 - 1]
    assert recovery._strict_stored_ids('[4,3]') == [3, 4]


class _NoneStdin:
    closed = False
    def write(self, data): return None
    def flush(self): pass
    def close(self): self.closed = True


class _ShortStdin:
    closed = False
    def write(self, data): return len(data) - 1
    def flush(self): pass
    def close(self): self.closed = True


@pytest.mark.parametrize("stdin_cls", [_NoneStdin, _ShortStdin])
def test_none_or_short_stdin_write_fails_with_cleanup(conn, stdin_cls):
    _p, cand = seeded_candidate(conn)
    payload = good_response({"proposal": cand}, {"reverse_event_ids": cand["reverse_event_ids"]})
    factory, holder = canned_factory(payload, stdin=stdin_cls())
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", cand, _process_factory=factory)
    proc = holder["proc"]
    assert proc.killed and proc.waited and proc.stdin.closed


def test_approved_preview_retains_full_fields(conn):
    p = make_proposal(conn)
    recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    prev = recovery.preview_proposal(conn, p["proposal_id"])
    assert prev["status"] == "approved" and prev["before_stock"] == 100 and prev["after_stock"] == 110
    assert prev["credit_qty"] == 10 and prev["reverse_event_ids"] == p["reverse_event_ids"]
    assert prev["simulation_pass"] is True and prev["rust_verification"]["status"] == "verified"
    assert prev["diff"]["stock_delta"] == 10 and prev["preserved_restock_ids"]


def _insert_applied_repair(conn, pid, qty, refs):
    conn.execute("INSERT INTO proposals(id,incident_id,product_id,credit_qty,reverse_event_ids,snapshot_hash,status,reason,created_at) VALUES (?,?,?,?,?, '', 'applied','',1)", (pid, "INC-001", "WIDGET-1", qty, json.dumps(refs)))
    cur = conn.execute("INSERT INTO events(product_id,kind,qty,order_id,ts,note,reversed_ids,repair_proposal_id) VALUES ('WIDGET-1','repair_credit',?,NULL,1,'x',?,?)", (qty, json.dumps(refs), pid))
    conn.commit()
    return cur.lastrowid


def _sync_stock_to_ledger(conn):
    conn.execute("UPDATE products SET stock=? WHERE product_id='WIDGET-1'", (domain.compute_ledger(conn, "WIDGET-1")["ledger_actual"],)); conn.commit()


def test_future_historical_ref_rejected_before_child(conn):
    rid = _insert_applied_repair(conn, "PROP-FUT", 10, [999999])
    cur = conn.execute("INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('WIDGET-1','order_deduction',10,'ORD-1001',1,'future duplicate')")
    future_id = cur.lastrowid; conn.commit()
    assert future_id > rid
    conn.execute("UPDATE events SET reversed_ids=? WHERE id=?", (json.dumps([future_id]), rid))
    conn.execute("UPDATE proposals SET reverse_event_ids=? WHERE id='PROP-FUT'", (json.dumps([future_id]),)); conn.commit(); _sync_stock_to_ledger(conn)
    called = []
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", None, _process_factory=lambda *a, **k: called.append(1))
    assert called == []


def test_oppositely_wrong_repair_credits_rejected_before_child(conn):
    ids = []
    for _ in range(2):
        cur = conn.execute("INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('WIDGET-1','order_deduction',10,'ORD-1001',1,'extra duplicate')"); ids.append(cur.lastrowid)
    conn.commit()
    _insert_applied_repair(conn, "PROP-W1", 15, [ids[0]])
    _insert_applied_repair(conn, "PROP-W2", 5, [ids[1]])
    _sync_stock_to_ledger(conn)
    called = []
    with pytest.raises(rust_verifier.RustVerificationError):
        rust_verifier.verify_current(conn, "WIDGET-1", None, _process_factory=lambda *a, **k: called.append(1))
    assert called == []


def test_unsorted_historical_refs_and_partial_repair_real_rust(conn):
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-2000", 7)
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-2000", 7)
    ids = [r["id"] for r in widget_events(conn) if r["kind"] == "order_deduction"]
    p = recovery.build_proposal(conn, "INC-001", "WIDGET-1", ids)
    assert p["status"] == "proposed" and p["credit_qty"] == 17 and len(p["reverse_event_ids"]) == 2
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
    applied = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "key-unsorted-1", actor_id="operator")
    assert applied["status"] == "applied"
    unsorted = list(reversed(p["reverse_event_ids"]))
    assert unsorted != p["reverse_event_ids"] and sorted(unsorted) == p["reverse_event_ids"]
    conn.execute("UPDATE events SET reversed_ids=? WHERE repair_proposal_id=?", (json.dumps(unsorted), p["proposal_id"]))
    conn.execute("UPDATE proposals SET reverse_event_ids=? WHERE id=?", (json.dumps(unsorted), p["proposal_id"])); conn.commit()
    assert recovery._strict_stored_ids(json.dumps(unsorted)) == p["reverse_event_ids"]
    raw_event = conn.execute("SELECT reversed_ids FROM events WHERE repair_proposal_id=?", (p["proposal_id"],)).fetchone()["reversed_ids"]
    raw_proposal = conn.execute("SELECT reverse_event_ids, snapshot_hash FROM proposals WHERE id=?", (p["proposal_id"],)).fetchone()
    assert raw_event == json.dumps(unsorted) and raw_proposal["reverse_event_ids"] == json.dumps(unsorted)
    assert raw_proposal["snapshot_hash"] == p["snapshot_hash"]
    out = rust_verifier.verify_current(conn, "WIDGET-1", None)
    assert out["status"] == "verified" and out["outstanding_credit"] == 0
    assert out["expected_stock"] == store.get_product(conn, "WIDGET-1")["stock"]
    assert conn.execute("SELECT reversed_ids FROM events WHERE repair_proposal_id=?", (p["proposal_id"],)).fetchone()["reversed_ids"] == json.dumps(unsorted)
    assert conn.execute("SELECT reverse_event_ids FROM proposals WHERE id=?", (p["proposal_id"],)).fetchone()["reverse_event_ids"] == json.dumps(unsorted)
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-3000", 5)
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-3000", 5)
    ord3000_ids = [r["id"] for r in widget_events(conn) if r["kind"] == "order_deduction" and r["order_id"] == "ORD-3000"]
    assert len(ord3000_ids) == 2
    new_extra_id = ord3000_ids[-1]
    already_reversed = set(p["reverse_event_ids"])
    ids = [r["id"] for r in widget_events(conn) if r["kind"] == "order_deduction"]
    evidence_ids = [event_id for event_id in ids if event_id not in already_reversed]
    assert new_extra_id in evidence_ids and not already_reversed.intersection(evidence_ids)
    before = store.get_product(conn, "WIDGET-1")["stock"]
    p2 = recovery.build_proposal(conn, "INC-001", "WIDGET-1", evidence_ids)
    assert p2["status"] == "proposed" and p2["credit_qty"] == 5 and p2["reverse_event_ids"] == [new_extra_id]
    prev = recovery.preview_proposal(conn, p2["proposal_id"])
    assert prev["rust_verification"]["status"] == "verified" and prev["simulation_pass"] is True
    assert prev["rust_verification"]["expected_stock"] == before + 5 and prev["after_stock"] == before + 5
    assert store.get_product(conn, "GADGET-9")["stock"] == 45
