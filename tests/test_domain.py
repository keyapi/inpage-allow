import tempfile
import unittest
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from domain import Store, DomainError


class DomainTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(str(Path(self.tmp.name) / 'state.sqlite'))

    def tearDown(self):
        self.store.db.close()
        self.tmp.cleanup()

    def propose(self, **kw):
        return self.store.propose('one', 'operator', 'integration',
                                  [{'field': 'timeout', 'value': 60}, {'field': 'retries', 'value': 1}],
                                  '根据有效规范与复盘调整联调配置', **kw)

    def config(self, workspace='one', scenario='integration', env='test'):
        return next(c for c in self.store.snapshot(workspace, 'operator')['configs']
                    if c['scenario'] == scenario and c['environment'] == env)

    def assertCode(self, code, fn):
        with self.assertRaises(DomainError) as raised:
            fn()
        self.assertEqual(raised.exception.code, code)

    def test_propose_does_not_write_execute_readbacks_and_is_idempotent(self):
        p = self.propose()
        self.assertEqual(self.config()['values']['timeout'], 30)
        result = self.store.execute('one', 'operator', p['id'], p['digest'])
        self.assertTrue(result['ok'])
        self.assertEqual(self.config()['values'], {'timeout': 60, 'retries': 1})
        self.assertEqual(self.config(env='prod')['values']['timeout'], 30)
        self.store.execute('one', 'operator', p['id'], p['digest'])
        self.assertEqual(len(self.store.snapshot('one', 'operator')['tickets']), 1)
        self.assertEqual(self.config()['version'], 2)

    def test_workspace_and_persistent_state(self):
        p = self.propose()
        self.store.execute('one', 'operator', p['id'], p['digest'])
        self.assertEqual(self.config('other')['values']['timeout'], 30)
        new = Store(self.store.path)
        self.assertEqual(new.snapshot('one', 'operator')['configs'][0]['version'], 2)
        new.db.close()
        self.assertCode('NOT_FOUND', lambda: self.store.execute('other', 'operator', p['id'], p['digest']))

    def test_permissions_are_rechecked_at_execution(self):
        p = self.propose()
        self.assertCode('FORBIDDEN', lambda: self.store.execute('one', 'reader', p['id'], p['digest']))
        self.assertCode('FORBIDDEN', lambda: self.store.execute('one', 'reviewer', p['id'], p['digest']))
        self.assertTrue(all(c['environment'] == 'test' for c in self.store.snapshot('one', 'reader')['configs']))

    def test_tamper_stale_expired_cancelled(self):
        p = self.propose()
        self.assertCode('DIGEST_MISMATCH', lambda: self.store.execute('one', 'operator', p['id'], 'fake'))
        other = self.propose(submit=False)
        self.store.execute('one', 'operator', other['id'], other['digest'])
        self.assertCode('STALE', lambda: self.store.execute('one', 'operator', p['id'], p['digest']))
        p = self.store.propose('two', 'operator', 'counter', [{'field': 'limit', 'value': 150000}], 'test')
        with patch('domain.time.time', return_value=p['expires_at'] + 1):
            self.assertCode('EXPIRED', lambda: self.store.execute('two', 'operator', p['id'], p['digest']))
        p = self.store.propose('three', 'operator', 'counter', [{'field': 'limit', 'value': 150000}], 'test')
        self.store.cancel('three', 'operator', p['id'])
        self.assertCode('CANCELLED', lambda: self.store.execute('three', 'operator', p['id'], p['digest']))

    def test_structured_validation_fails_closed(self):
        for changes in ([{'field': 'timeout', 'value': 999}], [{'field': 'password', 'value': 1}],
                        [{'field': 'timeout', 'value': True}], [{'field': 'timeout', 'value': float('nan')}],
                        [{'field': 'timeout', 'value': 60, 'environment': 'prod'}],
                        [{'field': 'timeout', 'value': 60}] * 2):
            self.assertCode('INVALID_CHANGE', lambda: self.store.propose('one', 'operator', 'integration', changes, 'x'))

    def test_noop_does_not_submit_production_ticket(self):
        p = self.store.propose('one', 'operator', 'counter', [{'field': 'limit', 'value': 500000}], 'x')
        result = self.store.execute('one', 'operator', p['id'], p['digest'])
        self.assertEqual(result['code'], 'NO_CHANGE')
        self.assertEqual(self.store.snapshot('one', 'operator')['tickets'], [])

    def test_rollback_requires_fresh_confirmation(self):
        p = self.propose()
        self.store.execute('one', 'operator', p['id'], p['digest'])
        restore = self.store.rollback('one', 'operator', p['id'])
        self.assertEqual(self.config()['values']['timeout'], 60)
        self.store.execute('one', 'operator', restore['id'], restore['digest'])
        self.assertEqual(self.config()['values']['timeout'], 30)

    def test_review_changes_request_only(self):
        p = self.propose()
        self.store.execute('one', 'operator', p['id'], p['digest'])
        ticket = self.store.snapshot('one', 'operator')['tickets'][0]
        self.assertCode('FORBIDDEN', lambda: self.store.review('one', 'operator', ticket['id'], True))
        self.store.review('one', 'reviewer', ticket['id'], True)
        self.assertEqual(self.config(env='prod')['values']['timeout'], 30)

    def test_all_safety_cases_isolated_and_real_guards(self):
        before = self.store.snapshot('one', 'operator')['configs']
        for case in ('unauthorized', 'tamper', 'stale', 'repeat', 'injection', 'production', 'unknown', 'failure'):
            with self.subTest(case=case):
                self.assertTrue(self.store.safety_test('one', 'operator', case)['passed'])
        self.assertEqual(self.store.snapshot('one', 'operator')['configs'], before)

    def test_knowledge_filters_invalid_documents_before_retrieval(self):
        docs = self.store.knowledge('超时', 'reader')
        self.assertTrue(docs)
        self.assertTrue(all(d['status'] == 'active' and d['environment'] != 'prod' for d in docs))
        self.assertTrue(all('忽略规则' not in d['content'] for d in docs))

    def test_parallel_execute_writes_once(self):
        p = self.propose()
        with ThreadPoolExecutor(max_workers=4) as workers:
            results = list(workers.map(lambda _: self.store.execute('one', 'operator', p['id'], p['digest']), range(8)))
        self.assertEqual(sum(r['code'] == 'EXECUTED' for r in results), 1)
        self.assertEqual(len(self.store.snapshot('one', 'operator')['tickets']), 1)

    def test_storage_abort_rolls_back_config_ticket_and_proposal(self):
        p = self.propose()
        before = self.store.snapshot('one', 'operator')
        self.store.db.execute("CREATE TEMP TRIGGER fail_save BEFORE INSERT ON workspaces BEGIN SELECT RAISE(ABORT, 'test failure'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.execute('one', 'operator', p['id'], p['digest'])
        self.store.db.execute('DROP TRIGGER fail_save')
        after = self.store.snapshot('one', 'operator')
        for key in ('configs', 'tickets', 'proposals'):
            self.assertEqual(after[key], before[key])

    def test_reset_cancels_pending_proposals_and_keeps_audit(self):
        p = self.propose()
        self.store.reset('one', 'operator')
        self.assertCode('CANCELLED', lambda: self.store.execute('one', 'operator', p['id'], p['digest']))
        self.assertTrue(any(a['action'] == 'reset' for a in self.store.audit('one', 'operator')))

    def test_rollback_does_not_overwrite_later_changes(self):
        p = self.propose()
        self.store.execute('one', 'operator', p['id'], p['digest'])
        later = self.store.propose('one', 'operator', 'integration', [{'field': 'timeout', 'value': 45}], 'later', False)
        self.store.execute('one', 'operator', later['id'], later['digest'])
        self.assertCode('STALE', lambda: self.store.rollback('one', 'operator', p['id']))
        self.assertEqual(self.config()['values']['timeout'], 45)


if __name__ == '__main__':
    unittest.main()
