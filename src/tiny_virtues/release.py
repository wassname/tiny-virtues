"""Project labelled stories and quoted teaching into the public dataset schema. -- PI/Sol"""
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import re
import shutil

import polars as pl
import pyarrow.parquet as pq

from tiny_virtues import case_flags, item_major
from tiny_virtues.major_export import jsonl
ROOT = item_major.ROOT
CELL_FIELDS = {'signed', 'probabilities'}
COLLECTIONS = {'tiny-virtues', 'tiny-stoic', 'fables'}
SOURCE_SCHOOLS = {'stoic', 'confucian', 'taoist', 'buddhist', 'christian', 'jewish',
                  'islamic', 'hindu', 'rationalist', 'fable', 'other'}
STORY_FIELDS = ('id', 'story', 'actor', 'act', 'tradition', 'source', 'virtue_labels', 'flags', 'context', 'tale', 'fold', 'bibkey')
RUBRIC_FIELDS = ('id', 'tradition', 'list_name', 'virtue', 'kind', 'definition', 'teaching', 'limits', 'examples', 'list_role', 'derived_from')
VIRTUE_FIELDS = ('id', 'tradition', 'list_name', 'virtue', 'kind', 'definition', 'list_role', 'derived_from')
FLAG_NAMES = {'flag_multi_person_agent':'several_people', 'flag_no_act':'no_act', 'flag_ocr_garble':'ocr',
              'flag_editor_header':'editor_header', 'flag_editor_gloss_target':'editor_gloss_target'}
GENRES = {'anecdote', 'generic', 'lesson', 'saying', 'analogy', 'dilemma', 'character', 'fable',
          'hypothetical', 'generic_character', 'parable'}
FABLE_BOOKS = {'townsend1867three', 'ryder1917twentytwo'}
SEED = 20261023


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


def rubric_rows(items=None):
    catalog = canonical_virtues()
    parents = {p['vice_id']:p['virtue_id'] for p in read(ROOT/'data/provenance/rubrics/named_contraries_20261022.json')['pairs']}
    replacements = citation_replacements()
    for item in item_major.load_items(scope='all') if items is None else items:
        identity = item['item_id']; base = catalog[identity]
        rendered = item_major.render_teaching(item['guide'], item['sources'], format=item_major.FIVE_LEVEL)
        originals = item['guide']['examples'] if 'opus_labelled_stories' in rendered else []
        examples = []
        for example, original in zip(rendered.get('opus_labelled_stories', []), originals, strict=True):
            prefix = f"Selected act or disposition (quoted from the passage):\n{original['selected_conduct_quote']}\n\nFull passage:\n"
            assert example['story'].startswith(prefix), original['case_id']
            examples.append(dict(story=example['story'][len(prefix):], actor=example['agent'],
                act=original['selected_conduct_quote'], level=example['evidence_level'], reason=example['reason']))
        row = {key:base[key] for key in ('id','tradition','list_name','virtue','kind')}
        row.update(definition=base['rubric_v1_gloss'], teaching=dict(scope=rendered['scope'],segments=rendered['segments']),
            limits=rendered['limits'],examples=examples,list_role=base['list_role'],derived_from=parents.get(identity))
        assert tuple(row) == RUBRIC_FIELDS
        yield public_teaching(row, replacements)


def compact_labels(cells):
    return {identity:dict(signed=cell['signed'], probabilities=cells[cell['derived_from']]['favorable_probabilities']
                if 'derived_from' in cell else cell['favorable_probabilities']) for identity,cell in cells.items()}


def example_membership(items):
    identities, quotes = set(), set()
    for item in items:
        guide = item['guide']
        if guide.get('example_format') != 'opus_labelled_stories_v1':
            continue
        for example in guide['examples']:
            identities.add(example['case_id'])
            prefix = f"Selected act or disposition (quoted from the passage):\n{example['selected_conduct_quote']}\n\nFull passage:\n"
            assert example['story'].startswith(prefix), example['case_id']
            quotes.add(' '.join(example['story'][len(prefix):].split()))
    return identities, quotes


def bibliography():
    spec = importlib.util.spec_from_file_location('source_bibliography', ROOT/'scripts/sources/update_bib.py')
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module.bib_entries((ROOT/'docs/sources.bib').read_text())


def short_source(fields):
    clean = lambda value: value.replace('{','').replace('}','').replace('\\&','&').replace('---','—').strip()
    def people(value):
        names = []
        for name in clean(value).split(' and '):
            parts = name.split(', ')
            names.append(' '.join(reversed(parts)) if len(parts)==2 else name)
        return ' and '.join(names)
    author = people(fields['author']) if fields.get('author') else ''
    title = clean(fields['title'])
    details = []
    if fields.get('translator'):
        details.append('tr. '+people(fields['translator']))
    if fields.get('year'):
        details.append(clean(fields['year']))
    return (author+', ' if author else '')+title+(' ('+', '.join(details)+')' if details else '')


def assign_folds_and_shuffle(rows, seed=SEED):
    group = lambda row: ('tale',row['tale']) if row['tale'] is not None else ('id',row['id'])
    groups = sorted({group(row) for row in rows})
    random.Random(seed).shuffle(groups)
    folds = {key:i%5 for i,key in enumerate(groups)}
    for row in rows:
        row['fold'] = folds[group(row)]
    rows.sort(key=lambda row:row['id'])
    random.Random(seed).shuffle(rows)


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


def project_story(row, bib_map, publish, school_repairs, *, sources, flags, examples):
    assert not row['flag_held'] and row['major_virtues']['status'] == 'complete'
    text = publish.case_text_fields(row)
    source = bib_map[row['source_file']]
    school = source_school(row, source, school_repairs)
    if row['dataset'] == 'tinystoic':
        school = 'stoic'
    elif school == 'other' and source['bibkey'] in FABLE_BOOKS:
        school = 'fable'
    marked = [name for key,name in FLAG_NAMES.items() if row['id'] in flags and flags[row['id']][key]]
    if row['id'] in examples[0] or ' '.join(row['quote'].split()) in examples[1]:
        marked.append('rubric_example')
    context = ([dict(attribution='editor',quote=text['source_header'])] if 'source_header' in text else []) + text['case_context']
    public = dict(id=row['id'],story=text['quote'],actor=text['agent'],act=text['target_act_quote'],tradition=school,
        source=short_source(sources[source['bibkey']]['fields']),virtue_labels=compact_labels(row['major_virtues']['virtues']),
        flags=marked,context=context,tale=None if row['story'] in GENRES else row['story'],fold=None,bibkey=source['bibkey'])
    assert tuple(public) == STORY_FIELDS
    publish.check_value(public, row['id'])
    return public


def rubric_markdown(items):
    text = []
    replacements = citation_replacements()
    for item in items:
        payload = public_teaching(item_major.render_teaching(item['guide'],item['sources'],format=item_major.FIVE_LEVEL), replacements)
        text += [f"## {payload['item']}", '', payload['scope'], '']
        for segment in payload['segments']:
            if 'context' in segment:
                text += [segment['context'], '']
            if 'quote' in segment:
                citation=re.sub(r'(?:[;,]\s*)?(?:retained-)?source lines?\s+\d+(?:\s*[-–—]\s*\d+)?','',segment['citation'],flags=re.I)
                text += ['\n'.join('> '+line for line in segment['quote'].splitlines()), '', citation, '']
            else:
                text += [segment['explanation'], '']
        if payload.get('example_scale'):
            text += [payload['example_scale'], '']
        for example in payload.get('opus_labelled_stories', []):
            text += ['<details>', f"<summary>{example['agent']} - {example['evidence_level']}</summary>", '',
                     example['story'], '', example['reason'], '', '</details>', '']
        if payload['limits']:
            text += [payload['limits'] if isinstance(payload['limits'],str) else json.dumps(payload['limits'],ensure_ascii=False), '']
    return '\n'.join(text)


def write_rubric_pages(output, items):
    grouped = {}
    for item in items:
        school, listing, _ = item['item_id'].split('|')
        grouped.setdefault((school,listing),[]).append(item)
    if (output/'assets/rubrics').exists():
        shutil.rmtree(output/'assets/rubrics')
    for (school,listing), members in grouped.items():
        folder=output/'assets/rubrics'/school/listing
        folder.mkdir(parents=True,exist_ok=True)
        (folder/'rubric.json.md').write_text(f"# {school.capitalize()}: {members[0]['list_name']}\n\n"+rubric_markdown(members))


def write_parquet(path, rows):
    overrides = ({'flags':pl.List(pl.String),'context':pl.List(pl.Struct({'attribution':pl.String,'quote':pl.String})),
                  'fold':pl.Int8} if rows and 'flags' in rows[0] else {})
    pl.DataFrame(rows,infer_schema_length=None,schema_overrides=overrides).write_parquet(path,compression='zstd')


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
    original = (ROOT / 'docs/sources.bib').read_text()
    types = {match[2]:match[1] for match in re.finditer(r'(?m)^@(\w+)\s*\{\s*([^,\s]+)\s*,', original)}
    fields = {'author', 'title', 'year', 'translator', 'editor', 'publisher', 'edition', 'volume', 'number',
              'pages', 'booktitle', 'journal', 'url', 'doi', 'isbn', 'note'}
    text = '\n\n'.join('@'+types[key]+'{'+key+',\n'+',\n'.join('  '+name+' = {'+public_teaching(value, replacements)+'}'
                         for name,value in entry['fields'].items() if name in fields)+'\n}'
                         for key,entry in bibliography().items())+'\n'
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
                   'fables.parquet','tiny_stoic.parquet','rubrics.parquet'}
    generated = {'assets','assembly.json','manifest.json','rubrics.json','source_flags.json',
                 'virtues.parquet','stories.parquet'}
    assert set(p.name for p in output.iterdir()) <= generated | old_configs, 'Unexpected file in the generated release directory'
    for name in old_configs:
        (output / name).unlink(missing_ok=True)
    bib = read(ROOT / 'data/provenance/source_file_bib_map.json')['mapping']
    school_repairs = read(ROOT / 'data/provenance/source_school_repairs_20261023.json')['books']
    flags_by_id, public_flags, flag_bindings = case_flags.load_registry(ROOT/'data/provenance/case_flags_20261023.json')
    items = item_major.load_items(scope='all')
    examples = example_membership(items)
    sources = bibliography()
    rubric_data = list(rubric_rows(items))
    assert len(rubric_data) == len({r['id'] for r in rubric_data}) == 569
    for row in rubric_data:
        publish.check_value(row, 'rubric:' + row['id'])
    write_parquet(output/'virtues.parquet',[{key:row[key] for key in VIRTUE_FIELDS} for row in rubric_data])
    inventory = {row['id']:dict(primary=row['list_role']=='main',
        **{key:row[key] for key in ('tradition','list_name','virtue','kind')},
        **(dict(derived_from=row['derived_from']) if row['derived_from'] is not None else {})) for row in rubric_data}
    (output/'rubrics.json').write_text(json.dumps(inventory,ensure_ascii=False,indent=2)+'\n')
    write_rubric_pages(output,items)
    counts, ids, flag_counts = Counter(), set(), Counter()
    school_counts, null_resolutions = Counter(), Counter()
    context_bibkeys, stories, genre_rows = {}, [], {}
    for stem in ('tinyvirtues', 'tinystoic'):
        for row in jsonl(build / f'{stem}.jsonl'):
            if row['flag_held'] or row['major_virtues']['status'] != 'complete':
                continue
            assert row['id'] not in flags_by_id or not flags_by_id[row['id']]['flag_held']
            public = project_story(row,bib,publish,school_repairs,sources=sources,flags=flags_by_id,examples=examples)
            locators = {str(i):[bib[locator]['bibkey'] for locator in publish.attribution_locators(part['attribution'])]
                        for i,part in enumerate(public['context']) if publish.attribution_locators(part['attribution'])}
            if locators:
                context_bibkeys[public['id']] = locators
            assert public['id'] not in ids
            ids.add(public['id'])
            counts['tiny-stoic' if row['dataset']=='tinystoic' else 'fables' if row['config']=='fables' else 'tiny-virtues'] += 1
            school_counts[public['tradition']] += 1
            if row['source_school'] is None:
                null_resolutions[public['bibkey']] += 1
            if row['story'] in GENRES:
                genre_rows[row['id']] = row['story']
            flag_counts.update(public['flags'])
            stories.append(public)
    assign_folds_and_shuffle(stories)
    for row in stories:
        publish.check_public_case(row)
    write_parquet(output/'stories.parquet',stories)
    assert len(ids) == private_manifest['complete_major_stories']
    flag_registry = dict(schema_version=2,entries={})
    private_names = {value:key for key,value in FLAG_NAMES.items()}
    for row in stories:
        if row['flags']:
            flag_registry['entries'][row['id']] = {name:('Used as a worked example in rubric v2.' if name=='rubric_example'
                else public_flags['entries'][row['id']][private_names[name]]['reason']) for name in row['flags']}
    (output/'source_flags.json').write_text(json.dumps(flag_registry,ensure_ascii=False,indent=2)+'\n')
    genre_record = dict(author='PI/Sol',field='private story',public_field='tale',genres=sorted(GENRES),rows=genre_rows)
    (ROOT/'data/provenance/public_tale_genres_20261023.json').write_text(json.dumps(genre_record,ensure_ascii=False,indent=2)+'\n')
    public_assets(output, bib, publish, context_bibkeys)
    result = dict(author='PI/Sol', private_build_sha256=sha(build / 'manifest.json'),
        stories=len(ids), source_stories=private_manifest['coverage']['rows'], held_stories=private_manifest['coverage']['held_stories'],
        counts=dict(counts), rubrics=len(rubric_data), context_locator_stories=len(context_bibkeys),
        source_school_counts=dict(sorted(school_counts.items())),
        source_school_null_resolutions={key:dict(rows=count, source_school=school_repairs[key]['source_school'])
                                        for key,count in sorted(null_resolutions.items())},
        flag_counts={flag:flag_counts[flag] for flag in (*FLAG_NAMES.values(),'rubric_example')},
        tale_named_stories=sum(row['tale'] is not None for row in stories),genre_values_removed=len(genre_rows),
        shuffle_seed=SEED,fold_seed=SEED,source_flag_registry_sha256=sha(ROOT/'data/provenance/case_flags_20261023.json'),
        label_schema=label_schema(private_manifest['versions']),
        files={p.relative_to(output).as_posix():sha(p) for p in sorted(output.rglob('*'))
               if p.is_file() and p not in (output/'assembly.json',output/'manifest.json')})
    (output / 'assembly.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    return result
