"""Exact local retest receipts and smoke-read bindings. -- PI/Sol"""
import fcntl
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from unittest.mock import patch


ACTIVE_STUDY_LEDGER = ContextVar('active_study_ledger', default=None)


def replace_json(path, value):
    """Keep the prior checkpoint intact until the replacement is fully written. -- PI/Sol"""
    path = Path(path)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as file:
        temporary = Path(file.name)
        try:
            json.dump(value, file, ensure_ascii=False, indent=2)
            file.write('\n')
            file.flush()
            os.fsync(file.fileno())
        except BaseException:
            temporary.unlink()
            raise
    os.replace(temporary, path)


def receipt_paths(cache, key):
    return [cache / 'charges' / key[:2] / (key + '.jsonl'), cache / 'failures' / (key + '.jsonl')]


def start_accounting(path, keys, cache):
    """Claim a run-exclusive cache before creating even a cached-only baseline. -- PI/Sol"""
    cache.mkdir(parents=True, exist_ok=True)
    with (cache / 'study_owner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        owner_path = cache / 'study_owner.json'
        owner = dict(output_directory=str(path.parent.resolve()))
        if owner_path.exists():
            assert json.loads(owner_path.read_text()) == owner, 'Use a run-exclusive cache: another resolved output owns this cache'
        baseline = json.loads(path.read_text()) if path.exists() else None
        if baseline is not None:
            assert baseline['keys'] == keys
            assert set(baseline['files']) == {str(file) for key in keys for file in receipt_paths(cache, key)}, 'Accounting cache changed'
        if not owner_path.exists():
            # A legacy baseline cannot retroactively claim another run's attempts.
            if baseline is not None:
                for name, prefix in baseline['files'].items():
                    content = Path(name).read_bytes() if Path(name).exists() else b''
                    assert len(content) == prefix['offset'] and hashlib.sha256(content).hexdigest() == prefix['sha256'], 'Unowned existing receipt history; use a fresh run-exclusive cache/output'
            replace_json(owner_path, owner)
        if baseline is None:
            files = {}
            for key in keys:
                for file in receipt_paths(cache, key):
                    content = file.read_bytes() if file.exists() else b''
                    files[str(file)] = dict(offset=len(content), sha256=hashlib.sha256(content).hexdigest(), key=key)
            baseline = dict(author='PI/Sol', keys=keys, files=files)
            replace_json(path, baseline)
        return baseline


def failed_attempts(baseline, key):
    """Full post-baseline failed HTTP/transport replies, in retained attempt order. -- PI/Sol"""
    paths = [(Path(name), prefix) for name, prefix in baseline['files'].items()
             if prefix['key'] == key and Path(name).parent.name == 'failures']
    assert len(paths) == 1
    path, prefix = paths[0]
    content = path.read_bytes() if path.exists() else b''
    assert len(content) >= prefix['offset'] and hashlib.sha256(content[:prefix['offset']]).hexdigest() == prefix['sha256'], 'Failure receipt prefix changed'
    rows = [json.loads(line) for line in content[prefix['offset']:].decode().splitlines()]
    assert all(row['key'] == key for row in rows)
    return rows


def collect_charges(baseline):
    charges, unknown = {}, []
    zero_cost_without_id = 0
    reconciled = []
    for name, prefix in baseline['files'].items():
        file = Path(name)
        content = file.read_bytes() if file.exists() else b''
        offset = prefix['offset']
        assert hashlib.sha256(content[:offset]).hexdigest() == prefix['sha256'], 'Receipt prefix changed: ' + name
        assert len(content) >= offset
        disposition_path = file.with_suffix('.billing.json')
        dispositions = json.loads(disposition_path.read_text())['unbilled_failures'] if disposition_path.exists() else []
        assert len({r['line'] for r in dispositions}) == len(dispositions)
        for line_number, line in enumerate(content[offset:].decode().splitlines(), start=len(content[:offset].splitlines()) + 1):
            row = json.loads(line)
            assert row['key'] == prefix['key']
            if file.parent.name == 'failures':
                body = row['response']
                usage = body['usage'] if isinstance(body,dict) and 'usage' in body else row['usage']
                response_id = body['id'] if isinstance(body,dict) and 'id' in body else None
            else:
                usage, response_id = row['usage'], row['response_id']
            if not isinstance(usage,dict) or 'cost' not in usage:
                approved = [r for r in dispositions if r['line'] == line_number]
                if approved:
                    assert file.parent.name == 'failures' and len(approved) == 1
                    decision = approved[0]
                    assert decision['sha256'] == hashlib.sha256(line.encode()).hexdigest(), 'Reconciled failure changed'
                    assert decision['status'] == 'unbilled_provider_overload' and decision['authority']
                    assert row['http_status'] == 529 and response_id is None and usage is None
                    assert body['error']['code'] == 529 and 'system_overloaded' in body['error']['message']
                    assert 'cannot process your request' in body['error']['message']
                    reconciled.append(dict(path=name, line=line_number, **{k:decision[k] for k in ('sha256','status','authority')}))
                else:
                    unknown.append(dict(path=name, response_id=response_id, reason='No returned billing cost; not assumed free'))
                continue
            cost = Decimal(str(usage['cost']))
            assert cost.is_finite() and cost >= 0
            if not response_id:
                if cost == 0:
                    zero_cost_without_id += 1
                    continue
                unknown.append(dict(path=name, response_id=None, reason='Positive returned cost lacks billing identity'))
                continue
            assert isinstance(response_id,str)
            if response_id in charges:
                assert charges[response_id]['cost_usd'] == str(cost), 'Conflicting costs for billing ID'
            else:
                charges[response_id] = dict(response_id=response_id, cost_usd=str(cost), key=row['key'])
    unknown = [r for r in unknown if r['response_id'] not in charges or r['response_id'] is None]
    result = dict(author='PI/Sol', cost_usd=str(sum((Decimal(r['cost_usd']) for r in charges.values()), Decimal(0))), charges=list(charges.values()), unresolved_billing=unknown, explicit_zero_cost_records_without_id=zero_cost_without_id)
    if reconciled:
        result['unbilled_provider_rejections'] = reconciled
    return result


@contextmanager
def paid_attempt_guard(llm, checkpoint, known_stop, next_allowance, reserve):
    """Check every paid attempt, including retries, after retained failure receipts. -- PI/Sol"""
    original = llm.require_paid_approval
    def approve(scope, **request_context):
        original(scope, **request_context)
        spent = checkpoint()
        assert spent < known_stop, 'Known cumulative cost stop reached before HTTP attempt'
        assert spent + next_allowance < reserve, 'Insufficient next-attempt reserved allowance'
    with patch.object(llm, 'require_paid_approval', approve):
        yield


STUDY_FUNDING_SCOPE = 'rubric_source_led_20261019'


def charge_prefix_sha(charges):
    values = [(c['response_id'], str(Decimal(str(c['cost_usd'])).normalize())) for c in charges]
    assert len(values) == len({key for key, _ in values}), 'Duplicate allocation-baseline receipt'
    return hashlib.sha256(json.dumps(sorted(values), separators=(',', ':')).encode()).hexdigest()


def suballocation_funded(ledger, total, remaining_liability):
    if 'active_suballocation' not in ledger:
        return True
    allocation = ledger['active_suballocation']
    assert allocation['scope'] == ledger['extension_phase']['scope']
    count = allocation['baseline_returned_ids']
    assert isinstance(count, int) and count >= 0 and len(ledger['charges']) >= count
    assert charge_prefix_sha(ledger['charges'][:count]) == allocation['baseline_charges_sha256'], 'Allocation baseline receipts changed'
    baseline = Decimal(allocation['baseline_cost_usd'])
    cap = Decimal(allocation['approved_new_usd'])
    assert baseline.is_finite() and baseline >= 0 and cap.is_finite() and cap > 0
    assert sum(Decimal(str(c['cost_usd'])) for c in ledger['charges'][:count]) == baseline
    assert total >= baseline and remaining_liability >= 0
    return total - baseline + remaining_liability < cap


@contextmanager
def study_accounting(out, keys, llm, scope, reserve, known_stop, name):
    """One serial reservation covers cumulative smoke/run receipts, including retries. -- PI/Sol"""
    assert scope == json.loads(llm.FUNDING_LEDGER.read_text())['funding_status']['scope'], 'Unapproved study funding scope'
    assert reserve.is_finite() and known_stop.is_finite() and 0 < known_stop < reserve
    ledger_path = llm.FUNDING_LEDGER
    # Cooperating maintained studies must not both acquire the single reservation.
    with ledger_path.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        baseline = start_accounting(out / 'billing_baseline.json', keys, llm.JEV_CACHE)
        assert set(baseline['files']) == {str(file) for key in keys for file in receipt_paths(llm.JEV_CACHE, key)}, 'Accounting cache changed'
        reservation = dict(name=name, max_additional_usd=str(reserve), scope=scope)
        assert json.loads(ledger_path.read_text())['funding_status']['new_paid_calls'] == 'approved', 'Paid calls not approved'

        def checkpoint():
            billing = collect_charges(baseline)
            replace_json(out / 'billing.json', billing)
            ledger = json.loads(ledger_path.read_text())
            phase = ledger['extension_phase']
            assert phase['scope'] == ledger['funding_status']['scope'] == scope
            # Revoking further calls must not prevent recording already returned charges.
            phase_baseline = Decimal(str(phase['baseline_cost_usd']))
            cap = Decimal(str(phase['approved_new_usd']))
            assert phase_baseline.is_finite() and phase_baseline >= 0 and cap.is_finite() and cap > 0
            charges = {}
            for charge in ledger['charges']:
                cost = Decimal(str(charge['cost_usd']))
                assert cost.is_finite() and cost >= 0
                if charge['response_id'] in charges:
                    assert Decimal(str(charges[charge['response_id']]['cost_usd'])) == cost, 'Conflicting central billing ID'
                charges[charge['response_id']] = charge
            for charge in billing['charges']:
                if charge['response_id'] in charges:
                    assert Decimal(str(charges[charge['response_id']]['cost_usd'])) == Decimal(charge['cost_usd']), 'Receipt cost differs from central ledger'
                else:
                    charges[charge['response_id']] = charge | dict(funding_scope=scope, status='rubric study; reading pending', evidence=str(out / 'billing.json'))
            total = sum((Decimal(str(c['cost_usd'])) for c in charges.values()), Decimal(0))
            # Only exact, ID-verified local receipts already counted above overlap
            # the total-run reserve. Historical cache costs create no headroom.
            overlap = sum((Decimal(c['cost_usd']) for c in billing['charges']), Decimal(0))
            assert ledger['reservations'] in ([], [reservation]), 'Another reservation is active or run budget changed'
            ledger['charges'] = list(charges.values())
            ledger['reported_task_cost_usd'] = float(total)
            remaining_liability = max(reserve - overlap, Decimal(0))
            phase_funded = total - phase_baseline + remaining_liability < cap
            allocation_funded = suballocation_funded(ledger, total, remaining_liability)
            funded = phase_funded and allocation_funded
            # Never manufacture a reservation when initial phase headroom is absent.
            # Already returned charges and an existing liability must still be retained.
            if funded:
                ledger['reservations'] = [reservation]
            if billing['charges'] or ledger['reservations']:
                replace_json(ledger_path, ledger)
            assert not billing['unresolved_billing'], 'Unresolved billing; reservation retained, do not resume paid calls'
            assert phase_funded, 'Phase charges plus remaining reservation exceed cap'
            assert allocation_funded, 'Suballocation charges plus remaining reservation exceed cap'
            return Decimal(billing['cost_usd'])

        checkpoint()
        token = ACTIVE_STUDY_LEDGER.set(str(ledger_path.resolve()))
        try:
            yield checkpoint
        finally:
            try:
                checkpoint()
            finally:
                ACTIVE_STUDY_LEDGER.reset(token)


def release_study_reservation(llm, name):
    """Only called after a complete successful full run, never on failure. -- PI/Sol"""
    with llm.FUNDING_LEDGER.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = json.loads(llm.FUNDING_LEDGER.read_text())
        assert len(ledger['reservations']) == 1 and ledger['reservations'][0]['name'] == name
        ledger['reservations'] = []
        replace_json(llm.FUNDING_LEDGER, ledger)


READ_FIELDS = {'original_state','scored_questions','diagnostic_own_input','all_moral_probabilities','all_diagnostic_probabilities','billing'}


def validate_smoke_gate(gate, packet_path, smoke_packets, out, request_keys):
    assert gate['status'] == 'complete_parent_read'
    assert gate['packet_sha256'] == hashlib.sha256(packet_path.read_bytes()).hexdigest()
    assert set(gate['read_fields']) == READ_FIELDS
    expected_ids = {p['id'] for p in smoke_packets}
    assert len(expected_ids) == len(smoke_packets) and expected_ids
    assert set(gate['responses']) == expected_ids
    for packet in smoke_packets:
        binding = gate['responses'][packet['id']]
        assert binding['request_key'] == request_keys[packet['id']]
        reply_path = out / (packet['id'] + '.json')
        assert binding['reply_sha256'] == hashlib.sha256(reply_path.read_bytes()).hexdigest()
        record = json.loads(reply_path.read_text())
        assert record['key'] == binding['request_key']
        assert hashlib.sha256(json.dumps(record['request'],ensure_ascii=False).encode()).hexdigest() == binding['request_key']
        assert record['served'] == 'typesafe/jev-1.14-20261014'
        assert record['judge_feedback']['status'] == 'recorded'
        assert record['request']['state'] == packet['request']['state']
        assert set(record['answers']) == set(packet['request']['questions'])
        for question in packet['request']['questions']:
            assert record['request']['questions'][question] == packet['request']['questions'][question]
