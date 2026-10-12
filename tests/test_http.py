import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
import http.cookiejar
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import server


@unittest.skipUnless(hasattr(server, 'STORE'), '待续：HTTP会话和领域接口尚未接入；这是下一轮验收用例')
class HttpTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.dbpatch = patch.object(server, 'STORE', server.Store(self.temp.name + '/demo.db'))
        self.dbpatch.start()
        self.http = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)
        self.thread.start()
        self.base = 'http://127.0.0.1:' + str(self.http.server_port)
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def tearDown(self):
        self.http.shutdown()
        self.http.server_close()
        self.dbpatch.stop()
        self.temp.cleanup()

    def request(self, path, body=None, header=True, origin=None):
        headers = {'Content-Type': 'application/json'}
        if header:
            headers['X-Demo-Request'] = '1'
        if origin:
            headers['Origin'] = origin
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.base + path, data=data, headers=headers)
        try:
            resp = self.opener.open(req)
        except urllib.error.HTTPError as exc:
            resp = exc
        with resp:
            return resp.status, json.loads(resp.read())

    def login(self, name='operator'):
        status, data = self.request('/api/login', {'username': name, 'password': 'demo2026'})
        self.assertEqual(status, 200, data)
        return data

    def test_auth_and_same_origin_guards(self):
        self.assertEqual(self.request('/api/state')[0], 401)
        self.assertEqual(self.request('/api/login', {'username': 'operator', 'password': 'wrong'})[0], 401)
        self.assertEqual(self.request('/api/login', {}, header=False)[0], 403)
        self.assertEqual(self.request('/api/login', {}, origin='https://attacker.invalid')[0], 403)

    def test_client_role_cannot_bypass_backend(self):
        self.login('reader')
        status, _ = self.request('/api/propose', {'role': 'operator', 'scenario': 'counter',
            'changes': [{'field': 'limit', 'value': 200000}], 'reason': 'test'})
        self.assertEqual(status, 403)
        status, data = self.request('/api/state')
        self.assertTrue(all(c['environment'] == 'test' for c in data['configs']))

    def test_confirmation_execute_readback_and_workspace_switch(self):
        first = self.login()
        status, data = self.request('/api/propose', {'scenario': 'integration',
            'changes': [{'field': 'timeout', 'value': 60}, {'field': 'retries', 'value': 1}],
            'reason': 'approved simulated change', 'submit': True})
        self.assertEqual(status, 200, data)
        p = data['proposal']
        status, executed = self.request('/api/execute', {'proposal_id': p['id'], 'digest': p['digest']})
        self.assertEqual(status, 200, executed)
        self.assertTrue(executed['ok'])
        second = self.login('reviewer')
        self.assertEqual(first['workspace'], second['workspace'])
        self.assertEqual(len(second['tickets']), 1)

    def test_model_failure_has_no_business_side_effects(self):
        self.login()
        with patch.object(server, 'model_call', side_effect=TimeoutError('fixture timeout')):
            status, data = self.request('/api/chat', {'scenario': 'integration', 'text': 'change timeout'})
        self.assertIn(status, (502, 503))
        state = self.request('/api/state')[1]
        self.assertEqual(len(state['tickets']), 0)
        self.assertEqual(len(state['proposals']), 0)

    def test_guard_is_independent_of_model(self):
        self.login()
        status, data = self.request('/api/safety-test', {'case': 'production'})
        self.assertEqual(status, 200, data)
        self.assertTrue(data['result']['passed'])

    def test_chat_enforces_read_before_propose_and_cites(self):
        self.login()
        def msg(calls):
            return {'choices': [{'message': {'tool_calls': [
                {'id': str(i), 'type': 'function', 'function': {'name': n, 'arguments': json.dumps(a)}}
                for i, (n, a) in enumerate(calls)]}}]}
        responses = [msg([('propose_change', {'changes': [{'field': 'timeout', 'value': 60}], 'reason': 'fixture'})]),
                     msg([('search_knowledge', {'query': 'integration'}), ('read_configuration', {})]),
                     msg([('propose_change', {'changes': [{'field': 'timeout', 'value': 60}], 'reason': 'fixture', 'submit': True})])]
        with patch.object(server, 'model_call', side_effect=responses), patch.dict(server.CONFIG, {'OPENAI_API_KEY': 'fixture-only', 'OPENAI_API_BASE_URL': 'https://example.invalid/v1'}):
            status, data = self.request('/api/chat', {'scenario': 'integration', 'text': 'change timeout to sixty seconds'})
        self.assertEqual(status, 200, data)
        self.assertEqual(data['source'], 'live-model')
        self.assertTrue(data['citations'])
        self.assertTrue(data['proposal'])
        self.assertTrue(any('先' in str(t) for t in data['trace']))


if __name__ == '__main__':
    unittest.main()
