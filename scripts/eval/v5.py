"""Evaluate saved major-virtue labels with rubric-v1-only prompts and Inspect traces. -- PI/Sol"""
import argparse
import json
from pathlib import Path

from tiny_virtues import major_evaluation as evaluation
from tiny_virtues import major_eval_reports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('prepare', 'price', 'run', 'report', 'recover', 'compare'))
    parser.add_argument('--labels', type=Path, default=evaluation.ROOT / 'private/labels/jev_major_20261022')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--stories', type=int, default=300)
    parser.add_argument('--seed', type=int, default=20261023)
    parser.add_argument('--budget', type=float, default=4.99)
    parser.add_argument('--threads', type=int, default=4)
    parser.add_argument('--request-delay', type=float, default=0.0)
    parser.add_argument('--limit', type=int, help='Run a prefix of the frozen requests for a real smoke test')
    parser.add_argument('--allow-paid', action='store_true')
    parser.add_argument('--model', default=evaluation.MODEL, choices=tuple(evaluation.MODELS))
    parser.add_argument('--reuse', type=Path, help='Extend a frozen debug sample and reuse exact matching replies')
    parser.add_argument('--final', action='store_true', help='Shared $15 scope for the approved three-model evaluation')
    parser.add_argument('--runs', type=Path, nargs='+', help='Saved model outputs for the matched comparison')
    parser.add_argument('--reference', type=Path, default=major_eval_reports.REFERENCE)
    args = parser.parse_args()
    if args.command in ('prepare', 'price'):
        manifest = evaluation.prepare(args.output, args.labels, count=args.stories, seed=args.seed, model=args.model, reuse=args.reuse)
        result = dict(selected_stories=len(manifest['selected']), candidates=manifest['candidate_stories'],
                      pricing=manifest['pricing'], prompt=manifest['prompts'])
    elif args.command == 'run':
        result = evaluation.run(args.output, allow_paid=args.allow_paid, limit=args.limit,
                                threads=args.threads, budget=args.budget, final=args.final,
                                request_delay=args.request_delay)
    elif args.command == 'recover':
        evaluation.LEDGER = evaluation.FINAL_LEDGER if args.final else evaluation.DEBUG_LEDGER
        result = evaluation.recover_failed(args.output)
    elif args.command == 'compare':
        result = major_eval_reports.compare(args.runs, args.output, reference=args.reference)
    else:
        result = evaluation.summarize(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2), flush=True)


if __name__ == '__main__':
    main()
