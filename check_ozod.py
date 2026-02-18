from app import app, db, Customer

with app.app_context():
    c = Customer.query.filter(Customer.name.ilike('%Ozod%')).first()
    if c:
        print(f'ID: {c.id}')
        print(f'Name repr: {repr(c.name)}')
        print(f'Name len: {len(c.name)}')
        for i, ch in enumerate(c.name):
            print(f'  char[{i}]: {repr(ch)} ord={ord(ch)}')
    else:
        print('Topilmadi')
