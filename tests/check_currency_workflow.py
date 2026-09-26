import argparse
import asyncio
import logging
import os
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import patch, AsyncMock
from types import SimpleNamespace

from flask import session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.serving import make_server

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def make_test_app(database_uri='sqlite://'):
    original_init = SQLAlchemy.init_app

    def init_test_database(database, application):
        application.config.update(SQLALCHEMY_DATABASE_URI=database_uri, SQLALCHEMY_ENGINE_OPTIONS={})
        original_init(database, application)

    test_environment = {
        'DB_PASSWORD': 'local-test-only',
        'SECRET_KEY': 'local-currency-test-key-012345678901234567890123456789'}
    with patch.dict(os.environ, test_environment):
        with patch.object(SQLAlchemy, 'init_app', init_test_database):
            import app as application

    application.app.config.update(TESTING=True, WTF_CSRF_ENABLED=False, SESSION_COOKIE_SECURE=False)
    import telegram_bot
    sandbox_bot = telegram_bot.DebtTelegramBot.__new__(telegram_bot.DebtTelegramBot)
    sandbox_bot.db = application.db
    sandbox_bot.token = None
    sandbox_bot.bot = None
    sandbox_bot.admin_chat_ids = []
    telegram_bot._bot_instance = sandbox_bot

    def test_session():
        session.update(user_id=1, role='admin', username='currency-test',
                       user_name='Currency Test', language='uz')

    application.app.before_request_funcs = {None: [test_session]}
    with application.app.app_context():
        application.db.create_all()
        application.db.session.add(application.Store(id=1, name='Test store', address='Test', manager_name='Test'))
        application.db.session.add(application.User(
            id=1, first_name='Currency', last_name='Test', username='currency-test',
            email='currency@example.test', password='disabled', role='admin', store_id=1))
        application.db.session.add(application.Customer(id=1, name='Currency customer', store_id=1))
        application.db.session.add(application.CurrencyRate(rate=12500, is_active=True))
        application.db.session.commit()
    return application


def check_api(application):
    client = application.app.test_client()
    products = [
        {'name': 'USD product', 'cost_price': '1', 'sell_price': '10', 'quantity': '20',
         'cost_currency': 'USD', 'sell_currency': 'USD', 'location_type': 'store', 'location_id': 1},
        {'name': 'UZS product', 'cost_price': '50000', 'sell_price': '100000', 'quantity': '20',
         'cost_currency': 'UZS', 'sell_currency': 'UZS', 'location_type': 'store', 'location_id': 1}]
    response = client.post('/api/batch-products', json={'products': products})
    assert response.status_code == 201, response.get_json()
    with application.app.app_context():
        stored = application.Product.query.order_by(application.Product.id).all()
        identifiers = [product.id for product in stored]
        assert stored[1].native_sell_price == 100000
        application.db.session.get(application.Customer, 1).telegram_chat_id = '1'
        application.db.session.commit()
    items = [
        {'product_id': identifiers[0], 'quantity': 5, 'unit_price': '10', 'price_currency': 'USD',
         'native_unit_price': '10', 'location_id': 1, 'location_type': 'store'},
        {'product_id': identifiers[1], 'quantity': 2, 'unit_price': '8', 'price_currency': 'UZS',
         'native_unit_price': '100000', 'location_id': 1, 'location_type': 'store'}]
    for item in items:
        response = client.post('/api/reserve-stock', json={
            'product_id': item['product_id'], 'quantity': item['quantity'],
            'location_id': 1, 'location_type': 'store'})
        assert response.status_code == 200, response.get_json()
    response = client.post('/api/pending-sales', json={
        'items': items, 'customer_id': 1, 'exchange_rate': 12500})
    assert 200 <= response.status_code < 300, response.get_json()
    sale_id = response.get_json()['sale_id']
    response = client.put(f'/api/pending-sales/{sale_id}', json={
        'items': items, 'customer_id': 1, 'exchange_rate': 12500})
    assert response.status_code == 200, response.get_json()
    with patch('telegram_bot.get_bot_instance') as get_bot:
        response = client.post(f'/api/finalize-sale/{sale_id}', json={
            'payment': {'debt_usd': '66'}, 'payment_status': 'debt',
            'customer_id': 1, 'exchange_rate': 12500, 'receipt_format': 'none'})
        notification = get_bot.return_value.send_sale_notification_sync.call_args
        assert notification is not None, response.get_json()
        assert notification.kwargs['native_debts'] == {'USD': 50, 'UZS': 200000}
    assert response.status_code == 200, response.get_json()
    with application.app.app_context():
        sale = application.db.session.get(application.Sale, sale_id)
        assert sale.native_debt_usd == 50 and sale.native_debt_uzs == 200000
        assert [item.price_currency for item in sale.items] == ['USD', 'UZS']
    with patch('telegram_bot.get_bot_instance') as get_bot:
        get_bot.return_value.send_payment_confirmation_sync.return_value = True
        response = client.post('/api/debts/payment', json={
            'customer_id': 1, 'native_currency_payment': True, 'cash_currency': 'UZS',
            'cash_uzs': '200000', 'debt_priority': 'UZS', 'exchange_rate': 13000})
        notification = get_bot.return_value.send_payment_confirmation_sync.call_args
        assert notification is not None, response.get_json()
        assert Decimal(notification.kwargs['native_debts']['USD']) == 50
        assert notification.kwargs['native_payments'][0] == {
            'channel': 'cash', 'currency': 'UZS', 'amount': '200000'}
    assert response.status_code == 200, response.get_json()
    assert Decimal(response.get_json()['native_debts']['UZS']) == 0
    with application.app.app_context():
        sale = application.db.session.get(application.Sale, sale_id)
        assert sale.native_debt_usd == 50 and sale.native_debt_uzs == 0
        payment = application.DebtPayment.query.one()
        payment_date = payment.payment_date.isoformat()
    response = client.post('/api/debt-payments/reverse', json={
        'customer_id': 1, 'payment_date': payment_date})
    assert response.status_code == 200, response.get_json()
    with application.app.app_context():
        sale = application.db.session.get(application.Sale, sale_id)
        assert sale.native_debt_usd == 50 and sale.native_debt_uzs == 200000
    response = client.post('/api/return-product', json={
        'sale_id': sale_id, 'items': [{'product_id': identifiers[1], 'quantity': 1}],
        'location_id': 1, 'location_type': 'store', 'refund_type': 'cash'})
    assert response.status_code == 200, response.get_json()
    with application.app.app_context():
        sale = application.db.session.get(application.Sale, sale_id)
        assert sale.native_debt_usd == 50 and sale.native_debt_uzs == 100000
        stock = application.StoreStock.query.filter_by(product_id=identifiers[1], store_id=1).one()
        assert stock.quantity == 19
        sale.payment_due_date = application.get_tashkent_time().date()
        sale.payment_status = 'debt'
        application.db.session.commit()
    from debt_scheduler import DebtScheduler
    from telegram_bot import check_debt_button, admin_today_debts_button, admin_total_debts_button
    update = SimpleNamespace(effective_chat=SimpleNamespace(id='1'),
                             message=SimpleNamespace(reply_text=AsyncMock()))
    for handler in (check_debt_button, admin_today_debts_button, admin_total_debts_button):
        with patch('telegram_bot._is_admin_chat', return_value=True):
            asyncio.run(handler(update, None))
        assert '50 USD + 100,000 UZS' in update.message.reply_text.call_args.args[0]
    scheduler = DebtScheduler.__new__(DebtScheduler)
    scheduler.app = application.app
    scheduler.db = application.db
    scheduler.bot = SimpleNamespace(token='test-only', admin_chat_ids=['2'])
    with patch('requests.post') as post, patch('debt_scheduler.time_module.sleep'):
        post.return_value.status_code = 200
        scheduler.check_due_date_reminders()
        assert post.call_count == 3, post.call_args_list
        for notification in post.call_args_list:
            assert '50 USD + 100,000 UZS' in notification.kwargs['json']['text']
    print('Real Flask API: product -> pending -> finalize -> payment -> reversal OK')


def check_browser(application):
    from playwright.sync_api import sync_playwright

    server = make_server('127.0.0.1', 0, application.app, threaded=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base_url = f'http://127.0.0.1:{server.server_port}'
    screenshots = Path(tempfile.mkdtemp(prefix='currency-ui-'))
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch()
            for width, height in ((1440, 1000), (390, 844)):
                page = browser.new_page(viewport={'width': width, 'height': height}, service_workers='block')
                errors = []
                page.on('pageerror', lambda error: errors.append(str(error)))
                page.on('dialog', lambda dialog: dialog.accept())
                response = page.goto(f'{base_url}/add_product_session')
                assert response.status == 200
                page.locator('#cost_currency').select_option('UZS')
                page.locator('#sell_currency').select_option('UZS')
                page.locator('#cost_price').fill('50000')
                page.locator('#sell_price').fill('100000')
                page.screenshot(path=str(screenshots / f'product-{width}.png'), full_page=True)
                page.route('**/api/search-product/namuna', lambda route: route.fulfill(json={
                    'exists': True, 'products': [{'product': {
                        'name': 'namuna', 'cost_price': 4, 'cost_currency': 'USD',
                        'native_cost_price': None}, 'locations': [{'quantity': '10'}]}]}))
                page.evaluate("window.currentExchangeRate = 11850")
                page.locator('#product_name').fill('namuna')
                page.locator('#quantity').fill('10')
                page.locator('#cost_price').fill('12000')
                page.locator('#sell_price').fill('15000')
                page.wait_for_function("document.getElementById('averageCostPreview').textContent.includes('29,700')")
                assert '14,700 UZS' in page.locator('#sellingPriceWarning').inner_text()
                assert page.locator('#cost_price').input_value() == '12000'
                assert page.locator('#profit_display').input_value().startswith('$-')
                page.screenshot(path=str(screenshots / f'average-warning-{width}.png'), full_page=True)
                page.locator('#sell_price').fill('35000')
                assert not page.locator('#sellingPriceWarning').is_visible()
                page.locator('#sell_currency').select_option('USD')
                page.locator('#sell_price').fill('1')
                assert page.locator('#sellingPriceWarning').is_visible()
                supplier_response = page.request.post(f'{base_url}/api/suppliers', data={'name': f'Browser supplier {width}'})
                assert supplier_response.status == 201
                browser_supplier_id = supplier_response.json()['supplier']['id']
                page.evaluate("""supplierId => {
                    window.currentExchangeRate = 12500;
                    tempProducts = [
                        {supplierId,name:`Browser USD ${supplierId}`,cost_currency:'USD',native_batch_cost:2,cost_price:2,sell_price:3,quantity:10,location:'store_1'},
                        {supplierId,name:`Browser UZS ${supplierId}`,cost_currency:'UZS',native_batch_cost:20000,cost_price:1.6,sell_currency:'UZS',native_sell_price:25000,sell_price:2,quantity:10,location:'store_1'}
                    ];
                    openSupplierPaymentModal();
                }""", browser_supplier_id)
                assert page.locator('#spTotalUSD').inner_text() == '20 USD'
                assert page.locator('#spTotalUZS').inner_text() == '200,000 UZS'
                assert page.locator('#spMixedPayment').is_checked()
                assert page.locator('.sp-modes input').evaluate_all('(inputs) => inputs.map(input => input.id)') == [
                    'spMixedPayment', 'spPaymentUSD', 'spPaymentUZS']
                assert page.locator('#spCashUSD').input_value() == '20'
                assert page.locator('#spPaymentPanel .sp-row').count() == 4
                assert page.locator('#spClickUSD').input_value() == ''
                assert page.locator('#spDebtUZS').get_attribute('placeholder') == 'UZS'
                assert page.locator('#spPaymentPanel .sp-remaining').all_text_contents() == ['+Qolgan'] * 4
                page.locator('#spCashUSD').fill('9.5')
                page.locator('#spPaymentPanel .sp-remaining').last.click()
                assert page.locator('#spDebtUSD').input_value() == '10.5'
                assert page.locator('#spDebtUZS').input_value() == '131250'
                assert not page.locator('#spConfirmBtn').is_disabled()
                assert page.evaluate("supplierPaymentData().payments.every(entry => entry.channel !== 'debt')")
                page.screenshot(path=str(screenshots / f'supplier-payment-rows-{width}.png'), full_page=True)
                page.locator('#spTabUZS').click()
                assert page.locator('#spDebtUZS').input_value() == ''
                page.locator('#spTabUSD').click()
                assert page.locator('#spDebtUSD').input_value() == '10.5'
                page.locator('#spDebtUZS').fill('125000')
                assert page.locator('#spDebtUSD').input_value() == '10'
                assert page.locator('#spConfirmBtn').is_disabled()
                page.locator('#spPaymentPanel .sp-remaining').first.click()
                assert page.locator('#spCashUSD').input_value() == '10'
                assert not page.locator('#spConfirmBtn').is_disabled()
                page.evaluate('openSupplierPaymentModal()')
                page.locator('#spCashUZS').fill('250000')
                assert page.locator('#spCashUSD').input_value() == '20'
                assert page.locator('#spDebtPriority, #spActualPayment, #spNativeDebt').count() == 0
                page.locator('#spTabUZS').click()
                assert page.locator('#spCashUZS').input_value() == '200000'
                page.locator('#spCashUSD').fill('16')
                assert page.locator('#spCashUZS').input_value() == '200000'
                assert not page.locator('#spConfirmBtn').is_disabled()
                page.screenshot(path=str(screenshots / f'supplier-mixed-tabs-{width}.png'), full_page=True)
                page.locator('#spMixedPayment').uncheck()
                assert page.locator('#spPaymentUZS').is_checked()
                assert not page.locator('#spPaymentUSD').is_checked()
                assert page.locator('#spTabUSD').is_disabled()
                assert not page.locator('#spTabUSD').is_visible()
                assert page.locator('#spTabUZS').is_visible()
                assert page.locator('#spCashUZS').input_value() == '450000'
                assert page.evaluate("supplierPaymentData().payments") == [
                    {'channel': 'cash', 'currency': 'UZS', 'amount': '450000'}]
                page.locator('#spPaymentUSD').check()
                assert not page.locator('#spPaymentUZS').is_checked()
                assert page.locator('#spTabUZS').is_disabled()
                assert not page.locator('#spTabUZS').is_visible()
                assert page.locator('#spTabUSD').is_visible()
                assert page.locator('#spCashUSD').input_value() == '36'
                assert page.evaluate("supplierPaymentData().payments") == [
                    {'channel': 'cash', 'currency': 'USD', 'amount': '36'}]
                page.locator('#spPaymentUSD').click()
                assert page.locator('#spPaymentUSD').is_checked()
                page.locator('#spMixedPayment').check()
                assert not page.locator('#spPaymentUSD').is_checked()
                assert not page.locator('#spTabUZS').is_disabled()
                assert not page.locator('#spTabUSD').is_disabled()
                assert page.locator('#spTabUSD').is_visible()
                assert page.locator('#spTabUZS').is_visible()
                assert page.locator('#spCashUSD').input_value() == '20'
                assert page.evaluate('supplierPaymentData().payments') == [
                    {'channel': 'cash', 'currency': 'USD', 'amount': '20'},
                    {'channel': 'cash', 'currency': 'UZS', 'amount': '200000'}]
                page.locator('#spTabUZS').click()
                assert page.locator('#spCashUZS').input_value() == '200000'
                page.locator('#spPaymentUZS').check()
                assert page.locator('#spCashUZS').input_value() == '450000'
                page.locator('#spMixedPayment').check()
                assert page.locator('#spCashUZS').input_value() == '200000'
                page.locator('#spTabUSD').click()
                assert page.locator('#spCashUSD').input_value() == '20'
                page.locator('#spCashUSD').fill('9.5')
                page.locator('#spClickUSD').fill('5')
                page.locator('#spTerminalUSD').fill('1')
                page.locator('#spPaymentPanel .sp-remaining').last.click()
                assert page.locator('#spDebtUSD').input_value() == '4.5'
                mixed_amounts = page.evaluate('supplierTabAmounts')
                for code in ('USD', 'UZS'):
                    page.locator(f'#spPayment{code}').check()
                    page.locator(f'#spCash{code}').fill('1')
                    page.locator('#spMixedPayment').check()
                    assert page.evaluate('supplierTabAmounts') == mixed_amounts
                    assert not page.locator('#spConfirmBtn').is_disabled()
                for code, expected in (('USD', '20'), ('UZS', '200000')):
                    result = page.evaluate("""code => {
                        const original = tempProducts;
                        tempProducts = original.filter(product => product.cost_currency === code);
                        openSupplierPaymentModal();
                        const result = {mixed: document.getElementById('spMixedPayment').checked,
                            cash: document.getElementById(`spCash${code}`).value,
                            tab: supplierPaymentTab};
                        tempProducts = original;
                        return result;
                    }""", code)
                    assert result == {'mixed': False, 'cash': expected, 'tab': code}
                page.evaluate('openSupplierPaymentModal()')
                page.locator('#spCashUSD').fill('0')
                page.locator('#spTabUZS').click()
                assert page.evaluate('supplierPaymentData().debt_priority') == 'USD'
                page.locator('#spCashUZS').fill('200000')
                assert page.locator('#spConfirmBtn').is_disabled()
                assert page.locator('#spCashUSD').is_editable()
                page.screenshot(path=str(screenshots / f'supplier-receipt-{width}.png'), full_page=True)
                page.locator('#spTabUSD').click()
                page.locator('#spCashUSD').fill('-1')
                assert page.locator('#spConfirmBtn').is_disabled()
                page.locator('#spCashUSD').fill('21')
                assert page.locator('#spConfirmBtn').is_disabled()
                page.locator('#spCashUSD').fill('0')
                page.locator('#spPaymentPanel .sp-remaining').first.click()
                assert page.locator('#spCashUSD').input_value() == '20'
                assert not page.locator('#spConfirmBtn').is_disabled()
                assert page.evaluate("""() => [...document.querySelectorAll('#supplierPaymentModal input[type="number"]')].every(input => {
                    const bounds = input.getBoundingClientRect();
                    return bounds.width >= 70 && bounds.left >= 0 && bounds.right <= innerWidth;
                })""")
                expected_debt = Decimal('0')
                expected_cash = '20'
                if width == 1440:
                    page.locator('#spCashUSD').fill('9.5')
                    page.locator('#spPaymentPanel .sp-remaining').last.click()
                    assert page.locator('#spDebtUSD').input_value() == '10.5'
                    expected_debt = Decimal('10.5')
                    expected_cash = '9.5'
                    assert not page.locator('#spConfirmBtn').is_disabled()
                with page.expect_response(lambda response: response.url.endswith('/api/batch-products')) as saved:
                    page.locator('#spConfirmBtn').click()
                assert saved.value.status == 201, saved.value.json()
                receipt = page.request.get(f'{base_url}/api/supplier/{browser_supplier_id}/timeline').json()
                assert Decimal(receipt['supplier']['native_debts']['USD']) == 0
                assert Decimal(receipt['supplier']['native_debts']['UZS']) == expected_debt * 12500
                assert receipt['events'][0]['batch']['native_payments'] == [
                    {'channel': 'cash', 'currency': 'USD', 'amount': expected_cash},
                    {'channel': 'cash', 'currency': 'UZS', 'amount': '200000'}]
                for code, expected in (('USD', '36'), ('UZS', '450000')):
                    page.reload()
                    page.wait_for_function("typeof openSupplierPaymentModal === 'function'")
                    supplier_response = page.request.post(f'{base_url}/api/suppliers', data={'name': f'Checkbox {code} {width}'})
                    assert supplier_response.status == 201
                    checkbox_supplier_id = supplier_response.json()['supplier']['id']
                    page.evaluate("""supplierId => {
                        window.currentExchangeRate = 12500;
                        tempProducts = [
                            {supplierId,name:`Checkbox USD ${supplierId}`,cost_currency:'USD',native_batch_cost:2,cost_price:2,sell_price:3,quantity:10,location:'store_1'},
                            {supplierId,name:`Checkbox UZS ${supplierId}`,cost_currency:'UZS',native_batch_cost:20000,cost_price:1.6,sell_currency:'UZS',native_sell_price:25000,sell_price:2,quantity:10,location:'store_1'}
                        ];
                        openSupplierPaymentModal();
                    }""", checkbox_supplier_id)
                    page.locator(f'#spPayment{code}').check()
                    assert not page.locator('#spMixedPayment').is_checked()
                    page.screenshot(path=str(screenshots / f'supplier-checkbox-{code}-{width}.png'), full_page=True)
                    with page.expect_response(lambda response: response.url.endswith('/api/batch-products')) as saved:
                        page.locator('#spConfirmBtn').click()
                    assert saved.value.status == 201, saved.value.json()
                    receipt = page.request.get(f'{base_url}/api/supplier/{checkbox_supplier_id}/timeline').json()
                    assert receipt['events'][0]['batch']['native_payments'] == [
                        {'channel': 'cash', 'currency': code, 'amount': expected}]
                    assert all(Decimal(value) == 0 for value in receipt['supplier']['native_debts'].values())
                response = page.goto(f'{base_url}/sales')
                assert response.status == 200
                page.wait_for_function("typeof productSellingUSD === 'function'")
                quote = page.evaluate("productSellingUSD({id: 2, sell_currency: 'UZS', native_sell_price: '100000'}) * exchangeRate")
                assert abs(quote - 100000) < 0.001
                page.evaluate("""() => {
                    cart = [
                        {id:1,name:'USD product',quantity:5,priceUSD:10,priceUZS:125000,priceCurrency:'USD'},
                        {id:2,name:'UZS product',quantity:2,priceUSD:8,priceUZS:100000,priceCurrency:'UZS'}
                    ];
                    updateCart();
                    completeSale();
                    fvSet(document.getElementById('paymentCashUSD'), 0);
                    document.getElementById('paymentCashUZS').value = '0';
                    fvSet(document.getElementById('paymentDebtUSD'), 66);
                    document.getElementById('paymentDebtUZS').value = '825000';
                    updateNativeDebtPreview();
                }""")
                assert '200' in page.locator('#nativeSaleDebt').inner_text()
                assert '66' in page.locator('#paymentModalTotalUSD').inner_text()
                page.screenshot(path=str(screenshots / f'sales-{width}.png'), full_page=True)
                response = page.goto(f'{base_url}/debts/customer/1')
                assert response.status == 200
                page.wait_for_function("document.getElementById('displayRemainingDebt').textContent.includes('UZS')")
                debt_text = page.locator('#displayRemainingDebt').inner_text()
                assert '50' in debt_text and '100' in debt_text, debt_text
                page.screenshot(path=str(screenshots / f'debt-{width}.png'), full_page=True)
                response = page.goto(f'{base_url}/supplier/1/debt-payment')
                assert response.status == 200
                page.wait_for_function("document.getElementById('displayRemainingDebt').textContent.includes('200,000 UZS')")
                assert page.locator('#debtMixedPayment').is_checked()
                assert page.locator('.ps-currency-modes input').evaluate_all('(inputs) => inputs.map(input => input.id)') == [
                    'debtMixedPayment', 'debtPaymentUSD', 'debtPaymentUZS']
                assert page.locator('#debtPaymentPanel .ps-payment-row').count() == 4
                assert page.evaluate("[...document.querySelectorAll('#debtPaymentPanel input')].every(input => input.value === '')")
                assert page.evaluate('supplierPaymentEntries()') == []
                assert page.locator('#submitBtn').is_disabled()
                for code in ('USD', 'UZS'):
                    page.locator(f'#debtPayment{code}').check()
                    assert page.evaluate("[...document.querySelectorAll('#debtPaymentPanel input')].every(input => input.value === '')")
                    assert page.evaluate('supplierPaymentEntries()') == []
                page.locator('#debtMixedPayment').check()
                assert page.locator('#paymentCashUZS').input_value() == ''
                page.screenshot(path=str(screenshots / f'supplier-debt-empty-{width}.png'), full_page=True)
                page.locator('#paymentCashUZS').fill('200000')
                page.locator('#debtTabUSD').click()
                assert page.locator('#paymentCashUSD').input_value() == ''
                page.locator('#paymentCashUSD').fill('20')
                assert page.locator('#paymentCashUSD').input_value() == '20'
                page.locator('#debtPaymentUSD').check()
                assert not page.locator('#debtTabUZS').is_visible()
                assert page.locator('#paymentCashUSD').input_value() == '36'
                page.locator('#debtPaymentUZS').check()
                assert not page.locator('#debtTabUSD').is_visible()
                assert page.locator('#paymentCashUZS').input_value() == '450000'
                page.locator('#debtMixedPayment').check()
                assert page.locator('#paymentCashUZS').input_value() == '200000'
                page.locator('#debtTabUSD').click()
                assert page.locator('#paymentCashUSD').input_value() == '20'
                page.locator('#paymentCashUSD').fill('-1')
                assert page.locator('#submitBtn').is_disabled()
                page.locator('#paymentCashUSD').fill('21')
                assert page.locator('#submitBtn').is_disabled()
                page.locator('#paymentCashUZS').fill('125000')
                assert page.locator('#paymentCashUSD').input_value() == '10'
                assert page.locator('#submitBtn').is_disabled()
                page.locator('#debtPaymentPanel .ps-btn-remaining').last.click()
                assert page.locator('#paymentDebtUSD').input_value() == '10'
                mixed_amounts = page.evaluate('debtTabAmounts')
                for code in ('USD', 'UZS'):
                    page.locator(f'#debtPayment{code}').check()
                    page.locator('#debtMixedPayment').check()
                    assert page.evaluate('debtTabAmounts') == mixed_amounts
                assert not page.locator('#submitBtn').is_disabled()
                assert page.evaluate("""() => [...document.querySelectorAll('#debtPaymentPanel input')].every(input => {
                    const bounds = input.getBoundingClientRect();
                    return bounds.width >= 70 && bounds.left >= 0 && bounds.right <= innerWidth;
                })""")
                assert page.evaluate('supplierPaymentEntries()') == [
                    {'channel': 'cash', 'currency': 'USD', 'amount': '10'},
                    {'channel': 'cash', 'currency': 'UZS', 'amount': '200000'}]
                page.screenshot(path=str(screenshots / f'supplier-debt-{width}.png'), full_page=True)
                page.locator('#submitBtn').click()
                page.wait_for_function("document.getElementById('displayRemainingDebt')?.textContent === '0 USD + 125,000 UZS'")
                page.locator('[data-tab="payments"]').click()
                page.wait_for_function("document.getElementById('paymentsHistoryBody').textContent.includes('200,000 UZS')")
                page.screenshot(path=str(screenshots / f'supplier-history-{width}.png'), full_page=True)
                page.locator('.ps-reverse-btn').first.click()
                assert '200,000 UZS' in page.locator('#reverseModalInfo').inner_text()
                page.locator('#reverseConfirmBtn').click()
                page.wait_for_function("document.getElementById('displayRemainingDebt')?.textContent === '20 USD + 200,000 UZS'")
                for code, expected in (('USD', '36'), ('UZS', '450000')):
                    page.wait_for_function("document.getElementById('debtMixedPayment')?.checked")
                    page.locator(f'#debtPayment{code}').check()
                    assert page.locator(f'#paymentCash{code}').input_value() == ''
                    assert page.locator('#submitBtn').is_disabled()
                    page.locator('#debtPaymentPanel .ps-btn-remaining').first.click()
                    outgoing_entries = page.evaluate('supplierPaymentEntries()')
                    assert all(entry['channel'] == 'cash' and entry['currency'] == code for entry in outgoing_entries), outgoing_entries
                    assert sum(Decimal(entry['amount']) for entry in outgoing_entries) == Decimal(expected), outgoing_entries
                    with page.expect_response(lambda response: response.url.endswith('/api/suppliers/1/debt-payment')) as paid:
                        page.locator('#submitBtn').click()
                    assert paid.value.status == 200, paid.value.json()
                    page.wait_for_function("document.getElementById('displayRemainingDebt')?.textContent === '0 USD + 0 UZS'")
                    assert page.locator('#submitBtn').is_disabled()
                    history = page.request.get(f'{base_url}/api/debt-payments/by-supplier/1').json()
                    saved_entries = history['payments'][0]['native_payments']
                    assert all(entry['channel'] == 'cash' and entry['currency'] == code for entry in saved_entries), saved_entries
                    assert sum(Decimal(entry['amount']) for entry in saved_entries) == Decimal(expected), saved_entries
                    page.locator('[data-tab="payments"]').click()
                    page.locator('.ps-reverse-btn').first.click()
                    page.locator('#reverseConfirmBtn').click()
                    page.wait_for_function("document.getElementById('displayRemainingDebt')?.textContent === '20 USD + 200,000 UZS'")
                for path, selector in (('/suppliers', '#supplierTableBody'),
                                       ('/supplier/1/products', '.sp-running-total-cell'),
                                       ('/supplier/1/timeline', '#statDebt')):
                    response = page.goto(base_url + path)
                    assert response.status == 200
                    page.wait_for_function("selector => document.querySelector(selector)?.textContent.includes('200,000 UZS')", arg=selector)
                    page.screenshot(path=str(screenshots / f'supplier-{path.split("/")[-1]}-{width}.png'), full_page=True)
                assert not errors, errors
                page.close()
            browser.close()
        print(f'Desktop/mobile browser checks OK. Screenshots: {screenshots}')
    finally:
        server.shutdown()
        worker.join()


def check_supplier_api(application):
    from models import Supplier, SupplierPurchaseBatch, SupplierPayment
    client = application.app.test_client()
    with application.app.app_context():
        supplier = Supplier(name='Mixed supplier')
        application.db.session.add(supplier)
        application.db.session.commit()
        supplier_id = supplier.id
    products = [
        {'name': 'Supplier USD', 'quantity': 10, 'cost_price': '2', 'sell_price': '3',
         'cost_currency': 'USD', 'supplierId': supplier_id, 'location_type': 'store', 'location_id': 1},
        {'name': 'Supplier UZS', 'quantity': 10, 'cost_price': '20000', 'sell_price': '25000',
         'cost_currency': 'UZS', 'sell_currency': 'UZS', 'supplierId': supplier_id,
         'location_type': 'store', 'location_id': 1}]
    response = client.post('/api/batch-products', json={
        'products': products, 'supplier_payments': {str(supplier_id): {'exchange_rate': 12500}}})
    assert response.status_code == 201, response.get_json()
    with application.app.app_context():
        batch = SupplierPurchaseBatch.query.filter_by(supplier_id=supplier_id).one()
        assert batch.native_debt_usd == 20 and batch.native_debt_uzs == 200000
    response = client.post(f'/api/suppliers/{supplier_id}/debt-payment', json={
        'native_currency_payment': True, 'cash_currency': 'UZS', 'cash_uzs': 200000,
        'exchange_rate': 13000, 'debt_priority': 'UZS'})
    assert response.status_code == 200, response.get_json()
    assert Decimal(response.get_json()['native_debts']['USD']) == 20
    assert Decimal(response.get_json()['native_debts']['UZS']) == 0
    response = client.get(f'/api/debt-payments/by-supplier/{supplier_id}')
    assert response.status_code == 200, response.get_json()
    assert response.get_json()['payments'][0]['native_payments'][0]['amount'] == '200000'
    response = client.get(f'/api/supplier/{supplier_id}/timeline')
    assert response.status_code == 200, response.get_json()
    payment_event = next(event for event in response.get_json()['events'] if event['type'] == 'payment')
    assert Decimal(payment_event['native_debt_after']['USD']) == 20
    assert Decimal(payment_event['native_debt_after']['UZS']) == 0
    with application.app.app_context():
        payment = SupplierPayment.query.filter_by(supplier_id=supplier_id).one()
        payment_date = payment.payment_date.isoformat()
    response = client.post('/api/suppliers/debt-payment/reverse', json={
        'supplier_id': supplier_id, 'payment_date': payment_date})
    assert response.status_code == 200, response.get_json()
    assert Decimal(response.get_json()['native_debts']['UZS']) == 200000
    assert response.get_json()['restored_amount'] == 16
    mixed_cash = [{'channel': 'cash', 'currency': 'USD', 'amount': '20'},
                  {'channel': 'cash', 'currency': 'UZS', 'amount': '200000'}]
    response = client.post(f'/api/suppliers/{supplier_id}/debt-payment', json={
        'native_currency_payment': True, 'payments': mixed_cash, 'exchange_rate': 13000})
    assert response.status_code == 200, response.get_json()
    paid = response.get_json()
    assert all(Decimal(value) == 0 for value in paid['native_debts'].values())
    response = client.get(f'/api/debt-payments/by-supplier/{supplier_id}')
    assert response.get_json()['payments'][0]['native_payments'] == mixed_cash
    mixed_payment_date = response.get_json()['payments'][0]['payment_date_iso']
    response = client.post('/api/suppliers/debt-payment/reverse', json={
        'supplier_id': supplier_id, 'payment_date': mixed_payment_date})
    assert response.status_code == 200, response.get_json()
    assert {code: Decimal(value) for code, value in response.get_json()['native_debts'].items()} == {'USD': 20, 'UZS': 200000}
    response = client.post('/api/products', json={'products': products})
    assert response.status_code == 400
    supplier_ids = []
    for name in ('Multi USD supplier', 'Multi UZS supplier'):
        response = client.post('/api/suppliers', json={'name': name})
        assert response.status_code == 201
        supplier_ids.append(response.get_json()['supplier']['id'])
    grouped_products = [dict(product, name=f'Multi {index}', supplierId=supplier_ids[index])
                        for index, product in enumerate(products)]
    response = client.post('/api/batch-products', json={
        'products': grouped_products, 'supplier_payments': {
            str(supplier_ids[0]): {'cash_usd': 10},
            str(supplier_ids[1]): {'click_currency': 'UZS', 'click_uzs': 50000}}})
    assert response.status_code == 201, response.get_json()
    for identifier, expected in zip(supplier_ids, ({'USD': 10, 'UZS': 0}, {'USD': 0, 'UZS': 150000})):
        response = client.get(f'/api/supplier/{identifier}/timeline')
        debts = response.get_json()['supplier']['native_debts']
        assert {code: Decimal(value) for code, value in debts.items()} == expected
    print('Supplier API: mixed receipt -> new-rate payment -> exact reversal OK')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=5057)
    options = parser.parse_args()
    application = make_test_app()
    logging.disable(logging.CRITICAL)
    check_api(application)
    check_supplier_api(application)
    if options.browser:
        check_browser(application)
    if options.serve:
        application.app.run(host='127.0.0.1', port=options.port, use_reloader=False)
