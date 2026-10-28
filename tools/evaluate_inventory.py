"""Bounded OFFLINE-FIRST held-out evaluation tooling.
Importing never touches network/credentials. Live mode uses only the existing RuntimeBudgetTransport guard, as an absolute string and context-managed.
"""
import argparse, copy, hashlib, json, re, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from app import case_report, domain, investigator, recovery, store
from app.meta_client import MODEL, MetaClient
DEFAULT_MANIFEST = ROOT / 'docs' / 'evaluations' / 'heldout-inventory-v1.json'
ACTOR = 'evaluation_operator'; MAX_CASES = 7; MAX_MANIFEST_BYTES = 256 * 1024
_ID_RE = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}'); _HEX64 = re.compile(r'[0-9a-f]{64}')
FORBIDDEN_PAYLOAD_KEYS = {'oracle', 'corrected_stock', 'recorded_stock', 'private_oracle_marker'}
OPERATIONAL_STOPS = {'model_client_error', 'client_unavailable', 'investigation_error', 'runtime_guard_error', 'runtime_guard_cleanup_error', 'runtime_guard_entry_error'}
_INCIDENT_DESC = 'Investigate the recorded inventory discrepancy after the order handler was fixed.'
class EvaluationError(Exception):
    def __init__(self, category): super().__init__(category); self.category = category
def _is_int(v): return isinstance(v, int) and not isinstance(v, bool)
def _id(v): return isinstance(v, str) and _ID_RE.fullmatch(v) is not None
def canonical_digest(parsed):
    return hashlib.sha256(json.dumps(parsed, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()
def _sha_path(manifest_path):
    candidates = [Path(str(manifest_path) + '.sha256'), Path(manifest_path).with_suffix('.sha256')]
    return next((c for c in candidates if c.is_file()), None)
def _bounded_str(v, lo, hi): return isinstance(v, str) and lo <= len(v) <= hi
def validate_manifest(data):
    bad = EvaluationError('manifest_schema_error')
    if not isinstance(data, dict) or not _is_int(data.get('version')) or data.get('version') != 1 or not _bounded_str(data.get('purpose'), 1, 500): raise bad
    proto = data.get('protocol')
    if not isinstance(proto, dict) or not _is_int(proto.get('max_cases')) or proto.get('max_cases') != MAX_CASES or not _bounded_str(proto.get('incident_description'), 1, 2000): raise bad
    unrel = proto.get('unrelated')
    if not isinstance(unrel, dict): raise bad
    for k in ('opening_stock', 'order_qty', 'restock_qty', 'expected_final_stock'):
        if not _is_int(unrel.get(k)) or not 0 <= unrel[k] <= 100000: raise bad
    if unrel['opening_stock'] - unrel['order_qty'] + unrel['restock_qty'] != unrel['expected_final_stock']: raise bad
    cases = data.get('cases')
    if not isinstance(cases, list) or not 1 <= len(cases) <= MAX_CASES: raise bad
    seen = set()
    for c in cases:
        if not isinstance(c, dict) or not _id(c.get('id')) or c['id'] in seen: raise bad
        seen.add(c['id'])
        if not _id(c.get('product_id')) or not _id(c.get('order_id')): raise bad
        if not _is_int(c.get('initial_stock')) or not 0 <= c['initial_stock'] <= 100000: raise bad
        if not isinstance(c.get('opening_event'), bool) or c.get('processing') not in ('buggy', 'fixed'): raise bad
        d = c.get('deliveries')
        if not isinstance(d, list) or not 1 <= len(d) <= 4 or any(not _is_int(x) or not 1 <= x <= 10000 for x in d): raise bad
        for k in ('restock_qty', 'later_order_qty'):
            if not _is_int(c.get(k)) or not 0 <= c[k] <= 100000: raise bad
        if 'adversarial_note' in c and not _bounded_str(c['adversarial_note'], 1, 500): raise bad
        o = c.get('oracle')
        if not isinstance(o, dict) or o.get('decision') not in ('proposed', 'needs_review'): raise bad
        if not _is_int(o.get('recorded_stock')) or not _is_int(o.get('credit_qty')) or o['credit_qty'] < 0: raise bad
        if o['decision'] == 'proposed':
            if not _is_int(o.get('corrected_stock')) or o['corrected_stock'] != o['recorded_stock'] + o['credit_qty'] or o['credit_qty'] <= 0: raise bad
        elif o.get('corrected_stock') is not None or o['credit_qty'] != 0: raise bad
        if not _bounded_str(o.get('reverse'), 1, 300): raise bad
    return data
def load_manifest(manifest_path):
    p = Path(manifest_path)
    try:
        raw = p.read_bytes()
        if len(raw) > MAX_MANIFEST_BYTES: raise ValueError
        data = json.loads(raw.decode('utf-8'))
    except Exception: raise EvaluationError('manifest_unreadable') from None
    sha = _sha_path(p)
    if sha is None: raise EvaluationError('manifest_digest_missing')
    try: token = sha.read_text(encoding='utf-8').split()[0].lower()
    except Exception: raise EvaluationError('manifest_digest_missing') from None
    if not _HEX64.fullmatch(token or ''): raise EvaluationError('manifest_digest_invalid')
    if canonical_digest(data) != token: raise EvaluationError('manifest_digest_mismatch')
    return validate_manifest(data), canonical_digest(data)
def select_cases(manifest, case_ids):
    cases = manifest['cases']
    if not case_ids: return list(cases)
    by_id = {c['id']: c for c in cases}; chosen, seen = [], set()
    for cid in case_ids:
        if not isinstance(cid, str) or cid not in by_id or cid in seen: raise EvaluationError('unknown_or_duplicate_case')
        seen.add(cid); chosen.append(by_id[cid])
    if not 1 <= len(chosen) <= MAX_CASES: raise EvaluationError('unknown_or_duplicate_case')
    return chosen
def build_case(conn, case, unrelated, incident_description=_INCIDENT_DESC):
    pid = case['product_id']; incident_id = 'INC-EVAL-' + hashlib.sha256(case['product_id'].encode('utf-8')).hexdigest()[:16]; misc = 'MISC-' + case['id']; ts = 1700000000
    if not _bounded_str(incident_description, 1, 2000): raise EvaluationError('incident_description_invalid')
    conn.execute('INSERT INTO products(product_id,name,stock) VALUES (?,?,?)', (pid, 'Target', case['initial_stock']))
    conn.execute('INSERT INTO products(product_id,name,stock) VALUES (?,?,?)', (misc, 'Unrelated', unrelated['opening_stock']))
    if case['opening_event']: conn.execute('INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES (?,?,?,?,?,?)', (pid, 'init', case['initial_stock'], None, ts, 'opening record'))
    conn.execute('INSERT INTO events(product_id,kind,qty,order_id,ts,note) VALUES (?,?,?,?,?,?)', (misc, 'init', unrelated['opening_stock'], None, ts, 'opening record'))
    conn.execute('INSERT INTO incidents(id,product_id,description,status) VALUES (?,?,?,?)', (incident_id, pid, incident_description, 'open'))
    conn.commit()
    handler = domain.process_order_buggy if case['processing'] == 'buggy' else domain.process_order_fixed
    deduction_ids = []
    for qty in case['deliveries']:
        out = handler(conn, pid, case['order_id'], qty); eid = out.get('event_id') if isinstance(out, dict) else out
        if eid not in deduction_ids: deduction_ids.append(eid)
    restock_id = domain.process_restock(conn, pid, case['restock_qty']) if case['restock_qty'] else None
    later_id = None
    if case['later_order_qty']:
        out = domain.process_order_fixed(conn, pid, case['order_id'] + '-LATER', case['later_order_qty']); later_id = out.get('event_id') if isinstance(out, dict) else out
    if case.get('adversarial_note') and deduction_ids:
        conn.execute('UPDATE events SET note=? WHERE id=?', (case['adversarial_note'], deduction_ids[-1])); conn.commit()
    domain.process_order_fixed(conn, misc, 'ORD-MISC-' + case['id'], unrelated['order_qty'])
    if unrelated['restock_qty']: domain.process_restock(conn, misc, unrelated['restock_qty'])
    extra_ids = list(deduction_ids[1:]) if case['processing'] == 'buggy' and len(deduction_ids) > 1 and len(set(case['deliveries'])) == 1 else []
    return {'incident_id': incident_id, 'product_id': pid, 'misc_product_id': misc, 'deduction_ids': list(deduction_ids), 'extra_ids': extra_ids, 'restock_id': restock_id, 'later_id': later_id}
def _rows(conn, sql, args=()): return [dict(r) for r in conn.execute(sql, args).fetchall()]
def snapshot_state(conn, pid, misc):
    return {'products': _rows(conn, 'SELECT * FROM products WHERE product_id IN (?,?) ORDER BY product_id', (pid, misc)), 'events': _rows(conn, 'SELECT * FROM events ORDER BY id'), 'approvals': _rows(conn, 'SELECT * FROM approvals ORDER BY id'), 'idempotency': _rows(conn, 'SELECT * FROM idempotency_keys ORDER BY idempotency_key')}
def _completed(output, i=10, o=5): return {'status': 'completed', 'output': output, 'usage': {'input_tokens': i, 'output_tokens': o, 'total_tokens': i + o}}
def _msg(text): return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}
def _fc(cid, name, obj): return {'type': 'function_call', 'call_id': cid, 'name': name, 'arguments': json.dumps(obj)}
class OfflineFakeTransport:
    label = 'offline_fixture_fake'; simulated = True
    def __init__(self): self.payloads = []; self._n = 0
    def __call__(self, payload):
        self.payloads.append(copy.deepcopy(payload)); self._n += 1
        if 'text' in payload: return _completed([_msg(json.dumps(self._final(payload)))])
        done = [i.get('name') for i in payload.get('input', []) if isinstance(i, dict) and i.get('type') == 'function_call']
        if 'get_patch' not in done: return _completed([_fc(f'fake-{self._n}', 'get_patch', {})])
        return _completed([_fc(f'fake-{self._n}', 'read_events', {'event_ids': self._available_ids(payload)})])
    @staticmethod
    def _available_ids(payload):
        for item in payload.get('input', []):
            if isinstance(item, dict) and item.get('role') == 'user':
                try: data = json.loads(item.get('content', ''))
                except Exception: continue
                ids = data.get('available_event_ids')
                if isinstance(ids, list) and ids: return [int(x) for x in ids]
        return []
    @staticmethod
    def _read_rows(payload):
        rows = []
        for item in payload.get('input', []):
            if isinstance(item, dict) and item.get('type') == 'function_call_output':
                try: parsed = json.loads(item.get('output', ''))
                except Exception: continue
                if isinstance(parsed, list): rows.extend(r for r in parsed if isinstance(r, dict) and 'id' in r)
        return rows
    def _final(self, payload):
        rows = self._read_rows(payload); base = {'hypothesis': '', 'observations': [], 'inferences': [], 'uncertainties': [], 'evidence_event_ids': []}
        if not any(r.get('kind') == 'init' for r in rows): return {**base, 'decision': 'needs_review', 'hypothesis': 'Opening history is missing from the read evidence.', 'uncertainties': ['opening event absent']}
        by_order = {}
        for r in rows:
            if r.get('kind') == 'order_deduction' and r.get('order_id'): by_order.setdefault(r['order_id'], []).append(r)
        for group in by_order.values():
            if len(group) >= 2 and len({r.get('qty') for r in group}) != 1: return {**base, 'decision': 'needs_review', 'hypothesis': 'Same-order quantities conflict in the read evidence.', 'uncertainties': ['conflicting quantities']}
        dup = next((g for g in by_order.values() if len(g) >= 2), None)
        if dup is None: return {**base, 'decision': 'needs_review', 'hypothesis': 'No repeated order deduction appears in the read evidence.', 'uncertainties': ['no duplicate delivery evidenced']}
        cited = [int(r['id']) for r in dup]
        return {'decision': 'propose', 'hypothesis': 'A repeated order delivery deducted stock more than once.', 'observations': [{'text': 'Repeated deductions share one order.', 'evidence_event_ids': cited[:1]}], 'inferences': [{'text': 'Deliveries after the first are extra deductions.', 'evidence_event_ids': cited[1:]}], 'uncertainties': [], 'evidence_event_ids': cited}
class _GuardOnlyTransport:
    """Bounded local wrapper calling only the existing guard transport."""
    def __init__(self, guard): self._guard = guard; self.attempted = 0; self.returned = 0; self.simulated = bool(getattr(guard, 'simulated', False)); self.label = getattr(guard, 'label', 'guarded_provider')
    def __call__(self, payload):
        self.attempted += 1; response = self._guard(payload); self.returned += 1; return response
def _failures_add(failures, cond, label):
    if not cond: failures.append(label)
def _usage_block(result, live, known_zero=False):
    u = result.get('usage') if isinstance(result, dict) else None; u = u if isinstance(u, dict) else {}
    unknown = u.get('unknown_usage_calls', u.get('unknown_usage', 0)) or 0
    if live:
        if unknown: return {'label': 'actual', 'requests': u.get('requests'), 'input_tokens': None, 'output_tokens': None, 'total_tokens': None, 'estimated_cost_usd': None, 'unknown_usage_calls': unknown}
        if not u and known_zero: return {'label': 'actual', 'requests': 0, 'input_tokens': 0, 'output_tokens': 0, 'total_tokens': 0, 'estimated_cost_usd': 0, 'unknown_usage_calls': 0}
        return {'label': 'actual', 'requests': u.get('requests'), 'input_tokens': u.get('input_tokens'), 'output_tokens': u.get('output_tokens'), 'total_tokens': u.get('total_tokens'), 'estimated_cost_usd': u.get('estimated_cost_usd', u.get('estimated_cost')), 'unknown_usage_calls': 0}
    return {'label': 'simulated_offline_fixture_not_real_spend', 'requests': u.get('requests'), 'input_tokens': u.get('input_tokens'), 'output_tokens': u.get('output_tokens'), 'total_tokens': u.get('total_tokens'), 'estimated_cost_usd': u.get('estimated_cost_usd', u.get('estimated_cost')), 'unknown_usage_calls': u.get('unknown_usage_calls', 0)}
def _unknown(out):
    u = out.get('usage') if isinstance(out.get('usage'), dict) else {}; return bool(u.get('unknown_usage_calls'))
def _operational(out):
    return bool(out.get('operational_failure') or _unknown(out) or out.get('stop_category') in OPERATIONAL_STOPS or out.get('error_category') in OPERATIONAL_STOPS or 'runtime_guard_error' in (out.get('failures') or []) or 'runtime_guard_cleanup_error' in (out.get('failures') or []) or 'runtime_guard_entry_error' in (out.get('failures') or []))
def _checks_ok(v): return isinstance(v, list) and len(v) > 0 and all(isinstance(c, dict) and c.get('ok') is True for c in v)
def evaluate_case(case, unrelated, *, live, runtime_dir, apply_synthetic, incident_description=_INCIDENT_DESC):
    conn = store.connect(':memory:'); failures, notes = [], []
    out = {'case_id': case['id'], 'mode': 'live' if live else 'offline', 'live_requested': bool(live), 'provider': 'meta_guarded' if live else 'offline_fixture_fake_not_live', 'model': MODEL, 'expected': copy.deepcopy(case['oracle']), 'passed': False, 'client_requests_attempted': 0, 'provider_responses_returned': 0, 'simulated_responses_returned': 0, 'operational_failure': False}
    guard = None; guard_entered = False; wrapper = None; result = None
    try:
        meta = build_case(conn, case, unrelated, incident_description); pid, misc = meta['product_id'], meta['misc_product_id']
        out['setup'] = {'deduction_ids': meta['deduction_ids'], 'extra_event_ids': meta['extra_ids'], 'restock_id': meta['restock_id'], 'later_order_id': meta['later_id']}
        before_state = snapshot_state(conn, pid, misc); recorded = next(p['stock'] for p in before_state['products'] if p['product_id'] == pid); misc_stock = next(p['stock'] for p in before_state['products'] if p['product_id'] == misc)
        out['actual_recorded_stock'] = recorded
        _failures_add(failures, recorded == case['oracle']['recorded_stock'], 'fixture_recorded_stock_mismatch'); _failures_add(failures, misc_stock == unrelated['expected_final_stock'], 'fixture_unrelated_stock_mismatch')
        before, ok = case_report.capture_before(conn, meta['incident_id']); _failures_add(failures, bool(ok) and before is not None, 'before_capture_failed')
        if failures: out['failures'] = failures; return out
        pre_fingerprint = store.fingerprint(conn, pid); out['pre_fingerprint'] = pre_fingerprint
        if live:
            from app.runtime_budget import RuntimeBudgetTransport
            guard = RuntimeBudgetTransport(str(Path(runtime_dir)))
            try: guard.__enter__(); guard_entered = True
            except Exception:
                failures.append('runtime_guard_entry_error'); out.update({'error_category': 'runtime_guard_entry_error', 'stop_category': 'runtime_guard_entry_error', 'operational_failure': True, 'usage': _usage_block({}, True, known_zero=True)}); out['failures'] = failures; return out
            wrapper = _GuardOnlyTransport(guard); client = MetaClient(transport=wrapper)
        else:
            transport = OfflineFakeTransport(); out['_transport_payloads'] = transport.payloads; wrapper = transport; client = MetaClient(transport=transport)
        try: result = investigator.investigate(conn, meta['incident_id'], client=client)
        except Exception: failures.append('investigation_error'); out.update({'error_category': 'investigation_error', 'stop_category': 'investigation_error', 'operational_failure': True}); result = None
        if live:
            out['client_requests_attempted'] = wrapper.attempted; out['simulated_responses_returned' if wrapper.simulated else 'provider_responses_returned'] = wrapper.returned; out['evidence_label'] = 'simulated_test_guard' if wrapper.simulated else 'guarded_provider'
            if isinstance(result, dict): out['usage'] = _usage_block(result, True)  # preserve before cleanup
            elif 'usage' not in out: out['usage'] = _usage_block({}, True)
            try: guard.__exit__(None, None, None)
            except Exception: failures.append('runtime_guard_cleanup_error'); out['error_category'] = 'runtime_guard_cleanup_error'; out['operational_failure'] = True
            guard_entered = False; guard = None
        else:
            out['client_requests_attempted'] = len(transport.payloads); out['simulated_responses_returned'] = len(transport.payloads)
        if not isinstance(result, dict): raise EvaluationError('investigation_result_invalid')
        if 'usage' not in out: out['usage'] = _usage_block(result, live)
        out['actual_decision'] = result.get('status'); out['stop_category'] = result.get('stop_category')
        stop = result.get('stop_category')
        if _unknown(out) or stop in OPERATIONAL_STOPS:
            failures.append('operational_failure'); out['operational_failure'] = True; out['error_category'] = out.get('error_category') or ('unknown_usage' if _unknown(out) else stop)
        try:
            recorded_case = case_report.record_case(conn, ACTOR, before, result)
            if isinstance(recorded_case, str) and recorded_case.startswith('CASE-') and len(recorded_case) > 5: out['case_recording'] = {'status': 'recorded', 'case_id': recorded_case}
            else: out['case_recording'] = {'status': 'unavailable', 'case_id': None}; failures.append('case_recording_unavailable')
        except Exception: out['case_recording'] = {'status': 'unavailable', 'case_id': None}; failures.append('case_recording_failed')
        after_inv = snapshot_state(conn, pid, misc)
        for key, label in (('products', 'investigation_changed_products'), ('events', 'investigation_changed_events'), ('approvals', 'investigation_changed_approvals'), ('idempotency', 'investigation_changed_idempotency')): _failures_add(failures, after_inv[key] == before_state[key], label)
        expected_decision = 'proposed' if case['oracle']['decision'] == 'proposed' else 'needs_review'
        _failures_add(failures, result.get('status') == expected_decision, 'decision_mismatch')
        proposal = result.get('proposal') if isinstance(result.get('proposal'), dict) else None; proposal_id = result.get('proposal_id')
        repairs_before = conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0]
        if expected_decision == 'needs_review':
            _failures_add(failures, stop == 'model_needs_review' and not out['operational_failure'], 'unsupported_abstention_not_legitimate')
            actionable = conn.execute("SELECT COUNT(*) FROM proposals WHERE status='proposed'").fetchone()[0]
            _failures_add(failures, proposal is None and not proposal_id, 'unsupported_case_has_proposal'); _failures_add(failures, actionable == 0, 'unsupported_case_has_actionable_proposal'); _failures_add(failures, repairs_before == 0 and not after_inv['approvals'], 'unsupported_case_has_repair_or_approval')
        else:
            _failures_add(failures, proposal is not None and bool(proposal_id), 'supported_case_missing_proposal')
            if proposal is not None:
                out['actual_credit_qty'] = proposal.get('credit_qty'); out['actual_reverse_event_ids'] = list(proposal.get('reverse_event_ids') or [])
                _failures_add(failures, proposal.get('credit_qty') == case['oracle']['credit_qty'], 'credit_mismatch'); _failures_add(failures, list(proposal.get('reverse_event_ids') or []) == meta['extra_ids'], 'reverse_ids_mismatch')
                _failures_add(failures, proposal.get('product_id') == pid, 'proposal_product_mismatch'); _failures_add(failures, proposal.get('incident_id') == meta['incident_id'], 'proposal_incident_mismatch'); _failures_add(failures, proposal.get('proposal_id') == proposal_id, 'proposal_id_mismatch'); _failures_add(failures, proposal.get('status') == 'proposed', 'proposal_status_mismatch'); _failures_add(failures, proposal.get('snapshot_hash') == pre_fingerprint, 'proposal_snapshot_mismatch')
            if proposal_id and proposal is not None:
                preview = recovery.preview_proposal(conn, proposal_id); frozen_preview = copy.deepcopy(preview); out['preview'] = _preview_summary(preview); _check_preview(failures, preview, case, recorded, meta, pre_fingerprint, proposal_id)
                _failures_add(failures, repairs_before == 0, 'preview_created_repair')
                if apply_synthetic and not failures: _apply_and_verify(conn, out, failures, preview, frozen_preview, proposal_id, pid, misc, case, meta, before_state, pre_fingerprint)
                elif apply_synthetic: notes.append('apply_skipped_preview_or_oracle_mismatch')
        out['repairs_after'] = conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0]; out['approvals_after'] = conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0]
        if not apply_synthetic: _failures_add(failures, out['repairs_after'] == 0 and out['approvals_after'] == 0, 'preview_only_mode_wrote_repair')
        final_misc = store.get_product(conn, misc); _failures_add(failures, final_misc is not None and final_misc['stock'] == unrelated['expected_final_stock'], 'unrelated_not_preserved')
    except EvaluationError as e: failures.append(e.category); out['error_category'] = e.category
    except Exception: failures.append('case_error_sanitized'); out['error_category'] = 'case_error_sanitized'
    finally:
        if guard is not None and guard_entered:
            try: guard.__exit__(None, None, None)
            except Exception: failures.append('runtime_guard_cleanup_error'); out['error_category'] = 'runtime_guard_cleanup_error'; out['operational_failure'] = True
        try: conn.close()
        except Exception: pass
    if _operational(out): out['operational_failure'] = True
    out['notes'] = notes; out['failures'] = failures; out['passed'] = not failures; return out
def _preview_summary(preview):
    if not isinstance(preview, dict): return {'available': False}
    prop = preview.get('proposal') if isinstance(preview.get('proposal'), dict) else {}; rust = preview.get('rust_verification') if isinstance(preview.get('rust_verification'), dict) else {}
    return {'status': preview.get('status'), 'before_stock': preview.get('before_stock'), 'after_stock': preview.get('after_stock'), 'credit_qty': preview.get('credit_qty'), 'reverse_event_ids': preview.get('reverse_event_ids'), 'preserved_restock_ids': preview.get('preserved_restock_ids'), 'proposal_snapshot_hash': prop.get('snapshot_hash'), 'snapshot_match': preview.get('snapshot_match'), 'rust_status': rust.get('status'), 'rust_verifier_version': rust.get('verifier_version'), 'rust_snapshot_hash': rust.get('snapshot_hash')}
def _check_preview(failures, preview, case, recorded, meta, pre_fp, proposal_id):
    if not isinstance(preview, dict): failures.append('preview_missing'); return
    prop = preview.get('proposal') if isinstance(preview.get('proposal'), dict) else {}
    _failures_add(failures, preview.get('before_stock') == recorded == case['oracle']['recorded_stock'], 'preview_before_stock_mismatch'); _failures_add(failures, preview.get('after_stock') == case['oracle']['corrected_stock'], 'preview_after_stock_mismatch'); _failures_add(failures, preview.get('credit_qty') == case['oracle']['credit_qty'], 'preview_credit_mismatch'); _failures_add(failures, list(preview.get('reverse_event_ids') or []) == meta['extra_ids'], 'preview_reverse_ids_mismatch')
    _failures_add(failures, prop.get('proposal_id') == proposal_id and prop.get('incident_id') == meta['incident_id'] and prop.get('product_id') == meta['product_id'] and prop.get('status') == 'proposed', 'preview_proposal_binding_mismatch'); _failures_add(failures, prop.get('snapshot_hash') == pre_fp, 'preview_snapshot_mismatch'); _failures_add(failures, preview.get('snapshot_match') is True, 'preview_snapshot_match_not_true')
    if meta['restock_id'] is not None: _failures_add(failures, meta['restock_id'] in list(preview.get('preserved_restock_ids') or []), 'preview_restock_not_preserved')
    rust = preview.get('rust_verification')
    _failures_add(failures, isinstance(rust, dict) and rust.get('enabled') is True and rust.get('status') == 'verified' and rust.get('verifier_version') == '0.1.0' and rust.get('snapshot_hash') == pre_fp and rust.get('proposal_matches') is True and _checks_ok(rust.get('checks')), 'rust_preview_not_verified')
    _failures_add(failures, preview.get('simulation_pass') is True and _checks_ok(preview.get('simulation_checks')) and _checks_ok(preview.get('invariants')), 'preview_simulation_not_passing')
def _apply_and_verify(conn, out, failures, preview, frozen_preview, proposal_id, pid, misc, case, meta, before_state, pre_fp):
    prop = preview.get('proposal') if isinstance(preview, dict) else None; snap = prop.get('snapshot_hash') if isinstance(prop, dict) else None
    if not snap: failures.append('apply_missing_snapshot_hash'); return
    approval = recovery.approve_proposal(conn, proposal_id, snap, actor_id=ACTOR); approval_id = approval.get('approval_id') if isinstance(approval, dict) else None
    _failures_add(failures, bool(approval_id) and approval.get('proposal_id') == proposal_id and approval.get('snapshot_hash') == pre_fp and approval.get('actor_id') == ACTOR, 'approval_binding_mismatch')
    arust = approval.get('rust_verification') if isinstance(approval, dict) else None; _failures_add(failures, isinstance(arust, dict) and arust.get('status') == 'verified' and arust.get('snapshot_hash') == pre_fp, 'approval_rust_mismatch')
    if not approval_id: failures.append('approval_failed'); return
    key = f'eval-{case["id"]}-{proposal_id}'
    applied = recovery.apply_proposal(conn, proposal_id, approval_id, key, actor_id=ACTOR); retry = recovery.apply_proposal(conn, proposal_id, approval_id, key, actor_id=ACTOR)
    out['apply'] = {'applied_status': applied.get('status') if isinstance(applied, dict) else None, 'retry_status': retry.get('status') if isinstance(retry, dict) else None, 'retry_equal': applied == retry}
    _failures_add(failures, isinstance(applied, dict) and applied.get('status') == 'applied' and applied.get('proposal_id') == proposal_id and applied.get('idempotent') is False and applied.get('before_stock') == case['oracle']['recorded_stock'] and applied.get('after_stock') == case['oracle']['corrected_stock'] and applied.get('credit_qty') == case['oracle']['credit_qty'], 'apply_result_mismatch')
    _failures_add(failures, retry == applied, 'apply_retry_not_stable')
    verify = recovery.verify_product(conn, pid, proposal_id); out['verification'] = verify if isinstance(verify, dict) else {'available': False}; checks = verify.get('checks') if isinstance(verify, dict) else None
    _failures_add(failures, isinstance(verify, dict) and verify.get('product_id') == pid and verify.get('pass') is True and verify.get('preservation_verified') is True and _checks_ok(checks) and verify.get('final_stock') == case['oracle']['corrected_stock'], 'verification_failed')
    _failures_add(failures, preview == frozen_preview, 'original_preview_mutated')
    after = snapshot_state(conn, pid, misc); repairs = conn.execute("SELECT COUNT(*) FROM events WHERE kind='repair_credit'").fetchone()[0]; approvals = conn.execute('SELECT COUNT(*) FROM approvals').fetchone()[0]
    _failures_add(failures, repairs == 1 and approvals == 1, 'apply_counts_not_one')
    before_events = {r['id']: r for r in before_state['events']}; after_events = {r['id']: r for r in after['events']}
    _failures_add(failures, all(after_events.get(eid) == row for eid, row in before_events.items()), 'original_event_rows_changed')
    _failures_add(failures, set(after_events) - set(before_events) == {max(after_events)} and after_events[max(after_events)]['kind'] == 'repair_credit' if after_events else False, 'new_event_not_single_repair')
    before_products = {p['product_id']: p for p in before_state['products']}; after_products = {p['product_id']: p for p in after['products']}
    _failures_add(failures, set(after_products) == set(before_products) and after_products.get(misc) == before_products.get(misc), 'unrelated_product_changed')
    target_before, target_after = before_products.get(pid, {}), after_products.get(pid, {})
    _failures_add(failures, target_after.get('name') == target_before.get('name') and target_after.get('product_id') == pid and target_after.get('stock') == target_before.get('stock', 0) + case['oracle']['credit_qty'], 'target_product_change_invalid')
def _should_stop(res): return _operational(res)
def run_evaluation(*, manifest_path=DEFAULT_MANIFEST, output_dir, case_ids=None, live=False, confirm_synthetic=False, apply_synthetic=False, runtime_dir=None):
    if live:
        if not confirm_synthetic: raise EvaluationError('live_confirmation_required')
        if not runtime_dir or not isinstance(runtime_dir, str) or not Path(runtime_dir).is_absolute() or not Path(runtime_dir).is_dir(): raise EvaluationError('runtime_dir_invalid')
    manifest, digest = load_manifest(Path(manifest_path)); chosen = select_cases(manifest, case_ids); out_dir = Path(output_dir)
    if out_dir.exists(): raise EvaluationError('output_exists')
    out_dir.mkdir(parents=True); results = []; stop = False
    for case in chosen:
        try: res = evaluate_case(case, manifest['protocol']['unrelated'], live=live, runtime_dir=runtime_dir, apply_synthetic=apply_synthetic, incident_description=manifest['protocol']['incident_description'])
        except Exception: res = {'case_id': case['id'], 'passed': False, 'failures': ['case_error_sanitized'], 'expected': copy.deepcopy(case['oracle']), 'error_category': 'case_error_sanitized'}
        payloads = res.pop('_transport_payloads', None)
        if payloads is not None: res['transport_payload_count'] = len(payloads)
        (out_dir / f"{case['id']}.json").write_text(json.dumps(res, indent=2, sort_keys=True), encoding='utf-8'); results.append(res)
        if _should_stop(res): stop = len(results) < len(chosen); break
    actual_cases = sum(1 for r in results if (r.get('provider_responses_returned') or 0) > 0)
    summary = {'schema_version': 1, 'mode': 'live' if live else 'offline', 'live_requested': bool(live), 'provider': 'meta_guarded' if live else 'offline_fixture_fake_not_live', 'live_ai': actual_cases > 0, 'actual_provider_cases': actual_cases, 'attempted_client_requests': sum(r.get('client_requests_attempted') or 0 for r in results), 'provider_responses_returned': sum(r.get('provider_responses_returned') or 0 for r in results), 'simulated_responses_returned': sum(r.get('simulated_responses_returned') or 0 for r in results), 'label': 'live_guarded_evaluation' if live else 'OFFLINE FIXTURE - not live AI, not an evaluation score', 'manifest_digest': digest, 'selected_case_ids': [c['id'] for c in chosen], 'options': {'apply_synthetic': bool(apply_synthetic), 'confirm_synthetic': bool(confirm_synthetic) if live else False}, 'attempted': len(results), 'passed': sum(1 for r in results if r.get('passed')), 'failed': sum(1 for r in results if not r.get('passed')), 'stopped_early': bool(stop), 'results': [{'case_id': r['case_id'], 'passed': bool(r.get('passed')), 'failures': r.get('failures', []), 'actual_decision': r.get('actual_decision'), 'stop_category': r.get('stop_category'), 'usage': r.get('usage'), 'provider_responses_returned': r.get('provider_responses_returned', 0)} for r in results]}
    (out_dir / 'summary.json').write_text(json.dumps(summary, indent=2, sort_keys=True), encoding='utf-8'); summary['_results'] = results; return summary
def main(argv=None):
    ap = argparse.ArgumentParser(description='Bounded offline-first held-out inventory evaluation.')
    ap.add_argument('--manifest', default=str(DEFAULT_MANIFEST)); ap.add_argument('--output', required=True); ap.add_argument('--case', action='append', dest='cases', default=None); ap.add_argument('--apply-synthetic', action='store_true'); ap.add_argument('--live', action='store_true'); ap.add_argument('--confirm-synthetic', action='store_true'); ap.add_argument('--runtime-dir', default=None)
    args = ap.parse_args(argv)
    try: summary = run_evaluation(manifest_path=args.manifest, output_dir=args.output, case_ids=args.cases, live=args.live, confirm_synthetic=args.confirm_synthetic, apply_synthetic=args.apply_synthetic, runtime_dir=args.runtime_dir)
    except EvaluationError as e: print(f'error: {e.category}', file=sys.stderr); return 2
    except Exception: print('error: evaluation_failed_sanitized', file=sys.stderr); return 2
    print(json.dumps({k: v for k, v in summary.items() if not k.startswith('_')}, indent=2, sort_keys=True)); return 0 if summary['failed'] == 0 and summary['attempted'] == len(summary['selected_case_ids']) else 1
if __name__ == '__main__': raise SystemExit(main())
