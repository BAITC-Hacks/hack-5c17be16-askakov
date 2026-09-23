import unittest

import networkx as nx
import pandas as pd

from investigation import edge_details, seed_paths


class InvestigationTests(unittest.TestCase):
    def test_direction_depth_cycles_and_isolated_seed(self):
        graph = nx.DiGraph([(1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (3, 1), (8, 1)])
        graph.add_node(7)
        paths = seed_paths(graph, [1, 7])
        self.assertEqual(paths['5'], [['1', '2', '3', '4', '5']])
        self.assertEqual(paths['6'], [])  # Five transitions are outside the limit.
        self.assertEqual(paths['8'], [])  # Incoming edge cannot be followed backwards.
        self.assertEqual(paths['1'], [])  # A cycle is not another seed source.
        self.assertEqual(paths['7'], [])  # Isolated seeds are preserved.

    def test_ties_and_multiple_seeds_are_deterministic(self):
        edges = [(1, 3), (3, 4), (1, 2), (2, 4), (5, 4), (5, 1)]
        first = seed_paths(nx.DiGraph(edges), [5, 1])
        second = seed_paths(nx.DiGraph(reversed(edges)), [1, 5])
        self.assertEqual(first, second)
        self.assertEqual(first['4'], [['5', '4'], ['1', '2', '4']])
        self.assertEqual(first['1'], [['5', '1']])

    def test_transactions_preserve_dates_cents_duplicates_and_direction(self):
        a, b = 100000000000000001, 100000000000000002
        tx = pd.DataFrame({'src': [a, a, a, b], 'dst': [b, b, b, a],
                           'date': pd.to_datetime(['2026-07-03', '2026-07-01', '2026-07-01', '2026-07-02']),
                           'sum_kzt': [5000.01, 6000., 6000., 7000.]})
        edges = tx.groupby(['src', 'dst']).agg(sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size')).reset_index()
        edges['depth'] = 1
        details = edge_details(edges, tx)
        outgoing = next(edge for edge in details if edge['src'] == str(a))
        self.assertEqual(outgoing['dst'], str(b))
        self.assertEqual(outgoing['n_tx'], 3)
        self.assertEqual(len(outgoing['transactions']), 3)
        self.assertEqual(outgoing['first_date'], '2026-07-01T00:00:00')
        self.assertEqual(outgoing['last_date'], '2026-07-03T00:00:00')
        self.assertAlmostEqual(sum(tx['sum_kzt'] for tx in outgoing['transactions']), 17000.01)
        self.assertEqual(details[1]['transactions'][0]['sum_kzt'], 7000.)


if __name__ == '__main__':
    unittest.main()
