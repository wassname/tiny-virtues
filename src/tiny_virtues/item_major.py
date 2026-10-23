"""Production two-Noul, own-item teaching across independent story fields. -- PI/Sol"""
import copy
import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path

from tiny_virtues.case_input import judging_story, require_selected_target, selected_act_question
from tiny_virtues.judge_feedback import decision_request, FEEDBACK_SHA, request_feedback_sha
from tiny_virtues.rubric_inputs import read_json
from tiny_virtues.student_teaching import teaching_view

ROOT = Path(__file__).resolve().parents[2]
MODEL = 'typesafe/jev-1.14'
SERVED_MODEL = 'typesafe/jev-1.14-20261014'
LAYOUT = 'item-major-selected-act-v2-stable-fields'
FORMAT = 'two-noul-shows-against-v1'
FIVE_LEVEL = 'five-level-favorable-v1'
FIVE_PROJECTION = 'normalized-half-weight-evidence-v1'
FIVE_TEMPLATE = ROOT / 'data/schemas/item_major_five_level.json'
PROJECTION = 'opus-probability-mass-two-noul-v3'
FUNDING_SCOPE = 'jev_item_major_production_20261022'
FUNDING_LEDGER = ROOT / 'data/provenance/jev_item_major_production_20261022.json'
VICE_CUE = "Resisting this vice needs a described temptation, opportunity or choice to commit it; if the vice was not in play, answer no."
TASK = ('Judge each question independently by this one item\'s own source teaching. '
        'Each question supplies its own named agent, full passage and selected conduct. '
        'Do not adopt the narrator\'s verdict or infer an answer from other stories. '
        'Shows and against ask for separate pieces of evidence; absence of one is not evidence of the other. '
        'Answer yes only when the selected conduct supplies evidence for that direction; otherwise answer no.')
LEVELS = {
    'virtues': ['Clear evidence that the conduct conflicts with this virtue.',
                'Some evidence that the conduct conflicts with this virtue.',
                'Neither direction is established by the described conduct.',
                'Some evidence that the conduct shows this virtue.',
                'Clear evidence that the conduct shows this virtue.'],
    'vices': ['Clear evidence that the conduct shows this vice.',
              'Some evidence that the conduct shows this vice.',
              'Neither direction is established by the described conduct.',
              'Some evidence that the conduct resists this vice.',
              'Clear evidence that the conduct resists this vice.'],
}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False).encode()).hexdigest()


def project_example(example, kind, *, story=None):
    """Describe the two directional masses of the retained Opus distribution. -- PI/Sol"""
    LEVELS[kind].index(example['evidence_level'])
    probabilities = example['reference_provenance']['reading']['probabilities']
    assert len(probabilities) == 5 and all(math.isfinite(p) and 0 <= p <= 1 for p in probabilities)
    assert math.isclose(sum(probabilities), 1, abs_tol=1e-6)
    adverse, favorable = [sum(Decimal(str(p)) for p in side) for side in (probabilities[:2], probabilities[3:])]
    directions = (favorable, adverse) if kind == 'virtues' else (adverse, favorable)
    labels = {direction: 'no evidence' if mass < Decimal('.25') else
              'yes, some evidence' if mass <= Decimal('.6') else 'yes, clear evidence'
              for direction, mass in zip(('shows', 'against'), directions, strict=True)}
    full = example['story']
    target = example['selected_conduct_quote']
    prefix = f'Selected act or disposition (quoted from the passage):\n{target}\n\nFull passage:\n'
    assert full.startswith(prefix), example['case_id']
    require_selected_target(dict(id=example['case_id'], quote=full[len(prefix):], target_act_quote=target))
    if story is not None:
        # Shortening is supplied by the parent; do not manufacture context. -- PI/Sol
        from tiny_virtues.quote_check import problems
        assert not problems(story, full[len(prefix):]), example['case_id']
        require_selected_target(dict(id=example['case_id'], quote=story, target_act_quote=target))
        full = f'Selected act or disposition (quoted from the passage):\n{target}'
        if story != target:
            if story.endswith(' ... ' + target):
                context = story[:-len(' ... ' + target)]
            else:
                assert story.startswith(target + ' ... '), example['case_id']
                context = story[len(target + ' ... '):]
            assert context.strip()
            full += f'\n\nContext from the same passage:\n{context}'
    return dict(agent=example['agent'], story=full, evidence=labels, reason=example['reason'])


def render_teaching(guide, sources, *, example_stories=None, format=FORMAT):
    """Source teaching plus only own Opus real-story examples; older examples stay audit-only. -- PI/Sol"""
    assert format in (FORMAT, FIVE_LEVEL), format
    teaching = teaching_view(guide, sources, examples=False)
    if 'example_format' in guide:
        assert guide['example_format'] == 'opus_labelled_stories_v1', guide['item_id']
        overrides = example_stories or {}
        assert set(overrides) <= {e['case_id'] for e in guide['examples']}
        examples = [project_example(e, guide['kind'], story=overrides.get(e['case_id'])) for e in guide['examples']]
        if format == FIVE_LEVEL:
            # Keep original verbal levels, not two-Noul masses or indices. -- PI/Sol
            teaching['example_scale'] = guide['example_scale']
            teaching['opus_labelled_stories'] = [dict(agent=e['agent'], story=e['story'], reason=e['reason'],
                                                     evidence_level=original['evidence_level'])
                                                 for e, original in zip(examples, guide['examples'], strict=True)]
        else:
            teaching['opus_labelled_stories'] = examples
            teaching['example_projection'] = PROJECTION
    else:
        assert not example_stories, 'No real-story examples available to shorten'
    return teaching


def load_items(root=ROOT, *, scope='production'):
    """Load the approved union, or every canonical virtue for offline pricing. -- PI/Sol"""
    assert scope in ('production', 'major', 'all')
    root = Path(root)
    canonical = {}
    for path in sorted((root / 'docs/virtues/lists').glob('*.json')):
        doc = read_json(path)
        for listing in doc['lists']:
            for kind in ('virtues', 'vices'):
                for item in listing.get(kind, []):
                    identity = f"{doc['school']}|{listing['key']}|{item['label']}"
                    assert identity not in canonical, identity
                    canonical[identity] = dict(item_id=identity, kind=kind, primary=listing['primary'],
                                               list_name=listing['name'],
                                               **{k: listing[k] for k in ('applicability', 'source_context') if k in listing})
    guides = {}
    for path in sorted((root / 'rubrics').glob('*/*/rubric.json')):
        doc = read_json(path)
        statuses = {s['item_id']: s for s in doc['item_statuses']}
        for guide in doc['guides']:
            identity = guide['item_id']
            assert identity in canonical and identity not in guides, identity
            assert guide['kind'] == canonical[identity]['kind'], identity
            binding = doc.get('source_bindings', {}).get(identity, doc.get('source_context'))
            assert binding, identity
            guides[identity] = dict(guide=guide, binding=binding, status=statuses[identity], rubric_path=str(path.relative_to(root)),
                                    rubric_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    assert set(guides) == set(canonical), 'Canonical teaching coverage changed'
    tested = {i for i, g in guides.items() if g['guide'].get('example_format') == 'opus_labelled_stories_v1'}
    assert len(tested) == 85, 'Reapprove changed tested-item coverage'
    main = {i for i, c in canonical.items() if c['primary']}
    selected = set(canonical) if scope == 'all' else main if scope == 'major' else main | tested
    result, contexts = [], {}
    for identity in sorted(selected):
        data = guides[identity]
        binding = data['binding']
        path = root / binding['path']
        if path not in contexts:
            contexts[path] = (hashlib.sha256(path.read_bytes()).hexdigest(), read_json(path)['source_units'])
        digest, sources = contexts[path]
        assert digest == binding['sha256'], f'Source context changed: {path}'
        result.append(canonical[identity] | data | dict(sources=sources, tested_real_examples=identity in tested))
    return result


def question_id(row_id, direction):
    """Keep each field name unchanged across alone/batched/reversed presentations. -- PI/Sol"""
    assert isinstance(row_id, str) and row_id
    assert direction in ('shows', 'against', 'level')
    return 's' + hashlib.sha256(row_id.encode()).hexdigest()[:16] + '_' + direction


def build_request(item, rows, *, example_stories=None, format=FORMAT, view='forward',
                  cached_teaching=None, validate_source=True):
    """Return exact native production request, including diagnostic; no I/O or inference. -- PI/Sol"""
    assert format in (FORMAT, FIVE_LEVEL), format
    assert view in ('forward', 'reverse') and (format == FIVE_LEVEL or view == 'forward')
    if format == FIVE_LEVEL:
        return five_level_request(item, rows, example_stories=example_stories, view=view,
                                  cached_teaching=cached_teaching, validate_source=validate_source)
    assert cached_teaching is None and validate_source
    assert rows and len(rows) <= 40, 'Use 1..40 stories per item request'
    assert len({r['id'] for r in rows}) == len(rows), 'Duplicate story IDs'
    teaching = render_teaching(item['guide'], item['sources'], example_stories=example_stories)
    state = dict(task=TASK, item_id=item['item_id'], kind=item['kind'], list_name=item['list_name'], source_led_teaching=teaching,
                 **{k: item[k] for k in ('applicability', 'source_context') if k in item})
    questions = {}
    quality = 'virtue' if item['kind'] == 'virtues' else 'vice'
    for row in rows:
        require_selected_target(row)
        assert isinstance(row['person'], str) and row['person'].strip(), row['id']
        for direction in ('shows', 'against'):
            question = (f'Does the selected conduct of {row["person"]} show this {quality} as described?'
                        if direction == 'shows' else f'Does the selected conduct of {row["person"]} go against this {quality} as described?')
            instructions = dict(question=question, agent=row['person'], story=judging_story(row), selected_act_quote=row['target_act_quote'],
                evidence_rule='Yes only when the described selected conduct supplies evidence for this direction under the teaching and any stated applicability. Mere absence of the other direction is not evidence.')
            if quality == 'vice':
                instructions['vice_evidence_rule'] = VICE_CUE
            key = question_id(row['id'], direction)
            assert key not in questions, 'Question ID collision'
            questions[key] = selected_act_question(dict(type='noul', instructions=instructions))
    return decision_request(state, questions, model=MODEL)


def five_level_request(item, rows, *, example_stories=None, view='forward', cached_teaching=None, validate_source=True):
    """Reuse frozen question/task/criteria wording; identity and story slots vary. -- PI/Sol"""
    assert rows and len(rows) <= 40, 'Use 1..40 stories per item request'
    assert len({r['id'] for r in rows}) == len(rows), 'Duplicate story IDs'
    template = read_json(FIVE_TEMPLATE)
    form = template['forms'][item['kind']][view]
    tradition, _, name = item['item_id'].split('|')
    question = dict(tradition=tradition, list=item['list_name'], item=name, **form['question'])
    # Keep canonical applicability for untested main items too. -- PI/Sol
    question.update({k: item[k] for k in ('applicability', 'source_context') if k in item})
    teaching = (render_teaching(item['guide'], item['sources'], example_stories=example_stories, format=FIVE_LEVEL)
                if cached_teaching is None else cached_teaching)
    state = dict(layout_scope=template['layout_scope'], judging_task=template['judging_task'], source_led_teaching=teaching)
    questions = {}
    for row in rows:
        if validate_source:
            require_selected_target(row)
        assert isinstance(row['person'], str) and row['person'].strip(), row['id']
        key = question_id(row['id'], 'level')
        assert key not in questions, 'Question ID collision'
        questions[key] = selected_act_question(dict(type='score', instructions=dict(question=copy.deepcopy(question),
            agent=row['person'], story=judging_story(row)), criteria=copy.deepcopy(form['criteria'])))
    return decision_request(state, questions, model=MODEL)


def parse_five_level(packet, answers):
    """Normalize rounded probabilities, then half-weight; retain raw values. -- PI/Sol"""
    result = []
    view = packet['identity']['view']
    for identity in packet['row_ids']:
        key = question_id(identity, 'level')
        answer = answers[key]
        assert answer['type'] == 'score'
        raw = answer['probabilities']
        assert isinstance(raw, dict) and set(raw) == {str(n) for n in range(5)}
        vector = [raw[str(n)] for n in range(5)]
        assert all(not isinstance(p, bool) and isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1 for p in vector)
        mass = sum(vector)
        assert math.isclose(mass, 1, abs_tol=.02 + 1e-8), 'Five-level raw probability mass outside rounding tolerance'
        normalized = [p / mass for p in vector]
        favorable = normalized if view == 'forward' else normalized[::-1]
        adverse = favorable[0] + .5 * favorable[1]
        positive = favorable[4] + .5 * favorable[3]
        shows, against = (positive, adverse) if packet['kind'] == 'virtues' else (adverse, positive)
        signed = shows - against if packet['kind'] == 'virtues' else against - shows
        expectation = sum(n * p for n, p in enumerate(favorable))
        # Normalize displayed probabilities; keep the native score separately. -- PI/Sol
        assert math.isclose(signed, (expectation - 2) / 2, abs_tol=1e-12)
        provider_score = answer['score']
        assert not isinstance(provider_score, bool) and isinstance(provider_score, (int, float))
        assert math.isfinite(provider_score) and 0 <= provider_score <= 4
        criteria = packet['request']['questions'][key]['criteria']
        expected = LEVELS[packet['kind']][::1 if view == 'forward' else -1]
        assert criteria == expected, 'Criterion order differs from declared view'
        assert answer['legend'] == {str(n): text for n, text in enumerate(criteria)}, 'Provider criterion legend differs'
        result.append(dict(id=identity, item_id=packet['item_id'], shows=shows, against=against, signed=signed,
                           raw_probabilities=copy.deepcopy(raw), normalized_probabilities=normalized,
                           favorable_probabilities=copy.deepcopy(favorable),
                           normalization_policy='divide each displayed probability by sum of all five; preserve raw values',
                           criteria=copy.deepcopy(criteria), view=view, provider_score=provider_score,
                           normalized_expected_level=expectation, raw_probability_mass=mass,
                           signed_definition='(sum(level * favorable_normalized_P) - 2) / 2',
                           format=FIVE_LEVEL, projection=FIVE_PROJECTION,
                           evidence_semantics='weighted evidence, not independently calibrated yes/no probabilities'))
    return result


def build_packet(item, rows, *, example_stories=None, format=FORMAT, view='forward', full_feedback_ids=None,
                 cached_teaching=None, validate_source=True):
    """Bind native request, original inputs, statuses and audit-only examples for exact resume. -- PI/Sol"""
    request = build_request(item, rows, example_stories=example_stories, format=format, view=view,
                            cached_teaching=cached_teaching, validate_source=validate_source)
    full_questions = None
    if full_feedback_ids is not None:
        assert format == FIVE_LEVEL, 'Hybrid production uses the approved five-level format'
        assert set(full_feedback_ids) <= {row['id'] for row in rows}
        full_questions = [question_id(row['id'], 'level') for row in rows if row['id'] in full_feedback_ids]
        scored = {key: value for key, value in request['questions'].items() if key != 'debug_word'}
        request = decision_request(request['state'], scored, model=MODEL, full_feedback_questions=full_questions)
    identity = dict(layout=LAYOUT, format=format, model=MODEL, served_model=SERVED_MODEL,
                    projection=FIVE_PROJECTION if format == FIVE_LEVEL else PROJECTION,
                    feedback_sha=request_feedback_sha(request), request_key=digest(request), rows_sha256=digest(rows),
                    teaching_sha256=digest(request['state']['source_led_teaching']),
                    item_sha256=digest({k: v for k, v in item.items() if k != 'sources'}))
    if format == FIVE_LEVEL:
        identity.update(view=view, template_sha256=hashlib.sha256(FIVE_TEMPLATE.read_bytes()).hexdigest())
    directions = ('level',) if format == FIVE_LEVEL else ('shows', 'against')
    return dict(identity=identity, packet_key=digest(identity), request=request, item_id=item['item_id'], kind=item['kind'],
                row_ids=[r['id'] for r in rows],
                question_mapping={question_id(row['id'], direction): dict(case_id=row['id'], item_id=item['item_id'], direction=direction)
                                  for row in rows for direction in directions},
                status=copy.deepcopy(item['status']), tested_real_examples=item['tested_real_examples'],
                source_rows=copy.deepcopy(rows), example_audit=copy.deepcopy(item['guide']['examples']),
                **(dict(full_feedback_questions=full_questions) if full_questions is not None else {}))


def parse_answers(packet, answers):
    """Preserve both raw true probabilities, deriving signed weight without rounding. -- PI/Sol"""
    expected = set(packet['request']['questions']) - {'debug_word'}
    assert set(answers) == expected, 'Missing or extra scored answers'
    if packet['identity']['format'] == FIVE_LEVEL:
        return parse_five_level(packet, answers)
    assert packet['identity']['format'] == FORMAT
    result = []
    for identity in packet['row_ids']:
        values = {d: answers[question_id(identity, d)]['noul'] for d in ('shows', 'against')}
        assert all(not isinstance(p, bool) and isinstance(p, (int, float)) and math.isfinite(p) and 0 <= p <= 1 for p in values.values())
        signed = values['shows'] - values['against']
        if packet['kind'] == 'vices':
            signed = -signed
        result.append(dict(id=identity, item_id=packet['item_id'], **values, signed=signed))
    return result


def estimate(packets, *, format=FORMAT):
    """Serialized-size estimate using observed token/character ratios; no cache discount. -- PI/Sol"""
    characters = sum(len(json.dumps(p['request'], ensure_ascii=False)) for p in packets)
    formats = {p.get('identity', {}).get('format', FORMAT) for p in packets}
    assert len(formats) <= 1, 'Estimate one format at a time'
    formats = formats or {format}
    ratios = (read_json(FIVE_TEMPLATE)['pricing_observation']['token_character_ratios'] if formats == {FIVE_LEVEL}
              else dict(low=0.25423, central=0.25830, high=0.28507))
    return dict(status='estimate_not_quote', tariff_usd_per_million_input_tokens=0.042, caching_discount_assumed=False,
                serialized_characters=characters, requests=len(packets), cells=sum(len(p['row_ids']) for p in packets),
                estimated_usd={k: characters * r * 0.042 / 1_000_000 for k, r in ratios.items()}, token_character_ratios=ratios)
