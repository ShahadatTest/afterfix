"""SQLite persistence, fingerprinting, and synthetic seed workflow."""
import hashlib
import json
import os
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS products(product_id TEXT PRIMARY KEY, name TEXT, stock INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY AUTOINCREMENT, product_id TEXT NOT NULL, kind TEXT NOT NULL, qty INTEGER NOT NULL, order_id TEXT, ts INTEGER NOT NULL, note TEXT DEFAULT '', reversed_ids TEXT NOT NULL DEFAULT '[]', repair_proposal_id TEXT);
CREATE TABLE IF NOT EXISTS incidents(id TEXT PRIMARY KEY, product_id TEXT NOT NULL, description TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS proposals(id TEXT PRIMARY KEY, incident_id TEXT NOT NULL, product_id TEXT NOT NULL, credit_qty INTEGER NOT NULL DEFAULT 0, reverse_event_ids TEXT NOT NULL DEFAULT '[]', snapshot_hash TEXT NOT NULL DEFAULT '', status TEXT NOT NULL, reason TEXT DEFAULT '', created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, snapshot_hash TEXT NOT NULL, baseline_json TEXT NOT NULL DEFAULT '{}', created_at INTEGER NOT NULL, actor_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS idempotency_keys(idempotency_key TEXT PRIMARY KEY, proposal_id TEXT NOT NULL, approval_id TEXT NOT NULL, result TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS audit_log(id INTEGER PRIMARY KEY AUTOINCREMENT, ts INTEGER NOT NULL, action TEXT NOT NULL, proposal_id TEXT NOT NULL, detail TEXT NOT NULL, actor_id TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS case_records(case_id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL DEFAULT 1, incident_id TEXT NOT NULL, product_id TEXT NOT NULL, proposal_id TEXT, actor_id TEXT NOT NULL DEFAULT '', created_at INTEGER NOT NULL, before_json TEXT NOT NULL, investigation_json TEXT NOT NULL, initial_preview_json TEXT);
"""

CANONICAL_INCIDENT = "INC-001"
CANONICAL_PRODUCT = "WIDGET-1"


def get_db_path():
    return os.environ.get("AFTERFIX_DB", "afterfix.db")


def connect(path=None):
    conn = sqlite3.connect(path or get_db_path(), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    init_db(conn)
    return conn


def init_db(conn):
    conn.executescript(SCHEMA)
    cols = {r[1] for r in conn.execute("PRAGMA table_info(events)").fetchall()}
    if "reversed_ids" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN reversed_ids TEXT NOT NULL DEFAULT '[]'")
    if "repair_proposal_id" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN repair_proposal_id TEXT")
    acols = {r[1] for r in conn.execute("PRAGMA table_info(approvals)").fetchall()}
    if "baseline_json" not in acols:
        conn.execute("ALTER TABLE approvals ADD COLUMN baseline_json TEXT NOT NULL DEFAULT '{}'")
    if "actor_id" not in acols:
        conn.execute("ALTER TABLE approvals ADD COLUMN actor_id TEXT NOT NULL DEFAULT ''")
    ucols = {r[1] for r in conn.execute("PRAGMA table_info(audit_log)").fetchall()}
    if "actor_id" not in ucols:
        conn.execute("ALTER TABLE audit_log ADD COLUMN actor_id TEXT NOT NULL DEFAULT ''")
    # Migration for a pre-existing draft case_records table without version.
    ccols = {r[1] for r in conn.execute("PRAGMA table_info(case_records)").fetchall()}
    if ccols and "schema_version" not in ccols:
        conn.execute("ALTER TABLE case_records ADD COLUMN schema_version INTEGER NOT NULL DEFAULT 1")
    conn.commit()


def get_product(conn, product_id):
    return conn.execute("SELECT * FROM products WHERE product_id=?", (product_id,)).fetchone()


def list_product_events(conn, product_id):
    return conn.execute("SELECT * FROM events WHERE product_id=? ORDER BY id", (product_id,)).fetchall()


def get_incident(conn, incident_id):
    return conn.execute("SELECT * FROM incidents WHERE id=?", (incident_id,)).fetchone()


def fingerprint(conn, product_id):
    prod = get_product(conn, product_id)
    if prod is None:
        raise ValueError(f"unknown product {product_id}")
    rows = conn.execute(
        "SELECT id, kind, qty, COALESCE(order_id,'') AS oid, ts, COALESCE(note,'') AS note, COALESCE(reversed_ids,'[]') AS rev, COALESCE(repair_proposal_id,'') AS rp FROM events WHERE product_id=? ORDER BY id",
        (product_id,),
    ).fetchall()
    payload = {
        "product_id": product_id, "stock": prod["stock"],
        "events": [{"id": r["id"], "kind": r["kind"], "qty": r["qty"], "order_id": r["oid"], "ts": r["ts"], "note": r["note"], "reversed_ids": r["rev"], "repair_proposal_id": r["rp"]} for r in rows],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def fingerprint_payload(product, events):
    """Recompute the accepted fingerprint from captured product/events.
    None-only coalescing exactly matches fingerprint(): empty strings,
    event order, IDs and metadata are preserved."""
    def pv(p, key):
        try:
            return p[key]
        except Exception:
            return p.get(key) if isinstance(p, dict) else None
    def coal(v, default):
        return default if v is None else v
    payload = {
        "product_id": pv(product, "product_id"), "stock": pv(product, "stock"),
        "events": [{
            "id": pv(r, "id"), "kind": pv(r, "kind"), "qty": pv(r, "qty"),
            "order_id": coal(pv(r, "order_id"), ""), "ts": pv(r, "ts"), "note": coal(pv(r, "note"), ""),
            "reversed_ids": coal(pv(r, "reversed_ids"), "[]"), "repair_proposal_id": coal(pv(r, "repair_proposal_id"), ""),
        } for r in events],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit(conn, action, proposal_id, detail, actor_id="system"):
    conn.execute("INSERT INTO audit_log(ts, action, proposal_id, detail, actor_id) VALUES (?,?,?,?,?)", (int(time.time()), action, proposal_id, detail, actor_id))


def reset_and_seed(conn, actor_id="system"):
    from app import domain
    init_db(conn)
    for t in ("case_records", "idempotency_keys", "audit_log", "approvals", "proposals", "events", "incidents", "products"):
        conn.execute(f"DELETE FROM {t}")
    conn.execute("INSERT INTO incidents(id, product_id, description, status) VALUES (?,?,?,?)", (CANONICAL_INCIDENT, CANONICAL_PRODUCT, "Reported stock discrepancy for WIDGET-1; reconcile ledger against recorded events", "open"))
    conn.execute("INSERT INTO products(product_id, name, stock) VALUES (?,?,?)", ("WIDGET-1", "Widget", 100))
    conn.execute("INSERT INTO products(product_id, name, stock) VALUES (?,?,?)", ("GADGET-9", "Gadget", 50))
    ts = int(time.time())
    conn.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES (?,?,?,?,?,?)", ("WIDGET-1", "init", 100, None, ts, "opening record"))
    conn.execute("INSERT INTO events(product_id, kind, qty, order_id, ts, note) VALUES (?,?,?,?,?,?)", ("GADGET-9", "init", 50, None, ts, "opening record"))
    conn.commit()
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-1001", 10)
    domain.process_order_buggy(conn, "WIDGET-1", "ORD-1001", 10)
    domain.process_restock(conn, "WIDGET-1", 20, note="received shipment")
    domain.process_order_fixed(conn, "GADGET-9", "ORD-9001", 5)
    audit(conn, "seed", "-", "seeded via executable order/restock path", actor_id=actor_id)
    conn.commit()
    return {"incident_id": CANONICAL_INCIDENT, "widget_stock": get_product(conn, "WIDGET-1")["stock"], "gadget_stock": get_product(conn, "GADGET-9")["stock"]}
