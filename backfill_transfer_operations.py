# -*- coding: utf-8 -*-
"""
Bir martalik migratsiya: `transfers` jadvalidagi yozuvlar uchun
OperationHistory'da mos yozuv bo'lmasa, uni qo'shib qo'yadi.

Bu faqat YANGI yozuv qo'shadi (INSERT), hech qanday mavjud
OperationHistory yoki Transfer yozuvini o'zgartirmaydi/o'chirmaydi.
Shuning uchun xavfsiz va bir necha marta ishga tushirilsa ham
takror yozuv qo'shmaydi (idempotent).

Ishlatish:
    python backfill_transfer_operations.py            # dry-run: nechta yozuv qo'shilishini ko'rsatadi
    python backfill_transfer_operations.py --apply     # haqiqatan bazaga yozadi
"""
import sys

from app import app, db
from models import Transfer, OperationHistory, Product, Store, Warehouse, User


def _location_name(loc_type, loc_id, stores_map, warehouses_map):
    if loc_type == 'store':
        return stores_map.get(loc_id, f"Do'kon #{loc_id}")
    if loc_type == 'warehouse':
        return warehouses_map.get(loc_id, f"Ombor #{loc_id}")
    return "Noma'lum"


def run(apply_changes=False):
    with app.app_context():
        transfers = Transfer.query.order_by(Transfer.id).all()
        if not transfers:
            print("Transfer yozuvlari topilmadi.")
            return

        # Allaqachon OperationHistory'da mavjud bo'lgan transfer.id lar
        existing_rows = OperationHistory.query.filter_by(
            table_name='transfers', operation_type='transfer'
        ).with_entities(OperationHistory.record_id).all()
        existing_ids = {r[0] for r in existing_rows if r[0] is not None}

        missing = [t for t in transfers if t.id not in existing_ids]
        print(f"Jami transfer: {len(transfers)}, OperationHistory'da yo'q: {len(missing)}")

        if not missing:
            print("Qo'shiladigan yozuv yo'q.")
            return

        # N+1 oldini olish uchun nom xaritalarini oldindan yuklash
        products_map = {p.id: p.name for p in Product.query.with_entities(Product.id, Product.name).all()}
        stores_map = {s.id: s.name for s in Store.query.with_entities(Store.id, Store.name).all()}
        warehouses_map = {w.id: w.name for w in Warehouse.query.with_entities(Warehouse.id, Warehouse.name).all()}
        users_map = {u.username: u.id for u in User.query.with_entities(User.id, User.username).all()}

        added = 0
        for t in missing:
            product_name = products_map.get(t.product_id, f"Mahsulot #{t.product_id}")
            from_name = _location_name(t.from_location_type, t.from_location_id, stores_map, warehouses_map)
            to_name = _location_name(t.to_location_type, t.to_location_id, stores_map, warehouses_map)
            qty = float(t.quantity) if t.quantity else 0

            desc = f"Transfer: {product_name} - {from_name} \u2192 {to_name} ({qty:.0f} ta)"

            op = OperationHistory(
                operation_type='transfer',
                table_name='transfers',
                record_id=t.id,
                user_id=users_map.get(t.user_name),
                username=t.user_name,
                description=desc,
                old_data={
                    'from_location': from_name,
                    'from_location_type': t.from_location_type,
                },
                new_data={
                    'product_id': t.product_id,
                    'product_name': product_name,
                    'quantity': qty,
                    'to_location': to_name,
                    'to_location_type': t.to_location_type,
                },
                ip_address=None,
                location_id=t.to_location_id,
                location_type=t.to_location_type,
                location_name=to_name,
                amount=None,
                created_at=t.created_at,
            )
            db.session.add(op)
            added += 1

        if apply_changes:
            db.session.commit()
            print(f"✅ {added} ta OperationHistory yozuvi qo'shildi.")
        else:
            db.session.rollback()
            print(f"(dry-run) {added} ta yozuv qo'shilgan bo'lardi. Haqiqatan yozish uchun --apply bilan ishga tushiring.")


if __name__ == '__main__':
    run(apply_changes='--apply' in sys.argv)
