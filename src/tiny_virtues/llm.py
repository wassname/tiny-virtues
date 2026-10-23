"""Shared helpers: OpenRouter chat call, quote normalising and verbatim check, paragraph window around a cited line.
Ported from tiny-stoic scripts/20_extract_cases.py. -- PI/Claude"""
import fcntl, gzip, hashlib, json, math, os, re, subprocess, tempfile, threading, time
from datetime import datetime, timezone
from pathlib import Path
import httpx
from tiny_virtues.providers import provider
from tiny_virtues.judge_feedback import (validate_choice, decision_request, FEEDBACK_WORDS,
    FEEDBACK_QUESTION, FEEDBACK_CONTEXT_FORMAT, FEEDBACK_SHA, request_feedback_sha)
from tiny_virtues.rubric_accounting import ACTIVE_STUDY_LEDGER

ROOT = Path(__file__).resolve().parents[2]
KEY = (os.environ['OPENROUTER_API_KEY'] if 'OPENROUTER_API_KEY' in os.environ else
       dict(l.split('=', 1) for l in (ROOT / '.env').read_text().splitlines() if '=' in l)['OPENROUTER_API_KEY'].strip().strip('"\''))
H = {'Authorization': f'Bearer {KEY}', 'X-OpenRouter-Title': 'tiny-virtues'}
FUNDING_LEDGER = ROOT / 'data/provenance/rubric_gym_budget_20261018.json'


def require_paid_approval(funding_scope, *, model=None, endpoint=None):
    """Check scope and any model/endpoint earmark before each paid attempt. -- PI/Sol"""
    ledger = json.loads(FUNDING_LEDGER.read_text())
    funding = ledger['funding_status']
    if funding['new_paid_calls'] != 'approved':
        raise RuntimeError(f"New paid inference blocked ({funding['new_paid_calls']}): {funding['reason']}")
    if funding_scope != funding['scope']:
        raise RuntimeError(f"New paid inference blocked: funding scope {funding_scope!r} != {funding['scope']!r}")
    if 'allowed_requests' in funding and dict(model=model, endpoint=endpoint) not in funding['allowed_requests']:
        raise RuntimeError(f"New paid inference blocked by earmark: {model!r} on {endpoint!r}")
    if 'active_suballocation' in ledger and ACTIVE_STUDY_LEDGER.get() != str(FUNDING_LEDGER.resolve()):
        raise RuntimeError('New paid inference requires managed study accounting for the active suballocation')

# quote marks, backslash escapes and markdown stars ignored; words broken across scan lines rejoined
norm = lambda s: re.sub(r'(\w)- (\w)', r'\1\2', re.sub(r'\s+', ' ', s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"').replace('\\', '').replace('*', ''))).strip()


def verbatim(q, src):
    """Nonempty quote pieces between ellipses occur in the normalized source, in order. -- PI/OpenAI"""
    pos = 0
    parts = [x.strip(' ,;:\"\'') for x in re.split(r'\.\.\.|…', norm(q)) if x.strip(' ,;:\"\'')]
    if not parts or not all(any(char.isalnum() for char in part) for part in parts):
        return False
    for part in parts:
        pattern = (r'(?<!\w)' if part[0].isalnum() else '') + re.escape(part) + (r'(?!\w)' if part[-1].isalnum() else '')
        match = re.compile(pattern).search(src, pos)
        if match is None:
            return False
        pos = match.end()
    return True


def paragraphs(path, max_words=None):
    """Source paragraphs; oversized blocks split only between existing source lines. -- PI/OpenAI"""
    paras, cur, start, size = [], [], None, 0
    for i, line in enumerate(Path(path).read_text(errors='ignore').splitlines(), 1):
        text = line.strip()
        words = len(text.split())
        if cur and (not text or (max_words is not None and size + words > max_words)):
            paras.append((start, ' '.join(cur)))
            cur, start, size = [], None, 0
        if text:
            cur.append(text)
            start = start or i
            size += words
    if cur:
        paras.append((start, ' '.join(cur)))
    return paras


def paragraph_end(path, start, max_words=2500):
    """Final source line of the same bounded block used by window. -- PI/OpenAI"""
    lines = Path(path).read_text(errors='ignore').splitlines()
    following = [a for a, _ in paragraphs(path, max_words) if a > start]
    limit = following[0] - 1 if following else len(lines)
    end = start
    while end < limit and lines[end].strip():
        end += 1
    return end


def window(path, line, words):
    """Cited block plus at most `words` on each side; one source line is indivisible. -- PI/OpenAI"""
    paras = paragraphs(path, words)
    k = max(j for j, (a, _) in enumerate(paras) if a <= line)
    lo, n = k, 0
    while lo > 0 and n + len(paras[lo - 1][1].split()) <= words:
        lo -= 1
        n += len(paras[lo][1].split())
    hi, n = k, 0
    while hi < len(paras) - 1 and n + len(paras[hi + 1][1].split()) <= words:
        hi += 1
        n += len(paras[hi][1].split())
    return [(j - lo + 1, a, t) for j, (a, t) in enumerate(paras[lo:hi + 1], start=lo)], k - lo + 1


SOL_SLOTS = threading.Semaphore(int(os.environ.get('SOL_THREADS', 12)))
PI_BIN = os.environ.get('PI_BIN', str(Path.home() / '.local/share/mise/shims/pi'))  # a stale pi 0.87 in ~/.local/bin comes first on some PATHs


def chat_pi(model, content, *, attempts=6):
    """Subscription model through `pi -p`, no tools; returns an OpenRouter-shaped reply with cost 0. -- PI/Sol"""
    for attempt in range(attempts):
        with SOL_SLOTS, tempfile.NamedTemporaryFile('w', suffix='.txt') as f:
            f.write(content); f.flush()
            try:  # stdin must be closed: pi -p waits for EOF on a piped stdin
                p = subprocess.run([PI_BIN, '-p', '--no-tools', '-ne', '-ns', '-nc', '-np', '--no-mcp', '--no-session', '--offline', '--model', model,
                                    '--system-prompt', 'You answer exactly as asked.', '@' + f.name], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   timeout=900, cwd='/tmp')
            except subprocess.TimeoutExpired:
                print(f'pi call timed out after 900 s (try {attempt + 1})', flush=True)
                open(ROOT / 'logs/pi_failures.log', 'a').write(time.strftime('%H:%M:%S ') + model + ' timeout\n')
                continue
        if p.returncode == 0 and p.stdout.strip():
            return dict(model=model, provider='pi-subscription', choices=[dict(message=dict(content=p.stdout, reasoning=None), finish_reason='stop')], usage=dict(cost=0))
        msg = f'pi call failed (try {attempt + 1}): rc={p.returncode} stdout={p.stdout[-200:]!r} stderr={p.stderr[-500:]!r}'
        print(msg, flush=True)
        with open(ROOT / 'logs/pi_failures.log', 'a') as fl:
            fl.write(time.strftime('%H:%M:%S ') + model + ' ' + msg + '\n')
        time.sleep(30 * 2 ** attempt)
    raise RuntimeError('pi call failed after retries')


CHAT_ATTEMPTS = ROOT / '.local/cache/chat_attempts.jsonl'
CHAT_TRACE_LOCK = threading.Lock()


def save_chat_attempt(record):
    """Keep complete paid HTTP attempts without authorization headers. -- PI/Sol"""
    CHAT_ATTEMPTS.parent.mkdir(parents=True, exist_ok=True)
    with CHAT_TRACE_LOCK, CHAT_ATTEMPTS.open('a') as file:
        file.write(json.dumps(record, ensure_ascii=False) + '\n')


class ChatRequestFailed(RuntimeError):
    def __init__(self, record):
        detail = record['error'] if 'error' in record else f'{record["http_status"]}: {record["response_text"][:300]}'
        super().__init__(f'failed after retries: {detail}')
        self.record = record


class ChatFundingBlocked(ChatRequestFailed):
    pass


class ChatRateLimited(ChatRequestFailed):
    pass


def chat(model, content, *, attempts=6, funding_scope=None, **kw):
    """Bound chat attempts explicitly; retain complete HTTP responses and transport failures. -- PI/Sol"""
    if model.startswith(('openai-codex/', 'pi:')):  # 'pi:anthropic/claude-opus-5-5' = Claude subscription, not OpenRouter
        return chat_pi(model.removeprefix('pi:'), content, attempts=attempts)  # max_tokens/reasoning are ignored; pi uses its default thinking level
    body = dict(model=model, messages=[dict(role='user', content=content)], **kw, **provider(model))  # open-weight models pinned to first-party fp8 (providers.py)
    for attempt in range(attempts):
        require_paid_approval(funding_scope, model=body['model'], endpoint='chat/completions')
        try:
            r = httpx.post('https://openrouter.ai/api/v1/chat/completions', json=body, headers=H, timeout=600)
            record = dict(time=datetime.now(timezone.utc).isoformat(), funding_scope=funding_scope, request=body, attempt=attempt,
                          http_status=r.status_code, response_text=r.text)
            save_chat_attempt(record)
            if r.status_code == 200 and 'choices' in r.json():
                return r.json()
            if r.status_code == 402:
                raise ChatFundingBlocked(record)
            if r.status_code == 429:
                raise ChatRateLimited(record)
            if r.status_code not in (200, 408, 429) and r.status_code < 500 and r.text.strip():  # empty bodies and errors inside a 200 are transient (seen via OpenRouter)
                raise RuntimeError(f'{r.status_code}: {r.text[:500]}')
        except (httpx.TransportError, json.JSONDecodeError) as error:
            record = dict(time=datetime.now(timezone.utc).isoformat(), funding_scope=funding_scope, request=body, attempt=attempt, error=repr(error))
            save_chat_attempt(record)
        if attempt + 1 < attempts:
            time.sleep(2 ** attempt)
    raise ChatRequestFailed(record)


JUDGE_TRACE = ROOT / 'private/judges/generative.jsonl'
JUDGE_TRACE_LOCK = threading.Lock()
JUDGE_NOTE_DESCRIPTION = ('Unscored feedback after the judgment, never rewarded or penalized. Flag problems with the task, rubric, '
                          'missing or ambiguous context, or suggestions for repair. Use an empty string when no issue is apparent.')


def judge_request(model, content, *, judge_note_max_chars=None, **kw):
    """Construct the exact generative judging input before pricing or cache lookup. -- PI/Sol"""
    content += '\n\nReturn a JSON object. After the judgment fields, include the required string field judge_note. ' + JUDGE_NOTE_DESCRIPTION
    if judge_note_max_chars is not None:
        assert type(judge_note_max_chars) is int and judge_note_max_chars > 0
        content += f' Keep judge_note empty or one short sentence about the task setup, at most {judge_note_max_chars} characters. Do not put case-by-case reasoning or repeat scores in judge_note.'
        assert kw['response_format']['type'] == 'json_schema', 'A bounded note requires JSON Schema'
    if 'response_format' in kw and kw['response_format']['type'] == 'json_schema':
        fmt = json.loads(json.dumps(kw['response_format']))
        schema = fmt['json_schema']['schema']
        assert schema['type'] == 'object'
        schema['properties'].pop('judge_note', None)
        schema['properties']['judge_note'] = dict(type='string', description=JUDGE_NOTE_DESCRIPTION)
        if judge_note_max_chars is not None:
            schema['properties']['judge_note']['maxLength'] = judge_note_max_chars
        schema['required'] = [key for key in schema['required'] if key != 'judge_note'] + ['judge_note']
        kw['response_format'] = fmt
    else:
        kw['response_format'] = dict(type='json_object')
    return dict(model=model, content=content, **kw)


def judge_chat(model, content, *, funding_scope=None, on_response=None, **kw):
    """Generative judgments require an unscored note and retain a full trace, including invalid replies. -- PI/OpenAI"""
    request = judge_request(model, content, **kw)
    record = dict(request_sha=hashlib.sha256(json.dumps(request, ensure_ascii=False).encode()).hexdigest(),
                  request=request, funding_scope=funding_scope, feedback_status='request_failed')
    try:
        response = chat(model, request['content'], funding_scope=funding_scope,
                        **{key:value for key,value in request.items() if key not in ('model','content')})
        record |= dict(response=response, feedback_status='feedback_missing')
        if on_response is not None:
            on_response(request, response)
        parsed = first_json(response['choices'][0]['message']['content'], strict=True)
        assert isinstance(parsed['judge_note'], str), 'judge_note must be a string; empty is allowed'
        record |= dict(judge_note=parsed['judge_note'], feedback_status='recorded')
    except Exception as error:
        record['error'] = f'{type(error).__name__}: {error}'
        raise
    finally:
        JUDGE_TRACE.parent.mkdir(parents=True, exist_ok=True)
        with JUDGE_TRACE_LOCK, JUDGE_TRACE.open('a') as f:
            f.write(json.dumps(record, ensure_ascii=False) + '\n')
    return response | dict(judge_note=parsed['judge_note'], feedback_status='recorded')


def first_json(text, *, strict=False):
    """Decode a reply object; strict reports syntax errors for retry feedback. -- PI/OpenAI"""
    m = re.search(r'\{.*\}', text or '', re.S)
    if m is None:
        if strict:
            raise ValueError('reply contains no JSON object')
        return None
    try:
        return json.loads(m[0])
    except json.JSONDecodeError:
        if strict:
            raise
        return None


JEV = os.environ.get('LABELLER', 'perplexity/pplx-decider-v1.2')  # Served checkpoint is retained with every response. -- PI/OpenAI
JEV_CACHE = ROOT / '.local/cache/labeller'  # machine-local labeller responses; one gzipped JSON per request hash, atomically replaced
jev_path = lambda k: JEV_CACHE / k[:2] / f'{k}.json.gz'
JEV_FAILURE_LOCK = threading.Lock()


def jev_key(request):
    """Choice order affects the answer, so preserve JSON insertion order. -- PI/OpenAI"""
    return hashlib.sha256(json.dumps(request, ensure_ascii=False).encode()).hexdigest()


def jev_request(state, questions, *, model=JEV, full_feedback_questions=None):
    """Isolated diagnostics receive explicit full or summary copies; scored state stays unchanged. -- PI/Sol"""
    return decision_request(state, questions, model=model, full_feedback_questions=full_feedback_questions)


def save_jev_failure(request, response, status, body=None, error=None):
    """Retain failed requests and any billed usage outside the success cache. -- PI/OpenAI"""
    if body is None and response is not None:
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = None
    key = jev_key(request)
    path = JEV_CACHE / 'failures' / f'{key}.jsonl'
    path.parent.mkdir(parents=True, exist_ok=True)
    with JEV_FAILURE_LOCK, path.open('a') as file:
        file.write(json.dumps(dict(key=key, request=request, response=body,
                   http_status=response.status_code if response is not None else None,
                   raw_response_text=response.text if response is not None else None,
                   feedback_status=status, error=str(error) if error is not None else None,
                   usage=body.get('usage') if isinstance(body, dict) else None), ensure_ascii=False) + '\n')
    return path


def jev(state, questions, *, model=JEV, funding_scope=None, full_feedback_questions=None):
    """Serialize cold calls for one exact request across threads and processes. -- PI/OpenAI"""
    options = dict(model=model)
    if full_feedback_questions is not None:
        options['full_feedback_questions'] = full_feedback_questions
    path = jev_path(jev_key(jev_request(state, questions, **options))).with_suffix('.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as file:
        fcntl.flock(file, fcntl.LOCK_EX)
        return _jev(state, questions, funding_scope=funding_scope, **options)


def _jev(state, questions, *, model=JEV, funding_scope=None, full_feedback_questions=None):
    """Decisions API (docs/vendor/llm_judge_gist/judge_jev.py): one shared state, many
    questions per request; batch composition can change answers. questions: name -> dict(type='choice'|'score'|'noul', instructions=..., criteria=...).
    Scored answers exclude the mandatory unscored debug_word; its full answer is kept in judge_feedback and response. -- PI/OpenAI
    """
    options = dict(model=model)
    if full_feedback_questions is not None:
        options['full_feedback_questions'] = full_feedback_questions
    req = jev_request(state, questions, **options)
    feedback_sha = request_feedback_sha(req)
    k = jev_key(req)
    if jev_path(k).exists():
        row = json.loads(gzip.decompress(jev_path(k).read_bytes()))
        assert row['request'] == req and row['key'] == k and set(row['answers']) == set(questions)
        assert row['feedback_sha'] == feedback_sha
        assert row['judge_feedback']['answer'] == row['response']['answers']['debug_word']
        validate_choice(row['judge_feedback']['answer'], FEEDBACK_QUESTION['criteria'])
        return row | dict(cache_hit=True)
    historical_request = dict(model=model, state=state, questions=questions)
    historical_key = jev_key(historical_request)
    if full_feedback_questions is None and jev_path(historical_key).exists():
        row = json.loads(gzip.decompress(jev_path(historical_key).read_bytes()))
        assert row['request'] == historical_request and set(row['answers']) == set(questions)
        return row | dict(cache_hit=True, feedback_sha=FEEDBACK_SHA,
                          judge_feedback=dict(status='not_collected', question_sha=None, answer=None))
    for attempt in range(6):
        require_paid_approval(funding_scope, model=req['model'], endpoint='decisions')
        try:
            r = httpx.post('https://openrouter.ai/api/alpha/decisions', json=req, headers=H, timeout=120)
            if r.status_code == 200:
                break
            failed = save_jev_failure(req, r, 'http_error')
            if r.status_code not in (408, 429, 499) and r.status_code < 500:  # 499: transient Jev internal error
                error = RuntimeError(r.text[:500])
                error.add_note(f'Failed judge request retained in {failed}')
                raise error
        except httpx.TransportError as error:
            save_jev_failure(req, None, 'transport_error', error=error)
        time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f'Decisions API failed after retries for {model}; failure traces: {JEV_CACHE / "failures" / (k + ".jsonl")}')
    body = None
    try:
        body = r.json()
        assert isinstance(body, dict), 'judge response must be a JSON object'
        assert isinstance(body['model'], str) and body['model'], 'served model must be named'
        assert isinstance(body['id'], str) and body['id'], 'billing response ID must be retained'
        assert set(body['answers']) == set(req['questions']), body['answers']
        assert math.isfinite(body['usage']['cost']) and body['usage']['cost'] >= 0
        for name, q in req['questions'].items():
            if q['type'] == 'noul':
                value = body['answers'][name]['noul']
                assert math.isfinite(value) and 0 <= value <= 1, (name, value)
                continue
            p = body['answers'][name]['probabilities']
            assert isinstance(p, dict), f'probabilities must be a JSON object: {name}'
            expected = set(q['criteria']) if q['type'] == 'choice' else {str(i) for i in range(len(q['criteria']))}
            assert set(p) == expected, (name, p)
            assert all(math.isfinite(v) and 0 <= v <= 1 for v in p.values()), (name, p)
            assert math.isclose(sum(p.values()), 1, abs_tol=0.02 + 1e-8), (name, p)
        feedback = body['answers']['debug_word']
        validate_choice(feedback, FEEDBACK_QUESTION['criteria'])
    except (AssertionError, KeyError, TypeError, ValueError) as error:
        failed = save_jev_failure(req, r, 'invalid_response', body=body, error=error)
        error.add_note(f'Failed judge response and billed usage retained in {failed}')
        raise
    row = dict(key=k, answers={name: body['answers'][name] for name in questions},
               judge_feedback=dict(status='recorded', question_sha=feedback_sha, answer=feedback), feedback_sha=feedback_sha,
               cost=body['usage']['cost'], served=body['model'], request=req, response=body, funding_scope=funding_scope,
               received_at=datetime.now(timezone.utc).isoformat())
    receipt = JEV_CACHE / 'charges' / k[:2] / f'{k}.jsonl'
    receipt.parent.mkdir(parents=True, exist_ok=True)
    with receipt.open('a') as file:
        file.write(json.dumps(dict(key=k, response_id=body['id'], received_at=row['received_at'], funding_scope=funding_scope,
                                   served=body['model'], usage=body['usage'])) + '\n')
    path = jev_path(k)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    tmp.write_bytes(gzip.compress(json.dumps(row, ensure_ascii=False).encode()))
    os.replace(tmp, path)
    return row | dict(cache_hit=False)
