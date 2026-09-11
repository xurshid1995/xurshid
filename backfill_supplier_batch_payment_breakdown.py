# -*- coding: utf-8 -*-
"""
Bir martalik tuzatish: qarz to'lash paytida SupplierPurchaseBatch.cash_usd/click_usd/terminal_usd
yangilanmagani uchun (eski xato), guruhga bog'langan SupplierPayment yozuvlaridagi
naqd/click/terminal ulushlarini batchga qo'shib, so'ng mahsulot qatorlariga qayta taqsimlaydi.

Invariant: to'g'ri holatda har doim cash_usd + click_usd + terminal_usd == paid_amount bo'lishi kerak
(har bir to'langan dollar naqd/click/terminalning biriga tegishli bo'lishi kerak). Shu invariant
buzilgan (ya'ni farq bor) batchlar uchungina SupplierPayment yozuvlaridagi ulush qo'shiladi.
Bu yondashuv idempotent - allaqachon tuzatilgan batchlarda farq nolga teng bo'lgani uchun
qayta ishga tushirilsa ham hech narsa o'zgarmaydi / ikki marta qo'shilmaydi.

Ishlatish:
    python backfill_supplier_batch_payment_breakdown.py            # dry-run
    python backfill_supplier_batch_payment_breakdown.py --apply     # haqiqatan bazaga yozadi
"""
import sys
from decimal import Decimal

from app import app, db, _redistribute_batch_to_items
from models import SupplierPurchaseBatch, SupplierPayment


def run(apply_changes=False):
    with app.app_context():
        batches = SupplierPurchaseBatch.query.order_by(SupplierPurchaseBatch.id).all()
        if not batches:
            print("Supplier purchase batch topilmadi.")
            return

        changed = 0
        for batch in batches:
            paid = batch.paid_amount or Decimal('0')
            current_sum = (batch.cash_usd or Decimal('0')) + (batch.click_usd or Decimal('0')) + (batch.terminal_usd or Decimal('0'))
            missing = paid - current_sum
            if missing <= Decimal('0.005'):
                continue  # allaqachon to'g'ri (yoki manfiy - kutilmagan holat, tegilmaymiz)

            payments = SupplierPayment.query.filter_by(batch_id=batch.id).all()
            pay_cash = sum((p.cash_usd or Decimal('0')) for p in payments)
            pay_click = sum((p.click_usd or Decimal('0')) for p in payments)
            pay_terminal = sum((p.terminal_usd or Decimal('0')) for p in payments)

            changed += 1
            print(
                f"Batch #{batch.id}: paid={paid}, joriy_yigindi={current_sum}, "
                f"yetishmayotgan={missing} -> +cash={pay_cash} +click={pay_click} +terminal={pay_terminal}"
            )
            if apply_changes:
                batch.cash_usd = (batch.cash_usd or Decimal('0')) + pay_cash
                batch.click_usd = (batch.click_usd or Decimal('0')) + pay_click
                batch.terminal_usd = (batch.terminal_usd or Decimal('0')) + pay_terminal
                _redistribute_batch_to_items(batch)

        print(f"\nJami tekshirilgan batch: {len(batches)}, tuzatilishi kerak bo'lganlar: {changed}")
        if apply_changes:
            db.session.commit()
            print("Saqlandi.")
        else:
            print("Dry-run: hech narsa saqlanmadi. Haqiqatan yozish uchun --apply bilan ishga tushiring.")


if __name__ == '__main__':
    run(apply_changes='--apply' in sys.argv)
