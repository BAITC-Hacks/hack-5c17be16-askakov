"""Проверка интерфейса в установленном Chrome; нужен pip install playwright."""
from pathlib import Path
import csv
import io
import random

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]

with sync_playwright() as p:
    browser = p.chromium.launch(channel='chrome', headless=True)
    page = browser.new_page(viewport={'width': 1512, 'height': 1100}, device_scale_factor=1)
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    page.goto('http://127.0.0.1:8000', wait_until='networkidle')
    page.wait_for_selector('.rank-item')
    assert page.locator('.rank-item').count() == 50
    payload = page.request.get('http://127.0.0.1:8000/api/graph').json()
    for date in (payload['meta']['period_start'], payload['meta']['period_end']):
        assert '.'.join(reversed(date.split('-'))) in page.locator('#period').inner_text()
    # Gid из проверки приоритетов: поиск не должен терять последние цифры.
    for gid in ('100000003684369100', '100000005382566100', '100000004962193100', '100000004486525100'):
        page.locator('#gid').fill(gid)
        page.locator('#search button').click()
        assert page.locator('#detail h3').inner_text() == f'Клиент {gid}'
    for node in random.Random(42).sample(payload['nodes'], 3):
        page.locator('#gid').fill(node['gid'])
        page.locator('#search button').click()
        assert page.locator('#detail h3').inner_text() == f"Клиент {node['gid']}"
        assert node['role_rule'] in page.locator('#detail').inner_text()
        neighbors = {node['gid']}
        for edge in payload['edges']:
            if node['gid'] in (edge['src'], edge['dst']):
                neighbors.update((edge['src'], edge['dst']))
        assert set(page.evaluate('nodes.map(n => n.gid)')) == neighbors
        assert {(edge['src'], edge['dst']) for edge in page.evaluate('edges')} == {
            (edge['src'], edge['dst']) for edge in payload['edges'] if edge['src'] in neighbors and edge['dst'] in neighbors
        }
    first = payload['top'][0]['gid']
    assert isinstance(first, str) and len(first) == 18
    assert all(isinstance(node['gid'], str) for node in payload['nodes'])
    assert all(isinstance(edge['src'], str) and isinstance(edge['dst'], str) for edge in payload['edges'])
    page.locator('.rank-item').first.click()
    assert page.locator('#detail h3').inner_text() == f'Клиент {first}'
    page.locator('#gid').fill(first)
    page.locator('#search button').click()
    assert page.locator('#detail h3').inner_text() == f'Клиент {first}'
    page.locator('.connections button').first.click()
    assert page.locator('#detail h3').inner_text() != f'Клиент {first}'
    boundary = next(n for n in payload['nodes'] if n['truncated_by_depth'])
    page.locator('#gid').fill(boundary['gid'])
    page.locator('#search button').click()
    assert 'Исходящие неизвестны' in page.locator('.detail-warning').inner_text()
    page.locator('#gid').fill('999999999999999999999')
    page.locator('#search button').click()
    assert 'не найден' in page.locator('#search-message').inner_text()
    page.locator('#cluster').select_option('0')
    assert page.locator('#detail .empty').is_visible()
    page.locator('#reset').click()
    assert '2\u00a0248' in page.locator('#graph-count').inner_text()
    for name in ('nodes_roles', 'clusters', 'top_nodes'):
        response = page.request.get(f'http://127.0.0.1:8000/download/{name}.csv')
        assert response.status == 200 and 'attachment' in response.headers['content-disposition']
        if name == 'nodes_roles':
            reader = csv.DictReader(io.StringIO(response.text()))
            assert reader.fieldnames == ['gid', 'role', 'role_score', 'cluster_id', 'priority_score', 'evidence']
            exported = {row['gid']: row for row in reader}
            assert len(exported) == len(payload['nodes'])
            assert all(exported[node['gid']]['role'] == node['role'] for node in payload['nodes'])
    assert page.request.get('http://127.0.0.1:8000/../requirements.txt').status == 404
    target, paths = next((gid, paths) for gid, paths in payload['seed_paths'].items()
                         if len(paths) > 1 and any(len(path) == 5 for path in paths))
    path_index = next(index for index, path in enumerate(paths) if len(path) == 5)
    page.locator('#gid').fill(target)
    page.locator('#search button').click()
    page.locator('#path-seed').select_option(str(path_index))
    page.locator('#show-path').click()
    route = paths[path_index]
    assert page.evaluate('activePath') == route
    assert page.evaluate('nodes.map(n => n.gid)') == route
    assert [(e['src'], e['dst']) for e in page.evaluate('edges')] == list(zip(route, route[1:]))
    assert page.locator('#path-summary .path-hop').count() == 4
    assert 'не установлены' in page.locator('#path-summary').inner_text()
    page.screenshot(path=str(ROOT / 'output/path.png'), full_page=True)

    # Попадание мышью в ребро проверяем отдельно от кнопки операций.
    point = page.evaluate('drawnEdges[0].points[8]')
    page.locator('#graph').click(position=point)
    page.wait_for_selector('#transfer-panel:not([hidden])')
    edge = next(e for e in payload['edges'] if (e['src'], e['dst']) == (route[0], route[1]))
    assert page.evaluate('selectedEdge') == f"{edge['src']}:{edge['dst']}"
    assert page.locator('.transactions-table tbody tr').count() == edge['n_tx']
    actual_sum = float(page.locator('#transfer-total').inner_text().replace('\u00a0', '').replace('\u202f', '').replace(' ', '').replace('₸', '').replace(',', '.'))
    assert abs(actual_sum - edge['sum_kzt']) < .005
    assert page.locator('.transactions-table tbody tr').first.locator('td').nth(1).inner_text() == '.'.join(edge['first_date'][:10].split('-')[::-1])
    page.screenshot(path=str(ROOT / 'output/transfers.png'), full_page=True)
    page.locator('#close-transfer').click()
    assert page.locator('#transfer-panel').is_hidden()

    other_index = (path_index + 1) % len(paths)
    page.locator('#path-seed').select_option(str(other_index))
    assert page.evaluate('activePath') == paths[other_index]
    page.locator('#path-summary .path-hop').first.click()
    assert page.locator('#transfer-panel').is_visible()
    page.locator('#show-neighbors').click()
    assert page.locator('#path-summary').is_hidden()
    assert page.locator('#transfer-panel').is_hidden()
    page.locator('.inspect-transfer').first.click()
    assert page.locator('#transfer-panel').is_visible()

    edge_keys = {(edge['src'], edge['dst']) for edge in payload['edges']}
    reciprocal = next((edge for edge in payload['edges']
                       if edge['src'] != edge['dst'] and (edge['dst'], edge['src']) in edge_keys), None)
    if reciprocal:
        page.evaluate('(key) => showTransfer(key)', f"{reciprocal['src']}:{reciprocal['dst']}")
        page.locator('.reverse-transfer').click()
        reverse_key = f"{reciprocal['dst']}:{reciprocal['src']}"
        assert page.evaluate('selectedEdge') == reverse_key
        assert reverse_key in page.evaluate('drawnEdges.map(edge => edge.key)')

    # У изолированного seed нет пути, в том числе к самому себе.
    isolated = next(n for n in payload['nodes'] if n['in_deg'] == n['out_deg'] == 0)
    page.locator('#gid').fill(isolated['gid'])
    page.locator('#search button').click()
    assert page.locator('#show-path').is_disabled()
    assert 'не найдено' in page.locator('#path-message').inner_text()
    assert page.locator('#transfer-panel').is_hidden()

    page.locator('.rank-item').first.click()
    page.screenshot(path=str(ROOT / 'output/desktop.png'), full_page=True)
    page.set_viewport_size({'width': 390, 'height': 844})
    page.locator('#show-path').click()
    page.locator('#path-summary .path-hop').first.click()
    page.screenshot(path=str(ROOT / 'output/mobile.png'), full_page=True)
    assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
    # По обе стороны точек смены вёрстки gid и меню CSV должны помещаться целиком.
    for viewport_width in (320, 360, 390, 720, 721, 768, 1024, 1150, 1151, 1440, 1600, 1920):
        page.set_viewport_size({'width': viewport_width, 'height': 900})
        page.evaluate('() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))')
        assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), viewport_width
        assert page.locator('.rank-info strong').evaluate_all('elements => elements.every(e => e.scrollWidth <= e.clientWidth && /^\\d{18}$/.test(e.textContent))'), viewport_width
        page.locator('.exports summary').click()
        box = page.locator('.exports > div').bounding_box()
        assert box['x'] >= 0 and box['x'] + box['width'] <= viewport_width, (viewport_width, box)
        if viewport_width == 390:
            with page.expect_download() as download:
                page.locator('.exports a').first.click()
            assert download.value.suggested_filename == 'nodes_roles.csv'
            assert not download.value.failure()
        page.locator('.exports summary').click()
    assert not errors, errors
    print('Browser PASS: 3 arbitrary and 4 tracked gids; visible period; four-hop directed path, seed switching, canvas edge click, exact transaction totals/dates, isolated seed; CSV download; full gids and export menu at 12 widths (320–1920px); no JavaScript errors.')
    browser.close()
