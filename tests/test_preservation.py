import json
import sqlite3
import threading

import pytest

from app import domain, recovery, store

W = store.CANONICAL_PRODUCT
G = "GADGET-9"
INC = store.CANONICAL_INCIDENT


def _conn(tmp_path):
    conn = store.connect(str(tmp_path / "afterfix.db"))
    store.reset_and_seed(conn)
    return conn


def _events(conn, product=W, kind=None):
    rows = store.list_product_events(conn, product)
    return [r for r in rows if kind is None or r["kind"] == kind]


def _ids(conn, product=W, kind=None):
    return [r["id"] for r in _events(conn, product, kind)]


def _one(conn, product, kind):
    return _events(conn, product, kind)[0]


def _sql(conn, sql, params=()):
    conn.execute(sql, params)
    conn.commit()


def _stock(conn, product):
    return store.get_product(conn, product)["stock"]


def _adjust(conn, product, delta):
    _sql(conn, "UPDATE products SET stock=stock+? WHERE product_id=?", (delta, product))


def _propose(conn, evidence=None):
    p = recovery.build_proposal(conn, INC, W, evidence or _ids(conn, W, "order_deduction"))
    assert p["status"] == "proposed", p
    return p


def _applied(conn):
    p = _propose(conn)
    prev = recovery.preview_proposal(conn, p["proposal_id"])
    assert prev["simulation_pass"] is True, prev
    appr = recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"])
    out = recovery.apply_proposal(conn, p["proposal_id"], appr["approval_id"], "apply-key-0001")
    assert out["status"] == "applied", out
    return p


def _assert_failed(result):
    assert result["pass"] is False, result
    assert result["preservation_verified"] is False, result


def test_lead_repro_restock_altered_and_unrelated_product_deleted(tmp_path):
    conn = _conn(tmp_path)
    try:
        p = _applied(conn)
        restock = _one(conn, W, "restock")
        _sql(conn, "UPDATE events SET qty=1 WHERE id=?", (restock["id"],))
        _adjust(conn, W, -19)  # stock still reconciles with altered ledger
        _sql(conn, "DELETE FROM products WHERE product_id=?", (G,))
        _assert_failed(recovery.verify_product(conn, W, p["proposal_id"]))
    finally:
        conn.close()


@pytest.mark.parametrize("case", [
    "opening_alter", "opening_delete", "restock_alter", "restock_delete",
    "order_alter", "order_delete", "unrelated_delete_product",
    "unrelated_event_alter", "unrelated_event_delete",
])
def test_historical_tampering_fails_despite_reconciled_ledger(tmp_path, case):
    conn = _conn(tmp_path)
    try:
        p = _applied(conn)
        if case == "opening_alter":
            e = _one(conn, W, "init"); _sql(conn, "UPDATE events SET qty=99 WHERE id=?", (e["id"],)); _adjust(conn, W, -1)
        elif case == "opening_delete":
            e = _one(conn, W, "init"); _sql(conn, "DELETE FROM events WHERE id=?", (e["id"],)); _adjust(conn, W, -100)
        elif case == "restock_alter":
            e = _one(conn, W, "restock"); _sql(conn, "UPDATE events SET qty=7 WHERE id=?", (e["id"],)); _adjust(conn, W, -13)
        elif case == "restock_delete":
            e = _one(conn, W, "restock"); _sql(conn, "DELETE FROM events WHERE id=?", (e["id"],)); _adjust(conn, W, -20)
        elif case == "order_alter":
            e = _one(conn, W, "order_deduction"); _sql(conn, "UPDATE events SET qty=9 WHERE id=?", (e["id"],)); _adjust(conn, W, 1)
        elif case == "order_delete":
            e = _events(conn, W, "order_deduction")[-1]; _sql(conn, "DELETE FROM events WHERE id=?", (e["id"],)); _adjust(conn, W, 10)
        elif case == "unrelated_delete_product":
            _sql(conn, "DELETE FROM products WHERE product_id=?", (G,))
        elif case == "unrelated_event_alter":
            e = _one(conn, G, "init"); _sql(conn, "UPDATE events SET qty=49 WHERE id=?", (e["id"],)); _adjust(conn, G, -1)
        elif case == "unrelated_event_delete":
            e = _one(conn, G, "init"); _sql(conn, "DELETE FROM events WHERE id=?", (e["id"],)); _adjust(conn, G, -50)
        _assert_failed(recovery.verify_product(conn, W, p["proposal_id"]))
    finally:
        conn.close()


def test_later_legitimate_activity_and_new_product_still_verify(tmp_path):
    conn = _conn(tmp_path)
    try:
        p = _applied(conn)
        domain.process_restock(conn, W, 7)
        domain.process_order_fixed(conn, W, "ORD-2001", 4)
        domain.process_restock(conn, G, 6)
        domain.process_order_fixed(conn, G, "ORD-9002", 3)
        _sql(conn, "INSERT INTO products(product_id,name,stock) VALUES ('NEW-1','New',30)")
        _sql(conn, "INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('NEW-1','init',30,NULL,1,'opening')")
        expected_w = 100 - 10 + 20 + 7 - 4
        expected_g = 50 - 5 + 6 - 3
        assert _stock(conn, W) == expected_w
        assert _stock(conn, G) == expected_g
        result = recovery.verify_product(conn, W, p["proposal_id"])
        assert result["pass"] is True, result
        assert result["final_stock"] == expected_w
        assert recovery.verify_product(conn, G)["pass"] is True
        assert recovery.verify_product(conn, "NEW-1")["pass"] is True
    finally:
        conn.close()


def test_wrong_product_and_missing_approval_do_not_pass(tmp_path):
    conn = _conn(tmp_path)
    try:
        wrong = recovery.build_proposal(conn, INC, G, _ids(conn, G, "order_deduction"))
        assert wrong["status"] == "needs_review", wrong
        p = _propose(conn)
        _assert_failed(recovery.verify_product(conn, W, p["proposal_id"]))
        with pytest.raises(recovery.ApprovalError):
            recovery.apply_proposal(conn, p["proposal_id"], "APPR-missing", "apply-key-0002")
        _assert_failed(recovery.verify_product(conn, G, p["proposal_id"]))
        _assert_failed(recovery.verify_product(conn, W, "PROP-missing"))
    finally:
        conn.close()


@pytest.mark.parametrize("baseline", ["malformed", "empty", "incomplete", "wrong_product"])
def test_corrupt_baseline_fails_verification(tmp_path, baseline):
    conn = _conn(tmp_path)
    try:
        p = _applied(conn)
        if baseline == "malformed":
            value = "{not-json"
        elif baseline == "empty":
            value = "{}"
        else:
            row = conn.execute("SELECT baseline_json FROM approvals WHERE proposal_id=?", (p["proposal_id"],)).fetchone()
            data = json.loads(row["baseline_json"])
            if baseline == "incomplete":
                data.pop("events")
            else:
                data["product_id"] = G
            value = json.dumps(data)
        _sql(conn, "UPDATE approvals SET baseline_json=? WHERE proposal_id=?", (value, p["proposal_id"]))
        _assert_failed(recovery.verify_product(conn, W, p["proposal_id"]))
    finally:
        conn.close()


@pytest.mark.parametrize("case", ["missing", "repeated", "wrong_amount", "wrong_reversed", "replaced_product"])
def test_repair_event_tampering_fails_verification(tmp_path, case):
    conn = _conn(tmp_path)
    try:
        p = _applied(conn)
        repair = _one(conn, W, "repair_credit")
        original = _events(conn, W, "order_deduction")[0]["id"]
        if case == "missing":
            _sql(conn, "DELETE FROM events WHERE id=?", (repair["id"],)); _adjust(conn, W, -10)
        elif case == "repeated":
            _sql(conn, "INSERT INTO events(product_id,kind,qty,order_id,ts,note,reversed_ids,repair_proposal_id) VALUES (?,?,?,?,?,?,?,?)",
                 (W, "repair_credit", repair["qty"], None, repair["ts"], "copy", repair["reversed_ids"], p["proposal_id"]))
            _adjust(conn, W, 10)
        elif case == "wrong_amount":
            _sql(conn, "UPDATE events SET qty=9 WHERE id=?", (repair["id"],)); _adjust(conn, W, -1)
        elif case == "wrong_reversed":
            _sql(conn, "UPDATE events SET reversed_ids=? WHERE id=?", (json.dumps([original]), repair["id"]))
        elif case == "replaced_product":
            _sql(conn, "UPDATE events SET product_id=? WHERE id=?", (G, repair["id"])); _adjust(conn, W, -10); _adjust(conn, G, 10)
        _assert_failed(recovery.verify_product(conn, W, p["proposal_id"]))
    finally:
        conn.close()


@pytest.mark.parametrize("case", ["bad_json", "duplicate_ids", "original_as_reversal", "credit_mismatch"])
def test_corrupt_prior_repair_blocks_new_proposal_and_verification(tmp_path, case):
    conn = _conn(tmp_path)
    try:
        p = _applied(conn)
        repair = _one(conn, W, "repair_credit")
        ded = _events(conn, W, "order_deduction")
        original, extra = ded[0]["id"], ded[1]["id"]
        if case == "bad_json":
            _sql(conn, "UPDATE events SET reversed_ids='not-json' WHERE id=?", (repair["id"],))
        elif case == "duplicate_ids":
            _sql(conn, "UPDATE events SET reversed_ids=? WHERE id=?", (json.dumps([extra, extra]), repair["id"]))
        elif case == "original_as_reversal":
            _sql(conn, "UPDATE events SET reversed_ids=? WHERE id=?", (json.dumps([original]), repair["id"]))
        elif case == "credit_mismatch":
            _sql(conn, "UPDATE events SET qty=9 WHERE id=?", (repair["id"],)); _adjust(conn, W, -1)
        domain.process_order_buggy(conn, W, "ORD-3001", 5)
        domain.process_order_buggy(conn, W, "ORD-3001", 5)
        new_ded = [r["id"] for r in _events(conn, W, "order_deduction") if r["order_id"] == "ORD-3001"]
        nxt = recovery.build_proposal(conn, INC, W, [original] + new_ded)
        assert nxt["status"] == "needs_review", nxt
        _assert_failed(recovery.verify_product(conn, W, p["proposal_id"]))
    finally:
        conn.close()


def test_second_legitimate_duplicate_repairs_only_new_extra(tmp_path):
    conn = _conn(tmp_path)
    try:
        first = _applied(conn)
        old_original = _events(conn, W, "order_deduction")[0]["id"]
        domain.process_order_buggy(conn, W, "ORD-3002", 6)
        domain.process_order_buggy(conn, W, "ORD-3002", 6)
        new_ded = [r["id"] for r in _events(conn, W, "order_deduction") if r["order_id"] == "ORD-3002"]
        second = recovery.build_proposal(conn, INC, W, [old_original] + new_ded)
        assert second["status"] == "proposed", second
        assert second["reverse_event_ids"] == [max(new_ded)], second
        assert second["credit_qty"] == 6, second
        appr = recovery.approve_proposal(conn, second["proposal_id"], second["snapshot_hash"])
        out = recovery.apply_proposal(conn, second["proposal_id"], appr["approval_id"], "apply-key-second")
        assert out["status"] == "applied", out
        assert _stock(conn, W) == 100 - 10 + 20 - 6
        assert recovery.verify_product(conn, W, first["proposal_id"])["pass"] is True
        assert recovery.verify_product(conn, W, second["proposal_id"])["pass"] is True
    finally:
        conn.close()


def test_approval_audit_failure_rolls_back(tmp_path):
    conn = _conn(tmp_path)
    try:
        p = _propose(conn)
        _sql(conn, "CREATE TRIGGER fail_approve_audit BEFORE INSERT ON audit_log WHEN NEW.action='approve' BEGIN SELECT RAISE(ABORT,'audit failed'); END")
        with pytest.raises(sqlite3.Error):
            recovery.approve_proposal(conn, p["proposal_id"], p["snapshot_hash"])
        row = conn.execute("SELECT status FROM proposals WHERE id=?", (p["proposal_id"],)).fetchone()
        count = conn.execute("SELECT COUNT(*) c FROM approvals WHERE proposal_id=?", (p["proposal_id"],)).fetchone()["c"]
        assert row["status"] == "proposed"
        assert count == 0
    finally:
        conn.close()


def test_approval_completes_with_open_write_transaction(tmp_path):
    conn = _conn(tmp_path)
    try:
        p = _propose(conn)
        outcome = {}

        def run():
            c = None
            try:
                c = store.connect(str(tmp_path / "afterfix.db"))
                c.execute("BEGIN IMMEDIATE")
                c.execute("INSERT INTO audit_log(ts,action,proposal_id,detail) VALUES (1,'hold','-','x')")
                outcome["approval"] = recovery.approve_proposal(c, p["proposal_id"], p["snapshot_hash"])
            except Exception as exc:  # captured for assertion below
                outcome["error"] = repr(exc)
            finally:
                if c is not None:
                    c.close()

        thread = threading.Thread(target=run)
        thread.start()
        thread.join(timeout=10)
        assert not thread.is_alive(), "approval deadlocked on backup with open write transaction"
        assert "approval" in outcome, outcome
    finally:
        conn.close()
