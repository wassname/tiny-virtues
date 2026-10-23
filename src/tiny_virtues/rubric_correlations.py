"""Paired, cross-tradition correlation summaries for the rubric gym. -- PI/Sol"""
import numpy as np


def _pair_correlations(scores, left, right):
    centered = scores - scores.mean(axis=-2, keepdims=True)
    numerator = (centered[..., left] * centered[..., right]).sum(axis=-2)
    denominator = np.sqrt((centered[..., left] ** 2).sum(axis=-2) * (centered[..., right] ** 2).sum(axis=-2))
    return np.divide(numerator, denominator, out=np.full_like(numerator, np.nan), where=denominator != 0)


def _summary(values):
    if not np.isfinite(values).all():
        return dict(mean=None, sd=None)
    return dict(mean=float(values.mean()), sd=float(values.std()) if len(values) > 1 else None)


def _bootstrap_summary(values):
    invalid = ~np.isfinite(values)
    result = dict(draws=len(values), undefined_draws=int(invalid.sum()), sd=None, interval_95=None)
    if not invalid.any():
        result.update(sd=float(values.std(ddof=1)), interval_95=np.quantile(values, [.025, .975]).tolist())
    return result


def per_rubric_model_agreement(scores, reference, items):
    scores, reference = np.asarray(scores, dtype=float), np.asarray(reference, dtype=float)
    assert scores.shape == reference.shape and scores.ndim == 2 and scores.shape[1] == len(items)
    assert len(items) == len(set(items)) and len(scores) >= 3
    assert np.isfinite(scores).all() and np.isfinite(reference).all()
    a, b = scores - scores.mean(axis=0), reference - reference.mean(axis=0)
    denominator = np.sqrt((a ** 2).sum(axis=0) * (b ** 2).sum(axis=0))
    values = np.divide((a * b).sum(axis=0), denominator, out=np.full(len(items), np.nan), where=denominator != 0)
    return dict(mean=float(values.mean()) if np.isfinite(values).all() else None,
                defined_rubrics=int(np.isfinite(values).sum()),
                per_rubric={item: float(value) if np.isfinite(value) else None for item, value in zip(items, values)},
                interpretation='Secondary Pearson agreement between model ratings across the same tales, separately for each rubric; not calibration or moral accuracy. Constant columns remain undefined.')


def paired_teaching_comparison(quotations, examples, reference, items, *, draws=5000, seed=20261020):
    quotations, examples, reference = [np.asarray(x, dtype=float) for x in (quotations, examples, reference)]
    assert quotations.shape == examples.shape == reference.shape and quotations.ndim == 2
    assert len(quotations) >= 3 and quotations.shape[1] == len(items) == len(set(items)) and draws >= 2
    assert all(np.isfinite(x).all() for x in (quotations, examples, reference))
    pairs = tradition_pairs(items)
    assert pairs, 'Need at least two different traditions'
    left, right = np.array(pairs).T
    indices = np.random.default_rng(seed).integers(0, len(quotations), size=(draws, len(quotations)))
    direct_pairs = _pair_correlations(examples, left, right) - _pair_correlations(quotations, left, right)
    direct_draws = (_pair_correlations(examples[indices], left, right) - _pair_correlations(quotations[indices], left, right)).mean(axis=-1)
    distance_reductions = (abs(quotations-reference) - abs(examples-reference)).mean(axis=1)
    return dict(cases=len(quotations), items=items,
                examples_minus_quotations_r=_summary(direct_pairs),
                quotation_distance=float(abs(quotations-reference).mean()), example_distance=float(abs(examples-reference).mean()),
                distance_reduction=float(distance_reductions.mean()),
                model_agreement={name: per_rubric_model_agreement(value, reference, items)
                                 for name, value in [('quotations', quotations), ('examples', examples)]},
                resampling=dict(seed=seed, unit='same tale rows jointly across both conditions and reference',
                                examples_minus_quotations_r=_bootstrap_summary(direct_draws),
                                distance_reduction=_bootstrap_summary(distance_reductions[indices].mean(axis=1))),
                interpretation='Direct paired condition comparison. Positive distance reduction means closer to a fallible reference, not moral accuracy. Correlation direction is not a quality ranking. Tale intervals do not measure repeat-call noise; related tales require family grouping. Undefined draws are not dropped.')


def probability_spread(probabilities):
    probabilities = np.asarray(probabilities, dtype=float)
    assert probabilities.ndim == 2 and probabilities.shape[1] == 5
    assert np.isfinite(probabilities).all() and (probabilities >= 0).all()
    sums = probabilities.sum(axis=1)
    assert (abs(sums - 1) <= .021).all()
    p = probabilities / sums[:, None]
    levels = np.linspace(-1, 1, 5)
    means = p @ levels
    variance = (p * (levels - means[:, None]) ** 2).sum(axis=1)
    log_p = np.log2(p, out=np.zeros_like(p), where=p > 0)
    return dict(vectors=len(p), mean_ordinal_sd=float(np.sqrt(variance).mean()),
                mean_entropy_bits=float(-(p * log_p).sum(axis=1).mean()),
                rounded_vectors_renormalized=int((abs(sums - 1) > 1e-9).sum()),
                interpretation='Spread of returned probabilities on -1..+1 levels, not measured repeat-call randomness or calibrated error. Rounded vectors are normalized for spread only; original judgment scores are unchanged.')


def tradition_pairs(items, *, within=False):
    """Select pairs by tradition identity, never by list membership."""
    traditions = [item.split('|')[0] for item in items]
    return [(i, j) for i in range(len(items)) for j in range(i + 1, len(items))
            if (traditions[i] == traditions[j]) == within]


def cross_tradition_correlations(scores, reference, items, *, draws=5000, seed=20261020):
    scores, reference = np.asarray(scores, dtype=float), np.asarray(reference, dtype=float)
    assert scores.shape == reference.shape and scores.ndim == 2
    assert scores.shape[0] >= 3 and scores.shape[1] == len(items)
    assert len(items) == len(set(items)) and draws >= 2
    assert np.isfinite(scores).all() and np.isfinite(reference).all()
    pairs = tradition_pairs(items)
    assert pairs, 'Need at least two different traditions'
    left, right = np.array(pairs).T
    actual = _pair_correlations(scores, left, right)
    target = _pair_correlations(reference, left, right)
    delta = actual - target
    indices = np.random.default_rng(seed).integers(0, len(scores), size=(draws, len(scores)))
    boot_actual = _pair_correlations(scores[indices], left, right)
    boot_target = _pair_correlations(reference[indices], left, right)
    # PI/Sol: identical sampled tales in every column and both models; undefined draws are never dropped.
    boot_delta = (boot_actual - boot_target).mean(axis=-1)
    pairs_readout = [dict(items=[items[i], items[j]], correlation=float(a) if np.isfinite(a) else None,
                          reference=float(t) if np.isfinite(t) else None,
                          signed_delta=float(d) if np.isfinite(d) else None)
                     for (i, j), a, t, d in zip(pairs, actual, target, delta)]
    result = dict(
        cases=len(scores), pairs=len(pairs), defined_pairs=int(np.isfinite(delta).sum()),
        correlation=_summary(actual), reference=_summary(target), signed_delta=_summary(delta),
        absolute_matrix_error=float(abs(delta).mean()) if np.isfinite(delta).all() else None,
        resampling=dict(seed=seed, unit='tale row, paired across all columns and both models',
                        correlation=_bootstrap_summary(boot_actual.mean(axis=-1)),
                        reference=_bootstrap_summary(boot_target.mean(axis=-1)),
                        signed_delta=_bootstrap_summary(boot_delta)),
        pair_values=pairs_readout,
        interpretation='A single cross-tradition pair has a correlation but no between-pair SD. Pair SD otherwise describes differences between the selected cross-tradition pairs. Resampling SD describes sensitivity to the selected tale rows, not model-repeat noise or a representative population. Related tales require family-grouped resampling before population inference. Signed delta = Jev correlation minus Opus correlation; negative means more separation, not necessarily better judgment.',
    )
    within = tradition_pairs(items, within=True)
    if within:
        left, right = np.array(within).T
        actual = _pair_correlations(scores, left, right)
        target = _pair_correlations(reference, left, right)
        result['within_tradition_pair_values'] = [
            dict(items=[items[i], items[j]], correlation=float(a) if np.isfinite(a) else None,
                 reference=float(t) if np.isfinite(t) else None,
                 signed_delta=float(a-t) if np.isfinite(a-t) else None)
            for (i, j), a, t in zip(within, actual, target)]
    return result
