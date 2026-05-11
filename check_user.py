from app import app, db, User, Store, Warehouse
with app.app_context():
    u = User.query.filter_by(username='sotuvchi').first()
    if u:
        print("allowed_locations:", u.allowed_locations)
    print("stores:", [(s.id, s.name) for s in Store.query.all()])
    print("warehouses:", [(w.id, w.name) for w in Warehouse.query.all()])
