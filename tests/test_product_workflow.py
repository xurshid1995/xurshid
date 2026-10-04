import importlib
import os
import tempfile
import threading
import unittest
from decimal import Decimal
from unittest.mock import patch
from pathlib import Path
from urllib.parse import urlparse

from flask_sqlalchemy import SQLAlchemy


def isolated_app():
    original_init = SQLAlchemy.init_app
    database_url = os.environ.get('PRICING_TEST_DATABASE_URL', 'sqlite://')
    if database_url != 'sqlite://':
        parsed = urlparse(database_url)
        if parsed.hostname != '127.0.0.1' or parsed.path != '/pricing_test':
            raise ValueError('Only isolated localhost pricing_test is allowed')

    def init_sqlite(database, flask_app):
        flask_app.config.update(
            SQLALCHEMY_DATABASE_URI=database_url,
            SQLALCHEMY_ENGINE_OPTIONS={}, TESTING=True,
            WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
        original_init(database, flask_app)

    with patch.dict(os.environ, {'DB_PASSWORD': 'isolated-test',
                                 'SECRET_KEY': 'isolated-test-key-not-production'}):
        with patch.object(SQLAlchemy, 'init_app', init_sqlite):
            module = importlib.import_module('app')
    module.app.before_request_funcs.clear()
    return module


class ProductWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.module = isolated_app()

    def setUp(self):
        self.context = self.module.app.app_context()
        self.context.push()
        self.db = self.module.db
        self.db.create_all()
        self.db.session.add(self.module.User(
            id=1, first_name='Test', last_name='Admin', username='test',
            email='test@example.invalid', password='unused', role='admin'))
        self.db.session.add(self.module.Store(
            id=1, name='Test store', address='Test address', manager_name='Test'))
        self.db.session.add(self.module.CurrencyRate(rate=12000, is_active=True))
        self.db.session.commit()
        self.client = self.module.app.test_client()
        with self.client.session_transaction() as session:
            session['user_id'] = 1
            session['role'] = 'admin'

    def tearDown(self):
        self.db.session.remove()
        self.db.drop_all()
        self.context.pop()

    def receipt(self, currency='UZS', name='Test product', rate='12000'):
        return self.client.post('/api/batch-products', json={'products': [{
            'name': name, 'quantity': 10, 'location_type': 'store', 'location_id': 1,
            'cost_price': '5000' if currency == 'UZS' else '5',
            'sell_price': '6000' if currency == 'UZS' else '6',
            'cost_currency_code': currency, 'sell_currency_code': currency,
            'receipt_exchange_rate': rate,
        }]})

    def test_product_native_storage_and_computed_usd(self):
        product = self.module.Product(
            name='Native product', cost_currency_code='UZS', sell_currency_code='UZS',
            cost_price_native=Decimal('5000'), sell_price_native=Decimal('6000'),
            last_batch_cost_native=Decimal('5000'))
        self.db.session.add(product)
        self.db.session.commit()
        self.assertEqual(product.sell_price, Decimal('0.5'))
        self.module.CurrencyRate.query.one().rate = Decimal('12500')
        self.db.session.commit()
        self.assertEqual(product.cost_price, Decimal('0.4'))
        self.assertEqual(product.sell_price, Decimal('0.48'))
        row = self.db.session.execute(self.db.text(
            'SELECT cost_price, sell_price, last_batch_cost FROM products')).one()
        self.assertEqual(tuple(row), (5000, 6000, 5000))
        columns = self.module.Product.__table__.columns.keys()
        self.assertNotIn('cost_price_original', columns)
        self.assertNotIn('sell_price_original', columns)
        sql_price = self.db.session.query(self.module.Product.sell_price).scalar()
        self.assertAlmostEqual(sql_price, Decimal('0.48'))

    def test_receipt_sale_and_historical_prices(self):
        response = self.receipt()
        self.assertEqual(response.status_code, 201, response.get_json())
        product = self.module.Product.query.one()
        self.assertEqual(product.cost_price_original, Decimal('5000'))
        self.assertEqual(product.sell_price_original, Decimal('6000'))
        self.assertEqual(product.cost_price_native, Decimal('5000'))
        self.assertAlmostEqual(product.cost_price, Decimal('5000') / Decimal('12000'))
        history = self.module.ProductAddHistory.query.one()
        self.assertEqual(history.receipt_exchange_rate, Decimal('12000'))
        self.module.CurrencyRate.query.one().rate = Decimal('12500')
        self.db.session.commit()
        response = self.client.post('/api/create-sale', json={
            'location_id': 1, 'location_type': 'store', 'exchange_rate': 12500,
            'items': [{'id': product.id, 'quantity': 1,
                       'unit_price': '0.48', 'price_uzs': '6000'}],
            'payment': {'cash_usd': '0.48', 'cash_uzs': '6000'},
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        item = self.module.SaleItem.query.one()
        self.assertEqual(item.unit_price, Decimal('0.48'))
        self.assertEqual(item.unit_price_original, Decimal('6000'))
        self.assertEqual(item.price_currency_code, 'UZS')
        self.module.CurrencyRate.query.one().rate = Decimal('13000')
        self.db.session.commit()
        self.db.session.expire_all()
        self.assertEqual(item.unit_price, Decimal('0.48'))
        self.assertEqual(product.cost_price_native, Decimal('5000'))
        self.assertAlmostEqual(product.cost_price, Decimal('5000') / Decimal('13000'))

    def test_repeat_receipt_converts_existing_stock_to_new_currency(self):
        self.assertEqual(self.receipt('USD').status_code, 201)
        self.assertEqual(self.receipt('UZS').status_code, 201)
        product = self.module.Product.query.one()
        self.assertEqual(product.cost_currency_code, 'UZS')
        self.assertEqual(product.cost_price_native, Decimal('32500'))
        self.assertEqual(product.last_batch_cost_native, Decimal('5000'))
        self.assertEqual(product.sell_price_native, Decimal('6000'))
        self.assertEqual(product.store_stocks[0].quantity, Decimal('20'))
        self.module.CurrencyRate.query.one().rate = Decimal('13000')
        self.db.session.commit()
        stale = self.receipt('USD')
        self.assertEqual(stale.status_code, 400)
        self.db.session.expire_all()
        self.assertEqual(product.cost_currency_code, 'UZS')
        self.assertEqual(product.cost_price_native, Decimal('32500'))
        self.assertEqual(product.store_stocks[0].quantity, Decimal('20'))
        self.assertEqual(self.receipt('USD', rate='13000').status_code, 201)
        self.db.session.expire_all()
        self.assertEqual(product.cost_currency_code, 'USD')
        self.assertEqual(product.cost_price_native, Decimal('3.33333'))
        self.assertEqual(product.sell_price_native, Decimal('6'))

    def test_repeat_uzs_receipt_does_not_revalue_existing_stock(self):
        self.assertEqual(self.receipt().status_code, 201)
        self.module.CurrencyRate.query.one().rate = Decimal('13000')
        self.db.session.commit()
        response = self.client.post('/api/batch-products', json={'products': [{
            'name': 'Test product', 'quantity': 10, 'location_type': 'store', 'location_id': 1,
            'cost_price': '7000', 'sell_price': '9000', 'cost_currency_code': 'UZS',
            'sell_currency_code': 'UZS', 'receipt_exchange_rate': '13000',
        }]})
        self.assertEqual(response.status_code, 201, response.get_json())
        product = self.module.Product.query.one()
        self.assertEqual(product.cost_price_native, Decimal('6000'))
        self.assertEqual(product.cost_price_original, Decimal('7000'))
        self.assertEqual(product.sell_price_native, Decimal('9000'))

    def test_stock_views_use_native_cost_and_current_usd(self):
        self.assertEqual(self.receipt().status_code, 201)
        product = self.module.Product.query.one()
        self.db.session.add(self.module.Warehouse(
            id=1, name='Warehouse', address='Test', manager_name='Test'))
        self.db.session.add(self.module.WarehouseStock(
            warehouse_id=1, product_id=product.id, quantity=10))
        self.module.CurrencyRate.query.one().rate = Decimal('13000')
        self.db.session.commit()
        for location in ('store', 'warehouse'):
            with self.subTest(location=location):
                response = self.client.get(f'/api/{location}/1/stock')
                self.assertEqual(response.status_code, 200, response.get_json())
                data = response.get_json()['data']
                row = data['stock_info'][0]
                self.assertEqual(Decimal(row['stock']['product']['cost_price_native']), Decimal('5000'))
                self.assertAlmostEqual(row['stock']['product']['cost_price'], 5000 / 13000)
                self.assertAlmostEqual(row['stock']['product']['sell_price'], 6000 / 13000)
                self.assertAlmostEqual(row['unit_profit'], 1000 / 13000)
                self.assertAlmostEqual(row['profit_percentage'], 20)
                page = self.client.get(f'/{location}/1')
                self.assertEqual(page.status_code, 200)

    def test_usd_receipt_is_unchanged(self):
        response = self.receipt('USD')
        self.assertEqual(response.status_code, 201, response.get_json())
        product = self.module.Product.query.one()
        self.assertEqual(product.cost_price, Decimal('5'))
        self.assertEqual(product.sell_price, Decimal('6'))

    def supplier_receipt(self):
        self.db.session.add(self.module.Supplier(id=8, name='Supplier test'))
        self.module.CurrencyRate.query.one().rate = Decimal('11850')
        self.db.session.commit()
        response = self.client.post('/api/batch-products', json={'products': [
            dict(name='UZS receipt', quantity=10, cost_price='5000', sell_price='6000',
                 cost_currency_code='UZS', sell_currency_code='UZS',
                 receipt_exchange_rate=11850, location_type='store', location_id=1,
                 supplierId=8),
            dict(name='USD receipt', quantity=10, cost_price='5', sell_price='6',
                 location_type='store', location_id=1, supplierId=8),
        ]})
        self.assertEqual(response.status_code, 201, response.get_json())
        return response

    def test_supplier_receipt_preserves_original_currency(self):
        self.supplier_receipt()
        self.db.session.expire_all()
        response = self.client.get('/api/supplier/8/products')
        self.assertEqual(response.status_code, 200, response.get_json())
        rows = {row['product_name']: row for row in response.get_json()['products']}
        self.assertEqual(Decimal(rows['UZS receipt']['receipt_total_uzs']), Decimal('50000'))
        self.assertEqual(Decimal(rows['UZS receipt']['receipt_unit_uzs']), Decimal('5000'))
        self.assertEqual(Decimal(rows['USD receipt']['receipt_total_usd']), Decimal('50'))
        self.assertEqual(Decimal(rows['USD receipt']['receipt_total_uzs']), Decimal('592500'))
        self.module.CurrencyRate.query.one().rate = Decimal('13000')
        product = self.module.Product.query.filter_by(name='UZS receipt').one()
        product.cost_price_original = Decimal('12345')
        self.db.session.commit()
        later = self.client.get('/api/supplier/8/products').get_json()['products']
        self.assertEqual(later, response.get_json()['products'])

    @unittest.skipUnless(os.environ.get('RUN_BROWSER') == '1', 'Optional browser workflow')
    def test_supplier_receipt_browser(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        self.supplier_receipt()
        self.module.CurrencyRate.query.one().rate = Decimal('13000')
        self.db.session.commit()
        server = make_server('127.0.0.1', 0, self.module.app, threaded=False)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f'http://127.0.0.1:{server.server_port}'
        cookie = self.module.app.session_interface.get_signing_serializer(
            self.module.app).dumps({'user_id': 1, 'role': 'admin'})
        screenshots = Path(tempfile.mkdtemp(prefix='supplier-receipt-'))
        print(f'Supplier screenshots: {screenshots}')
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for width in (1440, 390):
                    context = browser.new_context(viewport={'width': width, 'height': 900})
                    context.add_cookies([{'name': 'session', 'value': cookie, 'url': base_url}])
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(base_url + '/supplier/8/products')
                    page.wait_for_function('allProducts.length === 2')
                    uzs_row = page.locator('#spTableBody tr').filter(has_text='UZS receipt')
                    usd_row = page.locator('#spTableBody tr').filter(has_text='USD receipt')
                    digits = "cell => cell.firstChild.textContent.replace(/[^0-9]/g, '')"
                    self.assertEqual(uzs_row.locator('.sp-total-cell').evaluate(digits), '50000')
                    self.assertEqual(uzs_row.locator('.sp-price-cell').evaluate(digits), '5000')
                    self.assertEqual(usd_row.locator('.sp-total-cell').evaluate(digits), '50')
                    self.assertEqual(page.locator('.sp-running-total-cell small').evaluate(
                        "cell => cell.textContent.replace(/[^0-9]/g, '')"), '642500')
                    page.evaluate('window.currentExchangeRate = 14000; renderSupplierProducts(allProducts)')
                    self.assertEqual(uzs_row.locator('.sp-total-cell').evaluate(digits), '50000')
                    if width == 390:
                        uzs_row.locator('.sp-total-cell').scroll_into_view_if_needed()
                    page.screenshot(path=str(screenshots / f'supplier-{width}.png'), full_page=True, animations='disabled')
                    self.assertFalse(errors, errors)
                    context.close()
                browser.close()
        finally:
            server.shutdown()
            worker.join()

    def test_stale_receipt_is_atomic(self):
        self.module.CurrencyRate.query.one().rate = Decimal('12500')
        self.db.session.commit()
        response = self.receipt()
        self.assertEqual(response.status_code, 400, response.get_json())
        self.assertEqual(self.module.Product.query.count(), 0)

    def test_pending_mixed_sale_reprices_only_uzs(self):
        self.assertEqual(self.receipt().status_code, 201)
        self.assertEqual(self.receipt('USD', 'Dollar product').status_code, 201)
        uzs, usd = self.module.Product.query.order_by(self.module.Product.id).all()
        payload = {
            'exchange_rate': 12000,
            'items': [
                dict(product_id=uzs.id, quantity=1, unit_price='0.5', price='0.5',
                     price_uzs=6000, location_id=1, location_type='store'),
                dict(product_id=usd.id, quantity=1, unit_price='6', price='6',
                     price_uzs=72000, location_id=1, location_type='store'),
            ],
        }
        response = self.client.post('/api/pending-sales', json=payload)
        self.assertEqual(response.status_code, 201, response.get_json())
        sale = self.module.Sale.query.one()
        self.module.CurrencyRate.query.one().rate = Decimal('12500')
        self.db.session.commit()
        payload['exchange_rate'] = 12500
        payload['items'][0].update(unit_price='0.48', price='0.48')
        for route in (f'/api/sales/{sale.id}', f'/api/pending-sales/{sale.id}'):
            response = self.client.put(route, json=payload)
            self.assertEqual(response.status_code, 200, response.get_json())
            self.db.session.expire_all()
            self.assertEqual(sale.total_amount, Decimal('6.48'))
            self.assertEqual(sale.items[0].price_currency_code, 'UZS')
        response = self.client.post(f'/api/finalize-sale/{sale.id}', json={
            'exchange_rate': 12500, 'payment': {'cash_usd': '6.5'},
        })
        self.assertEqual(response.status_code, 400, response.get_json())
        self.db.session.expire_all()
        self.assertEqual(sale.payment_status, 'pending')
        response = self.client.post(f'/api/finalize-sale/{sale.id}', json={
            'exchange_rate': 12500, 'payment': {'cash_usd': '6.48', 'cash_uzs': '81000'},
        })
        self.assertEqual(response.status_code, 200, response.get_json())
        self.db.session.expire_all()
        self.assertEqual(sale.total_amount, Decimal('6.48'))
        self.assertEqual(sale.items[1].unit_price, Decimal('6'))

    def test_independent_receipt_currencies(self):
        for cost_currency in ('USD', 'UZS'):
            for sell_currency in ('USD', 'UZS'):
                name = f'{cost_currency}-{sell_currency}'
                response = self.client.post('/api/batch-products', json={'products': [{
                    'name': name, 'quantity': '1.5', 'unitType': 'litr',
                    'location_type': 'store', 'location_id': 1,
                    'cost_price': '5000' if cost_currency == 'UZS' else '0.4',
                    'sell_price': '6000' if sell_currency == 'UZS' else '0.5',
                    'cost_currency_code': cost_currency, 'sell_currency_code': sell_currency,
                    'receipt_exchange_rate': 12000,
                }]})
                self.assertEqual(response.status_code, 201, response.get_json())
                product = self.module.Product.query.filter_by(name=name).one()
                self.assertEqual(product.sell_price, Decimal('0.5'))
                self.assertEqual(product.cost_currency_code, cost_currency)
                self.assertEqual(product.sell_currency_code, sell_currency)
                self.assertEqual(product.store_stocks[0].quantity, Decimal('1.5'))

    @unittest.skipUnless(os.environ.get('RUN_BROWSER') == '1', 'Optional browser workflow')
    def test_browser_workflow(self):
        from playwright.sync_api import sync_playwright
        from werkzeug.serving import make_server

        self.db.session.add(self.module.Warehouse(
            id=1, name='Browser warehouse', address='Test', manager_name='Test'))
        self.db.session.commit()
        server = make_server('127.0.0.1', 0, self.module.app, threaded=False)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        base_url = f'http://127.0.0.1:{server.server_port}'
        cookie = self.module.app.session_interface.get_signing_serializer(
            self.module.app).dumps({'user_id': 1, 'role': 'admin'})
        screenshots = Path(tempfile.mkdtemp(prefix='fixed-uzs-browser-'))
        print(f'Browser screenshots: {screenshots}')
        try:
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch()
                for width in (1440, 390):
                    context = browser.new_context(viewport={'width': width, 'height': 900})
                    context.add_cookies([{'name': 'session', 'value': cookie, 'url': base_url}])
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('dialog', lambda dialog: dialog.accept())
                    page.goto(base_url + '/add_product_session')
                    page.wait_for_function('receiptExchangeRate > 0')
                    page.locator('#location').select_option('store_1')
                    page.locator('#cost_currency_code').select_option('UZS')
                    page.locator('#sell_currency_code').select_option('UZS')
                    page.locator('#product_name').fill(f'Browser UZS {width}')
                    page.locator('#cost_price').fill('5000')
                    page.locator('#sell_price').fill('6000')
                    page.locator('#quantity').fill('10')
                    self.assertIn("so'm", page.locator('#original_cost').input_value())
                    page.locator('#quantity').blur()
                    page.screenshot(path=str(screenshots / f'receipt-{width}.png'), full_page=True, animations='disabled')
                    page.evaluate('addToTempList()')
                    self.assertEqual(page.evaluate('tempProducts[0].sell_price_original'), 6000)
                    with page.expect_response('**/api/batch-products') as saved:
                        page.evaluate('submitAllProducts()')
                    self.assertEqual(saved.value.status, 201, saved.value.text())
                    product_id = saved.value.json()['saved_products'][0]['id']
                    self.db.session.add(self.module.WarehouseStock(
                        warehouse_id=1, product_id=product_id, quantity=10))
                    self.db.session.commit()
                    for location in ('store', 'warehouse'):
                        page.goto(base_url + f'/{location}/1')
                        stock_row = page.locator('#stock-table tbody tr').filter(has_text=f'Browser UZS {width}')
                        stock_row.wait_for()
                        for column, expected in ((4, '5000'), (5, '5000'), (6, '6000'), (7, '1000')):
                            primary = stock_row.locator('td').nth(column).evaluate(
                                "cell => cell.firstChild.textContent.replace(/[^0-9]/g, '')")
                            self.assertEqual(primary, expected)
                        page.screenshot(path=str(screenshots / f'{location}-{width}.png'), full_page=True)
                    page.goto(base_url + '/sales')
                    page.wait_for_function("!!getActiveTabElement('locationSelect')?.querySelector('option[value=store_1]')")
                    page.evaluate("getActiveTabElement('locationSelect').value = 'store_1'; getActiveTabElement('locationSelect').dispatchEvent(new Event('change', {bubbles:true}))")
                    page.wait_for_function('(productId) => saleProductPrices.has(productId)', arg=product_id)
                    page.evaluate('(productId) => { const product = saleProductPrices.get(productId); return addToCart(product.id, product.name, 10, quotedProductPrice(product), Number(product.cost_price)); }', product_id)
                    self.assertEqual(page.evaluate("Number(getActiveTabElement('modalPriceUZS').value)"), 6000)
                    page.screenshot(path=str(screenshots / f'sale-{width}.png'), full_page=True, animations='disabled')
                    page.evaluate('confirmAddToCart()')
                    page.wait_for_function('currentPendingSaleId !== null')
                    pending_id = page.evaluate('currentPendingSaleId')
                    self.module.CurrencyRate.query.one().rate = Decimal('12500')
                    self.db.session.commit()
                    page.evaluate('completeSale()')
                    self.assertAlmostEqual(page.evaluate('cart[0].priceUSD'), 0.48)
                    self.assertEqual(page.evaluate('cart[0].priceUZS'), 6000)
                    page.screenshot(path=str(screenshots / f'payment-{width}.png'), full_page=True)
                    with page.expect_response(f'**/api/finalize-sale/{pending_id}') as finalized:
                        page.evaluate('confirmPayment()')
                    self.assertEqual(finalized.value.status, 200)
                    self.db.session.expire_all()
                    item = self.module.SaleItem.query.filter_by(sale_id=pending_id).one()
                    self.assertEqual(item.unit_price, Decimal('0.48'))
                    self.assertEqual(item.unit_price_original, Decimal('6000'))
                    self.assertFalse(errors, errors)
                    context.close()
                browser.close()
        finally:
            server.shutdown()
            worker.join()
        print(f'Browser screenshots: {screenshots}')

    def test_legacy_editor_cannot_overwrite_uzs_price(self):
        self.assertEqual(self.receipt().status_code, 201)
        product = self.module.Product.query.one()
        response = self.client.post(f'/api/edit_store_stock/1/{product.id}', json={
            'productName': product.name, 'quantity': 10,
            'costPrice': str(product.cost_price), 'sellPrice': '6000',
        })
        self.assertFalse(response.get_json().get('success'))
        self.db.session.expire_all()
        self.assertEqual(product.sell_price_original, Decimal('6000'))
        self.assertEqual(product.sell_price, Decimal('0.5'))

    def test_postgres_supplier_snapshot_migration(self):
        if self.db.engine.dialect.name != 'postgresql':
            self.skipTest('PostgreSQL-only supplier migration')
        self.supplier_receipt()
        purchases = self.module.SupplierPurchase.query.order_by(self.module.SupplierPurchase.id).all()
        before = [(row.cost_price, row.total_amount, row.paid_amount, row.debt_amount) for row in purchases]
        history = self.module.ProductAddHistory.query.filter_by(product_name='UZS receipt').one()
        duplicate = {column.name: getattr(history, column.name)
                     for column in history.__table__.columns if column.name != 'id'}
        migration = (Path(__file__).resolve().parents[1] / 'migrations' /
                     'add_supplier_receipt_price_snapshots.sql').read_text(encoding='utf-8')
        self.db.session.remove()
        connection = self.db.engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute('ALTER TABLE supplier_purchases DROP COLUMN cost_currency_code, '
                           'DROP COLUMN cost_price_original, DROP COLUMN receipt_exchange_rate')
            connection.commit()
            cursor.execute(migration)
            cursor.execute(migration)
            cursor.close()
        finally:
            connection.close()
        purchases = self.module.SupplierPurchase.query.order_by(self.module.SupplierPurchase.id).all()
        self.assertEqual(before, [(row.cost_price, row.total_amount, row.paid_amount, row.debt_amount)
                                  for row in purchases])
        self.assertEqual(Decimal(purchases[0].to_dict()['receipt_total_uzs']), Decimal('50000'))
        self.assertEqual(Decimal(purchases[1].to_dict()['receipt_total_usd']), Decimal('50'))
        purchases[0].cost_currency_code = None
        purchases[0].cost_price_original = None
        purchases[0].receipt_exchange_rate = None
        self.db.session.add(self.module.ProductAddHistory(**duplicate))
        self.db.session.commit()
        self.db.session.remove()
        connection = self.db.engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute(migration)
            cursor.close()
        finally:
            connection.close()
        ambiguous = self.module.SupplierPurchase.query.filter_by(product_name='UZS receipt').one()
        self.assertIsNone(ambiguous.cost_price_original)
        self.assertNotIn('receipt_total_uzs', ambiguous.to_dict())

    def test_postgres_native_migration_is_atomic_and_repeatable(self):
        if self.db.engine.dialect.name != 'postgresql':
            self.skipTest('PostgreSQL-only native price migration')
        self.assertEqual(self.receipt('USD').status_code, 201)
        self.assertEqual(self.receipt('USD', 'Legacy UZS').status_code, 201)
        migration = (Path(__file__).resolve().parents[1] / 'migrations' /
                     'product_prices_native_currency.sql').read_text(encoding='utf-8')
        self.db.session.remove()
        connection = self.db.engine.raw_connection()
        try:
            cursor = connection.cursor()
            cursor.execute('ALTER TABLE products ADD COLUMN cost_price_original NUMERIC(24,5), '
                           'ADD COLUMN sell_price_original NUMERIC(24,5)')
            cursor.execute("UPDATE products SET cost_price=1.1234567891, sell_price=2.1234567891, "
                           "last_batch_cost=0.9876543210 WHERE name='Test product'")
            cursor.execute("UPDATE products SET cost_price=1, sell_price=0.5, last_batch_cost=0.4167, "
                           "cost_currency_code='UZS', sell_currency_code='UZS', "
                           "cost_price_original=5000, sell_price_original=6000, receipt_exchange_rate=12000 "
                           "WHERE name='Legacy UZS'")
            connection.commit()
            with self.assertRaises(Exception) as failure:
                cursor.execute(migration)
            self.assertIn('needs reconciliation', str(failure.exception))
            connection.rollback()
            cursor.execute("SELECT cost_price, cost_price_original FROM products WHERE name='Legacy UZS'")
            self.assertEqual(cursor.fetchone(), (Decimal('1'), Decimal('5000')))
            cursor.execute("UPDATE products SET cost_price=0.41667 WHERE name='Legacy UZS'")
            connection.commit()
            cursor.execute(migration)
            cursor.execute(migration)
            cursor.execute("SELECT cost_price, sell_price, last_batch_cost FROM products WHERE name='Legacy UZS'")
            self.assertEqual(cursor.fetchone(), (Decimal('5000'), Decimal('6000'), Decimal('5000')))
            cursor.execute("SELECT cost_price, sell_price, last_batch_cost FROM products WHERE name='Test product'")
            self.assertEqual(cursor.fetchone(), (Decimal('1.1234567891'), Decimal('2.1234567891'),
                                                 Decimal('0.9876543210')))
            cursor.execute('SELECT count(*) FROM product_add_history')
            self.assertEqual(cursor.fetchone()[0], 2)
            cursor.close()
        finally:
            connection.close()

    def test_postgres_repeat_receipt_and_migration(self):
        if self.db.engine.dialect.name != 'postgresql':
            self.skipTest('PostgreSQL-only migration and atomic stock test')
        from sqlalchemy import text

        self.assertEqual(self.receipt('USD').status_code, 201)
        product = self.module.Product.query.one()
        old_cost = product.cost_price
        old_sell = product.sell_price
        product_id = product.id
        self.db.session.remove()
        connection = self.db.engine.raw_connection()
        try:
            cursor = connection.cursor()
            for table in ('products', 'product_add_history'):
                for column in ('cost_currency_code', 'sell_currency_code',
                               'cost_price_original', 'sell_price_original',
                               'receipt_exchange_rate'):
                    cursor.execute(f'ALTER TABLE {table} DROP COLUMN IF EXISTS {column}')
            cursor.execute('ALTER TABLE sale_items DROP COLUMN price_currency_code, DROP COLUMN unit_price_original')
            connection.commit()
            migration = (Path(__file__).resolve().parents[1] / 'migrations' /
                         'add_fixed_uzs_product_prices.sql').read_text(encoding='utf-8')
            cursor.execute(migration)
            cursor.execute(migration)
            native_migration = (Path(__file__).resolve().parents[1] / 'migrations' /
                                'product_prices_native_currency.sql').read_text(encoding='utf-8')
            cursor.execute(native_migration)
            cursor.close()
        finally:
            connection.close()
        product = self.db.session.get(self.module.Product, product_id)
        self.assertEqual(product.cost_price, old_cost)
        self.assertEqual(product.sell_price, old_sell)
        self.assertEqual(product.sell_currency_code, 'USD')
        response = self.receipt()
        self.assertEqual(response.status_code, 201, response.get_json())
        self.db.session.expire_all()
        self.assertEqual(product.cost_price_native, Decimal('32500'))
        self.assertAlmostEqual(product.cost_price, Decimal('32500') / Decimal('12000'))
        self.assertEqual(product.sell_price_original, Decimal('6000'))
        self.assertEqual(product.store_stocks[0].quantity, Decimal('20'))
        self.assertEqual(self.db.session.execute(text('SELECT count(*) FROM product_add_history')).scalar(), 2)


if __name__ == '__main__':
    unittest.main()
