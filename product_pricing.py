from decimal import Decimal, InvalidOperation, localcontext


def positive_decimal(value, label):
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"{label}: musbat son kiriting") from None
    if not amount.is_finite() or amount <= 0:
        raise ValueError(f"{label}: musbat son kiriting")
    return amount


def currency_code(value):
    code = value or 'USD'
    if code not in ('USD', 'UZS'):
        raise ValueError('Valyuta USD yoki UZS bo\'lishi kerak')
    return code


def price_in_usd(amount, currency, rate=None):
    amount = positive_decimal(amount, 'Narx')
    if currency_code(currency) == 'USD':
        return amount
    rate = positive_decimal(rate, 'Valyuta kursi')
    with localcontext() as context:
        context.prec = 38
        return amount / rate


def product_sale_price(product, rate):
    if getattr(product, 'sell_currency_code', None) == 'UZS':
        return price_in_usd(product.sell_price_original, 'UZS', rate)
    return product.sell_price


def receipt_prices(data, rate):
    cost_currency = currency_code(data.get('cost_currency_code'))
    sell_currency = currency_code(data.get('sell_currency_code'))
    original_cost = positive_decimal(
        data.get('lastBatchCost', data['cost_price']), 'Tan narx')
    original_sell = positive_decimal(data['sell_price'], 'Sotish narxi')
    if 'UZS' in (cost_currency, sell_currency):
        rate = positive_decimal(rate, 'Valyuta kursi')
        quoted_rate = positive_decimal(data.get('receipt_exchange_rate'), 'Kirim kursi')
        if quoted_rate != rate:
            raise ValueError('Kurs o\'zgardi. Kirim narxlari va to\'lovni qayta tekshiring.')
    return {
        'cost_currency_code': cost_currency,
        'sell_currency_code': sell_currency,
        'cost_price_original': original_cost,
        'sell_price_original': original_sell,
        'receipt_exchange_rate': rate,
    }


def prepare_sale_prices(items, products, rate, quoted_rate):
    has_uzs = False
    for item in items:
        product = products.get(int(item.get('product_id') or item.get('id')))
        if product is None:
            raise ValueError('Mahsulot topilmadi')
        product_currency = getattr(product, 'sell_currency_code', None)
        requested_currency = item.get('price_currency_code') or product_currency
        code = currency_code('UZS' if product_currency == 'UZS' else requested_currency)
        item['price_currency_code'] = code
        if code == 'UZS':
            has_uzs = True
            actual_rate = positive_decimal(rate, 'Valyuta kursi')
            if positive_decimal(quoted_rate, 'Savdo kursi') != actual_rate:
                raise ValueError('Kurs o\'zgardi. To\'lov oynasini qayta oching.')
            original = positive_decimal(
                item.get('price_uzs') or product.sell_price_original, 'So\'m narxi')
            amount = price_in_usd(original, code, actual_rate)
            supplied = positive_decimal(
                item.get('unit_price') or item.get('price'), 'Sotish narxi')
            if abs(amount - supplied) > Decimal('0.00001'):
                raise ValueError('So\'m va dollar narxlari kursga mos emas. To\'lovni qayta tekshiring.')
            item['unit_price'] = str(amount)
            item['price'] = str(amount)
            item['price_uzs'] = str(original)
            item['unit_price_original'] = str(original)
        else:
            item['unit_price_original'] = str(
                item.get('unit_price') or item.get('price', 0))
    return has_uzs


def validate_legacy_price_edit(product, cost_price, sell_price):
    for kind, supplied in (('cost', cost_price), ('sell', sell_price)):
        if getattr(product, f'{kind}_currency_code', None) == 'UZS':
            current = Decimal(str(getattr(product, f'{kind}_price')))
            if abs(Decimal(str(supplied)) - current) > Decimal('0.00001'):
                raise ValueError(
                    "So'm narxini dollar maydonida o'zgartirib bo'lmaydi. "
                    "Yangi kirim narxini Mahsulot qo'shish sahifasida UZS bilan kiriting.")
