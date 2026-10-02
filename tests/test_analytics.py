from datetime import datetime, timedelta, timezone
import os
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from backend.analytics import analyze, dashboard, metrics
from backend.app import app
from backend.db import connection


@unittest.skipUnless(os.getenv('RUN_DB_TESTS') == '1', 'Set RUN_DB_TESTS=1 for PostgreSQL integration tests')
class AnalyticsTests(unittest.TestCase):
    def setUp(self):
        self.domain = 'analytics-' + uuid4().hex + '.invalid'
        self.context = TestClient(app)
        self.client = self.context.__enter__()
        self.now = datetime.now(timezone.utc)

    def tearDown(self):
        self.context.__exit__(None, None, None)
        with connection() as conn:
            conn.execute('DELETE FROM ai_gateway_eval_results WHERE domain=%s', (self.domain,))

    def insert(self, hours=1, **fields):
        record = {'domain': self.domain, 'model': 'gpt-6-astra', 'tested_at': (self.now-timedelta(hours=hours)).isoformat(),
                  'tested_date':'2000-01-01', 'key_hash':'a'*64,'key_prefix':'sk-abc', 'verdict':'真', 'candy':'4/5', 'candy_answers':'21,21,21,21,28'}
        record.update(fields)
        response = self.client.post('/api/results', json={'results':[record]})
        self.assertEqual(response.status_code,200,response.text)

    def test_windows_boundaries_and_empty_buckets(self):
        for hours in (0, .5, 12, 13, 24, 30, 168, 169, -1): self.insert(hours)
        for window,expected,buckets in [('12h',2,24),('1d',4,24),('7d',6,28)]:
            data=analyze(window=window,domain=self.domain,now=self.now)
            self.assertEqual(data['summary']['evaluations'],expected)
            self.assertEqual(len(data['timeline']),buckets)
            self.assertEqual(sum(b['evaluations'] for b in data['timeline']),expected)
            self.assertTrue(any(b['status']=='empty' and b['candy_accuracy'] is None for b in data['timeline']))
            self.assertEqual(data['summary']['candy_accuracy'],.8)
        empty=analyze(domain='no-record-'+self.domain,now=self.now)
        self.assertEqual(empty['summary']['status'],'empty')
        self.assertIsNone(empty['summary']['candy_accuracy'])
        self.assertEqual(empty['groups'],[])

    def test_dashboard_top_ten_per_model_and_shared_exact_filters(self):
        for model in ('gpt-6-astra', 'opus-5-5'):
            for key in '0123456789ab':
                self.insert(model=model, key_hash=key*64, fingerprint='match', top_model=model)
        data = dashboard(domain=self.domain, now=self.now, history_limit=2)
        self.assertEqual(len(data['leaderboards']), 2)
        self.assertEqual(data['history']['total'], 24)
        self.assertEqual(len(data['history']['items']), 2)
        for board in data['leaderboards']:
            self.assertEqual(board['total_channels'], 12)
            self.assertEqual([g['model_rank'] for g in board['channels']], list(range(1, 11)))
        page = dashboard(domain=self.domain, now=self.now, history_limit=2, history_offset=2)
        self.assertEqual(data['leaderboards'], page['leaderboards'])
        self.assertNotEqual(data['history']['items'][0]['id'], page['history']['items'][0]['id'])
        dimensions = dict(domain=self.domain, model='gpt-6-astra', key_hash='b'*64,
                          verdict='真', fingerprint='match', top_model='gpt-6-astra', candy='4/5')
        filtered = dashboard(now=self.now, **dimensions)
        self.assertEqual(filtered['history']['total'], 1)
        self.assertEqual(filtered['summary']['evaluations'], 1)
        self.assertEqual(filtered['leaderboards'][0]['channels'][0]['key_hash'], 'b'*64)
        self.assertEqual(filtered['history']['items'][0]['key_hash'], 'b'*64)
        self.insert(key_hash=None, key_prefix=None)
        missing = dashboard(domain=self.domain, key_recorded=False, now=self.now)
        self.assertEqual(missing['history']['total'], 1)
        self.assertIsNone(missing['leaderboards'][0]['channels'][0]['key_hash'])
        empty = dashboard(now=self.now, **(dimensions | {'candy':'0/5'}))
        self.assertEqual(empty['leaderboards'], [])
        self.assertEqual(empty['history']['items'], [])

    def test_dashboard_hides_unavailable_unless_requested(self):
        self.insert(verdict='无法评测', candy=None)
        self.insert(verdict='真', candy='5/5')
        hidden = dashboard(domain=self.domain, now=self.now)
        self.assertEqual(hidden['history']['total'], 1)
        self.assertEqual(hidden['summary']['evaluations'], 1)
        shown = dashboard(domain=self.domain, now=self.now, include_unavailable=True)
        self.assertEqual(shown['history']['total'], 2)
        self.assertEqual(shown['summary']['evaluations'], 2)

    def test_dashboard_custom_dates_follow_shanghai_execution_day(self):
        from datetime import date
        self.now = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)
        self.insert(tested_at='2026-10-01T16:00:01Z')  # Shanghai Oct 2, not business date 2000.
        self.insert(tested_at='2026-10-01T15:59:59Z')
        self.insert(tested_at='2026-10-02T09:00:00Z')  # Future precise record.
        for day in ('2026-10-02', '2026-10-02', '2026-10-03'):
            response = self.client.post('/api/results', json={'results':[{
                'domain':self.domain, 'model':'gpt-6-astra', 'tested_date':day,
                'key_hash':'a'*64, 'key_prefix':'sk-abc', 'verdict':'真', 'candy':'5/5'}]})
            self.assertEqual(response.status_code, 200, response.text)
        data = dashboard(window='custom', date_from=date(2026,10,2), date_to=date(2026,10,2),
                         domain=self.domain, now=self.now)
        self.assertEqual(data['history']['total'], 3)
        self.assertEqual(data['summary']['evaluations'], 3)
        items = data['history']['items']
        self.assertEqual([r['time_precision'] for r in items], ['date', 'date', 'timestamp'])
        self.assertGreater(items[0]['id'], items[1]['id'])
        previous = dashboard(window='custom', date_from=date(2026,10,1), date_to=date(2026,10,1),
                             domain=self.domain, now=self.now)
        self.assertEqual(previous['history']['total'], 1)
        future = dashboard(window='custom', date_from=date(2026,10,3), date_to=date(2026,10,3),
                           domain=self.domain, now=self.now)
        self.assertEqual(future['history']['total'], 0)

    def test_dashboard_api_validation_and_auth(self):
        path = '/api/analytics/dashboard'
        self.insert()
        response = self.client.get(path, params={'domain':self.domain})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['history']['total'], 1)
        unavailable = self.client.get(path, params={'domain':self.domain, 'include_unavailable':'true'})
        self.assertEqual(unavailable.status_code, 200, unavailable.text)
        for params in ({'window':'custom'}, {'window':'30d'}, {'key_hash':'short'},
                       {'date_from':'2026-10-01'}, {'history_limit':101}, {'history_offset':-1},
                       {'window':'custom','date_from':'2026-10-02','date_to':'2026-10-01'}):
            self.assertEqual(self.client.get(path, params=params).status_code, 422)
        from unittest.mock import patch
        with patch.dict(os.environ, {'RESULTS_API_TOKEN':'test-dashboard-token'}):
            self.assertEqual(self.client.get(path).status_code, 401)
            self.assertEqual(self.client.get(path, headers={'Authorization':'Bearer test-dashboard-token'}).status_code, 200)

    def test_key_grouping_uses_full_hash_and_pagination_keeps_summary(self):
        self.insert(key_hash='a'*64)
        self.insert(key_hash='b'*64)
        self.insert(key_hash=None,key_prefix=None)
        self.insert(key_hash='a'*64,model='opus-5-5')
        for group_by,total in [('domain',1),('domain_key',3),('domain_key_model',4)]:
            data=analyze(group_by=group_by,domain=self.domain,now=self.now)
            self.assertEqual(data['total_groups'],total)
            self.assertEqual(data['summary']['evaluations'],4)
        page=analyze(group_by='domain_key_model',domain=self.domain,limit=1,offset=1,now=self.now)
        self.assertEqual(len(page['groups']),1)
        self.assertEqual(page['summary']['evaluations'],4)
        filtered=analyze(group_by='domain_key',domain=self.domain,model='opus-5-5',now=self.now)
        self.assertEqual(filtered['total_groups'],1)
        self.assertEqual(filtered['summary']['evaluations'],1)

    def test_weighted_candy_and_separate_quality_failure_counts(self):
        self.insert(candy='1/1',candy_answers='21')                       # good
        self.insert(candy='0/9',candy_answers='28,28,28,28,28,28,28,28,28') # bad
        self.insert(candy='3/5',candy_answers='21,21,21,28,28')             # warning boundary
        self.insert(verdict='换模',candy='1/1',candy_answers='21')          # bad
        self.insert(candy='1/1',candy_answers='21,?')                     # warning incomplete
        self.insert(verdict='无法评测',candy='9/9',candy_answers='?')       # excluded from Candy
        self.insert(candy='0/0',candy_answers='?')                       # warning no score
        for candy in ('2/1','text','-1/3','999999999999999/1',None): self.insert(candy=candy)
        data=analyze(domain=self.domain,now=self.now)
        summary=data['summary']
        self.assertEqual(summary['good'],1)
        self.assertEqual(summary['bad'],2)
        self.assertEqual(summary['unknown'],1)
        self.assertEqual(summary['warning'],8)
        self.assertEqual(summary['candy_correct'],6)
        self.assertEqual(summary['candy_scored'],17)
        self.assertAlmostEqual(summary['candy_accuracy'],6/17)
        self.assertEqual(summary['candy_invalid'],5)
        self.assertEqual(summary['candy_unscored'],1)
        self.assertEqual(summary['candy_unknown_rounds'],3)
        self.assertEqual(summary['status'],'bad')
        self.assertEqual(data['groups'][0]['status'],'bad')

    def test_api_auth_validation_and_metadata(self):
        self.insert()
        response=self.client.get('/api/analytics',params={'domain':self.domain,'group_by':'domain_key_model','window':'12h'})
        self.assertEqual(response.status_code,200,response.text)
        data=response.json()
        self.assertEqual(data['timezone'],'Asia/Shanghai')
        self.assertEqual(data['groups'][0]['key_prefix'],'sk-abc')
        self.assertEqual(data['groups'][0]['evaluations'],1)
        for params in ({'window':'30d'},{'group_by':'key;DROP TABLE'},{'limit':51},{'offset':-1}):
            self.assertEqual(self.client.get('/api/analytics',params=params).status_code,422)
        from unittest.mock import patch
        with patch.dict(os.environ,{'RESULTS_API_TOKEN':'test-only-token'}):
            self.assertEqual(self.client.get('/api/analytics').status_code,401)
            self.assertEqual(self.client.get('/api/analytics',headers={'Authorization':'Bearer test-only-token'}).status_code,200)
            self.assertEqual(self.client.get('/api/analytics/models').status_code,401)
        models = self.client.get('/api/analytics/models', params={'domain':self.domain}).json()
        self.assertEqual(models['models'], ['gpt-6-astra'])

    def test_rankings_require_both_signals_and_recommendation_has_evidence(self):
        for hours in (3, 2, 1): self.insert(hours, candy='5/5')
        self.insert(key_hash='b'*64, candy='5/5')  # perfect but only one sample
        self.insert(key_hash='c'*64, candy=None)  # missing Candy must not score 100
        self.insert(key_hash='d'*64, candy='0/5')  # identity can match despite bad reasoning
        self.insert(key_hash='e'*64, candy='5/5')
        self.insert(key_hash='e'*64, candy=None)   # half the records have no usable Candy
        self.insert(key_hash='a'*64, model='opus-5-5', verdict='换模')
        data = analyze(group_by='domain_key_model', domain=self.domain, model='gpt-6-astra', now=self.now)
        self.assertEqual(data['groups'][0]['key_hash'], 'a'*64)
        self.assertEqual(data['recommendation']['key_hash'], 'a'*64)
        self.assertEqual(data['groups'][0]['stability_score'], 1)
        groups = {g['key_hash']:g for g in data['groups']}
        self.assertIsNone(groups['c'*64]['stability_score'])
        self.assertEqual(groups['e'*64]['evidence_coverage'], .5)
        self.assertEqual(groups['e'*64]['stability_score'], .45)
        self.assertEqual(groups['d'*64]['identity_match_rate'], 1)
        self.assertEqual(groups['d'*64]['sequence'][0]['status'], 'bad')
        # Unknown answers remain a warning even when the scored rounds are all
        # correct; the score must not imply a perfect complete evaluation.
        self.insert(key_hash='f'*64, candy='1/1', candy_answers='21,?')
        updated = analyze(group_by='domain_key_model', domain=self.domain, model='gpt-6-astra', now=self.now)
        partial = next(g for g in updated['groups'] if g['key_hash']=='f'*64)
        self.assertEqual(partial['stability_score'], .9)
        self.assertEqual(partial['sequence'][0]['status'], 'warning')
        page = analyze(group_by='domain_key_model', domain=self.domain, model='gpt-6-astra', limit=1, offset=1, now=self.now)
        self.assertEqual(page['groups'][0]['rank'], 2)
        self.assertEqual(page['recommendation']['rank'], 1)

    def test_date_only_window_and_sequence_order(self):
        from zoneinfo import ZoneInfo
        today = self.now.astimezone(ZoneInfo('Asia/Shanghai')).date()
        record = {'domain':self.domain, 'model':'gpt-6-astra', 'tested_date':str(today),
                  'key_hash':'a'*64, 'key_prefix':'sk-abc', 'verdict':'真', 'candy':'5/5'}
        for _ in range(2):
            response = self.client.post('/api/results', json={'results':[record]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()['items'][0]['time_precision'], 'date')
        record.update(tested_date=str(today + timedelta(days=1)))
        self.client.post('/api/results', json={'results':[record]})
        data = analyze(group_by='domain_key_model', domain=self.domain, now=self.now)
        self.assertEqual(data['summary']['evaluations'], 2)
        events = data['groups'][0]['sequence']
        self.assertLess(events[0]['id'], events[1]['id'])
        self.assertEqual(events[0]['tested_date'], str(today))
        self.assertIsNone(data['recommendation'])
        # A later date-only failure takes precedence over a precise timestamp
        # from that day when deciding whether a channel is still recommended.
        self.insert(.1, candy='5/5')
        record.update(tested_date=str(today), candy='0/5')
        self.client.post('/api/results', json={'results':[record]})
        data = analyze(group_by='domain_key_model', domain=self.domain, now=self.now)
        self.assertEqual(data['groups'][0]['latest_status'], 'bad')
        self.assertIsNone(data['recommendation'])

    def test_sequence_response_is_bounded_without_losing_aggregate_counts(self):
        with connection() as conn:
            conn.execute('''INSERT INTO ai_gateway_eval_results
                (domain,model,key_hash,key_prefix,tested_at,tested_date,verdict,candy)
                SELECT %s,'gpt-6-astra',%s,'sk-abc',%s,%s,'真','1/1'
                FROM generate_series(1,305)''',
                [self.domain, 'a'*64, self.now-timedelta(hours=1), self.now.date()])
        data = analyze(group_by='domain_key_model', domain=self.domain, now=self.now)
        group = data['groups'][0]
        self.assertEqual(group['evaluations'], 305)
        self.assertEqual(group['sequence_total'], 305)
        self.assertEqual(len(group['sequence']), 300)
        ids = [event['id'] for event in group['sequence']]
        self.assertEqual(ids, sorted(ids))


if __name__=='__main__': unittest.main()
