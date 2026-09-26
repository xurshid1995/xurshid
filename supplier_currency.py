from decimal import Decimal, ROUND_HALF_UP

from currency_accounting import amount, currency, exchange_rate, equivalents, allocate_payment
from database import db, get_tashkent_time
from models import Supplier, SupplierPurchaseBatch, SupplierPayment


def batch_debts(batch):
    return {'USD': amount(batch.native_debt_usd if batch.native_debt_usd is not None else batch.debt_amount or 0),
            'UZS': amount(batch.native_debt_uzs or 0)}


def book_value(values, rate):
    return amount(values['USD']) + equivalents(values['UZS'], 'UZS', rate)[0]


def payment_channels(data):
    result = []
    for channel in ('cash', 'click', 'terminal'):
        code = currency(data.get(f'{channel}_currency', 'USD'))
        value = amount(data.get(f'{channel}_{code.lower()}', 0) or 0)
        result.append({'channel': channel, 'currency': code, 'amount': str(value)})
    return result


def snapshot_supplier_batch(batch, data, rate):
    rate = exchange_rate(rate)
    totals = {'USD': Decimal('0'), 'UZS': Decimal('0')}
    for item in batch.items:
        totals[currency(item.cost_currency)] += amount(item.native_cost_price) * amount(item.quantity)
    totals = {code: value.quantize(Decimal('0.01') if code == 'UZS' else Decimal('0.0000000001'), rounding=ROUND_HALF_UP)
              for code, value in totals.items()}
    debts = dict(totals)
    payments = payment_channels(data)
    first = currency(data.get('debt_priority', 'USD'))
    book = {channel: Decimal('0') for channel in ('cash', 'click', 'terminal')}
    for payment in payments:
        debts, applied, extra = allocate_payment(
            debts['USD'], debts['UZS'], payment['amount'], payment['currency'], rate, first)
        tolerance = Decimal('0.01') if payment['currency'] == 'UZS' else Decimal('0.00001')
        if extra > tolerance:
            raise ValueError('Tolov kirim summasidan katta')
        book[payment['channel']] += book_value(applied, rate)
    batch.currency_rate = rate
    batch.native_total_usd, batch.native_total_uzs = totals['USD'], totals['UZS']
    batch.native_debt_usd, batch.native_debt_uzs = debts['USD'], debts['UZS']
    batch.native_initial_debts = {code: str(value) for code, value in debts.items()}
    batch.native_payments = payments
    batch.total_amount = book_value(totals, rate)
    batch.debt_amount = book_value(debts, rate)
    batch.paid_amount = batch.total_amount - batch.debt_amount
    batch.initial_paid_amount = batch.paid_amount
    for channel, value in book.items():
        setattr(batch, f'{channel}_usd', value)
    batch.payment_type = 'cash' if not batch.debt_amount else 'partial' if batch.paid_amount else 'debt'


def supplier_debts(supplier):
    batches = SupplierPurchaseBatch.query.filter_by(supplier_id=supplier.id).all()
    book_total = sum((amount(batch.debt_amount or 0) for batch in batches), Decimal('0'))
    totals = {'USD': max(Decimal('0'), amount(supplier.balance_usd or 0) - book_total), 'UZS': Decimal('0')}
    for batch in batches:
        for code, value in batch_debts(batch).items():
            totals[code] += value
    return {code: str(value) for code, value in totals.items()}


def pay_supplier_native(supplier_id, data, current_rate, paid_by):
    rate = exchange_rate(data.get('exchange_rate') or current_rate)
    first = currency(data.get('debt_priority', 'USD'))
    supplier = Supplier.query.filter_by(id=supplier_id).populate_existing().with_for_update().one()
    batches = SupplierPurchaseBatch.query.filter_by(supplier_id=supplier_id).order_by(
        SupplierPurchaseBatch.created_at, SupplierPurchaseBatch.id).populate_existing().with_for_update().all()
    previous = supplier_debts(supplier)
    remaining = payment_channels(data)
    if not any(amount(entry['amount']) for entry in remaining):
        raise ValueError('Tolov summasi noldan katta bolishi kerak')
    residual = max(Decimal('0'), amount(supplier.balance_usd or 0)
                   - sum((amount(batch.debt_amount or 0) for batch in batches), Decimal('0')))
    targets = [(batch, batch_debts(batch)) for batch in batches]
    if residual:
        targets.append((None, {'USD': residual, 'UZS': Decimal('0')}))
    records = {}
    for code in (first, 'UZS' if first == 'USD' else 'USD'):
        for batch, debts in targets:
            for entry in remaining:
                available = amount(entry['amount'])
                if not available or not debts[code]:
                    continue
                _, applied, extra = allocate_payment(
                    debts['USD'] if code == 'USD' else 0, debts['UZS'] if code == 'UZS' else 0,
                    available, entry['currency'], rate, code)
                used = available - extra
                if not applied[code]:
                    continue
                entry['amount'] = str(extra)
                debts[code] -= applied[code]
                new_book_debt = book_value(debts, batch.currency_rate or rate) if batch else debts['USD']
                book = (batch.debt_amount - new_book_debt) if batch else applied['USD']
                record = records.setdefault(batch.id if batch else None, {
                    'USD': Decimal('0'), 'UZS': Decimal('0'), 'payments': [],
                    'book': {channel: Decimal('0') for channel in ('cash', 'click', 'terminal')}})
                record[code] += applied[code]
                record['book'][entry['channel']] += book
                record['payments'].append({'channel': entry['channel'], 'currency': entry['currency'], 'amount': str(used)})
                supplier.balance_usd = (supplier.balance_usd or 0) - book
                if batch:
                    if batch.native_debt_usd is None:
                        batch.native_initial_debts = {'USD': str(batch.total_amount - batch.initial_paid_amount), 'UZS': '0'}
                    batch.native_debt_usd, batch.native_debt_uzs = debts['USD'], debts['UZS']
                    batch.debt_amount = new_book_debt
                    batch.paid_amount = batch.total_amount - batch.debt_amount
                    field = f"{entry['channel']}_usd"
                    setattr(batch, field, (getattr(batch, field) or 0) + book)
                    batch.payment_type = 'cash' if not batch.debt_amount else 'partial'
    if any(amount(entry['amount']) > (Decimal('0.01') if entry['currency'] == 'UZS' else Decimal('0.00001'))
           for entry in remaining):
        raise ValueError('Tolov joriy qarzdan katta')
    payment_time = get_tashkent_time()
    for batch_id, record in records.items():
        actual = {channel: Decimal('0') for channel in ('cash', 'click', 'terminal')}
        for entry in record['payments']:
            actual[entry['channel']] += equivalents(entry['amount'], entry['currency'], rate)[0]
        db.session.add(SupplierPayment(
            supplier_id=supplier_id, batch_id=batch_id, payment_date=payment_time,
            amount_usd=sum(actual.values()), cash_usd=actual['cash'], click_usd=actual['click'],
            terminal_usd=actual['terminal'], currency_rate=rate, paid_by=paid_by,
            payment_method='mixed', notes=data.get('notes') or None,
            native_allocation={'USD': str(record['USD']), 'UZS': str(record['UZS']),
                               'book': {channel: str(value) for channel, value in record['book'].items()},
                               'payments': record['payments']}))
    db.session.flush()
    return {'success': True, 'new_balance': float(supplier.balance_usd),
            'native_debts': supplier_debts(supplier), 'previous_native_debts': previous,
            'payment_date': payment_time.isoformat(), 'batch_ids': [key for key in records if key is not None]}


def reverse_supplier_native(payment, supplier):
    allocation = payment.native_allocation
    book = {channel: amount(value) for channel, value in allocation['book'].items()}
    restored = sum(book.values())
    if payment.batch_id:
        batch = SupplierPurchaseBatch.query.filter_by(id=payment.batch_id).with_for_update().one()
        debts = batch_debts(batch)
        for code in debts:
            debts[code] += amount(allocation[code])
        batch.native_debt_usd, batch.native_debt_uzs = debts['USD'], debts['UZS']
        batch.debt_amount = (batch.debt_amount or 0) + restored
        batch.paid_amount = (batch.paid_amount or 0) - restored
        if batch.paid_amount < -Decimal('0.00001'):
            raise ValueError('Kirim tolovi ozgargan, bekor qilib bolmaydi')
        for channel, value in book.items():
            field = f'{channel}_usd'
            setattr(batch, field, (getattr(batch, field) or 0) - value)
        batch.payment_type = 'debt' if not batch.paid_amount else 'partial'
    supplier.balance_usd = (supplier.balance_usd or 0) + restored
    return restored
