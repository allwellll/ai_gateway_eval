import asyncio
import json
import os
from pathlib import Path
import re
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.app import app, EvaluationResult
from backend.db import connection
from backend.evaluator import extract_answer, summarize
from backend.modeltrace import load_bank
from backend.routing import endpoint_for
from backend.transport import StreamParser, UpstreamError, check_address, text_from_json


class PureTests(unittest.TestCase):
    def test_shared_url_cases(self):
        for url, model, expected in json.loads(Path('tests/routing-cases.json').read_text()):
            with self.subTest(url=url, model=model):
                self.assertEqual(endpoint_for(url, model), expected)
        for url in ('https://u:p@example.com', 'https://example.com?key=secret', 'file:///etc/passwd'):
            with self.assertRaises(ValueError): endpoint_for(url, 'gpt-6-astra')

    def test_answer_is_final_conclusion(self):
        for text, expected in [('21', 21), ('21 不足以保证。最终答案是 28。', 28),
                               ('先考虑 21，最终答案：29 个糖果。', 29), (r'答案为 $\boxed{21}$。', 21),
                               ('题目包含 21 这个数字，但未得出结论。', None), ('无法回答', None)]:
            self.assertEqual(extract_answer(text), expected)

    def test_stream_ignores_reasoning_and_rejects_truncation(self):
        parser = StreamParser()
        parser.feed(json.dumps({'type':'content_block_delta','delta':{'type':'thinking_delta','thinking':'21'}}))
        parser.feed(json.dumps({'type':'content_block_delta','delta':{'type':'text_delta','text':'答案是28'}}))
        with self.assertRaises(UpstreamError): parser.text()
        parser.feed('{"type":"message_stop"}')
        self.assertEqual(parser.text(), '答案是28')
        parser = StreamParser()
        parser.feed('{"type":"response.output_text.delta","delta":"21"}')
        parser.feed('{"type":"response.completed","response":{"output":[{"content":[{"type":"output_text","text":"21"}]}]}}')
        self.assertEqual(parser.text(), '21')
        with self.assertRaises(UpstreamError): StreamParser().feed('{"type":"response.incomplete"}')
        self.assertEqual(text_from_json({'content':[{'type':'thinking','thinking':'wrong'},{'type':'text','text':'21'}]}), '21')

    def test_model_and_candy_verdicts_are_independent(self):
        bank = load_bank()
        fp = [{'text':'[1,2,3]', 'expected_count':3} for _ in range(3)]
        with patch('backend.evaluator.analyze_global_outputs', return_value={'prediction':'claude-opus-5-5','probability':.99}):
            result = summarize('opus-5-5', fp, [{'text':'最终答案是28'}], bank, True)
            self.assertEqual(result['verdict'], '真')
            self.assertEqual(result['candy'], '0/1')
            self.assertEqual(result['candy_answers'], '28')
            self.assertEqual(summarize('opus-5-5', fp, [{'text':'21'}], bank, False)['verdict'], '存疑')
            malformed = [{'text':'[1,2]', 'expected_count':3} for _ in range(3)]
            result = summarize('opus-5-5', malformed, [{'text':'21'}], bank, True)
            self.assertEqual(result['verdict'], '存疑')
            self.assertEqual(result['fingerprint'], '❌❌❌')
        failures = [{'error':'HTTP 502', 'expected_count':3} for _ in range(3)]
        result = summarize('gpt-6-astra', failures, [{'error':'HTTP 502'}], bank, True)
        self.assertEqual(result['verdict'], '无法评测')
        self.assertEqual(result['candy'], '0/0')

    def test_private_addresses_and_shanghai_date(self):
        with patch.dict(os.environ, {'ALLOWED_PRIVATE_HOSTS':''}):
            for address in ('127.0.0.1','172.17.29.212','169.254.169.254','::1','10.0.0.1'):
                with self.assertRaises(ValueError): check_address(address, address)
            check_address('8.141.2.179','public.example')
        result = EvaluationResult(domain='example.com', model='gpt-6-astra', tested_at='2026-10-01T18:00:00Z')
        self.assertEqual(str(result.tested_date), '2026-10-02')


@unittest.skipUnless(os.getenv('RUN_DB_TESTS') == '1', 'Set RUN_DB_TESTS=1 for PostgreSQL integration tests')
class DatabaseTests(unittest.TestCase):
    def setUp(self):
        self.domain = 'test-' + uuid4().hex + '.invalid'
        self.context = TestClient(app)
        self.client = self.context.__enter__()

    def tearDown(self):
        self.context.__exit__(None, None, None)
        with connection() as conn:
            conn.execute('DELETE FROM ai_gateway_eval_results WHERE domain=%s', (self.domain,))

    def test_create_query_idempotency_and_validation(self):
        result = {'result_uuid':str(uuid4()),'domain':self.domain,'model':'gpt-6-astra', 'tested_at':'2026-10-01T18:00:00Z','candy':'1/1','candy_answers':'21'}
        for _ in range(2):
            response = self.client.post('/api/results',json={'results':[result]})
            self.assertEqual(response.status_code,200,response.text)
        data = self.client.get('/api/results',params={'domain':self.domain,'date_from':'2026-10-02','date_to':'2026-10-02'}).json()
        self.assertEqual(data['total'],1)
        self.assertEqual(data['items'][0]['tested_date'],'2026-10-02')
        self.assertEqual(self.client.get('/api/results',params={'domain':"' OR true --"}).json()['total'],0)
        bad = self.client.post('/api/results',json={'results':[{**result,'api_key':'should-not-echo'}]})
        self.assertEqual(bad.status_code,422)
        self.assertNotIn('should-not-echo',bad.text)
        self.assertEqual(self.client.get('/.env').status_code,404)
        with patch.dict(os.environ,{'RESULTS_API_TOKEN':'service-password'}):
            self.assertEqual(self.client.get('/api/results').status_code,401)
            self.assertEqual(self.client.get('/api/results',headers={'Authorization':'Bearer service-password'}).status_code,200)
        cors = self.client.options('/api/evaluations',headers={'Origin':'https://allwellll.github.io','Access-Control-Request-Method':'POST','Access-Control-Request-Headers':'content-type'})
        self.assertEqual(cors.headers.get('access-control-allow-origin'),'https://allwellll.github.io')

    def test_async_evaluation_progress_and_storage(self):
        active, peak, calls = 0, 0, []
        async def bank(): return load_bank(), True
        async def call(session, endpoint, model, key, prompt, protocol, timeout):
            nonlocal active, peak
            active += 1; peak = max(peak, active); calls.append((endpoint,model,protocol))
            await asyncio.sleep(.03)
            active -= 1
            match = re.search(r'给出 (\d+) 个', prompt)
            return json.dumps([1] * int(match.group(1))) if match else '先考虑21。最终答案是28。'
        with patch('backend.evaluator.fresh_bank',bank),patch('backend.evaluator.call_model',call):
            response = self.client.post('/api/evaluations',json={'base_url':'https://'+self.domain,'api_key':'private-test-key','models':['gpt-6-astra','opus-5-5'],'candy_runs':3,'concurrency':4})
            self.assertEqual(response.status_code,202,response.text)
            job_id = response.json()['id']
            for _ in range(150):
                job = self.client.get('/api/evaluations/'+job_id).json()
                self.assertNotIn('private-test-key',json.dumps(job))
                if job['status'] not in ('running','queued'): break
                time.sleep(.02)
            self.assertEqual(job['status'],'completed',job)
            self.assertTrue(job['saved']); self.assertEqual(job['completed'],12)
            self.assertGreater(peak,1); self.assertLessEqual(peak,4)
            self.assertEqual(len(calls),12)
            self.assertEqual({p for _,_,p in calls},{'messages','responses'})
            self.assertTrue(all(r['candy_answers']=='28,28,28' for r in job['results']))
            data=self.client.get('/api/results',params={'domain':self.domain}).json()
            self.assertEqual(data['total'],2)
            self.assertNotIn('private-test-key',json.dumps(data))
            retry=self.client.post('/api/evaluations/'+job_id+'/save')
            self.assertEqual(retry.status_code,200)
            self.assertEqual(self.client.get('/api/results',params={'domain':self.domain}).json()['total'],2)

    def test_circuit_breaker_skips_pending_requests_and_redacts_key(self):
        calls = []
        async def bank(): return load_bank(), True
        async def fail(*args):
            calls.append(1)
            raise UpstreamError('HTTP 403 private-test-key',403)
        with patch('backend.evaluator.fresh_bank',bank),patch('backend.evaluator.call_model',fail):
            job_id=self.client.post('/api/evaluations',json={'base_url':'https://'+self.domain,'api_key':'private-test-key','models':['gpt-6-astra'],'candy_runs':3,'concurrency':1}).json()['id']
            for _ in range(100):
                job=self.client.get('/api/evaluations/'+job_id).json()
                if job['status'] not in ('queued','running'): break
                time.sleep(.02)
            self.assertEqual(len(calls),1)
            self.assertEqual(job['results'][0]['verdict'],'无法评测')
            self.assertNotIn('private-test-key',json.dumps(job))


if __name__ == '__main__': unittest.main()
