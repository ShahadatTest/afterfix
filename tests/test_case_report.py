"""Offline tests for durable case reports. Fake Meta transports are labelled
offline; the real release Rust verifier is exercised via existing preview/
approve/apply/verify routes."""
import json

import pytest
from fastapi.testclient import TestClient

from app import case_report, main as main_module, store
from app.auth import InvestigationGate
from app.main import app, get_investigation_client
from app.meta_client import MetaClient
from tests.test_investigator import FakeTransport, completed, ids, make_final, message, tool_then_final

OPERATOR = "operator-token-for-tests-1234567890abc"
VIEWER = "viewer-token-for-tests-1234567890abcd"
WIDGET = "WIDGET-1"


@pytest.fixture()
def api(tmp_path, monkeypatch):
    path = tmp_path / "afterfix.db"
    monkeypatch.setenv("AFTERFIX_DB", str(path))
    monkeypatch.setenv("AFTERFIX_OPERATOR_TOKEN", OPERATOR)
    monkeypatch.setenv("AFTERFIX_VIEWER_TOKEN", VIEWER)
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "1")
    conn = store.connect(str(path))
    try:
        store.reset_and_seed(conn)
        ev = ids(conn)
    finally:
        conn.close()
    saved = dict(app.dependency_overrides)
    saved_gate = main_module.investigation_gate
    gate = InvestigationGate()
    main_module.investigation_gate = gate
    app.state.investigation_gate = gate
    client = TestClient(app, headers={"Authorization": f"Bearer {OPERATOR}"})
    try:
        yield {"client": client, "path": str(path), "ids": ev}
    finally:
        client.close()
        app.dependency_overrides.clear(); app.dependency_overrides.update(saved)
        main_module.investigation_gate = saved_gate; app.state.investigation_gate = saved_gate


def fake(api, handler):
    f = FakeTransport(handler)
    app.dependency_overrides[get_investigation_client] = lambda: MetaClient(transport=f)
    return f


def investigate(api):
    r = api["client"].post("/incidents/INC-001/investigate", json={})
    assert r.status_code == 200, r.text
    return r.json()


def db(api, fn):
    conn = store.connect(api["path"])
    try:
        return fn(conn)
    finally:
        conn.close()


def counts(api):
    return db(api, lambda c: {
        "cases": c.execute("SELECT COUNT(*) FROM case_records").fetchone()[0],
        "audit": c.execute("SELECT COUNT(*) FROM audit_log").fetchone()[0],
        "stock": store.get_product(c, WIDGET)["stock"],
        "gadget": store.get_product(c, "GADGET-9")["stock"],
        "proposals": c.execute("SELECT COUNT(*) FROM proposals").fetchone()[0],
    })


def approve_apply(api, pid, snap, key):
    appr = api["client"].post(f"/proposals/{pid}/approve", json={"snapshot_hash": snap})
    assert appr.status_code == 200, appr.text
    appr_id = appr.json()["approval_id"] if "approval_id" in appr.json() else appr.json()["id"]
    applied = api["client"].post("/apply", json={"proposal_id": pid, "approval_id": appr_id, "idempotency_key": key})
    assert applied.status_code == 200, applied.text
    return appr.json(), applied.json()


def test_full_flow_case_verified_before_preserved(api):
    f = fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    assert res["status"] == "proposed" and res["case_id"] and res["case_recording_status"] == "recorded"
    pid, case_id = res["proposal_id"], res["case_id"]
    before_calls = len(f.payloads)
    rep = api["client"].get(f"/cases/{case_id}/report")
    assert rep.status_code == 200, rep.text
    rep = rep.json()
    assert rep["schema_version"] == 1 and rep["data_label"] == "synthetic"
    assert rep["before"]["evidence_binding"] == "bound"
    assert rep["before"]["product"]["stock"] == 100
    assert rep["before"]["patch"]  # exact raw patch retained
    assert rep["recovery_state"] == "proposed"
    assert rep["initial_preview"]["before_stock"] == 100
    assert rep["initial_preview"]["after_stock"] == 110
    assert '"verified"' in json.dumps(rep["initial_preview"])
    assert "0.1.0" in json.dumps(rep["initial_preview"])
    assert rep["investigation"]["hypothesis"]
    assert rep["investigation"]["usage"]["input_tokens"] > 0
    assert rep["investigation"]["usage"]["output_tokens"] > 0
    assert len(f.payloads) == before_calls
    prev = api["client"].get(f"/proposals/{pid}/preview").json()
    assert prev["before_stock"] == 100 and prev["after_stock"] == 110
    assert rep["initial_preview"]["proposal"]["snapshot_hash"] == prev["proposal"]["snapshot_hash"]
    assert rep["initial_preview"]["proposal"] == prev["proposal"]
    assert rep["initial_preview"]["rust_verification"]["verifier_version"] == "0.1.0"
    assert rep["initial_preview"]["rust_verification"]["snapshot_hash"] == prev["proposal"]["snapshot_hash"]
    assert rep["initial_preview"]["rust_verification"]["ledger_actual"] == 100
    assert rep["initial_preview"]["rust_verification"]["expected_stock"] == 110
    assert rep["initial_preview"]["rust_verification"]["outstanding_credit"] == rep["initial_preview"]["credit_qty"]
    appr, applied = approve_apply(api, pid, prev["proposal"]["snapshot_hash"], "case-test-apply-1")
    rep2 = api["client"].get(f"/cases/{case_id}/report").json()
    assert rep2["recovery_state"] == "verified", rep2
    assert rep2["verification_status"] == "passed"
    assert rep2["final_verification"]["pass"] is True
    assert rep2["final_verification"]["preservation_verified"] is True
    assert rep2["final_verification"]["checks"] and all(c["ok"] is True for c in rep2["final_verification"]["checks"])
    assert rep2["current"]["product"]["stock"] == 110
    assert rep2["before"]["product"]["stock"] == 100
    assert rep2["initial_preview"] == rep["initial_preview"]
    assert rep2["current"]["repair_events"]
    assert any(a.get("actor_id") == "operator" for a in rep2["audit"])
    assert db(api, lambda c: store.get_product(c, "GADGET-9")["stock"]) == 45
    assert any(e["kind"] == "restock" for e in rep2["current"]["events"])
    assert "idempotency" not in json.dumps(rep2).lower()
    assert len(f.payloads) == before_calls


def test_list_and_reload_new_connection_no_model(api):
    f = fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    calls = len(f.payloads)
    lst = api["client"].get("/incidents/INC-001/cases")
    assert lst.status_code == 200 and len(lst.json()["cases"]) == 1
    assert lst.json()["cases"][0]["case_id"] == res["case_id"]
    conn = store.connect(api["path"])
    try:
        rec = case_report.build_report(conn, res["case_id"])
    finally:
        conn.close()
    assert rec["case_id"] == res["case_id"] and rec["before"]["patch"]
    assert len(f.payloads) == calls
    assert api["client"].get("/incidents/INC-404/cases").status_code == 404
    assert api["client"].get("/cases/CASE-nope/report").status_code == 404


def test_needs_review_case_bound(api):
    final = make_final([], decision="needs_review", hypothesis="Insufficient evidence.")
    fake(api, lambda p, n: completed([message(json.dumps(final))]))
    res = investigate(api)
    assert res["status"] == "needs_review" and res["case_id"]
    rep = api["client"].get(f"/cases/{res['case_id']}/report").json()
    assert rep["recovery_state"] == "needs_review"
    assert rep["investigation"]["hypothesis"] == "Insufficient evidence."
    assert rep["final_verification"] is None and rep["verification_status"] == "not_run"


def test_viewer_get_anon_401_viewer_investigate_403(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    viewer = TestClient(app, headers={"Authorization": f"Bearer {VIEWER}"})
    anon = TestClient(app)
    try:
        assert viewer.get(f"/cases/{res['case_id']}/report").status_code == 200
        assert viewer.get("/incidents/INC-001/cases").status_code == 200
        assert viewer.post("/incidents/INC-001/investigate", json={}).status_code == 403
        assert anon.get(f"/cases/{res['case_id']}/report").status_code == 401
        assert anon.get("/incidents/INC-001/cases").status_code == 401
    finally:
        viewer.close(); anon.close()


def test_gets_do_not_write(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    before = counts(api)
    api["client"].get(f"/cases/{res['case_id']}/report")
    api["client"].get("/incidents/INC-001/cases")
    assert counts(api) == before


def test_reset_clears_cases(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    investigate(api)
    assert counts(api)["cases"] == 1
    assert api["client"].post("/admin/reset").status_code == 200
    assert counts(api)["cases"] == 0
    assert api["client"].get("/incidents/INC-001/cases").json()["cases"] == []


def test_persist_failure_sanitized_no_retry(api, monkeypatch):
    f = fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    def boom(*a, **k):
        raise RuntimeError("sensitive-db-path-marker")
    monkeypatch.setattr(case_report, "record_case", boom)
    r = api["client"].post("/incidents/INC-001/investigate", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "proposed" and body["case_id"] is None
    assert body["case_recording_status"] == "unavailable"
    assert "sensitive-db-path-marker" not in r.text
    assert len(f.payloads) == 3


def test_corrupt_and_cross_product_cases_needs_review(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    cid = res["case_id"]
    db(api, lambda c: (c.execute("UPDATE case_records SET investigation_json='{not json' WHERE case_id=?", (cid,)), c.commit()))
    rep = api["client"].get(f"/cases/{cid}/report")
    assert rep.status_code == 200 and rep.json()["recovery_state"] == "needs_review"
    assert "Traceback" not in rep.text
    db(api, lambda c: (c.execute("UPDATE case_records SET investigation_json=?, product_id='GADGET-9' WHERE case_id=?", ('{"status":"proposed"}', cid)), c.commit()))
    rep2 = api["client"].get(f"/cases/{cid}/report").json()
    assert rep2["recovery_state"] == "needs_review" and rep2["current"] is None


def test_changed_scope_binding_unavailable(api):
    conn = store.connect(api["path"])
    try:
        before, ok = case_report.capture_before(conn, "INC-001")
        assert ok and before["capture_id"]
        conn.execute("UPDATE products SET stock=99 WHERE product_id='WIDGET-1'"); conn.commit()
        result = {"status": "needs_review", "reason": "model_needs_review", "incident_id": "INC-001", "product_id": WIDGET, "read_event_ids": [], "evidence_event_ids": []}
        cid = case_report.record_case(conn, "operator", before, result)
        rep = case_report.build_report(conn, cid)
    finally:
        conn.close()
    assert rep["before"]["evidence_binding"] == "unavailable"
    assert rep["before"]["binding_reason"] == "changed_scope"
    assert rep["before"]["caveat"]


def test_current_tamper_after_apply_not_verified(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    pid = res["proposal_id"]
    prev = api["client"].get(f"/proposals/{pid}/preview").json()
    approve_apply(api, pid, prev["proposal"]["snapshot_hash"], "case-test-tamper")
    assert api["client"].get(f"/cases/{res['case_id']}/report").json()["recovery_state"] == "verified"
    db(api, lambda c: (c.execute("UPDATE products SET stock=105 WHERE product_id='WIDGET-1'"), c.commit()))
    rep = api["client"].get(f"/cases/{res['case_id']}/report").json()
    assert rep["recovery_state"] == "applied_unverified"
    assert rep["verification_status"] == "failed"


def test_bounds_usage_and_exact_verification_unit():
    out = case_report._bounded({"usage": {"requests": 3, "input_tokens": 12, "output_tokens": 7, "total_tokens": 19}})
    assert out == {"usage": {"requests": 3, "input_tokens": 12, "output_tokens": 7, "total_tokens": 19}}
    inv = case_report._whitelist_result({"status": "needs_review", "usage": {"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": None}})
    assert inv["usage"] == {"requests": 1, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost": None}
    assert case_report._bounded({"patch": "p" * 5000})["patch"] == "p" * 5000
    with pytest.raises(case_report.CaseInvalid):
        case_report._bounded({"x": ["z" * 600000]})
    with pytest.raises(case_report.CaseInvalid):
        case_report._bounded({"patch": "p" * 8001})
    # Exact verify_product contract only.
    assert case_report._verification_passed({"pass": False, "preservation_verified": False, "checks": [{"ok": True}]}) is False
    assert case_report._verification_passed({"pass": True, "preservation_verified": True, "checks": [{"ok": True}]}) is True
    assert case_report._verification_passed({"pass": True, "preservation_verified": True, "checks": []}) is False
    assert case_report._verification_passed({"passed": True, "status": "verified", "checks": [{"ok": True}]}) is False
    assert case_report._verification_passed({"pass": True, "preservation_verified": False, "checks": [{"ok": True}]}) is False
    assert case_report._verification_passed({"pass": True, "preservation_verified": True, "checks": [{"ok": "yes"}]}) is False


def test_patch_between_4000_and_8000_retained_and_over_rejected(api):
    conn = store.connect(api["path"])
    try:
        before, ok = case_report.capture_before(conn, "INC-001")
        assert ok
        assert len(before["patch"]) <= 8000
        before["patch"] = before["raw_patch"] = "p" * 5000
        # Fingerprint is over product/events only, so this exact patch is stored.
        result = {"status": "needs_review", "reason": "x", "incident_id": "INC-001", "product_id": WIDGET, "read_event_ids": before["event_ids"], "selected_event_ids": before["event_ids"], "evidence_event_ids": []}
        cid = case_report.record_case(conn, "operator", before, result)
        rep = case_report.build_report(conn, cid)
        assert rep["before"]["patch"] == "p" * 5000
    finally:
        conn.close()
    with pytest.raises(case_report.CaseInvalid):
        case_report._bounded({"raw_patch": "p" * 8001})


def test_idempotent_retry_and_mismatched_retry_rejected(api):
    conn = store.connect(api["path"])
    try:
        before, ok = case_report.capture_before(conn, "INC-001")
        assert ok
        result = {"status": "needs_review", "reason": "x", "incident_id": "INC-001", "product_id": WIDGET, "read_event_ids": [], "evidence_event_ids": [], "hypothesis": "h1"}
        cid1 = case_report.record_case(conn, "operator", before, result)
        cid2 = case_report.record_case(conn, "operator", before, result)
        assert cid1 == cid2
        assert conn.execute("SELECT COUNT(*) FROM case_records").fetchone()[0] == 1
        changed = dict(result, hypothesis="different")
        with pytest.raises(case_report.CaseInvalid):
            case_report.record_case(conn, "operator", before, changed)
    finally:
        conn.close()


def test_cross_scope_result_never_exposes_other_product(api):
    conn = store.connect(api["path"])
    try:
        before, ok = case_report.capture_before(conn, "INC-001")
        assert ok
        forged = {"status": "needs_review", "reason": "x", "incident_id": "INC-001", "product_id": "GADGET-9", "read_event_ids": [], "evidence_event_ids": []}
        cid = case_report.record_case(conn, "operator", before, forged)
        rep = case_report.build_report(conn, cid)
    finally:
        conn.close()
    assert rep["product_id"] == WIDGET
    assert rep["current"]["product"]["product_id"] == WIDGET
    assert rep["before"]["evidence_binding"] == "unavailable"


def test_proposed_stray_approval_still_proposed(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    pid = res["proposal_id"]
    db(api, lambda c: (c.execute("INSERT INTO approvals(id, proposal_id, snapshot_hash, created_at, actor_id) VALUES ('APPR-STRAY', ?, 'x', 1, 'operator')", (pid,)), c.commit()))
    rep = api["client"].get(f"/cases/{res['case_id']}/report").json()
    assert rep["current_proposal"]["status"] == "proposed"
    assert rep["recovery_state"] == "proposed"
    assert rep["final_verification"] is None and rep["verification_status"] == "not_run"


def test_corrupt_version_deep_before_preview_duplicate_nan(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    cid = res["case_id"]
    def set_before(mut):
        def fn(c):
            row = c.execute("SELECT before_json FROM case_records WHERE case_id=?", (cid,)).fetchone()
            b = json.loads(row[0]); mut(b)
            c.execute("UPDATE case_records SET before_json=? WHERE case_id=?", (json.dumps(b), cid)); c.commit()
        db(api, fn)
    original = api["client"].get(f"/cases/{cid}/report").json()
    set_before(lambda b: b["events"][0].__setitem__("product_id", "GADGET-9"))
    assert api["client"].get(f"/cases/{cid}/report").json()["recovery_state"] == "needs_review"
    db(api, lambda c: (c.execute("UPDATE case_records SET before_json=? WHERE case_id=?", (json.dumps(original["before"]), cid)), c.execute("UPDATE case_records SET schema_version=99 WHERE case_id=?", (cid,)), c.commit()))
    assert api["client"].get(f"/cases/{cid}/report").json()["recovery_state"] == "needs_review"
    db(api, lambda c: (c.execute("UPDATE case_records SET schema_version=1, investigation_json='{\"status\": \"needs_review\", \"status\": \"proposed\"}' WHERE case_id=?", (cid,)), c.commit()))
    assert api["client"].get(f"/cases/{cid}/report").json()["recovery_state"] == "needs_review"
    db(api, lambda c: (c.execute("UPDATE case_records SET investigation_json='{\"status\": NaN}' WHERE case_id=?", (cid,)), c.commit()))
    assert api["client"].get(f"/cases/{cid}/report").json()["recovery_state"] == "needs_review"


def test_audit_secret_detail_not_exposed(api):
    fake(api, tool_then_final(["get_patch", "read_events"], api["ids"]["all"]))
    res = investigate(api)
    pid = res["proposal_id"]
    secret = "AUDIT-SECRET-MARKER-123"
    detail = json.dumps({"idempotency_key": secret, "authorization": secret, "snapshot_hash": "abc123", "actor_id": "operator"})
    db(api, lambda c: (c.execute("INSERT INTO audit_log(ts, action, proposal_id, detail, actor_id) VALUES (1, 'approve', ?, ?, 'operator')", (pid, detail)), c.commit()))
    rep = api["client"].get(f"/cases/{res['case_id']}/report").json()
    assert secret not in json.dumps(rep)
    assert "idempotency" not in json.dumps(rep).lower()
    assert any(isinstance(a["detail"], dict) and a["detail"].get("available") is False for a in rep["audit"])


def test_open_write_transaction_rejected_without_backup(api):
    conn = store.connect(api["path"])
    try:
        conn.execute("INSERT INTO products(product_id, name, stock) VALUES ('TMP-1', 'Tmp', 1)")
        assert conn.in_transaction
        before, ok = case_report.capture_before(conn, "INC-001")
        assert ok is False and before["available"] is False
        with pytest.raises(case_report.CaseInvalid):
            case_report.record_case(conn, "operator", before, {"status": "needs_review", "incident_id": "INC-001", "product_id": WIDGET})
        conn.rollback()
    finally:
        conn.close()


def test_fingerprint_empty_null_regression(api):
    def setup(c):
        c.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note, reversed_ids, repair_proposal_id) VALUES ('WIDGET-1','note',0,NULL,1,NULL,'',NULL)")
        c.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note, reversed_ids, repair_proposal_id) VALUES ('WIDGET-1','note',0,'',2,'','[]','')")
        c.commit()
        prod = store.get_product(c, WIDGET)
        rows = [dict(r) for r in c.execute("SELECT * FROM events WHERE product_id='WIDGET-1' ORDER BY id")]
        captured = [{**r, 'note': r['note'] if r['note'] is not None else ''} for r in rows]
        return store.fingerprint(c, WIDGET), store.fingerprint_payload({'product_id': WIDGET, 'stock': prod['stock']}, captured)
    actual, helper = db(api, setup)
    assert helper == actual


def test_audit_actual_values_and_corruptions_invalid(api):
    fake(api, tool_then_final(['get_patch', 'read_events'], api['ids']['all']))
    res = investigate(api); cid, pid = res['case_id'], res['proposal_id']
    prev = api['client'].get(f'/proposals/{pid}/preview').json()
    snap = prev['proposal']['snapshot_hash']
    approve_apply(api, pid, snap, 'case-test-audit-values')
    rep = api['client'].get(f'/cases/{cid}/report').json()
    assert rep['recovery_state'] == 'verified'
    by_action = {a['action']: a for a in rep['audit'] if isinstance(a.get('detail'), dict) and a['detail'].get('rust_verification')}
    assert by_action['approve']['detail']['rust_verification']['verifier_version'] == '0.1.0'
    assert by_action['approve']['detail']['rust_verification']['snapshot_hash'] == snap
    assert by_action['approve']['detail']['actor_id'] == 'operator'
    assert by_action['apply']['detail']['before'] == 100 and by_action['apply']['detail']['after'] == 110
    assert by_action['apply']['detail']['credit'] == rep['initial_preview']['credit_qty']
    assert by_action['apply']['detail']['reversed'] == sorted(rep['initial_preview']['reverse_event_ids'])
    assert by_action['apply']['detail']['rust_verification']['ledger_actual'] == 100 == prev['before_stock'] == rep['initial_preview']['before_stock']
    assert by_action['apply']['detail']['rust_verification']['expected_stock'] == 110 == prev['after_stock'] == rep['initial_preview']['after_stock']
    assert by_action['apply']['detail']['rust_verification']['outstanding_credit'] == rep['initial_preview']['credit_qty'] == by_action['apply']['detail']['credit']
    assert by_action['apply']['detail']['rust_verification']['reverse_event_ids'] == sorted(rep['initial_preview']['reverse_event_ids'])
    assert by_action['apply']['detail']['rust_verification']['snapshot_hash'] == snap
    assert by_action['approve']['detail']['rust_verification']['ledger_actual'] == 100
    assert by_action['approve']['detail']['rust_verification']['expected_stock'] == 110
    secret_detail = json.dumps({'computed': {'authorization': 'SYNTHETIC-SECRET-MARKER'}, 'snapshot_hash': 'abc'})
    db(api, lambda c: (c.execute("INSERT INTO audit_log(ts, action, proposal_id, detail, actor_id) VALUES (9,'approve',?,?, 'operator')", (pid, secret_detail)), c.commit()))
    rep_secret = api['client'].get(f'/cases/{cid}/report').json()
    assert 'SYNTHETIC-SECRET-MARKER' not in json.dumps(rep_secret) and 'computed' not in json.dumps(rep_secret)
    def blobs(c):
        r = c.execute('SELECT before_json, investigation_json, initial_preview_json FROM case_records WHERE case_id=?', (cid,)).fetchone(); return r[0], r[1], r[2]
    orig = db(api, blobs)
    def set_blob(col, value):
        db(api, lambda c: (c.execute(f'UPDATE case_records SET {col}=? WHERE case_id=?', (value, cid)), c.commit()))
    def mutated_preview(fn):
        p = json.loads(orig[2]); fn(p); return json.dumps(p)
    for fn in (lambda p: p['proposal'].__setitem__('snapshot_hash', 'bad'), lambda p: p['proposal'].__setitem__('product_id', 'GADGET-9'), lambda p: p['rust_verification'].__setitem__('verifier_version', '9.9'), lambda p: p['rust_verification'].__setitem__('snapshot_hash', 'bad'), lambda p: p.__setitem__('unknown_nested', {'x': 1})):
        set_blob('initial_preview_json', mutated_preview(fn))
        bad = api['client'].get(f'/cases/{cid}/report').json(); assert bad['record_status'] == 'invalid' and bad['recovery_state'] == 'needs_review' and bad['before'] is None
    set_blob('initial_preview_json', orig[2])
    inv = json.loads(orig[1]); inv['observations'] = [{'text': 'x', 'evidence_event_ids': [999999]}]
    set_blob('investigation_json', json.dumps(inv))
    bad = api['client'].get(f'/cases/{cid}/report').json(); assert bad['record_status'] == 'invalid' and bad['recovery_state'] == 'needs_review'
    set_blob('investigation_json', orig[1])
    assert api['client'].get(f'/cases/{cid}/report').json()['recovery_state'] == 'verified'
    db(api, lambda c: (c.execute("UPDATE products SET stock=105 WHERE product_id='WIDGET-1'"), c.commit()))
    tampered = api['client'].get(f'/cases/{cid}/report').json(); assert tampered['verification_status'] == 'failed' and tampered['recovery_state'] == 'applied_unverified'


def test_changed_binding_not_actionable(api):
    fake(api, tool_then_final(['get_patch', 'read_events'], api['ids']['all']))
    conn = store.connect(api['path'])
    try:
        before, ok = case_report.capture_before(conn, 'INC-001'); assert ok
    finally:
        conn.close()
    res = investigate(api); pid = res['proposal_id']; assert pid
    conn = store.connect(api['path'])
    try:
        conn.execute("UPDATE products SET stock=99 WHERE product_id='WIDGET-1'"); conn.commit()
        cid = case_report.record_case(conn, 'operator', before, res)
        rep = case_report.build_report(conn, cid)
    finally:
        conn.close()
    assert rep['recovery_state'] == 'needs_review' and rep['initial_preview'] is None and rep['before']['evidence_binding'] == 'unavailable'
    assert rep['final_verification'] is None and rep['verification_status'] == 'not_run'


def test_capture_over_200_events_unavailable(api):
    def add(c):
        import time as _t
        for i in range(205):
            c.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES ('WIDGET-1','note',0,NULL,?, '')", (int(_t.time()) + i,))
        c.commit()
    db(api, add)
    conn = store.connect(api["path"])
    try:
        before, ok = case_report.capture_before(conn, "INC-001")
        assert ok is False and before["available"] is False
        assert before["reason"] == "oversized_or_missing_scope"
        assert before["event_count"] == 201  # LIMIT+1 probe, not unbounded fetch
        cid = case_report.record_case(conn, 'operator', before, {'status': 'needs_review', 'reason': 'x', 'incident_id': 'INC-001', 'product_id': WIDGET, 'read_event_ids': [], 'evidence_event_ids': []})
        rep = case_report.build_report(conn, cid)
    finally:
        conn.close()
    assert rep['recovery_state'] == 'needs_review' and rep['before']['available'] is False and rep['before']['event_count'] == 201


def test_report_contract_mutations_baseline_and_marker(api):
    fake(api, tool_then_final(['get_patch', 'read_events'], api['ids']['all']))
    res = investigate(api); cid, pid = res['case_id'], res['proposal_id']
    prev = api['client'].get(f'/proposals/{pid}/preview').json(); snap = prev['proposal']['snapshot_hash']
    approve_apply(api, pid, snap, 'case-test-contract-mutations')
    rep = api['client'].get(f'/cases/{cid}/report').json(); assert rep['recovery_state'] == 'verified'
    base = rep['approvals'][0]['baseline']
    assert base['available'] is True and base['proposal_id'] == pid and base['product_id'] == WIDGET and base['snapshot_hash'] == snap
    assert base['before_stock'] == 100 and base['after_stock'] == 110 and base['credit_qty'] == rep['initial_preview']['credit_qty']
    assert base['reverse_event_ids'] == sorted(rep['initial_preview']['reverse_event_ids']) and base['restock_total'] == 20
    orig = db(api, lambda c: c.execute('SELECT before_json, investigation_json, initial_preview_json FROM case_records WHERE case_id=?', (cid,)).fetchone())
    orig = (orig[0], orig[1], orig[2])
    orig_baseline = db(api, lambda c: c.execute('SELECT baseline_json FROM approvals WHERE proposal_id=?', (pid,)).fetchone()[0])
    def set_blob(col, val): db(api, lambda c: (c.execute(f'UPDATE case_records SET {col}=? WHERE case_id=?', (val, cid)), c.commit()))
    def mut(col, fn):
        vals = {'before_json': orig[0], 'investigation_json': orig[1], 'initial_preview_json': orig[2]}; obj = json.loads(vals[col]); fn(obj); set_blob(col, json.dumps(obj))
    def restore():
        set_blob('before_json', orig[0]); set_blob('investigation_json', orig[1]); set_blob('initial_preview_json', orig[2])
        db(api, lambda c: (c.execute('UPDATE approvals SET baseline_json=? WHERE proposal_id=?', (orig_baseline, pid)), c.commit()))
    bad_mutations = [
        ('initial_preview_json', lambda o: o['rust_verification']['checks'][0].__setitem__('ok', False)),
        ('initial_preview_json', lambda o: o.__setitem__('simulation_pass', False)),
        ('initial_preview_json', lambda o: o['simulation_checks'][0].__setitem__('ok', False)),
        ('investigation_json', lambda o: o.__setitem__('read_event_ids', [])),
        ('investigation_json', lambda o: o['proposal'].__setitem__('credit_qty', 999)),
        ('investigation_json', lambda o: o['proposal'].__setitem__('reverse_event_ids', [999999])),
        ('investigation_json', lambda o: o['proposal'].__setitem__('credential', 'x')),
        ('initial_preview_json', lambda o: o['proposal'].__setitem__('credit_qty', 999)),
    ]
    for col, fn in bad_mutations:
        mut(col, fn); bad = api['client'].get(f'/cases/{cid}/report').json()
        assert bad['record_status'] == 'invalid' and bad['recovery_state'] == 'needs_review', (col, bad)
        restore(); assert api['client'].get(f'/cases/{cid}/report').json()['recovery_state'] == 'verified'
    # Nested unknown in approval baseline: safely unavailable, no exposure, state unchanged.
    parsed = json.loads(orig_baseline); parsed['products'] = {'credential': 'BASELINE-SECRET'}; 
    db(api, lambda c: (c.execute('UPDATE approvals SET baseline_json=? WHERE proposal_id=?', (json.dumps(parsed), pid)), c.commit()))
    rep2 = api['client'].get(f'/cases/{cid}/report').json()
    assert rep2['approvals'][0]['baseline'] == {'available': False, 'reason': 'malformed_baseline'} and 'BASELINE-SECRET' not in json.dumps(rep2)
    restore()
    # Explicit genuine preview-unavailable marker: valid stored failure, needs_review.
    set_blob('initial_preview_json', json.dumps({'available': False, 'status': 'unavailable', 'reason': 'initial_preview_unavailable'}))
    rep3 = api['client'].get(f'/cases/{cid}/report').json()
    assert rep3['record_status'] == 'ok' and rep3['recovery_state'] == 'needs_review' and rep3['initial_preview']['available'] is False and rep3['final_verification'] is None
    restore(); assert api['client'].get(f'/cases/{cid}/report').json()['recovery_state'] == 'verified'
