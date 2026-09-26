from decimal import Decimal

from currency_accounting import amount, currency, equivalents, exchange_rate, allocate_payment
from database import db, get_tashkent_time
from models import Product, Sale, Customer, DebtPayment


def normalize_product_prices(data, rate):
    rate = exchange_rate(rate)
    if amount(data.get('quantity', 1)) <= 0:
        raise ValueError('Mahsulot miqdori noldan katta bolishi kerak')
    original_batch = data.get('native_batch_cost', data.get(
        'native_cost_price', data.get('lastBatchCost', data['cost_price'])))
    for kind in ('cost', 'sell'):
        code = currency(data.get(f'{kind}_currency', 'USD'))
        native = amount(original_batch if kind == 'cost' else
                        data.get(f'native_{kind}_price', data[f'{kind}_price']))
        data[f'{kind}_currency'] = code
        usd, uzs = equivalents(native, code, rate)
        data[f'native_{kind}_price'] = str(usd if code == 'USD' else uzs)
        data[f'{kind}_price'] = str(usd)
    native_batch = original_batch
    data['lastBatchCost'] = str(equivalents(native_batch, data['cost_currency'], rate)[0])
    data['price_currency_rate'] = str(rate)


def sync_legacy_price_edit(product, rate):
    rate = exchange_rate(rate)
    product.price_currency_rate = rate
    product.native_sell_price = (product.sell_price if product.sell_currency == 'USD'
                                 else equivalents(product.sell_price, 'USD', rate)[1])
    product.native_cost_price = (product.cost_price if product.cost_currency == 'USD'
                                 else equivalents(product.cost_price, 'USD', rate)[1])


def save_product_currency(product, data):
    product.cost_currency = data['cost_currency']
    product.sell_currency = data['sell_currency']
    product.price_currency_rate = exchange_rate(data['price_currency_rate'])
    product.native_sell_price = amount(data['native_sell_price'])
    product.native_last_batch_cost = amount(data.get('native_batch_cost', data['native_cost_price']))
    product.native_cost_price = amount(data.get('_native_average_cost', data['native_cost_price']))


def prepare_native_average(product, data):
    previous_quantity = sum((amount(stock.quantity) for stock in
                             [*product.warehouse_stocks, *product.store_stocks]), Decimal('0'))
    new_quantity = amount(data['quantity'])
    code = data['cost_currency']
    if product.cost_currency == code and product.native_cost_price is not None:
        previous_cost = product.native_cost_price
    else:
        previous_cost = (product.cost_price if code == 'USD' else
                         product.cost_price * exchange_rate(data['price_currency_rate']))
    batch_cost = amount(data.get('native_batch_cost', data['native_cost_price']))
    data['_native_average_cost'] = str((previous_cost * previous_quantity + batch_cost * new_quantity)
                                       / (previous_quantity + new_quantity))


def normalize_sale_prices(data, current_rate):
    rate = exchange_rate(data.get('exchange_rate', current_rate))
    if abs(rate - exchange_rate(current_rate)) > Decimal('0.0001'):
        raise ValueError('Kurs yangilangan. Savdo sahifasini yangilang.')
    items = data.get('items', [])
    product_ids = [item.get('product_id') or item.get('id') for item in items]
    products = {product.id: product for product in Product.query.filter(
        Product.id.in_(product_ids)).all()}
    for item in items:
        product = products.get(int(item.get('product_id') or item.get('id')))
        if product is None:
            raise ValueError('Mahsulot topilmadi')
        code = currency(item.get('price_currency', product.sell_currency or 'USD'))
        native = item.get('native_unit_price')
        if native is None:
            usd = item.get('unit_price', item.get('price_usd', item.get('price', 0)))
            native = usd if code == 'USD' else item.get('price_uzs', amount(usd) * rate)
        usd, uzs = equivalents(native, code, rate)
        if amount(item.get('quantity', 0)) <= 0:
            raise ValueError('Mahsulot miqdori noldan katta bolishi kerak')
        item.update(price_currency=code, native_unit_price=str(usd if code == 'USD' else uzs),
                    unit_price=str(usd), price=str(usd), price_usd=str(usd),
                    price_uzs=str(uzs))
    data['exchange_rate'] = str(rate)
    return rate


def snapshot_item(item, product, data):
    item.price_currency = currency(data.get('price_currency', product.sell_currency or 'USD'))
    fallback_price = item.unit_price if item.price_currency == 'USD' else item.unit_price_uzs
    item.native_unit_price = amount(data.get('native_unit_price', fallback_price))
    item.cost_currency = product.cost_currency or 'USD'
    item.native_cost_price = (product.native_cost_price if product.native_cost_price is not None
                              else product.cost_price)


def native_debts(sale):
    return {'USD': amount(sale.native_debt_usd if sale.native_debt_usd is not None
                          else sale.debt_usd or 0),
            'UZS': amount(sale.native_debt_uzs or 0)}


def set_native_debts(sale, debts):
    sale.native_debt_usd = amount(debts['USD'])
    sale.native_debt_uzs = amount(debts['UZS'])
    rate = exchange_rate(sale.currency_rate)
    sale.debt_usd = sale.native_debt_usd + equivalents(sale.native_debt_uzs, 'UZS', rate)[0]
    sale.debt_amount = sale.native_debt_usd * rate + sale.native_debt_uzs


def snapshot_sale(sale, items, payment, rate):
    debts = {'USD': Decimal('0'), 'UZS': Decimal('0')}
    for item in items:
        debts[item['price_currency']] += amount(item['native_unit_price']) * amount(item['quantity'])
    native_payments = []
    first = currency(payment.get('debt_priority', 'USD'))
    for channel in ('cash', 'click', 'terminal', 'balance'):
        code = currency(payment.get(f'{channel}_currency', 'USD'))
        usd = amount(payment.get('balance_used' if channel == 'balance' else f'{channel}_usd', 0))
        value = usd if code == 'USD' else amount(payment.get(f'{channel}_uzs', usd * rate))
        debts, applied, extra = allocate_payment(debts['USD'], debts['UZS'], value, code, rate, first)
        if extra > (Decimal('0.01') if code == 'UZS' else Decimal('0.00001')):
            raise ValueError('Tolov savdo summasidan katta')
        native_payments.append({'channel': channel, 'currency': code, 'amount': str(value),
                                'applied_usd': str(applied['USD']), 'applied_uzs': str(applied['UZS'])})
    expected = amount(payment.get('debt_usd', 0))
    actual = debts['USD'] + equivalents(debts['UZS'], 'UZS', rate)[0]
    if abs(actual - expected) > Decimal('0.00001'):
        raise ValueError('Qarz va tolov summalari mos emas')
    if actual and not sale.customer_id:
        raise ValueError('Qarz uchun mijozni tanlang')
    sale.native_payments = native_payments
    set_native_debts(sale, debts)


def customer_native_debts(customer_id):
    sales = Sale.query.filter(Sale.customer_id == customer_id,
                              Sale.payment_status != 'pending', Sale.debt_usd > 0).all()
    totals = {'USD': Decimal('0'), 'UZS': Decimal('0')}
    for sale in sales:
        debts = native_debts(sale)
        for code in totals:
            totals[code] += debts[code]
    return {code: str(value) for code, value in totals.items()}


def pay_native_debts(data, current_rate, received_by):
    rate = exchange_rate(data.get('exchange_rate', current_rate))
    customer = Customer.query.filter_by(id=data['customer_id']).with_for_update().first()
    if customer is None:
        raise ValueError('Mijoz topilmadi')
    first = currency(data.get('debt_priority', 'USD'))
    channels = {}
    for channel in ('cash', 'click', 'terminal'):
        code = currency(data.get(f'{channel}_currency', 'USD'))
        value = amount(data.get(f'{channel}_{code.lower()}', 0))
        channels[channel] = {'currency': code, 'remaining': value}
    if not any(entry['remaining'] for entry in channels.values()):
        raise ValueError('Tolov summasi noldan katta bolishi kerak')
    sales = Sale.query.filter(Sale.customer_id == customer.id, Sale.debt_usd > 0,
                              Sale.payment_status != 'pending').order_by(
                                  Sale.created_at, Sale.id).with_for_update().all()
    if not sales:
        raise ValueError('Qarzli savdolar topilmadi')
    previous_debts = {code: str(sum((native_debts(sale)[code] for sale in sales), Decimal('0')))
                      for code in ('USD', 'UZS')}
    received_payments = [{'channel': channel, 'currency': entry['currency'],
                          'amount': str(entry['remaining'])}
                         for channel, entry in channels.items()]
    records = {}
    payment_time = get_tashkent_time()
    for target in (first, 'UZS' if first == 'USD' else 'USD'):
        for sale in sales:
            debts = native_debts(sale)
            for channel, entry in channels.items():
                if not debts[target] or not entry['remaining']:
                    continue
                before = entry['remaining']
                _, applied, entry['remaining'] = allocate_payment(
                    debts['USD'] if target == 'USD' else 0,
                    debts['UZS'] if target == 'UZS' else 0,
                    before, entry['currency'], rate, target)
                used = before - entry['remaining']
                reduction = applied[target]
                debts[target] -= reduction
                book_usd = equivalents(reduction, target, sale.currency_rate)[0]
                paid_usd = equivalents(used, entry['currency'], rate)[0]
                setattr(sale, f'{channel}_usd', (getattr(sale, f'{channel}_usd') or 0) + book_usd)
                setattr(sale, f'{channel}_amount', getattr(sale, f'{channel}_usd') * sale.currency_rate)
                record = records.setdefault(sale.id, {
                    'cash': Decimal('0'), 'click': Decimal('0'), 'terminal': Decimal('0'),
                    'applied_usd': Decimal('0'), 'applied_uzs': Decimal('0'),
                    'book': {'cash': Decimal('0'), 'click': Decimal('0'), 'terminal': Decimal('0')},
                    'payments': []})
                record[channel] += paid_usd
                record[f'applied_{target.lower()}'] += reduction
                record['book'][channel] += book_usd
                record['payments'].append({'channel': channel, 'currency': entry['currency'], 'amount': str(used)})
                set_native_debts(sale, debts)
                sale.payment_status = 'paid' if sale.debt_usd == 0 else 'partial'
                sale.updated_at = payment_time
    total_paid = Decimal('0')
    for sale_id, record in records.items():
        paid = record['cash'] + record['click'] + record['terminal']
        total_paid += paid
        db.session.add(DebtPayment(
            customer_id=customer.id, sale_id=sale_id, payment_date=payment_time,
            cash_usd=record['cash'], click_usd=record['click'], terminal_usd=record['terminal'],
            total_usd=paid, currency_rate=rate, received_by=received_by,
            native_allocation={'applied_usd': str(record['applied_usd']),
                               'applied_uzs': str(record['applied_uzs']),
                               'book': {key: str(value) for key, value in record['book'].items()},
                               'payments': record['payments']}))
    balance_added = sum((equivalents(entry['remaining'], entry['currency'], rate)[0]
                         for entry in channels.values()), Decimal('0'))
    if balance_added:
        customer.balance = (customer.balance or Decimal('0')) + balance_added
        db.session.add(DebtPayment(
            customer_id=customer.id, payment_date=payment_time, total_usd=balance_added,
            cash_usd=equivalents(channels['cash']['remaining'], channels['cash']['currency'], rate)[0],
            click_usd=equivalents(channels['click']['remaining'], channels['click']['currency'], rate)[0],
            terminal_usd=equivalents(channels['terminal']['remaining'], channels['terminal']['currency'], rate)[0],
            currency_rate=rate, received_by=received_by,
            native_allocation={'balance_usd': str(balance_added)}))
    customer.last_debt_payment_usd = total_paid
    customer.last_debt_payment_uzs = total_paid * rate
    customer.last_debt_payment_rate = rate
    customer.last_debt_payment_date = payment_time
    return {'success': True, 'paid_amount': float(total_paid), 'updated_sales': list(records),
            'balance_added': float(balance_added), 'new_balance': float(customer.balance or 0),
            'native_debts': customer_native_debts(customer.id),
            'previous_native_debts': previous_debts, 'native_payments': received_payments}


def reverse_native_payment(payment):
    allocation = payment.native_allocation
    if allocation.get('balance_usd'):
        customer = Customer.query.filter_by(id=payment.customer_id).with_for_update().one()
        value = amount(allocation['balance_usd'])
        if (customer.balance or 0) < value:
            raise ValueError('Bu tolovdan qolgan balans ishlatilgan; bekor qilib bolmaydi')
        customer.balance -= value
    if payment.sale_id:
        sale = Sale.query.filter_by(id=payment.sale_id).with_for_update().one()
        debts = native_debts(sale)
        debts['USD'] += amount(allocation['applied_usd'])
        debts['UZS'] += amount(allocation['applied_uzs'])
        set_native_debts(sale, debts)
        for channel, value in allocation['book'].items():
            restored = (getattr(sale, f'{channel}_usd') or 0) - amount(value)
            if restored < 0:
                raise ValueError('Savdo tolovi ozgargan; bekor qilishni tekshiring')
            setattr(sale, f'{channel}_usd', restored)
            setattr(sale, f'{channel}_amount', restored * sale.currency_rate)
        sale.payment_status = 'partial'
        sale.updated_at = get_tashkent_time()


def apply_native_return(sale, returned, refund_type):
    if refund_type not in ('cash', 'balance', 'debt'):
        raise ValueError('Qaytarish turi notogri')
    debts = native_debts(sale)
    reduced = {}
    refund = {}
    for code in ('USD', 'UZS'):
        value = amount(returned[code])
        reduced[code] = min(debts[code], value)
        debts[code] -= reduced[code]
        refund[code] = value - reduced[code]
    set_native_debts(sale, debts)
    refund_usd = refund['USD'] + equivalents(refund['UZS'], 'UZS', sale.currency_rate)[0]
    remaining = refund_usd
    for channel in ('cash', 'click', 'terminal', 'balance'):
        field = f'{channel}_usd'
        current = amount(getattr(sale, field) or 0)
        reduction = min(current, remaining)
        setattr(sale, field, current - reduction)
        if channel != 'balance':
            setattr(sale, f'{channel}_amount', (current - reduction) * sale.currency_rate)
        remaining -= reduction
    if remaining > Decimal('0.00001'):
        raise ValueError('Qaytarish summasi savdoning tolangan qismidan katta')
    if refund_usd and refund_type in ('balance', 'debt'):
        customer = Customer.query.filter_by(id=sale.customer_id).with_for_update().first()
        if customer is None:
            raise ValueError('Balansga qaytarish uchun mijoz kerak')
        customer.balance = (customer.balance or Decimal('0')) + refund_usd
    return {'debt_reduced': {code: str(value) for code, value in reduced.items()},
            'refund': {code: str(value) for code, value in refund.items()},
            'refund_type': refund_type, 'currency_rate': str(sale.currency_rate)}
