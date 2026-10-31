"""Isolated external SQLite connector evidence; never real data/provider usage."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app import agent, domain, recovery, store
from app.main import app
from app.agent_site import create_site

OPERATOR = "operator-test-agent-access-1234567890abcd"
VIEWER = "viewer-test-agent-access-1234567890abcdef"


def external(path, opening=120, order=7, restock=13):
    c = sqlite3.connect(path)
    c.executescript("""CREATE TABLE products(product_id TEXT PRIMARY KEY,name TEXT,stock INTEGER NOT NULL);
    CREATE TABLE events(id INTEGER PRIMARY KEY,product_id TEXT NOT NULL,kind TEXT NOT NULL,qty INTEGER NOT NULL,order_id TEXT,ts INTEGER NOT NULL,note TEXT);""")
    c.execute("INSERT INTO products VALUES ('PART-A','Replacement part',?)", (opening - 2 * order + restock,))
    c.executemany("INSERT INTO events VALUES (?,?,?,?,?,?,?)", [(11,"PART-A","init",opening,None,100,"opening"),(14,"PART-A","order_deduction",order,"ORDER-A",101,"first"),(17,"PART-A","order_deduction",order,"ORDER-A",102,"duplicate"),(22,"PART-A","restock",restock,None,103,"restock")])
    c.commit(); c.close()
    return path


def business_rows(path):
    c = sqlite3.connect(path)
    result = (c.execute("SELECT * FROM products ORDER BY product_id").fetchall(), c.execute("SELECT id,product_id,kind,qty,order_id,ts,note FROM events ORDER BY id").fetchall())
    c.close()
    return result


@pytest.fixture()
def connected(tmp_path, monkeypatch):
    database = external(tmp_path / "inventory.sqlite")
    patch = tmp_path / "fix.patch"; patch.write_text("--- order.py\n+++ order.py\n+ reject repeated business order IDs\n", encoding="utf-8")
    backup = tmp_path / "before.sqlite"
    before = business_rows(database)
    agent.initialize_database(database, "PART-A", backup, "installation-for-tests")
    assert business_rows(database) == business_rows(backup) == before
    for name, value in {"AFTERFIX_AGENT_MODE":"1","AFTERFIX_DB":str(database),"AFTERFIX_AGENT_ID":"installation-for-tests","AFTERFIX_AGENT_PRODUCT":"PART-A","AFTERFIX_AGENT_PATCH":str(patch),"AFTERFIX_AGENT_PATCH_SHA256":hashlib.sha256(patch.read_bytes()).hexdigest(),"AFTERFIX_OPERATOR_TOKEN":OPERATOR,"AFTERFIX_VIEWER_TOKEN":VIEWER,"AFTERFIX_PUBLIC_VIEWER":"1","AFTERFIX_ALLOW_RESET":"1","AFTERFIX_ENABLE_SCENARIO_LAB":"1"}.items():
        monkeypatch.setenv(name,value)
    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    yield database, before, patch


def test_scan_registers_candidate_without_changing_business_data(connected):
    database, before, patch = connected
    result = agent.scan(register=True)
    assert result["status"] == "candidate" and result["product_id"] == "PART-A"
    assert result["stock"] == 119 and result["model_requests"] == 0
    assert business_rows(database) == before
    c = store.connect(); assert store.get_incident(c,"INC-001")["product_id"] == "PART-A"; c.close()
    assert domain.get_patch_text() == patch.read_bytes().decode("utf-8")


@pytest.mark.parametrize("change", ["DELETE FROM events WHERE kind='init'", "UPDATE products SET stock=999", "UPDATE events SET qty=8 WHERE id=17", "UPDATE events SET kind='unrecognized' WHERE id=17"])
def test_ambiguous_or_unsupported_evidence_never_repairs(connected, change):
    database, _, _ = connected
    with sqlite3.connect(database) as c: c.execute(change)
    before = business_rows(database)
    try: result = agent.scan(register=True)
    except agent.AgentError: pass
    else: assert result["status"] == "needs_review"
    assert business_rows(database) == before


def test_actual_connected_repair_uses_existing_approval_rust_and_stale_guards(connected):
    database, _, _ = connected
    agent.scan(register=True)
    c = store.connect()
    try:
        p = recovery.build_proposal(c,"INC-001","PART-A",[11,14,17,22])
        preview = recovery.preview_proposal(c,p["proposal_id"])
        assert preview["simulation_pass"] and preview["rust_verification"]["status"] == "verified"
        with pytest.raises(recovery.ApprovalError): recovery.apply_proposal(c,p["proposal_id"],"missing","attempt",actor_id="operator")
        approval = recovery.approve_proposal(c,p["proposal_id"],p["snapshot_hash"],actor_id="operator")
        result = recovery.apply_proposal(c,p["proposal_id"],approval["approval_id"],"agent-retry-key",actor_id="operator")
        assert result["status"] == "applied"
        again = recovery.apply_proposal(c,p["proposal_id"],approval["approval_id"],"agent-retry-key",actor_id="operator")
        assert again == result
        assert store.get_product(c,"PART-A")["stock"] == 126
        assert dict(c.execute("SELECT * FROM events WHERE id=22").fetchone())["qty"] == 13
        assert c.execute("SELECT count(*) FROM events WHERE kind='repair_credit'").fetchone()[0] == 1
    finally: c.close()
    assert agent.scan(register=True)["status"] == "clean"


def test_connected_stale_source_blocks_apply(connected):
    agent.scan(register=True); c=store.connect()
    try:
        p=recovery.build_proposal(c,"INC-001","PART-A",[11,14,17,22]); approval=recovery.approve_proposal(c,p["proposal_id"],p["snapshot_hash"],actor_id="operator")
        domain.process_restock(c,"PART-A",3)
        with pytest.raises(recovery.StaleStateError): recovery.apply_proposal(c,p["proposal_id"],approval["approval_id"],"stale",actor_id="operator")
        assert store.get_product(c,"PART-A")["stock"] == 122
        assert c.execute("SELECT count(*) FROM events WHERE kind='repair_credit'").fetchone()[0] == 0
    finally:c.close()


@pytest.mark.parametrize("change", ["CREATE TABLE unexpected(x)", "CREATE TRIGGER unexpected AFTER UPDATE ON products BEGIN DELETE FROM events; END", "DELETE FROM afterfix_agent_installation"])
def test_schema_or_identity_change_fails_closed(connected, change):
    database, _, _=connected
    with sqlite3.connect(database) as c:c.execute(change)
    before=business_rows(database)
    with pytest.raises(agent.AgentError):store.connect()
    assert business_rows(database)==before


def test_patch_change_is_not_replaced_with_demo_patch(connected):
    _, _, patch=connected; patch.write_text("changed")
    with pytest.raises(agent.AgentError):domain.get_patch_text()


def test_connected_access_and_reset_gates_are_forced(connected):
    agent.scan(register=True)
    client=TestClient(app); op={"Authorization":"Bearer "+OPERATOR}; vw={"Authorization":"Bearer "+VIEWER}
    assert client.get('/auth/me').status_code==401
    assert client.get('/agent/status',headers=vw).status_code==403
    assert client.post('/agent/scan',headers=vw,json={}).status_code==403
    assert client.post('/admin/reset',headers=op,json={}).status_code==403
    assert client.get('/lab/scenarios',headers=op).status_code==404
    assert client.post('/agent/scan',headers=op,json={"path":"untrusted.sqlite"}).status_code==422
    assert client.post('/agent/scan',headers=op,json={}).json()['status']=='candidate'
    assert client.get('/auth/me',headers=op).json()['workspace_mode']=='connected'


def test_single_container_site_routes_and_startup_scan(connected,tmp_path):
    frontend=tmp_path/'frontend';frontend.mkdir();(frontend/'index.html').write_text('<h1>Agent UI</h1>')
    with TestClient(create_site(frontend),base_url='http://127.0.0.1') as client:
        assert client.get('/').status_code==200
        assert client.get('/health').status_code==200
        assert client.get('/api/auth/me').status_code==401
        assert client.get('/api/auth/me',headers={'Authorization':'Bearer '+OPERATOR}).json()['workspace_mode']=='connected'
        assert client.get('/api/unknown',headers={'Authorization':'Bearer '+OPERATOR}).status_code==404
        assert client.get('/api/unknown',headers={'Host':'attacker.invalid'}).status_code==400


@pytest.mark.parametrize("schema_change", ["CREATE VIEW external_view AS SELECT * FROM products", "CREATE TRIGGER extra AFTER UPDATE ON products BEGIN DELETE FROM events; END", "CREATE TABLE approvals(x)"])
def test_install_rejects_unsupported_schema_without_changes(tmp_path,schema_change):
    database=external(tmp_path/'source.sqlite')
    with sqlite3.connect(database) as c:c.execute(schema_change)
    before=database.read_bytes()
    with pytest.raises(agent.AgentError):agent.initialize_database(database,'PART-A',tmp_path/'backup.sqlite','id')
    assert database.read_bytes()==before and not (tmp_path/'backup.sqlite').exists()


def test_install_command_requires_consent_and_preserves_backup(tmp_path):
    data=tmp_path/'data';data.mkdir();database=external(data/'source.sqlite');patch=tmp_path/'patch.txt';patch.write_text('code fix')
    args=['install','--database',str(database),'--patch',str(patch),'--directory',str(tmp_path/'private'),'--product','PART-A','--no-start']
    before=database.read_bytes()
    assert agent.main(args)==1 and database.read_bytes()==before
    assert agent.main(args+['--consent-schema-extension'])==0
    config=json.loads((tmp_path/'private/agent.json').read_text())
    assert config['product_id']=='PART-A'
    assert business_rows(database)==business_rows(tmp_path/'private/before-install.sqlite')
    assert agent.main(args+['--consent-schema-extension'])==1


def test_event_limit_rejected_before_migration(tmp_path):
    database=external(tmp_path/'source.sqlite')
    with sqlite3.connect(database) as c:
        c.executemany("INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES ('PART-A','restock',1,NULL,104,'')",[()]*201)
    before=database.read_bytes()
    with pytest.raises(agent.AgentError):agent.inspect_source(database,'PART-A')
    assert database.read_bytes()==before


def test_capacity_needs_review_and_retains_room_for_repair_record(connected):
    database,_,_=connected
    with sqlite3.connect(database) as c:
        c.execute("INSERT INTO products VALUES ('OTHER','Other',196)")
        c.executemany("INSERT INTO events(product_id,kind,qty,ts,note) VALUES ('OTHER','restock',1,104,'')",[()]*196)
    agent.scan(register=True);c=store.connect()
    try:
        p=recovery.build_proposal(c,'INC-001','PART-A',[11,14,17,22])
        assert p['status']=='needs_review' and 'capacity' in p['reason']
        assert c.execute("SELECT count(*) FROM events WHERE kind='repair_credit'").fetchone()[0]==0
    finally:c.close()


def test_unrelated_growth_to_capacity_after_approval_blocks_append(connected):
    database,_,_=connected;agent.scan(register=True);c=store.connect()
    try:
        p=recovery.build_proposal(c,'INC-001','PART-A',[11,14,17,22])
        approval=recovery.approve_proposal(c,p['proposal_id'],p['snapshot_hash'],actor_id='operator')
        c.execute("INSERT INTO products VALUES ('OTHER','Other',196)")
        c.executemany("INSERT INTO events(product_id,kind,qty,ts,note) VALUES ('OTHER','restock',1,104,'')",[()]*196);c.commit()
        with pytest.raises(recovery.ApprovalError,match='capacity'):
            recovery.apply_proposal(c,p['proposal_id'],approval['approval_id'],'capacity-key',actor_id='operator')
        assert store.get_product(c,'PART-A')['stock']==119
    finally:c.close()


def test_product_id_must_fit_existing_verification_contract(tmp_path):
    database=external(tmp_path/'source.sqlite')
    with sqlite3.connect(database) as c:
        c.execute("UPDATE products SET product_id=?",('X'*65,));c.execute("UPDATE events SET product_id=?",('X'*65,))
    before=database.read_bytes()
    with pytest.raises(agent.AgentError):agent.inspect_source(database,'X'*65)
    assert database.read_bytes()==before


def test_connected_case_report_preserves_source_patch_and_data_label(connected):
    from app import case_report, investigator
    from app.meta_client import MetaClient
    from tests.test_investigator import FakeTransport, tool_then_final
    agent.scan(register=True);c=store.connect()
    try:
        before,ok=case_report.capture_before(c,'INC-001');assert ok
        fixture=FakeTransport(tool_then_final(['get_patch','read_events'],[11,14,17,22]))
        result=investigator.investigate(c,'INC-001',client=MetaClient(transport=fixture))
        assert result['status']=='proposed'
        case_id=case_report.record_case(c,'operator',before,result)
        report=case_report.build_report(c,case_id)
        assert report['data_label']=='connected_inventory'
        assert report['before']['patch']==connected[2].read_bytes().decode('utf-8')
        assert case_report.list_cases(c,'INC-001')[0]['data_label']=='connected_inventory'
    finally:c.close()
