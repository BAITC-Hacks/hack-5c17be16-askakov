"""Cluster descriptions must be grounded in directed, internal transfers."""

import re
import unittest

import networkx as nx
import pandas as pd

from cluster_descriptions import cluster_hypothesis
from settings import CONFIG


class ClusterScenarioTests(unittest.TestCase):
    def gid(self, suffix):
        return str(100000000000000000 + suffix)

    def fixture(self, rows, transfers):
        """Rows: (id suffix, final role, seed, boundary); edges may be external."""
        subset = pd.DataFrame([
            dict(gid=self.gid(suffix), role=role, is_seed=seed, truncated_by_depth=boundary)
            for suffix, role, seed, boundary in rows
        ])
        graph = nx.DiGraph()
        graph.add_nodes_from(subset.gid)
        graph.add_edges_from((self.gid(src), self.gid(dst), {'sum_kzt': amount})
                             for src, dst, amount in transfers)
        return subset, graph, set(subset.gid)

    def assert_seed_count(self, text, count):
        self.assertRegex(text.lower(), rf'(?:seed[^0-9]*{count}\b|\b{count}\s*seed)')

    def test_entire_cluster_at_boundary_has_required_limitation(self):
        subset, graph, members = self.fixture([
            (1, 'peripheral', False, True), (2, 'consolidator', False, True),
        ], [])
        text = cluster_hypothesis(subset, graph, members)
        self.assertIn('назначение не определить, нужна выгрузка следующего колена', text)

    def test_isolated_seed_does_not_invent_a_flow(self):
        subset, graph, members = self.fixture([(1, 'peripheral', True, False)], [])
        text = cluster_hypothesis(subset, graph, members)
        self.assert_seed_count(text, 1)
        self.assertNotIn(self.gid(1), text)
        self.assertRegex(text.lower(), r'нет|не наблюда|не выявл|не обнаруж')

    def test_regular_chain_names_internal_collection_distribution_and_transit(self):
        subset, graph, members = self.fixture([
            (1, 'peripheral', True, False),
            (2, 'consolidator', False, False),
            (3, 'transit', False, False),
            (4, 'distributor', False, False),
            (5, 'terminal', False, False),
        ], [(1, 2, 100000.), (2, 3, 90000.), (3, 4, 80000.), (4, 5, 70000.)])
        text = cluster_hypothesis(subset, graph, members)
        self.assert_seed_count(text, 1)
        for suffix in (2, 3, 4):
            self.assertIn(self.gid(suffix), text)
        self.assertIn('транзит', text.lower())
        self.assertNotIn('назначение не определить', text)

    def test_external_only_roles_are_not_reported_as_internal_flow(self):
        subset, graph, members = self.fixture([
            (1, 'peripheral', True, False),
            (2, 'consolidator', False, False),
            (3, 'distributor', False, False),
            (4, 'transit', False, False),
            (5, 'coordinator', False, False),
        ], [(90, 2, 100000.), (3, 91, 90000.), (92, 4, 80000.),
            (4, 93, 70000.), (94, 5, 60000.), (5, 95, 50000.)])
        text = cluster_hypothesis(subset, graph, members)
        self.assert_seed_count(text, 1)
        for suffix in (2, 3, 4, 5, 90, 91, 92, 93, 94, 95):
            self.assertNotIn(self.gid(suffix), text)

    def test_transit_needs_both_internal_directions(self):
        for transfers in [[(1, 2, 50000.), (2, 90, 40000.)],
                          [(90, 2, 50000.), (2, 1, 40000.)]]:
            with self.subTest(transfers=transfers):
                subset, graph, members = self.fixture([
                    (1, 'peripheral', False, False), (2, 'transit', False, False),
                ], transfers)
                text = cluster_hypothesis(subset, graph, members)
                self.assertNotIn(self.gid(2), text)

    def test_collection_top_uses_internal_incoming_and_deterministic_ties(self):
        self.assertEqual(CONFIG['clusters']['description_top_nodes'], 3)
        rows = [(1, 'peripheral', True, False)] + [
            (suffix, 'consolidator', False, False) for suffix in (10, 11, 12, 13)
        ]
        # An enormous external incoming must not bring 13 into the internal top.
        transfers = [(1, 10, 40000.), (1, 11, 30000.), (1, 12, 30000.),
                     (1, 13, 20000.), (90, 13, 1000000000.)]
        subset, graph, members = self.fixture(rows, transfers)
        text = cluster_hypothesis(subset, graph, members)
        self.assertEqual(re.findall(r'\d{18}', text), [self.gid(i) for i in (10, 11, 12)])
        reversed_text = cluster_hypothesis(subset.iloc[::-1], graph, members)
        self.assertEqual(text, reversed_text)

    def test_distribution_top_uses_internal_outgoing_and_deterministic_ties(self):
        rows = [(1, 'peripheral', False, False)] + [
            (suffix, 'distributor', False, False) for suffix in (10, 11, 12, 13)
        ]
        transfers = [(10, 1, 40000.), (11, 1, 30000.), (12, 1, 30000.),
                     (13, 1, 20000.), (13, 90, 1000000000.)]
        subset, graph, members = self.fixture(rows, transfers)
        text = cluster_hypothesis(subset, graph, members)
        self.assertEqual(re.findall(r'\d{18}', text), [self.gid(i) for i in (10, 11, 12)])
        self.assertEqual(text, cluster_hypothesis(subset.iloc[::-1], graph, members))


if __name__ == '__main__':
    unittest.main()
