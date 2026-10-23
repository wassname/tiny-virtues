"""Plot case-to-case correlations from the frozen labelled build. -- PI/OpenAI"""
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
GREEN = '#176b38'
RED = '#a51c30'
CLUSTER_CUT = 0.2  # boxes join virtues whose average r with each other is above 0.8


def correlations(values):
    assert values.ndim == 2 and len(values) >= 2 and np.isfinite(values).all()
    active = np.ptp(values, axis=0) > 0
    corr = np.full((values.shape[1], values.shape[1]), np.nan)
    if active.any():
        corr[np.ix_(active, active)] = np.corrcoef(values[:, active].T) if active.sum() > 1 else 1.
    return corr


def item_values(rows, rubrics):
    labels, kinds, columns, moral_columns, groups = [], [], [], [], []
    for side in ('virtues', 'vices'):
        for school, rubric in rubrics.items():
            main = next(L for L in rubric['lists'] if L['primary'])
            start = len(labels)
            for item in main.get(side, []):
                labels.append(f'{school.capitalize()}: {item["label"]}')
                kinds.append('virtue' if side == 'virtues' else 'vice')
                records = [r['virtue_lists'][school][main['key']][item['label']] for r in rows]
                shows = 'shows' if side == 'virtues' else 'shows_vice'
                columns.append([record[shows] - record['against'] for record in records])
                moral_columns.append([record['signed'] for record in records])
            if len(labels) > start:
                groups.append(dict(kind=kinds[start], school=school, start=start, end=len(labels)))
    return dict(values=np.array(columns).T, moral_values=np.array(moral_columns).T,
                labels=labels, kinds=kinds, groups=groups)


def cluster_order(corr, indices):
    from scipy.cluster.hierarchy import leaves_list, linkage

    active = [i for i in indices if np.isfinite(corr[i, i])]
    constant = [i for i in indices if not np.isfinite(corr[i, i])]
    if len(active) < 2:
        return active + constant
    distance = 1 - corr[np.ix_(active, active)]
    condensed = distance[np.triu_indices(len(active), 1)]
    tree = linkage(condensed, method='average', optimal_ordering=True)
    return [active[int(i)] for i in leaves_list(tree)] + constant


def cluster_blocks(corr, order, cut=CLUSTER_CUT):
    # Contiguous runs of the clustering tree cut at distance `cut` (1 - r); singletons are not boxed.
    from scipy.cluster.hierarchy import fcluster, linkage
    distance = 1 - corr[np.ix_(order, order)]
    tree = linkage(distance[np.triu_indices(len(order), 1)], method='average', optimal_ordering=True)
    ids = fcluster(tree, t=cut, criterion='distance')
    blocks, start = [], 0
    for k in range(1, len(order) + 1):
        if k == len(order) or ids[k] != ids[start]:
            if k - start > 1:
                blocks.append((start, k))
            start = k
    return blocks


def ordered_items(items, corr, *, global_sort=False):
    if global_sort:
        order = cluster_order(corr, range(len(items['labels'])))
    else:
        order = [i for group in items['groups'] for i in cluster_order(corr, range(group['start'], group['end']))]
    return dict(labels=[items['labels'][i] for i in order], kinds=[items['kinds'][i] for i in order],
                groups=[] if global_sort else items['groups'], source_order=order,
                clusters=cluster_blocks(corr, order) if global_sort else [],  # drawn only in the *_clusters figure
                score='P(shows trait) - P(acts against trait), for both virtues and vices',
                ordering='Average linkage, distance 1-r, optimal leaf order; ' + ('all traits' if global_sort else 'within tradition and virtue/vice blocks'),
                correlations=json_matrix(corr[np.ix_(order, order)]))


def json_matrix(corr):
    return [[float(x) if np.isfinite(x) else None for x in row] for row in corr]


def heatmap(corr, labels, title, subtitle, path, *, annotate=False, kinds=None, groups=(), clusters=(), diagonal_note='Diagonal: self-correlation (1).'):
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.colors import LinearSegmentedColormap, Normalize
    from matplotlib.patches import Polygon, Rectangle

    plt.rcParams['font.family'] = ['DejaVu Sans', 'Noto Sans CJK JP']
    plt.rcParams['svg.hashsalt'] = 'tiny-virtues-correlations-v2'
    colours = LinearSegmentedColormap.from_list('red_white_green', [RED, '#ffffff', GREEN])
    colours.set_bad('#bdbdbd')
    norm = Normalize(-1, 1)
    n = len(labels)
    rgba = colours(norm(np.ma.masked_invalid(corr)))
    rgba[np.triu_indices(n, 1)] = (1, 1, 1, 1)
    size = 8 if annotate else 18
    fig, ax = plt.subplots(figsize=(size, size * .92), layout='constrained')
    fig.get_layout_engine().set(w_pad=.13, h_pad=.06)
    ax.imshow(rgba, interpolation='nearest')
    ax.set_xlim(-.5, n - .5 + max(2, n * .23))
    ax.set_ylim(n - .5, -max(1.5, n * .07))
    ax.set_xticks([])
    ax.set_yticks(range(n), labels, fontsize=10 if annotate else 8.5)
    ax.tick_params(axis='y', length=0, pad=5)
    for spine in ax.spines.values():
        spine.set_visible(False)
    two_kinds = bool(kinds) and 'vice' in kinds
    for i, label in enumerate(labels):
        color = (GREEN if kinds[i] == 'virtue' else RED) if two_kinds else '#222222'
        ax.get_yticklabels()[i].set_color(color)
        ax.add_patch(Rectangle((i - .5, i - .5), 1, 1, fill=False, edgecolor='white', linewidth=.6))
        ax.plot([i + .25, i + .70], [i - .25, i - .70], color='#444444', linewidth=.6)
        ax.text(i + .85, i - .85, label, rotation=45, rotation_mode='anchor',
                ha='left', va='center', color=color, fontsize=10 if annotate else 8.5)
        if not annotate:
            ax.text(i, i, '1' if np.isfinite(corr[i, i]) else 'N/A', ha='center', va='center',
                    color='white' if np.isfinite(corr[i, i]) else 'black', fontsize=8)
    for group in groups[1:]:
        edge = group['start'] - .5
        major = two_kinds and kinds[group['start']] != kinds[group['start'] - 1]
        color, width = ('#333333', 1.5) if major else ('#bcbcbc', .45)
        ax.plot([-.5, edge], [edge, edge], color=color, linewidth=width)
        ax.plot([edge, edge], [edge, n - .5], color=color, linewidth=width)
    for a, b in clusters:
        ax.add_patch(Polygon([(a - .5, a - .5), (a - .5, b - .5), (b - .5, b - .5)], closed=True, fill=False,
                             edgecolor='#111111', linewidth=1.6, zorder=4))
    if annotate:
        for i in range(n):
            for j in range(i + 1):
                text = f'{corr[i, j]:.2f}' if np.isfinite(corr[i, j]) else 'N/A'
                ax.text(j, i, text, ha='center', va='center',
                        color='white' if abs(corr[i, j]) > .65 else 'black', fontsize=11)
    ax.text(.56, .97, title, transform=ax.transAxes, ha='left', va='top', fontsize=13 if annotate else 16)
    ax.text(.56, .88, subtitle, transform=ax.transAxes, ha='left', va='top', fontsize=9 if annotate else 11)
    cax = ax.inset_axes([.56, .77, .37, .025])
    bar = fig.colorbar(matplotlib.cm.ScalarMappable(norm=norm, cmap=colours), cax=cax, orientation='horizontal',
                       ticks=[-1, -.5, 0, .5, 1])
    bar.ax.tick_params(labelsize=9 if annotate else 10)
    bar.set_label('Pearson r: opposite (−1) to together (+1)', fontsize=9 if annotate else 11)
    ax.text(.56, .66, diagonal_note + '\nUpper triangle: repeated pairs omitted.',
            transform=ax.transAxes, fontsize=9 if annotate else 10, va='top')
    if two_kinds:
        ax.text(.56, .58, f'Virtues ({kinds.count("virtue")})', transform=ax.transAxes, color=GREEN, fontsize=11)
        ax.text(.56, .54, f'Vices ({kinds.count("vice")})', transform=ax.transAxes, color=RED, fontsize=11)
    if np.isnan(corr).any():
        ax.text(.56, .48, 'Grey cells: constant scores.', transform=ax.transAxes, fontsize=10, va='top')
    fig.canvas.draw()
    bounds = fig.get_tightbbox(fig.canvas.get_renderer())
    width, height = fig.get_size_inches()
    assert bounds.x0 >= 0 and bounds.y0 >= 0 and bounds.x1 <= width and bounds.y1 <= height
    fig.savefig(path.with_suffix('.png'), dpi=160, metadata={'Software': 'tiny-virtues / PI/Sol'})
    fig.savefig(path.with_suffix('.svg'), metadata={'Date': None, 'Creator': 'tiny-virtues / PI/Sol'})
    plt.close(fig)


def major_catalog():
    catalog = []
    for path in sorted((ROOT / 'docs/virtues/lists').glob('*.json')):
        doc = json.loads(path.read_text())
        listing = next(L for L in doc['lists'] if L['primary'])
        for kind in ('virtues', 'vices'):
            for item in listing.get(kind, []):
                catalog.append(dict(id=f"{doc['school']}|{listing['key']}|{item['label']}",
                                    school=doc['school'], name=item['label'], kind=kind[:-1]))
    return sorted(catalog, key=lambda r: (r['kind'] != 'virtue', r['school'], r['name']))


def major_item_values(rows, catalog):
    presence, moral, identities = [], [], []
    derived = set()
    for row in rows:
        if row['major_virtues']['status'] != 'complete':
            continue
        assert not row['flag_held'], 'Held story cannot enter a current-label figure'
        cells = row['major_virtues']['virtues']
        assert set(cells) == {item['id'] for item in catalog}
        assert all(cell['input_sha256'] == row['major_virtues']['input_sha256'] for cell in cells.values())
        moral.append([cells[item['id']]['signed'] for item in catalog])
        presence.append([cells[item['id']]['signed'] if item['kind'] == 'virtue' else cells[item['id']]['vice_presence'] for item in catalog])
        derived.update(identity for identity, cell in cells.items() if 'derived_from' in cell)
        identities.append(row['id'])
    assert len(identities) >= 2 and len(identities) == len(set(identities))
    groups = []
    for i, item in enumerate(catalog):
        if not groups or (groups[-1]['kind'], groups[-1]['school']) != (item['kind'], item['school']):
            groups.append(dict(kind=item['kind'], school=item['school'], start=i, end=i + 1))
        else:
            groups[-1]['end'] = i + 1
    return dict(values=np.array(presence), moral_values=np.array(moral), ids=identities,
                labels=[f"{item['school'].capitalize()}: {item['name']}" + (' [derived]' if item['id'] in derived else '') for item in catalog],
                kinds=[item['kind'] for item in catalog], groups=groups, derived=sorted(derived))


def major_main(build, destination):
    manifest = json.loads((build / 'manifest.json').read_text())
    assert manifest['schema'] == 'major-virtues-mixed-v2', 'Use the source-matched rubric-v2 private build'
    paths = [build / f'{stem}.jsonl' for stem in ('tinyvirtues', 'tinystoic')]
    def rows():
        for path in paths:
            with path.open() as file:
                yield from map(json.loads, file)
    catalog = major_catalog()
    assert len(catalog) == 80
    items = major_item_values(rows(), catalog)
    assert len(items['ids']) == manifest['complete_major_stories']
    destination.mkdir(parents=True, exist_ok=True)
    direct = [i for i, item in enumerate(catalog) if item['id'] not in items['derived']]
    good = sorted(direct, key=lambda i: (catalog[i]['school'], catalog[i]['name'].lower()))
    good_labels = [f"{catalog[i]['school'].capitalize()}: " + ('not ' if catalog[i]['kind'] == 'vice' else '') + catalog[i]['name'] for i in good]
    good_groups = []
    for k, i in enumerate(good):
        if good_groups and good_groups[-1]['school'] == catalog[i]['school']:
            good_groups[-1]['end'] = k + 1
        else:
            good_groups.append(dict(kind='virtue', school=catalog[i]['school'], start=k, end=k + 1))
    good_items = dict(labels=good_labels, kinds=['virtue'] * len(good), groups=good_groups)
    corr = correlations(items['moral_values'][:, good])
    views = {name: ordered_items(good_items, corr, global_sort=global_sort) for name, global_sort in
             (('main_item_correlations', False), ('main_item_correlations_global', True))}
    for name, view in [*views.items(), ('main_item_correlations_clusters', views['main_item_correlations_global'])]:
        boxes = view['clusters'] if name.endswith('_clusters') else []
        view['score'] = 'Rubric v2 signed score (good direction) for the 66 directly judged virtues; vices reversed; derived contraries omitted'
        heatmap(np.array(view['correlations'], float), view['labels'],
                {'main_item_correlations': 'Virtues within traditions', 'main_item_correlations_global': 'Virtues, global grouping',
                 'main_item_correlations_clusters': 'Virtues, global grouping with clusters'}[name],
                f"Rubric v2 · Jev 1.14 · {len(items['ids']):,} accepted stories\n{len(good)} directly judged virtues, all in the good direction\n(vices reversed: 'not rebellion'; derived opposites omitted)",
                destination / name, kinds=view['kinds'], groups=view['groups'], clusters=boxes,
                diagonal_note='Diagonal: self-correlation (1).' + ('\nBoxes: clusters from the same tree,\ncut where average r drops below 0.8.' if boxes else ''))
    schools = sorted({item['school'] for item in catalog})
    tradition_scores = np.stack([items['moral_values'][:, [i for i in direct if catalog[i]['school'] == school]].mean(axis=1)
                                 for school in schools], axis=1)
    average = correlations(tradition_scores)
    off = np.where(np.eye(len(schools), dtype=bool), np.nan, average)
    anchor = int(np.nanargmax(np.nanmean(off, axis=1)))
    order = [anchor] + sorted((j for j in range(len(schools)) if j != anchor), key=lambda j: -average[anchor, j])
    schools = [schools[j] for j in order]
    average = average[np.ix_(order, order)]
    heatmap(average, [s.capitalize() for s in schools], 'How similarly traditions judge stories',
            f"Tradition score for a story = mean of its virtue scores.\nr across {len(items['ids']):,} stories. Ordered by r with\n{schools[0].capitalize()}, the tradition closest to all others.",
            destination / 'tradition_correlations', annotate=True,
            diagonal_note='Diagonal: each tradition with itself (1).')
    data = dict(author='PI/Sol', rubric_version='v2', label_model='typesafe/jev-1.14-20261014',
                rows=len(items['ids']), ids=items['ids'], catalog=catalog, derived=items['derived'],
                build_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                manifest_sha256=hashlib.sha256((build / 'manifest.json').read_bytes()).hexdigest(),
                schools=schools, tradition_pair_mean=json_matrix(average),
                trait_items=views['main_item_correlations'], trait_items_global=views['main_item_correlations_global'], tradition_order_anchor=schools[0],
                method='Pearson across complete accepted stories. Virtue maps use signed scores in the good direction for the 66 directly judged virtues (vices reversed, derived contraries omitted). Tradition table: Pearson r between per-story tradition scores (mean signed score over each tradition’s directly judged virtues).')
    (destination.parent / 'correlations.json').write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(dict(rows=len(items['ids']), rubric_version='v2', figures=str(destination), derived=len(items['derived']))))


def main(preview=False):
    build = ROOT / ('private/build/v5_preview' if preview else 'private/build/v5')
    manifest = json.loads((build / 'manifest.json').read_text())
    assert manifest['preview'] == preview
    rubrics = json.loads((build / 'rubrics.json').read_text())
    paths = [build / f'{name}.jsonl' for name in ('tinyvirtues', 'tinystoic')]
    rows = [json.loads(line) for p in paths for line in p.read_text().splitlines()]
    rows = sorted((r for r in rows if r['label_status'] == 'complete'), key=lambda r: r['id'])
    schools = list(rubrics)
    dest = ROOT / 'docs/results/v5/figures'
    dest.mkdir(parents=True, exist_ok=True)
    provenance = f'{len(rows):,}-case historical preview' if preview else f'{len(rows):,} labelled cases'
    verdict = np.array([[r['p'][s]['well'] - r['p'][s]['badly'] for s in schools] for r in rows])
    overall = correlations(verdict)
    heatmap(overall, [s.capitalize() for s in schools], 'Overall moral-approval scores',
            'P(virtue) − P(vice)\n' + provenance, dest / 'tradition_correlations', annotate=True)
    items = item_values(rows, rubrics)
    trait_corr = correlations(items['values'])
    grouped = ordered_items(items, trait_corr)
    global_view = ordered_items(items, trait_corr, global_sort=True)
    for view, name, title in ((grouped, 'main_item_correlations', 'Traits grouped within traditions'),
                              (global_view, 'main_item_correlations_global', 'Traits grouped across all traditions')):
        heatmap(np.array(view['correlations'], dtype=float), view['labels'], title,
                'P(shows the trait) − P(acts against it)\n' + provenance,
                dest / name, kinds=view['kinds'], groups=view['groups'])
    old_labels = [f'{s.capitalize()}: {item["label"]}' for s, rubric in rubrics.items()
                  for L in rubric['lists'] if L['primary'] for side in ('virtues', 'vices') for item in L.get(side, [])]
    old_order = [items['labels'].index(label) for label in old_labels]
    data = dict(qsha=manifest['qsha'], preview=preview, rows=len(rows), item_plot_values='trait_items.correlations',
                global_item_plot_values='trait_items_global.correlations', ids=[r['id'] for r in rows],
                build_hashes={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in paths},
                schools=schools, overall=json_matrix(overall), items=old_labels,
                main_item_score='Moral approval: shows-against for virtues, against-shows_vice for vices',
                main_items=json_matrix(correlations(items['moral_values'][:, old_order])),
                trait_base_labels=items['labels'], trait_items=grouped, trait_items_global=global_view,
                method='Pearson across cases; overall = p(virtue)-p(vice); main_items = morally oriented signed paired difference; '
                       'trait_items = named-trait paired difference, vices not sign-reversed; null/grey = constant output; '
                       'plots show self-correlations and unique pairs, green positive and red negative')
    target = ROOT / 'data/analysis/v5/correlations.json'
    target.write_text(json.dumps(data, indent=2, ensure_ascii=False) + '\n')
    print(dest)
    print(target)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--preview', action='store_true')
    parser.add_argument('--major-build', type=Path)
    parser.add_argument('--output', type=Path, default=ROOT / 'docs/results/v5/figures')
    args = parser.parse_args()
    if args.major_build is not None:
        assert not args.preview
        major_main(args.major_build, args.output)
    else:
        main(args.preview)
