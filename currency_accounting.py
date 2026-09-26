from decimal import Decimal, InvalidOperation, ROUND_HALF_UP, localcontext


USD_STEP = Decimal('0.0000000001')
UZS_STEP = Decimal('0.01')


def amount(value):
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as error:
        raise ValueError('Invalid monetary amount') from error
    if not result.is_finite() or result < 0:
        raise ValueError('Amount must be finite and nonnegative')
    return result


def currency(value):
    if value not in ('USD', 'UZS'):
        raise ValueError('Currency must be USD or UZS')
    return value


def exchange_rate(value):
    result = amount(value)
    if result == 0:
        raise ValueError('Exchange rate must be positive')
    return result


def format_native_amounts(values):
    parts = []
    for code in ('USD', 'UZS'):
        value = amount(values.get(code, 0))
        if value:
            formatted = f'{value:,.10f}'.rstrip('0').rstrip('.')
            parts.append(f'{formatted} {code}')
    return ' + '.join(parts) or '0 USD + 0 UZS'


def equivalents(value, native_currency, rate):
    value = amount(value)
    currency(native_currency)
    rate = exchange_rate(rate)
    with localcontext() as context:
        context.prec = 38
        usd = value if native_currency == 'USD' else value / rate
        uzs = value if native_currency == 'UZS' else value * rate
        return (usd.quantize(USD_STEP, rounding=ROUND_HALF_UP),
                uzs.quantize(UZS_STEP, rounding=ROUND_HALF_UP))


def allocate_payment(debt_usd, debt_uzs, paid, payment_currency, rate,
                     first_currency):
    debts = {'USD': amount(debt_usd), 'UZS': amount(debt_uzs)}
    currency(payment_currency)
    currency(first_currency)
    rate = exchange_rate(rate)
    remaining = amount(paid)
    applied = {'USD': Decimal('0'), 'UZS': Decimal('0')}
    order = (first_currency, 'UZS' if first_currency == 'USD' else 'USD')
    for target in order:
        factor = (Decimal('1') if target == payment_currency else
                  rate if target == 'UZS' else Decimal('1') / rate)
        step = USD_STEP if target == 'USD' else UZS_STEP
        reduction = min(debts[target], (remaining * factor).quantize(
            step, rounding=ROUND_HALF_UP))
        remaining = max(Decimal('0'), remaining - reduction / factor)
        applied[target] = reduction
        debts[target] -= reduction
    return debts, applied, remaining
