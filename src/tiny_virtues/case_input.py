"""One quoted input for labelling, building and evaluation. -- PI/OpenAI"""
import hashlib
import json
import os
import re
from pathlib import Path

from tiny_virtues.quote_check import editor_brackets, norm, problems

ROOT = Path(__file__).resolve().parents[2]
INPUT_DIR = ROOT / os.environ.get('V5_INPUT_DIR', 'private/build/v5_inputs')


def check_target(row):
    if 'target_act_quote' in row:
        text = row['target_act_quote']
        assert text and not problems(text, row['quote']) and not editor_brackets(text, row['quote']), row['id']


def require_selected_target(row):
    assert 'target_act_quote' in row, f"{row['id']}: new judging requires a source-checked selected act; do not judge an unscoped full passage"
    check_target(row)
    passage = norm(row['quote'], keep_verse_refs=True)
    position = 0
    for fragment in re.split(r'\.\.\.|…', row['target_act_quote']):
        fragment = norm(fragment, keep_verse_refs=True).strip()
        if not fragment:
            continue
        found = passage.find(fragment, position)
        assert found >= 0, f"{row['id']}: selected act is not a displayed passage span: {fragment!r}"
        position = found + len(fragment)


def selected_act_question(question):
    return question | dict(instructions=question['instructions'] | dict(
        target_scope='Judge only the selected act or disposition quoted at the start of the story. '
        'Use the full passage to interpret its motive, circumstances and consequences, '
        'not to score other acts by the same person or by other characters.'))


def scoped(row, change):
    assert change['id'] == row['id'], row['id']
    assert change['before_quote_sha256'] == hashlib.sha256(row['quote'].encode()).hexdigest(), row['id']
    person = change['after_person'] if 'after_person' in change else row['person']
    pending_reference = dict(source_verdict='', verdict_basis='unclear', verdict_reading='', verdict_quote='', judgeable=False,
        judgeable_why='Source reference is pending for the restored target; historical fields are preserved separately',
        source_verdict_target_status='pending_review')
    patch_sha = hashlib.sha256(json.dumps(change, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if 'target_scope_sha256' in row:
        prior_hashes = {patch_sha}
        if 'before_target_scope_sha256' in change:
            prior_hashes.add(change['before_target_scope_sha256'])
        assert row['target_scope_sha256'] in prior_hashes and row['person'] == person, row['id']
        assert row['target_act_quote'] == change['target_act_quote'] and row['target_scope_reason'] == change['reason'], row['id']
        if 'after_story' in change:
            assert row['story'] == change['after_story'], row['id']
        if 'legacy_target_metadata' in row or 'legacy_target_metadata' in change:
            assert 'legacy_target_metadata' in row and 'legacy_target_metadata' in change, row['id']
            assert row['legacy_target_metadata'] == change['legacy_target_metadata'], row['id']
            assert row['act'] == change['target_act_quote'] and row['act_spans'] == [change['target_act_quote']], row['id']
            assert all(row[k] == value for k, value in pending_reference.items()), row['id']
        if change['before_person'] != person:
            assert row['legacy_person'] == change['before_person'], row['id']
            assert row['original_survey_target'] == change['original_survey'], row['id']
            assert row['actor_change_reason'] == change['actor_change_reason'], row['id']
        check_target(row)
        return row | dict(target_scope_sha256=patch_sha)
    assert change['before_person'] == row['person'], row['id']
    output = row | dict(target_act_quote=change['target_act_quote'], target_scope_reason=change['reason'], target_scope_sha256=patch_sha)
    if 'legacy_target_metadata' in change:
        assert all(row[k] == value for k, value in change['legacy_target_metadata'].items()), row['id']
        historical = {k: row[k] for k in ('source_verdict', 'verdict_basis', 'verdict_reading', 'verdict_quote', 'judgeable', 'judgeable_why') if k in row}
        output |= dict(legacy_target_metadata=change['legacy_target_metadata'], legacy_source_reference=historical,
                       **pending_reference, act=change['target_act_quote'], act_spans=[change['target_act_quote']])
    if 'after_story' in change:
        assert row['story'] == change['legacy_target_metadata']['story'], row['id']
        output |= dict(story=change['after_story'])
    if person != row['person']:
        assert change['actor_change_reason'] and change['original_survey'], row['id']
        assert change['original_survey']['id'] == row['id'].split(':')[-1], row['id']
        assert not problems(change['actor_source_quote'], row['original'], row['fixed']), row['id']
        output |= dict(person=person, legacy_person=row['person'], actor_change_reason=change['actor_change_reason'],
                       original_survey_target=change['original_survey'])
    check_target(output)
    return output


def judging_story(row):
    text = row['quote']
    if 'source_header' in row:
        header = row['source_header']
        assert isinstance(header, str) and header.startswith('Source: ') and header[8:].strip()
        assert '\n' not in header and '\r' not in header
        text = header + '\n' + text
    if 'target_act_quote' in row:
        text = f'Selected act or disposition (quoted from the passage):\n{row["target_act_quote"]}\n\nFull passage:\n{text}'
    if 'source_context' in row:
        assert text is not None, 'Supplementary context needs a primary quote'
        for context in row['source_context']:
            text += f'\n\nSupplementary source context — {context["attribution"]}:\n{context["quote"]}'
    return text
