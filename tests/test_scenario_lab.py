"""Offline Scenario Lab regression tests: gates, fixtures, atomicity, stale flow.

No network/real keys: synthetic tokens, tmp SQLite DB, model dependency is a
counting override asserted never to be invoked by lab routes.
"""
import uuid

import pytest
from fastapi.testclient import TestClient

from app import recovery, scenario_lab, store
from app.main import app, get_investigation_client

OPERATOR = "operator-token-for-tests-1234567890abc"
VIEWER = "viewer-token-for-tests-1234567890abcd"
MARKER = "SYNTHETIC_SENTINEL_DO_NOT_ECHO"
LOAD_BODY = {"confirmation": "RESET_SYNTHETIC_SCENARIO"}
_FIXED_TABLES = ("products", "incidents", "events", "proposals", "approvals",
                 "idempotency_keys", "audit_log", "case_records")


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    path = str(tmp_path / "lab.db")
    monkeypatch.setenv("AFTERFIX_DB", path)
    monkeypatch.setenv("AFTERFIX_OPERATOR_TOKEN", OPERATOR)
    monkeypatch.setenv("AFTERFIX_VIEWER_TOKEN", VIEWER)
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "1")
    monkeypatch.setenv("AFTERFIX_ENABLE_SCENARIO_LAB", "1")
    saved = dict(app.dependency_overrides)
    calls = {"model": 0}
    def counting_client():
        calls["model"] += 1
        raise AssertionError("lab must not build a model client")
    app.dependency_overrides[get_investigation_client] = counting_client
    # Default raise_server_exceptions=True is intentional below.
    op = TestClient(app, headers={"Authorization": f"Bearer {OPERATOR}"})
    vw = TestClient(app, headers={"Authorization": f"Bearer {VIEWER}"})
    anon = TestClient(app)
    try:
        yield {"op": op, "vw": vw, "anon": anon, "path": path, "calls": calls,
               "monkeypatch": monkeypatch}
    finally:
        op.close(); vw.close(); anon.close()
        app.dependency_overrides.clear(); app.dependency_overrides.update(saved)


def db(lab, fn):
    conn = store.connect(lab["path"])
    try:
        return fn(conn)
    finally:
        conn.close()


def load(lab, scenario="duplicate_order"):
    r = lab["op"].post(f"/lab/scenarios/{scenario}/load", json=LOAD_BODY)
    assert r.status_code == 200, r.text
    return r.json()


def events(lab, product="WIDGET-1"):
    return db(lab, lambda c: [dict(r) for r in store.list_product_events(c, product)])


def _lab_table_present(conn):
    return conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='scenario_lab_state'").fetchone() is not None


def _snapshot(conn):
    """Snapshot fixed tables only; never creates scenario_lab_state."""
    snap = {}
    for table in _FIXED_TABLES:
        snap[table] = [tuple(r) for r in conn.execute(
            f"SELECT * FROM {table} ORDER BY 1, 2")]
    snap["scenario_lab_state_present"] = _lab_table_present(conn)
    snap["scenario_lab_state"] = (
        [tuple(r) for r in conn.execute(
            "SELECT * FROM scenario_lab_state ORDER BY id")]
        if snap["scenario_lab_state_present"] else None)
    seq_present = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='sqlite_sequence'").fetchone() is not None
    snap["sqlite_sequence"] = (
        [tuple(r) for r in conn.execute(
            "SELECT * FROM sqlite_sequence ORDER BY name")] if seq_present else None)
    return snap


def snapshot(lab):
    return db(lab, _snapshot)


class _FailingConn:
    """Proxy raising a raw sentinel when one fixed SQL needle is executed."""
    def __init__(self, real, needle):
        self._real = real
        self._needle = needle
    def execute(self, sql, params=()):
        if self._needle in str(sql):
            raise RuntimeError(MARKER)
        return self._real.execute(sql, params)
    def executemany(self, sql, seq):
        if self._needle in str(sql):
            raise RuntimeError(MARKER)
        return self._real.executemany(sql, seq)
    def __getattr__(self, name):
        return getattr(self._real, name)


def _seed_representative_rows(conn):
    conn.execute(
        "INSERT INTO proposals(id, incident_id, product_id, credit_qty, "
        "reverse_event_ids, snapshot_hash, status, reason, created_at) VALUES "
        "('PROP-rep','INC-001','WIDGET-1',10,'[]','rep-hash','approved','rep',11)")
    conn.execute(
        "INSERT INTO approvals(id, proposal_id, snapshot_hash, baseline_json, "
        "created_at, actor_id) VALUES ('APPRO-rep','PROP-rep','rep-hash','{}',12,'operator')")
    conn.execute(
        "INSERT INTO idempotency_keys(idempotency_key, proposal_id, approval_id, "
        "result, created_at) VALUES ('rep-key','PROP-rep','APPRO-rep','{}',13)")
    conn.execute(
        "INSERT INTO case_records(case_id, schema_version, incident_id, product_id, "
        "proposal_id, actor_id, created_at, before_json, investigation_json, "
        "initial_preview_json) VALUES ('CASE-rep',1,'INC-001','WIDGET-1',"
        "'PROP-rep','operator',14,'{}','{}','{}')")
    conn.commit()


def test_disabled_by_default_and_exact_flag(lab, monkeypatch):
    monkeypatch.delenv("AFTERFIX_ENABLE_SCENARIO_LAB", raising=False)
    assert lab["op"].get("/lab/scenarios").status_code == 404
    assert lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY).status_code == 404
    monkeypatch.setenv("AFTERFIX_ENABLE_SCENARIO_LAB", "true")
    assert lab["op"].get("/lab/scenarios").status_code == 404
    monkeypatch.setenv("AFTERFIX_ENABLE_SCENARIO_LAB", "1")
    assert lab["op"].get("/lab/scenarios").status_code == 200


def test_denials_before_db_connect(lab, monkeypatch):
    calls = {"n": 0}
    def boom(*a, **k):
        calls["n"] += 1
        raise AssertionError("connect must not be called")
    monkeypatch.setattr(store, "connect", boom)
    assert lab["anon"].get("/lab/scenarios").status_code == 401
    assert lab["vw"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY).status_code == 403
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "0")
    assert lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY).status_code == 403
    assert lab["op"].post("/lab/scenarios/nope/load", json=LOAD_BODY).status_code == 403
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "1")
    monkeypatch.delenv("AFTERFIX_ENABLE_SCENARIO_LAB", raising=False)
    assert lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY).status_code == 404
    assert calls["n"] == 0


def test_catalogue_readonly_capabilities_no_model(lab, monkeypatch):
    before = db(lab, lambda c: c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0])
    r = lab["vw"].get("/lab/scenarios")
    assert r.status_code == 200
    body = r.json()
    assert body["data_label"] == "Synthetic Scenario Lab" and body["can_load"] is False
    assert [s["id"] for s in body["scenarios"]] == [
        "duplicate_order", "missing_opening", "stale_snapshot"]
    assert [s["can_advance"] for s in body["scenarios"]] == [False, False, True]
    assert lab["op"].get("/lab/scenarios").json()["can_load"] is True
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "0")
    assert lab["op"].get("/lab/scenarios").json()["can_load"] is False
    after = db(lab, lambda c: c.execute("SELECT COUNT(*) FROM sqlite_master").fetchone()[0])
    assert after == before and lab["calls"]["model"] == 0


@pytest.mark.parametrize("payload", [
    {}, {"confirmation": "nope"}, {"confirmation": "RESET_SYNTHETIC_SCENARIO", "x": MARKER},
    {"confirmation": MARKER}, {"confirmation": "RESET_SYNTHETIC_SCENARIO\n"},
    {"confirmation": " RESET_SYNTHETIC_SCENARIO"}, {"confirmation": 123},
])
def test_load_body_strict_and_sanitized(lab, payload):
    r = lab["op"].post("/lab/scenarios/duplicate_order/load", json=payload)
    assert r.status_code == 422 and MARKER not in r.text
    assert r.json() == {"detail": "Invalid request"}


def test_unknown_scenario_404(lab):
    assert lab["op"].post("/lab/scenarios/unknown/load", json=LOAD_BODY).status_code == 404


@pytest.mark.parametrize("scenario", ["duplicate_order", "stale_snapshot"])
def test_canonical_fixture_structure(lab, scenario):
    out = load(lab, scenario)
    assert out["incident_id"] == "INC-001" and out["product_id"] == "WIDGET-1"
    assert uuid.UUID(out["load_id"])
    assert out["widget_stock"] == 100 and out["gadget_stock"] == 45
    ev = events(lab)
    assert [e["kind"] for e in ev] == ["init", "order_deduction", "order_deduction", "restock"]
    assert events(lab, "GADGET-9")[-1]["kind"] == "order_deduction"
    def checks(c):
        assert c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM approvals").fetchone()[0] == 0
        assert c.execute("SELECT COUNT(*) FROM case_records").fetchone()[0] == 0
        assert not [r for r in store.list_product_events(c, "WIDGET-1") if r["kind"] == "repair_credit"]
        row = c.execute("SELECT * FROM scenario_lab_state WHERE id=1").fetchone()
        assert row["load_id"] == out["load_id"] and row["scenario_id"] == scenario
        assert row["advanced"] == 0 and row["actor_id"] == "operator"
        audit = c.execute("SELECT * FROM audit_log WHERE action='scenario_load'").fetchall()
        assert len(audit) == 1 and audit[0]["actor_id"] == "operator"
        return store.fingerprint(c, "WIDGET-1")
    assert db(lab, checks) == out["snapshot_hash"]
    assert lab["calls"]["model"] == 0


def test_missing_opening_fixture_and_no_deterministic_repair(lab):
    load(lab, "missing_opening")
    ev = events(lab)
    assert not any(e["kind"] == "init" for e in ev)
    assert db(lab, lambda c: store.get_product(c, "WIDGET-1")["stock"]) == 100
    assert any(e["kind"] == "init" for e in events(lab, "GADGET-9"))
    ids = [e["id"] for e in ev]
    r = lab["op"].post("/proposals", json={
        "incident_id": "INC-001", "product_id": "WIDGET-1", "evidence_event_ids": ids})
    assert r.status_code == 200 and r.json()["status"] == "needs_review"


def test_successive_loads_fresh_ids_and_fingerprints(lab):
    first = load(lab)
    first_ids = {e["id"] for e in events(lab)}
    second = load(lab)
    second_ids = {e["id"] for e in events(lab)}
    assert first["load_id"] != second["load_id"]
    assert first["snapshot_hash"] != second["snapshot_hash"]
    assert second_ids.isdisjoint(first_ids) or min(second_ids) > max(first_ids)


def test_load_rollback_restores_prior_data_and_session(lab):
    first = load(lab)
    db(lab, _seed_representative_rows)
    def sentinel(c):
        c.execute("INSERT INTO proposals(id, incident_id, product_id, credit_qty, "
                  "reverse_event_ids, snapshot_hash, status, reason, created_at) "
                  "VALUES ('PROP-sentinel','INC-001','WIDGET-1',0,'[]','','needs_review','x',1)")
        c.execute("CREATE TRIGGER fail_lab BEFORE INSERT ON audit_log "
                  "WHEN NEW.action='scenario_load' BEGIN SELECT RAISE(ABORT,'boom'); END")
        c.commit()
    db(lab, sentinel)
    before = snapshot(lab)
    prior_events = events(lab)
    r = lab["op"].post("/lab/scenarios/stale_snapshot/load", json=LOAD_BODY)
    assert r.status_code == 500 and r.json() == {"detail": "scenario load unavailable"}
    assert MARKER not in r.text
    assert snapshot(lab) == before
    def verify(c):
        c.execute("DROP TRIGGER IF EXISTS fail_lab"); c.commit()
        assert c.execute("SELECT COUNT(*) FROM proposals WHERE id='PROP-sentinel'").fetchone()[0] == 1
        row = c.execute("SELECT * FROM scenario_lab_state WHERE id=1").fetchone()
        assert row["load_id"] == first["load_id"] and row["scenario_id"] == "duplicate_order"
        return [dict(x) for x in store.list_product_events(c, "WIDGET-1")]
    assert db(lab, verify) == prior_events


def test_first_failed_load_leaves_lab_table_absent_and_preserves_data(lab):
    assert lab["op"].post("/admin/reset").status_code == 200
    def arm(c):
        assert not _lab_table_present(c)
        c.execute("CREATE TRIGGER fail_first BEFORE INSERT ON audit_log "
                  "WHEN NEW.action='scenario_load' BEGIN SELECT RAISE(ABORT,'boom'); END")
        c.commit()
    db(lab, arm)
    before = snapshot(lab)
    assert before["scenario_lab_state_present"] is False
    r = lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY)
    assert r.status_code == 500 and r.json() == {"detail": "scenario load unavailable"}
    after = snapshot(lab)
    assert after == before and after["scenario_lab_state_present"] is False
    db(lab, lambda c: (c.execute("DROP TRIGGER IF EXISTS fail_first"), c.commit()))


def test_load_scratch_runtime_error_is_fixed_500(lab, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(MARKER)
    monkeypatch.setattr(scenario_lab, "_build_scratch", boom)
    r = lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY)
    assert r.status_code == 500 and r.json() == {"detail": "scenario load unavailable"}
    assert MARKER not in r.text
    assert db(lab, lambda c: not _lab_table_present(c))


def test_load_connect_runtime_error_is_fixed_500(lab, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError(MARKER)
    monkeypatch.setattr(store, "connect", boom)
    r = lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY)
    assert r.status_code == 500 and r.json() == {"detail": "scenario load unavailable"}
    assert MARKER not in r.text


@pytest.mark.parametrize("needle", ["BEGIN IMMEDIATE",
                                    "CREATE TABLE IF NOT EXISTS scenario_lab_state"])
def test_load_begin_or_metadata_setup_error_is_fixed_500(lab, monkeypatch, needle):
    assert lab["op"].post("/admin/reset").status_code == 200
    before = snapshot(lab)
    real_connect = store.connect
    monkeypatch.setattr(store, "connect",
                        lambda *a, **k: _FailingConn(real_connect(lab["path"]), needle))
    r = lab["op"].post("/lab/scenarios/duplicate_order/load", json=LOAD_BODY)
    assert r.status_code == 500 and r.json() == {"detail": "scenario load unavailable"}
    assert MARKER not in r.text
    monkeypatch.setattr(store, "connect", real_connect)
    after = snapshot(lab)
    assert after == before and after["scenario_lab_state_present"] is False


def _stale_proposal(lab, approve=True):
    out = load(lab, "stale_snapshot")
    ids = [e["id"] for e in events(lab) if e["kind"] == "order_deduction"]
    p = lab["op"].post("/proposals", json={
        "incident_id": "INC-001", "product_id": "WIDGET-1", "evidence_event_ids": ids}).json()
    assert p["status"] == "proposed"
    prev = lab["op"].get(f"/proposals/{p['proposal_id']}/preview")
    assert prev.status_code == 200 and prev.json()["simulation_pass"] is True
    approval = None
    if approve:
        r = lab["op"].post(f"/proposals/{p['proposal_id']}/approve",
                           json={"snapshot_hash": p["snapshot_hash"]})
        assert r.status_code == 200, r.text
        approval = r.json()
    return out, p, approval


def _advance(lab, out, p, **over):
    body = {"confirmation": "ADD_SYNTHETIC_RESTOCK", "load_id": out["load_id"],
            "proposal_id": p["proposal_id"],
            "expected_snapshot_hash": p["snapshot_hash"]}
    body.update(over)
    return lab["op"].post("/lab/scenarios/stale_snapshot/advance", json=body)


def test_advance_full_stale_flow_and_repeat(lab):
    out, p, approval = _stale_proposal(lab)
    r = _advance(lab, out, p)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["stock"] == 101 and body["snapshot_hash"] != p["snapshot_hash"]
    ev = events(lab)
    restocks = [e for e in ev if e["kind"] == "restock"]
    assert len(restocks) == 2 and restocks[-1]["qty"] == 1 and restocks[-1]["id"] == body["event_id"]
    assert not any(e["kind"] == "repair_credit" for e in ev)
    assert db(lab, lambda c: store.get_product(c, "GADGET-9")["stock"]) == 45
    apply_r = lab["op"].post("/apply", json={
        "proposal_id": p["proposal_id"], "approval_id": approval["approval_id"],
        "idempotency_key": "lab-stale-key-1"})
    assert apply_r.status_code == 409
    assert _advance(lab, out, p).status_code == 409
    assert len([e for e in events(lab) if e["kind"] == "restock"]) == 2
    assert db(lab, lambda c: store.get_product(c, "WIDGET-1")["stock"]) == 101
    assert lab["calls"]["model"] == 0


def test_advance_old_approve_rejected(lab):
    out, p, _ = _stale_proposal(lab, approve=False)
    assert _advance(lab, out, p).status_code == 200
    r = lab["op"].post(f"/proposals/{p['proposal_id']}/approve",
                       json={"snapshot_hash": p["snapshot_hash"]})
    assert r.status_code == 409


@pytest.mark.parametrize("over", [
    {"load_id": str(uuid.uuid4())}, {"proposal_id": "PROP-missing"},
    {"expected_snapshot_hash": "0" * 64},
])
def test_advance_binding_conflicts_no_writes(lab, over):
    out, p, _ = _stale_proposal(lab)
    before = events(lab)
    assert _advance(lab, out, p, **over).status_code == 409
    assert events(lab) == before


@pytest.mark.parametrize("over", [
    {"load_id": "not-a-uuid"}, {"load_id": str(uuid.uuid4()).upper()},
    {"expected_snapshot_hash": "A" * 64}, {"expected_snapshot_hash": MARKER},
    {"extra": MARKER}, {"confirmation": "ADD_SYNTHETIC_RESTOCK\n"},
    {"load_id": 123},
])
def test_advance_strict_fields_sanitized(lab, over):
    out, p, _ = _stale_proposal(lab)
    r = _advance(lab, out, p, **over)
    assert r.status_code == 422 and MARKER not in r.text


def test_advance_wrong_scenario_session_409(lab):
    load(lab, "duplicate_order")
    ids = [e["id"] for e in events(lab) if e["kind"] == "order_deduction"]
    p = lab["op"].post("/proposals", json={
        "incident_id": "INC-001", "product_id": "WIDGET-1", "evidence_event_ids": ids}).json()
    state = db(lab, lambda c: dict(c.execute("SELECT * FROM scenario_lab_state WHERE id=1").fetchone()))
    r = lab["op"].post("/lab/scenarios/stale_snapshot/advance", json={
        "confirmation": "ADD_SYNTHETIC_RESTOCK", "load_id": state["load_id"],
        "proposal_id": p["proposal_id"], "expected_snapshot_hash": p["snapshot_hash"]})
    assert r.status_code == 409


def test_advance_without_lab_session_409_and_metadata_absent(lab):
    assert lab["op"].post("/admin/reset").status_code == 200
    before = snapshot(lab)
    assert before["scenario_lab_state_present"] is False
    r = lab["op"].post("/lab/scenarios/stale_snapshot/advance", json={
        "confirmation": "ADD_SYNTHETIC_RESTOCK", "load_id": str(uuid.uuid4()),
        "proposal_id": "PROP-any", "expected_snapshot_hash": "0" * 64})
    assert r.status_code == 409 and r.json() == {"detail": "scenario state conflict"}
    after = snapshot(lab)
    assert after == before and after["scenario_lab_state_present"] is False


def test_advance_connect_runtime_error_is_fixed_500(lab, monkeypatch):
    out, p, _ = _stale_proposal(lab)
    before = snapshot(lab)
    def boom(*a, **k):
        raise RuntimeError(MARKER)
    monkeypatch.setattr(store, "connect", boom)
    r = _advance(lab, out, p)
    assert r.status_code == 500 and r.json() == {"detail": "scenario advance unavailable"}
    assert MARKER not in r.text
    monkeypatch.undo()
    assert snapshot(lab) == before


@pytest.mark.parametrize("needle", ["BEGIN IMMEDIATE", "sqlite_master"])
def test_advance_begin_or_metadata_lookup_error_is_fixed_500(lab, monkeypatch, needle):
    out, p, _ = _stale_proposal(lab)
    before = snapshot(lab)
    real_connect = store.connect
    monkeypatch.setattr(store, "connect",
                        lambda *a, **k: _FailingConn(real_connect(lab["path"]), needle))
    r = _advance(lab, out, p)
    assert r.status_code == 500 and r.json() == {"detail": "scenario advance unavailable"}
    assert MARKER not in r.text
    monkeypatch.setattr(store, "connect", real_connect)
    assert snapshot(lab) == before


def test_advance_rollback_after_restock_insert(lab):
    out, p, _ = _stale_proposal(lab)
    def arm(c):
        c.execute("CREATE TRIGGER fail_adv BEFORE INSERT ON audit_log "
                  "WHEN NEW.action='scenario_advance' BEGIN SELECT RAISE(ABORT,'boom'); END")
        c.commit()
    db(lab, arm)
    before = snapshot(lab)
    before_events = events(lab)
    r = _advance(lab, out, p)
    assert r.status_code == 500 and r.json() == {"detail": "scenario advance unavailable"}
    assert snapshot(lab) == before
    def verify(c):
        c.execute("DROP TRIGGER IF EXISTS fail_adv"); c.commit()
        assert c.execute("SELECT advanced FROM scenario_lab_state WHERE id=1").fetchone()[0] == 0
        return store.get_product(c, "WIDGET-1")["stock"]
    assert db(lab, verify) == 100 and events(lab) == before_events
    assert _advance(lab, out, p).status_code == 200


def test_normal_reset_invalidates_lab_session(lab):
    out, p, _ = _stale_proposal(lab)
    assert lab["op"].post("/admin/reset").status_code == 200
    assert _advance(lab, out, p).status_code == 409
    def no_state(c):
        row = c.execute("SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='scenario_lab_state'").fetchone()
        return row is None or c.execute("SELECT COUNT(*) FROM scenario_lab_state").fetchone()[0] == 0
    assert db(lab, no_state)


def test_invalidate_session_without_table_is_noop(tmp_path):
    conn = store.connect(str(tmp_path / "plain.db"))
    try:
        store.reset_and_seed(conn)
        assert scenario_lab.invalidate_session(conn) is False
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='table' "
                            "AND name='scenario_lab_state'").fetchone() is None
    finally:
        conn.close()


def test_duplicate_recovery_still_works_after_load(lab):
    load(lab, "duplicate_order")
    ev = events(lab)
    ids = [e["id"] for e in ev if e["kind"] == "order_deduction"]
    conn = store.connect(lab["path"])
    try:
        p = recovery.build_proposal(conn, "INC-001", "WIDGET-1", ids)
        assert p["status"] == "proposed" and p["credit_qty"] == 10
        assert recovery.preview_proposal(conn, p["proposal_id"])["simulation_pass"] is True
        a = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"], actor_id="operator")
        res = recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"],
                                      "lab-recovery-key", actor_id="operator")
        assert res["after_stock"] == 110
        assert recovery.verify_product(conn, "WIDGET-1", p["proposal_id"])["pass"]
    finally:
        conn.close()


def test_manifest_unchanged_no_lab_tools(lab):
    r = lab["op"].get("/tools/manifest")
    assert r.status_code == 200
    names = {t["name"] for t in r.json()["tools"]}
    assert not any("lab" in n or "scenario" in n or n in ("reset", "advance") for n in names)
