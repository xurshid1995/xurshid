from app import app, db, Product, WarehouseStock, Warehouse

with app.app_context():
    # Y7D mahsulotini qidirish
    products = Product.query.filter(Product.name.ilike('%Y7D%')).all()
    
    print('\n=== TOPILGAN MAHSULOTLAR ===')
    for p in products:
        print(f'ID: {p.id}, Name: {p.name}, Cost: ${p.cost_price}, Sell: ${p.sell_price}')
    
    print('\n=== WAREHOUSE STOCKS ===')
    if products:
        for product in products:
            stocks = WarehouseStock.query.filter_by(product_id=product.id).all()
            if stocks:
                for stock in stocks:
                    warehouse = Warehouse.query.get(stock.warehouse_id)
                    print(f'Product: {product.name}, Warehouse: {warehouse.name}, Qty: {stock.quantity}')
            else:
                print(f'Product: {product.name} - STOCK YO\'Q!')
    else:
        print('Hech qanday mahsulot topilmadi!')
