"""Independent semantic acceptance checks grounded in the raw directed edges."""
import re

import numpy as np
import pandas as pd

from settings import CONFIG


def validate_semantics(raw_nodes, raw_edges, nodes, top, clusters):
    """Check financial rules independently of classify/formatter implementations."""
    def require(condition, message):
        if not condition:
            raise ValueError(f'Semantic audit: {message}')

    r, p = CONFIG['roles'], CONFIG['priority']
    raw = raw_nodes.set_index('gid')
    observed = nodes.set_index('gid').reindex(raw.index)
    require(nodes.gid.is_unique and set(nodes.gid) == set(raw.index), 'raw/exported gid mismatch')
    require(observed.is_seed.equals(raw.is_seed), 'seed flags differ from raw nodes')
    require(observed.depth.equals(raw.depth), 'depth differs from raw nodes')

    incoming, outgoing = {}, {}
    for edge in raw_edges.itertuples(index=False):
        incoming.setdefault(edge.dst, {})[edge.src] = float(edge.sum_kzt)
        outgoing.setdefault(edge.src, {})[edge.dst] = float(edge.sum_kzt)
    totals = pd.DataFrame(index=raw.index)
    totals['in_deg'] = [len(incoming.get(gid, {})) for gid in raw.index]
    totals['out_deg'] = [len(outgoing.get(gid, {})) for gid in raw.index]
    totals['in_kzt'] = [sum(incoming.get(gid, {}).values()) for gid in raw.index]
    totals['out_kzt'] = [sum(outgoing.get(gid, {}).values()) for gid in raw.index]
    for column in totals:
        require(np.allclose(observed[column], totals[column], rtol=0, atol=.000001),
                f'{column} differs from raw edges')
    truncated = (raw.depth == CONFIG['graph']['max_hops']) & (totals.out_deg == 0)
    require(np.array_equal(observed.truncated_by_depth, truncated), 'incorrect depth-boundary flags')
    external = ((totals.out_kzt >= CONFIG['external_funds']['min_out_kzt'])
                & ((totals.in_kzt == 0)
                   | (totals.out_kzt > CONFIG['external_funds']['max_pass_through'] * totals.in_kzt)))
    require(np.array_equal(observed.external_funds, external), 'incorrect external-funds flags')
    roles, seeds = observed.role.to_dict(), raw.is_seed.to_dict()
    coordinator_count = 0
    for gid, row in totals.iterrows():
        role = roles[gid]
        if role == 'coordinator':
            coordinator_count += 1
            require(not seeds[gid], f'coordinator {gid} is seed')
            require(row.in_deg >= r['coordinator_min_payers']
                    and row.out_deg >= r['coordinator_min_recipients'],
                    f'coordinator {gid} lacks required counterparties')
            collected = [amount for src, amount in incoming.get(gid, {}).items()
                         if src != gid and roles[src] == 'consolidator']
            distributed = [amount for dst, amount in outgoing.get(gid, {}).items()
                           if dst != gid and not seeds[dst]
                           and roles[dst] in {'consolidator', 'distributor'}]
            valid_in = (len(collected) >= r['coordinator_min_neighbors'] and row.in_kzt > 0
                        and sum(collected) / row.in_kzt >= r['coordinator_min_flow_share'])
            valid_out = (len(distributed) >= r['coordinator_min_neighbors'] and row.out_kzt > 0
                         and sum(distributed) / row.out_kzt >= r['coordinator_min_flow_share'])
            require(valid_in or valid_out, f'coordinator {gid} lacks final-role flow witnesses')
            continue

        # Conservative coordinator selection need not promote every potential
        # candidate; every remaining node must obey the ordered local rules.
        ratio = row.out_kzt / row.in_kzt if row.in_kzt else None
        usable = not seeds[gid] and not truncated[gid] and ratio is not None
        if row.out_deg >= r['distributor_min_recipients']:
            expected = 'distributor'
        elif row.in_deg >= r['consolidator_min_payers']:
            expected = 'consolidator'
        elif (usable and row.in_deg > 0 and row.out_deg > 0
              and r['transit_min_pass_through'] <= ratio <= r['transit_max_pass_through']):
            expected = 'transit'
        elif (usable and row.in_kzt >= r['terminal_min_in_kzt']
              and ratio <= r['terminal_max_pass_through']):
            expected = 'terminal'
        else:
            expected = 'peripheral'
        require(role == expected, f'{gid}: {role}, expected {expected} from raw flows')
        if row.out_deg >= r['distributor_large_fanout']:
            require(role == 'distributor', f'large fan-out {gid} was not assigned distributor')

    # Recompute average percentiles with sorted arrays, without pandas.rank or
    # cached base_priority_score/seed_priority_multiplier values.
    active = (totals.in_deg + totals.out_deg) > 0
    base = np.zeros(len(raw))
    values_by_signal = {
        'turnover': totals.in_kzt + totals.out_kzt,
        'seed_reach': observed.seed_reach,
        'betweenness': observed.betweenness,
        'degree': totals.in_deg + totals.out_deg,
        'pagerank': observed.pagerank,
    }
    if active.any():
        for signal, values in values_by_signal.items():
            sorted_values = np.sort(values[active].to_numpy())
            require(np.isfinite(values).all(), f'invalid priority signal {signal}')
            left = np.searchsorted(sorted_values, values, side='left')
            right = np.searchsorted(sorted_values, values, side='right')
            average_rank = (left + 1 + right) / 2
            percentile = np.where(active & (values > 0), average_rank / len(sorted_values), 0.)
            base += p['weights'][signal] * percentile
    role_factor = np.where(external & (observed.role == 'peripheral'),
                           p['external_peripheral_multiplier'], 1.)
    seed_factor = np.where(raw.is_seed, p['seed_multiplier'], 1.)
    expected_priority = np.round(base * role_factor * seed_factor, 6)
    require(np.allclose(base, observed.base_priority_score, rtol=0, atol=1e-12), 'base priority formula mismatch')
    require(np.array_equal(role_factor, observed.role_priority_multiplier), 'peripheral role multiplier mismatch')
    require(np.array_equal(seed_factor, observed.seed_priority_multiplier), 'seed multiplier mismatch')
    require(np.allclose(expected_priority, observed.priority_score, rtol=0, atol=1e-9), 'final priority formula mismatch')

    forbidden = re.compile(r'pagerank|betweenness|контр\.|посредничество\s+[-+]?\d+(?:[.,]\d+)?', re.I)
    for gid, row in observed.iterrows():
        require(isinstance(row.evidence, str)
                and 1 <= len(row.evidence) <= CONFIG['output']['evidence_max_chars'],
                f'{gid}: invalid evidence length')
        for label, text in [('evidence', row.evidence), ('why', row.priority_why)]:
            require(isinstance(text, str) and 'гипотез' in text.lower(), f'{gid}: {label} is not a hypothesis')
            require(forbidden.search(text) is None, f'{gid}: technical/raw wording in {label}')
        if external[gid]:
            require('источник средств вне выборки' in row.evidence.lower(), f'{gid}: missing external-source caveat')
        if seeds[gid] and p['seed_multiplier'] < 1:
            require('уже известен — приоритет снижен' in row.priority_why.lower(), f'{gid}: missing seed-priority explanation')
    require((top.role != 'peripheral').all(), 'peripheral present in top list')
    require(top.gid.is_unique and set(top.gid) <= set(raw.index), 'invalid top gids')
    for row in top.itertuples(index=False):
        require(row.why == observed.loc[row.gid, 'priority_why'], f'{row.gid}: exported why differs from debug')
        require(row.role == roles[row.gid], f'{row.gid}: wrong role in top')

    # A wholly truncated community must not receive a claimed financial purpose.
    for cid, group in observed.groupby('cluster_id'):
        if truncated.reindex(group.index).all():
            hypothesis = clusters.loc[clusters.cluster_id == cid, 'hypothesis']
            require(len(hypothesis) == 1 and
                    'назначение не определить, нужна выгрузка следующего колена' in hypothesis.iloc[0],
                    f'cluster {cid}: missing next-hop limitation')
    first_twenty = top.sort_values('rank').head(20)
    top_five = []
    for row in first_twenty.head(5).itertuples(index=False):
        top_five.append(dict(rank=int(row.rank), gid=row.gid, role=row.role,
                             priority_score=float(row.priority_score),
                             evidence=observed.loc[row.gid, 'evidence'], why=row.why))
    return dict(semantic_nodes_checked=len(nodes), coordinator_flow_witnesses_checked=coordinator_count,
                role_distribution={role: int(count) for role, count in nodes.role.value_counts().items()},
                top20_peripheral=int((first_twenty.role == 'peripheral').sum()),
                top20_seed=sum(bool(seeds[gid]) for gid in first_twenty.gid), top5=top_five)
