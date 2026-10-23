"""Bounded production preparation, pilot gate and existing receipt accounting. -- PI/Sol"""
import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, as_completed, wait
from contextvars import copy_context
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import tempfile
import threading
from unittest.mock import patch

from tiny_virtues.case_input import INPUT_DIR
from tiny_virtues import item_major as production
from tiny_virtues.judge_feedback import FEEDBACK_SHA, validate_choice
from tiny_virtues.judge_traces import decision_trace
from tiny_virtues.rubric_accounting import (paid_attempt_guard, receipt_paths, release_study_reservation,
                                           study_accounting)


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
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
    try:
        if path.exists():
            assert file_sha(path) == file_sha(temporary), f'Immutable artifact changed: {path}'
        else:
            os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def production_contract(items, *, batch_size=40, example_stories=None, format=production.FORMAT, view='forward'):
    """Pilot binds the rendered teaching, all selected IDs, question implementation and chunk size. -- PI/Sol"""
    assert 20 <= batch_size <= 40
    assert format in (production.FORMAT, production.FIVE_LEVEL)
    assert view in ('forward', 'reverse') and (format == production.FIVE_LEVEL or view == 'forward')
    return dict(layout=production.LAYOUT, format=format, criterion_view=view, model=production.MODEL,
                projection=production.FIVE_PROJECTION if format == production.FIVE_LEVEL else production.PROJECTION,
                five_level_template_sha256=file_sha(production.FIVE_TEMPLATE) if format == production.FIVE_LEVEL else None,
                served_model=production.SERVED_MODEL, feedback_sha=FEEDBACK_SHA, batch_size=batch_size,
                example_stories=example_stories or {},
                builder_sha256=hashlib.sha256(Path(production.__file__).read_bytes()).hexdigest(),
                renderer_sha256=hashlib.sha256((Path(production.__file__).parent / 'student_teaching.py').read_bytes()).hexdigest(),
                items=[dict(item_id=i['item_id'], status=i['status'], primary=i['primary'],
                            tested_real_examples=i['tested_real_examples'],
                            teaching=production.render_teaching(i['guide'], i['sources'], example_stories=own_examples(i, example_stories), format=format),
                            binding=i['binding'], rubric_sha256=i['rubric_sha256']) for i in items])


def own_examples(item, overrides):
    return {} if overrides is None or overrides == {} else overrides[item['item_id']]


def validate_pilot_gate(path, contract_sha256):
    gate = json.loads(Path(path).read_text())
    assert gate['status'] == 'passing_production_format_pilot', 'Full run blocked: production pilot has not passed'
    assert gate['contract_sha256'] == contract_sha256, 'Pilot does not match production teaching/model/layout/questions'
    assert gate['checks'] == dict.fromkeys(('all_13_groups', 'story_independence', 'story_order', 'same_pattern', 'feedback_read'), True)
    assert gate['evidence'], 'Pilot requires bound evidence, not approval text'
    for binding in gate['evidence']:
        evidence = Path(binding['path'])
        if not evidence.is_absolute():
            evidence = Path(path).parent / evidence
        assert hashlib.sha256(evidence.read_bytes()).hexdigest() == binding['sha256'], 'Pilot evidence changed'
    return gate


def funding_template():
    """A blocked, separate $30 production ledger; parent alone authorizes it after pilot. -- PI/Sol"""
    return dict(author='PI/Sol', funding_status=dict(scope=production.FUNDING_SCOPE, new_paid_calls='blocked',
                reason='Parent must independently record a passing production-format pilot before funding',
                allowed_requests=[dict(model=production.MODEL, endpoint='decisions')]),
                extension_phase=dict(scope=production.FUNDING_SCOPE, baseline_cost_usd='0', approved_new_usd='30'),
                charges=[], reservations=[], reported_task_cost_usd=0)


def iter_packets(items, rows, *, batch_size=40, example_stories=None, format=production.FORMAT, view='forward'):
    assert 20 <= batch_size <= 40
    assert rows and len({r['id'] for r in rows}) == len(rows)
    for item in items:
        for start in range(0, len(rows), batch_size):
            yield production.build_packet(item, rows[start:start + batch_size], example_stories=own_examples(item, example_stories), format=format, view=view)


def prepare(items, rows, out, *, batch_size=40, example_stories=None, source_bindings=None, format=production.FORMAT, view='forward'):
    """Stream immutable actual requests; preparation cannot import or call paid transport. -- PI/Sol"""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    if example_stories is not None:
        assert set(example_stories) == {i['item_id'] for i in items}, 'Supply explicit own-item excerpt mappings for the selected scope'
        for item in items:
            known = {e['case_id'] for e in item['guide']['examples']} if item['tested_real_examples'] else set()
            assert set(example_stories[item['item_id']]) <= known, item['item_id']
    contract = production_contract(items, batch_size=batch_size, example_stories=example_stories, format=format, view=view)
    save(out / 'contract.json', contract)
    manifest = dict(source_bindings=source_bindings or [], contract_sha256=production.digest(contract), rows_sha256=production.digest(rows),
                    item_ids=[i['item_id'] for i in items], cases=len(rows), main_items=sum(i['primary'] for i in items),
                    tested_items=sum(i['tested_real_examples'] for i in items),
                    untested_main_items=[i['item_id'] for i in items if i['primary'] and not i['tested_real_examples']])
    save(out / 'manifest.json', manifest)
    keys, packet_keys = [], []
    totals = dict(serialized_characters=0, requests=0, cells=0)
    path = out / 'packets.jsonl'
    temporary = out / 'packets.preparing.jsonl'
    with temporary.open('w') as file:
        for packet in iter_packets(items, rows, batch_size=batch_size, example_stories=example_stories, format=format, view=view):
            file.write(json.dumps(packet, ensure_ascii=False) + '\n')
            keys.append(packet['identity']['request_key'])
            packet_keys.append(packet['packet_key'])
            price = production.estimate([packet])
            for field in totals:
                totals[field] += price[field]
    assert len(set(keys)) == len(keys), 'Duplicate requests'
    if path.exists():
        assert file_sha(path) == file_sha(temporary), 'Prepared packets changed'
        temporary.unlink()
    else:
        temporary.rename(path)
    price = production.estimate([], format=format) | totals
    price['estimated_usd'] = {k: totals['serialized_characters'] * ratio * 0.042 / 1_000_000 for k, ratio in price['token_character_ratios'].items()}
    summary = manifest | dict(estimate=price, request_keys=keys, packet_keys=packet_keys,
                             packets_sha256=file_sha(path))
    save(out / 'preflight.json', summary)
    return summary


def file_sha(path):
    sha = hashlib.sha256()
    with Path(path).open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            sha.update(chunk)
    return sha.hexdigest()


def packet_rows(out):
    with (Path(out) / 'packets.jsonl').open() as file:
        for line in file:
            yield json.loads(line)


def run_packet(packet, out, llm, *, funding_scope=production.FUNDING_SCOPE):
    """Use the existing Jev adapter; native reply and raw Noul values remain durable. -- PI/Sol"""
    key = packet['identity']['request_key']
    assert packet['request']['model'] == production.MODEL
    assert key == llm.jev_key(packet['request'])
    assert packet['packet_key'] == production.digest(packet['identity'])
    scored = {k: v for k, v in packet['request']['questions'].items() if k != 'debug_word'}
    feedback_options = ({'full_feedback_questions': packet['full_feedback_questions']}
                        if 'full_feedback_questions' in packet else {})
    assert llm.jev_request(packet['request']['state'], scored, model=production.MODEL, **feedback_options) == packet['request']
    reply = Path(out) / 'replies' / (packet['packet_key'] + '.json')
    attempt = Path(out) / 'attempts' / (packet['packet_key'] + '.json')
    cache = llm.JEV_CACHE / key[:2] / (key + '.json.gz')
    if reply.exists():
        native = json.loads(reply.read_text())
    else:
        if not cache.exists():
            assert not attempt.exists(), f'Interrupted attempt has no durable reply: {attempt}; reconcile before another HTTP call'
            assert not any(p.exists() and p.stat().st_size for p in receipt_paths(llm.JEV_CACHE, key)), 'Receipt without cached reply; recover it before another HTTP call'
            save(attempt, dict(request_key=key, packet_key=packet['packet_key']))
        native = llm.jev(packet['request']['state'], scored, model=production.MODEL, funding_scope=funding_scope, **feedback_options)
    assert native['key'] == key and native['request'] == packet['request']
    stable = {k: v for k, v in native.items() if k != 'cache_hit'}
    save(reply, stable)
    attempt.unlink(missing_ok=True)
    trace = decision_trace(Path(out), dict(id=packet['packet_key'], request=packet['request']), stable)
    assert native['served'] == production.SERVED_MODEL, 'Served model mismatch; no fallback is permitted'
    assert native['feedback_sha'] == packet['identity']['feedback_sha']
    feedback = native['judge_feedback']
    assert feedback['status'] == 'recorded', 'New production requires saved diagnostic feedback'
    validate_choice(feedback['answer'], packet['request']['questions']['debug_word']['criteria'])
    scores = production.parse_answers(packet, native['answers'])
    result = dict(packet_key=packet['packet_key'], identity=packet['identity'], item_id=packet['item_id'],
                  status=packet['status'], tested_real_examples=packet['tested_real_examples'],
                  scores=scores, judge_feedback=feedback, feedback_sha=packet['identity']['feedback_sha'],
                  request_key=key, served=native['served'], trace=trace,
                  receipt_paths=[str(p) for p in receipt_paths(llm.JEV_CACHE, key)])
    save(Path(out) / 'labels' / (packet['packet_key'] + '.json'), result)
    return result


def execute_packets(packets, out, llm, *, threads=4, funding_scope=production.FUNDING_SCOPE, on_result=None):
    """Keep at most four requests active; results remain in per-request files. -- PI/Sol"""
    assert 1 <= threads <= 4, 'Use 1..4 concurrent Jev requests'
    with ThreadPoolExecutor(threads) as executor:
        pending = set()
        for packet in packets:
            pending.add(executor.submit(copy_context().run, run_packet, packet, out, llm, funding_scope=funding_scope))
            if len(pending) == threads:
                done, pending = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    result = future.result()
                    if on_result is not None:
                        on_result(result)
        for future in as_completed(pending):
            result = future.result()
            if on_result is not None:
                on_result(result)


def run(out, *, allow_paid=False, pilot_gate=None, ledger=production.FUNDING_LEDGER,
        reserve_usd=None, cost_stop_usd=None, threads=4, format=production.FORMAT, view='forward'):
    assert allow_paid and pilot_gate, 'Full run requires --allow-paid and a passing --pilot-gate'
    out = Path(out).resolve()
    preflight = json.loads((out / 'preflight.json').read_text())
    contract = json.loads((out / 'contract.json').read_text())
    assert production.digest(contract) == preflight['contract_sha256']
    assert contract['format'] == format and contract['criterion_view'] == view, 'Explicit run format/view differs from preparation'
    current = production_contract(production.load_items(), batch_size=contract['batch_size'], example_stories=contract['example_stories'], format=format, view=view)
    assert current == contract, 'Live production inputs/code changed; prepare and pilot the new contract'
    validate_pilot_gate(pilot_gate, preflight['contract_sha256'])
    assert preflight['source_bindings'], 'Full run requires live source file bindings'
    for binding in preflight['source_bindings']:
        assert file_sha(binding['path']) == binding['sha256'], 'Production source inputs changed; prepare again'
    assert file_sha(out / 'packets.jsonl') == preflight['packets_sha256']
    assert preflight['estimate']['estimated_usd']['high'] < 30, 'Estimated production exceeds approved $30 cap'
    assert 1 <= threads <= 4, 'Use 1..4 concurrent Jev requests'
    reserve, stop = Decimal(str(reserve_usd)), Decimal(str(cost_stop_usd))
    assert 0 < stop < reserve < Decimal('30')
    funding = json.loads(Path(ledger).read_text())
    assert funding['funding_status']['scope'] == production.FUNDING_SCOPE
    assert Decimal(str(funding['extension_phase']['baseline_cost_usd'])) == 0
    assert 0 < Decimal(str(funding['extension_phase']['approved_new_usd'])) <= 30
    assert Path(ledger).resolve() != production.ROOT / 'data/provenance/v5_relabel_budget_20261021.json'
    print(json.dumps(dict(coverage=preflight['cases'], items=len(preflight['item_ids']), estimate=preflight['estimate']), indent=2), flush=True)
    from tiny_virtues import llm
    llm.FUNDING_LEDGER = Path(ledger).resolve()
    llm.JEV_CACHE = out / 'cache'
    lock = threading.RLock()
    name = 'jev-item-major-production'
    with study_accounting(out, preflight['request_keys'], llm, production.FUNDING_SCOPE, reserve, stop, name) as checkpoint:
        def checked():
            with lock:
                return checkpoint()
        max_cost = max(production.estimate([p])['estimated_usd']['high'] for p in packet_rows(out))
        allowance = Decimal(str(max_cost * 2 * threads))
        with paid_attempt_guard(llm, checked, stop, allowance, reserve):
            guarded_approval = llm.require_paid_approval
            def serialized_approval(scope, **context):
                with lock:
                    return guarded_approval(scope, **context)
            with patch.object(llm, 'require_paid_approval', serialized_approval):
                execute_packets(packet_rows(out), out, llm, threads=threads)
    release_study_reservation(llm, name)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--item-major', action='store_true', required=True)
    parser.add_argument('--five-level', action='store_true', help='Conditional five-level format; requires its own matching passing pilot before run')
    parser.add_argument('--criterion-view', choices=['forward', 'reverse'], default='forward', help='Reverse is five-level only; raw answers are mapped back to favorable order')
    parser.add_argument('action', choices=['prepare', 'estimate', 'run'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input-dir', type=Path, default=INPUT_DIR)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--example-stories', type=Path, help='Source excerpts keyed by item ID, then case ID; empty mappings for source-only items')
    parser.add_argument('--stories-per-request', type=int, help='Preparation only; defaults to 40')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--pilot-gate', type=Path)
    parser.add_argument('--funding-ledger', type=Path, default=production.FUNDING_LEDGER)
    parser.add_argument('--write-funding-template', type=Path)
    parser.add_argument('--reserve-usd', type=Decimal)
    parser.add_argument('--cost-stop-usd', type=Decimal)
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args(argv)
    format = production.FIVE_LEVEL if args.five_level else production.FORMAT
    assert args.five_level or args.criterion_view == 'forward', '--criterion-view reverse requires --five-level'
    if args.action != 'run':
        assert not args.allow_paid and not args.pilot_gate and args.reserve_usd is None and args.cost_stop_usd is None
        if args.write_funding_template:
            save(args.write_funding_template, funding_template())
        rows = [json.loads(line) for stem in ('tinyvirtues', 'tinystoic')
                for line in (args.input_dir / (stem + '.jsonl')).read_text().splitlines() if line.strip()]
        rows = [r for r in rows if r.get('quote') and r.get('person') and r.get('target_act_quote')]
        if args.limit is not None:
            assert args.limit > 0
            rows = rows[:args.limit]
        bindings = [dict(path=str((args.input_dir / (stem + '.jsonl')).resolve()), sha256=file_sha(args.input_dir / (stem + '.jsonl'))) for stem in ('tinyvirtues', 'tinystoic')]
        examples = json.loads(args.example_stories.read_text()) if args.example_stories else None
        summary = prepare(production.load_items(), rows, args.output, batch_size=args.stories_per_request or 40, example_stories=examples, source_bindings=bindings, format=format, view=args.criterion_view)
        print(json.dumps({k: v for k, v in summary.items() if k not in ('request_keys', 'packet_keys')}, indent=2), flush=True)
    else:
        assert args.write_funding_template is None
        assert args.limit is None and args.example_stories is None and args.stories_per_request is None and args.input_dir == INPUT_DIR, 'Run uses frozen preparation; selection/teaching options require prepare first'
        run(args.output, allow_paid=args.allow_paid, pilot_gate=args.pilot_gate, ledger=args.funding_ledger,
            reserve_usd=args.reserve_usd, cost_stop_usd=args.cost_stop_usd, threads=args.threads, format=format, view=args.criterion_view)
