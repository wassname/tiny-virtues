"""Assemble and stage an explicit public file list; upload only after editorial approval. -- PI/Sol"""
import argparse
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import unquote, urlsplit

import pyarrow.parquet as pq

from tiny_virtues import case_flags
from tiny_virtues.case_input import require_selected_target
from tiny_virtues.major_cells import story_hash
from tiny_virtues.release import COLLECTIONS, SOURCE_SCHOOLS, parquet_rows
from tiny_virtues.rubric_accounting import replace_json

ROOT = Path(__file__).resolve().parents[1]
REPO = 'wassname/tiny-virtues'
TOTAL_STORIES, TOTAL_VIRTUES = 6149, 569
DATA_FILES = {f'data/{name}.parquet': f'data/release/{name}.parquet'
              for name in ('stories', 'rubrics')}
ASSETS = ['docs/sources.bib', 'data/provenance/source_file_bib_map.json',
    *['docs/results/rubric_nshot/'+name for name in ('README.md','table.md','verdicts.md','meanings.md','figure.png')],
    'docs/results/v5/correlations.json',
    *['docs/results/v5/figures/'+stem+suffix for stem in
      ('main_item_correlations_global','main_item_correlations','main_item_correlations_clusters','tradition_correlations') for suffix in ('.png','.svg')],
    *[str(path.parent.relative_to(ROOT)/name) for path in sorted((ROOT/'rubrics').glob('*/*/rubric.json'))
      for name in ('rubric.json','rubric.json.md')],
]
CODE_FILES = ['justfile', 'pyproject.toml', 'uv.lock',
    *[str(path.relative_to(ROOT)) for path in sorted((ROOT/'src/tiny_virtues').glob('*.py'))],
    'scripts/label.py', 'scripts/build_dataset.py', 'scripts/eval/v5.py', 'scripts/publish.py',
    'scripts/analysis/correlations.py', 'scripts/prepare_inputs.py', 'scripts/fast_dev_run.py',
    'scripts/label_all_virtues.py']
PUBLISH = ['LICENSE', 'README.md', 'data/release/manifest.json', *DATA_FILES, *CODE_FILES,
    'data/release/rubrics.json', case_flags.PUBLIC_REGISTRY, *ASSETS,
    *['data/release/assets/docs/examples/jev_v2/'+name for name in ('request.json','reply.json','hashes.json')],
    'examples/use_dataset.py']
CASE_FIELDS = {
    'id','collection','quote','agent','target_act_quote','case_context','source_header','input_sha256',
    'story','source_school','source','source_verdict','flags','flag_sources','span_warnings',*case_flags.FLAGS,
    'modernised','label_status','virtue_labels','legacy_labels',
}
FORBIDDEN_FIELDS = {
    'snippet','book_scene','passage','full_passage','full_text','source_text','original','fixed','source_context',
    'source_header_registry_sha256','source_units','source_bindings','reference_provenance',
    'example_audit','request','response','native_path','receipt_path','cache','trace',
}
PRIVATE_PATH = re.compile(r'(?:^|[/\\])(?:private|\.local|\.git)[/\\]|/(?:home|workspace)/')
SECRET = re.compile(r'sk-or-v1-[A-Za-z0-9]{20,}|hf_[A-Za-z0-9]{24,}|-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----')
ATTRIBUTION_LOCATOR = re.compile(r'(?<![\w/\\])private/sources/(?:[A-Za-z0-9_.-]+/)*[A-Za-z0-9_.-]+\.[A-Za-z0-9]+(?=[:;,\s)\]]|$)')
EXAMPLE_DIAGNOSTICS = {
    'data/release/assets/docs/examples/jev_v2/request.json.questions.debug_word.criteria',
    'data/release/assets/docs/examples/jev_v2/reply.json.answers.debug_word.probabilities',
}


def sha(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def read(path):
    return json.loads(path.read_text())


def attribution_locators(text):
    paths = ATTRIBUTION_LOCATOR.findall(text)
    assert all(not {'.','..',''}.intersection(path.split('/')) for path in paths), 'Invalid source locator'
    return paths


def check_value(value, location):
    if isinstance(value, dict):
        forbidden = FORBIDDEN_FIELDS.intersection(value)
        if location in EXAMPLE_DIAGNOSTICS and 'cache' in forbidden:
            assert value['cache'] == '' or type(value['cache']) in (int,float) and 0 <= value['cache'] <= 1
            forbidden -= {'cache'}
        assert not forbidden, f'{location}: unpublished source/trace field'
        for key, child in value.items():
            assert not PRIVATE_PATH.search(key) and not SECRET.search(key), f'{location}: private key/path'
            check_value(child, location+'.'+key)
    elif isinstance(value, list):
        for child in value:
            check_value(child, location)
    elif isinstance(value, str):
        checked = value
        if location.endswith('.case_context.attribution'):
            for locator in attribution_locators(value):
                checked = checked.replace(locator,'[source locator]')
        assert not PRIVATE_PATH.search(checked) and not SECRET.search(value), f'{location}: private path or credential'


def case_text_fields(row):
    """Project only the exact case text shown to the judge. -- PI/Sol"""
    fresh = row['major_virtues']['virtues']
    context = row['source_context'] if fresh and 'source_context' in row else []
    text = dict(quote=row['quote'], agent=row['person'], target_act_quote=row.get('target_act_quote'),
        case_context=[dict(attribution=part['attribution'],quote=part['quote']) for part in context],
        input_sha256=row['major_virtues']['input_sha256'])
    if 'source_header' in row:
        text['source_header'] = row['source_header']
    if fresh:
        assert text['input_sha256'] == story_hash(row), f'{row["id"]}: stale private judging input'
        check_case_text(dict(id=row['id'],virtue_labels=fresh,**text))
    else:
        assert text['input_sha256'] is None, row['id']
    return text


def public_case_fields(row):
    return case_text_fields(row) | case_flags.public_columns(row)


def check_case_text(row):
    if row.get('source_header') is not None:
        header = row['source_header']
        assert isinstance(header,str) and header.startswith('Source: ') and header[8:].strip(), row['id']
        assert '\n' not in header and '\r' not in header, row['id']
    context = row['case_context']
    assert isinstance(context,list), f'{row["id"]}: case_context must be a list'
    for part in context:
        assert set(part) == {'attribution','quote'}, f'{row["id"]}: unexpected case context field'
        assert all(isinstance(value,str) and value.strip() for value in part.values()), row['id']
    if row.get('flag_held',False):
        assert row['input_sha256'] is None and not context and not row['virtue_labels'], row['id']
        return
    if row['target_act_quote'] is None:
        assert row['input_sha256'] is None and not context, row['id']
        return
    rebuilt = dict(id=row['id'],person=row['agent'],quote=row['quote'],
                   target_act_quote=row['target_act_quote'],source_context=context)
    if row.get('source_header') is not None:
        rebuilt['source_header'] = row['source_header']
    assert story_hash(rebuilt) == row['input_sha256'], f'{row["id"]}: public fields differ from the judged input'
    assert all(cell['input_sha256'] == row['input_sha256'] for cell in row['virtue_labels'].values() if 'input_sha256' in cell), \
        f'{row["id"]}: label belongs to another judging input'


def publish_source(root,name):
    assert name in PUBLISH
    source = DATA_FILES[name] if name in DATA_FILES else 'data/release/assets/'+name if name in ASSETS else name
    path = root/source
    resolved = path.resolve()
    assert not path.is_symlink() and resolved.is_relative_to(root.resolve()), f'Unexpected published source: {name}'
    assert not {'private','.local'}.intersection(resolved.relative_to(root.resolve()).parts) and 'human' not in str(resolved).lower(), name
    return path


def freeze_release(root):
    assembly = read(root/'data/release/assembly.json')
    result = dict(author='PI/Sol', total_stories=assembly['stories'], source_total_stories=assembly['source_stories'],
        held_stories=assembly['held_stories'], source_flag_counts=assembly['flag_counts'],
        collection_counts=assembly['counts'],source_school_counts=assembly['source_school_counts'],
        source_school_null_resolutions=assembly['source_school_null_resolutions'],
        release_ready=True, source_work_complete=True, major_labels_complete=True, feedback_read_complete=True,
        major_virtues=80, direct_major_virtues=66, derived_major_virtues=14,
        major_complete_stories=assembly['stories'], rubrics=assembly['rubrics'],
        source_flag_registry_sha256=assembly['source_flag_registry_sha256'],label_schema=assembly['label_schema'],
        files={name:sha(publish_source(root,name)) for name in PUBLISH if name!='data/release/manifest.json'})
    replace_json(root/'data/release/manifest.json',result)
    return result


def validate_release(root,*,staged=False):
    manifest = read(root/'data/release/manifest.json')
    assert manifest['source_total_stories'] == TOTAL_STORIES and 0 < manifest['total_stories'] <= TOTAL_STORIES
    assert all(manifest[key] is True for key in ('release_ready','source_work_complete','major_labels_complete','feedback_read_complete')), 'Extraction, labelling and feedback review must finish before staging real data'
    assert (manifest['major_virtues'],manifest['direct_major_virtues'],manifest['derived_major_virtues']) == (80,66,14)
    assert manifest['label_schema']['rubric_version']=='v2' and len(manifest['label_schema']['rubric_versions'])==80
    assert set(manifest['collection_counts']) <= COLLECTIONS and sum(manifest['collection_counts'].values()) == manifest['total_stories']
    assert set(manifest['source_school_counts']) <= SOURCE_SCHOOLS and sum(manifest['source_school_counts'].values()) == manifest['total_stories']
    assert all(len(manifest['label_schema']['level_meanings'][kind])==5 for kind in ('virtues','vices'))
    assert set(manifest['files']) == set(PUBLISH)-{'data/release/manifest.json'}, 'Release manifest must bind the exact publish inputs'
    for name,digest in manifest['files'].items():
        path = root/name if staged else publish_source(root,name)
        assert sha(path) == digest, f'Changed release input: {name}'
    check_value(manifest,'manifest')
    return manifest


def reject_full_sources(directory,root):
    sizes = {}
    for path in directory.rglob('*'):
        if path.is_file():
            sizes.setdefault(path.stat().st_size,set()).add(sha(path))
    for source in (root/'private/sources').rglob('*'):
        if 'human' not in str(source).lower() and source.is_file() and source.stat().st_size in sizes:
            assert sha(source) not in sizes[source.stat().st_size], 'A full source file was copied into the public stage'


def leak_checks(directory):
    assert {p.relative_to(directory).as_posix() for p in directory.rglob('*') if p.is_file()} == set(PUBLISH), 'Unexpected published file'
    ids,configs,complete = set(),Counter(),0
    collections,schools = Counter(),Counter()
    rubrics = read(directory/'data/release/rubrics.json')
    source_flags = read(directory/case_flags.PUBLIC_REGISTRY)
    assert set(source_flags) == {'schema_version','entries'} and source_flags['schema_version'] == 1
    major = {identity for identity,rubric in rubrics.items() if rubric['primary']}
    assert len(rubrics) == TOTAL_VIRTUES and len(major) == 80, 'Public virtue inventory differs'
    for name in PUBLISH:
        path = directory/name
        assert not path.is_symlink(), f'Published symlink: {name}'
        if path.suffix == '.parquet':
            metadata = pq.ParquetFile(path).metadata
            assert all(metadata.row_group(group).column(column).compression == 'ZSTD'
                       for group in range(metadata.num_row_groups) for column in range(metadata.num_columns)), 'Public Parquet must use Zstandard'
            for row in parquet_rows(path):
                check_value(row,name)
                if path.name == 'rubrics.parquet':
                    assert row['id'] in rubrics and row['rubric_version'] == 'v2', 'Unmapped public rubric'
                    configs[name] += 1
                    continue
                assert set(row) <= CASE_FIELDS, f'{name}: unexpected case field'
                assert row['collection'] in COLLECTIONS, f'{name}: wrong story collection'
                assert row['source_school'] in SOURCE_SCHOOLS, f'{name}: unknown source school'
                assert row['id'] not in ids, f'Duplicate public story: {row["id"]}'
                ids.add(row['id']); configs[name] += 1
                collections[row['collection']] += 1; schools[row['source_school']] += 1
                assert not row['flag_held'] and row['target_act_quote'] is not None and row['virtue_labels'], 'Only rubric v2-labelled stories may enter the public release'
                assert isinstance(row['agent'],str) and row['agent'].strip(), row['id']
                current = row['virtue_labels']
                assert isinstance(current,dict) and set(current) == major and all(isinstance(cell,dict) for cell in current.values()), 'Incomplete fresh major-virtue labels'
                require_selected_target(row)
                check_case_text(row)
                refs = row['flag_sources']
                assert isinstance(refs, dict) and set(refs) <= set(case_flags.FLAGS), 'Unexpected flag receipt fields'
                case_flags.validate_public(row | dict(flag_sources={key:value for key,value in refs.items() if value is not None}),source_flags)
                for identity,cell in current.items():
                    assert set(cell)=={'shows','against','signed','probabilities'}, 'Unexpected public label fields'
                    probabilities=cell['probabilities']
                    assert isinstance(probabilities,list) and len(probabilities)==5
                    assert all(type(p) in (int,float) and math.isfinite(p) and 0<=p<=1 for p in probabilities)
                    assert math.isclose(sum(probabilities),1,abs_tol=1e-8)
                    assert math.isclose(cell['signed'],sum(i*p for i,p in enumerate(probabilities))/2-1,abs_tol=1e-8)
                    positive=probabilities[4]+.5*probabilities[3];negative=probabilities[0]+.5*probabilities[1]
                    expected=(positive,negative) if rubrics[identity]['kind']=='virtue' else (negative,positive)
                    assert all(math.isclose(actual,want,abs_tol=1e-8) for actual,want in zip((cell['shows'],cell['against']),expected)), 'Evidence weights do not match the probability vector'
                    if 'derived_from' in rubrics[identity]:
                        parent=current[rubrics[identity]['derived_from']]
                        assert cell['signed']==parent['signed'] and cell['probabilities']==parent['probabilities']
                        assert (cell['shows'],cell['against'])==(parent['against'],parent['shows'])
                assert sum('derived_from' in rubrics[identity] for identity in current)==14
                complete += 1
        elif path.suffix == '.json':
            check_value(read(path),name)
        elif path.suffix in ('.md','.bib','.py','.toml','.lock','.svg') or name in ('LICENSE','justfile'):
            text = path.read_text()
            if name in CODE_FILES:
                assert not SECRET.search(text), f'{name}: credential in code'
            else:
                check_value(text,name)
    check_readme_links(directory)
    manifest = validate_release(directory,staged=True)
    flag_shas = {value['private_registry_sha256'] for flags in source_flags['entries'].values() for value in flags.values()}
    assert flag_shas <= {manifest['source_flag_registry_sha256']}, 'Public flag registry belongs to another source review'
    assert set(manifest['label_schema']['rubric_versions'])==major, 'Public rubric versions differ from the labels'
    assert set(source_flags['entries']) <= ids, 'Public flag registry contains unknown story IDs'
    assert len(ids)==manifest['total_stories'] and complete==manifest['major_complete_stories'], 'Public coverage does not match the manifest'
    assert dict(collections) == manifest['collection_counts'] and dict(schools) == manifest['source_school_counts'], 'Public origin counts differ from the manifest'
    assert configs['data/rubrics.parquet'] == TOTAL_VIRTUES
    assert all(configs[name]>0 for name in DATA_FILES)
    return dict(stories=len(ids),configs=dict(configs))


def check_readme_links(directory):
    text = (directory/'README.md').read_text()
    links = re.findall(r'!?\[[^\]]*\]\(([^)]+)\)',text)
    links += re.findall(r'^\s+path:\s*(\S+)\s*$',text.split('---',2)[1],re.MULTILINE) if text.startswith('---\n') else []
    missing = []
    for link in links:
        url = urlsplit(link.split(' ',1)[0].strip('<>\"\''))
        if url.scheme or not url.path:
            continue
        path = (directory/unquote(url.path)).resolve()
        assert path.is_relative_to(directory.resolve()), f'Readme link leaves public stage: {link}'
        if not path.exists():
            missing.append(link)
    assert not missing, f'Unresolved README links: {missing}'
    return len(links)


def stage(root=ROOT):
    validate_release(root)
    parent = root/'.local'; parent.mkdir(exist_ok=True)
    out,receipt = parent/'public',parent/'public-stage.json'
    with tempfile.TemporaryDirectory(dir=parent,prefix='public-candidate-') as temporary:
        candidate = Path(temporary)
        for name in PUBLISH:
            relative = Path(name)
            assert not relative.is_absolute() and not {'private','.local','..'}.intersection(relative.parts) and 'human' not in name
            path = candidate/relative; path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(publish_source(root,name),path)
        coverage = leak_checks(candidate)
        reject_full_sources(candidate,root)
        hashes = {name:sha(candidate/name) for name in PUBLISH}
        if out.exists():
            assert not out.is_symlink()
            assert receipt.exists() and {name:sha(out/name) for name in read(receipt)['files']} == read(receipt)['files'], 'Existing stage has unreviewed changes'
            shutil.rmtree(out)
        shutil.copytree(candidate,out)
    result = dict(author='PI/Sol',status='staged_not_published',repo=REPO,files=hashes,coverage=coverage)
    replace_json(receipt,result)
    return result


def push(*,approved=False,root=ROOT):
    assert approved, 'Wassname must approve the staged snapshot before push --approved'
    out = root/'.local/public'; receipt = read(root/'.local/public-stage.json')
    assert receipt['repo']==REPO and receipt['status']=='staged_not_published'
    assert {name:sha(out/name) for name in PUBLISH}==receipt['files'], 'The reviewed stage changed; stage and review again'
    leak_checks(out); reject_full_sources(out,root)
    target = json.loads(subprocess.run(['gh','api',f'repos/{REPO}'],check=True,text=True,capture_output=True).stdout)
    assert target['full_name']==REPO and target['visibility']=='public', 'Create/verify the exact public GitHub repository first; never use the dev repo'
    from huggingface_hub import HfApi
    api=HfApi(); api.create_repo(REPO,repo_type='dataset',private=False,exist_ok=True)
    target=api.repo_info(REPO,repo_type='dataset')
    assert target.id==REPO and target.private is False
    message=f"Rubric v2: {read(out/'data/release/manifest.json')['major_complete_stories']} stories, 80 major virtues"
    env={key:value for key,value in os.environ.items() if not key.startswith('GIT_')} | dict(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=os.devnull)
    git=['git','-c','credential.helper=!gh auth git-credential']
    with tempfile.TemporaryDirectory(dir=root/'.local',prefix='public-git-') as temporary:
        checkout=Path(temporary)
        def run(*args):
            return subprocess.run(git+list(args),cwd=checkout,env=env,check=True,text=True,capture_output=True)
        run('init','-q','-b','main'); remote=f'https://github.com/{REPO}'
        heads=run('ls-remote','--heads',remote).stdout
        if heads:
            assert 'refs/heads/main' in heads
            run('fetch','-q',remote,'main');run('reset','--soft','FETCH_HEAD')
        for name in PUBLISH:
            path=checkout/name;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(out/name,path)
        run('add','-A')
        run('-c','user.name=PI/Sol','-c','user.email=288921227+claudypoo@users.noreply.github.com','commit','-qm',message,
            '-m','Co-Authored-By: PI/Sol <288921227+claudypoo@users.noreply.github.com>')
        api.upload_folder(repo_id=REPO,repo_type='dataset',folder_path=str(out),delete_patterns=['*'],commit_message=message)
        run('push',remote,'main')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['assemble','stage','push','show-card'])
    parser.add_argument('--build',type=Path,default=ROOT/'private/build/major_v2')
    parser.add_argument('--approved',action='store_true')
    args=parser.parse_args()
    if args.action=='assemble':
        from tiny_virtues.release import assemble
        result=assemble(args.build,ROOT/'data/release',sys.modules[__name__])
        freeze_release(ROOT)
        print(json.dumps(result,indent=2))
    elif args.action=='stage':
        freeze_release(ROOT)
        print(json.dumps(stage(),indent=2))
    elif args.action=='show-card':
        print((ROOT/'README.md').read_text(),end='')
    else:
        push(approved=args.approved)


if __name__=='__main__':
    main()
