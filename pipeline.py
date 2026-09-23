"""Расчёт ролей, кластеров и приоритета проверки."""
import argparse
import json
import time
from pathlib import Path

import networkx as nx
import numpy as np
import pandas as pd

from investigation import edge_details, seed_paths
from settings import CONFIG, role_rules
from explanations import describe_role, describe_priority
from cluster_descriptions import cluster_hypothesis
from csv_io import write_csv

ROLES = ('consolidator', 'transit', 'distributor', 'terminal', 'coordinator', 'peripheral')
ROLE_RULES = role_rules()


def load_data(directory):
    directory = Path(directory)
    nodes = pd.read_parquet(directory / 'nodes.parquet')
    edges = pd.read_parquet(directory / 'edges.parquet')
    tx = pd.read_parquet(directory / 'transactions.parquet')
    for frame, columns, name in [(nodes, ['gid', 'depth', 'is_seed'], 'nodes'),
                                  (edges, ['src', 'dst', 'sum_kzt', 'n_tx', 'depth'], 'edges'),
                                  (tx, ['src', 'dst', 'sum_kzt', 'date'], 'transactions')]:
        if not set(columns) <= set(frame.columns) or frame[columns].isna().any().any():
            raise ValueError(f'{name}: missing columns or null values')
    if nodes.empty or nodes.gid.duplicated().any() or edges.duplicated(['src', 'dst']).any():
        raise ValueError('Empty nodes, duplicate gids or duplicate edges')
    for series in [nodes.gid, edges.src, edges.dst, tx.src, tx.dst, nodes.depth, edges.depth, edges.n_tx]:
        if not pd.api.types.is_integer_dtype(series):
            raise ValueError(f'{series.name}: expected integer values')
    if not nodes.is_seed.isin([True, False]).all() or not nodes.depth.between(0, 4).all():
        raise ValueError('Invalid is_seed or depth')
    nodes['is_seed'] = nodes.is_seed.astype(bool)
    ids = set(nodes.gid)
    if not (set(edges.src) | set(edges.dst) | set(tx.src) | set(tx.dst)) <= ids:
        raise ValueError('Transaction or edge refers to an unknown gid')
    for values in [edges.sum_kzt, tx.sum_kzt]:
        if not np.isfinite(values).all() or (values <= 0).any():
            raise ValueError('Amounts must be finite and positive')
    if (edges.n_tx <= 0).any():
        raise ValueError('n_tx must be positive')
    tx['date'] = pd.to_datetime(tx.date, errors='raise')
    agg = tx.groupby(['src', 'dst']).agg(tx_sum=('sum_kzt', 'sum'), tx_count=('sum_kzt', 'size')).reset_index()
    check = edges.merge(agg, on=['src', 'dst'], how='outer', indicator=True, validate='one_to_one')
    if not (check['_merge'] == 'both').all() or not np.allclose(check.sum_kzt, check.tx_sum, rtol=0, atol=.01) or not (check.n_tx == check.tx_count).all():
        raise ValueError('Edges do not match transaction pairs, amounts or counts')
    return nodes.sort_values('gid').reset_index(drop=True), edges.sort_values(['src', 'dst']), tx


def qualifies_coordinator(row):
    incoming = (getattr(row, 'incoming_consolidator_count', 0) >= CONFIG['roles']['coordinator_min_neighbors']
                and getattr(row, 'incoming_consolidator_share', 0) >= CONFIG['roles']['coordinator_min_flow_share'])
    outgoing = (getattr(row, 'outgoing_hub_count', 0) >= CONFIG['roles']['coordinator_min_neighbors']
                and getattr(row, 'outgoing_hub_share', 0) >= CONFIG['roles']['coordinator_min_flow_share'])
    return bool(not row.is_seed and row.in_deg >= CONFIG['roles']['coordinator_min_payers'] and row.out_deg >= CONFIG['roles']['coordinator_min_recipients'] and (incoming or outgoing))


def coordinator_features(graph, features):
    """Исключаем взаимное подтверждение ролей между кандидатами в координаторы."""
    base = {gid: ('distributor' if row.out_deg >= CONFIG['roles']['distributor_min_recipients'] else 'consolidator' if row.in_deg >= CONFIG['roles']['consolidator_min_payers'] else 'other')
            for gid, row in features.iterrows()}
    seeds = features.is_seed.to_dict()

    def measure(excluded):
        records = {}
        for gid, row in features.iterrows():
            incoming = [src for src in graph.predecessors(gid)
                        if src != gid and src not in excluded and base[src] == 'consolidator']
            outgoing = [dst for dst in graph.successors(gid)
                        if dst != gid and dst not in excluded and not seeds[dst]
                        and base[dst] in {'consolidator', 'distributor'}]
            incoming_sum = sum(graph[src][gid]['sum_kzt'] for src in incoming)
            outgoing_sum = sum(graph[gid][dst]['sum_kzt'] for dst in outgoing)
            records[gid] = dict(incoming_consolidator_count=len(incoming),
                incoming_consolidator_share=incoming_sum / row.in_kzt if row.in_kzt else 0.,
                outgoing_hub_count=len(outgoing), outgoing_hub_share=outgoing_sum / row.out_kzt if row.out_kzt else 0.,
                incoming_consolidator_gids=json.dumps([str(src) for src in sorted(incoming)]),
                outgoing_hub_gids=json.dumps([str(dst) for dst in sorted(outgoing)]))
        return pd.DataFrame.from_dict(records, orient='index')

    preliminary = features.join(measure(set()))
    candidates = {gid for gid, row in preliminary.iterrows() if qualifies_coordinator(row)}
    result = features.join(measure(candidates))
    result['base_hub_role'] = pd.Series(base)
    result['hub_anchor'] = pd.Series({gid: base[gid] != 'other' and gid not in candidates for gid in graph})
    result['coordinator_eligible'] = result.apply(qualifies_coordinator, axis=1)
    return result


def analyze(nodes, edges, tx):
    graph = nx.DiGraph()
    graph.add_nodes_from(int(gid) for gid in nodes.gid)
    for row in edges.itertuples(index=False):
        graph.add_edge(int(row.src), int(row.dst), sum_kzt=float(row.sum_kzt), n_tx=int(row.n_tx))
    undirected = nx.Graph()
    undirected.add_nodes_from(graph)
    for src, dst, attrs in graph.edges(data=True):
        previous = undirected.get_edge_data(src, dst, {}).get('weight', 0)
        undirected.add_edge(src, dst, weight=previous + attrs['sum_kzt'])
    connected = undirected.subgraph([gid for gid, degree in undirected.degree() if degree > 0])
    communities = list(nx.community.louvain_communities(connected, weight='weight', seed=CONFIG['graph']['random_seed'], resolution=CONFIG['graph']['louvain_resolution'])) if connected.number_of_edges() else []
    communities += [{gid} for gid in nx.isolates(undirected)]
    communities.sort(key=lambda members: (-len(members), min(members)))
    cluster_map = {gid: index for index, members in enumerate(communities) for gid in members}
    features = nodes.copy().set_index('gid')
    for column, values in [('in_deg', graph.in_degree()), ('out_deg', graph.out_degree()),
                           ('in_kzt', graph.in_degree(weight='sum_kzt')), ('out_kzt', graph.out_degree(weight='sum_kzt')),
                           ('in_tx', graph.in_degree(weight='n_tx')), ('out_tx', graph.out_degree(weight='n_tx'))]:
        features[column] = pd.Series(dict(values))
    features['cluster_id'] = pd.Series(cluster_map)
    features['pagerank'] = pd.Series(nx.pagerank(graph, weight='sum_kzt'))
    # Расстояние — число переходов. Сумма перевода здесь не подходит.
    features['betweenness'] = pd.Series(nx.betweenness_centrality(graph, k=min(CONFIG['graph']['betweenness_samples'], len(graph)), seed=CONFIG['graph']['random_seed'], weight=None))
    features['seed_reach'] = 0
    for seed in nodes.loc[nodes.is_seed, 'gid']:
        reached = set(nx.single_source_shortest_path_length(graph, int(seed), cutoff=CONFIG['graph']['max_hops'])) - {seed}
        if reached:
            features.loc[list(reached), 'seed_reach'] += 1
    features['neighbor_clusters'] = pd.Series({gid: len({cluster_map[n] for n in set(graph.predecessors(gid)) | set(graph.successors(gid))}) for gid in graph})
    features['truncated_by_depth'] = (features.depth == CONFIG['graph']['max_hops']) & (features.out_deg == 0)
    features['pass_through'] = features.out_kzt.div(features.in_kzt.replace(0, np.nan))
    features['external_funds'] = ((features.out_kzt >= CONFIG['external_funds']['min_out_kzt'])
        & (features.out_kzt > CONFIG['external_funds']['max_pass_through'] * features.in_kzt))
    features['ratio_usable'] = (~features.is_seed) & (~features.truncated_by_depth) & (features.in_kzt > 0) & (features.pass_through <= CONFIG['roles']['transit_max_pass_through'])
    # Близость дат не доказывает, что дальше ушли те же деньги.
    incoming_dates = {gid: np.sort(group.date.to_numpy(dtype='datetime64[ns]')) for gid, group in tx.groupby('dst')}
    proximity = {}
    for gid, group in tx.groupby('src'):
        incoming = incoming_dates.get(gid)
        if incoming is None or len(incoming) == 0:
            proximity[gid] = 0.0
            continue
        outgoing = group.date.to_numpy(dtype='datetime64[ns]')
        indices = np.searchsorted(incoming, outgoing, side='right') - 1
        deltas = outgoing - incoming[np.maximum(indices, 0)]
        proximity[gid] = float(((indices >= 0) & (deltas <= np.timedelta64(CONFIG['temporal']['window_days'], 'D'))).mean())
    features['outgoing_with_recent_incoming'] = pd.Series(proximity).reindex(features.index).fillna(0)
    features['bridge_percentile'] = features.betweenness.rank(pct=True)
    features = coordinator_features(graph, features)
    rows = []
    for gid, row in features.iterrows():
        role, score = classify(row)
        rows.append((role, score, describe_role(row, role)))
    features[['role', 'role_score', 'evidence']] = rows
    features['role_score'] = features.role_score.astype(float)
    features['role_rule'] = features.role.map(ROLE_RULES)
    # Изоляты не участвуют в перцентилях и получают нулевой приоритет.
    active = (features.in_deg + features.out_deg) > 0
    priority = pd.Series(0.0, index=features.index)
    weights = CONFIG['priority']['weights']
    signals = {'turnover': features.in_kzt + features.out_kzt, 'seed_reach': features.seed_reach,
               'betweenness': features.betweenness, 'degree': features.in_deg + features.out_deg,
               'pagerank': features.pagerank}
    for name, values in signals.items():
        ranks = values[active].rank(method='average', pct=True)
        ranks.loc[values[active] == 0] = 0
        features[f'priority_percentile_{name}'] = ranks.reindex(features.index, fill_value=0.)
        component = weights[name] * features[f'priority_percentile_{name}']
        features[f'priority_component_{name}'] = component
        priority += component
    features['base_priority_score'] = priority
    features['role_priority_multiplier'] = np.where(
        features.external_funds & (features.role == 'peripheral'),
        CONFIG['priority']['external_peripheral_multiplier'], 1.)
    discount_seed = features.is_seed & features.role.isin(CONFIG['priority']['seed_discount_roles'])
    features['seed_priority_multiplier'] = np.where(discount_seed, CONFIG['priority']['seed_multiplier'], 1.)
    features['priority_score'] = (priority * features.role_priority_multiplier * features.seed_priority_multiplier).round(6)
    for name, values in [('turnover', features.in_kzt + features.out_kzt), ('degree', features.in_deg + features.out_deg)]:
        features[f'{name}_higher_than_pct'] = 0
        if active.any():
            # Для текста «больше, чем у N%» считаем только строго меньшие значения.
            features.loc[active, f'{name}_higher_than_pct'] = np.floor((values[active].rank(method='min') - 1) / active.sum() * 100).astype(int)
    features['priority_why'] = features.apply(describe_priority, axis=1)
    features = features.reset_index()
    clusters = []
    for cid, members in enumerate(communities):
        subset = features[features.cluster_id == cid]
        internal = sum(attrs['sum_kzt'] for src in members for dst, attrs in graph[src].items() if dst in members)
        clusters.append(dict(cluster_id=cid, n_nodes=len(members), n_seed=int(subset.is_seed.sum()),
                             sum_kzt_internal=float(internal), top_gids=';'.join(map(str, subset.sort_values(['priority_score', 'gid'], ascending=[False, True]).head(CONFIG['clusters']['top_gids_count']).gid)),
                             hypothesis=cluster_hypothesis(subset, graph, members)))
    return graph, undirected, features, pd.DataFrame(clusters)


def classify(row):
    """Первая подходящая роль. Скор отражает силу признаков, а не вероятность нарушения."""
    score = CONFIG['role_scores']
    if qualifies_coordinator(row):
        return 'coordinator', score['coordinator_base'] + (score['coordinator_bridge_bonus'] * row.bridge_percentile if row.betweenness > 0 else 0)
    if row.out_deg >= CONFIG['roles']['distributor_min_recipients']:
        return 'distributor', score['distributor_base'] + score['distributor_bonus'] * min(row.out_deg / score['distributor_saturation_recipients'], 1)
    if row.in_deg >= CONFIG['roles']['consolidator_min_payers']:
        return 'consolidator', (score['consolidator_base'] + score['consolidator_bonus'] * min(row.in_deg / score['consolidator_saturation_payers'], 1)) * (score['truncated_consolidator_multiplier'] if row.truncated_by_depth else 1)
    if row.ratio_usable and row.in_deg >= 1 and row.out_deg >= 1 and CONFIG['roles']['transit_min_pass_through'] <= row.pass_through <= CONFIG['roles']['transit_max_pass_through']:
        return 'transit', score['transit_base'] + score['transit_temporal_bonus'] * row.outgoing_with_recent_incoming
    if (not row.is_seed and not row.truncated_by_depth and row.in_deg >= 1
            and row.in_kzt >= CONFIG['roles']['terminal_min_in_kzt']
            and row.pass_through <= CONFIG['roles']['terminal_max_pass_through']):
        return 'terminal', score['terminal']
    return 'peripheral', score['peripheral']


def run(data_dir='data', out_dir='output'):
    started = time.perf_counter()
    nodes, edges, tx = load_data(data_dir)
    graph, undirected, features, clusters = analyze(nodes, edges, tx)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    required = ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence']
    features = features[required + [c for c in features if c not in required]]
    write_csv(features[required], out / 'nodes_roles.csv')
    debug_columns = ['gid'] + [column for column in features if column not in required]
    write_csv(features[debug_columns], out / 'features_debug.csv')
    write_csv(clusters, out / 'clusters.csv')
    top = features.loc[features.role != 'peripheral'].sort_values(['priority_score', 'gid'], ascending=[False, True]).head(CONFIG['priority']['top_n']).copy()
    top['rank'] = range(1, len(top) + 1)
    top['why'] = top.priority_why
    write_csv(top[['rank', 'gid', 'role', 'priority_score', 'why']], out / 'top_nodes.csv')
    # Координаты считаем здесь, чтобы браузеру не пришлось раскладывать граф.
    layout = nx.spring_layout(undirected, seed=CONFIG['graph']['random_seed'], iterations=CONFIG['graph']['layout_iterations'], weight=None)
    features['x'] = features.gid.map(lambda gid: round(float(layout[gid][0]), 6))
    features['y'] = features.gid.map(lambda gid: round(float(layout[gid][1]), 6))
    meta = dict(n_nodes=len(nodes), n_edges=len(edges), n_transactions=len(tx), n_seed=int(nodes.is_seed.sum()),
                total_kzt=float(edges.sum_kzt.sum()), n_clusters=len(clusters),
                n_components=nx.number_weakly_connected_components(graph), n_isolates=nx.number_of_isolates(graph),
                n_truncated=int(features.truncated_by_depth.sum()),
                period_start=str(tx.date.min().date()) if len(tx) else None,
                period_end=str(tx.date.max().date()) if len(tx) else None,
                elapsed_seconds=round(time.perf_counter() - started, 2))
    # 18-значный gid теряет точность в JS Number, поэтому передаём строку.
    web_nodes, web_top = features.copy(), top[['gid', 'rank', 'why']].copy()
    web_nodes['gid'] = web_nodes.gid.astype(str)
    web_top['gid'] = web_top.gid.astype(str)
    payload = dict(meta=meta, nodes=json.loads(web_nodes.to_json(orient='records')),
                   edges=edge_details(edges, tx), seed_paths=seed_paths(graph, nodes.loc[nodes.is_seed, 'gid'], max_hops=CONFIG['graph']['max_hops']),
                   clusters=json.loads(clusters.to_json(orient='records')), top=json.loads(web_top.to_json(orient='records')))
    (out / 'graph.json').write_text(json.dumps(payload, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print(json.dumps(meta, ensure_ascii=False, indent=2))
    return payload


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--data', default='data')
    parser.add_argument('--out', default='output')
    args = parser.parse_args()
    run(args.data, args.out)
