from app import app, db, User, Store, Warehouse
with app.app_context():
    users = User.query.all()
    for u in users:
        print(f"ID:{u.id} username:{u.username} role:{u.role} allowed_locations:{u.allowed_locations}")
    print("stores:", [(s.id, s.name) for s in Store.query.all()])
    print("warehouses:", [(w.id, w.name) for w in Warehouse.query.all()])
