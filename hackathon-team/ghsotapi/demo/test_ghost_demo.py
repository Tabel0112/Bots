import copy
import tempfile
import unittest
from pathlib import Path

from ghost_demo import connect, run_task, simulated_discovery, replay, verify


def task(query='headphones', price=150):
    return {'schema_version': '0.1', 'request_id': 'test', 'site_id': 'demo-catalog',
            'operation': 'search_products', 'parameters': {'query': query, 'max_price': price}, 'mode': 'auto'}


class WorkflowLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / 'test.sqlite3'
        self.db = connect(self.path)

    def tearDown(self):
        self.db.close()
        self.directory.cleanup()

    def test_discovery_persistence_reuse_and_empty_results(self):
        first = run_task(self.db, task())
        self.assertEqual(first['mode'], 'exploration')
        self.assertEqual(first['workflow_status'], 'qualified')
        self.assertEqual(len(first['items']), 2)
        self.db.close()
        self.db = connect(self.path)
        second = run_task(self.db, task('keyboard', 100))
        self.assertEqual(second['mode'], 'reuse')
        self.assertEqual(second['version'], first['version'])
        self.assertEqual({p['title'] for p in second['items']}, {'Mechanical keyboard', 'Compact keyboard'})
        empty = run_task(self.db, task('headphones', 50))
        self.assertEqual(empty['items'], [])
        self.assertEqual(empty['validation']['status'], 'passed')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM workflow_runs').fetchone()[0], 6)

    def test_candidates_not_reused(self):
        first = run_task(self.db, task(), auto_qualify=False)
        second = run_task(self.db, task('keyboard'), auto_qualify=False)
        self.assertEqual(first['workflow_status'], 'candidate')
        self.assertEqual(second['mode'], 'exploration')
        self.assertEqual(second['version'], 2)

    def test_unknown_inputs_and_nonfinite_price_rejected(self):
        invalid = task()
        invalid['parameters']['free_shipping'] = True
        with self.assertRaisesRegex(ValueError, 'cannot be ignored'):
            run_task(self.db, invalid)
        for price in [float('nan'), float('inf'), -1, True]:
            with self.assertRaises(ValueError):
                run_task(self.db, task(price=price))

    def test_unsupported_site_does_not_create_workflow(self):
        unsupported = task()
        unsupported['site_id'] = 'unknown-site'
        result = run_task(self.db, unsupported)
        self.assertEqual(result['error'], 'UNSUPPORTED_DISCOVERY')
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM workflows').fetchone()[0], 0)

    def test_validator_detects_stale_bindings_and_missing_results(self):
        request = task()
        definition = simulated_discovery(request)
        items, evidence = replay(definition, request['parameters'])
        self.assertEqual(verify(request['parameters'], items[:1], evidence)['status'], 'failed')
        stale = copy.deepcopy(evidence)
        stale['applied_inputs']['query'] = 'keyboard'
        self.assertEqual(verify(request['parameters'], items, stale)['status'], 'failed')


if __name__ == '__main__':
    unittest.main()
