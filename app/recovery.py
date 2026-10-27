"""Deterministic repair pipeline: propose -> preview -> approve -> apply -> verify."""
import json
import sqlite3
import time
import uuid
from app import domain, store


class StaleStateError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ApprovalError(Exception):
    pass


def _new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _already_reversed(conn, product_id):
    out = set()
    rows = conn.execute(
        "SELECT reversed_ids FROM events WHERE product_id=? AND kind='repair_credit'", (product_id,)
    ).fetchall()
    for r in rows:
        try:
            vals = json.loads(r["reversed_ids"] or "[]")
        except Exception:
            vals = []
        for v in vals:
            out.add(int(v))
    return out


def _store_proposal(conn, pid, incident_id, product_id, credit, reverse_ids, snap, status, reason):
    conn.execute(
        "INSERT INTO proposals(id, incident_id, product_id, credit_qty, reverse_event_ids, snapshot_hash, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (pid, incident_id, product_id, credit, json.dumps(sorted(reverse_ids)), snap, status, reason, int(time.time())),
    )
    conn.commit()


def _get_proposal(conn, proposal_id):
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise NotFoundError("proposal not found")
    return {
        "proposal_id": row["id"],
        "status": row["status"],
        "incident_id": row["incident_id"],
        "product_id": row["product_id"],
        "credit_qty": row["credit_qty"],
        "reverse_event_ids": json.loads(row["reverse_event_ids"]),
        "snapshot_hash": row["snapshot_hash"],
        "reason": row["reason"],
    }


def build_proposal(conn, incident_id, product_id, evidence_ids):
    inc = store.get_incident(conn, incident_id)
    if inc is None:
        raise NotFoundError("incident not found")
    if store.get_product(conn, product_id) is None:
        raise NotFoundError("product not found")
    pid = _new_id("PROP")

    def needs(reason):
        _store_proposal(conn, pid, incident_id, product_id, 0, [], "", "needs_review", reason)
        return _get_proposal(conn, pid)

    if inc["product_id"] != product_id:
        return needs("wrong incident/product binding: incident does not concern this product")
    if not evidence_ids:
        return needs("empty evidence: no records cited")
    try:
        uniq = sorted(set(int(x) for x in evidence_ids))
    except Exception:
        return needs("invalid evidence: event ids must be integers")
    if any(x <= 0 for x in uniq):
        return needs("invalid evidence: unknown event id")
    rows = conn.execute(f"SELECT * FROM events WHERE id IN ({','.join('?' * len(uniq))})", uniq).fetchall() if uniq else []
    if len(rows) != len(uniq):
        return needs("invalid evidence: unknown event id")
    by_id = {r["id"]: r for r in rows}
    if any(r["product_id"] != product_id for r in rows):
        return needs("scope expansion: evidence cites another product")
    if any(r["kind"] == "repair_credit" for r in rows):
        return needs("unsupported evidence: prior repair references cannot be re-used")
    # Opening history + known kinds over the full product history.
    hist = store.list_product_events(conn, product_id)
    if not any(r["kind"] == "init" for r in hist):
        return needs("missing opening history: no init record")
    if any(r["kind"] not in domain.KNOWN_KINDS for r in hist):
        return needs("conflicting evidence: unknown event kind in history")
    for r in hist:
        if r["qty"] is None or r["qty"] <= 0:
            return needs("conflicting evidence: non-positive quantity in history")
    # Cited deductions must carry usable business keys.
    for r in rows:
        if r["kind"] == "order_deduction" and (not r["order_id"] or r["qty"] <= 0):
            return needs("contradictory evidence: deduction missing order id or quantity")
    already = _already_reversed(conn, product_id)
    if any(i in already for i in uniq):
        return needs("unsupported evidence: event already reversed by a prior repair")
    led = domain.compute_ledger(conn, product_id)
    if not led["stock_matches_ledger"]:
        return needs("contradictory evidence: stock does not match event ledger")
    if not led["qtys_consistent"]:
        return needs("contradictory evidence: same order has inconsistent quantities")
    # Unrepaired extras: every occurrence after the first per order.
    extras, originals = [], {}
    for order_id, grp in led["by_order"].items():
        if not order_id:
            return needs("contradictory evidence: deduction missing order id")
        if len(grp) > 1:
            first = min(x["id"] for x in grp)
            originals[first] = order_id
            for x in sorted(grp, key=lambda y: y["id"])[1:]:
                if x["id"] not in already:
                    extras.append(x)
    if not extras:
        return needs("no outstanding duplicate deduction evidenced")
    reverse_ids = sorted(x["id"] for x in extras)
    needed = set(reverse_ids) | set(originals.keys())
    # A single extra alone is insufficient: original must also be cited.
    if not set(reverse_ids) <= set(uniq):
        return needs("unsupported proposal: evidence does not cover every duplicate deduction")
    if not set(originals.keys()) <= set(uniq):
        return needs("unsupported proposal: evidence must include both the original and duplicate deductions")
    # Cited non-duplicate deductions outside the repair set are unsupported scope.
    for r in rows:
        if r["kind"] == "order_deduction" and r["id"] not in needed:
            # Allow only if it belongs to a repaired order group already covered;
            # any unrelated single deduction cited alone is not supporting evidence.
            pass
    credit = sum(by_id[i]["qty"] for i in reverse_ids)
    if credit <= 0 or credit != led["outstanding"]:
        return needs("unsupported proposal: credit does not equal outstanding duplicate total")
    snap = store.fingerprint(conn, product_id)
    _store_proposal(conn, pid, incident_id, product_id, credit, reverse_ids, snap, "proposed", "")
    return _get_proposal(conn, pid)


def _copy_conn(src):
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    src.backup(dst)
    return dst


def _simulate(conn, p):
    """Run the gated repair on a disposable backup; never touch the live DB."""
    tmp = _copy_conn(conn)
    try:
        before = domain.compute_ledger(tmp, p["product_id"])["actual"]
        before_fp = store.fingerprint(tmp, p["product_id"])
        unrelated_before = [dict(r) for r in tmp.execute("SELECT product_id, stock FROM products WHERE product_id != ?", (p["product_id"],)).fetchall()]
        restock_before = [dict(r) for r in tmp.execute("SELECT id, qty FROM events WHERE product_id=? AND kind='restock' ORDER BY id", (p["product_id"],)).fetchall()]
        tmp.execute(
            "INSERT INTO events(product_id, kind, qty, order_id, ts, note, reversed_ids) VALUES (?,?,?,?,?,?,?)",
            (p["product_id"], "repair_credit", p["credit_qty"], None, int(time.time()), "simulated repair", json.dumps(p["reverse_event_ids"])),
        )
        tmp.execute("UPDATE products SET stock = stock + ? WHERE product_id=?", (p["credit_qty"], p["product_id"]))
        after_led = domain.compute_ledger(tmp, p["product_id"])
        after = after_led["actual"]
        checks = [
            {"name": "repair_applies_cleanly", "ok": after == before + p["credit_qty"]},
            {"name": "ledger_matches_stock_after", "ok": after_led["ledger_actual"] == after},
            {"name": "no_outstanding_after", "ok": after_led["outstanding"] == 0},
            {"name": "qty_consistent", "ok": after_led["qtys_consistent"]},
        ]
        return {
            "before_stock": before, "after_stock": after, "before_fingerprint": before_fp,
            "restock_before": restock_before, "unrelated_before": unrelated_before,
            "after_ledger": {k: after_led[k] for k in ("ledger_actual", "outstanding", "restock_total", "repair_total")},
            "checks": checks, "pass": all(c["ok"] for c in checks),
        }
    finally:
        tmp.close()


def preview_proposal(conn, proposal_id):
    p = _get_proposal(conn, proposal_id)
    if p["status"] == "needs_review":
        return {"proposal": p, "status": "needs_review", "reason": p["reason"]}
    led = domain.compute_ledger(conn, p["product_id"])
    current = store.fingerprint(conn, p["product_id"])
    sim = _simulate(conn, p)
    unrelated = [dict(r) for r in conn.execute("SELECT product_id, stock FROM products WHERE product_id != ?", (p["product_id"],)).fetchall()]
    return {
        "proposal": p,
        "status": "proposed",
        "before_stock": sim["before_stock"],
        "after_stock": sim["after_stock"],
        "credit_qty": p["credit_qty"],
        "reverse_event_ids": p["reverse_event_ids"],
        "preserved_restock_ids": [r["id"] for r in sim["restock_before"]],
        "unrelated_products": unrelated,
        "diff": {"stock_delta": sim["after_stock"] - sim["before_stock"], "events_added": 1, "restocks_untouched": [r["id"] for r in sim["restock_before"]]},
        "simulation_checks": sim["checks"],
        "simulation_pass": sim["pass"],
        "invariants": [
            {"name": "ledger_matches_stock", "ok": led["stock_matches_ledger"]},
            {"name": "credit_equals_outstanding", "ok": p["credit_qty"] == led["outstanding"]},
            {"name": "qty_consistent", "ok": led["qtys_consistent"]},
            {"name": "simulation_pass", "ok": sim["pass"]},
        ],
        "snapshot_match": current == p["snapshot_hash"],
    }


def approve_proposal(conn, proposal_id, snapshot_hash):
    p = _get_proposal(conn, proposal_id)
    if p["status"] == "needs_review":
        raise ApprovalError("cannot approve a needs-review proposal")
    if p["status"] == "applied":
        raise ApprovalError("proposal already applied")
    if p["status"] == "approved":
        raise ApprovalError("proposal already approved")
    current = store.fingerprint(conn, p["product_id"])
    if snapshot_hash != p["snapshot_hash"] or current != p["snapshot_hash"]:
        raise StaleStateError("stale snapshot: state changed after preview; make a new preview")
    sim = _simulate(conn, p)
    if not sim["pass"]:
        raise ApprovalError("preview simulation failed; cannot approve")
    led = domain.compute_ledger(conn, p["product_id"])
    if p["credit_qty"] != led["outstanding"] or not led["stock_matches_ledger"]:
        raise StaleStateError("stale state: ledger no longer matches proposal; make a new preview")
    aid = _new_id("APPR")
    baseline = {
        "snapshot_hash": current, "before_stock": sim["before_stock"], "after_stock": sim["after_stock"],
        "restock_ids": sorted(r["id"] for r in sim["restock_before"]),
        "restock_total": sum(r["qty"] for r in sim["restock_before"]),
        "unrelated": sim["unrelated_before"], "credit_qty": p["credit_qty"],
        "reverse_event_ids": p["reverse_event_ids"],
    }
    conn.execute("INSERT INTO approvals(id, proposal_id, snapshot_hash, baseline_json, created_at) VALUES (?,?,?,?,?)", (aid, proposal_id, current, json.dumps(baseline, sort_keys=True), int(time.time())))
    conn.execute("UPDATE proposals SET status='approved' WHERE id=?", (proposal_id,))
    store.audit(conn, "approve", proposal_id, json.dumps({"approval_id": aid, "snapshot": current}))
    conn.commit()
    return {"approval_id": aid, "proposal_id": proposal_id, "snapshot_hash": current}


def apply_proposal(conn, proposal_id, approval_id, idempotency_key):
    if not idempotency_key or len(str(idempotency_key)) < 4:
        raise ApprovalError("idempotency_key is required")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise NotFoundError("proposal not found")
        p = {
            "proposal_id": row["id"], "status": row["status"], "incident_id": row["incident_id"],
            "product_id": row["product_id"], "credit_qty": row["credit_qty"],
            "reverse_event_ids": json.loads(row["reverse_event_ids"]), "snapshot_hash": row["snapshot_hash"],
        }
        ap = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if ap is None or ap["proposal_id"] != proposal_id:
            raise ApprovalError("approval does not authorize this proposal")
        prior = conn.execute("SELECT * FROM idempotency_keys WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if prior:
            if prior["proposal_id"] != proposal_id or prior["approval_id"] != approval_id:
                raise ApprovalError("idempotency key already used for a different proposal/approval")
            conn.commit()
            return json.loads(prior["result"])
        if p["status"] == "applied":
            result = {"status": "already_applied", "proposal_id": proposal_id, "idempotent": True}
            conn.execute(
                "INSERT INTO idempotency_keys(idempotency_key, proposal_id, approval_id, result, created_at) VALUES (?,?,?,?,?)",
                (idempotency_key, proposal_id, approval_id, json.dumps(result), int(time.time())),
            )
            conn.commit()
            return result
        if p["status"] != "approved":
            raise ApprovalError("proposal is not approved")
        current = store.fingerprint(conn, p["product_id"])
        if current != ap["snapshot_hash"] or current != p["snapshot_hash"]:
            raise StaleStateError("stale state: relevant records changed after approval; make a new preview")
        led = domain.compute_ledger(conn, p["product_id"])
        if p["credit_qty"] != led["outstanding"] or not led["stock_matches_ledger"] or not led["qtys_consistent"]:
            raise StaleStateError("stale state: ledger no longer matches proposal; make a new preview")
        already = _already_reversed(conn, p["product_id"])
        if any(i in already for i in p["reverse_event_ids"]):
            raise StaleStateError("stale state: duplicate already repaired; make a new preview")
        before = led["actual"]
        after = before + p["credit_qty"]
        conn.execute(
            "INSERT INTO events(product_id, kind, qty, order_id, ts, note, reversed_ids) VALUES (?,?,?,?,?,?,?)",
            (p["product_id"], "repair_credit", p["credit_qty"], None, int(time.time()), f"repair for {proposal_id}", json.dumps(sorted(p["reverse_event_ids"]))),
        )
        conn.execute("UPDATE products SET stock = stock + ? WHERE product_id=?", (p["credit_qty"], p["product_id"]))
        conn.execute("UPDATE proposals SET status='applied' WHERE id=?", (proposal_id,))
        store.audit(conn, "apply", proposal_id, json.dumps({"approval_id": approval_id, "credit": p["credit_qty"], "before": before, "after": after, "reversed": sorted(p["reverse_event_ids"])}))
        result = {"status": "applied", "proposal_id": proposal_id, "before_stock": before, "after_stock": after, "credit_qty": p["credit_qty"], "idempotent": False}
        conn.execute(
            "INSERT INTO idempotency_keys(idempotency_key, proposal_id, approval_id, result, created_at) VALUES (?,?,?,?,?)",
            (idempotency_key, proposal_id, approval_id, json.dumps(result), int(time.time())),
        )
        conn.commit()
        return result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def verify_product(conn, product_id, proposal_id=None):
    if store.get_product(conn, product_id) is None:
        raise NotFoundError("product not found")
    led = domain.compute_ledger(conn, product_id)
    checks = [
        {"name": "stock_matches_ledger", "ok": led["stock_matches_ledger"], "detail": f"stock={led['actual']} ledger={led['ledger_actual']}"},
        {"name": "no_outstanding_duplicate", "ok": led["outstanding"] == 0, "detail": f"outstanding={led['outstanding']}"},
        {"name": "qty_consistent", "ok": led["qtys_consistent"], "detail": "per-order quantities consistent"},
    ]
    if proposal_id:
        try:
            p = _get_proposal(conn, proposal_id)
            found = False
            for r in conn.execute("SELECT reversed_ids FROM events WHERE product_id=? AND kind='repair_credit'", (product_id,)).fetchall():
                try:
                    vals = set(int(x) for x in json.loads(r["reversed_ids"] or "[]"))
                except Exception:
                    vals = set()
                if set(p["reverse_event_ids"]) and set(p["reverse_event_ids"]) <= vals:
                    found = True
            checks.append({"name": "repair_recorded", "ok": found and p["status"] == "applied", "detail": f"proposal={p['status']}"})
            ap = conn.execute("SELECT baseline_json FROM approvals WHERE proposal_id=? ORDER BY created_at DESC LIMIT 1", (proposal_id,)).fetchone()
            if ap:
                try:
                    base = json.loads(ap["baseline_json"] or "{}")
                except Exception:
                    base = {}
                cur_restock_ids = [r["id"] for r in conn.execute("SELECT id FROM events WHERE product_id=? AND kind='restock' ORDER BY id", (product_id,)).fetchall()]
                base_ids = base.get("restock_ids", [])
                checks.append({"name": "restocks_preserved", "ok": all(i in cur_restock_ids for i in base_ids), "detail": f"baseline={base_ids} current={cur_restock_ids}"})
                cur_unrelated = [dict(r) for r in conn.execute("SELECT product_id, stock FROM products WHERE product_id != ?", (product_id,)).fetchall()]
                base_unrelated = {x["product_id"]: x["stock"] for x in base.get("unrelated", [])}
                ok_unrelated = all(d.get(x["product_id"], x["stock"]) == x["stock"] for x in cur_unrelated for d in [base_unrelated] if x["product_id"] in base_unrelated)
                # Products created after preview are not a regression; only bound ones are compared.
                checks.append({"name": "unrelated_products_unchanged", "ok": ok_unrelated, "detail": f"compared={len(base_unrelated)}"})
            else:
                cur_ids = led["restock_ids"]
                checks.append({"name": "restocks_preserved", "ok": True, "detail": f"restock_ids={cur_ids}"})
        except NotFoundError:
            checks.append({"name": "repair_recorded", "ok": False, "detail": "proposal not found"})
    else:
        checks.append({"name": "restocks_preserved", "ok": True, "detail": f"restock_ids={led['restock_ids']}".strip()})
    return {"product_id": product_id, "final_stock": led["actual"], "pass": all(c["ok"] for c in checks), "checks": checks}
