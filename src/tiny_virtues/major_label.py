"""Major-virtue production: missing cells, hybrid feedback and bounded native files. -- PI/Sol"""
import argparse
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import json
from pathlib import Path
import random
import threading
import uuid
from unittest.mock import patch

from tiny_virtues import case_flags, item_major as builder, item_major_run as runner
from tiny_virtues.case_input import INPUT_DIR
from tiny_virtues.judge_feedback import HYBRID_SHA
from tiny_virtues.major_cells import CellStore, cell_key, derive_contrary, story_hash
from tiny_virtues.rubric_accounting import paid_attempt_guard, release_study_reservation, replace_json, study_accounting

ROOT = builder.ROOT
PAIRS = ROOT / 'data/provenance/rubrics/named_contraries_20261022.json'
SHORT_EXAMPLES = ROOT / 'data/development/rubric_gym/major_short_examples.json'
SCOPE = 'jev_major_virtues_20261022'
LEDGER = ROOT / 'data/provenance/jev_major_virtues_20261022.json'
SLICE_SCOPE = 'rubric_nshot_loops_20261022'
SLICE_LEDGER = ROOT / 'data/provenance/rubric_nshot_budget_20261022.json'


def read(path):
    return json.loads(Path(path).read_text())


@contextmanager
def run_lock(out):
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'runner.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def selected_virtues(*, scope='major', virtue_ids=None):
    assert scope in ('major', 'all')
    all_major = builder.load_items(scope=scope)
    assert len(all_major) == (569 if scope == 'all' else 80)
    if virtue_ids is not None:
        selected = set(virtue_ids)
        assert selected and selected <= {v['item_id'] for v in all_major}, 'Unknown virtue selection'
        all_major = [v for v in all_major if v['item_id'] in selected]
    mapping = read(PAIRS)
    for binding in mapping['source_bindings']:
        assert runner.file_sha(ROOT / binding['path']) == binding['sha256'], 'Contrary source binding changed'
    assert len(mapping['pairs']) == 14 and len({p['vice_id'] for p in mapping['pairs']}) == 14
    known = {virtue['item_id']: virtue for virtue in all_major}
    contraries = {pair['virtue_id']: pair['vice_id'] for pair in mapping['pairs']
                 if pair['virtue_id'] in known and pair['vice_id'] in known}
    direct = [virtue for virtue in all_major if virtue['item_id'] not in contraries.values()]
    assert len(direct) + len(contraries) == len(known)
    excerpts = read(SHORT_EXAMPLES)
    versions = {}
    template = read(builder.FIVE_TEMPLATE)
    for virtue in direct:
        own = excerpts[virtue['item_id']] if virtue['tested_real_examples'] else {}
        teaching = builder.render_teaching(virtue['guide'], virtue['sources'], example_stories=own, format=builder.FIVE_LEVEL)
        versions[virtue['item_id']] = builder.digest(dict(model=builder.MODEL, served=builder.SERVED_MODEL,
            format=builder.FIVE_LEVEL, template={k: template[k] for k in ('forms', 'layout_scope', 'judging_task')},
            teaching=teaching, kind=virtue['kind'], list_name=virtue['list_name'],
            scope={k: virtue[k] for k in ('applicability', 'source_context') if k in virtue}))
        if virtue['item_id'] in contraries:
            versions[contraries[virtue['item_id']]] = builder.digest([versions[virtue['item_id']], runner.file_sha(PAIRS)])
    assert len(versions) == len(known)
    return direct, contraries, excerpts, versions


def input_rows(input_dir, held_ids=()):
    for stem in ('tinyvirtues', 'tinystoic'):
        with (input_dir / (stem + '.jsonl')).open() as file:
            for line in file:
                row = json.loads(line)
                if row['id'] in held_ids or not row.get('quote') or not row.get('person') or not row.get('target_act_quote'):
                    continue
                yield {k: row[k] for k in ('id', 'person', 'quote', 'target_act_quote', 'source_context', 'source_header') if k in row}


def stage_packets(store, out, virtue, contraries, excerpts, versions, batch_size):
    stage = out / 'stages' / uuid.uuid4().hex
    batch, full = [], []
    def save_batch():
        packet = builder.build_packet(virtue, batch,
            example_stories=excerpts[virtue['item_id']] if virtue['tested_real_examples'] else {},
            format=builder.FIVE_LEVEL, full_feedback_ids=full)
        packet['cell_bindings'] = dict(rubric_sha=versions[virtue['item_id']],
            story_shas={row['id']: story_hash(row) for row in batch}, full_feedback_ids=list(full),
            contrary=(dict(id=contraries[virtue['item_id']], rubric_sha=versions[contraries[virtue['item_id']]])
                      if virtue['item_id'] in contraries else None))
        path = stage / 'packets' / (packet['packet_key'] + '.json')
        runner.save(path, packet)
        store.register_request(packet['packet_key'], path, virtue['item_id'])
    for identity, input_sha, row in store.stories():
        if store.get(virtue['item_id'], identity, input_sha, versions[virtue['item_id']]) is not None:
            continue
        batch.append(row)
        if store.full_owner(identity, input_sha, virtue['item_id']):
            full.append(identity)
        if len(batch) == batch_size:
            save_batch()
            batch, full = [], []
    if batch:
        save_batch()


def prepare(out, *, input_dir=INPUT_DIR, slice_size=None, seed=20261022, batch_size=40, source_flags=None,
            virtue_scope='major', virtue_ids=None, funding_scope=SCOPE):
    assert batch_size in (20, 40)
    if (out / 'current.json').exists():
        previous = read(out / 'current.json')
        assert previous['scope'] == funding_scope, 'Use a separate output for a new funding scope'
        assert previous.get('virtue_scope', 'major') == virtue_scope
        assert previous.get('virtue_ids') == (sorted(virtue_ids) if virtue_ids is not None else None), 'Use a separate output for another virtue selection'
    held_ids, flag_bindings = set(), {}
    if source_flags is not None:
        flags_by_id, _, flag_bindings = case_flags.load_registry(source_flags)
        held_ids = {identity for identity, flags in flags_by_id.items() if flags['flag_held']}
    direct, contraries, excerpts, versions = selected_virtues(scope=virtue_scope, virtue_ids=virtue_ids)
    store = CellStore(out / 'cells.sqlite')
    try:
        assert next(store.pending_requests(), None) is None, 'Finish the existing frozen requests before preparing changed batches'
        bindings = {str(input_dir / (stem + '.jsonl')): runner.file_sha(input_dir / (stem + '.jsonl'))
                    for stem in ('tinyvirtues', 'tinystoic')}
        if slice_size is not None:
            ids = [row['id'] for row in input_rows(input_dir, held_ids)]
            chosen = set(random.Random(seed).sample(sorted(ids), slice_size))
            rows = (row for row in input_rows(input_dir, held_ids) if row['id'] in chosen)
        else:
            rows = input_rows(input_dir, held_ids)
        store.set_stories(rows)
        assert all(runner.file_sha(path) == digest for path, digest in (bindings | flag_bindings).items()), 'Source/flags changed during snapshot; prepare again before calling Jev'
        store.set_virtues(versions)
        for virtue in direct:
            stage_packets(store, out, virtue, contraries, excerpts, versions, batch_size)
        total = dict(requests=0, serialized_characters=0, direct_cells=0)
        for path in store.pending_requests():
            packet = read(path)
            total['requests'] += 1
            total['serialized_characters'] += len(json.dumps(packet['request'], ensure_ascii=False))
            total['direct_cells'] += len(packet['row_ids'])
        ratios = read(builder.FIVE_TEMPLATE)['pricing_observation']['token_character_ratios']
        total['estimated_usd'] = {key: total['serialized_characters'] * ratio * .042 / 1_000_000 for key, ratio in ratios.items()}
        manifest = dict(author='PI/Sol', scope=funding_scope, format=builder.FIVE_LEVEL, model=builder.MODEL,
            served_model=builder.SERVED_MODEL, feedback_sha=HYBRID_SHA, feedback='full once per story version; summaries elsewhere',
            source_files=bindings, source_flag_bindings=flag_bindings, excluded_held_ids=sorted(held_ids),
            versions=versions, batch_size=batch_size, seed=seed, slice_size=slice_size,
            coverage=store.coverage(), pending=total, contrary_sha256=runner.file_sha(PAIRS), excerpts_sha256=runner.file_sha(SHORT_EXAMPLES))
        if virtue_scope != 'major' or virtue_ids is not None or funding_scope != SCOPE:
            manifest.update(virtue_scope=virtue_scope, virtue_ids=sorted(virtue_ids) if virtue_ids is not None else None)
        runner.save(out / 'preparations' / (builder.digest(manifest) + '.json'), manifest)
        replace_json(out / 'current.json', manifest)
        return manifest
    finally:
        store.close()


def complete_result(store, stage, result):
    packet = read(stage / 'packets' / (result['packet_key'] + '.json'))
    binding = packet['cell_bindings']
    native_path = stage / 'replies' / (packet['packet_key'] + '.json')
    native = read(native_path)
    store.record_receipt(native['response']['id'], native['key'], native['cost'], native_path)
    cells, reviews = [], []
    native_sha = runner.file_sha(native_path)
    for score in result['scores']:
        identity = score['id']
        input_sha = binding['story_shas'][identity]
        value = score | dict(vice_presence=-score['signed'], input_sha256=input_sha,
            rubric_v2_sha256=binding['rubric_sha'], native_path=str(native_path), native_sha256=native_sha,
            request_key=native['key'], feedback_sha=native['feedback_sha'], feedback_status=native['judge_feedback']['status'])
        cells.append((packet['item_id'], identity, input_sha, binding['rubric_sha'], value))
        if binding['contrary'] is not None:
            contrary = derive_contrary(value, binding['contrary']['id']) | dict(input_sha256=input_sha,
                rubric_v2_sha256=binding['contrary']['rubric_sha'], native_path=str(native_path),
                native_sha256=native_sha, parent_cell_key=cell_key(packet['item_id'], identity, input_sha, binding['rubric_sha']))
            cells.append((contrary['item_id'], identity, input_sha, binding['contrary']['rubric_sha'], contrary))
        if identity in binding['full_feedback_ids']:
            reviews.append((identity, input_sha, packet['item_id']))
    store.commit_request(packet['packet_key'], cells, reviews, native_path)


def execute_stage(store, stage, llm, *, scope, reserve, stop, threads=4, cap=Decimal('29.90')):
    keys = [read(path)['identity']['request_key'] for path in store.stage_requests(stage)]
    assert keys and len(keys) == len(set(keys))
    llm.JEV_CACHE = stage / 'cache'
    name = 'major-' + stage.name
    funding_path = stage / 'funding.json'
    if funding_path.exists():
        funded = read(funding_path)
        assert funded['scope'] == scope and funded['ledger'] == str(llm.FUNDING_LEDGER)
        reserve, stop = Decimal(funded['reserve_usd']), Decimal(funded['stop_usd'])
    else:
        runner.save(funding_path, dict(scope=scope, ledger=str(llm.FUNDING_LEDGER), reserve_usd=str(reserve), stop_usd=str(stop)))
    lock = threading.RLock()
    with study_accounting(stage, keys, llm, scope, reserve, stop, name) as checkpoint:
        def checked():
            with lock:
                return checkpoint()
        maximum = max(builder.estimate([read(path)])['estimated_usd']['high'] for path in store.stage_requests(stage))
        allowance = Decimal(str(maximum * 2 * threads))
        with paid_attempt_guard(llm, checked, stop, allowance, reserve):
            guarded = llm.require_paid_approval
            def approve(funding_scope, **context):
                with lock:
                    assert store.total_cost() < cap, 'Run cap reached'
                    return guarded(funding_scope, **context)
            with patch.object(llm, 'require_paid_approval', new=approve):
                packets = (read(path) for path in store.stage_requests(stage, pending=True))
                runner.execute_packets(packets, stage, llm, threads=threads, funding_scope=scope,
                                       on_result=lambda result: complete_result(store, stage, result))
    release_study_reservation(llm, name)
    print(json.dumps(dict(sqlite_checkpoint=dict(stage=stage.name, **store.checkpoint()))), flush=True)


def recover_completed_reservation(store, out, llm, scope):
    ledger = read(llm.FUNDING_LEDGER)
    for reservation in ledger['reservations']:
        assert reservation['scope'] == scope and reservation['name'].startswith('major-'), 'Unrelated reservation remains active'
        stage = out / 'stages' / reservation['name'][len('major-'):]
        assert next(store.stage_requests(stage), None) is not None, 'Reserved stage is absent from this run'
        if next(store.stage_requests(stage, pending=True), None) is None:
            funding = read(stage / 'funding.json')
            execute_stage(store, stage, llm, scope=scope, reserve=Decimal(funding['reserve_usd']),
                          stop=Decimal(funding['stop_usd']))


def initialize_scope(path, scope, cap, authority):
    assert scope not in (SCOPE, SLICE_SCOPE) and Decimal(str(cap)) > 0
    path = Path(path)
    assert path.resolve() not in (LEDGER.resolve(), SLICE_LEDGER.resolve())
    if path.exists():
        existing = read(path)
        assert existing['funding_status']['scope'] == scope and Decimal(str(existing['approved_usd'])) == Decimal(str(cap))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_json(path, dict(author='PI/OpenAI', authority=authority, approved_usd=str(cap),
        reported_task_cost_usd=0, charges=[], reservations=[], unresolved_billing=[],
        funding_status=dict(scope=scope, new_paid_calls='approved', reason=authority,
            allowed_requests=[dict(model=builder.MODEL, endpoint='decisions')]),
        extension_phase=dict(scope=scope, baseline_cost_usd='0', approved_new_usd=str(cap))))


def execute(out, *, slice_mode=False, threads=4, funding_scope=None, funding_ledger=None, cap=None):
    from tiny_virtues import llm
    store = CellStore(out / 'cells.sqlite')
    custom = funding_scope is not None
    assert custom == (funding_ledger is not None) == (cap is not None), 'Custom runs need scope, ledger and cap'
    scope, ledger = (SLICE_SCOPE, SLICE_LEDGER) if slice_mode else (SCOPE, LEDGER)
    if custom:
        assert not slice_mode and funding_scope not in (SCOPE, SLICE_SCOPE)
        assert Path(funding_ledger).resolve() not in (LEDGER.resolve(), SLICE_LEDGER.resolve())
        scope, ledger = funding_scope, Path(funding_ledger)
    cap = Decimal(str(cap)) if custom else Decimal('4.95' if slice_mode else '29.99')
    assert cap > 0
    llm.FUNDING_LEDGER = ledger
    try:
        manifest = read(out / 'current.json')
        assert manifest['format'] == builder.FIVE_LEVEL and manifest['feedback_sha'] == HYBRID_SHA
        if 'source_flag_bindings' in manifest:
            assert all(runner.file_sha(path) == digest for path,digest in manifest['source_flag_bindings'].items()), 'Held flags changed; prepare the cohort again before calling Jev'
        assert manifest['pending']['estimated_usd']['high'] < (float(cap) if custom else 5 if slice_mode else 30), 'Priced run exceeds approved cap'
        if custom:
            assert manifest['scope'] == scope == read(ledger)['funding_status']['scope']
            assert Decimal(str(read(ledger)['approved_usd'])) == cap
        if not slice_mode and not custom:
            approval = read(out / 'slice_pass.json')
            assert approval['status'] == 'parent_read_measured_slice_under_30' and approval['projected_usd'] < 30
            assert approval['feedback_sha'] == HYBRID_SHA and approval['versions'] == manifest['versions']
        recover_completed_reservation(store, out, llm, scope)
        initial = store.coverage()
        fraction = initial['completed_cells'] / initial['expected_cells']
        thresholds = iter(value for value in (.25, .5, 1.0) if value > fraction)
        threshold = next(thresholds, 2)
        for stage in store.pending_stages():
            reserve = (cap - store.total_cost()) * (Decimal('.99') if custom else 1)
            stop = reserve * Decimal('.9') if custom else reserve - Decimal('.10')
            assert 0 < stop < reserve
            execute_stage(store, stage, llm, scope=scope, reserve=reserve, stop=stop, threads=threads,
                          cap=cap if custom else Decimal('29.90'))
            coverage = store.coverage()
            fraction = coverage['completed_cells'] / coverage['expected_cells']
            if fraction >= threshold:
                print(json.dumps(dict(milestone=round(threshold * 100), coverage=coverage, returned_cost_usd=str(store.total_cost()))), flush=True)
                threshold = next(thresholds, 2)
        coverage = store.coverage()
        assert coverage['completed_cells'] == coverage['expected_cells']
        assert coverage['full_feedback_stories'] == coverage['stories'], 'Full-copy feedback coverage incomplete'
        result = dict(status='labels_complete_feedback_read_pending', coverage=coverage, cost_usd=str(store.total_cost()))
        replace_json(out / 'progress.json', result)
        print(json.dumps(result), flush=True)
        return result
    finally:
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['estimate', 'slice', 'run', 'status'])
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--input-dir', type=Path, default=INPUT_DIR)
    parser.add_argument('--source-flags', type=Path, default=case_flags.DEFAULT_REGISTRY,
                        help='Final source-owner registry; held stories are not labelled')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--limit', type=int, help='Offline estimate only: seeded story count')
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--seed', type=int, default=20261022)
    args = parser.parse_args(argv)
    out = args.output.resolve()
    with run_lock(out):
        if args.action == 'status':
            store = CellStore(out / 'cells.sqlite')
            try:
                print(json.dumps(dict(coverage=store.coverage(), returned_cost_usd=str(store.total_cost())), indent=2))
            finally:
                store.close()
        elif args.action == 'estimate':
            assert not args.allow_paid
            print(json.dumps(prepare(out, input_dir=args.input_dir, slice_size=args.limit, seed=args.seed,
                                     source_flags=args.source_flags), indent=2))
        else:
            assert args.allow_paid and args.limit is None
            if not (out / 'current.json').exists():
                prepare(out, input_dir=args.input_dir, slice_size=40 if args.action == 'slice' else None,
                        seed=args.seed, batch_size=20 if args.action == 'slice' else 40, source_flags=args.source_flags)
            elif args.action == 'run':
                store = CellStore(out / 'cells.sqlite')
                try:
                    pending = next(store.pending_requests(), None) is not None
                finally:
                    store.close()
                if not pending:
                    prepare(out, input_dir=args.input_dir, seed=args.seed, source_flags=args.source_flags)
            execute(out, slice_mode=args.action == 'slice', threads=args.threads)


if __name__ == '__main__':
    main()
