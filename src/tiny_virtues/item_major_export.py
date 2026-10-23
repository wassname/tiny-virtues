"""No-call export: preserve base rows and add source-matched item labels. -- PI/Sol"""
import copy
import json
from pathlib import Path

from tiny_virtues import item_major as production
from tiny_virtues import item_major_run as runner
from tiny_virtues.judge_feedback import FEEDBACK_SHA, validate_choice


def read(path):
    return json.loads(Path(path).read_text())


def merge(source_rows, base_rows, run_dir):
    """Add a complete, source-matched item overlay without changing old labels. -- PI/Sol"""
    out = Path(run_dir)
    contract, preflight = read(out / 'contract.json'), read(out / 'preflight.json')
    assert contract['format'] == production.FIVE_LEVEL, 'This export requires explicit five-level preparation'
    assert production.digest(contract) == preflight['contract_sha256'], 'Mismatched contract'
    items = production.load_items()
    assert runner.production_contract(items, batch_size=contract['batch_size'],
        example_stories=contract['example_stories'], format=contract['format'],
        view=contract['criterion_view']) == contract, 'Live production contract changed'
    assert preflight['item_ids'] == [i['item_id'] for i in items]
    assert preflight['source_bindings'], 'Missing source file bindings'
    for binding in preflight['source_bindings']:
        assert runner.file_sha(binding['path']) == binding['sha256'], 'Bound source file changed'
    assert runner.file_sha(out / 'packets.jsonl') == preflight['packets_sha256'], 'Prepared packets changed'
    source = {r['id']: r for r in source_rows}
    base = {r['id']: r for r in base_rows}
    assert len(source) == len(source_rows) and len(base) == len(base_rows), 'Duplicate source/base ID'
    assert set(base) == set(source), 'Base and canonical source coverage differs'
    for identity, row in source.items():
        # Only label fields may differ from the canonical source row. -- PI/Sol
        assert all(base[identity].get(k) == v for k, v in row.items() if k not in ('labels', 'p', 'labeller')), \
            f'Base source row/actor/selected act changed: {identity}'
        if any(base[identity].get(k) != row.get(k) for k in ('labels', 'p', 'labeller')):
            legacy = base[identity]['legacy_labels']
            assert all(legacy[k] == row[k] for k in ('labels', 'p', 'labeller')), 'Legacy labels changed'
        assert 'item_major_export' not in base[identity], 'Do not stack item-major exports'
    by_item = {i['item_id']: i for i in items}
    overlays, seen, selected_rows, packets = {}, set(), {}, []
    for packet in runner.packet_rows(out):
        packets.append(packet)
        rows = packet['source_rows']
        for row in rows:
            assert row == source[row['id']], f'Stale source row/actor/selected act: {row["id"]}'
            selected_rows[row['id']] = row
        item = by_item[packet['item_id']]
        expected = production.build_packet(item, rows, example_stories=runner.own_examples(item, contract['example_stories']),
                                           format=contract['format'], view=contract['criterion_view'])
        assert packet == expected, 'Packet input/request/teaching binding differs'
        key = packet['packet_key']
        label_path, native_path = out / 'labels' / (key + '.json'), out / 'replies' / (key + '.json')
        label, native = read(label_path), read(native_path)
        assert native['request'] == packet['request'] and native['key'] == packet['identity']['request_key']
        assert native['served'] == native['response']['model'] == production.SERVED_MODEL
        assert native['request']['provider'] == dict(only=['typesafe'], allow_fallbacks=False)
        assert native['response']['provider'] == 'TypeSafe'
        feedback = native['judge_feedback']
        assert native['feedback_sha'] == FEEDBACK_SHA and feedback['question_sha'] == FEEDBACK_SHA
        assert feedback['status'] == 'recorded', 'Missing diagnostic feedback'
        criteria = packet['request']['questions']['debug_word']['criteria']
        validate_choice(feedback['answer'], criteria)
        assert native['response']['answers']['debug_word'] == feedback['answer']
        assert {k: v for k, v in native['response']['answers'].items() if k != 'debug_word'} == native['answers']
        scores = production.parse_answers(packet, native['answers'])
        assert label['identity'] == packet['identity'] and label['packet_key'] == key
        assert label['item_id'] == packet['item_id'] and label['status'] == packet['status']
        assert label['tested_real_examples'] == packet['tested_real_examples']
        assert label['request_key'] == native['key'] and label['served'] == native['served']
        assert label['scores'] == scores and label['judge_feedback'] == feedback and label['feedback_sha'] == FEEDBACK_SHA
        for score in scores:
            cell = (score['id'], score['item_id'])
            assert cell not in seen, 'Duplicate item-cell'
            seen.add(cell)
            overlays.setdefault(score['id'], {})[score['item_id']] = dict(
                score=copy.deepcopy(score), label_status='current_selected_item', labeller=production.MODEL,
                served=native['served'], identity=copy.deepcopy(packet['identity']), packet_key=key,
                contract_sha256=preflight['contract_sha256'], request_key=native['key'],
                source_row_sha256=production.digest(source[score['id']]), teaching_status=copy.deepcopy(packet['status']),
                tested_real_examples=packet['tested_real_examples'], judge_feedback=copy.deepcopy(feedback),
                feedback_sha=FEEDBACK_SHA, native_reply_sha256=runner.file_sha(native_path),
                label_sha256=runner.file_sha(label_path))
    # Request identity includes order and chunk composition. -- PI/Sol
    selected = [r for r in source_rows if r['id'] in selected_rows]
    assert production.digest(selected) == preflight['rows_sha256'] and len(selected) == preflight['cases'], 'Stale selected inputs'
    assert [p['identity']['request_key'] for p in packets] == preflight['request_keys']
    assert [p['packet_key'] for p in packets] == preflight['packet_keys']
    assert seen == {(r['id'], i['item_id']) for r in selected for i in items}, 'Missing item-cell'
    assert {p.stem for p in (out / 'labels').glob('*.json')} == set(preflight['packet_keys']), 'Missing or extra packet labels'
    result = []
    for row in base_rows:
        previous = {f'{school}|{listing}|{name}': dict(label_status='old_or_stale_not_rescored', value=copy.deepcopy(value))
                    for school, lists in row.get('virtue_lists', {}).items()
                    for listing, cells in lists.items() for name, value in cells.items()
                    if f'{school}|{listing}|{name}' not in overlays.get(row['id'], {})}
        overlay = dict(schema='item-major-mixed-export-v1', overall_label_status='old_or_stale_not_rescored',
                       legacy_label_status='old_untouched', source_row_sha256=production.digest(source[row['id']]),
                       selected_item_status='complete' if row['id'] in overlays else 'not_selected',
                       item_labels=previous | overlays.get(row['id'], {}),
                       conditional='Not a production approval: requires wassname choosing A and a matching passing independence pilot')
        result.append(copy.deepcopy(row) | dict(item_major_export=overlay))
    return result


def export(source_rows, base_rows, run_dir, output):
    """Validate before creating a new JSONL output; never overwrite a dataset. -- PI/Sol"""
    destination = Path(output)
    assert not destination.exists(), 'Use a new mixed-export output path'
    rows = merge(source_rows, base_rows, run_dir)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x') as file:
        for row in rows:
            file.write(json.dumps(row, ensure_ascii=False) + '\n')
    return dict(rows=len(rows), output=str(destination), sha256=runner.file_sha(destination),
                selected_item_cells=sum(v['label_status'] == 'current_selected_item' for r in rows
                                        for v in r['item_major_export']['item_labels'].values()))
