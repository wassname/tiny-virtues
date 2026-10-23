"""Paired, rubric-v1-only predictions against saved rubric-v2 labels. -- PI/Sol"""
import asyncio
from collections import Counter
from contextlib import contextmanager
from decimal import Decimal
import fcntl
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import time

import numpy as np
from tabulate import tabulate

from tiny_virtues import case_flags, evaluation, item_major, llm
from tiny_virtues.case_input import INPUT_DIR, judging_story
from tiny_virtues.major_cells import story_hash
from tiny_virtues.rubric_accounting import replace_json

ROOT = item_major.ROOT
MODEL = 'deepseek/deepseek-v4.1-flash'
PROVIDER = dict(only=['deepinfra/fp8'], allow_fallbacks=False, require_parameters=True, quantizations=['fp8'])
SCOPE = 'eval_major_debug_20261023'
LEDGER = ROOT / 'data/provenance/eval_major_20261023.json'
DEBUG_LEDGER = LEDGER
FINAL_LEDGER = ROOT / 'data/provenance/eval_major_models_20261023.json'
PRICES = dict(input=.14 / 1_000_000, output=.42 / 1_000_000)
MODELS = {
    MODEL: dict(provider=dict(only=['inference-net/fp8'], allow_fallbacks=False,
                             require_parameters=True, quantizations=['fp8']),
                prices=dict(input=.103/1_000_000, output=.715/1_000_000), reasoning=dict(enabled=False)),
    'google/gemini-2.5-flash-lite': dict(
        provider=dict(only=['google-ai-studio'], allow_fallbacks=False, require_parameters=True),
        prices=dict(input=.10/1_000_000, output=.40/1_000_000), reasoning=dict(enabled=False)),
    'qwen/qwen3.5-35b-a3b': dict(provider=PROVIDER, reasoning=dict(enabled=False),
        prices=dict(input=.14/1_000_000, output=1./1_000_000)),
}
MAX_TOKENS = 1536
EXPECTED_OUTPUT_TOKENS = 700
BUDGET_LOCK = threading.RLock()


def read(path):
    return json.loads(Path(path).read_text())


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    replace_json(path, value)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def v1_virtues(virtue_ids=None):
    pairs = read(ROOT / 'data/provenance/rubrics/named_contraries_20261022.json')['pairs']
    derived = {p['vice_id'] for p in pairs}
    result = []
    for path in sorted((ROOT / 'docs/virtues/lists').glob('*.json')):
        doc = read(path)
        for listing in doc['lists']:
            if not listing['primary']:
                continue
            for kind in ('virtues', 'vices'):
                for virtue in listing.get(kind, []):
                    identity = f"{doc['school']}|{listing['key']}|{virtue['label']}"
                    if identity not in derived:
                        result.append(dict(id=identity, tradition=doc['school'], list=listing['name'],
                                           name=virtue['label'], kind=kind, gloss=virtue['gloss']))
    assert len(result) == 66 and len(derived) == 14
    if virtue_ids is not None:
        assert virtue_ids and set(virtue_ids) <= {v['id'] for v in result}, 'Unknown direct major virtue'
        result = [v for v in result if v['id'] in virtue_ids]
    return sorted(result, key=lambda v: v['id'])


def request(virtue, rows, view, *, model=MODEL):
    assert view in ('forward', 'reverse') and 0 < len(rows) <= 40
    template = read(item_major.FIVE_TEMPLATE)
    form = template['forms'][virtue['kind']][view]
    task = dict(tradition=virtue['tradition'], list=virtue['list'], virtue=virtue['name'], kind=virtue['kind'],
                definition=virtue['gloss'], question=form['question']['question'],
                evidence_rule=form['question']['evidence_rule'].replace('Use the quoted teaching', 'Use this definition and your knowledge of the named tradition'),
                options={str(i): text for i, text in enumerate(form['criteria'])})
    stories = {row['id']: dict(agent=row['person'], story=judging_story(row)) for row in rows}
    content = ('Judge each named agent independently by the named tradition, not by the narrator\'s verdict. '
               'The tradition is the judging lens; the agent need not belong to it. '
               'Judge only the selected act or disposition quoted at the start of its story. '
               'Use the full passage for motive, circumstances and consequences, not to score other acts or other characters. '
               'The same scale applies to every story. '
               'Return judgments mapping each story ID to exactly one option number (integer 0..4).\n\n'
               + json.dumps(dict(task=task, stories=stories), ensure_ascii=False))
    schema = dict(type='object', properties=dict(judgments=dict(type='object',
        properties={identity: dict(type='integer', minimum=0, maximum=4) for identity in stories},
        required=list(stories), additionalProperties=False)), required=['judgments'], additionalProperties=False)
    return dict(model=model, content=content, provider=MODELS[model]['provider'], temperature=.6,
                max_tokens=MAX_TOKENS, reasoning=MODELS[model]['reasoning'], judge_note_max_chars=320, attempts=1,
                response_format=dict(type='json_schema', json_schema=dict(name='virtue_predictions', strict=True, schema=schema)))


def packet(virtue, rows, view, *, model=MODEL):
    raw = request(virtue, rows, view, model=model)
    actual = llm.judge_request(**raw)
    return dict(key=item_major.digest(actual), virtue=virtue['id'], tradition=virtue['tradition'],
                view=view, stories=[r['id'] for r in rows], request=raw, actual_request=actual)


def parse_response(response, identities, view):
    choice = response['choices'][0]
    provider_failed = choice.get('finish_reason') == 'error' or choice.get('error') is not None
    text = choice['message']['content']
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return dict(levels={i: None for i in identities}, judge_note=None,
                    error='provider_error' if provider_failed else 'invalid_json')
    note = parsed.get('judge_note')
    if provider_failed:
        return dict(levels={i: None for i in identities}, judge_note=note if isinstance(note, str) else None, error='provider_error')
    answers = parsed.get('judgments')
    if not isinstance(note, str) or not isinstance(answers, dict):
        return dict(levels={i: None for i in identities}, judge_note=note if isinstance(note, str) else None, error='missing_judgments_or_note')
    assert view in ('forward', 'reverse')
    levels = {}
    for identity in identities:
        value = answers.get(identity)
        levels[identity] = (value if view == 'forward' else 4 - value) if type(value) is int and 0 <= value <= 4 else None
    extra = sorted(set(answers) - set(identities))
    return dict(levels=levels, judge_note=note, error='unexpected_story_ids' if extra else None, extra=extra)


def prepare(out, label_run, *, count=300, seed=20261023, source_flags=case_flags.DEFAULT_REGISTRY,
            model=MODEL, reuse=None, virtue_ids=None, input_dir=INPUT_DIR):
    assert count > 0
    input_dir = Path(input_dir)
    virtues = v1_virtues(virtue_ids)
    flags, _, flag_bindings = case_flags.load_registry(source_flags)
    held = {i for i, f in flags.items() if f['flag_held']}
    existing_path = out / 'manifest.json'
    if existing_path.exists():
        existing = read(existing_path)
        assert existing['model'] == model
        assert Path(existing['label_run']).resolve() == label_run.resolve()
        assert existing['label_manifest_sha256'] == sha(label_run / 'current.json'), 'Use a new output for a changed label cohort'
        assert existing['source_flags'] == flag_bindings and len(existing['selected']) == count and existing['seed'] == seed
        assert existing.get('virtue_ids') == (sorted(virtue_ids) if virtue_ids is not None else None)
        assert Path(existing.get('input_dir', INPUT_DIR)).resolve() == input_dir.resolve()
        virtues = {v['id']: v for v in virtues}
        checked = set()
        for name in existing['packets']:
            saved = read(name)
            condition = saved['virtue'], saved['view']
            if condition in checked:
                continue
            ids = set(saved['stories'])
            rows = [r for r in existing['selected'] if r['id'] in ids]
            assert packet(virtues[saved['virtue']], rows, saved['view'], model=model) == saved, 'Frozen evaluation request policy changed'
            checked.add(condition)
        assert len(checked) == len(virtues) * 2
        return existing
    canonical = {}
    for name in ('tinyvirtues', 'tinystoic'):
        with (input_dir / f'{name}.jsonl').open() as file:
            for row in map(json.loads, file):
                canonical[row['id']] = {key: row[key] for key in
                    ('id', 'person', 'quote', 'target_act_quote', 'source_context', 'source_header', 'source_file') if key in row}
    db = sqlite3.connect((label_run / 'cells.sqlite').resolve().as_uri() + '?mode=ro', uri=True)
    try:
        candidates = []
        for identity, input_sha, data in db.execute('SELECT id,input_sha,data FROM stories ORDER BY id'):
            row = json.loads(data)
            if identity in held or story_hash(canonical[identity]) != input_sha:
                continue
            candidates.append(row | dict(source_file=canonical[identity]['source_file'], input_sha=input_sha))
        if reuse is None:
            selected = evaluation.select(candidates, count, seed)
        else:
            prior = read(Path(reuse)/'manifest.json')['selected']
            known = {row['id']:row for row in candidates}
            assert all(row['id'] in known and row['input_sha']==known[row['id']]['input_sha'] for row in prior), 'Reused sample must still be accepted with identical judged inputs'
            selected = [known[row['id']] for row in prior[:count]]
            if count > len(prior):
                prior_ids = {row['id'] for row in prior}
                selected += evaluation.select([row for row in candidates if row['id'] not in prior_ids],count-len(prior),seed+1)
        selected_ids = {row['id'] for row in selected}
        targets = {v['id']: {} for v in virtues}
        versions = dict(db.execute('SELECT id,rubric_sha FROM virtues'))
        print(json.dumps(dict(preparing='selected_story_labels', stories=len(selected), candidates=len(candidates))), flush=True)
        placeholders = ','.join('?' for _ in selected_ids)
        query = f'''SELECT l.virtue_id,l.story_id,json_extract(l.data,'$.signed'),json_extract(l.data,'$.favorable_probabilities')
                    FROM labels l JOIN stories s ON l.story_id=s.id AND l.input_sha=s.input_sha
                    JOIN virtues v ON l.virtue_id=v.id AND l.rubric_sha=v.rubric_sha
                    WHERE l.story_id IN ({placeholders}) ORDER BY l.story_id,l.virtue_id'''
        for virtue_id, identity, signed, probabilities in db.execute(query, tuple(sorted(selected_ids))):
            if virtue_id in targets:
                probs = json.loads(probabilities)
                targets[virtue_id][identity] = dict(signed=signed, mode=int(np.argmax(probs)))
        assert all(set(values) == selected_ids for values in targets.values()), 'Reference-label coverage incomplete'
        print(json.dumps(dict(preparing='frozen_request_files', direct_virtues=len(virtues))), flush=True)
    finally:
        db.close()
    groups_path = ROOT / 'outputs/rubric_gym/nshot-all-groups/loop-4/metrics.json'
    groups = {g['group']: [i for i in g['items'] if i in targets] for g in read(groups_path)['groups']}
    groups = {name: ids for name, ids in groups.items() if len({i.split('|')[0] for i in ids}) >= 2}
    paths, characters, pending_characters, pending_requests, cached_requests, failed_requests = [], 0, 0, 0, 0, 0
    for virtue in virtues:
        for start in range(0, len(selected), 40):
            for view in ('forward', 'reverse'):
                value = packet(virtue, selected[start:start + 40], view, model=model)
                path = out / 'packets' / (value['key'] + '.json')
                if path.exists():
                    assert read(path) == value, 'Frozen evaluation packet changed'
                else:
                    save(path, value)
                paths.append(str(path))
                if reuse is not None:
                    for folder in ('replies','failures'):
                        cached = Path(reuse)/folder/(value['key']+'.json')
                        if cached.exists():
                            saved = read(cached)
                            assert saved['request'] == value['actual_request']
                            save(out/folder/cached.name,saved)
                size = len(json.dumps(value['actual_request'], ensure_ascii=False))
                characters += size
                if (out/'replies'/(value['key']+'.json')).exists():
                    cached_requests += 1
                elif (out/'failures'/(value['key']+'.json')).exists():
                    failed_requests += 1
                else:
                    pending_requests += 1; pending_characters += size
    prices = MODELS[model]['prices']
    manifest = dict(author='PI/Sol', model=model, provider=MODELS[model]['provider'], label_run=str(label_run),
        label_manifest_sha256=sha(label_run / 'current.json'), versions=versions,
        source_flags=flag_bindings, selected=selected, candidate_stories=len(candidates), seed=seed,
        selection=('Seeded round-robin over source_file books, accepted/non-held and identical frozen judged input; no label-based selection.'
                   if reuse is None else f'First {count} stories from the frozen {len(prior)}-story sample; no label-based selection.' if count <= len(prior) else
                   f'Frozen {len(prior)}-story debug sample plus a seeded, book-stratified {count-len(prior)}-story top-up; no label-based selection.'),
        prompts='One virtue per request; rubric v1 gloss only; no rubric v2 quotations or examples; both option orders.',
        targets=targets, groups=groups, group_input_sha256=sha(groups_path), packets=paths,
        pricing=dict(requests=len(paths), serialized_characters=characters, prices=prices,
            central_usd=characters * .267 * prices['input'] + len(paths) * EXPECTED_OUTPUT_TOKENS * prices['output'],
            high_usd=characters * .34 * prices['input'] + len(paths) * MAX_TOKENS * prices['output'],
            pending_requests=pending_requests, cached_requests=cached_requests, failed_requests_not_retried=failed_requests,
            pending_central_usd=pending_characters*.267*prices['input']+pending_requests*EXPECTED_OUTPUT_TOKENS*prices['output'],
            pending_high_usd=pending_characters*.34*prices['input']+pending_requests*MAX_TOKENS*prices['output']))
    if virtue_ids is not None or input_dir.resolve() != INPUT_DIR.resolve():
        manifest.update(virtue_ids=sorted(virtue_ids) if virtue_ids is not None else None, input_dir=str(input_dir))
    save(out / 'manifest.json', manifest)
    return manifest


@contextmanager
def funding():
    with BUDGET_LOCK, LEDGER.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        ledger = read(LEDGER)
        yield ledger
        replace_json(LEDGER, ledger)


def initialize_budget(cap):
    final = SCOPE == 'eval_major_models_20261023'
    assert 0 < cap <= (.01 if SCOPE == 'fast_dev_eval' else 15 if final else 5), 'Evaluation cap exceeds the approved scope'
    LEDGER.parent.mkdir(parents=True,exist_ok=True)
    with BUDGET_LOCK, LEDGER.with_suffix('.lock').open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX)
        if LEDGER.exists():
            assert read(LEDGER)['cap_usd'] == cap
            return
        save(LEDGER, dict(author='PI/Sol', cap_usd=cap,
            funding_status=dict(new_paid_calls='approved', scope=SCOPE,
                allowed_requests=[dict(model=model, endpoint='chat/completions') for model in (MODELS if final else [MODEL])],
                reason=('Supervisor336: two-story, one-virtue runtime smoke under one cent.' if SCOPE == 'fast_dev_eval' else
                        'wassname v3 goals: three matched story samples under $15, including the debug budget.' if final else
                        'wassname via supervisor305/321: run the 300-story rubric-v1-only debug sample under $5.')),
            charges={}, inflight={}, **(dict(external_debug_ledger=str(DEBUG_LEDGER), completed_models={}) if final else {})))


def committed_budget(ledger):
    if 'external_debug_ledger' not in ledger:
        return sum(Decimal(str(c['cost'])) for c in ledger['charges'].values())
    external = read(ledger['external_debug_ledger'])
    external_bill = sum(Decimal(str(c['cost'])) for c in external['charges'].values())
    external_reserved = sum(Decimal(str(c)) for c in external['inflight'].values())
    finished = external.get('run_status') in ('all_planned_attempts_finished','stopped_with_missing')
    external_bound = external_bill + external_reserved if finished else Decimal(str(external['cap_usd']))
    own = sum(Decimal(str(c['cost'])) for identity,c in ledger['charges'].items() if identity not in external['charges'])
    return own + external_bound


def fetch(value, out, *, allow_paid, request_delay=0.0):
    key = value['key']
    path = out / 'replies' / (key + '.json')
    failed = out / 'failures' / (key + '.json')
    if failed.exists() and not path.exists():
        return None
    if not path.exists():
        assert allow_paid, 'Uncached evaluation request; --allow-paid is required'
        if request_delay:
            time.sleep(request_delay)
        req = value['actual_request']
        prices = MODELS[value['request']['model']]['prices']
        allowance = Decimal(str((len(json.dumps(req, ensure_ascii=False).encode()) + 1024) * prices['input'] + req['max_tokens'] * prices['output']))
        with funding() as ledger:
            assert not key in ledger['inflight'], 'Interrupted HTTP request has unknown billing; reconcile before retry'
            total = committed_budget(ledger)
            reserved = sum(Decimal(str(v)) for v in ledger['inflight'].values())
            assert total + reserved + allowance < Decimal(str(ledger['cap_usd'])), 'Evaluation cost cap reached before HTTP'
            ledger['inflight'][key] = str(allowance)
        def persist(actual, response):
            assert actual == value['actual_request']
            save(path, dict(key=key, request=actual, response=response))
            record_charge(key, response)
        try:
            llm.judge_chat(**value['request'], funding_scope=SCOPE, on_response=persist)
        except llm.ChatFundingBlocked as error:
            with funding() as ledger:
                ledger['inflight'].pop(key)
            save(out/'funding_blocked'/(key+'.json'),dict(key=key,request=value['actual_request'],
                attempt=error.record,status='not_attempted_account_credits',billing_status='rejected_before_inference'))
            raise
        except llm.ChatRateLimited as error:
            with funding() as ledger:
                ledger['inflight'].pop(key)
            save(failed, dict(key=key, request=value['actual_request'], attempt=error.record,
                             status='rate_limited_no_retry', billing_status='rejected_before_inference'))
            return None
        except llm.ChatRequestFailed as error:
            save(failed, dict(key=key, request=value['actual_request'], attempt=error.record,
                             status='request_failed_no_retry', billing_status='reservation_retained'))
            return None
        except (ValueError, KeyError, AssertionError):
            if not path.exists():
                raise
    saved = read(path)
    assert saved['key'] == key and saved['request'] == value['actual_request']
    record_charge(key, saved['response'])
    return saved['response']


def recover_failed(out):
    destination = out / 'recovery' / 'slow_pass_1'
    assert not destination.exists(), 'The single slow recovery pass has already been prepared'
    rows = []
    with funding() as ledger:
        for path in sorted((out / 'failures').glob('*.json')):
            failure = read(path)
            key = failure['key']
            assert path.stem == key == item_major.digest(failure['request'])
            assert not (out / 'replies' / (key + '.json')).exists(), 'Do not retry a returned model reply'
            status = failure['attempt']['http_status']
            assert status in (429, 503), f'Unclassified failed request: HTTP {status}'
            amount = ledger['inflight'][key]
            retained_key = key + ':unknown-before-slow-pass' if status == 503 else None
            assert retained_key is None or retained_key not in ledger['inflight']
            rows.append(dict(key=key, source=str(path), archive=str(destination/'failures'/path.name),
                             http_status=status, reservation_usd=amount, retained_key=retained_key))
        assert rows, 'No failed requests to recover'
        save(destination/'plan.json', dict(author='PI/Sol', status='prepared', requests=rows))
        for row in rows:
            amount = ledger['inflight'].pop(row['key'])
            if row['retained_key'] is not None:
                ledger['inflight'][row['retained_key']] = amount
            target = Path(row['archive'])
            target.parent.mkdir(parents=True, exist_ok=True)
            Path(row['source']).rename(target)
    result = dict(author='PI/Sol', status='ready_for_one_slow_pass', requests=len(rows),
                  rejected_429=sum(row['http_status'] == 429 for row in rows),
                  retained_unknown_503=sum(row['http_status'] == 503 for row in rows))
    save(destination/'result.json', result)
    return result


def record_charge(key, response):
    identity, cost = response['id'], response['usage']['cost']
    assert Decimal(str(cost)).is_finite() and cost >= 0
    with funding() as ledger:
        record = dict(key=key, cost=cost)
        if identity in ledger['charges']:
            assert ledger['charges'][identity] == record
        else:
            ledger['charges'][identity] = record
        ledger['inflight'].pop(key, None)


def correlation(a, b):
    if len(a) < 2 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


def story_interval(rows, identities, *, seed, draws=1000):
    grouped = {}
    for row in rows:
        grouped.setdefault(row['id'], []).append((row['a'], row['b']))
    width = max((len(values) for values in grouped.values()), default=0)
    if width == 0:
        return dict(interval_95=None, undefined_draws=draws, draws=draws)
    a = np.full((len(identities), width), np.nan)
    b = np.full_like(a, np.nan)
    for i, identity in enumerate(identities):
        values = grouped.get(identity, [])
        if values:
            a[i, :len(values)], b[i, :len(values)] = np.array(values).T
    rng = np.random.default_rng(seed)
    sampled, undefined = [], 0
    for _ in range(draws):
        chosen = rng.integers(0, len(identities), len(identities))
        left, right = a[chosen].ravel(), b[chosen].ravel()
        valid = np.isfinite(left) & np.isfinite(right)
        value = correlation(left[valid], right[valid])
        if value is None:
            undefined += 1
        else:
            sampled.append(value)
    return dict(interval_95=None if undefined else np.quantile(sampled, [.025, .975]).tolist(),
                undefined_draws=undefined, draws=draws)


def comparison_metrics(comparisons, manifest, *, draws=1000):
    selected_ids = [r['id'] for r in manifest['selected']]
    seed = manifest.get('seed', 20261023)
    schools = sorted({r['tradition'] for r in comparisons})
    metrics = {}
    for number, school in enumerate(schools + ['all']):
        rows = [r for r in comparisons if school == 'all' or r['tradition'] == school]
        valid = [r for r in rows if not r['missing']]
        metrics[school] = dict(
            r=correlation([r['prediction'] for r in valid], [r['target'] for r in valid]),
            mae=float(np.mean([abs(r['prediction'] - r['target']) for r in valid])) if valid else None,
            exact=None if any(r['exact'] is None for r in rows) else sum(r['exact'] for r in rows) / len(rows),
            flips=None if any(r['flipped'] is None for r in rows) else sum(r['flipped'] for r in rows),
            missing=sum(r['missing'] for r in rows), cells=len(rows),
            r_resampling=story_interval([dict(id=r['id'], a=r['prediction'], b=r['target']) for r in valid],
                                       selected_ids, seed=seed + number, draws=draws))
    pattern, residuals = {}, {school: [] for school in metrics}
    lookup = {(r['id'], r['virtue']): r for r in comparisons}
    assert len(lookup) == len(comparisons), 'Duplicate story/virtue comparison'
    for number, (name, ids) in enumerate(manifest['groups'].items()):
        pairs, kept = [], 0
        for story in manifest['selected']:
            rows = [lookup[(story['id'], virtue)] for virtue in ids]
            if any(r['missing'] for r in rows):
                continue
            a, b = np.array([r['prediction'] for r in rows]), np.array([r['target'] for r in rows])
            # Constant predictions have exactly zero differences, including the story-mean baseline. -- PI/Sol
            left = np.zeros_like(a) if np.ptp(a) == 0 else a - a.mean()
            right = np.zeros_like(b) if np.ptp(b) == 0 else b - b.mean()
            kept += 1
            for row, x, y in zip(rows, left, right, strict=True):
                pair = dict(id=story['id'], a=float(x), b=float(y))
                residuals[row['tradition']].append(pair)
                residuals['all'].append(pair)
                pairs.append(pair)
        pattern[name] = dict(r=correlation([r['a'] for r in pairs], [r['b'] for r in pairs]), stories=kept, virtues=ids,
            resampling=story_interval(pairs, selected_ids, seed=seed + 100 + number, draws=draws))
    for number, (school, rows) in enumerate(residuals.items()):
        metrics[school].update(pattern_r=correlation([r['a'] for r in rows], [r['b'] for r in rows]),
            pattern_cells=len(rows), pattern_stories=len({r['id'] for r in rows}),
            pattern_resampling=story_interval(rows, selected_ids, seed=seed + 200 + number, draws=draws))
    return dict(traditions={school: metrics[school] for school in schools}, overall=metrics['all'], same_pattern=pattern)


def good_bad_baseline(manifest, *, draws=1000):
    comparisons = []
    for story in manifest['selected']:
        identity = story['id']
        mean = float(np.mean([labels[identity]['signed'] for labels in manifest['targets'].values()]))
        for virtue, labels in manifest['targets'].items():
            comparisons.append(dict(id=identity, virtue=virtue, tradition=virtue.split('|')[0],
                target=labels[identity]['signed'], prediction=mean, missing=False, exact=None, flipped=None))
    return comparison_metrics(comparisons, manifest, draws=draws)


def summarize(out):
    manifest = read(out / 'manifest.json')
    records, notes = [], []
    for path in manifest['packets']:
        value = read(path)
        reply = out / 'replies' / (value['key'] + '.json')
        if reply.exists():
            saved = read(reply)
            assert saved['request'] == value['actual_request']
            parsed = parse_response(saved['response'], value['stories'], value['view'])
            notes.append(dict(key=value['key'], view=value['view'], virtue=value['virtue'],
                              judge_note=parsed['judge_note'], error=parsed['error'], reply=str(reply),
                              response_id=saved['response']['id'], cost_usd=saved['response']['usage']['cost']))
        else:
            parsed = dict(levels={i: None for i in value['stories']})
        for identity, level in parsed['levels'].items():
            target = manifest['targets'][value['virtue']][identity]
            records.append(dict(id=identity, virtue=value['virtue'], tradition=value['tradition'], view=value['view'],
                                level=level, prediction=None if level is None else (level - 2) / 2, **target))
    paired = {}
    for r in records:
        paired.setdefault((r['id'], r['virtue']), {})[r['view']] = r
    comparisons = []
    for (identity, virtue), views in paired.items():
        a, b = views['forward'], views['reverse']
        complete = a['level'] is not None and b['level'] is not None
        comparisons.append(dict(id=identity, virtue=virtue, tradition=a['tradition'], target=a['signed'],
            mode=a['mode'], prediction=(a['prediction'] + b['prediction']) / 2 if complete else None,
            exact=complete and a['level'] == b['level'] == a['mode'],
            flipped=complete and a['level'] != b['level'], missing=not complete))
    metrics = comparison_metrics(comparisons, manifest)
    tradition_metrics, pattern = metrics['traditions'], metrics['same_pattern']
    complete = [r for r in comparisons if not r['missing']]
    worst = sorted(complete, key=lambda r: -abs(r['prediction'] - r['target']))[:30]
    ledger_path = Path(read(out/'funding.json')['ledger']) if (out/'funding.json').exists() else LEDGER
    ledger = read(ledger_path) if ledger_path.exists() else dict(charges={}, inflight={})
    bills = {note['response_id']:note['cost_usd'] for note in notes}
    result = dict(author='PI/Sol', model=manifest['model'], provider=manifest['provider'],
                  reasoning=read(manifest['packets'][0])['request']['reasoning'], stories=len(manifest['selected']),
                  **metrics, largest_disagreements=worst,
                  returned_cost_usd=str(sum(Decimal(str(cost)) for cost in bills.values())),
                  shared_budget_committed_usd=str(committed_budget(ledger)),
                  failed_requests=sum((out/'failures'/(Path(path).stem+'.json')).exists() for path in manifest['packets']),
                  replies=len(notes), planned_requests=len(manifest['packets']),
                  note_counts=dict(Counter('missing' if n['judge_note'] is None else 'empty' if n['judge_note'] == '' else 'text' for n in notes),
                                   unreturned=len(manifest['packets'])-len(notes)),
                  returned_errors=dict(Counter(n['error'] for n in notes if n['error'] is not None)),
                  inflight=ledger['inflight'])
    save(out / 'notes.json', notes)
    save(out / 'predictions.json', comparisons)
    save(out / 'summary.json', result)
    fmt = lambda value: 'undefined' if value is None else f'{value:.3f}'
    def interval(point, resampling):
        limits = resampling['interval_95']
        return fmt(point) + (f' [{limits[0]:.3f}, {limits[1]:.3f}]' if limits is not None else ' [undefined]')
    table = tabulate([[k, interval(v['pattern_r'], v['pattern_resampling']), interval(v['r'], v['r_resampling']),
                      fmt(v['mae']), fmt(v['exact']), v['flips'], v['missing'], v['cells']]
        for k, v in tradition_metrics.items()], headers=['Tradition', 'Pattern r↑ [95%]', 'r↑ [95%]', 'MAE↓', 'Exact↑', 'Flips', 'Missing', 'Cells'], tablefmt='pipe', disable_numparse=True)
    mode = 'non-thinking' if result['reasoning'].get('enabled') is False else 'reasoning'
    text = (f'# Paired major-virtue evaluation\n\nModel: `{manifest["model"]}`. Provider: `{", ".join(manifest["provider"]["only"])}`. Mode: {mode}.\n\n'
            + f'{len(notes)}/{len(manifest["packets"])} requests returned. '
            + ('Complete run.\n\n' if len(notes) == len(manifest['packets']) else 'Partial run.\n\n')
            + "The model sees each story with one virtue's one-line description (rubric v1); the reference labels come from rubric v2. "
            "Each question is asked twice, with the answer options in opposite orders, and the two answers are averaged. "
            "'Flips' = the two orders gave different answers. 'r' = correlation with the reference labels. "
            "'Pattern r' = the same after removing each story's average over a group of related virtues, "
            "i.e. does the model put the differences between traditions where the labels do. "
            'Intervals: 95%, resampling stories (1,000 draws).\n\n'
            + table + '\n\n<small>Exact: both orders match the reference mode (ties: lower level). '
            'Missing pairs count against exact; r/MAE use complete pairs. Undefined draws invalidate an interval. '
            'Retellings are resampled as stories, not independent books. Flips mix order effects and sampling.</small>\n\n'
            '## Related-virtue groups\n\nPer-tradition pattern r uses that tradition’s centered scores across these groups. '
            'Only directly judged major virtues enter the comparison; none is averaged into an overall moral verdict.\n\n'
            + tabulate([[k, interval(v['r'], v['resampling']), v['stories'], len(v['virtues'])] for k, v in pattern.items()],
                headers=['Group', 'Pattern r↑ [95%]', 'Stories', 'Virtues'], tablefmt='pipe', disable_numparse=True)
            + f'\n\nReturned bill: ${result["returned_cost_usd"]}; {len(notes)}/{len(manifest["packets"])} requests. '
            'Full requests, replies, notes and Inspect .eval files are in this output directory.\n')
    (out / 'results.md').write_text(text)
    return result


def run(out, *, allow_paid=False, limit=None, threads=4, budget=4.99, final=False,
        funding_scope=None, funding_ledger=None, request_delay=0.0):
    from inspect_ai import Task, eval
    from inspect_ai.dataset import Sample
    from inspect_ai.model import ChatMessageAssistant, ModelOutput
    from inspect_ai.solver import solver
    global LEDGER, SCOPE
    assert 0 <= request_delay <= 60, 'Inter-request delay must be between zero and sixty seconds'
    manifest = read(out / 'manifest.json')
    if funding_scope is not None or funding_ledger is not None:
        assert funding_scope == 'fast_dev_eval' and funding_ledger is not None and not final
        assert len(manifest['selected']) <= 2 and len(manifest['targets']) == 1, 'Tiny funding scope requires two stories and one virtue'
        LEDGER, SCOPE = Path(funding_ledger), funding_scope
    elif final:
        LEDGER, SCOPE = FINAL_LEDGER, 'eval_major_models_20261023'
    initialize_budget(budget)
    save(out/'funding.json',dict(ledger=str(LEDGER),scope=SCOPE,cap_usd=budget,
                               threads=threads,request_delay_seconds=request_delay))
    llm.FUNDING_LEDGER = LEDGER
    paths = manifest['packets'][:limit] if limit is not None else manifest['packets']
    # One virtue at a time bounds Inspect's retained prompt/trace buffers. -- PI/Sol
    groups = {}
    for path in paths:
        groups.setdefault(read(path)['virtue'], []).append(path)
    @solver
    def predict():
        async def solve(state, generate):
            value = read(state.metadata['packet_path'])
            response = await asyncio.to_thread(fetch, value, out, allow_paid=allow_paid, request_delay=request_delay)
            completion = response['choices'][0]['message']['content'] or '' if response is not None else ''
            state.messages.append(ChatMessageAssistant(content=completion, model=manifest['model']))
            state.output = ModelOutput.from_content(manifest['model'], completion)
            state.store.set('exact_request', value['actual_request'])
            state.store.set('raw_response', response)
            parsed = (parse_response(response, value['stories'], value['view']) if response is not None else
                      dict(levels={identity:None for identity in value['stories']}, judge_note=None, error='request_failed'))
            state.store.set('parsed', parsed)
            if response is None:
                state.store.set('transport_failure', read(out/'failures'/(value['key']+'.json')))
            return state
        return solve
    for virtue, own in groups.items():
        samples = [Sample(id=read(path)['key'], input=read(path)['actual_request']['content'],
                          metadata=dict(packet_path=path)) for path in own]
        logs = eval(Task(name='major_virtue_' + virtue.split('|')[0], dataset=samples, solver=predict(),
                    metadata=dict(evaluated_model=manifest['model'], adapter='llm.judge_chat; mockllm is only the Inspect task host',
                                  rubric_v2_labels_not_in_prompt=True, virtue=virtue)),
                    model='mockllm/model', log_dir=str(out / 'inspect'), display='none', max_samples=threads,
                    fail_on_error=True, retry_on_error=0, log_model_api=True)
        assert all(log.status == 'success' for log in logs), 'Inspect recorded an evaluation failure; inspect the retained reply before resuming'
        print(json.dumps(dict(virtue=virtue, inspect=[log.location for log in logs])), flush=True)
    if limit is None:
        with funding() as ledger:
            if final:
                ledger['completed_models'][manifest['model']] = str(out)
                if len(ledger['completed_models']) == len(MODELS):
                    ledger['run_status'] = 'all_planned_attempts_finished'
            else:
                ledger['run_status'] = 'all_planned_attempts_finished'
    return summarize(out)
