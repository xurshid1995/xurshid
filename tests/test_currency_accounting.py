import unittest
import asyncio
import tempfile
from pathlib import Path
from unittest.mock import patch, AsyncMock
from decimal import Decimal

from flask import Flask
from database import db
from models import Customer, Product, Sale, SaleItem, DebtPayment, StoreStock
from currency_service import (normalize_product_prices, normalize_sale_prices,
                              snapshot_sale, snapshot_item, pay_native_debts,
                              reverse_native_payment, native_debts, prepare_native_average,
                              apply_native_return)

from currency_accounting import allocate_payment, equivalents, format_native_amounts


class CurrencyAccountingTests(unittest.TestCase):
    def test_async_confirmation_does_not_hide_som_debt(self):
        from telegram_bot import DebtTelegramBot
        bot = DebtTelegramBot.__new__(DebtTelegramBot)
        bot.bot = AsyncMock()
        self.assertTrue(asyncio.run(bot.send_payment_confirmation(
            1, 'Test', 5, 62500, 0, 100000, 'Test',
            native_debts={'USD': '0', 'UZS': '100000'})))
        text = bot.bot.send_message.call_args.kwargs['text']
        self.assertIn('Qolgan qarz: 100,000 UZS', text)
        self.assertNotIn("to'liq to'landi", text)

    def test_telegram_sale_and_payment_use_original_currencies(self):
        from datetime import datetime
        from telegram_bot import DebtTelegramBot
        bot = DebtTelegramBot.__new__(DebtTelegramBot)
        bot.token = 'test-only'
        debts = {'USD': '50', 'UZS': '200000'}
        with patch('telegram_bot.requests.post') as post:
            post.return_value.status_code = 200
            self.assertTrue(bot.send_sale_notification_sync(
                1, 'Test', 1, datetime(2026, 9, 26), 'Test', 825000, 0,
                total_amount_usd=66, debt_usd=66, native_debts=debts,
                total_native_debts=debts, native_payments=[]))
            text = post.call_args.kwargs['json']['text']
            self.assertIn('Qarz: 50 USD + 200,000 UZS', text)
            self.assertNotIn('Qarz: $66', text)
            self.assertTrue(bot.send_payment_confirmation_sync(
                1, 'Test', 66, 825000, 16, 200000, 0, 100000,
                native_debts={'USD': '0', 'UZS': '100000'},
                previous_native_debts=debts,
                native_payments=[{'channel': 'cash', 'currency': 'UZS', 'amount': '200000'}]))
            text = post.call_args.kwargs['json']['text']
            self.assertIn('Naqd: 200,000 UZS', text)
            self.assertIn('Qolgan qarz: 100,000 UZS', text)
            self.assertNotIn("to'liq to'landi", text)

    def test_native_debt_format_does_not_convert(self):
        self.assertEqual(format_native_amounts({'USD': '50', 'UZS': '200000'}),
                         '50 USD + 200,000 UZS')
        self.assertEqual(format_native_amounts({'USD': 0, 'UZS': '0.01'}), '0.01 UZS')

    def test_pdf_debts_are_native_in_both_receipt_formats(self):
        from pdf_generator import generate_sale_receipt_pdf
        for code in ('usd', 'uzs'):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                with patch('pdf_generator.canvas.Canvas') as canvas_class:
                    generate_sale_receipt_pdf({
                        'sale_id': 1, 'date': '2026-09-26',
                        'debt_usd': 66, 'debt_uzs': 825000,
                        'native_debts': {'USD': '50', 'UZS': '200000'},
                        'native_payments': [{'channel': 'cash', 'currency': 'UZS', 'amount': '12500'}],
                        'items': [{'name': 'Test', 'quantity': 1, 'unit_price_usd': 8,
                                   'unit_price_uzs': 100000, 'price_currency': 'UZS',
                                   'native_unit_price': '100000'}],
                    }, str(Path(directory) / 'receipt.pdf'), currency=code)
                texts = [call.args[-1] for call in canvas_class.return_value.drawRightString.call_args_list]
                self.assertIn('50 USD', texts)
                self.assertIn('200,000 UZS', texts)
                self.assertIn('12,500 UZS', texts)
                labels = [call.args[-1] for call in canvas_class.return_value.drawString.call_args_list]
                self.assertIn('100,000 UZS', labels)
                self.assertNotIn('$66', texts)
                self.assertNotIn("825,000 so'm", texts)

    def test_native_prices_survive_rate_change(self):
        self.assertEqual(equivalents('10', 'USD', '13000'), (10, 130000))
        self.assertEqual(equivalents('100000', 'UZS', '12500'), (8, 100000))
        self.assertEqual(equivalents('100000', 'UZS', '13000')[1], 100000)

    def test_mixed_sale(self):
        dollar_price = equivalents('10', 'USD', '12500')
        som_price = equivalents('100000', 'UZS', '12500')
        self.assertEqual(dollar_price[0] * 5 + som_price[0] * 2, 66)
        self.assertEqual(dollar_price[1] * 5 + som_price[1] * 2, 825000)

    def test_pay_only_som_debt(self):
        debts, applied, remaining = allocate_payment(
            50, 200000, 200000, 'UZS', 13000, 'UZS')
        self.assertEqual(debts, {'USD': 50, 'UZS': 0})
        self.assertEqual(applied, {'USD': 0, 'UZS': 200000})
        self.assertEqual(remaining, 0)

    def test_cross_currency_at_payment_rate(self):
        debts, applied, remaining = allocate_payment(
            50, 200000, 650000, 'UZS', 13000, 'USD')
        self.assertEqual(debts, {'USD': 0, 'UZS': 200000})
        self.assertEqual(applied['USD'], 50)
        self.assertEqual(remaining, 0)

    def test_partial_and_excess_payment(self):
        debts, _, remaining = allocate_payment(50, 200000, 70, 'USD', 12500, 'USD')
        self.assertEqual(debts, {'USD': 0, 'UZS': 0})
        self.assertEqual(remaining, 4)
        debts, _, _ = allocate_payment(50, 200000, 5, 'USD', 12500, 'UZS')
        self.assertEqual(debts, {'USD': 50, 'UZS': 137500})

    def test_invalid_input(self):
        for value, code, rate in [('NaN', 'USD', 1), (-1, 'USD', 1),
                                  (1, 'EUR', 1), (1, 'USD', 0),
                                  (1, 'USD', 'Infinity')]:
            with self.subTest(value=value, code=code, rate=rate):
                with self.assertRaises(ValueError):
                    equivalents(value, code, rate)


class CurrencyPersistenceTests(unittest.TestCase):
    def test_supplier_mixed_batch_snapshot(self):
        from models import Supplier, SupplierPurchaseBatch, SupplierPurchase
        from supplier_currency import snapshot_supplier_batch, batch_debts
        supplier = Supplier(name='Native supplier')
        batch = SupplierPurchaseBatch(supplier=supplier)
        batch.items = [
            SupplierPurchase(product_name='USD', quantity=10, cost_price=2,
                             total_amount=20, cost_currency='USD', native_cost_price=2),
            SupplierPurchase(product_name='UZS', quantity=10, cost_price=1,
                             total_amount=16, cost_currency='UZS', native_cost_price=20000)]
        db.session.add(batch)
        snapshot_supplier_batch(batch, {}, 11850)
        self.assertEqual(batch_debts(batch), {'USD': 20, 'UZS': 200000})
        payment = {'cash_currency': 'UZS', 'cash_uzs': 200000, 'debt_priority': 'UZS'}
        snapshot_supplier_batch(batch, payment, 11850)
        self.assertEqual(batch_debts(batch), {'USD': 20, 'UZS': 0})
        self.assertEqual(batch.native_payments[0]['amount'], '200000')

    def test_supplier_payment_new_rate_and_reversal(self):
        from models import Supplier, SupplierPurchaseBatch, SupplierPayment
        from supplier_currency import pay_supplier_native, reverse_supplier_native, batch_debts
        supplier = Supplier(name='Native supplier', balance_usd=36)
        batch = SupplierPurchaseBatch(supplier=supplier, total_amount=36, debt_amount=36,
                                      currency_rate=12500, native_debt_usd=20, native_debt_uzs=200000)
        db.session.add(batch)
        db.session.commit()
        result = pay_supplier_native(supplier.id, {'cash_currency': 'UZS', 'cash_uzs': 200000,
                                                   'exchange_rate': 13000, 'debt_priority': 'UZS'}, 13000, 'Test')
        self.assertEqual(batch_debts(batch), {'USD': 20, 'UZS': 0})
        self.assertEqual(Decimal(result['native_debts']['USD']), 20)
        payment = SupplierPayment.query.one()
        self.assertEqual(payment.native_allocation['book']['cash'], '16.0000000000')
        reverse_supplier_native(payment, supplier)
        self.assertEqual(batch_debts(batch), {'USD': 20, 'UZS': 200000})
        self.assertEqual(supplier.balance_usd, 36)
        db.session.rollback()
        with self.assertRaises(ValueError):
            pay_supplier_native(supplier.id, {'cash_usd': 1000}, 13000, 'Test')
        db.session.rollback()
        self.assertEqual(batch_debts(batch), {'USD': 20, 'UZS': 200000})

    def setUp(self):
        self.app = Flask(__name__)
        self.app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite://'
        db.init_app(self.app)
        self.context = self.app.app_context()
        self.context.push()
        db.create_all()
        self.customer = Customer(name='Currency test')
        db.session.add(self.customer)
        db.session.flush()

    def test_supplier_small_payments_leave_no_book_residual(self):
        from models import Supplier, SupplierPurchaseBatch, SupplierPayment
        from supplier_currency import book_value, pay_supplier_native, reverse_supplier_native, supplier_debts
        totals = {'USD': Decimal('0'), 'UZS': Decimal('200000')}
        original_book = book_value(totals, 11850)
        supplier = Supplier(name='Rounding supplier', balance_usd=original_book)
        batch = SupplierPurchaseBatch(supplier=supplier, total_amount=original_book, debt_amount=original_book,
                                      currency_rate=11850, native_debt_usd=0, native_debt_uzs=200000)
        db.session.add(batch)
        db.session.commit()
        for value in (1, 1, 199998):
            pay_supplier_native(supplier.id, {'cash_currency': 'UZS', 'cash_uzs': value}, 13000, 'Test')
            db.session.commit()
        self.assertEqual(supplier.balance_usd, 0)
        self.assertEqual(batch.debt_amount, 0)
        self.assertEqual({code: Decimal(value) for code, value in supplier_debts(supplier).items()}, {'USD': 0, 'UZS': 0})
        for payment in SupplierPayment.query.order_by(SupplierPayment.id).all():
            reverse_supplier_native(payment, supplier)
            db.session.delete(payment)
        db.session.commit()
        self.assertEqual(supplier.balance_usd, original_book)
        self.assertEqual(batch.native_debt_uzs, 200000)

    def test_supplier_priority_across_batches_and_legacy_residual(self):
        from models import Supplier, SupplierPurchaseBatch, SupplierPayment
        from supplier_currency import pay_supplier_native, reverse_supplier_native, supplier_debts
        supplier = Supplier(name='FIFO supplier', balance_usd=46)
        old = SupplierPurchaseBatch(supplier=supplier, total_amount=20, debt_amount=20)
        new = SupplierPurchaseBatch(supplier=supplier, total_amount=16, debt_amount=16,
                                    currency_rate=12500, native_debt_usd=0, native_debt_uzs=200000)
        db.session.add_all([old, new])
        db.session.commit()
        pay_supplier_native(supplier.id, {'click_usd': 5, 'debt_priority': 'UZS'}, 13000, 'Test')
        db.session.commit()
        self.assertEqual(old.debt_amount, 20)
        self.assertEqual(new.native_debt_uzs, 135000)
        pay_supplier_native(supplier.id, {'terminal_usd': 30, 'debt_priority': 'USD'}, 13000, 'Test')
        db.session.commit()
        self.assertEqual(old.debt_amount, 0)
        self.assertEqual(new.native_debt_uzs, 135000)
        self.assertEqual(Decimal(supplier_debts(supplier)['USD']), 0)
        for payment in SupplierPayment.query.order_by(SupplierPayment.id.desc()).all():
            reverse_supplier_native(payment, supplier)
            db.session.delete(payment)
        db.session.commit()
        self.assertEqual(supplier.balance_usd, 46)
        self.assertEqual(old.debt_amount, 20)
        self.assertEqual(new.native_debt_uzs, 200000)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        db.engine.dispose()
        self.context.pop()

    def make_sale(self, usd=50, uzs=200000):
        sale = Sale(customer_id=self.customer.id, currency_rate=Decimal('12500'),
                    total_amount=Decimal(str(usd)) + Decimal(str(uzs)) / 12500,
                    native_debt_usd=usd, native_debt_uzs=uzs,
                    debt_usd=Decimal(str(usd)) + Decimal(str(uzs)) / 12500,
                    payment_status='debt')
        db.session.add(sale)
        db.session.commit()
        return sale

    def test_product_and_sale_snapshot(self):
        data = {'cost_price': 50000, 'sell_price': 100000,
                'cost_currency': 'UZS', 'sell_currency': 'UZS'}
        normalize_product_prices(data, 12500)
        self.assertEqual(Decimal(data['cost_price']), 4)
        self.assertEqual(Decimal(data['sell_price']), 8)
        product = Product(name='Som product', cost_price=4, sell_price=8,
                          cost_currency='UZS', sell_currency='UZS',
                          native_cost_price=50000, native_sell_price=100000)
        db.session.add(product)
        db.session.flush()
        payload = {'items': [{'product_id': product.id, 'quantity': 2,
                             'native_unit_price': '100000', 'price_currency': 'UZS'}]}
        normalize_sale_prices(payload, 13000)
        item_data = payload['items'][0]
        item = SaleItem(unit_price=Decimal(item_data['unit_price']), unit_price_uzs=100000)
        snapshot_item(item, product, item_data)
        self.assertEqual(item.native_unit_price, 100000)
        self.assertEqual(item.cost_currency, 'UZS')
        self.assertEqual(item.native_cost_price, 50000)
        sale = Sale(customer_id=self.customer.id, currency_rate=Decimal('13000'))
        snapshot_sale(sale, payload['items'], {'debt_usd': str(Decimal(item_data['unit_price']) * 2)}, Decimal('13000'))
        self.assertEqual(native_debts(sale), {'USD': 0, 'UZS': 200000})

    def test_payment_and_reversal_at_new_rate(self):
        sale = self.make_sale()
        pay_native_debts({'customer_id': self.customer.id, 'cash_currency': 'UZS',
                          'cash_uzs': 200000, 'exchange_rate': 13000,
                          'debt_priority': 'UZS'}, 13000, 'test')
        db.session.commit()
        self.assertEqual(native_debts(sale), {'USD': 50, 'UZS': 0})
        payment = DebtPayment.query.one()
        self.assertEqual(payment.native_allocation['payments'][0]['currency'], 'UZS')
        reverse_native_payment(payment)
        db.session.commit()
        self.assertEqual(native_debts(sale), {'USD': 50, 'UZS': 200000})
        self.assertEqual(sale.debt_usd, 66)

    def test_priority_applies_across_sales(self):
        older = self.make_sale(usd=50, uzs=0)
        newer = self.make_sale(usd=0, uzs=200000)
        pay_native_debts({'customer_id': self.customer.id, 'cash_currency': 'UZS',
                          'cash_uzs': 200000, 'debt_priority': 'UZS'}, 13000, 'test')
        db.session.commit()
        self.assertEqual(native_debts(older)['USD'], 50)
        self.assertEqual(native_debts(newer)['UZS'], 0)

    def test_overpayment_is_reversible_once(self):
        sale = self.make_sale()
        pay_native_debts({'customer_id': self.customer.id, 'cash_usd': 70}, 12500, 'test')
        db.session.commit()
        self.assertEqual(self.customer.balance, 4)
        for payment in DebtPayment.query.all():
            reverse_native_payment(payment)
            db.session.delete(payment)
        db.session.commit()
        self.assertEqual(self.customer.balance, 0)
        self.assertEqual(sale.debt_usd, 66)

    def test_invalid_payment_rollback(self):
        sale = self.make_sale()
        with self.assertRaises(ValueError):
            pay_native_debts({'customer_id': self.customer.id, 'cash_usd': '-1'}, 12500, 'test')
        db.session.rollback()
        self.assertEqual(sale.debt_usd, 66)
        self.assertEqual(DebtPayment.query.count(), 0)

    def test_native_average_does_not_revalue_old_stock(self):
        product = Product(name='Average test', cost_price=4, sell_price=8,
                          cost_currency='UZS', native_cost_price=50000)
        product.store_stocks.append(StoreStock(store_id=1, quantity=2))
        data = {'cost_currency': 'UZS', 'quantity': 2, 'native_cost_price': 50000,
                'native_batch_cost': 50000, 'price_currency_rate': 13000}
        prepare_native_average(product, data)
        self.assertEqual(Decimal(data['_native_average_cost']), 50000)

    def test_return_reduces_only_original_currency_debt(self):
        sale = self.make_sale()
        apply_native_return(sale, {'USD': 0, 'UZS': 100000}, 'cash')
        self.assertEqual(native_debts(sale), {'USD': 50, 'UZS': 100000})
        self.assertEqual(sale.debt_usd, 58)

    def test_paid_return_credits_balance_without_inventing_debt(self):
        sale = self.make_sale(0, 0)
        sale.cash_usd = Decimal('8')
        apply_native_return(sale, {'USD': 0, 'UZS': 100000}, 'balance')
        self.assertEqual(native_debts(sale), {'USD': 0, 'UZS': 0})
        self.assertEqual(self.customer.balance, 8)
        self.assertEqual(sale.cash_usd, 0)


if __name__ == '__main__':
    unittest.main()
