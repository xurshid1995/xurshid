# -*- coding: utf-8 -*-
"""Ma'lumotlar bazasi modellari (app.py dan ajratilgan).

Barcha SQLAlchemy modellari shu yerda. `db` obyekti va umumiy helperlar
database.py modulidan import qilinadi (aylanma importni oldini olish uchun).
"""
import logging
from datetime import datetime

from database import (
    db,
    get_tashkent_time,
    DEFAULT_PHONE_PLACEHOLDER,
    _get_location_name_cached,
)

logger = logging.getLogger(__name__)


# Model yaratish - Kategoriya jadvali
class Category(db.Model):
    __tablename__ = 'categories'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False, unique=True)
    color = db.Column(db.String(7), default='#6366f1')
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())

    products = db.relationship('Product', backref='category', lazy=True)

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'color': self.color,
        }


# Model yaratish - Mahsulot jadvali
class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    barcode = db.Column(db.String(255), unique=True, nullable=True, index=True)  # Barcode raqami
    cost_price = db.Column(db.DECIMAL(precision=10, scale=5),
                           nullable=False)  # Ortacha tan narxi
    sell_price = db.Column(db.DECIMAL(precision=10, scale=5),
                           nullable=False)  # Sotish narxi
    min_stock = db.Column(db.Integer, default=0,
                          nullable=False)  # Minimal qoldiq
    unit_type = db.Column(db.String(10), default='dona', nullable=False)  # O'lchov birligi: 'dona' yoki 'litr'
    last_batch_cost = db.Column(db.DECIMAL(precision=10, scale=4))  # Oxirgi partiya tan narxi
    last_batch_date = db.Column(db.DateTime)  # Oxirgi partiya sanasi
    created_at = db.Column(db.DateTime,
                           default=lambda: get_tashkent_time())  # Qo'shilgan sana
    is_checked = db.Column(
        db.Boolean,
        default=False,
        nullable=False)  # Tekshirilganlik holati
    image_path = db.Column(db.String(255), nullable=True)  # Mahsulot rasmi
    category_id = db.Column(db.Integer, db.ForeignKey('categories.id', ondelete='SET NULL'), nullable=True)  # Kategoriya

    # Relationships
    warehouse_stocks = db.relationship('WarehouseStock',
                                       cascade='all, delete-orphan')
    store_stocks = db.relationship('StoreStock', cascade='all, delete-orphan')

    # Eski price ustunini compatibility uchun property sifatida qoldiraman
    @property
    def price(self):
        return self.sell_price

    @price.setter
    def price(self, value):
        self.sell_price = value

    def __repr__(self):
        return f'<Product {self.name}: {self.price}>'

    def to_dict(self):
        # Stock ma'lumotlarini olish - warehouse_stocks va store_stocks dan
        stocks = []
        if self.warehouse_stocks:
            stocks.extend([stock.to_dict() for stock in self.warehouse_stocks])
        if self.store_stocks:
            stocks.extend([stock.to_dict() for stock in self.store_stocks])

        return {
            'id': self.id,
            'name': self.name,
            'barcode': self.barcode,  # Barcode qo'shildi
            'cost_price': str(self.cost_price),  # Decimal precision saqlanadi
            'sell_price': str(self.sell_price),  # Decimal precision saqlanadi
            'price': str(self.sell_price),  # Compatibility uchun
            'min_stock': self.min_stock,
            'unit_type': self.unit_type,  # O'lchov birligi
            'last_batch_cost': str(self.last_batch_cost) if self.last_batch_cost else None,
            'last_batch_date': self.last_batch_date.isoformat() if self.last_batch_date else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'image_url': f'/static/uploads/products/{self.image_path}' if self.image_path else None,
            'category_id': self.category_id,
            'category_name': self.category.name if self.category else None,
            'category_color': self.category.color if self.category else None,
            'stocks': stocks
        }


# Model yaratish - Buyurtma jadvali
# ESLATMA: Order modeli eskirgan va ishlatilmaydi.
# Jadval ma'lumotlar bazasida saqlanmoqda (migratsiya uchun), lekin kod tomonidan ishlatilmaydi.
class Order(db.Model):
    __tablename__ = 'orders'

    id = db.Column(db.Integer, primary_key=True)
    customer_name = db.Column(db.String(100), nullable=False)
    product_name = db.Column(db.String(200), nullable=True)  # Mahsulot nomi
    quantity = db.Column(db.DECIMAL(precision=10, scale=2), default=1, nullable=False)  # Miqdori
    cost_price = db.Column(db.DECIMAL(precision=10, scale=2),
                           nullable=True)  # Tan narxi
    sell_price = db.Column(db.DECIMAL(precision=10, scale=2),
                           nullable=True)  # Sotish narxi
    total_cost_price = db.Column(db.DECIMAL(precision=12, scale=2),
                                 nullable=True)  # Jami tan narx
    total_sell_price = db.Column(db.DECIMAL(precision=12, scale=2),
                                 nullable=True)  # Jami sotish narx
    total_amount = db.Column(db.DECIMAL(precision=12, scale=2), nullable=False)
    order_date = db.Column(db.DateTime, default=db.func.current_timestamp())

    def __repr__(self):
        return f'<Order {self.id}: {self.total_amount}>'


# Model yaratish - Omborlar jadvali
class Warehouse(db.Model):
    __tablename__ = 'warehouses'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    manager_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    current_stock = db.Column(db.Integer, default=0)  # Joriy zaxira
    created_date = db.Column(db.DateTime, default=db.func.current_timestamp())

    def __repr__(self):
        return f'<Warehouse {self.name}: {self.current_stock}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'manager_name': self.manager_name,
            'phone': self.phone,
            'current_stock': self.current_stock,
            'created_date': self.created_date.strftime('%Y-%m-%d')
        }


# Model yaratish - Do'konlar jadvali
class Store(db.Model):
    __tablename__ = 'stores'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    address = db.Column(db.String(200), nullable=False)
    manager_name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    current_stock = db.Column(db.Integer, default=0)  # Joriy zaxira
    created_date = db.Column(db.DateTime, default=db.func.current_timestamp())

    def __repr__(self):
        return f'<Store {self.name}: {self.current_stock}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'address': self.address,
            'manager_name': self.manager_name,
            'phone': self.phone,
            'current_stock': self.current_stock,
            'created_date': self.created_date.strftime('%Y-%m-%d')
        }


# Model yaratish - Ombor mahsulotlari jadvali
class WarehouseStock(db.Model):
    __tablename__ = 'warehouse_stocks'

    id = db.Column(db.Integer, primary_key=True)
    warehouse_id = db.Column(
        db.Integer,
        db.ForeignKey('warehouses.id'),
        nullable=False)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.id'),
        nullable=False)
    quantity = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False, default=0)
    min_stock = db.Column(db.Integer, default=10)  # Minimal zaxira
    last_updated = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    warehouse = db.relationship('Warehouse', backref='stocks')
    product = db.relationship('Product', overlaps="warehouse_stocks")

    def __repr__(self):
        return f'<Stock W:{self.warehouse_id} P:{self.product_id} Q:{self.quantity}>'

    @property
    def purchase_price(self):
        """Sotib olish narxi - product ning cost_price i bilan bir xil"""
        return self.product.cost_price if self.product else 0

    def to_dict(self):
        return {
            'id': self.id,
            'warehouse_id': self.warehouse_id,
            'warehouse_name': self.warehouse.name if self.warehouse else 'Noma\'lum',
            'product_id': self.product_id,
            'quantity': float(self.quantity) if self.quantity else 0,
            'min_stock': self.min_stock,
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M:%S')}


# Model yaratish - Do'kon mahsulotlari jadvali
class StoreStock(db.Model):
    __tablename__ = 'store_stocks'

    id = db.Column(db.Integer, primary_key=True)
    store_id = db.Column(
        db.Integer,
        db.ForeignKey('stores.id'),
        nullable=False)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.id'),
        nullable=False)
    quantity = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False, default=0)
    min_stock = db.Column(db.Integer, default=10)  # Minimal zaxira
    last_updated = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    store = db.relationship('Store', backref='stocks')
    product = db.relationship('Product', overlaps="store_stocks")

    def __repr__(self):
        return f'<StoreStock S:{self.store_id} P:{self.product_id} Q:{self.quantity}>'

    @property
    def purchase_price(self):
        """Sotib olish narxi - product ning cost_price i bilan bir xil"""
        return self.product.cost_price if self.product else 0

    def to_dict(self):
        return {
            'id': self.id,
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else 'Noma\'lum',
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else 'Noma\'lum',
            'quantity': float(self.quantity) if self.quantity else 0,
            'min_stock': self.min_stock,
            'status': 'low' if (self.min_stock > 0 and self.quantity <= self.min_stock) else 'normal',
            'last_updated': self.last_updated.strftime('%Y-%m-%d %H:%M')
        }


# Transfer tarixi modeli
class Transfer(db.Model):
    __tablename__ = 'transfers'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.id', ondelete='CASCADE'),
        nullable=False)
    from_location_type = db.Column(db.String(20),
                                   nullable=False)  # 'store' yoki 'warehouse'
    from_location_id = db.Column(db.Integer, nullable=False)
    to_location_type = db.Column(db.String(20),
                                 nullable=False)  # 'store' yoki 'warehouse'
    to_location_id = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False)
    user_name = db.Column(db.String(100), default='Admin')
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    product = db.relationship('Product', backref='transfers')

    def __repr__(self):
        return f'<Transfer {self.id}: {self.product.name if self.product else "N/A"} {self.quantity}>'

    @property
    def from_location_name(self):
        """Qayerdan joylashuv nomi (keshdan olish - N+1 oldini oladi)"""
        if self.from_location_type and self.from_location_id:
            return _get_location_name_cached(self.from_location_type, self.from_location_id)
        return "Noma'lum"

    @property
    def to_location_name(self):
        """Qayerga joylashuv nomi (keshdan olish - N+1 oldini oladi)"""
        if self.to_location_type and self.to_location_id:
            return _get_location_name_cached(self.to_location_type, self.to_location_id)
        return "Noma'lum"

    def to_dict(self):
        return {
            'id': self.id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else 'Noma\'lum mahsulot',
            'from_location_type': self.from_location_type,
            'from_location_id': self.from_location_id,
            'from_location_name': self.from_location_name,
            'to_location_type': self.to_location_type,
            'to_location_id': self.to_location_id,
            'to_location_name': self.to_location_name,
            'quantity': float(self.quantity) if self.quantity else 0,
            'user_name': self.user_name,
            'created_at': self.created_at.isoformat()}


# Tasdiqlanmagan (pending) transferlar modeli
class PendingTransfer(db.Model):
    __tablename__ = 'pending_transfers'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    from_location_type = db.Column(db.String(20), nullable=False)  # 'store' yoki 'warehouse'
    from_location_id = db.Column(db.Integer, nullable=False)
    to_location_type = db.Column(db.String(20), nullable=False)  # 'store' yoki 'warehouse'
    to_location_id = db.Column(db.Integer, nullable=False)
    items = db.Column(db.JSON, nullable=False)  # [{product_id, name, price, quantity, available}, ...]
    status = db.Column(db.String(20), default='draft')  # draft | sent | picking | dispatched | completed
    sent_at = db.Column(db.DateTime, nullable=True)
    dispatched_at = db.Column(db.DateTime, nullable=True)
    dispatched_by_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    receiver_confirmed_at = db.Column(db.DateTime, nullable=True)
    # Sotuvchi qisman qabul qilganda: [{product_id, received_qty}, ...]
    received_items = db.Column(db.JSON, nullable=True)
    received_at = db.Column(db.DateTime, nullable=True)
    received_note = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())

    # Relationship
    user = db.relationship('User', backref='pending_transfers', foreign_keys=[user_id])
    dispatched_by = db.relationship('User', foreign_keys=[dispatched_by_id])

    def __repr__(self):
        return f'<PendingTransfer {self.id}: User {self.user_id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': self.user.username if self.user else 'N/A',
            'from_location_type': self.from_location_type,
            'from_location_id': self.from_location_id,
            'to_location_type': self.to_location_type,
            'to_location_id': self.to_location_id,
            'items': self.items,
            'status': self.status or 'draft',
            'sent_at': self.sent_at.isoformat() if self.sent_at else None,
            'dispatched_at': self.dispatched_at.isoformat() if self.dispatched_at else None,
            'dispatched_by_name': self.dispatched_by.username if self.dispatched_by else None,
            'receiver_confirmed_at': self.receiver_confirmed_at.isoformat() if self.receiver_confirmed_at else None,
            'received_items': self.received_items,
            'received_at': self.received_at.isoformat() if self.received_at else None,
            'received_note': self.received_note,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# Tugallanmagan mahsulot qo'shish sessiyalari modeli
class PendingProductBatch(db.Model):
    __tablename__ = 'pending_product_batches'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    items = db.Column(db.JSON, nullable=False, default=list)  # tempProducts array
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())

    user = db.relationship('User', backref='pending_product_batches')

    def __repr__(self):
        return f'<PendingProductBatch {self.id}: User {self.user_id}, {len(self.items) if self.items else 0} items>'

    def to_dict(self):
        items = self.items or []
        # Birinchi mahsulotning joylashuv nomini olish (preview uchun)
        first_location = items[0].get('location_name', '—') if items else '—'
        return {
            'id': self.id,
            'user_id': self.user_id,
            'user_name': f"{self.user.first_name} {self.user.last_name}".strip() if self.user else 'N/A',
            'items': items,
            'items_count': len(items),
            'first_location': first_location,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# Mijozlar modeli
class Customer(db.Model):
    __tablename__ = 'customers'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    phone = db.Column(db.String(20))
    email = db.Column(db.String(120))
    address = db.Column(db.Text)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id'), nullable=True)
    telegram_chat_id = db.Column(db.BigInteger, nullable=True)
    last_debt_payment_usd = db.Column(db.Numeric(10, 2), default=0)
    last_debt_payment_uzs = db.Column(db.Numeric(15, 2), default=0)  # UZS da aniq to'lov summasi
    last_debt_payment_date = db.Column(db.DateTime, nullable=True)
    last_debt_payment_rate = db.Column(db.Numeric(10, 2), default=13000)
    balance = db.Column(db.DECIMAL(precision=15, scale=4), nullable=False, default=0)  # Mijoz balansi (ortiqcha to'lov)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp())

    # Relationship
    store = db.relationship('Store', backref='customers')

    def __repr__(self):
        return f'<Customer {self.id}: {self.name}>'

    def to_dict(self):
        try:
            store_name = 'Umumiy'
            if self.store_id and self.store:
                store_name = self.store.name
            elif self.store_id:
                # Agar store_id bor lekin relationship yuklanmagan bo'lsa
                store = Store.query.get(self.store_id)
                store_name = store.name if store else 'Umumiy'
        except Exception as e:
            store_name = 'Umumiy'
            logger.error(f"Error getting store name for customer {self.id}: {str(e)}")

        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'email': self.email,
            'address': self.address,
            'store_id': self.store_id,
            'store_name': store_name,
            'telegram_chat_id': self.telegram_chat_id,
            'balance': float(self.balance or 0),
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None}


# Qarz to'lovlari tarixi modeli
class DebtPayment(db.Model):
    __tablename__ = 'debt_payments'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='SET NULL'), nullable=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id', ondelete='SET NULL'), nullable=True)
    payment_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    cash_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    click_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    terminal_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    total_usd = db.Column(db.DECIMAL(precision=15, scale=10), nullable=False)
    currency_rate = db.Column(db.DECIMAL(precision=15, scale=4), nullable=True)
    received_by = db.Column(db.String(100), nullable=False)
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())

    # Relationships
    customer = db.relationship('Customer', backref='debt_payments')
    sale = db.relationship('Sale', backref='debt_payments')

    def __repr__(self):
        return f'<DebtPayment {self.id}: {self.customer_id} - {self.total_usd} USD>'

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'customer_name': self.customer.name if self.customer else 'Unknown',
            'sale_id': self.sale_id,
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M') if self.payment_date else None,
            'cash_usd': float(self.cash_usd or 0),
            'click_usd': float(self.click_usd or 0),
            'terminal_usd': float(self.terminal_usd or 0),
            'total_usd': float(self.total_usd or 0),
            'currency_rate': float(self.currency_rate) if self.currency_rate else 0,
            'received_by': self.received_by,
            'notes': self.notes
        }


# Qarz eslatmalari modeli
class DebtReminder(db.Model):
    __tablename__ = 'debt_reminders'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='CASCADE'), nullable=False)
    reminder_date = db.Column(db.Date, nullable=False)
    reminder_time = db.Column(db.Time, nullable=False, default=datetime.strptime('10:00', '%H:%M').time())
    message = db.Column(db.Text, nullable=True)
    is_sent = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)
    created_by = db.Column(db.String(100), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    sent_at = db.Column(db.DateTime, nullable=True)

    # Relationship
    customer = db.relationship('Customer', backref='debt_reminders')

    def __repr__(self):
        return f'<DebtReminder {self.id}: customer={self.customer_id} date={self.reminder_date} time={self.reminder_time}>'

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'customer_name': self.customer.name if self.customer else 'Noma\'lum',
            'reminder_date': self.reminder_date.strftime('%Y-%m-%d') if self.reminder_date else None,
            'reminder_time': self.reminder_time.strftime('%H:%M') if self.reminder_time else '10:00',
            'message': self.message,
            'is_sent': self.is_sent,
            'is_active': self.is_active,
            'created_by': self.created_by,
            'created_at': self.created_at.strftime('%Y-%m-%d %H:%M') if self.created_at else None,
            'sent_at': self.sent_at.strftime('%Y-%m-%d %H:%M') if self.sent_at else None
        }


# Mijoz amallar tarixi snapshot modeli (timeline uchun immutable yozuvlar)
class CustomerTimelineSnapshot(db.Model):
    __tablename__ = 'customer_timeline_snapshot'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, nullable=False, index=True)
    event_type = db.Column(db.String(20), nullable=False)   # 'sale', 'payment', 'return'
    event_id = db.Column(db.Integer, nullable=False)         # sale.id yoki debt_payment.id
    event_date = db.Column(db.DateTime, nullable=False)
    snapshot_data = db.Column(db.JSON, nullable=False, default=dict)
    debt_before = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    debt_after = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    balance_before = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    balance_after = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())

    def to_dict(self):
        return {
            'id': self.id,
            'customer_id': self.customer_id,
            'event_type': self.event_type,
            'event_id': self.event_id,
            'event_date': self.event_date.strftime('%Y-%m-%d %H:%M') if self.event_date else None,
            'snapshot_data': self.snapshot_data,
            'debt_before': float(self.debt_before or 0),
            'debt_after': float(self.debt_after or 0),
            'balance_before': float(self.balance_before or 0),
            'balance_after': float(self.balance_after or 0),
        }


# Foydalanuvchilar modeli
class User(db.Model):
    __tablename__ = 'users'

    id = db.Column(db.Integer, primary_key=True)
    first_name = db.Column(db.String(60), nullable=False)
    last_name = db.Column(db.String(60), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    username = db.Column(db.String(80), unique=True, nullable=False)
    password = db.Column(db.String(255), nullable=False)
    phone = db.Column(db.String(20))
    telegram_chat_id = db.Column(db.BigInteger, nullable=True)  # Parol tiklash uchun Telegram chat ID
    reset_code = db.Column(db.String(6), nullable=True)
    reset_code_expires_at = db.Column(db.DateTime, nullable=True)
    reset_token = db.Column(db.String(64), nullable=True)           # Parol tiklash tokeni (DB da)
    reset_token_expires_at = db.Column(db.DateTime, nullable=True)  # Token muddati
    # admin, sotuvchi, kassir, omborchi, ombor_xodimi
    role = db.Column(db.String(50), nullable=False, default='sotuvchi')
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id'), nullable=True)
    permissions = db.Column(db.JSON, default=lambda: {})  # Ruxsatlar (JSON)
    # Ruxsat etilgan joylashuvlar
    allowed_locations = db.Column(db.JSON, default=lambda: [])
    # Transfer qilish uchun ruxsat etilgan joylashuvlar
    transfer_locations = db.Column(db.JSON, default=lambda: [])
    # Qoldiqni tekshirish uchun ruxsat etilgan joylashuvlar
    stock_check_locations = db.Column(db.JSON, default=lambda: [])
    is_active = db.Column(db.Boolean, default=True)
    photo = db.Column(db.String(255), nullable=True)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp())

    # Relationship
    store = db.relationship('Store', backref='users')

    def __repr__(self):
        return f'<User {self.id}: {self.username} ({self.role})>'

    def to_dict(self):
        return {
            'id': self.id,
            'first_name': self.first_name,
            'last_name': self.last_name,
            'full_name': f"{self.first_name} {self.last_name}",
            'email': self.email,
            'username': self.username,
            'phone': self.phone,
            'role': self.role,
            'role_display': self.get_role_display(),
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else 'Barcha do\'konlar',
            'permissions': self.permissions or {},
            'allowed_locations': self.allowed_locations or [],
            'transfer_locations': self.transfer_locations or [],
            'stock_check_locations': self.stock_check_locations or [],
            'is_active': self.is_active,
            'photo': f'/static/uploads/users/{self.photo}' if self.photo else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def get_role_display(self):
        role_names = {
            'admin': 'Admin',
            'sotuvchi': 'Sotuvchi',
            'kassir': 'Kassir',
            'omborchi': 'Omborchi',
            'ombor_xodimi': 'Ombor xodimi',
            'manager': 'Menejer'
        }
        return role_names.get(self.role, self.role)


# API Operations modeli - Idempotency uchun
class ApiOperation(db.Model):
    __tablename__ = 'api_operations'

    id = db.Column(db.Integer, primary_key=True)
    idempotency_key = db.Column(db.String(100), unique=True, nullable=False, index=True)
    operation_type = db.Column(db.String(50), nullable=False)  # 'transfer', 'sale', 'payment'
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'))
    status = db.Column(db.String(20), default='completed')  # 'completed', 'failed'
    result_data = db.Column(db.Text)  # JSON natija
    created_at = db.Column(db.DateTime, default=get_tashkent_time)

    def __repr__(self):
        return f'<ApiOperation {self.operation_type} - {self.idempotency_key}>'


class OperationHistory(db.Model):
    """Barcha tizim amaliyotlarini saqlash uchun audit log"""
    __tablename__ = 'operations_history'

    id = db.Column(db.Integer, primary_key=True)
    operation_type = db.Column(db.String(50), nullable=False, index=True)  # 'sale', 'add_product', 'transfer', 'return', 'edit', 'delete', 'payment'
    table_name = db.Column(db.String(50))  # Ta'sirlangan jadval
    record_id = db.Column(db.Integer)  # Ta'sirlangan record ID
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='SET NULL'), nullable=True)
    username = db.Column(db.String(100))  # Foydalanuvchi nomi (cache)
    description = db.Column(db.Text)  # Amaliyot tavsifi
    old_data = db.Column(db.JSON)  # Eski ma'lumotlar (edit/delete uchun)
    new_data = db.Column(db.JSON)  # Yangi ma'lumotlar
    ip_address = db.Column(db.String(50))  # Foydalanuvchi IP
    location_id = db.Column(db.Integer)  # Joylashuv ID
    location_type = db.Column(db.String(20))  # 'store' yoki 'warehouse'
    location_name = db.Column(db.String(200))  # Joylashuv nomi (cache)
    amount = db.Column(db.Numeric(15, 2))  # Summa (agar mavjud bo'lsa)
    created_at = db.Column(db.DateTime, default=get_tashkent_time, index=True)

    def __repr__(self):
        return f'<OperationHistory {self.operation_type} by {self.username}>'


# Foydalanuvchi session'lari modeli
class UserSession(db.Model):
    __tablename__ = 'user_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=False)
    session_id = db.Column(db.String(255), unique=True, nullable=False, index=True)
    login_time = db.Column(db.DateTime, default=get_tashkent_time)
    last_activity = db.Column(db.DateTime, default=get_tashkent_time)
    ip_address = db.Column(db.String(45))
    user_agent = db.Column(db.Text)
    is_active = db.Column(db.Boolean, default=True, nullable=False, index=True)
    created_at = db.Column(db.DateTime, default=get_tashkent_time)

    # Relationships
    user = db.relationship('User', backref=db.backref('sessions', cascade='all, delete-orphan', lazy='dynamic'))

    def __repr__(self):
        return f'<UserSession {self.id}: User {self.user_id} - {self.session_id[:8]}...>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'username': self.user.username if self.user else None,
            'full_name': f"{self.user.first_name} {self.user.last_name}" if self.user else None,
            'session_id': self.session_id,
            'login_time': self.login_time.isoformat() if self.login_time else None,
            'last_activity': self.last_activity.isoformat() if self.last_activity else None,
            'ip_address': self.ip_address,
            'user_agent': self.user_agent,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None
        }


# Sozlamalar modeli
class Settings(db.Model):
    __tablename__ = 'settings'

    id = db.Column(db.Integer, primary_key=True)
    key = db.Column(db.String(100), unique=True, nullable=False)
    value = db.Column(db.Text)
    description = db.Column(db.String(255))
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp())

    def __repr__(self):
        return f'<Settings {self.key}: {self.value}>'


# Stock checking session holatini saqlash modeli
class StockCheckSession(db.Model):
    __tablename__ = 'stock_check_sessions'

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=False)
    completed_by_user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)  # Tekshiruvni tugatgan foydalanuvchi
    location_id = db.Column(db.Integer, nullable=False)
    location_type = db.Column(db.String(20), nullable=False)  # 'store' or 'warehouse'
    location_name = db.Column(db.String(200))
    started_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(
        db.DateTime,
        default=db.func.current_timestamp(),
        onupdate=db.func.current_timestamp())
    completed_at = db.Column(db.DateTime, nullable=True)  # Tekshiruv tugatilgan vaqt
    status = db.Column(db.String(20), default='active')  # 'active', 'completed', 'cancelled'

    # Relationships
    user = db.relationship('User', foreign_keys=[user_id], backref=db.backref('stock_check_sessions_started', cascade='all, delete-orphan', lazy='dynamic'))
    completed_by = db.relationship('User', foreign_keys=[completed_by_user_id], backref=db.backref('stock_check_sessions_completed', cascade='all, delete-orphan', lazy='dynamic'))

    def __repr__(self):
        return f'<StockCheckSession {self.location_type}-{self.location_id} - User: {self.user_id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'user_id': self.user_id,
            'location_id': self.location_id,
            'location_type': self.location_type,
            'location_name': self.location_name,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'status': self.status,
        }


# Tekshirilgan mahsulotlar modeli
class StockCheckItem(db.Model):
    __tablename__ = 'stock_check_items'

    id = db.Column(db.Integer, primary_key=True)
    session_id = db.Column(db.Integer, db.ForeignKey('stock_check_sessions.id'), nullable=False)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    product_name = db.Column(db.String(200))  # Snapshot
    system_quantity = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False)
    actual_quantity = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False)
    difference = db.Column(db.DECIMAL(precision=10, scale=2))
    status = db.Column(db.String(20))  # 'normal', 'kamomad', 'ortiqcha'
    checked_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    session = db.relationship('StockCheckSession', backref='items')
    product = db.relationship('Product')

    def __repr__(self):
        return f'<StockCheckItem session={self.session_id} product={self.product_id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'session_id': self.session_id,
            'product_id': self.product_id,
            'product_name': self.product_name,
            'system_quantity': float(self.system_quantity) if self.system_quantity else 0,
            'actual_quantity': float(self.actual_quantity) if self.actual_quantity else 0,
            'difference': float(self.difference) if self.difference else 0,
            'status': self.status,
            'checked_at': self.checked_at.isoformat() if self.checked_at else None
        }


# Sotish tarixi modeli
class SaleItem(db.Model):
    __tablename__ = 'sale_items'

    id = db.Column(db.Integer, primary_key=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id'), nullable=False)
    product_id = db.Column(
        db.Integer,
        db.ForeignKey('products.id'),
        nullable=False)
    quantity = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False)
    unit_price = db.Column(db.DECIMAL(precision=15, scale=10), nullable=False)
    total_price = db.Column(db.DECIMAL(precision=18, scale=10), nullable=False)
    unit_price_uzs = db.Column(db.DECIMAL(precision=15, scale=2), default=0)  # UZS narx
    total_price_uzs = db.Column(db.DECIMAL(precision=15, scale=2), default=0)  # UZS jami
    cost_price = db.Column(db.DECIMAL(precision=15, scale=10), nullable=False)
    profit = db.Column(db.DECIMAL(precision=18, scale=10), nullable=False)
    source_type = db.Column(db.String(20))  # 'store' yoki 'warehouse'
    source_id = db.Column(db.Integer)  # Store yoki Warehouse ID
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    product = db.relationship('Product', backref='sale_items')

    def to_dict(self):
        # Joylashuv nomini keshdan olish (N+1 oldini olish)
        location_name = "Noma'lum"
        try:
            if self.source_type and self.source_id:
                base_name = _get_location_name_cached(self.source_type, self.source_id)
                prefix = 'Ombor' if self.source_type == 'warehouse' else 'Dokon'
                location_name = f"{prefix}: {base_name}"
        except Exception as e:
            logger.error(f"Error getting location name for SaleItem {self.id}: {str(e)}")
            location_name = f"{self.source_type.title()} (ID: {self.source_id})" if self.source_type and self.source_id else "Noma'lum"

        return {
            'id': self.id,
            'sale_id': self.sale_id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else 'Noma\'lum mahsulot',
            'quantity': float(self.quantity) if self.quantity is not None else 0,
            'unit_price': float(self.unit_price) if self.unit_price is not None else 0.0,
            'total_price': float(self.total_price) if self.total_price is not None else 0.0,
            'unit_price_uzs': float(self.unit_price_uzs) if self.unit_price_uzs is not None else 0.0,
            'total_price_uzs': float(self.total_price_uzs) if self.total_price_uzs is not None else 0.0,
            'cost_price': float(self.cost_price) if self.cost_price is not None else 0.0,
            'profit': float(self.profit) if self.profit is not None else 0.0,
            'source_type': self.source_type,
            'source_id': self.source_id,
            'location_name': location_name,  # To'liq joylashuv nomi
            'notes': self.notes if self.notes else ''
        }


class Sale(db.Model):
    __tablename__ = 'sales'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(
        db.Integer,
        db.ForeignKey('customers.id'),
        nullable=True)
    store_id = db.Column(
        db.Integer,
        db.ForeignKey('stores.id'),
        nullable=True)
    # Multi-location support
    location_id = db.Column(db.Integer, nullable=True)
    location_type = db.Column(db.String(20), nullable=True)  # 'store' yoki 'warehouse'
    seller_id = db.Column(
        db.Integer,
        db.ForeignKey('users.id'),
        nullable=True)
    sale_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    total_amount = db.Column(
        db.DECIMAL(
            precision=15,
            scale=10),
        nullable=False,
        default=0)
    total_cost = db.Column(
        db.DECIMAL(
            precision=15,
            scale=10),
        nullable=False,
        default=0)
    total_profit = db.Column(
        db.DECIMAL(
            precision=15,
            scale=10),
        nullable=False,
        default=0)
    payment_method = db.Column(db.String(20), default='cash')
    payment_status = db.Column(db.String(20), default='paid')
    cash_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    click_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    terminal_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    debt_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    # USD ustunlari
    debt_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    cash_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    click_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    terminal_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)
    balance_usd = db.Column(db.DECIMAL(precision=15, scale=10), default=0)  # Mijoz balansidan to'langan
    notes = db.Column(db.Text)
    currency_rate = db.Column(
        db.DECIMAL(
            precision=15,
            scale=4),
        nullable=True)
    payment_due_date = db.Column(db.Date, nullable=True)  # Qarz to'lash muddati
    created_by = db.Column(db.String(100), default='System')
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    updated_at = db.Column(db.DateTime, default=lambda: get_tashkent_time(), onupdate=lambda: get_tashkent_time())
    # updated_by = db.Column(db.String(100))  # Qarz to'lovini qabul qilgan foydalanuvchi - database'da hali yo'q

    # Relationships
    customer = db.relationship('Customer', backref='sales')
    store = db.relationship('Store', backref='sales')
    seller = db.relationship('User', backref='sales')
    items = db.relationship(
        'SaleItem',
        backref='sale',
        cascade='all, delete-orphan')

    def __repr__(self):
        return f'<Sale {self.id}: {self.total_amount}>'

    def _get_returned_products(self):
        """Qaytarilgan mahsulotlarni operation_history dan topish"""
        try:
            # Bu savdoga tegishli qaytarilgan mahsulotlarni topish
            returned_ops = OperationHistory.query.filter_by(
                record_id=self.id,
                operation_type='return'
            ).all()

            returned_items = []
            for op in returned_ops:
                if op.new_data and isinstance(op.new_data, dict):
                    # Narxni old_data dan olish (unit_price va total_price)
                    unit_price = 0
                    total_price = 0
                    if op.old_data and isinstance(op.old_data, dict):
                        old_quantity = op.old_data.get('quantity', 0)
                        old_total = op.old_data.get('total_price', 0)
                        if old_quantity > 0:
                            unit_price = old_total / old_quantity

                    returned_quantity = op.new_data.get('returned_quantity', 0)
                    total_price = unit_price * returned_quantity

                    returned_items.append({
                        'product_id': op.new_data.get('product_id'),
                        'product_name': op.new_data.get('product_name'),
                        'returned_quantity': returned_quantity,
                        'unit_price': float(unit_price),
                        'total_price': float(total_price),
                        'location_name': op.location_name if op.location_name else 'Noma\'lum',
                        'return_date': op.created_at.isoformat() if op.created_at else None
                    })

            return returned_items
        except Exception as e:
            logger.error(f"Error getting returned products for sale {self.id}: {str(e)}")
            return []

    def _get_payment_refunds(self):
        """Qaytarilgan to'lovlarni operation_history dan topish"""
        try:
            refund_ops = OperationHistory.query.filter_by(
                record_id=self.id,
                operation_type='payment_refund'
            ).all()

            refunds = []
            for op in refund_ops:
                if op.new_data and isinstance(op.new_data, dict):
                    refunds.append({
                        'payment_type': op.new_data.get('payment_type'),
                        'refund_amount_usd': op.new_data.get('refund_amount_usd', 0),
                        'refund_amount_uzs': op.new_data.get('refund_amount_uzs', 0),
                        'refund_date': op.created_at.isoformat() if op.created_at else None
                    })

            return refunds
        except Exception as e:
            logger.error(f"Error getting payment refunds for sale {self.id}: {str(e)}")
            return []

    def to_dict(self, include_items=True, include_details=False):
        """Sale obyektini dict ga aylantirish

        Args:
            include_items: SaleItem'larni qo'shish (default: True)
            include_details: Qo'shimcha ma'lumotlar (returned_products, payment_refunds, debt_payments)
        """
        # Mijoz nomini va telefon raqamini aniqlash
        if self.customer:
            # Mijoz mavjud
            customer_name = self.customer.name
            customer_phone = self.customer.phone if self.customer.phone else DEFAULT_PHONE_PLACEHOLDER
        elif self.customer_id is None:
            # Mijoz tanlanmagan (naqd savdo)
            customer_name = ''  # Bo'sh qoldirish
            customer_phone = ''  # Bo'sh qoldirish
        else:
            # Mijoz o'chirilgan
            customer_name = '🚫 O\'chirilgan mijoz'
            customer_phone = ''
        result = {
            'id': self.id,
            'customer_id': self.customer_id,
            'customer_name': customer_name,
            'customer_phone': customer_phone,
            'customer_balance': float(self.customer.balance or 0) if self.customer else 0.0,
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else '🚫 O\'chirilgan do\'kon',
            'location_id': self.location_id if self.location_id else self.store_id,
            'location_type': self.location_type if self.location_type else ('store' if self.store_id else None),
            'seller_id': self.seller_id,
            'seller_name': f"{self.seller.first_name} {self.seller.last_name}" if self.seller else 'Admin',
            'seller_phone': self.seller.phone if self.seller and self.seller.phone else None,
            'sale_date': self.sale_date.isoformat() if self.sale_date else None,
            'total_amount': float(
                self.total_amount) if self.total_amount is not None else 0.0,
            'total_cost': float(
                self.total_cost) if self.total_cost is not None else 0.0,
            'total_profit': float(
                self.total_profit) if self.total_profit is not None else 0.0,
            'payment_method': self.payment_method if self.payment_method else 'cash',
            'payment_status': self.payment_status if self.payment_status else 'paid',
            # UZS qiymatlar
            'cash_amount': float(self.cash_amount) if self.cash_amount is not None else 0.0,
            'click_amount': float(self.click_amount) if self.click_amount is not None else 0.0,
            'terminal_amount': float(self.terminal_amount) if self.terminal_amount is not None else 0.0,
            'debt_amount': float(self.debt_amount) if self.debt_amount is not None else 0.0,
            # USD qiymatlar
            'cash_usd': float(self.cash_usd) if self.cash_usd is not None else 0.0,
            'click_usd': float(self.click_usd) if self.click_usd is not None else 0.0,
            'terminal_usd': float(self.terminal_usd) if self.terminal_usd is not None else 0.0,
            'debt_usd': float(self.debt_usd) if self.debt_usd is not None else 0.0,
            'balance_usd': float(self.balance_usd) if self.balance_usd is not None else 0.0,
            'payment_details': {
                'cash': float(self.cash_usd) if self.cash_usd is not None else 0.0,
                'click': float(self.click_usd) if self.click_usd is not None else 0.0,
                'terminal': float(self.terminal_usd) if self.terminal_usd is not None else 0.0,
                'debt': float(self.debt_usd) if self.debt_usd is not None else 0.0,
                'balance': float(self.balance_usd) if self.balance_usd is not None else 0.0
            },
            'notes': self.notes if self.notes else '',
            'currency_rate': float(
                self.currency_rate) if self.currency_rate is not None else 0,
            'created_by': self.created_by if self.created_by else 'System',
        }

        # ✅ Optional: Faqat kerak bo'lganda items yuklash
        if include_items:
            result['items'] = [
                item.to_dict() for item in self.items] if self.items else []
        else:
            result['items'] = []  # Bo'sh list qaytarish

        # ✅ Optional: Qo'shimcha ma'lumotlar (xotira tejash uchun)
        if include_details:
            result['returned_products'] = self._get_returned_products()
            result['payment_refunds'] = self._get_payment_refunds()
            result['debt_payments'] = [
                {
                    'id': dp.id,
                    'cash_usd': float(dp.cash_usd or 0),
                    'click_usd': float(dp.click_usd or 0),
                    'terminal_usd': float(dp.terminal_usd or 0),
                    'total_usd': float(dp.total_usd or 0),
                    'payment_date': dp.payment_date.isoformat() if dp.payment_date else None,
                    'received_by': dp.received_by or 'Unknown',
                    'notes': dp.notes or ''
                } for dp in self.debt_payments
            ]
        else:
            # Default: faqat asosiy ma'lumotlar
            result['returned_products'] = []
            result['payment_refunds'] = []
            result['debt_payments'] = []

        return result


# Valyuta kursi modeli
class StockChange(db.Model):
    """Stock o'zgarishlari tarixi - qo'shish, ayirish, transfer"""
    __tablename__ = 'stock_changes'

    id = db.Column(db.Integer, primary_key=True)
    product_id = db.Column(db.Integer, db.ForeignKey('products.id'), nullable=False)
    action = db.Column(db.String(20), nullable=False)  # 'add', 'deduct', 'transfer', 'sale'
    quantity = db.Column(db.DECIMAL(precision=15, scale=3), nullable=False)
    location_type = db.Column(db.String(20), nullable=False)  # 'warehouse' or 'store'
    warehouse_id = db.Column(db.Integer, db.ForeignKey('warehouses.id'), nullable=True)
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id'), nullable=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    change_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    notes = db.Column(db.Text, nullable=True)

    # Relationships (backref olib tashlandi - delete muammosini keltirib chiqaradi)
    product = db.relationship('Product')
    warehouse = db.relationship('Warehouse')
    store = db.relationship('Store')
    user = db.relationship('User')


class ProductAddHistory(db.Model):
    """Mahsulot qo'shilgan tarix - faqat ma'lumot uchun"""
    __tablename__ = 'product_add_history'

    id = db.Column(db.Integer, primary_key=True)
    product_name = db.Column(db.String(200), nullable=False)
    cost_price = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False)
    sell_price = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False)
    quantity = db.Column(db.DECIMAL(precision=15, scale=3), nullable=False)
    location_type = db.Column(db.String(20), nullable=False)  # 'warehouse' or 'store'
    location_name = db.Column(db.String(200), nullable=False)  # Ombor yoki do'kon nomi
    added_by = db.Column(db.String(100), nullable=True)  # Qo'shgan foydalanuvchi
    added_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    notes = db.Column(db.Text, nullable=True)


class CurrencyRate(db.Model):
    __tablename__ = 'currency_rates'

    id = db.Column(db.Integer, primary_key=True)
    from_currency = db.Column(db.String(3), nullable=False, default='USD')
    to_currency = db.Column(db.String(3), nullable=False, default='UZS')
    rate = db.Column(db.DECIMAL(precision=15, scale=4), nullable=False)
    created_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    updated_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    is_active = db.Column(db.Boolean, default=True)
    updated_by = db.Column(db.String(100), default='system')

    def to_dict(self):
        return {
            'id': self.id,
            'from_currency': self.from_currency,
            'to_currency': self.to_currency,
            'rate': float(
                self.rate),
            'created_date': self.created_date.isoformat() if self.created_date else None,
            'updated_date': self.updated_date.isoformat() if self.updated_date else None,
            'is_active': self.is_active,
            'updated_by': self.updated_by}


class Expense(db.Model):
    """Xarajatlar jadvali"""
    __tablename__ = 'expenses'

    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(300), nullable=False)
    amount_usd = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False, default=0)
    amount_uzs = db.Column(db.DECIMAL(precision=20, scale=2), nullable=False, default=0)
    category = db.Column(db.String(100), nullable=True)
    description = db.Column(db.Text, nullable=True)
    expense_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    created_by = db.Column(db.String(100), nullable=True)
    location_type = db.Column(db.String(20), nullable=True)  # 'store' | 'warehouse' | None
    location_id = db.Column(db.Integer, nullable=True)
    location_name = db.Column(db.String(200), nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())

    def to_dict(self):
        return {
            'id': self.id,
            'title': self.title,
            'amount_usd': float(self.amount_usd or 0),
            'amount_uzs': float(self.amount_uzs or 0),
            'category': self.category,
            'description': self.description,
            'expense_date': self.expense_date.strftime('%Y-%m-%d %H:%M') if self.expense_date else None,
            'created_by': self.created_by,
            'location_type': self.location_type,
            'location_id': self.location_id,
            'location_name': self.location_name,
        }


# ============================================
# HOSTING TO'LOV TIZIMI MODELLARI
# ============================================

class HostingClient(db.Model):
    """Hosting mijozlari - DigitalOcean serverlari"""
    __tablename__ = 'hosting_clients'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(200), nullable=False)  # Mijoz ismi / kompaniya
    phone = db.Column(db.String(20), nullable=True)
    telegram_chat_id = db.Column(db.BigInteger, nullable=True)  # Telegram chat ID
    telegram_username = db.Column(db.String(100), nullable=True)  # @username

    # DigitalOcean ma'lumotlari
    droplet_id = db.Column(db.BigInteger, nullable=True)  # DO Droplet ID
    droplet_name = db.Column(db.String(200), nullable=True)  # Server nomi
    server_ip = db.Column(db.String(50), nullable=True)  # IP manzil

    # To'lov ma'lumotlari
    monthly_price_uzs = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False, default=0)
    payment_day = db.Column(db.Integer, default=1)  # Oyning nechanchi kuni to'laydi
    balance = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False, default=0)  # Mijoz balansi

    # Holat
    is_active = db.Column(db.Boolean, default=True)
    server_status = db.Column(db.String(20), default='active')  # active, off, suspended
    status_token = db.Column(db.String(64), unique=True, nullable=True)  # Mijoz uchun maxfiy token

    # Vaqtlar
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    updated_at = db.Column(db.DateTime, default=lambda: get_tashkent_time(), onupdate=lambda: get_tashkent_time())
    notes = db.Column(db.Text, nullable=True)

    # Relationships
    payment_orders = db.relationship('HostingPaymentOrder', backref='client', lazy='dynamic', passive_deletes=True)
    payments = db.relationship('HostingPayment', backref='client', lazy='dynamic', passive_deletes=True)

    def __repr__(self):
        return f'<HostingClient {self.id}: {self.name}>'

    def to_dict(self):
        return {
            'id': self.id,
            'name': self.name,
            'phone': self.phone,
            'telegram_chat_id': self.telegram_chat_id,
            'telegram_username': self.telegram_username,
            'droplet_id': self.droplet_id,
            'droplet_name': self.droplet_name,
            'server_ip': self.server_ip,
            'monthly_price_uzs': float(self.monthly_price_uzs or 0),
            'payment_day': self.payment_day,
            'balance': float(self.balance or 0),
            'is_active': self.is_active,
            'server_status': self.server_status,
            'status_token': self.status_token,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'notes': self.notes
        }


class HostingPaymentOrder(db.Model):
    """To'lov buyurtmalari - mijoz to'lov qilmoqchi"""
    __tablename__ = 'hosting_payment_orders'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('hosting_clients.id', ondelete='CASCADE'), nullable=False)
    order_code = db.Column(db.String(20), unique=True, nullable=False)  # Buyurtma kodi: HP-XXXX

    # To'lov ma'lumotlari
    amount_uzs = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False)
    months = db.Column(db.Integer, default=1)  # Nechi oylik to'lov

    # Status: pending -> client_confirmed -> payment_matched -> approved / rejected / expired
    status = db.Column(db.String(30), default='pending', nullable=False)

    # Card Xabar matching
    card_xabar_amount = db.Column(db.DECIMAL(precision=15, scale=2), nullable=True)
    card_xabar_time = db.Column(db.DateTime, nullable=True)
    card_xabar_message = db.Column(db.Text, nullable=True)

    # Vaqtlar
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    confirmed_at = db.Column(db.DateTime, nullable=True)  # Mijoz "To'ladim" bosgan vaqt
    matched_at = db.Column(db.DateTime, nullable=True)  # Card Xabar mos kelgan vaqt
    approved_at = db.Column(db.DateTime, nullable=True)  # Admin tasdiqlagan vaqt
    expires_at = db.Column(db.DateTime, nullable=True)  # Buyurtma muddati

    admin_notes = db.Column(db.Text, nullable=True)

    def __repr__(self):
        return f'<HostingPaymentOrder {self.order_code}: {self.status}>'

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'client_name': self.client.name if self.client else None,
            'order_code': self.order_code,
            'amount_uzs': float(self.amount_uzs or 0),
            'months': self.months,
            'status': self.status,
            'card_xabar_amount': float(self.card_xabar_amount) if self.card_xabar_amount else None,
            'card_xabar_time': self.card_xabar_time.isoformat() if self.card_xabar_time else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'confirmed_at': self.confirmed_at.isoformat() if self.confirmed_at else None,
            'matched_at': self.matched_at.isoformat() if self.matched_at else None,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'admin_notes': self.admin_notes
        }


class HostingPayment(db.Model):
    """Tasdiqlangan to'lovlar tarixi"""
    __tablename__ = 'hosting_payments'

    id = db.Column(db.Integer, primary_key=True)
    client_id = db.Column(db.Integer, db.ForeignKey('hosting_clients.id', ondelete='CASCADE'), nullable=False)
    order_id = db.Column(db.Integer, db.ForeignKey('hosting_payment_orders.id', ondelete='SET NULL'), nullable=True)

    # To'lov ma'lumotlari
    amount_uzs = db.Column(db.DECIMAL(precision=15, scale=2), nullable=False)
    months_paid = db.Column(db.Integer, default=1)
    payment_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())

    # Davr
    period_start = db.Column(db.Date, nullable=True)
    period_end = db.Column(db.Date, nullable=True)

    # Tasdiqlash
    confirmed_by = db.Column(db.String(100), default='admin')
    notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(db.DateTime, default=lambda: get_tashkent_time())

    # Relationships
    order = db.relationship('HostingPaymentOrder', backref='payment')

    def __repr__(self):
        return f'<HostingPayment {self.id}: {self.client_id} - {self.amount_uzs} UZS>'

    def to_dict(self):
        return {
            'id': self.id,
            'client_id': self.client_id,
            'client_name': self.client.name if self.client else None,
            'order_id': self.order_id,
            'order_code': self.order.order_code if self.order else None,
            'amount_uzs': float(self.amount_uzs or 0),
            'months_paid': self.months_paid,
            'payment_date': self.payment_date.strftime('%Y-%m-%d %H:%M') if self.payment_date else None,
            'period_start': self.period_start.isoformat() if self.period_start else None,
            'period_end': self.period_end.isoformat() if self.period_end else None,
            'confirmed_by': self.confirmed_by,
            'notes': self.notes
        }
