"""Join current paired labels to versioned, source-checked quoted inputs. No model calls. -- PI/OpenAI

uv run python scripts/build_dataset.py [--preview]
Preview retains pending rows under v5_preview; a final build refuses missing or stale labels.
"""
import argparse
import hashlib
import gzip
import json
from collections import Counter
from pathlib import Path

from tiny_virtues import case_flags
from tiny_virtues.disagreement import add_disagreement_ranks
from tiny_virtues.judge_feedback import coverage, validate_record
from tiny_virtues.case_input import INPUT_DIR, judging_story
from tiny_virtues.quote_check import SPAN_CHECK_POLICY, span_warnings

ROOT = Path(__file__).resolve().parents[1]


def load(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def state_sha(row, task):
    state = dict(task=task, agent=row['person'], story=judging_story(row))
    return hashlib.sha256(json.dumps(state, ensure_ascii=False).encode()).hexdigest()


def with_span_warnings(row):
    return row | dict(span_check_policy=SPAN_CHECK_POLICY, span_warnings=span_warnings(row['quote'] or ''))


def attach(row, label, schema):
    row = with_span_warnings(row)
    if not row['quote']:
        assert label is None
        return row | dict(label_status='no_valid_quote', labels=None, p=None, labeller=None,
                          legacy_labels=dict(labels=row['labels'], p=row['p'], labeller=row['labeller']))
    assert label['state_sha'] == state_sha(row, schema['task']), row['id']
    assert label['qsha'] == schema['qsha'], row['id']
    assert label['labeller'] == schema['labeller'], row['id']
    schools = list(dict.fromkeys(k.split('|')[0] for k in schema['questions']))
    expected = {s: {} for s in schools}
    for key in schema['questions']:
        parts = key.split('|', 2)
        if len(parts) == 3:
            s, L, item = parts
            expected[s].setdefault(L, set()).add(item[1:])
    for s, lists in expected.items():
        assert set(label[s]['lists']) == set(lists), (row['id'], s)
        for L, items in lists.items():
            assert set(label[s]['lists'][L]) == items, (row['id'], s, L)
            for d in label[s]['lists'][L].values():
                forward = d['shows_vice'] if 'shows_vice' in d else d['shows']
                assert 0 <= forward <= 1 and 0 <= d['against'] <= 1
                signed = d['against'] - forward if 'shows_vice' in d else forward - d['against']
                assert abs(d['signed'] - signed) <= 0.00011
    p = {s: label[s]['p'] for s in schools}
    labels = {s: label[s]['label'] for s in schools}
    scores = [p[s]['well'] - p[s]['badly'] for s in schools]
    if 'feedback_sha' in schema or 'feedback_sha' in label:
        expected_sha = schema['feedback_sha'] if 'feedback_sha' in schema else label['feedback_sha']
        options = schema['unscored_questions']['debug_word']['criteria'] if 'unscored_questions' in schema else None
        policy = schema.get('feedback_policy', label.get('feedback_policy'))
        validate_record(label, expected_sha=expected_sha, expected_requests=-(-len(schema['questions']) // schema['chunk']),
                        options=options, allow_historical=policy == 'required_on_new_calls')
        feedback_fields = dict(feedback_status=label['feedback_status'], feedback_sha=label['feedback_sha'],
                               feedback_policy=policy, judge_feedback=label['judge_feedback'])
    else:
        feedback_fields = dict(feedback_status='feedback_missing', feedback_sha=None, judge_feedback=None)
    return row | feedback_fields | dict(
        agent=row['person'], labels=labels, p=p,
        legacy_labels=dict(labels=row['labels'], p=row['p'], labeller=row['labeller']),
        labeller=label['labeller'], served=label['served'],
        label_status='complete', question_sha=label['qsha'], state_sha=label['state_sha'],
        request_keys=label['request_keys'],
        virtue_lists={s: label[s]['lists'] for s in schools},
        labels_vary=len(set(labels.values())) > 1,
        spread=round(max(scores) - min(scores), 4))


def checked_rubrics(schema, snapshot=None):
    schools = list(dict.fromkeys(k.split('|')[0] for k in schema['questions']))
    if snapshot is not None:
        data = snapshot.read_bytes()
        rubrics = json.loads(gzip.decompress(data) if snapshot.suffix == '.gz' else data)
        rubrics = {school: rubrics[school] for school in schools}
    elif 'rubrics' in schema:
        rubrics = schema['rubrics']
        assert hashlib.sha256(json.dumps(rubrics, ensure_ascii=False).encode()).hexdigest() == schema['rubric_sha256']
    else:
        rubrics = {s: json.loads((ROOT / f'docs/virtues/lists/{s}.json').read_text()) for s in schools}
    assert set(rubrics) == set(schools), 'Rubric schools differ from scored schema'
    for school, rubric in rubrics.items():
        assert f': {rubric["tradition"]}\nBy ' in schema['questions'][f'{school}|verdict_r0']['instructions'], school
        expected = {f'{school}|verdict_r{i}' for i in range(3)}
        for L in rubric['lists']:
            names = {'virtues': 'virtue', 'vices': 'vice'} | L.get('side_names', {})
            for side, sign in (('virtues', '+'), ('vices', '-')):
                for item in L.get(side, []):
                    prefix = f'{school}|{L["key"]}|'
                    head = f'list "{L["name"]}": the {names[side]} {item["label"]}, described as: "{item["gloss"]}"\n'
                    if 'gloss_attribution' in item:
                        head += f'Description source: {item["gloss_attribution"]}\n'
                    if 'applicability' in L:
                        head += f'Applicability: {L["applicability"]}\n'
                    if 'source_context' in L:
                        head += f'Source context: {L["source_context"]}\n'
                    for marker in (sign, '~'):
                        key = prefix + marker + item['label']
                        assert head in schema['questions'][key]['instructions'], f'Rubric differs from scored schema: {key}; use a matching --rubric-snapshot'
                        expected.add(key)
        assert expected == {key for key in schema['questions'] if key.startswith(school + '|')}, school
    return rubrics


def merge_groups(parts):
    assert parts
    if len(parts) == 1:
        return parts[0]
    assert len({p['state_sha'] for p in parts}) == 1, 'Label groups have different input states'
    assert len({p['labeller'] for p in parts}) == 1, 'Label groups use different labellers'
    out = parts[0].copy()
    for field in ('labels', 'p', 'virtue_lists'):
        out[field] = {}
        for part in parts:
            assert not (out[field].keys() & part[field].keys()), 'Tradition appears in multiple label groups'
            out[field].update(part[field])
    out['question_sha_by_tradition'] = {s: p['question_sha'] for p in parts for s in p['labels']}
    out['question_sha'] = hashlib.sha256(json.dumps([p['question_sha'] for p in parts]).encode()).hexdigest()[:12]
    out['request_keys'] = [k for p in parts for k in p['request_keys']]
    assert len(out['request_keys']) == len(set(out['request_keys'])), 'Duplicate requests across groups'
    out['served'] = sorted({s for p in parts for s in p['served']})
    feedback = [entry for p in parts for entry in (p['judge_feedback'] if p['judge_feedback'] is not None else
                [dict(request_key=k, status='not_collected', question_sha=None, answer=None) for k in p['request_keys']])]
    out['judge_feedback'] = feedback
    out['feedback_status'] = coverage(feedback)
    versions = {p['feedback_sha'] for p in parts if p['feedback_sha'] is not None}
    assert len(versions) <= 1, 'Label groups have different feedback schemas'
    out['feedback_sha'] = next(iter(versions)) if versions else None
    out['labels_vary'] = len(set(out['labels'].values())) > 1
    scores = [p['well'] - p['badly'] for p in out['p'].values()]
    out['spread'] = round(max(scores) - min(scores), 4)
    return out


def build(preview=False, rubric_snapshot=None, label_groups=(), output=None, source_flags=case_flags.DEFAULT_REGISTRY):
    flags_by_id, public_flags, flag_bindings = case_flags.load_registry(source_flags)
    paths = [(ROOT / 'data/schemas/v5_questions.json', ROOT / 'private/labels/v5_labels.jsonl')] + list(label_groups)
    groups, rubric = [], {}
    for i, (schema_path, labels_path) in enumerate(paths):
        schema = json.loads(schema_path.read_text())
        own_rubrics = checked_rubrics(schema, rubric_snapshot if i == 0 else None)
        assert not (rubric.keys() & own_rubrics.keys()), 'Tradition appears in multiple label groups'
        rubric.update(own_rubrics)
        current = {x['id']: x for x in load(labels_path)
                   if x['qsha'] == schema['qsha'] and x['labeller'] == schema['labeller']}
        groups.append((schema, current))
    schema = groups[0][0]
    inputs = {name: load(INPUT_DIR / f'{name}.jsonl') for name in ('tinyvirtues', 'tinystoic')}
    assert set(flags_by_id) <= {row['id'] for rows in inputs.values() for row in rows}, 'Flag registry contains unknown story IDs'
    outputs, counts = {}, Counter()
    for name, rows in inputs.items():
        out = []
        for r in rows:
            r = with_span_warnings(case_flags.attach(r, flags_by_id))
            if not r['quote']:
                out.append(attach(r, None, schema))
            elif any(r['id'] not in current or current[r['id']]['state_sha'] != state_sha(r, own_schema['task'])
                     for own_schema, current in groups):
                assert preview, f"Missing current label for {r['id']}; full run unfinished"
                out.append(r | dict(label_status='pending', legacy_labels=dict(labels=r['labels'], p=r['p'], labeller=r['labeller']), labels=None, p=None, labeller=None))
            else:
                out.append(merge_groups([attach(r, current[r['id']], own_schema) for own_schema, current in groups]))
            counts[out[-1]['label_status']] += 1
        outputs[name] = out
    add_disagreement_ranks([row for rows in outputs.values() for row in rows])
    dest = ROOT / (output if output is not None else 'private/build/v5_preview' if preview else 'private/build/v5')
    dest.mkdir(parents=True, exist_ok=True)
    for name, rows in outputs.items():
        path = dest / f'{name}.jsonl'
        tmp = path.with_suffix('.jsonl.tmp')
        tmp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows))
        tmp.replace(path)
    assert all(case_flags.file_sha(name) == digest for name,digest in flag_bindings.items()), 'Source flag registry/receipt changed during build'
    sources = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
               for p in [INPUT_DIR / f'{name}.jsonl' for name in inputs]}
    (dest / 'source_flags.json').write_text(json.dumps(public_flags, ensure_ascii=False, indent=2) + '\n')
    (dest / 'rubrics.json').write_text(json.dumps(rubric, ensure_ascii=False, indent=1) + '\n')
    feedback_counts = Counter(r['feedback_status'] for rows in outputs.values() for r in rows if r['label_status'] == 'complete')
    manifest = dict(preview=preview, counts=dict(counts), feedback_counts=dict(feedback_counts),
                    span_check_policy=SPAN_CHECK_POLICY,
                    span_warning_rows=sum(bool(r['span_warnings']) for rows in outputs.values() for r in rows),
                    source_flag_columns=list(case_flags.FLAGS),
                    source_flag_counts={flag:sum(r[flag] for rows in outputs.values() for r in rows) for flag in case_flags.FLAGS},
                    source_flag_registry_sha256=case_flags.file_sha(source_flags), source_flag_bindings=flag_bindings,
                    qsha=(schema['qsha'] if len(groups) == 1 else hashlib.sha256(json.dumps([s['qsha'] for s, _ in groups]).encode()).hexdigest()[:12]),
                    label_groups=[dict(schema=str(p.relative_to(ROOT) if p.is_relative_to(ROOT) else p),
                                       labels=str(labels.relative_to(ROOT) if labels.is_relative_to(ROOT) else labels),
                                       qsha=s['qsha'], traditions=list(s['rubrics']) if 'rubrics' in s else list(dict.fromkeys(k.split('|')[0] for k in s['questions'])))
                                  for (p, labels), (s, _) in zip(paths, groups)],
                    labeller=schema['labeller'],
                    rubric_sha256=hashlib.sha256(json.dumps(rubric, ensure_ascii=False).encode()).hexdigest(),
                    sources=sources, question_count=sum(len(s['questions']) for s, _ in groups),
                    missing_quote_policy='Retained with original flags, no labels invented',
                    commonsense='Retained only in legacy_labels from v2, not relabelled',
                    signed='shows - against (virtues); against - shows_vice (vices)',
                    source_quotes='Unchanged from private/build/v2_quotes',
                    disagreement='Equal-weight Jensen-Shannon divergence of overall tradition probabilities, divided by log(3); ranked within (config, source_school); top fractions round up, ties use ID')
    (dest / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps(manifest, indent=2))
    print(dest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--major-run', type=Path, help='Offline major-virtue export from a complete cell database')
    parser.add_argument('--major-base', nargs='+', type=Path, help='Previous dataset JSONL files; retain old label values separately')
    parser.add_argument('--feedback-read', type=Path, help='Completed parent feedback reading bound to the current native replies')
    parser.add_argument('--source-flags', type=Path, default=case_flags.DEFAULT_REGISTRY,
                        help='Source-owner flag registry with receipt hashes; required for current builds')
    parser.add_argument('--item-major-run', type=Path, help='Offline five-level mixed export from a complete prepared run')
    parser.add_argument('--item-major-base', nargs='+', type=Path, help='Existing dataset JSONL files; all original values remain unchanged')
    parser.add_argument('--input-dir', type=Path, default=INPUT_DIR, help='Canonical source inputs for item-major export only')
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--rubric-snapshot', type=Path, help='Explicit frozen rubric JSON or JSON.gz for a historical scored schema')
    parser.add_argument('--label-group', nargs=2, action='append', type=Path, default=[], metavar=('SCHEMA', 'LABELS'),
                        help='Join another independently cached tradition group with its exact schema')
    parser.add_argument('--output', type=Path, help='Separate build destination for a real small-pipeline check')
    args = parser.parse_args()
    assert args.major_run or (args.major_base is None and args.feedback_read is None)
    if args.major_run:
        assert args.major_base and args.output and args.feedback_read, 'Major export requires old base files, feedback reading and a new --output directory'
        assert not args.item_major_run and args.item_major_base is None
        assert not args.preview and not args.rubric_snapshot and not args.label_group
        from tiny_virtues.major_export import export
        print(json.dumps(export(args.major_run, args.input_dir, args.major_base, args.output, args.feedback_read,
                                source_flags=args.source_flags), indent=2))
    elif args.item_major_run:
        assert args.source_flags == case_flags.DEFAULT_REGISTRY, 'Historical immutable export does not apply current source flags'
        assert args.item_major_base and args.output, 'Mixed export requires explicit base files and separate --output JSONL'
        assert not args.preview and not args.rubric_snapshot and not args.label_group
        from tiny_virtues.item_major_export import export
        sources = [r for name in ('tinyvirtues', 'tinystoic') for r in load(args.input_dir / f'{name}.jsonl')]
        base = [r for path in args.item_major_base for r in load(path)]
        print(json.dumps(export(sources, base, args.item_major_run, args.output), indent=2))
    else:
        assert args.item_major_base is None and args.input_dir == INPUT_DIR
        build(args.preview, args.rubric_snapshot, args.label_group, args.output, args.source_flags)
