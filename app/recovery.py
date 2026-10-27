"""Deterministic repair pipeline: propose -> preview -> approve -> apply -> verify."""
import json
import sqlite3
import time
import uuid
from app import domain, rust_verifier, store


class StaleStateError(Exception):
    pass


class NotFoundError(Exception):
    pass


class ApprovalError(Exception):
    pass


_MAX_ACTOR_ID_LEN = 128


def _validate_actor_id(actor_id):
    if not isinstance(actor_id, str):
        raise ApprovalError("invalid actor_id")
    if actor_id != actor_id.strip() or not (1 <= len(actor_id) <= _MAX_ACTOR_ID_LEN):
        raise ApprovalError("invalid actor_id")
    if any(ord(c) < 32 or ord(c) == 127 for c in actor_id):
        raise ApprovalError("invalid actor_id")
    return actor_id


def _new_id(prefix):
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _event_dict(r):
    keys = set(r.keys())
    return {
        "id": r["id"], "product_id": r["product_id"], "kind": r["kind"], "qty": r["qty"],
        "order_id": r["order_id"] if "order_id" in keys else None,
        "ts": r["ts"], "note": r["note"] if "note" in keys and r["note"] is not None else "",
        "reversed_ids": r["reversed_ids"] if "reversed_ids" in keys and r["reversed_ids"] is not None else "[]",
        "repair_proposal_id": r["repair_proposal_id"] if "repair_proposal_id" in keys else None,
    }


def _strict_stored_ids(raw):
    """Exact stored JSON int list; bool/string/float coercion is malformed."""
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 4096:
        raise ValueError("malformed")
    vals = json.loads(raw)
    if not isinstance(vals, list) or len(vals) > 200:
        raise ValueError("malformed")
    if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 or v > 2**63 - 1 for v in vals):
        raise ValueError("malformed")
    if len(set(vals)) != len(vals):
        raise ValueError("malformed")
    return sorted(vals)


def _repair_state(conn, product_id):
    rows = store.list_product_events(conn, product_id)
    by_order = {}
    for r in rows:
        if r["kind"] == "order_deduction":
            by_order.setdefault(r["order_id"], []).append(r)
    extras = set()
    for grp in by_order.values():
        for x in sorted(grp, key=lambda y: y["id"])[1:]:
            extras.add(x["id"])
    by_id = {r["id"]: r for r in rows}
    errors, repairs, seen = [], [], {}
    for r in rows:
        if r["kind"] != "repair_credit":
            continue
        ev = _event_dict(r)
        repairs.append(ev)
        if ev["qty"] is None or ev["qty"] <= 0:
            errors.append(f"repair {ev['id']}: non-positive qty")
        rp = ev["repair_proposal_id"]
        if not rp or not isinstance(rp, str):
            errors.append(f"repair {ev['id']}: missing repair_proposal_id")
            rp = None
        try:
            vals = _strict_stored_ids(ev["reversed_ids"] or "")
            if not vals:
                raise ValueError
        except Exception:
            errors.append(f"repair {ev['id']}: malformed reversed_ids")
            continue
        total = 0
        for v in vals:
            if v in seen:
                errors.append(f"repair {ev['id']}: reused reversed id {v}")
            seen[v] = ev["id"]
            t = by_id.get(v)
            if t is None or t["product_id"] != product_id or t["kind"] != "order_deduction":
                errors.append(f"repair {ev['id']}: reversed id {v} is not a deduction of this product")
            elif v not in extras:
                errors.append(f"repair {ev['id']}: reversed id {v} is not an extra duplicate")
            else:
                total += t["qty"]
        if total and ev["qty"] != total:
            errors.append(f"repair {ev['id']}: credit {ev['qty']} != reversed total {total}")
        if rp:
            prow = conn.execute("SELECT * FROM proposals WHERE id=?", (rp,)).fetchone()
            if prow is None:
                errors.append(f"repair {ev['id']}: unknown repair proposal")
            else:
                try:
                    pids = _strict_stored_ids(prow["reverse_event_ids"])
                except Exception:
                    pids = None
                if prow["product_id"] != product_id or prow["credit_qty"] != ev["qty"] or pids != sorted(vals) or prow["status"] != "applied":
                    errors.append(f"repair {ev['id']}: proposal linkage/amount mismatch")
    rps = [x["repair_proposal_id"] for x in repairs if x["repair_proposal_id"]]
    if len(rps) != len(set(rps)):
        errors.append("multiple repair events for one proposal")
    return set(seen), errors, repairs


def _store_proposal(conn, pid, incident_id, product_id, credit, reverse_ids, snap, status, reason):
    conn.execute("INSERT INTO proposals(id, incident_id, product_id, credit_qty, reverse_event_ids, snapshot_hash, status, reason, created_at) VALUES (?,?,?,?,?,?,?,?,?)", (pid, incident_id, product_id, credit, json.dumps(sorted(reverse_ids)), snap, status, reason, int(time.time())))
    conn.commit()


def _get_proposal(conn, proposal_id):
    row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
    if row is None:
        raise NotFoundError("proposal not found")
    try:
        rev = _strict_stored_ids(row["reverse_event_ids"])
    except Exception:
        raise ApprovalError("malformed stored proposal")
    credit = row["credit_qty"]
    if isinstance(credit, bool) or not isinstance(credit, int) or credit < 0 or credit > 2**63 - 1:
        raise ApprovalError("malformed stored proposal")
    if row["status"] == "proposed" and (credit <= 0 or not rev):
        raise ApprovalError("malformed stored proposal")
    if row["status"] == "needs_review" and (credit != 0 or rev):
        raise ApprovalError("malformed stored proposal")
    return {"proposal_id": row["id"], "status": row["status"], "incident_id": row["incident_id"], "product_id": row["product_id"], "credit_qty": credit, "reverse_event_ids": rev, "snapshot_hash": row["snapshot_hash"], "reason": row["reason"]}


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
        if any(isinstance(x, bool) or not isinstance(x, int) for x in evidence_ids):
            raise ValueError
        uniq = sorted(set(evidence_ids))
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
    hist = store.list_product_events(conn, product_id)
    if not any(r["kind"] == "init" for r in hist):
        return needs("missing opening history: no init record")
    if any(r["kind"] not in domain.KNOWN_KINDS for r in hist):
        return needs("conflicting evidence: unknown event kind in history")
    for r in hist:
        if r["qty"] is None or r["qty"] <= 0:
            return needs("conflicting evidence: non-positive quantity in history")
    for r in rows:
        if r["kind"] == "order_deduction" and (not r["order_id"] or r["qty"] <= 0):
            return needs("contradictory evidence: deduction missing order id or quantity")
    already, repair_errors, _ = _repair_state(conn, product_id)
    if repair_errors:
        return needs("conflicting evidence: invalid prior repair reference: " + "; ".join(repair_errors))
    if any(i in already for i in uniq):
        return needs("unsupported evidence: event already reversed by a prior repair")
    led = domain.compute_ledger(conn, product_id)
    if not led["stock_matches_ledger"]:
        return needs("contradictory evidence: stock does not match event ledger")
    if not led["qtys_consistent"]:
        return needs("contradictory evidence: same order has inconsistent quantities")
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
    if not set(reverse_ids) <= set(uniq):
        return needs("unsupported proposal: evidence does not cover every duplicate deduction")
    if not set(originals.keys()) <= set(uniq):
        return needs("unsupported proposal: evidence must include both the original and duplicate deductions")
    credit = sum(by_id[i]["qty"] for i in reverse_ids)
    if credit <= 0 or credit != led["outstanding"]:
        return needs("unsupported proposal: credit does not equal outstanding duplicate total")
    snap = store.fingerprint(conn, product_id)
    _store_proposal(conn, pid, incident_id, product_id, credit, reverse_ids, snap, "proposed", "")
    return _get_proposal(conn, pid)


def _copy_conn(src):
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    if getattr(src, "in_transaction", False):
        dst.executescript("\n".join(src.iterdump()))
    else:
        src.backup(dst)
    return dst


def _capture_baseline(src, p, snapshot_hash, before_stock, after_stock):
    products = [dict(r) for r in src.execute("SELECT product_id, name, stock FROM products ORDER BY product_id").fetchall()]
    events = [_event_dict(r) for r in src.execute("SELECT * FROM events ORDER BY id").fetchall()]
    return {"version": 2, "proposal_id": p["proposal_id"], "product_id": p["product_id"], "snapshot_hash": snapshot_hash, "before_stock": before_stock, "after_stock": after_stock, "credit_qty": p["credit_qty"], "reverse_event_ids": sorted(p["reverse_event_ids"]), "restock_ids": sorted(e["id"] for e in events if e["product_id"] == p["product_id"] and e["kind"] == "restock"), "restock_total": sum(e["qty"] for e in events if e["product_id"] == p["product_id"] and e["kind"] == "restock"), "unrelated": [{"product_id": x["product_id"], "stock": x["stock"]} for x in products if x["product_id"] != p["product_id"]], "products": products, "events": events}


def _simulate(conn, p):
    tmp = _copy_conn(conn)
    try:
        before = domain.compute_ledger(tmp, p["product_id"])["actual"]
        before_fp = store.fingerprint(tmp, p["product_id"])
        unrelated_before = [dict(r) for r in tmp.execute("SELECT product_id, stock FROM products WHERE product_id != ?", (p["product_id"],)).fetchall()]
        restock_before = [dict(r) for r in tmp.execute("SELECT id, qty FROM events WHERE product_id=? AND kind='restock' ORDER BY id", (p["product_id"],)).fetchall()]
        tmp.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note, reversed_ids, repair_proposal_id) VALUES (?,?,?,?,?,?,?,?)", (p["product_id"], "repair_credit", p["credit_qty"], None, int(time.time()), "simulated repair", json.dumps(p["reverse_event_ids"]), p["proposal_id"]))
        tmp.execute("UPDATE products SET stock = stock + ? WHERE product_id=?", (p["credit_qty"], p["product_id"]))
        after_led = domain.compute_ledger(tmp, p["product_id"])
        after = after_led["actual"]
        checks = [{"name": "repair_applies_cleanly", "ok": after == before + p["credit_qty"]}, {"name": "ledger_matches_stock_after", "ok": after_led["ledger_actual"] == after}, {"name": "no_outstanding_after", "ok": after_led["outstanding"] == 0}, {"name": "qty_consistent", "ok": after_led["qtys_consistent"]}]
        return {"before_stock": before, "after_stock": after, "before_fingerprint": before_fp, "restock_before": restock_before, "unrelated_before": unrelated_before, "after_ledger": {k: after_led[k] for k in ("ledger_actual", "outstanding", "restock_total", "repair_total")}, "checks": checks, "pass": all(c["ok"] for c in checks)}
    finally:
        tmp.close()


def _candidate(p):
    return {"credit_qty": p["credit_qty"], "reverse_event_ids": sorted(p["reverse_event_ids"])}


def _rust_gate(conn, p, expected_after=None):
    try:
        result = rust_verifier.verify_current(conn, p["product_id"], _candidate(p))
    except rust_verifier.RustVerificationError as exc:
        raise ApprovalError(f"rust verification failed: {exc.code}") from None
    if result.get("proposal_matches") is not True:
        raise ApprovalError("rust verification failed: candidate_mismatch")
    if expected_after is not None and result.get("expected_stock") != expected_after:
        raise ApprovalError("rust verification failed: candidate_mismatch")
    return result


def _rust_public_on_snapshot(snap_conn, p, candidate):
    snap = store.fingerprint(snap_conn, p["product_id"])
    try:
        return rust_verifier.verify_current(snap_conn, p["product_id"], candidate, snapshot_hash=snap), True
    except rust_verifier.RustVerificationError as exc:
        return rust_verifier.failure_public(exc, snap), False
    except Exception:
        return rust_verifier.failure_public(rust_verifier.RustVerificationError("rust_unavailable"), snap), False


def preview_proposal(conn, proposal_id):
    p = _get_proposal(conn, proposal_id)
    if p["status"] == "needs_review":
        return {"proposal": p, "status": "needs_review", "reason": p["reason"]}
    snap_conn = _copy_conn(conn)
    try:
        sp = _get_proposal(snap_conn, proposal_id)
        if sp["status"] == "needs_review":
            return {"proposal": sp, "status": "needs_review", "reason": sp["reason"]}
        if sp["status"] == "applied":
            led = domain.compute_ledger(snap_conn, sp["product_id"])
            current = store.fingerprint(snap_conn, sp["product_id"])
            rust, rust_ok = _rust_public_on_snapshot(snap_conn, sp, None)
            unrelated = [dict(r) for r in snap_conn.execute("SELECT product_id, stock FROM products WHERE product_id != ?", (sp["product_id"],)).fetchall()]
            restocks = [r["id"] for r in snap_conn.execute("SELECT id FROM events WHERE product_id=? AND kind='restock' ORDER BY id", (sp["product_id"],)).fetchall()]
            return {"proposal": sp, "status": "applied", "before_stock": led["actual"], "after_stock": led["actual"], "credit_qty": sp["credit_qty"], "reverse_event_ids": sp["reverse_event_ids"], "preserved_restock_ids": restocks, "unrelated_products": unrelated, "diff": {"stock_delta": 0, "events_added": 0, "restocks_untouched": restocks}, "simulation_checks": [], "simulation_pass": False, "invariants": [{"name": "ledger_matches_stock", "ok": led["stock_matches_ledger"]}, {"name": "qty_consistent", "ok": led["qtys_consistent"]}, {"name": "rust_agreement", "ok": rust_ok}], "snapshot_match": current == sp["snapshot_hash"], "rust_verification": rust}
        if sp["status"] not in ("proposed", "approved"):
            return {"proposal": sp, "status": sp["status"], "reason": sp["reason"]}
        led = domain.compute_ledger(snap_conn, sp["product_id"])
        current = store.fingerprint(snap_conn, sp["product_id"])
        sim = _simulate(snap_conn, sp)
        rust, rust_ok = _rust_public_on_snapshot(snap_conn, sp, _candidate(sp))
        unrelated = [dict(r) for r in snap_conn.execute("SELECT product_id, stock FROM products WHERE product_id != ?", (sp["product_id"],)).fetchall()]
        return {"proposal": sp, "status": sp["status"], "before_stock": sim["before_stock"], "after_stock": sim["after_stock"], "credit_qty": sp["credit_qty"], "reverse_event_ids": sp["reverse_event_ids"], "preserved_restock_ids": [r["id"] for r in sim["restock_before"]], "unrelated_products": unrelated, "diff": {"stock_delta": sim["after_stock"] - sim["before_stock"], "events_added": 1, "restocks_untouched": [r["id"] for r in sim["restock_before"]]}, "simulation_checks": sim["checks"], "simulation_pass": bool(sim["pass"] and rust_ok), "invariants": [{"name": "ledger_matches_stock", "ok": led["stock_matches_ledger"]}, {"name": "credit_equals_outstanding", "ok": sp["credit_qty"] == led["outstanding"]}, {"name": "qty_consistent", "ok": led["qtys_consistent"]}, {"name": "simulation_pass", "ok": sim["pass"]}, {"name": "rust_agreement", "ok": rust_ok}], "snapshot_match": current == sp["snapshot_hash"], "rust_verification": rust}
    finally:
        snap_conn.close()


def approve_proposal(conn, proposal_id, snapshot_hash, actor_id="local-core"):
    actor_id = _validate_actor_id(actor_id)
    p = _get_proposal(conn, proposal_id)
    if p["status"] == "needs_review":
        raise ApprovalError("cannot approve a needs-review proposal")
    if p["status"] == "applied":
        raise ApprovalError("proposal already applied")
    if p["status"] == "approved":
        raise ApprovalError("proposal already approved")
    if snapshot_hash != p["snapshot_hash"]:
        raise StaleStateError("stale snapshot: state changed after preview; make a new preview")
    snap_conn = _copy_conn(conn)
    try:
        sp = _get_proposal(snap_conn, proposal_id)
        if sp != p:
            raise StaleStateError("stale snapshot: proposal changed; make a new preview")
        if sp["status"] == "needs_review":
            raise ApprovalError("cannot approve a needs-review proposal")
        current_snap = store.fingerprint(snap_conn, p["product_id"])
        if current_snap != p["snapshot_hash"]:
            raise StaleStateError("stale snapshot: state changed after preview; make a new preview")
        sim = _simulate(snap_conn, p)
        if not sim["pass"]:
            raise ApprovalError("preview simulation failed; cannot approve")
        led = domain.compute_ledger(snap_conn, p["product_id"])
        if p["credit_qty"] != led["outstanding"] or not led["stock_matches_ledger"]:
            raise StaleStateError("stale state: ledger no longer matches proposal; make a new preview")
        _, repair_errors, _ = _repair_state(snap_conn, p["product_id"])
        if repair_errors:
            raise ApprovalError("invalid prior repair reference: " + "; ".join(repair_errors))
        baseline = _capture_baseline(snap_conn, p, current_snap, sim["before_stock"], sim["after_stock"])
    finally:
        snap_conn.close()
    aid = _new_id("APPR")
    own_txn = not getattr(conn, "in_transaction", False)
    try:
        if own_txn:
            conn.execute("BEGIN IMMEDIATE")
        p2 = _get_proposal(conn, proposal_id)
        if p2 != p or p2["status"] in ("needs_review", "applied", "approved"):
            raise StaleStateError("stale snapshot: proposal changed; make a new preview")
        current = store.fingerprint(conn, p["product_id"])
        if current != p["snapshot_hash"]:
            raise StaleStateError("stale snapshot: state changed after preview; make a new preview")
        recheck = _capture_baseline(conn, p, current, baseline["before_stock"], baseline["after_stock"])
        if recheck != baseline:
            raise StaleStateError("stale snapshot: baseline changed; make a new preview")
        led = domain.compute_ledger(conn, p["product_id"])
        if p["credit_qty"] != led["outstanding"] or not led["stock_matches_ledger"]:
            raise StaleStateError("stale state: ledger no longer matches proposal; make a new preview")
        rust = _rust_gate(conn, p, expected_after=baseline["after_stock"])
        conn.execute("INSERT INTO approvals(id, proposal_id, snapshot_hash, baseline_json, created_at, actor_id) VALUES (?,?,?,?,?,?)", (aid, proposal_id, current, json.dumps(baseline, sort_keys=True), int(time.time()), actor_id))
        conn.execute("UPDATE proposals SET status='approved' WHERE id=?", (proposal_id,))
        store.audit(conn, "approve", proposal_id, json.dumps({"approval_id": aid, "snapshot": current, "actor_id": actor_id, "rust_verification": {"verifier_version": rust["verifier_version"], "snapshot_hash": rust["snapshot_hash"], "ledger_actual": rust["ledger_actual"], "expected_stock": rust["expected_stock"], "outstanding_credit": rust["outstanding_credit"], "reverse_event_ids": rust["reverse_event_ids"]}}), actor_id=actor_id)
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    return {"approval_id": aid, "proposal_id": proposal_id, "snapshot_hash": baseline["snapshot_hash"], "actor_id": actor_id, "rust_verification": rust}


def apply_proposal(conn, proposal_id, approval_id, idempotency_key, actor_id="local-core"):
    actor_id = _validate_actor_id(actor_id)
    if not idempotency_key or len(str(idempotency_key)) < 4:
        raise ApprovalError("idempotency_key is required")
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute("SELECT * FROM proposals WHERE id=?", (proposal_id,)).fetchone()
        if row is None:
            raise NotFoundError("proposal not found")
        p = _get_proposal(conn, proposal_id)
        ap = conn.execute("SELECT * FROM approvals WHERE id=?", (approval_id,)).fetchone()
        if ap is None or ap["proposal_id"] != proposal_id:
            raise ApprovalError("approval does not authorize this proposal")
        try:
            approval_actor = ap["actor_id"]
        except Exception:
            approval_actor = None
        if not approval_actor or approval_actor != actor_id:
            raise ApprovalError("approval actor does not match apply actor")
        prior = conn.execute("SELECT * FROM idempotency_keys WHERE idempotency_key=?", (idempotency_key,)).fetchone()
        if prior:
            if prior["proposal_id"] != proposal_id or prior["approval_id"] != approval_id:
                raise ApprovalError("idempotency key already used for a different proposal/approval")
            conn.commit()
            return json.loads(prior["result"])
        if p["status"] == "applied":
            result = {"status": "already_applied", "proposal_id": proposal_id, "idempotent": True}
            conn.execute("INSERT INTO idempotency_keys(idempotency_key, proposal_id, approval_id, result, created_at) VALUES (?,?,?,?,?)", (idempotency_key, proposal_id, approval_id, json.dumps(result), int(time.time())))
            conn.commit()
            return result
        if p["status"] != "approved":
            raise ApprovalError("proposal is not approved")
        try:
            base = json.loads(ap["baseline_json"] or "")
            if not isinstance(base, dict) or base.get("proposal_id") != proposal_id or base.get("product_id") != p["product_id"] or base.get("snapshot_hash") != ap["snapshot_hash"]:
                raise ValueError
        except Exception:
            raise ApprovalError("missing or malformed approval baseline")
        current = store.fingerprint(conn, p["product_id"])
        if current != ap["snapshot_hash"] or current != p["snapshot_hash"]:
            raise StaleStateError("stale state: relevant records changed after approval; make a new preview")
        led = domain.compute_ledger(conn, p["product_id"])
        if p["credit_qty"] != led["outstanding"] or not led["stock_matches_ledger"] or not led["qtys_consistent"]:
            raise StaleStateError("stale state: ledger no longer matches proposal; make a new preview")
        already, repair_errors, _ = _repair_state(conn, p["product_id"])
        if repair_errors:
            raise StaleStateError("invalid prior repair reference; make a new preview")
        if any(i in already for i in p["reverse_event_ids"]):
            raise StaleStateError("stale state: duplicate already repaired; make a new preview")
        before, after = led["actual"], led["actual"] + p["credit_qty"]
        rust = _rust_gate(conn, p, expected_after=after)
        conn.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note, reversed_ids, repair_proposal_id) VALUES (?,?,?,?,?,?,?,?)", (p["product_id"], "repair_credit", p["credit_qty"], None, int(time.time()), "repair credit", json.dumps(sorted(p["reverse_event_ids"])), proposal_id))
        conn.execute("UPDATE products SET stock = stock + ? WHERE product_id=?", (p["credit_qty"], p["product_id"]))
        conn.execute("UPDATE proposals SET status='applied' WHERE id=?", (proposal_id,))
        store.audit(conn, "apply", proposal_id, json.dumps({"approval_id": approval_id, "credit": p["credit_qty"], "before": before, "after": after, "reversed": sorted(p["reverse_event_ids"]), "actor_id": actor_id, "rust_verification": {"verifier_version": rust["verifier_version"], "snapshot_hash": rust["snapshot_hash"], "ledger_actual": rust["ledger_actual"], "expected_stock": rust["expected_stock"], "outstanding_credit": rust["outstanding_credit"], "reverse_event_ids": rust["reverse_event_ids"]}}), actor_id=actor_id)
        result = {"status": "applied", "proposal_id": proposal_id, "before_stock": before, "after_stock": after, "credit_qty": p["credit_qty"], "idempotent": False, "rust_verification": rust}
        conn.execute("INSERT INTO idempotency_keys(idempotency_key, proposal_id, approval_id, result, created_at) VALUES (?,?,?,?,?)", (idempotency_key, proposal_id, approval_id, json.dumps(result), int(time.time())))
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
    _, repair_errors, repairs = _repair_state(conn, product_id)
    checks = [{"name": "stock_matches_ledger", "ok": led["stock_matches_ledger"], "detail": f"stock={led['actual']} ledger={led['ledger_actual']}"}, {"name": "no_outstanding_duplicate", "ok": led["outstanding"] == 0, "detail": f"outstanding={led['outstanding']}"}, {"name": "qty_consistent", "ok": led["qtys_consistent"], "detail": "per-order quantities consistent"}, {"name": "repair_history_valid", "ok": not repair_errors, "detail": "; ".join(repair_errors) or "prior repair references valid"}]
    preservation_verified = False
    if proposal_id:
        preservation_verified = True
        try:
            p = _get_proposal(conn, proposal_id)
        except NotFoundError:
            checks.append({"name": "repair_recorded", "ok": False, "detail": "proposal not found"})
            checks.append({"name": "preservation_baseline", "ok": False, "detail": "proposal not found"})
            return {"product_id": product_id, "final_stock": led["actual"], "pass": False, "preservation_verified": False, "checks": checks}
        binding_ok = p["product_id"] == product_id
        checks.append({"name": "proposal_binding", "ok": binding_ok, "detail": f"proposal_product={p['product_id']}"})
        linked = [r for r in repairs if r["repair_proposal_id"] == proposal_id]
        link_ok = binding_ok and p["status"] == "applied" and len(linked) == 1
        if link_ok:
            try:
                vals = _strict_stored_ids(linked[0]["reversed_ids"])
            except Exception:
                vals = None
            link_ok = linked[0]["product_id"] == product_id and linked[0]["qty"] == p["credit_qty"] and vals == sorted(p["reverse_event_ids"]) and bool(vals)
        checks.append({"name": "repair_recorded", "ok": link_ok, "detail": f"proposal={p['status']} linked_repairs={len(linked)}"})
        ap = conn.execute("SELECT * FROM approvals WHERE proposal_id=? ORDER BY created_at DESC, id DESC LIMIT 1", (proposal_id,)).fetchone()
        base, base_ok = None, False
        if ap is not None:
            try:
                base = json.loads(ap["baseline_json"] or "")
                prods, evs = base["products"], base["events"]
                if not isinstance(base, dict) or base.get("proposal_id") != proposal_id or base.get("product_id") != product_id:
                    raise ValueError
                if base.get("snapshot_hash") != ap["snapshot_hash"] or base.get("snapshot_hash") != p["snapshot_hash"]:
                    raise ValueError
                if base.get("credit_qty") != p["credit_qty"] or _strict_stored_ids(json.dumps(base["reverse_event_ids"])) != sorted(p["reverse_event_ids"]):
                    raise ValueError
                if not isinstance(prods, list) or not prods or not isinstance(evs, list):
                    raise ValueError
                if len({x["product_id"] for x in prods}) != len(prods) or product_id not in {x["product_id"] for x in prods}:
                    raise ValueError
                if any(not isinstance(x, dict) or not {"product_id", "name", "stock"} <= set(x) for x in prods):
                    raise ValueError
                if any(not isinstance(x, dict) or not {"id", "product_id", "kind", "qty", "order_id", "ts", "note", "reversed_ids", "repair_proposal_id"} <= set(x) for x in evs):
                    raise ValueError
                if len({x["id"] for x in evs}) != len(evs):
                    raise ValueError
                base_ok = True
            except Exception:
                base, base_ok = None, False
        checks.append({"name": "preservation_baseline", "ok": base_ok, "detail": "approved baseline bound to proposal/product" if base_ok else "missing/malformed/misbound baseline"})
        if base_ok:
            cur_products = {r["product_id"]: dict(r) for r in conn.execute("SELECT product_id, name, stock FROM products").fetchall()}
            present_ok = all(x["product_id"] in cur_products and cur_products[x["product_id"]]["name"] == x["name"] for x in base["products"])
            checks.append({"name": "baseline_products_present", "ok": present_ok, "detail": f"baseline_products={len(base['products'])}"})
            cur_events = {r["id"]: _event_dict(r) for r in conn.execute("SELECT * FROM events ORDER BY id").fetchall()}
            preserved = all(x["id"] in cur_events and cur_events[x["id"]] == x for x in base["events"])
            checks.append({"name": "baseline_records_preserved", "ok": preserved, "detail": f"baseline_events={len(base['events'])}"})
            ledger_ok = present_ok
            if present_ok:
                for x in base["products"]:
                    try:
                        l2 = domain.compute_ledger(conn, x["product_id"])
                        _, e2, _ = _repair_state(conn, x["product_id"])
                        ledger_ok = ledger_ok and l2["stock_matches_ledger"] and l2["qtys_consistent"] and not e2
                    except Exception:
                        ledger_ok = False
            checks.append({"name": "baseline_products_ledger_consistent", "ok": ledger_ok, "detail": "all baseline products reconcile with current events; later events allowed"})
        else:
            checks.append({"name": "baseline_products_present", "ok": False, "detail": "no valid baseline"})
            checks.append({"name": "baseline_records_preserved", "ok": False, "detail": "no valid baseline"})
            checks.append({"name": "baseline_products_ledger_consistent", "ok": False, "detail": "no valid baseline"})
        preservation_verified = all(c["ok"] for c in checks if c["name"] in ("preservation_baseline", "baseline_products_present", "baseline_records_preserved", "baseline_products_ledger_consistent", "repair_recorded", "proposal_binding"))
    return {"product_id": product_id, "final_stock": led["actual"], "pass": all(c["ok"] for c in checks), "preservation_verified": preservation_verified if proposal_id else False, "checks": checks}
