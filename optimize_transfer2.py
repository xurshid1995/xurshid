"""
Faqat get_all_pending_transfers funksiyasini optimallashtirish:
- N+1 query -> 2 ta oldindan load + dict lookup
- joinedload user va dispatched_by uchun
- sotuvchi uchun user_id SQL filtri
"""

with open(r'd:\hisobot\Xurshid\app.py', 'r', encoding='utf-8') as f:
    content = f.read()

# Topish uchun marker — faqat shu blokni almashtirish
OLD = (
    "        # Admin va kassir barcha transferlarni ko'radi\n"
    "        if current_user.role in ('admin', 'kassir'):\n"
    "            pending_transfers = PendingTransfer.query.all()\n"
    "        elif current_user.role == 'omborchi':\n"
    "            # Omborchi faqat yuborilgan (sent/picking/dispatched) transferlarni ko'radi\n"
    "            # Draft holatdagi (sotuvchi hali yozmayotgan) transferlarni ko'rmaydi\n"
    "            all_pendings = PendingTransfer.query.filter(\n"
    "                PendingTransfer.status.in_(('sent', 'picking', 'dispatched'))\n"
    "            ).all()\n"
    "            pending_transfers = [\n"
    "                p for p in all_pendings\n"
    "                if user_can_manage_transfer(current_user, p)\n"
    "            ]\n"
    "        else:\n"
    "            # Sotuvchi: faqat o'zining va ruxsat berilgan transferlar (barcha statuslar)\n"
    "            all_pendings = PendingTransfer.query.all()\n"
    "            pending_transfers = [\n"
    "                p for p in all_pendings\n"
    "                if user_can_manage_transfer(current_user, p)\n"
    "            ]\n"
    "\n"
    "        result = []\n"
    "        for pending in pending_transfers:\n"
    "            # Joylashuv nomlarini olish\n"
    "            from_location_name = \"N/A\"\n"
    "            to_location_name = \"N/A\"\n"
    "\n"
    "            if pending.from_location_type == 'warehouse':\n"
    "                warehouse = Warehouse.query.get(pending.from_location_id)\n"
    "                from_location_name = warehouse.name if warehouse else f\"Ombor #{pending.from_location_id}\"\n"
    "            elif pending.from_location_type == 'store':\n"
    "                store = Store.query.get(pending.from_location_id)\n"
    "                from_location_name = store.name if store else f\"Dokon #{pending.from_location_id}\"\n"
    "\n"
    "            if pending.to_location_type == 'warehouse':\n"
    "                warehouse = Warehouse.query.get(pending.to_location_id)\n"
    "                to_location_name = warehouse.name if warehouse else f\"Ombor #{pending.to_location_id}\"\n"
    "            elif pending.to_location_type == 'store':\n"
    "                store = Store.query.get(pending.to_location_id)\n"
    "                to_location_name = store.name if store else f\"Dokon #{pending.to_location_id}\""
)

NEW = (
    "        # Barcha omborlar va do'konlarni 2 ta so'rovda oldindan yuklash (N+1 ni oldini olish)\n"
    "        warehouses_map = {w.id: w.name for w in Warehouse.query.with_entities(Warehouse.id, Warehouse.name).all()}\n"
    "        stores_map = {s.id: s.name for s in Store.query.with_entities(Store.id, Store.name).all()}\n"
    "\n"
    "        base_q = PendingTransfer.query.options(\n"
    "            db.joinedload(PendingTransfer.user),\n"
    "            db.joinedload(PendingTransfer.dispatched_by)\n"
    "        ).order_by(PendingTransfer.updated_at.desc())\n"
    "\n"
    "        # Admin va kassir barcha transferlarni ko'radi\n"
    "        if current_user.role in ('admin', 'kassir'):\n"
    "            pending_transfers = base_q.all()\n"
    "        elif current_user.role == 'omborchi':\n"
    "            # Omborchi faqat yuborilgan (sent/picking/dispatched) transferlarni ko'radi\n"
    "            all_pendings = base_q.filter(\n"
    "                PendingTransfer.status.in_(('sent', 'picking', 'dispatched'))\n"
    "            ).all()\n"
    "            pending_transfers = [p for p in all_pendings if user_can_manage_transfer(current_user, p)]\n"
    "        else:\n"
    "            # Sotuvchi: faqat o'zining transferlari (SQL darajasida filterlash)\n"
    "            all_pendings = base_q.filter(\n"
    "                PendingTransfer.user_id == current_user.id\n"
    "            ).all()\n"
    "            pending_transfers = [p for p in all_pendings if user_can_manage_transfer(current_user, p)]\n"
    "\n"
    "        result = []\n"
    "        for pending in pending_transfers:\n"
    "            # Dict lookup — qo'shimcha SQL so'rovi yo'q\n"
    "            if pending.from_location_type == 'warehouse':\n"
    "                from_location_name = warehouses_map.get(pending.from_location_id, f\"Ombor #{pending.from_location_id}\")\n"
    "            elif pending.from_location_type == 'store':\n"
    "                from_location_name = stores_map.get(pending.from_location_id, f\"Dokon #{pending.from_location_id}\")\n"
    "            else:\n"
    "                from_location_name = \"N/A\"\n"
    "\n"
    "            if pending.to_location_type == 'warehouse':\n"
    "                to_location_name = warehouses_map.get(pending.to_location_id, f\"Ombor #{pending.to_location_id}\")\n"
    "            elif pending.to_location_type == 'store':\n"
    "                to_location_name = stores_map.get(pending.to_location_id, f\"Dokon #{pending.to_location_id}\")\n"
    "            else:\n"
    "                to_location_name = \"N/A\""
)

if OLD in content:
    content = content.replace(OLD, NEW)
    print("OK: get_all_pending_transfers optimized")
else:
    print("WARN: target block not found!")
    # Debug: first 80 chars around the suspect area
    idx = content.find("        # Admin va kassir barcha transferlarni ko'radi")
    if idx >= 0:
        print("Found partial match at:", idx)
        print(repr(content[idx:idx+200]))
    raise SystemExit(1)

with open(r'd:\hisobot\Xurshid\app.py', 'w', encoding='utf-8') as f:
    f.write(content)

print("Done!")
