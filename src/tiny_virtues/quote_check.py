"""Check a quote made by the quote-with-provenance skill (~/.agents/skills/quote-with-provenance): after applying `fixed`
(silent typo fixes, {"source spelling": "fixed spelling"}) to the original, the text outside [brackets], split on ..., must occur
verbatim and in order. Comparison ignores case, quote-mark style, whitespace, scan line-break hyphens and markdown stars.   -- PI/Claude"""
import re
import unicodedata

BRACKET = re.compile(r'\[[^\[\]]*\]')
SPAN_CHECK_POLICY = 'pronoun-after-cut-warning-v1'


VERSE = re.compile(r'(?<![\w:])\d{1,3}:\d{1,3}(?![\w:])')  # Bible verse numbers inside a passage, e.g. "3:14"
REFUSAL = re.compile(r"\b(I can(?:no|')t|I'm unable|I am unable|unable to (?:quote|provide|reproduce)|copyright|copyrighted|I won't|summari[sz]e instead)\b", re.I)


def apply_fixed(original, fixed):
    """Apply `fixed` ({"source spelling": "fixed spelling"}) to whole words only: "an" -> "and" must not turn "another" into "andother"."""
    for bad, good in (fixed or {}).items():
        original = re.sub(_fixed_pattern(bad), lambda m: good, original)
    return original


def _fixed_pattern(bad):
    body = r'\s+'.join(re.escape(w) for w in bad.split())  # a scan may break the misspelt words across lines
    return (r'(?<!\w)' if bad[:1].isalnum() else '') + body + (r'(?!\w)' if bad[-1:].isalnum() else '')


def norm(s, *, keep_verse_refs=False):
    s = unicodedata.normalize('NFKC', s if keep_verse_refs else VERSE.sub(' ', s))  # full-width OCR punctuation ('；') and ligatures
    s = s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"').replace('*', '').replace('\\', '').replace('_', '')
    s = re.sub(r'([^\W\d_])[-¬\u00ad][ \t]*(?:\n[ \t]*|[ \t]+)([^\W\d_])', r'\1\2', s)
    s = re.sub(r'\s*(["\'])\s*', r'\1', s)  # scans space quote marks unevenly: '" it is just,"'
    s = re.sub(r'\s+([,.;:!?)\]])', r'\1', s)
    return re.sub(r'\s+', ' ', s).strip().lower()


def editor_brackets(quote, original):
    """Brackets the editor added: translators also bracket supplied words (e.g. Major's Huainanzi), and those occur in the original."""
    src = norm(original)
    return [b for b in BRACKET.findall(quote) if norm(b) not in src]


def pieces(quote, keep=(), *, keep_verse_refs=False):
    """Quoted text split at cuts and editor brackets; brackets in `keep` (the original's own) stay as text."""
    q = re.sub(r'\[(\w)\](?=\w)', r'\1', quote)  # [T]he -> The
    q = BRACKET.sub(lambda m: m.group() if m.group() in keep else ' … ', q)
    return [p for p in (x.strip(' ,;:"\'.-—') for x in re.split(r'\.\.\.|…', norm(q, keep_verse_refs=keep_verse_refs))) if any(c.isalnum() for c in p)]



def _words(s):
    # PI/Sol: Keep non-ASCII letters and digits instead of accepting an empty quote.
    return re.findall(r'[a-z0-9]+|[^\W\x00-\x7f]', s)


def _find(hay, needle, start):
    """Match quoted tokens in order, allowing omitted source number markers. -- PI/OpenAI"""
    for i in range(start, len(hay)):
        pos = i
        for token in needle:
            while pos < len(hay) and hay[pos].isdigit() and hay[pos] != token:
                pos += 1
            if pos == len(hay) or hay[pos] != token:
                break
            pos += 1
        else:
            return i, pos
    return None

def _scan_hyphens(text, split_pairs):
    """Compare retained compounds only where this scan has a line-end split. -- PI/OpenAI"""
    return re.sub(r'(?<!\w)([^\W\d_]+)[-¬\u00ad]([^\W\d_]+)(?!\w)',
        lambda m: m[1]+m[2] if (m[1].lower(),m[2].lower()) in split_pairs else m[0],text)


def problems(quote, original, fixed=None, max_bracket_share=0.3):
    src = original
    for bad in (fixed or {}):
        if not re.search(_fixed_pattern(bad), src):
            return [f'fixed: "{bad}" is not in the original']
    src = apply_fixed(src, fixed)
    split_pairs = {(m[1].lower(),m[2].lower()) for m in re.finditer(r'([^\W\d_]+)[-¬\u00ad][ \t]*\n[ \t]*([^\W\d_]+)',src)}
    quote = _scan_hyphens(quote, split_pairs)
    src = _scan_hyphens(src, split_pairs)
    missing_numbers = [n for n in re.findall(r'\b\d{1,4}\b', BRACKET.sub(' ', quote)) if not re.search(rf'\b{n}\b', src)]
    if missing_numbers:
        return [f'numbers not in the original: {missing_numbers}']
    own = set(BRACKET.findall(quote)) - set(editor_brackets(quote, src))
    out, srcn = [], norm(src, keep_verse_refs=True)
    src_toks = _words(srcn)
    for keep in (own, ()):  # the original's own brackets as text; if that fails, every bracket as an editor's
        out, pos = [], 0
        ps = pieces(quote, keep, keep_verse_refs=True)
        for p in ps:  # word tokens: OCR noise in punctuation ("travesty^", "justin?", ".'.'") is not a change of words
            toks = _words(p)
            hit = _find(src_toks, toks, pos)
            if hit is None:
                out.append(f'not verbatim or out of order: "{p}"' + (' (occurs earlier in the original)' if _find(src_toks, toks, 0) is not None else ''))
            else:
                pos = hit[1]
        if not out:
            break
    if not ps:
        out.append('no quoted source text')
    added = sum(len(b.split()) for b in editor_brackets(quote, src))
    if added > max_bracket_share * max(1, len(BRACKET.sub(' ', quote).split()) + added):
        out.append(f'{added} bracketed words: brackets should be a small part of the quote')
    unmatched = re.findall(r'\S*[\[\]]\S*', BRACKET.sub('', norm(quote)))
    if any(token not in srcn for token in unmatched):
        out.append('unbalanced bracket')
    return out


def passage_in_source(passage, source_text):
    """The stored passage must be the book's own text: it occurs in the source file (same normalisation)."""
    return norm(passage).rstrip("-") in norm(source_text)  # a passage may end on a scan line-break hyphen


def act_coverage(quote, act):
    """Share of the extracted act's words that the quote contains: a low value means the quote left out the act itself."""
    words = lambda t: set(re.findall(r'[a-z]{3,}', norm(BRACKET.sub(' ', t or ''))))
    a = words(act)
    return len(a & words(quote)) / len(a) if a else None


def _tokens(t):
    return _words(norm(BRACKET.sub(' ', t or '')))


STOP = set('a an and as at be but by for from had has have he her his i in is it its me my no not of on or our she so that the their them then they this to was we were what when which who with you your'.split())


def longest_shared_run(a, b):
    """Content words in the longest run of consecutive words that texts a and b share (brackets ignored). Function words do not count,
    so a set name like "the Lords of the Land" in both is not a leak, while a copied sentence is."""
    ta, tb = _tokens(a), _tokens(b)
    best, prev = [], [[] for _ in range(len(tb) + 1)]
    for x in ta:
        cur = [[] for _ in range(len(tb) + 1)]
        for j, y in enumerate(tb, 1):
            if x == y:
                cur[j] = prev[j - 1] + [x]
                if len(cur[j]) > len(best):
                    best = cur[j]
        prev = cur
    return sum(w not in STOP for w in best)


def span_problems(quote, original, act_spans, verdict_spans, cut, fixed=None):
    """The quoter marks the act and the verdict in the original, verbatim. The act must be in the quote; in a verdict-cut quote no verdict span may be."""
    out = [] if act_spans else ['no act marked: the passage may have no particular act to judge']
    for kind, spans in (('act', act_spans), ('verdict', verdict_spans)):
        for sp in spans:
            if problems(sp, original, fixed):
                out.append(f'{kind} span not verbatim in the passage: "{sp[:80]}"')
    qwords = set(_tokens(quote))
    for sp in act_spans:
        missing = [w for w in _tokens(sp) if w not in STOP and w not in qwords]  # every content word of the act; pronouns may become [names]
        if missing:
            out.append(f'the quote leaves out part of the act {missing[:6]}: "{sp[:80]}"')
    if cut:
        acts = ' '.join(norm(a) for a in act_spans)
        for sp in verdict_spans:
            if norm(sp) in acts:
                continue  # a judging word inside the act belongs to the act ("sought false witness")
            if longest_shared_run(sp, quote) >= 4:
                out.append(f'the verdict-cut quote still contains verdict text: "{sp[:80]}"')
    return out


def span_warnings(quote):
    """Possible referent loss needs source reading, not automatic rejection. -- PI/OpenAI"""
    out = []
    for m in re.finditer(r'(?:\.\.\.|…)\s*["“‘\']?(he|she|they|them|him|her|it|this|these|those)\b(?!\s*\[)', quote, re.I):
        if m.group(1).lower() in {'he', 'she', 'they', 'those'} and re.match(r'\s+(?:who|that)\b', quote[m.end():], re.I):
            continue
        if m.group(1).lower() == 'it' and re.match(r'\s+(?:occur(?:s|red)\s+to\b[^.!?;]{0,120}\bthat\b|is\s+reported\s+that\b)', quote[m.end():], re.I):
            continue
        out.append(f'after a cut, "{m.group(1)}" may point to someone else: name them in [brackets]')
    return out
