# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "loguru==0.7.3"]
# ///
"""Label the 80 major virtues with rubric v2, using 66 direct five-level questions per story.
One Jev request per virtue; hybrid feedback and cell-level resume are the defaults.
Estimate without credentials; slice/run require explicit --allow-paid and approved funding.
The historical description-only CLI requires --legacy-descriptions.
-- PI/Sol"""
import argparse, hashlib, json, os, random, sys, threading, time, traceback, uuid
from datetime import datetime, timezone
from loguru import logger
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from tiny_virtues.item_major import build_request as build_item_major_request, project_example

if __name__ == '__main__':
    if '--legacy-descriptions' in sys.argv:
        sys.argv.remove('--legacy-descriptions')
    else:
        from tiny_virtues.major_label import main as major_main
        major_main(sys.argv[1:])
        raise SystemExit(0)

from tiny_virtues import llm
from tiny_virtues.judge_feedback import coverage, validate_record
from tiny_virtues.case_input import INPUT_DIR, judging_story, require_selected_target
from tiny_virtues.judge_traces import decision_trace

T = tuple(os.environ.get('TRADITIONS', 'stoic,confucian,taoist,buddhist,christian').split(','))
NAME = dict(stoic='Stoic tradition', confucian='Confucian tradition', taoist='Taoist tradition', buddhist='Buddhist tradition', christian='Christian tradition', jewish='Jewish tradition', islamic='Islamic tradition', hindu='Hindu tradition', rationalist='Yudkowsky rationalist lens')
RUBRIC = {s: json.loads((ROOT / f'docs/virtues/lists/{s}.json').read_text()) for s in T}
REP = int(os.environ.get('REP', 1))
OUT = ROOT / os.environ.get('OUTPUT', f"private/labels/v5_labels{'' if REP == 1 else f'_rep{REP}'}.jsonl")
if REP != 1:
    llm.JEV_CACHE = ROOT / f'.local/cache/labeller_rep{REP}'
    llm.jev_path = lambda k: llm.JEV_CACHE / k[:2] / f'{k}.json.gz'
REQUEST_LOG = ROOT / os.environ.get('REQUEST_LOG', 'private/labels/v5_requests.jsonl')
FUNDING_LEDGER = ROOT / 'data/provenance/v5_relabel_budget_20261021.json'  # own scope; the gym ledger funds rubric studies only -- PI/Claude-opus
FUNDING_SCOPE = json.loads(FUNDING_LEDGER.read_text())['funding_status']['scope']
INVOCATION = uuid.uuid4().hex
CHUNK = 40  # questions per request (the 155-item Ganying Pian timed out as one request)
TASK = ("You are doing a moral labelling task. Each question names a moral tradition and one of its principles (a virtue or vice) with that tradition's own description, "
        "or asks for the tradition's overall verdict. Judge the act of the agent named in \"agent\", in the story, by that tradition's own standards, "
        "which often differ from modern common sense and from what the story's author says. If the story gives its author's or narrator's verdict or argues for one, "
        "or you recognise the story, do not adopt that view: judge the act as written. ... marks cut text and [brackets] mark words an editor added. "
        "Read the agent's most likely motive from the act, any stated reason, their role and the setting.")
OPTS = {'well': 'This tradition counts this act as a virtue.', 'badly': 'This tradition counts this act as a vice.', 'neither': 'This tradition counts this act as neither a virtue nor a vice.'}


def item_head(school, L, side, x, names):
    head = f'The {NAME[school]}, list "{L["name"]}": the {names[side]} {x["label"]}, described as: "{x["gloss"]}"\n'
    if 'gloss_attribution' in x:
        head += f'Description source: {x["gloss_attribution"]}\n'
    if 'applicability' in L:
        head += f'Applicability: {L["applicability"]}\n'
    if 'source_context' in L:
        head += f'Source context: {L["source_context"]}\n'
    return head


def source_conjunction(head, tail, L):
    condition = ('the applicability restriction quoted above applies to this agent' if 'applicability' in L else
        "the named agent's conduct or disposition concerns the specific subject matter, people and circumstances of the quoted description")
    return dict(type='noul', instructions=head +
        f'Does the story establish BOTH of the following for the named agent: (1) {condition}; AND '
        '(2) the following original item test is satisfied? Original item test: ' + tail +
        ' Both conditions must have story evidence; satisfying the conduct test alone is insufficient.')


def item_question(head, tail, L):
    if 'applicability' in L:
        tail += ' Count evidence only when the story establishes the stated applicability for this agent; otherwise there is no evidence for this role-limited item.'
        return source_conjunction(head, tail, L)
    return dict(type='noul', instructions=head + tail)


def questions(school):
    """Question id -> Jev question. Ids: '<school>|verdict_r<i>' and '<school>|<list key>|<+|~|-><item label>'."""
    name, k = NAME[school], list(OPTS)
    verdict = f'The {name}: {RUBRIC[school]["tradition"]}\nBy the {name}, is what the agent does a virtue, a vice, or neither?'
    qs = {f'{school}|verdict_r{i}': dict(type='choice', instructions=verdict, criteria={x: OPTS[x] for x in k[i:] + k[:i]}) for i in range(3)}
    for L in sorted(RUBRIC[school]['lists'], key=lambda L: not L['primary']):
        names = {'virtues': 'virtue', 'vices': 'vice'} | L.get('side_names', {})
        for side in ('virtues', 'vices'):
            for x in L.get(side, []):
                head = item_head(school, L, side, x, names)
                q = f"{school}|{L['key']}|"
                if side == 'virtues':
                    qs[q + '+' + x['label']] = item_question(head, f"Does the agent's act show this {names[side]} as described? Yes only if the story gives evidence of the conduct or disposition described, including any positive practice it names. For an abstention, the relevant matter must arise in the story; mere absence of the opposite act is not evidence.", L)
                    if not L.get('vices'):
                        qs[q + '~' + x['label']] = item_question(head, f"Does the agent's act go against this {names[side]}? Yes only if the story shows the agent acting against it.", L)
                else:
                    qs[q + '-' + x['label']] = item_question(head, f"Does the agent's act show this {names[side]}? Yes only if the story shows the agent doing it.", L)
    return qs


def missing_reverse_questions(school):
    """Append the previously missing questions without rebatching cached forward requests. -- PI/OpenAI"""
    qs = {}
    for L in sorted(RUBRIC[school]['lists'], key=lambda L: not L['primary']):
        if not L.get('vices'):
            continue
        names = {'virtues': 'virtue', 'vices': 'vice'} | L.get('side_names', {})
        for side in ('virtues', 'vices'):
            for x in L.get(side, []):
                head = item_head(school, L, side, x, names)
                if side == 'virtues':
                    question = item_question(head, f"Does the agent's act go against this {names[side]}? Yes only if the story shows the agent acting against it.", L)
                else:
                    question = source_conjunction(head,
                        f'Does the story give evidence that the named agent resists or acts contrary to "{x["label"]}", as described above? '
                        'Preserve the description\'s roles, relationships, conditions and scope. '
                        'If a stated applicability restriction is not established for this agent, there is no evidence for this item. '
                        'Expressly described refusal, avoidance or a contrary disposition can count. '
                        'Violating a quoted prohibition is not resisting the prohibited conduct. '
                        'Unrelated good conduct, another agent\'s act, and mere non-mention of the vice are not evidence.', L)
                qs[f"{school}|{L['key']}|~{x['label']}"] = question
    return qs


for school in T:  # PI/OpenAI: reject duplicates in the source before dictionaries could overwrite them.
    keys = [L['key'] for L in RUBRIC[school]['lists']]
    assert len(keys) == len(set(keys)), school
    for L in RUBRIC[school]['lists']:
        labels = [x['label'] for side in ('virtues', 'vices') for x in L.get(side, [])]
        assert len(labels) == len(set(labels)), (school, L['key'])
QS = [(k, q) for s in T for k, q in questions(s).items()]
QS += [(k, q) for s in T for k, q in missing_reverse_questions(s).items()]
assert len(QS) == len({k for k, _ in QS})
if REP != 1:  # PI/OpenAI: measure sensitivity to batching, not independent sampling noise.
    random.Random(REP).shuffle(QS)
QSHA = hashlib.sha256(json.dumps([TASK, CHUNK, QS], ensure_ascii=False).encode()).hexdigest()[:12]
RUN_LOG = None
DEMO_WRITTEN = False
RUN_BUDGET_USD = float(os.environ.get('RUN_BUDGET_USD', 'inf'))
RUN_SPENT_USD = 0.0
RUN_CANCELLED = threading.Event()
lock = threading.Lock()
load = lambda p: [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


def state(r):
    return dict(task=TASK, agent=r['person'], story=judging_story(r))


def identity(r):
    """Resume only the same state, scored questions, feedback channel, batching and model. -- PI/OpenAI"""
    return (r['id'], hashlib.sha256(json.dumps(state(r), ensure_ascii=False).encode()).hexdigest(), QSHA, llm.JEV, llm.FEEDBACK_SHA)


def label(r):
    global DEMO_WRITTEN, RUN_SPENT_USD
    a, cost, served, requests, feedback = {}, 0.0, set(), [], []
    for c in range(0, len(QS), CHUNK):
        if RUN_CANCELLED.is_set():
            raise RuntimeError('Run stopped after another case failed; paid request cache retained')
        with lock:
            if RUN_SPENT_USD >= RUN_BUDGET_USD:
                raise RuntimeError(f'Run budget reached: ${RUN_SPENT_USD:.5f}; paid requests remain cached')
        current_state, questions = state(r), dict(QS[c:c + CHUNK])
        request = llm.jev_request(current_state, questions)
        historical = dict(model=llm.JEV, state=current_state, questions=questions)
        if not any(llm.jev_path(llm.jev_key(value)).exists() for value in (request, historical)):
            require_selected_target(r)
        x = llm.jev(current_state, questions, funding_scope=FUNDING_SCOPE)
        if RUN_LOG is not None and c == 0:
            with lock:
                if not DEMO_WRITTEN:
                    demo = dict(request=x['request'], answers=x['answers'], judge_feedback=x['judge_feedback'],
                                feedback_sha=x['feedback_sha'], served=x['served'], cost=x['cost'], cache_hit=x['cache_hit'])
                    trace = llm.jev_path(x['key']).relative_to(ROOT)
                    logger.debug("## labelling -> real request and answer\n\n```json\n" + json.dumps(demo, ensure_ascii=False, indent=2) + f"\n```\n\nFull cache record: `{trace}`.\n")
                    DEMO_WRITTEN = True
        a |= x['answers']
        cost += x['cost'] * (not x.get('cache_hit'))
        served.add(x['served'])
        requests.append(x['key'])
        feedback.append(dict(request_key=x['key']) | x['judge_feedback'])
        if x['judge_feedback']['status'] == 'recorded' and x['judge_feedback']['answer']['choice'] != 'none':
            logger.debug(f"Judge feedback: {r['id']} chunk {c // CHUNK + 1}: {x['judge_feedback']['answer']['choice']}; request {x['key']}\n")
        with lock, REQUEST_LOG.open('a') as f:
            f.write(json.dumps(dict(invocation=INVOCATION, row=r['id'], qsha=QSHA, labeller=llm.JEV, key=x['key'],
                                   response_id=x['response']['id'] if 'id' in x['response'] else None,
                                   recorded_at=datetime.now(timezone.utc).isoformat(),
                                   feedback_sha=x['feedback_sha'], judge_feedback=x['judge_feedback'],
                                   cache_hit=x['cache_hit'], cost=x['cost'] if not x['cache_hit'] else 0.0), ensure_ascii=False) + '\n')
            RUN_SPENT_USD += x['cost'] if not x['cache_hit'] else 0.0
            if RUN_SPENT_USD > RUN_BUDGET_USD:
                raise RuntimeError(f'Run budget exceeded: ${RUN_SPENT_USD:.5f}; ledger and paid request cache retained')
        if RUN_LOG is not None:
            trace = decision_trace(RUN_LOG.parent, dict(id=f"{r['id']}-{c:04}", request=dict(state=current_state, questions=questions)), x)
            logger.info('Complete judge trace: {}', trace['path'])
    out = dict(id=r['id'], state_sha=identity(r)[1], quote_sha=hashlib.sha256(r['quote'].encode()).hexdigest()[:12], qsha=QSHA,
               labeller=llm.JEV, served=sorted(served), rep=REP, cost=cost, request_keys=requests,
               feedback_sha=llm.FEEDBACK_SHA, feedback_policy='required_on_new_calls', feedback_status=coverage(feedback), judge_feedback=feedback)
    validate_record(out, expected_sha=llm.FEEDBACK_SHA, expected_requests=-(-len(QS) // CHUNK), options=llm.FEEDBACK_QUESTION['criteria'], allow_historical=True)
    for s in T:
        p = {k: sum(a[f'{s}|verdict_r{i}']['probabilities'][k] for i in range(3)) / 3 for k in OPTS}
        lists = {}
        for qid, v in a.items():
            school, key, item = qid.split('|', 2) if qid.count('|') >= 2 else (None, None, None)
            if school != s:
                continue
            lists.setdefault(key, {}).setdefault(item[1:], {})[{'+': 'shows', '~': 'against', '-': 'shows_vice'}[item[0]]] = round(v['noul'], 4)
        for items in lists.values():
            for d in items.values():
                d['signed'] = round(d['against'] - d['shows_vice'] if 'shows_vice' in d else d['shows'] - d['against'], 4)
        out[s] = dict(p={k: round(v, 4) for k, v in p.items()}, label=max(p, key=p.get), lists=lists)
    with lock, OUT.open('a') as f:
        f.write(json.dumps(out, ensure_ascii=False) + '\n')
    return out


def main():
    llm.FUNDING_LEDGER = FUNDING_LEDGER  # set only when labelling runs, so importing this module leaves other ledgers alone
    RUN_CANCELLED.clear()
    started = time.monotonic()
    rows = [r for f in ('tinyvirtues', 'tinystoic') for r in load(INPUT_DIR / f'{f}.jsonl') if r['quote'] and r['person']]
    if os.environ.get('ROWS') == 'dev':
        dev = set(json.loads((ROOT / 'data/splits/v5_dev_ids.json').read_text())['ids'])
        assert dev <= {r['id'] for r in rows}, 'Development split contains unknown case IDs'
        rows = [r for r in rows if r['id'] in dev]
    if 'ID_FILE' in os.environ:
        selected = set(json.loads((ROOT / os.environ['ID_FILE']).read_text())['ids'])
        assert selected and selected <= {r['id'] for r in rows}, 'Selection contains unknown or unquoted case IDs'
        rows = [r for r in rows if r['id'] in selected]
    done = set()
    for x in load(OUT):
        if 'feedback_sha' in x and x['feedback_sha'] == llm.FEEDBACK_SHA and x['qsha'] == QSHA and x['labeller'] == llm.JEV:
            validate_record(x, expected_sha=llm.FEEDBACK_SHA, expected_requests=-(-len(QS) // CHUNK), options=llm.FEEDBACK_QUESTION['criteria'], allow_historical=True)
            done.add((x['id'], x['state_sha'], x['qsha'], x['labeller'], x['feedback_sha']))
    todo = [r for r in rows if identity(r) not in done][:int(os.environ.get('LIMIT', 10 ** 9))]
    OUT.parent.mkdir(exist_ok=True)
    logger.info(f'## labelling\n\n{len(QS)} questions per case in {-(-len(QS) // CHUNK)} requests. {len(todo)} selected cases; {len(rows)} quoted inputs.\n')
    n = [0]

    def go(r):
        if RUN_CANCELLED.is_set():
            raise RuntimeError('Run stopped after another case failed')
        try:
            x = label(r)
        except Exception:
            with lock:
                if not RUN_CANCELLED.is_set():
                    logger.error(f'## Failed case {r["id"]}\n\n```text\n{traceback.format_exc()}\n```\n')
                    RUN_CANCELLED.set()
            raise
        with lock:
            n[0] += 1
            count = n[0]
        if count % 100 == 0:
            logger.info(f'{count}/{len(todo)} selected cases labelled.\n')
        return x
    ex = ThreadPoolExecutor(int(os.environ.get('THREADS', 16)))
    try:
        futures = [ex.submit(go, r) for r in todo]
        res = [future.result() for future in as_completed(futures)]
    except Exception:
        RUN_CANCELLED.set()
        ex.shutdown(wait=False, cancel_futures=True)
        raise
    else:
        ex.shutdown(wait=True)
    current = done | {(x['id'], x['state_sha'], x['qsha'], x['labeller'], x['feedback_sha']) for x in res}
    complete = sum(identity(r) in current for r in rows)
    entries = [entry for row in res for entry in row['judge_feedback']]
    feedback_words = [entry['answer']['choice'] for entry in entries if entry['status'] == 'recorded']
    logger.info(f"""## Result

| measure | value |
|:--|--:|
| cases labelled this run | {len(res)} |
| current labelled cases | {complete} |
| quoted inputs | {len(rows)} |
| judge calls with saved feedback | {len(feedback_words)} |
| historical cached calls without feedback | {sum(entry['status'] == 'not_collected' for entry in entries)} |
| feedback choices other than none | {sum(word != 'none' for word in feedback_words)} |
| charged this run, USD | {sum(x['cost'] for x in res):.5f} |
| elapsed seconds | {time.monotonic() - started:.1f} |

Labels: `{OUT.relative_to(ROOT)}`. Requests: `{REQUEST_LOG.relative_to(ROOT)}`.
Scored question version: `{QSHA}`. Unscored feedback version: `{llm.FEEDBACK_SHA}`. Invocation: `{INVOCATION}`.
Feedback is kept in `judge_feedback` in each label and request record; it never enters moral scores.

{RUN_LOG.relative_to(ROOT)}
""")


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--write-schema', type=Path, help='Write the exact request schema without calling the model')
    args = parser.parse_args()
    if args.write_schema is not None:
        schema = dict(task=TASK, questions=dict(QS), qsha=QSHA, chunk=CHUNK, labeller=llm.JEV,
                      feedback_sha=llm.FEEDBACK_SHA, feedback_policy='required_on_new_calls',
                      feedback_context_format=llm.FEEDBACK_CONTEXT_FORMAT, unscored_questions={'debug_word': llm.FEEDBACK_QUESTION},
                      rubrics=RUBRIC, rubric_sha256=hashlib.sha256(json.dumps(RUBRIC, ensure_ascii=False).encode()).hexdigest())
        args.write_schema.parent.mkdir(parents=True, exist_ok=True)
        args.write_schema.write_text(json.dumps(schema, ensure_ascii=False, indent=2) + '\n')
        print(f'{args.write_schema}: {len(QS)} questions; {QSHA}')
        raise SystemExit(0)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    RUN_LOG = ROOT / os.environ.get('RUN_DIR', f'logs/labels/{stamp}_{INVOCATION[:6]}') / 'run.md'
    RUN_LOG.parent.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(sys.stdout, level='INFO', format='{message}\n', colorize=False)
    logger.add(RUN_LOG, level='DEBUG', format='{message}\n', colorize=False)
    config = dict(argv=sys.argv, traditions=T, model=llm.JEV, questions=len(QS), chunk=CHUNK, qsha=QSHA,
                  feedback_sha=llm.FEEDBACK_SHA, unscored_question='debug_word',
                  rep=REP, rows=os.environ.get('ROWS', 'all'), id_file=os.environ.get('ID_FILE'), limit=os.environ.get('LIMIT'),
                  threads=int(os.environ.get('THREADS', 16)), output=str(OUT.relative_to(ROOT)), request_log=str(REQUEST_LOG.relative_to(ROOT)),
                  budget_usd=None if RUN_BUDGET_USD == float('inf') else RUN_BUDGET_USD)
    logger.info('# Moral labelling\n\n## Resolved setup\n\n```json\n' + json.dumps(config, indent=2) + '\n```\n')
    try:
        main()
    except Exception:
        logger.error('## Failed\n\n```text\n' + traceback.format_exc() + f'\n```\n\nCharged requests this invocation: ${RUN_SPENT_USD:.5f}.\n\n{RUN_LOG.relative_to(ROOT)}')
        raise
