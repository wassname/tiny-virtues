"""Rank cross-tradition label disagreement within source/config groups. -- PI/OpenAI"""
import math
from collections import defaultdict


def disagreement_score(probabilities):
    distributions = [[p[k] for k in ('well', 'badly', 'neither')] for p in probabilities.values()]
    assert len(distributions) >= 2
    assert all(all(math.isfinite(x) and 0 <= x <= 1 for x in p) and abs(sum(p) - 1) < .01 for p in distributions)
    distributions = [[x / sum(p) for x in p] for p in distributions]
    mean = [sum(p[i] for p in distributions) / len(distributions) for i in range(3)]
    divergence = sum(sum(x * math.log(x / mean[i]) for i, x in enumerate(p) if x > 0)
                     for p in distributions) / len(distributions)
    return max(0., divergence / math.log(3))


def add_disagreement_ranks(rows):
    groups = defaultdict(list)
    for row in rows:
        row['disagreement'] = None
        if row['label_status'] == 'complete':
            group = (row['config'], row['source_school'])
            row['disagreement'] = dict(score=disagreement_score(row['p']), group=list(group))
            groups[group].append(row)
    for members in groups.values():
        members.sort(key=lambda r: (-r['disagreement']['score'], r['id']))
        n = len(members)
        for rank, row in enumerate(members, 1):
            row['disagreement'].update(rank=rank, group_size=n,
                                      top_10pct=rank <= math.ceil(n * .1),
                                      top_1pct=rank <= math.ceil(n * .01))
    return rows
