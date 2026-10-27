"""Durable, versioned, read-only case reports for synthetic investigations.

Integrity model: stored JSON is validated strictly on read. Consistency
checks detect accidental/corrupt/mismatched records; they are not a claim
of tamper-proofing against someone who can rewrite the whole database.
"""
import hashlib
import json
import sqlite3
import uuid

from app import recovery, store

SCHEMA_VERSION = 1
MAX_BLOB_BYTES = 512 * 1024
MAX_COMBINED_BYTES = 512 * 1024
MAX_EVENTS = 200
MAX_LIST = 20
MAX_DEPTH = 8
MAX_STRING = 8000
MAX_PATCH = 8000
MAX_NOTE = 500
MAX_DESCRIPTION = 2000

_USAGE_KEYS = ("requests", "input_tokens", "output_tokens", "total_tokens", "estimated_cost", "estimated_cost_usd", "cost", "cost_usd")
_COUNT_KEYS = ("requests", "read_events")
_CLAIM_KEYS = ("text", "evidence_event_ids")
_TRACE_KEYS = ("tool", "event_ids", "count")
_PREVIEW_KEYS = ("proposal", "status", "before_stock", "after_stock", "credit_qty", "reverse_event_ids", "preserved_restock_ids", "unrelated_products", "diff", "simulation_checks", "simulation_pass", "invariants", "snapshot_match", "rust_verification")
_RUST_KEYS = ("enabled", "status", "verifier_version", "snapshot_hash", "checks", "ledger_actual", "expected_stock", "outstanding_credit", "reverse_event_ids", "proposal_matches")
_PROPOSAL_KEYS = ("proposal_id", "incident_id", "product_id", "snapshot_hash", "status", "credit_qty", "reverse_event_ids", "reason")


class CaseNotFound(Exception):
    pass


class CaseInvalid(Exception):
    pass


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return (_is_int(v) or isinstance(v, float)) and v == v and v not in (float("inf"), float("-inf"))


def _copy(src):
    # Never run SQLite backup against a connection holding an open write
    # transaction: reject before backup instead of blocking/hanging.
    if getattr(src, "in_transaction", False):
        raise CaseInvalid("open_transaction")
    dst = sqlite3.connect(":memory:")
    dst.row_factory = sqlite3.Row
    try:
        src.backup(dst)
    except Exception:
        dst.close()
        raise
    return dst


def _dumps(obj):
    return json.dumps(obj, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _validate_bounded(value, depth=0):
    """Reject (never silently truncate/shrink) oversized or non-finite data."""
    if depth > MAX_DEPTH:
        raise CaseInvalid("case_too_deep")
    if value is None or isinstance(value, bool) or _is_int(value):
        return value
    if isinstance(value, float):
        if not _is_num(value):
            raise CaseInvalid("nonfinite_value")
        return value
    if isinstance(value, str):
        if len(value) > MAX_STRING:
            raise CaseInvalid("string_too_large")
        return value
    if isinstance(value, (list, tuple)):
        if len(value) > MAX_EVENTS:
            raise CaseInvalid("array_too_large")
        return [_validate_bounded(v, depth + 1) for v in value]
    if isinstance(value, dict):
        if len(value) > 100:
            raise CaseInvalid("object_too_large")
        out = {}
        for k, v in value.items():
            if not isinstance(k, str) or len(k) > 200:
                raise CaseInvalid("bad_key")
            out[k] = _validate_bounded(v, depth + 1)
        return out
    raise CaseInvalid("unsupported_value")


def _bounded(obj):
    obj = _validate_bounded(obj)
    try:
        encoded = _dumps(obj).encode()
    except Exception:
        raise CaseInvalid("case_not_serializable")
    if len(encoded) > MAX_BLOB_BYTES:
        raise CaseInvalid("case_too_large")
    return obj


def _loads_strict(text):
    if not isinstance(text, str):
        raise CaseInvalid("case_record_invalid")
    if len(text.encode()) > MAX_BLOB_BYTES:
        raise CaseInvalid("case_record_invalid")
    def no_dup(pairs):
        out = {}
        for k, v in pairs:
            if k in out:
                raise CaseInvalid("duplicate_key")
            out[k] = v
        return out
    def no_const(x):
        raise CaseInvalid("nonfinite_value")
    try:
        return json.loads(text, object_pairs_hook=no_dup, parse_constant=no_const)
    except CaseInvalid:
        raise
    except Exception:
        raise CaseInvalid("case_record_invalid")


def _row_dict(r):
    return dict(r) if r is not None else None


def _event_from_row(d):
    # Exact fields; length/type validation is done by callers/_bounded.
    return {
        "id": d.get("id"), "product_id": d.get("product_id"), "kind": d.get("kind"),
        "qty": d.get("qty"), "order_id": d.get("order_id"), "ts": d.get("ts"),
        "note": d.get("note") if d.get("note") is not None else "",
        "reversed_ids": d.get("reversed_ids") if d.get("reversed_ids") is not None else "[]",
        "repair_proposal_id": d.get("repair_proposal_id"),
    }


def _check_event_shapes(events, product_id):
    if not isinstance(events, list) or len(events) > MAX_EVENTS:
        raise CaseInvalid("case_record_invalid")
    seen = set()
    for e in events:
        if not isinstance(e, dict):
            raise CaseInvalid("case_record_invalid")
        if not _is_int(e.get("id")) or e["id"] <= 0 or e["id"] in seen:
            raise CaseInvalid("case_record_invalid")
        seen.add(e["id"])
        if e.get("product_id") != product_id:
            raise CaseInvalid("case_binding_invalid")
        if not isinstance(e.get("kind"), str) or len(e["kind"]) > 100:
            raise CaseInvalid("case_record_invalid")
        if not _is_int(e.get("qty")) or not _is_int(e.get("ts")):
            raise CaseInvalid("case_record_invalid")
        if e.get("order_id") is not None and (not isinstance(e["order_id"], str) or len(e["order_id"]) > 200):
            raise CaseInvalid("case_record_invalid")
        if not isinstance(e.get("note"), str) or len(e["note"]) > MAX_NOTE:
            raise CaseInvalid("case_record_invalid")
        if not isinstance(e.get("reversed_ids"), str) or len(e["reversed_ids"]) > 2000:
            raise CaseInvalid("case_record_invalid")
        # Raw empty reversed_ids is supported by the domain/Rust parser and
        # must be preserved exactly in the fingerprint; other values must be
        # a JSON list of positive ints (exact, not trimmed).
        if e["reversed_ids"] != "":
            rev = _loads_strict(e["reversed_ids"])
            if not isinstance(rev, list) or any(not _is_int(x) or x <= 0 for x in rev):
                raise CaseInvalid("case_record_invalid")
        if e.get("repair_proposal_id") is not None and (not isinstance(e["repair_proposal_id"], str) or len(e["repair_proposal_id"]) > 200):
            raise CaseInvalid("case_record_invalid")
    return seen


def _fetch_events_limited(conn, product_id):
    rows = conn.execute(
        "SELECT * FROM events WHERE product_id=? ORDER BY id LIMIT ?", (product_id, MAX_EVENTS + 1)
    ).fetchall()
    return rows


def capture_before(conn, incident_id):
    """Consistent BEFORE snapshot from a disposable copy, closed before Meta."""
    snap = None
    try:
        snap = _copy(conn)
        inc = store.get_incident(snap, incident_id)
        if inc is None:
            return {"available": False, "reason": "incident_not_found", "incident_id": incident_id, "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
        incd = _row_dict(inc)
        product_id = incd.get("product_id")
        prod = store.get_product(snap, product_id) if isinstance(product_id, str) else None
        if prod is None:
            return {"available": False, "reason": "oversized_or_missing_scope", "incident_id": incident_id, "product_id": product_id, "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
        rows = _fetch_events_limited(snap, product_id)
        from app import domain
        patch = domain.get_patch_text()
        if len(rows) > MAX_EVENTS:
            return {"available": False, "reason": "oversized_or_missing_scope", "incident_id": incident_id, "product_id": product_id, "event_count": len(rows), "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
        if not isinstance(patch, str) or len(patch) > MAX_PATCH:
            return {"available": False, "reason": "oversized_or_missing_scope", "incident_id": incident_id, "product_id": product_id, "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
        if not isinstance(incd.get("description"), str) or len(incd["description"]) > MAX_DESCRIPTION:
            return {"available": False, "reason": "oversized_or_missing_scope", "incident_id": incident_id, "product_id": product_id, "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
        ev = [_event_from_row(dict(r)) for r in rows]
        _check_event_shapes(ev, product_id)
        for e in ev:
            if not _is_int(e["qty"]):
                raise CaseInvalid("case_record_invalid")
        before = {
            "available": True, "reason": None, "capture_id": "CAPTURE-" + uuid.uuid4().hex,
            "incident_id": incident_id, "product_id": product_id,
            "incident": {"id": incd.get("id"), "product_id": incd.get("product_id"), "description": incd.get("description"), "status": incd.get("status")},
            "product": {"product_id": prod["product_id"], "name": prod["name"], "stock": prod["stock"]},
            "events": ev, "event_ids": [e["id"] for e in ev],
            "patch": patch, "raw_patch": patch,
            "fingerprint": store.fingerprint(snap, product_id),
        }
        before["snapshot_hash"] = before["fingerprint"]
        if not isinstance(before["product"].get("name"), (str, type(None))) or (isinstance(before["product"].get("name"), str) and len(before["product"]["name"]) > 500):
            return {"available": False, "reason": "oversized_or_missing_scope", "incident_id": incident_id, "product_id": product_id, "capture_id": before["capture_id"]}, False
        if not _is_int(before["product"].get("stock")):
            return {"available": False, "reason": "oversized_or_missing_scope", "incident_id": incident_id, "product_id": product_id, "capture_id": before["capture_id"]}, False
        return _bounded(before), True
    except CaseInvalid:
        return {"available": False, "reason": "capture_unavailable", "incident_id": incident_id if isinstance(incident_id, str) else None, "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
    except Exception:
        return {"available": False, "reason": "capture_unavailable", "incident_id": incident_id if isinstance(incident_id, str) else None, "capture_id": "CAPTURE-" + uuid.uuid4().hex}, False
    finally:
        if snap is not None:
            try:
                snap.close()
            except Exception:
                pass


def _strict_proposal(conn, proposal_id):
    # Exact accepted recovery strict getter; malformed proposals raise.
    return recovery._get_proposal(conn, proposal_id)


def _proposal_public(p):
    if not isinstance(p, dict):
        return None
    rev = p.get("reverse_event_ids")
    if isinstance(rev, str):
        rev = _loads_strict(rev)
    if rev is None:
        rev = []
    if not isinstance(rev, list):
        raise CaseInvalid("case_record_invalid")
    pid = p.get("proposal_id") or p.get("id")
    return {
        "proposal_id": pid, "id": pid, "incident_id": p.get("incident_id"), "product_id": p.get("product_id"),
        "credit_qty": p.get("credit_qty"), "reverse_event_ids": rev,
        "snapshot_hash": p.get("snapshot_hash"), "status": p.get("status"), "reason": p.get("reason"), "created_at": p.get("created_at"),
    }


def _int_ids(v, required=False):
    if v is None and not required:
        return []
    if not isinstance(v, list):
        raise CaseInvalid("case_record_invalid")
    if any(not _is_int(x) or x <= 0 for x in v):
        raise CaseInvalid("case_record_invalid")
    return list(v)


def _claim_list(v):
    if v is None:
        return []
    if not isinstance(v, list) or len(v) > 10:
        raise CaseInvalid("case_record_invalid")
    out = []
    for it in v:
        if isinstance(it, dict):
            if not isinstance(it.get("text"), str):
                raise CaseInvalid("case_record_invalid")
            out.append({"text": it["text"], "evidence_event_ids": _int_ids(it.get("evidence_event_ids") or [])})
        elif isinstance(it, str):
            out.append({"text": it, "evidence_event_ids": []})
        else:
            raise CaseInvalid("case_record_invalid")
    return out


def _whitelist_result(result):
    r = result if isinstance(result, dict) else {}
    usage_in = r.get("usage") if isinstance(r.get("usage"), dict) else {}
    usage = {}
    for k in _USAGE_KEYS:
        if k in usage_in:
            v = usage_in[k]
            if v is not None and not _is_num(v):
                raise CaseInvalid("case_record_invalid")
            usage[k] = v  # preserve actual counts/cost, including 0 and None; never invent
    counts_in = r.get("counts") if isinstance(r.get("counts"), dict) else {}
    counts = {k: counts_in[k] for k in _COUNT_KEYS if k in counts_in and _is_int(counts_in[k])}
    trace = []
    if isinstance(r.get("trace"), list):
        for t in r["trace"][:50]:
            if isinstance(t, dict):
                trace.append({"tool": t.get("tool"), "event_ids": _int_ids(t.get("event_ids") or []), "count": t.get("count") if _is_int(t.get("count")) else None})
    proposal = None
    if r.get("proposal") is not None:
        proposal = _proposal_public(r.get("proposal"))
    proposal_id = r.get("proposal_id") or (proposal.get("proposal_id") if proposal else None)
    uncertainties = r.get("uncertainties") or []
    if not isinstance(uncertainties, list) or any(not isinstance(x, str) for x in uncertainties):
        raise CaseInvalid("case_record_invalid")
    return _bounded({
        "status": r.get("status") or "needs_review", "reason": r.get("reason") or "",
        "stop_category": r.get("stop_category") or r.get("reason") or "",
        "incident_id": r.get("incident_id"), "product_id": r.get("product_id"),
        "proposal_id": proposal_id, "proposal": proposal,
        "hypothesis": r.get("hypothesis") or "",
        "observations": _claim_list(r.get("observations")), "inferences": _claim_list(r.get("inferences")),
        "uncertainties": list(uncertainties),
        "claims_label": "model-generated claims, not authority", "explanations_label": "model-generated claims, not authority",
        "evidence_event_ids": _int_ids(r.get("evidence_event_ids") or []), "read_event_ids": _int_ids(r.get("read_event_ids") or []), "selected_event_ids": _int_ids(r.get("selected_event_ids") or []),
        "trace": trace, "counts": counts, "model": r.get("model") if isinstance(r.get("model"), str) else None, "usage": usage,
    })


def _all_cited_ids(inv):
    ids = set(inv.get("read_event_ids") or []) | set(inv.get("evidence_event_ids") or []) | set(inv.get("selected_event_ids") or [])
    for key in ("observations", "inferences"):
        for c in inv.get(key) or []:
            ids |= set(c.get("evidence_event_ids") or [])
    if isinstance(inv.get("proposal"), dict):
        ids |= set(inv["proposal"].get("reverse_event_ids") or [])
    return ids


def _binding(inv, before, snap):
    if not isinstance(before, dict) or not before.get("available"):
        return "unavailable", (before or {}).get("reason") or "capture_unavailable"
    incident_id, product_id = before.get("incident_id"), before.get("product_id")
    if inv.get("incident_id") != incident_id or inv.get("product_id") != product_id:
        return "unavailable", "changed_scope"
    inc = store.get_incident(snap, incident_id)
    prod = store.get_product(snap, product_id) if product_id else None
    if inc is None or prod is None:
        return "unavailable", "changed_scope"
    incd, prodd = _row_dict(inc), _row_dict(prod)
    if incd.get("id") != before["incident"].get("id") or incd.get("product_id") != before["incident"].get("product_id") or incd.get("description") != before["incident"].get("description") or incd.get("status") != before["incident"].get("status"):
        return "unavailable", "changed_scope"
    if prodd.get("product_id") != before["product"].get("product_id") or prodd.get("name") != before["product"].get("name") or prodd.get("stock") != before["product"].get("stock"):
        return "unavailable", "changed_scope"
    try:
        cur_fp = store.fingerprint(snap, product_id)
    except Exception:
        return "unavailable", "changed_scope"
    unchanged = cur_fp == before.get("fingerprint")
    scope = set(before.get("event_ids") or [])
    if not _all_cited_ids(inv) <= scope:
        return "unavailable", "changed_scope"
    proposal_id = inv.get("proposal_id")
    if inv.get("status") == "proposed" or proposal_id:
        if not proposal_id:
            return "unavailable", "changed_scope"
        try:
            prop = _proposal_public(_strict_proposal(snap, proposal_id))
        except Exception:
            return "unavailable", "changed_scope"
        if prop.get("incident_id") != incident_id or prop.get("product_id") != product_id:
            return "unavailable", "changed_scope"
        if prop.get("snapshot_hash") != before.get("fingerprint") or not unchanged:
            return "unavailable", "changed_scope"
        if not set(prop.get("reverse_event_ids") or []) <= scope:
            return "unavailable", "changed_scope"
        # Stored investigation proposal, if present, must match strict current proposal.
        if inv.get("proposal") is not None:
            sp = inv["proposal"]
            if sp.get("proposal_id") != proposal_id or sp.get("incident_id") != incident_id or sp.get("product_id") != product_id or sp.get("snapshot_hash") != before.get("fingerprint"):
                return "unavailable", "changed_scope"
        return "bound", None
    if unchanged and _all_cited_ids(inv) <= scope:
        return "bound", None
    return "unavailable", "changed_scope"


def _rust_public(v):
    if not isinstance(v, dict):
        raise CaseInvalid('case_record_invalid')
    if set(v) - set(_RUST_KEYS) - {'error_code'}:
        raise CaseInvalid('case_record_invalid')
    status = v.get('status')
    if status == 'verified':
        if v.get('enabled') is not True or v.get('verifier_version') != '0.1.0' or not isinstance(v.get('snapshot_hash'), str) or not v['snapshot_hash']:
            raise CaseInvalid('case_binding_invalid')
        checks = v.get('checks')
        if not isinstance(checks, list) or not checks:
            raise CaseInvalid('case_record_invalid')
        out_checks = []
        for c in checks:
            if not isinstance(c, dict) or set(c) - {'name', 'ok', 'detail'} or not isinstance(c.get('name'), str) or c.get('ok') is not True or ('detail' in c and not isinstance(c['detail'], str)):
                raise CaseInvalid('case_record_invalid')
            out_checks.append({k: c[k] for k in ('name', 'ok', 'detail') if k in c})
        if len({c['name'] for c in out_checks}) != len(out_checks) or set(c['name'] for c in out_checks) != {'protocol_version', 'stock_matches_ledger', 'qty_consistent', 'ledger_actual', 'outstanding_credit', 'expected_stock', 'reverse_event_ids', 'proposal_matches'}:
            raise CaseInvalid('case_binding_invalid')
        out = {'enabled': True, 'status': 'verified', 'verifier_version': '0.1.0', 'snapshot_hash': v['snapshot_hash'], 'checks': out_checks}
        for k in ('ledger_actual', 'expected_stock', 'outstanding_credit'):
            if not _is_int(v.get(k)):
                raise CaseInvalid('case_record_invalid')
            out[k] = v[k]
        out['reverse_event_ids'] = _int_ids(v.get('reverse_event_ids'))
        if not isinstance(v.get('proposal_matches'), bool):
            raise CaseInvalid('case_record_invalid')
        out['proposal_matches'] = v['proposal_matches']
        return _validate_bounded(out)
    # Failure shape from rust_verifier.failure_public: preserve unknown
    # numeric values as absent/None; never invent a pass or version.
    out = {'enabled': v.get('enabled') if isinstance(v.get('enabled'), bool) else True, 'status': status if isinstance(status, str) else 'unavailable', 'snapshot_hash': v.get('snapshot_hash') if isinstance(v.get('snapshot_hash'), str) else ''}
    if isinstance(v.get('error_code'), str):
        out['error_code'] = v['error_code']
    if v.get('verifier_version') == '0.1.0':
        out['verifier_version'] = '0.1.0'
    return _validate_bounded(out)


def _rust_verified(v, snapshot_hash):
    r = _rust_public(v)
    if r.get('status') != 'verified' or r.get('snapshot_hash') != snapshot_hash or r.get('proposal_matches') is not True:
        raise CaseInvalid('case_binding_invalid')
    return r


def _initial_preview(snap, inv, before, binding):
    if binding != "bound" or not inv.get("proposal_id"):
        return None
    try:
        preview = recovery.preview_proposal(snap, inv["proposal_id"])
    except Exception:
        return {"available": False, "status": "unavailable", "reason": "initial_preview_unavailable"}
    if not isinstance(preview, dict):
        return {"available": False, "status": "unavailable", "reason": "initial_preview_unavailable"}
    # Exact recovery.preview_proposal contract: IDs/hash live in the nested
    # proposal; no root aliases are invented.
    try:
        prop = preview.get('proposal')
        if not isinstance(prop, dict) or set(prop) != set(_PROPOSAL_KEYS):
            raise CaseInvalid('case_binding_invalid')
        if prop.get('proposal_id') != inv['proposal_id'] or prop.get('incident_id') != before.get('incident_id') or prop.get('product_id') != before.get('product_id') or prop.get('snapshot_hash') != before.get('fingerprint'):
            raise CaseInvalid('case_binding_invalid')
        if preview.get('snapshot_match') is not True or preview.get('status') != prop.get('status'):
            raise CaseInvalid('case_binding_invalid')
        if set(preview) - set(_PREVIEW_KEYS):
            raise CaseInvalid('case_binding_invalid')
        rust = _rust_verified(preview.get('rust_verification'), before.get('fingerprint'))
        if rust.get('reverse_event_ids') != list(prop.get('reverse_event_ids') or []) or rust.get('ledger_actual') != preview.get('before_stock') or rust.get('expected_stock') != preview.get('after_stock') or rust.get('outstanding_credit') != preview.get('credit_qty'):
            raise CaseInvalid('case_binding_invalid')
        if preview.get('credit_qty') != prop.get('credit_qty') or preview.get('reverse_event_ids') != prop.get('reverse_event_ids'):
            raise CaseInvalid('case_binding_invalid')
        out = {}
        for k in _PREVIEW_KEYS:
            if k in preview:
                out[k] = rust if k == 'rust' and False else _rust_public(preview[k]) if k == 'rust_verification' else preview[k]
        out['proposal'] = {k: prop[k] for k in _PROPOSAL_KEYS}
        return _bounded(out)
    except Exception:
        return {'available': False, 'status': 'unavailable', 'reason': 'initial_preview_unavailable'}


def _case_id_for(before):
    cap = before.get("capture_id") if isinstance(before, dict) else None
    if isinstance(cap, str) and cap:
        return "CASE-" + hashlib.sha256(cap.encode()).hexdigest()[:16]
    return "CASE-" + uuid.uuid4().hex[:16]


def record_case(conn, actor_id, before, result):
    if getattr(conn, "in_transaction", False):
        raise CaseInvalid("recording_unavailable")
    inv = _whitelist_result(result)
    snap = None
    try:
        snap = _copy(conn)
        binding, reason = _binding(inv, before, snap)
        preview = _initial_preview(snap, inv, before if isinstance(before, dict) else {}, binding)
    finally:
        if snap is not None:
            try:
                snap.close()
            except Exception:
                pass
    before_out = dict(before) if isinstance(before, dict) else {"available": False, "reason": "capture_unavailable"}
    if not isinstance(before_out.get("capture_id"), str) or not before_out["capture_id"]:
        before_out["capture_id"] = "CAPTURE-" + uuid.uuid4().hex
    # Forged/mismatched result scope: never retarget the case. Store a minimal
    # unavailable investigation indexed under the original captured request.
    if isinstance(before, dict) and before.get("incident_id") and (inv.get("incident_id") != before.get("incident_id") or inv.get("product_id") != before.get("product_id")):
        binding, reason = "unavailable", "changed_scope"
        preview = None
        inv = _bounded({
            "status": "needs_review", "reason": "changed_scope", "stop_category": "changed_scope",
            "incident_id": before.get("incident_id"), "product_id": before.get("product_id"),
            "proposal_id": None, "proposal": None, "hypothesis": "", "observations": [], "inferences": [], "uncertainties": [],
            "claims_label": "model-generated claims, not authority", "explanations_label": "model-generated claims, not authority",
            "evidence_event_ids": [], "read_event_ids": [], "selected_event_ids": [], "trace": [], "counts": {}, "model": inv.get("model"), "usage": inv.get("usage") or {},
        })
    before_out["evidence_binding"] = binding
    before_out["binding"] = binding
    before_out["binding_label"] = "bound" if binding == "bound" else "unavailable"
    if reason:
        before_out["binding_reason"] = reason
    if binding != "bound":
        before_out["caveat"] = "before snapshot is not labelled as actual model read history because scope changed or could not be matched"
    case_id = _case_id_for(before_out)
    import time as _time
    created = int(_time.time())
    incident_id = before_out.get("incident_id") if before_out.get("incident_id") else inv.get("incident_id")
    product_id = before_out.get("product_id") if before_out.get("product_id") else inv.get("product_id")
    if incident_id != inv.get("incident_id") or product_id != inv.get("product_id"):
        incident_id, product_id = inv.get("incident_id"), inv.get("product_id")
    record = _bounded({
        "schema_version": SCHEMA_VERSION, "case_id": case_id, "data_label": "synthetic",
        "incident_id": incident_id, "product_id": product_id, "proposal_id": inv.get("proposal_id"),
        "recorded_actor_id": actor_id, "actor_id": actor_id, "created_at": created,
        "before": before_out, "investigation": inv, "initial_preview": preview, "initial_proposal": inv.get("proposal"),
    })
    blob_before, blob_inv = _dumps(record["before"]), _dumps(record["investigation"])
    blob_prev = _dumps(record["initial_preview"]) if record.get("initial_preview") is not None else None
    if len(blob_before.encode()) + len(blob_inv.encode()) + len((blob_prev or "").encode()) > MAX_COMBINED_BYTES:
        raise CaseInvalid("case_too_large")
    if conn.in_transaction:
        raise CaseInvalid("recording_unavailable")
    existing = conn.execute("SELECT * FROM case_records WHERE case_id=?", (case_id,)).fetchone()
    if existing is not None:
        if (existing["incident_id"] == record["incident_id"] and existing["product_id"] == record["product_id"]
                and (existing["proposal_id"] or None) == (record["proposal_id"] or None)
                and existing["before_json"] == blob_before and existing["investigation_json"] == blob_inv
                and (existing["initial_preview_json"] or None) == blob_prev and existing["actor_id"] == str(actor_id or "")):
            return case_id  # idempotent retry of same capture/helper call
        raise CaseInvalid("capture_retry_mismatch")
    conn.execute(
        "INSERT INTO case_records(case_id, schema_version, incident_id, product_id, proposal_id, actor_id, created_at, before_json, investigation_json, initial_preview_json) VALUES (?,?,?,?,?,?,?,?,?,?)",
        (case_id, SCHEMA_VERSION, record["incident_id"], record["product_id"], record["proposal_id"], str(actor_id or ""), created, blob_before, blob_inv, blob_prev),
    )
    conn.commit()
    return case_id


_BEFORE_KEYS = {'available', 'reason', 'capture_id', 'incident_id', 'product_id', 'incident', 'product', 'events', 'event_ids', 'event_count', 'patch', 'raw_patch', 'fingerprint', 'snapshot_hash', 'evidence_binding', 'binding', 'binding_label', 'binding_reason', 'caveat'}
_INV_KEYS = {'status', 'reason', 'stop_category', 'incident_id', 'product_id', 'proposal_id', 'proposal', 'hypothesis', 'observations', 'inferences', 'uncertainties', 'claims_label', 'explanations_label', 'evidence_event_ids', 'read_event_ids', 'selected_event_ids', 'trace', 'counts', 'model', 'usage'}

def _validate_before(before, row):
    if not isinstance(before, dict):
        raise CaseInvalid("case_record_invalid")
    _validate_bounded(before)
    if set(before) - _BEFORE_KEYS:
        raise CaseInvalid('case_record_invalid')
    if not isinstance(before.get('capture_id'), str) or not before['capture_id']:
        raise CaseInvalid('case_record_invalid')
    if before.get("available") is True:
        if before.get("incident_id") != row["incident_id"] or before.get("product_id") != row["product_id"]:
            raise CaseInvalid("case_binding_invalid")
        inc, prod, events = before.get("incident"), before.get("product"), before.get("events")
        if not isinstance(inc, dict) or not isinstance(prod, dict) or set(inc) != {'id', 'product_id', 'description', 'status'} or set(prod) != {'product_id', 'name', 'stock'}:
            raise CaseInvalid("case_record_invalid")
        if isinstance(events, list) and any(not isinstance(e, dict) or set(e) != {'id', 'product_id', 'kind', 'qty', 'order_id', 'ts', 'note', 'reversed_ids', 'repair_proposal_id'} for e in events):
            raise CaseInvalid('case_record_invalid')
        if inc.get("id") != row["incident_id"] or inc.get("product_id") != row["product_id"]:
            raise CaseInvalid("case_binding_invalid")
        if not isinstance(inc.get("description"), str) or len(inc["description"]) > MAX_DESCRIPTION or not isinstance(inc.get("status"), str):
            raise CaseInvalid("case_record_invalid")
        if prod.get("product_id") != row["product_id"] or not _is_int(prod.get("stock")) or not (prod.get("name") is None or isinstance(prod.get("name"), str)):
            raise CaseInvalid("case_binding_invalid")
        seen = _check_event_shapes(events, row["product_id"])
        if before.get("event_ids") != [e["id"] for e in events]:
            raise CaseInvalid("case_binding_invalid")
        patch = before.get("patch")
        if not isinstance(patch, str) or len(patch) > MAX_PATCH or before.get("raw_patch") != patch:
            raise CaseInvalid("case_record_invalid")
        fp = store.fingerprint_payload(prod, events)
        if before.get("fingerprint") != fp or before.get("snapshot_hash") != fp:
            raise CaseInvalid("case_binding_invalid")
        return seen
    if before.get("available") is False:
        if not isinstance(before.get("reason"), str) or not before['reason']:
            raise CaseInvalid("case_record_invalid")
        if 'event_count' in before and (not _is_int(before.get('event_count')) or before['event_count'] < 0 or before['event_count'] > MAX_EVENTS + 1):
            raise CaseInvalid('case_record_invalid')
        for k in ('incident_id', 'product_id'):
            if k in before and before[k] is not None and not isinstance(before[k], str):
                raise CaseInvalid('case_record_invalid')
        if before.get('incident_id') not in (None, row['incident_id']) or before.get('product_id') not in (None, row['product_id']):
            raise CaseInvalid('case_binding_invalid')
        return set()
    raise CaseInvalid("case_record_invalid")


def _validate_preview(prev, row, before):
    # Exact explicit marker is valid stored failure info in any branch;
    # build_report must keep it needs_review and never actionable.
    if prev == {'available': False, 'status': 'unavailable', 'reason': 'initial_preview_unavailable'}:
        return
    if not isinstance(before, dict) or before.get('available') is not True or not row['proposal_id']:
        raise CaseInvalid('case_binding_invalid')
    if set(prev) != set(_PREVIEW_KEYS):
        raise CaseInvalid('case_record_invalid')
    prop = prev.get('proposal')
    if not isinstance(prop, dict) or set(prop) != set(_PROPOSAL_KEYS):
        raise CaseInvalid('case_record_invalid')
    if prop.get('proposal_id') != row['proposal_id'] or prop.get('incident_id') != row['incident_id'] or prop.get('product_id') != row['product_id'] or prop.get('snapshot_hash') != before.get('fingerprint'):
        raise CaseInvalid('case_binding_invalid')
    if prev.get('status') != prop.get('status') or prev.get('snapshot_match') is not True:
        raise CaseInvalid('case_binding_invalid')
    if not _is_int(prev.get('before_stock')) or not _is_int(prev.get('after_stock')) or not _is_int(prev.get('credit_qty')) or prev.get('credit_qty') != prop.get('credit_qty') or prev.get('reverse_event_ids') != prop.get('reverse_event_ids'):
        raise CaseInvalid('case_binding_invalid')
    _int_ids(prev.get('preserved_restock_ids'))
    if not isinstance(prev.get('unrelated_products'), list) or any(not isinstance(x, dict) or set(x) != {'product_id', 'stock'} or not isinstance(x.get('product_id'), str) or not _is_int(x.get('stock')) for x in prev['unrelated_products']):
        raise CaseInvalid('case_record_invalid')
    diff = prev.get('diff')
    if not isinstance(diff, dict) or set(diff) != {'stock_delta', 'events_added', 'restocks_untouched'} or diff.get('stock_delta') != prev['after_stock'] - prev['before_stock'] or not _is_int(diff.get('events_added')):
        raise CaseInvalid('case_binding_invalid')
    _int_ids(diff.get('restocks_untouched'))
    if not _is_int(prev.get('before_stock')) or prev['before_stock'] != before['product'].get('stock') or prev.get('after_stock') != prev['before_stock'] + prev['credit_qty']:
        raise CaseInvalid('case_binding_invalid')
    for key in ('simulation_checks', 'invariants'):
        checks = prev.get(key)
        if not isinstance(checks, list) or not checks or any(not isinstance(c, dict) or set(c) - {'name', 'ok', 'detail'} or not isinstance(c.get('name'), str) or c.get('ok') is not True or ('detail' in c and not isinstance(c['detail'], str)) for c in checks):
            raise CaseInvalid('case_record_invalid')
    if prev.get('simulation_pass') is not True:
        raise CaseInvalid('case_record_invalid')
    rust = _rust_verified(prev.get('rust_verification'), before.get('fingerprint'))
    if rust.get('reverse_event_ids') != prop.get('reverse_event_ids') or rust.get('ledger_actual') != prev.get('before_stock') or rust.get('expected_stock') != prev.get('after_stock') or rust.get('outstanding_credit') != prev.get('credit_qty'):
        raise CaseInvalid('case_binding_invalid')


def _validate_inv(inv, row, scope, before=None):
    if not isinstance(inv, dict):
        raise CaseInvalid("case_record_invalid")
    _validate_bounded(inv)
    if inv.get("incident_id") != row["incident_id"] or inv.get("product_id") != row["product_id"]:
        raise CaseInvalid("case_binding_invalid")
    if (inv.get("proposal_id") or None) != (row["proposal_id"] or None):
        raise CaseInvalid("case_binding_invalid")
    if not isinstance(inv.get("status"), str) or not isinstance(inv.get("hypothesis", ""), str):
        raise CaseInvalid("case_record_invalid")
    for k in ("read_event_ids", "evidence_event_ids", "selected_event_ids"):
        _int_ids(inv.get(k) or [])
    _claim_list(inv.get("observations") or [])
    _claim_list(inv.get("inferences") or [])
    if set(inv) - _INV_KEYS:
        raise CaseInvalid('case_record_invalid')
    usage = inv.get('usage')
    if not isinstance(usage, dict) or set(usage) - set(_USAGE_KEYS) or any(v is not None and not _is_num(v) for v in usage.values()):
        raise CaseInvalid('case_record_invalid')
    counts = inv.get('counts')
    if not isinstance(counts, dict) or set(counts) - set(_COUNT_KEYS) or any(not _is_int(v) for v in counts.values()):
        raise CaseInvalid('case_record_invalid')
    trace = inv.get('trace')
    if not isinstance(trace, list) or any(not isinstance(t, dict) or set(t) != set(_TRACE_KEYS) or not (t.get('tool') is None or isinstance(t.get('tool'), str)) or (t.get('count') is not None and not _is_int(t.get('count'))) for t in trace):
        raise CaseInvalid('case_record_invalid')
    for t in trace: _int_ids(t.get('event_ids'))
    for key in ('observations', 'inferences'):
        claims = inv.get(key)
        if not isinstance(claims, list) or any(not isinstance(c, dict) or set(c) != set(_CLAIM_KEYS) for c in claims):
            raise CaseInvalid('case_record_invalid')
    for k in ('reason', 'stop_category', 'claims_label', 'explanations_label'):
        if k in inv and not isinstance(inv.get(k), str): raise CaseInvalid('case_record_invalid')
    if not isinstance(inv.get('uncertainties'), list) or any(not isinstance(x, str) for x in inv.get('uncertainties') or []): raise CaseInvalid('case_record_invalid')
    if inv.get('model') is not None and not isinstance(inv.get('model'), str): raise CaseInvalid('case_record_invalid')
    if scope:
        read = inv.get('read_event_ids') or []
        if len(set(read)) != len(read) or not set(read) <= scope: raise CaseInvalid('case_binding_invalid')
        if sorted(inv.get('selected_event_ids') or []) != sorted(read): raise CaseInvalid('case_binding_invalid')
        cited = set(inv.get('evidence_event_ids') or [])
        for key in ('observations', 'inferences'):
            for c in inv.get(key) or []: cited |= set(c.get('evidence_event_ids') or [])
        for t in inv.get('trace') or []: cited |= set(t.get('event_ids') or [])
        if isinstance(inv.get('proposal'), dict): cited |= set(inv['proposal'].get('reverse_event_ids') or [])
        if not cited <= set(read): raise CaseInvalid('case_binding_invalid')
        if not _all_cited_ids(inv) <= scope: raise CaseInvalid('case_binding_invalid')
    prop = inv.get('proposal')
    if prop is not None:
        if not isinstance(prop, dict) or set(prop) - {'proposal_id', 'id', 'incident_id', 'product_id', 'credit_qty', 'reverse_event_ids', 'snapshot_hash', 'status', 'reason', 'created_at'}:
            raise CaseInvalid('case_record_invalid')
        p = _proposal_public(prop)
        if p.get('proposal_id') != row['proposal_id'] or p.get('id') != p.get('proposal_id') or p.get('incident_id') != row['incident_id'] or p.get('product_id') != row['product_id']:
            raise CaseInvalid('case_binding_invalid')
        if not _is_int(p.get('credit_qty')) or not isinstance(p.get('status'), str):
            raise CaseInvalid('case_record_invalid')
        _int_ids(p.get('reverse_event_ids'))
        if scope and not set(p.get('reverse_event_ids') or []) <= scope:
            raise CaseInvalid('case_binding_invalid')
        if isinstance(before, dict) and before.get('available') is True and p.get('snapshot_hash') != before.get('fingerprint'):
            raise CaseInvalid('case_binding_invalid')


def _load_record(row):
    # Size-check the entire row BEFORE any JSON decode.
    blobs = [row["before_json"], row["investigation_json"], row["initial_preview_json"] or ""]
    if sum(len(b.encode()) for b in blobs) > MAX_COMBINED_BYTES or any(len(b.encode()) > MAX_BLOB_BYTES for b in blobs):
        raise CaseInvalid("case_record_invalid")
    try:
        version = row["schema_version"]
    except Exception:
        raise CaseInvalid("case_record_invalid")
    if version != SCHEMA_VERSION:
        raise CaseInvalid("case_record_invalid")
    before = _loads_strict(row["before_json"])
    inv = _loads_strict(row["investigation_json"])
    prev = _loads_strict(row["initial_preview_json"]) if row["initial_preview_json"] else None
    if not isinstance(before, dict) or not isinstance(inv, dict) or (prev is not None and not isinstance(prev, dict)):
        raise CaseInvalid("case_record_invalid")
    scope = _validate_before(before, row)
    _validate_inv(inv, row, scope, before)
    if prev is not None:
        _validate_bounded(prev)
        _validate_preview(prev, row, before)
        if isinstance(prev.get('proposal'), dict):
            pp, ip = prev['proposal'], inv.get('proposal')
            if not isinstance(ip, dict): raise CaseInvalid('case_binding_invalid')
            for k in ('proposal_id', 'incident_id', 'product_id', 'snapshot_hash', 'credit_qty', 'reverse_event_ids'):
                if ip.get(k) != pp.get(k): raise CaseInvalid('case_binding_invalid')
            if ip.get('id', ip.get('proposal_id')) != pp.get('proposal_id'): raise CaseInvalid('case_binding_invalid')
    return {
        "schema_version": SCHEMA_VERSION, "case_id": row["case_id"], "data_label": "synthetic",
        "incident_id": row["incident_id"], "product_id": row["product_id"], "proposal_id": row["proposal_id"],
        "recorded_actor_id": row["actor_id"], "actor_id": row["actor_id"], "created_at": row["created_at"],
        "before": before, "investigation": inv, "initial_preview": prev, "initial_proposal": inv.get("proposal"),
    }


def list_cases(conn, incident_id):
    snap = _copy(conn)
    try:
        rows = snap.execute("SELECT * FROM case_records WHERE incident_id=? ORDER BY created_at DESC, case_id DESC LIMIT ?", (incident_id, MAX_LIST)).fetchall()
        out = []
        for r in rows:
            try:
                rec = _load_record(r)
                inv = rec["investigation"]
                out.append({"case_id": rec["case_id"], "incident_id": rec["incident_id"], "product_id": rec["product_id"], "proposal_id": rec["proposal_id"], "recorded_actor_id": rec["recorded_actor_id"], "created_at": rec["created_at"], "status": inv.get("status"), "stop_category": inv.get("stop_category"), "data_label": "synthetic"})
            except Exception:
                out.append({"case_id": r["case_id"], "incident_id": r["incident_id"], "product_id": r["product_id"], "proposal_id": r["proposal_id"], "recorded_actor_id": r["actor_id"], "created_at": r["created_at"], "status": "needs_review", "data_label": "synthetic", "record_status": "invalid"})
        return out
    finally:
        snap.close()


def _verification_passed(v):
    """ONLY the exact accepted verify_product contract."""
    if not isinstance(v, dict):
        return None
    if v.get("pass") is not True or v.get("preservation_verified") is not True:
        return False
    checks = v.get("checks")
    if not isinstance(checks, list) or not checks:
        return False
    return all(isinstance(c, dict) and c.get("ok") is True for c in checks)


def _audit_rust_public(v):
    # Actual audit Rust shape omits enabled/checks/computed quantities.
    if not isinstance(v, dict) or set(v) != {'verifier_version', 'snapshot_hash', 'ledger_actual', 'expected_stock', 'outstanding_credit', 'reverse_event_ids'}:
        raise CaseInvalid('case_record_invalid')
    if v.get('verifier_version') != '0.1.0' or not isinstance(v.get('snapshot_hash'), str) or not v['snapshot_hash']:
        raise CaseInvalid('case_record_invalid')
    out = {'verifier_version': '0.1.0', 'snapshot_hash': v['snapshot_hash']}
    for k in ('ledger_actual', 'expected_stock', 'outstanding_credit'):
        if not _is_int(v.get(k)):
            raise CaseInvalid('case_record_invalid')
        out[k] = v[k]
    out['reverse_event_ids'] = _int_ids(v.get('reverse_event_ids'))
    return out


def _audit_detail_public(detail):
    unavailable = {'available': False, 'reason': 'unsupported_audit_detail'}
    if not isinstance(detail, str) or len(detail) > MAX_STRING or not detail.strip().startswith('{'):
        return unavailable
    try:
        parsed = _loads_strict(detail.strip())
        if not isinstance(parsed, dict):
            return unavailable
        keys = set(parsed)
        if keys == {'approval_id', 'snapshot', 'actor_id', 'rust_verification'}:
            if not isinstance(parsed['approval_id'], str) or not isinstance(parsed['snapshot'], str) or not isinstance(parsed['actor_id'], str):
                return unavailable
            return {'approval_id': parsed['approval_id'], 'snapshot': parsed['snapshot'], 'actor_id': parsed['actor_id'], 'rust_verification': _audit_rust_public(parsed['rust_verification'])}
        if keys == {'approval_id', 'credit', 'before', 'after', 'reversed', 'actor_id', 'rust_verification'}:
            if not isinstance(parsed['approval_id'], str) or not isinstance(parsed['actor_id'], str) or not all(_is_int(parsed[k]) for k in ('credit', 'before', 'after')):
                return unavailable
            return {'approval_id': parsed['approval_id'], 'credit': parsed['credit'], 'before': parsed['before'], 'after': parsed['after'], 'reversed': _int_ids(parsed['reversed']), 'actor_id': parsed['actor_id'], 'rust_verification': _audit_rust_public(parsed['rust_verification'])}
        return unavailable
    except Exception:
        return unavailable


def _approval_public(r):
    d = dict(r)
    out = {'id': d.get('id'), 'proposal_id': d.get('proposal_id'), 'snapshot_hash': d.get('snapshot_hash'), 'created_at': d.get('created_at'), 'actor_id': d.get('actor_id')}
    baseline = d.get('baseline_json')
    if isinstance(baseline, str) and baseline.strip().startswith('{'):
        try:
            parsed = _loads_strict(baseline)
            _keys = {'version', 'proposal_id', 'product_id', 'snapshot_hash', 'before_stock', 'after_stock', 'credit_qty', 'reverse_event_ids', 'restock_ids', 'restock_total', 'unrelated', 'products', 'events'}
            if not isinstance(parsed, dict) or set(parsed) != _keys or not _is_int(parsed.get('version')) or parsed['version'] != 2:
                raise CaseInvalid('case_record_invalid')
            if parsed.get('proposal_id') != d.get('proposal_id') or parsed.get('snapshot_hash') != d.get('snapshot_hash') or not isinstance(parsed.get('product_id'), str) or not parsed['product_id']:
                raise CaseInvalid('case_binding_invalid')
            for k in ('before_stock', 'after_stock', 'credit_qty', 'restock_total'):
                if not _is_int(parsed.get(k)): raise CaseInvalid('case_record_invalid')
            if parsed['after_stock'] != parsed['before_stock'] + parsed['credit_qty']: raise CaseInvalid('case_binding_invalid')
            b = {'available': True, 'version': 2, 'proposal_id': parsed['proposal_id'], 'product_id': parsed['product_id'], 'snapshot_hash': parsed['snapshot_hash']}
            for k in ('before_stock', 'after_stock', 'credit_qty', 'restock_total'): b[k] = parsed[k]
            for k in ('reverse_event_ids', 'restock_ids'): b[k] = _int_ids(parsed.get(k))
            if not isinstance(parsed.get('unrelated'), list) or len(parsed['unrelated']) > 20 or any(not isinstance(x, dict) or set(x) != {'product_id', 'stock'} or not isinstance(x.get('product_id'), str) or not _is_int(x.get('stock')) for x in parsed['unrelated']): raise CaseInvalid('case_record_invalid')
            b['unrelated'] = [dict(x) for x in parsed['unrelated']]
            if not isinstance(parsed.get('products'), list) or not parsed['products'] or len(parsed['products']) > 20 or any(not isinstance(x, dict) or set(x) != {'product_id', 'name', 'stock'} or not isinstance(x.get('product_id'), str) or not (x.get('name') is None or isinstance(x.get('name'), str)) or not _is_int(x.get('stock')) for x in parsed['products']): raise CaseInvalid('case_record_invalid')
            if parsed['product_id'] not in [x['product_id'] for x in parsed['products']]: raise CaseInvalid('case_binding_invalid')
            b['products'] = [dict(x) for x in parsed['products']]
            if not isinstance(parsed.get('events'), list) or len(parsed['events']) > MAX_EVENTS: raise CaseInvalid('case_record_invalid')
            _product_ids = [x['product_id'] for x in parsed['products']]
            if len(set(_product_ids)) != len(_product_ids): raise CaseInvalid('case_binding_invalid')
            _groups = {pid: [] for pid in _product_ids}; _seen_event_ids = set()
            for _e in parsed['events']:
                if not isinstance(_e, dict) or set(_e) != {'id', 'product_id', 'kind', 'qty', 'order_id', 'ts', 'note', 'reversed_ids', 'repair_proposal_id'} or _e.get('product_id') not in _groups or _e.get('id') in _seen_event_ids: raise CaseInvalid('case_binding_invalid')
                _seen_event_ids.add(_e.get('id')); _groups[_e['product_id']].append(_e)
            for _pid, _evs in _groups.items(): _check_event_shapes(_evs, _pid)
            _target = next(x for x in parsed['products'] if x['product_id'] == parsed['product_id'])
            if _target.get('stock') != parsed['before_stock'] or store.fingerprint_payload(_target, _groups[parsed['product_id']]) != parsed['snapshot_hash']: raise CaseInvalid('case_binding_invalid')
            b['events'] = [dict(x) for x in parsed['events']]
            out['baseline'] = _validate_bounded(b)
        except Exception:
            out['baseline'] = {'available': False, 'reason': 'malformed_baseline'}
    elif baseline not in (None, '', '{}'):
        out['baseline'] = {'available': False, 'reason': 'malformed_baseline'}
    return out


def build_report(conn, case_id):
    """Derive a report from ONE consistent disposable copy. Never writes."""
    snap = _copy(conn)
    try:
        row = snap.execute("SELECT * FROM case_records WHERE case_id=?", (case_id,)).fetchone()
        if row is None:
            raise CaseNotFound("case not found")
        base = {"schema_version": SCHEMA_VERSION, "case_id": case_id, "data_label": "synthetic", "recorded_actor_id": row["actor_id"], "actor_id": row["actor_id"], "created_at": row["created_at"], "incident_id": row["incident_id"], "product_id": row["product_id"], "proposal_id": row["proposal_id"]}
        try:
            rec = _load_record(row)
        except Exception:
            return {**base, "record_status": "invalid", "recovery_state": "needs_review", "before": None, "investigation": None, "initial_preview": None, "initial_proposal": None, "current": None, "final_verification": None, "verification_status": "not_run"}
        incident_id, product_id, proposal_id = rec["incident_id"], rec["product_id"], rec["proposal_id"]
        inc = store.get_incident(snap, incident_id) if incident_id else None
        prod = store.get_product(snap, product_id) if product_id else None
        if inc is None or prod is None or dict(inc).get("product_id") != product_id:
            return {**base, "record_status": "invalid", "recovery_state": "needs_review", "before": rec["before"], "investigation": rec["investigation"], "initial_preview": rec["initial_preview"], "initial_proposal": rec.get("initial_proposal"), "current": None, "final_verification": None, "verification_status": "not_run"}
        event_rows = _fetch_events_limited(snap, product_id)
        if len(event_rows) > MAX_EVENTS:
            current = {"available": False, "reason": "oversized_current_events", "event_count": len(event_rows)}
            return {**base, "record_status": "ok", "recorded_at": rec["created_at"], "before": rec["before"], "investigation": rec["investigation"], "model_claims": rec["investigation"], "claims_label": "model-generated claims, not authority", "initial_proposal": rec.get("initial_proposal"), "initial_preview": rec["initial_preview"], "current": current, "current_proposal": None, "approvals": [], "audit": [], "final_verification": None, "verification_status": "not_run", "recovery_state": "needs_review"}
        events = [_event_from_row(dict(r)) for r in event_rows]
        try:
            _check_event_shapes(events, product_id)
            _bounded(events)
        except Exception:
            current = {"available": False, "reason": "invalid_current_events"}
            return {**base, "record_status": "ok", "recorded_at": rec["created_at"], "before": rec["before"], "investigation": rec["investigation"], "model_claims": rec["investigation"], "claims_label": "model-generated claims, not authority", "initial_proposal": rec.get("initial_proposal"), "initial_preview": rec["initial_preview"], "current": current, "current_proposal": None, "approvals": [], "audit": [], "final_verification": None, "verification_status": "not_run", "recovery_state": "needs_review"}
        cur_prop, state = None, None
        if proposal_id:
            try:
                cur_prop = _proposal_public(_strict_proposal(snap, proposal_id))
            except Exception:
                cur_prop, state = None, "needs_review"
            if cur_prop is not None and (cur_prop.get("incident_id") != incident_id or cur_prop.get("product_id") != product_id):
                cur_prop, state = None, "needs_review"
        approvals, audit_rows, oversized_history = [], [], False
        if proposal_id:
            arows = snap.execute("SELECT * FROM approvals WHERE proposal_id=? ORDER BY created_at, id LIMIT ?", (proposal_id, MAX_EVENTS + 1)).fetchall()
            urows = snap.execute("SELECT * FROM audit_log WHERE proposal_id=? ORDER BY id LIMIT ?", (proposal_id, MAX_EVENTS + 1)).fetchall()
            if len(arows) > MAX_EVENTS or len(urows) > MAX_EVENTS:
                oversized_history = True
            else:
                approvals = [_approval_public(r) for r in arows]
                audit_rows = [{"id": r["id"], "ts": r["ts"], "action": r["action"], "proposal_id": r["proposal_id"], "detail": _audit_detail_public(r["detail"]), "actor_id": r["actor_id"]} for r in urows]
        if oversized_history:
            current = {"available": False, "reason": "oversized_current_history"}
            return {**base, "record_status": "ok", "recorded_at": rec["created_at"], "before": rec["before"], "investigation": rec["investigation"], "model_claims": rec["investigation"], "claims_label": "model-generated claims, not authority", "initial_proposal": rec.get("initial_proposal"), "initial_preview": rec["initial_preview"], "current": current, "current_proposal": cur_prop, "approvals": [], "audit": [], "final_verification": None, "verification_status": "not_run", "recovery_state": "needs_review"}
        try:
            approvals = _bounded(approvals); audit_rows = _bounded(audit_rows)
        except Exception:
            approvals, audit_rows, state = [], [], "needs_review"
        repairs = [e for e in events if proposal_id and e.get("repair_proposal_id") == proposal_id]
        verification, vstatus = None, "not_run"
        # State comes from current proposal.status ONLY; stray approval rows never promote it.
        # Actionable states additionally require bound available evidence and
        # an actually available valid initial preview. Applied cases are not
        # blocked by the fingerprint change caused by their own repair, but
        # proposed/approved cases with a stale current fingerprint are.
        _bound_ok = isinstance(rec.get('before'), dict) and rec['before'].get('available') is True and rec['before'].get('evidence_binding') == 'bound'
        _prev_obj = rec.get('initial_preview')
        _preview_ok = isinstance(_prev_obj, dict) and isinstance(_prev_obj.get('proposal'), dict) and _prev_obj.get('snapshot_match') is True
        _stale = False
        if cur_prop is not None and cur_prop.get('status') in ('proposed', 'approved') and _bound_ok and _preview_ok:
            try: _stale = store.fingerprint(snap, product_id) != rec['before'].get('fingerprint')
            except Exception: _stale = True
        if not (_bound_ok and _preview_ok) or _stale:
            state = 'needs_review'
        if state is None:
            if cur_prop is None:
                state = "needs_review"
            elif cur_prop.get("status") == "applied":
                try:
                    verification = recovery.verify_product(snap, product_id, proposal_id)
                    if not isinstance(verification, dict):
                        verification, vstatus = {"available": False, "status": "unavailable", "reason": "verification_unavailable"}, "unavailable"
                    else:
                        verification = _bounded(_validate_bounded(verification))
                        vstatus = "passed" if _verification_passed(verification) else "failed"
                except Exception:
                    verification, vstatus = {"available": False, "status": "unavailable", "reason": "verification_unavailable"}, "unavailable"
                state = "verified" if vstatus == "passed" else "applied_unverified"
            elif cur_prop.get("status") == "approved":
                state = "approved"
            elif cur_prop.get("status") == "proposed":
                state = "proposed"
            else:
                state = "needs_review"
        elif cur_prop is not None and cur_prop.get("status") == "applied":
            state = "needs_review"
        current_fp = store.fingerprint(snap, product_id)
        current = {
            "incident": _row_dict(inc), "product": _row_dict(prod), "events": events,
            "fingerprint": current_fp, "snapshot_hash": current_fp,
            "proposal": cur_prop, "approvals": approvals, "audit": audit_rows, "repair_events": repairs,
        }
        return {
            **base, "record_status": "ok", "recorded_at": rec["created_at"],
            "before": rec["before"], "investigation": rec["investigation"],
            "model_claims": rec["investigation"], "claims_label": "model-generated claims, not authority",
            "initial_proposal": rec.get("initial_proposal"), "initial_preview": rec["initial_preview"],
            "current": current, "current_proposal": cur_prop, "approvals": approvals, "audit": audit_rows,
            "final_verification": verification, "verification_status": vstatus, "recovery_state": state,
        }
    finally:
        snap.close()
