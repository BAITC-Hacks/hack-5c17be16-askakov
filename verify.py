"""Приёмка с пересчётом из Parquet в пустую папку."""
import argparse
import json
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import numpy as np
import pandas as pd

from pipeline import ROLES, ROLE_RULES, classify
from csv_io import read_csv
from documentation import check_readme
from semantic_checks import validate_semantics
from settings import CONFIG

ROOT = Path(__file__).resolve().parent
SCHEMAS = {
    'nodes_roles': ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence'],
    'clusters': ['cluster_id', 'n_nodes', 'n_seed', 'sum_kzt_internal', 'top_gids', 'hypothesis'],
    'top_nodes': ['rank', 'gid', 'role', 'priority_score', 'why'],
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate(data_dir, output_dir, expected_nodes=2248):
    raw_nodes = pd.read_parquet(data_dir / 'nodes.parquet')
    raw_edges = pd.read_parquet(data_dir / 'edges.parquet')
    raw_nodes['gid'] = raw_nodes.gid.astype(str)
    for column in ('src', 'dst'):
        raw_edges[column] = raw_edges[column].astype(str)
    frames = {}
    for name, columns in SCHEMAS.items():
        frame = read_csv(output_dir / f'{name}.csv')
        require(set(columns) <= set(frame), f'{name}: missing required columns')
        require(frame[columns].notna().all().all(), f'{name}: null values')
        for col in columns:
            if pd.api.types.is_string_dtype(frame[col]):
                require(frame[col].str.strip().ne('').all(), f'{name}: blank {col}')
        frames[name] = frame
    nodes, clusters, top = (frames[key] for key in SCHEMAS)
    require(nodes.columns.tolist() == SCHEMAS['nodes_roles'], 'nodes_roles: expected exactly six columns in the required order')
    debug = read_csv(output_dir / 'features_debug.csv')
    require('gid' in debug and debug.gid.notna().all() and debug.gid.is_unique, 'Invalid debug gids')
    require(set(debug.gid) == set(nodes.gid), 'Debug gids differ from nodes_roles')
    require(set(debug.columns) & set(nodes.columns) == {'gid'}, 'Debug export must not duplicate role output columns')
    nodes = nodes.merge(debug, on='gid', how='left', validate='one_to_one')
    require(len(nodes) == expected_nodes == len(raw_nodes), 'Wrong node count')
    require(nodes.gid.is_unique and set(nodes.gid) == set(raw_nodes.gid), 'Missing or duplicate gid')
    require(nodes.role.isin(ROLES).all(), 'Role outside vocabulary')
    require(nodes.gid.map(lambda value: isinstance(value, str)).all(), 'gid must be text')
    require(pd.api.types.is_integer_dtype(nodes.cluster_id), 'cluster_id must contain integers')
    for col in ['role_score', 'priority_score']:
        require(np.isfinite(nodes[col]).all() and nodes[col].between(0, 1).all(), f'Invalid {col}')
    require(nodes.evidence.str.len().between(1, CONFIG['output']['evidence_max_chars']).all(), 'Invalid evidence length')
    require(nodes.evidence.str.contains(r'\d').all(), 'Evidence must contain numeric observations')
    for row in nodes.itertuples(index=False):
        role, score = classify(row)
        require(role == row.role and abs(score - row.role_score) < 1e-9, f'Rule mismatch for {row.gid}')
        require(row.role_rule == ROLE_RULES[row.role], f'Missing formal rule for {row.gid}')
    require(not ((nodes.role == 'terminal') & nodes.truncated_by_depth).any(), 'Truncated terminal')
    require(clusters.cluster_id.is_unique and set(nodes.cluster_id) == set(clusters.cluster_id), 'Invalid cluster assignment')
    grouped = nodes.groupby('cluster_id').agg(n_nodes=('gid', 'size'), n_seed=('is_seed', 'sum'))
    cluster_rows = clusters.set_index('cluster_id').sort_index()
    require(grouped.n_nodes.equals(cluster_rows.n_nodes), 'Cluster size mismatch')
    require((grouped.n_seed == cluster_rows.n_seed).all(), 'Cluster seed count mismatch')
    mapping = nodes.set_index('gid').cluster_id
    src_cluster, dst_cluster = raw_edges.src.map(mapping), raw_edges.dst.map(mapping)
    internal = raw_edges.loc[src_cluster == dst_cluster].groupby(src_cluster).sum_kzt.sum()
    expected = internal.reindex(cluster_rows.index, fill_value=0)
    require(np.allclose(expected, cluster_rows.sum_kzt_internal, rtol=0, atol=.01), 'Cluster turnover mismatch')
    require(len(top) >= 20 and top.gid.is_unique, 'Insufficient or duplicate top nodes')
    require(top['rank'].tolist() == list(range(1, len(top) + 1)), 'Invalid ranks')
    require((top.role != 'peripheral').all(), 'Peripheral node in top list')
    ordered = nodes.loc[nodes.role != 'peripheral'].sort_values(['priority_score', 'gid'], ascending=[False, True]).head(len(top))
    require(top.gid.tolist() == ordered.gid.tolist(), 'Priority ordering mismatch')
    require(top.role.tolist() == ordered.role.tolist(), 'Top roles mismatch')
    require(np.allclose(top.priority_score, ordered.priority_score, rtol=0, atol=1e-9), 'Top scores mismatch')
    require(len(top) == min(CONFIG['priority']['top_n'], int((nodes.role != 'peripheral').sum())), 'Wrong top list size')
    semantic = validate_semantics(raw_nodes, raw_edges, nodes, top, clusters)
    graph = json.loads((output_dir / 'graph.json').read_text())
    require({n['gid'] for n in graph['nodes']} == {str(gid) for gid in raw_nodes.gid}, 'JSON loses gid precision')
    expected_edges = {(str(row.src), str(row.dst)) for row in raw_edges.itertuples(index=False)}
    require({(e['src'], e['dst']) for e in graph['edges']} == expected_edges, 'Missing directed edges')
    seed_ids = {str(gid) for gid in raw_nodes.loc[raw_nodes.is_seed, 'gid']}
    for row in nodes.itertuples(index=False):
        paths = graph['seed_paths'][str(row.gid)]
        require(len(paths) == row.seed_reach, 'Path count differs from seed reach')
        require(len({path[0] for path in paths}) == len(paths), 'Duplicate path source')
        for path in paths:
            require(2 <= len(path) <= CONFIG['graph']['max_hops'] + 1 and len(set(path)) == len(path), 'Invalid path length or cycle')
            require(path[0] in seed_ids and path[-1] == str(row.gid), 'Wrong path endpoints')
            require(all(pair in expected_edges for pair in zip(path, path[1:])), 'Path reverses or invents an edge')
    raw_tx = pd.read_parquet(data_dir / 'transactions.parquet')
    raw_tx['date'] = pd.to_datetime(raw_tx.date)
    expected_tx = {}
    for tx in raw_tx.itertuples(index=False):
        expected_tx.setdefault((str(tx.src), str(tx.dst)), []).append((tx.date.isoformat(), tx.sum_kzt))
    for edge in graph['edges']:
        actual = sorted((tx['date'], tx['sum_kzt']) for tx in edge['transactions'])
        require(actual == sorted(expected_tx[(edge['src'], edge['dst'])]), 'Individual transactions differ from source')
        require(len(actual) == edge['n_tx'], 'Transaction count mismatch in UI')
        require(abs(sum(value for date, value in actual) - edge['sum_kzt']) < .01, 'Transaction sum mismatch in UI')
    return dict(nodes=len(nodes), clusters=len(clusters), top_nodes=len(top), edges=len(graph['edges']),
                rules_checked=len(nodes), truncated_nodes=int(nodes.truncated_by_depth.sum()),
                paths_checked=sum(map(len, graph['seed_paths'].values())), transactions_checked=len(raw_tx), **semantic)


def main():
    parser = argparse.ArgumentParser(description='Приёмка обязательных выгрузок на свежем расчёте')
    parser.add_argument('--data', type=Path, default=ROOT / 'data')
    parser.add_argument('--expected-nodes', type=int, default=2248)
    parser.add_argument('--report', type=Path, default=ROOT / 'output/acceptance_report.json')
    args = parser.parse_args()
    require(check_readme(), 'README/config differ; run documentation.py')
    with tempfile.TemporaryDirectory(prefix='money-graph-check-') as directory:
        started = time.perf_counter()
        subprocess.run([sys.executable, str(ROOT / 'pipeline.py'), '--data', str(args.data.resolve()), '--out', directory],
                       check=True, timeout=300, capture_output=True, text=True)
        elapsed = time.perf_counter() - started
        require(elapsed < 300, 'Pipeline exceeded 5 minutes')
        result = validate(args.data.resolve(), Path(directory), args.expected_nodes)
    result.update(status='PASS', pipeline_seconds=round(elapsed, 2),
                  readme_config='PASS',
                  scope='Fresh pipeline; independent raw-flow role checks, priority formula, role distribution, top-20 composition and full top-5 explanations; CSV schemas, clusters and directed paths/transactions. Browser UI checked separately.')
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
