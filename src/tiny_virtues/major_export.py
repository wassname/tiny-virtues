"""Offline, source-matched export of the major-virtue cell database. -- PI/Sol"""
from collections import Counter
from contextlib import closing
from decimal import Decimal
import hashlib
import json
import inspect
from pathlib import Path
import shutil
import sqlite3
import tempfile

from tiny_virtues import case_flags, item_major as builder, item_major_run as runner, major_label as major
from tiny_virtues.judge_feedback import HYBRID_SHA, validate_choice
from tiny_virtues.major_cells import cell_key, derive_contrary, story_hash
from tiny_virtues.quote_check import SPAN_CHECK_POLICY, span_warnings

SCHEMA = 'major-virtues-mixed-v2'
SOURCE_FIELDS = ('person', 'quote', 'target_act_quote', 'source_context', 'source_header')
OLD_LABEL_FIELDS = ('labels', 'p', 'labeller', 'legacy_labels', 'virtue_lists', 'label_status',
    'labels_vary', 'spread', 'disagreement', 'served', 'question_sha', 'question_sha_by_tradition',
    'state_sha', 'request_keys', 'feedback_status', 'feedback_sha', 'feedback_policy', 'judge_feedback')
ACTIVE_LABELS = '''FROM labels l JOIN stories s ON l.story_id=s.id AND l.input_sha=s.input_sha
                  JOIN virtues v ON l.virtue_id=v.id AND l.rubric_sha=v.rubric_sha'''


def read(path):
    return json.loads(Path(path).read_text())


def jsonl(path):
    with Path(path).open() as file:
        for line in file:
            yield json.loads(line)


def source_fields(row):
    return {key: row[key] for key in SOURCE_FIELDS if key in row}


def current_cell(db, virtue_id, identity, input_sha, version):
    value = db.execute('SELECT data FROM labels WHERE cell_key=?',
                       (cell_key(virtue_id, identity, input_sha, version),)).fetchone()
    assert value is not None, f'Missing current virtue/story cell: {virtue_id}, {identity}'
    return json.loads(value[0])


def validate_cohort(db, input_dir, held_ids=()):
    counts, selected, seen = Counter(), 0, set()
    for stem in ('tinyvirtues', 'tinystoic'):
        for row in jsonl(input_dir / (stem + '.jsonl')):
            assert row['id'] not in seen, f'Duplicate source ID: {row["id"]}'
            seen.add(row['id']); counts[stem] += 1
            if row['id'] not in held_ids and row.get('quote') and row.get('person') and row.get('target_act_quote'):
                stored = db.execute('SELECT input_sha FROM stories WHERE id=?', (row['id'],)).fetchone()
                assert stored == (story_hash(row),), f'Unlabelled or changed source; resume labelling first: {row["id"]}'
                selected += 1
    assert selected == db.execute('SELECT COUNT(*) FROM stories').fetchone()[0], 'Prepared cohort contains removed or unselected stories'
    return dict(rows=sum(counts.values()), configs=dict(counts), selected_stories=selected,
                held_stories=len(set(held_ids) & seen))


def audit_native(db, run_dir, manifest, direct, contraries, excerpts, *, inventory=None):
    """Reconstruct current cells from actual replies, without importing paid transport. -- PI/Sol"""
    db.execute('PRAGMA cache_size=-65536')
    db.execute('PRAGMA temp_store=FILE')
    by_id = {virtue['item_id']: virtue for virtue in direct}
    teachings = {identity:builder.render_teaching(virtue['guide'],virtue['sources'],
        example_stories=excerpts[identity] if virtue['tested_real_examples'] else {},format=builder.FIVE_LEVEL)
        for identity,virtue in by_id.items()}
    checked_sources = set()
    versions = manifest['versions']
    expected = db.execute('SELECT COUNT(*) FROM stories').fetchone()[0] * len(versions)
    assert db.execute('SELECT COUNT(*) ' + ACTIVE_LABELS).fetchone()[0] == expected, 'Current labels are incomplete'
    assert db.execute('SELECT COUNT(*) FROM requests WHERE complete=0').fetchone()[0] == 0, 'Run still has pending requests'
    paths = db.execute("SELECT DISTINCT json_extract(l.data,'$.native_path') " + ACTIVE_LABELS + ' ORDER BY 1')
    binding_hash, full_seen, counts = hashlib.sha256(), set(), Counter()
    strata = {mode: Counter() for mode in ('full', 'mixed', 'summary')}
    for native_name, in paths:
        native_path = Path(native_name)
        assert native_path.resolve().is_relative_to((run_dir / 'stages').resolve()), 'Native reply is outside this run'
        stage, packet_key = native_path.parent.parent, native_path.stem
        packet = read(stage / 'packets' / (packet_key + '.json'))
        binding = packet['cell_bindings']
        virtue = by_id[packet['item_id']]
        assert binding['rubric_sha'] == versions[packet['item_id']], 'Stale rubric version'
        for row in packet['source_rows']:
            source = row['id'], story_hash(row)
            if source not in checked_sources:
                builder.require_selected_target(row)
                checked_sources.add(source)
        expected_packet = builder.build_packet(virtue, packet['source_rows'],
            example_stories=excerpts[virtue['item_id']] if virtue['tested_real_examples'] else {},
            format=builder.FIVE_LEVEL, full_feedback_ids=binding['full_feedback_ids'],
            cached_teaching=teachings[virtue['item_id']],validate_source=False)
        assert packet == expected_packet | dict(cell_bindings=binding), 'Prepared request or teaching changed'
        assert binding['story_shas'] == {row['id']: story_hash(row) for row in packet['source_rows']}
        expected_contrary = (dict(id=contraries[virtue['item_id']], rubric_sha=versions[contraries[virtue['item_id']]])
                            if virtue['item_id'] in contraries else None)
        assert binding['contrary'] == expected_contrary, 'Contrary source binding changed'
        native = read(native_path)
        assert native['request'] == packet['request'] and native['key'] == packet['identity']['request_key']
        assert native['key'] == builder.digest(native['request']) and packet_key == builder.digest(packet['identity'])
        assert native['served'] == native['response']['model'] == builder.SERVED_MODEL
        assert native['response']['provider'] == 'TypeSafe'
        assert native['request']['provider'] == dict(only=['typesafe'], allow_fallbacks=False)
        assert native['feedback_sha'] == packet['identity']['feedback_sha'] == HYBRID_SHA
        feedback = native['judge_feedback']
        assert feedback['status'] == 'recorded' and feedback['question_sha'] == HYBRID_SHA, 'Missing actual feedback'
        validate_choice(feedback['answer'], native['request']['questions']['debug_word']['criteria'])
        assert native['response']['answers']['debug_word'] == feedback['answer']
        assert {k:v for k,v in native['response']['answers'].items() if k!='debug_word'} == native['answers']
        native_sha = runner.file_sha(native_path)
        scores = builder.parse_answers(packet, native['answers'])
        for score in scores:
            identity = score['id']; input_sha = binding['story_shas'][identity]
            current = db.execute('SELECT input_sha FROM stories WHERE id=?', (identity,)).fetchone()
            if current != (input_sha,):
                continue
            counts['current_scored_answers'] += 1
            expected_value = score | dict(vice_presence=-score['signed'], input_sha256=input_sha,
                rubric_v2_sha256=binding['rubric_sha'], native_path=str(native_path), native_sha256=native_sha,
                request_key=native['key'], feedback_sha=HYBRID_SHA, feedback_status='recorded')
            assert current_cell(db, packet['item_id'], identity, input_sha, binding['rubric_sha']) == expected_value
            if binding['contrary'] is not None:
                counts['current_derived_cells'] += 1
                vice = binding['contrary']
                derived = derive_contrary(expected_value, vice['id']) | dict(input_sha256=input_sha,
                    rubric_v2_sha256=vice['rubric_sha'], native_path=str(native_path), native_sha256=native_sha,
                    parent_cell_key=cell_key(packet['item_id'], identity, input_sha, binding['rubric_sha']))
                assert current_cell(db, vice['id'], identity, input_sha, vice['rubric_sha']) == derived
            if identity in binding['full_feedback_ids']:
                owner = db.execute('SELECT owner_virtue,complete,native_path FROM full_feedback WHERE story_id=? AND input_sha=?',
                                   (identity, input_sha)).fetchone()
                assert owner == (packet['item_id'], 1, str(native_path)), 'Full-copy ownership or coverage changed'
                assert identity not in full_seen, 'Multiple full copies for the same current story'
                full_seen.add(identity)
        receipt = db.execute('SELECT request_key,cost_usd,receipt_path FROM receipts WHERE response_id=?',
                             (native['response']['id'],)).fetchone()
        assert receipt == (native['key'], str(native['cost']), str(native_path)), 'Missing billing identity'
        assert Decimal(str(native['cost'])) == Decimal(str(native['response']['usage']['cost']))
        mode = ('summary' if not binding['full_feedback_ids'] else
                'full' if set(binding['full_feedback_ids']) == set(packet['row_ids']) else 'mixed')
        word = feedback['answer']['choice']
        counts['requests'] += 1; counts['raw_scored_answers'] += len(scores); counts['feedback:' + word] += 1
        strata[mode][word] += 1
        identity = dict(response_id=native['response']['id'], native_sha256=native_sha, request_key=native['key'])
        binding_hash.update((json.dumps(identity, sort_keys=True)+'\n').encode())
        if inventory is not None:
            inventory.write(json.dumps(identity | dict(native_path=str(native_path), feedback_mode=mode,
                full_story_ids=binding['full_feedback_ids'], row_ids=packet['row_ids'], feedback=feedback), ensure_ascii=False)+'\n')
    stories = db.execute('SELECT COUNT(*) FROM stories').fetchone()[0]
    assert len(full_seen) == stories, 'Current story lacks its full diagnostic copy'
    assert counts['current_scored_answers'] == stories * len(direct), 'Direct current-cell coverage changed'
    assert counts['current_derived_cells'] == stories * len(contraries), 'Derived current-cell coverage changed'
    return dict(status='native_reconstruction_pass', prepared_sha256=runner.file_sha(run_dir/'current.json'),
                native_bindings_sha256=binding_hash.hexdigest(), full_copy_stories=len(full_seen),
                counts=dict(counts), feedback_by_mode={key:dict(value) for key,value in strata.items()},
                missing_feedback=0, model_calls=0)


def audit_policy_sha():
    paths = ('item_major.py', 'student_teaching.py', 'case_input.py', 'judge_feedback.py', 'major_cells.py')
    return builder.digest(dict(core=inspect.getsource(audit_native),
        modules={name:runner.file_sha(Path(__file__).parent/name) for name in paths}))


def seal_native_audit(directory, run_dir):
    """Cache one completed reconstruction only while its SQLite snapshot is unchanged. -- PI/Sol"""
    directory, run_dir = Path(directory), Path(run_dir)
    snapshot = read(directory/'sqlite_snapshot.json')
    audit = read(directory/'native_audit.json')
    assert audit['status'] == 'native_reconstruction_pass'
    assert snapshot['prepared_sha256'] == audit['prepared_sha256'] == runner.file_sha(run_dir/'current.json')
    assert snapshot['sqlite_sha256'] == runner.file_sha(run_dir/'cells.sqlite'), 'Store changed during native audit'
    wal = run_dir/'cells.sqlite-wal'
    assert not wal.exists() or wal.stat().st_size == 0, 'Checkpoint before sealing a native audit'
    inventory = directory/'native_inventory.jsonl'
    count = 0
    for record in jsonl(inventory):
        assert runner.file_sha(record['native_path']) == record['native_sha256'], 'Native reply changed after audit'
        count += 1
    assert count == audit['counts']['requests']
    result = dict(schema_version=1, status='complete_native_audit_cached',
        prepared_sha256=audit['prepared_sha256'], sqlite_sha256=snapshot['sqlite_sha256'],
        audit_policy_sha256=audit_policy_sha(), audit_path=str(directory/'native_audit.json'),
        audit_sha256=runner.file_sha(directory/'native_audit.json'),
        inventory_path=str(inventory), inventory_sha256=runner.file_sha(inventory))
    runner.save(directory/'native_audit_cache.json', result)
    return result


def cached_native_audit(reference, run_dir, work):
    path = Path(reference['path'])
    assert runner.file_sha(path) == reference['sha256'], 'Native-audit cache record changed'
    cache = read(path)
    assert cache['schema_version'] == 1 and cache['status'] == 'complete_native_audit_cached'
    assert cache['prepared_sha256'] == runner.file_sha(run_dir/'current.json'), 'Cached audit belongs to another cohort'
    assert cache['sqlite_sha256'] == runner.file_sha(run_dir/'cells.sqlite'), 'Cached SQLite labels changed'
    assert cache['audit_policy_sha256'] == audit_policy_sha(), 'Native-audit code changed; reconstruct again explicitly'
    wal = run_dir/'cells.sqlite-wal'
    assert not wal.exists() or wal.stat().st_size == 0, 'Uncheckpointed labels differ from the cached audit'
    assert runner.file_sha(cache['audit_path']) == cache['audit_sha256']
    assert runner.file_sha(cache['inventory_path']) == cache['inventory_sha256']
    for record in jsonl(cache['inventory_path']):
        assert runner.file_sha(record['native_path']) == record['native_sha256'], 'Cached native reply changed'
    shutil.copyfile(cache['inventory_path'], work/'native_inventory.jsonl')
    print('Native audit: reusing the complete reconstruction after SQLite, policy and every reply hash matched.', flush=True)
    return read(cache['audit_path'])


def validate_feedback_read(path, audit):
    review = read(path)
    assert review['status'] == 'complete_parent_read_with_bounded_scope', 'Feedback has not been read'
    assert review['prepared_sha256'] == audit['prepared_sha256'], 'Feedback read belongs to another cohort'
    assert review['native_bindings_sha256'] == audit['native_bindings_sha256'], 'Feedback read belongs to other replies'
    assert review['feedback_missing'] == 0 and review['requests'] == audit['counts']['requests']
    assert review['feedback_counts'] == {k.removeprefix('feedback:'):v for k,v in audit['counts'].items() if k.startswith('feedback:')}
    assert review['representative_selection'] and review['personally_read']
    assert set(review['feedback_counts']) <= review['dispositions'].keys(), 'An observed feedback category has no disposition'
    for name, digest in review['read_artifacts'].items():
        assert runner.file_sha(name) == digest, f'Feedback reading artifact changed: {name}'
    return review


def export(run_dir, input_dir, base_paths, destination, feedback_read, *, source_flags=case_flags.DEFAULT_REGISTRY, smoke=False):
    """Keep canonical rows intact; add fresh major cells and explicitly old label snapshots. -- PI/Sol"""
    run_dir, input_dir, destination = map(Path, (run_dir, input_dir, destination))
    run_dir = run_dir.resolve()
    assert not destination.exists(), 'Use a new private build destination'
    source_paths = [input_dir/(stem+'.jsonl') for stem in ('tinyvirtues', 'tinystoic')]
    bindings = {str(path):runner.file_sha(path) for path in source_paths+list(map(Path, base_paths))}
    flags_by_id, public_flags, flag_bindings = case_flags.load_registry(source_flags)
    held_ids = {identity for identity, flags in flags_by_id.items() if flags['flag_held']}
    bindings.update(flag_bindings)
    assert base_paths, 'Explicit previous dataset JSONL files are required to preserve old labels'
    destination.parent.mkdir(parents=True, exist_ok=True)
    with major.run_lock(run_dir), closing(sqlite3.connect((run_dir/'cells.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.execute('BEGIN')
        manifest = read(run_dir/'current.json')
        options = ({'scope':manifest['virtue_scope'], 'virtue_ids':manifest['virtue_ids']}
                   if 'virtue_scope' in manifest else {})
        direct, contraries, excerpts, versions = major.selected_virtues(**options)
        assert manifest['format'] == builder.FIVE_LEVEL and manifest['feedback_sha'] == HYBRID_SHA
        assert manifest['versions'] == versions, 'Current rubric v2 differs; finish its labels before export'
        assert dict(db.execute('SELECT id,rubric_sha FROM virtues')) == versions
        coverage = validate_cohort(db, input_dir, held_ids)
        if smoke:
            assert manifest['scope'] == 'fast_dev_jev', 'Smoke export requires the separate tiny funding scope'
            assert coverage['selected_stories'] == 2 and len(versions) == len(direct) == 1, 'Smoke export requires two stories and one virtue'
            assert feedback_read is None, 'Smoke does not claim a completed semantic feedback review'
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix='major-build-') as temp:
            work = Path(temp)/'build'
            work.mkdir()
            review = read(feedback_read) if not smoke else {}
            if 'native_audit_cache' in review:
                audit = cached_native_audit(review['native_audit_cache'], run_dir, work)
            else:
                with (work/'native_inventory.jsonl').open('w') as inventory:
                    audit = audit_native(db, run_dir, manifest, direct, contraries, excerpts, inventory=inventory)
            if not smoke:
                validate_feedback_read(feedback_read, audit)
            runner.save(work/'native_audit.json', audit)
            with closing(sqlite3.connect(work/'old-labels.sqlite')) as old:
                old.execute('CREATE TABLE base (id TEXT PRIMARY KEY,data TEXT NOT NULL)')
                for path in base_paths:
                    for row in jsonl(path):
                        payload = dict(source=source_fields(row), values={k:row[k] for k in OLD_LABEL_FIELDS if k in row})
                        old.execute('INSERT INTO base VALUES (?,?)', (row['id'], json.dumps(payload, ensure_ascii=False)))
                old.commit()
                seen, completed, flag_counts, old_counts = set(), 0, Counter(), Counter()
                for source_path in source_paths:
                    with (work/source_path.name).open('w') as output:
                        for row in jsonl(source_path):
                            identity = row['id']; seen.add(identity)
                            found = old.execute('SELECT data FROM base WHERE id=?', (identity,)).fetchone()
                            previous = json.loads(found[0]) if found is not None else None
                            fresh = {}
                            if identity not in held_ids and row.get('quote') and row.get('person') and row.get('target_act_quote'):
                                input_sha = story_hash(row)
                                for virtue_id, version in versions.items():
                                    value = current_cell(db, virtue_id, identity, input_sha, version)
                                    fresh[virtue_id] = value | dict(rubric_version='v2')
                                    if 'derived_from' not in value:
                                        fresh[virtue_id].update(labeller=builder.MODEL, served_model=builder.SERVED_MODEL)
                                completed += 1
                            matches = previous['source'] == source_fields(row) if previous is not None else None
                            old_status = ('not_available' if previous is None else
                                          'old_not_rescored' if matches else 'old_stale_source_not_rescored')
                            old_counts[old_status] += 1
                            status = 'held' if identity in held_ids else 'complete' if fresh else 'no_selected_judging_input'
                            overlay = dict(schema=SCHEMA, rubric_version='v2', status=status,
                                input_sha256=story_hash(row) if fresh else None, virtues=fresh,
                                old_overall_status='old_not_rescored', old_legacy_status='old_untouched')
                            assert 'major_virtues' not in row and 'rubric_v1_labels' not in row, 'Do not stack builds'
                            result = case_flags.attach(row, flags_by_id)
                            if 'span_warnings' not in result:
                                result |= dict(span_check_policy=SPAN_CHECK_POLICY, span_warnings=span_warnings(row['quote'] or ''))
                            result |= dict(major_virtues=overlay, rubric_v1_labels=dict(
                                rubric_version='v1_old', status=old_status, source_matches_current=matches,
                                source_sha256=builder.digest(previous['source']) if previous is not None else None,
                                values=previous['values'] if previous is not None else {}))
                            flag_counts.update(flag for flag in case_flags.FLAGS if result[flag])
                            output.write(json.dumps(result, ensure_ascii=False)+'\n')
                assert set(flags_by_id) <= seen, 'Flag registry contains unknown story IDs'
                assert {identity for identity, in old.execute('SELECT id FROM base')} <= seen, 'Previous dataset has extra IDs'
                assert completed == coverage['selected_stories']
            (work/'old-labels.sqlite').unlink()
            runner.save(work/'source_flags.json', public_flags)
            assert all(runner.file_sha(name) == digest for name,digest in bindings.items()), 'Source/base/flag receipt changed during export'
            result = dict(author='PI/Sol', schema=SCHEMA, status='private_build_complete', coverage=coverage,
                complete_major_stories=completed, virtues=len(versions), direct_virtues=len(direct), derived_contraries=len(contraries),
                versions=versions, source_and_base_files=bindings, prepared_sha256=audit['prepared_sha256'],
                source_flag_columns=list(case_flags.FLAGS), source_flag_counts={flag:flag_counts[flag] for flag in case_flags.FLAGS},
                source_flag_registry_sha256=runner.file_sha(source_flags),
                feedback_read_sha256=runner.file_sha(feedback_read) if not smoke else None, native_bindings_sha256=audit['native_bindings_sha256'],
                old_label_status_counts=dict(old_counts),
                old_labels='Original canonical fields unchanged; available previous labels retained as old or stale; absent history is not_available',
                files={path.name:runner.file_sha(path) for path in work.iterdir()}, model_calls=0)
            if smoke:
                result.update(non_release_smoke=True, feedback_review_status='not_required_for_runtime_smoke')
            runner.save(work/'manifest.json', result)
            work.rename(destination)
            return result
