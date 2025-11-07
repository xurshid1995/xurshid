"""
Migration script: Convert allowed_locations and transfer_locations 
from old format [1, 2] to new format [{'id': 1, 'type': 'store'}, ...]
"""
from app import app, db, User, Store, Warehouse

def migrate_locations():
    with app.app_context():
        users = User.query.all()
        
        for user in users:
            print(f"\n{'='*60}")
            print(f"👤 Migrating user: {user.username} (ID: {user.id})")
            
            # Migrate allowed_locations
            if user.allowed_locations and isinstance(user.allowed_locations[0], int):
                print(f"📍 Old allowed_locations: {user.allowed_locations}")
                
                new_allowed_locations = []
                for loc_id in user.allowed_locations:
                    # Check if it's a store
                    store = Store.query.filter_by(id=loc_id).first()
                    if store:
                        new_allowed_locations.append({'id': loc_id, 'type': 'store'})
                        print(f"  ✅ Added store: ID={loc_id}, Name={store.name}")
                    
                    # Check if it's a warehouse
                    warehouse = Warehouse.query.filter_by(id=loc_id).first()
                    if warehouse:
                        new_allowed_locations.append({'id': loc_id, 'type': 'warehouse'})
                        print(f"  ✅ Added warehouse: ID={loc_id}, Name={warehouse.name}")
                
                user.allowed_locations = new_allowed_locations
                print(f"📍 New allowed_locations: {new_allowed_locations}")
            
            # Migrate transfer_locations
            if user.transfer_locations and isinstance(user.transfer_locations[0], int):
                print(f"🔄 Old transfer_locations: {user.transfer_locations}")
                
                new_transfer_locations = []
                for loc_id in user.transfer_locations:
                    # Check if it's a store
                    store = Store.query.filter_by(id=loc_id).first()
                    if store:
                        new_transfer_locations.append({'id': loc_id, 'type': 'store'})
                        print(f"  ✅ Added store: ID={loc_id}, Name={store.name}")
                    
                    # Check if it's a warehouse
                    warehouse = Warehouse.query.filter_by(id=loc_id).first()
                    if warehouse:
                        new_transfer_locations.append({'id': loc_id, 'type': 'warehouse'})
                        print(f"  ✅ Added warehouse: ID={loc_id}, Name={warehouse.name}")
                
                user.transfer_locations = new_transfer_locations
                print(f"🔄 New transfer_locations: {new_transfer_locations}")
        
        # Commit changes
        try:
            db.session.commit()
            print(f"\n{'='*60}")
            print("✅ Migration completed successfully!")
            print(f"{'='*60}")
        except Exception as e:
            db.session.rollback()
            print(f"\n❌ Migration failed: {str(e)}")

if __name__ == '__main__':
    migrate_locations()
