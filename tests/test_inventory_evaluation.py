"""Offline tests for held-out evaluation tooling. No paid/live calls, no env/credential reads."""
import hashlib, json, os, shutil, socket
from pathlib import Path
import pytest
from app import store
from app.meta_client import MetaClient
from app.investigator import investigate
from tools import evaluate_inventory as ev
ROOT = Path(__file__).resolve().parents[1]; MANIFEST = ROOT / 'docs' / 'evaluations' / 'heldout-inventory-v1.json'
EXPECTED_DIGEST = 'bca5a6c003d03ef9fedf4eeedd2624ecb13f4f77f2ba3070535db5092aceca51'
CASE_IDS = ['H01-varied','H02-many-extras','H03-preserve-later','H04-clean-replay','H05-missing-opening','H06-conflicting-quantity','H07-adversarial-note']
@pytest.fixture(scope='module')
def manifest():
    data, digest = ev.load_manifest(MANIFEST); assert digest == EXPECTED_DIGEST; return data
def run_offline(tmp_path, name='out', **kw): return ev.run_evaluation(manifest_path=MANIFEST, output_dir=tmp_path / name, **kw)
class FakeGuardBase:
    label = 'simulated_test_guard'; simulated = True; entered = 0; exited = 0
    def __init__(self, runtime_dir):
        assert isinstance(runtime_dir, str) and runtime_dir and Path(runtime_dir).is_absolute(), 'guard requires nonempty absolute str'
        self._fake = ev.OfflineFakeTransport()
    def __enter__(self): type(self).entered += 1; return self
    def __exit__(self, *a): type(self).exited += 1; return False
    def __call__(self, payload): return self._fake(payload)
def test_offline_purity_sentinel_env_no_network_or_real_guard(tmp_path, monkeypatch):
    monkeypatch.setenv('MODEL_API_KEY', 'offline-test-sentinel-not-a-credential')
    real_getenv = os.getenv
    def guarded_getenv(key, default=None):
        if key == 'MODEL_API_KEY': raise AssertionError('credential_read_attempted')
        return real_getenv(key, default)
    def blocked(*a, **k): raise AssertionError('offline_purity_violation')
    monkeypatch.setattr(os, 'getenv', guarded_getenv)
    monkeypatch.setattr('app.runtime_budget.RuntimeBudgetTransport', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    summary = run_offline(tmp_path, case_ids=['H01-varied']); res = summary['_results'][0]
    assert res['passed'], res['failures']
    assert summary['live_ai'] is False and summary['actual_provider_cases'] == 0 and res['provider'] == 'offline_fixture_fake_not_live'
    assert res['usage']['label'] == 'simulated_offline_fixture_not_real_spend'
def test_manifest_digest_and_bounds(manifest):
    assert [c['id'] for c in manifest['cases']] == CASE_IDS
    assert hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest() == EXPECTED_DIGEST
def test_import_and_default_are_offline(manifest): assert ev.OfflineFakeTransport.label == 'offline_fixture_fake' and ev.DEFAULT_MANIFEST.name == 'heldout-inventory-v1.json'
@pytest.mark.parametrize('case_id', CASE_IDS)
def test_all_seven_offline_fixtures(tmp_path, case_id):
    summary = run_offline(tmp_path, case_ids=[case_id]); res = summary['_results'][0]
    assert summary['mode'] == 'offline' and summary['live_ai'] is False and summary['actual_provider_cases'] == 0
    assert res['passed'], res['failures']; assert res['actual_recorded_stock'] == res['expected']['recorded_stock']
    assert (tmp_path / 'out' / f'{case_id}.json').is_file() and (tmp_path / 'out' / 'summary.json').is_file()
def test_full_offline_matrix_and_other_stock_64(tmp_path, manifest):
    summary = run_offline(tmp_path); assert summary['attempted'] == 7 and summary['passed'] == 7
    u = manifest['protocol']['unrelated']; assert u['opening_stock'] - u['order_qty'] + u['restock_qty'] == u['expected_final_stock'] == 64
def test_exact_reverse_ids_and_preview_only_default(tmp_path):
    res = run_offline(tmp_path, case_ids=['H02-many-extras'])['_results'][0]; ded = res['setup']['deduction_ids']
    assert len(ded) == 4 and res['setup']['extra_event_ids'] == ded[1:] and res['actual_reverse_event_ids'] == ded[1:] and res['preview']['reverse_event_ids'] == ded[1:]
    assert res['preview']['rust_status'] == 'verified' and res['preview']['snapshot_match'] is True and res['repairs_after'] == 0 and res['approvals_after'] == 0
def test_unsupported_histories_have_no_actionable_writes(tmp_path):
    for cid in ('H04-clean-replay','H05-missing-opening','H06-conflicting-quantity'):
        res = run_offline(tmp_path, name=cid, case_ids=[cid])['_results'][0]; assert res['passed'], res['failures']; assert res['actual_decision'] == 'needs_review' and res['stop_category'] == 'model_needs_review' and res['repairs_after'] == 0 and res['approvals_after'] == 0
def test_clean_replay_emits_one_actual_deduction(manifest):
    case = next(c for c in manifest['cases'] if c['id'] == 'H04-clean-replay'); conn = store.connect(':memory:')
    try:
        meta = ev.build_case(conn, case, manifest['protocol']['unrelated'], manifest['protocol']['incident_description']); assert len(meta['deduction_ids']) == 1 and meta['extra_ids'] == [] and store.get_product(conn, case['product_id'])['stock'] == 222
    finally: conn.close()
def test_missing_opening_recorded_stock_exists_without_init(manifest):
    case = next(c for c in manifest['cases'] if c['id'] == 'H05-missing-opening'); conn = store.connect(':memory:')
    try:
        ev.build_case(conn, case, manifest['protocol']['unrelated']); assert 'init' not in [r['kind'] for r in store.list_product_events(conn, case['product_id'])] and store.get_product(conn, case['product_id'])['stock'] == 100
    finally: conn.close()
def test_explicit_apply_stable_retry_and_preservation(tmp_path):
    res = run_offline(tmp_path, case_ids=['H01-varied'], apply_synthetic=True)['_results'][0]
    assert res['passed'], res['failures']; assert res['repairs_after'] == 1 and res['approvals_after'] == 1 and res['apply']['retry_equal'] is True
    assert res['verification']['pass'] is True and res['verification']['final_stock'] == res['expected']['corrected_stock'] and res['verification']['checks']
def test_apply_never_runs_for_unsupported(tmp_path):
    res = run_offline(tmp_path, case_ids=['H06-conflicting-quantity'], apply_synthetic=True)['_results'][0]; assert res['passed'] and res['repairs_after'] == 0 and res['approvals_after'] == 0
def test_hash_changed_manifest_fails_before_calls_or_writes(tmp_path, manifest):
    bad = tmp_path / 'heldout-inventory-v1.json'; data = json.loads(json.dumps(manifest)); data['cases'][0]['oracle']['credit_qty'] = 999; bad.write_text(json.dumps(data))
    shutil.copy(ev._sha_path(MANIFEST), tmp_path / ev._sha_path(MANIFEST).name); out = tmp_path / 'out'
    with pytest.raises(ev.EvaluationError): ev.run_evaluation(manifest_path=bad, output_dir=out)
    assert not out.exists()
def test_malformed_manifest_fails_closed(tmp_path):
    bad = tmp_path / 'heldout-inventory-v1.json'; bad.write_text('{not json'); (tmp_path / 'heldout-inventory-v1.sha256').write_text('0'*64)
    with pytest.raises(ev.EvaluationError): ev.load_manifest(bad)
def test_existing_output_and_unknown_case_fail_closed(tmp_path):
    out = tmp_path / 'out'; out.mkdir()
    with pytest.raises(ev.EvaluationError): ev.run_evaluation(manifest_path=MANIFEST, output_dir=out)
    with pytest.raises(ev.EvaluationError): ev.run_evaluation(manifest_path=MANIFEST, output_dir=tmp_path / 'o2', case_ids=['H99-nope'])
    assert not (tmp_path / 'o2').exists()
def test_live_missing_confirmation_or_runtime_fails_closed(tmp_path):
    with pytest.raises(ev.EvaluationError): ev.run_evaluation(manifest_path=MANIFEST, output_dir=tmp_path / 'live', live=True)
    with pytest.raises(ev.EvaluationError): ev.run_evaluation(manifest_path=MANIFEST, output_dir=tmp_path / 'live2', live=True, confirm_synthetic=True, runtime_dir='relative/path')
    assert not (tmp_path / 'live').exists() and not (tmp_path / 'live2').exists()
def test_fake_payloads_never_contain_oracle_material(manifest):
    case = next(c for c in manifest['cases'] if c['id'] == 'H07-adversarial-note'); conn = store.connect(':memory:')
    try:
        meta = ev.build_case(conn, case, manifest['protocol']['unrelated']); fake = ev.OfflineFakeTransport(); result = investigate(conn, meta['incident_id'], client=MetaClient(transport=fake)); assert result['status'] == 'proposed'
        blob = json.dumps(fake.payloads); assert manifest['private_oracle_marker'] not in blob
        for key in ev.FORBIDDEN_PAYLOAD_KEYS: assert f'"{key}"' not in blob
        assert conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0] == 0 and store.get_product(conn, 'MISC-H07-adversarial-note')['stock'] == 64
    finally: conn.close()
def test_fake_decision_uses_only_read_outputs(manifest):
    case = next(c for c in manifest['cases'] if c['id'] == 'H06-conflicting-quantity'); conn = store.connect(':memory:')
    try:
        meta = ev.build_case(conn, case, manifest['protocol']['unrelated']); fake = ev.OfflineFakeTransport(); result = investigate(conn, meta['incident_id'], client=MetaClient(transport=fake)); assert result['status'] == 'needs_review' and fake.payloads and 'text' in fake.payloads[-1]
    finally: conn.close()
def test_failure_remains_failed_and_saved(tmp_path, monkeypatch):
    monkeypatch.setattr(ev, 'evaluate_case', lambda case, unrelated, **kw: {'case_id': case['id'], 'passed': False, 'failures': ['case_error_sanitized'], 'expected': case['oracle'], 'error_category': 'case_error_sanitized'})
    summary = run_offline(tmp_path, case_ids=['H01-varied']); assert summary['failed'] == 1 and summary['passed'] == 0
    saved = json.loads((tmp_path / 'out' / 'H01-varied.json').read_text()); assert saved['passed'] is False and saved['expected']['credit_qty'] == 7
    assert ev.main(['--manifest', str(MANIFEST), '--output', str(tmp_path / 'out2'), '--case', 'H01-varied']) == 1
def _live_with_guard(tmp_path, guard_cls, monkeypatch, cases):
    monkeypatch.setattr('app.runtime_budget.RuntimeBudgetTransport', guard_cls)
    return ev.run_evaluation(manifest_path=MANIFEST, output_dir=tmp_path / 'liveout', case_ids=cases, live=True, confirm_synthetic=True, runtime_dir=str(tmp_path))
def test_live_transport_failure_unknown_usage_stops_first_needs_review(tmp_path, monkeypatch):
    class Raising(FakeGuardBase):
        def __call__(self, payload): raise RuntimeError('injected')
    FakeGuardBase.entered = FakeGuardBase.exited = 0
    summary = _live_with_guard(tmp_path, Raising, monkeypatch, ['H05-missing-opening','H01-varied'])
    res = summary['_results'][0]; assert summary['attempted'] == 1 and summary['stopped_early'] is True and summary['live_ai'] is False and summary['actual_provider_cases'] == 0
    assert res['passed'] is False and res['actual_decision'] == 'needs_review' and res['stop_category'] == 'model_client_error' and res['usage']['unknown_usage_calls'] == 1 and res['usage']['total_tokens'] is None and res['usage']['estimated_cost_usd'] is None
    assert Raising.entered == 1 and Raising.exited == 1
def test_live_guard_entry_failure_zero_actual_and_stops(tmp_path, monkeypatch):
    class EntryFail(FakeGuardBase):
        def __enter__(self): raise RuntimeError('entry')
    summary = _live_with_guard(tmp_path, EntryFail, monkeypatch, ['H01-varied','H02-many-extras'])
    res = summary['_results'][0]; assert summary['attempted'] == 1 and summary['stopped_early'] is True and summary['live_ai'] is False and summary['actual_provider_cases'] == 0 and summary['provider_responses_returned'] == 0
    assert res['passed'] is False and res['client_requests_attempted'] == 0 and res['usage']['requests'] == 0
def test_live_guard_exit_failure_retains_known_usage_and_stops(tmp_path, monkeypatch):
    class ExitFail(FakeGuardBase):
        def __exit__(self, *a): type(self).exited += 1; raise RuntimeError('cleanup')
    FakeGuardBase.entered = FakeGuardBase.exited = 0
    summary = _live_with_guard(tmp_path, ExitFail, monkeypatch, ['H01-varied','H02-many-extras'])
    res = summary['_results'][0]; assert summary['attempted'] == 1 and summary['stopped_early'] is True and res['passed'] is False and 'runtime_guard_cleanup_error' in res['failures']
    assert res['usage']['requests'] and res['usage']['total_tokens'] is not None and ExitFail.exited == 1
def test_successful_injected_guard_is_simulated_counts_and_once(tmp_path, monkeypatch):
    FakeGuardBase.entered = FakeGuardBase.exited = 0
    summary = _live_with_guard(tmp_path, FakeGuardBase, monkeypatch, ['H01-varied'])
    res = summary['_results'][0]; assert res['passed'], res['failures']; assert res['evidence_label'] == 'simulated_test_guard' and summary['live_ai'] is False and summary['actual_provider_cases'] == 0
    assert res['client_requests_attempted'] == res['simulated_responses_returned'] > 0 and res['provider_responses_returned'] == 0 and FakeGuardBase.entered == 1 and FakeGuardBase.exited == 1
def test_run_evaluation_stops_on_unknown_usage_alone(tmp_path, monkeypatch):
    calls = []
    def fake(case, unrelated, **kw):
        calls.append(case['id']); return {'case_id': case['id'], 'passed': False, 'failures': ['operational_failure'], 'expected': case['oracle'], 'actual_decision': 'needs_review', 'stop_category': 'model_needs_review', 'usage': {'label': 'actual', 'unknown_usage_calls': 1, 'total_tokens': None, 'estimated_cost_usd': None}, 'provider_responses_returned': 0, 'client_requests_attempted': 1}
    monkeypatch.setattr(ev, 'evaluate_case', fake); summary = run_offline(tmp_path, case_ids=['H04-clean-replay','H01-varied'])
    assert calls == ['H04-clean-replay'] and summary['attempted'] == 1 and summary['stopped_early'] is True and summary['failed'] == 1 and json.loads((tmp_path / 'out' / 'summary.json').read_text())['stopped_early'] is True
def test_malformed_snapshot_and_rust_binding_fail(tmp_path, monkeypatch):
    orig = ev.recovery.preview_proposal
    def tampered(conn, pid):
        p = json.loads(json.dumps(orig(conn, pid))); p['rust_verification']['snapshot_hash'] = '0'*64; return p
    monkeypatch.setattr(ev.recovery, 'preview_proposal', tampered)
    res = run_offline(tmp_path, case_ids=['H01-varied'])['_results'][0]; assert res['passed'] is False and 'rust_preview_not_verified' in res['failures']
def test_missing_product_binding_fails(tmp_path, monkeypatch):
    orig = ev.investigator.investigate
    def altered(conn, incident_id, client=None):
        r = orig(conn, incident_id, client=client)
        if isinstance(r.get('proposal'), dict): r['proposal'].pop('product_id', None)
        return r
    monkeypatch.setattr(ev.investigator, 'investigate', altered)
    res = run_offline(tmp_path, case_ids=['H01-varied'])['_results'][0]; assert res['passed'] is False and 'proposal_product_mismatch' in res['failures']
def test_case_recording_unavailable_return_fails_without_reinvestigate(tmp_path, monkeypatch):
    monkeypatch.setattr(ev.case_report, 'record_case', lambda *a, **k: None)
    res = run_offline(tmp_path, case_ids=['H01-varied'])['_results'][0]; assert res['passed'] is False and res['case_recording']['status'] == 'unavailable' and 'case_recording_unavailable' in res['failures']

@pytest.mark.parametrize('case_id', CASE_IDS)
def test_runtime_payloads_exclude_descriptive_case_ids_all_cases(manifest, case_id):
    case = next(c for c in manifest['cases'] if c['id'] == case_id); conn = store.connect(':memory:')
    try:
        meta = ev.build_case(conn, case, manifest['protocol']['unrelated'], manifest['protocol']['incident_description'])
        neutral = 'INC-EVAL-' + hashlib.sha256(case['product_id'].encode('utf-8')).hexdigest()[:16]
        assert meta['incident_id'] == neutral and meta['incident_id'] != 'INC-' + case_id
        before, ok = ev.case_report.capture_before(conn, meta['incident_id']); assert ok and before is not None
        fake = ev.OfflineFakeTransport(); result = investigate(conn, meta['incident_id'], client=MetaClient(transport=fake))
        blob = json.dumps(fake.payloads)
        for cid in CASE_IDS: assert cid not in blob
        assert manifest['private_oracle_marker'] not in blob
        for key in ev.FORBIDDEN_PAYLOAD_KEYS: assert f'"{key}"' not in blob
        expected = 'proposed' if case['oracle']['decision'] == 'proposed' else 'needs_review'; assert result['status'] == expected
        if expected == 'proposed':
            prop = result.get('proposal') or {}; assert prop.get('incident_id') == neutral
            preview = ev.recovery.preview_proposal(conn, result['proposal_id']); assert preview['proposal']['incident_id'] == neutral and preview['proposal']['product_id'] == case['product_id']
        else: assert not result.get('proposal') and not result.get('proposal_id')
    finally: conn.close()
