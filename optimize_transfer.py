import re

with open(r'd:\hisobot\Xurshid\app.py', 'r', encoding='utf-8') as f:
    content = f.read()

original_len = len(content)

# 1. user_can_manage_transfer ichidagi barcha print() larni olib tashlash
#    (faqat shu funksiya ichidagi, 4 ta bo'shliqli indentatsiya)
content = re.sub(r'    print\(f?"[^"]*"\)\n', '', content)
content = re.sub(r"    print\(f'[^']*'\)\n", '', content)

# 2. get_all_pending_transfers - N+1 query optimallashtirish
old_block = '''        # Admin va kassir barcha transferlarni ko'radi
        if current_user.role in ('admin', 'kassir'):
            pending_transfers = PendingTransfer.query.all()
        elif current_user.role == 'omborchi':
            # Omborchi faqat yuborilgan (sent/picking/dispatched) transferlarni ko'radi
            # Draft holatdagi (sotuvchi hali yozmayotgan) transferlarni ko'rmaydi
            all_pendings = PendingTransfer.query.filter(
                PendingTransfer.status.in_(('sent', 'picking', 'dispatched'))
            ).all()
            pending_transfers = [
                p for p in all_pendings
                if user_can_manage_transfer(current_user, p)
            ]
        else:
            # Sotuvchi: faqat o'zining va ruxsat berilgan transferlar (barcha statuslar)
            all_pendings = PendingTransfer.query.all()
            pending_transfers = [
                p for p in all_pendings
                if user_can_manage_transfer(current_user, p)
            ]

        result = []
        for pending in pending_transfers:
            # Joylashuv nomlarini olish
            from_location_name = "N/A"
            to_location_name = "N/A"

            if pending.from_location_type == 'warehouse':
                warehouse = Warehouse.query.get(pending.from_location_id)
                from_location_name = warehouse.name if warehouse else f"Ombor #{pending.from_location_id}"
            elif pending.from_location_type == 'store':
                store = Store.query.get(pending.from_location_id)
                from_location_name = store.name if store else f"Dokon #{pending.from_location_id}"

            if pending.to_location_type == 'warehouse':
                warehouse = Warehouse.query.get(pending.to_location_id)
                to_location_name = warehouse.name if warehouse else f"Ombor #{pending.to_location_id}"
            elif pending.to_location_type == 'store':
                store = Store.query.get(pending.to_location_id)
                to_location_name = store.name if store else f"Dokon #{pending.to_location_id}"'''

new_block = '''        # Barcha omborlar va do'konlarni 2 ta so'rovda oldindan yuklash (N+1 ni oldini olish)
        warehouses_map = {w.id: w.name for w in Warehouse.query.with_entities(Warehouse.id, Warehouse.name).all()}
        stores_map = {s.id: s.name for s in Store.query.with_entities(Store.id, Store.name).all()}

        base_q = PendingTransfer.query.options(
            db.joinedload(PendingTransfer.user),
            db.joinedload(PendingTransfer.dispatched_by)
        ).order_by(PendingTransfer.updated_at.desc())

        # Admin va kassir barcha transferlarni ko'radi
        if current_user.role in ('admin', 'kassir'):
            pending_transfers = base_q.all()
        elif current_user.role == 'omborchi':
            # Omborchi faqat yuborilgan (sent/picking/dispatched) transferlarni ko'radi
            all_pendings = base_q.filter(
                PendingTransfer.status.in_(('sent', 'picking', 'dispatched'))
            ).all()
            pending_transfers = [p for p in all_pendings if user_can_manage_transfer(current_user, p)]
        else:
            # Sotuvchi: faqat o'zining transferlari (SQL darajasida filterlash)
            all_pendings = base_q.filter(
                PendingTransfer.user_id == current_user.id
            ).all()
            pending_transfers = [p for p in all_pendings if user_can_manage_transfer(current_user, p)]

        result = []
        for pending in pending_transfers:
            # Dict lookup — qo'shimcha SQL so'rovi yo'q
            if pending.from_location_type == 'warehouse':
                from_location_name = warehouses_map.get(pending.from_location_id, f"Ombor #{pending.from_location_id}")
            elif pending.from_location_type == 'store':
                from_location_name = stores_map.get(pending.from_location_id, f"Dokon #{pending.from_location_id}")
            else:
                from_location_name = "N/A"

            if pending.to_location_type == 'warehouse':
                to_location_name = warehouses_map.get(pending.to_location_id, f"Ombor #{pending.to_location_id}")
            elif pending.to_location_type == 'store':
                to_location_name = stores_map.get(pending.to_location_id, f"Dokon #{pending.to_location_id}")
            else:
                to_location_name = "N/A"'''

if old_block in content:
    content = content.replace(old_block, new_block)
    print('OK: get_all_pending_transfers optimized')
else:
    print('WARN: old_block not found - manual check needed')

new_len = len(content)
print(f'File size: {original_len} -> {new_len} chars')

with open(r'd:\hisobot\Xurshid\app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print('Done!')
