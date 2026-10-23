"""Run the real preparation, Jev, build and DeepSeek pipeline on two stories. -- PI/OpenAI"""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import json
from pathlib import Path
import sys
import time
import traceback

from loguru import logger
from tabulate import tabulate

from scripts.prepare_inputs import build as prepare_inputs
from tiny_virtues import major_evaluation as evaluation, major_export, major_label
from tiny_virtues.rubric_accounting import replace_json

IDS = ['stoic:dl-027', 'stoic:far-139']
VIRTUE = 'christian|seven_deadly_sins_contrary_virtues|temperance'
CAP = Decimal('0.0099')


def run(out):
    out.mkdir(parents=True, exist_ok=False)
    log_path = out / 'run.md'
    logger.remove()
    logger.add(sys.stdout, format='{message}', level='INFO', colorize=False)
    logger.add(log_path, format='{message}', level='DEBUG', colorize=False)
    logger.info('# Tiny real pipeline\n\nRuntime check only; two stories cannot establish label quality.\n')
    logger.info('```json\n' + json.dumps(dict(argv=sys.argv, ids=IDS, virtue=VIRTUE,
        jev_cap_usd=str(CAP), eval_cap_usd=str(CAP), output=str(out)), indent=2) + '\n```\n')
    logger.info('SHOULD: two source stories produce two Jev cells and four paired evaluation answers.\n')
    stages = []
    failed = False
    try:
        started = time.monotonic()
        inputs = out / 'inputs'
        prepare_inputs(inputs, new_rows=[], case_ids=IDS)
        flags = out / 'source_flags.json'
        replace_json(flags, dict(schema_version=1, author='PI/OpenAI', entries=[], receipt_sha256={}))
        stages.append(['prepare', '2 stories', '—', f'{time.monotonic()-started:.1f}'])
        logger.info('## Label\n')
        started = time.monotonic()
        labels = out / 'labels'
        manifest = major_label.prepare(labels, input_dir=inputs, source_flags=flags,
            virtue_ids=[VIRTUE], funding_scope='fast_dev_jev')
        assert manifest['pending']['estimated_usd']['high'] < float(CAP)
        ledger = out / 'jev_budget.json'
        major_label.initialize_scope(ledger, 'fast_dev_jev', CAP, 'Supervisor336: real tiny smoke, under one cent per stage')
        result = major_label.execute(labels, threads=1, funding_scope='fast_dev_jev', funding_ledger=ledger, cap=CAP)
        assert result['coverage']['completed_cells'] == 2
        stages.append(['Jev', '2 cells', result['cost_usd'], f'{time.monotonic()-started:.1f}'])
        diagnostics = []
        for path in sorted((labels / 'stages').glob('*/replies/*.json')):
            native = json.loads(path.read_text())
            diagnostics.append(native['judge_feedback'])
            logger.debug('## Jev real request and reply\n\n```json\n' + json.dumps(native, ensure_ascii=False, indent=2) + '\n```\n')
            logger.info(f'Full Jev trace: {path}\n')
        logger.info('Unscored Jev feedback:\n```json\n' + json.dumps(diagnostics, ensure_ascii=False, indent=2) + '\n```\n')
        logger.info('## Build\n')
        started = time.monotonic()
        built = major_export.export(labels, inputs, [inputs/'tinyvirtues.jsonl', inputs/'tinystoic.jsonl'],
            out/'build', None, source_flags=flags, smoke=True)
        assert built['complete_major_stories'] == 2 and built['virtues'] == 1
        stages.append(['build', '2 labelled rows', '—', f'{time.monotonic()-started:.1f}'])
        logger.info('## Evaluate\n')
        started = time.monotonic()
        evaluation.prepare(out/'evaluation', labels, count=2, source_flags=flags, input_dir=inputs, virtue_ids=[VIRTUE])
        measured = evaluation.run(out/'evaluation', allow_paid=True, threads=1, budget=float(CAP),
            funding_scope='fast_dev_eval', funding_ledger=out/'eval_budget.json')
        for path in sorted((out/'evaluation'/'replies').glob('*.json')):
            native = json.loads(path.read_text())
            logger.debug('## DeepSeek real request and reply\n\n```json\n' + json.dumps(native, ensure_ascii=False, indent=2) + '\n```\n')
            logger.info(f'Full evaluated-model trace: {path}\n')
        notes = json.loads((out/'evaluation'/'notes.json').read_text())
        logger.info('Evaluation result and unscored notes:\n```json\n' + json.dumps(dict(result=measured, notes=notes), ensure_ascii=False, indent=2) + '\n```\n')
        assert measured['replies'] == measured['planned_requests'] == 2
        assert sum(v['missing'] for v in measured['traditions'].values()) == 0
        stages.append(['DeepSeek', '4 paired answers', measured['returned_cost_usd'], f'{time.monotonic()-started:.1f}'])
    except BaseException:
        failed = True
        logger.error('```text\n' + traceback.format_exc() + '\n```\n')
        raise
    finally:
        logger.info('## Result: ' + ('FAILED' if failed else 'complete runtime smoke') + '\n')
        logger.info(tabulate(stages, headers=['stage', 'completed', 'cost USD', 'seconds'], tablefmt='pipe') + '\n')
        logger.info(str(log_path))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=Path('outputs/fast-dev-run') / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
    args = parser.parse_args()
    run(args.output)


if __name__ == '__main__':
    main()
