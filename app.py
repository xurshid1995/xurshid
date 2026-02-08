# -*- coding: utf-8 -*-
import bcrypt
import json
import logging
import os
import sys
import time
import urllib.parse
import uuid
from datetime import datetime, timezone, timedelta
from decimal import Decimal, getcontext, InvalidOperation
from functools import wraps
import pytz

from dotenv import load_dotenv
from flask import (Flask, render_template, request, jsonify, redirect,
                   url_for, render_template_string, send_from_directory,
                   session, abort, flash)
from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import text, select
from sqlalchemy.exc import (
    OperationalError,      # Database connection muammolari
    TimeoutError,          # Database timeout
    DatabaseError,         # Umumiy database xatolari
    IntegrityError         # Constraint violation (unique, foreign key)
)
from werkzeug.exceptions import BadRequest
from werkzeug.security import check_password_hash

# Windows console uchun UTF-8 qo'llab-quvvatlash
if sys.platform.startswith('win'):
    import codecs
    sys.stdout = codecs.getwriter('utf-8')(sys.stdout.buffer, 'replace')
    sys.stderr = codecs.getwriter('utf-8')(sys.stderr.buffer, 'replace')

# Environment variables yuklash
load_dotenv()

# Flask app yaratish
app = Flask(__name__)

# Template cache'ni o'chirish - yangilanishlarni darhol ko'rish uchun
app.config['TEMPLATES_AUTO_RELOAD'] = True
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0

# Logging konfiguratsiyasi
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


# Database konfiguratsiyasi - encoding muammosini hal qilish

# PostgreSQL ulanish parametrlari - .env faylidan olish
db_params = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'port': os.getenv('DB_PORT', '5432'),
    'database': os.getenv('DB_NAME', 'sayt_db'),
    'user': os.getenv('DB_USER', 'postgres'),
    'password': os.getenv('DB_PASSWORD', 'postgres')
}

# URL-safe qilish
safe_password = urllib.parse.quote_plus(db_params['password'])
safe_database = urllib.parse.quote_plus(db_params['database'])

# Clean URL yaratish
base_url = f"postgresql://{db_params['user']}:{safe_password}"
full_url = f"{base_url}@{db_params['host']}:{db_params['port']}"
database_url = f"{full_url}/{safe_database}?client_encoding=utf8"

logger.info("DATABASE_URL configured")
app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# SECRET_KEY xavfsizlik tekshiruvi
SECRET_KEY = os.getenv('SECRET_KEY')
if not SECRET_KEY or SECRET_KEY in ['your-secret-key-here', 'your-very-secret-key-here-change-this']:
    raise ValueError(
        "âŒ XAVFSIZLIK: SECRET_KEY o'rnatilmagan! "
        "Yangi kalit: python -c 'import secrets; print(secrets.token_hex(32))'"
    )
app.config['SECRET_KEY'] = SECRET_KEY

# Database Connection Pool - API timeout muammosini hal qilish
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    'pool_size': 10,          # Maksimal 10 ta active connection
    'pool_recycle': 3600,     # Har 1 soatda connection yangilash
    'pool_pre_ping': True,    # Connection alive ekanini tekshirish (dead connection oldini oladi)
    'max_overflow': 20,       # Qo'shimcha 20 ta temporary connection
    'pool_timeout': 30,       # Connection olish uchun 30 sekund timeout
    'connect_args': {
        'connect_timeout': 10,  # PostgreSQL connection timeout
        'options': '-c statement_timeout=10000 -c timezone=Asia/Tashkent'  # Query timeout va timezone
    }
}

# Session xavfsizligi
app.config['SESSION_COOKIE_SECURE'] = False  # HTTP uchun False, HTTPS uchun True
app.config['SESSION_COOKIE_HTTPONLY'] = True  # JavaScript orqali o'qib bo'lmaydi
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if app.config['SESSION_COOKIE_SECURE'] else 'Lax'  # Cross-site uchun None, HTTP uchun Lax
app.config['PERMANENT_SESSION_LIFETIME'] = 43200  # 12 soat (uzaytirilgan ish vaqti)
app.config['SESSION_COOKIE_DOMAIN'] = None  # Subdomen muammosini hal qilish

# SQLAlchemy obyektini yaratish
db = SQLAlchemy(app)

# Decimal aniqlik o'rnatish
getcontext().prec = 10

# O'zbekiston vaqt zonasi
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

def get_tashkent_time():
    """O'zbekiston vaqtini qaytaradi"""
    return datetime.now(TASHKENT_TZ)

# âœ… Cache o'zgaruvchilari - xotira tejash uchun
_locations_cache = None
_locations_cache_time = None
_all_locations_cache = None
_all_locations_cache_time = None
CACHE_DURATION = 300  # 5 daqiqa


# Timeout monitoring decorator
def timeout_monitor(max_seconds=5, operation_name=None):
    """API timeout va xatolarni monitoring qilish"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            operation_id = str(uuid.uuid4())[:8]
            op_name = operation_name or f.__name__
            start_time = time.time()

            logger.info(f"ğŸ†” [{operation_id}] {op_name} started")

            try:
                result = f(*args, **kwargs)
                duration = time.time() - start_time

                # Sekin API'larni aniqlash
                if duration > max_seconds:
                    logger.warning(
                        f"âš ï¸ [{operation_id}] SLOW API: {op_name} took {duration:.2f}s "
                        f"(max: {max_seconds}s)"
                    )
                else:
                    logger.info(
                        f"âœ… [{operation_id}] {op_name} completed in {duration:.2f}s"
                    )

                return result

            except Exception as e:
                duration = time.time() - start_time
                logger.error(
                    f"âŒ [{operation_id}] {op_name} FAILED after {duration:.2f}s: {str(e)}"
                )
                raise

        return wrapped
    return decorator


# Idempotency key tekshirish uchun funksiya
def check_idempotency(operation_type):
    """Idempotency key orqali takroriy so'rovlarni oldini olish"""
    def decorator(f):
        @wraps(f)
        def wrapped(*args, **kwargs):
            # Idempotency key'ni olish (header yoki body dan)
            idempotency_key = request.headers.get('X-Idempotency-Key')

            if not idempotency_key:
                # Agar key berilmagan bo'lsa, body dan olish
                data = request.get_json() or {}
                idempotency_key = data.get('idempotency_key')

            if idempotency_key:
                # Avval bajarilgan operatsiyani tekshirish
                existing = ApiOperation.query.filter_by(
                    idempotency_key=idempotency_key,
                    operation_type=operation_type
                ).first()

                if existing:
                    logger.warning(
                        f"âš ï¸ Duplicate request detected: {operation_type} - {idempotency_key}"
                    )

                    # Oldingi natijani qaytarish
                    if existing.result_data:
                        result = json.loads(existing.result_data)
                        result['already_processed'] = True
                        return jsonify(result)

                    return jsonify({
                        'success': True,
                        'already_processed': True,
                        'message': 'Bu operatsiya allaqachon bajarilgan'
                    })

            # Yangi operatsiya - bajarish
            result = f(*args, **kwargs)

            # Natijani saqlash (faqat success bo'lsa)
            if idempotency_key and isinstance(result, tuple):
                response_data, status_code = result
                if status_code == 200:
                    try:
                        current_user = get_current_user()
                        api_op = ApiOperation(
                            idempotency_key=idempotency_key,
                            operation_type=operation_type,
                            user_id=current_user.id if current_user else None,
                            status='completed',
                            result_data=json.dumps(response_data.get_json() if hasattr(response_data, 'get_json') else {})
                        )
                        db.session.add(api_op)
                        db.session.commit()
                    except Exception as e:
                        logger.error(f"âŒ Idempotency save error: {e}")
                        db.session.rollback()
                        # Bu xatolik asosiy operatsiyaga ta'sir qilmasin
                        pass

            return result

        return wrapped
    return decorator

# Konstantalar
DEFAULT_PHONE_PLACEHOLDER = os.getenv('DEFAULT_PHONE_PLACEHOLDER', 'Telefon kiritilmagan')


# Helper functions
def hash_password(password):
    """Parolni hash qilish"""
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')


def check_password(password, hashed):
    """Parolni tekshirish - bcrypt va eski pbkdf2 formatlarini qo'llab-quvvatlash"""
    try:
        # Yangi bcrypt formatini tekshirish
        if hashed.startswith('$2b$') or hashed.startswith('$2a$') or hashed.startswith('$2y$'):
            return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

        # Eski pbkdf2 formatini tekshirish (werkzeug)
        elif hashed.startswith('pbkdf2:'):
            return check_password_hash(hashed, password)

        # Noma'lum format
        else:
            logger.error(f"Noma'lum parol formati: {hashed[:20]}...")
            return False

    except Exception as e:
        logger.error(f"Parol tekshirishda xatolik: {str(e)}")
        return False


def validate_quantity(quantity, field_name='Miqdor'):
    """Miqdorni validatsiya qilish - manfiy va haddan tashqari katta qiymatlardan himoya"""
    try:
        qty = Decimal(str(quantity))

        if qty < 0:
            return False, f"{field_name} manfiy bo'lishi mumkin emas"

        if qty > 999999999:
            return False, f"{field_name} juda katta (maksimal: 999,999,999)"

        # Kasr qismini tekshirish - maksimal 2 ta raqam
        if qty.as_tuple().exponent < -2:
            return False, f"{field_name} maksimal 2 ta kasr raqamga ega bo'lishi mumkin"

        return True, None

    except (ValueError, TypeError, InvalidOperation):
        return False, f"{field_name} noto'g'ri formatda"


def log_operation(operation_type, table_name=None, record_id=None, description=None,
                  old_data=None, new_data=None, location_id=None, location_type=None,
                  location_name=None, amount=None):
    """
    Tizim amaliyotlarini loglash

    Args:
        operation_type: Amaliyot turi ('sale', 'add_product', 'transfer', 'return', 'edit', 'delete', 'payment')
        table_name: Ta'sirlangan jadval nomi
        record_id: Ta'sirlangan record ID
        description: Amaliyot tavsifi
        old_data: Eski ma'lumotlar (dict)
        new_data: Yangi ma'lumotlar (dict)
        location_id: Joylashuv ID
        location_type: 'store' yoki 'warehouse'
        location_name: Joylashuv nomi
        amount: Summa
    """
    try:
        current_user = get_current_user()

        # IP addressni olish
        ip_address = request.headers.get('X-Forwarded-For', request.remote_addr)
        if ip_address and ',' in ip_address:
            ip_address = ip_address.split(',')[0].strip()

        log_entry = OperationHistory(
            operation_type=operation_type,
            table_name=table_name,
            record_id=record_id,
            user_id=current_user.id if current_user else None,
            username=current_user.username if current_user else 'System',
            description=description,
            old_data=old_data,
            new_data=new_data,
            ip_address=ip_address,
            location_id=location_id,
            location_type=location_type,
            location_name=location_name,
            amount=float(amount) if amount else None
        )

        db.session.add(log_entry)
        db.session.commit()

        logger.info(f"ğŸ“ Operation logged: {operation_type} by {log_entry.username}")

    except Exception as e:
        logger.error(f"âŒ Operatsiyani loglashda xatolik: {str(e)}")
        # Loglash xatosi asosiy amaliyotni to'xtatmasligi kerak
        db.session.rollback()


# Role-based access control decorator
def role_required(*allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Session tekshirish
            if 'user_id' not in session:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Login required'}), 401
                return redirect(url_for('login_page'))

            # Session hijacking himoyasi - User-Agent tekshirish
            session_ua = session.get('user_agent')
            current_ua = request.headers.get('User-Agent', '')
            if session_ua and session_ua != current_ua:
                logger.warning(f"Session hijacking attempt: user_id={session.get('user_id')}, expected UA={session_ua[:50]}, got UA={current_ua[:50]}")
                session.clear()
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Session invalid'}), 401
                return redirect(url_for('login_page'))

            user_role = session.get('role')
            if not user_role:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Login required'}), 401
                return redirect(url_for('login_page'))

            # Role tekshirish
            if user_role not in allowed_roles:
                if request.path.startswith('/api/'):
                    return jsonify({'error': 'Access denied'}), 403
                abort(403)  # Forbidden

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def location_permission_required(
        location_param_name,
        location_type_param_name=None):
    """
    Joylashuv ruxsatini tekshiruvchi decorator
    location_param_name: URL parametri nomi (masalan: 'store_id', 'warehouse_id')
    location_type_param_name: Joylashuv turi parametri (masalan: 'location_type')
    """
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            # Admin va kassir uchun cheklov yo'q
            user_role = session.get('role')
            if user_role in ['admin', 'kassir']:
                return f(*args, **kwargs)

            # Sotuvchi uchun joylashuv ruxsatini tekshirish
            if user_role == 'sotuvchi':
                user_id = session.get('user_id')
                user = User.query.get(user_id)

                if not user or not user.allowed_locations:
                    abort(403)  # Ruxsat etilgan joylashuvlar yo'q

                # URL'dan joylashuv ID'sini olish
                location_id = kwargs.get(location_param_name)
                if not location_id:
                    abort(400)  # Joylashuv ID'si topilmadi

                # Joylashuv ruxsat etilganligini tekshirish
                # Eski format [{'id': 1, 'type': 'store'}, ...] va yangi format [1, 2, 3, ...] ni qo'llab-quvvatlash
                allowed_location_ids = []
                for loc in user.allowed_locations:
                    if isinstance(loc, dict) and 'id' in loc:
                        # Eski format: {'id': 1, 'type': 'store'}
                        allowed_location_ids.append(loc['id'])
                    elif isinstance(loc, int):
                        # Yangi format: [1, 2, 3, ...]
                        allowed_location_ids.append(loc)

                logger.debug(f"Location permission check: location_id={location_id}, allowed={allowed_location_ids}")
                if location_id not in allowed_location_ids:
                    logger.warning(f"Access denied to location {location_id} for user {user_id}")
                    abort(403)  # Bu joylashuvga ruxsat yo'q

                logger.debug(f"Access granted to location {location_id}")

            return f(*args, **kwargs)
        return decorated_function
    return decorator


def get_current_user():
    """Session'dan current user ni olish - request ichida cache bilan"""
    # Request ichida cache qilish - bir request'da faqat 1 marta DB query
    if not hasattr(request, '_cached_user'):
        user_id = session.get('user_id')
        if user_id:
            try:
                # Type validation - session manipulyatsiyadan himoya
                user_id = int(user_id)
                request._cached_user = User.query.get(user_id)
            except (ValueError, TypeError):
                logger.warning(f"Invalid user_id in session: {user_id}")
                session.clear()
                request._cached_user = None
        else:
            request._cached_user = None
    return request._cached_user


def extract_location_ids(locations, location_type):
    """
    Allowed_locations dan ID'larni olish - eski va yangi formatlarni qo'llab-quvvatlash
    locations: list - allowed_locations yoki transfer_locations
    location_type: str - 'store' yoki 'warehouse'
    """
    print(f"ğŸ” extract_location_ids called: locations={locations}, type={location_type}")

    if not locations:
        print("ğŸ” extract_location_ids: locations is empty, returning []")
        return []

    # Eski format (ID'lar ro'yxati) tekshirish
    if isinstance(locations[0], int):
        print("ğŸ” extract_location_ids: Old format detected (int list)")
        # Eski format: [1, 2, 3]
        # Type bo'yicha filtrlash uchun ma'lumotlar bazasidan tekshirish kerak

        if location_type == 'store':
            # Faqat store ID'larni olish
            existing_store_ids = [s.id for s in Store.query.filter(Store.id.in_(locations)).all()]
            print(f"ğŸ” extract_location_ids: Store IDs from DB: {existing_store_ids}")
            return existing_store_ids
        else:  # warehouse
            # Faqat warehouse ID'larni olish
            existing_warehouse_ids = [w.id for w in Warehouse.query.filter(Warehouse.id.in_(locations)).all()]
            print(f"ğŸ” extract_location_ids: Warehouse IDs from DB: {existing_warehouse_ids}")
            return existing_warehouse_ids

    # Yangi format: [{'id': 1, 'type': 'store'}, {'id': 2, 'type':
    # 'warehouse'}]
    print("ğŸ” extract_location_ids: New format detected (dict list)")
    result = [loc['id'] for loc in locations if isinstance(
        loc, dict) and loc.get('type') == location_type]
    print(f"ğŸ” extract_location_ids: Result: {result}")
    return result


# Model yaratish - Mahsulot jadvali
class Product(db.Model):
    __tablename__ = 'products'

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    barcode = db.Column(db.String(255), unique=True, nullable=True, index=True)  # Barcode raqami
    cost_price = db.Column(db.DECIMAL(precision=10, scale=2),
                           nullable=False)  # Ortacha tan narxi
    sell_price = db.Column(db.DECIMAL(precision=10, scale=2),
                           nullable=False)  # Sotish narxi
    min_stock = db.Column(db.Integer, default=0,
                          nullable=False)  # Minimal qoldiq
    unit_type = db.Column(db.String(10), default='dona', nullable=False)  # O'lchov birligi: 'dona' yoki 'litr'
    last_batch_cost = db.Column(db.DECIMAL(precision=10, scale=2))  # Oxirgi partiya tan narxi
    last_batch_date = db.Column(db.DateTime)  # Oxirgi partiya sanasi
    created_at = db.Column(db.DateTime,
                           default=lambda: get_tashkent_time())  # Qo'shilgan sana
    is_checked = db.Column(
        db.Boolean,
        default=False,
        nullable=False)  # Tekshirilganlik holati

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
            'stocks': stocks
        }


# Model yaratish - Buyurtma jadvali
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
            'status': 'low' if self.quantity <= self.min_stock else 'normal',
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
        """Qayerdan joylashuv nomi"""
        if self.from_location_type == 'store':
            store = Store.query.get(self.from_location_id)
            return store.name if store else 'Noma\'lum do\'kon'
        elif self.from_location_type == 'warehouse':
            warehouse = Warehouse.query.get(self.from_location_id)
            return warehouse.name if warehouse else 'Noma\'lum ombor'
        return 'Noma\'lum'

    @property
    def to_location_name(self):
        """Qayerga joylashuv nomi"""
        if self.to_location_type == 'store':
            store = Store.query.get(self.to_location_id)
            return store.name if store else 'Noma\'lum do\'kon'
        elif self.to_location_type == 'warehouse':
            warehouse = Warehouse.query.get(self.to_location_id)
            return warehouse.name if warehouse else 'Noma\'lum ombor'
        return 'Noma\'lum'

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
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())
    updated_at = db.Column(db.DateTime, default=db.func.current_timestamp(), onupdate=db.func.current_timestamp())

    # Relationship
    user = db.relationship('User', backref='pending_transfers')

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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }


# Helper funksiya: Transfer boshqarish ruxsatini tekshirish
def user_can_manage_transfer(user, pending_transfer):
    """
    Foydalanuvchi transferni tahrirlashi/o'chirishi/yakunlashi mumkinligini aniqlash.

    Ruxsat beriladi agar:
    1. Admin (har doim)
    2. Transfer joylashuvlaridan (FROM yoki TO) kamida biriga ruxsati bor foydalanuvchi
    """
    # 1. Admin har doim
    if user.role == 'admin':
        return True

    # 2. FROM yoki TO joylashuvlaridan biriga ruxsati bo'lsa (transfer_locations yoki allowed_locations)
    # Transfer locations dan tekshirish (transfer qilish huquqi)
    transfer_locations = user.transfer_locations or []
    if not transfer_locations:
        transfer_locations = user.allowed_locations or []
    
    # Allowed locations dan ham tekshirish (umuman joylashuvga ruxsat)
    allowed_locations = user.allowed_locations or []
    
    # Barcha mavjud joylashuvlarni birlashtirish
    all_user_locations = transfer_locations + allowed_locations
    
    if not all_user_locations:
        return False
    
    # FROM joylashuv
    from_type = pending_transfer.from_location_type
    from_id = pending_transfer.from_location_id
    
    # TO joylashuv
    to_type = pending_transfer.to_location_type
    to_id = pending_transfer.to_location_id
    
    # Joylashuvlarni tekshirish (yangi va eski formatni qo'llab-quvvatlash)
    has_from_permission = False
    has_to_permission = False
    
    for loc in all_user_locations:
        # Yangi format: {'id': 1, 'type': 'warehouse'}
        if isinstance(loc, dict):
            loc_id = loc.get('id')
            loc_type = loc.get('type')
            
            if loc_id == from_id and loc_type == from_type:
                has_from_permission = True
            if loc_id == to_id and loc_type == to_type:
                has_to_permission = True
        
        # Eski format: integer (faqat id)
        elif isinstance(loc, int):
            if loc == from_id:
                has_from_permission = True
            if loc == to_id:
                has_to_permission = True
    
    # Kamida biriga ruxsat bo'lsa yetarli
    if has_from_permission or has_to_permission:
        return True

    return False


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
    last_debt_payment_date = db.Column(db.DateTime, nullable=True)
    last_debt_payment_rate = db.Column(db.Numeric(10, 2), default=13000)
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
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None}


# Qarz to'lovlari tarixi modeli
class DebtPayment(db.Model):
    __tablename__ = 'debt_payments'

    id = db.Column(db.Integer, primary_key=True)
    customer_id = db.Column(db.Integer, db.ForeignKey('customers.id', ondelete='SET NULL'), nullable=True)
    sale_id = db.Column(db.Integer, db.ForeignKey('sales.id', ondelete='SET NULL'), nullable=True)
    payment_date = db.Column(db.DateTime, default=lambda: get_tashkent_time())
    cash_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    click_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    terminal_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    total_usd = db.Column(db.DECIMAL(precision=12, scale=2), nullable=False)
    currency_rate = db.Column(db.DECIMAL(precision=15, scale=4), nullable=False, default=12500)
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
            'currency_rate': float(self.currency_rate or 12500),
            'received_by': self.received_by,
            'notes': self.notes
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
    # admin, sotuvchi, kassir, ombor_xodimi
    role = db.Column(db.String(50), nullable=False, default='sotuvchi')
    store_id = db.Column(db.Integer, db.ForeignKey('stores.id'), nullable=True)
    permissions = db.Column(db.JSON, default=lambda: {})  # Ruxsatlar (JSON)
    # Ruxsat etilgan joylashuvlar
    allowed_locations = db.Column(db.JSON, default=lambda: [])
    # Transfer qilish uchun ruxsat etilgan joylashuvlar
    transfer_locations = db.Column(db.JSON, default=lambda: [])
    is_active = db.Column(db.Boolean, default=True)
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
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None
        }

    def get_role_display(self):
        role_names = {
            'admin': 'Administrator',
            'sotuvchi': 'Sotuvchi',
            'kassir': 'Kassir',
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
            'full_name': self.user.full_name if self.user else None,
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
    unit_price = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False)
    total_price = db.Column(db.DECIMAL(precision=12, scale=2), nullable=False)
    cost_price = db.Column(db.DECIMAL(precision=10, scale=2), nullable=False)
    profit = db.Column(db.DECIMAL(precision=12, scale=2), nullable=False)
    source_type = db.Column(db.String(20))  # 'store' yoki 'warehouse'
    source_id = db.Column(db.Integer)  # Store yoki Warehouse ID
    notes = db.Column(db.Text)
    created_at = db.Column(db.DateTime, default=db.func.current_timestamp())

    # Relationships
    product = db.relationship('Product', backref='sale_items')

    def to_dict(self):
        # Joylashuv nomini olish
        location_name = 'Noma\'lum'
        try:
            if self.source_type == 'warehouse' and self.source_id:
                warehouse = Warehouse.query.get(self.source_id)
                location_name = f"Ombor: {warehouse.name}" if warehouse else f"Ombor (ID: {self.source_id})"
            elif self.source_type == 'store' and self.source_id:
                store = Store.query.get(self.source_id)
                location_name = f"Dokon: {store.name}" if store else f"Dokon (ID: {self.source_id})"
        except Exception as e:
            app.logger.error(f"Error getting location name for SaleItem {self.id}: {str(e)}")
            location_name = f"{self.source_type.title()} (ID: {self.source_id})" if self.source_type and self.source_id else 'Noma\'lum'

        return {
            'id': self.id,
            'sale_id': self.sale_id,
            'product_id': self.product_id,
            'product_name': self.product.name if self.product else 'Noma\'lum mahsulot',
            'quantity': float(self.quantity) if self.quantity is not None else 0,
            'unit_price': float(self.unit_price) if self.unit_price is not None else 0.0,
            'total_price': float(self.total_price) if self.total_price is not None else 0.0,
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
            precision=12,
            scale=2),
        nullable=False,
        default=0)
    total_cost = db.Column(
        db.DECIMAL(
            precision=12,
            scale=2),
        nullable=False,
        default=0)
    total_profit = db.Column(
        db.DECIMAL(
            precision=12,
            scale=2),
        nullable=False,
        default=0)
    payment_method = db.Column(db.String(20), default='cash')
    payment_status = db.Column(db.String(20), default='paid')
    cash_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    click_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    terminal_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    debt_amount = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    # USD ustunlari
    debt_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    cash_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    click_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    terminal_usd = db.Column(db.DECIMAL(precision=12, scale=2), default=0)
    notes = db.Column(db.Text)
    currency_rate = db.Column(
        db.DECIMAL(
            precision=15,
            scale=4),
        nullable=False,
        default=12500.0000)
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
            app.logger.error(f"Error getting returned products for sale {self.id}: {str(e)}")
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
            app.logger.error(f"Error getting payment refunds for sale {self.id}: {str(e)}")
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
            'store_id': self.store_id,
            'store_name': self.store.name if self.store else 'ğŸš« O\'chirilgan do\'kon',
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
            'payment_details': {
                'cash': float(self.cash_usd) if self.cash_usd is not None else 0.0,
                'click': float(self.click_usd) if self.click_usd is not None else 0.0,
                'terminal': float(self.terminal_usd) if self.terminal_usd is not None else 0.0,
                'debt': float(self.debt_usd) if self.debt_usd is not None else 0.0
            },
            'notes': self.notes if self.notes else '',
            'currency_rate': float(
                self.currency_rate) if self.currency_rate is not None else 12500.0,
            'created_by': self.created_by if self.created_by else 'System',
        }

        # âœ… Optional: Faqat kerak bo'lganda items yuklash
        if include_items:
            result['items'] = [
                item.to_dict() for item in self.items] if self.items else []
        else:
            result['items'] = []  # Bo'sh list qaytarish

        # âœ… Optional: Qo'shimcha ma'lumotlar (xotira tejash uchun)
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


# API Test sahifasi
@app.route('/api_test.html')
def api_test():
    return """<!DOCTYPE html> <html>
<head>
    <title>API Test</title>
</head>
<body>
    <h1>Sales History API Test</h1>
    <button onclick="testAPI()">Test API</button>
    <pre id="result"></pre>

    <script>
        async function testAPI() {
            try {
                console.log('Testing API...');
                const response = await fetch('/api/sales-history');
                const data = await response.json();
                console.log('API Response:', data);
                document.getElementById('result').textContent = JSON.stringify(data, null, 2);
            } catch (error) {
                console.error('API Error:', error);
                document.getElementById('result').textContent = 'Error: ' + error.message;
            }
        }
    </script>
</body>
</html>"""

# Favicon route


@app.route('/favicon.ico')
def favicon():
    return send_from_directory(
        os.path.join(app.root_path, 'static'),
        'favicon.ico',
        mimetype='image/vnd.microsoft.icon'
    )


# Asosiy sahifa - login sahifasiga yo'naltirish
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect('/dashboard')
    return redirect('/login')

# Mahsulot qo'shish sahifasi


@app.route('/add_product', methods=['GET'])
@role_required('admin', 'kassir')
def add_product():
    return render_template('add_product.html')


@app.route('/add_product_new')
def add_product_new():
    return render_template('add_product.html')


@app.route('/currency-rate')
@role_required('admin', 'kassir', 'sotuvchi')
def currency_rate():
    return render_template('currency_rate.html')

# API endpoint - keyingi barcode raqamini olish
@app.route('/api/next-barcode', methods=['GET', 'POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_next_barcode():
    """O'rtada qolgan yoki keyingi barcode'ni topish (vaqtinchalik ro'yxatni ham hisobga olib)"""
    try:
        # Barcha 8 xonali barcode'larni olish
        products = Product.query.filter(
            Product.barcode.isnot(None),
            Product.barcode != ''
        ).all()

        # Barcha mavjud barcode'larni raqamga aylantirish
        existing_barcodes = set()
        max_barcode = 0

        for product in products:
            try:
                # Faqat raqamli va 8 xonali barcode'larni tekshirish
                if product.barcode and product.barcode.isdigit() and len(product.barcode) == 8:
                    barcode_num = int(product.barcode)
                    existing_barcodes.add(barcode_num)
                    if barcode_num > max_barcode:
                        max_barcode = barcode_num
            except Exception:
                continue

        # POST request bo'lsa, vaqtinchalik ro'yxatdagi barcode'larni ham qo'shish
        temp_barcodes_count = 0
        if request.method == 'POST':
            data = request.get_json() or {}
            temp_barcodes = data.get('temp_barcodes', [])

            for barcode in temp_barcodes:
                try:
                    if barcode and isinstance(barcode, str) and barcode.isdigit() and len(barcode) == 8:
                        barcode_num = int(barcode)
                        existing_barcodes.add(barcode_num)
                        if barcode_num > max_barcode:
                            max_barcode = barcode_num
                        temp_barcodes_count += 1
                except Exception:
                    continue

            logger.info(f"ğŸ“¦ Vaqtinchalik ro'yxatdan {temp_barcodes_count} ta barcode qo'shildi")

        # 1 dan boshlab birinchi bo'sh joyni topish
        next_barcode_num = None
        for i in range(1, max_barcode + 2):  # max + 2 gacha tekshirish
            if i not in existing_barcodes:
                next_barcode_num = i
                break

        # Agar topilmasa (hamma joy band), eng kattasidan keyingisini berish
        if next_barcode_num is None:
            next_barcode_num = max_barcode + 1

        # 8 xonali formatga aylantirish
        next_barcode = str(next_barcode_num).zfill(8)

        # O'rtada qolgan barcode ekanligini aniqlash
        is_gap_filled = next_barcode_num <= max_barcode

        return jsonify({
            'success': True,
            'barcode': next_barcode,
            'is_gap_filled': is_gap_filled,
            'max_barcode': str(max_barcode).zfill(8) if max_barcode > 0 else None,
            'total_used': len(existing_barcodes),
            'temp_barcodes_count': temp_barcodes_count
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

# API endpoint - mahsulotlar ro'yxati (faqat stock mavjud bo'lganlar)


@app.route('/api/products')
@role_required('admin', 'kassir', 'sotuvchi')
def api_products():
    """Optimized products API with pagination and location filtering support"""

    # Pagination parametrlar
    page = request.args.get('page', 1, type=int)
    per_page = request.args.get('per_page', 50, type=int)  # Default 50
    per_page = min(per_page, 20000)  # Maximum 20000 limit (transfer uchun barcha mahsulotlar)

    # Search parameter
    search = request.args.get('search', '', type=str).strip()

    # Location filter parameters - eski va yangi formatlarni qo'llab-quvvatlash
    location_filter = request.args.get('location', '', type=str).strip()
    location_type = request.args.get('location_type', '', type=str).strip()
    location_id = request.args.get('location_id', type=int)

    # Base query with eager loading to avoid N+1 problem
    query = Product.query.options(
        db.joinedload(Product.warehouse_stocks),
        db.joinedload(Product.store_stocks)
    )

    # Search filter - qisman so'zlar bilan qidirish
    if search:
        # Qidiruv so'zlarini bo'laklarga ajratish
        search_words = search.lower().split()

        # Har bir so'z uchun filter qo'shish (barcha so'zlar mahsulot nomida bo'lishi kerak)
        for word in search_words:
            if word:  # Bo'sh so'zlarni o'tkazib yuborish
                query = query.filter(Product.name.ilike(f'%{word}%'))

    # Location filter - yangi format (location_type va location_id)
    if location_type and location_id:
        if location_type == 'warehouse':
            # Filter products that have stock in specific warehouse
            query = query.filter(
                Product.warehouse_stocks.any(
                    WarehouseStock.warehouse_id == location_id
                )
            )
        elif location_type == 'store':
            # Filter products that have stock in specific store
            query = query.filter(
                Product.store_stocks.any(
                    StoreStock.store_id == location_id
                )
            )
    # Location filter - eski format (location)
    elif location_filter and location_filter != 'all':
        try:
            loc_type, loc_id = location_filter.split('_')
            loc_id = int(loc_id)

            if loc_type == 'warehouse':
                # Filter products that have stock in specific warehouse
                query = query.filter(
                    Product.warehouse_stocks.any(
                        WarehouseStock.warehouse_id == loc_id
                    )
                )
            elif loc_type == 'store':
                # Filter products that have stock in specific store
                query = query.filter(
                    Product.store_stocks.any(
                        StoreStock.store_id == loc_id
                    )
                )
        except (ValueError, IndexError):
            # Invalid location filter format, ignore
            pass
    # Location filter yo'q bo'lsa - barcha mahsulotlarni ko'rsatish (stock bo'lsin yoki bo'lmasin)

    # Saralash: Yangi qo'shilgan mahsulotlar birinchi bo'lishi uchun ID bo'yicha kamayish tartibida
    from sqlalchemy import desc
    query = query.order_by(desc(Product.id))

    # Get paginated results
    paginated = query.paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    products_list = []
    for product in paginated.items:
        product_dict = product.to_dict()
        products_list.append(product_dict)

    # Return with pagination metadata
    return jsonify({
        'products': products_list,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': paginated.total,
            'pages': paginated.pages,
            'has_next': paginated.has_next,
            'has_prev': paginated.has_prev
        },
        'filters': {
            'search': search,
            'location': location_filter
        }
    })


# API endpoint - dokon va omborlar ro'yxati
@app.route('/api/locations')
def api_locations():
    """Savdo uchun ruxsat etilgan joylashuvlarni qaytarish (allowed_locations ishlatadi)"""
    try:
        logger.debug(" API Locations called")
        import sys
        sys.stdout.flush()
        # Session tekshirish
        if 'user_id' not in session:
            logger.error(" No user_id in session")
            return jsonify({'error': 'Login required'}), 401

        logger.debug(f" Session user_id: {session.get('user_id')}")
        locations = []

        # Joriy foydalanuvchini olish
        current_user = get_current_user()
        logger.debug(f" API Locations - Current user: {current_user}")
        if not current_user:
            logger.error(" No current user found - returning empty locations")
            return jsonify([])  # Bo'sh array qaytarish

        print(
            f"ğŸ” API Locations - User: {current_user.username}, Role: {current_user.role}")
        print(f"ğŸ” allowed_locations RAW: {current_user.allowed_locations}")

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role == 'admin':
            # Admin hamma joylashuvlarni ko'radi
            allowed_store_ids = None
            allowed_warehouse_ids = None
            print("ğŸ” Admin user - showing ALL locations")
        else:
            # Oddiy foydalanuvchilar faqat allowed_locations dan ruxsat etilgan
            # joylashuvlarni ko'radi (savdo uchun)
            allowed_locations = current_user.allowed_locations or []
            logger.debug(f" Raw allowed_locations: {allowed_locations}")
            print("ğŸ” Non-admin user - filtering locations")
            print(f"ğŸ” allowed_locations: {allowed_locations}")

            # Helper funksiya bilan ID'larni olish (eski va yangi formatlar uchun)
            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            allowed_warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')

            print(f"ğŸ” Filtered store IDs: {allowed_store_ids}")
            print(f"ğŸ” Filtered warehouse IDs: {allowed_warehouse_ids}")
            logger.debug(f" Allowed store IDs: {allowed_store_ids}")
            logger.debug(f" Allowed warehouse IDs: {allowed_warehouse_ids}")

        # Do'konlarni qo'shish (birinchi bo'lib)
        if allowed_store_ids is None:
            stores = Store.query.all()
        else:
            stores = Store.query.filter(
                Store.id.in_(allowed_store_ids)).all() if allowed_store_ids else []

        for store in stores:
            locations.append({
                'type': 'store',
                'id': store.id,
                'name': store.name,
                'emoji': '🏪',
                'display': f'🏪 {store.name} (Do\'kon)'
            })

        # Omborlarni qo'shish (ikkinchi bo'lib)
        if allowed_warehouse_ids is None:
            warehouses = Warehouse.query.all()
        else:
            warehouses = Warehouse.query.filter(Warehouse.id.in_(
                allowed_warehouse_ids)).all() if allowed_warehouse_ids else []

        for warehouse in warehouses:
            locations.append({
                'type': 'warehouse',
                'id': warehouse.id,
                'name': warehouse.name,
                'emoji': '📦',
                'display': f'📦 {warehouse.name} (Ombor)'
            })

        logger.debug(f" Final locations count: {len(locations)}")
        return jsonify(locations)

    except Exception as e:
        import traceback
        logger.error(f" Error in api_locations: {str(e)}")
        logger.error(f" Traceback: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


# API endpoint - mahsulotlar sahifasi uchun barcha joylashuvlar (filterlashsiz)
@app.route('/api/all-locations')
@role_required('admin', 'kassir', 'sotuvchi')
def api_all_locations():
    """Mahsulotlar sahifasi uchun barcha joylashuvlar (filterlashsiz)"""
    global _all_locations_cache, _all_locations_cache_time

    # âœ… Cache tekshirish
    if _all_locations_cache and _all_locations_cache_time:
        elapsed = (datetime.now() - _all_locations_cache_time).total_seconds()
        if elapsed < CACHE_DURATION:
            logger.debug(f"ğŸ“¦ All-locations cache hit - {int(CACHE_DURATION - elapsed)}s qoldi")
            return jsonify(_all_locations_cache)

    logger.debug(" All Locations API - Barcha foydalanuvchilar uchun barcha joylashuvlar")

    locations = []

    # Barcha omborlarni qo'shish
    warehouses = Warehouse.query.all()
    for warehouse in warehouses:
        logger.debug(f" Including warehouse {warehouse.id} ({warehouse.name})")
        locations.append({
            'type': 'warehouse',
            'id': warehouse.id,
            'name': warehouse.name,
            'display': f'📦 {warehouse.name} (Ombor)'
        })

    # Barcha do'konlarni qo'shish
    stores = Store.query.all()
    for store in stores:
        logger.debug(f" Including store {store.id} ({store.name})")
        locations.append({
            'type': 'store',
            'id': store.id,
            'name': store.name,
            'display': f'🏪 {store.name} (Do\'kon)'
        })

    logger.info(f" Total locations for products page: {len(locations)}")

    # âœ… Cache'ga saqlash
    _all_locations_cache = locations
    _all_locations_cache_time = datetime.now()
    logger.debug("ğŸ’¾ All-locations cached")
    return jsonify(locations)


# API endpoint - joylashuv bo'yicha mahsulotlarni qidirish (OPTIMIZED)
@app.route('/api/search-products-by-location/<location_type>/<int:location_id>')
def api_search_products_by_location(location_type, location_id):
    """Tanlangan joylashuv bo'yicha mahsulotlarni qidirish (lazy loading)"""
    try:
        search_term = request.args.get('search', '').strip()
        limit = int(request.args.get('limit', 20))  # Maksimum 20 ta natija

        products_list = []

        if location_type == 'warehouse':
            # Ombor mahsulotlarini qidirish
            query = db.session.query(WarehouseStock, Product).join(
                Product, WarehouseStock.product_id == Product.id
            ).filter(WarehouseStock.warehouse_id == location_id)

            if search_term:
                # Qidiruv so'zlarini bo'laklarga ajratish
                search_words = search_term.lower().split()
                # Har bir so'z uchun filter qo'shish
                for word in search_words:
                    if word:
                        query = query.filter(Product.name.ilike(f'%{word}%'))

            stocks = query.limit(limit).all()

            for stock, product in stocks:
                if product:  # Mahsulot mavjudligini tekshirish
                    product_dict = product.to_dict()
                    product_dict['available_quantity'] = stock.quantity
                    product_dict['location_type'] = 'warehouse'
                    product_dict['location_id'] = location_id
                    product_dict['location_name'] = stock.warehouse.name if stock.warehouse else 'Noma\'lum ombor'
                    products_list.append(product_dict)

        elif location_type == 'store':
            # Do'kon mahsulotlarini qidirish
            query = db.session.query(StoreStock, Product).join(
                Product, StoreStock.product_id == Product.id
            ).filter(StoreStock.store_id == location_id)

            if search_term:
                # Qidiruv so'zlarini bo'laklarga ajratish
                search_words = search_term.lower().split()
                # Har bir so'z uchun filter qo'shish
                for word in search_words:
                    if word:
                        query = query.filter(Product.name.ilike(f'%{word}%'))

            stocks = query.limit(limit).all()

            for stock, product in stocks:
                if product:
                    product_dict = product.to_dict()
                    product_dict['available_quantity'] = stock.quantity
                    product_dict['location_type'] = 'store'
                    product_dict['location_id'] = location_id
                    store_name = 'Noma\'lum do\'kon'
                    if stock.store:
                        store_name = stock.store.name
                    product_dict['location_name'] = store_name
                    products_list.append(product_dict)

        return jsonify({
            'products': products_list,
            'total': len(products_list),
            'search_term': search_term
        })

    except Exception as e:
        logger.error(f"Error in search products by location: {e}")
        return jsonify({'error': 'Server xatosi'}), 500


# API endpoint - joylashuv bo'yicha mahsulotlar (LEGACY - eski usul)
@app.route('/api/products-by-location/<location_type>/<int:location_id>')
def api_products_by_location(location_type, location_id):
    """Tanlangan joylashuv bo'yicha mahsulotlar ro'yxatini qaytarish (DEPRECATED)"""
    try:
        products_list = []

        if location_type == 'warehouse':
            # Ombor mahsulotlari - barcha mahsulotlar (miqdori 0 bo'lsa ham)
            warehouse_stocks = WarehouseStock.query.filter_by(
                warehouse_id=location_id).all()
            for stock in warehouse_stocks:
                if stock.product:  # Mahsulot mavjudligini tekshirish
                    product_dict = stock.product.to_dict()
                    product_dict['available_quantity'] = stock.quantity
                    product_dict['location_type'] = 'warehouse'
                    product_dict['location_id'] = location_id
                    product_dict['location_name'] = stock.warehouse.name if stock.warehouse else 'Noma\'lum ombor'
                    products_list.append(product_dict)

        elif location_type == 'store':
            # Do'kon mahsulotlari
            store_stocks = StoreStock.query.filter_by(
                store_id=location_id).all()
            for stock in store_stocks:
                # Mahsulot mavjudligini tekshirish (miqdorga qaramay)
                if stock.product:
                    product_dict = stock.product.to_dict()
                    product_dict['available_quantity'] = stock.quantity
                    product_dict['location_type'] = 'store'
                    product_dict['location_id'] = location_id
                    store_name = 'Noma\'lum do\'kon'
                    if stock.store:
                        store_name = stock.store.name
                    location_name = store_name
                    product_dict['location_name'] = location_name
                    products_list.append(product_dict)

        return jsonify(products_list)

    except Exception as e:
        logger.error(f"Error in products by location: {e}")
        return jsonify({'error': 'Server xatosi'}), 500


# API endpoint - barcode bo'yicha mahsulot qidirish
@app.route('/api/search-product-by-barcode', methods=['POST'])
def search_product_by_barcode():
    """Barcode bo'yicha mahsulot qidirish - timeout handling bilan"""
    start_time = time.time()

    try:
        # Request validatsiya
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'Ma\'lumot yuborilmagan',
                'error_type': 'validation'
            }), 400

        barcode = data.get('barcode', '').strip()
        if not barcode:
            return jsonify({
                'success': False,
                'error': 'Barcode kiritilmagan',
                'error_type': 'validation'
            }), 400

        location_type = data.get('location_type')
        location_id = data.get('location_id')
        if not location_type or not location_id:
            return jsonify({
                'success': False,
                'error': 'Joylashuv tanlanmagan',
                'error_type': 'validation'
            }), 400

        # Database query - timeout bilan
        try:
            product = Product.query.filter_by(barcode=barcode).first()
        except TimeoutError:
            duration = time.time() - start_time
            logger.error(f"â±ï¸ Database timeout: {duration:.2f}s - Barcode: {barcode}")
            return jsonify({
                'success': False,
                'error': 'So\'rov juda uzoq davom etdi. Qayta urinib ko\'ring.',
                'error_type': 'timeout',
                'duration': round(duration, 2)
            }), 504
        except OperationalError as e:
            logger.error(f"ğŸ”Œ Database connection xatosi: {e}")
            return jsonify({
                'success': False,
                'error': 'Ma\'lumotlar bazasiga ulanishda xatolik',
                'error_type': 'database_connection'
            }), 503

        if not product:
            return jsonify({
                'success': False,
                'error': f'Barcode {barcode} topilmadi',
                'error_type': 'not_found'
            }), 404

        # Stock tekshirish - timeout bilan
        available_quantity = 0
        location_name = ''

        try:
            if location_type == 'warehouse':
                stock = WarehouseStock.query.filter_by(
                    product_id=product.id,
                    warehouse_id=location_id
                ).first()
                if stock:
                    available_quantity = stock.quantity
                    location_name = stock.warehouse.name if stock.warehouse else 'Noma\'lum ombor'
            elif location_type == 'store':
                stock = StoreStock.query.filter_by(
                    product_id=product.id,
                    store_id=location_id
                ).first()
                if stock:
                    available_quantity = stock.quantity
                    location_name = stock.store.name if stock.store else 'Noma\'lum do\'kon'
        except TimeoutError:
            logger.error("â±ï¸ Stock query timeout")
            return jsonify({
                'success': False,
                'error': 'Stock ma\'lumotlarini olishda timeout',
                'error_type': 'timeout'
            }), 504

        # Muvaffaqiyatli javob
        duration = time.time() - start_time
        if duration > 5:
            logger.warning(f"âš ï¸ Slow query: {request.path} - {duration:.2f}s")

        product_dict = product.to_dict()
        product_dict['available_quantity'] = available_quantity
        product_dict['location_type'] = location_type
        product_dict['location_id'] = location_id
        product_dict['location_name'] = location_name

        logger.info(f"âœ… Barcode {barcode} topildi: {product.name}, Miqdor: {available_quantity}")
        return jsonify({
            'success': True,
            'data': product_dict,
            'query_duration': round(duration, 2)
        })

    except BadRequest as e:
        logger.error(f"âŒ Bad request: {e}")
        return jsonify({
            'success': False,
            'error': 'Noto\'g\'ri so\'rov formati',
            'error_type': 'bad_request'
        }), 400
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"âŒ Kutilmagan xato ({duration:.2f}s): {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Kutilmagan server xatosi',
            'error_type': 'internal_server_error',
            'duration': round(duration, 2)
        }), 500


# API endpoint - mahsulot nomini tekshirish
@app.route('/api/check-product-name', methods=['POST'])
def check_product_name():
    """Mahsulot nomini tekshirish - yaxshilangan error handling bilan"""
    start_time = time.time()

    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'Ma\'lumot yuborilmagan',
                'error_type': 'validation'
            }), 400

        name = data.get('name', '').strip()
        exclude_id = data.get('exclude_id')

        if not name:
            return jsonify({'exists': False})

        # Database query
        try:
            query = Product.query.filter(Product.name.ilike(name))
            if exclude_id:
                query = query.filter(Product.id != exclude_id)
            existing_product = query.first()
        except TimeoutError:
            duration = time.time() - start_time
            logger.error(f"â±ï¸ Database timeout: {duration:.2f}s")
            return jsonify({
                'success': False,
                'error': 'So\'rov juda uzoq davom etdi',
                'error_type': 'timeout'
            }), 504
        except OperationalError as e:
            logger.error(f"ğŸ”Œ Database connection xatosi: {e}")
            return jsonify({
                'success': False,
                'error': 'Ma\'lumotlar bazasiga ulanishda xatolik',
                'error_type': 'database_connection'
            }), 503

        duration = time.time() - start_time
        if duration > 3:
            logger.warning(f"âš ï¸ Slow query: {request.path} - {duration:.2f}s")

        return jsonify({'exists': existing_product is not None})

    except BadRequest as e:
        logger.error(f"âŒ Bad request: {e}")
        return jsonify({
            'success': False,
            'error': 'Noto\'g\'ri so\'rov formati',
            'error_type': 'bad_request'
        }), 400
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"âŒ Xato ({duration:.2f}s): {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Server xatosi',
            'error_type': 'internal_server_error'
        }), 500


# API endpoint - barcode mavjudligini tekshirish
@app.route('/api/check-barcode', methods=['POST'])
def check_barcode():
    """Barcode mavjudligini tekshirish - yaxshilangan error handling bilan"""
    start_time = time.time()

    try:
        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'error': 'Ma\'lumot yuborilmagan',
                'error_type': 'validation'
            }), 400

        barcode = data.get('barcode', '').strip()
        exclude_id = data.get('exclude_id')

        if not barcode:
            return jsonify({'exists': False, 'product': None})

        # Database query
        try:
            query = Product.query.filter_by(barcode=barcode)
            if exclude_id:
                query = query.filter(Product.id != exclude_id)
            existing_product = query.first()
        except TimeoutError:
            duration = time.time() - start_time
            logger.error(f"â±ï¸ Database timeout: {duration:.2f}s")
            return jsonify({
                'success': False,
                'error': 'So\'rov juda uzoq davom etdi',
                'error_type': 'timeout'
            }), 504
        except OperationalError as e:
            logger.error(f"ğŸ”Œ Database connection xatosi: {e}")
            return jsonify({
                'success': False,
                'error': 'Ma\'lumotlar bazasiga ulanishda xatolik',
                'error_type': 'database_connection'
            }), 503

        duration = time.time() - start_time
        if duration > 3:
            logger.warning(f"âš ï¸ Slow query: {request.path} - {duration:.2f}s")

        if existing_product:
            return jsonify({
                'exists': True,
                'product': {
                    'id': existing_product.id,
                    'name': existing_product.name,
                    'barcode': existing_product.barcode
                }
            })

        return jsonify({'exists': False, 'product': None})

    except BadRequest as e:
        logger.error(f"âŒ Bad request: {e}")
        return jsonify({
            'success': False,
            'error': 'Noto\'g\'ri so\'rov formati',
            'error_type': 'bad_request'
        }), 400
    except Exception as e:
        duration = time.time() - start_time
        logger.error(f"âŒ Xato ({duration:.2f}s): {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': 'Server xatosi',
            'error_type': 'internal_server_error'
        }), 500

# API endpoint - yangi mahsulot qo'shish


@app.route('/api/products', methods=['POST'])
def api_add_product():
    try:
        data = request.get_json()
        logger.info(f"ğŸ“¦ Mahsulot qo'shish so'rovi: {data}")

        # Bir nechta mahsulotlar uchun
        if 'products' in data:
            created_products = []
            for product_data in data['products']:
                cost_price = Decimal(str(product_data['costPrice']))
                sell_price = Decimal(str(product_data['sellPrice']))
                quantity = product_data.get('quantity', 0)

                # Quantity validation
                is_valid, error_msg = validate_quantity(quantity, 'Miqdor')
                if not is_valid:
                    return jsonify({'error': f"{product_data['name']}: {error_msg}"}), 400

                logger.info(f"ğŸ“Š Mahsulot: {product_data['name']}, Miqdor: {quantity}, Location: {product_data.get('locationValue', 'N/A')}")

                # Validatsiya
                if sell_price < cost_price:
                    return jsonify({'error': 'Sotish narxi tan narxidan past '
                                    f'bo\'lishi mumkin emas! ({product_data["name"]})'}), 400

                # Barcode validatsiyasi
                barcode = product_data.get('barcode', None)
                if barcode:
                    barcode = barcode.strip()
                    # Barcode mavjudligini tekshirish (faqat yangi mahsulot uchun)
                    existing_barcode_product = Product.query.filter_by(barcode=barcode).first()
                    if existing_barcode_product:
                        # Agar mavjud mahsulot bilan bir xil nom bo'lmasa, xato qaytarish
                        if existing_barcode_product.name != product_data['name']:
                            return jsonify({
                                'error': f'Barcode {barcode} allaqachon "{existing_barcode_product.name}" mahsulotida mavjud!'
                            }), 400

                # Mahsulot yaratish yoki topish
                existing_product = Product.query.filter_by(
                    name=product_data['name']).first()
                if existing_product:
                    # Mavjud mahsulot - Frontend'dan ortacha narx va asl narx keladi

                    logger.info("ğŸ“Š Mavjud mahsulot yangilanmoqda:")
                    logger.info(f"   Eski cost_price (ortacha): ${existing_product.cost_price}")
                    logger.info(f"   Frontend dan kelgan cost_price (ortacha): ${cost_price}")
                    logger.info(f"   Frontend dan kelgan last_batch_cost (yangi partiya): ${product_data.get('lastBatchCost', cost_price)}")

                    # Ortacha narxni yangilash
                    existing_product.cost_price = cost_price

                    # Oxirgi partiya ma'lumotlarini saqlash
                    existing_product.last_batch_cost = Decimal(str(product_data.get('lastBatchCost', cost_price)))
                    existing_product.last_batch_date = get_tashkent_time()

                    # Boshqa maydonlar
                    existing_product.sell_price = sell_price
                    existing_product.min_stock = product_data.get(
                        'minStock', existing_product.min_stock)

                    # Unit type yangilash (agar berilgan bo'lsa)
                    if 'unitType' in product_data:
                        existing_product.unit_type = product_data['unitType']

                    # Barcode yangilash (agar kiritilgan bo'lsa)
                    if 'barcode' in product_data and product_data['barcode']:
                        existing_product.barcode = product_data['barcode']

                    product = existing_product
                else:
                    # Yangi mahsulot yaratish
                    product = Product(
                        name=product_data['name'],
                        barcode=product_data.get('barcode', None),  # Barcode qo'shish
                        cost_price=cost_price,
                        sell_price=sell_price,
                        last_batch_cost=cost_price,  # Birinchi partiya
                        last_batch_date=get_tashkent_time(),
                        stock_quantity=0,  # Global stock 0 ga qo'yamiz
                        min_stock=product_data.get('minStock', 0),
                        unit_type=product_data.get('unitType', 'dona')  # O'lchov birligi
                    )
                    db.session.add(product)
                    db.session.flush()  # ID olish uchun

                # Joylashuvni aniqlash va stock qo'shish
                location_value = product_data.get('locationValue', '')
                location_name = ''
                location_type_str = ''

                if location_value.startswith('store_'):
                    store_id = int(location_value.replace('store_', ''))
                    store = Store.query.get(store_id)
                    if store:
                        location_type_str = 'store'
                        location_name = store.name

                        # Store stock qo'shish yoki yangilash
                        existing_stock = StoreStock.query.filter_by(
                            store_id=store_id, product_id=product.id).first()
                        if existing_stock:
                            # Race condition oldini olish - atomic UPDATE
                            db.session.execute(
                                text("UPDATE store_stocks SET quantity = quantity + :qty WHERE id = :stock_id"),
                                {'qty': quantity, 'stock_id': existing_stock.id}
                            )
                            # Object'ni refresh qilish
                            db.session.refresh(existing_stock)
                        else:
                            store_stock = StoreStock(
                                store_id=store_id,
                                product_id=product.id,
                                quantity=quantity
                            )
                            db.session.add(store_stock)

                elif location_value.startswith('warehouse_'):
                    warehouse_id = int(
                        location_value.replace(
                            'warehouse_', ''))
                    warehouse = Warehouse.query.get(warehouse_id)
                    if warehouse:
                        location_type_str = 'warehouse'
                        location_name = warehouse.name

                        # Warehouse stock qo'shish yoki yangilash
                        existing_stock = WarehouseStock.query.filter_by(
                            warehouse_id=warehouse_id, product_id=product.id).first()
                        if existing_stock:
                            # Race condition oldini olish - atomic UPDATE
                            db.session.execute(
                                text("UPDATE warehouse_stocks SET quantity = quantity + :qty WHERE id = :stock_id"),
                                {'qty': quantity, 'stock_id': existing_stock.id}
                            )
                            db.session.refresh(existing_stock)
                        else:
                            warehouse_stock = WarehouseStock(
                                warehouse_id=warehouse_id,
                                product_id=product.id,
                                quantity=quantity
                            )
                            db.session.add(warehouse_stock)

                # History yozuvi yaratish (faqat ma'lumot uchun)
                logger.info(f"ğŸ” History check: quantity={quantity}, location_name='{location_name}', location_type='{location_type_str}'")
                if quantity > 0 and location_name:
                    current_user_name = None
                    if 'user_id' in session:
                        user = User.query.get(session['user_id'])
                        if user:
                            current_user_name = user.username

                    history = ProductAddHistory(
                        product_name=product.name,
                        cost_price=cost_price,
                        sell_price=sell_price,
                        quantity=quantity,
                        location_type=location_type_str,
                        location_name=location_name,
                        added_by=current_user_name
                    )
                    db.session.add(history)

                    # OperationHistory ga ham yozish
                    location_id_int = None
                    if location_type_str == 'store':
                        location_id_int = int(location_value.replace('store_', ''))
                    elif location_type_str == 'warehouse':
                        location_id_int = int(location_value.replace('warehouse_', ''))

                    operation = OperationHistory(
                        operation_type='add_product',
                        table_name='products',
                        record_id=product.id,
                        user_id=session.get('user_id'),
                        username=current_user_name or 'System',
                        description=f"Mahsulot qo'shildi: {product.name} - {quantity} {product.unit_type}",
                        old_data=None,
                        new_data={
                            'product_id': product.id,
                            'product_name': product.name,
                            'quantity': float(quantity),
                            'cost_price': float(cost_price),
                            'sell_price': float(sell_price),
                            'barcode': product.barcode
                        },
                        ip_address=request.remote_addr,
                        location_id=location_id_int,
                        location_type=location_type_str,
                        location_name=location_name,
                        amount=float(cost_price * Decimal(str(quantity)))  # Jami summa
                    )
                    db.session.add(operation)

                    logger.info(f"âœ… History yozuvi yaratildi: {product.name}, {quantity} ta, {location_name}")
                else:
                    logger.warning(f"âš ï¸ History yaratilmadi: quantity={quantity}, location_name='{location_name}'")

                created_products.append(product)

            db.session.commit()
            return jsonify(
                {'success': True, 'count': len(created_products)}), 201

        # Bitta mahsulot uchun (eski format)
        else:
            cost_price = Decimal(str(data['cost_price']))
            sell_price = Decimal(str(data['sell_price']))

            # Validatsiya
            if sell_price < cost_price:
                return jsonify({'error': 'Sotish narxi tan narxidan past '
                                         'bo\'lishi mumkin emas!'}), 400

            new_product = Product(
                name=data['name'],
                cost_price=cost_price,
                sell_price=sell_price,
                stock_quantity=data.get('stock_quantity', 0),
                min_stock=data.get('min_stock', 0),
                unit_type=data.get('unit_type', 'dona')  # O'lchov birligi
            )

            db.session.add(new_product)
            db.session.commit()

            return jsonify(new_product.to_dict()), 201

    except Exception as e:
        db.session.rollback()
        error_msg = str(e)

        # Check for duplicate barcode error
        if 'unique constraint' in error_msg.lower() and 'barcode' in error_msg.lower():
            return jsonify({'error': 'Bu barcode allaqachon boshqa mahsulotda mavjud!'}), 400

        logger.error(f"âŒ Mahsulot qo'shish xatosi: {e}")
        return jsonify({'error': error_msg}), 400


# Batch mahsulotlar qo'shish API
@app.route('/api/batch-products', methods=['POST'])
def api_batch_products():
    try:
        data = request.get_json()
        products = data.get('products', [])

        logger.info(f"ğŸ“¦ Batch products request keldi: {len(products)} ta mahsulot")

        if not products:
            return jsonify({'error': 'Mahsulotlar ro\'yxati bo\'sh'}), 400

        created_count = 0

        for product_data in products:
            # Ma'lumotlarni olish
            location_type = product_data['location_type']

            # Location ID ni parse qilish (warehouse_3 -> 3, store_5 -> 5)
            location_id_raw = product_data['location_id']
            if isinstance(location_id_raw, str):
                # String bo'lsa, raqamni ajratib olish
                location_id = int(location_id_raw.split('_')[-1])
            else:
                # Integer bo'lsa, o'zini qoldirish
                location_id = int(location_id_raw)

            logger.info(f"ğŸ” Location: type={location_type}, id={location_id} (raw: {location_id_raw})")
            name = product_data['name']
            barcode = product_data.get('barcode', None)  # Barcode olish
            quantity = Decimal(str(product_data['quantity']))
            cost_price = Decimal(str(product_data['cost_price']))
            sell_price = Decimal(str(product_data['sell_price']))
            min_stock = int(float(product_data['min_stock']))
            last_batch_cost = Decimal(str(product_data.get('lastBatchCost', cost_price)))

            logger.info(f"ğŸ” Batch mahsulot qo'shilmoqda: {name}")
            logger.info(f"   Barcode: {barcode}")
            logger.info(f"   Tan narx (cost_price - ortacha): ${cost_price}")
            logger.info(f"   Asl tan narx (lastBatchCost): ${last_batch_cost}")
            logger.info(f"   Sotish narx: ${sell_price}")
            logger.info(f"   Miqdor: {quantity}")

            # Barcode validatsiyasi
            if barcode:
                barcode = barcode.strip()
                # Barcode mavjudligini tekshirish
                existing_barcode_product = Product.query.filter_by(barcode=barcode).first()
                if existing_barcode_product and existing_barcode_product.name != name:
                    return jsonify({
                        'error': f'Barcode {barcode} allaqachon "{existing_barcode_product.name}" mahsulotida mavjud!'
                    }), 400

            # Mahsulot mavjudligini tekshirish
            product = Product.query.filter_by(name=name).first()
            if not product:
                # Yangi mahsulot yaratish
                logger.info("âœ¨ Yangi mahsulot yaratilmoqda")
                product = Product(
                    name=name,
                    barcode=barcode,  # Barcode saqlash
                    cost_price=cost_price,
                    sell_price=sell_price,
                    last_batch_cost=last_batch_cost,  # Frontend'dan kelgan qiymat
                    last_batch_date=get_tashkent_time(),
                    min_stock=min_stock,
                    unit_type=product_data.get('unitType', 'dona')  # O'lchov birligi
                )
                db.session.add(product)
                db.session.flush()  # ID olish uchun
                logger.info(f"âœ… Yangi mahsulot yaratildi - ID: {product.id}, barcode: {product.barcode}, cost_price: ${product.cost_price}, last_batch_cost: ${product.last_batch_cost}")
            else:
                # Mavjud mahsulot - Frontend'dan ortacha narx va asl narx keladi
                logger.info(f"â™»ï¸ Mavjud mahsulot yangilanmoqda - ID: {product.id}")
                logger.info(f"   Eski cost_price (ortacha): ${product.cost_price}")
                logger.info(f"   Frontend dan kelgan cost_price (ortacha): ${cost_price}")
                logger.info(f"   Frontend dan kelgan last_batch_cost (yangi partiya): ${product_data.get('lastBatchCost', cost_price)}")

                # Ortacha narxni yangilash
                product.cost_price = cost_price

                # Barcode yangilash (agar kiritilgan bo'lsa)
                if barcode:
                    product.barcode = barcode

                # Unit type yangilash (agar berilgan bo'lsa)
                if 'unitType' in product_data:
                    product.unit_type = product_data['unitType']

                # Oxirgi partiya ma'lumotlarini saqlash
                product.last_batch_cost = last_batch_cost
                product.last_batch_date = get_tashkent_time()

                logger.info(f"âœ… Yangilandi - barcode: {product.barcode}, cost_price: ${product.cost_price}, last_batch_cost: ${product.last_batch_cost}")

                # Boshqa maydonlar
                product.sell_price = sell_price
                product.min_stock = min_stock

            # Stock qo'shish va joylashuv nomini olish
            location_name = ''

            if location_type == 'warehouse':
                warehouse = Warehouse.query.get(location_id)
                if warehouse:
                    location_name = warehouse.name

                stock = WarehouseStock.query.filter_by(
                    warehouse_id=location_id,
                    product_id=product.id
                ).first()

                if stock:
                    stock.quantity += quantity
                else:
                    stock = WarehouseStock(
                        warehouse_id=location_id,
                        product_id=product.id,
                        quantity=quantity
                    )
                    db.session.add(stock)

            elif location_type == 'store':
                store = Store.query.get(location_id)
                if store:
                    location_name = store.name

                stock = StoreStock.query.filter_by(
                    store_id=location_id,
                    product_id=product.id
                ).first()

                if stock:
                    stock.quantity += quantity
                else:
                    stock = StoreStock(
                        store_id=location_id,
                        product_id=product.id,
                        quantity=quantity
                    )
                    db.session.add(stock)

            # History yozuvi yaratish (faqat ma'lumot uchun)
            if quantity > 0 and location_name:
                current_user_name = None
                if 'user_id' in session:
                    user = User.query.get(session['user_id'])
                    if user:
                        current_user_name = user.username

                history = ProductAddHistory(
                    product_name=product.name,
                    cost_price=cost_price,
                    sell_price=sell_price,
                    quantity=quantity,
                    location_type=location_type,
                    location_name=location_name,
                    added_by=current_user_name
                )
                db.session.add(history)

                # OperationHistory ga ham yozish
                operation = OperationHistory(
                    operation_type='add_product',
                    table_name='products',
                    record_id=product.id,
                    user_id=session.get('user_id'),
                    username=current_user_name or 'System',
                    description=f"Mahsulot qo'shildi: {product.name} - {quantity} {product.unit_type}",
                    old_data=None,
                    new_data={
                        'product_id': product.id,
                        'product_name': product.name,
                        'quantity': float(quantity),
                        'cost_price': float(cost_price),
                        'sell_price': float(sell_price),
                        'barcode': product.barcode
                    },
                    ip_address=request.remote_addr,
                    location_id=location_id,
                    location_type=location_type,
                    location_name=location_name,
                    amount=float(cost_price * quantity)
                )
                db.session.add(operation)

            created_count += 1

        db.session.commit()
        return jsonify({
            'success': True,
            'created': created_count,
            'message': f'{created_count} ta mahsulot muvaffaqiyatli qo\'shildi'
        }), 201

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 400


# Mahsulot qo'shish tarixi API
@app.route('/api/products/history', methods=['GET'])
def get_product_history():
    """Qo'shilgan mahsulotlar tarixini olish - ProductAddHistory jadvalidan"""
    try:
        # Oxirgi 50 ta qo'shish operatsiyalari
        limit = int(request.args.get('limit', 50))

        # ProductAddHistory jadvalidan ma'lumotlarni olish
        history_records = ProductAddHistory.query.order_by(
            ProductAddHistory.added_date.desc()
        ).limit(limit).all()

        history_data = []
        for record in history_records:
            # Joylashuv turini aniqlash
            location_type_uz = 'Ombor' if record.location_type == 'warehouse' else 'Do\'kon'

            # Miqdor va qiymat
            quantity = float(record.quantity)
            total_value = quantity * float(record.cost_price)

            history_data.append({
                'id': record.id,
                'name': record.product_name,
                'cost_price': float(record.cost_price),
                'sell_price': float(record.sell_price),
                'total_quantity': quantity,
                'total_value': total_value,
                'locations': [{
                    'type': location_type_uz,
                    'name': record.location_name,
                    'quantity': quantity
                }],
                'created_date': (record.added_date.isoformat()
                                 if record.added_date else None),
                'added_by': record.added_by if record.added_by else 'Admin'
            })

        return jsonify({
            'success': True,
            'history': history_data,
            'count': len(history_data)
        })

    except Exception as e:
        logger.error(f"Mahsulot tarixini olishda xatolik: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Tarixni yuklashda xatolik yuz berdi'
        }), 500


# Mahsulot qidirish API
@app.route('/api/search-product/<product_name>')
def search_product(product_name):
    """Mahsulot nomiga qarab joylashuvlarini topish (partial search)"""
    try:
        logger.debug(f"ğŸ” Qidiruv so'rovi: '{product_name}'")

        # Optimized query - eager loading va limit
        products = Product.query.filter(
            Product.name.ilike(f'%{product_name}%')
        ).options(
            db.joinedload(Product.warehouse_stocks).joinedload(WarehouseStock.warehouse),
            db.joinedload(Product.store_stocks).joinedload(StoreStock.store)
        ).limit(10).all()  # Faqat birinchi 10 ta natija

        if not products:
            logger.debug(f"âŒ Mahsulot topilmadi: '{product_name}'")
            return jsonify({'exists': False})

        logger.info(f"âœ… {len(products)} ta mahsulot topildi")

        products_data = []

        for product in products:

            locations = []
            total_quantity = 0

            # Eager-loaded relationships orqali foydalanish
            # Omborlardan ma'lumot olish
            for stock in product.warehouse_stocks:
                locations.append({
                    'type': 'warehouse',
                    'name': stock.warehouse.name,
                    'quantity': float(stock.quantity),
                    'id': stock.warehouse.id
                })
                total_quantity += float(stock.quantity)

            # Do'konlardan ma'lumot olish
            for stock in product.store_stocks:
                locations.append({
                    'type': 'store',
                    'name': stock.store.name,
                    'quantity': float(stock.quantity),
                    'id': stock.store.id
                })
                total_quantity += float(stock.quantity)

            # Har bir mahsulot uchun ma'lumot
            products_data.append({
                'product': {
                    'name': product.name,
                    'barcode': product.barcode,  # Barcode qo'shildi
                    'cost_price': float(product.cost_price),
                    'sell_price': float(product.sell_price),
                    'min_stock': product.min_stock,
                    'last_batch_cost': float(product.last_batch_cost) if product.last_batch_cost else None,
                    'last_batch_date': product.last_batch_date.isoformat() if product.last_batch_date else None
                },
                'locations': locations,
                'total_quantity': total_quantity
            })

        return jsonify({
            'exists': True,
            'products': products_data
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 400


# Decimal hisoblash namunasi
@app.route('/api/calculate')
def calculate_total():
    """Decimal hisoblash misoli - barcha mahsulotlar qiymatini hisoblash"""
    products = Product.query.all()
    total = Decimal('0.00')

    for product in products:
        total += product.price * product.stock_quantity

    return jsonify({
        'total_value': float(total),
        'precision': str(total),  # Aniq qiymat
        'currency': 'USD'
    })


# Yangi sahifalar uchun route'lar
@app.route('/sales')
def sales():
    return render_template('sales.html')


@app.route('/sales-history')
def sales_history():
    return render_template(
        'sales-history.html',
        page_title='Savdo tarixi',
        icon='ğŸ“Š')


@app.route('/debt-sales')
def debt_sales():
    return render_template(
        'debt-sales.html',
        page_title='Qarz savdolar',
        icon='ğŸ’³')


@app.route('/pending-sales')
def pending_sales():
    return render_template(
        'pending-sales.html',
        page_title='Tasdiqlanmagan savdolar',
        icon='â³')


@app.route('/customers')
@role_required('admin', 'kassir', 'sotuvchi')
def customers():
    user_role = session.get('role', 'guest')
    return render_template(
        'customers.html',
        page_title='Mijozlar',
        icon='ğŸ‘¥',
        user_role=user_role)


@app.route('/debts')
@role_required('admin', 'kassir', 'sotuvchi')
def debts():
    """Qarzlar sahifasi"""
    user = get_current_user()
    return render_template(
        'debts.html',
        page_title='Qarzlar',
        icon='ğŸ’°',
        current_user=user,
        user_role=user.role if user else 'guest',
        allowed_locations=user.allowed_locations if user else [])


@app.route('/paid-debts-history')
@role_required('admin', 'kassir', 'sotuvchi')
def paid_debts_history():
    """Mijozlarni qarz to'lash tarixi sahifasi"""
    user = get_current_user()
    return render_template(
        'paid_debts_history.html',
        page_title='Qarz to\'lash tarixi',
        icon='ğŸ“œ',
        current_user=user)


@app.route('/debts/customer/<int:customer_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def customer_debt_detail(customer_id):
    """Mijoz qarz tafsilotlari sahifasi"""
    return render_template(
        'customer_debt_detail.html',
        customer_id=customer_id,
        page_title='Qarz ma\'lumotlari',
        icon='ğŸ’°')


@app.route('/debts/payment-history')
@role_required('admin', 'kassir', 'sotuvchi')
def debt_payment_history():
    """Qarz to'lash tarixi sahifasi"""
    return render_template(
        'debt_payment_history.html',
        page_title='Qarz to\'lash tarixi',
        icon='ğŸ“œ')


@app.route('/customer/<int:customer_id>')
def customer_detail(customer_id):
    """Mijoz tafsilotlari sahifasi"""
    try:
        customer = Customer.query.get_or_404(customer_id)
        return render_template(
            'customer_detail.html',
            customer=customer,
            page_title='Mijoz tafsilotlari',
            icon='ğŸ‘¤')
    except Exception as e:
        app.logger.error(f"Error loading customer details: {str(e)}")
        return "Mijoz ma'lumotlari yuklanmadi", 500


@app.route('/add-customer', methods=['GET', 'POST'])
def add_customer():
    """Mijoz qo'shish sahifasi"""
    if request.method == 'POST':
        try:
            name = request.form.get('name')
            phone = request.form.get('phone')
            email = request.form.get('email')
            address = request.form.get('address')
            store_id = request.form.get('store_id')

            # Validatsiya
            if not name:
                return jsonify({'error': 'Mijoz nomi kiritilishi shart'}), 400

            # Store ID ni integer ga aylantirish
            if store_id and store_id != '':
                try:
                    store_id = int(store_id)
                except ValueError:
                    store_id = None
            else:
                store_id = None

            # Yangi mijozni yaratish
            new_customer = Customer(
                name=name,
                phone=phone,
                email=email,
                address=address,
                store_id=store_id,
                created_at=get_tashkent_time()
            )

            db.session.add(new_customer)
            db.session.commit()

            return redirect(url_for('customers'))

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Error adding customer: {str(e)}")
            return jsonify(
                {'error': 'Mijoz qo\'shishda xatolik yuz berdi'}), 500

    # GET so'rovi uchun - forma ko'rsatish
    stores = Store.query.all()
    return render_template(
        'add_customer.html',
        stores=stores,
        page_title='Mijoz qo\'shish',
        icon='ğŸ‘¥')


@app.route('/products')
def products_list():
    return render_template('products.html')


@app.route('/print-barcode')
def print_barcode():
    """Barcode chop etish sahifasi"""
    return render_template('barcode_print.html')


@app.route('/transfer')
@role_required('admin', 'kassir', 'sotuvchi')
def transfer():
    return render_template('transfer1.html')


@app.route('/transfer_old')
@role_required('admin', 'kassir', 'sotuvchi')
def transfer_old():
    return render_template('transfer.html')


@app.route('/return-product')
@role_required('admin', 'kassir', 'sotuvchi')
def return_product():
    """Mahsulotni qaytarish sahifasi"""
    return render_template('return_product.html')


@app.route('/api/returned-products-history', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_returned_products_history():
    """Qaytarilgan mahsulotlar tarixi"""
    try:
        # OperationHistory dan 'return' tipidagi operatsiyalarni olish
        returned_operations = OperationHistory.query.filter_by(
            operation_type='return'
        ).order_by(OperationHistory.created_at.desc()).limit(100).all()

        history = []
        for op in returned_operations:
            # new_data dan mahsulot ma'lumotlarini olish
            new_data = op.new_data or {}
            product_name = new_data.get('product_name', 'Noma\'lum')
            returned_qty = new_data.get('returned_quantity', 0)
            sale_id = new_data.get('sale_id', op.record_id)

            # Location ma'lumotini formatlash
            location_info = op.location_name or 'Noma\'lum'
            if op.location_type:
                location_type_uz = 'Do\'kon' if op.location_type == 'store' else 'Ombor'
                location_info = f"{location_type_uz}: {location_info}"

            # USD va UZS summalarini olish
            amount_usd = float(new_data.get('amount_usd', 0)) if new_data.get('amount_usd') else 0
            amount_uzs = float(op.amount) if op.amount else 0

            history.append({
                'id': op.id,
                'date': op.created_at.strftime('%d/%m/%y %H:%M'),
                'sale_id': sale_id,
                'product_name': product_name,
                'quantity': returned_qty,
                'location': location_info,
                'user': op.username or 'Noma\'lum',
                'description': op.description,
                'amount_usd': amount_usd,
                'amount_uzs': amount_uzs
            })

        return jsonify({'success': True, 'history': history})

    except Exception as e:
        logger.error(f"Qaytarilgan mahsulotlar tarixini olishda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/return-product', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_return_product():
    """Mahsulotni qaytarish API"""
    try:
        logger.info("=== RETURN PRODUCT API called ===")
        data = request.json
        logger.info(f"Request data: {data}")

        sale_id = data.get('sale_id')
        items = data.get('items', [])
        location_id = data.get('location_id')
        location_type = data.get('location_type')

        logger.info(f"sale_id={sale_id}, items count={len(items)}, location_id={location_id}, location_type={location_type}")

        if not sale_id or not items:
            return jsonify({'success': False, 'error': 'Savdo ID va mahsulotlar talab qilinadi'}), 400

        # Savdoni tekshirish
        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({'success': False, 'error': 'Savdo topilmadi'}), 404

        # Har bir mahsulotni qaytarish
        returned_items = []
        total_returned_usd = Decimal('0')
        total_returned_cost = Decimal('0')
        total_returned_profit = Decimal('0')

        for item in items:
            product_id = item.get('product_id')
            # Frontend'dan 'quantity' yoki 'return_quantity' kelishi mumkin
            return_quantity = item.get('return_quantity') or item.get('quantity', 0)

            logger.info(f"ğŸ“¦ Item: product_id={product_id}, return_quantity={return_quantity}, type={type(return_quantity)}")

            # Decimal ga o'tkazish
            try:
                return_quantity = Decimal(str(return_quantity))
            except BaseException:
                logger.error(f"âŒ return_quantity Decimal'ga aylantirib bo'lmadi: {return_quantity}")
                continue

            if return_quantity <= 0:
                logger.warning(f"âš ï¸ return_quantity <= 0: {return_quantity}, o'tkazib yuborildi")
                continue

            # Mahsulotni topish
            product = Product.query.get(product_id)
            if not product:
                logger.warning(f"Mahsulot topilmadi: {product_id}")
                continue

            # Savdodagi bu mahsulotni topish
            sale_item = SaleItem.query.filter_by(
                sale_id=sale_id,
                product_id=product_id
            ).first()

            if not sale_item:
                logger.warning(f"Bu mahsulot bu savdoda yo'q: {product_id} (Savdo #{sale_id})")
                continue

            # Qaytariladigan miqdor savdodagi miqdordan ko'p bo'lmasligi kerak
            if return_quantity > sale_item.quantity:
                logger.warning(f"Qaytariladigan miqdor ({return_quantity}) savdodagi miqdordan ({sale_item.quantity}) ko'p")
                return_quantity = sale_item.quantity

            # SaleItem dan miqdorni kamaytirish
            old_quantity = sale_item.quantity
            sale_item.quantity -= return_quantity

            # Qaytariladigan summa, xarajat va foyda (USD da)
            # SaleItem da total qiymatlar saqlanadi, shuning uchun unit qiymatni hisoblash kerak
            returned_usd = sale_item.unit_price * Decimal(str(return_quantity))
            returned_cost = Decimal(str(sale_item.cost_price or 0)) * Decimal(str(return_quantity))

            # Foyda = total_profit dan proporsional qismini olish
            if old_quantity > 0:
                unit_profit = Decimal(str(sale_item.profit or 0)) / Decimal(str(old_quantity))
                returned_profit = unit_profit * Decimal(str(return_quantity))
            else:
                returned_profit = Decimal('0')

            total_returned_usd += returned_usd
            total_returned_cost += returned_cost
            total_returned_profit += returned_profit

            logger.info(f"Qaytarildi: {product.name} x{return_quantity} = ${returned_usd} (foyda: ${returned_profit})")

            # Agar miqdor 0 bo'lsa, SaleItem ni o'chirish
            if sale_item.quantity <= 0:
                logger.info(f"SaleItem #{sale_item.id} o'chirildi (miqdor 0 bo'ldi)")
                db.session.delete(sale_item)
            else:
                # Total price ni yangilash
                sale_item.total_price = sale_item.unit_price * Decimal(str(sale_item.quantity))

            # Stock ga qaytarish
            if location_type == 'store':
                stock = StoreStock.query.filter_by(
                    store_id=location_id,
                    product_id=product_id
                ).first()

                if stock:
                    stock.quantity += return_quantity
                    logger.info(f"Do'kon stock yangilandi: {product.name} +{return_quantity} = {stock.quantity}")
                else:
                    # Agar stock yo'q bo'lsa, yangi yaratish
                    new_stock = StoreStock(
                        store_id=location_id,
                        product_id=product_id,
                        quantity=return_quantity
                    )
                    db.session.add(new_stock)
                    logger.info(f"Yangi do'kon stock yaratildi: {product.name} = {return_quantity}")

            elif location_type == 'warehouse':
                stock = WarehouseStock.query.filter_by(
                    warehouse_id=location_id,
                    product_id=product_id
                ).first()

                if stock:
                    stock.quantity += return_quantity
                    logger.info(f"Ombor stock yangilandi: {product.name} +{return_quantity} = {stock.quantity}")
                else:
                    # Agar stock yo'q bo'lsa, yangi yaratish
                    new_stock = WarehouseStock(
                        warehouse_id=location_id,
                        product_id=product_id,
                        quantity=return_quantity
                    )
                    db.session.add(new_stock)
                    logger.info(f"Yangi ombor stock yaratildi: {product.name} = {return_quantity}")

            returned_items.append({
                'product_name': product.name,
                'quantity': float(return_quantity),
                'old_quantity': float(old_quantity),
                'new_quantity': float(sale_item.quantity) if sale_item.quantity > 0 else 0,
                'returned_usd': float(returned_usd)
            })

            # Amaliyotlar tarixiga yozish (har bir mahsulot uchun)
            location_name = None
            if location_type == 'store':
                store = Store.query.get(location_id)
                location_name = store.name if store else 'Noma\'lum do\'kon'
            elif location_type == 'warehouse':
                warehouse = Warehouse.query.get(location_id)
                location_name = warehouse.name if warehouse else 'Noma\'lum ombor'

            operation = OperationHistory(
                operation_type='return',
                table_name='sale_items',
                record_id=sale_id,
                user_id=session.get('user_id'),
                username=session.get('username'),
                description=f"Qaytarildi: {product.name} - {return_quantity} dona (Savdo #{sale_id})",
                old_data={
                    'quantity': float(old_quantity),
                    'total_price': float(sale_item.unit_price * Decimal(str(old_quantity)))
                },
                new_data={
                    'product_id': product_id,
                    'product_name': product.name,
                    'quantity': float(sale_item.quantity) if sale_item.quantity > 0 else 0,
                    'returned_quantity': float(return_quantity),
                    'sale_id': sale_id,
                    'amount_usd': float(returned_usd)
                },
                ip_address=request.remote_addr,
                location_id=location_id,
                location_type=location_type,
                location_name=location_name,
                amount=float(returned_usd * sale.currency_rate)  # Amount UZS da saqlanadi
            )
            db.session.add(operation)

        # Sale jami summasini yangilash (USD da)
        if total_returned_usd > 0:
            sale.total_amount -= total_returned_usd
            sale.total_cost -= total_returned_cost
            sale.total_profit -= total_returned_profit

            logger.info(f"Savdo #{sale_id} yangilandi:")
            logger.info(f"  - Summa: -${total_returned_usd}")
            logger.info(f"  - Xarajat: -${total_returned_cost}")
            logger.info(f"  - Foyda: -${total_returned_profit}")

            # To'lovlarni avtomatik qaytarish (Smart Logic: avval qarz, keyin naqd, click, terminal)
            remaining_refund = total_returned_usd
            refunded_payments = []

            # 1. AVVAL qarzdan qaytarish (agar qarz mavjud bo'lsa)
            if sale.debt_usd and sale.debt_usd > 0 and remaining_refund > 0:
                debt_refund = min(Decimal(str(sale.debt_usd)), remaining_refund)
                sale.debt_usd = float(Decimal(str(sale.debt_usd)) - debt_refund)
                remaining_refund -= debt_refund
                refunded_payments.append(('debt', float(debt_refund)))
                logger.info(f"  ğŸ“ Qarzdan kamaytirildi: ${debt_refund} (Qolgan qarz: ${sale.debt_usd})")

            # 2. Naqddan qaytarish
            if sale.cash_usd and sale.cash_usd > 0 and remaining_refund > 0:
                cash_refund = min(Decimal(str(sale.cash_usd)), remaining_refund)
                sale.cash_usd = float(Decimal(str(sale.cash_usd)) - cash_refund)
                remaining_refund -= cash_refund
                refunded_payments.append(('cash', float(cash_refund)))
                logger.info(f"  ğŸ’µ Naqd puldan qaytarildi: ${cash_refund}")

            # 3. Click dan qaytarish
            if sale.click_usd and sale.click_usd > 0 and remaining_refund > 0:
                click_refund = min(Decimal(str(sale.click_usd)), remaining_refund)
                sale.click_usd = float(Decimal(str(sale.click_usd)) - click_refund)
                remaining_refund -= click_refund
                refunded_payments.append(('click', float(click_refund)))
                logger.info(f"  ğŸ“± Click dan qaytarildi: ${click_refund}")

            # 4. Terminal dan qaytarish
            if sale.terminal_usd and sale.terminal_usd > 0 and remaining_refund > 0:
                terminal_refund = min(Decimal(str(sale.terminal_usd)), remaining_refund)
                sale.terminal_usd = float(Decimal(str(sale.terminal_usd)) - terminal_refund)
                remaining_refund -= terminal_refund
                refunded_payments.append(('terminal', float(terminal_refund)))
                logger.info(f"  ğŸ’³ Terminal dan qaytarildi: ${terminal_refund}")

            # 5. Agar hali ham qolsa, qarzga qo'shish (manfiy qarz - endi do'kon mijozga qarzdor)
            if remaining_refund > 0:
                sale.debt_usd = float(Decimal(str(sale.debt_usd or 0)) + remaining_refund)
                logger.info(f"  ğŸ“ Qarzga qo'shildi: ${remaining_refund} (Jami qarz: ${sale.debt_usd})")

            # Qaytarilgan to'lovlarni operation_history ga yozish
            for payment_type, refund_amount in refunded_payments:
                refund_operation = OperationHistory(
                    operation_type='payment_refund',
                    table_name='sales',
                    record_id=sale_id,
                    user_id=session.get('user_id'),
                    username=session.get('username'),
                    description=f"To'lov qaytarildi: {payment_type.upper()} - ${refund_amount:.2f}",
                    old_data=None,
                    new_data={
                        'sale_id': sale_id,
                        'payment_type': payment_type,
                        'refund_amount_usd': refund_amount,
                        'refund_amount_uzs': float(Decimal(str(refund_amount)) * sale.currency_rate)
                    },
                    ip_address=request.remote_addr,
                    location_id=location_id,
                    location_type=location_type,
                    location_name=location_name,
                    amount=-float(Decimal(str(refund_amount)) * sale.currency_rate)
                )
                db.session.add(refund_operation)

            logger.info(f"Mahsulot qaytarildi: {len(returned_items)} ta")

            # Payment_status ni yangilash
            current_debt = Decimal(str(sale.debt_usd or 0))
            if current_debt <= 0:
                # Qarz 0 ga tushgan yoki manfiy bo'lgan (to'liq to'langan)
                sale.payment_status = 'paid'
                logger.info(f"âœ… Qarz to'liq qaytarildi (${current_debt}), payment_status='paid' qilindi")

        # Agar sale'da mahsulot qolmasa, savdoni bekor qilish
        remaining_items = SaleItem.query.filter_by(sale_id=sale_id).count()
        if remaining_items == 0:
            logger.info(f"Savdo #{sale_id} butunlay qaytarildi, payment_status='cancelled' qilindi")
            sale.payment_status = 'cancelled'

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'{len(returned_items)} ta mahsulot qaytarildi',
            'returned_items': returned_items
        })

    except Exception as e:
        db.session.rollback()
        import traceback
        error_traceback = traceback.format_exc()
        logger.error(f"Mahsulot qaytarishda xatolik: {str(e)}")
        logger.error(f"Traceback: {error_traceback}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales-by-product/<int:product_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def api_sales_by_product(product_id):
    """Mahsulot bo'yicha savdolarni topish"""
    try:
        logger.info(f"Mahsulot bo'yicha savdolar qidirilmoqda: {product_id}")

        # Mahsulotni tekshirish
        product = Product.query.get(product_id)
        if not product:
            logger.warning(f"Mahsulot topilmadi: {product_id}")
            return jsonify({'success': False, 'error': 'Mahsulot topilmadi'}), 404

        logger.info(f"Mahsulot topildi: {product.name}")

        # Bu mahsulot bor savdolarni topish (oxirgi 50 ta)
        sales = db.session.query(Sale).join(SaleItem).filter(
            SaleItem.product_id == product_id,
            Sale.payment_status.in_(['paid', 'partial', 'debt'])  # Faqat to'langan yoki qarzda savdolar
        ).order_by(Sale.created_at.desc()).limit(50).all()

        logger.info(f"Topilgan savdolar soni: {len(sales)}")

        sales_list = []
        for sale in sales:
            try:
                # Bu savdodagi shu mahsulotni topish
                sale_item = SaleItem.query.filter_by(
                    sale_id=sale.id,
                    product_id=product_id
                ).first()

                if sale_item:
                    # Customer name'ni xavfsiz olish
                    customer_name = 'Noma\'lum'
                    try:
                        if sale.customer:
                            customer_name = sale.customer.name
                    except Exception as ce:
                        logger.warning(f"Customer ma'lumotini olishda xatolik: {str(ce)}")

                    # Savdodagi jami mahsulot turlarini hisoblash
                    total_items_count = SaleItem.query.filter_by(sale_id=sale.id).count()

                    # To'lov ma'lumotlarini qo'shish
                    total_amount = float(sale.total_amount or 0)
                    debt_usd = float(sale.debt_usd or 0)
                    cash_usd = float(sale.cash_usd or 0)
                    click_usd = float(sale.click_usd or 0)
                    terminal_usd = float(sale.terminal_usd or 0)

                    sales_list.append({
                        'id': sale.id,
                        'customer_name': customer_name,
                        'created_at': sale.created_at.isoformat() if sale.created_at else None,
                        'payment_status': sale.payment_status,
                        'location_id': sale.location_id,
                        'location_type': sale.location_type,
                        'product_quantity': sale_item.quantity,
                        'product_price': float(sale_item.unit_price),
                        'total_usd': float(sale_item.total_price),
                        'total_items': total_items_count,
                        'total_amount': total_amount,
                        'debt_usd': debt_usd,
                        'cash_usd': cash_usd,
                        'click_usd': click_usd,
                        'terminal_usd': terminal_usd
                    })
            except Exception as se:
                logger.error(f"Savdo {sale.id} ni qayta ishlashda xatolik: {str(se)}")
                continue

        logger.info(f"Qaytariladigan savdolar soni: {len(sales_list)}")

        return jsonify({
            'success': True,
            'product': {
                'id': product.id,
                'name': product.name,
                'barcode': product.barcode
            },
            'sales': sales_list
        })

    except Exception as e:
        import traceback
        logger.error(f"Mahsulot bo'yicha savdolarni topishda xatolik: {str(e)}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/operations-history')
@role_required('admin', 'kassir')
def operations_history():
    """Amaliyotlar tarixi sahifasi"""
    return render_template('operations_history.html')


@app.route('/api/operations-history')
@role_required('admin', 'kassir')
def api_operations_history():
    """Amaliyotlar tarixini olish API"""
    try:
        # Filterlar
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        operation_type = request.args.get('operation_type')
        user_id = request.args.get('user_id', type=int)
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        # Query yaratish
        query = OperationHistory.query

        # Filterlarni qo'llash
        if start_date:
            query = query.filter(OperationHistory.created_at >= start_date)
        if end_date:
            # End date'ga 1 kun qo'shish (oxirgi kunni ham qamrab olish uchun)
            end_datetime = datetime.strptime(end_date, '%Y-%m-%d') + timedelta(days=1)
            query = query.filter(OperationHistory.created_at < end_datetime)
        if operation_type:
            query = query.filter(OperationHistory.operation_type == operation_type)
        if user_id:
            query = query.filter(OperationHistory.user_id == user_id)

        # Pagination
        query = query.order_by(OperationHistory.created_at.desc())
        paginated = query.paginate(page=page, per_page=per_page, error_out=False)

        # Ma'lumotlarni formatlash
        operations = []
        for op in paginated.items:
            operations.append({
                'id': op.id,
                'operation_type': op.operation_type,
                'table_name': op.table_name,
                'record_id': op.record_id,
                'username': op.username,
                'description': op.description,
                'location_name': op.location_name,
                'amount': float(op.amount) if op.amount else None,
                'created_at': op.created_at.strftime('%Y-%m-%d %H:%M:%S') if op.created_at else None,
                'old_data': op.old_data,
                'new_data': op.new_data
            })

        return jsonify({
            'success': True,
            'operations': operations,
            'total': paginated.total,
            'pages': paginated.pages,
            'current_page': page
        })

    except Exception as e:
        logger.error(f"Operations history API xatosi: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/check_stock')
@role_required('admin', 'kassir', 'sotuvchi')
def check_stock():
    """Qoldiqni tekshirish sahifasi"""
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('login'))

    # Admin uchun avtomatik ruxsat
    if current_user.role != 'admin':
        # Stock check huquqini tekshirish
        permissions = current_user.permissions or {}
        if not permissions.get('stock_check', False):
            logger.error(f" User {current_user.username} (role: {current_user.role}) tried to access stock check without permission")
            logger.debug(f" User permissions: {permissions}")
            # Temporary: Kassir va eski sotuvchilarga ham ruxsat berish (migration uchun)
            if current_user.role == 'kassir':
                logger.info(f" Allowing kassir {current_user.username} temporary access to stock check")
            else:
                abort(403)  # Qoldiqni tekshirish huquqi yo'q

        # Sotuvchi uchun qo'shimcha sozlamani tekshirish
        if current_user.role == 'sotuvchi':
            setting = Settings.query.filter_by(key='stock_check_visible').first()
            if setting and setting.value.lower() == 'false':
                abort(403)  # Sahifa yashirilgan

    return render_template('check_stock.html')


@app.route('/api/check_stock_locations')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_locations():
    """Qoldiqni tekshirish uchun ruxsat etilgan joylashuvlarni qaytarish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        logger.debug(f"ğŸ” Check Stock Locations - User: {current_user.username}, Role: {current_user.role}")

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role == 'admin':
            # Admin barcha joylashuvlarni ko'radi
            allowed_store_ids = None
            allowed_warehouse_ids = None
            logger.debug("âœ… Admin user - showing all stock check locations")
        else:
            # Oddiy foydalanuvchilar faqat allowed_locations dan ruxsat etilgan joylashuvlarni ko'radi
            allowed_locations = current_user.allowed_locations or []
            logger.debug(f"ğŸ“ User allowed_locations: {allowed_locations}")

            # Helper funksiya bilan ID'larni olish (eski va yangi formatlar uchun)
            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            allowed_warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')

            logger.debug(f"ğŸª Filtered store IDs: {allowed_store_ids}")
            logger.debug(f"ğŸ­ Filtered warehouse IDs: {allowed_warehouse_ids}")

        # Do'konlarni olish - faqat ruxsat etilganlar
        if allowed_store_ids is None:
            stores = Store.query.all()
        else:
            stores = Store.query.filter(Store.id.in_(allowed_store_ids)).all() if allowed_store_ids else []

        stores_data = [{'id': s.id, 'name': s.name} for s in stores]
        logger.debug(f"ğŸª Stores to return: {len(stores_data)}")

        # Omborlarni olish - faqat ruxsat etilganlar
        if allowed_warehouse_ids is None:
            warehouses = Warehouse.query.all()
        else:
            warehouses = Warehouse.query.filter(Warehouse.id.in_(allowed_warehouse_ids)).all() if allowed_warehouse_ids else []

        warehouses_data = [{'id': w.id, 'name': w.name} for w in warehouses]
        logger.debug(f"ğŸ­ Warehouses to return: {len(warehouses_data)}")

        # Faol tekshiruvlar bor joylashuvlarni olish
        active_sessions = StockCheckSession.query.filter_by(status='active').all()
        active_locations = []
        for check_session in active_sessions:
            active_locations.append({
                'type': check_session.location_type,
                'id': check_session.location_id
            })

        return jsonify({
            'success': True,
            'stores': stores_data,
            'warehouses': warehouses_data,
            'active_locations': active_locations
        })
    except Exception as e:
        logger.error(f"Error loading locations: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/check_stock/active_sessions')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_active_sessions():
    """Joriy (active) tekshiruv sessiyalarini olish - faqat ruxsat etilgan joylashuvlar"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        logger.debug(f"ğŸ” Active Sessions - User: {current_user.username}, Role: {current_user.role}")

        # Faol sessiyalarni olish
        sessions = StockCheckSession.query.filter_by(status='active').order_by(StockCheckSession.started_at.desc()).all()

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role != 'admin':
            # Oddiy foydalanuvchilar faqat ruxsat etilgan joylashuvlardagi sessiyalarni ko'radi
            allowed_locations = current_user.allowed_locations or []
            logger.debug(f"ğŸ“ User allowed_locations: {allowed_locations}")

            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            allowed_warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')

            logger.debug(f"ğŸª Allowed store IDs: {allowed_store_ids}")
            logger.debug(f"ğŸ­ Allowed warehouse IDs: {allowed_warehouse_ids}")

            # Sessiyalarni filterlash
            filtered_sessions = []
            for check_session in sessions:
                if check_session.location_type == 'store' and check_session.location_id in allowed_store_ids:
                    filtered_sessions.append(check_session)
                elif check_session.location_type == 'warehouse' and check_session.location_id in allowed_warehouse_ids:
                    filtered_sessions.append(check_session)

            sessions = filtered_sessions
            logger.debug(f"âœ… Filtered sessions count: {len(sessions)}")
        else:
            logger.debug("âœ… Admin user - showing all active sessions")

        sessions_data = []
        for check_session in sessions:
            # Tekshirilgan mahsulotlar sonini olish
            checked_items_count = StockCheckItem.query.filter_by(session_id=check_session.id).count()

            # Jami mahsulotlar sonini olish (location_type va location_id ga qarab)
            if check_session.location_type == 'warehouse':
                total_products = WarehouseStock.query.filter(
                    WarehouseStock.warehouse_id == check_session.location_id,
                    WarehouseStock.quantity > 0
                ).count()
            else:  # store
                total_products = StoreStock.query.filter(
                    StoreStock.store_id == check_session.location_id,
                    StoreStock.quantity > 0
                ).count()

            # Progress foizini hisoblash
            progress_percent = 0
            if total_products > 0:
                progress_percent = round((checked_items_count / total_products) * 100, 1)

            sessions_data.append({
                'id': check_session.id,
                'location_name': check_session.location_name,
                'location_type': check_session.location_type,
                'user_name': f"{check_session.user.first_name} {check_session.user.last_name}" if check_session.user else 'N/A',
                'started_at': check_session.started_at.strftime('%d.%m.%Y %H:%M') if check_session.started_at else '',
                'updated_at': check_session.updated_at.strftime('%d.%m.%Y %H:%M') if check_session.updated_at else '',
                'checked_items': checked_items_count,
                'total_products': total_products,
                'progress_percent': progress_percent
            })

        return jsonify({
            'success': True,
            'sessions': sessions_data
        })
    except Exception as e:
        logger.error(f"Error loading active sessions: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/completed_sessions')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_completed_sessions():
    """Tugatilgan tekshiruv sessiyalarini olish - faqat ruxsat etilgan joylashuvlar"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        # Pagination parametrlari
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)

        logger.debug(f"ğŸ” Completed Sessions - User: {current_user.username}, Role: {current_user.role}")

        # Base query
        query = StockCheckSession.query.filter_by(status='completed')

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role != 'admin':
            # Oddiy foydalanuvchilar faqat ruxsat etilgan joylashuvlardagi sessiyalarni ko'radi
            allowed_locations = current_user.allowed_locations or []
            logger.debug(f"ğŸ“ User allowed_locations: {allowed_locations}")

            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            allowed_warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')

            logger.debug(f"ğŸª Allowed store IDs: {allowed_store_ids}")
            logger.debug(f"ğŸ­ Allowed warehouse IDs: {allowed_warehouse_ids}")

            # Filterlash - faqat ruxsat etilgan joylashuvlar
            from sqlalchemy import or_, and_
            filters = []
            if allowed_store_ids:
                filters.append(and_(StockCheckSession.location_type == 'store',
                                    StockCheckSession.location_id.in_(allowed_store_ids)))
            if allowed_warehouse_ids:
                filters.append(and_(StockCheckSession.location_type == 'warehouse',
                                    StockCheckSession.location_id.in_(allowed_warehouse_ids)))

            if filters:
                query = query.filter(or_(*filters))
            else:
                # Agar hech qanday ruxsat yo'q bo'lsa, bo'sh natija
                query = query.filter(StockCheckSession.id == -1)

            logger.debug("âœ… Query filtered for non-admin user")
        else:
            logger.debug("âœ… Admin user - showing all completed sessions")

        # Tugatilgan sessiyalarni olish (pagination bilan)
        pagination = query.order_by(StockCheckSession.updated_at.desc()).paginate(
            page=page, per_page=per_page, error_out=False
        )

        sessions_data = []
        for check_session in pagination.items:
            # Tekshirilgan mahsulotlar sonini olish
            items_count = StockCheckItem.query.filter_by(session_id=check_session.id).count()

            sessions_data.append({
                'id': session.id,
                'location_name': session.location_name,
                'location_type': session.location_type,
                'started_user_name': f"{session.user.first_name} {session.user.last_name}" if session.user else 'N/A',
                'completed_user_name': f"{session.completed_by.first_name} {session.completed_by.last_name}" if session.completed_by else (f"{session.user.first_name} {session.user.last_name}" if session.user else 'N/A'),
                'started_at': session.started_at.strftime('%d.%m.%Y %H:%M') if session.started_at else '',
                'updated_at': session.updated_at.strftime('%d.%m.%Y %H:%M') if session.updated_at else '',
                'items_count': items_count
            })

        return jsonify({
            'success': True,
            'sessions': sessions_data,
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'pages': pagination.pages,
                'has_prev': pagination.has_prev,
                'has_next': pagination.has_next
            }
        })
    except Exception as e:
        logger.error(f"Error loading completed sessions: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/start', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_start_check_stock():
    """Yangi tekshiruv sessiyasini boshlash"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        data = request.get_json()
        location_type = data.get('location_type')  # 'store' yoki 'warehouse'
        location_id = data.get('location_id')

        if not location_type or not location_id:
            return jsonify({'success': False, 'message': 'Joylashuv ma\'lumotlari to\'liq emas'}), 400

        # Joylashuv nomini olish
        location_name = ''
        if location_type == 'store':
            store = Store.query.get(location_id)
            location_name = store.name if store else f'Do\'kon #{location_id}'
        else:
            warehouse = Warehouse.query.get(location_id)
            location_name = warehouse.name if warehouse else f'Ombor #{location_id}'

        # Sessiyani database'ga saqlash
        session = StockCheckSession(
            user_id=current_user.id,
            location_id=location_id,
            location_type=location_type,
            location_name=location_name,
            status='active'
        )
        db.session.add(session)
        db.session.commit()

        return jsonify({
            'success': True,
            'session_id': session.id,
            'location_type': location_type,
            'location_id': location_id
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error starting check stock: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/check_stock/session/<int:session_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def check_stock_session(session_id):
    """Tekshiruv sessiyasi sahifasi"""
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('login'))

    # Database'dan sessiyani olish
    try:
        session = StockCheckSession.query.get(session_id)
        if not session:
            flash('Tekshiruv sessiyasi topilmadi', 'error')
            return redirect(url_for('check_stock'))

        location_type = session.location_type
        location_id = session.location_id
        location_name = session.location_name

        return render_template('check_stock_session.html',
                               session_id=session_id,
                               location_type=location_type,
                               location_id=location_id,
                               location_name=location_name)
    except Exception as e:
        logger.error(f"Error loading check stock session: {e}")
        abort(404)


@app.route('/check_stock/view/<int:session_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def check_stock_view(session_id):
    """Tugatilgan tekshiruv tafsilotlarini ko'rish"""
    current_user = get_current_user()
    if not current_user:
        return redirect(url_for('login'))

    try:
        check_session = StockCheckSession.query.get(session_id)
        if not check_session:
            flash('Tekshiruv sessiyasi topilmadi', 'error')
            return redirect(url_for('check_stock'))

        return render_template('check_stock_view.html', check_session=check_session)
    except Exception as e:
        logger.error(f"Error loading check stock view: {e}")
        abort(404)


@app.route('/api/check_stock/search')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_search():
    """Mahsulotlarni qidirish (nom yoki barkod bo'yicha)"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        query = request.args.get('query', '').strip()
        location_type = request.args.get('location_type')
        location_id = int(request.args.get('location_id'))

        if not query or not location_type or not location_id:
            return jsonify({'success': False, 'message': 'Qidiruv parametrlari to\'liq emas'}), 400

        # Mahsulotlarni qidirish (nom yoki barkod bo'yicha)
        products = Product.query.filter(
            db.or_(
                Product.name.ilike(f'%{query}%'),
                Product.barcode.ilike(f'%{query}%')
            )
        ).limit(50).all()

        products_data = []
        for product in products:
            # Joylashuvdagi qoldiqni olish
            if location_type == 'store':
                stock = StoreStock.query.filter_by(
                    product_id=product.id,
                    store_id=location_id
                ).first()
            else:
                stock = WarehouseStock.query.filter_by(
                    product_id=product.id,
                    warehouse_id=location_id
                ).first()

            system_quantity = stock.quantity if stock else 0

            products_data.append({
                'id': product.id,
                'name': product.name,
                'barcode': product.barcode,
                'system_quantity': float(system_quantity)
            })

        return jsonify({
            'success': True,
            'products': products_data
        })
    except Exception as e:
        logger.error(f"Error searching products: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/products')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_products():
    """Joylashuvdagi barcha mahsulotlarni olish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        location_type = request.args.get('location_type')
        location_id = int(request.args.get('location_id'))

        if not location_type or not location_id:
            return jsonify({'success': False, 'message': 'Joylashuv parametrlari to\'liq emas'}), 400

        # Joylashuvdagi barcha mahsulotlarni olish (qoldiq 0 bo'lganlarni ham)
        if location_type == 'store':
            # JOIN qilib bir marta query - N+1 problem hal qilindi
            stocks = db.session.query(StoreStock, Product)\
                .join(Product, StoreStock.product_id == Product.id)\
                .filter(StoreStock.store_id == location_id)\
                .all()
        else:
            stocks = db.session.query(WarehouseStock, Product)\
                .join(Product, WarehouseStock.product_id == Product.id)\
                .filter(WarehouseStock.warehouse_id == location_id)\
                .all()

        products_data = []
        for stock, product in stocks:
            products_data.append({
                'id': product.id,
                'name': product.name,
                'barcode': product.barcode,
                'price': float(product.sell_price) if product.sell_price else 0,
                'system_quantity': float(stock.quantity)
            })

        return jsonify({
            'success': True,
            'products': products_data
        })
    except Exception as e:
        logger.error(f"Error loading products: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/add_item', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_add_item():
    """Tekshirilgan mahsulotni saqlash - yaxshilangan error handling bilan"""
    start_time = time.time()

    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        data = request.get_json()
        if not data:
            return jsonify({
                'success': False,
                'message': 'Ma\'lumot yuborilmagan',
                'error_type': 'validation'
            }), 400

        session_id = data.get('session_id')
        product_id = data.get('product_id')
        product_name = data.get('product_name')
        system_quantity = data.get('system_quantity')
        actual_quantity = data.get('actual_quantity')
        difference = data.get('difference')
        status = data.get('status')

        if not session_id or not product_id:
            return jsonify({
                'success': False,
                'message': 'Ma\'lumotlar to\'liq emas',
                'error_type': 'validation'
            }), 400

        # Database operations
        try:
            # Allaqachon tekshirilganmi?
            existing = StockCheckItem.query.filter_by(session_id=session_id, product_id=product_id).first()
            if existing:
                # Yangilash
                existing.actual_quantity = actual_quantity
                existing.difference = difference
                existing.status = status
                db.session.commit()
                return jsonify({
                    'success': True,
                    'item': existing.to_dict(),
                    'message': 'Mahsulot ma\'lumoti yangilandi',
                    'updated': True
                })

            # Yangi mahsulot qo'shish
            item = StockCheckItem(
                session_id=session_id,
                product_id=product_id,
                product_name=product_name,
                system_quantity=system_quantity,
                actual_quantity=actual_quantity,
                difference=difference,
                status=status
            )
            db.session.add(item)
            db.session.commit()

            # Session updated_at ni yangilash
            session = StockCheckSession.query.get(session_id)
            if session:
                session.updated_at = db.func.current_timestamp()
                db.session.commit()

            duration = time.time() - start_time
            if duration > 5:
                logger.warning(f"âš ï¸ Slow query: {request.path} - {duration:.2f}s")

            return jsonify({'success': True, 'item': item.to_dict()})

        except TimeoutError:
            db.session.rollback()
            duration = time.time() - start_time
            logger.error(f"â±ï¸ Database timeout: {duration:.2f}s")
            return jsonify({
                'success': False,
                'message': 'So\'rov juda uzoq davom etdi',
                'error_type': 'timeout'
            }), 504
        except OperationalError as e:
            db.session.rollback()
            logger.error(f"ğŸ”Œ Database connection xatosi: {e}")
            return jsonify({
                'success': False,
                'message': 'Ma\'lumotlar bazasiga ulanishda xatolik',
                'error_type': 'database_connection'
            }), 503
        except IntegrityError as e:
            db.session.rollback()
            logger.error(f"âŒ Integrity error: {e}")
            return jsonify({
                'success': False,
                'message': 'Ma\'lumotlarni saqlashda xatolik (dublikat yoki bog\'liqlik)',
                'error_type': 'integrity_error'
            }), 400

    except BadRequest as e:
        logger.error(f"âŒ Bad request: {e}")
        return jsonify({
            'success': False,
            'message': 'Noto\'g\'ri so\'rov formati',
            'error_type': 'bad_request'
        }), 400
    except Exception as e:
        db.session.rollback()
        duration = time.time() - start_time
        logger.error(f"âŒ Xato adding check item ({duration:.2f}s): {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'message': 'Kutilmagan server xatosi',
            'error_type': 'internal_server_error'
        }), 500


@app.route('/api/check_stock/items/<int:session_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_items(session_id):
    """Session'dagi barcha tekshirilgan mahsulotlarni olish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        items = StockCheckItem.query.filter_by(session_id=session_id).all()

        items_data = []
        for item in items:
            product = Product.query.get(item.product_id)
            items_data.append({
                'id': item.product_id,
                'name': item.product_name,
                'barcode': product.barcode if product else '',
                'price': float(product.sell_price) if product and product.sell_price else 0,
                'system_quantity': float(item.system_quantity),
                'actual_quantity': float(item.actual_quantity),
                'difference': float(item.difference) if item.difference else 0,
                'status': item.status
            })

        return jsonify({'success': True, 'items': items_data})
    except Exception as e:
        logger.error(f"Error loading check items: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/remove_item', methods=['DELETE'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_remove_item():
    """Tekshirilgan mahsulotni o'chirish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        data = request.get_json()
        session_id = data.get('session_id')
        product_id = data.get('product_id')

        if not session_id or not product_id:
            return jsonify({'success': False, 'message': 'Ma\'lumotlar to\'liq emas'}), 400

        item = StockCheckItem.query.filter_by(session_id=session_id, product_id=product_id).first()
        if item:
            db.session.delete(item)
            db.session.commit()

        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error removing check item: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/finish', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_finish():
    """Tekshiruvni yakunlash va tizim miqdorlarini haqiqiy miqdorlar bilan yangilash"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        data = request.get_json()
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'success': False, 'message': 'Session ID topilmadi'}), 400

        # Sessiyani topish
        session = StockCheckSession.query.get(session_id)
        if not session:
            return jsonify({'success': False, 'message': 'Sessiya topilmadi'}), 404

        # Tekshirilgan mahsulotlarni olish (barcha statuslar: kamomad, ortiqcha, normal)
        # actual_quantity NULL bo'lmagan barcha mahsulotlar
        checked_items = StockCheckItem.query.filter(
            StockCheckItem.session_id == session_id,
            StockCheckItem.actual_quantity.isnot(None)
        ).all()

        logger.info(f"ğŸ” Found {len(checked_items)} items with actual_quantity in session {session_id}")
        updated_count = 0
        errors = []

        # Har bir tekshirilgan mahsulot uchun tizim miqdorini yangilash
        for item in checked_items:
            try:
                if item.actual_quantity is not None:
                    if session.location_type == 'store':
                        # Do'kon stokini yangilash
                        stock = StoreStock.query.filter_by(
                            store_id=session.location_id,
                            product_id=item.product_id
                        ).first()

                        if stock:
                            old_qty = stock.quantity
                            stock.quantity = item.actual_quantity
                            updated_count += 1
                            logger.info(f"ğŸ“¦ Store stock updated: Product {item.product_id} ({item.product_name}), "
                                        f"Old: {old_qty}, New: {item.actual_quantity}, Diff: {item.difference}")
                        else:
                            error_msg = f"âŒ Store stock not found: store_id={session.location_id}, product_id={item.product_id}"
                            logger.error(error_msg)
                            errors.append(error_msg)

                    elif session.location_type == 'warehouse':
                        # Ombor stokini yangilash
                        stock = WarehouseStock.query.filter_by(
                            warehouse_id=session.location_id,
                            product_id=item.product_id
                        ).first()

                        if stock:
                            old_qty = stock.quantity
                            stock.quantity = item.actual_quantity
                            updated_count += 1
                            logger.info(f"ğŸ“¦ Warehouse stock updated: Product {item.product_id} ({item.product_name}), "
                                        f"Old: {old_qty}, New: {item.actual_quantity}, Diff: {item.difference}")
                        else:
                            error_msg = f"âŒ Warehouse stock not found: warehouse_id={session.location_id}, product_id={item.product_id}"
                            logger.error(error_msg)
                            errors.append(error_msg)
            except Exception as item_error:
                error_msg = f"âŒ Error updating product {item.product_id}: {str(item_error)}"
                logger.error(error_msg)
                errors.append(error_msg)

        # Sessiyani yakunlash
        session.status = 'completed'
        session.completed_by_user_id = current_user.id
        db.session.commit()

        logger.info(f"âœ… Check stock finished: session_id={session_id}, user={current_user.username}, "
                    f"updated={updated_count} products, errors={len(errors)}")

        message = f'Tekshiruv yakunlandi. {updated_count} ta mahsulot yangilandi.'
        if errors:
            message += f'\n\nâš ï¸ {len(errors)} ta xatolik:'
            for err in errors[:3]:  # Faqat birinchi 3 ta xatolikni ko'rsatish
                message += f'\n- {err}'

        return jsonify({
            'success': True,
            'message': message,
            'updated_count': updated_count,
            'errors': errors
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"âŒ Error finishing check stock: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/delete_session', methods=['DELETE'])
@role_required('admin')
def api_check_stock_delete_session():
    """Tekshiruv sessiyasini o'chirish (faqat admin)"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        data = request.get_json()
        session_id = data.get('session_id')

        if not session_id:
            return jsonify({'success': False, 'message': 'Session ID topilmadi'}), 400

        # Sessiyani topish
        session = StockCheckSession.query.get(session_id)
        if not session:
            return jsonify({'success': False, 'message': 'Sessiya topilmadi'}), 404

        # Sessiya bilan bog'liq itemlarni o'chirish
        StockCheckItem.query.filter_by(session_id=session_id).delete()

        # Sessiyani o'chirish
        db.session.delete(session)
        db.session.commit()

        logger.info(f"Check stock session deleted: session_id={session_id}, deleted_by={current_user.username}")

        return jsonify({
            'success': True,
            'message': 'Tekshiruv muvaffaqiyatli o\'chirildi'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Error deleting check stock session: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/session_items/<int:session_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_session_items(session_id):
    """Sessiya bo'yicha tekshirilgan mahsulotlarni olish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        items = StockCheckItem.query.filter_by(session_id=session_id).all()

        items_data = []
        for item in items:
            items_data.append({
                'id': item.id,
                'product_id': item.product_id,
                'product_name': item.product_name,
                'system_quantity': float(item.system_quantity),
                'actual_quantity': float(item.actual_quantity) if item.actual_quantity is not None else None,
                'difference': float(item.difference) if item.difference is not None else None,
                'status': item.status,
                'price': float(item.product.sell_price) if item.product and item.product.sell_price else 0
            })

        return jsonify({
            'success': True,
            'items': items_data
        })
    except Exception as e:
        logger.error(f"Error getting session items: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/check_stock/all_location_products')
@role_required('admin', 'kassir', 'sotuvchi')
def api_check_stock_all_location_products():
    """Joylashuvdagi barcha mahsulotlarni olish (tekshirilgan va tekshirilmagan)"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        location_type = request.args.get('location_type')
        location_id = int(request.args.get('location_id'))

        if not location_type or not location_id:
            return jsonify({'success': False, 'message': 'Joylashuv parametrlari to\'liq emas'}), 400

        products_data = []

        if location_type == 'store':
            stocks = db.session.query(StoreStock, Product)\
                .join(Product, StoreStock.product_id == Product.id)\
                .filter(StoreStock.store_id == location_id)\
                .all()

            for stock, product in stocks:
                products_data.append({
                    'id': product.id,
                    'name': product.name,
                    'barcode': product.barcode,
                    'price': float(product.sell_price) if product.sell_price else 0,
                    'system_quantity': float(stock.quantity)
                })
        else:
            stocks = db.session.query(WarehouseStock, Product)\
                .join(Product, WarehouseStock.product_id == Product.id)\
                .filter(WarehouseStock.warehouse_id == location_id)\
                .all()

            for stock, product in stocks:
                products_data.append({
                    'id': product.id,
                    'name': product.name,
                    'barcode': product.barcode,
                    'price': float(product.sell_price) if product.sell_price else 0,
                    'system_quantity': float(stock.quantity)
                })

        return jsonify({
            'success': True,
            'products': products_data
        })
    except Exception as e:
        logger.error(f"Error getting all location products: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/history_details')
def history_details():
    """Tekshiruv tarixi tafsilotlari sahifasi"""
    return render_template('history_details.html')


@app.route('/stores')
@role_required('admin', 'kassir')
def stores():
    stores_list = Store.query.all()
    return render_template('stores.html', stores=stores_list)


@app.route('/debug-stores')
def debug_stores():
    """Debug sahifasi - dokonlar o'chirish test uchun (faqat development)"""
    if not app.debug:
        abort(404)  # Production'da ko'rsatmaslik
    return render_template('debug_stores.html')


@app.route('/add_store', methods=['GET', 'POST'])
def add_store():
    if request.method == 'POST':
        try:
            name = request.form['name']
            address = request.form['address']
            manager_name = request.form['manager_name']
            phone = request.form.get('phone', '')

            new_store = Store(
                name=name,
                address=address,
                manager_name=manager_name,
                phone=phone
            )

            db.session.add(new_store)
            db.session.commit()

            # OperationHistory logini yozish
            try:
                history = OperationHistory(
                    operation_type='create_store',
                    table_name='stores',
                    record_id=new_store.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"Yangi dokon yaratildi: {name}",
                    old_data=None,
                    new_data={'name': name, 'address': address, 'manager': manager_name, 'phone': phone},
                    ip_address=request.remote_addr,
                    location_id=new_store.id,
                    location_type='store',
                    location_name=name,
                    amount=None
                )
                db.session.add(history)
                db.session.commit()
            except Exception as log_error:
                logger.error(f"OperationHistory log xatoligi: {log_error}")

            return redirect(url_for('stores'))

        except Exception as e:
            db.session.rollback()
            return f"Xatolik: {str(e)}", 400

    return render_template('add_store.html')


@app.route('/store/<int:store_id>')
def store_detail(store_id):
    """Optimized store detail view - loads only basic info, stock data loaded via AJAX"""
    store = Store.query.get_or_404(store_id)

    # Faqat asosiy statistikani hisoblash (tezkor query)
    from sqlalchemy import func, case

    try:
        # Aggregated statistics - much faster than loading all records
        stats = db.session.query(
            func.count(StoreStock.id).label('total_products'),
            func.sum(StoreStock.quantity).label('total_quantity'),
            func.sum(StoreStock.quantity * Product.sell_price).label('total_value'),
            func.sum(StoreStock.quantity * Product.cost_price).label('total_cost_value'),
            func.sum(StoreStock.quantity * (Product.sell_price - Product.cost_price)).label('total_profit'),
            func.sum(case((StoreStock.quantity == 0, 1), else_=0)).label('critical_stock_count')
        ).join(Product).filter(StoreStock.store_id == store_id).first()

        # Safe values
        total_products = stats.total_products or 0
        total_quantity = int(stats.total_quantity or 0)
        total_value = stats.total_value or Decimal('0')
        total_cost_value = stats.total_cost_value or Decimal('0')
        total_profit = stats.total_profit or Decimal('0')
        critical_stock_count = int(stats.critical_stock_count or 0)

        # Profit percentage
        profit_percentage = 0
        if total_cost_value > 0:
            profit_percentage = (total_profit / total_cost_value) * 100

    except Exception as e:
        app.logger.error(f"Error calculating store stats: {str(e)}")
        # Fallback values
        total_products = 0
        total_quantity = 0
        total_value = Decimal('0')
        total_cost_value = Decimal('0')
        total_profit = Decimal('0')
        profit_percentage = Decimal('0')
        critical_stock_count = 0

    return render_template('store_detail.html',
                           store=store,
                           total_products=total_products,
                           total_quantity=total_quantity,
                           total_value=total_value,
                           total_cost_value=total_cost_value,
                           total_profit=total_profit,
                           profit_percentage=profit_percentage,
                           critical_stock_count=critical_stock_count)


@app.route('/api/store/<int:store_id>/stock', methods=['GET'])
def api_store_stock(store_id):
    """Store stock API with pagination and filtering"""
    try:
        # Validate store exists
        Store.query.get_or_404(store_id)

        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        status = request.args.get('status', '', type=str)

        # Base query
        query = StoreStock.query.filter_by(store_id=store_id)

        # Search filter - qisman so'zlar bilan qidirish
        if search:
            query = query.join(Product)
            # Qidiruv so'zlarini bo'laklarga ajratish
            search_words = search.lower().split()
            # Har bir so'z uchun filter qo'shish
            for word in search_words:
                if word:  # Bo'sh so'zlarni o'tkazib yuborish
                    query = query.filter(Product.name.ilike(f'%{word}%'))

        # Execute query with pagination
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        stocks = pagination.items

        # Calculate stock info with status filtering
        stock_info = []
        total_value = Decimal('0.00')
        total_cost_value = Decimal('0.00')
        total_profit = Decimal('0.00')
        total_quantity = 0
        critical_stock_count = 0

        for stock in stocks:
            # Calculate unit profit
            unit_profit = stock.product.sell_price - stock.product.cost_price
            total_stock_value = stock.product.sell_price * stock.quantity
            total_stock_cost_value = stock.product.cost_price * stock.quantity
            total_stock_profit = unit_profit * stock.quantity

            # Determine status
            item_status = 'normal'
            min_stock = stock.min_stock or 10

            if stock.quantity == 0:
                item_status = 'critical'
                critical_stock_count += 1
            elif stock.quantity <= min_stock:
                item_status = 'low'

            # Skip if status filter doesn't match
            if status and item_status != status:
                continue

            # Calculate profit percentage
            profit_percentage = 0
            if stock.product.cost_price > 0:
                profit_percentage = (float(unit_profit) / float(stock.product.cost_price)) * 100

            stock_data = {
                'stock': {
                    'quantity': stock.quantity,
                    'product': {
                        'id': stock.product.id,
                        'name': stock.product.name,
                        'barcode': stock.product.barcode,
                        'unit_type': stock.product.unit_type,
                        'cost_price': float(stock.product.cost_price),
                        'sell_price': float(stock.product.sell_price),
                        'last_batch_cost': float(stock.product.last_batch_cost) if stock.product.last_batch_cost else None,
                        'last_batch_date': stock.product.last_batch_date.isoformat() if stock.product.last_batch_date else None
                    }
                },
                'unit_profit': float(unit_profit),
                'total_value': float(total_stock_value),
                'total_cost_value': float(total_stock_cost_value),
                'total_profit': float(total_stock_profit),
                'profit_percentage': profit_percentage,
                'status': item_status
            }
            stock_info.append(stock_data)

            total_value += total_stock_value
            total_cost_value += total_stock_cost_value
            total_profit += total_stock_profit
            total_quantity += stock.quantity

        # If status filter is applied, filter stock_info
        if status:
            stock_info = [item for item in stock_info if item['status'] == status]

        profit_percentage = 0
        if total_cost_value > 0:
            profit_percentage = (float(total_profit) / float(total_cost_value)) * 100

        return jsonify({
            'success': True,
            'data': {
                'stock_info': stock_info,
                'total_products': len(stock_info),
                'total_quantity': total_quantity,
                'total_value': float(total_value),
                'total_cost_value': float(total_cost_value),
                'total_profit': float(total_profit),
                'profit_percentage': profit_percentage,
                'critical_stock_count': critical_stock_count
            },
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'total_pages': pagination.pages,
                'has_prev': pagination.has_prev,
                'has_next': pagination.has_next
            }
        })

    except Exception as e:
        app.logger.error(f"Error fetching store stock: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/edit_store/<int:store_id>', methods=['GET', 'POST'])
def edit_store(store_id):
    store = Store.query.get_or_404(store_id)

    if request.method == 'POST':
        try:
            old_data = {
                'name': store.name,
                'address': store.address,
                'manager': store.manager_name,
                'phone': store.phone
            }

            store.name = request.form['name']
            store.address = request.form['address']
            store.manager_name = request.form['manager_name']
            store.phone = request.form.get('phone', '')

            db.session.commit()

            # OperationHistory logini yozish
            try:
                history = OperationHistory(
                    operation_type='edit_store',
                    table_name='stores',
                    record_id=store.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"Dokon tahrirlandi: {store.name}",
                    old_data=old_data,
                    new_data={'name': store.name, 'address': store.address, 'manager': store.manager_name, 'phone': store.phone},
                    ip_address=request.remote_addr,
                    location_id=store.id,
                    location_type='store',
                    location_name=store.name,
                    amount=None
                )
                db.session.add(history)
                db.session.commit()
            except Exception as log_error:
                logger.error(f"OperationHistory log xatoligi: {log_error}")

            return redirect(url_for('stores'))

        except Exception as e:
            db.session.rollback()
            return f"Xatolik: {str(e)}", 400

    return render_template('edit_store.html', store=store)


@app.route('/api/store/<int:store_id>', methods=['DELETE'])
def api_delete_store(store_id):
    try:
        logger.info(f" Dokon o'chirish so'rovi: Store ID: {store_id}")

        # Store mavjudligini tekshirish
        store = Store.query.get_or_404(store_id)
        logger.info(f" Store topildi: {store.name}")

        # Savdolar hisobini tekshirish
        sales_count = Sale.query.filter_by(store_id=store_id).count()
        if sales_count > 0:
            logger.info(f" Bu dokonda {sales_count} ta savdo mavjud, lekin savdolar saqlanadi")

        # Store ga bog'liq stocklarni olish
        store_stocks = StoreStock.query.filter_by(store_id=store_id).all()
        deleted_stocks_count = len(store_stocks)
        deleted_products_count = 0

        # Har bir stockni tekshirish va agar mahsulot boshqa joyda bo'lmasa o'chirish
        for stock in store_stocks:
            product_id = stock.product_id

            # Bu mahsulot boshqa do'konlarda bormi?
            # Boshqa do'konlarda bormi?
            other_store_stocks = StoreStock.query.filter(
                StoreStock.product_id == product_id,
                StoreStock.store_id != store_id
            ).count()

            # Omborlarda bormi?
            warehouse_stocks = WarehouseStock.query.filter_by(
                product_id=product_id
            ).count()

            # StoreStock'ni o'chirish
            db.session.delete(stock)
            deleted_products_count += 1

            # Agar mahsulot boshqa joylarda yo'q bo'lsa, Product'ni ham o'chirish
            if other_store_stocks == 0 and warehouse_stocks == 0:
                product = Product.query.get(product_id)
                if product:
                    # Mahsulot bilan bog'liq sale_items'larda product_id ni NULL qilish (tarixni saqlab qolish)
                    sale_items = SaleItem.query.filter_by(product_id=product_id).all()
                    for sale_item in sale_items:
                        sale_item.product_id = None

                    db.session.delete(product)
                    logger.info(f" Product ham o'chirildi: {product.name} (boshqa joylarda mavjud emas)")
            else:
                product = Product.query.get(product_id)
                if product:
                    logger.info(f" Faqat StoreStock o'chirildi: {product.name} (boshqa joylarda mavjud)")

        # Store ni o'chirish (Savdo tarixi saqlanadi, chunki Sale jadvalida store_id saqlanadi)
        store_name = store.name
        store_address = store.address
        db.session.delete(store)
        db.session.commit()

        # OperationHistory logini yozish
        try:
            history = OperationHistory(
                operation_type='delete_store',
                table_name='stores',
                record_id=store_id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Dokon o'chirildi: {store_name} ({deleted_stocks_count} ta stock, {deleted_products_count} ta mahsulot)",
                old_data={'name': store_name, 'address': store_address, 'stocks_count': deleted_stocks_count},
                new_data=None,
                ip_address=request.remote_addr,
                location_id=store_id,
                location_type='store',
                location_name=store_name,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        message = f'Do\'kon "{store_name}" muvaffaqiyatli o\'chirildi'
        if sales_count > 0:
            message += f' (Savdo tarixi saqlanadi: {sales_count} ta savdo)'
        if deleted_products_count > 0:
            message += f'\n{deleted_products_count} ta mahsulot butunlay o\'chirildi (faqat shu do\'konda edi)'

        logger.info(f" Store muvaffaqiyatli o'chirildi: {store_name}")
        logger.info(f" O'chirilgan stocklar: {deleted_stocks_count} ta, mahsulotlar: {deleted_products_count} ta")
        return jsonify({
            'success': True,
            'message': message,
            'deleted_stocks': deleted_stocks_count,
            'deleted_products': deleted_products_count
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f" Store o'chirish xatosi: {str(e)}")
        return jsonify({'success': False, 'error': f'Xatolik: {str(e)}'}), 400


@app.route('/api/warehouse/<int:warehouse_id>/stock', methods=['GET'])
def api_warehouse_stock(warehouse_id):
    """Warehouse stock API with pagination and filtering"""
    try:
        # Validate warehouse exists
        Warehouse.query.get_or_404(warehouse_id)

        # Get query parameters
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        search = request.args.get('search', '', type=str)
        status = request.args.get('status', '', type=str)

        # Base query
        query = WarehouseStock.query.filter_by(warehouse_id=warehouse_id)

        # Search filter - qisman so'zlar bilan qidirish
        if search:
            query = query.join(Product)
            # Qidiruv so'zlarini bo'laklarga ajratish
            search_words = search.lower().split()
            # Har bir so'z uchun filter qo'shish
            for word in search_words:
                if word:  # Bo'sh so'zlarni o'tkazib yuborish
                    query = query.filter(Product.name.ilike(f'%{word}%'))

        # Execute query with pagination
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        stocks = pagination.items

        # Calculate stock info with status filtering
        stock_info = []
        total_value = Decimal('0.00')
        total_cost_value = Decimal('0.00')
        total_profit = Decimal('0.00')
        total_quantity = 0
        critical_stock_count = 0

        for stock in stocks:
            # Calculate unit profit
            unit_profit = stock.product.sell_price - stock.product.cost_price
            total_stock_value = stock.product.sell_price * stock.quantity
            total_stock_cost_value = stock.product.cost_price * stock.quantity
            total_stock_profit = unit_profit * stock.quantity

            # Determine status
            item_status = 'normal'
            min_stock = getattr(stock.product, 'min_stock', None) or 10

            if stock.quantity == 0:
                item_status = 'critical'
                critical_stock_count += 1
            elif stock.quantity <= min_stock:
                item_status = 'low'

            # Skip if status filter doesn't match
            if status and item_status != status:
                continue

            # Calculate profit percentage
            profit_percentage = 0
            if stock.product.cost_price > 0:
                profit_percentage = (float(unit_profit) / float(stock.product.cost_price)) * 100

            stock_data = {
                'stock': {
                    'quantity': stock.quantity,
                    'product': {
                        'id': stock.product.id,
                        'name': stock.product.name,
                        'barcode': stock.product.barcode,
                        'unit_type': stock.product.unit_type,
                        'cost_price': float(stock.product.cost_price),
                        'sell_price': float(stock.product.sell_price),
                        'last_batch_cost': float(stock.product.last_batch_cost) if stock.product.last_batch_cost else None,
                        'last_batch_date': stock.product.last_batch_date.isoformat() if stock.product.last_batch_date else None
                    }
                },
                'unit_profit': float(unit_profit),
                'total_value': float(total_stock_value),
                'total_cost_value': float(total_stock_cost_value),
                'total_profit': float(total_stock_profit),
                'profit_percentage': profit_percentage,
                'status': item_status
            }
            stock_info.append(stock_data)

            total_value += total_stock_value
            total_cost_value += total_stock_cost_value
            total_profit += total_stock_profit
            total_quantity += stock.quantity

        # If status filter is applied, filter stock_info
        if status:
            stock_info = [item for item in stock_info if item['status'] == status]

        profit_percentage = 0
        if total_cost_value > 0:
            profit_percentage = (float(total_profit) / float(total_cost_value)) * 100

        return jsonify({
            'success': True,
            'data': {
                'stock_info': stock_info,
                'total_products': len(stock_info),
                'total_quantity': total_quantity,
                'total_value': float(total_value),
                'total_cost_value': float(total_cost_value),
                'total_profit': float(total_profit),
                'profit_percentage': profit_percentage,
                'critical_stock_count': critical_stock_count
            },
            'pagination': {
                'page': page,
                'per_page': per_page,
                'total': pagination.total,
                'total_pages': pagination.pages,
                'has_prev': pagination.has_prev,
                'has_next': pagination.has_next
            }
        })

    except Exception as e:
        app.logger.error(f"Error fetching warehouse stock: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/warehouses')
@role_required('admin', 'kassir')
def warehouses():
    warehouses_list = Warehouse.query.all()
    return render_template('warehouses.html', warehouses=warehouses_list)


@app.route('/add_warehouse', methods=['GET', 'POST'])
def add_warehouse():
    if request.method == 'POST':
        try:
            name = request.form['name']
            address = request.form['address']
            manager_name = request.form['manager_name']
            phone = request.form.get('phone', '')

            new_warehouse = Warehouse(
                name=name,
                address=address,
                manager_name=manager_name,
                phone=phone
            )

            db.session.add(new_warehouse)
            db.session.commit()

            # OperationHistory logini yozish
            try:
                history = OperationHistory(
                    operation_type='create_warehouse',
                    table_name='warehouses',
                    record_id=new_warehouse.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"Yangi ombor yaratildi: {name}",
                    old_data=None,
                    new_data={'name': name, 'address': address, 'manager': manager_name, 'phone': phone},
                    ip_address=request.remote_addr,
                    location_id=new_warehouse.id,
                    location_type='warehouse',
                    location_name=name,
                    amount=None
                )
                db.session.add(history)
                db.session.commit()
            except Exception as log_error:
                logger.error(f"OperationHistory log xatoligi: {log_error}")

            return redirect(url_for('warehouses'))

        except Exception as e:
            db.session.rollback()
            return f"Xatolik: {str(e)}", 400

    return render_template('add_warehouse.html')


@app.route('/edit_warehouse/<int:warehouse_id>', methods=['GET', 'POST'])
def edit_warehouse(warehouse_id):
    warehouse = Warehouse.query.get_or_404(warehouse_id)

    if request.method == 'POST':
        try:
            old_data = {
                'name': warehouse.name,
                'address': warehouse.address,
                'manager': warehouse.manager_name,
                'phone': warehouse.phone
            }

            warehouse.name = request.form['name']
            warehouse.address = request.form['address']
            warehouse.manager_name = request.form['manager_name']
            warehouse.phone = request.form.get('phone', '')

            db.session.commit()

            # OperationHistory logini yozish
            try:
                history = OperationHistory(
                    operation_type='edit_warehouse',
                    table_name='warehouses',
                    record_id=warehouse.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"Ombor tahrirlandi: {warehouse.name}",
                    old_data=old_data,
                    new_data={'name': warehouse.name, 'address': warehouse.address, 'manager': warehouse.manager_name, 'phone': warehouse.phone},
                    ip_address=request.remote_addr,
                    location_id=warehouse.id,
                    location_type='warehouse',
                    location_name=warehouse.name,
                    amount=None
                )
                db.session.add(history)
                db.session.commit()
            except Exception as log_error:
                logger.error(f"OperationHistory log xatoligi: {log_error}")

            return redirect(url_for('warehouses'))

        except Exception as e:
            db.session.rollback()
            return f"Xatolik: {str(e)}", 400

    return render_template('edit_warehouse.html', warehouse=warehouse)


@app.route('/api/warehouses')
@role_required('admin', 'kassir', 'sotuvchi')
def api_warehouses():
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Debug ma'lumotlari
        print(
            f"ğŸ” Warehouses API - User: {current_user.username}, Role: {current_user.role}")

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role == 'admin':
            # Admin hamma omborlarni ko'radi
            warehouses_list = Warehouse.query.all()
            print(f"ğŸ” Admin user, returning all {len(warehouses_list)} warehouses")
        else:
            # Oddiy foydalanuvchilar faqat allowed_locations dan ruxsat etilgan
            # omborlarni ko'radi (savdo uchun)
            allowed_locations = current_user.allowed_locations or []
            print(
                f"ğŸ” User allowed locations for warehouses: {allowed_locations}")

            # Helper funksiya bilan warehouse ID'larni olish
            allowed_warehouse_ids = extract_location_ids(
                allowed_locations, 'warehouse')
            logger.debug(f" Allowed warehouse IDs: {allowed_warehouse_ids}")

            if allowed_warehouse_ids:
                warehouses_list = Warehouse.query.filter(
                    Warehouse.id.in_(allowed_warehouse_ids)).all()
                logger.debug(f" Found {len(warehouses_list)} allowed warehouses")
            else:
                # Ruxsat berilgan ombor bo'lmasa
                warehouses_list = []
                logger.debug(" No allowed warehouses for user")

        result = [wh.to_dict() for wh in warehouses_list]
        logger.debug(f" Returning {len(result)} warehouses")

        return jsonify(result)
    except Exception as e:
        logger.error(f" Error in api_warehouses: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stores')
@role_required('admin', 'kassir', 'sotuvchi')
def api_stores():
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Debug ma'lumotlari
        print(
            f"ğŸ” Stores API - User: {current_user.username}, Role: {current_user.role}")

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role == 'admin':
            # Admin hamma do'konlarni ko'radi
            stores_list = Store.query.all()
            logger.debug(f" Admin user, returning all {len(stores_list)} stores")
        else:
            # Oddiy foydalanuvchilar faqat allowed_locations dan ruxsat etilgan
            # do'konlarni ko'radi (savdo uchun)
            allowed_locations = current_user.allowed_locations or []
            logger.debug(f" User allowed locations for stores: {allowed_locations}")

            # Helper funksiya bilan store ID'larni olish
            allowed_store_ids = extract_location_ids(
                allowed_locations, 'store')
            logger.debug(f" Allowed store IDs: {allowed_store_ids}")

            if allowed_store_ids:
                stores_list = Store.query.filter(
                    Store.id.in_(allowed_store_ids)).all()
                logger.debug(f" Found {len(stores_list)} allowed stores")
            else:
                # Ruxsat berilgan do'kon bo'lmasa
                stores_list = []
                logger.debug(" No allowed stores for user")

        result = [store.to_dict() for store in stores_list]
        logger.debug(f" Returning {len(result)} stores")

        return jsonify(result)
    except Exception as e:
        logger.error(f" Error in api_stores: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stores-warehouses')
@role_required('admin', 'kassir', 'sotuvchi')
def api_stores_warehouses():
    try:
        locations = []

        current_user = get_current_user()
        if not current_user:
            return jsonify({'success': False, 'error': 'Foydalanuvchi topilmadi'}), 401

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role == 'admin':
            # Admin hamma joylashuvlarni ko'radi
            allowed_store_ids = None
            allowed_warehouse_ids = None
        else:
            # Oddiy foydalanuvchilar faqat allowed_locations dan ruxsat etilgan joylashuvlarni ko'radi
            allowed_locations = current_user.allowed_locations or []

            # Helper funksiya bilan ID'larni olish (eski va yangi formatlar uchun)
            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            allowed_warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')

        # Do'konlarni qo'shish
        if allowed_store_ids is None:
            stores = Store.query.all()
        else:
            stores = Store.query.filter(
                Store.id.in_(allowed_store_ids)).all() if allowed_store_ids else []

        for store in stores:
            locations.append({
                'id': store.id,
                'name': store.name,
                'type': 'store',
                'address': store.address,
                'manager_name': store.manager_name,
                'phone': store.phone
            })

        # Omborlarni qo'shish
        if allowed_warehouse_ids is None:
            warehouses = Warehouse.query.all()
        else:
            warehouses = Warehouse.query.filter(Warehouse.id.in_(
                allowed_warehouse_ids)).all() if allowed_warehouse_ids else []

        for warehouse in warehouses:
            locations.append({
                'id': warehouse.id,
                'name': warehouse.name,
                'type': 'warehouse',
                'address': warehouse.address,
                'manager_name': warehouse.manager_name,
                'current_stock': warehouse.current_stock
            })

        # Stores va warehouses'ni alohida array sifatida ham qaytarish
        stores = [loc for loc in locations if loc['type'] == 'store']
        warehouses = [loc for loc in locations if loc['type'] == 'warehouse']

        return jsonify({
            'success': True,
            'locations': locations,
            'stores': stores,
            'warehouses': warehouses
        })

    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/transfer-locations')
@role_required('admin', 'kassir', 'sotuvchi')
def api_transfer_locations():
    """Transfer uchun ruxsat etilgan joylashuvlarni qaytarish"""
    try:
        locations = []

        # Joriy foydalanuvchini olish
        current_user = get_current_user()
        logger.debug(f" API Transfer Locations - Current user: {current_user}")
        if not current_user:
            logger.error(" No current user found - returning empty locations")
            # Bo'sh array qaytarish
            return jsonify({'success': True, 'locations': []})

        # Foydalanuvchi huquqlarini tekshirish
        if current_user.role == 'admin':
            # Admin hamma joylashuvlarni ko'radi
            allowed_store_ids = None
            allowed_warehouse_ids = None
            logger.debug(" Admin user - showing all transfer locations")
        else:
            # Oddiy foydalanuvchilar faqat transfer_locations dan ruxsat
            # etilgan joylashuvlarni ko'radi
            transfer_locations = current_user.transfer_locations or []
            logger.debug(f" User transfer_locations: {transfer_locations}")
            logger.debug(f" Type of transfer_locations: {type(transfer_locations)}")

            # Transfer_locations eski format (faqat ID'lar) bo'lgani uchun
            # hamma ID'larni store va warehouse sifatida tekshiramiz
            if transfer_locations and isinstance(transfer_locations[0], int):
                # Eski format: barcha ID'larni har ikki tipga qo'llamiz
                print(
                    "ğŸ” Using old format for transfer_locations - checking all IDs as both stores and warehouses")
                allowed_store_ids = transfer_locations
                allowed_warehouse_ids = transfer_locations
            else:
                # Yangi format
                allowed_store_ids = extract_location_ids(
                    transfer_locations, 'store')
                allowed_warehouse_ids = extract_location_ids(
                    transfer_locations, 'warehouse')

            logger.debug(f" Final store IDs for transfer: {allowed_store_ids}")
            print(
                f"ğŸ” Final warehouse IDs for transfer: {allowed_warehouse_ids}")

        # Do'konlarni qo'shish
        if allowed_store_ids is None:
            stores = Store.query.all()
        else:
            stores = Store.query.filter(
                Store.id.in_(allowed_store_ids)).all() if allowed_store_ids else []

        for store in stores:
            locations.append({
                'id': store.id,
                'name': store.name,
                'type': 'store',
                'address': store.address,
                'manager_name': store.manager_name,
                'phone': store.phone
            })

        # Omborlarni qo'shish
        if allowed_warehouse_ids is None:
            warehouses = Warehouse.query.all()
        else:
            warehouses = Warehouse.query.filter(Warehouse.id.in_(
                allowed_warehouse_ids)).all() if allowed_warehouse_ids else []

        for warehouse in warehouses:
            locations.append({
                'id': warehouse.id,
                'name': warehouse.name,
                'type': 'warehouse',
                'address': warehouse.address,
                'manager_name': warehouse.manager_name,
                'current_stock': warehouse.current_stock
            })

        return jsonify({
            'success': True,
            'locations': locations
        })

    except Exception as e:
        import traceback
        logger.error(f" Error in api_transfer_locations: {str(e)}")
        logger.error(f" Traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/warehouse_stats')
def api_warehouse_stats():
    warehouses = Warehouse.query.all()
    total_stock = sum(wh.current_stock for wh in warehouses)

    return jsonify({
        'total_warehouses': len(warehouses),
        'total_stock': total_stock,
        'warehouses': [wh.to_dict() for wh in warehouses]
    })


@app.route('/api/warehouse/<int:warehouse_id>', methods=['DELETE'])
def api_delete_warehouse(warehouse_id):
    try:
        warehouse = Warehouse.query.get_or_404(warehouse_id)
        logger.info(f" Ombor o'chirish so'rovi: Warehouse ID: {warehouse_id}")
        logger.info(f" Warehouse topildi: {warehouse.name}")

        # Ombor bilan bog'liq barcha stocklarni olish
        warehouse_stocks = WarehouseStock.query.filter_by(warehouse_id=warehouse_id).all()
        deleted_stocks_count = len(warehouse_stocks)
        deleted_products_count = 0
        deleted_transfers_count = 0

        # Har bir stockni tekshirish va agar mahsulot boshqa joyda bo'lmasa o'chirish
        for stock in warehouse_stocks:
            product_id = stock.product_id

            # Bu mahsulot boshqa omborlarda bormi?
            other_warehouse_stocks = WarehouseStock.query.filter(
                WarehouseStock.product_id == product_id,
                WarehouseStock.warehouse_id != warehouse_id
            ).count()

            # Bu mahsulot do'konlarda bormi?
            store_stocks = StoreStock.query.filter_by(
                product_id=product_id
            ).count()

            # Stockni o'chirish
            db.session.delete(stock)

            # Agar mahsulot boshqa joyda yo'q bo'lsa, productni ham o'chirish
            if other_warehouse_stocks == 0 and store_stocks == 0:
                product = Product.query.get(product_id)
                if product:
                    # Mahsulot bilan bog'liq sale_items'larda product_id ni NULL qilish (tarixni saqlab qolish)
                    sale_items = SaleItem.query.filter_by(product_id=product_id).all()
                    for sale_item in sale_items:
                        sale_item.product_id = None

                    # Mahsulot bilan bog'liq transferlarni o'chirish
                    product_transfers = Transfer.query.filter(
                        db.or_(
                            Transfer.product_id == product_id,
                            db.and_(
                                Transfer.from_location_type == 'warehouse',
                                Transfer.from_location_id == warehouse_id
                            ),
                            db.and_(
                                Transfer.to_location_type == 'warehouse',
                                Transfer.to_location_id == warehouse_id
                            )
                        )
                    ).all()

                    for transfer in product_transfers:
                        db.session.delete(transfer)
                        deleted_transfers_count += 1

                    db.session.delete(product)
                    deleted_products_count += 1
                    logger.info(f" Mahsulot butunlay o'chirildi: {product.name} (faqat shu omborda edi)")

        # Ombor nomini saqlash (log uchun)
        warehouse_name = warehouse.name
        warehouse_address = warehouse.address

        # Omborni o'chirish
        db.session.delete(warehouse)
        db.session.commit()

        # OperationHistory logini yozish
        try:
            history = OperationHistory(
                operation_type='delete_warehouse',
                table_name='warehouses',
                record_id=warehouse_id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Ombor o'chirildi: {warehouse_name} ({deleted_stocks_count} ta stock, {deleted_products_count} ta mahsulot)",
                old_data={'name': warehouse_name, 'address': warehouse_address, 'stocks_count': deleted_stocks_count},
                new_data=None,
                ip_address=request.remote_addr,
                location_id=warehouse_id,
                location_type='warehouse',
                location_name=warehouse_name,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        message = f'"{warehouse_name}" ombori muvaffaqiyatli o\'chirildi'
        if deleted_stocks_count > 0:
            message += f' ({deleted_stocks_count} ta stock ma\'lumoti o\'chirildi)'
        if deleted_products_count > 0:
            message += f'\n{deleted_products_count} ta mahsulot butunlay o\'chirildi (faqat shu omborda edi)'
        if deleted_transfers_count > 0:
            message += f'\n{deleted_transfers_count} ta transfer yozuvi o\'chirildi'

        logger.info(f" Warehouse muvaffaqiyatli o'chirildi: {warehouse_name}")
        logger.info(f" O'chirilgan stocklar: {deleted_stocks_count} ta, mahsulotlar: {deleted_products_count} ta, transferlar: {deleted_transfers_count} ta")
        return jsonify({
            'success': True,
            'message': message,
            'deleted_stocks': deleted_stocks_count,
            'deleted_products': deleted_products_count,
            'deleted_transfers': deleted_transfers_count
        }), 200

    except Exception as e:
        db.session.rollback()
        logger.error(f" Warehouse o'chirish xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'message': f'Xatolik: {str(e)}'
        }), 400


@app.route('/users')
@role_required('admin', 'kassir')
def users():
    return render_template(
        'users.html',
        page_title='Foydalanuvchilar',
        icon='ğŸ‘¤')


# Qarzlar API
@app.route('/api/debts')
@role_required('admin', 'kassir', 'sotuvchi')
def api_debts():
    """Barcha qarzlar ro'yxati - allowed_locations bo'yicha filtrlangan"""
    try:
        # Exchange rate olish
        rate = CurrencyRate.query.order_by(CurrencyRate.id.desc()).first()
        exchange_rate = float(rate.rate) if rate else 13000

        # Joriy foydalanuvchini olish
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Location filter parametri (frontend dan)
        location_id = request.args.get('location_id', type=int)

        # Foydalanuvchi huquqlarini tekshirish
        allowed_location_ids = None
        if current_user.role != 'admin':
            # Admin bo'lmagan foydalanuvchilar uchun ruxsat etilgan locationlarni olish
            allowed_locations = current_user.allowed_locations or []
            if allowed_locations:
                # Store va warehouse ID'larni olish
                store_ids = extract_location_ids(allowed_locations, 'store')
                warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')

                # Barcha ruxsat etilgan location ID'larni birlashtirish
                allowed_location_ids = []
                if store_ids:
                    allowed_location_ids.extend(store_ids)
                if warehouse_ids:
                    allowed_location_ids.extend(warehouse_ids)

                logger.info(f"ğŸ“‹ User {current_user.username} allowed locations: {allowed_location_ids}")

        # Qarzli mijozlar ro'yxati
        if location_id:
            # Frontend'dan tanlangan location bo'yicha filter
            # Agar user allowed locations'ga ega bo'lsa, tekshirish
            if allowed_location_ids is not None and location_id not in allowed_location_ids:
                logger.warning(f"âš ï¸ User {current_user.username} tried to access unauthorized location {location_id}")
                return jsonify({'success': True, 'debts': [], 'exchange_rate': exchange_rate})

            query = text("""
                SELECT
                    c.id as customer_id,
                    c.name as customer_name,
                    c.phone as customer_phone,
                    c.address as customer_address,
                    COALESCE(SUM(s.debt_usd), 0) as total_debt,
                    0 as paid_amount,
                    COALESCE(SUM(s.debt_usd), 0) as remaining_debt,
                    c.last_debt_payment_date as last_payment_date,
                    COALESCE(c.last_debt_payment_usd, 0) as last_payment_amount,
                    COALESCE(c.last_debt_payment_rate, 13000) as last_payment_rate
                FROM customers c
                LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0 AND s.location_id = :location_id
                GROUP BY c.id, c.name, c.phone, c.address, c.last_debt_payment_date, c.last_debt_payment_usd, c.last_debt_payment_rate
                HAVING COALESCE(SUM(s.debt_usd), 0) > 0
                ORDER BY remaining_debt DESC
            """)
            result = db.session.execute(query, {'location_id': location_id})
        else:
            # Location tanlanmagan - barcha ruxsat etilgan locationlar
            if allowed_location_ids is not None:
                # Admin bo'lmagan user - faqat ruxsat etilgan locationlar
                if not allowed_location_ids:
                    # Hech qanday location'ga ruxsat yo'q
                    return jsonify({'success': True, 'debts': [], 'exchange_rate': exchange_rate})

                logger.info(f"ğŸ” Debts query location_ids: {allowed_location_ids}")

                query = text("""
                    SELECT
                        c.id as customer_id,
                        c.name as customer_name,
                        c.phone as customer_phone,
                        c.address as customer_address,
                        COALESCE(SUM(s.debt_usd), 0) as total_debt,
                        0 as paid_amount,
                        COALESCE(SUM(s.debt_usd), 0) as remaining_debt,
                        c.last_debt_payment_date as last_payment_date,
                        COALESCE(c.last_debt_payment_usd, 0) as last_payment_amount,
                        COALESCE(c.last_debt_payment_rate, 13000) as last_payment_rate
                    FROM customers c
                    LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
                        AND s.location_id = ANY(:location_ids)
                    GROUP BY c.id, c.name, c.phone, c.address, c.last_debt_payment_date, c.last_debt_payment_usd, c.last_debt_payment_rate
                    HAVING COALESCE(SUM(s.debt_usd), 0) > 0
                    ORDER BY remaining_debt DESC
                """)
                result = db.session.execute(query, {'location_ids': allowed_location_ids})
            else:
                # Admin - barcha qarzlar
                query = text("""
                    SELECT
                        c.id as customer_id,
                        c.name as customer_name,
                        c.phone as customer_phone,
                        c.address as customer_address,
                        COALESCE(SUM(s.debt_usd), 0) as total_debt,
                        0 as paid_amount,
                        COALESCE(SUM(s.debt_usd), 0) as remaining_debt,
                        c.last_debt_payment_date as last_payment_date,
                        COALESCE(c.last_debt_payment_usd, 0) as last_payment_amount,
                        COALESCE(c.last_debt_payment_rate, 13000) as last_payment_rate
                    FROM customers c
                    LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
                GROUP BY c.id, c.name, c.phone, c.address, c.last_debt_payment_date, c.last_debt_payment_usd, c.last_debt_payment_rate
                HAVING COALESCE(SUM(s.debt_usd), 0) > 0
                ORDER BY remaining_debt DESC
            """)
            result = db.session.execute(query)

        debts = []

        for row in result:
            debts.append({
                'customer_id': row.customer_id,
                'customer_name': row.customer_name,
                'customer_phone': row.customer_phone,
                'customer_address': row.customer_address,
                'total_debt': float(row.total_debt),
                'paid_amount': float(row.paid_amount),
                'remaining_debt': float(row.remaining_debt),
                'last_payment_date': row.last_payment_date.strftime('%Y-%m-%d %H:%M') if row.last_payment_date else None,
                'last_payment_amount': float(row.last_payment_amount) if row.last_payment_amount else 0,
                'last_payment_rate': float(row.last_payment_rate) if row.last_payment_rate else 13000
            })

        return jsonify({
            'success': True,
            'debts': debts,
            'exchange_rate': exchange_rate
        })

    except Exception as e:
        app.logger.error(f"Qarzlar API xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/debts/paid')
@role_required('admin', 'kassir', 'sotuvchi')
def api_paid_debts():
    """To'langan qarzlar tarixi - allowed_locations bo'yicha filtrlangan"""
    try:
        # Joriy foydalanuvchini olish
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Location filter parametri
        location_id = request.args.get('location_id', type=int)

        # Allowed locations tekshirish
        allowed_location_ids = None
        if current_user.role != 'admin':
            allowed_locations = current_user.allowed_locations or []
            if allowed_locations:
                store_ids = extract_location_ids(allowed_locations, 'store')
                warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')
                allowed_location_ids = []
                if store_ids:
                    allowed_location_ids.extend(store_ids)
                if warehouse_ids:
                    allowed_location_ids.extend(warehouse_ids)

        if location_id:
            # Location'ga ruxsat tekshirish
            if allowed_location_ids is not None and location_id not in allowed_location_ids:
                return jsonify({'success': True, 'paid_debts': []})

            query = text("""
                SELECT
                    s.id as sale_id,
                    s.updated_at as payment_date,
                    s.created_at as sale_date,
                    c.name as customer_name,
                    s.total_amount as total_amount,
                    COALESCE(s.cash_usd, 0) as cash_usd,
                    COALESCE(s.click_usd, 0) as click_usd,
                    COALESCE(s.terminal_usd, 0) as terminal_usd
                FROM sales s
                JOIN customers c ON s.customer_id = c.id
                WHERE s.payment_status = 'paid'
                    AND s.debt_usd = 0
                    AND s.total_amount > 0
                    AND (COALESCE(s.cash_usd, 0) + COALESCE(s.click_usd, 0) + COALESCE(s.terminal_usd, 0)) > 0
                    AND s.updated_at > s.created_at + INTERVAL '1 second'
                    AND s.location_id = :location_id
                ORDER BY s.updated_at DESC
                LIMIT 200
            """)
            result = db.session.execute(query, {'location_id': location_id})
        else:
            # Allowed locations bo'yicha filtrlash
            if allowed_location_ids is not None:
                if not allowed_location_ids:
                    return jsonify({'success': True, 'paid_debts': []})

                placeholders = ','.join([f':loc{i}' for i in range(len(allowed_location_ids))])
                query = text(f"""
                SELECT
                    s.id as sale_id,
                    s.updated_at as payment_date,
                    s.created_at as sale_date,
                    c.name as customer_name,
                    s.total_amount as total_amount,
                    COALESCE(s.cash_usd, 0) as cash_usd,
                    COALESCE(s.click_usd, 0) as click_usd,
                    COALESCE(s.terminal_usd, 0) as terminal_usd
                FROM sales s
                JOIN customers c ON s.customer_id = c.id
                WHERE s.payment_status = 'paid'
                    AND s.debt_usd = 0
                    AND s.total_amount > 0
                    AND (COALESCE(s.cash_usd, 0) + COALESCE(s.click_usd, 0) + COALESCE(s.terminal_usd, 0)) > 0
                    AND s.updated_at > s.created_at + INTERVAL '1 second'
                    AND s.location_id IN ({placeholders})
                ORDER BY s.updated_at DESC
                LIMIT 200
            """)
                params = {f'loc{i}': loc_id for i, loc_id in enumerate(allowed_location_ids)}
                result = db.session.execute(query, params)
            else:
                # Admin - barcha qarzlarni ko'radi
                query = text("""
                SELECT
                    s.id as sale_id,
                    s.updated_at as payment_date,
                    s.created_at as sale_date,
                    c.name as customer_name,
                    s.total_amount as total_amount,
                    COALESCE(s.cash_usd, 0) as cash_usd,
                    COALESCE(s.click_usd, 0) as click_usd,
                    COALESCE(s.terminal_usd, 0) as terminal_usd
                FROM sales s
                JOIN customers c ON s.customer_id = c.id
                WHERE s.payment_status = 'paid'
                    AND s.debt_usd = 0
                    AND s.total_amount > 0
                    AND (COALESCE(s.cash_usd, 0) + COALESCE(s.click_usd, 0) + COALESCE(s.terminal_usd, 0)) > 0
                    AND s.updated_at > s.created_at + INTERVAL '1 second'
                ORDER BY s.updated_at DESC
                LIMIT 200
            """)
                result = db.session.execute(query)
        paid_debts = []

        for row in result:
            paid_debts.append({
                'sale_id': row.sale_id,
                'payment_date': row.payment_date.strftime('%Y-%m-%d %H:%M') if row.payment_date else None,
                'sale_date': row.sale_date.strftime('%Y-%m-%d %H:%M'),
                'customer_name': row.customer_name,
                'total_amount': float(row.total_amount),
                'cash_usd': float(row.cash_usd),
                'click_usd': float(row.click_usd),
                'terminal_usd': float(row.terminal_usd)
            })

        return jsonify({
            'success': True,
            'paid_debts': paid_debts
        })

    except Exception as e:
        app.logger.error(f"To'langan qarzlar API xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/debt-payments')
@role_required('admin', 'kassir', 'sotuvchi')
def api_debt_payment_history():
    """Qarz to'lovlar tarixi - allowed_locations bo'yicha filtrlangan"""
    try:
        # Joriy foydalanuvchini olish
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Location filter parametri
        location_id = request.args.get('location_id', type=int)

        # Allowed locations tekshirish
        allowed_location_ids = None
        if current_user.role != 'admin':
            allowed_locations = current_user.allowed_locations or []
            if allowed_locations:
                store_ids = extract_location_ids(allowed_locations, 'store')
                warehouse_ids = extract_location_ids(allowed_locations, 'warehouse')
                allowed_location_ids = []
                if store_ids:
                    allowed_location_ids.extend(store_ids)
                if warehouse_ids:
                    allowed_location_ids.extend(warehouse_ids)

        # Debt payments jadvalidan ma'lumotlarni olish
        query = DebtPayment.query

        # Agar location_id berilgan bo'lsa, sale orqali filtrlash
        if location_id:
            # Location'ga ruxsat tekshirish
            if allowed_location_ids is not None and location_id not in allowed_location_ids:
                return jsonify({'success': True, 'payments': []})

            # Faqat berilgan location'dagi savdolar uchun to'lovlar
            query = query.join(Sale, DebtPayment.sale_id == Sale.id, isouter=True).filter(
                db.or_(
                    Sale.location_id == location_id,
                    DebtPayment.sale_id is None  # sale_id NULL bo'lgan to'lovlar ham ko'rinadi
                )
            )
        else:
            # Allowed locations bo'yicha filtrlash
            if allowed_location_ids is not None:
                if not allowed_location_ids:
                    return jsonify({'success': True, 'payments': []})

                # Faqat ruxsat etilgan locationlardagi to'lovlar
                query = query.join(Sale, DebtPayment.sale_id == Sale.id, isouter=True).filter(
                    db.or_(
                        Sale.location_id.in_(allowed_location_ids),
                        DebtPayment.sale_id is None
                    )
                )

        debt_payments = query.order_by(DebtPayment.payment_date.desc()).limit(200).all()

        payments = []
        for payment in debt_payments:
            payments.append(payment.to_dict())

        return jsonify({
            'success': True,
            'payments': payments
        })

    except Exception as e:
        app.logger.error(f"Qarz to'lovlar tarixi API xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/debts/<int:customer_id>')
@role_required('admin', 'kassir', 'sotuvchi')
def api_debt_details(customer_id):
    """Mijozning batafsil qarz ma'lumotlari"""
    try:
        # Mijoz ma'lumotlari
        customer = Customer.query.get_or_404(customer_id)

        # Qarzlar tarixi (ham qarzli, ham to'langan)
        query = text("""
            SELECT
                s.id as sale_id,
                s.created_at as sale_date,
                COALESCE(s.debt_usd, 0) + COALESCE(s.cash_usd, 0) + COALESCE(s.click_usd, 0) + COALESCE(s.terminal_usd, 0) as original_debt,
                COALESCE(s.cash_usd, 0) as cash_usd,
                COALESCE(s.click_usd, 0) as click_usd,
                COALESCE(s.terminal_usd, 0) as terminal_usd,
                COALESCE(s.debt_usd, 0) as debt_usd
            FROM sales s
            WHERE s.customer_id = :customer_id
                AND (s.debt_usd > 0 OR s.payment_status = 'paid')
            ORDER BY s.created_at DESC
        """)

        result = db.session.execute(query, {'customer_id': customer_id})
        history = []
        total_debt = Decimal('0')

        for row in result:
            paid_amount = float(row.cash_usd) + float(row.click_usd) + float(row.terminal_usd)
            debt_amount = float(row.original_debt)

            history.append({
                'sale_id': row.sale_id,
                'sale_date': row.sale_date.strftime('%Y-%m-%d %H:%M'),
                'debt_amount': debt_amount,
                'paid_amount': paid_amount,
                'remaining': float(row.debt_usd),
                'cash_usd': float(row.cash_usd),
                'click_usd': float(row.click_usd),
                'terminal_usd': float(row.terminal_usd)
            })

            if row.debt_usd and Decimal(str(row.debt_usd)) > 0:
                total_debt += Decimal(str(row.debt_usd))

        remaining_debt = total_debt

        return jsonify({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone,
                'address': customer.address
            },
            'total_debt': str(total_debt),
            'total_paid': 0,
            'remaining_debt': str(remaining_debt),
            'history': history
        })

    except Exception as e:
        app.logger.error(f"Qarz tafsilotlari API xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/debts/payment', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
@timeout_monitor(max_seconds=10, operation_name='DebtPayment')
@check_idempotency('payment')
def api_debt_payment():
    """Qarzga to'lov qilish"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')

        # Har bir to'lov turini olish
        cash_usd = Decimal(str(data.get('cash_usd', 0)))
        click_usd = Decimal(str(data.get('click_usd', 0)))
        terminal_usd = Decimal(str(data.get('terminal_usd', 0)))

        payment_usd = cash_usd + click_usd + terminal_usd

        if payment_usd <= 0:
            return jsonify({
                'success': False,
                'error': 'To\'lov summasi 0 dan katta bo\'lishi kerak'
            }), 400

        # Mijozning qarzli savdolarini topish
        sales = Sale.query.filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0
        ).order_by(Sale.created_at.asc()).all()

        if not sales:
            return jsonify({
                'success': False,
                'error': 'Qarzli savdolar topilmadi'
            }), 404

        remaining_payment = payment_usd
        updated_sales = []

        # To'lov turlarini ketma-ket taqsimlash: 1) naqd, 2) click, 3) terminal
        remaining_cash = cash_usd
        remaining_click = click_usd
        remaining_terminal = terminal_usd

        # Har bir qarzga to'lovni taqsimlash
        for sale in sales:
            if remaining_payment <= 0:
                break

            # Ushbu savdodagi qolgan qarz
            current_debt = sale.debt_usd

            if current_debt <= 0:
                continue

            # Ushbu savdoga qancha to'lov qilish mumkin
            payment_for_this_sale = min(remaining_payment, current_debt)

            # Har bir to'lov turidan qancha ishlatish mumkin
            # 1. Naqd puldan
            cash_for_this = min(remaining_cash, payment_for_this_sale)
            if cash_for_this > 0:
                sale.cash_usd = (sale.cash_usd or Decimal('0')) + cash_for_this
                sale.cash_amount = float(sale.cash_usd)  # USD da saqlaymiz
                remaining_cash -= cash_for_this
                payment_for_this_sale -= cash_for_this

            # 2. Click dan
            click_for_this = min(remaining_click, payment_for_this_sale)
            if click_for_this > 0:
                sale.click_usd = (sale.click_usd or Decimal('0')) + click_for_this
                sale.click_amount = float(sale.click_usd)  # USD da saqlaymiz
                remaining_click -= click_for_this
                payment_for_this_sale -= click_for_this

            # 3. Terminal dan
            terminal_for_this = min(remaining_terminal, payment_for_this_sale)
            if terminal_for_this > 0:
                sale.terminal_usd = (sale.terminal_usd or Decimal('0')) + terminal_for_this
                sale.terminal_amount = float(sale.terminal_usd)  # USD da saqlaymiz
                remaining_terminal -= terminal_for_this
                payment_for_this_sale -= terminal_for_this

            # Jami to'langan summa
            total_paid = cash_for_this + click_for_this + terminal_for_this

            # Qarzni kamaytirish
            sale.debt_usd = sale.debt_usd - total_paid
            sale.debt_amount = float(sale.debt_usd)  # USD da saqlaymiz

            # Payment statusni yangilash
            if sale.debt_usd == 0:
                # Qarz to'liq to'landi
                sale.payment_status = 'paid'
            elif sale.debt_usd > 0:
                # Hali qarz qolgan (qisman to'langan yoki qisman to'landi)
                sale.payment_status = 'partial'

            logger.info(f"ğŸ’° Savdo #{sale.id}: To'landi ${total_paid}, Qolgan qarz ${sale.debt_usd}, Status: {sale.payment_status}")

            # updated_at ni yangilash (qarz to'lash belgisi)
            sale.updated_at = get_tashkent_time()
            # sale.updated_by = session.get('user_name', 'Unknown')  # Database'da hali yo'q

            remaining_payment -= total_paid
            updated_sales.append(sale.id)

        # Mijozning oxirgi to'lov ma'lumotlarini yangilash
        customer = Customer.query.get(customer_id)
        if customer:
            # Mijozning qolgan umumiy qarzini tekshirish
            total_remaining_debt = db.session.query(
                db.func.sum(Sale.debt_usd)
            ).filter(
                Sale.customer_id == customer_id,
                Sale.debt_usd > 0
            ).scalar() or 0

            # Avvalgi qarzni hisoblash (to'lovdan oldin)
            previous_total_debt = total_remaining_debt + (payment_usd - remaining_payment)

            # Agar barcha qarzlar to'langan bo'lsa, oxirgi to'lov ma'lumotlarini tozalash
            if total_remaining_debt == 0:
                customer.last_debt_payment_usd = 0
                customer.last_debt_payment_date = None
                customer.last_debt_payment_rate = 0
            else:
                # Agar hali qarz qolgan bo'lsa, oxirgi to'lov ma'lumotlarini yangilash
                customer.last_debt_payment_usd = payment_usd - remaining_payment
                customer.last_debt_payment_date = db.func.current_timestamp()
                customer.last_debt_payment_rate = get_current_currency_rate()

        # Qarz to'lovi tarixiga yozuv qo'shish
        debt_payment = DebtPayment(
            customer_id=customer_id,
            sale_id=updated_sales[0] if updated_sales else None,  # Birinchi to'langan savdo
            payment_date=get_tashkent_time(),
            cash_usd=cash_usd,
            click_usd=click_usd,
            terminal_usd=terminal_usd,
            total_usd=payment_usd - remaining_payment,
            currency_rate=get_current_currency_rate(),
            received_by=session.get('user_name', 'Unknown'),
            notes=f"{len(updated_sales)} ta savdoning qarziga to'lov qilindi"
        )
        db.session.add(debt_payment)

        db.session.commit()

        # OperationHistory logini yozish
        try:
            history = OperationHistory(
                operation_type='debt_payment',
                table_name='debt_payments',
                record_id=debt_payment.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{customer.name if customer else 'Mijoz'} qarziga to'lov: ${float(payment_usd - remaining_payment):.2f}",
                old_data={'previous_debt': str(previous_total_debt)},
                new_data={
                    'payment_usd': str(payment_usd - remaining_payment),
                    'cash_usd': str(cash_usd),
                    'click_usd': str(click_usd),
                    'terminal_usd': str(terminal_usd),
                    'remaining_debt': str(total_remaining_debt),
                    'updated_sales': updated_sales
                },
                ip_address=request.remote_addr,
                location_id=None,
                location_type=None,
                location_name=None,
                amount=float(payment_usd - remaining_payment)
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        # Telegram orqali mijozga xabar yuborish
        if customer and customer.telegram_chat_id:
            try:
                from debt_scheduler import get_scheduler_instance

                scheduler = get_scheduler_instance(app, db)

                telegram_result = scheduler.bot.send_payment_confirmation_sync(
                    chat_id=customer.telegram_chat_id,
                    customer_name=customer.name,
                    previous_debt_usd=float(previous_total_debt),
                    previous_debt_uzs=0,  # Not used anymore
                    paid_usd=float(payment_usd - remaining_payment),
                    paid_uzs=0,  # Not used anymore
                    remaining_usd=float(total_remaining_debt),
                    remaining_uzs=0,  # Not used anymore
                    customer_id=customer_id,
                    cash_uzs=float(cash_usd),  # Actually USD
                    click_uzs=float(click_usd),  # Actually USD
                    terminal_uzs=float(terminal_usd)  # Actually USD
                )

                if telegram_result:
                    logger.info(f"âœ… To'lov tasdiq xabari yuborildi: {customer.name}")
                else:
                    logger.warning(f"âš ï¸ To'lov tasdiq xabari yuborilmadi: {customer.name}")

            except Exception as e:
                logger.error(f"âŒ Telegram xabar yuborishda xatolik: {e}")

        return jsonify({
            'success': True,
            'message': 'To\'lov muvaffaqiyatli amalga oshirildi',
            'updated_sales': updated_sales,
            'paid_amount': float(payment_usd - remaining_payment)
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Qarzga to'lov xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/edit-user/<int:user_id>')
# @role_required('admin', 'kassir')  # Test uchun vaqtincha o'chirilgan
def edit_user_page(user_id):
    return render_template(
        'edit_user.html',
        page_title='Foydalanuvchini Tahrirlash',
        icon='âœï¸')


@app.route('/add-user')
@role_required('admin', 'kassir')
def add_user_page():
    return render_template(
        'add_user.html',
        page_title='Yangi Foydalanuvchi',
        icon='ğŸ‘¤')


# Database jadvallarini yaratish
@app.before_request
def create_tables():
    if not hasattr(create_tables, 'created'):
        db.create_all()
        create_tables.created = True

    # Test ombor stocklari o'chirildi - manual ravishda qo'shiladi


@app.before_request
def check_user_status():
    """Har request da foydalanuvchining faol ekanligini va session statusini tekshirish"""
    try:
        # Faqat authenticated foydalanuvchilar uchun
        user_id = session.get('user_id')
        session_id = session.get('session_id')

        if user_id:
            # Static fayllar va login sahifalari uchun tekshirmaslik
            if (request.endpoint
                    and (request.endpoint == 'static'
                         or request.endpoint == 'login_page'
                         or request.endpoint == 'api_login')):
                return

            # Session ID mavjudligini tekshirish
            if session_id:
                # Database'dan session holatini tekshirish
                user_session = UserSession.query.filter_by(
                    session_id=session_id,
                    user_id=user_id
                ).first()

                # Agar session topilmasa yoki faol bo'lmasa - logout
                if not user_session or not user_session.is_active:
                    username = session.get('username', 'Unknown')
                    app.logger.info(f"ğŸš« Session bekor qilingan yoki faol emas: {username} (ID: {user_id})")

                    session.clear()

                    # AJAX request uchun
                    if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                        return jsonify({
                            'error': 'Sessiyangiz boshqa kompyuterda ochilgan. Qayta login qiling.',
                            'redirect': '/login',
                            'logout': True
                        }), 401
                    else:
                        return redirect('/login?message=session_expired')

            # Foydalanuvchi faol ekanligini tekshirish
            user = User.query.get(user_id)

            if not user or not user.is_active:
                # Foydalanuvchi faol emas - logout qilish
                username = session.get('username', 'Unknown')
                app.logger.info(f"ğŸš« Faol emas foydalanuvchi avtomatik logout: {username} (ID: {user_id})")

                session.clear()

                # Agar AJAX request bo'lsa, JSON javob qaytarish
                if request.is_json or request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                    return jsonify({
                        'error': 'Hisobingiz faol emas. Qayta login qiling.',
                        'redirect': '/login',
                        'logout': True
                    }), 401
                else:
                    # Oddiy request uchun login sahifasiga redirect
                    return redirect('/login?message=account_disabled')

    except Exception as e:
        # Xato bo'lsa ham request'ni davom ettirish
        app.logger.debug(f"User status tekshirish xatosi: {e}")
        pass


# Stock tahrirlash route
@app.route('/edit_stock/<int:warehouse_id>/<int:product_id>',
           methods=['GET', 'POST'])
def edit_stock(warehouse_id, product_id):
    stock = WarehouseStock.query.filter_by(
        warehouse_id=warehouse_id,
        product_id=product_id
    ).first_or_404()

    # Hisob-kitoblar - to'g'ri cost_price va sell_price dan
    cost_price = stock.product.cost_price
    sell_price = stock.product.sell_price
    unit_profit = sell_price - cost_price  # Birlik foyda
    total_cost = cost_price * stock.quantity
    total_sell = sell_price * stock.quantity
    profit_percent = (float((unit_profit / cost_price * 100))
                      if cost_price > 0 else 0)

    calculations = {
        'cost_price': float(cost_price),
        'sell_price': float(sell_price),
        'unit_profit': float(unit_profit),  # Birlik foyda
        'total_cost': float(total_cost),
        'total_sell': float(total_sell),
        'profit': float(unit_profit),  # Birlik foyda sifatida
        'profit_percent': profit_percent,
        'quantity': stock.quantity
    }

    if request.method == 'POST':
        try:
            # Form ma'lumotlarini olish
            new_quantity = int(float(request.form['quantity']))
            new_product_name = request.form['productName'].strip()
            new_barcode = request.form.get('barcode', '').strip()
            new_cost_price = float(request.form['costPrice'])
            new_sell_price = float(request.form['sellPrice'])
            new_min_stock = int(float(request.form.get('minStock', 0)))

            # Validatsiya
            if new_quantity < 0:
                return render_template(
                    'edit_stock.html',
                    stock=stock,
                    calculations=calculations,
                    error='Miqdor manfiy bo\'lishi mumkin emas')

            if not new_product_name:
                return render_template(
                    'edit_stock.html',
                    stock=stock,
                    calculations=calculations,
                    error='Mahsulot nomi bo\'sh bo\'lishi mumkin emas')

            if new_cost_price < 0 or new_sell_price < 0:
                return render_template(
                    'edit_stock.html',
                    stock=stock,
                    calculations=calculations,
                    error='Narxlar manfiy bo\'lishi mumkin emas')

            # Sotish narxi tan narxidan past bo'lmasligini tekshirish
            if new_sell_price < new_cost_price:
                return render_template('edit_stock.html',
                                       stock=stock,
                                       calculations=calculations,
                                       error='Sotish narxi tan narxidan past '
                                             'bo\'lishi mumkin emas!')

            # Barcode tekshiruvi - agar yangi barcode mavjud bo'lsa
            if new_barcode:
                existing_barcode_product = Product.query.filter(
                    Product.barcode == new_barcode,
                    Product.id != product_id
                ).first()
                if existing_barcode_product:
                    return render_template('edit_stock.html',
                                           stock=stock,
                                           calculations=calculations,
                                           error=f'Siz {new_barcode} barcode raqamini kirityapsiz, lekin bu barcode allaqachon "{existing_barcode_product.name}" mahsulotida mavjud.')

            # Mahsulot ma'lumotlarini yangilash
            stock.product.name = new_product_name
            stock.product.barcode = new_barcode if new_barcode else None  # Barcode yangilash
            stock.product.min_stock = new_min_stock

            # Cost price va sell price ni alohida saqlash
            stock.product.cost_price = Decimal(str(new_cost_price))
            stock.product.sell_price = Decimal(str(new_sell_price))

            # Stock miqdorini yangilash
            old_quantity = stock.quantity
            stock.quantity = new_quantity

            db.session.commit()

            # OperationHistory logini yozish
            try:
                warehouse = Warehouse.query.get(warehouse_id)
                history = OperationHistory(
                    operation_type='edit_stock',
                    table_name='warehouse_stock',
                    record_id=stock.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"{new_product_name} tahrirlandi: miqdor {old_quantity} -> {new_quantity}, narx ${new_cost_price:.2f} -> ${new_sell_price:.2f}",
                    old_data={'quantity': str(old_quantity), 'cost_price': str(cost_price), 'sell_price': str(sell_price)},
                    new_data={'quantity': str(new_quantity), 'cost_price': str(new_cost_price), 'sell_price': str(new_sell_price)},
                    ip_address=request.remote_addr,
                    location_id=warehouse_id,
                    location_type='warehouse',
                    location_name=warehouse.name if warehouse else 'Unknown',
                    amount=None
                )
                db.session.add(history)
                db.session.commit()
            except Exception as log_error:
                logger.error(f"OperationHistory log xatoligi: {log_error}")

            return redirect(url_for('warehouse_detail',
                                    warehouse_id=warehouse_id))

        except ValueError as ve:
            return render_template('edit_stock.html',
                                   stock=stock,
                                   calculations=calculations,
                                   error=f'Yaroqsiz ma\'lumot kiritildi: '
                                   f'{str(ve)}')
        except Exception as e:
            db.session.rollback()
            # Barcode unique constraint error
            error_msg = str(e)
            if 'unique_barcode' in error_msg or 'UniqueViolation' in error_msg:
                # Barcode raqamini topish
                import re
                barcode_match = re.search(r'\(barcode\)=\((\d+)\)', error_msg)
                barcode_num = barcode_match.group(1) if barcode_match else new_barcode
                return render_template('edit_stock.html',
                                       stock=stock,
                                       calculations=calculations,
                                       error=f'Siz {barcode_num} barcode raqamini kirityapsiz, lekin bu barcode allaqachon boshqa mahsulotda mavjud.')
            return render_template('edit_stock.html',
                                   stock=stock,
                                   calculations=calculations,
                                   error=f'Xatolik: {str(e)}')

    return render_template('edit_stock.html', stock=stock,
                           calculations=calculations)


@app.route('/warehouse/<int:warehouse_id>')
def warehouse_detail(warehouse_id):
    """Optimized warehouse detail view - loads only basic info, stock data loaded via AJAX"""
    warehouse = Warehouse.query.get_or_404(warehouse_id)

    # Faqat asosiy statistikani hisoblash (tezkor query)
    from sqlalchemy import func, case

    try:
        # Aggregated statistics - much faster than loading all records
        stats = db.session.query(
            func.count(WarehouseStock.id).label('total_products'),
            func.sum(WarehouseStock.quantity).label('total_quantity'),
            func.sum(WarehouseStock.quantity * Product.sell_price).label('total_value'),
            func.sum(WarehouseStock.quantity * Product.cost_price).label('total_cost_value'),
            func.sum(WarehouseStock.quantity * (Product.sell_price - Product.cost_price)).label('total_profit'),
            func.sum(case((WarehouseStock.quantity == 0, 1), else_=0)).label('critical_stock_count')
        ).join(Product).filter(WarehouseStock.warehouse_id == warehouse_id).first()

        # Safe values
        total_products = stats.total_products or 0
        total_quantity = int(stats.total_quantity or 0)
        total_value = stats.total_value or Decimal('0')
        total_cost_value = stats.total_cost_value or Decimal('0')
        total_profit = stats.total_profit or Decimal('0')
        critical_stock_count = int(stats.critical_stock_count or 0)

        # Profit percentage
        profit_percentage = 0
        if total_cost_value > 0:
            profit_percentage = (total_profit / total_cost_value) * 100

    except Exception as e:
        app.logger.error(f"Error calculating warehouse stats: {str(e)}")
        # Fallback values
        total_products = 0
        total_quantity = 0
        total_value = Decimal('0')
        total_cost_value = Decimal('0')
        total_profit = Decimal('0')
        profit_percentage = Decimal('0')
        critical_stock_count = 0

    return render_template('warehouse_detail.html',
                           warehouse=warehouse,
                           total_products=total_products,
                           total_quantity=total_quantity,
                           total_value=total_value,
                           total_cost_value=total_cost_value,
                           total_profit=total_profit,
                           profit_percentage=profit_percentage,
                           critical_stock_count=critical_stock_count)


# Store stock tahrirlash route
@app.route('/edit_store_stock/<int:store_id>/<int:product_id>',
           methods=['GET', 'POST'])
@role_required('admin', 'kassir', 'sotuvchi')
@location_permission_required('store_id')
def edit_store_stock(store_id, product_id):
    print(
        f"ğŸ” DEBUG: edit_store_stock called with store_id={store_id}, product_id={product_id}")
    stock = StoreStock.query.filter_by(
        store_id=store_id,
        product_id=product_id
    ).first_or_404()
    print(f"ğŸ” DEBUG: Stock found: {stock.product.name}, quantity: {stock.quantity}")

    # Hisob-kitoblar - to'g'ri cost_price va sell_price dan
    cost_price = stock.product.cost_price
    sell_price = stock.product.sell_price
    unit_profit = sell_price - cost_price  # Birlik foyda
    total_cost = cost_price * stock.quantity
    total_sell = sell_price * stock.quantity
    profit_percent = (float((unit_profit / cost_price * 100))
                      if cost_price > 0 else 0)

    calculations = {
        'cost_price': float(cost_price),
        'sell_price': float(sell_price),
        'unit_profit': float(unit_profit),  # Birlik foyda
        'total_cost': float(total_cost),
        'total_sell': float(total_sell),
        'profit': float(unit_profit),  # Birlik foyda sifatida
        'profit_percent': profit_percent,
        'quantity': stock.quantity
    }

    if request.method == 'POST':
        try:
            # Form ma'lumotlarini olish
            new_quantity = int(float(request.form['quantity']))
            new_product_name = request.form['productName'].strip()
            new_barcode = request.form.get('barcode', '').strip()
            new_cost_price = float(request.form['costPrice'])
            new_sell_price = float(request.form['sellPrice'])
            new_min_stock = int(float(request.form.get('minStock', 0)))

            # Validatsiya
            if new_quantity < 0:
                return render_template(
                    'edit_stock.html',
                    stock=stock,
                    calculations=calculations,
                    store=stock.store,
                    error='Miqdor manfiy bo\'lishi mumkin emas')

            if not new_product_name:
                return render_template(
                    'edit_stock.html',
                    stock=stock,
                    calculations=calculations,
                    store=stock.store,
                    error='Mahsulot nomi bo\'sh bo\'lishi mumkin emas')

            if new_cost_price < 0 or new_sell_price < 0:
                return render_template(
                    'edit_stock.html',
                    stock=stock,
                    calculations=calculations,
                    store=stock.store,
                    error='Narxlar manfiy bo\'lishi mumkin emas')

            # Sotish narxi tan narxidan past bo'lmasligini tekshirish
            if new_sell_price < new_cost_price:
                return render_template('edit_stock.html',
                                       stock=stock,
                                       calculations=calculations,
                                       store=stock.store,
                                       error='Sotish narxi tan narxidan past '
                                             'bo\'lishi mumkin emas!')

            # Barcode tekshiruvi - agar yangi barcode mavjud bo'lsa
            if new_barcode:
                existing_barcode_product = Product.query.filter(
                    Product.barcode == new_barcode,
                    Product.id != product_id
                ).first()
                if existing_barcode_product:
                    return render_template('edit_stock.html',
                                           stock=stock,
                                           calculations=calculations,
                                           store=stock.store,
                                           error=f'Siz {new_barcode} barcode raqamini kirityapsiz, lekin bu barcode allaqachon "{existing_barcode_product.name}" mahsulotida mavjud.')

            # Mahsulot ma'lumotlarini yangilash
            stock.product.name = new_product_name
            stock.product.barcode = new_barcode if new_barcode else None  # Barcode yangilash
            stock.product.min_stock = new_min_stock

            # Cost price va sell price ni alohida saqlash
            stock.product.cost_price = Decimal(str(new_cost_price))
            stock.product.sell_price = Decimal(str(new_sell_price))

            # Stock miqdorini yangilash
            old_quantity = stock.quantity
            stock.quantity = new_quantity

            db.session.commit()

            # OperationHistory logini yozish
            try:
                store = Store.query.get(store_id)
                history = OperationHistory(
                    operation_type='edit_stock',
                    table_name='store_stock',
                    record_id=stock.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"{new_product_name} tahrirlandi: miqdor {old_quantity} -> {new_quantity}, narx ${new_cost_price:.2f} -> ${new_sell_price:.2f}",
                    old_data={'quantity': str(old_quantity), 'cost_price': str(cost_price), 'sell_price': str(sell_price)},
                    new_data={'quantity': str(new_quantity), 'cost_price': str(new_cost_price), 'sell_price': str(new_sell_price)},
                    ip_address=request.remote_addr,
                    location_id=store_id,
                    location_type='store',
                    location_name=store.name if store else 'Unknown',
                    amount=None
                )
                db.session.add(history)
                db.session.commit()
            except Exception as log_error:
                logger.error(f"OperationHistory log xatoligi: {log_error}")

            return redirect(url_for('store_detail',
                                    store_id=store_id))

        except ValueError as ve:
            return render_template('edit_stock.html',
                                   stock=stock,
                                   calculations=calculations,
                                   store=stock.store,
                                   error=f'Yaroqsiz ma\'lumot kiritildi: '
                                   f'{str(ve)}')
        except Exception as e:
            db.session.rollback()
            # Barcode unique constraint error
            error_msg = str(e)
            if 'unique_barcode' in error_msg or 'UniqueViolation' in error_msg:
                # Barcode raqamini topish
                import re
                barcode_match = re.search(r'\(barcode\)=\((\d+)\)', error_msg)
                barcode_num = barcode_match.group(1) if barcode_match else new_barcode
                return render_template('edit_stock.html',
                                       stock=stock,
                                       calculations=calculations,
                                       store=stock.store,
                                       error=f'Siz {barcode_num} barcode raqamini kirityapsiz, lekin bu barcode allaqachon boshqa mahsulotda mavjud.')
            return render_template('edit_stock.html',
                                   stock=stock,
                                   calculations=calculations,
                                   store=stock.store,
                                   error=f'Xatolik: {str(e)}')

    logger.debug(f" DEBUG: Rendering template with stock={stock.product.name}")
    return render_template('edit_stock.html', stock=stock,
                           calculations=calculations, store=stock.store)


# Faqat store stock miqdorini yangilash (stock checking uchun)
@app.route('/api/update_store_stock/<int:store_id>/<int:product_id>',
           methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
@location_permission_required('store_id')
def update_store_stock_quantity(store_id, product_id):
    try:
        print(
            f"ğŸ”„ API: Store stock miqdor yangilash: store_id={store_id}, product_id={product_id}")

        stock = StoreStock.query.filter_by(
            store_id=store_id,
            product_id=product_id
        ).first()

        if not stock:
            logger.error(" API: Stock topilmadi")
            return jsonify({'error': 'Stock topilmadi'}), 404

        # Yangi miqdorni olish
        new_quantity = float(request.form.get('quantity', 0))
        logger.info(f" API: Yangi miqdor: {new_quantity}")

        # Validatsiya
        if new_quantity < 0:
            return jsonify(
                {'error': 'Miqdor manfiy bo\'lishi mumkin emas'}), 400

        # Stock miqdorini yangilash
        old_quantity = float(stock.quantity)
        stock.quantity = new_quantity
        db.session.commit()

        print(
            f"âœ… API: {stock.product.name} stock yangilandi: {old_quantity} -> {new_quantity}")

        # OperationHistory logini yozish
        try:
            store = Store.query.get(store_id)
            history = OperationHistory(
                operation_type='edit_stock',
                table_name='store_stock',
                record_id=stock.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{stock.product.name} miqdori o'zgartirildi: {old_quantity} -> {new_quantity}",
                old_data={'quantity': str(old_quantity)},
                new_data={'quantity': str(new_quantity)},
                ip_address=request.remote_addr,
                location_id=store_id,
                location_type='store',
                location_name=store.name if store else 'Unknown',
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': f'{stock.product.name} miqdori yangilandi',
            'old_quantity': old_quantity,
            'new_quantity': new_quantity
        }), 200

    except Exception as e:
        print(f"ğŸ’¥ API xatoligi: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Faqat warehouse stock miqdorini yangilash (stock checking uchun)
@app.route('/api/update_warehouse_stock/<int:warehouse_id>/<int:product_id>',
           methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
@location_permission_required('warehouse_id')
def update_warehouse_stock_quantity(warehouse_id, product_id):
    try:
        print(
            f"ğŸ”„ API: Warehouse stock miqdor yangilash: warehouse_id={warehouse_id}, product_id={product_id}")

        stock = WarehouseStock.query.filter_by(
            warehouse_id=warehouse_id,
            product_id=product_id
        ).first()

        if not stock:
            logger.error(" API: Stock topilmadi")
            return jsonify({'error': 'Stock topilmadi'}), 404

        # Yangi miqdorni olish
        new_quantity = float(request.form.get('quantity', 0))
        logger.info(f" API: Yangi miqdor: {new_quantity}")

        # Validatsiya
        if new_quantity < 0:
            return jsonify(
                {'error': 'Miqdor manfiy bo\'lishi mumkin emas'}), 400

        # Stock miqdorini yangilash
        old_quantity = float(stock.quantity)
        stock.quantity = new_quantity
        db.session.commit()

        print(
            f"âœ… API: {stock.product.name} stock yangilandi: {old_quantity} -> {new_quantity}")

        # OperationHistory logini yozish
        try:
            warehouse = Warehouse.query.get(warehouse_id)
            history = OperationHistory(
                operation_type='edit_stock',
                table_name='warehouse_stock',
                record_id=stock.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{stock.product.name} miqdori o'zgartirildi: {old_quantity} -> {new_quantity}",
                old_data={'quantity': str(old_quantity)},
                new_data={'quantity': str(new_quantity)},
                ip_address=request.remote_addr,
                location_id=warehouse_id,
                location_type='warehouse',
                location_name=warehouse.name if warehouse else 'Unknown',
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': f'{stock.product.name} miqdori yangilandi',
            'old_quantity': old_quantity,
            'new_quantity': new_quantity
        }), 200

    except Exception as e:
        print(f"ğŸ’¥ API xatoligi: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# ========== STOCK CHECK SESSION API'LAR ==========

# Qoldiq tekshirish sessionini boshlash
@app.route('/api/start-stock-check', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def start_stock_check():
    """Qoldiq tekshirish sessionini boshlash"""
    try:
        data = request.get_json()
        location_id = data.get('location_id')
        location_type = data.get('location_type')  # 'store' or 'warehouse'
        location_name = data.get('location_name')

        if not all([location_id, location_type, location_name]):
            return jsonify({'error': 'Barcha maydonlar talab qilinadi'}), 400

        # Joylashuvda aktiv session bor yoki yo'qligini tekshirish
        existing_session = db.session.execute(text("""
            SELECT s.id, u.first_name || ' ' || u.last_name as full_name, s.started_at
            FROM stock_check_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.location_id = :location_id
            AND s.location_type = :location_type
            AND s.status = 'active'
        """), {
            'location_id': location_id,
            'location_type': location_type
        }).first()

        if existing_session:
            return jsonify({
                'error': f'Bu joylashuv hozir {existing_session.full_name} tomonidan tekshirilmoqda',
                'active_user': existing_session.full_name,
                'started_at': existing_session.started_at.isoformat()
            }), 409  # Conflict

        # Yangi session yaratish
        db.session.execute(text("""
            INSERT INTO stock_check_sessions
            (user_id, location_id, location_type, location_name, started_at, updated_at, status)
            VALUES (:user_id, :location_id, :location_type, :location_name, NOW(), NOW(), 'active')
        """), {
            'user_id': session.get('user_id'),
            'location_id': location_id,
            'location_type': location_type,
            'location_name': location_name
        })
        db.session.commit()

        logger.info(f"âœ… Stock check session boshlandi: {location_name} ({location_type} #{location_id}) - User: {session.get('user_id')}")

        return jsonify({
            'success': True,
            'message': 'Qoldiq tekshirish boshlandi'
        }), 200

    except Exception as e:
        logger.error(f"âŒ Start stock check error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Aktiv sessionlarni olish
@app.route('/api/get-active-sessions', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def get_active_sessions():
    """Barcha aktiv qoldiq tekshirish sessionlarini olish"""
    try:
        sessions = db.session.execute(text("""
            SELECT
                s.id,
                s.location_id,
                s.location_type,
                s.location_name,
                u.first_name || ' ' || u.last_name as user_name,
                s.started_at,
                s.updated_at
            FROM stock_check_sessions s
            JOIN users u ON s.user_id = u.id
            WHERE s.status = 'active'
            ORDER BY s.started_at DESC
        """)).fetchall()

        result = []
        for s in sessions:
            result.append({
                'id': s.id,
                'location_id': s.location_id,
                'location_type': s.location_type,
                'location_name': s.location_name,
                'user_name': s.user_name,
                'started_at': s.started_at.isoformat() if s.started_at else None,
                'updated_at': s.updated_at.isoformat() if s.updated_at else None
            })

        return jsonify({'sessions': result}), 200

    except Exception as e:
        logger.error(f"âŒ Get active sessions error: {e}")
        return jsonify({'error': str(e)}), 500


# Sessionni yangilash (heartbeat)
@app.route('/api/update-stock-check-session', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def update_stock_check_session():
    """Session'ni aktiv deb belgilash (heartbeat)"""
    try:
        data = request.get_json()
        location_id = data.get('location_id')
        location_type = data.get('location_type')

        db.session.execute(text("""
            UPDATE stock_check_sessions
            SET updated_at = NOW()
            WHERE user_id = :user_id
            AND location_id = :location_id
            AND location_type = :location_type
            AND status = 'active'
        """), {
            'user_id': session.get('user_id'),
            'location_id': location_id,
            'location_type': location_type
        })
        db.session.commit()

        return jsonify({'success': True}), 200

    except Exception as e:
        logger.error(f"âŒ Update session error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Sessionni tugatish
@app.route('/api/end-stock-check', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def end_stock_check():
    """Qoldiq tekshirish sessionini tugatish"""
    try:
        data = request.get_json()
        location_id = data.get('location_id')
        location_type = data.get('location_type')
        status = data.get('status', 'completed')  # 'completed' or 'cancelled'

        if status not in ['completed', 'cancelled']:
            return jsonify({'error': 'Noto\'g\'ri status'}), 400

        db.session.execute(text("""
            UPDATE stock_check_sessions
            SET status = :status, updated_at = NOW()
            WHERE user_id = :user_id
            AND location_id = :location_id
            AND location_type = :location_type
            AND status = 'active'
        """), {
            'user_id': session.get('user_id'),
            'location_id': location_id,
            'location_type': location_type,
            'status': status
        })
        db.session.commit()

        logger.info(f"âœ… Stock check session tugatildi: {location_type} #{location_id} - Status: {status}")

        return jsonify({
            'success': True,
            'message': 'Session tugatildi'
        }), 200

    except Exception as e:
        logger.error(f"âŒ End stock check error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Eski sessionlarni tozalash (1 soatdan ortiq)
@app.route('/api/cleanup-old-sessions', methods=['POST'])
@role_required('admin')
def cleanup_old_sessions():
    """1 soatdan ortiq aktiv sessionlarni avtomatik yopish"""
    try:
        result = db.session.execute(text("""
            UPDATE stock_check_sessions
            SET status = 'cancelled', updated_at = NOW()
            WHERE status = 'active'
            AND updated_at < NOW() - INTERVAL '1 hour'
            RETURNING id, location_name
        """))
        closed_sessions = result.fetchall()
        db.session.commit()

        count = len(closed_sessions)
        logger.info(f"ğŸ§¹ Tozalash: {count} ta eski session yopildi")

        return jsonify({
            'success': True,
            'closed_count': count,
            'message': f'{count} ta eski session yopildi'
        }), 200

    except Exception as e:
        logger.error(f"âŒ Cleanup error: {e}")
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Store stock o'chirish route
@app.route('/api/store_stock/<int:store_id>/<int:product_id>', methods=['DELETE'])
def delete_store_stock(store_id, product_id):
    try:
        print(
            f"ğŸŸ¡ Store stock o'chirish so'rovi: Store ID: {store_id}, Product ID: {product_id}")

        stock = StoreStock.query.filter_by(
            store_id=store_id,
            product_id=product_id
        ).first()

        if not stock:
            print(
                f"ğŸ”´ Stock topilmadi: Store ID: {store_id}, Product ID: {product_id}")
            return jsonify({
                'success': False,
                'error': 'Mahsulot bu do\'konda topilmadi'
            }), 404

        product = stock.product
        product_name = product.name

        # Mahsulot boshqa joylarda mavjudligini tekshirish
        other_store_stocks = StoreStock.query.filter(
            StoreStock.product_id == product_id,
            StoreStock.store_id != store_id
        ).count()

        warehouse_stocks = WarehouseStock.query.filter_by(
            product_id=product_id
        ).count()

        total_other_locations = other_store_stocks + warehouse_stocks

        # Stock ni o'chirish
        db.session.delete(stock)

        # Agar boshqa stoklarda yo'q bo'lsa - product'ni ham o'chirish
        # ON DELETE SET NULL - product_id NULL bo'ladi, lekin notes'da nom saqlanadi
        deleted_completely = (total_other_locations == 0)
        if deleted_completely:
            db.session.delete(product)

        db.session.commit()

        # OperationHistory logini yozish
        try:
            store = Store.query.get(store_id)
            history = OperationHistory(
                operation_type='delete_stock',
                table_name='store_stock',
                record_id=stock.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{product_name} o'chirildi" + (" (butunlay)" if deleted_completely else " (faqat dokondan)"),
                old_data={'product_name': product_name, 'quantity': str(stock.quantity)},
                new_data=None,
                ip_address=request.remote_addr,
                location_id=store_id,
                location_type='store',
                location_name=store.name if store else 'Unknown',
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        if deleted_completely:
            return jsonify({
                'success': True,
                'message': f'{product_name} mahsuloti butunlay o\'chirildi (tarixda notes bilan saqlanadi)',
                'deleted_completely': True
            })

        return jsonify({
            'success': True,
            'message': f'{product_name} bu do\'kondan o\'chirildi (boshqa joylarda hali mavjud)',
            'deleted_completely': False,
            'other_locations': total_other_locations
        })

    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        print("ğŸ”´ Store stock o'chirishda xatolik!")
        print(f"ğŸ”´ Store ID: {store_id}, Product ID: {product_id}")
        print(f"ğŸ”´ Xatolik: {error_msg}")
        import traceback
        print(f"ğŸ”´ Traceback:\n{traceback.format_exc()}")

        logger.error(f"Store stock o'chirishda xatolik: {error_msg}")
        logger.error(f"Store ID: {store_id}, Product ID: {product_id}")
        logger.error(f"Traceback: {traceback.format_exc()}")

        return jsonify({
            'success': False,
            'error': error_msg,
            'details': f'Store ID: {store_id}, Product ID: {product_id}'
        }), 500


# Warehouse stock o'chirish route
@app.route('/api/warehouse_stock/<int:warehouse_id>/<int:product_id>',
           methods=['DELETE'])
def delete_warehouse_stock(warehouse_id, product_id):
    try:
        stock = WarehouseStock.query.filter_by(
            warehouse_id=warehouse_id,
            product_id=product_id
        ).first_or_404()

        product = stock.product
        product_name = product.name

        # Mahsulot boshqa joylarda mavjudligini tekshirish
        other_warehouse_stocks = WarehouseStock.query.filter(
            WarehouseStock.product_id == product_id,
            WarehouseStock.warehouse_id != warehouse_id
        ).count()

        store_stocks = StoreStock.query.filter_by(
            product_id=product_id
        ).count()

        total_other_locations = other_warehouse_stocks + store_stocks

        # Agar mahsulot faqat shu joyda mavjud bo'lsa - butunlay o'chirish
        deleted_completely = (total_other_locations == 0)
        # Avval stock ni o'chirish
        db.session.delete(stock)
        if deleted_completely:
            # Keyin productni ham o'chirish
            db.session.delete(product)

        db.session.commit()

        # OperationHistory logini yozish
        try:
            warehouse = Warehouse.query.get(warehouse_id)
            history = OperationHistory(
                operation_type='delete_stock',
                table_name='warehouse_stock',
                record_id=stock.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{product_name} o'chirildi" + (" (butunlay)" if deleted_completely else " (faqat ombordan)"),
                old_data={'product_name': product_name, 'quantity': str(stock.quantity)},
                new_data=None,
                ip_address=request.remote_addr,
                location_id=warehouse_id,
                location_type='warehouse',
                location_name=warehouse.name if warehouse else 'Unknown',
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        if deleted_completely:
            return jsonify({
                'success': True,
                'message': f'{product_name} mahsuloti butunlay o\'chirildi (faqat bu joyda mavjud edi)',
                'deleted_completely': True
            })
        else:
            # Faqat shu joydagi stock ni o'chirish
            return jsonify({
                'success': True,
                'message': f'{product_name} bu ombordan o\'chirildi (boshqa joylarda hali mavjud)',
                'deleted_completely': False,
                'other_locations': total_other_locations
            })

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# Database ma'lumotlarini tekshirish uchun debug endpoint
@app.route('/api/debug/products')
def debug_products():
    """Barcha mahsulotlarni ko'rish (faqat development)"""
    if not app.debug:
        abort(404)  # Production'da ko'rsatmaslik
    try:
        # âœ… Eager loading - N+1 query muammosini hal qilish
        from sqlalchemy.orm import joinedload

        products = Product.query.options(
            joinedload(Product.warehouse_stocks).joinedload(WarehouseStock.warehouse),
            joinedload(Product.store_stocks).joinedload(StoreStock.store)
        ).all()

        products_data = []

        for product in products:
            # âœ… Eager loading natijasida stocks allaqachon yuklangan
            warehouse_data = []
            for ws in product.warehouse_stocks:
                if ws.warehouse:  # Eager loaded
                    warehouse_data.append({
                        'warehouse_name': ws.warehouse.name,
                        'quantity': float(ws.quantity)
                    })

            store_data = []
            for ss in product.store_stocks:
                if ss.store:  # Eager loaded
                    store_data.append({
                        'store_name': ss.store.name,
                        'quantity': float(ss.quantity)
                    })

            products_data.append({
                'id': product.id,
                'name': product.name,
                'cost_price': float(product.cost_price),
                'sell_price': float(product.sell_price),
                'min_stock': product.min_stock,
                'warehouses': warehouse_data,
                'stores': store_data
            })

        return jsonify({
            'total_products': len(products),
            'products': products_data
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/debug/stats')
def debug_stats():
    """Database statistikasi (faqat development)"""
    if not app.debug:
        abort(404)  # Production'da ko'rsatmaslik
    try:
        stats = {
            'products_count': Product.query.count(),
            'warehouses_count': Warehouse.query.count(),
            'stores_count': Store.query.count(),
            'warehouse_stocks_count': WarehouseStock.query.count(),
            'store_stocks_count': StoreStock.query.count()
        }
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# Yetim mahsulotlarni tozalash API
@app.route('/api/cleanup-orphan-products', methods=['POST'])
def cleanup_orphan_products():
    try:
        # Hech qayerda stock mavjud bo'lmagan mahsulotlarni topish
        orphan_products = db.session.query(Product).filter(
            ~Product.id.in_(
                db.session.query(WarehouseStock.product_id).distinct()
            ),
            ~Product.id.in_(
                db.session.query(StoreStock.product_id).distinct()
            )
        ).all()

        orphan_count = len(orphan_products)
        orphan_names = [p.name for p in orphan_products]

        # Yetim mahsulotlarni o'chirish
        for product in orphan_products:
            db.session.delete(product)

        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'{orphan_count} ta yetim mahsulot o\'chirildi',
            'deleted_products': orphan_names,
            'count': orphan_count
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Transfer uchun API endpointlar
@app.route('/api/product/<int:product_id>/locations')
def get_product_locations(product_id):
    """Mahsulotning barcha joylashuv va miqdorlarini qaytarish"""
    logger.debug(f" get_product_locations called for product_id: {product_id}")
    try:
        # Do'konlardagi mahsulotlar (quantity >= 0, chunki 0 ham ko'rsatilishi kerak)
        store_stocks = db.session.query(
            StoreStock.store_id,
            Store.name,
            StoreStock.quantity
        ).join(Store).filter(
            StoreStock.product_id == product_id
        ).all()

        logger.info(f" Found {len(store_stocks)} store stocks")

        # Omborlardagi mahsulotlar (quantity >= 0)
        warehouse_stocks = db.session.query(
            WarehouseStock.warehouse_id,
            Warehouse.name,
            WarehouseStock.quantity
        ).join(Warehouse).filter(
            WarehouseStock.product_id == product_id
        ).all()

        logger.debug(f" Found {len(warehouse_stocks)} warehouse stocks")

        # Debug: har bir stock'ni alohida ko'rsatish
        for stock in store_stocks:
            logger.debug(f" Store Stock: store_id={stock.store_id}, name={stock.name}, quantity={stock.quantity}")

        for stock in warehouse_stocks:
            logger.debug(f" Warehouse Stock: warehouse_id={stock.warehouse_id}, name={stock.name}, quantity={stock.quantity}")

        # Frontend uchun birlashtirish - locations array bilan
        locations = []

        # Do'konlarni qo'shish
        for stock in store_stocks:
            locations.append({
                'id': stock.store_id,
                'type': 'store',
                'name': stock.name,
                'quantity': int(stock.quantity)
            })

        # Omborlarni qo'shish
        for stock in warehouse_stocks:
            locations.append({
                'id': stock.warehouse_id,
                'type': 'warehouse',
                'name': stock.name,
                'quantity': int(stock.quantity)
            })

        logger.debug(f" API response: {len(locations)} locations")
        return jsonify(locations)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/transfer', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
@timeout_monitor(max_seconds=10, operation_name='Transfer')
@check_idempotency('transfer')
def process_transfers():
    """Transferlarni amalga oshirish"""
    print("ğŸ”„ Transfer API called")
    try:
        # Current user tekshirish
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        print(
            f"ğŸ” Transfer API - User: {current_user.username}, Role: {current_user.role}")

        # Sotuvchi uchun transfer huquqi va joylashuv tekshirish
        if current_user.role == 'sotuvchi':
            # Transfer huquqi tekshirish
            permissions = current_user.permissions or {}
            has_transfer_permission = permissions.get('transfer', False)

            # Agar transfer huquqi yo'q bo'lsa, xatolik qaytarish
            if not has_transfer_permission:
                print(
                    f"âŒ User {current_user.username} has no transfer permission")
                return jsonify(
                    {'error': 'Transfer qilish huquqingiz yo\'q'}), 403

            # Transfer joylashuvlari - agar bo'sh bo'lsa, allowed_locations dan foydalanish
            transfer_locations = current_user.transfer_locations or []

            # Agar transfer_locations bo'sh bo'lsa, allowed_locations dan foydalanish
            if not transfer_locations:
                transfer_locations = current_user.allowed_locations or []
                print(f"â„¹ï¸ Transfer locations bo'sh, allowed_locations ishlatilmoqda: {transfer_locations}")

            logger.debug(f" User transfer locations: {transfer_locations}")

            # Agar ikkala list ham bo'sh bo'lsa, faqat o'shanda xatolik qaytarish
            if not transfer_locations:
                print(
                    f"âŒ User {current_user.username} has no transfer locations")
                return jsonify(
                    {'error': 'Transfer qilish uchun ruxsat etilgan joylashuvlar yo\'q'}), 403

        data = request.get_json()
        print(f"ğŸ“¥ Received data: {data}")
        transfers = data.get('transfers', [])
        logger.debug(f" Transfers count: {len(transfers)}")

        if not transfers:
            logger.error(" No transfers provided")
            return jsonify({'error': 'Transfer ro\'yxati bo\'sh'}), 400

        for transfer in transfers:
            logger.info(f" Processing transfer: {transfer}")
            product_id = transfer['product_id']
            from_location = transfer['from_location']
            to_location = transfer['to_location']
            quantity = Decimal(str(transfer['quantity']))  # Decimal ishlatish (0.5 litr uchun)
            print(
                f"ğŸ“¦ Transfer: {product_id} from {from_location} to {to_location}, qty: {quantity}")

            # Sotuvchi uchun from_location ruxsatini tekshirish
            if current_user.role == 'sotuvchi':
                from_type, from_id = from_location.split('_')
                from_location_id = int(from_id)

                # Transfer_locations yoki allowed_locations dan foydalanish
                transfer_locations = current_user.transfer_locations or []
                if not transfer_locations:
                    transfer_locations = current_user.allowed_locations or []

                # Ruxsat borligini tekshirish (yangi va eski formatni qo'llab-quvvatlash)
                if transfer_locations:
                    has_permission = False
                    
                    for loc in transfer_locations:
                        # Yangi format: {'id': 1, 'type': 'warehouse'}
                        if isinstance(loc, dict):
                            if loc.get('id') == from_location_id and loc.get('type') == from_type:
                                has_permission = True
                                print(f"✅ Transfer permission granted: {from_type}_{from_location_id} matches {loc}")
                                break
                        # Eski format: integer (faqat id, type noma'lum)
                        elif isinstance(loc, int):
                            if loc == from_location_id:
                                has_permission = True
                                print(f"✅ Transfer permission granted (old format): location ID {from_location_id}")
                                break
                    
                    if not has_permission:
                        print(
                            f"❌ User {current_user.username} cannot transfer from location {from_location} (type: {from_type}, id: {from_location_id})")
                        print(f"❌ Available transfer locations: {transfer_locations}")
                        return jsonify({
                            'error': f'Bu joylashuvdan ({from_location}) transfer qilish huquqingiz yo\'q. Ruxsat etilgan joylashuvlar: {transfer_locations}'
                        }), 403

            # Transfer tarixiga saqlash
            from_type, from_id = from_location.split('_')
            to_type, to_id = to_location.split('_')

            transfer_record = Transfer(
                product_id=product_id,
                from_location_type=from_type,
                from_location_id=int(from_id),
                to_location_type=to_type,
                to_location_id=int(to_id),
                quantity=quantity,
                user_name='Admin'
            )
            db.session.add(transfer_record)

            # From location dan miqdorni kamaytirish
            if from_location.startswith('store_'):
                store_id = int(from_location.replace('store_', ''))
                store_stock = StoreStock.query.filter_by(
                    store_id=store_id,
                    product_id=product_id
                ).first()

                if not store_stock or store_stock.quantity < quantity:
                    return jsonify(
                        {'error': 'Do\'konda yetarli miqdor yo\'q'}), 400

                store_stock.quantity -= quantity
                # Miqdor 0 bo'lsa ham stockni saqlab qolamiz (o'chirmaymiz)

            elif from_location.startswith('warehouse_'):
                warehouse_id = int(from_location.replace('warehouse_', ''))
                warehouse_stock = WarehouseStock.query.filter_by(
                    warehouse_id=warehouse_id,
                    product_id=product_id
                ).first()

                if not warehouse_stock or warehouse_stock.quantity < quantity:
                    return jsonify(
                        {'error': 'Omborda yetarli miqdor yo\'q'}), 400

                warehouse_stock.quantity -= quantity
                # Miqdor 0 bo'lsa ham stockni saqlab qolamiz (o'chirmaymiz)

            # To location ga miqdorni qo'shish
            if to_location.startswith('store_'):
                store_id = int(to_location.replace('store_', ''))
                store_stock = StoreStock.query.filter_by(
                    store_id=store_id,
                    product_id=product_id
                ).first()

                if store_stock:
                    store_stock.quantity += quantity
                else:
                    # Yangi stock yaratish (StoreStock da cost_price va
                    # sell_price yo'q)
                    new_stock = StoreStock(
                        store_id=store_id,
                        product_id=product_id,
                        quantity=quantity
                    )
                    db.session.add(new_stock)

            elif to_location.startswith('warehouse_'):
                warehouse_id = int(to_location.replace('warehouse_', ''))
                warehouse_stock = WarehouseStock.query.filter_by(
                    warehouse_id=warehouse_id,
                    product_id=product_id
                ).first()

                if warehouse_stock:
                    warehouse_stock.quantity += quantity
                else:
                    # Yangi stock yaratish (WarehouseStock da cost_price va
                    # sell_price yo'q)
                    new_stock = WarehouseStock(
                        warehouse_id=warehouse_id,
                        product_id=product_id,
                        quantity=quantity
                    )
                    db.session.add(new_stock)

            # OperationHistory ga transfer yozish
            product = Product.query.get(product_id)
            from_location_name = ''
            to_location_name = ''

            if from_type == 'store':
                from_store = Store.query.get(int(from_id))
                if from_store:
                    from_location_name = from_store.name
            elif from_type == 'warehouse':
                from_warehouse = Warehouse.query.get(int(from_id))
                if from_warehouse:
                    from_location_name = from_warehouse.name

            if to_type == 'store':
                to_store = Store.query.get(int(to_id))
                if to_store:
                    to_location_name = to_store.name
            elif to_type == 'warehouse':
                to_warehouse = Warehouse.query.get(int(to_id))
                if to_warehouse:
                    to_location_name = to_warehouse.name

            operation = OperationHistory(
                operation_type='transfer',
                table_name='transfers',
                record_id=transfer_record.id,
                user_id=session.get('user_id'),
                username=session.get('username') or 'Admin',
                description=f"Transfer: {product.name} - {from_location_name} â†’ {to_location_name}",
                old_data={
                    'from_location': from_location_name,
                    'from_location_type': from_type
                },
                new_data={
                    'product_id': product_id,
                    'product_name': product.name,
                    'quantity': float(quantity),
                    'to_location': to_location_name,
                    'to_location_type': to_type
                },
                ip_address=request.remote_addr,
                location_id=int(to_id),
                location_type=to_type,
                location_name=to_location_name,
                amount=None
            )
            db.session.add(operation)

        db.session.commit()
        return jsonify(
            {'message': 'Transferlar muvaffaqiyatli amalga oshirildi'})

    except TimeoutError:
        db.session.rollback()
        logger.error("â±ï¸ Database timeout in transfer")
        return jsonify({
            'success': False,
            'error': 'So\'rov juda uzoq davom etdi. Qayta urinib ko\'ring.',
            'error_type': 'timeout'
        }), 504
    except OperationalError as e:
        db.session.rollback()
        logger.error(f"ğŸ”Œ Database connection xatosi: {e}")
        return jsonify({
            'success': False,
            'error': 'Ma\'lumotlar bazasiga ulanishda xatolik',
            'error_type': 'database_connection'
        }), 503
    except IntegrityError as e:
        db.session.rollback()
        logger.error(f"âŒ Integrity error: {e}")
        return jsonify({
            'success': False,
            'error': 'Ma\'lumotlarni saqlashda xatolik',
            'error_type': 'integrity_error'
        }), 400
    except BadRequest as e:
        db.session.rollback()
        logger.error(f"âŒ Bad request: {e}")
        return jsonify({
            'success': False,
            'error': 'Noto\'g\'ri so\'rov formati',
            'error_type': 'bad_request'
        }), 400
    except Exception as e:
        db.session.rollback()
        logger.error(f"âŒ Transfer xatosi: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': str(e),
            'error_type': 'internal_server_error'
        }), 500


@app.route('/api/transfer/history', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def get_transfer_history():
    """Transfer tarixini qaytarish - faqat 40 kunlik ma'lumotlar"""
    try:
        from datetime import datetime, timedelta

        # Avval eski ma'lumotlarni tozalash
        cleanup_old_transfers()

        # 40 kun oldini hisoblash
        forty_days_ago = get_tashkent_time() - timedelta(days=40)

        # Faqat so'nggi 40 kun ichidagi transferlarni olish
        transfers = Transfer.query.filter(
            Transfer.created_at >= forty_days_ago
        ).order_by(Transfer.created_at.desc()).all()

        history_data = []
        for transfer in transfers:
            history_data.append(transfer.to_dict())

        return jsonify(history_data)

    except Exception as e:
        return jsonify({'error': str(e)}), 500


def cleanup_old_transfers():
    """40 kundan eski transferlarni o'chirish"""
    try:
        # 40 kun oldini hisoblash
        forty_days_ago = get_tashkent_time() - timedelta(days=40)

        # Eski transferlarni topish
        old_transfers = Transfer.query.filter(
            Transfer.created_at < forty_days_ago
        ).all()

        # Eski transferlarni o'chirish
        for transfer in old_transfers:
            db.session.delete(transfer)

        # O'zgarishlarni saqlash
        db.session.commit()

        logger.info(f"Tozalandi: {len(old_transfers)} ta eski transfer")

    except Exception as e:
        logger.error(f"Transfer tozalashda xatolik: {str(e)}")
        db.session.rollback()


@app.route('/api/transfer/cleanup', methods=['POST'])
@role_required('admin')
def manual_cleanup_transfers():
    """Qo'lda eski transferlarni tozalash"""
    try:
        # 40 kun oldini hisoblash
        forty_days_ago = get_tashkent_time() - timedelta(days=40)

        # Eski transferlarni topish
        old_transfers = Transfer.query.filter(
            Transfer.created_at < forty_days_ago
        ).all()

        deleted_count = len(old_transfers)

        # Eski transferlarni o'chirish
        for transfer in old_transfers:
            db.session.delete(transfer)

        # O'zgarishlarni saqlash
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'{deleted_count} ta eski transfer o\'chirildi',
            'deleted_count': deleted_count
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/transfer-history')
@role_required('admin', 'kassir', 'sotuvchi')
def get_transfer_history_formatted():
    """Transfer tarixini formatlangan ko'rinishda qaytarish"""
    try:
        limit = request.args.get('limit', 50, type=int)
        limit = min(limit, 200)  # Maximum 200

        # So'nggi transferlarni olish
        transfers = Transfer.query.order_by(
            Transfer.created_at.desc()
        ).limit(limit * 10).all()  # Ko'proq olish, keyin guruhlash

        # Transferlarni guruhlash - bir xil vaqt, from_location, to_location, user
        grouped_transfers = {}

        for transfer in transfers:
            # Joylashuv nomlarini olish
            from_location_name = "N/A"
            to_location_name = "N/A"

            if transfer.from_location_type == 'warehouse':
                warehouse = Warehouse.query.get(transfer.from_location_id)
                from_location_name = warehouse.name if warehouse else f"Ombor #{transfer.from_location_id}"
            elif transfer.from_location_type == 'store':
                store = Store.query.get(transfer.from_location_id)
                from_location_name = store.name if store else f"Dokon #{transfer.from_location_id}"

            if transfer.to_location_type == 'warehouse':
                warehouse = Warehouse.query.get(transfer.to_location_id)
                to_location_name = warehouse.name if warehouse else f"Ombor #{transfer.to_location_id}"
            elif transfer.to_location_type == 'store':
                store = Store.query.get(transfer.to_location_id)
                to_location_name = store.name if store else f"Dokon #{transfer.to_location_id}"

            # Mahsulot ma'lumotlarini olish
            product = Product.query.get(transfer.product_id)
            product_name = product.name if product else f"Mahsulot #{transfer.product_id}"

            # Grupplash kaliti - 1 daqiqa oralig'ida, bir xil joylashuvlar va foydalanuvchi
            if transfer.created_at:
                # 1 daqiqa aniqlik bilan guruhlash
                time_key = transfer.created_at.replace(second=0, microsecond=0)
            else:
                time_key = "unknown"

            group_key = (
                time_key,
                transfer.from_location_type,
                transfer.from_location_id,
                transfer.to_location_type,
                transfer.to_location_id,
                transfer.user_name or 'N/A'
            )

            if group_key not in grouped_transfers:
                grouped_transfers[group_key] = {
                    'created_at': transfer.created_at.isoformat() if transfer.created_at else None,
                    'from_location': from_location_name,
                    'to_location': to_location_name,
                    'user_name': transfer.user_name or 'N/A',
                    'products': []
                }

            grouped_transfers[group_key]['products'].append({
                'name': product_name,
                'quantity': float(transfer.quantity) if transfer.quantity else 0
            })

        # Ro'yxatga aylantirish va limit qo'llash
        history_list = list(grouped_transfers.values())

        # Vaqt bo'yicha saralash (eng yangi birinchi)
        history_list.sort(key=lambda x: x['created_at'] if x['created_at'] else '', reverse=True)

        # Limit qo'llash
        history_list = history_list[:limit]

        return jsonify({
            'transfers': history_list,
            'count': len(history_list)
        })

    except Exception as e:
        logger.error(f"Transfer tarixini olishda xatolik: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/pending-transfer', methods=['GET', 'POST', 'PUT', 'DELETE'])
@app.route('/api/pending-transfer/<int:pending_id>', methods=['GET', 'PUT', 'DELETE'])
@role_required('admin', 'kassir', 'sotuvchi')
def manage_pending_transfer(pending_id=None):
    """Tasdiqlanmagan transferni boshqarish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # GET - ID bo'yicha yoki foydalanuvchining pending transferini olish
        if request.method == 'GET':
            if pending_id:
                # ID bo'yicha olish
                pending = PendingTransfer.query.get(pending_id)
                
                if pending:
                    # Ruxsat tekshirish
                    if not user_can_manage_transfer(current_user, pending):
                        return jsonify({'error': 'Sizga bu transferni ko\'rish uchun ruxsat yo\'q'}), 403
                    
                    return jsonify({
                        'success': True,
                        'pending_transfer': pending.to_dict()
                    })
            else:
                # Barcha ruxsat etilgan pending transferlarni olish
                all_pendings = PendingTransfer.query.order_by(PendingTransfer.updated_at.desc()).all()
                
                # Birinchi ruxsat etilgan transferni topish
                pending = None
                for p in all_pendings:
                    if user_can_manage_transfer(current_user, p):
                        pending = p
                        break
                
                if pending:
                    return jsonify({
                        'success': True,
                        'pending_transfer': pending.to_dict()
                    })
            return jsonify({
                'success': True,
                'pending_transfer': None
            })

        # POST - yangi tasdiqlanmagan transfer yaratish
        elif request.method == 'POST':
            data = request.get_json()

            pending = PendingTransfer(
                user_id=current_user.id,
                from_location_type=data['from_location_type'],
                from_location_id=data['from_location_id'],
                to_location_type=data['to_location_type'],
                to_location_id=data['to_location_id'],
                items=data['items']
            )

            db.session.add(pending)
            db.session.commit()

            return jsonify({
                'success': True,
                'pending_transfer': pending.to_dict()
            })

        # PUT - tasdiqlanmagan transferni yangilash
        elif request.method == 'PUT':
            data = request.get_json()

            if not pending_id:
                return jsonify({'error': 'Transfer ID talab qilinadi'}), 400
            
            pending = PendingTransfer.query.get(pending_id)

            if not pending:
                return jsonify({'error': 'Tasdiqlanmagan transfer topilmadi'}), 404

            # Ruxsat tekshirish
            if not user_can_manage_transfer(current_user, pending):
                return jsonify({'error': 'Sizga bu transferni tahrirlash uchun ruxsat yo\'q'}), 403

            pending.from_location_type = data['from_location_type']
            pending.from_location_id = data['from_location_id']
            pending.to_location_type = data['to_location_type']
            pending.to_location_id = data['to_location_id']
            pending.items = data['items']

            db.session.commit()

            return jsonify({
                'success': True,
                'pending_transfer': pending.to_dict()
            })

        # DELETE - tasdiqlanmagan transferni o'chirish
        elif request.method == 'DELETE':
            if pending_id:
                pending = PendingTransfer.query.get(pending_id)
                if not pending:
                    return jsonify({'error': 'Tasdiqlanmagan transfer topilmadi'}), 404

                # Ruxsat tekshirish
                if not user_can_manage_transfer(current_user, pending):
                    return jsonify({'error': 'Sizga bu transferni o\'chirish uchun ruxsat yo\'q'}), 403

                db.session.delete(pending)
            else:
                # Barcha ruxsat etilgan pending transferlarni o'chirish
                all_pendings = PendingTransfer.query.all()
                for p in all_pendings:
                    if user_can_manage_transfer(current_user, p):
                        db.session.delete(p)

            db.session.commit()

            return jsonify({
                'success': True,
                'message': 'Tasdiqlanmagan transfer o\'chirildi'
            })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Tasdiqlanmagan transferni boshqarishda xatolik: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/all-pending-transfers', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def get_all_pending_transfers():
    """Barcha tasdiqlanmagan transferlarni olish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Admin barcha tasdiqlanmagan transferlarni ko'rishi mumkin
        if current_user.role == 'admin':
            pending_transfers = PendingTransfer.query.all()
        else:
            # Boshqa foydalanuvchilar:
            # 1. O'zining pending transferlari
            # 2. Ikkala joylashuvga ruxsati bor transferlar
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
                to_location_name = store.name if store else f"Dokon #{pending.to_location_id}"

            result.append({
                'id': pending.id,
                'user_name': pending.user.username if pending.user else 'N/A',
                'from_location': from_location_name,
                'to_location': to_location_name,
                'items': pending.items,
                'created_at': pending.created_at.isoformat() if pending.created_at else None,
                'updated_at': pending.updated_at.isoformat() if pending.updated_at else None
            })

        return jsonify({
            'success': True,
            'pending_transfers': result
        })

    except Exception as e:
        logger.error(f"Tasdiqlanmagan transferlarni olishda xatolik: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/product/<int:product_id>', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def get_single_product(product_id):
    """Bitta mahsulotni olish (stokiga qaramay)"""
    try:
        product = Product.query.options(
            db.joinedload(Product.warehouse_stocks),
            db.joinedload(Product.store_stocks)
        ).get_or_404(product_id)

        return jsonify({
            'success': True,
            'product': product.to_dict()
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/product/<int:product_id>', methods=['DELETE'])
def delete_product(product_id):
    """Mahsulotni o'chirish"""
    try:
        product = Product.query.get_or_404(product_id)

        # Mahsulot bilan bog'liq barcha stock'larni o'chirish
        WarehouseStock.query.filter_by(product_id=product_id).delete()
        StoreStock.query.filter_by(product_id=product_id).delete()

        # Mahsulotni o'chirish
        db.session.delete(product)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Mahsulot muvaffaqiyatli o\'chirildi'
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


# Mijozlar API route'lari
@app.route('/api/customers', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def get_customers():
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Qidiruv parametrini olish
        search = request.args.get('search', '').strip()
        time_filter = request.args.get('time_filter', 'all')  # all, today, week, month, year

        # Debug ma'lumotlari
        print(
            f"ğŸ” Customers API - User: {current_user.username}, Role: {current_user.role}, Search: {search}, Time: {time_filter}")
        logger.debug(f" Allowed locations: {current_user.allowed_locations}")

        # Mijozlarni joylashuv bo'yicha filterlash
        if current_user.role == 'sotuvchi':
            # Sotuvchi faqat o'ziga ruxsat berilgan do'konlardagi mijozlarni
            # ko'radi
            allowed_locations = current_user.allowed_locations or []
            logger.debug(f" Filtering customers by locations: {allowed_locations}")

            if allowed_locations:
                # Faqat store ID'larni olish (mijozlar faqat do'konlarda bo'ladi)
                allowed_store_ids = extract_location_ids(allowed_locations, 'store')
                print(f"ğŸ” Allowed store IDs for customers: {allowed_store_ids}")

                if allowed_store_ids:
                    # Faqat ruxsat berilgan do'konlardagi mijozlar
                    query = Customer.query.filter(
                        Customer.store_id.in_(allowed_store_ids))

                    # Qisman so'zlar bilan qidirish
                    if search:
                        search_words = search.lower().split()
                        for word in search_words:
                            if word:
                                query = query.filter(
                                    db.or_(
                                        Customer.name.ilike(f'%{word}%'),
                                        Customer.phone.ilike(f'%{word}%'),
                                        Customer.email.ilike(f'%{word}%')
                                    )
                                )

                    customers = query.all()
                    print(
                        f"ğŸ” Found {len(customers)} customers in allowed stores")
                else:
                    customers = []
                    print("ğŸ” No allowed stores for this user")
            else:
                # Agar ruxsat berilgan joylashuv bo'lmasa, bo'sh ro'yxat
                customers = []
                logger.debug(" No allowed locations, returning empty customer list")
        else:
            # Admin barcha mijozlarni ko'radi
            query = Customer.query

            # Qisman so'zlar bilan qidirish
            if search:
                search_words = search.lower().split()
                for word in search_words:
                    if word:
                        query = query.filter(
                            db.or_(
                                Customer.name.ilike(f'%{word}%'),
                                Customer.phone.ilike(f'%{word}%'),
                                Customer.email.ilike(f'%{word}%')
                            )
                        )

            customers = query.all()
            logger.debug(f" Admin user, returning all {len(customers)} customers")

        # Vaqt filtriga asosan savdo ma'lumotlarini hisoblash
        from datetime import datetime, timedelta
        now = get_tashkent_time()  # Toshkent vaqti

        print(f"â° Time filter: {time_filter}, Toshkent vaqti: {now}")

        # Vaqt oralig'ini aniqlash
        start_date = None
        end_date = None

        if time_filter == 'today':
            # Bugun: kun boshidan kun oxirigacha
            start_date = datetime(now.year, now.month, now.day, 0, 0, 0)
            end_date = datetime(now.year, now.month, now.day, 23, 59, 59)
            print(f"ğŸ“… Bugun filtri: {start_date} - {end_date}")
        elif time_filter == 'week':
            # Oxirgi 7 kun
            start_date = now - timedelta(days=7)
            print(f"ğŸ“… Hafta filtri: {start_date} dan")
        elif time_filter == 'month':
            # Joriy oy boshidan
            start_date = datetime(now.year, now.month, 1)
            print(f"ğŸ“… Oy filtri: {start_date} dan")
        elif time_filter == 'year':
            # Joriy yil boshidan
            start_date = datetime(now.year, 1, 1)
            print(f"ğŸ“… Yil filtri: {start_date} dan")
        else:
            print("ğŸ“… Barcha vaqt (filtr yo'q)")

        result = []
        for customer in customers:
            customer_dict = customer.to_dict()

            # Savdolar ma'lumotini hisoblash
            try:
                sales_query = Sale.query.filter_by(customer_id=customer.id)
                if start_date:
                    sales_query = sales_query.filter(Sale.sale_date >= start_date)
                if end_date:
                    sales_query = sales_query.filter(Sale.sale_date <= end_date)

                sales = sales_query.all()
                total_sales = len(sales)

                # Total amount va profit ni hisoblash (allaqachon USD da)
                total_amount = Decimal('0')
                total_profit = Decimal('0')
                for sale in sales:
                    # Ma'lumotlar allaqachon USD da saqlanadi
                    if sale.total_amount:
                        total_amount += sale.total_amount

                    if sale.total_profit:
                        total_profit += sale.total_profit

                customer_dict['total_sales'] = total_sales
                customer_dict['total_amount'] = round(total_amount, 2)
                customer_dict['total_profit'] = round(total_profit, 2)

                # âš ï¸ MUHIM: Agar vaqt filtri qo'llangan bo'lsa va mijozning savdosi bo'lmasa, uni ro'yxatga qo'shmaslik
                if time_filter != 'all' and total_sales == 0:
                    continue  # Bu mijozni o'tkazib yuborish

                result.append(customer_dict)
            except Exception as e:
                logger.error(f"Error calculating sales for customer {customer.id}: {str(e)}")
                # Xatolik bo'lsa ham, faqat 'all' filtri uchun mijozni qo'shamiz
                if time_filter == 'all':
                    customer_dict['total_sales'] = 0
                    customer_dict['total_amount'] = 0
                    customer_dict['total_profit'] = 0
                    result.append(customer_dict)

        logger.debug(f" Returning {len(result)} customers with sales data")
        print(f"ğŸ“Š Jami {len(result)} ta mijoz qaytarilmoqda")

        return jsonify(result)
    except Exception as e:
        app.logger.error(f"Error fetching customers: {str(e)}")
        logger.error(f" Error in get_customers: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/customers', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_add_customer():
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        data = request.get_json()

        if not data or not data.get('name'):
            return jsonify({'error': 'Mijoz nomi talab qilinadi'}), 400

        # Store_id ni data'dan olish
        store_id = data.get('store_id')
        print(
            f"ğŸ” Customer API - Received store_id: {store_id} (type: {type(store_id)})")
        print(
            f"ğŸ” Customer API - Current user: {current_user.username}, role: {current_user.role}")
        print(
            f"ğŸ” Customer API - User allowed_locations: {current_user.allowed_locations}")
        if store_id:
            # Dokon mavjudligini tekshirish
            store = Store.query.get(store_id)
            if not store:
                return jsonify({'error': 'Tanlangan dokon topilmadi'}), 400

            # Sotuvchi uchun ruxsat tekshirish
            if current_user.role == 'sotuvchi':
                allowed_locations = current_user.allowed_locations or []
                store_id_int = int(store_id)  # String'dan integer'ga o'tkazish
                print(
                    f"ğŸ” Customer API - Checking if {store_id_int} in {allowed_locations}")
                if store_id_int not in allowed_locations:
                    print(
                        f"âŒ Customer API - Store {store_id_int} not in allowed locations {allowed_locations}")
                    return jsonify(
                        {'error': 'Bu dokonga mijoz qo\'shish uchun ruxsatingiz yo\'q'}), 403
                else:
                    logger.info(f" Customer API - Store {store_id_int} is allowed")

        customer = Customer(
            name=data['name'],
            phone=data.get('phone', ''),
            email=data.get('email', ''),
            address=data.get('address', ''),
            store_id=store_id
        )

        db.session.add(customer)
        db.session.commit()

        # OperationHistory logini yozish
        try:
            store = Store.query.get(store_id) if store_id else None
            history = OperationHistory(
                operation_type='create_customer',
                table_name='customers',
                record_id=customer.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Yangi mijoz qo'shildi: {data['name']}",
                old_data=None,
                new_data={'name': data['name'], 'phone': data.get('phone', ''), 'store_id': store_id},
                ip_address=request.remote_addr,
                location_id=store_id,
                location_type='store' if store_id else None,
                location_name=store.name if store else None,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': 'Mijoz muvaffaqiyatli qo\'shildi',
            'customer': customer.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error adding customer: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/customer/<int:customer_id>/orders')
def get_customer_orders(customer_id):
    try:
        sales = Sale.query.filter_by(customer_id=customer_id).order_by(
            Sale.sale_date.desc()).all()

        orders_list = []
        for sale in sales:
            # Sale to_dict() metodini ishlatamiz
            sale_dict = sale.to_dict()
            orders_list.append(sale_dict)

        return jsonify({
            'success': True,
            'orders': orders_list,
            'total_orders': len(orders_list)
        })
    except Exception as e:
        app.logger.error(f"Error getting customer orders: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/customers/<int:customer_id>', methods=['DELETE'])
def delete_customer(customer_id):
    try:
        logger.info(f" Mijoz o'chirish so'rovi: Customer ID: {customer_id}")

        # Customer mavjudligini tekshirish
        customer = Customer.query.get_or_404(customer_id)
        logger.info(f" Customer topildi: {customer.name}")

        # Savdolar hisobini tekshirish
        sales_count = Sale.query.filter_by(customer_id=customer_id).count()
        if sales_count > 0:
            logger.info(f" Bu mijozda {sales_count} ta savdo mavjud, lekin savdolar saqlanadi")

        # Debt payments yozuvlarida customer_id ni NULL qilish (tarixi saqlanadi)
        debt_payments_count = DebtPayment.query.filter_by(customer_id=customer_id).count()
        if debt_payments_count > 0:
            DebtPayment.query.filter_by(customer_id=customer_id).update({'customer_id': None})
            logger.info(f" {debt_payments_count} ta debt payment'da customer_id NULL qilindi")

        # Mijoz nomini saqlash
        customer_name = customer.name
        customer_phone = customer.phone
        customer_store_id = customer.store_id

        # Mijozni o'chirish (Savdo tarixi saqlanadi, chunki Sale jadvalida customer_id nullable)
        db.session.delete(customer)
        db.session.commit()

        # OperationHistory logini yozish
        try:
            store = Store.query.get(customer_store_id) if customer_store_id else None
            history = OperationHistory(
                operation_type='delete_customer',
                table_name='customers',
                record_id=customer_id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Mijoz o'chirildi: {customer_name} ({sales_count} ta savdo)",
                old_data={'name': customer_name, 'phone': customer_phone, 'store_id': customer_store_id, 'sales_count': sales_count},
                new_data=None,
                ip_address=request.remote_addr,
                location_id=customer_store_id,
                location_type='store' if customer_store_id else None,
                location_name=store.name if store else None,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        message = f'Mijoz "{customer_name}" muvaffaqiyatli o\'chirildi'
        if sales_count > 0:
            message += f' (Savdo tarixi saqlanadi: {sales_count} ta savdo)'

        logger.info(f" Customer muvaffaqiyatli o'chirildi: {customer_name}")
        return jsonify({
            'success': True,
            'message': message
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f" Customer o'chirish xatosi: {str(e)}")
        app.logger.error(f"Error deleting customer: {str(e)}")
        return jsonify({'error': f'Xatolik: {str(e)}'}), 500


@app.route('/add_customer')
def add_customer_page():
    return render_template('add_customer.html')


@app.route('/edit-customer/<int:customer_id>')
def edit_customer_page(customer_id):
    return render_template('edit_customer.html')


@app.route('/api/customers/<int:customer_id>', methods=['PUT'])
def update_customer(customer_id):
    try:
        customer = Customer.query.get_or_404(customer_id)
        data = request.get_json()

        if not data or not data.get('name'):
            return jsonify({'error': 'Mijoz nomi talab qilinadi'}), 400

        # Eski ma'lumotlarni saqlash
        old_data = {
            'name': customer.name,
            'phone': customer.phone,
            'email': customer.email,
            'address': customer.address,
            'store_id': customer.store_id
        }

        # Ma'lumotlarni yangilash
        customer.name = data['name']
        customer.phone = data.get('phone', '')
        customer.email = data.get('email', '')
        customer.address = data.get('address', '')

        # Dokonni yangilash
        store_id = data.get('store_id')
        if store_id:
            # Dokon mavjudligini tekshirish
            store = Store.query.get(store_id)
            if not store:
                return jsonify({'error': 'Tanlangan dokon topilmadi'}), 400
            customer.store_id = store_id
        else:
            customer.store_id = None

        db.session.commit()

        # OperationHistory logini yozish
        try:
            store = Store.query.get(customer.store_id) if customer.store_id else None
            history = OperationHistory(
                operation_type='edit_customer',
                table_name='customers',
                record_id=customer_id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Mijoz tahrirlandi: {customer.name}",
                old_data=old_data,
                new_data={'name': customer.name, 'phone': customer.phone, 'email': customer.email, 'address': customer.address, 'store_id': customer.store_id},
                ip_address=request.remote_addr,
                location_id=customer.store_id,
                location_type='store' if customer.store_id else None,
                location_name=store.name if store else None,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': 'Mijoz ma\'lumotlari muvaffaqiyatli yangilandi',
            'customer': customer.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating customer: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Foydalanuvchilar API route'lari
@app.route('/api/users', methods=['GET'])
@role_required('admin', 'kassir')
def get_users():
    try:
        # âœ… Pagination qo'shish - xotira tejash
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 50, type=int)
        get_all = request.args.get('all', 'false').lower() == 'true'

        # Hozirgi foydalanuvchi ID sini ham yuborish
        current_user_id = session.get('user_id')

        if get_all:
            # Barcha userlar kerak bo'lsa (dropdown uchun)
            users = User.query.all()
            return jsonify({
                'users': [user.to_dict() for user in users],
                'current_user_id': current_user_id
            })
        else:
            # Pagination bilan
            pagination = User.query.paginate(page=page, per_page=per_page, error_out=False)
            return jsonify({
                'users': [user.to_dict() for user in pagination.items],
                'current_user_id': current_user_id,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': pagination.total,
                    'pages': pagination.pages
                }
            })
    except Exception as e:
        app.logger.error(f"Error fetching users: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/users', methods=['POST'])
@role_required('admin', 'kassir')
def api_add_user():
    try:
        data = request.get_json()
        logger.debug(f" User Creation Debug - Received data: {data}")

        if not data or not data.get('username') or not data.get('first_name') or not data.get(
                'last_name') or not data.get('email') or not data.get('password'):
            return jsonify(
                {'error': 'Barcha majburiy maydonlar (ism, familya, email, login, parol) to\'ldirilishi kerak'}), 400

        # Username va email unikalligini tekshirish
        existing_user = User.query.filter(
            ((User.username == data['username'])
             | (User.email == data.get('email', '')))
        ).first()

        if existing_user:
            return jsonify(
                {'error': 'Bu foydalanuvchi nomi yoki email allaqachon mavjud'}), 400

        # Store_id ni data'dan olish
        store_id = data.get('store_id')
        if store_id:
            # Dokon mavjudligini tekshirish
            store = Store.query.get(store_id)
            if not store:
                return jsonify({'error': 'Tanlangan dokon topilmadi'}), 400

        permissions = data.get('permissions', {})
        allowed_locations = data.get('allowed_locations', [])
        transfer_locations = data.get('transfer_locations', [])
        stock_check_locations = data.get('stock_check_locations', [])

        # Stock check locations'ni allowed_locations'ga qo'shish
        if stock_check_locations:
            for loc in stock_check_locations:
                if loc not in allowed_locations:
                    allowed_locations.append(loc)

        logger.debug(f" Permissions: {permissions}")
        logger.debug(f" Stock check locations: {stock_check_locations}")
        logger.debug(f" Allowed locations (after adding stock check): {allowed_locations}")
        logger.debug(f" Transfer locations: {transfer_locations}")
        print(
            f"ğŸ” Primary store_id: {store_id} (UI uchun, huquqlarga ta'sir qilmaydi)")

        # Yangi foydalanuvchi yaratish
        new_user = User(
            first_name=data['first_name'],
            last_name=data['last_name'],
            email=data['email'],
            username=data['username'],
            password=hash_password(data['password']),  # Hash qilingan parol
            phone=data.get('phone', ''),
            role=data.get('role', 'sotuvchi'),
            store_id=store_id,
            permissions=permissions,
            allowed_locations=allowed_locations,
            transfer_locations=transfer_locations,
            is_active=data.get('is_active', True)
        )

        db.session.add(new_user)
        db.session.commit()

        logger.debug(f" User created successfully: {new_user.username}")
        logger.debug(f" Final permissions: {new_user.permissions}")
        logger.debug(f" Final allowed_locations: {new_user.allowed_locations}")

        # OperationHistory logini yozish
        try:
            history = OperationHistory(
                operation_type='create_user',
                table_name='users',
                record_id=new_user.id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Yangi foydalanuvchi yaratildi: {new_user.username} ({new_user.role})",
                old_data=None,
                new_data={'username': new_user.username, 'role': new_user.role, 'email': new_user.email},
                ip_address=request.remote_addr,
                location_id=None,
                location_type=None,
                location_name=None,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': 'Foydalanuvchi muvaffaqiyatli qo\'shildi',
            'user': new_user.to_dict()
        }), 201

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error adding user: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<int:user_id>', methods=['DELETE'])
@role_required('admin')
def delete_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        username = user.username
        user_role = user.role

        # Foydalanuvchiga tegishli barcha session'larini o'chirish
        UserSession.query.filter_by(user_id=user_id).delete()

        # Foydalanuvchiga tegishli stock check sessions'larini o'chirish
        StockCheckSession.query.filter_by(user_id=user_id).delete()

        # Foydalanuvchini o'chirish
        db.session.delete(user)
        db.session.commit()

        app.logger.info(f"âœ… User {user_id}, uning session'lari va stock check sessions'lari o'chirildi")

        # OperationHistory logini yozish
        try:
            history = OperationHistory(
                operation_type='delete_user',
                table_name='users',
                record_id=user_id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Foydalanuvchi o'chirildi: {username} ({user_role})",
                old_data={'username': username, 'role': user_role},
                new_data=None,
                ip_address=request.remote_addr,
                location_id=None,
                location_type=None,
                location_name=None,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': 'Foydalanuvchi muvaffaqiyatli o\'chirildi'
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error deleting user: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<int:user_id>/toggle-status', methods=['PATCH'])
@role_required('admin')
def toggle_user_status(user_id):
    """Foydalanuvchini faol/faol emas qilish"""
    try:
        user = User.query.get_or_404(user_id)

        # O'zini o'zi faol emas qilishga ruxsat bermaydi
        if user_id == session.get('user_id'):
            return jsonify({
                'error': 'O\'zingizni faol emas qila olmaysiz'
            }), 400

        # Status ni o'zgartirish
        user.is_active = not user.is_active

        # Faol emas qilingan foydalanuvchi keyingi request da avtomatik logout bo'ladi

        db.session.commit()

        status_text = "faol" if user.is_active else "faol emas"

        app.logger.info(f"ğŸ”„ User status o'zgartirildi: {user.username} -> {status_text}")

        return jsonify({
            'success': True,
            'message': f'Foydalanuvchi {status_text} qilindi',
            'is_active': user.is_active
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error toggling user status: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/active-sessions', methods=['GET'])
@role_required('admin', 'kassir')
def get_active_user_sessions():
    """Aktiv foydalanuvchi seanslarini olish"""
    try:
        # Aktiv seanslarni olish
        active_sessions = UserSession.query.filter_by(is_active=True).order_by(UserSession.login_time.desc()).all()

        sessions_data = []
        for session_obj in active_sessions:
            session_dict = session_obj.to_dict()
            # User relationship orqali store_name qo'shish
            if session_obj.user and session_obj.user.store_id:
                store = Store.query.get(session_obj.user.store_id)
                session_dict['store_name'] = store.name if store else 'Noma\'lum'
            else:
                session_dict['store_name'] = 'Barcha dokonlar'

            # User role qo'shish
            if session_obj.user:
                session_dict['role'] = session_obj.user.role

            sessions_data.append(session_dict)

        return jsonify({
            'success': True,
            'sessions': sessions_data,
            'total': len(sessions_data)
        })

    except Exception as e:
        app.logger.error(f"Aktiv seanslarni olishda xatolik: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<int:user_id>', methods=['GET'])
# @role_required('admin', 'kassir')  # Test uchun vaqtincha o'chirilgan
def get_user(user_id):
    try:
        user = User.query.get_or_404(user_id)

        return jsonify({
            'id': user.id,
            'username': user.username,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'full_name': f"{user.first_name} {user.last_name}",
            'email': user.email,
            'phone': user.phone,
            'role': user.role,
            'store_id': user.store_id,
            'is_active': user.is_active,
            'permissions': user.permissions,
            'allowed_locations': user.allowed_locations,
            'transfer_locations': user.transfer_locations,
            'created_at': user.created_at.isoformat() if user.created_at else None
        })

    except Exception as e:
        app.logger.error(f"Error getting user: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<int:user_id>', methods=['PUT'])
@role_required('admin', 'kassir')
def update_user(user_id):
    try:
        user = User.query.get_or_404(user_id)
        data = request.get_json()

        if not data or not data.get('first_name') or not data.get('last_name'):
            return jsonify({'error': 'Ism va familya talab qilinadi'}), 400

        # Username va email unikalligini tekshirish (o'zi bundan mustasno)
        if data.get('username'):
            existing_user = User.query.filter(
                (User.username == data.get('username')) & (User.id != user_id)
            ).first()

            if existing_user:
                return jsonify({'error': 'Bu username allaqachon mavjud'}), 400

        if data.get('email'):
            existing_user = User.query.filter(
                (User.email == data.get('email')) & (User.id != user_id)
            ).first()

            if existing_user:
                return jsonify({'error': 'Bu email allaqachon mavjud'}), 400

        # Ma'lumotlarni yangilash
        user.first_name = data['first_name']
        user.last_name = data['last_name']
        if data.get('username'):
            user.username = data['username']
        user.email = data.get('email', '')
        user.phone = data.get('phone', '')
        user.role = data.get('role', 'sotuvchi')
        user.is_active = data.get('is_active', True)

        # Parol o'zgartirish (agar berilsa)
        if data.get('password'):
            user.password = hash_password(data['password'])

        # Huquqlarni yangilash
        permissions = data.get('permissions', {})
        allowed_locations = data.get('allowed_locations', [])
        transfer_locations = data.get('transfer_locations', [])
        stock_check_locations = data.get('stock_check_locations', [])

        # Stock check locations'ni allowed_locations'ga qo'shish
        if stock_check_locations:
            for loc in stock_check_locations:
                if loc not in allowed_locations:
                    allowed_locations.append(loc)

        if permissions:
            logger.debug(f" Updating permissions: {permissions}")
            user.permissions = permissions

            # Allowed locations ni saqlash
            user.allowed_locations = allowed_locations
            logger.debug(f" Allowed locations (including stock check): {allowed_locations}")
            logger.debug(f" Stock check locations: {stock_check_locations}")

            # Transfer locations ni saqlash
            user.transfer_locations = transfer_locations
            logger.debug(f" Transfer locations: {transfer_locations}")

        # Asosiy joylashuvni yangilash (store_id faqat)
        store_id = data.get('store_id')
        if store_id:
            # Dokon yoki ombor ID sini tekshirish
            store = Store.query.get(store_id)
            warehouse = Warehouse.query.get(store_id)

            if not store and not warehouse:
                return jsonify({'error': 'Tanlangan joylashuv topilmadi'}), 400

            user.store_id = store_id
            if store:
                logger.error(f" Primary store set: {store_id} ({store.name})")
            else:
                print(
                    f"ğŸ­ Primary warehouse set: {store_id} ({warehouse.name})")
        else:
            user.store_id = None
            print("ğŸš« No primary location set")

        db.session.commit()

        # OperationHistory logini yozish
        try:
            history = OperationHistory(
                operation_type='edit_user',
                table_name='users',
                record_id=user_id,
                user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"Foydalanuvchi tahrirlandi: {user.username} ({user.role})",
                old_data={'username': user.username, 'role': user.role},
                new_data={'first_name': user.first_name, 'last_name': user.last_name, 'email': user.email, 'role': user.role},
                ip_address=request.remote_addr,
                location_id=None,
                location_type=None,
                location_name=None,
                amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f"OperationHistory log xatoligi: {log_error}")

        return jsonify({
            'success': True,
            'message': 'Foydalanuvchi ma\'lumotlari muvaffaqiyatli yangilandi',
            'user': user.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating user: {str(e)}")
        return jsonify({'error': str(e)}), 500


# Sales History API endpoint
@app.route('/api/sales-history', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_sales_history():
    """Sales history with filtering and statistics"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        print(
            f"ğŸ” Sales history API - User: {current_user.username}, Role: {current_user.role}")

        # Get query parameters for filtering
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        customer_id = request.args.get('customer_id')
        store_id = request.args.get('store_id')
        payment_status = request.args.get('payment_status')
        payment_method = request.args.get('payment_method')
        location_filter = request.args.get('location_filter')  # store_1, warehouse_2 formatida
        search_term = request.args.get('search_term')  # Mahsulot nomi bo'yicha qidiruv

        # Statistika uchun logika:
        # - Agar sana filtri berilgan bo'lsa, shu sanalar bo'yicha statistika
        # - Agar sana filtri yo'q bo'lsa, faqat bugungi kun statistikasi

        # Sana filtri tekshirish (empty string ham yo'q deb hisoblanadi)
        has_date_filter = bool(start_date and start_date.strip()) or bool(end_date and end_date.strip())

        if has_date_filter:
            stats_date_filter = 'filtered'  # Tanlangan sana oralig'i
            logger.info(f"ğŸ“… Sana filtri aniqlandi: {start_date} - {end_date}")
        else:
            stats_date_filter = 'today'  # Default: bugungi kun
            logger.info("ğŸ“… Sana filtri yo'q, default bugungi kun")

        print(
            f"ğŸ“‹ Query parameters: start_date={start_date}, end_date={end_date}, customer_id={customer_id}, payment_status={payment_status}, location_filter={location_filter}, search_term={search_term}, stats_date_filter={stats_date_filter}")

        # Base query - payment_status parametriga qarab filtrlash
        if payment_status and payment_status == 'pending':
            # Faqat tasdiqlanmagan savdolar
            query = Sale.query.filter(Sale.payment_status == 'pending')
            logger.info("ğŸ“‹ Filter: pending savdolar")
        elif payment_status and payment_status == 'completed':
            # Faqat to'langan savdolar
            query = Sale.query.filter(Sale.payment_status == 'completed')
            logger.info("ğŸ“‹ Filter: completed savdolar")
        elif payment_status and payment_status == 'partial':
            # Faqat qisman to'langan savdolar (QARZ SAVDOLAR)
            # MUHIM: debt_usd > 0 sharti - haqiqatdan qarz bor bo'lsa
            query = Sale.query.filter(
                Sale.payment_status == 'partial',
                Sale.debt_usd > 0
            )
            logger.info("ğŸ’³ Filter: QARZ SAVDOLAR (partial + debt_usd > 0)")
        elif payment_status and payment_status != 'all':
            # Belgilangan status bo'yicha filtrlash
            query = Sale.query.filter(Sale.payment_status == payment_status)
            logger.info(f"ğŸ“‹ Filter: status={payment_status}")
        else:
            # Default: barcha tasdiqlangan savdolar (pending emas)
            # Bu qarz savdolarni ham o'z ichiga oladi
            query = Sale.query.filter(Sale.payment_status.in_(['completed', 'partial']))
            logger.info("ğŸ“‹ Filter: completed + partial (default)")

        # Sotuvchi uchun joylashuv filterlash
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []
            print(
                f"ğŸ” Seller allowed locations for sales history: {allowed_locations}")

            if allowed_locations:
                # Extract location IDs from allowed locations
                location_conditions = []
                for loc in allowed_locations:
                    if isinstance(loc, dict):
                        # New format: {'id': 4, 'type': 'store'}
                        loc_id = loc.get('id')
                        loc_type = loc.get('type')
                        if loc_id and loc_type:
                            location_conditions.append(
                                db.and_(Sale.location_id == loc_id, Sale.location_type == loc_type)
                            )
                    elif isinstance(loc, (int, str)):
                        # Old format: just ID (assume store)
                        try:
                            location_conditions.append(
                                db.and_(Sale.location_id == int(loc), Sale.location_type == 'store')
                            )
                        except (ValueError, TypeError):
                            pass

                if location_conditions:
                    # Ruxsat berilgan joylashuvlardagi savdolar + NULL location'li savdolar
                    # NULL location'li savdolar ham qo'shiladi (eski savdolar uchun)
                    location_conditions.append(Sale.location_id is None)
                    query = query.filter(db.or_(*location_conditions))
                    logger.info(f"ğŸ” Sotuvchi uchun {len(location_conditions) - 1} ta joylashuv + NULL location bo'yicha filtrlash")
                else:
                    # Hech qaysi joylashuv ruxsat berilmagan
                    query = query.filter(Sale.id == -1)
                    logger.warning("âš ï¸ Sotuvchiga hech qaysi joylashuv ruxsat berilmagan!")
            else:
                # Ruxsat berilgan joylashuv bo'lmasa, bo'sh natija
                query = query.filter(Sale.id == -1)
                logger.warning("âš ï¸ Sotuvchining allowed_locations bo'sh!")

        # Apply date filters
        if start_date:
            try:
                start_dt = datetime.strptime(start_date, '%Y-%m-%d')
                query = query.filter(Sale.sale_date >= start_dt)
            except ValueError:
                pass

        if end_date:
            try:
                end_dt = datetime.strptime(end_date, '%Y-%m-%d')
                # Add one day to include the entire end date
                end_dt = end_dt.replace(hour=23, minute=59, second=59)
                query = query.filter(Sale.sale_date <= end_dt)
            except ValueError:
                pass

        # Apply other filters
        if customer_id and customer_id != 'all':
            query = query.filter(Sale.customer_id == customer_id)

        if store_id and store_id != 'all':
            query = query.filter(Sale.store_id == store_id)

        # payment_status filter yuqorida base query'da qo'llanilgan

        if payment_method and payment_method != 'all':
            query = query.filter(Sale.payment_method == payment_method)

        # Joylashuv filtri (yangi location_id va location_type ishlatish)
        if location_filter and location_filter != 'all':
            if location_filter.startswith('store_'):
                store_filter_id = int(location_filter.replace('store_', ''))
                # Yangi tizim: location_id va location_type ishlatish
                query = query.filter(
                    Sale.location_id == store_filter_id,
                    Sale.location_type == 'store'
                )
                print(f"ğŸª Location filtri: store_id={store_filter_id}")
            elif location_filter.startswith('warehouse_'):
                warehouse_filter_id = int(location_filter.replace('warehouse_', ''))
                # Yangi tizim: location_id va location_type ishlatish
                query = query.filter(
                    Sale.location_id == warehouse_filter_id,
                    Sale.location_type == 'warehouse'
                )
                print(f"ğŸ­ Location filtri: warehouse_id={warehouse_filter_id}")

        # Qidiruv filtri (mahsulot nomi bo'yicha - bir nechta so'z bilan)
        if search_term and search_term.strip():
            search_term_cleaned = search_term.strip()
            # Bo'sh joy bo'yicha so'zlarga ajratish
            search_words = search_term_cleaned.split()

            # Har bir so'z mahsulot nomida bo'lishi kerak (AND logic)
            search_conditions = [Product.name.ilike(f'%{word}%') for word in search_words]

            query = query.join(SaleItem).join(Product).filter(
                db.and_(*search_conditions)
            ).distinct()
            print(f"ğŸ” Qidiruv: '{search_term_cleaned}' ({len(search_words)} ta so'z)")

        # STATISTIKA: SQL aggregate funksiyalari bilan optimal hisoblash
        from sqlalchemy import func

        # Base query'ni statistics uchun saqlash (ORDER BY siz)
        base_stats_query = query

        # Statistika uchun alohida query yaratish
        if stats_date_filter == 'today':
            # Faqat bugungi kun
            today = get_tashkent_time().date()
            today_start = datetime.combine(today, datetime.min.time())
            today_end = datetime.combine(today, datetime.max.time())
            stats_filtered_query = base_stats_query.filter(
                Sale.sale_date >= today_start,
                Sale.sale_date <= today_end
            )
            logger.info(f"ğŸ“Š Statistika: Faqat bugungi kun ({today})")
        elif stats_date_filter == 'filtered':
            # Tanlangan sana oralig'i (base_query allaqachon sana filtri bilan)
            stats_filtered_query = base_stats_query
            logger.info(f"ğŸ“Š Statistika: Tanlangan sana oralig'i bo'yicha ({start_date} - {end_date})")
        else:
            # Barcha savdolar (sana filtrisiz)
            stats_filtered_query = base_stats_query
            logger.info("ğŸ“Š Statistika: Barcha savdolar")

        # Asosiy statistika (count, sum) - aggregate qilish
        stats_aggregate_result = stats_filtered_query.with_entities(
            func.count(Sale.id).label('total_count'),
            func.sum(Sale.total_amount).label('total_revenue'),
            func.sum(Sale.total_profit).label('total_profit')
        ).first()

        total_sales_count = stats_aggregate_result.total_count or 0
        total_revenue = float(stats_aggregate_result.total_revenue or 0)
        total_profit = float(stats_aggregate_result.total_profit or 0)

        logger.info(f"ğŸ“Š Jami savdolar: {total_sales_count}")
        logger.info(f"ğŸ’° Jami daromad: ${total_revenue:.2f}")
        logger.info(f"ğŸ’µ Jami foyda: ${total_profit:.2f}")

        # Order by date descending (yangi savdolardan eski savdolarga)
        # Bu pagination uchun kerak
        query = query.order_by(Sale.sale_date.desc())

        # Pagination parametrlarini olish
        page = request.args.get('page', 1, type=int)
        per_page = request.args.get('per_page', 20, type=int)  # âœ… Optimizatsiya: 50->20
        per_page = min(per_page, 100)  # Maximum 100 limit

        # âœ… Eager loading - N+1 query muammosini hal qilish
        from sqlalchemy.orm import joinedload
        query = query.options(
            joinedload(Sale.customer),
            joinedload(Sale.seller),
            joinedload(Sale.store),
            joinedload(Sale.items).joinedload(SaleItem.product)
        )

        # Execute query with pagination
        pagination = query.paginate(page=page, per_page=per_page, error_out=False)
        sales = pagination.items

        logger.info(f"ğŸ“„ Ma'lumotlar bazasidan topildi: {len(sales)} ta savdo (sahifa {page}, {per_page} ta per sahifa)")
        logger.info(f"ğŸ“Š Jami sahifalar: {pagination.pages}, Jami savdolar: {pagination.total}")

        # Qarz savdolar uchun maxsus log
        if payment_status == 'partial':
            logger.info(f"ğŸ’³ QARZ SAVDOLAR: {pagination.total} ta")
            if pagination.total == 0:
                logger.warning(f"âš ï¸ QARZ SAVDOLAR TOPILMADI! User: {current_user.username}, Role: {current_user.role}")

        # Debug: Query parametrlarini ko'rsatish
        logger.debug(" Query details:")
        print(f"   - Current user: {current_user.username}")
        print(f"   - User role: {current_user.role}")
        print(f"   - Payment status filter: {payment_status}")
        if hasattr(current_user, 'allowed_locations'):
            print(f"   - Allowed locations: {current_user.allowed_locations}")

        # Birinchi 3 ta savdo ID'larini ko'rsatish
        if sales:
            sale_ids = [sale.id for sale in sales[:3]]
            print(f"   - Birinchi 3 ta savdo ID: {sale_ids}")

        # STATISTIKA: Subquery ishlatish - xotira sarfini kamaytirish
        # âœ… Optimizatsiya: filtered_sale_ids list o'rniga subquery
        from sqlalchemy.orm import aliased
        sale_ids_subquery = stats_filtered_query.with_entities(Sale.id).subquery()

        # Jami mahsulotlar soni - subquery bilan
        total_items = 0
        items_count_query = db.session.query(
            func.sum(SaleItem.quantity)
        ).filter(
            SaleItem.sale_id.in_(select(sale_ids_subquery.c.id))
        )
        total_items = float(items_count_query.scalar() or 0)

        # Average order value
        avg_order_value = total_revenue / total_sales_count if total_sales_count > 0 else 0

        # Profit margin
        profit_margin = (
            (total_profit / total_revenue * 100) if total_revenue > 0 else 0
        )

        # Payment method breakdown - SQL GROUP BY bilan optimal
        # stats_filtered_query ishlatamiz (bugungi kun filtri bilan)
        payment_stats_query = stats_filtered_query.with_entities(
            Sale.payment_method,
            func.count(Sale.id).label('count'),
            func.sum(Sale.total_amount).label('total')
        ).group_by(Sale.payment_method)

        payment_methods = {}
        for method, count, total in payment_stats_query.all():
            method_name = method or 'Unknown'
            payment_methods[method_name] = {
                'count': count,
                'amount': float(total or 0)
            }

        # Top selling products - subquery bilan optimizatsiya
        top_products = []
        Product_alias = aliased(Product)

        # âœ… Subquery ishlatish - list o'rniga
        top_products_query = db.session.query(
            Product_alias.name,
            func.sum(SaleItem.quantity).label('quantity'),
            func.sum(SaleItem.total_price).label('revenue')
        ).join(
            SaleItem, SaleItem.product_id == Product_alias.id
        ).filter(
            SaleItem.sale_id.in_(select(sale_ids_subquery.c.id))
        ).group_by(
            Product_alias.name
        ).order_by(
            func.sum(SaleItem.quantity).desc()
        ).limit(5)

        for name, quantity, revenue in top_products_query.all():
            top_products.append({
                'name': name or 'Noma\'lum',
                'quantity': float(quantity or 0),
                'revenue': float(revenue or 0)
            })

        # Store performance - subquery bilan optimizatsiya
        store_performance = []
        Store_alias = aliased(Store)

        # âœ… Subquery ishlatish
        store_perf_query = db.session.query(
            Store_alias.name,
            func.count(Sale.id).label('sales'),
            func.sum(Sale.total_amount).label('revenue'),
            func.sum(Sale.total_profit).label('profit')
        ).join(
            Sale, Sale.store_id == Store_alias.id
        ).filter(
            Sale.id.in_(select(sale_ids_subquery.c.id))
        ).group_by(
            Store_alias.name
        ).order_by(
            func.sum(Sale.total_amount).desc()
        )

        for name, sales_count, revenue, profit in store_perf_query.all():
            store_performance.append({
                'name': name or 'Noma\'lum',
                'sales': sales_count,
                'revenue': float(revenue or 0),
                'profit': float(profit or 0)
            })

        # Sales list conversion with error handling
        sales_list = []
        for sale in sales:
            try:
                sales_list.append(sale.to_dict())
            except Exception as e:
                app.logger.error(f"Error converting sale {sale.id} to dict: {str(e)}")
                app.logger.exception("Full traceback:")
                # Skip this sale and continue
                continue

        logger.debug(f" API javobida yuborilayotgan sales: {len(sales_list)} ta")
        if sales_list:
            logger.debug(f" Birinchi sale sample: {sales_list[0]}")
            if len(sales_list) > 1:
                logger.debug(f" Ikkinchi sale sample: {sales_list[1]}")
            if len(sales_list) > 2:
                logger.debug(f" Uchinchi sale sample: {sales_list[2]}")

        return jsonify({
            'success': True,
            'data': {
                'sales': sales_list,
                'pagination': {
                    'page': page,
                    'per_page': per_page,
                    'total': pagination.total,
                    'total_pages': pagination.pages,
                    'has_prev': pagination.has_prev,
                    'has_next': pagination.has_next,
                    'prev_num': pagination.prev_num,
                    'next_num': pagination.next_num
                },
                'statistics': {
                    'total_sales': total_sales_count,  # Filtr qo'llanilgan barcha savdolar soni
                    'total_revenue': round(total_revenue, 2),
                    'total_profit': round(total_profit, 2),
                    'total_items': total_items,
                    'avg_order_value': round(avg_order_value, 2),
                    'profit_margin': round(profit_margin, 2),
                    'payment_methods': payment_methods,
                    'top_products': top_products,
                    'store_performance': store_performance
                },
                'filters': {
                    'start_date': start_date,
                    'end_date': end_date,
                    'customer_id': customer_id,
                    'store_id': store_id,
                    'payment_status': payment_status,
                    'payment_method': payment_method
                }
            }
        })

    except Exception as e:
        app.logger.error(f"Error fetching sales history: {str(e)}")
        app.logger.exception("Full traceback for sales history error:")
        return jsonify({
            'success': False,
            'error': str(e),
            'data': {
                'sales': [],
                'statistics': {
                    'total_sales': 0,
                    'total_revenue': 0,
                    'total_profit': 0,
                    'total_quantity': 0,
                    'avg_order_value': 0,
                    'profit_margin': 0,
                    'payment_methods': {},
                    'top_products': [],
                    'store_performance': []
                }
            }
        }), 500


# Pending savdoni yakunlash (faqat status o'zgartirish)
@app.route('/api/finalize-sale/<int:sale_id>', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def finalize_sale(sale_id):
    """Pending savdoni yakunlash - faqat status va to'lov ma'lumotlarini yangilash"""
    try:
        data = request.get_json()

        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({'success': False, 'error': 'Savdo topilmadi'}), 404

        if sale.payment_status != 'pending':
            return jsonify({'success': False, 'error': 'Bu savdo allaqachon yakunlangan'}), 400

        logger.info(f"ğŸ”„ Pending savdoni yakunlash: Sale ID {sale_id}")

        # To'lov ma'lumotlarini olish
        payment = data.get('payment', {})
        payment_status = data.get('payment_status', 'paid')
        customer_id = data.get('customer_id')
        exchange_rate = data.get('exchange_rate', get_current_currency_rate())

        # To'lov ma'lumotlarini yangilash
        sale.cash_usd = Decimal(str(payment.get('cash_usd', 0)))
        sale.cash_amount = Decimal(str(payment.get('cash_uzs', 0)))
        sale.click_usd = Decimal(str(payment.get('click_usd', 0)))
        sale.click_amount = Decimal(str(payment.get('click_uzs', 0)))
        sale.terminal_usd = Decimal(str(payment.get('terminal_usd', 0)))
        sale.terminal_amount = Decimal(str(payment.get('terminal_uzs', 0)))
        sale.debt_usd = Decimal(str(payment.get('debt_usd', 0)))
        sale.debt_amount = Decimal(str(payment.get('debt_uzs', 0)))

        # Status va boshqa ma'lumotlarni yangilash
        sale.payment_status = payment_status
        sale.currency_rate = Decimal(str(exchange_rate))
        sale.sale_date = get_tashkent_time()  # Tasdiqlash vaqti

        # Mijoz ID ni yangilash (agar kiritilgan bo'lsa)
        if customer_id:
            sale.customer_id = int(customer_id)

        db.session.commit()

        logger.info(f"âœ… Savdo yakunlandi: Sale ID {sale_id}, Status: {payment_status}, Location: {sale.location_id}/{sale.location_type}")

        # Chek formatini olish
        receipt_format = data.get('receipt_format', 'both')  # 'usd', 'uzs', yoki 'both'
        logger.info(f"ğŸ“„ Tanlangan chek formati: {receipt_format}")

        # Telegram xabar yuborish (mijoz telegram_chat_id bor bo'lsa)
        if customer_id:
            try:
                customer = Customer.query.get(customer_id)
                if customer and customer.telegram_chat_id:
                    from telegram_bot import get_bot_instance
                    bot = get_bot_instance(db=db)

                    # Joylashuv nomini olish
                    if sale.location_type == 'warehouse':
                        warehouse_obj = Warehouse.query.get(sale.location_id)
                        location_name = warehouse_obj.name if warehouse_obj else "Ombor"
                    else:
                        store_obj = Store.query.get(sale.location_id)
                        location_name = store_obj.name if store_obj else "Do'kon"

                    # To'lov summalari (UZS)
                    total_uzs = float(sale.cash_amount) + float(sale.click_amount) + float(sale.terminal_amount) + float(sale.debt_amount)
                    paid_uzs = float(sale.cash_amount) + float(sale.click_amount) + float(sale.terminal_amount)

                    # To'lov summalari (USD)
                    total_usd = float(sale.cash_usd) + float(sale.click_usd) + float(sale.terminal_usd) + float(sale.debt_usd)
                    paid_usd = float(sale.cash_usd) + float(sale.click_usd) + float(sale.terminal_usd)

                    # Savdo mahsulotlarini PDF uchun tayyorlash
                    seller_name = f"{sale.seller.first_name} {sale.seller.last_name}" if sale.seller else session.get('username', 'Sotuvchi')
                    seller_phone = sale.seller.phone if sale.seller and sale.seller.phone else ''

                    sale_items_for_pdf = []
                    for item in sale.items:
                        sale_items_for_pdf.append({
                            'name': item.product.name if item.product else 'Mahsulot',
                            'seller_name': seller_name,
                            'quantity': float(item.quantity),
                            'unit_price_uzs': float(item.unit_price) * float(sale.currency_rate),
                            'total_uzs': float(item.total_price) * float(sale.currency_rate),
                            'unit_price_usd': float(item.unit_price),
                            'total_usd': float(item.total_price),
                            'unit_price': float(item.unit_price),  # Backward compatibility
                            'total': float(item.total_price) * float(sale.currency_rate)  # Backward compatibility
                        })

                    # Telegram xabar yuborish
                    bot.send_sale_notification_sync(
                        chat_id=customer.telegram_chat_id,
                        customer_name=customer.name,
                        customer_id=customer.id,
                        sale_date=sale.sale_date,
                        location_name=location_name,
                        total_amount_uzs=total_uzs,
                        paid_uzs=paid_uzs,
                        cash_uzs=float(sale.cash_amount),
                        click_uzs=float(sale.click_amount),
                        terminal_uzs=float(sale.terminal_amount),
                        debt_uzs=float(sale.debt_amount),
                        sale_id=sale.id,
                        sale_items=sale_items_for_pdf,
                        receipt_format=receipt_format,
                        seller_phone=seller_phone,
                        total_amount_usd=total_usd,
                        paid_usd=paid_usd,
                        cash_usd=float(sale.cash_usd),
                        click_usd=float(sale.click_usd),
                        terminal_usd=float(sale.terminal_usd),
                        debt_usd=float(sale.debt_usd)
                    )
                    logger.info(f"âœ… Telegram xabar va PDF yuborildi (finalize): {customer.name}")
            except Exception as telegram_error:
                logger.warning(f"âš ï¸ Telegram xabar yuborishda xatolik (finalize): {telegram_error}")
                # Telegram xatosi savdoni to'xtatmasin

        return jsonify({
            'success': True,
            'message': 'Savdo muvaffaqiyatli yakunlandi',
            'sale_id': sale_id,
            'payment_status': payment_status
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"âŒ Savdoni yakunlashda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Savdoni tasdiqlash API'si
@app.route('/api/approve-sale/<int:sale_id>', methods=['POST'])
def approve_sale(sale_id):
    try:
        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({'success': False, 'error': 'Savdo topilmadi'}), 404

        # Savdo holatini tasdiqlangan qilib o'zgartirish va joriy kurs bilan
        # yangilash
        sale.payment_status = 'paid'
        # Tasdiqlash vaqtidagi joriy kurs
        sale.currency_rate = get_current_currency_rate()
        db.session.commit()

        app.logger.info(f"âœ… Savdo tasdiqlandi: Sale ID {sale_id}")

        return jsonify({
            'success': True,
            'message': 'Savdo muvaffaqiyatli tasdiqlandi'
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error approving sale: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Savdoni rad etish API'si
@app.route('/api/reject-sale/<int:sale_id>', methods=['POST'])
def reject_sale(sale_id):
    try:
        data = request.get_json()
        reason = data.get('reason', '') if data else ''

        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({'success': False, 'error': 'Savdo topilmadi'}), 404

        print(f"ğŸš« Savdoni rad etish va o'chirish: Sale ID {sale_id}")

        # Stock'ni qaytarish - har bir mahsulot uchun
        for sale_item in sale.items:
            if sale_item.source_type == 'store':
                # Store stock'ni qaytarish
                stock = StoreStock.query.filter_by(
                    store_id=sale_item.source_id,
                    product_id=sale_item.product_id
                ).first()

                if stock:
                    stock.quantity += sale_item.quantity
                    print(
                        f"ğŸ“¦ Store stock qaytarildi: {sale_item.product.name} +{sale_item.quantity} = {stock.quantity}")
                else:
                    # Agar stock yo'q bo'lsa, yangi stock yaratish
                    new_stock = StoreStock(
                        store_id=sale_item.source_id,
                        product_id=sale_item.product_id,
                        quantity=sale_item.quantity
                    )
                    db.session.add(new_stock)
                    print(
                        f"ğŸ“¦ Yangi store stock yaratildi: {sale_item.product.name} = {sale_item.quantity}")

            elif sale_item.source_type == 'warehouse':
                # Warehouse stock'ni qaytarish
                stock = WarehouseStock.query.filter_by(
                    warehouse_id=sale_item.source_id,
                    product_id=sale_item.product_id
                ).first()

                if stock:
                    stock.quantity += sale_item.quantity
                    print(
                        f"ğŸ“¦ Warehouse stock qaytarildi: {sale_item.product.name} +{sale_item.quantity} = {stock.quantity}")
                else:
                    # Agar stock yo'q bo'lsa, yangi stock yaratish
                    new_stock = WarehouseStock(
                        warehouse_id=sale_item.source_id,
                        product_id=sale_item.product_id,
                        quantity=sale_item.quantity
                    )
                    db.session.add(new_stock)
                    print(
                        f"ğŸ“¦ Yangi warehouse stock yaratildi: {sale_item.product.name} = {sale_item.quantity}")

        # Savdoni butunlay o'chirish
        db.session.delete(sale)
        db.session.commit()

        app.logger.info(
            f"âŒ Savdo rad etildi va o'chirildi: Sale ID {sale_id}, Sabab: {reason}")

        return jsonify({
            'success': True,
            'message': 'Savdo rad etildi va o\'chirildi. Mahsulotlar o\'z joylashuviga qaytarildi.'
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error rejecting sale: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Dokon holatini yangilash API'si olib tashlandi
# Endi faol/faol emas mantigi yo'q


@app.route('/edit-sale/<int:sale_id>')
def edit_sale_page(sale_id):
    """Savdoni tahrirlash sahifasi"""
    try:
        sale = Sale.query.get(sale_id)
        if not sale:
            return redirect(url_for('sales_history'))

        # Bu yerda tahrirlash sahifasini yaratish mumkin
        # Hozircha sales-history ga qaytarish
        return redirect(url_for('sales_history'))

    except Exception as e:
        logger.error(f" Edit sale sahifasida xatolik: {str(e)}")
        return redirect(url_for('sales_history'))


@app.route('/api/create-sale', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def create_sale():
    """Yangi savdo yaratish API endpoint"""
    try:
        print("ğŸš€ create-sale API ga so'rov keldi")
        logger.debug(f" Request method: {request.method}")
        logger.debug(f" Content-Type: {request.content_type}")
        logger.debug(f" Raw data: {request.get_data(as_text=True)}")

        data = request.get_json()
        logger.debug(f" Parsed JSON data: {data}")

        # Agar payment_status 'pending' bo'lsa, draft sifatida saqlash
        payment_status = data.get('payment_status', 'paid')
        if payment_status == 'pending':
            return create_pending_sale(data)

        if not data:
            logger.error(" Ma'lumot topilmadi!")
            return jsonify({
                'success': False,
                'error': 'Ma\'lumot topilmadi'
            }), 400

        customer_id = data.get('customer_id')
        items = data.get('items', [])
        multi_location = data.get('multi_location', False)
        # Tahrirlash rejimi uchun
        original_sale_id = data.get('original_sale_id')
        # Tahrirlash rejimi belgisi
        is_edit_mode = data.get('is_edit_mode', False)  # Tahrirlash rejimi

        logger.debug(f" Customer ID: {customer_id} (type: {type(customer_id)})")
        logger.info(f" Original Sale ID: {original_sale_id}")
        print(f"ğŸ“ Is Edit Mode: {is_edit_mode}")
        logger.debug(f" Items count: {len(items)}")
        logger.info(f" Multi-location mode: {multi_location}")

        # DEBUG: Barcha parametrlarni ko'rsatish
        logger.debug(" DEBUG: Kelgan barcha parametrlar:")
        for key, value in data.items():
            print(f"   {key}: {value}")

        # Debug: har bir item ni ko'rsatish
        for i, item in enumerate(items):
            print(
                f"ğŸ“‹ Item {i + 1}: ID={item.get('id')}, Name={item.get('name')}")
            print(
                f"   Location ID: {item.get('location_id')} (type: {type(item.get('location_id'))})")
            print(f"   Location Type: {item.get('location_type')}")
            print(f"   Location Name: {item.get('location_name')}")

        if not items:
            return jsonify({'success': False, 'error': 'Korzina bo\'sh'}), 400

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        user_role = session.get('role')
        if user_role == 'sotuvchi':
            user_id = session.get('user_id')
            user = User.query.get(user_id)

            if not user or not user.allowed_locations:
                return jsonify({
                    'success': False,
                    'error': 'Sizga ruxsat etilgan joylashuvlar mavjud emas'
                }), 403

            allowed_locations = user.allowed_locations

            # Joylashuvlarni tekshirish
            if multi_location:
                for item in items:
                    item_location_id = item.get('location_id')
                    item_location_type = item.get('location_type', 'store')

                    if item_location_id:
                        # Extract allowed IDs for this location type
                        allowed_ids = extract_location_ids(allowed_locations, item_location_type)

                        if item_location_id not in allowed_ids:
                            return jsonify({
                                'success': False,
                                'error': f'"{item.get("name", "")}" mahsuloti uchun tanlangan joylashuvga ruxsatingiz yo\'q'
                            }), 403
            else:
                location_id = data.get('location_id')
                location_type = data.get('location_type', 'store')

                if location_id:
                    # Extract allowed IDs for this location type
                    allowed_ids = extract_location_ids(allowed_locations, location_type)

                    if location_id not in allowed_ids:
                        return jsonify({
                            'success': False,
                            'error': 'Tanlangan joylashuvga ruxsatingiz yo\'q'
                        }), 403

        # Multi-location rejimida har bir item uchun joylashuv tekshirish
        if multi_location:
            for item in items:
                if (not item.get('location_id') or not item.get('location_type')):
                    product_name = item.get("name", "noma'lum")
                    error_msg = f'Mahsulot "{product_name}" uchun joylashuv ma\'lumoti yo\'q'
                    return jsonify({'success': False, 'error': error_msg}), 400
        else:
            # Eski rejim - bitta joylashuv
            location_id = data.get('location_id')
            location_type = data.get('location_type')

            if not location_id:
                return jsonify({
                    'success': False,
                    'error': 'Joylashuv tanlanmagan'
                }), 400

        # Multi-location yoki bitta location bo'yicha store topish
        if not multi_location:
            # Eski rejim - bitta joylashuv
            if location_type == 'store':
                store = Store.query.get(location_id)
                if not store:
                    return jsonify({
                        'success': False,
                        'error': 'Tanlangan do\'kon topilmadi (ID: {location_id})'
                    }), 404
                logger.info(f" Store topildi: {store.name} (ID: {store.id})")

            elif location_type == 'warehouse':
                warehouse = Warehouse.query.get(location_id)
                if not warehouse:
                    return jsonify({
                        'success': False,
                        'error': f'Tanlangan ombor topilmadi (ID: {location_id})'
                    }), 404

                # Warehouse uchun birinchi store ni ishlatish
                store = Store.query.first()
                if not store:
                    return jsonify({
                        'success': False,
                        'error': 'Savdo uchun dokon topilmadi'
                    }), 404
                logger.warning(f" Warehouse tanlangan: {warehouse.name}")
        else:
            # Multi-location rejimida eng ko'p ishlatiladigan store ni topish
            store_ids = []
            for item in items:
                if item.get('location_type') == 'store':
                    item_store_id = item.get('location_id')
                    if item_store_id:
                        store_ids.append(int(item_store_id))

            # Eng ko'p uchraydigan store_id ni topish
            if store_ids:
                from collections import Counter
                most_common_store_id = Counter(store_ids).most_common(1)[0][0]
                store = Store.query.get(most_common_store_id)
                logger.info(f" Multi-location: Eng ko'p ishlatilgan store - {store.name} (ID: {most_common_store_id})")
            else:
                # Agar barcha mahsulotlar warehouse dan bo'lsa, default store
                store = Store.query.first()
                logger.warning(f" Multi-location: Barcha mahsulotlar warehouse dan, default store - {store.name}")

            if not store:
                return jsonify({
                    'success': False,
                    'error': 'Savdo uchun dokon topilmadi'
                }), 404

        # Customer ID ni int ga o'girish
        final_customer_id = None
        if customer_id:
            try:
                final_customer_id = int(customer_id)
            except (ValueError, TypeError):
                final_customer_id = None

        # Agar mijoz tanlanmagan bo'lsa, null qoldiramiz (yangi mijoz yaratmaymiz)
        # Frontend'da "Noma'lum" matn ko'rsatiladi

        # Hozirgi kursni olish
        current_rate = get_current_currency_rate()

        # Current user ni olish
        current_user = get_current_user()
        if not current_user:
            return jsonify({
                'success': False,
                'error': 'Foydalanuvchi topilmadi'
            }), 401

        # Payment status ni frontend dan olish yoki avtomatik aniqlash
        final_payment_status = data.get('payment_status', 'paid')

        # Payment ma'lumotlarini olish
        payment_info = data.get('payment', {})

        # Har bir to'lov turining summasini olish (USD)
        cash_usd = float(payment_info.get('cash_usd', 0))
        click_usd = float(payment_info.get('click_usd', 0))
        terminal_usd = float(payment_info.get('terminal_usd', 0))
        debt_usd = float(payment_info.get('debt_usd', 0))

        # UZS qiymatlarni olish
        cash_uzs = float(payment_info.get('cash_uzs', 0))
        click_uzs = float(payment_info.get('click_uzs', 0))
        terminal_uzs = float(payment_info.get('terminal_uzs', 0))
        debt_uzs = float(payment_info.get('debt_uzs', 0))

        # Debug: To'lov ma'lumotlarini ko'rsatish
        print("ğŸ’° To'lov ma'lumotlari:")
        print(f"   Cash USD: {cash_usd}, UZS: {cash_uzs}")
        print(f"   Click USD: {click_usd}, UZS: {click_uzs}")
        print(f"   Terminal USD: {terminal_usd}, UZS: {terminal_uzs}")
        print(f"   Debt USD: {debt_usd}, UZS: {debt_uzs}")
        print(f"   Jami: {cash_usd + click_usd + terminal_usd + debt_usd} USD")

        # Payment status ni qarz bo'yicha avtomatik aniqlash
        if debt_usd > 0:
            # Agar qarz bo'lsa - partial (qisman to'langan)
            final_payment_status = 'partial'
            logger.info(f"ğŸ’³ Qarz aniqlandi: {debt_usd} USD, payment_status = 'partial'")
        else:
            # Agar qarz yo'q bo'lsa - to'liq to'langan (pending bo'lsa ham)
            final_payment_status = 'paid'
            logger.info("âœ… To'liq to'langan, payment_status = 'paid'")

        # Payment method ni aniqlash (birinchi to'lov turini olish)
        payment_method = 'cash'  # default
        if click_usd > 0:
            payment_method = 'click'
        elif terminal_usd > 0:
            payment_method = 'terminal'
        elif debt_usd > 0:
            payment_method = 'debt'
        elif cash_usd > 0:
            payment_method = 'cash'

        print(f"ğŸ’³ Payment method aniqlandi: {payment_method}")

        # Barcha qiymatlarni USD da saqlaymiz
        # cash_amount, click_amount, terminal_amount, debt_amount - hammasi USD!
        cash_amount = cash_usd
        click_amount = click_usd
        terminal_amount = terminal_usd
        debt_amount = debt_usd

        print("ğŸ’µ USD summalar (DB'ga saqlanadi):")
        print(f"   Cash: ${cash_amount}")
        print(f"   Click: ${click_amount}")
        print(f"   Terminal: ${terminal_amount}")
        print(f"   Debt: ${debt_amount}")

        # Savdo uchun asosiy joylashuvni aniqlash
        # Multi-location bo'lsa - eng ko'p ishlatiladigan
        # Bitta location bo'lsa - o'sha location
        if multi_location:
            # Eng ko'p ishlatiladigan store location_id va location_type
            sale_location_id = store.id
            sale_location_type = 'store'
        else:
            sale_location_id = location_id
            sale_location_type = location_type

        # TAHRIRLASH yoki YANGI SAVDO?
        if is_edit_mode and original_sale_id:
            print(f"\nğŸ”„ TAHRIRLASH REJIMI: Sale ID={original_sale_id}")

            current_sale = Sale.query.get(original_sale_id)
            if not current_sale:
                return jsonify({
                    'success': False,
                    'error': f'Tahrirlash uchun savdo topilmadi: {original_sale_id}'
                }), 404

            print("âœ… Asl savdo topildi - UPDATE qilinmoqda")

            # Eski SaleItem'larni o'chirish
            SaleItem.query.filter_by(sale_id=original_sale_id).delete()
            print("ğŸ—‘ï¸  Eski mahsulotlar o'chirildi")

            # Sale ma'lumotlarini yangilash
            current_sale.customer_id = final_customer_id
            current_sale.store_id = store.id
            current_sale.location_id = sale_location_id
            current_sale.location_type = sale_location_type
            current_sale.seller_id = current_user.id
            current_sale.payment_method = payment_method
            current_sale.payment_status = final_payment_status
            # UZS summalar
            current_sale.cash_amount = Decimal(str(cash_amount))
            current_sale.click_amount = Decimal(str(click_amount))
            current_sale.terminal_amount = Decimal(str(terminal_amount))
            current_sale.debt_amount = Decimal(str(debt_amount))
            # USD summalar
            current_sale.cash_usd = Decimal(str(cash_usd))
            current_sale.click_usd = Decimal(str(click_usd))
            current_sale.terminal_usd = Decimal(str(terminal_usd))
            current_sale.debt_usd = Decimal(str(debt_usd))
            current_sale.notes = f'Tahrirlandi - {len(items)} ta mahsulot' if multi_location else 'Tahrirlandi'
            current_sale.currency_rate = current_rate
            # Savdo sanasi asl holatda qoladi (o'zgartirilmaydi)

        else:
            # Yangi savdo yaratish
            print("\nâœ… YANGI SAVDO yaratilmoqda")

            current_sale = Sale(
                customer_id=final_customer_id,
                store_id=store.id,
                location_id=sale_location_id,
                location_type=sale_location_type,
                seller_id=current_user.id,
                payment_method=payment_method,
                payment_status=final_payment_status,
                # UZS summalar
                cash_amount=Decimal(str(cash_amount)),
                click_amount=Decimal(str(click_amount)),
                terminal_amount=Decimal(str(terminal_amount)),
                debt_amount=Decimal(str(debt_amount)),
                # USD summalar
                cash_usd=Decimal(str(cash_usd)),
                click_usd=Decimal(str(click_usd)),
                terminal_usd=Decimal(str(terminal_usd)),
                debt_usd=Decimal(str(debt_usd)),
                notes=f'Multi-location savdo - {len(items)} ta mahsulot' if multi_location else None,
                currency_rate=current_rate,
                created_by=f'{current_user.first_name} {current_user.last_name}'
            )
            db.session.add(current_sale)
            db.session.flush()  # ID ni olish uchun

        total_profit = Decimal('0')
        total_revenue = Decimal('0')
        total_cost = Decimal('0')

        # Har bir mahsulot uchun SaleItem yaratish (ham yangi, ham tahrirlashda bir xil)
        for item in items:
            # product_id ni id yoki product_id dan olish
            product_id = item.get('product_id') or item.get('id')
            quantity = Decimal(str(item.get('quantity', 0)))
            unit_price_usd = float(item.get('unit_price') or item.get('price', 0))

            logger.debug(f" Processing item: {item}")
            logger.debug(f" Product ID: {product_id} (type: {type(product_id)})")

            if quantity <= 0:
                continue

            # Multi-location rejimida har bir mahsulot uchun joylashuv
            if multi_location:
                item_location_id = item.get('location_id')
                item_location_type = item.get('location_type')
            else:
                item_location_id = location_id
                item_location_type = location_type

            # Mahsulotni topish
            product = Product.query.get(product_id)
            if not product:
                return jsonify({
                    'success': False,
                    'error': f'Mahsulot topilmadi: {product_id}'
                }), 404

            # Stock tekshirish va yangilash (har bir mahsulot o'z
            # joylashuvidan)
            if item_location_type == 'store':
                # Store stock tekshirish
                stock = StoreStock.query.filter_by(
                    store_id=item_location_id,
                    product_id=product_id
                ).first()

                if not stock:
                    store_obj = Store.query.get(item_location_id)
                    store_name = store_obj.name if store_obj else 'noma\'lum'
                    return jsonify({
                        'success': False,
                        'error': (f'{store_name} do\'konida {product.name} '
                                  f'mahsuloti mavjud emas')
                    }), 400

                # Tahrirlash rejimida asl savdo miqdorini hisobga olish
                available_quantity = stock.quantity
                print(
                    f"ğŸ” Store stock tekshiruvi: product_id={product_id}, current_stock={stock.quantity}")
                print(
                    f"ğŸ” Tahrirlash rejimi: is_edit_mode={is_edit_mode}, original_sale_id={original_sale_id}")

                if is_edit_mode and original_sale_id:
                    # Asl savdoda bu mahsulotning miqdorini topish
                    original_sale_item = db.session.query(SaleItem).filter_by(
                        sale_id=original_sale_id,
                        product_id=product_id
                    ).first()

                    logger.debug(f" Asl savdo item topildi: {original_sale_item}")
                    if original_sale_item:
                        print(
                            f"ğŸ” Asl savdo miqdori: {original_sale_item.quantity}")
                        # Asl savdo miqdorini qo'shish (chunki tahrirlashda
                        # qaytariladi)
                        available_quantity += original_sale_item.quantity
                        print(
                            f"ğŸ“ Tahrirlash rejimi: mahsulot {product_id} uchun asl miqdor {original_sale_item.quantity} qo'shildi")
                        print(
                            f"ğŸ“Š Mavjud miqdor: {stock.quantity} + {original_sale_item.quantity} = {available_quantity}")
                        logger.info(f" Kerakli miqdor: {quantity}")
                        logger.info(f" Farq: {available_quantity - quantity}")
                    else:
                        print(
                            f"âš ï¸ Asl savdoda mahsulot {product_id} topilmadi")

                # Stock tekshirish olib tashlandi - stock allaqachon rezerv
                # qilingan
                print(
                    f"â„¹ï¸ Stock validation o'tkazildi: available={available_quantity}, required={quantity}")

                # Stock allaqachon korzinaga qo'shilganda ayirilgan
                print(
                    "â„¹ï¸ Store stock dan ayirilmaydi (allaqachon rezerv qilingan)")

            elif item_location_type == 'warehouse':
                # Warehouse stock tekshirish
                stock = WarehouseStock.query.filter_by(
                    warehouse_id=item_location_id,
                    product_id=product_id
                ).first()

                if not stock:
                    warehouse_obj = Warehouse.query.get(item_location_id)
                    warehouse_name = warehouse_obj.name if warehouse_obj else 'noma\'lum'
                    return jsonify({
                        'success': False,
                        'error': (f'{warehouse_name} omborida {product.name} '
                                  f'mahsuloti mavjud emas')
                    }), 400

                # Tahrirlash rejimida asl savdo miqdorini hisobga olish
                available_quantity = stock.quantity
                print(
                    f"ğŸ” Warehouse stock tekshiruvi: product_id={product_id}, current_stock={stock.quantity}")
                print(
                    f"ğŸ” Tahrirlash rejimi: is_edit_mode={is_edit_mode}, original_sale_id={original_sale_id}")

                if is_edit_mode and original_sale_id:
                    # Asl savdoda bu mahsulotning miqdorini topish
                    original_sale_item = db.session.query(SaleItem).filter_by(
                        sale_id=original_sale_id,
                        product_id=product_id
                    ).first()

                    logger.debug(f" Asl savdo item topildi: {original_sale_item}")
                    if original_sale_item:
                        print(
                            f"ğŸ” Asl savdo miqdori: {original_sale_item.quantity}")
                        # Asl savdo miqdorini qo'shish
                        available_quantity += original_sale_item.quantity
                        print(
                            f"ğŸ“ Warehouse tahrirlash: mahsulot {product_id} uchun asl miqdor {original_sale_item.quantity} qo'shildi")
                        print(
                            f"ğŸ“Š Mavjud miqdor: {stock.quantity} + {original_sale_item.quantity} = {available_quantity}")
                        logger.info(f" Kerakli miqdor: {quantity}")
                        logger.info(f" Farq: {available_quantity - quantity}")
                    else:
                        print(
                            f"âš ï¸ Asl savdoda mahsulot {product_id} topilmadi")

                # Stock tekshirish olib tashlandi - stock allaqachon rezerv
                # qilingan
                print(
                    f"â„¹ï¸ Warehouse stock validation o'tkazildi: available={available_quantity}, required={quantity}")

                # Stock allaqachon korzinaga qo'shilganda ayirilgan
                print(
                    "â„¹ï¸ Warehouse stock dan ayirilmaydi (allaqachon rezerv qilingan)")

            # Savdo summasini hisoblash
            total_amount_usd = Decimal(str(unit_price_usd)) * quantity  # USD da

            # Cost price allaqachon USD da (products jadvalidagi qiymat)
            unit_cost_price_usd = float(product.cost_price)  # USD da
            total_cost_price_usd = Decimal(str(unit_cost_price_usd)) * quantity  # Jami tan narx (USD)

            # Foyda USD da hisoblash
            profit_usd = total_amount_usd - total_cost_price_usd  # USD da

            # Location ma'lumotini yaratish
            if item_location_type == 'warehouse':
                warehouse_obj = Warehouse.query.get(item_location_id)
                location_info = f'Ombor: {warehouse_obj.name} (ID: {warehouse_obj.id})'
            else:
                store_obj = Store.query.get(item_location_id)
                location_info = f'Dokon: {store_obj.name} (ID: {store_obj.id})'

            # SaleItem yaratish
            sale_item = SaleItem(
                sale_id=current_sale.id,  # Yangi yoki tahrirlangan sale ID
                product_id=product_id,
                quantity=quantity,
                unit_price=Decimal(str(unit_price_usd)),  # USD da saqlanadi
                total_price=Decimal(str(unit_price_usd)) * quantity,  # USD da
                cost_price=Decimal(str(unit_cost_price_usd)),  # USD da
                profit=profit_usd,  # USD da (allaqachon Decimal)
                source_type=item_location_type,
                source_id=item_location_id,
                notes=f'{product.name} | {location_info}'
            )

            db.session.add(sale_item)
            total_profit += profit_usd
            total_revenue += total_amount_usd  # USD da
            total_cost += total_cost_price_usd

        # Savdo jami summasini yangilash (ham yangi, ham tahrirlash uchun)
        current_sale.total_amount = Decimal(str(total_revenue))  # USD da
        current_sale.total_cost = Decimal(str(total_cost))  # USD da
        current_sale.total_profit = Decimal(str(total_profit))  # USD da

        # Ma'lumotlar bazasiga saqlash
        db.session.commit()

        action_text = 'tahrirlandi' if is_edit_mode else 'yaratildi'
        print(f"âœ… Savdo {action_text}: ID={current_sale.id}, Items={len(items)}, Total=${total_revenue}")

        # Telegram xabar yuborish (faqat yangi savdo yaratilganda va mijoz telegram_chat_id bor bo'lsa)
        if not is_edit_mode and final_customer_id:
            try:
                customer = Customer.query.get(final_customer_id)
                if customer and customer.telegram_chat_id:
                    from telegram_bot import get_bot_instance
                    bot = get_bot_instance(db=db)

                    # Joylashuv nomini olish
                    if sale_location_type == 'warehouse':
                        warehouse_obj = Warehouse.query.get(sale_location_id)
                        location_name = warehouse_obj.name if warehouse_obj else "Ombor"
                    else:
                        store_obj = Store.query.get(sale_location_id)
                        location_name = store_obj.name if store_obj else "Do'kon"

                    # To'lov summalari (UZS da)
                    total_uzs = cash_amount + click_amount + terminal_amount + debt_amount
                    paid_uzs = cash_amount + click_amount + terminal_amount

                    # Savdo mahsulotlarini PDF uchun tayyorlash
                    sale_items_for_pdf = []
                    for item in current_sale.items:
                        sale_items_for_pdf.append({
                            'name': item.product.name if item.product else 'Mahsulot',
                            'quantity': float(item.quantity),
                            'unit_price': float(item.unit_price) * float(current_sale.currency_rate),
                            'total': float(item.total_price) * float(current_sale.currency_rate)
                        })

                    # Telegram xabar yuborish
                    bot.send_sale_notification_sync(
                        chat_id=customer.telegram_chat_id,
                        customer_name=customer.name,
                        customer_id=customer.id,
                        sale_date=current_sale.sale_date,
                        location_name=location_name,
                        total_amount_uzs=total_uzs,
                        paid_uzs=paid_uzs,
                        cash_uzs=cash_amount,
                        click_uzs=click_amount,
                        terminal_uzs=terminal_amount,
                        debt_uzs=debt_amount,
                        sale_id=current_sale.id,
                        sale_items=sale_items_for_pdf
                    )
                    logger.info(f"âœ… Telegram xabar va PDF yuborildi: {customer.name}")
            except Exception as telegram_error:
                logger.warning(f"âš ï¸ Telegram xabar yuborishda xatolik: {telegram_error}")
                # Telegram xatosi savdo yaratishni to'xtatmasin

        # OperationHistory ga yozish
        location_name = ''
        if sale_location_type == 'store':
            store_obj = Store.query.get(sale_location_id)
            if store_obj:
                location_name = store_obj.name
        elif sale_location_type == 'warehouse':
            warehouse_obj = Warehouse.query.get(sale_location_id)
            if warehouse_obj:
                location_name = warehouse_obj.name

        # Mahsulotlar tavsifi
        products_desc = ', '.join([f"{item.product.name} ({item.quantity} ta)" for item in current_sale.items])

        operation_type = 'sale' if not is_edit_mode else 'edit'
        description = f"Savdo yaratildi: {products_desc}" if not is_edit_mode else f"Savdo tahrirlandi: {products_desc}"

        operation = OperationHistory(
            operation_type=operation_type,
            table_name='sales',
            record_id=current_sale.id,
            user_id=current_user.id,
            username=f'{current_user.first_name} {current_user.last_name}',
            description=description,
            old_data=None,
            new_data={
                'sale_id': current_sale.id,
                'total_amount_usd': float(current_sale.total_amount),
                'payment_status': current_sale.payment_status,
                'payment_method': current_sale.payment_method,
                'items_count': len(current_sale.items)
            },
            ip_address=request.remote_addr,
            location_id=sale_location_id,
            location_type=sale_location_type,
            location_name=location_name,
            amount=float(current_sale.total_amount * current_sale.currency_rate)  # UZS da
        )
        db.session.add(operation)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': f'Savdo {action_text} - {len(items)} ta mahsulot',
            'data': {
                'sale_id': current_sale.id,
                'items_count': len(items),
                'total_revenue': total_revenue,
                'total_profit': total_profit,
                'store_name': store.name
            }
        })

    except TimeoutError:
        db.session.rollback()
        logger.error("â±ï¸ Database timeout in create_sale")
        return jsonify({
            'success': False,
            'error': 'So\'rov juda uzoq davom etdi. Qayta urinib ko\'ring.',
            'error_type': 'timeout'
        }), 504
    except OperationalError as e:
        db.session.rollback()
        logger.error(f"ğŸ”Œ Database connection xatosi: {e}")
        return jsonify({
            'success': False,
            'error': 'Ma\'lumotlar bazasiga ulanishda xatolik',
            'error_type': 'database_connection'
        }), 503
    except IntegrityError as e:
        db.session.rollback()
        logger.error(f"âŒ Integrity error: {e}")
        return jsonify({
            'success': False,
            'error': 'Ma\'lumotlarni saqlashda xatolik',
            'error_type': 'integrity_error'
        }), 400
    except BadRequest as e:
        db.session.rollback()
        logger.error(f"âŒ Bad request: {e}")
        return jsonify({
            'success': False,
            'error': 'Noto\'g\'ri so\'rov formati',
            'error_type': 'bad_request'
        }), 400
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"âŒ Error creating sale: {str(e)}", exc_info=True)
        return jsonify({
            'success': False,
            'error': f'Savdo yaratishda xatolik: {str(e)}',
            'error_type': 'internal_server_error'
        }), 500


# Savdoni o'chirish API
@app.route('/api/sales/<int:sale_id>', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def get_sale(sale_id):
    """Bitta savdoni olish va tafsilotlarini ko'rish uchun"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({
                'success': False,
                'error': 'Savdo topilmadi'
            }), 404

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []

            # Savdo qaysi joyda amalga oshirilganini aniqlash
            sale_location_id = sale.store_id if sale.store_id else sale.warehouse_id
            sale_location_type = 'store' if sale.store_id else 'warehouse'

            # Ruxsat etilgan joylashuvlardan ID larni ajratib olish
            allowed_location_ids = extract_location_ids(allowed_locations, sale_location_type)

            # Agar sotuvchiga bu joylashuvda savdo qilish ruxsati bo'lmasa
            if sale_location_id not in allowed_location_ids:
                return jsonify({
                    'success': False,
                    'error': 'Bu savdoni ko\'rish uchun ruxsatingiz yo\'q'
                }), 403

        return jsonify({
            'success': True,
            'sale': sale.to_dict()
        })

    except Exception as e:
        app.logger.error(f"Error fetching sale: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Savdoni olishda xatolik: {str(e)}'
        }), 500


@app.route('/api/sales/<int:sale_id>', methods=['PUT'])
@role_required('admin', 'kassir', 'sotuvchi')
def update_sale(sale_id):
    """Savdoni tahrirlash (yangi structure)"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({
                'success': False,
                'error': 'Savdo topilmadi'
            }), 404

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []

            # Savdo qaysi joyda amalga oshirilganini aniqlash
            sale_location_id = sale.store_id if sale.store_id else sale.warehouse_id
            sale_location_type = 'store' if sale.store_id else 'warehouse'

            # Ruxsat etilgan joylashuvlardan ID larni ajratib olish
            allowed_location_ids = extract_location_ids(allowed_locations, sale_location_type)

            # Agar sotuvchiga bu joylashuvda savdo qilish ruxsati bo'lmasa
            if sale_location_id not in allowed_location_ids:
                return jsonify({
                    'success': False,
                    'error': 'Bu savdoni tahrirlash uchun ruxsatingiz yo\'q'
                }), 403

        data = request.get_json()
        app.logger.info(f"ğŸ”„ UPDATE Sale ID: {sale_id}")
        app.logger.info(f"ğŸ“¦ Update data: {data}")
        app.logger.info(f"ğŸ’° Sale payment status: {sale.payment_status}")

        # Sale statusini tekshirish
        is_confirmed_sale = sale.payment_status == 'paid'
        app.logger.info(f"ğŸ” Is confirmed sale: {is_confirmed_sale}")

        # Eski Sale ma'lumotlarini yangilash
        if 'customer_id' in data and data['customer_id']:
            sale.customer_id = int(data['customer_id'])
        elif 'customer_id' in data and not data['customer_id']:
            sale.customer_id = None
        if 'payment_method' in data:
            sale.payment_method = data['payment_method']
        if 'payment_status' in data:
            sale.payment_status = data['payment_status']
        if 'notes' in data:
            sale.notes = data['notes']

        # Eski SaleItem'larni dictionary'ga yig'ish (product_id -> quantity
        # mapping)
        old_items = SaleItem.query.filter_by(sale_id=sale.id).all()
        old_quantities = {}
        for item in old_items:
            key = (item.product_id, item.source_type, item.source_id)
            old_quantities[key] = item.quantity

        # Confirmed sale: stock allaqachon ayirilgan, real-time API bilan boshqariladi
        # Edit da faqat difference logic ishlatamiz
        app.logger.info(
            f"ğŸ“¦ Real-time stock system: {'confirmed' if is_confirmed_sale else 'pending'} sale")

        # Eski SaleItem'larni o'chirish
        SaleItem.query.filter_by(sale_id=sale.id).delete()

        # Yangi SaleItem'larni yaratish va stock farqlarini hisoblash
        if 'items' in data:
            total_amount = Decimal('0')
            total_cost = Decimal('0')
            total_profit = Decimal('0')
            new_quantities = {}

            for item_data in data['items']:
                product = Product.query.get(item_data['product_id'])
                if not product:
                    continue

                quantity = Decimal(str(item_data['quantity']))
                unit_price_usd = Decimal(str(item_data['unit_price']))

                # Cost price allaqachon USD da (products jadvalidagi qiymat)
                cost_price_usd = product.cost_price or Decimal('0')

                location_id = item_data.get('location_id')
                location_type = item_data.get('location_type', 'store')

                key = (product.id, location_type, location_id)
                new_quantities[key] = new_quantities.get(key, 0) + quantity

                # Yangi SaleItem yaratish
                loc_name = 'Ombor' if location_type == 'warehouse' else "Do'kon"
                source_name = f"{loc_name} (ID: {location_id})"

                # Foyda USD da hisoblash
                profit_usd = (unit_price_usd - cost_price_usd) * quantity

                sale_item = SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=unit_price_usd,  # USD da
                    total_price=quantity * unit_price_usd,  # USD da
                    cost_price=cost_price_usd,  # USD da
                    profit=profit_usd,  # USD da
                    source_type=location_type,
                    source_id=location_id,
                    notes=f"{product.name} | {source_name}"
                )
                db.session.add(sale_item)

                # Jami hisoblar (USD da)
                total_amount += sale_item.total_price
                total_cost += cost_price_usd * quantity
                total_profit += sale_item.profit

            # ✅ BUG FIX: Stock farqlarini hisoblash va avtomatik tuzatish
            # Eski va yangi miqdorlarni solishtirish, farqni stockga qaytarish/ayirish
            for key in set(list(old_quantities.keys()) + list(new_quantities.keys())):
                product_id, location_type, location_id = key
                old_qty = old_quantities.get(key, Decimal('0'))
                new_qty = new_quantities.get(key, Decimal('0'))
                difference = new_qty - old_qty  # Ijobiy: qo'shimcha sotildi, Manfiy: qaytarildi
                
                if difference != 0:
                    app.logger.info(f"📦 STOCK DIFFERENCE UPDATE: Product {product_id}, {location_type}/{location_id}")
                    app.logger.info(f"   Old quantity: {old_qty}, New quantity: {new_qty}, Difference: {difference}")
                    
                    # Stock'ni yangilash
                    if location_type == 'warehouse':
                        warehouse_stock = WarehouseStock.query.filter_by(
                            warehouse_id=location_id,
                            product_id=product_id
                        ).first()
                        
                        if warehouse_stock:
                            old_stock = warehouse_stock.quantity
                            warehouse_stock.quantity -= difference  # Difference ijobiy bo'lsa kamaytiradi, manfiy bo'lsa ko'paytiradi
                            warehouse_stock.last_updated = db.func.current_timestamp()
                            app.logger.info(f"   ✅ Warehouse stock: {old_stock} -> {warehouse_stock.quantity}")
                    
                    elif location_type == 'store':
                        store_stock = StoreStock.query.filter_by(
                            store_id=location_id,
                            product_id=product_id
                        ).first()
                        
                        if store_stock:
                            old_stock = store_stock.quantity
                            store_stock.quantity -= difference  # Difference ijobiy bo'lsa kamaytiradi, manfiy bo'lsa ko'paytiradi
                            store_stock.last_updated = db.func.current_timestamp()
                            app.logger.info(f"   ✅ Store stock: {old_stock} -> {store_stock.quantity}")
                    
                    # Operations history ga yozish
                    log_operation(
                        operation_type='sale_edit',
                        table_name='sales',
                        record_id=sale.id,
                        description=f"Savdo tahrirlandi - Stock tuzatildi: Product #{product_id}, Eski: {old_qty}, Yangi: {new_qty}",
                        old_data={'product_id': product_id, 'quantity': float(old_qty)},
                        new_data={'product_id': product_id, 'quantity': float(new_qty), 'stock_difference': float(difference)},
                        user_id=current_user.id,
                        username=current_user.username,
                        location_id=location_id,
                        location_type=location_type
                    )
            
            # Sale jami ma'lumotlarini yangilash
            sale.total_amount = total_amount
            sale.total_cost = total_cost
            sale.total_profit = total_profit
            # Tahrirlash vaqtidagi joriy kurs
            sale.currency_rate = get_current_currency_rate()

        db.session.commit()
        app.logger.info(f"âœ… Sale {sale_id} successfully updated")

        return jsonify({
            'success': True,
            'message': 'Savdo muvaffaqiyatli yangilandi',
            'data': sale.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Error updating sale: {str(e)}")
        return jsonify({
            'success': False,
            'error': f'Savdoni yangilashda xatolik: {str(e)}'
        }), 500


@app.route('/api/sales/<int:sale_id>', methods=['DELETE'])
@role_required('admin', 'kassir', 'sotuvchi')
def delete_sale_with_stock_return(sale_id):
    """Savdoni o'chirish va stock ni qaytarish - yangi tuzilma bilan"""
    try:
        # Query parameter: return_stock (default: true)
        return_stock = request.args.get('return_stock', 'true').lower() == 'true'

        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Savdoni topish
        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({
                'success': False,
                'error': 'Savdo topilmadi'
            }), 404

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []

            # Savdo qaysi joyda amalga oshirilganini aniqlash
            sale_location_id = sale.store_id if sale.store_id else sale.warehouse_id
            sale_location_type = 'store' if sale.store_id else 'warehouse'

            # Ruxsat etilgan joylashuvlardan ID larni ajratib olish
            allowed_location_ids = extract_location_ids(allowed_locations, sale_location_type)

            # Agar sotuvchiga bu joylashuvda savdo qilish ruxsati bo'lmasa
            if sale_location_id not in allowed_location_ids:
                return jsonify({
                    'success': False,
                    'error': 'Bu savdoni o\'chirish uchun ruxsatingiz yo\'q'
                }), 403

        # Debug: Savdo ma'lumotlarini ko'rsatish
        print("ğŸ—‘ï¸ ========== SAVDO O'CHIRILMOQDA ==========")
        print(f"ğŸ—‘ï¸ Sale ID: {sale_id}")
        print(f"ğŸ—‘ï¸ Return stock: {return_stock}")
        print(f"ğŸ—‘ï¸ Items count: {len(sale.items)}")
        logger.info(f"ğŸ—‘ï¸ DELETE: Sale ID={sale_id}, return_stock={return_stock}, items={len(sale.items)}")

        # Faqat return_stock=true bo'lsa stokni qaytarish
        if return_stock:
            print(f"âœ… Stock qaytariladi - {len(sale.items)} ta mahsulot")
            for item in sale.items:
                # Agar product o'chirilgan bo'lsa (product_id NULL), stock qaytarib bo'lmaydi
                if not item.product_id:
                    logger.warning(f"âš ï¸ DELETE: Product o'chirilgan (sale_item {item.id}), stock qaytarilmaydi")
                    continue

                # Agar source_id yo'q bo'lsa (ma'lumot buzilgan), stock qaytarib bo'lmaydi
                if not item.source_id:
                    logger.warning(f"âš ï¸ DELETE: Source ID yo'q (sale_item {item.id}), stock qaytarilmaydi")
                    continue

                logger.debug(f" DELETE: Product {item.product_id}, Qty: {item.quantity}")
                logger.debug(f" DELETE: Source {item.source_type}, ID: {item.source_id}")

                # SaleItem uchun stock qaytarish
                if item.source_type == 'warehouse':
                    # Warehouse stock ga qaytarish
                    warehouse_stock = WarehouseStock.query.filter_by(
                        warehouse_id=item.source_id,
                        product_id=item.product_id
                    ).first()

                    if warehouse_stock:
                        # Mavjud stock ga qo'shish
                        old_qty = warehouse_stock.quantity
                        warehouse_stock.quantity += item.quantity
                        warehouse_stock.last_updated = db.func.current_timestamp()
                        print(
                            f"ğŸ” DELETE: Warehouse updated: {old_qty} + {item.quantity}")
                    else:
                        # Yangi stock yaratish
                        new_stock = WarehouseStock(
                            warehouse_id=item.source_id,
                            product_id=item.product_id,
                            quantity=item.quantity,
                            last_updated=db.func.current_timestamp()
                        )
                        db.session.add(new_stock)
                        logger.debug(f" DELETE: New warehouse stock: {item.quantity}")

                elif item.source_type == 'store':
                    # Store stock ga qaytarish
                    store_stock = StoreStock.query.filter_by(
                        store_id=item.source_id,
                        product_id=item.product_id
                    ).first()

                    if store_stock:
                        # Mavjud stock ga qo'shish
                        old_qty = store_stock.quantity
                        store_stock.quantity += item.quantity
                        store_stock.last_updated = db.func.current_timestamp()
                        print(
                            f"ğŸ” DELETE: Store updated: {old_qty} + {item.quantity}")
                    else:
                        # Yangi stock yaratish
                        new_stock = StoreStock(
                            store_id=item.source_id,
                            product_id=item.product_id,
                            quantity=item.quantity,
                            last_updated=db.func.current_timestamp()
                        )
                        db.session.add(new_stock)
                        logger.debug(f" DELETE: New store stock: {item.quantity}")
        else:
            logger.debug("âš ï¸ DELETE: Stock qaytarilmaydi (return_stock=false)")

        # Ma'lumotlarni olish (o'chirishdan oldin)
        total_items = len(sale.items)
        store_name = sale.store.name if sale.store else 'Noma\'lum'

        # Savdoni o'chirish (cascade delete SaleItems ham o'chiradi)
        db.session.delete(sale)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Savdo o\'chirildi! {total_items} ta mahsulot stockga qaytarildi.',
            'data': {
                'sale_id': sale_id,
                'total_items': total_items,
                'store_name': store_name
            }
        })

    except Exception as e:
        db.session.rollback()
        error_msg = str(e)
        print("ğŸ”´ Sale o'chirishda xatolik!")
        print(f"ğŸ”´ Sale ID: {sale_id}")
        print(f"ğŸ”´ Xatolik: {error_msg}")
        import traceback
        traceback.print_exc()
        app.logger.error(f"Error deleting sale {sale_id}: {error_msg}")
        app.logger.error(f"Traceback: {traceback.format_exc()}")
        return jsonify({
            'success': False,
            'error': f'Savdoni o\'chirishda xatolik: {error_msg}'
        }), 500


def create_pending_sale(data):
    """Tasdiqlanmagan savdoni yaratish (draft holatida)"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        logger.info("ğŸ“ Pending savdo yaratilmoqda...")

        customer_id = data.get('customer_id')
        items = data.get('items', [])
        notes = data.get('notes', 'Keyinroq tasdiqlash uchun saqlangan')
        original_sale_id = data.get('original_sale_id')
        pending_sale_id = data.get('pending_sale_id')
        skip_stock_return = data.get('skip_stock_return', False)
        original_quantities = data.get('original_quantities', {})  # Asl miqdorlar

        logger.info("ğŸ” PENDING SALE PARAMS:")
        logger.info(f"   original_sale_id: {original_sale_id}")
        logger.info(f"   pending_sale_id: {pending_sale_id}")
        logger.info(f"   skip_stock_return: {skip_stock_return} (type: {type(skip_stock_return)})")
        logger.info(f"   original_quantities: {original_quantities}")

        if not items:
            return jsonify({'success': False, 'error': 'Korzina bo\'sh'}), 400

        # Eski pending savdoni o'chirish (agar mavjud bo'lsa)
        if pending_sale_id:
            old_pending_sale = Sale.query.get(pending_sale_id)
            if old_pending_sale:
                # Eski pending sale items'ni o'chirish
                SaleItem.query.filter_by(sale_id=pending_sale_id).delete()
                # Eski pending savdoni o'chirish
                db.session.delete(old_pending_sale)
                logger.info(f" Eski pending savdo o'chirildi: {pending_sale_id}")

        # Agar asl savdo ID'si berilgan bo'lsa, uni o'chirish
        original_sale_date = None  # Asl savdo vaqtini saqlash uchun
        if original_sale_id:
            logger.info(f"ğŸ“ Asl savdoni pending qilish: ID={original_sale_id}")
            original_sale = Sale.query.get(original_sale_id)
            if original_sale:
                # Asl savdo vaqtini saqlash
                original_sale_date = original_sale.sale_date
                logger.info(f"ğŸ• Asl savdo vaqti saqlandi: {original_sale_date}")

                # âš ï¸ MUHIM: Stock qaytarilmasligi kerak!
                # Frontend allaqachon real-time stock boshqaradi:
                # - Miqdor kamaysa: frontend stock qaytaradi
                # - Miqdor oshsa: frontend stock rezerv qiladi
                # Backend'da stock qaytarish duplicate yaratadi!
                logger.info("â­ï¸ Stock qaytarilmaydi - frontend real-time boshqaradi")

                # Asl savdoni o'chirish
                db.session.delete(original_sale)
                logger.info("âœ… Asl savdo o'chirildi")
        else:
            logger.info("ğŸ“ Yangi pending savdo yaratilmoqda (asl savdo yo'q)")

        # Customer ID ni int ga o'girish
        final_customer_id = None
        if customer_id:
            try:
                final_customer_id = int(customer_id)
            except (ValueError, TypeError):
                final_customer_id = None

        # Birinchi mahsulotdan store_id ni olish
        first_item = items[0]
        item_location_id = first_item.get('location_id')
        item_location_type = first_item.get('location_type')

        logger.info(f"ğŸ“ Location ma'lumotlari: location_id={item_location_id}, location_type={item_location_type}")

        # Store ID ni aniqlash
        if item_location_type == 'store':
            store_id = item_location_id
        else:
            # Agar warehouse bo'lsa, birinchi store'ni olish
            store = Store.query.first()
            store_id = store.id if store else 1

        # Hozirgi kursni olish
        current_rate = get_current_currency_rate()

        # Pending savdoni yaratish
        new_sale = Sale(
            customer_id=final_customer_id,
            store_id=store_id,
            location_id=item_location_id,  # Multi-location support
            location_type=item_location_type,  # Multi-location support
            seller_id=current_user.id,
            payment_method='cash',
            payment_status='pending',  # Pending holatda
            notes=notes,
            currency_rate=current_rate,
            created_by=f'{current_user.first_name} {current_user.last_name} - Pending'
        )

        logger.info(f"âœ… Pending savdo yaratildi: location_id={new_sale.location_id}, location_type={new_sale.location_type}")

        # Agar asl savdo vaqti mavjud bo'lsa, uni o'rnatish
        if original_sale_date:
            new_sale.sale_date = original_sale_date
            logger.info(f"âœ… Asl savdo vaqti o'rnatildi: {original_sale_date}")

        db.session.add(new_sale)
        db.session.flush()  # ID ni olish uchun

        # Items ni qo'shish (stock'dan ayirmasdan)
        total_amount = Decimal('0')

        for item in items:
            product_id = item.get('product_id') or item.get('id')
            quantity = Decimal(str(item.get('quantity', 0)))
            unit_price = float(item.get('unit_price') or item.get('price', 0))

            if quantity <= 0:
                continue

            # Product tekshirish
            product = Product.query.get(product_id)
            if not product:
                db.session.rollback()
                return jsonify({
                    'success': False,
                    'error': f'Mahsulot topilmadi: {product_id}'
                }), 404

            # Cost price allaqachon USD da (products jadvalidagi qiymat)
            cost_price_usd = float(product.cost_price or Decimal('0'))
            if unit_price < cost_price_usd:
                db.session.rollback()
                return jsonify({
                    'success': False,
                    'error': f"{product.name} uchun narx tan narxdan past bo'lishi mumkin emas (min: {cost_price_usd}, kiritilgan: {unit_price})"
                }), 400

            # ESLATMA: Stock ayirish frontend'da korzinaga qo'shilganda amalga oshiriladi
            # Bu yerda faqat ma'lumot saqlash amalga oshiriladi
            item_location_id = item.get('location_id', store_id)
            item_location_type = item.get('location_type', 'store')

            print(
                f"ğŸ“¦ Pending savdo item yaratilmoqda: {product.name} - {quantity} ta (Stock oldindan rezerv qilingan)")

            # SaleItem yaratish - USD da
            unit_price_usd = Decimal(str(unit_price))
            total_price_usd = Decimal(str(quantity)) * unit_price_usd

            # Foyda USD da hisoblash
            profit_usd = total_price_usd - (Decimal(str(quantity)) * Decimal(str(cost_price_usd)))

            sale_item = SaleItem(
                sale_id=new_sale.id,
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price_usd,  # USD da
                total_price=total_price_usd,  # USD da
                cost_price=Decimal(str(cost_price_usd)),  # USD da
                profit=profit_usd,  # USD da
                source_type=item_location_type,
                source_id=item_location_id,
                notes=f"Pending: {product.name}"
            )

            db.session.add(sale_item)
            total_amount += total_price_usd  # USD da yig'ish

        # Savdo jami summalarini hisoblash (USD da)
        total_cost = Decimal('0')
        total_profit = Decimal('0')

        for sale_item in new_sale.items:
            total_cost += sale_item.cost_price * sale_item.quantity
            total_profit += sale_item.profit

        new_sale.total_amount = total_amount  # USD da
        new_sale.total_cost = total_cost  # USD da
        new_sale.total_profit = total_profit  # USD da

        db.session.commit()

        logger.info(f" Pending savdo yaratildi: ID={new_sale.id}")

        # OperationHistory ga pending savdoni yozish
        location_name = ''
        if item_location_type == 'store':
            store_obj = Store.query.get(item_location_id)
            if store_obj:
                location_name = store_obj.name
        elif item_location_type == 'warehouse':
            warehouse_obj = Warehouse.query.get(item_location_id)
            if warehouse_obj:
                location_name = warehouse_obj.name

        # Barcha mahsulotlarni tavsif uchun yig'ish
        products_desc = ', '.join([f"{item.product.name} ({item.quantity} ta)" for item in new_sale.items])

        operation = OperationHistory(
            operation_type='sale',
            table_name='sales',
            record_id=new_sale.id,
            user_id=current_user.id,
            username=f'{current_user.first_name} {current_user.last_name}',
            description=f"Savdo yaratildi (Pending): {products_desc}",
            old_data=None,
            new_data={
                'sale_id': new_sale.id,
                'total_amount_usd': float(total_amount),
                'payment_status': 'pending',
                'items_count': len(items)
            },
            ip_address=request.remote_addr,
            location_id=item_location_id,
            location_type=item_location_type,
            location_name=location_name,
            amount=float(Decimal(str(total_amount)) * new_sale.currency_rate)  # UZS da
        )
        db.session.add(operation)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Savdo keyinroq tasdiqlash uchun saqlandi',
            'sale_id': new_sale.id,
            'total_amount': total_amount
        }), 201

    except Exception as e:
        db.session.rollback()
        logger.error(f" Pending savdo yaratishda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Helper function - hozirgi valyuta kursini olish
def get_current_currency_rate():
    """Hozirgi aktiv valyuta kursini qaytaradi"""
    try:
        current_rate = CurrencyRate.query.filter_by(
            is_active=True).order_by(
            CurrencyRate.updated_date.desc()).first()
        if current_rate:
            return float(current_rate.rate)
        else:
            return 12500.0  # Default kursi
    except Exception:
        return 12500.0  # Xatolik bo'lsa default qaytarish


# Oxirgi operatsiyalarni saqlash (memory cache)
_last_operations = {}

# API endpoint - Real-time stock rezerv qilish (korzinaga qo'shilganda)
@app.route('/api/reserve-stock', methods=['POST'])
def api_reserve_stock():
    """Mahsulot korzinaga qo'shilganda real-time stock'dan ayirish"""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = Decimal(str(data.get('quantity', 1)))
        location_id = data.get('location_id')
        location_type = data.get('location_type')

        import traceback
        logger.debug(''.join(traceback.format_stack()[-5:-1]))
        print(f"\n{'=' * 80}")
        print("ğŸ“¦ RESERVE-STOCK API CHAQIRILDI:")
        print(f"   Product ID: {product_id}")
        print(f"   Quantity: {quantity}")
        print(f"   Location: {location_id} ({location_type})")
        print(f"   Timestamp: {get_tashkent_time()}")
        print(f"{'=' * 80}\n")

        # Duplicate operatsiyani oldini olish
        operation_key = f"reserve_{product_id}_{location_id}_{location_type}_{quantity}"
        current_time = get_tashkent_time()

        if operation_key in _last_operations:
            last_time = _last_operations[operation_key]
            time_diff = (current_time - last_time).total_seconds()
            if time_diff < 2:  # 2 sekund ichida bir xil operatsiya
                print(f"âš ï¸ DUPLICATE OPERATION BLOCKED: {time_diff:.2f} sekund oldin bajarilgan")
                return jsonify({'success': True, 'message': 'Duplicate operatsiya blocked', 'blocked': True}), 200

        _last_operations[operation_key] = current_time

        # Mahsulotni tekshirish
        product = Product.query.get(product_id)
        if not product:
            return jsonify(
                {'success': False, 'error': 'Mahsulot topilmadi'}), 400

        # Real-time stock ayirish
        if location_type == 'store':
            stock = StoreStock.query.filter_by(
                store_id=location_id,
                product_id=product_id
            ).first()

            if not stock:
                store_obj = Store.query.get(location_id)
                store_name = store_obj.name if store_obj else 'noma\'lum'
                return jsonify({
                    'success': False,
                    'error': f'{store_name} do\'konida {product.name} mahsuloti stock\'i yo\'q!'
                }), 400

            if stock.quantity < quantity:
                store_obj = Store.query.get(location_id)
                store_name = store_obj.name if store_obj else 'noma\'lum'
                return jsonify({'success': False, 'error': f'{store_name} do\'konida yetarli stock yo\'q! Mavjud: {stock.quantity}, Kerak: {quantity}'}), 400

            # Real-time stock'dan ayirish
            old_quantity = stock.quantity
            stock.quantity -= quantity
            remaining_stock = stock.quantity
            print(f"âœ… STORE STOCK O'ZGARDI: {old_quantity} - {quantity} = {remaining_stock}")
            print(f"   Product: {product.name} (ID: {product_id})")
            print(f"   Store ID: {location_id}")

        elif location_type == 'warehouse':
            stock = WarehouseStock.query.filter_by(
                warehouse_id=location_id,
                product_id=product_id
            ).first()

            if not stock:
                warehouse_obj = Warehouse.query.get(location_id)
                warehouse_name = warehouse_obj.name if warehouse_obj else 'noma\'lum'
                return jsonify({
                    'success': False,
                    'error': f'{warehouse_name} omborida {product.name} mahsuloti stock\'i yo\'q!'
                }), 400

            if stock.quantity < quantity:
                warehouse_obj = Warehouse.query.get(location_id)
                warehouse_name = warehouse_obj.name if warehouse_obj else 'noma\'lum'
                return jsonify({'success': False, 'error': f'{warehouse_name} omborida yetarli stock yo\'q! Mavjud: {stock.quantity}, Kerak: {quantity}'}), 400

            # Real-time stock'dan ayirish
            old_quantity = stock.quantity
            stock.quantity -= quantity
            remaining_stock = stock.quantity
            print(f"âœ… WAREHOUSE STOCK O'ZGARDI: {old_quantity} - {quantity} = {remaining_stock}")
            print(f"   Product: {product.name} (ID: {product_id})")
            print(f"   Warehouse ID: {location_id}")

        else:
            return jsonify(
                {'success': False, 'error': 'Noto\'g\'ri joylashuv turi'}), 400

        # O'zgarishlarni saqlash
        db.session.commit()
        print("ğŸ’¾ DB COMMIT: Stock o'zgarish saqlandi\n")

        return jsonify({
            'success': True,
            'message': f'{product.name} uchun {quantity} ta stock real-time rezerv qilindi',
            'remaining_stock': remaining_stock
        })

    except Exception as e:
        logger.error(f" Stock tekshirishda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API endpoint - Real-time stock qaytarish (korzinadan o'chirilganda)


@app.route('/api/return-stock', methods=['POST'])
def api_return_stock():
    """Mahsulot korzinadan o'chirilganda real-time stock'ga qaytarish"""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = Decimal(str(data.get('quantity', 1)))
        location_id = data.get('location_id')
        location_type = data.get('location_type')

        print(f"\n{'=' * 80}")
        print("â†©ï¸ RETURN-STOCK API CHAQIRILDI:")
        print(f"   Product ID: {product_id}")
        print(f"   Quantity: {quantity}")
        print(f"   Location: {location_id} ({location_type})")
        print(f"   Timestamp: {get_tashkent_time()}")
        print(f"{'=' * 80}\n")

        # Duplicate operatsiyani oldini olish
        operation_key = f"return_{product_id}_{location_id}_{location_type}_{quantity}"
        current_time = get_tashkent_time()

        if operation_key in _last_operations:
            last_time = _last_operations[operation_key]
            time_diff = (current_time - last_time).total_seconds()
            if time_diff < 2:  # 2 sekund ichida bir xil operatsiya
                print(f"âš ï¸ DUPLICATE OPERATION BLOCKED: {time_diff:.2f} sekund oldin bajarilgan")
                return jsonify({'success': True, 'message': 'Duplicate operatsiya blocked', 'blocked': True}), 200

        _last_operations[operation_key] = current_time

        # Mahsulotni tekshirish
        product = Product.query.get(product_id)
        if not product:
            return jsonify(
                {'success': False, 'error': 'Mahsulot topilmadi'}), 400

        # Real-time stock qaytarish
        if location_type == 'store':
            # Do'kon stock'ini qaytarish
            stock = StoreStock.query.filter_by(
                store_id=location_id,
                product_id=product_id
            ).first()

            if not stock:
                # Agar stock yo'q bo'lsa, yangi stock yaratish
                stock = StoreStock(
                    store_id=location_id,
                    product_id=product_id,
                    quantity=quantity
                )
                db.session.add(stock)
                print(f"âœ… YANGI STORE STOCK YARATILDI: {quantity}")
                print(f"   Product: {product.name} (ID: {product_id})")
                print(f"   Store ID: {location_id}")
            else:
                old_quantity = stock.quantity
                stock.quantity += quantity
                print(f"âœ… STORE STOCK O'ZGARDI: {old_quantity} + {quantity} = {stock.quantity}")
                print(f"   Product: {product.name} (ID: {product_id})")
                print(f"   Store ID: {location_id}")

            new_stock = stock.quantity

        elif location_type == 'warehouse':
            # Ombor stock'ini qaytarish
            stock = WarehouseStock.query.filter_by(
                warehouse_id=location_id,
                product_id=product_id
            ).first()

            if not stock:
                # Agar stock yo'q bo'lsa, yangi stock yaratish
                stock = WarehouseStock(
                    warehouse_id=location_id,
                    product_id=product_id,
                    quantity=quantity
                )
                db.session.add(stock)
                print(f"âœ… YANGI WAREHOUSE STOCK YARATILDI: {quantity}")
                print(f"   Product: {product.name} (ID: {product_id})")
                print(f"   Warehouse ID: {location_id}")
            else:
                old_quantity = stock.quantity
                stock.quantity += quantity
                print(f"âœ… WAREHOUSE STOCK O'ZGARDI: {old_quantity} + {quantity} = {stock.quantity}")
                print(f"   Product: {product.name} (ID: {product_id})")
                print(f"   Warehouse ID: {location_id}")

            new_stock = stock.quantity

        else:
            return jsonify(
                {'success': False, 'error': 'Noto\'g\'ri joylashuv turi'}), 400

        # O'zgarishlarni saqlash
        db.session.commit()
        print("ğŸ’¾ DB COMMIT: Stock qaytarish saqlandi\n")

        return jsonify({
            'success': True,
            'message': f'{product.name} uchun {quantity} ta stock real-time qaytarildi',
            'new_stock': new_stock
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f" Stock qaytarishda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API endpoint - Pending savdolar yaratish


@app.route('/api/pending-sales', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_create_pending_sale():
    """Avtomatik pending savdo yaratish API endpoint"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        print(
            f"ğŸ’¾ API pending-sales - User: {current_user.username}, Role: {current_user.role}")
        data = request.get_json()
        logger.debug(f" Avtomatik pending savdo ma'lumotlari: {data}")

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        if current_user.role == 'sotuvchi':
            store_id = data.get('store_id')
            if store_id:
                allowed_locations = current_user.allowed_locations or []
                if store_id not in allowed_locations:
                    return jsonify({
                        'success': False,
                        'error': 'Bu dokonda savdo qilish uchun ruxsatingiz yo\'q'
                    }), 403

        return create_pending_sale(data)

    except Exception as e:
        logger.error(f" API pending-sales xatoligi: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API endpoint - Pending savdoni yangilash


@app.route('/api/pending-sales/<int:sale_id>', methods=['PUT'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_update_pending_sale(sale_id):
    """Mavjud pending savdoni yangilash"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        print(
            f"ğŸ”„ Pending savdo yangilash - User: {current_user.username}, Sale ID: {sale_id}")
        data = request.get_json()
        logger.debug(f" Yangilanayotgan ma'lumotlar: {data}")

        # Mavjud savdoni topish
        existing_sale = Sale.query.get(sale_id)
        if not existing_sale:
            logger.error(f" Savdo topilmadi: {sale_id}")
            return jsonify({'success': False, 'error': 'Savdo topilmadi'}), 404

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []
            if existing_sale.store_id not in allowed_locations:
                return jsonify({
                    'success': False,
                    'error': 'Bu savdoni yangilash uchun ruxsatingiz yo\'q'
                }), 403

        print(
            f"âœ… Mavjud savdo topildi: {existing_sale.id}, Status: {existing_sale.payment_status}")

        # Eski sale items'ni o'chirish
        SaleItem.query.filter_by(sale_id=sale_id).delete()

        # Yangi ma'lumotlar bilan yangilash
        if data.get('customer_id'):
            existing_sale.customer_id = data['customer_id']

        existing_sale.total_amount = Decimal(str(data.get('total_amount', 0)))
        existing_sale.updated_date = get_tashkent_time()

        if data.get('notes'):
            existing_sale.notes = data['notes']

        # Yangi sale items qo'shish
        items = data.get('items', [])

        for item in items:
            product_id = item.get('id') or item.get('product_id')
            quantity = Decimal(str(item.get('quantity', 1)))
            unit_price = Decimal(str(item.get('price', 0)))

            product = Product.query.get(product_id)
            if not product:
                continue

            cost_price = Decimal(str(product.cost_price or Decimal('0')))
            if unit_price < cost_price:
                db.session.rollback()
                return jsonify({
                    'success': False,
                    'error': f"{product.name} uchun narx tan narxdan past bo'lishi mumkin emas (min: {cost_price}, kiritilgan: {unit_price})"
                }), 400

            total_price = Decimal(str(quantity * unit_price))
            profit = total_price - (Decimal(str(quantity)) * cost_price)

            sale_item = SaleItem(
                sale_id=sale_id,
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                cost_price=cost_price,
                profit=profit,
                source_id=item.get('location_id'),
                source_type=item.get('location_type', 'store')
            )
            db.session.add(sale_item)

        db.session.commit()
        logger.info(f" Pending savdo yangilandi: {sale_id}")

        return jsonify({
            'success': True,
            'message': 'Pending savdo yangilandi',
            'sale_id': sale_id
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f" Pending savdo yangilashda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# API endpoint - Pending savdoni o'chirish


@app.route('/api/pending-sales/<int:sale_id>', methods=['DELETE'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_delete_pending_sale(sale_id):
    """Pending savdoni o'chirish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        print(
            f"ğŸ—‘ï¸ Pending savdo o'chirish - User: {current_user.username}, Sale ID: {sale_id}")

        # Savdoni topish
        sale = Sale.query.get(sale_id)
        if not sale:
            return jsonify({'success': False, 'error': 'Savdo topilmadi'}), 404

        # Sotuvchi uchun joylashuv ruxsatini tekshirish
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []
            if sale.store_id not in allowed_locations:
                return jsonify({
                    'success': False,
                    'error': 'Bu savdoni o\'chirish uchun ruxsatingiz yo\'q'
                }), 403

        # Sale items'ni o'chirish
        SaleItem.query.filter_by(sale_id=sale_id).delete()

        # Savdoni o'chirish
        db.session.delete(sale)
        db.session.commit()

        logger.info(f" Pending savdo o'chirildi: {sale_id}")

        return jsonify({
            'success': True,
            'message': 'Pending savdo o\'chirildi'
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f" Pending savdo o'chirishda xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500

# Valyuta kursi API'lari


@app.route('/api/currency-rate', methods=['GET'])
def get_currency_rate():
    """Joriy valyuta kursini olish"""
    try:
        current_rate = CurrencyRate.query.filter_by(
            is_active=True).order_by(
            CurrencyRate.updated_date.desc()).first()

        if current_rate:
            return jsonify({
                'success': True,
                'rate': current_rate.to_dict()
            })
        else:
            # Default kursi qaytarish
            return jsonify({
                'success': True,
                'rate': {
                    'id': 0,
                    'from_currency': 'USD',
                    'to_currency': 'UZS',
                    'rate': 12500.0000,
                    'is_active': True,
                    'updated_by': 'system'
                }
            })
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ================== HEALTH CHECK API ==================
@app.route('/api/health-check', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def health_check():
    """Session heartbeat - sessionni faol ushlab turish"""
    try:
        return jsonify({
            'success': True,
            'status': 'active',
            'timestamp': get_tashkent_time().isoformat(),
            'user_id': session.get('user_id'),
            'username': session.get('username')
        }), 200
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        return jsonify({'error': 'Health check failed'}), 500


@app.route('/api/currency-rate', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def update_currency_rate():
    """Valyuta kursini yangilash"""
    try:
        data = request.get_json()

        if not data or 'rate' not in data:
            return jsonify({'error': 'Valyuta kursi talab qilinadi'}), 400

        new_rate = float(data['rate'])
        updated_by = data.get('updated_by', 'admin')

        if new_rate <= 0:
            return jsonify(
                {'error': 'Valyuta kursi musbat son bo\'lishi kerak'}), 400

        # Yangi kurs qo'shish
        currency_rate = CurrencyRate(
            from_currency='USD',
            to_currency='UZS',
            rate=new_rate,
            updated_by=updated_by,
            updated_date=get_tashkent_time()
        )

        # Eski kurslarni nofaol qilish
        CurrencyRate.query.filter_by(
            is_active=True).update({'is_active': False})

        db.session.add(currency_rate)
        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Valyuta kursi muvaffaqiyatli yangilandi',
            'rate': currency_rate.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/currency-rate/history', methods=['GET'])
def get_currency_rate_history():
    """Valyuta kursi tarixini olish"""
    try:
        rates = CurrencyRate.query.order_by(
            CurrencyRate.updated_date.desc()).limit(20).all()

        return jsonify({
            'success': True,
            'rates': [rate.to_dict() for rate in rates]
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/currency-rate/clear-history', methods=['DELETE'])
@role_required('admin')
def clear_currency_rate_history():
    """Valyuta kursi tarixini tozalash"""
    try:
        # Barcha yozuvlarni olish va sanash
        all_rates = CurrencyRate.query.all()
        deleted_count = len(all_rates)

        if deleted_count == 0:
            return jsonify({
                'success': False,
                'message': 'Tozalash uchun yozuvlar topilmadi'
            })

        # Barcha yozuvlarni o'chirish
        for rate in all_rates:
            db.session.delete(rate)

        db.session.commit()

        print(
            f"âœ… Valyuta kursi tarixi tozalandi: {deleted_count} ta yozuv o'chirildi")

        return jsonify({
            'success': True,
            'message': 'Barcha valyuta kursi tarixi tozalandi',
            'deleted_count': deleted_count
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f" Valyuta kursi tarixini tozalashda xatolik: {e}")
        return jsonify({
            'success': False,
            'error': str(e),
            'message': 'Tarixni tozalashda xatolik yuz berdi'
        }), 500


@app.route('/debug_api.html')
def debug_api():
    """Debug sahifasi"""
    try:
        with open('debug_api.html', 'r', encoding='utf-8') as f:
            return f.read()
    except BaseException:
        return render_template_string("""<!DOCTYPE html> <html>
<head>
    <title>API Debug</title>
</head>
<body>
    <h1>Currency API Debug</h1>
    <button onclick="testAPI()">Test Currency API</button>
    <pre id="result"></pre>

    <script>
        async function testAPI() {
            try {
                console.log('Testing API...');
                const response = await fetch('/api/currency-rate');
                const data = await response.json();

                document.getElementById('result').textContent = JSON.stringify(data, null, 2);
                console.log('API Response:', data);

                if (data.success && data.rate) {
                    console.log('Rate value:', data.rate.rate);
                    console.log('Rate type:', typeof data.rate.rate);
                }
            } catch (error) {
                console.error('Error:', error);
                document.getElementById('result').textContent = 'Error: ' + error.message;
            }
        }
    </script>
</body>
</html>""")


@app.route('/header_debug.html')
def header_debug():
    """Header debug sahifasi"""
    try:
        with open('header_debug.html', 'r', encoding='utf-8') as f:
            return f.read()
    except BaseException:
        return "Header debug file not found"


@app.route('/currency_test.html')
def currency_test():
    """Currency test sahifasi"""
    try:
        with open('currency_test.html', 'r', encoding='utf-8') as f:
            return f.read()
    except BaseException:
        return "Currency test file not found"


@app.route('/migrate')
def migrate_page():
    """Database migration page"""
    return render_template('migrate.html')


@app.route('/api/add-currency-column', methods=['POST'])
def add_currency_column():
    """Add currency_rate column to sales table"""
    try:
        # Rollback any pending transactions
        db.session.rollback()

        # Check if column exists using information_schema
        result = db.session.execute(text("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name='sales' AND column_name='currency_rate'
        """)).fetchone()

        if result:
            return jsonify({
                'success': True,
                'message': 'currency_rate column already exists'
            })

        # Add currency_rate column
        db.session.execute(text("""
            ALTER TABLE sales
            ADD COLUMN currency_rate DECIMAL(15,4) DEFAULT 12500.0000
        """))
        db.session.commit()

        # Get current active rate
        current_rate = get_current_currency_rate()

        # Update all existing sales with current rate
        result = db.session.execute(text("""
            UPDATE sales
            SET currency_rate = :rate
        """), {'rate': current_rate})
        db.session.commit()

        return jsonify({'success': True, 'message': f'currency_rate column added and {result.rowcount} sales updated with rate {current_rate}'})

    except Exception as e:
        db.session.rollback()
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stock-status')
def api_stock_status():
    """Barcha stock ma'lumotlarini qaytarish API"""
    try:
        print("ğŸ“¦ Stock status API so'rovi")

        # Store stocks
        store_stocks = db.session.query(
            StoreStock.product_id,
            StoreStock.store_id.label('location_id'),
            StoreStock.quantity,
            StoreStock.updated_date,
            Product.name.label('product_name'),
            Product.code.label('product_code'),
            Store.name.label('location_name')
        ).join(
            Product, StoreStock.product_id == Product.id
        ).join(
            Store, StoreStock.store_id == Store.id
        ).filter(Store.is_active).all()

        # Warehouse stocks
        warehouse_stocks = db.session.query(
            WarehouseStock.product_id,
            WarehouseStock.warehouse_id.label('location_id'),
            WarehouseStock.quantity,
            WarehouseStock.updated_date,
            Product.name.label('product_name'),
            Product.code.label('product_code'),
            Warehouse.name.label('location_name')
        ).join(
            Product, WarehouseStock.product_id == Product.id
        ).join(
            Warehouse, WarehouseStock.warehouse_id == Warehouse.id
        ).all()

        # Ma'lumotlarni birlashtirish
        stock_data = []

        # Store stocks ni qo'shish
        for stock in store_stocks:
            stock_data.append({
                'product_id': stock.product_id,
                'product_name': stock.product_name,
                'product_code': stock.product_code,
                'location_id': stock.location_id,
                'location_type': 'store',
                'location_name': stock.location_name,
                'quantity': stock.quantity,
                'unit': 'dona',
                'updated_date': stock.updated_date.isoformat() if stock.updated_date else None
            })

        # Warehouse stocks ni qo'shish
        for stock in warehouse_stocks:
            stock_data.append({
                'product_id': stock.product_id,
                'product_name': stock.product_name,
                'product_code': stock.product_code,
                'location_id': stock.location_id,
                'location_type': 'warehouse',
                'location_name': stock.location_name,
                'quantity': stock.quantity,
                'unit': 'dona',
                'updated_date': stock.updated_date.isoformat() if stock.updated_date else None
            })

        logger.info(f" Jami stock ma'lumotlari: {len(stock_data)} ta")

        return jsonify({
            'success': True,
            'data': stock_data,
            'total_count': len(stock_data)
        })

    except Exception as e:
        logger.error(f" Stock status API xatoligi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/unchecked-products-count', methods=['POST'])
def api_unchecked_products_count():
    """Tekshiruv sessiyasi uchun tekshirilmagan mahsulotlar soni (localStorage ma'lumotlari asosida)"""
    try:
        # POST request orqali localStorage ma'lumotlarini olish
        data = request.get_json()

        if not data or 'products' not in data:
            return jsonify({
                'success': False,
                'error': 'Ma\'lumotlar yetarli emas - products massivi kerak'
            }), 400

        products = data['products']
        session_id = data.get('session_id', 'unknown')

        logger.debug(f" DEBUG: API'ga kelgan ma'lumotlar - session_id: {session_id}")
        logger.debug(f" DEBUG: Products soni: {len(products)}")

        # Statistika hisoblash
        total_products = len(products)
        checked_products = 0
        unchecked_products = 0

        for product in products:
            is_checked = product.get('isChecked', False)
            print(f"ğŸ” DEBUG: Product {product.get('name', 'Unknown')}: isChecked = {is_checked}")

            if is_checked:
                checked_products += 1
            else:
                unchecked_products += 1

        checked_percentage = round(
            (checked_products / total_products * 100),
            1) if total_products > 0 else 0
        unchecked_percentage = round(
            (unchecked_products / total_products * 100),
            1) if total_products > 0 else 0

        logger.info(f" Sessiya {session_id} statistikasi:")
        print(f"   - Jami: {total_products}")
        print(f"   - Tekshirilgan: {checked_products} ({checked_percentage}%)")
        print(
            f"   - Tekshirilmagan: {unchecked_products} ({unchecked_percentage}%)")

        return jsonify({
            'success': True,
            'data': {
                'total_products': total_products,
                'checked_products': checked_products,
                'unchecked_products': unchecked_products,
                'checked_percentage': checked_percentage,
                'unchecked_percentage': unchecked_percentage
            }
        })

    except Exception as e:
        logger.error(f" Tekshirilmagan mahsulotlar soni API xatoligi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/stock-by-location')
def get_stock_by_location():
    """Joylashuv bo'yicha stock ma'lumotlarini olish"""
    try:
        location_type = request.args.get('type')  # 'store' yoki 'warehouse'
        location_id = request.args.get('location_id')
        show_zero = request.args.get('show_zero', 'true').lower(
        ) == 'true'  # 0 miqdorlilarni ko'rsatishmi

        print(
            f"ğŸ“ Stock ma'lumotlari: {location_type}, ID: {location_id}, Show zero: {show_zero}")

        if not location_type or not location_id:
            return jsonify({
                'success': False,
                'error': 'Location type va location_id majburiy'
            }), 400

        stock_data = []

        if location_type == 'store':
            # Do'kon uchun stock ma'lumotlari
            stocks = db.session.query(
                StoreStock,
                Product.name.label('product_name'),
                Product.sell_price.label('sell_price'),
                Store.name.label('store_name')
            ).join(
                Product, StoreStock.product_id == Product.id
            ).join(
                Store, StoreStock.store_id == Store.id
            ).filter(
                StoreStock.store_id == location_id,
                StoreStock.quantity > 0 if not show_zero else True
            ).all()

            for stock, product_name, sell_price, store_name in stocks:
                stock_data.append({
                    'product_id': stock.product_id,
                    'product_name': product_name,
                    'quantity': float(stock.quantity),
                    'sell_price': float(sell_price) if sell_price else 0,
                    'unit': 'dona',
                    'location_name': store_name,
                    'location_type': 'Do\'kon'
                })

        elif location_type == 'warehouse':
            # Ombor uchun stock ma'lumotlari
            stocks = db.session.query(
                WarehouseStock,
                Product.name.label('product_name'),
                Product.sell_price.label('sell_price'),
                Warehouse.name.label('warehouse_name')
            ).join(
                Product, WarehouseStock.product_id == Product.id
            ).join(
                Warehouse, WarehouseStock.warehouse_id == Warehouse.id
            ).filter(
                WarehouseStock.warehouse_id == location_id,
                WarehouseStock.quantity > 0 if not show_zero else True
            ).all()

            for stock, product_name, sell_price, warehouse_name in stocks:
                stock_data.append({
                    'product_id': stock.product_id,
                    'product_name': product_name,
                    'quantity': float(stock.quantity),
                    'sell_price': float(sell_price) if sell_price else 0,
                    'unit': 'dona',
                    'location_name': warehouse_name,
                    'location_type': 'Ombor'
                })

        logger.info(f" {location_type} uchun {len(stock_data)} ta mahsulot topildi")

        return jsonify({
            'success': True,
            'data': stock_data
        })

    except Exception as e:
        logger.error(f" Stock by location API xatoligi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


# ==================== LOGIN SAHIFASI ====================
@app.route('/login')
def login_page():
    message = request.args.get('message')
    error_message = None

    if message == 'account_disabled':
        error_message = 'Hisobingiz faol emas qilingan. Administrator bilan bog\'laning.'

    return render_template('login.html', error_message=error_message)


@app.route('/api/login', methods=['POST'])
def api_login():
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()

        if not username or not password:
            return jsonify({
                'success': False,
                'message': 'Login va parol talab qilinadi'
            }), 400

        # Foydalanuvchini topish
        user = User.query.filter_by(username=username).first()

        if not user:
            return jsonify({
                'success': False,
                'message': 'Login yoki parol noto\'g\'ri'
            }), 401

        # Faol emasligini tekshirish
        if not user.is_active:
            return jsonify({
                'success': False,
                'message': 'Hisobingiz faol emas. Administrator bilan bog\'laning'
            }), 401

        # Parolni tekshirish
        if not check_password(password, user.password):
            return jsonify({
                'success': False,
                'message': 'Login yoki parol noto\'g\'ri'
            }), 401

        # Session yaratish
        session['user_id'] = user.id
        session['username'] = user.username
        session['role'] = user.role
        session['user_name'] = f"{user.first_name} {user.last_name}"
        session['user_phone'] = user.phone or ''
        session['store_id'] = user.store_id
        # Session hijacking himoyasi
        session['user_agent'] = request.headers.get('User-Agent', '')[:500]
        session['login_ip'] = request.remote_addr

        # Session tracking - database'da session yaratish
        try:
            import uuid
            session_id = str(uuid.uuid4())

            # Eski session'larni deactivate qilish (MUHIM: Avval commit qilish)
            old_sessions = UserSession.query.filter_by(user_id=user.id, is_active=True).all()
            if old_sessions:
                for old_session in old_sessions:
                    # user_id ni saqlash va faqat is_active ni o'zgartirish
                    db.session.execute(
                        db.text("UPDATE user_sessions SET is_active = false WHERE id = :id"),
                        {"id": old_session.id}
                    )
                    app.logger.info(f"ğŸ”’ Eski session o'chirildi: User {user.username}, Session: {old_session.session_id[:8]}...")

                db.session.commit()  # Eski sessionlarni saqlash

            # Yangi session yaratish
            user_session = UserSession(
                user_id=user.id,
                session_id=session_id,
                ip_address=request.remote_addr,
                user_agent=request.headers.get('User-Agent', '')[:500],  # Truncate if too long
                is_active=True
            )

            db.session.add(user_session)
            db.session.commit()  # Yangi sessionni saqlash

            # Session'ga session_id qo'shish
            session['session_id'] = session_id

            app.logger.info(f"ğŸ” Yangi session yaratildi: User {user.username} (ID: {user.id}), Session: {session_id[:8]}...")

        except Exception as e:
            db.session.rollback()
            app.logger.error(f"Session tracking xatosi: {e}")
            # Session tracking xato bo'lsa ham login'ga ruxsat berish
            pass

        # Session'ni har doim permanent qilish (8 soat)
        session.permanent = True

        # Muvaffaqiyatli javob
        redirect_url = '/dashboard'  # Barcha foydalanuvchilar bosh sahifaga

        return jsonify({
            'success': True,
            'message': 'Muvaffaqiyatli kirildi',
            'redirect': redirect_url,
            'user': {
                'id': user.id,
                'username': user.username,
                'name': f"{user.first_name} {user.last_name}",
                'role': user.role
            }
        })

    except Exception as e:
        logger.error(f"Login xatoligi: {str(e)}")
        import traceback
        logger.error(traceback.format_exc())
        # Foydalanuvchiga faqat umumiy xabar
        return jsonify({
            'success': False,
            'message': 'Server xatoligi yuz berdi. Iltimos qayta urinib ko\'ring.'
        }), 500


@app.route('/logout')
def logout():
    try:
        # Session'ni database'da deactivate qilish
        user_id = session.get('user_id')
        session_id = session.get('session_id')

        if user_id and session_id:
            user_session = UserSession.query.filter_by(
                user_id=user_id,
                session_id=session_id,
                is_active=True
            ).first()

            if user_session:
                user_session.is_active = False
                db.session.commit()
                app.logger.info(f"ğŸšª Session deactivated: User {user_id}, Session {session_id[:8]}...")

    except Exception as e:
        app.logger.error(f"Logout session deactivation xatosi: {e}")

    session.clear()
    return redirect('/login')

# Dashboard API endpoints


@app.route('/api/sales-statistics')
def api_sales_statistics():
    """Savdo statistikasini qaytarish"""
    try:
        location_id = request.args.get('location_id')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        # Base query
        query = """
            SELECT
                COUNT(*) as total_sales,
                COALESCE(SUM(si.price * si.quantity), 0) as total_revenue,
                COALESCE(AVG(si.price * si.quantity), 0) as avg_sale
            FROM sale_items si
            JOIN sales s ON si.sale_id = s.id
        """

        conditions = []
        params = []

        if location_id:
            conditions.append("s.location_id = %s")
            params.append(location_id)

        if date_from:
            conditions.append("s.sale_date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("s.sale_date <= %s")
            params.append(date_to)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        # Database query bilan statistics olish
        result = db.session.execute(text(query), params)
        stats = result.fetchone()

        # En faol joylashuvni topish
        top_location_query = """
            SELECT
                CASE
                    WHEN s.location_type = 'store' THEN st.name
                    WHEN s.location_type = 'warehouse' THEN w.name
                END as location_name,
                COUNT(*) as sales_count
            FROM sales s
            LEFT JOIN stores st ON s.location_id = st.id AND s.location_type = 'store'
            LEFT JOIN warehouses w ON s.location_id = w.id AND s.location_type = 'warehouse'
        """

        top_conditions = []
        top_params = []

        if date_from:
            top_conditions.append("s.sale_date >= %s")
            top_params.append(date_from)

        if date_to:
            top_conditions.append("s.sale_date <= %s")
            top_params.append(date_to)

        if top_conditions:
            top_location_query += " WHERE " + " AND ".join(top_conditions)

        top_location_query += " GROUP BY location_name ORDER BY sales_count DESC LIMIT 1"

        result = db.session.execute(text(top_location_query), top_params)
        top_location = result.fetchone()

        return jsonify({
            'total_sales': stats[0] if stats else 0,
            'total_revenue': float(stats[1]) if stats and stats[1] else 0,
            'avg_sale': float(stats[2]) if stats and stats[2] else 0,
            'top_location': top_location[0] if top_location else '-'
        })

    except Exception as e:
        print(f"Statistika API xatoligi: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/sales-chart')
def api_sales_chart():
    """Savdo grafigi ma'lumotlarini qaytarish"""
    try:
        location_id = request.args.get('location_id')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        period = request.args.get('period', 'week')  # default: bu hafta

        # Debug uchun parametrlarni chop etamiz
        print(
            f"ğŸ” API parametrlari: location_id={location_id}, period={period}, date_from={date_from}, date_to={date_to}")

        from datetime import datetime, timedelta

        # Vaqt filtri bo'yicha date_from va date_to ni belgilaymiz
        if not date_from:
            today = get_tashkent_time().date()

            if period == 'today':
                date_from = today.strftime('%Y-%m-%d')
                date_to = today.strftime('%Y-%m-%d')
            elif period == 'week':
                # Bu haftaning boshidan
                days_since_monday = today.weekday()
                monday = today - timedelta(days=days_since_monday)
                date_from = monday.strftime('%Y-%m-%d')
                date_to = today.strftime('%Y-%m-%d')
            elif period == 'month':
                # Bu oyning boshidan
                first_day_of_month = today.replace(day=1)
                date_from = first_day_of_month.strftime('%Y-%m-%d')
                date_to = today.strftime('%Y-%m-%d')
            else:
                # Default: 7 kunlik
                date_from = (today - timedelta(days=7)).strftime('%Y-%m-%d')

        # Hisoblangan sanalarni chop etamiz
        print(
            f"ğŸ“… Hisoblangan sanalar: date_from={date_from}, date_to={date_to}")

        # Bugun filtri uchun soat bo'yicha, boshqalar uchun kun bo'yicha
        if period == 'today':
            query = """
                SELECT
                    EXTRACT(HOUR FROM s.sale_date) as time_period,
                    COUNT(*) as period_sales,
                    COALESCE(SUM(s.total_amount), 0) as period_total,
                    COALESCE(SUM(s.total_profit), 0) as period_profit,
                    COALESCE(SUM(s.cash_usd), 0) as cash_total,
                    COALESCE(SUM(s.click_usd), 0) as click_total,
                    COALESCE(SUM(s.terminal_usd), 0) as terminal_total,
                    COALESCE(SUM(s.debt_usd), 0) as debt_total
                FROM sales s
                WHERE (s.cash_usd > 0 OR s.click_usd > 0 OR s.terminal_usd > 0 OR s.debt_usd > 0)
            """
        else:
            query = """
                SELECT
                    DATE(s.sale_date) as time_period,
                    COUNT(*) as period_sales,
                    COALESCE(SUM(s.total_amount), 0) as period_total,
                    COALESCE(SUM(s.total_profit), 0) as period_profit,
                    COALESCE(SUM(s.cash_usd), 0) as cash_total,
                    COALESCE(SUM(s.click_usd), 0) as click_total,
                    COALESCE(SUM(s.terminal_usd), 0) as terminal_total,
                    COALESCE(SUM(s.debt_usd), 0) as debt_total
                FROM sales s
                WHERE (s.cash_usd > 0 OR s.click_usd > 0 OR s.terminal_usd > 0 OR s.debt_usd > 0)
            """

        conditions = []
        params = {}

        # Joylashuv filtri - location_id va location_type ishlatish
        location_type = request.args.get('location_type')
        if location_id:
            if location_type == 'store':
                conditions.append("(s.location_id = :location_id AND s.location_type = 'store')")
                params['location_id'] = int(location_id)
                print(f"ğŸª Store filtri: location_id={location_id}, location_type=store")
            elif location_type == 'warehouse':
                # Warehouse'dan savdo bo'lishi mumkin (yangi tizimda)
                conditions.append("(s.location_id = :location_id AND s.location_type = 'warehouse')")
                params['location_id'] = int(location_id)
                print(f"ğŸ­ Warehouse filtri: location_id={location_id}, location_type=warehouse")
            else:
                # Type berilmagan, location_id bo'yicha
                conditions.append("s.location_id = :location_id")
                params['location_id'] = int(location_id)
                print(f"ğŸ¢ Umumiy filtri: location_id={location_id}")

        if date_from:
            conditions.append("DATE(s.sale_date) >= :date_from")
            params['date_from'] = date_from

        if date_to:
            conditions.append("DATE(s.sale_date) <= :date_to")
            params['date_to'] = date_to

        if conditions:
            query += " AND " + " AND ".join(conditions)

        # GROUP BY va ORDER BY
        if period == 'today':
            query += " GROUP BY EXTRACT(HOUR FROM s.sale_date) ORDER BY time_period"
        else:
            query += " GROUP BY DATE(s.sale_date) ORDER BY time_period"

        # SQLAlchemy ishlatamiz
        print(f"ğŸ” SQL Query: {query}")
        print(f"ğŸ” Params: {params}")
        results = db.session.execute(text(query), params).fetchall()
        print(f"ğŸ“Š Results count: {len(results)}")
        for row in results:
            print(f"  Row: {row}")

        labels = []
        values = []
        amounts = []
        profits = []
        debts = []  # Qarzlar ro'yxati
        cash_list = []  # Naqd pul
        click_list = []  # Click
        terminal_list = []  # Terminal

        if period == 'today':
            # Bugun filtri uchun 24 soatli ma'lumot yaratish
            # Avval ma'lumotlarni dictionary ga yig'amiz
            hourly_data = {}
            for row in results:
                hour = int(row[0]) if row[0] is not None else 0
                hourly_data[hour] = {
                    'sales': row[1],
                    'amount': float(row[2]) if row[2] else 0.0,
                    'profit': float(row[3]) if row[3] else 0.0,
                    'cash': float(row[4]) if row[4] else 0.0,
                    'click': float(row[5]) if row[5] else 0.0,
                    'terminal': float(row[6]) if row[6] else 0.0,
                    'debt': float(row[7]) if row[7] else 0.0
                }

            # 0 dan 23 gacha barcha soatlarni qo'shamiz
            for hour in range(24):
                labels.append(f"{hour:02d}:00")
                if hour in hourly_data:
                    values.append(hourly_data[hour]['sales'])
                    amounts.append(hourly_data[hour]['amount'])
                    profits.append(hourly_data[hour]['profit'])
                    debts.append(hourly_data[hour]['debt'])
                    cash_list.append(hourly_data[hour]['cash'])
                    click_list.append(hourly_data[hour]['click'])
                    terminal_list.append(hourly_data[hour]['terminal'])
                else:
                    values.append(0)
                    amounts.append(0.0)
                    profits.append(0.0)
                    debts.append(0.0)
                    cash_list.append(0.0)
                    click_list.append(0.0)
                    terminal_list.append(0.0)
        else:
            # Hafta/oy filtri uchun kunlik ma'lumot
            for row in results:
                labels.append(row[0].strftime('%m-%d') if row[0] else '')
                values.append(row[1])  # savdo soni
                amounts.append(float(row[2]) if row[2]
                               else 0.0)  # savdo summasi
                profits.append(float(row[3]) if row[3]
                               else 0.0)  # savdo foydasi
                cash_list.append(float(row[4]) if len(row) > 4 and row[4]
                                 else 0.0)  # naqd pul
                click_list.append(float(row[5]) if len(row) > 5 and row[5]
                                  else 0.0)  # click
                terminal_list.append(float(row[6]) if len(row) > 6 and row[6]
                                     else 0.0)  # terminal
                debts.append(float(row[7]) if len(row) > 7 and row[7]
                             else 0.0)  # qarz summasi

        # To'lov turlarini hisoblash
        payment_totals = {
            'cash': sum(float(row[4]) if len(row) > 4 and row[4] else 0.0 for row in results),
            'click': sum(float(row[5]) if len(row) > 5 and row[5] else 0.0 for row in results),
            'terminal': sum(float(row[6]) if len(row) > 6 and row[6] else 0.0 for row in results),
            'debt': sum(float(row[7]) if len(row) > 7 and row[7] else 0.0 for row in results)
        }

        return jsonify({
            'labels': labels,
            'values': values,
            'amounts': amounts,
            'profits': profits,
            'debts': debts,
            'cash_list': cash_list,
            'click_list': click_list,
            'terminal_list': terminal_list,
            'payment_totals': payment_totals
        })

    except Exception as e:
        import traceback
        logger.error(f" Savdo grafigi API xatoligi: {e}")
        logger.debug(f" Traceback: {traceback.format_exc()}")
        return jsonify(
            {'error': str(e), 'traceback': traceback.format_exc()}), 500


@app.route('/api/location-chart')
def api_location_chart():
    """Joylashuv grafigi ma'lumotlarini qaytarish"""
    try:
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')

        query = """
            SELECT
                CASE
                    WHEN s.location_type = 'store' THEN st.name
                    WHEN s.location_type = 'warehouse' THEN w.name
                END as location_name,
                COUNT(*) as sales_count
            FROM sales s
            LEFT JOIN stores st ON s.location_id = st.id AND s.location_type = 'store'
            LEFT JOIN warehouses w ON s.location_id = w.id AND s.location_type = 'warehouse'
        """

        conditions = []
        params = []

        if date_from:
            conditions.append("s.sale_date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("s.sale_date <= %s")
            params.append(date_to)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " GROUP BY location_name ORDER BY sales_count DESC"

        result = db.session.execute(text(query), params)
        results = result.fetchall()

        labels = []
        values = []

        for row in results:
            if row[0]:  # location_name mavjud bo'lsa
                labels.append(row[0])
                values.append(row[1])

        return jsonify({
            'labels': labels,
            'values': values
        })

    except Exception as e:
        print(f"Joylashuv grafigi API xatoligi: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/recent-sales')
def api_recent_sales():
    """So'nggi savdolar ro'yxatini qaytarish"""
    try:
        location_id = request.args.get('location_id')
        date_from = request.args.get('date_from')
        date_to = request.args.get('date_to')
        limit = request.args.get('limit', 10)

        query = """
            SELECT
                s.id,
                s.sale_date,
                CASE
                    WHEN s.location_type = 'store' THEN st.name
                    WHEN s.location_type = 'warehouse' THEN w.name
                END as location_name,
                p.name as product_name,
                si.quantity,
                (si.price * si.quantity) as total_amount
            FROM sales s
            JOIN sale_items si ON s.id = si.sale_id
            JOIN products p ON si.product_id = p.id
            LEFT JOIN stores st ON s.location_id = st.id AND s.location_type = 'store'
            LEFT JOIN warehouses w ON s.location_id = w.id AND s.location_type = 'warehouse'
        """

        conditions = []
        params = []

        if location_id:
            conditions.append("s.location_id = %s")
            params.append(location_id)

        if date_from:
            conditions.append("s.sale_date >= %s")
            params.append(date_from)

        if date_to:
            conditions.append("s.sale_date <= %s")
            params.append(date_to)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY s.sale_date DESC, s.id DESC LIMIT %s"
        params.append(limit)

        result = db.session.execute(text(query), params)
        results = result.fetchall()

        sales = []
        for row in results:
            sales.append({
                'id': row[0],
                'sale_date': row[1].isoformat() if row[1] else '',
                'location_name': row[2] or 'Noma\'lum',
                'product_name': row[3] or 'Noma\'lum',
                'quantity': row[4],
                'total_amount': float(row[5]) if row[5] else 0
            })

        return jsonify(sales)

    except Exception as e:
        print(f"So'nggi savdolar API xatoligi: {e}")
        return jsonify({'error': str(e)}), 500

# Settings API endpointlari


@app.route('/api/settings', methods=['GET'])
def get_settings():
    """Tizim sozlamalarini olish"""
    try:
        # Standart sozlamalar
        default_settings = {
            'stock_check_visible': True,  # Sotuvchi uchun qoldiq tekshirish sahifasi ko'rinadimi
            'auto_currency_update': False,
            'auto_backup': False
        }

        # Bazadan sozlamalarni olish
        settings_data = {}
        settings_list = Settings.query.all()

        for setting in settings_list:
            if setting.value.lower() in ['true', 'false']:
                settings_data[setting.key] = setting.value.lower() == 'true'
            else:
                settings_data[setting.key] = setting.value

        # Standart sozlamalar bilan birlashtirish
        result = {**default_settings, **settings_data}

        return jsonify(result)

    except Exception as e:
        print(f"Sozlamalarni olishda xato: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings', methods=['POST'])
def save_settings():
    """Tizim sozlamalarini saqlash"""
    try:
        # Admin yoki manager rolini tekshirish
        if 'user_id' not in session:
            return jsonify({'error': 'Avtorizatsiya talab qilinadi'}), 401

        user = User.query.get(session['user_id'])
        if not user or user.role not in ['admin', 'manager']:
            return jsonify({'error': 'Ruxsat yo\'q'}), 403

        data = request.get_json()

        # Har bir sozlamani saqlash
        for key, value in data.items():
            setting = Settings.query.filter_by(key=key).first()

            if setting:
                # Mavjud sozlamani yangilash
                setting.value = str(value)
                setting.updated_at = get_tashkent_time()
            else:
                # Yangi sozlama yaratish
                setting = Settings(
                    key=key,
                    value=str(value),
                    description=f'Sozlama: {key}'
                )
                db.session.add(setting)

        db.session.commit()
        return jsonify({'message': 'Sozlamalar muvaffaqiyatli saqlandi'})

    except Exception as e:
        db.session.rollback()
        print(f"Sozlamalarni saqlashda xato: {e}")
        return jsonify({'error': str(e)}), 500

# Sozlamalar sahifasi


@app.route('/settings')
def settings_page():
    """Sozlamalar sahifasi"""
    if 'user_id' not in session:
        return redirect('/login')

    user = User.query.get(session['user_id'])
    if not user or user.role not in ['admin', 'manager']:
        abort(403)  # Faqat admin va manager kirishi mumkin

    return render_template('settings.html')


@app.route('/dashboard')
def dashboard():
    # Session tekshirish
    if 'user_id' not in session:
        return redirect('/login')

    # Current user ma'lumotlarini olish
    current_user = get_current_user()

    return render_template('dashboard.html', current_user=current_user)


# =======================================================
# STOCK CHECK SESSION API ENDPOINTS
# =======================================================

@app.route('/api/stock-check-session/save', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def save_stock_check_session():
    """Stock checking session holatini saqlash (deprecated - heartbeat ishlatiladi)"""
    try:
        user_id = session.get('user_id')
        print(f"ğŸ” SESSION SAVE - User ID: {user_id}")

        if not user_id:
            logger.error(" User not authenticated")
            return jsonify({'error': 'User not authenticated'}), 401

        data = request.get_json()
        location_type = data.get('location_type')
        location_id = data.get('location_id')

        print("ğŸ“¥ Kelgan ma'lumotlar:")
        print(f"  - Location type: {location_type}")
        print(f"  - Location ID: {location_id}")

        # Permission validation
        user = User.query.get(user_id)
        if not user:
            logger.error(" User not found")
            return jsonify({'error': 'User not found'}), 404

        # Mavjud active session ni topish
        existing_session = StockCheckSession.query.filter_by(
            user_id=user_id,
            status='active'
        ).first()

        if existing_session:
            logger.info(f" Mavjud session yangilanmoqda: {existing_session.id}")
            # Mavjud session ni yangilash (heartbeat vazifasini bajaradi)
            existing_session.updated_at = db.func.current_timestamp()
            db.session.commit()

            return jsonify({
                'success': True,
                'message': 'Session yangilandi (heartbeat)'
            })
        else:
            # Active session yo'q - bu route deprecated, /api/start-stock-check ishlatilishi kerak
            print("âš ï¸ Active session topilmadi - /api/start-stock-check ishlatilishi kerak")
            return jsonify({
                'success': False,
                'message': 'Active session topilmadi. Avval tekshiruvni boshlang.'
            }), 404

    except Exception as e:
        db.session.rollback()
        logger.error(f" Session saqlashda xato: {e}")
        logger.debug(f" Error type: {type(e).__name__}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-check-session/load', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi')
def load_stock_check_session():
    """Stock checking session holatini yuklash"""
    try:
        user_id = session.get('user_id')
        logger.debug(f" SESSION LOAD - User ID: {user_id}")

        if not user_id:
            logger.error(" User not authenticated")
            return jsonify({'error': 'User not authenticated'}), 401

        # Permission validation
        user = User.query.get(user_id)
        if not user:
            logger.error(" User not found")
            return jsonify({'error': 'User not found'}), 404

        # Stock check huquqini tekshirish (admin uchun avtomatik ruxsat)
        if user.role != 'admin':
            permissions = user.permissions or {}
            if not permissions.get('stock_check', False):
                logger.error(f" User {user.username} has no stock check permission")
                return jsonify({
                    'error': 'Qoldiqni tekshirish huquqingiz yo\'q',
                    'required_permission': 'stock_check'
                }), 403
            logger.info(f" Stock check permission verified for user: {user.username}")
        else:
            print("âœ… Admin user - stock check permission granted")

        # Active session'ni topish
        active_session = StockCheckSession.query.filter_by(
            user_id=user_id,
            status='active'
        ).order_by(StockCheckSession.updated_at.desc()).first()

        if not active_session:
            print("â„¹ï¸ Active session topilmadi")
            return jsonify({
                'success': False,
                'message': 'Active session topilmadi'
            })

        logger.info(f" Active session topildi: {active_session.location_type}-{active_session.location_id}")
        print(f"ğŸ“ Location: {active_session.location_type}-{active_session.location_id}")
        print(f"ğŸ• Updated at: {active_session.updated_at}")

        # Session ma'lumotlarini qaytarish
        result = {
            'success': True,
            'location_type': active_session.location_type,
            'location_id': active_session.location_id,
            'location_name': active_session.location_name,
            'session_data': {},  # Eski session_data field'i hozir ishlatilmaydi
            'updated_at': active_session.updated_at.isoformat()
        }

        print("ğŸ“¤ Session ma'lumotlari qaytarilmoqda")
        return jsonify(result)

    except Exception as e:
        logger.error(f" Session yuklashda xato: {e}")
        logger.debug(f" Error type: {type(e).__name__}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/stock-check-session/clear', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def clear_stock_check_session():
    """Stock checking session holatini tozalash"""
    try:
        user_id = session.get('user_id')
        if not user_id:
            return jsonify({'error': 'User not authenticated'}), 401

        # User ning barcha active session'larini cancelled qilish
        StockCheckSession.query.filter_by(
            user_id=user_id,
            status='active'
        ).update({'status': 'cancelled'})

        db.session.commit()

        return jsonify({
            'success': True,
            'message': 'Session tozalandi'
        })

    except Exception as e:
        db.session.rollback()
        print(f"Session tozalashda xato: {e}")
        return jsonify({'error': str(e)}), 500


# Context processor - barcha templatelarga global o'zgaruvchilarni uzatish
@app.context_processor
def inject_settings():
    """Barcha templatelarga sozlamalarni uzatish"""
    try:
        # stock_check_visible sozlamasini olish
        setting = Settings.query.filter_by(key='stock_check_visible').first()
        stock_check_visible = (setting.value.lower() == 'true'
                               if setting else True)

        return {
            'stock_check_visible': stock_check_visible,
            'config': app.config
        }
    except Exception:
        # Xato bo'lsa, standart qiymat
        return {
            'stock_check_visible': True,
            'config': app.config
        }


# Session Cleanup - memory leak va connection exhaustion muammosini hal qilish
@app.teardown_appcontext
def shutdown_session(exception=None):
    """Har bir request'dan keyin session cleanup - connection pool'ga qaytarish"""
    try:
        if exception:
            # Agar xato bo'lgan bo'lsa, rollback qilish
            db.session.rollback()
            app.logger.warning(f"Request exception, session rollback: {exception}")
        # Session'ni tozalash va connection'ni pool'ga qaytarish
        db.session.remove()
    except Exception as e:
        # Cleanup jarayonida xato bo'lsa, log qilish lekin crash qilmaslik
        app.logger.error(f"Session cleanup error: {e}")

# =====================================================================
# SMS API ENDPOINTS - DISABLED (SMS Eskiz moduli o'chirilgan)
# =====================================================================

@app.route('/api/sms/send-debt-reminder', methods=['POST'])
@role_required('admin', 'kassir')
def api_send_debt_sms():
    """Qarzli mijozga Telegram eslatmasi yuborish"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')

        if not customer_id:
            return jsonify({'success': False, 'error': 'Mijoz ID kiritilmagan'}), 400

        # Mijoz ma'lumotlarini olish
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        if not customer.telegram_chat_id:
            return jsonify({'success': False, 'error': 'Mijozda Telegram ID yo\'q. Mijoz botga /start yuborishi kerak'}), 400

        # Qarz miqdorini va joylashuvni hisoblash
        sale_with_location = db.session.query(
            db.func.sum(Sale.debt_usd).label('total_debt'),
            db.func.sum(Sale.debt_amount).label('total_debt_uzs'),
            Sale.location_id,
            Sale.location_type
        ).filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0
        ).group_by(Sale.location_id, Sale.location_type).first()

        if not sale_with_location or not sale_with_location.total_debt:
            return jsonify({'success': False, 'error': 'Mijozda qarz yo\'q'}), 400

        debt_usd = float(sale_with_location.total_debt)
        debt_uzs = float(sale_with_location.total_debt_uzs or 0)

        # Agar debt_uzs 0 yoki juda kichik bo'lsa (USD saqlanib qolgan), kursga ko'paytiramiz
        if debt_uzs == 0 or debt_uzs < debt_usd * 100:  # UZS kamida 100 barobar katta bo'lishi kerak
            rate = get_current_currency_rate()
            debt_uzs = debt_usd * rate

        # Joylashuv nomini olish
        location_name = "Do'kon"
        if sale_with_location.location_type == 'store':
            store = Store.query.get(sale_with_location.location_id)
            location_name = store.name if store else "Do'kon"
        elif sale_with_location.location_type == 'warehouse':
            warehouse = Warehouse.query.get(sale_with_location.location_id)
            location_name = warehouse.name if warehouse else "Ombor"

        # Telegram orqali yuborish
        try:
            from debt_scheduler import get_scheduler_instance

            scheduler = get_scheduler_instance(app, db)

            # Sync funksiyadan foydalanish (Flask uchun)
            telegram_result = scheduler.bot.send_debt_reminder_sync(
                chat_id=customer.telegram_chat_id,
                customer_name=customer.name,
                debt_usd=debt_usd,
                debt_uzs=debt_uzs,
                location_name=location_name,
                customer_id=customer_id  # Customer ID qo'shamiz
            )

            if telegram_result:
                logger.info(f"âœ… Telegram qarz eslatmasi yuborildi: {customer.name} (Chat ID: {customer.telegram_chat_id})")
                return jsonify({
                    'success': True,
                    'message': f'Telegram orqali qarz eslatmasi yuborildi: {customer.name}',
                    'telegram_sent': True
                })
            else:
                logger.error(f"âŒ Telegram xabar yuborilmadi: {customer.name}")
                return jsonify({'success': False, 'error': 'Telegram xabar yuborilmadi'}), 500

        except Exception as e:
            logger.error(f"âŒ Telegram xabar yuborishda xatolik: {e}")
            return jsonify({'success': False, 'error': f'Telegram xatolik: {str(e)}'}), 500

    except Exception as e:
        logger.error(f"Xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sms/send-payment-confirmation', methods=['POST'])
@role_required('admin', 'kassir')
def api_send_payment_sms():
    """To'lov tasdiqlash Telegram yuborish"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        paid_amount_usd = float(data.get('paid_amount_usd', 0))

        if not customer_id or paid_amount_usd <= 0:
            return jsonify({'success': False, 'error': 'Noto\'g\'ri ma\'lumotlar'}), 400

        # Mijoz ma'lumotlari
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        if not customer.telegram_chat_id:
            return jsonify({'success': False, 'error': 'Mijozda Telegram ID yo\'q'}), 400

        # Qolgan qarzni hisoblash
        remaining_debt_usd = db.session.query(
            db.func.sum(Sale.debt_usd)
        ).filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0
        ).scalar() or 0

        remaining_debt_uzs = db.session.query(
            db.func.sum(Sale.debt_amount)
        ).filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0
        ).scalar() or 0

        # Kurs
        rate = CurrencyRate.query.order_by(CurrencyRate.id.desc()).first()
        exchange_rate = float(rate.rate) if rate else 13000

        paid_amount_uzs = paid_amount_usd * exchange_rate

        # Telegram orqali yuborish
        try:
            import asyncio
            from debt_scheduler import get_scheduler_instance

            # Location nomini olish
            sale_with_location = db.session.query(
                Sale.location_id,
                Sale.location_type
            ).filter(
                Sale.customer_id == customer_id,
                Sale.debt_usd > 0
            ).first()

            location_name = "Do'kon"
            if sale_with_location:
                if sale_with_location.location_type == 'store':
                    store = Store.query.get(sale_with_location.location_id)
                    location_name = store.name if store else "Do'kon"
                elif sale_with_location.location_type == 'warehouse':
                    warehouse = Warehouse.query.get(sale_with_location.location_id)
                    location_name = warehouse.name if warehouse else "Ombor"

            scheduler = get_scheduler_instance(app, db)

            telegram_result = asyncio.run(
                scheduler.bot.send_payment_confirmation(
                    chat_id=customer.telegram_chat_id,
                    customer_name=customer.name,
                    paid_usd=paid_amount_usd,
                    paid_uzs=paid_amount_uzs,
                    remaining_usd=float(remaining_debt_usd),
                    remaining_uzs=float(remaining_debt_uzs),
                    location_name=location_name,
                    customer_id=customer_id  # Customer ID qo'shamiz
                )
            )

            if telegram_result:
                logger.info(f"âœ… To'lov Telegram xabari yuborildi: {customer.name}")
                return jsonify({
                    'success': True,
                    'message': f'Telegram orqali to\'lov tasdiq xabari yuborildi: {customer.name}',
                    'telegram_sent': True
                })
            else:
                return jsonify({'success': False, 'error': 'Telegram xabar yuborilmadi'}), 500

        except Exception as e:
            logger.error(f"âš ï¸ Telegram to'lov xabari yuborishda xatolik: {e}")
            return jsonify({'success': False, 'error': f'Telegram xatolik: {str(e)}'}), 500

    except Exception as e:
        logger.error(f"To'lov xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# === Telegram Bulk Reminders ===

@app.route('/api/telegram/send-bulk-reminders', methods=['POST'])
@role_required('admin')
def api_send_bulk_telegram():
    """Barcha qarzli mijozlarga Telegram yuborish"""
    try:
        data = request.get_json()
        min_debt = float(data.get('min_debt', 10))  # Minimal qarz (USD)

        # Kurs
        rate = CurrencyRate.query.order_by(CurrencyRate.id.desc()).first()
        exchange_rate = float(rate.rate) if rate else 13000

        # Qarzli mijozlarni olish (faqat Telegram ID bor mijozlar)
        query = text("""
            SELECT
                c.id, c.name, c.phone, c.telegram_chat_id,
                COALESCE(SUM(s.debt_usd), 0) as total_debt_usd,
                COALESCE(SUM(s.debt_amount), 0) as total_debt_uzs
            FROM customers c
            LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
            WHERE c.telegram_chat_id IS NOT NULL
            GROUP BY c.id, c.name, c.phone, c.telegram_chat_id
            HAVING COALESCE(SUM(s.debt_usd), 0) >= :min_debt
            ORDER BY total_debt_usd DESC
        """)

        results = db.session.execute(query, {'min_debt': min_debt})

        sent_count = 0
        failed_count = 0
        errors = []

        # Telegram bot instance'ni olish
        from debt_scheduler import get_scheduler_instance
        scheduler = get_scheduler_instance(app, db)

        for row in results:
            try:
                debt_usd = float(row.total_debt_usd)
                debt_uzs = float(row.total_debt_uzs) if row.total_debt_uzs else debt_usd * exchange_rate

                # Telegram xabari yuborish
                success = scheduler.send_telegram_debt_reminder_sync(
                    chat_id=row.telegram_chat_id,
                    customer_name=row.name,
                    debt_usd=debt_usd,
                    debt_uzs=debt_uzs,
                    location_name="Do'kon",
                    customer_id=row.id  # Customer ID qo'shamiz
                )

                if success:
                    sent_count += 1
                else:
                    failed_count += 1
                    errors.append({
                        'customer': row.name,
                        'error': 'Telegram xabari yuborilmadi'
                    })

                # Rate limiting
                import time
                time.sleep(1)  # Telegram limitga tushmaslik uchun

            except Exception as e:
                failed_count += 1
                errors.append({
                    'customer': row.name,
                    'error': str(e)
                })

        logger.info(f"ğŸ“Š Bulk Telegram: {sent_count} yuborildi, {failed_count} xatolik")

        return jsonify({
            'success': True,
            'sent': sent_count,
            'failed': failed_count,
            'errors': errors
        })

    except Exception as e:
        logger.error(f"Bulk Telegram xatolik: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Monitoring routes qo'shish
try:
    from monitoring import setup_monitoring_routes
    setup_monitoring_routes(app, db)
    logger.info("âœ… Monitoring tizimi ishga tushdi")
except Exception as e:
    logger.warning(f"âš ï¸ Monitoring tizimi ishga tushmadi: {e}")


if __name__ == '__main__':
    # Telegram bot scheduler ni ishga tushirish
    try:
        from debt_scheduler import init_debt_scheduler
        logger.info("ğŸ¤– Telegram bot scheduler ishga tushirilmoqda...")
        init_debt_scheduler(app, db)
        logger.info("âœ… Telegram bot scheduler ishga tushdi")
    except Exception as e:
        logger.warning(f"âš ï¸ Telegram bot scheduler ishga tushmadi: {e}")

    # Development rejimi uchun debug=True (avtomatik qayta yuklash)
    app.run(debug=True, host='0.0.0.0', port=5000)
