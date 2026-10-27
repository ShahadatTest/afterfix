"""Regression tests for incident-scope races during investigation.

All model calls use the fake transport/helpers from test_investigator.py;
no network. Mutations are made on a separate committed connection while
the fake model's first call is running, reproducing the live race.
"""

import json

import pytest

from app import store
from app.investigator import investigate
from app.meta_client import MetaClient

try:
    from test_investigator import FakeTransport, ids, tool_then_final
except ImportError:  # package-style collection
    from tests.test_investigator import FakeTransport, ids, tool_then_final

WIDGET = "WIDGET-1"
GADGET = "GADGET-9"


@pytest.fixture()
def db(tmp_path):
    path = tmp_path / "afterfix.db"
    conn = store.connect(str(path))
    store.reset_and_seed(conn)
    yield conn, path
    conn.close()


def _count(conn, sql, params=()):
    return conn.execute(sql, params).fetchone()[0]


def assert_no_proposal_writes(conn):
    assert _count(conn, "SELECT COUNT(*) FROM proposals") == 0
    assert _count(conn, "SELECT COUNT(*) FROM approvals") == 0
    assert _count(conn, "SELECT COUNT(*) FROM events WHERE kind='repair_credit'") == 0
    assert {r[0] for r in conn.execute("SELECT action FROM audit_log")} <= {"seed"}


def _mutate_once(path, sql, params, base):
    """Wrap a fake handler: commit `sql` on another connection during call 1."""
    state = {"done": False}

    def handler(payload, n):
        if not state["done"]:
            state["done"] = True
            other = store.connect(str(path))
            try:
                other.execute(sql, params)
                other.commit()
            finally:
                other.close()
        return base(payload, n)

    return handler


def _run(conn, handler):
    fake = FakeTransport(handler)
    result = investigate(conn, "INC-001", client=MetaClient(transport=fake))
    return result, fake


def test_incident_product_rebinding_during_model_call_writes_nothing(db):
    conn, path = db
    info = ids(conn)
    widget_stock = store.get_product(conn, WIDGET)["stock"]
    gadget_stock = store.get_product(conn, GADGET)["stock"]
    base = tool_then_final(["get_patch", "read_events"], info["all"])
    handler = _mutate_once(
        path,
        "UPDATE incidents SET product_id=? WHERE id='INC-001'",
        (GADGET,),
        base,
    )
    result, fake = _run(conn, handler)
    assert len(fake.payloads) == 3
    assert result["status"] == "needs_review"
    assert result["reason"] == "stale_state"
    assert result["proposal"] is None
    assert result["proposal_id"] is None
    assert_no_proposal_writes(conn)
    # The deliberate live edit is preserved; no stock was repaired/changed.
    live = store.get_incident(conn, "INC-001")
    assert live is not None and live["product_id"] == GADGET
    assert store.get_product(conn, WIDGET)["stock"] == widget_stock
    assert store.get_product(conn, GADGET)["stock"] == gadget_stock


@pytest.mark.parametrize(
    "sql,params,check",
    [
        (
            "DELETE FROM incidents WHERE id='INC-001'",
            (),
            lambda conn: store.get_incident(conn, "INC-001") is None,
        ),
        (
            "UPDATE incidents SET description=? WHERE id='INC-001'",
            ("Changed while investigating",),
            lambda conn: store.get_incident(conn, "INC-001")["description"]
            == "Changed while investigating",
        ),
        (
            "UPDATE incidents SET status=? WHERE id='INC-001'",
            ("triaged",),
            lambda conn: store.get_incident(conn, "INC-001")["status"] == "triaged",
        ),
    ],
    ids=["deleted", "description_changed", "status_changed"],
)
def test_incident_deleted_or_edited_during_model_call_writes_nothing(db, sql, params, check):
    conn, path = db
    info = ids(conn)
    widget_stock = store.get_product(conn, WIDGET)["stock"]
    base = tool_then_final(["get_patch", "read_events"], info["all"])
    handler = _mutate_once(path, sql, params, base)
    result, _fake = _run(conn, handler)
    assert result["status"] == "needs_review"
    assert result["reason"] == "stale_state"
    assert result["proposal"] is None
    assert result["proposal_id"] is None
    assert_no_proposal_writes(conn)
    assert check(conn)  # deliberate edit/deletion preserved, not rolled back
    assert store.get_product(conn, WIDGET)["stock"] == widget_stock


def test_unchanged_canonical_incident_produces_one_draft_only(db):
    conn, _path = db
    info = ids(conn)
    result, fake = _run(
        conn, tool_then_final(["get_patch", "read_events"], info["all"])
    )
    assert result["status"] == "proposed"
    assert len(fake.payloads) == 3
    rows = conn.execute("SELECT * FROM proposals ORDER BY id").fetchall()
    assert len(rows) == 1
    assert rows[0]["incident_id"] == "INC-001"
    assert rows[0]["product_id"] == WIDGET
    assert rows[0]["credit_qty"] == 10
    assert json.loads(rows[0]["reverse_event_ids"]) == [info["extra"]]
    # Draft only: no approval, no repair credit, stock not yet applied.
    assert _count(conn, "SELECT COUNT(*) FROM approvals") == 0
    assert _count(conn, "SELECT COUNT(*) FROM events WHERE kind='repair_credit'") == 0
    assert store.get_product(conn, WIDGET)["stock"] == 100
    assert {r[0] for r in conn.execute("SELECT action FROM audit_log")} == {"seed"}


def test_caller_owned_transaction_still_preserved(db):
    conn, _path = db
    fake = FakeTransport(lambda p, n: {"status": "completed", "output": [], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}})
    conn.execute("BEGIN")
    conn.execute("INSERT INTO products(product_id,name,stock) VALUES ('TEMP-1','Temp',1)")
    assert conn.in_transaction
    result = investigate(conn, "INC-001", client=MetaClient(transport=fake))
    assert result["status"] == "needs_review"
    assert fake.payloads == []
    # Neither committed nor rolled back by investigate().
    assert conn.in_transaction
    assert _count(conn, "SELECT COUNT(*) FROM products WHERE product_id='TEMP-1'") == 1
    conn.rollback()
    assert _count(conn, "SELECT COUNT(*) FROM products WHERE product_id='TEMP-1'") == 0
    assert_no_proposal_writes(conn)
