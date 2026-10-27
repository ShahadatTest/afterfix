"""Recovery integrity tests (domain DB APIs used for synthetic variations)."""
import concurrent.futures
import inspect
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

from app import domain, recovery, schemas, store


@pytest.fixture
def db_file(tmp_path):
    return str(tmp_path / "t.db")


def open_db(db_file):
    return store.connect(db_file)


def make_product(conn, pid, opening, incident_id="INC-T"):
    conn.execute("INSERT INTO products(product_id, name, stock) VALUES (?,?,?)", (pid, pid, opening))
    conn.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES (?,?,?,?,?,?)", (pid, "init", opening, None, int(time.time()), "opening record"))
    conn.execute("INSERT OR IGNORE INTO incidents(id, product_id, description, status) VALUES (?,?,?,?)", (incident_id, pid, f"discrepancy report for {pid}", "open"))
    conn.commit()
    return incident_id


def full_flow(conn, incident, product, evidence):
    p = recovery.build_proposal(conn, incident, product, evidence)
    assert p["status"] == "proposed"
    prev = recovery.preview_proposal(conn, p["proposal_id"])
    assert prev["simulation_pass"]
    a = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"])
    return p, a


def order_deduction_ids(conn, product):
    rows = store.list_product_events(conn, product)
    return [r["id"] for r in rows if r["kind"] == "order_deduction"]


def test_canonical_100_to_110_restock_retained(db_file):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        rows = store.list_product_events(conn, "WIDGET-1")
        ids = [r["id"] for r in rows if r["kind"] == "order_deduction"]
        assert len(ids) == 2
        p, a = full_flow(conn, "INC-001", "WIDGET-1", [r["id"] for r in rows if r["product_id"] == "WIDGET-1" and r["kind"] == "order_deduction"])
        assert p["credit_qty"] == 10
        before = store.get_product(conn, "WIDGET-1")["stock"]
        assert before == 100
        res = recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "key-canonical-1")
        assert res["after_stock"] == 110
        v = recovery.verify_product(conn, "WIDGET-1", p["proposal_id"])
        assert v["pass"]
        assert store.get_product(conn, "GADGET-9")["stock"] == 45
    finally:
        conn.close()


def test_varied_quantities_and_multiple_orders(db_file):
    conn = open_db(db_file)
    try:
        make_product(conn, "P1", 200, "INC-V")
        domain.process_order_buggy(conn, "P1", "O-1", 7)
        domain.process_order_buggy(conn, "P1", "O-1", 7)
        domain.process_order_buggy(conn, "P1", "O-2", 5)
        domain.process_order_buggy(conn, "P1", "O-2", 5)
        domain.process_order_buggy(conn, "P1", "O-2", 5)
        domain.process_restock(conn, "P1", 30)
        make_product(conn, "OTHER", 50, "INC-OTHER")
        domain.process_order_fixed(conn, "OTHER", "OO-1", 5)
        rows = store.list_product_events(conn, "P1")
        ev = [r["id"] for r in rows if r["kind"] == "order_deduction"]
        p = recovery.build_proposal(conn, "INC-V", "P1", ev)
        assert p["status"] == "proposed"
        assert p["credit_qty"] == 17
        p2, a2 = full_flow(conn, "INC-V", "P1", ev)
        res = recovery.apply_proposal(conn, p2["proposal_id"], a2["approval_id"], "k-varied")
        assert res["credit_qty"] == 17
        v = recovery.verify_product(conn, "P1", p2["proposal_id"])
        assert v["pass"]
        assert store.get_product(conn, "OTHER")["stock"] == 45
    finally:
        conn.close()


def test_single_extra_without_original_abstains(db_file):
    conn = open_db(db_file)
    try:
        make_product(conn, "P2", 100, "INC-S")
        domain.process_order_buggy(conn, "P2", "O-9", 10)
        b = domain.process_order_buggy(conn, "P2", "O-9", 10)
        p = recovery.build_proposal(conn, "INC-S", "P2", [b])
        assert p["status"] == "needs_review"
    finally:
        conn.close()


def test_missing_event_and_null_order_and_contradictory_qty(db_file):
    conn = open_db(db_file)
    try:
        make_product(conn, "P3", 100, "INC-M")
        x = domain.process_order_buggy(conn, "P3", "O-1", 10)
        domain.process_order_buggy(conn, "P3", "O-1", 10)
        assert recovery.build_proposal(conn, "INC-M", "P3", [x, 99999])["status"] == "needs_review"
        assert recovery.build_proposal(conn, "INC-M", "P3", [])["status"] == "needs_review"
        conn.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES (?,?,?,?,?,?)", ("P3", "order_deduction", 10, None, int(time.time()), "observation"))
        conn.execute("UPDATE products SET stock = stock - 10 WHERE product_id='P3'")
        conn.commit()
        rows = store.list_product_events(conn, "P3")
        ev = [r["id"] for r in rows]
        assert recovery.build_proposal(conn, "INC-M", "P3", ev)["status"] == "needs_review"
    finally:
        conn.close()
    conn2 = open_db(db_file)
    try:
        make_product(conn2, "P4", 100, "INC-C")
        conn2.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES (?,?,?,?,?,?)", ("P4", "order_deduction", 10, "O-X", int(time.time()), "observation"))
        conn2.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES (?,?,?,?,?,?)", ("P4", "order_deduction", 12, "O-X", int(time.time()), "observation"))
        conn2.execute("UPDATE products SET stock = stock - 22 WHERE product_id='P4'")
        conn2.commit()
        rows = store.list_product_events(conn2, "P4")
        assert recovery.build_proposal(conn2, "INC-C", "P4", [r["id"] for r in rows if r["kind"] == "order_deduction"])["status"] == "needs_review"
    finally:
        conn2.close()


def test_wrong_incident_product(db_file):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        ev = order_deduction_ids(conn, "WIDGET-1")
        p = recovery.build_proposal(conn, "INC-001", "GADGET-9", ev)
        assert p["status"] == "needs_review"
    finally:
        conn.close()


def test_stale_after_approval_and_unauthorised_apply(db_file):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        ev = order_deduction_ids(conn, "WIDGET-1")
        p, a = full_flow(conn, "INC-001", "WIDGET-1", ev)
        domain.process_restock(conn, "WIDGET-1", 5)
        with pytest.raises(recovery.StaleStateError):
            recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "k-stale")
        with pytest.raises(recovery.ApprovalError):
            recovery.apply_proposal(conn, p["proposal_id"], "APPR-nope", "k-bad")
    finally:
        conn.close()


def test_same_and_different_key_repeat_and_wrong_key_reuse(db_file):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        ev = order_deduction_ids(conn, "WIDGET-1")
        p, a = full_flow(conn, "INC-001", "WIDGET-1", ev)
        r1 = recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "same-key")
        r2 = recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "same-key")
        assert r1 == r2 and r1["status"] == "applied"
        r3 = recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "other-key")
        assert r3["status"] == "already_applied"
        assert store.get_product(conn, "WIDGET-1")["stock"] == 110
        make_product(conn, "PX", 100, "INC-PX")
        domain.process_order_buggy(conn, "PX", "O-PX", 5)
        domain.process_order_buggy(conn, "PX", "O-PX", 5)
        rows2 = store.list_product_events(conn, "PX")
        p2 = recovery.build_proposal(conn, "INC-PX", "PX", [r["id"] for r in rows2 if r["kind"] == "order_deduction"])
        recovery.preview_proposal(conn, p2["proposal_id"])
        a2 = recovery.approve_proposal(conn, p2["proposal_id"], p2["snapshot_hash"])
        with pytest.raises(recovery.ApprovalError):
            recovery.apply_proposal(conn, p2["proposal_id"], a2["approval_id"], "same-key")
    finally:
        conn.close()


def test_preview_leaves_live_db_untouched(db_file):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        fp_before = store.fingerprint(conn, "WIDGET-1")
        stock_before = store.get_product(conn, "WIDGET-1")["stock"]
        n_before = len(store.list_product_events(conn, "WIDGET-1"))
        p = recovery.build_proposal(conn, "INC-001", "WIDGET-1", order_deduction_ids(conn, "WIDGET-1"))
        recovery.preview_proposal(conn, p["proposal_id"])
        assert store.fingerprint(conn, "WIDGET-1") == fp_before
        assert store.get_product(conn, "WIDGET-1")["stock"] == stock_before
        assert len(store.list_product_events(conn, "WIDGET-1")) == n_before
    finally:
        conn.close()


def test_rollback_on_write_failure_via_trigger(db_file):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        ev = order_deduction_ids(conn, "WIDGET-1")
        p, a = full_flow(conn, "INC-001", "WIDGET-1", ev)
        stock_before = store.get_product(conn, "WIDGET-1")["stock"]
        n_events_before = len(store.list_product_events(conn, "WIDGET-1"))
        conn.execute("CREATE TRIGGER fail_repair BEFORE INSERT ON events WHEN NEW.kind='repair_credit' BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
        conn.commit()
        try:
            with pytest.raises(Exception):
                recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "k-boom")
        finally:
            conn.execute("DROP TRIGGER IF EXISTS fail_repair")
            conn.commit()
        assert store.get_product(conn, "WIDGET-1")["stock"] == stock_before
        assert len(store.list_product_events(conn, "WIDGET-1")) == n_events_before
        assert recovery._get_proposal(conn, p["proposal_id"])["status"] == "approved"
        assert conn.execute("SELECT COUNT(*) AS c FROM idempotency_keys WHERE idempotency_key='k-boom'").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM events WHERE kind='repair_credit'").fetchone()["c"] == 0
    finally:
        conn.close()


def test_rollback_on_audit_failure(db_file, monkeypatch):
    conn = open_db(db_file)
    try:
        store.reset_and_seed(conn)
        ev = order_deduction_ids(conn, "WIDGET-1")
        p, a = full_flow(conn, "INC-001", "WIDGET-1", ev)
        stock_before = store.get_product(conn, "WIDGET-1")["stock"]
        n_events_before = len(store.list_product_events(conn, "WIDGET-1"))

        def bad_audit(*args, **kwargs):
            raise RuntimeError("audit down")
        monkeypatch.setattr(store, "audit", bad_audit)
        with pytest.raises(RuntimeError):
            recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "k-boom2")
        assert store.get_product(conn, "WIDGET-1")["stock"] == stock_before
        assert len(store.list_product_events(conn, "WIDGET-1")) == n_events_before
        assert recovery._get_proposal(conn, p["proposal_id"])["status"] == "approved"
        assert conn.execute("SELECT COUNT(*) AS c FROM idempotency_keys WHERE idempotency_key='k-boom2'").fetchone()["c"] == 0
        assert conn.execute("SELECT COUNT(*) AS c FROM events WHERE kind='repair_credit'").fetchone()["c"] == 0
    finally:
        conn.close()


def test_parallel_race_same_proposal_different_keys(db_file):
    setup = open_db(db_file)
    try:
        store.reset_and_seed(setup)
        ev = order_deduction_ids(setup, "WIDGET-1")
        p, a = full_flow(setup, "INC-001", "WIDGET-1", ev)
        pid, aid = p["proposal_id"], a["approval_id"]
    finally:
        setup.close()
    barrier = threading.Barrier(2)

    def worker(key):
        c = store.connect(db_file)
        try:
            barrier.wait(timeout=10)
            return recovery.apply_proposal(c, pid, aid, key)
        finally:
            c.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(worker, "race-key-1")
        f2 = ex.submit(worker, "race-key-2")
        r1 = f1.result(timeout=15)
        r2 = f2.result(timeout=15)
    statuses = sorted([r1["status"], r2["status"]])
    assert statuses == ["already_applied", "applied"]
    check = open_db(db_file)
    try:
        assert store.get_product(check, "WIDGET-1")["stock"] == 110
        assert check.execute("SELECT COUNT(*) AS c FROM events WHERE kind='repair_credit'").fetchone()["c"] == 1
        assert recovery.verify_product(check, "WIDGET-1", pid)["pass"]
    finally:
        check.close()


def test_parallel_race_two_approved_proposals_same_snapshot(db_file):
    setup = open_db(db_file)
    try:
        store.reset_and_seed(setup)
        ev = order_deduction_ids(setup, "WIDGET-1")
        p1 = recovery.build_proposal(setup, "INC-001", "WIDGET-1", ev)
        p2 = recovery.build_proposal(setup, "INC-001", "WIDGET-1", ev)
        assert p1["status"] == "proposed" and p2["status"] == "proposed"
        assert p1["snapshot_hash"] == p2["snapshot_hash"]
        a1 = recovery.approve_proposal(setup, p1["proposal_id"], p1["snapshot_hash"])
        a2 = recovery.approve_proposal(setup, p2["proposal_id"], p2["snapshot_hash"])
        ids = (p1["proposal_id"], a1["approval_id"], p2["proposal_id"], a2["approval_id"])
    finally:
        setup.close()
    pid1, aid1, pid2, aid2 = ids
    barrier = threading.Barrier(2)

    def worker(pid, aid, key):
        c = store.connect(db_file)
        try:
            barrier.wait(timeout=10)
            try:
                return recovery.apply_proposal(c, pid, aid, key)
            except recovery.StaleStateError as e:
                return {"status": "stale", "error": str(e)}
        finally:
            c.close()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as ex:
        f1 = ex.submit(worker, pid1, aid1, "dup-race-1")
        f2 = ex.submit(worker, pid2, aid2, "dup-race-2")
        r1 = f1.result(timeout=15)
        r2 = f2.result(timeout=15)
    applied = [r for r in (r1, r2) if r["status"] == "applied"]
    stale = [r for r in (r1, r2) if r["status"] == "stale"]
    assert len(applied) == 1 and len(stale) == 1
    check = open_db(db_file)
    try:
        assert store.get_product(check, "WIDGET-1")["stock"] == 110
        assert check.execute("SELECT COUNT(*) AS c FROM events WHERE kind='repair_credit'").fetchone()["c"] == 1
    finally:
        check.close()


def test_fixed_handler_dedupes_and_rejects_conflict(db_file):
    conn = open_db(db_file)
    try:
        make_product(conn, "PF", 100, "INC-PF")
        r1 = domain.process_order_fixed(conn, "PF", "BIZ-1", 8)
        assert r1["deduped"] is False
        r2 = domain.process_order_fixed(conn, "PF", "BIZ-1", 8)
        assert r2["deduped"] is True and r2["event_id"] == r1["event_id"]
        assert store.get_product(conn, "PF")["stock"] == 92
        with pytest.raises(ValueError):
            domain.process_order_fixed(conn, "PF", "BIZ-1", 9)
        assert store.get_product(conn, "PF")["stock"] == 92
    finally:
        conn.close()


def test_manifest_has_no_apply_approve_reset():
    names = {t["name"] for t in schemas.TOOL_MANIFEST}
    assert "approve" not in names and "apply" not in names and "reset" not in names
    assert {"inspect_incident", "inspect_evidence", "preview", "verify"} <= names


def test_no_restock_required_and_prior_repair_not_reused(db_file):
    conn = open_db(db_file)
    try:
        make_product(conn, "PN", 100, "INC-PN")
        domain.process_order_buggy(conn, "PN", "O-N", 6)
        domain.process_order_buggy(conn, "PN", "O-N", 6)
        rows = store.list_product_events(conn, "PN")
        ev = [r["id"] for r in rows if r["kind"] == "order_deduction"]
        p, a = full_flow(conn, "INC-PN", "PN", ev)
        res = recovery.apply_proposal(conn, p["proposal_id"], a["approval_id"], "k-norestock")
        assert res["after_stock"] == 100 - 12 + 6
        assert recovery.verify_product(conn, "PN", p["proposal_id"])["pass"]
        p2 = recovery.build_proposal(conn, "INC-PN", "PN", ev)
        assert p2["status"] == "needs_review"
    finally:
        conn.close()


def test_approve_rejects_needs_review_and_mismatched_fingerprint(db_file):
    conn = open_db(db_file)
    try:
        make_product(conn, "P2", 100, "INC-S")
        domain.process_order_buggy(conn, "P2", "O-9", 10)
        b = domain.process_order_buggy(conn, "P2", "O-9", 10)
        bad = recovery.build_proposal(conn, "INC-S", "P2", [b])
        assert bad["status"] == "needs_review"
        with pytest.raises(recovery.ApprovalError):
            recovery.approve_proposal(conn, bad["proposal_id"], bad["snapshot_hash"])
        store.reset_and_seed(conn)
        ev = order_deduction_ids(conn, "WIDGET-1")
        p = recovery.build_proposal(conn, "INC-001", "WIDGET-1", ev)
        with pytest.raises(recovery.StaleStateError):
            recovery.approve_proposal(conn, p["proposal_id"], "0" * 64)
    finally:
        conn.close()


def test_inspect_patch_exposes_current_handler_source():
    text = domain.get_patch_text()
    src_buggy = inspect.getsource(domain.process_order_buggy)
    src_fixed = inspect.getsource(domain.process_order_fixed)
    assert "process_order_buggy" in text and "process_order_fixed" in text
    assert "BEGIN IMMEDIATE" in text
    for line in src_fixed.splitlines():
        s = line.strip()
        if len(s) > 8:
            assert s in text or s.strip("+- ") in text
            break
    assert src_buggy.strip().splitlines()[0].strip() in text


def test_api_smoke_flow_isolated_db(tmp_path, monkeypatch):
    dbp = str(tmp_path / "api.db")
    monkeypatch.setenv("AFTERFIX_DB", dbp)
    monkeypatch.setenv("AFTERFIX_OPERATOR_TOKEN", "operator-token-for-tests-1234567890abc")
    monkeypatch.setenv("AFTERFIX_VIEWER_TOKEN", "viewer-token-for-tests-1234567890abcd")
    monkeypatch.setenv("AFTERFIX_ALLOW_RESET", "1")
    from app.main import app as fastapp
    client = TestClient(fastapp, headers={"Authorization": "Bearer operator-token-for-tests-1234567890abc"})
    r = client.post("/admin/reset")
    assert r.status_code == 200
    inc = client.get("/incidents/INC-001")
    assert inc.status_code == 200
    evr = client.get("/incidents/INC-001/evidence")
    assert evr.status_code == 200
    evdata = evr.json()
    snap = evdata["snapshot_hash"]
    assert isinstance(snap, str) and len(snap) >= 8
    patch = client.get("/incidents/INC-001/patch")
    assert patch.status_code == 200
    assert "process_order_fixed" in patch.json()["patch"]
    prod = client.get("/products/WIDGET-1")
    assert prod.status_code == 200
    assert prod.json()["product"]["stock"] == 100
    ev_ids = [e["id"] for e in evdata["events"] if e["kind"] == "order_deduction"]
    assert len(ev_ids) == 2
    rej = client.post("/proposals", json={"incident_id": "INC-001", "product_id": "WIDGET-1", "evidence_event_ids": ev_ids, "extra_evil": 1})
    assert rej.status_code == 422
    pr = client.post("/proposals", json={"incident_id": "INC-001", "product_id": "WIDGET-1", "evidence_event_ids": ev_ids})
    assert pr.status_code == 200
    p = pr.json()
    assert p["status"] == "proposed" and p["credit_qty"] == 10
    pid = p["proposal_id"]
    pv = client.get(f"/proposals/{pid}/preview")
    assert pv.status_code == 200
    assert pv.json()["simulation_pass"] is True
    bad_approve = client.post(f"/proposals/{pid}/approve", json={"snapshot_hash": "0" * 64})
    assert bad_approve.status_code == 409
    ap = client.post(f"/proposals/{pid}/approve", json={"snapshot_hash": p["snapshot_hash"]})
    assert ap.status_code == 200
    aid = ap.json()["approval_id"]
    needy = client.post("/proposals", json={"incident_id": "INC-001", "product_id": "GADGET-9", "evidence_event_ids": ev_ids})
    assert needy.json()["status"] == "needs_review"
    bad_a2 = client.post(f"/proposals/{needy.json()['proposal_id']}/approve", json={"snapshot_hash": "0" * 64})
    assert bad_a2.status_code in (409, 422)
    ay = client.post("/apply", json={"proposal_id": pid, "approval_id": aid, "idempotency_key": "api-key-1"})
    assert ay.status_code == 200
    assert ay.json()["after_stock"] == 110
    vf = client.post("/verify", json={"product_id": "WIDGET-1", "proposal_id": pid})
    assert vf.status_code == 200
    assert vf.json()["pass"] is True
