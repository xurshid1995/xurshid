from app import app, db, Supplier, SupplierPurchase, SupplierPayment

with app.app_context():
    suppliers = Supplier.query.filter(Supplier.name.ilike('%Ravon%')).all()
    for s in suppliers:
        print('---')
        print('supplier_id:', s.id, 'name:', s.name, 'balance_usd:', s.balance_usd)
        purchases = SupplierPurchase.query.filter_by(supplier_id=s.id).all()
        total_debt = sum(float(p.debt_amount or 0) for p in purchases)
        total_amount = sum(float(p.total_amount or 0) for p in purchases)
        total_paid = sum(float(p.paid_amount or 0) for p in purchases)
        print('purchases count:', len(purchases), 'sum(debt_amount):', total_debt, 'sum(total_amount):', total_amount, 'sum(paid_amount):', total_paid)
        for p in purchases:
            print(' purchase', p.id, 'batch', p.batch_id, 'product', p.product_name, 'total', p.total_amount, 'paid', p.paid_amount, 'debt', p.debt_amount, 'payment_type', p.payment_type)
        payments = SupplierPayment.query.filter_by(supplier_id=s.id).all()
        total_payments = sum(float(pm.amount_usd or 0) for pm in payments)
        print('payments count:', len(payments), 'sum(amount_usd):', total_payments)
        for pm in payments:
            print(' payment', pm.id, 'purchase_id', pm.purchase_id, 'amount_usd', pm.amount_usd, 'date', pm.payment_date)
