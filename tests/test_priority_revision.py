"""Приоритет и совмещение ролей на небольших графах."""

import unittest
from unittest.mock import patch

import pandas as pd

from explanations import describe_priority, describe_role
from pipeline import analyze
from settings import CONFIG


class PriorityRevisionTests(unittest.TestCase):
    def features(self, transfers, seeds=()):
        ids = sorted({gid for src, dst, _ in transfers for gid in (src, dst)})
        nodes = pd.DataFrame({
            'gid': ids, 'depth': [1] * len(ids),
            'is_seed': [gid in seeds for gid in ids],
        })
        tx = pd.DataFrame(transfers, columns=['src', 'dst', 'sum_kzt'])
        tx['date'] = pd.Timestamp('2026-07-01')
        edges = tx.groupby(['src', 'dst']).agg(
            sum_kzt=('sum_kzt', 'sum'), n_tx=('sum_kzt', 'size'),
        ).reset_index()
        edges['depth'] = 1
        return analyze(nodes, edges, tx)[2].set_index('gid')

    def role_row(self, **overrides):
        row = dict(
            in_deg=9, out_deg=8, in_kzt=900000., out_kzt=720000.,
            ratio_usable=True, pass_through=.8, external_funds=False,
            truncated_by_depth=False, is_seed=False,
            incoming_consolidator_count=0, incoming_consolidator_share=0.,
            outgoing_hub_count=2, outgoing_hub_share=.25,
        )
        row.update(overrides)
        return pd.Series(row)

    def test_seed_collection_and_distribution_keep_full_priority(self):
        examples = {
            'consolidator': [(payer, 1, 100000.) for payer in range(10, 15)],
            'distributor': [(1, recipient, 100000.) for recipient in range(10, 18)],
        }
        for role, transfers in examples.items():
            with self.subTest(role=role):
                with patch.dict(CONFIG['priority'], seed_multiplier=1.):
                    full = self.features(transfers, seeds=(1,)).loc[1]
                with patch.dict(CONFIG['priority'], seed_multiplier=.7):
                    actual = self.features(transfers, seeds=(1,)).loc[1]
                self.assertEqual(actual.role, role)
                self.assertEqual(actual.seed_priority_multiplier, 1.)
                self.assertEqual(actual.priority_score, full.priority_score)
                self.assertIn(
                    'уже известен правоохранителям, но является точкой сбора/раздачи — ключ к уровню выше',
                    actual.priority_why.lower(),
                )
                self.assertNotIn('уже известен — приоритет снижен', actual.priority_why.lower())

    def test_seed_without_hub_role_retains_configured_penalty(self):
        transfers = [(2, 1, 10000.), (1, 3, 10000.)]
        with patch.dict(CONFIG['priority'], seed_multiplier=1.):
            full = self.features(transfers, seeds=(1,)).loc[1]
        with patch.dict(CONFIG['priority'], seed_multiplier=.7):
            reduced = self.features(transfers, seeds=(1,)).loc[1]
        self.assertEqual(reduced.role, 'peripheral')
        self.assertEqual(reduced.seed_priority_multiplier, .7)
        self.assertAlmostEqual(reduced.priority_score, full.priority_score * .7, delta=1e-6)
        self.assertIn('уже известен — приоритет снижен', reduced.priority_why.lower())
        self.assertNotIn('ключ к уровню выше', reduced.priority_why.lower())

    def test_non_seed_priority_is_unaffected_by_seed_multiplier(self):
        transfers = [(2, 1, 10000.), (1, 3, 10000.)]
        with patch.dict(CONFIG['priority'], seed_multiplier=1.):
            full = self.features(transfers).loc[1]
        with patch.dict(CONFIG['priority'], seed_multiplier=.7):
            actual = self.features(transfers).loc[1]
        self.assertEqual(actual.role, 'transit')
        self.assertEqual(actual.seed_priority_multiplier, 1.)
        self.assertEqual(actual.priority_score, full.priority_score)
        self.assertNotIn('уже известен', actual.priority_why.lower())

    def test_seed_explanation_distinguishes_all_six_roles(self):
        # Проверяем и сочетания, которые классификатор сейчас не назначает:
        # текст должен соответствовать переданной роли и множителю.
        for role in ('consolidator', 'distributor', 'coordinator', 'peripheral', 'terminal', 'transit'):
            with self.subTest(role=role):
                is_hub = role in {'consolidator', 'distributor', 'coordinator'}
                row = pd.Series(dict(
                    role=role, evidence='Гипотеза: роль требует проверки.',
                    turnover_higher_than_pct=80, degree_higher_than_pct=90,
                    seed_reach=2, role_priority_multiplier=1., is_seed=True,
                    seed_priority_multiplier=1. if is_hub else .7,
                ))
                text = describe_priority(row).lower()
                self.assertEqual('ключ к уровню выше' in text, is_hub)
                self.assertEqual('уже известен — приоритет снижен' in text, not is_hub)

    def test_mixed_distributor_mentions_collection_immediately_after_hypothesis(self):
        text = describe_role(self.role_row(), 'distributor')
        self.assertTrue(text.lower().startswith(
            'гипотеза: веерная раздача. также признаки консолидации: 9 плательщиков.'
        ))
        self.assertLessEqual(len(text), CONFIG['output']['evidence_max_chars'])

    def test_mixed_consolidator_mentions_distribution(self):
        text = describe_role(self.role_row(), 'consolidator')
        self.assertTrue(text.lower().startswith(
            'гипотеза: признаки консолидации. также признаки раздачи: 8 получателей.'
        ))
        self.assertLessEqual(len(text), CONFIG['output']['evidence_max_chars'])

    def test_secondary_role_requires_both_configured_thresholds(self):
        payers = CONFIG['roles']['consolidator_min_payers']
        recipients = CONFIG['roles']['distributor_min_recipients']
        for in_deg, out_deg, expected in [
            (payers - 1, recipients, False),
            (payers, recipients - 1, False),
            (payers, recipients, True),
        ]:
            with self.subTest(in_deg=in_deg, out_deg=out_deg):
                text = describe_role(self.role_row(in_deg=in_deg, out_deg=out_deg), 'distributor')
                self.assertEqual('также признаки консолидации' in text.lower(), expected)

    def test_mixed_coordinator_keeps_flow_proof_and_external_source_within_limit(self):
        row = self.role_row(
            in_deg=9, out_deg=42, in_kzt=1110000., out_kzt=7610000.,
            external_funds=True, ratio_usable=False,
            outgoing_hub_count=7, outgoing_hub_share=.24,
        )
        text = describe_role(row, 'coordinator')
        self.assertTrue(text.lower().startswith(
            'гипотеза: координация. также признаки консолидации: 9 плательщиков.'
        ))
        self.assertIn('7 хабов', text)
        self.assertIn('24%', text)
        self.assertIn('источник средств вне выборки', text.lower())
        self.assertLessEqual(len(text), CONFIG['output']['evidence_max_chars'])

    def test_mixed_role_is_present_in_pipeline_evidence(self):
        transfers = [(payer, 1, 100000.) for payer in range(10, 19)]
        transfers += [(1, recipient, 90000.) for recipient in range(20, 28)]
        row = self.features(transfers).loc[1]
        self.assertEqual(row.role, 'distributor')
        self.assertIn('также признаки консолидации: 9 плательщиков', row.evidence.lower())
        self.assertLessEqual(len(row.evidence), CONFIG['output']['evidence_max_chars'])

    def test_sink_consolidator_receives_full_degree_component_without_betweenness(self):
        # Восемь одинаковых плательщиков переводят одному получателю.
        # Он первый по четырём взвешенным метрикам, но посредничество нулевое.
        transfers = [(payer, 1, 100000.) for payer in range(10, 18)]
        row = self.features(transfers, seeds=(10,)).loc[1]
        self.assertEqual(row.role, 'consolidator')
        self.assertEqual(row.in_deg, 8)
        self.assertEqual(row.out_deg, 0)
        self.assertEqual(row.betweenness, 0.)
        self.assertEqual(row.priority_percentile_degree, 1.)
        self.assertAlmostEqual(row.priority_component_degree, .4)
        self.assertEqual(row.priority_component_betweenness, 0.)
        components = [row[f'priority_component_{name}'] for name in
                      ('turnover', 'seed_reach', 'betweenness', 'degree', 'pagerank')]
        self.assertAlmostEqual(sum(components), 1.)
        self.assertAlmostEqual(row.base_priority_score, 1.)
        self.assertEqual(row.priority_score, 1.)

    def test_tied_percentiles_and_seed_penalty_apply_to_the_component_sum(self):
        transfers = [(payer, 1, 100000.) for payer in range(10, 18)]
        row = self.features(transfers, seeds=(10,)).loc[10]
        # Плательщики делят места 1–8 из 9: средний ранг 4,5 / 9 = 0,5.
        # Нулевые seed_reach и betweenness не получают баллов за равенство рангов.
        expected = {
            'turnover': (.5, .125), 'seed_reach': (0., 0.),
            'betweenness': (0., 0.), 'degree': (.5, .2), 'pagerank': (.5, .05),
        }
        for name, (percentile, component) in expected.items():
            with self.subTest(signal=name):
                self.assertAlmostEqual(row[f'priority_percentile_{name}'], percentile)
                self.assertAlmostEqual(row[f'priority_component_{name}'], component)
        self.assertEqual(row.role, 'peripheral')
        self.assertAlmostEqual(row.base_priority_score, .375)
        self.assertAlmostEqual(sum(row[f'priority_component_{name}'] for name in expected), .375)
        self.assertEqual(row.role_priority_multiplier, .5)
        self.assertEqual(row.seed_priority_multiplier, .7)
        self.assertEqual(row.priority_score, .13125)


if __name__ == '__main__':
    unittest.main()
