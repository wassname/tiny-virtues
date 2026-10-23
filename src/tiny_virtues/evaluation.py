"""Quoted-case evaluation rules, adapted from tiny-stoic scripts/eval/01 and 06. -- PI/OpenAI"""
import hashlib
import random
import re
from collections import defaultdict
from pathlib import Path
from tiny_virtues.case_input import judging_story

LABELS = ('well', 'badly', 'neither')
OPTIONS = ('acting well', 'acting badly', 'neither well nor badly')
QUESTION = 'Morally, is the person you see acting well, badly, or neither?'
QUALITY_FLAGS = ('audit_no_act', 'audit_not_self_contained', 'audit_not_faithful')


def eligible(row: dict, traditions: list[str], *, exclude_span_warnings: bool = False) -> bool:
    return (row['label_status'] == 'complete'
            and ('source_review_status' not in row or row['source_review_status'] != 'unresolved')
            and all(row[flag] is False for flag in QUALITY_FLAGS)
            and (not exclude_span_warnings or not row['span_warnings'])
            and not set(traditions).intersection(row['primer_overlap']))


def select(rows: list[dict], count: int, seed: int) -> list[dict]:
    """Round-robin shuffled source groups; selection never uses target labels. -- PI/OpenAI"""
    assert 0 < count <= len(rows)
    assert len({r['id'] for r in rows}) == len(rows)
    rng = random.Random(seed)
    groups = defaultdict(list)
    for row in sorted(rows, key=lambda r: r['id']):
        assert row['source_file'], row['id']
        groups[row['source_file']].append(row)
    for group in groups.values():
        rng.shuffle(group)
    sources = sorted(groups)
    rng.shuffle(sources)
    selected = []
    while len(selected) < count:
        for source in sources:
            if groups[source]:
                selected.append(groups[source].pop())
                if len(selected) == count:
                    break
    return selected


def prompt(row: dict, instruction: str, rotate: bool) -> tuple[str, list[str]]:
    order = [1, 2, 0] if rotate else [0, 1, 2]
    prefix = instruction + '\n\n---\n\n' if instruction else ''
    listing = '\n'.join(f'{position}. {OPTIONS[index]}' for position, index in enumerate(order))
    text = (f"{prefix}Agent being judged: {row['agent']}\n\n{judging_story(row)}\n\n"
            f'{QUESTION}\n{listing}\n\nAnswer with the number only.')
    return text, [LABELS[index] for index in order]


def parse(reply: str, order: list[str]) -> str | None:
    match = re.fullmatch(r'\s*([0-2])\s*\.?\s*', reply)
    return order[int(match[1])] if match else None


def paired_answer(picks: list[str | None]) -> str:
    assert len(picks) == 2
    if None in picks:
        return 'missing'
    return picks[0] if picks[0] == picks[1] else 'order_changed'


def build_hashes(build: Path) -> dict[str, str]:
    return {name: hashlib.sha256((build / name).read_bytes()).hexdigest()
            for name in ('manifest.json', 'rubrics.json', 'tinyvirtues.jsonl', 'tinystoic.jsonl')}


def instruction(rubric: dict) -> str:
    return rubric['tradition'] + '\n\nJudge the named agent by this tradition\'s standards.'


def primer(path: Path) -> str:
    text = path.read_text()
    if '<!-- doctrine: begin' in text:
        return text.split('<!-- doctrine: begin', 1)[1].split('-->', 1)[1].split('<!-- doctrine: end -->', 1)[0].strip()
    assert '\n## Part 2' in text, path
    return text.split('\n## Part 2', 1)[0].strip()
