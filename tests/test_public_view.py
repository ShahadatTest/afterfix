"""Public synthetic viewing is opt-in; every mutation still requires a key."""
import pytest
from fastapi.testclient import TestClient
from app import case_report, investigator, store
from app.main import app, get_investigation_client
from app.meta_client import MetaClient
from tests.test_investigator import FakeTransport, ids, tool_then_final

OPERATOR = 'operator-token-for-tests-1234567890abc'
VIEWER = 'viewer-token-for-tests-1234567890abcd'


@pytest.fixture()
def public_api(tmp_path, monkeypatch):
    monkeypatch.setenv('AFTERFIX_DB', str(tmp_path/'public.sqlite'))
    monkeypatch.setenv('AFTERFIX_OPERATOR_TOKEN', OPERATOR)
    monkeypatch.setenv('AFTERFIX_VIEWER_TOKEN', VIEWER)
    monkeypatch.setenv('AFTERFIX_PUBLIC_VIEWER', '1')
    monkeypatch.setenv('AFTERFIX_ENABLE_SCENARIO_LAB', '1')
    monkeypatch.setenv('AFTERFIX_ALLOW_RESET', '1')
    c = store.connect(); store.reset_and_seed(c); c.close()
    with TestClient(app) as client:
        yield client


@pytest.mark.parametrize('path', ['/auth/me','/incidents/INC-001','/incidents/INC-001/evidence',
                                  '/incidents/INC-001/patch','/incidents/INC-001/cases','/lab/scenarios'])
def test_public_reads_are_viewer_only_and_not_cached(public_api, path):
    response = public_api.get(path)
    assert response.status_code == 200
    assert response.headers['cache-control'] == 'no-store'
    if path == '/auth/me':
        assert response.json() == {'actor_id':'viewer','role':'viewer','can_reset':False}
    if path == '/lab/scenarios':
        assert response.json()['can_load'] is False


@pytest.mark.parametrize('path', ['/admin/reset','/incidents/INC-001/investigate','/preview',
                                  '/approve','/apply','/verify','/lab/scenarios/duplicate_order/load',
                                  '/lab/scenarios/stale_snapshot/advance'])
def test_public_mutations_denied_before_model_or_validation(public_api, path):
    calls = []
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_investigation_client] = lambda: calls.append('unexpected')
    try:
        assert public_api.post(path, json={}).status_code == 401
        assert not calls
        c = store.connect()
        try:
            assert store.get_product(c, 'WIDGET-1')['stock'] == 100
            assert c.execute('SELECT COUNT(*) FROM approvals').fetchone()[0] == 0
            assert c.execute('SELECT COUNT(*) FROM proposals').fetchone()[0] == 0
        finally:
            c.close()
    finally:
        app.dependency_overrides.clear(); app.dependency_overrides.update(previous)


@pytest.mark.parametrize('path', ['/docs','/openapi.json','/tools/manifest','/unknown',
                                  '/incidents/OTHER','/cases/invalid/report'])
def test_public_allowlist_does_not_open_other_routes(public_api, path):
    assert public_api.get(path).status_code == 401


@pytest.mark.parametrize('headers', [ {'Authorization':''}, {'Authorization':'Basic abc'},
    {'Authorization':'Bearer invalid-key'}, [('Authorization','Bearer '+VIEWER),('Authorization','Bearer '+OPERATOR)] ])
def test_bad_auth_never_falls_back_to_public_view(public_api, headers):
    assert public_api.get('/auth/me',headers=headers).status_code == 401


def test_public_mode_default_off(public_api, monkeypatch):
    monkeypatch.delenv('AFTERFIX_PUBLIC_VIEWER')
    assert public_api.get('/incidents/INC-001').status_code == 401
    monkeypatch.setenv('AFTERFIX_PUBLIC_VIEWER','true')
    assert public_api.get('/auth/me').status_code == 401


def test_public_mode_does_not_ignore_broken_auth_configuration(public_api, monkeypatch):
    monkeypatch.setenv('AFTERFIX_OPERATOR_TOKEN','short')
    assert public_api.get('/auth/me').status_code == 503


def test_public_body_bound_and_operator_identity(public_api):
    assert public_api.request('GET','/auth/me',content=b'x'*65537).status_code == 413
    assert public_api.get('/auth/me',headers={'Authorization':'Bearer '+OPERATOR}).json() == {
        'actor_id':'operator','role':'operator','can_reset':True}
    assert public_api.post('/admin/reset',json={},headers={'Authorization':'Bearer '+VIEWER}).status_code == 403


def test_public_report_is_an_actual_read_only_case(public_api):
    c=store.connect()
    try:
        before,ok=case_report.capture_before(c,'INC-001'); assert ok
        result=investigator.investigate(c,'INC-001',client=MetaClient(transport=FakeTransport(
            tool_then_final(['get_patch','read_events'],ids(c)['all']))))
        case_id=case_report.record_case(c,'operator',before,result)
    finally:
        c.close()
    response=public_api.get('/cases/'+case_id+'/report')
    assert response.status_code == 200 and response.json()['case_id'] == case_id
    assert public_api.post('/cases/'+case_id+'/report',json={}).status_code == 401
