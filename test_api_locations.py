from app import app, db, User, Store, Warehouse

# Test API locations logic
with app.app_context():
    # Sotuvchi user'ni olish
    user = User.query.filter_by(username='sotuvchi').first()
    
    if not user:
        print("❌ Sotuvchi user topilmadi!")
    else:
        print(f"\n{'='*60}")
        print(f"👤 User: {user.username}")
        print(f"🎭 Role: {user.role}")
        print(f"📍 Allowed locations: {user.allowed_locations}")
        print(f"🔄 Transfer locations: {user.transfer_locations}")
        print(f"✅ Permissions: {user.permissions}")
        
        # Extract location IDs logic test
        from app import extract_location_ids
        
        allowed_locations = user.allowed_locations or []
        print(f"\n{'='*60}")
        print(f"🧪 Testing extract_location_ids:")
        print(f"Input: {allowed_locations}")
        
        store_ids = extract_location_ids(allowed_locations, 'store')
        warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')
        
        print(f"Store IDs: {store_ids}")
        print(f"Warehouse IDs: {warehouse_ids}")
        
        # Ma'lumotlar bazasidan to'g'ri ID'larni olish
        print(f"\n{'='*60}")
        print(f"📊 Ma'lumotlar bazasidan:")
        
        all_stores = Store.query.all()
        all_warehouses = Warehouse.query.all()
        
        print(f"\nBarcha do'konlar:")
        for s in all_stores:
            print(f"  - ID: {s.id}, Name: {s.name}")
            
        print(f"\nBarcha omborlar:")
        for w in all_warehouses:
            print(f"  - ID: {w.id}, Name: {w.name}")
        
        # Filter qilingan
        if store_ids:
            filtered_stores = Store.query.filter(Store.id.in_(store_ids)).all()
            print(f"\n✅ Sotuvchiga ruxsat etilgan do'konlar:")
            for s in filtered_stores:
                print(f"  - ID: {s.id}, Name: {s.name}")
        else:
            print(f"\n❌ Sotuvchiga hech qanday do'kon ruxsat etilmagan!")
            
        if warehouse_ids:
            filtered_warehouses = Warehouse.query.filter(Warehouse.id.in_(warehouse_ids)).all()
            print(f"\n✅ Sotuvchiga ruxsat etilgan omborlar:")
            for w in filtered_warehouses:
                print(f"  - ID: {w.id}, Name: {w.name}")
        else:
            print(f"\n❌ Sotuvchiga hech qanday ombor ruxsat etilmagan!")
