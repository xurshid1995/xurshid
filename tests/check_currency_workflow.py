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
                response = page.goto(f'{base_url}/add_product_session')
                assert response.status == 200
                page.locator('#cost_currency').select_option('UZS')
                page.locator('#sell_currency').select_option('UZS')
                page.locator('#cost_price').fill('50000')
                page.locator('#sell_price').fill('100000')
                page.screenshot(path=str(screenshots / f'product-{width}.png'), full_page=True)
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
                assert not errors, errors
                page.close()
            browser.close()
        print(f'Desktop/mobile browser checks OK. Screenshots: {screenshots}')
    finally:
        server.shutdown()
        worker.join()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--browser', action='store_true')
    parser.add_argument('--serve', action='store_true')
    parser.add_argument('--port', type=int, default=5057)
    options = parser.parse_args()
    application = make_test_app()
    logging.disable(logging.CRITICAL)
    check_api(application)
    if options.browser:
        check_browser(application)
    if options.serve:
        application.app.run(host='127.0.0.1', port=options.port, use_reloader=False)
