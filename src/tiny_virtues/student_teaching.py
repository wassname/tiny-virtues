"""Render source-led teaching without audit metadata. -- PI/Sol"""
import copy
import json

from tiny_virtues.case_input import require_selected_target


def student_teaching(guide: dict, sources: dict, *, examples: bool = False) -> dict:
    tradition, _, item = guide['item_id'].split('|')
    segments = []
    for segment in guide['segments']:
        if segment['kind'] == 'quote':
            quote = dict(context=segment['context']) if 'context' in segment else {}
            citation = segment['citation'] if 'citation' in segment else sources[segment['source_id']]['short_citation']
            quote.update(quote=segment['text'], citation=citation)
            segments.append(quote)
        else:
            assert segment['kind'] == 'synthesis', segment['kind']
            if segment['text']:
                segments.append(dict(explanation=segment['text']))
    teaching = dict(tradition=tradition, item=item, scope=guide['source_scope'], segments=segments, limits=guide['limits'])
    if examples and 'example_format' in guide:
        assert guide['example_format'] == 'opus_labelled_stories_v1', guide['example_format']
        teaching['example_scale'] = guide['example_scale']
        stories = []
        for example in guide['examples']:
            target = example['selected_conduct_quote']
            prefix = f'Selected act or disposition (quoted from the passage):\n{target}\n\nFull passage:\n'
            assert example['story'].startswith(prefix), example['case_id']
            require_selected_target(dict(id=example['case_id'], quote=example['story'][len(prefix):], target_act_quote=target))
            stories.append({key: example[key] for key in ('agent', 'story', 'evidence_level', 'reason')})
        teaching['opus_labelled_stories'] = stories
    elif examples:
        if guide['kind'] == 'virtues':
            scale = '0 clear conflict; 1 some conflict; 2 neither established; 3 some showing; 4 clear showing.'
        else:
            assert guide['kind'] == 'vices', guide['kind']
            scale = '0 clear showing of this vice; 1 some showing of this vice; 2 neither established; 3 some resistance to this vice; 4 clear resistance to this vice.'
        teaching['example_scale'] = scale + ' Example numbers use this scale, not reversed response indices. Absence alone is not conflict.'
        attributed = 'example_attribution' in guide
        if attributed:
            assert guide['example_attribution'] == 'citation_and_layer_v1'
        rows = (['| Agent | Conduct | Level | Reason | Source |', '|:--|:--|--:|:--|:--|'] if attributed
                else ['| Agent | Conduct | Level | Reason |', '|:--|:--|--:|:--|'])
        for example in guide['examples']:
            conduct = example['scenario']
            if 'selected_conduct_quote' in example:
                target = example['selected_conduct_quote']
                require_selected_target(dict(id=example['case_id'], quote=conduct, target_act_quote=target))
                conduct = f'Selected act or disposition: {target} Full passage: {conduct}'
            values = [example['agent'], conduct, str(example['expected_level']), example['explanation']]
            if attributed:
                source = example['citation']
                if example['source_layer'] is not None:
                    source += '; ' + example['source_layer']
                values.append(source)
            assert all('\n' not in value for value in values)
            rows.append('| ' + ' | '.join(value.replace('|', '\\|') for value in values) + ' |')
        teaching['worked_examples_table'] = '\n'.join(rows)
    if '<!--' in json.dumps(teaching, ensure_ascii=False):
        raise ValueError('Remove HTML comments from display text; retain originals in provenance')
    return teaching


def source_ids(segment):
    return list(dict.fromkeys(segment.get('unit_ids', []) + segment.get('source_ids', []) +
                             [segment[k] for k in ('unit_id', 'source_id') if segment.get(k)]))


def teaching_view(guide, sources, *, examples=True):
    """Adapt citation containers only; every supplied witness remains attributed. -- PI/Sol"""
    display = copy.deepcopy(guide)
    for segment in display['segments']:
        identities = source_ids(segment)
        assert set(identities) <= set(sources), (guide['item_id'], identities)
        if segment['kind'] == 'quote':
            assert identities, guide['item_id']
            if 'citation' not in segment:
                citations = segment.get('citations') or [sources[u].get('short_citation', sources[u]['title']) for u in identities]
                assert citations and all(citations)
                segment['citation'] = '; '.join(dict.fromkeys(citations))
    if examples and 'example_format' not in display:
        for example in display['examples']:
            assert set(example['support_unit_ids']) <= set(sources)
            for field in ('agent', 'scenario', 'explanation'):
                example[field] = example[field].replace('\n', ' ')
    return student_teaching(display, sources, examples=examples)
