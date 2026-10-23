"""Project labelled stories and quoted teaching into the public dataset schema. -- PI/Sol"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import re
import shutil

import polars as pl
import pyarrow.parquet as pq

from tiny_virtues import case_flags, item_major
from tiny_virtues.major_export import jsonl
from tiny_virtues.quote_check import problems

ROOT = item_major.ROOT
CELL_FIELDS = {'shows', 'against', 'signed', 'probabilities'}
COLLECTIONS = {'tiny-virtues', 'tiny-stoic', 'fables'}
SOURCE_SCHOOLS = {'stoic', 'confucian', 'taoist', 'buddhist', 'christian', 'jewish',
                  'islamic', 'hindu', 'rationalist', 'other'}


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    return case_flags.file_sha(path)


def canonical_virtues():
    result = {}
    for path in sorted((ROOT / 'docs/virtues/lists').glob('*.json')):
        doc = read(path)
        for listing in doc['lists']:
            for kind in ('virtues', 'vices'):
                for virtue in listing.get(kind, []):
                    identity = f"{doc['school']}|{listing['key']}|{virtue['label']}"
                    result[identity] = dict(id=identity, tradition=doc['school'], list_key=listing['key'],
                        list_name=listing['name'], list_role='main' if listing['primary'] else 'alternative',
                        primary=listing['primary'], virtue=virtue['label'], kind=kind[:-1], rubric_v1_gloss=virtue['gloss'])
    assert len(result) == 569
    return result


def citation_replacements():
    mapping = read(ROOT / 'data/provenance/source_file_bib_map.json')['mapping']
    replacements = {}
    for raw, record in mapping.items():
        for path in (raw, record['resolved_path'], str(ROOT / record['resolved_path'])):
            replacements[path] = '[' + record['bibkey'] + ']'
    return sorted(replacements.items(), key=lambda pair: -len(pair[0]))


def public_teaching(value, replacements):
    if isinstance(value, str):
        for old, new in replacements:
            value = value.replace(old, new)
        return value
    if isinstance(value, list):
        return [public_teaching(v, replacements) for v in value]
    if isinstance(value, dict):
        return {k: public_teaching(v, replacements) for k, v in value.items()}
    return value


def rubric_rows():
    catalog = canonical_virtues()
    pairs = read(ROOT / 'data/provenance/rubrics/named_contraries_20261022.json')['pairs']
    parents = {p['vice_id']: p['virtue_id'] for p in pairs}
    group_path = ROOT / 'outputs/rubric_gym/nshot-all-groups/loop-4/metrics.json'
    groups = {}
    for group in read(group_path)['groups']:
        for identity in group['items']:
            groups.setdefault(identity, []).append(group['group'])
    replacements = citation_replacements()
    for virtue in item_major.load_items(scope='all'):
        identity = virtue['item_id']
        rendered = item_major.render_teaching(virtue['guide'], virtue['sources'], format=item_major.FIVE_LEVEL)
        examples = rendered.get('opus_labelled_stories', [])
        row = catalog[identity] | dict(rubric_version='v2', rubric_sha256=virtue['rubric_sha256'],
            source_scope=rendered['scope'], teaching=rendered['segments'], limits=rendered['limits'],
            examples=examples, example_scale=rendered.get('example_scale'),
            status=dict(tested_groups=groups.get(identity, []), real_story_examples=virtue['tested_real_examples'],
                        source_status=virtue['status']['status'],
                        production_teaching='source quotations and own real-story examples' if virtue['tested_real_examples'] else 'source quotations only'))
        if identity in parents:
            row['derived_from'] = parents[identity]
        yield public_teaching(row, replacements)


def compact_labels(cells):
    return {identity:{key:cell[key] for key in ('shows','against','signed')} | dict(
                probabilities=cells[cell['derived_from']]['favorable_probabilities'] if 'derived_from' in cell else cell['favorable_probabilities'])
            for identity,cell in cells.items()}


def label_schema(versions):
    template=read(item_major.FIVE_TEMPLATE)
    return dict(labeller=item_major.MODEL,served_model=item_major.SERVED_MODEL,rubric_version='v2',
        format=item_major.FIVE_LEVEL,view='forward',rubric_versions=versions,
        signed_definition='(sum(level * favorable_normalized_P) - 2) / 2',
        normalization_policy='divide each displayed probability by sum of all five',
        probabilities='Five normalized probabilities, ordered from morally adverse to favorable.',
        evidence_semantics='weighted evidence, not independently calibrated yes/no probabilities',
        shows_definition='P(clear evidence of the named quality) + 0.5 * P(some evidence of it)',
        against_definition='The same weighting for evidence against the named quality',
        level_meanings={kind:template['forms'][kind]['forward']['criteria'] for kind in ('virtues','vices')},
        derived_contraries='Named contraries copy the parent probability vector and signed weight; shows/against swap. See derived_from in the rubrics config.',
        old_virtue_labels='Not released: partial coverage and stale source versions; retained in the private build.')


def source_school(row, source, repairs):
    school = row['source_school']
    if school is None:
        repair = repairs[source['bibkey']]
        assert repair['source_file'] == source['resolved_path'], 'Source-school repair belongs to another book'
        school = repair['source_school']
    school = school.strip().lower()
    assert school in SOURCE_SCHOOLS, f'Unknown source school: {school}'
    return school


def project_story(row, bib_map, publish, school_repairs):
    assert not row['flag_held'] and row['major_virtues']['status'] == 'complete'
    current = row['major_virtues']['virtues']
    source = bib_map[row['source_file']]
    public = dict(id=row['id'], collection='tiny-stoic' if row['dataset'] == 'tinystoic' else 'fables' if row['config'] == 'fables' else 'tiny-virtues',
        story=row['story'], source_school=source_school(row, source, school_repairs),
        source=dict(citation=row['citation'], bibkey=source['bibkey'], additional_bibkeys=source['additional_bibkeys'],
                    source_sha256=source['source_sha256']),
        label_status='rubric_v2_complete',
        virtue_labels=compact_labels(current), **publish.public_case_fields(row))
    context_bibkeys = {str(i):[bib_map[locator]['bibkey'] for locator in publish.attribution_locators(part['attribution'])]
                      for i,part in enumerate(public['case_context']) if publish.attribution_locators(part['attribution'])}
    if context_bibkeys:
        public['source']['case_context_bibkeys'] = context_bibkeys
    verdict = row.get('source_verdict')
    judged_passages = row['quote'] + '\n' + '\n'.join(part['quote'] for part in public['case_context'])
    public['source_verdict'] = verdict if verdict and not problems(verdict, judged_passages) else None
    public['source']['verdict_status'] = 'in_judged_passage' if public['source_verdict'] else 'not_quoted_in_judged_passage'
    public['legacy_labels'] = dict(status='legacy_not_rescored', source_matches_current=None,
        values={key:row[key] for key in ('labels', 'p', 'labeller') if key in row})
    if 'modernised' in row:
        public['modernised'] = row['modernised']
    publish.check_value(public, row['id'])
    publish.check_case_text(public)
    return public


def rubric_markdown(rows):
    text = []
    for row in rows:
        text += [f"## {row['virtue']}", '', row['source_scope'], '']
        if 'derived_from' in row:
            text += [f"Labels derive from `{row['derived_from']}`.", '']
        for segment in row['teaching']:
            if 'quote' in segment:
                text += ['\n'.join('> ' + line for line in segment['quote'].splitlines()), '', segment['citation'], '']
            else:
                text += [segment['explanation'], '']
        for example in row['examples']:
            text += ['<details>', f"<summary>{example['agent']} — {example['evidence_level']}</summary>", '',
                     example['story'], '', example['reason'], '', '</details>', '']
        if row['limits']:
            text += ['Limits: ' + (row['limits'] if isinstance(row['limits'], str) else json.dumps(row['limits'], ensure_ascii=False)), '']
    return '\n'.join(text)


def write_parquet(path, rows):
    overrides = {}
    if rows and 'flag_sources' in rows[0]:
        assert all(set(row['flag_sources']) <= set(case_flags.FLAGS) for row in rows)
        overrides['flag_sources'] = pl.Struct({flag: pl.String for flag in case_flags.FLAGS})
    pl.DataFrame(rows, infer_schema_length=None, schema_overrides=overrides).write_parquet(path, compression='zstd')


def parquet_rows(path):
    for batch in pq.ParquetFile(path).iter_batches(batch_size=128):
        yield from batch.to_pylist()


def public_assets(output, bib, publish, context_bibkeys):
    assets = output / 'assets'
    replacements = citation_replacements()
    names = ['docs/results/rubric_nshot/' + name for name in ('README.md', 'table.md', 'verdicts.md', 'meanings.md', 'figure.png')]
    names += ['docs/results/v5/correlations.json']
    names += ['docs/results/v5/figures/' + stem + suffix
              for stem in ('main_item_correlations_global', 'main_item_correlations', 'main_item_correlations_clusters', 'tradition_correlations')
              for suffix in ('.png', '.svg')]
    for name in names:
        dest = assets / name; dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.suffix == '.md':
            text = public_teaching((ROOT / name).read_text(), replacements)
            text = text.replace('See `.local/supervision/spot_checks/anger_five_meanings.md`.', 'See [anger meanings](meanings.md#anger).')
            text = text.replace('Generated by `.local/supervision/verdicts/meanings_extract.py` from ', 'Generated from ')
            publish.check_value(text, name)
            dest.write_text(text)
        else:
            shutil.copyfile(ROOT / name, dest)
    spec = importlib.util.spec_from_file_location('source_bibliography', ROOT / 'scripts/sources/update_bib.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    original = (ROOT / 'docs/sources.bib').read_text()
    types = {match[2]:match[1] for match in re.finditer(r'(?m)^@(\w+)\s*\{\s*([^,\s]+)\s*,', original)}
    fields = {'author', 'title', 'year', 'translator', 'editor', 'publisher', 'edition', 'volume', 'number',
              'pages', 'booktitle', 'journal', 'url', 'doi', 'isbn', 'note'}
    text = '\n\n'.join('@'+types[key]+'{'+key+',\n'+',\n'.join('  '+name+' = {'+public_teaching(value, replacements)+'}'
                         for name,value in entry['fields'].items() if name in fields)+'\n}'
                         for key,entry in module.bib_entries(original).items())+'\n'
    publish.check_value(text, 'docs/sources.bib')
    (assets / 'docs/sources.bib').write_text(text)
    mapping = [dict(source_name=Path(name).name, bibkey=record['bibkey'], additional_bibkeys=record['additional_bibkeys'],
                    source_sha256=record['source_sha256']) for name,record in bib.items()]
    public_map = dict(author='PI/Sol', mapping=mapping, case_context_bibkeys=context_bibkeys)
    publish.check_value(public_map, 'public source map')
    dest = assets / 'data/provenance/source_file_bib_map.json'; dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(public_map, ensure_ascii=False, indent=2)+'\n')


def assemble(build, output, publish):
    build, output = Path(build), Path(output)
    private_manifest = read(build / 'manifest.json')
    assert not private_manifest.get('non_release_smoke'), 'A non-release smoke cannot enter publication'
    assert private_manifest['schema'] == 'major-virtues-mixed-v2' and private_manifest['status'] == 'private_build_complete'
    output.mkdir(parents=True, exist_ok=True)
    old_configs = {'rubrics.jsonl.gz','tinyvirtues.jsonl.gz','fables.jsonl.gz','tinystoic.jsonl.gz',
                   'fables.parquet','tiny_stoic.parquet'}
    generated = {'assets','assembly.json','manifest.json','rubrics.json','source_flags.json',
                 'rubrics.parquet','stories.parquet'}
    assert set(p.name for p in output.iterdir()) <= generated | old_configs, 'Unexpected file in the generated release directory'
    for name in old_configs:
        (output / name).unlink(missing_ok=True)
    bib = read(ROOT / 'data/provenance/source_file_bib_map.json')['mapping']
    school_repairs = read(ROOT / 'data/provenance/source_school_repairs_20261023.json')['books']
    public_flags = read(build / 'source_flags.json')
    rubric_data = list(rubric_rows())
    assert len(rubric_data) == len({r['id'] for r in rubric_data}) == 569
    for row in rubric_data:
        publish.check_value(row, 'rubric:' + row['id'])
    write_parquet(output / 'rubrics.parquet', rubric_data)
    inventory = {row['id']: {k:row[k] for k in ('primary','tradition','list_key','list_name','virtue','kind','derived_from') if k in row}
                 for row in rubric_data}
    (output / 'rubrics.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2) + '\n')
    grouped = {}
    for row in rubric_data:
        grouped.setdefault((row['tradition'], row['list_key']), []).append(row)
    for (school, listing), rows in grouped.items():
        folder = output / 'assets/rubrics' / school / listing
        folder.mkdir(parents=True, exist_ok=True)
        (folder / 'rubric.json').write_text(json.dumps(rows, ensure_ascii=False, indent=2) + '\n')
        (folder / 'rubric.json.md').write_text(f"# {school.capitalize()}: {rows[0]['list_name']}\n\n" + rubric_markdown(rows))
    counts, ids, flag_counts = Counter(), set(), Counter()
    school_counts, null_resolutions = Counter(), Counter()
    context_bibkeys, stories = {}, []
    for stem in ('tinyvirtues', 'tinystoic'):
        for row in jsonl(build / f'{stem}.jsonl'):
            if row['flag_held'] or row['major_virtues']['status'] != 'complete':
                continue
            public = project_story(row, bib, publish, school_repairs)
            if 'case_context_bibkeys' in public['source']:
                context_bibkeys[public['id']] = public['source']['case_context_bibkeys']
            assert public['id'] not in ids
            ids.add(public['id']); counts[public['collection']] += 1
            school_counts[public['source_school']] += 1
            if row['source_school'] is None:
                null_resolutions[public['source']['bibkey']] += 1
            flag_counts.update(flag for flag in case_flags.FLAGS if public[flag])
            stories.append(public)
    write_parquet(output / 'stories.parquet', stories)
    assert len(ids) == private_manifest['complete_major_stories']
    flag_registry = dict(schema_version=1, entries={identity: flags for identity, flags in public_flags['entries'].items() if identity in ids})
    (output / 'source_flags.json').write_text(json.dumps(flag_registry, ensure_ascii=False, indent=2) + '\n')
    public_assets(output, bib, publish, context_bibkeys)
    result = dict(author='PI/Sol', private_build_sha256=sha(build / 'manifest.json'),
        stories=len(ids), source_stories=private_manifest['coverage']['rows'], held_stories=private_manifest['coverage']['held_stories'],
        counts=dict(counts), rubrics=len(rubric_data), context_locator_stories=len(context_bibkeys),
        source_school_counts=dict(sorted(school_counts.items())),
        source_school_null_resolutions={key:dict(rows=count, source_school=school_repairs[key]['source_school'])
                                        for key,count in sorted(null_resolutions.items())},
        flag_counts={flag:flag_counts[flag] for flag in case_flags.FLAGS},
        source_flag_registry_sha256=private_manifest['source_flag_registry_sha256'],
        label_schema=label_schema(private_manifest['versions']),
        files={p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*'))
               if p.is_file() and p not in (output/'assembly.json',output/'manifest.json')})
    (output / 'assembly.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result
