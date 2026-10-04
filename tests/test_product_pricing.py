import unittest
from decimal import Decimal
from types import SimpleNamespace

from product_pricing import price_in_usd, product_sale_price, prepare_sale_prices, receipt_prices


class ProductPricingTests(unittest.TestCase):
    def test_uzs_price_uses_sale_rate(self):
        product = SimpleNamespace(
            sell_currency_code='UZS', sell_price_original=Decimal('6000'),
            sell_price=Decimal('0.5'))
        self.assertEqual(product_sale_price(product, 12000), Decimal('0.5'))
        self.assertEqual(product_sale_price(product, 12500), Decimal('0.48'))
        self.assertEqual(product.sell_price_original, Decimal('6000'))

    def test_legacy_usd_price_is_unchanged(self):
        product = SimpleNamespace(sell_price=Decimal('12.34567'))
        self.assertEqual(product_sale_price(product, None), product.sell_price)
        self.assertEqual(price_in_usd('12.34567', 'USD'), product.sell_price)

    def test_invalid_values_are_rejected(self):
        for value in ('NaN', 'Infinity', '-1', '0', None, 'bad'):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    price_in_usd(value, 'UZS', 12000)
                with self.assertRaises(ValueError):
                    price_in_usd('6000', 'UZS', value)
        with self.assertRaises(ValueError):
            price_in_usd('6000', 'EUR', 12000)

    def test_historical_cost_is_not_revalued(self):
        receipt_cost = price_in_usd('5000', 'UZS', 12500)
        sale_price = price_in_usd('6000', 'UZS', 12000)
        self.assertEqual(receipt_cost, Decimal('0.4'))
        self.assertEqual(sale_price - receipt_cost, Decimal('0.1'))

    def test_receipt_preserves_originals_and_rejects_stale_rate(self):
        data = dict(cost_price='5000', sell_price='6000',
                    cost_currency_code='UZS', sell_currency_code='UZS',
                    receipt_exchange_rate='12000')
        prices = receipt_prices(data, 12000)
        self.assertEqual(prices['cost_price_original'], Decimal('5000'))
        with self.assertRaises(ValueError):
            receipt_prices(data, 12500)

    def test_sale_price_is_verified_on_server(self):
        products = {1: SimpleNamespace(sell_currency_code='UZS', sell_price_original=6000)}
        item = dict(id=1, unit_price='0.48', price_uzs='6000')
        self.assertTrue(prepare_sale_prices([item], products, 12500, 12500))
        self.assertEqual(item['unit_price_original'], '6000')
        self.assertEqual(Decimal(item['unit_price']), Decimal('0.48'))
        with self.assertRaises(ValueError):
            prepare_sale_prices([item], products, 13000, 12500)
        with self.assertRaises(ValueError):
            prepare_sale_prices([dict(id=1, unit_price='0.50', price_uzs=6000)], products, 12500, 12500)
        with self.assertRaises(ValueError):
            prepare_sale_prices([dict(id=1, unit_price='0.50', price_uzs=6000,
                                      price_currency_code='USD')], products, 12500, 12500)


if __name__ == '__main__':
    unittest.main()
