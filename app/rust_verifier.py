"""Fixed-executable Rust verifier adapter: fail-closed agreement only."""
import json
import os
import subprocess
import threading
import time
from pathlib import Path

from app import store

PROTOCOL_VERSION = 1
VERIFIER_VERSION = "0.1.0"
MAX_INPUT = 524288
MAX_OUTPUT = 16384
MAX_STDERR = 16384
MAX_EVENTS = 200
MAX_REF_JSON = 4096
TIMEOUT_SECONDS = 2.0
CLEANUP_SECONDS = 1.0
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1
_KINDS = {"init", "restock", "order_deduction", "repair_credit"}
_KNOWN_CODES = {
    "decode_error", "invalid_args", "protocol_error", "limit_exceeded",
    "invalid_id", "event_order", "invalid_string", "missing_init",
    "nonpositive_qty", "product_mismatch", "order_id_rule", "repair_ref_rule",
    "conflicting_qty", "overflow", "stock_mismatch", "invalid_repair_ref",
    "repair_credit_mismatch", "repair_linkage", "ledger_mismatch",
    "candidate_mismatch", "invalid_history", "rust_unavailable",
    "rust_timeout", "rust_output_limit",
}
_RESPONSE_KEYS = {
    "protocol_version", "verifier_version", "ok", "errors", "ledger_actual",
    "expected_stock", "outstanding_credit", "reverse_event_ids",
    "proposal_matches",
}


class RustVerificationError(Exception):
    def __init__(self, code, status="unavailable", verifier_version=None):
        self.code = code if code in _KNOWN_CODES else "protocol_error"
        self.status = status if status in ("failed", "unavailable") else "unavailable"
        self.verifier_version = verifier_version
        super().__init__("rust verification failed")


def executable_path():
    root = Path(__file__).resolve().parent.parent
    name = "afterfix-verifier.exe" if os.name == "nt" else "afterfix-verifier"
    return root / "rust-verifier" / "target" / "release" / name


def _fail(code="protocol_error", status="unavailable", version=None):
    raise RustVerificationError(code, status=status, verifier_version=version)


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool) and _INT64_MIN <= value <= _INT64_MAX


def _valid_text(value):
    return (isinstance(value, str) and 1 <= len(value.encode("utf-8")) <= 128
            and all(32 <= ord(c) != 127 for c in value))


def _strict_refs(raw, allow_empty=True):
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > MAX_REF_JSON:
        _fail("invalid_history", "failed")
    try:
        vals = json.loads(raw)
    except Exception:
        _fail("invalid_history", "failed")
    if not isinstance(vals, list) or len(vals) > MAX_EVENTS:
        _fail("invalid_history", "failed")
    if not allow_empty and not vals:
        _fail("invalid_history", "failed")
    if any(not _is_int(v) or v <= 0 for v in vals) or len(set(vals)) != len(vals):
        _fail("invalid_history", "failed")
    return sorted(vals)


def _checked_add(total, value):
    total += value
    if not _INT64_MIN <= total <= _INT64_MAX:
        _fail("overflow", "failed")
    return total


def _subtotal(events, kind, sign=1):
    total = 0
    for ev in events:
        if ev["kind"] == kind:
            total = _checked_add(total, sign * ev["qty"])
    return total


def build_request(conn, product_id, candidate=None):
    """Independently validate/normalize history; fail before any child launch."""
    if not _valid_text(product_id):
        _fail("invalid_history", "failed")
    prod = store.get_product(conn, product_id)
    if prod is None or not _is_int(prod["stock"]):
        _fail("invalid_history", "failed")
    recorded = prod["stock"]
    rows = conn.execute(
        "SELECT id,product_id,kind,qty,order_id,reversed_ids,repair_proposal_id "
        "FROM events WHERE product_id=? ORDER BY id LIMIT ?",
        (product_id, MAX_EVENTS + 1)).fetchall()
    if len(rows) > MAX_EVENTS:
        _fail("limit_exceeded", "failed")
    events, by_id = [], {}
    for r in rows:
        eid, qty, kind = r["id"], r["qty"], r["kind"]
        if not _is_int(eid) or eid <= 0 or not _is_int(qty) or qty <= 0:
            _fail("invalid_history", "failed")
        if eid in by_id or (events and eid <= events[-1]["id"]):
            _fail("invalid_history", "failed")
        if not isinstance(kind, str) or kind not in _KINDS or r["product_id"] != product_id:
            _fail("invalid_history", "failed")
        order_id, rp = r["order_id"], r["repair_proposal_id"]
        if order_id is not None and not _valid_text(order_id):
            _fail("invalid_history", "failed")
        if rp is not None and not _valid_text(rp):
            _fail("invalid_history", "failed")
        raw_rev = r["reversed_ids"] if r["reversed_ids"] is not None else "[]"
        refs = _strict_refs(raw_rev)
        if kind == "order_deduction":
            if not order_id:
                _fail("invalid_history", "failed")
        elif order_id is not None:
            _fail("invalid_history", "failed")
        if kind == "repair_credit":
            if not refs or not rp:
                _fail("invalid_history", "failed")
        elif refs or rp is not None:
            _fail("invalid_history", "failed")
        ev = {"id": eid, "product_id": product_id, "kind": kind, "qty": qty,
              "order_id": order_id, "reversed_ids": refs, "repair_proposal_id": rp}
        events.append(ev)
        by_id[eid] = ev
    if not any(e["kind"] == "init" for e in events):
        _fail("missing_init", "failed")
    by_order = {}
    for ev in events:
        if ev["kind"] == "order_deduction":
            by_order.setdefault(ev["order_id"], []).append(ev)
    if any(len({x["qty"] for x in grp}) != 1 for grp in by_order.values()):
        _fail("conflicting_qty", "failed")
    extras = set()
    for grp in by_order.values():
        for extra in sorted(grp, key=lambda x: x["id"])[1:]:
            extras.add(extra["id"])
    applied, seen_reversed, seen_proposals = [], {}, set()
    for ev in events:
        if ev["kind"] != "repair_credit":
            continue
        refs, rp = ev["reversed_ids"], ev["repair_proposal_id"]
        if rp in seen_proposals:
            _fail("invalid_history", "failed")
        seen_proposals.add(rp)
        for v in refs:
            target = by_id.get(v)
            if v in seen_reversed or v >= ev["id"] or target is None or target["kind"] != "order_deduction" or v not in extras:
                _fail("invalid_history", "failed")
            seen_reversed[v] = ev["id"]
        this_repair_total = 0
        for v in refs:
            this_repair_total = _checked_add(this_repair_total, by_id[v]["qty"])
        if this_repair_total != ev["qty"]:
            _fail("invalid_history", "failed")
        prow = conn.execute(
            "SELECT product_id,credit_qty,reverse_event_ids,status FROM proposals WHERE id=?",
            (rp,)).fetchone()
        if prow is None:
            _fail("invalid_history", "failed")
        praw = prow["reverse_event_ids"]
        if not isinstance(praw, str) or len(praw.encode("utf-8")) > MAX_REF_JSON:
            _fail("invalid_history", "failed")
        p_refs = _strict_refs(praw, allow_empty=False)
        if (prow["status"] != "applied" or prow["product_id"] != product_id
                or not _is_int(prow["credit_qty"]) or prow["credit_qty"] != ev["qty"]
                or sorted(p_refs) != sorted(refs)):
            _fail("invalid_history", "failed")
        applied.append({"proposal_id": rp, "product_id": product_id,
                        "credit_qty": ev["qty"], "reverse_event_ids": sorted(refs),
                        "status": "applied"})
    init_total = _subtotal(events, "init")
    restock_total = _subtotal(events, "restock")
    repair_total = _subtotal(events, "repair_credit")
    all_ded = _subtotal(events, "order_deduction")
    ledger_actual = init_total
    for part in (restock_total, repair_total, -all_ded):
        ledger_actual = _checked_add(ledger_actual, part)
    if ledger_actual != recorded:
        _fail("stock_mismatch", "failed")
    deduped = 0
    for grp in by_order.values():
        deduped = _checked_add(deduped, sorted(grp, key=lambda x: x["id"])[0]["qty"])
    deduped_ledger = _checked_add(_checked_add(init_total, restock_total), -deduped)
    outstanding_ids = sorted(extras - set(seen_reversed))
    outstanding_credit = 0
    for i in outstanding_ids:
        outstanding_credit = _checked_add(outstanding_credit, by_id[i]["qty"])
    expected_stock = _checked_add(recorded, outstanding_credit)
    if expected_stock != deduped_ledger:
        _fail("ledger_mismatch", "failed")
    cand = None
    if candidate is not None:
        if not isinstance(candidate, dict) or set(candidate) != {"credit_qty", "reverse_event_ids"}:
            _fail("candidate_mismatch", "failed")
        credit, refs = candidate["credit_qty"], candidate["reverse_event_ids"]
        if (not _is_int(credit) or not isinstance(refs, list) or len(refs) > MAX_EVENTS
                or any(not _is_int(v) or v <= 0 for v in refs) or refs != sorted(set(refs))):
            _fail("candidate_mismatch", "failed")
        cand = {"credit_qty": credit, "reverse_event_ids": list(refs)}
        if not (credit > 0 and credit == outstanding_credit and refs == outstanding_ids):
            _fail("candidate_mismatch", "failed")
    expected = {"ledger_actual": ledger_actual, "outstanding_credit": outstanding_credit,
                "expected_stock": expected_stock, "reverse_event_ids": outstanding_ids,
                "proposal_matches": True if cand is not None else None}
    request = {"protocol_version": PROTOCOL_VERSION, "product_id": product_id,
               "recorded_stock": recorded, "events": events,
               "applied_proposals": applied, "proposal": cand}
    return request, expected


def _minimal_env():
    if os.name != "nt":
        return {}
    env = {}
    for name in ("SYSTEMROOT", "WINDIR"):
        value = os.environ.get(name)
        if isinstance(value, str) and value and all(32 <= ord(c) != 127 for c in value):
            env[name] = value
    return env


def _run_child(payload, process_factory=None):
    factory = process_factory or subprocess.Popen
    try:
        proc = factory([str(executable_path())], stdin=subprocess.PIPE,
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                       shell=False, env=_minimal_env())
    except Exception:
        _fail("rust_unavailable")
    out, err = bytearray(), bytearray()
    state = {"overflow": False, "read_error": False, "write_error": False, "write_done": False}

    def reader(stream, buf, limit):
        try:
            while True:
                chunk = stream.read(8192)
                if chunk is None:
                    raise OSError("read")
                if not chunk:
                    break
                if len(buf) + len(chunk) > limit:
                    state["overflow"] = True
                    return
                buf.extend(chunk)
        except Exception:
            state["read_error"] = True

    def writer():
        try:
            written = proc.stdin.write(payload)
            if written != len(payload):
                raise OSError("short write")
            proc.stdin.flush()
            proc.stdin.close()
            state["write_done"] = True
        except Exception:
            state["write_error"] = True

    threads = [
        threading.Thread(target=reader, args=(proc.stdout, out, MAX_OUTPUT), daemon=True),
        threading.Thread(target=reader, args=(proc.stderr, err, MAX_STDERR), daemon=True),
        threading.Thread(target=writer, daemon=True),
    ]
    started = []
    deadline = time.monotonic() + TIMEOUT_SECONDS
    timed_out = poll_error = False
    rc = None
    try:
        for t in threads:
            t.start()
            started.append(t)
        while True:
            try:
                rc = proc.poll()
            except Exception:
                poll_error = True
                break
            if state["overflow"] or state["read_error"] or state["write_error"]:
                break
            if rc is not None and not started[0].is_alive() and not started[1].is_alive():
                break
            if time.monotonic() >= deadline:
                timed_out = True
                break
            time.sleep(0.005)
    except Exception:
        poll_error = True
    cleanup_deadline = time.monotonic() + CLEANUP_SECONDS
    if timed_out or poll_error or state["overflow"] or state["read_error"] or state["write_error"] or rc is None:
        try:
            proc.kill()
        except Exception:
            pass
    try:
        rc2 = proc.wait(timeout=max(0.01, cleanup_deadline - time.monotonic()))
        if rc is None:
            rc = rc2
    except Exception:
        if not (timed_out or poll_error or state["overflow"] or state["read_error"] or state["write_error"]):
            poll_error = True
    for t in started:
        remaining = cleanup_deadline - time.monotonic()
        if remaining <= 0:
            break
        t.join(timeout=remaining)
    for t, stream in ((started[0] if len(started) > 0 else None, getattr(proc, "stdout", None)),
                      (started[1] if len(started) > 1 else None, getattr(proc, "stderr", None)),
                      (started[2] if len(started) > 2 else None, getattr(proc, "stdin", None))):
        if stream is not None and (t is None or not t.is_alive()):
            try:
                stream.close()
            except Exception:
                pass
    if any(t.is_alive() for t in started):
        _fail("rust_unavailable")
    if timed_out:
        _fail("rust_timeout")
    if state["overflow"]:
        _fail("rust_output_limit")
    if poll_error or state["read_error"] or state["write_error"] or not state["write_done"]:
        _fail("rust_unavailable")
    if rc is None:
        _fail("rust_unavailable")
    return rc, bytes(out), bytes(err)


def _no_duplicates(pairs):
    obj = {}
    for key, value in pairs:
        if key in obj:
            raise ValueError("duplicate")
        obj[key] = value
    return obj


def _reject_constant(_value):
    raise ValueError("constant")


def _validate_response(raw, request, expected):
    if len(raw) > MAX_OUTPUT:
        _fail("rust_output_limit")
    try:
        text = raw.decode("utf-8")
    except Exception:
        _fail("protocol_error")
    try:
        decoder = json.JSONDecoder(object_pairs_hook=_no_duplicates, parse_constant=_reject_constant)
        obj, end = decoder.raw_decode(text)
        if text[end:].strip(" \t\r\n"):
            raise ValueError("trailing")
    except Exception:
        _fail("protocol_error")
    if not isinstance(obj, dict) or set(obj) != _RESPONSE_KEYS:
        _fail("protocol_error")
    if not _is_int(obj["protocol_version"]) or obj["protocol_version"] != PROTOCOL_VERSION:
        _fail("protocol_error")
    if obj["verifier_version"] != VERIFIER_VERSION:
        _fail("protocol_error")
    version = VERIFIER_VERSION
    if not isinstance(obj["ok"], bool):
        _fail("protocol_error", version=version)
    errors = obj["errors"]
    if not isinstance(errors, list) or len(errors) > 32:
        _fail("protocol_error", version=version)
    for code in errors:
        if not isinstance(code, str) or code not in _KNOWN_CODES:
            _fail("protocol_error", version=version)
    has_candidate = request["proposal"] is not None
    if not obj["ok"]:
        if errors and len(set(errors)) != len(errors):
            _fail("protocol_error", "failed", version)
        if obj["ledger_actual"] is not None or obj["expected_stock"] is not None or obj["outstanding_credit"] is not None:
            _fail("protocol_error", "failed", version)
        if obj["reverse_event_ids"] != []:
            _fail("protocol_error", "failed", version)
        pm = obj["proposal_matches"]
        if has_candidate:
            if not (pm is None or pm is False):
                _fail("protocol_error", "failed", version)
        elif pm is not None:
            _fail("protocol_error", "failed", version)
        _fail(errors[0] if errors else "ledger_mismatch", "failed", version)
    if errors:
        _fail("protocol_error", "failed", version)
    for key in ("ledger_actual", "expected_stock", "outstanding_credit"):
        if not _is_int(obj[key]):
            _fail("protocol_error", "failed", version)
    refs = obj["reverse_event_ids"]
    if not isinstance(refs, list) or len(refs) > MAX_EVENTS or any(not _is_int(v) or v <= 0 for v in refs) or refs != sorted(set(refs)):
        _fail("protocol_error", "failed", version)
    want_match = True if has_candidate else None
    if obj["proposal_matches"] is not want_match:
        _fail("candidate_mismatch" if has_candidate else "protocol_error", "failed", version)
    if (obj["ledger_actual"] != expected["ledger_actual"]
            or obj["outstanding_credit"] != expected["outstanding_credit"]
            or obj["expected_stock"] != expected["expected_stock"]
            or refs != expected["reverse_event_ids"]):
        _fail("ledger_mismatch", "failed", version)
    return obj


def verify_current(conn, product_id, candidate=None, snapshot_hash=None, _process_factory=None):
    actual_snap = store.fingerprint(conn, product_id)
    if snapshot_hash is not None and snapshot_hash != actual_snap:
        _fail("protocol_error")
    request, expected = build_request(conn, product_id, candidate)
    try:
        payload = json.dumps(request, ensure_ascii=True, separators=(",", ":")).encode("ascii")
    except Exception:
        _fail("protocol_error")
    if len(payload) > MAX_INPUT:
        _fail("limit_exceeded")
    rc, raw, _err = _run_child(payload, _process_factory)
    if rc != 0:
        _fail("rust_unavailable")
    obj = _validate_response(raw, request, expected)
    checks = [
        {"name": "protocol_version", "ok": True},
        {"name": "stock_matches_ledger", "ok": True},
        {"name": "qty_consistent", "ok": True},
        {"name": "ledger_actual", "ok": True},
        {"name": "outstanding_credit", "ok": True},
        {"name": "expected_stock", "ok": True},
        {"name": "reverse_event_ids", "ok": True},
    ]
    if candidate is not None:
        checks.append({"name": "proposal_matches", "ok": True})
    return {"enabled": True, "status": "verified", "verifier_version": VERIFIER_VERSION,
            "snapshot_hash": actual_snap, "checks": checks,
            "ledger_actual": obj["ledger_actual"], "expected_stock": obj["expected_stock"],
            "outstanding_credit": obj["outstanding_credit"],
            "reverse_event_ids": obj["reverse_event_ids"],
            "proposal_matches": obj["proposal_matches"]}


def failure_public(exc, snapshot_hash):
    out = {"enabled": True,
           "status": exc.status if isinstance(exc, RustVerificationError) else "unavailable",
           "snapshot_hash": snapshot_hash if isinstance(snapshot_hash, str) else "",
           "error_code": exc.code if isinstance(exc, RustVerificationError) else "rust_unavailable"}
    if getattr(exc, "verifier_version", None) == VERIFIER_VERSION:
        out["verifier_version"] = VERIFIER_VERSION
    return out
