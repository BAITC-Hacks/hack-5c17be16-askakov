import tempfile
import unittest
from pathlib import Path

import pandas as pd

from pipeline import analyze, classify, load_data, run


class PipelineTests(unittest.TestCase):
    def fixture(self):
        nodes = pd.DataFrame({'gid': [1, 2, 3, 4, 5], 'depth': [0, 1, 2, 4, 0], 'is_seed': [True, False, False, False, True]})
        tx = pd.DataFrame({'src': [1, 2, 1], 'dst': [2, 3, 4], 'sum_kzt': [10000., 9000., 6000.], 'date': pd.to_datetime(['2026-07-01', '2026-07-02', '2026-07-03'])})
        edges = tx.groupby(['src', 'dst']).agg(sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size')).reset_index()
        edges['depth'] = 1
        return nodes, edges, tx

    def test_boundary_isolate_and_temporal_transit(self):
        nodes, edges, tx = self.fixture()
        graph, _, features, clusters = analyze(nodes, edges, tx)
        f = features.set_index('gid')
        self.assertEqual(len(graph), 5)
        self.assertEqual(f.loc[4, 'role'], 'peripheral')
        self.assertTrue(f.loc[4, 'truncated_by_depth'])
        self.assertEqual(f.loc[3, 'role'], 'peripheral')  # Одного поступления на 9 000 ₸ недостаточно для terminal.
        self.assertEqual(f.loc[2, 'role'], 'transit')
        self.assertEqual(f.loc[2, 'outgoing_with_recent_incoming'], 1)
        self.assertEqual(f.loc[5, 'priority_score'], 0)
        self.assertEqual(clusters.n_nodes.sum(), 5)
        self.assertTrue(features.evidence.str.len().between(1, 200).all())

    def test_reversed_edges_sum_in_projection(self):
        nodes, edges, tx = self.fixture()
        reverse_tx = pd.DataFrame({'src': [2], 'dst': [1], 'sum_kzt': [7000.], 'date': pd.to_datetime(['2026-07-03'])})
        tx = pd.concat([tx, reverse_tx], ignore_index=True)
        reverse_edge = pd.DataFrame({'src': [2], 'dst': [1], 'sum_kzt': [7000.], 'n_tx': [1], 'depth': [1]})
        edges = pd.concat([edges, reverse_edge], ignore_index=True)
        _, projection, _, _ = analyze(nodes, edges, tx)
        self.assertEqual(projection[1][2]['weight'], 17000)

    def test_seed_ratio_cannot_imply_transit(self):
        row = pd.Series(dict(in_deg=1, out_deg=1, seed_reach=0, neighbor_clusters=1, betweenness=0,
                             bridge_percentile=.2, ratio_usable=False, pass_through=1,
                             is_seed=True, truncated_by_depth=False, outgoing_with_recent_incoming=1))
        self.assertEqual(classify(row)[0], 'peripheral')

    def test_amount_and_count_mismatch_rejected(self):
        for column in ('sum_kzt', 'n_tx'):
            with self.subTest(column=column), tempfile.TemporaryDirectory() as tmp:
                nodes, edges, tx = self.fixture()
                edges.loc[0, column] += 1
                for name, frame in [('nodes', nodes), ('edges', edges), ('transactions', tx)]:
                    frame.to_parquet(Path(tmp) / f'{name}.parquet', index=False)
                with self.assertRaisesRegex(ValueError, 'do not match'):
                    load_data(tmp)

    def test_missing_endpoint_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes, edges, tx = self.fixture()
            nodes = nodes[nodes.gid != 3]
            for name, frame in [('nodes', nodes), ('edges', edges), ('transactions', tx)]:
                frame.to_parquet(Path(tmp) / f'{name}.parquet', index=False)
            with self.assertRaisesRegex(ValueError, 'unknown gid'):
                load_data(tmp)

    def test_json_preserves_large_identifiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes, edges, tx = self.fixture()
            offset = 100000000000000000
            nodes['gid'] += offset
            for frame in (edges, tx):
                frame['src'] += offset
                frame['dst'] += offset
            for name, frame in [('nodes', nodes), ('edges', edges), ('transactions', tx)]:
                frame.to_parquet(Path(tmp) / f'{name}.parquet', index=False)
            payload = run(tmp, Path(tmp) / 'out')
            self.assertEqual({n['gid'] for n in payload['nodes']}, {str(offset + i) for i in range(1, 6)})
            self.assertTrue(all(isinstance(e['src'], str) and isinstance(e['dst'], str) for e in payload['edges']))
            self.assertTrue(all(isinstance(n['gid'], str) for n in payload['top']))
            # 18-значный gid записываем в кавычках, без округления.
            self.assertTrue((Path(tmp) / 'out/nodes_roles.csv').read_text().splitlines()[1].startswith(f'"{offset + 1}",'))

    def test_role_export_is_separate_from_debug_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            nodes, edges, tx = self.fixture()
            for name, frame in [('nodes', nodes), ('edges', edges), ('transactions', tx)]:
                frame.to_parquet(Path(tmp) / f'{name}.parquet', index=False)
            output = Path(tmp) / 'results'
            payload = run(tmp, output)
            roles = pd.read_csv(output / 'nodes_roles.csv', dtype={'gid': str})
            debug = pd.read_csv(output / 'features_debug.csv', dtype={'gid': str})
            self.assertEqual(roles.columns.tolist(), ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence'])
            self.assertEqual(set(roles.columns) & set(debug.columns), {'gid'})
            merged = roles.merge(debug, on='gid', validate='one_to_one').set_index('gid')
            self.assertEqual(set(merged.index), {node['gid'] for node in payload['nodes']})
            # Метрики графа должны сохраниться в CSV, кроме координат отрисовки.
            for node in payload['nodes']:
                for column, value in node.items():
                    if column in {'gid', 'x', 'y'}:
                        continue
                    actual = merged.loc[node['gid'], column]
                    if value is None:
                        self.assertTrue(pd.isna(actual))
                    elif isinstance(value, float):
                        self.assertAlmostEqual(actual, value, places=8)
                    else:
                        self.assertEqual(actual, value)


if __name__ == '__main__':
    unittest.main()
