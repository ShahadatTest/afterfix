"""Offline regressions for investigator output-token limits."""
import copy
import json

import pytest

from app import store
from app.investigator import investigate
from app.meta_client import MetaClient

WIDGET = "WIDGET-1"
SECRET = "RAW_PARTIAL_OR_REASONING_MARKER"


def usage(i=11, o=7):
    return {"input_tokens": i, "output_tokens": o, "total_tokens": i + o}


def completed(output, o=7):
    return {"status": "completed", "output": output, "usage": usage(o=o)}


def fc(cid, name, obj):
    return {"type": "function_call", "call_id": cid, "name": name, "arguments": json.dumps(obj)}


def message(text):
    return {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}


def reasoning():
    return {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": SECRET}]}


class FakeTransport:
    def __init__(self, handler):
        self.handler = handler
        self.payloads = []

    def __call__(self, payload):
        self.payloads.append(copy.deepcopy(payload))
        return self.handler(payload, len(self.payloads))


@pytest.fixture()
def db(tmp_path):
    conn = store.connect(str(tmp_path / "afterfix.db"))
    store.reset_and_seed(conn)
    yield conn
    conn.close()


def event_ids(conn):
    rows = store.list_product_events(conn, WIDGET)
    deductions = [r["id"] for r in rows if r["kind"] == "order_deduction"]
    return [r["id"] for r in rows], deductions[:2]


def final_json(cited):
    return json.dumps({
        "decision": "propose",
        "hypothesis": "Duplicate delivery caused one extra deduction.",
        "observations": [{"text": "Two deductions share one order.", "evidence_event_ids": cited[:1]}],
        "inferences": [{"text": "The later duplicate is extra.", "evidence_event_ids": cited[1:]}],
        "uncertainties": [],
        "evidence_event_ids": list(cited),
    })


def tool_prefix(all_ids):
    def prefix(payload, n):
        if n == 1:
            return completed([reasoning(), fc("c1", "get_patch", {})])
        if n == 2:
            return completed([reasoning(), fc("c2", "read_events", {"event_ids": list(all_ids)})])
        return None
    return prefix


def run(conn, handler):
    fake = FakeTransport(handler)
    result = investigate(conn, "INC-001", client=MetaClient(transport=fake))
    return result, fake


def assert_no_writes(conn):
    assert conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0] == 0
    assert {r[0] for r in conn.execute("SELECT action FROM audit_log")} <= {"seed"}
    assert store.get_product(conn, WIDGET)["stock"] == 100


def test_tool_phase_800_final_phase_2048_and_normal_proposal(db):
    all_ids, cited = event_ids(db)
    prefix = tool_prefix(all_ids)

    def handler(payload, n):
        return prefix(payload, n) or completed([reasoning(), message(final_json(cited))])

    result, fake = run(db, handler)
    assert [p["max_output_tokens"] for p in fake.payloads] == [800, 800, 2048]
    assert "tools" in fake.payloads[0] and "tools" in fake.payloads[1]
    assert "tools" not in fake.payloads[2] and "text" in fake.payloads[2]
    assert result["status"] == "proposed" and result["stop_category"] == "proposal_created"
    assert result["counts"]["requests"] == len(fake.payloads) == 3
    assert result["evidence_event_ids"] == cited
    assert result["usage"]["requests"] == 3
    assert result["usage"]["unknown_usage_calls"] == 0
    rows = db.execute("SELECT * FROM proposals").fetchall()
    assert len(rows) == 1 and rows[0]["credit_qty"] == 10
    assert db.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
    assert db.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0] == 0
    assert store.get_product(db, WIDGET)["stock"] == 100
    assert SECRET not in json.dumps(result)
    assert SECRET not in json.dumps(fake.payloads[1:])


def test_incomplete_max_output_tokens_final_no_retry_no_repair(db):
    all_ids, cited = event_ids(db)
    prefix = tool_prefix(all_ids)
    partial = final_json(cited)  # plausible complete-looking text must be ignored

    def handler(payload, n):
        early = prefix(payload, n)
        if early is not None:
            return early
        return {
            "status": "incomplete",
            "incomplete_details": {"reason": "max_output_tokens"},
            "output": [reasoning(), message(partial + SECRET)],
            "usage": usage(o=2048),
        }

    result, fake = run(db, handler)
    assert result["status"] == "needs_review"
    assert result["stop_category"] == result["reason"] == "model_output_limit"
    assert result["proposal"] is None and result["proposal_id"] is None
    assert result["counts"]["requests"] == len(fake.payloads) == 3
    assert [p["max_output_tokens"] for p in fake.payloads] == [800, 800, 2048]
    assert result["usage"]["input_tokens"] == 33
    assert result["usage"]["output_tokens"] == 7 + 7 + 2048
    assert result["usage"]["total_tokens"] == 33 + 7 + 7 + 2048
    assert result["usage"]["unknown_usage_calls"] == 0
    assert SECRET not in json.dumps(result) and partial not in json.dumps(result)
    assert "max_output_tokens" not in json.dumps(result)
    assert_no_writes(db)


@pytest.mark.parametrize("final_resp", [
    {"status": "incomplete", "incomplete_details": {"reason": "content_filter"}, "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "incomplete_details": {"reason": "MAX_OUTPUT_TOKENS"}, "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "incomplete_details": None, "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "incomplete_details": "max_output_tokens", "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "incomplete_details": [{"reason": "max_output_tokens"}], "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "incomplete_details": {"reason": 2048}, "output": [], "usage": usage(o=2048)},
    {"status": "incomplete", "incomplete_details": {}, "output": [], "usage": usage(o=2048)},
    {"status": "failed", "output": [], "usage": usage(o=2048)},
    {"status": "failed", "incomplete_details": {"reason": "max_output_tokens"}, "output": [], "usage": usage(o=2048)},
    {"status": "queued", "output": [], "usage": usage(o=2048)},
])
def test_other_incomplete_or_failed_final_stays_generic(db, final_resp):
    all_ids, _cited = event_ids(db)
    prefix = tool_prefix(all_ids)

    def handler(payload, n):
        return prefix(payload, n) or copy.deepcopy(final_resp)

    result, fake = run(db, handler)
    assert result["status"] == "needs_review"
    assert result["stop_category"] == result["reason"] == "invalid_or_incomplete_response"
    assert result["proposal"] is None
    assert result["counts"]["requests"] == len(fake.payloads) == 3
    assert result["usage"]["unknown_usage_calls"] == 0
    assert_no_writes(db)


def test_client_error_fails_closed_with_single_attempt(db):
    def handler(payload, n):
        raise RuntimeError("upstream billing detail " + SECRET)

    result, fake = run(db, handler)
    assert result["status"] == "needs_review"
    assert result["stop_category"] == result["reason"] == "model_client_error"
    assert len(fake.payloads) == 1 and result["counts"]["requests"] == 1
    assert SECRET not in json.dumps(result)
    assert_no_writes(db)
