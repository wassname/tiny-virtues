"""Compare saved paired evaluations and a story-mean control without model calls. -- PI/Sol"""
from pathlib import Path

import numpy as np
from tabulate import tabulate

from tiny_virtues import major_evaluation as evaluation

REFERENCE = evaluation.ROOT / 'outputs/rubric_gym/nshot-all-groups/loop-4/metrics.json'


def teaching_reference(path, *, draws=1000):
    document = evaluation.read(path)
    raw, centered = [], []
    groups = {}
    for group in document['groups']:
        scores = np.asarray(group['matrices']['opus_nshot'], float)
        reference = np.asarray(group['reference_matrix'], float)
        assert scores.shape == reference.shape == (len(group['cases']), len(group['items']))
        assert np.isfinite(scores).all() and np.isfinite(reference).all()
        left, right = scores - scores[:, :1], reference - reference[:, :1]
        left -= left.mean(axis=1, keepdims=True)
        right -= right.mean(axis=1, keepdims=True)
        own = []
        for identity, a, b, x, y in zip(group['cases'], scores, reference, left, right, strict=True):
            raw.extend(dict(id=identity, a=float(i), b=float(j)) for i, j in zip(a, b, strict=True))
            own.extend(dict(id=identity, a=float(i), b=float(j)) for i, j in zip(x, y, strict=True))
        centered.extend(own)
        groups[group['group']] = dict(pattern_r=evaluation.correlation([r['a'] for r in own], [r['b'] for r in own]),
            stories=len(group['cases']), cells=scores.size)
    identities = sorted({r['id'] for r in raw})
    return dict(model='Jev with rubric v2 teaching vs Opus 5.5', source=str(path),
        comparison_scope='Separate 13-group development cohort; not the 400-story model comparison.',
        stories=len(identities), cells=len(raw), groups=groups,
        r=evaluation.correlation([r['a'] for r in raw], [r['b'] for r in raw]),
        pattern_r=evaluation.correlation([r['a'] for r in centered], [r['b'] for r in centered]),
        r_resampling=evaluation.story_interval(raw, identities, seed=20261023, draws=draws),
        pattern_resampling=evaluation.story_interval(centered, identities, seed=20261023, draws=draws))


def interval(point, resampling):
    if point is None:
        return 'undefined'
    limits = resampling['interval_95']
    return f'{point:.3f}' + (' [undefined]' if limits is None else f' [{limits[0]:.3f}, {limits[1]:.3f}]')


def compare(outputs, destination, *, reference=REFERENCE, draws=1000):
    assert outputs
    documents = [evaluation.read(path / 'manifest.json') for path in outputs]
    first = documents[0]
    for document in documents[1:]:
        for key in ('selected', 'targets', 'groups', 'seed'):
            assert document[key] == first[key], f'Mismatched model comparison: {key}'
    models = []
    for path, document in zip(outputs, documents, strict=True):
        summary = evaluation.read(path / 'summary.json')
        metrics = evaluation.comparison_metrics(evaluation.read(path / 'predictions.json'), document, draws=draws)
        models.append(dict(model=summary['model'], provider=summary['provider'], reasoning=summary['reasoning'],
            replies=summary['replies'], planned_requests=summary['planned_requests'], note_counts=summary['note_counts'],
            returned_cost_usd=summary['returned_cost_usd'], output=str(path), **metrics))
    baseline = evaluation.good_bad_baseline(first, draws=draws)
    result = dict(author='PI/Sol', stories=len(first['selected']), seed=first['seed'], models=models,
        baseline=dict(name='good or bad only', definition='Every virtue receives the story mean of all 66 reference labels; no model calls.', **baseline),
        reference=teaching_reference(reference, draws=draws))
    destination.mkdir(parents=True, exist_ok=True)
    evaluation.save(destination / 'summary.json', result)
    rows = [(row['model'], row['overall']) for row in models] + [('*good or bad only*', baseline['overall'])]
    headers = ['Model', 'Pattern r↑ [95%]', 'r↑ [95%]', 'Flips', 'Missing', 'Cells']
    values = lambda name, row: [name, interval(row['pattern_r'], row['pattern_resampling']),
        interval(row['r'], row['r_resampling']), '—' if row['flips'] is None else row['flips'], row['missing'], row['cells']]
    overall = tabulate([values(name, row) for name, row in rows], headers=headers, tablefmt='pipe', disable_numparse=True)
    tradition_rows = []
    for school in sorted(baseline['traditions']):
        for model in models:
            tradition_rows.append([school, *values(model['model'], model['traditions'][school])])
        tradition_rows.append([school, *values('*good or bad only*', baseline['traditions'][school])])
    traditions = tabulate(tradition_rows, headers=['Tradition', *headers], tablefmt='pipe', disable_numparse=True)
    ref = result['reference']
    reference_table = tabulate([[ref['model'], interval(ref['pattern_r'], ref['pattern_resampling']),
        interval(ref['r'], ref['r_resampling']), ref['stories'], ref['cells']]],
        headers=['Separate development reference', 'Pattern r↑ [95%]', 'r↑ [95%]', 'Stories', 'Cells'],
        tablefmt='pipe', disable_numparse=True)
    (destination / 'tables.md').write_text(overall + '\n\n' + traditions + '\n\n' + reference_table + '\n')
    return result
