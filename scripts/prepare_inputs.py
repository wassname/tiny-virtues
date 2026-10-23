"""Build repaired quoted inputs without modifying frozen v2. -- PI/OpenAI"""
import argparse
import gzip
import hashlib
import json
from collections import Counter
from pathlib import Path

from loguru import logger
from tiny_virtues.case_input import INPUT_DIR, ROOT, check_target, judging_story, require_selected_target, scoped
from tiny_virtues.quote_check import editor_brackets, problems, span_problems
from tiny_virtues.source_extraction import apply_patch as restore_whole_case


def read_rows(path):
    data = path.read_bytes()
    text = gzip.decompress(data).decode() if path.suffix == '.gz' else data.decode()
    return [json.loads(line) for line in text.splitlines()]


def source_path(reference):
    path = Path(reference)
    if not path.is_absolute():
        return ROOT / path
    aliases = json.loads((ROOT / 'data/provenance/source_paths.json').read_text())
    entry = aliases[str(path)]
    source = ROOT / entry['path']
    assert hashlib.sha256(source.read_bytes()).hexdigest() == entry['sha256'], reference
    return source


def repaired(row, change):
    assert change['id'] == row['id']
    if 'kind' in change and change['kind'] == 'source_transcription':
        assert change['before_row_sha256'] == hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), row['id']
    assert change['before_quote'] == row['quote'], row['id']
    if row['quote'] is None:
        assert row['person'] is None and change['kind'] == 'recover_failed_quote', row['id']
        assert change['before_quote_sha256'] is None and change['before_person'] is None, row['id']
        assert change['before_row_sha256'] == hashlib.sha256(json.dumps(row, ensure_ascii=False, sort_keys=True).encode()).hexdigest(), row['id']
        assert change['person'] == change['reviewed_actor'] and change['person'], row['id']
        assert change['original_actor_proof']['id'] == row['id'].split(':')[-1], row['id']
        assert change['original_actor_proof']['act'] == row['act'], row['id']
    else:
        assert change['before_quote_sha256'] == hashlib.sha256(row['quote'].encode()).hexdigest(), row['id']
        assert change['before_person'] == row['person'], row['id']
        if change['person'] != row['person']:
            assert 'actor_spelling' in change and change['kind'] == 'source_transcription', row['id']
            spelling = change['actor_spelling']
            assert spelling['before'] and spelling['after'] and spelling['same_entity_reason'], row['id']
            assert spelling['before'] in row['person'] and spelling['before'] != spelling['after'], row['id']
            assert change['person'] == row['person'].replace(spelling['before'], spelling['after']), row['id']
            assert spelling['original_survey']['id'] == row['id'].split(':')[-1], row['id']
    spans = change['source_spans']
    for span in spans:
        path = source_path(span['path'])
        assert hashlib.sha256(path.read_bytes()).hexdigest() == span['source_sha256'], span['path']
        assert '\n'.join(path.read_text().splitlines()[span['line_start'] - 1:span['line_end']]) == span['original'], row['id']
    original = '\n...\n'.join(span['original'] for span in spans)
    assert not problems(change['quote'], original, change['fixed']), row['id']
    for context in change['source_context']:
        assert context['attribution'] and not problems(context['quote'], original, change['fixed']), row['id']
    acts = change['act_spans'] if 'act_spans' in change else row['act_spans']
    output = row | dict(quote=change['quote'], person=change['person'], fixed=change['fixed'],
                        original=original, original_is_composite=len(spans) > 1,
                        quote_sources=spans, source_context=change['source_context'], act_spans=acts,
                        source_review_status='repaired' if change['status'] == 'repair' else 'unresolved',
                        source_review_reason=change['reason'])
    source_view = {key: value for key, value in output.items() if key != 'target_act_quote'}
    assert not span_problems(judging_story(source_view), original, acts, [], False, change['fixed']), row['id']
    output |= dict(quote_words=len(output['quote'].split()), quote_share=len(output['quote'].split()) / len(original.split()),
                   editor_insertions=editor_brackets(output['quote'], original),
                   legacy_quote_validation=dict(quote_problems=row['quote_problems'], span_problems=row['span_problems']),
                   quote_problems=[], span_problems=[])
    if len(spans) == 1 or ('kind' in change and change['kind'] == 'source_transcription'):
        assert len({span['path'] for span in spans}) == 1, row['id']
        location = dict(source_file=row['source_file'], snippet_lines=row['snippet_lines'])
        history = row['legacy_source_reference'] if 'legacy_source_reference' in row else {}
        assert all(key not in history or history[key] == value for key, value in location.items()), row['id']
        output |= dict(legacy_source_reference=history | location,
                       source_file=spans[0]['path'],
                       snippet_lines=f'{min(s["line_start"] for s in spans)}-{max(s["line_end"] for s in spans)}')
    if 'act_spans' in change:
        output |= dict(legacy_act=row['act'], act=' ... '.join(acts))
    if 'source_verdict_scope' in change:
        output |= dict(source_verdict_scope=change['source_verdict_scope'], legacy_verdict_reading=row['verdict_reading'],
                       verdict_reading=change['source_verdict_scope'])
    if row['quote'] is None:
        output |= dict(failed_quote_recovery=dict(before_row_sha256=change['before_row_sha256'],
            original_actor_proof=change['original_actor_proof'], reviewed_actor=change['reviewed_actor'],
            source_layer_notes=change['recovery_context_notes']),
            target_act_quote=change['target_act_quote'])
        check_target(output)
    if 'target_act_quote' in change and row['quote'] is not None:
        assert change['kind'] == 'source_transcription', row['id']
        assert change['before_target_act_quote'] == row['target_act_quote'], row['id']
        output |= dict(target_act_quote=change['target_act_quote'])
        check_target(output)
    if change['person'] != row['person'] and row['quote'] is not None:
        spelling = change['actor_spelling']
        assert spelling['after'] in spelling['source_quote'], row['id']
        assert not problems(spelling['source_quote'], original, change['fixed']), row['id']
        output |= dict(legacy_person=row['person'], actor_spelling=spelling)
    if 'source_replacement' in change:
        output |= dict(source_replacement=change['source_replacement'], legacy_citation=row['citation'], citation=change['citation'])
    return output


def completed_target(row, change):
    assert not {'after_person', 'after_story', 'legacy_target_metadata'} & change.keys(), row['id']
    output = scoped(row, change)
    require_selected_target(output)
    assert all(output[key] == value for key, value in row.items()), row['id']
    assert set(output) - set(row) <= {'target_act_quote', 'target_scope_reason', 'target_scope_sha256'}, row['id']
    return output


def source_header(row, change, registry_sha256):
    assert change['id'] == row['id'], row['id']
    assert hashlib.sha256(row['quote'].encode()).hexdigest() == change['quote_sha256'], row['id']
    assert {key: row[key] for key in change['source_fields']} == change['source_fields'], row['id']
    assert hashlib.sha256(source_path(row['source_file']).read_bytes()).hexdigest() == change['source_sha256'], row['id']
    assert 'source_header' not in row and 'source_header_registry_sha256' not in row, row['id']
    output = row | dict(source_header=change['source_header'], source_header_registry_sha256=registry_sha256)
    judging_story(output)
    return output


def build(destination=INPUT_DIR, new_rows=None, *, case_ids=None):
    assert destination.resolve() != (ROOT / 'private/build/v2_quotes').resolve(), 'Frozen v2 inputs must not be overwritten'
    registry = ROOT / 'data/development/new_books/accepted_files.json'
    registry_used = new_rows is None
    if registry_used:
        new_rows = [ROOT / name for name in json.loads(registry.read_text())['files']]
    else:
        new_rows = [ROOT / path for path in new_rows]
    sources = [ROOT / f'private/build/v2_quotes/{name}.jsonl' for name in ('tinyvirtues', 'tinystoic')]
    patch_path = ROOT / 'data/provenance/case_repairs_20261018.jsonl'
    changes = read_rows(patch_path)
    assert len(changes) == len({change['id'] for change in changes}), 'Duplicate case repair'
    patches = {change['id']: change for change in changes}
    new_patch_path = ROOT / 'data/provenance/new_book_case_repairs_20261018.jsonl'
    new_changes = read_rows(new_patch_path)
    assert len(new_changes) == len({change['id'] for change in new_changes}), 'Duplicate new-case repair'
    new_patches = {change['id']: change for change in new_changes}
    applied_new_patches = set()
    datasets = {path.stem: read_rows(path) for path in sources}
    scope_path = ROOT / 'data/provenance/selected_act_scope_20261018.jsonl'
    scopes = read_rows(scope_path)
    assert len(scopes) == len({change['id'] for change in scopes}), 'Duplicate selected-act scope'
    scope_map = {change['id']: change for change in scopes}
    new_scope_path = ROOT / 'data/provenance/new_book_selected_act_scope_20261018.jsonl'
    new_scopes = read_rows(new_scope_path)
    assert len(new_scopes) == len({change['id'] for change in new_scopes}), 'Duplicate new-case selected-act scope'
    new_scope_map = {change['id']: change for change in new_scopes}
    review_path = ROOT / 'data/provenance/selected_act_review_20261018.json.gz'
    survey_proofs = json.loads(gzip.decompress(review_path.read_bytes()))['original_surveys']
    assert new_scope_map.keys() <= survey_proofs.keys(), 'New scope lacks its original survey proof'
    for change in scopes + new_scopes:
        if change['before_person'] != change['after_person']:
            proof = survey_proofs[change['id']]
            assert change['original_survey'] == proof['record'], change['id']
            assert change['original_survey_reference'] == {k: v for k, v in proof.items() if k != 'record'}, change['id']
    ids = {row['id'] for rows in datasets.values() for row in rows}
    assert scope_map.keys() <= ids, 'Unknown frozen case in selected-act scope repairs'
    assert len(ids) == sum(map(len, datasets.values())), 'Duplicate frozen case ID'
    assert patches.keys() <= ids, 'Repair refers to an unknown frozen case'
    if case_ids is not None:
        assert set(case_ids) and set(case_ids) <= ids, 'Tiny preparation requires known frozen case IDs'
        datasets = {name: [row for row in rows if row['id'] in case_ids] for name, rows in datasets.items()}
    for name, rows in datasets.items():
        datasets[name] = [repaired(row, patches[row['id']]) if row['id'] in patches else row for row in rows]
        datasets[name] = [scoped(row, scope_map[row['id']]) if row['id'] in scope_map else row for row in datasets[name]]
    added = 0
    for path in new_rows:
        for row in read_rows(path):
            assert row['id'] not in ids, row['id']
            assert row['quote'] and row['person'] and row['quote_sources'], row['id']
            for span in row['quote_sources']:
                source = source_path(span['path'])
                assert hashlib.sha256(source.read_bytes()).hexdigest() == span['source_sha256'], row['id']
                assert '\n'.join(source.read_text().splitlines()[span['line_start'] - 1:span['line_end']]) == span['original'], row['id']
            assert row['original'] == '\n...\n'.join(span['original'] for span in row['quote_sources']), row['id']
            assert not problems(row['quote'], row['original'], row['fixed']), row['id']
            for context in row['source_context']:
                assert context['attribution'] and not problems(context['quote'], row['original'], row['fixed']), row['id']
            assert not span_problems(row['quote'], row['original'], row['act_spans'], [], False, row['fixed']), row['id']
            if row['id'] in new_patches:
                row = repaired(row, new_patches[row['id']])
                applied_new_patches.add(row['id'])
            if row['id'] in new_scope_map:
                row = scoped(row, new_scope_map[row['id']])
            check_target(row)
            assert row['merge_status'] == 'accepted', 'New source-stage records need an explicit merge disposition'
            for key in ('labels', 'p', 'labeller'):
                assert row[key] is None, 'New cases must not import legacy moral labels'
            datasets['tinyvirtues'].append(row)
            ids.add(row['id'])
            added += 1
    if registry_used:
        assert new_patches.keys() == applied_new_patches, 'Repair refers to an unregistered new case'
    completed_target_path = ROOT / 'data/provenance/validated_unchanged_targets_20261021.jsonl'
    completed_targets = read_rows(completed_target_path)
    assert len(completed_targets) == len({c['id'] for c in completed_targets}), 'Duplicate checked target completion'
    completed_target_map = {c['id']: c for c in completed_targets}
    if registry_used:
        assert completed_target_map.keys() <= ids, 'Checked target completion refers to an unregistered case'
    for name, rows in datasets.items():
        datasets[name] = [completed_target(row, completed_target_map[row['id']]) if row['id'] in completed_target_map else row for row in rows]
    whole_case_path = ROOT / 'data/provenance/whole_case_repairs_20261021.jsonl'
    whole_case_changes = read_rows(whole_case_path)
    assert len(whole_case_changes) == len({c['id'] for c in whole_case_changes}), 'Duplicate whole-case repair'
    whole_case_map = {c['id']:c for c in whole_case_changes}
    if registry_used:
        assert whole_case_map.keys() <= ids, 'Whole-case repair refers to an unregistered case'
    for name, rows in datasets.items():
        datasets[name] = [restore_whole_case(row,whole_case_map[row['id']]) if row['id'] in whole_case_map else row for row in rows]
    header_path = ROOT / 'data/provenance/source_headers_20261022.json'
    header_bytes = header_path.read_bytes()
    headers = json.loads(header_bytes)['entries']
    assert len(headers) == len({header['id'] for header in headers}), 'Duplicate source header'
    header_map = {header['id']: header for header in headers}
    if registry_used:
        assert header_map.keys() <= ids, 'Source header refers to an unregistered case'
    header_sha256 = hashlib.sha256(header_bytes).hexdigest()
    for name, rows in datasets.items():
        datasets[name] = [source_header(row, header_map[row['id']], header_sha256) if row['id'] in header_map else row for row in rows]
    destination.mkdir(parents=True, exist_ok=True)
    outputs = {}
    for name, rows in datasets.items():
        path = destination / f'{name}.jsonl'
        temporary = path.with_suffix('.jsonl.tmp')
        temporary.write_text(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows))
        temporary.replace(path)
        outputs[path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    alias_path = ROOT / 'data/provenance/source_paths.json'
    aliases = json.loads(alias_path.read_text())
    inputs = sources + [patch_path, new_patch_path, scope_path, new_scope_path, review_path, alias_path, completed_target_path, whole_case_path, header_path]
    inputs += [ROOT / entry['path'] for entry in aliases.values()] + ([registry] if registry_used else []) + list(new_rows)
    manifest = dict(stage='quoted inputs, not completed labels', author='PI/OpenAI', added_cases=added,
                    rows=sum(map(len, datasets.values())), changes=dict(Counter(c['status'] for c in changes)),
                    new_case_changes=dict(Counter(new_patches[c]['status'] for c in applied_new_patches)),
                    unapplied_new_repair_ids=sorted(new_patches.keys() - applied_new_patches),
                    target_scope_applied=dict(frozen=len(scopes), new=len(new_scope_map.keys() & ids),
                                             validated_unchanged=len(completed_target_map.keys() & ids)),
                    source_headers_applied=len(header_map.keys() & ids),
                    unapplied_source_header_ids=sorted(header_map.keys() - ids),
                    whole_case_repairs_applied=len(whole_case_map.keys() & ids),
                    unapplied_whole_case_repair_ids=sorted(whole_case_map.keys() - ids),
                    unregistered_new_scope_ids=sorted(new_scope_map.keys() - ids),
                    sources={str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs},
                    files=outputs)
    (destination / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n')
    logger.info(f'Quoted inputs: {manifest["rows"]} rows; {added} accepted new cases; {manifest["changes"]}. {destination / "manifest.json"}')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=INPUT_DIR)
    parser.add_argument('--new-rows', type=Path, action='append', help='Explicit checked new-case JSONL; otherwise use the accepted-files registry')
    args = parser.parse_args()
    build(args.output, args.new_rows)
