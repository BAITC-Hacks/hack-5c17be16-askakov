"""Semantic examples for the audit rules, independent of the supplied dataset."""

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from pipeline import analyze, classify, run
from settings import CONFIG


class AuditSemanticsTests(unittest.TestCase):
    def frames(self, transfers, seeds=(), depths=None, extra_nodes=()):
        depths = depths or {}
        ids = sorted(set(extra_nodes) | {gid for src, dst, _ in transfers for gid in (src, dst)})
        nodes = pd.DataFrame({
            'gid': ids,
            'depth': [depths.get(gid, 1) for gid in ids],
            'is_seed': [gid in seeds for gid in ids],
        })
        tx = pd.DataFrame(transfers, columns=['src', 'dst', 'sum_kzt'])
        tx['date'] = pd.Timestamp('2026-07-01')
        edges = tx.groupby(['src', 'dst']).agg(
            sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size'),
        ).reset_index()
        edges['depth'] = 1
        return nodes, edges, tx

    def features(self, transfers, **kwargs):
        result = analyze(*self.frames(transfers, **kwargs))[2]
        return result.assign(gid=result.gid.astype(str)).set_index('gid')

    def coordinator_incoming(self, count=2, other_incoming=80000., outgoing=15000.):
        transfers = []
        for hub in range(10, 10 + count):
            # The payers have no incoming edges, so these hubs cannot coordinate.
            transfers.extend((hub * 100 + payer, hub, 10000.) for payer in range(5))
            transfers.append((hub, 1, 20000. / count))
        transfers.extend((payer, 1, other_incoming / 2) for payer in (2, 3))
        transfers.extend((1, recipient, outgoing / 3) for recipient in (4, 5, 6))
        return transfers

    def coordinator_outgoing(self, count=2, other_outgoing=80000., incoming=15000.):
        transfers = []
        for hub in range(10, 10 + count):
            # Eight leaf recipients make each hub an unambiguous distributor.
            transfers.extend((hub, hub * 100 + recipient, 5000.) for recipient in range(8))
            transfers.append((1, hub, 20000. / count))
        transfers.extend((1, recipient, other_outgoing / 2) for recipient in (2, 3))
        transfers.extend((payer, 1, incoming / 3) for payer in (4, 5, 6))
        return transfers

    def test_coordinator_incoming_count_and_share_boundaries(self):
        for count, other, expected in [(2, 80000., True), (2, 80001., False), (1, 80000., False)]:
            with self.subTest(count=count, other_incoming=other):
                features = self.features(self.coordinator_incoming(count, other))
                self.assertEqual(features.loc['1', 'role'] == 'coordinator', expected)
                for hub in range(10, 10 + count):
                    self.assertEqual(features.loc[str(hub), 'role'], 'consolidator')

    def test_coordinator_outgoing_count_and_share_boundaries(self):
        for count, other, expected in [(2, 80000., True), (2, 80001., False), (1, 80000., False)]:
            with self.subTest(count=count, other_outgoing=other):
                features = self.features(self.coordinator_outgoing(count, other))
                self.assertEqual(features.loc['1', 'role'] == 'coordinator', expected)
                for hub in range(10, 10 + count):
                    self.assertEqual(features.loc[str(hub), 'role'], 'distributor')

    def test_coordinator_share_uses_the_corresponding_direction(self):
        # A much larger opposite-direction flow must not dilute the qualifying 20%.
        incoming = self.features(self.coordinator_incoming(outgoing=1000000.))
        outgoing = self.features(self.coordinator_outgoing(incoming=1000000.))
        self.assertEqual(incoming.loc['1', 'role'], 'coordinator')
        self.assertEqual(outgoing.loc['1', 'role'], 'coordinator')

    def test_seed_is_never_coordinator(self):
        for transfers in [self.coordinator_incoming(), self.coordinator_outgoing()]:
            with self.subTest(direction=transfers[0]):
                features = self.features(transfers, seeds=(1,))
                self.assertNotEqual(features.loc['1', 'role'], 'coordinator')

    def test_seed_outgoing_hub_does_not_count(self):
        features = self.features(self.coordinator_outgoing(), seeds=(10,))
        self.assertEqual(features.loc['10', 'role'], 'distributor')
        self.assertNotEqual(features.loc['1', 'role'], 'coordinator')

    def test_seed_incoming_consolidator_is_allowed(self):
        # The non-seed restriction on neighbors applies to outgoing hubs only.
        features = self.features(self.coordinator_incoming(), seeds=(10,))
        self.assertEqual(features.loc['10', 'role'], 'consolidator')
        self.assertEqual(features.loc['1', 'role'], 'coordinator')

    def test_betweenness_supports_score_but_does_not_gate_coordinator(self):
        row = pd.Series(dict(
            in_deg=3, out_deg=3, in_kzt=100000., out_kzt=15000., is_seed=False,
            truncated_by_depth=False, pass_through=0., ratio_usable=True,
            outgoing_with_recent_incoming=0., seed_reach=0, neighbor_clusters=1,
            coordinator_eligible=True, incoming_consolidator_count=2,
            incoming_consolidator_share=.2, outgoing_hub_count=0, outgoing_hub_share=0.,
            betweenness=0., bridge_percentile=.1,
        ))
        low_role, low_score = classify(row)
        row['betweenness'], row['bridge_percentile'] = .1, .999
        high_role, high_score = classify(row)
        self.assertEqual((low_role, high_role), ('coordinator', 'coordinator'))
        self.assertGreaterEqual(high_score, low_score)

    def test_large_fanout_without_qualifying_hubs_is_distributor(self):
        features = self.features([(1, recipient, 5000.) for recipient in range(10, 70)])
        self.assertEqual(features.loc['1', 'out_deg'], 60)
        self.assertEqual(features.loc['1', 'role'], 'distributor')

    def test_consolidator_requires_five_distinct_payers(self):
        for payers, expected in [(4, False), (5, True)]:
            with self.subTest(payers=payers):
                transfers = [(payer, 1, 10000.) for payer in range(10, 10 + payers)]
                features = self.features(transfers)
                self.assertEqual(features.loc['1', 'role'] == 'consolidator', expected)

    def test_terminal_amount_and_retention_boundaries(self):
        examples = [
            (20000., 0., True),
            (20000., 4000., True),
            (20000., 4001., False),
            (19999., 0., False),
            (5000., 0., False),
        ]
        for incoming, outgoing, expected in examples:
            with self.subTest(incoming=incoming, outgoing=outgoing):
                transfers = [(2, 1, incoming)]
                if outgoing:
                    transfers.append((1, 3, outgoing))
                features = self.features(transfers)
                self.assertEqual(features.loc['1', 'role'] == 'terminal', expected)
                if incoming < 20000:
                    self.assertEqual(features.loc['1', 'role'], 'peripheral')

    def test_terminal_excludes_seed_and_depth_boundary(self):
        for options in [dict(seeds=(1,)), dict(depths={1: 4})]:
            with self.subTest(options=options):
                features = self.features([(2, 1, 50000.)], **options)
                self.assertNotEqual(features.loc['1', 'role'], 'terminal')

    def test_external_funds_amount_and_ratio_boundaries(self):
        examples = [(20000., 24000., False), (20000., 24001., True),
                    (5000., 19999., False), (5000., 20000., True), (0., 20000., True)]
        for incoming, outgoing, expected in examples:
            with self.subTest(incoming=incoming, outgoing=outgoing):
                transfers = [(1, 3, outgoing)]
                if incoming:
                    transfers.append((2, 1, incoming))
                features = self.features(transfers)
                self.assertEqual('источник средств вне выборки' in features.loc['1', 'evidence'].lower(), expected)

    def test_external_peripheral_priority_multiplier_is_applied(self):
        transfers = [(2, 1, 5000.), (1, 3, 25000.)]
        with patch.dict(CONFIG['priority'], external_peripheral_multiplier=1.):
            full = self.features(transfers).loc['1']
        with patch.dict(CONFIG['priority'], external_peripheral_multiplier=.5):
            reduced = self.features(transfers).loc['1']
        self.assertEqual(full.role, 'peripheral')
        self.assertEqual(reduced.role, 'peripheral')
        self.assertAlmostEqual(reduced.priority_score, full.priority_score * .5, delta=1e-6)

    def test_seed_distributor_keeps_priority_and_why_explains_it(self):
        transfers = [(1, recipient, 25000.) for recipient in range(10, 18)]
        original = self.features(transfers, seeds=(99,), extra_nodes=(99,)).loc['1']
        seed = self.features(transfers, seeds=(1, 99), extra_nodes=(99,)).loc['1']
        self.assertEqual(original.role, 'distributor')
        self.assertEqual(seed.role, 'distributor')
        self.assertAlmostEqual(seed.priority_score, original.priority_score, delta=1e-6)
        payload, roles, top = self.run_fixture(transfers, seeds=(1, 99), extra_nodes=(99,))
        why = top.loc[top.gid == '1', 'why'].iloc[0]
        self.assertIn('уже известен правоохранителям, но является точкой сбора/раздачи — ключ к уровню выше', why.lower())

    def run_fixture(self, transfers, **kwargs):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            frames = self.frames(transfers, **kwargs)
            for name, frame in zip(('nodes', 'edges', 'transactions'), frames):
                frame.to_parquet(directory / f'{name}.parquet', index=False)
            output = directory / 'results'
            payload = run(directory, output)
            roles = pd.read_csv(output / 'nodes_roles.csv', dtype={'gid': str})
            top = pd.read_csv(output / 'top_nodes.csv', dtype={'gid': str})
        return payload, roles, top

    def test_top_excludes_peripheral_and_explanations_are_readable(self):
        transfers = self.coordinator_incoming(outgoing=1000000.)
        transfers += [(20, recipient, 25000.) for recipient in range(30, 38)]
        payload, roles, top = self.run_fixture(transfers, seeds=(20,))
        self.assertGreater(len(top), 0)
        self.assertNotIn('peripheral', set(top.role))
        self.assertEqual(set(top.gid), {node['gid'] for node in payload['top']})
        self.assertTrue(roles.evidence.str.len().between(1, 200).all())
        for text in [*roles.evidence, *top.why]:
            self.assertNotRegex(text.lower(), r'pagerank|betweenness|контр\.|посредничество\s+0[.,]')
            self.assertIn('гипотез', text.lower())


if __name__ == '__main__':
    unittest.main()
