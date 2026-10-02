"""Browser checks against a local server; fixtures never reach the database."""
import os
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import sync_playwright


def main():
    output = Path('/tmp/ai-gateway-browser')
    output.mkdir(exist_ok=True)
    records = []
    for model in ('gpt-6-astra', 'opus-5-5'):
        for rank in range(1, 4):
            for i in range(3):
                records.append(dict(id=len(records)+1, domain=f'channel-{rank}.example', model=model,
                    key_hash=str(rank)*64, key_prefix='sk-dem', tested_at=f'2026-10-02T01:02:{i+10:02d}Z',
                    tested_date='2026-10-02', time_precision='date' if i == 2 else 'timestamp',
                    fingerprint='matched', top_model=model, candy='5/5' if rank == 1 else '2/5',
                    candy_answers='21,21,21,21,21', verdict='真', status='good' if rank == 1 else 'bad',
                    candy_accuracy=1 if rank == 1 else .4, note='<img src=x onerror=alert(1)>'))
    requests = []
    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.getenv('CHROMIUM_EXECUTABLE', '/root/.cache/ms-playwright/chromium-1217/chrome-linux64/chrome'), args=['--no-sandbox'])
        page = browser.new_page(viewport={'width':1440, 'height':1000}, device_scale_factor=1)
        errors = []
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(os.getenv('BROWSER_BASE_URL', 'http://127.0.0.1:8080'), wait_until='networkidle')
        page.wait_for_function("document.querySelector('#analysis-status').textContent.includes('已更新')")
        page.screenshot(path=str(output/'dashboard-live.png'), full_page=True)

        def respond(route):
            params = {key:values[0] for key, values in parse_qs(urlparse(route.request.url).query).items()}
            requests.append(params)
            selected = [r for r in records if all(r.get(key) == value for key,value in params.items()
                        if key in ('domain','model','key_hash','fingerprint','top_model','candy','verdict'))]
            boards = []
            for model in sorted({r['model'] for r in selected}):
                channels = []
                for domain in sorted({r['domain'] for r in selected if r['model'] == model}):
                    events = [r for r in selected if r['model'] == model and r['domain'] == domain]
                    channels.append(dict(domain=domain, model=model, key_hash=events[0]['key_hash'], key_prefix='sk-dem',
                        model_rank=len(channels)+1, evaluations=len(events), stability_score=1 if '1' in domain else .53,
                        identity_match_rate=1, candy_accuracy=events[0]['candy_accuracy'], is_reference='1' in domain,
                        sequence=events))
                boards.append(dict(model=model, total_channels=len(channels), evaluations=sum(c['evaluations'] for c in channels), channels=channels))
            offset = int(params.get('history_offset', 0))
            route.fulfill(json=dict(leaderboards=boards, total_groups=sum(b['total_channels'] for b in boards),
                summary={'evaluations':len(selected)}, history=dict(items=selected[offset:offset+30], total=len(selected), limit=30, offset=offset),
                models=['gpt-6-astra', 'gpt-6-sol', 'opus-5-5', 'opus-4-8', 'claude-sonnet-5', 'model-6', 'model-7', 'model-8'],
                window=params['window'], timezone='Asia/Shanghai', **{'from':'2026-10-01T20:00:00Z', 'to':'2026-10-02T08:00:00Z'}))

        page.route('**/api/analytics/dashboard?*', respond)
        def updated():
            page.wait_for_function("document.querySelector('#analysis-status').textContent.includes('已更新') && !document.querySelector('#analytics-view').classList.contains('analysis-loading')")
        def click(selector):
            with page.expect_response(lambda r: '/api/analytics/dashboard?' in r.url):
                page.locator(selector).first.click()
            updated()
        click('#analysis-refresh')
        assert page.locator('.model-board').count() == 2
        assert page.locator('.top-channel').count() == 6
        expected_ranking_headers = ['排名', '域名 / Key', '稳定性分数', '指纹', 'Candy', '数量', '评测历史序列图']
        assert page.locator('.model-ranking-table').count() == 2
        for table in page.locator('.model-ranking-table').all():
            assert table.locator('thead th').all_text_contents() == expected_ranking_headers
        assert page.locator('.model-ranking-table tbody tr').count() == 6
        assert page.locator('.model-board').nth(0).locator('tbody tr').count() == 3
        assert page.locator('.ranking-channel-grid').count() == 0
        assert page.locator('.model-ranking-table tbody tr').first.locator('td').count() == 7
        for row in page.locator('.model-ranking-table tbody tr').all():
            bottoms = [cell.bounding_box()['y'] + cell.bounding_box()['height'] for cell in row.locator('td').all()]
            assert max(bottoms) - min(bottoms) < 1, f'misaligned row borders: {bottoms}'
        assert page.locator('.sequence-point').count() == 18
        page.locator('.sequence-point[title*="仅日期"]').first.click()
        assert '仅日期' in page.locator('#analytics-detail').inner_text()
        page.locator('#close-analytics-dialog').click()
        page.screenshot(path=str(output/'dashboard-desktop.png'), full_page=True)
        for width in (320, 390, 768, 1440, 1920):
            page.set_viewport_size({'width':width, 'height':1000})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'overflow at {width}'
            if width == 390:
                page.locator('#model-toggle-more').wait_for(state='visible')
                assert page.locator('#model-toggle-more').inner_text() == '展开更多'
                page.locator('#model-toggle-more').click()
                assert page.locator('#model-toggle-more').inner_text() == '收起'
                page.screenshot(path=str(output/'dashboard-mobile.png'), full_page=True)
        page.set_viewport_size({'width':1440, 'height':1000})
        click('#history-nav')
        assert page.locator('#ranking-section').is_visible()
        assert page.locator('#records-section').is_visible()
        assert page.locator('#analysis-title').inner_text() == '历史记录'
        assert page.locator('#analysis-records tr').count() == 18
        assert '09:02:10' in page.locator('#analysis-records').inner_text()
        assert 'sk-dem***' in page.locator('#analysis-records').inner_text()
        page.locator('#analysis-records .analysis-icon').first.click()
        assert page.locator('#analytics-detail img').count() == 0
        assert '<img' in page.locator('#analytics-detail').inner_text()
        page.locator('#close-analytics-dialog').click()
        for window in ('1d', '7d'):
            click(f'#history-windows [data-window="{window}"]')
            assert requests[-1]['window'] == window
        for dimension, value in [('model','gpt-6-astra'), ('domain','channel-1.example'), ('key_hash','1'*64),
                                 ('fingerprint','matched'), ('top_model','gpt-6-astra'), ('candy','5/5'), ('verdict','真')]:
            click(f'#analysis-records [data-filter="{dimension}"]')
            assert requests[-1][dimension] == value
            assert requests[-1]['window'] == '7d'
        assert page.locator('#analysis-filters .filter-tag').count() == 7
        assert page.locator('#history-filters .filter-tag').count() == 7
        assert requests[-1]['model'] == 'gpt-6-astra' and requests[-1]['key_hash'] == '1'*64
        assert page.locator('#analysis-records tr').count() == 3
        # Spy on the native picker invocation, keeping user activation and focus behavior.
        page.evaluate("window.pickers=[]; HTMLInputElement.prototype.showPicker=function(){window.pickers.push(this.id)}")
        page.locator('#analysis-records .time-value').first.click()
        assert page.evaluate('window.pickers.at(-1)') == 'analysis-record-date'
        page.locator('#analysis-record-date').evaluate("input => { input.value='2026-10-02'; input.dispatchEvent(new Event('change')); }")
        updated()
        assert requests[-1]['window'] == 'custom'
        assert requests[-1]['date_from'] == requests[-1]['date_to'] == '2026-10-02'
        assert requests[-1]['model'] == 'gpt-6-astra'
        page.locator('#analysis-date-from').click()
        assert page.evaluate('window.pickers.at(-1)') == 'analysis-date-from'
        click('.filter-tag[title="移除Key筛选"]')
        assert 'key_hash' not in requests[-1] and requests[-1]['model'] == 'gpt-6-astra'
        click('#analysis-filters .text-button')
        assert 'model' not in requests[-1] and requests[-1]['window'] == 'custom'
        page.locator('#analysis-model').evaluate("select => { const option = new Option('no-such-model', 'no-such-model'); select.add(option); select.value = 'no-such-model'; select.dispatchEvent(new Event('change')); }")
        updated()
        assert page.locator('#analysis-empty').is_visible()
        click('.filter-tag')
        assert page.locator('.model-board').count() == 2
        page.route('**/api/analytics/dashboard?*', lambda route: route.fulfill(status=503, json={'detail':'test unavailable'}))
        page.locator('#analysis-refresh').click()
        page.locator('#analysis-status.error').wait_for()
        assert not page.locator('.ranking-section').is_visible()
        assert not page.locator('.records-section').is_visible()
        page.unroute('**/api/analytics/dashboard?*')
        page.locator('#evaluate-nav').click()
        page.locator('#evaluation-view').wait_for(state='visible')
        assert page.locator('#concurrency').input_value() == '8'
        assert page.evaluate("getComputedStyle(document.querySelector('#evaluation-view label')).fontSize") == '15px'
        page.locator('#base-url').fill('https://857728.com/v1/responses')
        page.locator('#models input[value="claude-opus-5-5"]').check()
        assert '/v1/messages' in page.locator('#endpoints').inner_text()
        page.locator('#ranking-nav').click()
        page.locator('#ranking-section').wait_for(state='visible')
        assert page.locator('#records-section').is_visible()
        assert page.locator('#analysis-title').inner_text() == '评测榜单'
        page.locator('#history-nav').click()
        page.locator('#records-section').wait_for(state='visible')
        assert page.locator('#ranking-section').is_visible()
        assert page.locator('#analysis-title').inner_text() == '历史记录'
        assert not errors, errors
        browser.close()
    print('Browser checks passed: per-model boards, history seconds, cumulative filters, masked-key hash, date picker, empty/error states, XSS, navigation, five responsive widths.')


if __name__ == '__main__':
    main()
