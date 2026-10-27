"""Offline API tests for POST /incidents/{id}/investigate (fake transport only)."""
import json

import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app import store
from app.main import app, get_investigation_client
from app.meta_client import MODEL, MetaClient
from tests.test_investigator import FakeTransport, completed, fc, ids, make_final, message, tool_then_final

DUMMY_KEY = "DUMMY_BROWSER_KEY_MARKER"
DUMMY_ENV_KEY = "DUMMY_ENV_KEY_MARKER"
WIDGET = "WIDGET-1"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    path = tmp_path / "afterfix.db"
    monkeypatch.setenv("AFTERFIX_DB", str(path))
    conn = store.connect(str(path))
    try:
        store.reset_and_seed(conn)
        event_ids = ids(conn)
    finally:
        conn.close()
    saved_overrides = dict(app.dependency_overrides)
    client = TestClient(app)
    try:
        yield {"client": client, "path": str(path), "ids": event_ids}
    finally:
        client.close()
        app.dependency_overrides.clear()
        app.dependency_overrides.update(saved_overrides)
        # monkeypatch restores AFTERFIX_DB / MODEL_API_KEY / gateway env.


def install_fake(api, handler):
    fake = FakeTransport(handler)
    app.dependency_overrides[get_investigation_client] = lambda: MetaClient(transport=fake)
    return fake


def db_state(api):
    conn = store.connect(api["path"])
    try:
        return {
            "stock": store.get_product(conn, WIDGET)["stock"],
            "proposals": conn.execute("SELECT COUNT(*) FROM proposals").fetchone()[0],
            "approvals": conn.execute("SELECT COUNT(*) FROM approvals").fetchone()[0],
            "repairs": conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0],
            "actions": {r[0] for r in conn.execute("SELECT action FROM audit_log")},
        }
    finally:
        conn.close()


def assert_no_approval_or_repair(api, stock=100):
    state = db_state(api)
    assert state["stock"] == stock
    assert state["approvals"] == 0
    assert state["repairs"] == 0
    assert state["actions"] <= {"seed"}
    return state


def _pick(payload, *keys):
    if isinstance(payload, dict):
        for key in keys:
            if payload.get(key):
                return payload[key]
        for value in payload.values():
            found = _pick(value, *keys)
            if found:
                return found
    return None


def test_investigate_creates_draft_only_then_human_workflow_completes(api):
    fake = install_fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    resp = api["client"].post("/incidents/INC-001/investigate", json={})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["status"] == "proposed"
    assert result["proposal"] and result["proposal_id"]
    assert [t["tool"] for t in result["trace"]] == ["get_patch", "read_events"]
    # Investigator route alone: draft only, stock unchanged, nothing approved/applied.
    state = assert_no_approval_or_repair(api, stock=100)
    assert state["proposals"] == 1

    # Separate, explicit human-controlled workflow (never auto-triggered above).
    pid = result["proposal_id"]
    preview = api["client"].get(f"/proposals/{pid}/preview")
    assert preview.status_code == 200, preview.text
    snapshot = _pick(preview.json(), "snapshot_hash") or result["proposal"].get("snapshot_hash")
    assert snapshot
    approved = api["client"].post(f"/proposals/{pid}/approve", json={"snapshot_hash": snapshot})
    assert approved.status_code == 200, approved.text
    approval_id = _pick(approved.json(), "approval_id", "id")
    assert approval_id
    applied = api["client"].post(
        "/apply",
        json={"proposal_id": pid, "approval_id": approval_id, "idempotency_key": "synthetic-api-test-1"},
    )
    assert applied.status_code == 200, applied.text
    verified = api["client"].post("/verify", json={"product_id": WIDGET, "proposal_id": pid})
    assert verified.status_code == 200, verified.text
    assert db_state(api)["stock"] == 110
    assert len(fake.payloads) == 3  # human workflow made no further model calls


def test_missing_incident_404_and_extra_body_fields_422(api):
    install_fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    assert api["client"].post("/incidents/INC-MISSING/investigate", json={}).status_code == 404
    for extra in ("sql", "model", "api_key", "approval", "MODEL_API_KEY", "environment"):
        resp = api["client"].post("/incidents/INC-001/investigate", json={extra: DUMMY_KEY})
        assert resp.status_code == 422, (extra, resp.text)
        assert DUMMY_KEY not in resp.text
    assert db_state(api)["proposals"] == 0
    assert_no_approval_or_repair(api)


def test_validation_errors_never_echo_synthetic_credential_markers(api):
    fake = install_fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    url = "/incidents/INC-001/investigate"

    responses = [
        # Extra-field value containing the marker (default handler echoes input).
        api["client"].post(url, json={"api_key": DUMMY_KEY}),
        api["client"].post(url, json={"unexpected": {"nested": DUMMY_KEY}}),
        # Marker used as an unknown field name itself.
        api["client"].post(url, json={DUMMY_KEY: "synthetic-value"}),
        # Wrong top-level type carrying the marker.
        api["client"].post(url, json=[DUMMY_KEY]),
        api["client"].post(url, json=DUMMY_KEY),
        # Malformed JSON carrying the marker.
        api["client"].post(
            url,
            content='{"api_key": "' + DUMMY_KEY + '",',
            headers={"Content-Type": "application/json"},
        ),
    ]
    # Validation failures on other routes must be sanitized too.
    responses.append(api["client"].post("/apply", json={"idempotency_key": DUMMY_KEY}))
    # product_id must be a string; a dict carrying the marker is a genuine
    # validation error (a bare string would be a valid VerifyRequest).
    responses.append(api["client"].post("/verify", json={"product_id": {"value": DUMMY_KEY}}))

    for resp in responses:
        assert resp.status_code == 422, resp.text
        assert resp.json() == {"detail": "Invalid request"}
        assert DUMMY_KEY not in resp.text
    # No validation-failed request reached the model transport or persistence.
    assert fake.payloads == []
    assert db_state(api)["proposals"] == 0
    assert_no_approval_or_repair(api)


def test_absent_gateway_503_no_leak_and_invalid_gateway_host_503(api, monkeypatch):
    # No dependency override: exercise the real default dependency, offline.
    monkeypatch.delenv("AFTERFIX_META_BASE_URL", raising=False)
    monkeypatch.setenv("MODEL_API_KEY", DUMMY_ENV_KEY)
    resp = api["client"].post("/incidents/INC-001/investigate", json={})
    assert resp.status_code == 503
    assert DUMMY_ENV_KEY not in resp.text

    for bad in ("https://example.com/v1", "http://localhost:8765/v1", "http://127.0.0.1/v1"):
        monkeypatch.setenv("AFTERFIX_META_BASE_URL", bad)
        resp = api["client"].post("/incidents/INC-001/investigate", json={})
        assert resp.status_code == 503, (bad, resp.text)  # rejected before any network call
        assert DUMMY_ENV_KEY not in resp.text
    assert db_state(api)["proposals"] == 0


def test_model_needs_review_leaves_no_draft(api):
    final = make_final([], decision="needs_review", hypothesis="Insufficient evidence.")
    install_fake(api, lambda p, n: completed([message(json.dumps(final))]))
    resp = api["client"].post("/incidents/INC-001/investigate", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "needs_review"
    assert resp.json()["proposal"] is None
    assert db_state(api)["proposals"] == 0
    assert_no_approval_or_repair(api)


def test_forbidden_apply_tool_leaves_no_draft_or_repair(api):
    install_fake(api, lambda p, n: completed([fc("c1", "apply_repair", {})]))
    resp = api["client"].post("/incidents/INC-001/investigate", json={})
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "needs_review"
    assert db_state(api)["proposals"] == 0
    assert_no_approval_or_repair(api)


def test_browser_key_not_honored_and_usage_model_trace_returned(api, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", DUMMY_ENV_KEY)
    fake = install_fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    # A browser-supplied key is rejected as an extra field and never reaches the transport.
    rejected = api["client"].post("/incidents/INC-001/investigate", json={"api_key": DUMMY_KEY})
    assert rejected.status_code == 422
    assert DUMMY_KEY not in rejected.text
    assert fake.payloads == []
    assert DUMMY_KEY not in json.dumps(fake.payloads)

    resp = api["client"].post("/incidents/INC-001/investigate", json={})
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["model"] == MODEL == "muse-spark-1.3"
    assert result["counts"]["requests"] == len(fake.payloads) == 3
    assert result["usage"]["requests"] == 3
    assert result["usage"]["input_tokens"] == 33
    assert result["usage"]["output_tokens"] == 21
    assert result["usage"]["total_tokens"] == 54
    assert result["trace"] and result["explanations"] and result["evidence_event_ids"]
    assert DUMMY_KEY not in resp.text and DUMMY_ENV_KEY not in resp.text
    assert DUMMY_KEY not in json.dumps(fake.payloads)
    assert_no_approval_or_repair(api)
