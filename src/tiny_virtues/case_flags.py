"""Receipt-backed source flags; flag metadata does not change judged text. -- PI/Sol"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_REGISTRY = ROOT / 'data/provenance/case_flags_20261023.json'
PUBLIC_REGISTRY = 'data/release/source_flags.json'
MAX_REASON_CHARS = 300
FLAGS = ('flag_multi_person_agent', 'flag_no_act', 'flag_ocr_garble',
         'flag_editor_header', 'flag_editor_gloss_target', 'flag_held')


def file_sha(path):
    with Path(path).open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def validate_columns(row):
    assert all(type(row[flag]) is bool for flag in FLAGS), 'Source flags must be booleans'
    true_flags = {flag for flag in FLAGS if row[flag]}
    assert isinstance(row['flag_sources'], dict) and set(row['flag_sources']) == true_flags, \
        'Every true flag requires a receipt, and false flags must not have receipts'
    assert all(isinstance(path, str) and path.strip() for path in row['flag_sources'].values()), \
        'Flag receipt paths must be nonempty strings'


def validate_reason(reason):
    assert isinstance(reason, str) and reason.strip() and len(reason) <= MAX_REASON_CHARS and '\n' not in reason and '\r' not in reason, \
        'Flag reasons must be nonempty single lines of at most 300 characters'


def load_registry(path=DEFAULT_REGISTRY):
    path, root = Path(path), ROOT.resolve()
    digest = file_sha(path)
    registry = json.loads(path.read_text())
    assert registry['schema_version'] == 1 and registry['author'].strip(), 'Unsupported source-flag registry'
    by_id, public, receipts = {}, {}, set()
    for entry in registry['entries']:
        identity = entry['id']
        assert isinstance(identity, str) and identity.strip() and identity not in by_id, 'Duplicate or empty flag ID'
        flags = set(entry) - {'id', 'flag_sources', 'flag_reasons'}
        assert flags and flags <= set(FLAGS), 'Unknown or empty source flag set'
        assert all(entry[flag] is True for flag in flags), 'Registry entries contain true flags only'
        columns = {flag: flag in flags for flag in FLAGS} | dict(flag_sources=entry['flag_sources'])
        validate_columns(columns)
        assert set(entry['flag_reasons']) == flags, 'Every true flag requires a short reason'
        for reason in entry['flag_reasons'].values():
            validate_reason(reason)
        by_id[identity] = columns
        receipts.update(entry['flag_sources'].values())
        public[identity] = {flag: dict(reason=entry['flag_reasons'][flag], private_registry_sha256=digest)
                            for flag in sorted(flags)}
    assert set(registry['receipt_sha256']) == receipts, 'Receipt hashes must match the referenced receipt set'
    bindings = {str(path): digest}
    for name, expected in registry['receipt_sha256'].items():
        relative = Path(name)
        resolved = (root / relative).resolve()
        assert not relative.is_absolute() and resolved.is_relative_to(root), 'Flag receipts must be project-relative'
        assert file_sha(resolved) == expected, f'Flag receipt changed: {name}'
        bindings[str(resolved)] = expected
    assert file_sha(path) == digest, 'Source-flag registry changed while loading'
    return by_id, dict(schema_version=1, entries=public), bindings


def attach(row, by_id):
    columns = ({flag: False for flag in FLAGS} | dict(flag_sources={}))
    if row['id'] in by_id:
        columns = by_id[row['id']]
    validate_columns(columns)
    assert all(key not in row or row[key] == value for key, value in columns.items()), \
        f'{row["id"]}: existing source flags differ from the authoritative registry'
    return row | columns


def public_columns(row):
    validate_columns(row)
    columns = {flag: row[flag] for flag in FLAGS}
    columns['flag_sources'] = {flag: PUBLIC_REGISTRY for flag in FLAGS if row[flag]}
    if 'span_warnings' in row:
        columns['span_warnings'] = row['span_warnings']
    return columns


def validate_public(row, registry):
    validate_columns(row)
    flags = registry['entries'][row['id']] if row['id'] in registry['entries'] else {}
    assert set(flags) == set(row['flag_sources']), f'{row["id"]}: public flag receipts do not match'
    assert all(path == PUBLIC_REGISTRY for path in row['flag_sources'].values()), 'Unexpected public flag registry'
    for value in flags.values():
        assert set(value) == {'reason', 'private_registry_sha256'}, 'Unexpected public flag receipt field'
        validate_reason(value['reason'])
        digest = value['private_registry_sha256']
        assert isinstance(digest, str) and len(digest) == 64 and set(digest) <= set('0123456789abcdef'), \
            'Missing private flag-registry hash'
