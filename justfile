# Current dataset pipeline. -- PI/OpenAI
set shell := ["bash", "-cu"]

build := "private/build/major_v2_5869"

default:
    @just --list

# Real two-story prepare, Jev, build and paired DeepSeek evaluation. -- PI/OpenAI
fast-dev-run *options:
    uv run --locked --extra eval python -m scripts.fast_dev_run {{options}}

# Rebuild checked cases without model calls. -- PI/OpenAI
prepare:
    uv run --locked python scripts/prepare_inputs.py

# Source inventory is not a claim that each case was read. -- PI/OpenAI
source-inventory cohort output:
    uv run --locked python scripts/sources/extract.py inventory '{{cohort}}' '{{output}}'

# Compile source-bound requests; optional --saved reuses pre-review extractions. -- PI/OpenAI
source-compile cohort output *options:
    uv run --locked python scripts/sources/extract.py compile '{{cohort}}' '{{output}}' {{options}}

# Produce the recorded native-Sol workflow, executed through Pi subagent. -- PI/OpenAI
source-workflow manifest output *options:
    uv run --locked python scripts/sources/extract.py workflow '{{manifest}}' '{{output}}' {{options}}

source-check batch reply output:
    uv run --locked python scripts/sources/extract.py check '{{batch}}' '{{reply}}' '{{output}}'

# Stage only parent-approved cases; prepare applies the checked repair registry. -- PI/OpenAI
source-stage batch reply cohort approval output:
    uv run --locked python scripts/sources/extract.py stage '{{batch}}' '{{reply}}' '{{cohort}}' '{{approval}}' '{{output}}'

# Freeze and price missing major-virtue labels without model calls. -- PI/Sol
label-prepare output *options:
    uv run --locked python scripts/label.py estimate --output '{{output}}' {{options}}

# The first measured slice stays outside the dataset; its cells can be reused. -- PI/Sol
label-slice output:
    uv run --locked python scripts/label.py slice --output '{{output}}' --allow-paid

# Resume only missing or changed cells under the approved $30 scope. -- PI/Sol
label output:
    uv run --locked python scripts/label.py run --output '{{output}}' --allow-paid

label-status output:
    uv run --locked python scripts/label.py status --output '{{output}}'

# Source-matched major labels plus explicitly old labels; no model calls. -- PI/Sol
build-major run base_virtues base_stoic review output:
    uv run --locked python scripts/build_dataset.py --major-run '{{run}}' --major-base '{{base_virtues}}' '{{base_stoic}}' --feedback-read '{{review}}' --output '{{output}}'

build:
    uv run --locked python scripts/build_dataset.py --label-group data/schemas/v5_additions_questions.json private/labels/v5_additions_labels.jsonl
    uv run --locked python scripts/rubrics/render.py
    uv run --locked --extra plots python scripts/analysis/correlations.py
    uv run --locked python scripts/analysis/list_information.py
    uv run --locked python scripts/analysis/judge_feedback.py
    uv run --locked python scripts/reports/v5.py

preview:
    uv run --locked python scripts/build_dataset.py --preview --label-group data/schemas/v5_additions_questions.json private/labels/v5_additions_labels.jsonl
    uv run --locked python scripts/rubrics/render.py
    uv run --locked --extra plots python scripts/analysis/correlations.py --preview
    uv run --locked python scripts/analysis/list_information.py --preview
    uv run --locked python scripts/analysis/judge_feedback.py --preview
    uv run --locked python scripts/reports/v5.py --preview

# The README is hand-written; reports only refresh the current-label figures. -- PI/Sol
report build_dir=build output="docs/results/v5/figures":
    uv run --locked --extra plots python scripts/analysis/correlations.py --major-build '{{build_dir}}' --output '{{output}}'

# Only labelled stories, sanitized rubrics, citations and the approved README. -- PI/Sol
release build_dir=build:
    uv run --locked python scripts/publish.py assemble --build '{{build_dir}}'

stage:
    uv run --locked python scripts/publish.py stage

# Run after checking .local/public; this uploads to GitHub and Hugging Face. -- PI/Sol
publish: stage
    uv run --locked python scripts/publish.py push --approved

check: prepare test report

test:
    #!/usr/bin/env bash
    set -euo pipefail
    for group in sources judging dataset reports; do
        uv run --locked --extra plots python -m unittest discover -s tests/$group -v
    done

test-eval:
    uv run --locked --extra eval python -m unittest discover -s tests/integration -v

# Small-model rubric traces; checked-in pilot cases are the default. -- PI/Sol
rubric-probe selection output budget="0.25":
    uv run --locked --extra eval python scripts/rubrics/probe.py {{selection}} {{output}} --budget {{budget}}

# Real decision-judge mechanics; fixed cases, full traces and unscored feedback. -- PI/Sol
rubric-gym selection output budget="0.25" funding_scope="":
    uv run --locked --extra eval python scripts/rubrics/gym.py {{selection}} {{output}} --budget {{budget}} --funding-scope '{{funding_scope}}'

# Paired major-virtue predictions; an unchanged frozen output reuses its replies. -- PI/Sol
evaluate output stories="300" labels="private/labels/jev_major_20261022":
    uv run --locked --extra eval python scripts/eval/v5.py prepare --labels '{{labels}}' --output '{{output}}' --stories {{stories}}
    uv run --locked --extra eval python scripts/eval/v5.py run --output '{{output}}' --allow-paid --budget 4.99

# Rebuild only plots of current rubric-v2 labels; no model calls. -- PI/Sol
major-plots build_dir=build output="docs/results/v5/figures":
    uv run --locked --extra plots python scripts/analysis/correlations.py --major-build '{{build_dir}}' --output '{{output}}'

# Rebuild the N-shot table and figure from saved runs, without model calls. -- PI/Sol
rubric-report:
    uv run --locked --extra plots python scripts/rubrics/nshot_report.py

# Signed cross-tradition correlations and paired uncertainty; no API calls. -- PI/Sol
rubric-correlations metrics references:
    uv run --locked python scripts/rubrics/correlations.py {{metrics}} --reference-directory {{references}}

# Frozen courage pilot: compile -> cached smoke -> parent read -> run/replay -> report.
rubric-study-compile group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} compile

rubric-study-smoke cache group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} smoke --cached-only --cache-directory '{{cache}}'

rubric-study-review-smoke group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} review-smoke

rubric-study-confirm-smoke note group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} confirm-smoke --read-note '{{note}}'

rubric-study-run cache group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} run --cached-only --cache-directory '{{cache}}'

rubric-study-replay cache group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} replay --cached-only --cache-directory '{{cache}}'

rubric-study-report group="courage" experiment="source-led-v3":
    OPENROUTER_API_KEY=unused uv run --locked python scripts/rubrics/study.py {{group}} {{experiment}} report

rubrics-render-teaching:
    uv run --locked python scripts/rubrics/render_teaching.py

rubrics-check:
    uv run --locked python scripts/validation/rubrics.py

results:
    @cat docs/results/rubric_nshot/README.md
    @if test -f docs/results/evaluation.md; then cat docs/results/evaluation.md; else echo 'Model evaluation is running.'; fi

# Price without calls: just label-all-virtues 160 --dry-run. -- PI/OpenAI
label-all-virtues cap *options:
    uv run --locked python -m scripts.label_all_virtues '{{cap}}' {{options}}
