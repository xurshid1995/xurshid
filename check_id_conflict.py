from app import app, db, Store, Warehouse

with app.app_context():
    print("\n🔍 Checking ID=1 in both tables:")
    
    store_1 = Store.query.filter_by(id=1).first()
    warehouse_1 = Warehouse.query.filter_by(id=1).first()
    
    print(f"\nStore with ID=1: {store_1}")
    if store_1:
        print(f"  Name: {store_1.name}")
    
    print(f"\nWarehouse with ID=1: {warehouse_1}")
    if warehouse_1:
        print(f"  Name: {warehouse_1.name}")
    
    print(f"\n{'='*60}")
    print("🔍 All Stores:")
    for s in Store.query.all():
        print(f"  ID={s.id}: {s.name}")
    
    print(f"\n🔍 All Warehouses:")
    for w in Warehouse.query.all():
        print(f"  ID={w.id}: {w.name}")
