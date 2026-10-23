"""Read immutable rubric-study snapshots and their exact file bindings. -- PI/Sol"""
import gzip
import hashlib
import json
import tomllib
from pathlib import Path

from tiny_virtues.case_input import judging_story, require_selected_target

PROJECT = Path(__file__).resolve().parents[2]


def read_json(path):
    path = Path(path)
    raw = path.read_bytes()
    if path.suffix == '.toml':
        return tomllib.loads(raw.decode())
    return json.loads(gzip.decompress(raw) if path.suffix == '.gz' else raw)


def read_bound(binding):
    path = PROJECT / binding['path']
    assert hashlib.sha256(path.read_bytes()).hexdigest() == binding['sha256'], f'Input changed: {path}'
    return read_json(path)


def study_inputs(group, experiment, split):
    path = PROJECT / 'data/development/rubric_gym' / group / 'study.json'
    manifest = read_json(path)
    assert manifest['format_version'] == 1 and manifest['group'] == group
    selected = manifest['experiments'][experiment]
    snapshot = read_bound(selected['inputs'])
    assert snapshot['format_version'] == 1
    # Validate every curated input, even when it is also present in the frozen snapshot.
    assert read_bound(selected['cases']) == snapshot['cases']
    assert read_bound(selected['questions']) == snapshot['question_templates']
    reference = read_bound(selected['references'][split])
    assert reference['items'] == [item['item_id'] for item in snapshot['package']['items']]
    reference_cases(reference)
    return selected, snapshot, reference


def validate_target_bindings(snapshot, cases):
    """Bind fresh inputs to frozen selected rows, not independent book verification. -- PI/Sol"""
    bindings = snapshot.get('target_bindings')
    assert isinstance(bindings, dict), 'Fresh judging requires snapshot target_bindings'
    for case in cases:
        row = bindings[case['id']]
        assert row['id'] == case['id']
        for field in ('quote', 'person', 'target_act_quote'):
            assert isinstance(row[field], str) and any(c.isalnum() for c in row[field]), (case['id'], field)
        require_selected_target(row)
        binding = row['source_binding']
        relative = Path(binding['path'])
        assert not relative.is_absolute() and '..' not in relative.parts, 'Use a project-relative source binding'
        path = (PROJECT / relative).resolve()
        assert path.is_relative_to(PROJECT.resolve()) and path.is_file(), 'Source binding escapes project or is missing'
        assert hashlib.sha256(path.read_bytes()).hexdigest() == binding['sha256'], 'Target source changed'
        assert path.suffix in ('.json', '.jsonl'), 'Target source must be a JSON array or JSONL'
        source = read_json(path) if path.suffix == '.json' else [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
        assert isinstance(source, list)
        by_id = {r['id']: r for r in source}
        assert len(by_id) == len(source), 'Duplicate target source case IDs'
        original = by_id[case['id']]
        fields = ('id', 'quote', 'person', 'target_act_quote', 'source_context')
        assert {k: row[k] for k in fields if k in row} == {k: original[k] for k in fields if k in original}, 'Target row differs from bound source'
        assert case['state']['agent'] == row['person'], case['id']
        assert case['state']['story'] == judging_story(row), case['id']


def reference_cases(reference):
    cases = {case: {} for case in reference['cases']}
    for reading in reference['readings']:
        case, item = reading['case'], reading['item']
        assert item in reference['items'] and item not in cases[case]
        value = {k: v for k, v in reading.items() if k not in ('case', 'item')}
        probabilities = value['probabilities']
        assert len(probabilities) == 5 and all(0 <= p <= 1 for p in probabilities)
        assert abs(sum(probabilities) - 1) < 1e-6
        cases[case][item] = value
    assert all(set(rows) == set(reference['items']) for rows in cases.values())
    return cases
