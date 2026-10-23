"""Price or run all569 virtues through the production cell runner. -- PI/OpenAI"""
import argparse
from decimal import Decimal
import json
import math
from pathlib import Path

from tiny_virtues import case_flags, item_major as builder, major_label as major
from tiny_virtues.major_cells import CellStore

SCOPE = 'jev_all_virtues'
LEDGER = builder.ROOT / 'data/provenance/jev_all_virtues.json'


def size(value):
    return len(json.dumps(value, ensure_ascii=False))


def price(rows, direct, excerpts, *, batch_size=40):
    assert rows and direct
    def request(virtue, batch, full=False):
        packet = builder.build_packet(virtue, batch,
            example_stories=excerpts[virtue['item_id']] if virtue['tested_real_examples'] else {},
            format=builder.FIVE_LEVEL, full_feedback_ids=[r['id'] for r in batch] if full else [],
            validate_source=False)
        return packet['request']
    base = direct[0]
    summary, full = 0, 0
    for start in range(0, len(rows), batch_size):
        batch = rows[start:start+batch_size]
        summary += size(request(base, batch))
        full += size(request(base, batch, full=True))
    prototype = request(base, rows[:1])
    question = builder.question_id(rows[0]['id'], 'level')
    batches = math.ceil(len(rows)/batch_size)
    characters = full
    for virtue in direct[1:]:
        actual = request(virtue, rows[:1])
        state_delta = size(actual['state']) - size(prototype['state'])
        question_delta = size(actual['questions'][question]) - size(prototype['questions'][question])
        characters += summary + batches*state_delta + len(rows)*question_delta
    ratios = major.read(builder.FIVE_TEMPLATE)['pricing_observation']['token_character_ratios']
    return dict(stories=len(rows), direct_virtues=len(direct), requests=batches*len(direct),
        serialized_characters=characters,
        estimated_usd={k: characters*ratio*.042/1_000_000 for k, ratio in ratios.items()},
        method='Exact serialized-character total: one full-feedback virtue, summary feedback elsewhere; token/character pricing band, not a token invoice.',
        model_calls=0)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('cap', type=Decimal)
    parser.add_argument('--dry-run', action='store_true', help='Print full-run price without credentials, packets or paid calls')
    parser.add_argument('--output', type=Path, default=Path('private/labels/all_virtues'))
    parser.add_argument('--threads', type=int, default=4)
    args = parser.parse_args()
    assert args.cap > 0
    flags, _, _ = case_flags.load_registry(case_flags.DEFAULT_REGISTRY)
    held = {identity for identity, f in flags.items() if f['flag_held']}
    rows = list(major.input_rows(major.INPUT_DIR, held))
    direct, contraries, excerpts, versions = major.selected_virtues(scope='all')
    result = price(rows, direct, excerpts)
    result.update(virtues=len(versions), derived_contraries=len(contraries), cap_usd=str(args.cap),
        high_estimate_fits_cap=result['estimated_usd']['high'] < float(args.cap), dry_run=args.dry_run)
    print(json.dumps(result, indent=2), flush=True)
    if args.dry_run:
        return
    assert result['high_estimate_fits_cap'], 'Cap below the price estimate; no packets or paid calls started'
    out = args.output.resolve()
    with major.run_lock(out):
        pending = False
        if (out/'current.json').exists():
            store = CellStore(out/'cells.sqlite')
            try:
                pending = next(store.pending_requests(), None) is not None
            finally:
                store.close()
        manifest = major.read(out/'current.json') if pending else major.prepare(out,
            source_flags=case_flags.DEFAULT_REGISTRY, virtue_scope='all', funding_scope=SCOPE)
        assert manifest['scope'] == SCOPE and manifest['virtue_scope'] == 'all' and len(manifest['versions']) == 569
        assert manifest['pending']['estimated_usd']['high'] < float(args.cap), 'Cap below the priced request total'
        major.initialize_scope(LEDGER, SCOPE, args.cap, 'Explicit just label-all-virtues CAP invocation; separate from major production')
        major.execute(out, funding_scope=SCOPE, funding_ledger=LEDGER, cap=args.cap, threads=args.threads)


if __name__ == '__main__':
    main()
