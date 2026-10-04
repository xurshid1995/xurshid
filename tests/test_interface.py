import logging
import os
import tempfile
import threading
import unittest
from pathlib import Path

import test_product_workflow as product_workflow


@unittest.skipUnless(os.environ.get('RUN_BROWSER') == '1', 'Optional browser checks')
class InterfaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = product_workflow.isolated_app()

    def setUp(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        self.fixture = product_workflow.ProductWorkflowTests()
        self.fixture.module = self.module
        self.fixture.setUp()
        self.fixture.supplier_receipt()
        self.server = make_server('127.0.0.1', 0, self.module.app, threaded=False)
        self.worker = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.worker.start()
        self.base_url = f'http://127.0.0.1:{self.server.server_port}'
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch()

    def tearDown(self):
        self.browser.close()
        self.playwright.stop()
        self.server.shutdown()
        self.worker.join()
        self.fixture.tearDown()

    def browser_context(self, width):
        context = self.browser.new_context(viewport={'width': width, 'height': 900})
        cookie = self.module.app.session_interface.get_signing_serializer(
            self.module.app).dumps({'user_id': 1, 'role': 'admin'})
        context.add_cookies([{'name': 'session', 'value': cookie, 'url': self.base_url}])
        return context

    def test_responsive_pages(self):
        folder = Path(tempfile.mkdtemp(prefix='interface-check-', dir=Path.cwd().parent))
        print(f'Interface screenshots: {folder}')
        pages = {
            'products': '/products', 'suppliers': '/suppliers',
            'supplier': '/supplier/8/products', 'receipt': '/add_product_session',
            'sales': '/sales', 'debts': '/debt-sales',
        }
        for width in (320, 390, 1440):
            with self.subTest(width=width):
                context = self.browser_context(width)
                for name, path in pages.items():
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    response = page.goto(self.base_url + path, wait_until='networkidle')
                    self.assertEqual(response.status, 200)
                    self.assertIn('Diamond CRM', page.title())
                    if name == 'sales':
                        self.assertTrue(page.get_by_role('heading', name='Savdo savati', exact=True).is_visible())
                    self.assertFalse(page.evaluate('document.documentElement.scrollWidth > innerWidth'), (width, path))
                    if name in ('products', 'suppliers', 'supplier'):
                        page.wait_for_selector('tr[data-ui-ready]')
                        if width < 769:
                            self.assertTrue(page.evaluate('''() => {
                                const table = document.querySelector('.ui-mobile-table');
                                return table.getBoundingClientRect().width <= table.parentElement.clientWidth + 1;
                            }'''), (width, path))
                            row = page.locator('tr[data-ui-ready]').first
                            self.assertFalse(row.locator('[data-ui-role="detail"]').first.is_visible())
                            row.locator('.ui-detail-toggle button').click()
                            self.assertTrue(row.locator('[data-ui-role="detail"]').first.is_visible())
                            row.locator('.ui-detail-toggle button').click()
                        else:
                            self.assertFalse(page.locator('.ui-detail-toggle').first.is_visible())
                    if name == 'receipt' and width < 769:
                        self.assertGreaterEqual(page.locator('#unit_type').bounding_box()['width'], 100)
                        if width <= 480:
                            self.assertGreater(page.locator('#category_id').bounding_box()['width'], 180)
                        self.assertTrue(page.evaluate('''() => {
                            const button = document.querySelector('#addToListBtn').getBoundingClientRect();
                            const widget = document.querySelector('#hosting-widget');
                            return !widget || widget.getBoundingClientRect().top > button.bottom;
                        }'''))
                    page.evaluate("document.documentElement.style.scrollBehavior = 'auto'; window.scrollTo(0, 0)")
                    page.screenshot(path=str(folder / f'{name}-{width}.png'), full_page=True, animations='disabled')
                    self.assertFalse(errors, errors)
                    page.close()
                context.close()

    def test_chart_states(self):
        context = self.browser_context(390)
        page = context.new_page()
        empty = {'labels': ['12:00'], 'values': [0], 'amounts': [0],
                 'debts': [0], 'profits': [0], 'payment_totals': {}}
        pattern = '**/api/sales-chart?*'
        page.route(pattern, lambda route: route.fulfill(json=empty))
        page.goto(self.base_url + '/', wait_until='networkidle')
        self.assertEqual(page.locator('#salesChartState').inner_text(), "Tanlangan davrda savdo yo'q")
        self.assertFalse(page.locator('#salesChart').is_visible())
        page.unroute(pattern)
        page.route(pattern, lambda route: route.fulfill(status=500, json={'error': 'test'}))
        page.evaluate('loadSalesChart()')
        self.assertTrue(page.locator('#salesChartRetry').is_visible())
        page.unroute(pattern)
        page.route(pattern, lambda route: route.fulfill(json={
            **empty, 'values': [1], 'amounts': [10], 'profits': [2]}))
        page.locator('#salesChartRetry').click()
        page.wait_for_function('salesChart && !document.querySelector("#salesChart").hidden')
        self.assertFalse(page.locator('#salesChartState').is_visible())
        page.wait_for_function('''() => {
            const canvas = document.querySelector('#salesChart');
            return canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height).data.some(value => value > 0);
        }''')
        context.close()

    def test_mobile_filters_and_actions(self):
        context = self.browser_context(390)
        page = context.new_page()
        page.goto(self.base_url + '/supplier/8/products', wait_until='networkidle')
        self.assertEqual(page.locator('td[data-ui-role="name"]').count(), 2)
        self.assertIn('11,850', page.locator('.sp-receipt-rate').first.inner_text())
        page.locator('.ui-detail-toggle button').first.click()
        self.assertIn('$54.22', page.locator('.sp-running-total-cell').inner_text())
        page.locator('#spSearchInput').fill('UZS')
        page.wait_for_function('document.querySelectorAll("tr[data-ui-ready]").length === 1')
        self.assertIn('UZS receipt', page.locator('td[data-ui-role="name"]').inner_text())
        page.locator('.ui-detail-toggle button').click()
        self.assertTrue(page.locator('.sp-price-cell').is_visible())
        page.goto(self.base_url + '/suppliers', wait_until='networkidle')
        page.locator('.ui-detail-toggle button').click()
        page.locator('.sup-actions button[title="Tahrirlash"]').click()
        self.assertTrue(page.locator('.sup-modal').is_visible())
        context.close()


if __name__ == '__main__':
    logging.disable(logging.CRITICAL)
    unittest.main()
