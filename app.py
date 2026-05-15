# -*- coding: utf-8 -*-
import base64
import bcrypt
import io
import json
import logging
import os
import sys
import time
import urllib.parse
import secrets
import uuid
from translations import TRANSLATIONS
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
from rapidfuzz import process as fuzz_process, fuzz as rfuzz
import re

def normalize_search(text):
    """
    Qidiruv matnini normalizatsiya qilish:
    - faqat 2+ harfli guruh oldida/keyin raqam bo'lsa ajratish: "zimmerE8" â†’ "zimmer E8"
      (yakkĞ° harfli tokenlar yaratilmaydi: "50*30m" â†’ "50*30m", "e8h7" â†’ "e8h7")
    - pastki chiziqni bo'sh joy bilan almashtirish: "pro_h7" â†’ "pro h7"
    """
    text = text.replace('_', ' ')
    text = re.sub(r'([a-zA-Z]{2,})(\d)', r'\1 \2', text)
    text = re.sub(r'(\d)([a-zA-Z]{2,})', r'\1 \2', text)
    return re.sub(r'\s+', ' ', text).lower().strip()

def fuzzy_score(query, name):
    """
    So'z darajasida moslik (substring OR ratio>=60%) + fuzzy ball + raqam bonusi.
    - coverage: query tokenlarining qanchasi nomda mos keladi
    - digit_bonus: query va nom umumiy raqamlari (+10 har biri)
      "zmre8h7" â†’ digits={8,7}; E8 PRO-H7 â†’ {8,7} â†’ +20; E9 PRO-H7 â†’ {9,7} â†’ +10
    Shu bonus E8 vs E9 farqini aniq ajratadi.
    """
    q = normalize_search(query)
    n = normalize_search(name)
    q_words = [w for w in q.split() if len(w) >= 2]
    n_words = [w for w in n.split() if len(w) >= 2]
    if not q_words or not n_words:
        return 0

    def word_match(qw, n_words):
        if any(qw in nw for nw in n_words):
            return True
        return max((rfuzz.ratio(qw, nw) for nw in n_words), default=0) >= 60

    word_hits = sum(1 for qw in q_words if word_match(qw, n_words))
    coverage = word_hits / len(q_words)
    if coverage == 0:
        return 0

    s1 = rfuzz.partial_ratio(q, n)
    s2 = rfuzz.token_set_ratio(q, n)
    s3 = rfuzz.partial_token_set_ratio(q, n)

    # Umumiy raqam tokenlar bonusi: "8","7" mos â†’ +20; faqat "7" mos â†’ +10
    q_digits = set(re.findall(r'\d+', query))
    n_digits = set(re.findall(r'\d+', name))
    digit_bonus = len(q_digits & n_digits) * 10

    return coverage * 60 + max(s1, s2, s3) * 0.4 + digit_bonus
from flask_wtf.csrf import CSRFProtect

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

# Foydalanuvchi rasmlarini yuklash uchun papka
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'users')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB

# Mahsulot rasmlarini yuklash uchun papka
PRODUCT_UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), 'static', 'uploads', 'products')
os.makedirs(PRODUCT_UPLOAD_FOLDER, exist_ok=True)
app.config['PRODUCT_UPLOAD_FOLDER'] = PRODUCT_UPLOAD_FOLDER
ALLOWED_IMAGE_EXTENSIONS = {'jpg', 'jpeg', 'png', 'webp'}

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
    'pool_recycle': 540,      # 9 minut (PostgreSQL idle_in_transaction_timeout=10min dan oldin)
    'pool_pre_ping': True,    # Connection alive ekanini tekshirish (dead connection oldini oladi)
    'max_overflow': 20,       # Qo'shimcha 20 ta temporary connection
    'pool_timeout': 30,       # Connection olish uchun 30 sekund timeout
    'connect_args': {
        'connect_timeout': 10,  # PostgreSQL connection timeout
        'options': '-c statement_timeout=30000 -c timezone=Asia/Tashkent'  # Query timeout 30s va timezone
    }
}

# Session xavfsizligi
_secure = os.getenv('SESSION_COOKIE_SECURE', 'False').lower() == 'true'
app.config['SESSION_COOKIE_SECURE'] = _secure  # Production HTTPS uchun .env da True qiling
app.config['SESSION_COOKIE_HTTPONLY'] = True  # JavaScript orqali o'qib bo'lmaydi
app.config['SESSION_COOKIE_SAMESITE'] = 'None' if _secure else 'Lax'  # Cross-site uchun None, HTTP uchun Lax
app.config['PERMANENT_SESSION_LIFETIME'] = 43200  # 12 soat (uzaytirilgan ish vaqti)
app.config['SESSION_COOKIE_DOMAIN'] = None  # Subdomen muammosini hal qilish
app.config['WTF_CSRF_HEADERS'] = ['X-CSRFToken']  # AJAX CSRF header

# SQLAlchemy obyektini yaratish
db = SQLAlchemy(app)

# CSRF himoya - barcha POST/PUT/DELETE so'rovlari uchun
csrf = CSRFProtect(app)

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
def format_phone_number(phone):
    """Telefon raqamini formatlash: +998(99) 123-45-67"""
    if not phone:
        return ''

    # Faqat raqamlarni qoldirish
    digits = ''.join(filter(str.isdigit, phone))

    # Agar 998 bilan boshlanmasa va 9 bilan boshlansa, 998 qo'shish
    if digits and not digits.startswith('998'):
        if digits.startswith('9') and len(digits) == 9:
            digits = '998' + digits

    # Format: +998(99) 123-45-67
    if len(digits) >= 12:
        return f"+{digits[0:3]}({digits[3:5]}) {digits[5:8]}-{digits[8:10]}-{digits[10:12]}"
    elif len(digits) >= 10:
        return f"+{digits[0:3]}({digits[3:5]}) {digits[5:8]}-{digits[8:10]}"
    elif len(digits) >= 8:
        return f"+{digits[0:3]}({digits[3:5]}) {digits[5:8]}"
    elif len(digits) >= 5:
        return f"+{digits[0:3]}({digits[3:5]})"
    elif len(digits) >= 3:
        return f"+{digits[0:3]}"
    else:
        return phone  # Agar juda qisqa bo'lsa, asl qiymatni qaytarish


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


def calculate_average_cost(product_id, new_quantity, new_batch_cost):
    """
    Og'irlikli o'rtacha tan narxni backend'da hisoblash.
    Formula: (mavjud_qty * mavjud_narx + yangi_qty * yangi_narx) / (mavjud_qty + yangi_qty)
    """
    from sqlalchemy import func as sql_func
    warehouse_qty = db.session.query(
        sql_func.sum(WarehouseStock.quantity)
    ).filter_by(product_id=product_id).scalar() or Decimal('0')

    store_qty = db.session.query(
        sql_func.sum(StoreStock.quantity)
    ).filter_by(product_id=product_id).scalar() or Decimal('0')

    total_existing_qty = Decimal(str(warehouse_qty)) + Decimal(str(store_qty))

    product = Product.query.get(product_id)
    existing_cost = product.cost_price if product else Decimal('0')

    existing_value = total_existing_qty * existing_cost
    new_value = Decimal(str(new_quantity)) * Decimal(str(new_batch_cost))
    total_qty = total_existing_qty + Decimal(str(new_quantity))

    if total_qty > 0:
        average = (existing_value + new_value) / total_qty
    else:
        average = Decimal(str(new_batch_cost))

    return average.quantize(Decimal('0.00001'))


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
            'status': 'low' if (self.product and self.product.min_stock > 0 and self.quantity <= self.product.min_stock) else 'normal',
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
        first_location = items[0].get('location_name', 'â€”') if items else 'â€”'
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


# Helper funksiya: Transfer boshqarish ruxsatini tekshirish
def user_can_manage_transfer(user, pending_transfer):
    """
    Foydalanuvchi transferni tahrirlashi/o'chirishi/yakunlashi mumkinligini aniqlash.

    Ruxsat beriladi agar:
    1. Admin (har doim)
    2. Transfer joylashuvlaridan (FROM yoki TO) kamida biriga ruxsati bor foydalanuvchi
    """
    print(f"ğŸ” user_can_manage_transfer: User={user.username}, Role={user.role}, Transfer ID={pending_transfer.id}")
    print(f"   Transfer: {pending_transfer.from_location_type}_{pending_transfer.from_location_id} -> {pending_transfer.to_location_type}_{pending_transfer.to_location_id}")

    # 1. Admin har doim
    if user.role == 'admin':
        print("   âœ… ADMIN ACCESS")
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

    print(f"   User locations: {all_user_locations}")

    if not all_user_locations:
        print("   âŒ NO LOCATIONS")
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
            # int/str type mismatch oldini olish
            try:
                loc_id = int(loc_id)
            except (TypeError, ValueError):
                pass

            if loc_id == from_id and loc_type == from_type:
                has_from_permission = True
                print(f"   âœ… FROM permission: {from_type}_{from_id}")
            if loc_id == to_id and loc_type == to_type:
                has_to_permission = True
                print(f"   âœ… TO permission: {to_type}_{to_id}")

        # Eski format: integer (faqat id)
        elif isinstance(loc, int):
            if loc == from_id:
                has_from_permission = True
                print(f"   âœ… FROM permission (old format): {from_id}")
            if loc == to_id:
                has_to_permission = True
                print(f"   âœ… TO permission (old format): {to_id}")
        # String format
        elif isinstance(loc, str):
            try:
                loc_int = int(loc)
                if loc_int == from_id:
                    has_from_permission = True
                if loc_int == to_id:
                    has_to_permission = True
            except (ValueError, TypeError):
                pass

    # Kamida biriga ruxsat bo'lsa yetarli
    if has_from_permission or has_to_permission:
        print(f"   âœ… ACCESS GRANTED (from={has_from_permission}, to={has_to_permission})")
        return True

    print("   âŒ ACCESS DENIED")
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
            customer_name = 'ğŸš« O\'chirilgan mijoz'
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
@role_required('admin', 'kassir', 'omborchi')
def add_product():
    return render_template('add_product.html')


@app.route('/add_product_session')
@role_required('admin', 'kassir', 'omborchi')
def add_product_session():
    return render_template('add_product_session.html')


@app.route('/currency-rate')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def currency_rate():
    return render_template('currency_rate.html')

# API endpoint - keyingi barcode raqamini olish
@app.route('/api/next-barcode', methods=['GET', 'POST'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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

    # Search filter - nom yoki barcode bo'yicha qidirish
    if search:
        # Normalizatsiya: "e8h7" â†’ "e8 h7", "pro_h7" â†’ "pro h7"
        norm_search = normalize_search(search)
        search_words = [w for w in norm_search.split() if w]
        if search_words:
            # Har bir so'z uchun OR sharti: har qanday so'z bo'lsa topadi
            word_conditions = [
                db.or_(
                    Product.name.ilike(f'%{word}%'),
                    Product.barcode.ilike(f'%{word}%')
                )
                for word in search_words
            ]
            query = query.filter(db.or_(*word_conditions))

    # Saralash uchun location ma'lumotlarini saqlash
    final_loc_type = None
    final_loc_id = None

    # Location filter - yangi format (location_type va location_id)
    if location_type and location_id:
        final_loc_type = location_type
        final_loc_id = location_id
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
            final_loc_type = loc_type
            final_loc_id = loc_id

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

    # Category filter
    category_id_filter = request.args.get('category_id', type=int)
    if category_id_filter:
        query = query.filter(Product.category_id == category_id_filter)

    # Saralash: Eng ko'p sotilgan mahsulotlar birinchi bo'lishi uchun
    from sqlalchemy import desc, func
    from sqlalchemy import select as sa_select

    # Correlated subquery: har bir mahsulot uchun joylashuvdagi sotuvlar soni
    sold_sq = sa_select(func.coalesce(func.count(SaleItem.id), 0)).where(
        SaleItem.product_id == Product.id
    )
    if final_loc_type and final_loc_id:
        sold_sq = sold_sq.where(
            SaleItem.source_type == final_loc_type,
            SaleItem.source_id == final_loc_id
        )
    sold_sq = sold_sq.correlate(Product).scalar_subquery()

    query = query.order_by(desc(sold_sq))

    # Get paginated results
    paginated = query.paginate(
        page=page,
        per_page=per_page,
        error_out=False
    )

    db_product_ids = set()
    products_list = []
    for product in paginated.items:
        product_dict = product.to_dict()
        products_list.append(product_dict)
        db_product_ids.add(product.id)

    # Qidiruv bo'lganda: DB + fuzzy natijalarni birgalikda relevantlik bo'yicha saralash
    total_count = paginated.total
    if search and len(search) >= 2:
        logger.debug(f"ğŸ” Smart sort+fuzzy (api_products): '{search}'")
        base_query = Product.query.options(
            db.joinedload(Product.warehouse_stocks),
            db.joinedload(Product.store_stocks)
        )
        if final_loc_type and final_loc_id:
            if final_loc_type == 'warehouse':
                base_query = base_query.filter(
                    Product.warehouse_stocks.any(
                        WarehouseStock.warehouse_id == final_loc_id
                    )
                )
            elif final_loc_type == 'store':
                base_query = base_query.filter(
                    Product.store_stocks.any(
                        StoreStock.store_id == final_loc_id
                    )
                )
        all_products = base_query.all()
        name_to_product = {p.name: p for p in all_products if p.name}

        if name_to_product:
            CUTOFF = 45
            scores = {name: fuzzy_score(search, name) for name in name_to_product}

            # DB natijalariga score qo'shish (ular allaqachon products_list da)
            for pd in products_list:
                pd['_score'] = scores.get(pd.get('name', ''), 0)

            # Fuzzy orqali topilgan yangi natijalar qo'shish (DB da yo'qlar)
            fuzzy_added = 0
            for name, score in sorted(scores.items(), key=lambda x: -x[1]):
                if score < CUTOFF:
                    break
                product = name_to_product[name]
                if product.id not in db_product_ids:
                    pd = product.to_dict()
                    pd['fuzzy_match'] = True
                    pd['fuzzy_score'] = round(score)
                    pd['_score'] = score
                    products_list.append(pd)
                    db_product_ids.add(product.id)
                    fuzzy_added += 1
                    if fuzzy_added >= 10:
                        break

            # Barcha natijalarni relevantlik bo'yicha saralash (eng to'g'ri tepada)
            products_list.sort(key=lambda x: x.get('_score', 0), reverse=True)
            # Yordamchi maydonni tozalash
            for pd in products_list:
                pd.pop('_score', None)

            total_count = len(products_list)
            logger.debug(f"âœ… Smart sort: {len(products_list)} ta natija saralandi ({fuzzy_added} fuzzy)")

    # Return with pagination metadata
    return jsonify({
        'products': products_list,
        'pagination': {
            'page': page,
            'per_page': per_page,
            'total': total_count,
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
                'emoji': 'ğŸª',
                'display': f'ğŸª {store.name} (Do\'kon)'
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
                'emoji': 'ğŸ“¦',
                'display': f'ğŸ“¦ {warehouse.name} (Ombor)'
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
            'display': f'ğŸ“¦ {warehouse.name} (Ombor)'
        })

    # Barcha do'konlarni qo'shish
    stores = Store.query.all()
    for store in stores:
        logger.debug(f" Including store {store.id} ({store.name})")
        locations.append({
            'type': 'store',
            'id': store.id,
            'name': store.name,
            'display': f'ğŸª {store.name} (Do\'kon)'
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
                search_words = [w for w in normalize_search(search_term).split() if w]
                if search_words:
                    query = query.filter(db.or_(*[Product.name.ilike(f'%{w}%') for w in search_words]))

            stocks = query.limit(limit).all()

            for stock, product in stocks:
                if product:
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
                search_words = [w for w in normalize_search(search_term).split() if w]
                if search_words:
                    query = query.filter(db.or_(*[Product.name.ilike(f'%{w}%') for w in search_words]))

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

        # Smart sort: DB + fuzzy natijalarni birgalikda relevantlik bo'yicha saralash
        if search_term and len(search_term) >= 2:
            db_ids = {p['id'] for p in products_list}
            # M5 fix: Barcha stock'larni xotiraga yuklamaslik.
            # Faqat mahsulot nomlarini (id + name) olamiz - bu ancha yengil.
            if location_type == 'warehouse':
                name_rows = db.session.query(
                    WarehouseStock.product_id, WarehouseStock.quantity, Product.name
                ).join(Product, WarehouseStock.product_id == Product.id
                ).filter(WarehouseStock.warehouse_id == location_id
                ).with_entities(Product.id, Product.name, WarehouseStock.quantity).all()
            else:
                name_rows = db.session.query(
                    StoreStock.product_id, StoreStock.quantity, Product.name
                ).join(Product, StoreStock.product_id == Product.id
                ).filter(StoreStock.store_id == location_id
                ).with_entities(Product.id, Product.name, StoreStock.quantity).all()

            # Faqat id, name, quantity saqlash (to'liq object emas)
            name_to_light = {row.name: (row.id, row.quantity) for row in name_rows if row.name}

            if name_to_light:
                CUTOFF = 45
                scores = {name: fuzzy_score(search_term, name) for name in name_to_light}

                # DB natijalariga score qo'shish
                for pd in products_list:
                    pd['_score'] = scores.get(pd.get('name', ''), 0)

                # Fuzzy orqali yangi natijalar qo'shish (faqat top 10 ta)
                fuzzy_added = 0
                top_fuzzy = sorted(
                    ((name, score) for name, score in scores.items() if score >= CUTOFF),
                    key=lambda x: -x[1]
                )[:20]  # Kandidatlar soni cheklangan

                # Faqat kerakli product_id larni DB dan olamiz (bulk)
                fuzzy_product_ids = [name_to_light[name][0] for name, _ in top_fuzzy
                                     if name_to_light[name][0] not in db_ids][:10]

                if fuzzy_product_ids:
                    if location_type == 'warehouse':
                        fuzzy_stocks = db.session.query(WarehouseStock, Product).join(
                            Product, WarehouseStock.product_id == Product.id
                        ).filter(
                            WarehouseStock.warehouse_id == location_id,
                            WarehouseStock.product_id.in_(fuzzy_product_ids)
                        ).all()
                        loc_name_attr = lambda s: s.warehouse.name if s.warehouse else "Noma'lum ombor"
                        loc_type_val = 'warehouse'
                    else:
                        fuzzy_stocks = db.session.query(StoreStock, Product).join(
                            Product, StoreStock.product_id == Product.id
                        ).filter(
                            StoreStock.store_id == location_id,
                            StoreStock.product_id.in_(fuzzy_product_ids)
                        ).all()
                        loc_name_attr = lambda s: s.store.name if s.store else "Noma'lum do'kon"
                        loc_type_val = 'store'

                    for stock, product in fuzzy_stocks:
                        if product and product.id not in db_ids:
                            pd = product.to_dict()
                            pd['available_quantity'] = stock.quantity
                            pd['fuzzy_match'] = True
                            pd['fuzzy_score'] = round(scores.get(product.name, 0))
                            pd['_score'] = scores.get(product.name, 0)
                            pd['location_type'] = loc_type_val
                            pd['location_id'] = location_id
                            pd['location_name'] = loc_name_attr(stock)
                            products_list.append(pd)
                            db_ids.add(product.id)
                            fuzzy_added += 1

                # Hammani relevantlik bo'yicha saralash
                products_list.sort(key=lambda x: x.get('_score', 0), reverse=True)
                for pd in products_list:
                    pd.pop('_score', None)
                logger.debug(f"âœ… Smart sort: {len(products_list)} ta natija ({fuzzy_added} fuzzy)")

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
                    # Mavjud mahsulot - Backend'da og'irlikli o'rtacha narx hisoblanadi
                    last_batch_cost = Decimal(str(product_data.get('lastBatchCost', cost_price)))

                    logger.info('Mavjud mahsulot yangilanmoqda (backend hisoblash):')
                    logger.info(f'   Eski cost_price: ${existing_product.cost_price}')
                    logger.info(f'   Yangi partiya narxi: ${last_batch_cost}')
                    logger.info(f'   Yangi miqdor: {quantity}')

                    # Backend'da og'irlikli o'rtacha hisoblash
                    average_cost = calculate_average_cost(
                        existing_product.id, quantity, last_batch_cost
                    )
                    logger.info(f"   Hisoblangan o'rtacha: ${average_cost}")

                    # Ortacha narxni saqlash
                    existing_product.cost_price = average_cost

                    # Oxirgi partiya ma'lumotlarini saqlash
                    existing_product.last_batch_cost = last_batch_cost
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

                    # Kategoriya yangilash (agar berilgan bo'lsa)
                    if 'categoryId' in product_data and product_data['categoryId']:
                        existing_product.category_id = product_data['categoryId']
                        logger.info(f"   Kategoriya saqlandi: {product_data['categoryId']}")
                    else:
                        logger.info(f"   Kategoriya yo'q (categoryId: {product_data.get('categoryId')})")

                    product = existing_product
                else:
                    # Yangi mahsulot yaratish
                    cat_id = product_data.get('categoryId', None)
                    logger.info(f"   Yangi mahsulot kategoriyasi: {cat_id}")
                    product = Product(
                        name=product_data['name'],
                        barcode=product_data.get('barcode', None),  # Barcode qo'shish
                        cost_price=cost_price,
                        sell_price=sell_price,
                        last_batch_cost=cost_price,  # Birinchi partiya
                        last_batch_date=get_tashkent_time(),
                        stock_quantity=0,  # Global stock 0 ga qo'yamiz
                        min_stock=product_data.get('minStock', 0),
                        unit_type=product_data.get('unitType', 'dona'),  # O'lchov birligi
                        category_id=cat_id  # Kategoriya
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

            # Kategoriya ID ni integer ga o'tkazish
            raw_cat_id = product_data.get('categoryId')
            category_id = int(raw_cat_id) if raw_cat_id else None

            # Mahsulot mavjudligini tekshirish
            product = Product.query.filter_by(name=name).first()
            if not product:
                # Yangi mahsulot yaratish
                logger.info("âœ¨ Yangi mahsulot yaratilmoqda")
                logger.info(f"   Kategoriya: {category_id}")
                product = Product(
                    name=name,
                    barcode=barcode,  # Barcode saqlash
                    cost_price=cost_price,
                    sell_price=sell_price,
                    last_batch_cost=last_batch_cost,  # Frontend'dan kelgan qiymat
                    last_batch_date=get_tashkent_time(),
                    min_stock=min_stock,
                    unit_type=product_data.get('unitType', 'dona'),  # O'lchov birligi
                    category_id=category_id  # Kategoriya
                )
                db.session.add(product)
                db.session.flush()  # ID olish uchun
                logger.info(f"âœ… Yangi mahsulot yaratildi - ID: {product.id}, barcode: {product.barcode}, cost_price: ${product.cost_price}, last_batch_cost: ${product.last_batch_cost}")
            else:
                # Mavjud mahsulot - Backend'da og'irlikli o'rtacha narx hisoblanadi
                logger.info(f'Mavjud mahsulot yangilanmoqda (backend) - ID: {product.id}')
                logger.info(f'   Eski cost_price: ${product.cost_price}')
                logger.info(f'   Yangi partiya narxi: ${last_batch_cost}')
                logger.info(f'   Yangi miqdor: {quantity}')
                logger.info(f'   Kategoriya: {category_id}')

                # Backend'da og'irlikli o'rtacha hisoblash
                average_cost = calculate_average_cost(
                    product.id, int(quantity), last_batch_cost
                )
                logger.info(f"   Hisoblangan o'rtacha: ${average_cost}")

                # Ortacha narxni saqlash
                product.cost_price = average_cost

                # Barcode yangilash (agar kiritilgan bo'lsa)
                if barcode:
                    product.barcode = barcode

                # Unit type yangilash (agar berilgan bo'lsa)
                if 'unitType' in product_data:
                    product.unit_type = product_data['unitType']

                # Kategoriya yangilash
                if category_id is not None:
                    product.category_id = category_id

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

        # saved_products ro'yxatini qaytarish (rasm upload uchun)
        saved_list = []
        for p_data in products:
            prod = Product.query.filter_by(
                name=p_data.get('name')
            ).order_by(Product.id.desc()).first()
            if prod:
                saved_list.append({'id': prod.id, 'name': prod.name})

        return jsonify({
            'success': True,
            'created': created_count,
            'saved_products': saved_list,
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

        # Optimized query - qisman so'zlar bilan qidiruv (har bir so'z alohida)
        words = product_name.split()
        filters = [Product.name.ilike(f'%{w}%') for w in words]
        products = Product.query.filter(
            db.and_(*filters)
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
                    'last_batch_date': product.last_batch_date.isoformat() if product.last_batch_date else None,
                    'category_id': product.category_id,
                    'image_url': f'/static/uploads/products/{product.image_path}' if product.image_path else None
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
@role_required('admin', 'kassir', 'sotuvchi')
def sales():
    return render_template('sales.html')


@app.route('/sales-history')
@role_required('admin', 'kassir', 'sotuvchi')
def sales_history():
    return render_template(
        'sales-history.html',
        page_title='Savdo tarixi',
        icon='ğŸ“Š')


@app.route('/debt-sales')
@role_required('admin', 'kassir', 'sotuvchi')
def debt_sales():
    return render_template(
        'debt-sales.html',
        page_title='Qarz savdolar',
        icon='ğŸ’³')


@app.route('/pending-sales')
@role_required('admin', 'kassir', 'sotuvchi')
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


@app.route('/customer-balances')
@role_required('admin', 'kassir', 'sotuvchi')
def customer_balances():
    """Mijozlar balansi sahifasi"""
    user = get_current_user()
    return render_template(
        'customer_balances.html',
        page_title='Mijozlar balansi',
        icon='ğŸ’°',
        current_user=user,
        user_role=user.role if user else 'guest')


@app.route('/api/customer-balances')
@role_required('admin', 'kassir', 'sotuvchi')
def api_customer_balances():
    """Barcha mijozlar balansi (qarz + to'langan) API"""
    try:
        current_user = get_current_user()
        exchange_rate = get_current_currency_rate()

        from sqlalchemy import text as sa_text

        # Sotuvchi uchun faqat ruxsat berilgan do'konlar
        store_filter_sql = ""
        bind_params = {}
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []
            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            if allowed_store_ids:
                store_filter_sql = "WHERE c.store_id = ANY(:store_ids)"
                bind_params['store_ids'] = allowed_store_ids
            else:
                # Hech qanday do'kon ruxsati yo'q â€” bo'sh qaytarish
                return jsonify({'success': True, 'customers': [], 'exchange_rate': float(exchange_rate)})

        query = sa_text(f"""
            SELECT
                c.id,
                c.name,
                c.phone,
                c.store_id,
                COALESCE(c.balance, 0) AS balance,
                COALESCE(SUM(s.debt_usd), 0) AS debt_usd,
                COALESCE(c.last_debt_payment_usd, 0) AS last_payment_amount,
                c.last_debt_payment_date AS last_payment_date,
                COALESCE(
                    (SELECT SUM(dp.total_usd)
                     FROM debt_payments dp
                     WHERE dp.customer_id = c.id), 0
                ) AS paid_usd
            FROM customers c
            LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
            {store_filter_sql}
            GROUP BY c.id, c.name, c.phone, c.store_id, c.balance,
                     c.last_debt_payment_usd, c.last_debt_payment_date
            ORDER BY debt_usd DESC, c.name ASC
        """)

        result = db.session.execute(query, bind_params)

        customers = []
        for row in result:
            customers.append({
                'id': row.id,
                'name': row.name,
                'phone': row.phone or '',
                'debt_usd': float(row.debt_usd),
                'paid_usd': float(row.paid_usd),
                'balance': float(row.balance),
                'last_payment_amount': float(row.last_payment_amount),
                'last_payment_date': row.last_payment_date.strftime('%d.%m.%Y %H:%M') if row.last_payment_date else None,
            })

        return jsonify({
            'success': True,
            'customers': customers,
            'exchange_rate': float(exchange_rate)
        })
    except Exception as e:
        logger.error(f"Customer balances API xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/customer/<int:customer_id>/update-balance', methods=['POST'])
@role_required('admin', 'kassir')
def api_update_customer_balance(customer_id):
    """Mijoz balansini tahrirlash"""
    try:
        data = request.get_json()
        new_balance = data.get('balance')
        if new_balance is None:
            return jsonify({'success': False, 'error': 'balance kiritilmadi'}), 400
        try:
            new_balance = Decimal(str(new_balance))
        except Exception:
            return jsonify({'success': False, 'error': 'Noto\'g\'ri balans qiymati'}), 400
        if new_balance < 0:
            return jsonify({'success': False, 'error': 'Balans 0 dan kichik bo\'lishi mumkin emas'}), 400

        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        old_balance = float(customer.balance or 0)
        customer.balance = new_balance
        db.session.commit()
        logger.info(f"Mijoz #{customer_id} balansi tahrirlandi: ${old_balance} \u2192 ${float(new_balance)}")
        log_operation(
            operation_type='edit',
            table_name='customers',
            record_id=customer_id,
            description=f"Mijoz balansi tahrirlandi: {customer.name} | ${old_balance:.2f} \u2192 ${float(new_balance):.2f}",
            old_data={'balance': old_balance, 'customer_name': customer.name},
            new_data={'balance': float(new_balance), 'customer_name': customer.name},
            amount=float(new_balance)
        )
        return jsonify({'success': True, 'new_balance': float(new_balance)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"update-balance xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/customer/<int:customer_id>/add-balance', methods=['POST'])
@role_required('admin', 'kassir')
def api_add_customer_balance(customer_id):
    """Mijoz balansiga qo'shish"""
    try:
        data = request.get_json()
        amount = data.get('amount')
        if amount is None:
            return jsonify({'success': False, 'error': 'amount kiritilmadi'}), 400
        try:
            amount = Decimal(str(amount))
        except Exception:
            return jsonify({'success': False, 'error': 'Noto\'g\'ri summa'}), 400
        if amount <= 0:
            return jsonify({'success': False, 'error': 'Summa 0 dan katta bo\'lishi kerak'}), 400

        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        old_balance = Decimal(str(customer.balance or 0))
        customer.balance = old_balance + amount
        db.session.commit()
        logger.info(f"Mijoz #{customer_id} balansiga ${amount} qo'shildi (yangi: ${float(customer.balance)})")
        log_operation(
            operation_type='payment',
            table_name='customers',
            record_id=customer_id,
            description=f"Mijoz balansiga qo'shildi: {customer.name} | +${float(amount):.2f} (yangi balans: ${float(customer.balance):.2f})",
            old_data={'balance': float(old_balance), 'customer_name': customer.name},
            new_data={'balance': float(customer.balance), 'added': float(amount), 'customer_name': customer.name},
            amount=float(amount)
        )
        return jsonify({'success': True, 'new_balance': float(customer.balance)})
    except Exception as e:
        db.session.rollback()
        logger.error(f"add-balance xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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


@app.route('/customer/<int:customer_id>/timeline')
@role_required('admin', 'kassir', 'sotuvchi')
def customer_timeline(customer_id):
    """Mijoz barcha amallari ketma-ketligi sahifasi"""
    try:
        customer = Customer.query.get_or_404(customer_id)
        return render_template(
            'customer_timeline.html',
            customer=customer,
            page_title=f'{customer.name} - Amallar tarixi',
            icon='ğŸ“‹')
    except Exception as e:
        app.logger.error(f"Error loading customer timeline: {str(e)}")
        return "Mijoz ma'lumotlari yuklanmadi", 500


@app.route('/api/customer/<int:customer_id>/timeline')
@role_required('admin', 'kassir', 'sotuvchi')
def api_customer_timeline(customer_id):
    """Mijoz barcha amallarini ketma-ketlikda qaytaradi (savdolar + to'lovlar)"""
    try:
        customer = Customer.query.get_or_404(customer_id)
        events = []

        # Savdolarni olish
        sales = Sale.query.filter_by(customer_id=customer_id).order_by(Sale.sale_date.desc()).all()
        for sale in sales:
            items_list = []
            for item in sale.items:
                try:
                    pname = item.product.name if item.product else 'Noma\'lum'
                except Exception:
                    pname = 'Noma\'lum'
                items_list.append({
                    'product_name': pname,
                    'quantity': float(item.quantity or 0),
                    'unit_price': float(item.unit_price or 0),
                    'total': float((item.unit_price or 0) * (item.quantity or 0))
                })
            events.append({
                'type': 'sale',
                'id': sale.id,
                'date': sale.sale_date.strftime('%Y-%m-%d %H:%M:%S') if sale.sale_date else None,
                'total_amount': float(sale.total_amount or 0),
                'payment_status': sale.payment_status,
                'cash_usd': float(sale.cash_usd or 0),
                'click_usd': float(sale.click_usd or 0),
                'terminal_usd': float(sale.terminal_usd or 0),
                'debt_usd': float(sale.debt_usd or 0),
                'currency_rate': float(sale.currency_rate or 0),
                'seller': f"{sale.seller.first_name} {sale.seller.last_name}".strip() if sale.seller else 'Noma\'lum',
                'notes': sale.notes or '',
                'items': items_list,
                'items_count': len(items_list)
            })

        # Qarz to'lovlarini olish
        payments = DebtPayment.query.filter_by(customer_id=customer_id).order_by(DebtPayment.payment_date.desc()).all()
        for p in payments:
            events.append({
                'type': 'payment',
                'id': p.id,
                'date': p.payment_date.strftime('%Y-%m-%d %H:%M:%S') if p.payment_date else None,
                'total_usd': float(p.total_usd or 0),
                'cash_usd': float(p.cash_usd or 0),
                'click_usd': float(p.click_usd or 0),
                'terminal_usd': float(p.terminal_usd or 0),
                'currency_rate': float(p.currency_rate or 0),
                'received_by': p.received_by or '',
                'notes': p.notes or '',
                'sale_id': p.sale_id
            })

        # Qaytarilgan mahsulotlarni olish (operation_history orqali)
        sale_ids = [s.id for s in sales]
        if sale_ids:
            returns = OperationHistory.query.filter(
                OperationHistory.operation_type == 'return',
                OperationHistory.record_id.in_(sale_ids)
            ).order_by(OperationHistory.created_at.desc()).all()
            for r in returns:
                nd = r.new_data or {}
                events.append({
                    'type': 'return',
                    'id': r.id,
                    'date': r.created_at.strftime('%Y-%m-%d %H:%M:%S') if r.created_at else None,
                    'sale_id': nd.get('sale_id') or r.record_id,
                    'product_name': nd.get('product_name', 'Noma\'lum'),
                    'returned_quantity': float(nd.get('returned_quantity', 0)),
                    'amount_usd': float(nd.get('amount_usd', 0)),
                    'username': r.username or '',
                    'description': r.description or ''
                })

        # Sanaga ko'ra tartiblash (yangirog'i birinchi)
        events.sort(key=lambda x: x['date'] or '', reverse=True)

        # Haqiqiy qarz = savdolardagi joriy qolgan debt_usd yig'indisi
        current_debt = sum(float(s.debt_usd or 0) for s in sales if float(s.debt_usd or 0) > 0)
        total_paid_usd = sum(float(p.total_usd or 0) for p in payments)

        # Har bir savdo uchun asl qarz = joriy debt_usd + o'sha savdoga bog'liq to'lovlar
        sale_linked_payments = {}
        for p in payments:
            if p.sale_id:
                sale_linked_payments[p.sale_id] = sale_linked_payments.get(p.sale_id, 0.0) + float(p.total_usd or 0)
        original_debt_map = {}
        for s in sales:
            orig = float(s.debt_usd or 0) + sale_linked_payments.get(s.id, 0.0)
            original_debt_map[s.id] = orig

        # --- Orqaga ishlash: oxirgi to'g'ri holatdan (DB) vaqt bo'yicha orqaga ---
        # Bu yondashuv har doim DB dagi joriy qarz/balans bilan mos keladi
        final_debt = current_debt
        final_balance = float(customer.balance or 0)

        chrono_rev = sorted(events, key=lambda x: x['date'] or '', reverse=True)
        rd = final_debt
        rb = final_balance

        for ev in chrono_rev:
            ev['debt_after'] = round(max(0.0, rd), 2)
            ev['balance_after'] = round(max(0.0, rb), 2)

            if ev['type'] == 'sale':
                # Bu savdoni bekor qilish: qarz + orqaga
                orig = original_debt_map.get(ev['id'], 0.0)
                rd = max(0.0, rd - orig)
            elif ev['type'] == 'payment':
                amt = ev.get('total_usd', 0)
                rb -= amt
                if rb < 0:
                    rd += abs(rb)
                    rb = 0.0
            elif ev['type'] == 'return':
                amt = ev.get('amount_usd', 0)
                rb -= amt
                if rb < 0:
                    rd += abs(rb)
                    rb = 0.0

        return jsonify({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone or '',
                'balance': float(customer.balance or 0),
                'current_debt': current_debt,
                'total_paid_usd': total_paid_usd
            },
            'events': events
        })
    except Exception as e:
        app.logger.error(f"Error in customer timeline API: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/customer/<int:customer_id>')
@role_required('admin', 'kassir', 'sotuvchi')
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
@role_required('admin', 'kassir', 'sotuvchi')
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

            # Telefon raqami unikligini tekshirish
            if phone and phone.strip():
                existing = Customer.query.filter(Customer.phone == phone.strip()).first()
                if existing:
                    return jsonify({
                        'error': f'Bu telefon raqam ({phone.strip()}) allaqachon "{existing.name}" mijozida ro\'yxatdan o\'tgan'
                    }), 400

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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def products_list():
    return render_template('products.html')


# â”€â”€â”€ Kategoriya API endpoints â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route('/api/categories', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def api_get_categories():
    try:
        categories = Category.query.order_by(Category.name).all()
        return jsonify({'success': True, 'categories': [c.to_dict() for c in categories]})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/categories', methods=['POST'])
@role_required('admin')
def api_create_category():
    try:
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Nom kiritilishi shart'}), 400
        if Category.query.filter_by(name=name).first():
            return jsonify({'success': False, 'error': 'Bu nom allaqachon mavjud'}), 400
        color = (data.get('color') or '#6366f1').strip()
        cat = Category(name=name, color=color)
        db.session.add(cat)
        db.session.commit()
        return jsonify({'success': True, 'category': cat.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/categories/<int:cat_id>', methods=['PATCH'])
@role_required('admin')
def api_update_category(cat_id):
    try:
        cat = Category.query.get_or_404(cat_id)
        data = request.get_json() or {}
        name = (data.get('name') or '').strip()
        if not name:
            return jsonify({'success': False, 'error': 'Nom kiritilishi shart'}), 400
        existing = Category.query.filter_by(name=name).first()
        if existing and existing.id != cat_id:
            return jsonify({'success': False, 'error': 'Bu nom allaqachon mavjud'}), 400
        cat.name = name
        if 'color' in data and data['color']:
            cat.color = data['color'].strip()
        db.session.commit()
        return jsonify({'success': True, 'category': cat.to_dict()})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/categories/<int:cat_id>', methods=['DELETE'])
@role_required('admin')
def api_delete_category(cat_id):
    try:
        cat = Category.query.get_or_404(cat_id)
        db.session.delete(cat)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# â”€â”€â”€ Mahsulot rasmi upload endpoint â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def _allowed_image(filename):
    return ('.' in filename and
            filename.rsplit('.', 1)[1].lower() in ALLOWED_IMAGE_EXTENSIONS)


@app.route('/api/products/<int:product_id>/image', methods=['POST'])
@role_required('admin', 'kassir', 'omborchi')
def api_upload_product_image(product_id):
    try:
        from PIL import Image as PILImage
        product = Product.query.get_or_404(product_id)

        if 'image' not in request.files:
            return jsonify({'success': False, 'error': 'Rasm tanlanmagan'}), 400

        file = request.files['image']
        if not file or not file.filename:
            return jsonify({'success': False, 'error': 'Rasm tanlanmagan'}), 400

        if not _allowed_image(file.filename):
            return jsonify({'success': False, 'error': 'Faqat jpg, jpeg, png, webp formatlar ruxsat etilgan'}), 400

        # Eski rasmni o'chirish
        if product.image_path:
            old_file = os.path.join(app.config['PRODUCT_UPLOAD_FOLDER'], product.image_path)
            if os.path.exists(old_file):
                os.remove(old_file)

        # Yangi fayl nomi - UUID bilan (xavfsizlik uchun)
        filename = f"{product_id}_{uuid.uuid4().hex[:8]}.jpg"

        # Faylni to'g'ridan-to'g'ri saqlash (qayta encode yo'q - frontend 800x800 JPEG yuboradi)
        # PIL faqat validatsiya uchun ishlatiladi
        from PIL import Image as PILImage2
        file_bytes = file.read()
        import io
        PILImage2.open(io.BytesIO(file_bytes)).verify()  # rasm ekanligini tekshirish
        save_path = os.path.join(app.config['PRODUCT_UPLOAD_FOLDER'], filename)
        with open(save_path, 'wb') as f_out:
            f_out.write(file_bytes)

        product.image_path = filename
        db.session.commit()

        return jsonify({
            'success': True,
            'image_url': f'/static/uploads/products/{filename}'
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f'Product image upload error: {e}')
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/products/<int:product_id>/image', methods=['DELETE'])
@role_required('admin', 'kassir', 'omborchi')
def api_delete_product_image(product_id):
    try:
        product = Product.query.get_or_404(product_id)
        if product.image_path:
            old_file = os.path.join(app.config['PRODUCT_UPLOAD_FOLDER'], product.image_path)
            if os.path.exists(old_file):
                os.remove(old_file)
            product.image_path = None
            db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# â”€â”€â”€ Mahsulot kategoriyasini yangilash â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

@app.route('/api/products/<int:product_id>/category', methods=['PATCH'])
@role_required('admin', 'kassir', 'omborchi')
def api_update_product_category(product_id):
    try:
        product = Product.query.get_or_404(product_id)
        data = request.get_json() or {}
        category_id = data.get('category_id')
        if category_id is not None and category_id != '':
            cat = Category.query.get(category_id)
            if not cat:
                return jsonify({'success': False, 'error': 'Kategoriya topilmadi'}), 404
            product.category_id = category_id
        else:
            product.category_id = None
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/print-barcode')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def print_barcode():
    """Barcode chop etish sahifasi"""
    return render_template('barcode_print.html')


@app.route('/transfer')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def transfer():
    return render_template('transfer.html')


@app.route('/transfer_session')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def transfer_session():
    return render_template('transfer_session.html')


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
        refund_type = data.get('refund_type', 'cash')  # 'cash' yoki 'balance'

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
                # Total price va profit ni yangilash (qisman qaytarishdan keyin)
                sale_item.total_price = sale_item.unit_price * Decimal(str(sale_item.quantity))
                sale_item.profit = Decimal(str(sale_item.profit or 0)) - returned_profit

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

            # To'lovlarni qaytarish turi bo'yicha
            if refund_type == 'balance':
                # Mijoz balansiga qo'shish
                customer = Customer.query.get(sale.customer_id) if sale.customer_id else None
                if customer:
                    old_balance = Decimal(str(customer.balance or 0))
                    customer.balance = old_balance + total_returned_usd
                    logger.info(f"ğŸ¦ Mijoz #{customer.id} balansiga ${total_returned_usd} qo'shildi (yangi balans: ${customer.balance})")
                    refund_operation = OperationHistory(
                        operation_type='payment_refund',
                        table_name='sales',
                        record_id=sale_id,
                        user_id=session.get('user_id'),
                        username=session.get('username'),
                        description=f"Qaytarilgan summa mijoz balansiga qo'shildi: ${total_returned_usd:.2f}",
                        old_data={'balance': float(old_balance)},
                        new_data={
                            'sale_id': sale_id,
                            'payment_type': 'balance',
                            'refund_amount_usd': float(total_returned_usd),
                            'new_balance': float(customer.balance)
                        },
                        ip_address=request.remote_addr,
                        location_id=location_id,
                        location_type=location_type,
                        location_name=location_name,
                        amount=float(total_returned_usd * sale.currency_rate)
                    )
                    db.session.add(refund_operation)
                else:
                    logger.warning("âš ï¸ Savdoda mijoz yo'q, balans o'rniga naqd qaytarish amalga oshiriladi")
                    refund_type = 'cash'  # Fallback to cash

            elif refund_type == 'debt':
                # Mijoz qarzidan sondir
                customer = Customer.query.get(sale.customer_id) if sale.customer_id else None
                if not customer:
                    return jsonify({'success': False, 'error': 'no_customer',
                                    'message': 'Savdoda mijoz biriktirilmagan. Naqd qaytarish tanlang.'}), 400

                remaining_to_deduct = total_returned_usd

                # 1. Avval o'sha savdoning o'z qarzidan ayir
                if sale.debt_usd and sale.debt_usd > 0:
                    old_debt = Decimal(str(sale.debt_usd))
                    deduct = min(remaining_to_deduct, old_debt)
                    sale.debt_usd = old_debt - deduct
                    if sale.debt_usd <= 0:
                        sale.debt_usd = Decimal('0')
                        sale.payment_status = 'paid'
                    remaining_to_deduct -= deduct
                    logger.info(f"ğŸ’³ Savdo #{sale_id} qarzidan ${deduct} sondirildi (qoldi: ${sale.debt_usd})")

                # 2. Agar hali qolsa â€” boshqa qarzli savdolardan (eng eskisidan)
                if remaining_to_deduct > 0:
                    other_debt_sales = Sale.query.filter(
                        Sale.customer_id == sale.customer_id,
                        Sale.id != sale_id,
                        Sale.debt_usd > 0
                    ).order_by(Sale.sale_date.asc()).all()

                    if not other_debt_sales and remaining_to_deduct == total_returned_usd:
                        # Umuman qarz yo'q â€” session rollback qilish
                        db.session.rollback()
                        return jsonify({
                            'success': False,
                            'error': 'no_debt',
                            'message': f'Bu mijozda hech qanday qarz savdosi yo\'q. '
                                       f'Iltimos balansga o\'tkazing yoki naqd qaytaring.'
                        }), 400

                    for ds in other_debt_sales:
                        if remaining_to_deduct <= 0:
                            break
                        old_d = Decimal(str(ds.debt_usd))
                        d2 = min(remaining_to_deduct, old_d)
                        ds.debt_usd = old_d - d2
                        if ds.debt_usd <= 0:
                            ds.debt_usd = Decimal('0')
                            ds.payment_status = 'paid'
                        remaining_to_deduct -= d2
                        logger.info(f"ğŸ’³ Savdo #{ds.id} qarzidan ${d2} sondirildi (qoldi: ${ds.debt_usd})")

                # 3. Agar baribir qolsa (barcha qarzlar yopildi) â€” balansga
                if remaining_to_deduct > Decimal('0.001'):
                    customer.balance = Decimal(str(customer.balance or 0)) + Decimal(str(remaining_to_deduct))
                    logger.info(f"ğŸ¦ Ortiqcha ${remaining_to_deduct:.2f} balansga qo'shildi")

                refund_operation = OperationHistory(
                    operation_type='payment_refund',
                    table_name='sales',
                    record_id=sale_id,
                    user_id=session.get('user_id'),
                    username=session.get('username'),
                    description=f"Qaytarilgan summa qarzdan sondirildi: ${float(total_returned_usd):.2f} (Savdo #{sale_id})",
                    old_data={},
                    new_data={
                        'sale_id': sale_id,
                        'payment_type': 'debt',
                        'refund_amount_usd': float(total_returned_usd),
                    },
                    ip_address=request.remote_addr,
                    location_id=location_id,
                    location_type=location_type,
                    location_name=location_name,
                    amount=float(total_returned_usd * sale.currency_rate)
                )
                db.session.add(refund_operation)



            if refund_type == 'cash':
                # Smart Logic: avval qarz, keyin naqd, click, terminal
                remaining_refund = total_returned_usd
                refunded_payments = []

                # 1. AVVAL qarzdan qaytarish (agar qarz mavjud bo'lsa)
                if sale.debt_usd and sale.debt_usd > 0 and remaining_refund > 0:
                    debt_refund = min(Decimal(str(sale.debt_usd)), remaining_refund)
                    sale.debt_usd = Decimal(str(sale.debt_usd)) - debt_refund
                    remaining_refund -= debt_refund
                    refunded_payments.append(('debt', float(debt_refund)))
                    logger.info(f"  ğŸ“ Qarzdan kamaytirildi: ${debt_refund} (Qolgan qarz: ${sale.debt_usd})")

                # 2. Naqddan qaytarish
                if sale.cash_usd and sale.cash_usd > 0 and remaining_refund > 0:
                    cash_refund = min(Decimal(str(sale.cash_usd)), remaining_refund)
                    sale.cash_usd = Decimal(str(sale.cash_usd)) - cash_refund
                    remaining_refund -= cash_refund
                    refunded_payments.append(('cash', float(cash_refund)))
                    logger.info(f"  ğŸ’µ Naqd puldan qaytarildi: ${cash_refund}")

                # 3. Click dan qaytarish
                if sale.click_usd and sale.click_usd > 0 and remaining_refund > 0:
                    click_refund = min(Decimal(str(sale.click_usd)), remaining_refund)
                    sale.click_usd = Decimal(str(sale.click_usd)) - click_refund
                    remaining_refund -= click_refund
                    refunded_payments.append(('click', float(click_refund)))
                    logger.info(f"  ğŸ“± Click dan qaytarildi: ${click_refund}")

                # 4. Terminal dan qaytarish
                if sale.terminal_usd and sale.terminal_usd > 0 and remaining_refund > 0:
                    terminal_refund = min(Decimal(str(sale.terminal_usd)), remaining_refund)
                    sale.terminal_usd = Decimal(str(sale.terminal_usd)) - terminal_refund
                    remaining_refund -= terminal_refund
                    refunded_payments.append(('terminal', float(terminal_refund)))
                    logger.info(f"  ğŸ’³ Terminal dan qaytarildi: ${terminal_refund}")

                # 5. Agar hali ham qolsa, qarzga qo'shish (manfiy qarz - endi do'kon mijozga qarzdor)
                if remaining_refund > 0:
                    sale.debt_usd = Decimal(str(sale.debt_usd or 0)) + remaining_refund
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
            'returned_items': returned_items,
            'refund_type': refund_type
        })

    except Exception as e:
        db.session.rollback()
        import traceback
        error_traceback = traceback.format_exc()
        logger.error(f"Mahsulot qaytarishda xatolik: {str(e)}")
        logger.error(f"Traceback: {error_traceback}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/sales-by-product/<int:product_id>')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
                    customer_phone = ''
                    try:
                        if sale.customer:
                            customer_name = sale.customer.name
                            customer_phone = sale.customer.phone or ''
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
                        'customer_phone': customer_phone,
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def operations_history():
    """Amaliyotlar tarixi sahifasi"""
    return render_template('operations_history.html')


@app.route('/api/operations-history')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
            # user_id bo'yicha filter (username orqali ham qo'llab-quvvatlash)
            user = User.query.get(user_id)
            if user:
                query = query.filter(
                    db.or_(
                        OperationHistory.user_id == user_id,
                        OperationHistory.username == user.username,
                        OperationHistory.username == f"{user.first_name} {user.last_name}"
                    )
                )

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


@app.route('/api/operations-history/users')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def api_operations_history_users():
    """Amaliyotlar tarixidagi haqiqiy foydalanuvchilar ro'yxati"""
    try:
        users = User.query.filter_by(is_active=True).order_by(User.first_name).all()
        result = []
        for u in users:
            result.append({
                'id': u.id,
                'display': f"{u.first_name} {u.last_name} ({u.role})"
            })
        return jsonify({'success': True, 'users': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/product/<int:product_id>/operations')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def api_product_operations(product_id):
    """Mahsulot bo'yicha amaliyotlar tarixini olish"""
    try:
        product = Product.query.get(product_id)
        if not product:
            return jsonify({'success': False, 'error': 'Mahsulot topilmadi'}), 404

        # Bu mahsulotga tegishli stock IDlarini olish
        warehouse_stock_ids = [ws.id for ws in WarehouseStock.query.filter_by(product_id=product_id).all()]
        store_stock_ids = [ss.id for ss in StoreStock.query.filter_by(product_id=product_id).all()]

        # Bu mahsulot sotilgan savdo IDlarini olish (eski format: table_name='sales')
        sale_ids_old = [si.sale_id for si in SaleItem.query.filter_by(product_id=product_id).all()]

        # To'liq filter: barcha amaliyotlarni qamrab olish
        from sqlalchemy import or_, and_, text as sa_text

        conditions = [
            # 1. Bevosita mahsulot operatsiyalari (add_product, yangi sale, edit, delete)
            and_(
                OperationHistory.record_id == product_id,
                OperationHistory.table_name == 'products'
            ),
            # 2. new_data ichida product_id bo'lgan operatsiyalar (transfer, ba'zi add_product)
            sa_text("(new_data->>'product_id')::text = :pid").bindparams(pid=str(product_id)),
        ]

        # 3. Ombor stok operatsiyalari (edit_stock)
        if warehouse_stock_ids:
            conditions.append(
                and_(
                    OperationHistory.record_id.in_(warehouse_stock_ids),
                    OperationHistory.table_name == 'warehouse_stock'
                )
            )

        # 4. Do'kon stok operatsiyalari (edit_stock)
        if store_stock_ids:
            conditions.append(
                and_(
                    OperationHistory.record_id.in_(store_stock_ids),
                    OperationHistory.table_name.in_(['store_stocks', 'store_stock'])
                )
            )

        # 5. Eski savdo loglari (table_name='sales') â€” faqat description da mahsulot nomi bor bo'lsa
        if sale_ids_old:
            conditions.append(
                and_(
                    OperationHistory.record_id.in_(sale_ids_old),
                    OperationHistory.table_name == 'sales',
                    OperationHistory.operation_type.in_(['sale', 'return', 'sale_edit', 'payment_refund']),
                    OperationHistory.description.ilike(f'%{product.name}%')
                )
            )

        ops = OperationHistory.query.filter(
            or_(*conditions)
        ).order_by(OperationHistory.created_at.desc()).limit(200).all()

        op_labels = {
            'sale': 'ğŸ›’ Sotish',
            'sale_edit': 'âœï¸ Savdo tahrirlash',
            'add_product': 'ğŸ“¦ Qo\'shish',
            'edit_stock': 'ğŸ“ Zaxira tahrirlash',
            'transfer': 'ğŸ”„ Transfer',
            'return': 'â†©ï¸ Qaytarish',
            'debt_payment': 'ğŸ’³ Qarz to\'lash',
            'edit': 'âœï¸ Tahrirlash',
            'delete': 'ğŸ—‘ï¸ O\'chirish',
            'delete_stock': 'ğŸ—‘ï¸ Zaxira o\'chirish',
            'edit_user': 'ğŸ‘¤ Foydalanuvchi tahrirlash',
            'create_store': 'ğŸª Do\'kon yaratish',
        }

        # user_id â†’ user ma'lumotlari cache
        user_cache = {}

        import re as _re
        result = []
        seen_ids = set()
        seen_sale_keys = set()  # "Savdo #NNN" takroriylikni bloklash
        for op in ops:
            if op.id in seen_ids:
                continue
            seen_ids.add(op.id)

            # Savdo takroriyligini tekshirish
            if op.operation_type == 'sale' and op.description:
                sale_match = _re.search(r'Savdo #(\d+)', op.description)
                if sale_match:
                    sale_key = sale_match.group(1)
                    if sale_key in seen_sale_keys:
                        continue
                    seen_sale_keys.add(sale_key)

            user_role = None
            user_phone = None
            if op.user_id:
                if op.user_id not in user_cache:
                    u = User.query.get(op.user_id)
                    user_cache[op.user_id] = u
                u = user_cache[op.user_id]
                if u:
                    user_role = u.get_role_display() if hasattr(u, 'get_role_display') else u.role
                    user_phone = u.phone
            elif op.username:
                # user_id yo'q bo'lsa username bo'yicha qidirish
                uname = op.username.strip()
                cache_key = f'name_{uname}'
                if cache_key not in user_cache:
                    parts = uname.split(' ', 1)
                    first = parts[0] if parts else ''
                    last = parts[1] if len(parts) > 1 else ''
                    u = User.query.filter(
                        (User.username == uname) |
                        (User.email == uname) |
                        ((User.first_name + ' ' + User.last_name) == uname) |
                        (User.first_name == first)
                    ).first()
                    # 'admin' string saqlangan lekin haqiqiy user topilmasa â€” role='admin' userni olish
                    if not u and uname.lower() == 'admin':
                        u = User.query.filter_by(role='admin').first()
                    user_cache[cache_key] = u
                u = user_cache[cache_key]
                if u:
                    user_role = u.get_role_display() if hasattr(u, 'get_role_display') else u.role
                    user_phone = u.phone

            result.append({
                'operation_type': op.operation_type,
                'label': op_labels.get(op.operation_type, op.operation_type),
                'description': op.description,
                'username': op.username,
                'user_role': user_role,
                'user_phone': user_phone,
                'location_name': op.location_name,
                'old_data': op.old_data,
                'new_data': op.new_data,
                'amount': float(op.amount) if op.amount else None,
                'created_at': op.created_at.strftime('%d.%m.%Y %H:%M') if op.created_at else None,
                '_sort_key': op.created_at if op.created_at else __import__('datetime').datetime.min,
            })

        # 6. SaleItem dan to'g'ridan-to'g'ri qidirish (operations_history da yozilmagan eski savdolar)
        covered_sale_ids = set()
        for op in ops:
            # table_name='sales' da record_id = sale_id
            if op.table_name == 'sales':
                covered_sale_ids.add(op.record_id)
            # new_data da sale_id bo'lishi mumkin
            if op.new_data and isinstance(op.new_data, dict):
                sid = op.new_data.get('sale_id')
                if sid:
                    covered_sale_ids.add(int(sid))
            # description da "Savdo #252" formatida
            if op.description:
                m = _re.search(r'Savdo #(\d+)', op.description)
                if m:
                    covered_sale_ids.add(int(m.group(1)))

        all_sale_items = SaleItem.query.filter_by(product_id=product_id).all()
        for si in all_sale_items:
            if si.sale_id in covered_sale_ids:
                continue
            sale = Sale.query.get(si.sale_id)
            if not sale or sale.payment_status not in ('paid', 'debt'):
                continue
            # Joylashuv
            loc_name = ''
            if si.source_type == 'store' and si.source_id:
                s_obj = Store.query.get(si.source_id)
                loc_name = s_obj.name if s_obj else ''
            elif si.source_type == 'warehouse' and si.source_id:
                w_obj = Warehouse.query.get(si.source_id)
                loc_name = w_obj.name if w_obj else ''
            elif sale.location_id and sale.location_type:
                if sale.location_type == 'store':
                    s_obj = Store.query.get(sale.location_id)
                    loc_name = s_obj.name if s_obj else ''
                else:
                    w_obj = Warehouse.query.get(sale.location_id)
                    loc_name = w_obj.name if w_obj else ''
            # Sotuvchi
            seller = sale.seller
            si_user_role = None
            si_user_phone = None
            si_uname = None
            if seller:
                si_uname = f"{seller.first_name} {seller.last_name}".strip()
                si_user_role = seller.get_role_display() if hasattr(seller, 'get_role_display') else seller.role
                si_user_phone = seller.phone
            # Valyuta kursi
            rate = float(sale.currency_rate) if sale.currency_rate else 1
            sort_dt = sale.sale_date if sale.sale_date else __import__('datetime').datetime.min
            result.append({
                'operation_type': 'sale',
                'label': 'ğŸ›’ Sotish',
                'description': f"Sotildi: {product.name} - {float(si.quantity):.0f} ta Ã— ${float(si.unit_price):.2f} (Savdo #{si.sale_id})",
                'username': si_uname,
                'user_role': si_user_role,
                'user_phone': si_user_phone,
                'location_name': loc_name,
                'old_data': None,
                'new_data': {'sale_id': si.sale_id, 'quantity': float(si.quantity), 'unit_price': float(si.unit_price)},
                'amount': float(si.total_price) * rate if si.total_price else None,
                'created_at': sale.sale_date.strftime('%d.%m.%Y %H:%M') if sale.sale_date else None,
                '_sort_key': sort_dt,
            })
            covered_sale_ids.add(si.sale_id)

        # Sana bo'yicha tartiblash (yangi â†’ eski)
        result.sort(key=lambda x: x.get('_sort_key', __import__('datetime').datetime.min), reverse=True)
        # _sort_key ni javobdan olib tashlash
        for r in result:
            r.pop('_sort_key', None)

        return jsonify({'success': True, 'product_name': product.name, 'operations': result})
    except Exception as e:
        logger.error(f"Product operations xatosi: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/check_stock')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
            # Temporary: Kassir, omborchi va eski sotuvchilarga ham ruxsat berish (migration uchun)
            if current_user.role in ('kassir', 'omborchi'):
                logger.info(f" Allowing {current_user.role} {current_user.username} temporary access to stock check")
            else:
                abort(403)  # Qoldiqni tekshirish huquqi yo'q

        # Sotuvchi uchun qo'shimcha sozlamani tekshirish (omborchi uchun emas)
        if current_user.role == 'sotuvchi':
            setting = Settings.query.filter_by(key='stock_check_visible').first()
            if setting and setting.value.lower() == 'false':
                abort(403)  # Sahifa yashirilgan

    return render_template('check_stock.html')


@app.route('/api/check_stock_locations')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
            # Oddiy foydalanuvchilar faqat stock_check_locations dan ruxsat etilgan joylashuvlarni ko'radi
            # Agar stock_check_locations bo'sh bo'lsa, allowed_locations dan fallback
            stock_check_locs = current_user.stock_check_locations or []
            if not stock_check_locs:
                stock_check_locs = current_user.allowed_locations or []
            logger.debug(f"ğŸ“ User stock_check_locations: {stock_check_locs}")

            # Helper funksiya bilan ID'larni olish (eski va yangi formatlar uchun)
            allowed_store_ids = extract_location_ids(stock_check_locs, 'store')
            allowed_warehouse_ids = extract_location_ids(stock_check_locs, 'warehouse')

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

        # Faol tekshiruvlar bor joylashuvlarni olish (in_progress ham - yakunlash jarayonida)
        active_sessions = StockCheckSession.query.filter(
            StockCheckSession.status.in_(['active', 'in_progress'])
        ).all()
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def api_check_stock_active_sessions():
    """Joriy (active) tekshiruv sessiyalarini olish - faqat ruxsat etilgan joylashuvlar"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Unauthorized'}), 401

        logger.debug(f"ğŸ” Active Sessions - User: {current_user.username}, Role: {current_user.role}")

        # Faol sessiyalarni olish (in_progress ham - yakunlash jarayonida)
        sessions = StockCheckSession.query.filter(
            StockCheckSession.status.in_(['active', 'in_progress'])
        ).order_by(StockCheckSession.started_at.desc()).all()

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
            # quantity > 0 filtri yo'q - 0 miqdorda bo'lgan mahsulotlar ham tekshirilishi kerak
            if check_session.location_type == 'warehouse':
                total_products = WarehouseStock.query.filter(
                    WarehouseStock.warehouse_id == check_session.location_id
                ).count()
            else:  # store
                total_products = StoreStock.query.filter(
                    StoreStock.store_id == check_session.location_id
                ).count()

            # Progress foizini hisoblash (max 100% da ko'rsatiladi)
            progress_percent = 0
            if total_products > 0:
                progress_percent = min(round((checked_items_count / total_products) * 100, 1), 100.0)

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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
                'id': check_session.id,
                'location_name': check_session.location_name,
                'location_type': check_session.location_type,
                'started_user_name': f"{check_session.user.first_name} {check_session.user.last_name}" if check_session.user else 'N/A',
                'completed_user_name': f"{check_session.completed_by.first_name} {check_session.completed_by.last_name}" if check_session.completed_by else (f"{check_session.user.first_name} {check_session.user.last_name}" if check_session.user else 'N/A'),
                'started_at': check_session.started_at.strftime('%d.%m.%Y %H:%M') if check_session.started_at else '',
                'updated_at': check_session.updated_at.strftime('%d.%m.%Y %H:%M') if check_session.updated_at else '',
                'completed_at': check_session.completed_at.strftime('%d.%m.%Y %H:%M') if check_session.completed_at else '',
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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

        # M4 fix: Foydalanuvchi bu joylashuvga ruxsati borligini tekshirish (admin uchun o'tkazib yuboriladi)
        if current_user.role != 'admin':
            stock_check_locs = current_user.stock_check_locations or []
            if not stock_check_locs:
                stock_check_locs = current_user.allowed_locations or []
            allowed_ids = extract_location_ids(stock_check_locs, location_type)
            if allowed_ids is not None and int(location_id) not in allowed_ids:
                logger.warning(f"â›” User {current_user.username} tried to start stock check for unauthorized location: {location_type}#{location_id}")
                return jsonify({'success': False, 'message': 'Bu joylashuv uchun ruxsatingiz yo\'q'}), 403

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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
        session_obj = StockCheckSession.query.get(session_id)
        if not session_obj:
            return jsonify({'success': False, 'message': 'Sessiya topilmadi'}), 404

        # Ruxsat tekshirish: admin va kassir har qanday sessiyani yakunlay oladi.
        # Sotuvchi esa o'sha joylashuvga stock_check ruxsati bo'lsa yakunlay oladi
        # (user_id emas, joylashuv ruxsatiga qarab â€” sessiyani boshqasi boshlagan bo'lsa ham)
        if current_user.role not in ('admin', 'kassir'):
            stock_check_locs = current_user.stock_check_locations or []
            if not stock_check_locs:
                stock_check_locs = current_user.allowed_locations or []
            allowed_ids = extract_location_ids(stock_check_locs, session_obj.location_type)
            if allowed_ids is not None and int(session_obj.location_id) not in allowed_ids:
                logger.warning(
                    f"â›” Unauthorized finish attempt: user={current_user.username} "
                    f"has no permission for {session_obj.location_type}#{session_obj.location_id}"
                )
                return jsonify({'success': False, 'message': 'Bu joylashuv uchun tekshiruv yakunlash ruxsatingiz yo\'q'}), 403

        # Idempotency: agar sessiya allaqachon yakunlangan bo'lsa, darhol success qaytarish
        # (internet uzilsa va foydalanuvchi qayta bosganda ham xato bo'lmaydi)
        if session_obj.status == 'completed':
            logger.info(f"â„¹ï¸ Session {session_id} already completed, returning success (idempotent)")
            return jsonify({
                'success': True,
                'message': 'Tekshiruv allaqachon yakunlangan.',
                'updated_count': 0,
                'errors': []
            })

        # Race condition himoyasi: DB darajasida atomik holda 'active'/'in_progress' -> 'in_progress' qilish
        # 'in_progress' ham qabul qilinadi: tarmoq uzilsa sessiya shu holatda qotib qolishi mumkin,
        # shu sababli sessiya egasi qayta urinishda sessiyasini yakunlay olishi kerak.
        # Bulk UPDATE idempotent bo'lgani uchun bir necha marta bajarilsa ham xavfsiz.
        lock_result = db.session.execute(text("""
            UPDATE stock_check_sessions
            SET status = 'in_progress'
            WHERE id = :session_id AND status IN ('active', 'in_progress')
        """), {'session_id': session_id})
        db.session.flush()

        if lock_result.rowcount == 0:
            # Sessiya completed yoki boshqa noma'lum holatda
            db.session.refresh(session_obj)
            if session_obj.status == 'completed':
                return jsonify({
                    'success': True,
                    'message': 'Tekshiruv allaqachon yakunlangan.',
                    'updated_count': 0,
                    'errors': []
                })
            logger.warning(f"âš ï¸ Session {session_id} lock failed, status={session_obj.status}")
            return jsonify({'success': False, 'message': 'Sessiya topilmadi yoki yakunlanib bo\'lgan'}), 409

        logger.info(f"ğŸ”’ Session {session_id} locked for finalization by user={current_user.username}")

        # Bulk UPDATE â€” bitta SQL so'rov bilan barcha stokni yangilash (N+1 muammosi yo'q)
        if session_obj.location_type == 'store':
            result = db.session.execute(text("""
                UPDATE store_stocks ss
                SET quantity = sci.actual_quantity
                FROM stock_check_items sci
                WHERE sci.session_id = :session_id
                  AND sci.actual_quantity IS NOT NULL
                  AND ss.store_id = :location_id
                  AND ss.product_id = sci.product_id
            """), {
                'session_id': session_id,
                'location_id': session_obj.location_id
            })
            updated_count = result.rowcount

        elif session_obj.location_type == 'warehouse':
            result = db.session.execute(text("""
                UPDATE warehouse_stocks ws
                SET quantity = sci.actual_quantity
                FROM stock_check_items sci
                WHERE sci.session_id = :session_id
                  AND sci.actual_quantity IS NOT NULL
                  AND ws.warehouse_id = :location_id
                  AND ws.product_id = sci.product_id
            """), {
                'session_id': session_id,
                'location_id': session_obj.location_id
            })
            updated_count = result.rowcount

        else:
            updated_count = 0

        # Sessiyani yakunlash
        session_obj.status = 'completed'
        session_obj.completed_by_user_id = current_user.id
        db.session.commit()

        logger.info(f"âœ… Check stock finished: session_id={session_id}, user={current_user.username}, "
                    f"updated={updated_count} products (bulk SQL)")

        message = f'Tekshiruv yakunlandi. {updated_count} ta mahsulot yangilandi.'

        return jsonify({
            'success': True,
            'message': message,
            'updated_count': updated_count,
            'errors': []
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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


@app.route('/api/stores/create', methods=['POST'])
@role_required('admin')
def api_create_store():
    """Do'kon qo'shish (JSON API - modal uchun)"""
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        address = (data.get('address') or '').strip()
        manager_name = (data.get('manager_name') or '').strip()
        phone = (data.get('phone') or '').strip()

        if not name or not address or not manager_name:
            return jsonify({'success': False, 'error': 'Nom, manzil va menejer ismi majburiy'}), 400

        new_store = Store(name=name, address=address, manager_name=manager_name, phone=phone)
        db.session.add(new_store)
        db.session.commit()

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

        return jsonify({'success': True, 'store_id': new_store.id})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Do'kon yaratish xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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

    return redirect(url_for('stores'))


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
        category_id = request.args.get('category_id', type=int)

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

        # Category filter
        if category_id:
            if not search:  # join faqat bir marta bo'lsin
                query = query.join(Product)
            query = query.filter(Product.category_id == category_id)

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
            min_stock = stock.product.min_stock

            if stock.quantity == 0:
                item_status = 'critical'
                critical_stock_count += 1
            elif min_stock > 0 and stock.quantity <= min_stock:
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
                        'min_stock': min_stock,
                        'sell_price': float(stock.product.sell_price),
                        'last_batch_cost': float(stock.product.last_batch_cost) if stock.product.last_batch_cost else None,
                        'last_batch_date': stock.product.last_batch_date.isoformat() if stock.product.last_batch_date else None,
                        'image_url': f'/static/uploads/products/{stock.product.image_path}' if stock.product.image_path else None,
                        'category_id': stock.product.category_id
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


@app.route('/api/store/<int:store_id>/stock/export', methods=['GET'])
def api_store_stock_export(store_id):
    """Export ALL store stocks as JSON for Excel download (no pagination)"""
    try:
        Store.query.get_or_404(store_id)

        search = request.args.get('search', '', type=str).strip()
        status = request.args.get('status', '', type=str).strip()

        query = StoreStock.query.filter_by(store_id=store_id)

        if search:
            query = query.join(Product)
            for word in search.lower().split():
                if word:
                    query = query.filter(
                        db.or_(
                            Product.name.ilike(f'%{word}%'),
                            Product.barcode.ilike(f'%{word}%')
                        )
                    )

        stocks = query.all()
        stock_info = []

        for stock in stocks:
            unit_profit = stock.product.sell_price - stock.product.cost_price
            min_stock = stock.product.min_stock

            if stock.quantity == 0:
                item_status = 'critical'
            elif min_stock > 0 and stock.quantity <= min_stock:
                item_status = 'low'
            else:
                item_status = 'normal'

            if status and item_status != status:
                continue

            profit_percentage = 0
            if stock.product.cost_price > 0:
                profit_percentage = (float(unit_profit) / float(stock.product.cost_price)) * 100

            stock_info.append({
                'barcode': stock.product.barcode or '',
                'name': stock.product.name,
                'quantity': float(stock.quantity),
                'unit_type': stock.product.unit_type or 'dona',
                'last_batch_cost': float(stock.product.last_batch_cost) if stock.product.last_batch_cost else 0.0,
                'cost_price': float(stock.product.cost_price),
                'sell_price': float(stock.product.sell_price),
                'unit_profit': float(unit_profit),
                'profit_percentage': round(profit_percentage, 1),
                'status': item_status
            })

        return jsonify({'success': True, 'data': stock_info, 'total': len(stock_info)})

    except Exception as e:
        app.logger.error(f"Error exporting store stock: {str(e)}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/stores/<int:store_id>/edit', methods=['POST'])
@role_required('admin')
def api_edit_store(store_id):
    """Do'kon tahrirlash (JSON API - modal uchun)"""
    try:
        store = Store.query.get_or_404(store_id)
        data = request.get_json()

        name = (data.get('name') or '').strip()
        address = (data.get('address') or '').strip()
        manager_name = (data.get('manager_name') or '').strip()
        phone = (data.get('phone') or '').strip()

        if not name or not address or not manager_name:
            return jsonify({'success': False, 'error': 'Nom, manzil va menejer ismi majburiy'}), 400

        old_data = {'name': store.name, 'address': store.address, 'manager': store.manager_name, 'phone': store.phone}
        store.name = name
        store.address = address
        store.manager_name = manager_name
        store.phone = phone
        db.session.commit()

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

        return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Do'kon tahrirlash xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


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

    return redirect(url_for('stores'))


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
        category_id = request.args.get('category_id', type=int)

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

        # Category filter
        if category_id:
            if not search:  # join faqat bir marta bo'lsin
                query = query.join(Product)
            query = query.filter(Product.category_id == category_id)

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
            min_stock = stock.product.min_stock

            if stock.quantity == 0:
                item_status = 'critical'
                critical_stock_count += 1
            elif min_stock > 0 and stock.quantity <= min_stock:
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
                        'min_stock': min_stock,
                        'last_batch_cost': float(stock.product.last_batch_cost) if stock.product.last_batch_cost else None,
                        'last_batch_date': stock.product.last_batch_date.isoformat() if stock.product.last_batch_date else None,
                        'image_url': f'/static/uploads/products/{stock.product.image_path}' if stock.product.image_path else None,
                        'category_id': stock.product.category_id
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


@app.route('/api/warehouses/create', methods=['POST'])
@role_required('admin')
def api_create_warehouse():
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        address = (data.get('address') or '').strip()
        manager_name = (data.get('manager_name') or '').strip()
        phone = (data.get('phone') or '').strip()
        if not name or not address or not manager_name:
            return jsonify({'success': False, 'error': 'Barcha majburiy maydonlarni to\'ldiring'}), 400
        new_warehouse = Warehouse(name=name, address=address, manager_name=manager_name, phone=phone)
        db.session.add(new_warehouse)
        db.session.commit()
        try:
            history = OperationHistory(
                operation_type='create_warehouse', table_name='warehouses',
                record_id=new_warehouse.id, user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f'Yangi ombor yaratildi: {name}',
                old_data=None,
                new_data={'name': name, 'address': address, 'manager': manager_name, 'phone': phone},
                ip_address=request.remote_addr,
                location_id=new_warehouse.id, location_type='warehouse', location_name=name, amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f'OperationHistory log xatoligi: {log_error}')
        return jsonify({'success': True, 'warehouse_id': new_warehouse.id})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/warehouses/<int:warehouse_id>/edit', methods=['POST'])
@role_required('admin')
def api_edit_warehouse(warehouse_id):
    warehouse = Warehouse.query.get_or_404(warehouse_id)
    try:
        data = request.get_json()
        name = (data.get('name') or '').strip()
        address = (data.get('address') or '').strip()
        manager_name = (data.get('manager_name') or '').strip()
        phone = (data.get('phone') or '').strip()
        if not name or not address or not manager_name:
            return jsonify({'success': False, 'error': 'Barcha majburiy maydonlarni to\'ldiring'}), 400
        old_data = {'name': warehouse.name, 'address': warehouse.address,
                    'manager': warehouse.manager_name, 'phone': warehouse.phone}
        warehouse.name = name
        warehouse.address = address
        warehouse.manager_name = manager_name
        warehouse.phone = phone
        db.session.commit()
        try:
            history = OperationHistory(
                operation_type='edit_warehouse', table_name='warehouses',
                record_id=warehouse.id, user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f'Ombor tahrirlandi: {warehouse.name}',
                old_data=old_data,
                new_data={'name': warehouse.name, 'address': warehouse.address,
                          'manager': warehouse.manager_name, 'phone': warehouse.phone},
                ip_address=request.remote_addr,
                location_id=warehouse.id, location_type='warehouse', location_name=warehouse.name, amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_error:
            logger.error(f'OperationHistory log xatoligi: {log_error}')
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


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

    return redirect(url_for('warehouses'))


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

    return redirect(url_for('warehouses'))


@app.route('/api/warehouses')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
        location_type = request.args.get('location_type', type=str, default='store')

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

            # Warehouse tanlansa, customers jadvalida warehouse bog'liq maydon yo'q
            if location_type == 'warehouse':
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
                    COALESCE(SUM(s.debt_amount), 0) as remaining_debt_uzs,
                    c.last_debt_payment_date as last_payment_date,
                    COALESCE(c.last_debt_payment_usd, 0) as last_payment_amount,
                    COALESCE(c.last_debt_payment_rate, 13000) as last_payment_rate,
                    COALESCE(c.last_debt_payment_uzs, 0) as last_payment_uzs,
                    MIN(s.payment_due_date) as nearest_due_date,
                    MAX(s.sale_date) as last_sale_date
                FROM customers c
                LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
                WHERE c.store_id = :location_id
                GROUP BY c.id, c.name, c.phone, c.address, c.last_debt_payment_date, c.last_debt_payment_usd, c.last_debt_payment_rate, c.last_debt_payment_uzs
                HAVING COALESCE(SUM(s.debt_usd), 0) > 0
                ORDER BY GREATEST(COALESCE(MAX(s.sale_date), '1970-01-01'), COALESCE(c.last_debt_payment_date, '1970-01-01')) DESC
            """)
            result = db.session.execute(query, {'location_id': location_id})
        else:
            # Location tanlanmagan - barcha ruxsat etilgan locationlar
            if allowed_location_ids is not None:
                # Admin bo'lmagan user - faqat ruxsat etilgan store locationlar
                if not allowed_location_ids:
                    # Hech qanday location'ga ruxsat yo'q
                    return jsonify({'success': True, 'debts': [], 'exchange_rate': exchange_rate})

                # Faqat store ID larini olish (customers.store_id faqat stores ga reference)
                allowed_store_ids = extract_location_ids(
                    current_user.allowed_locations or [], 'store')
                if not allowed_store_ids:
                    # Faqat warehouse larga ruxsat bor, customers warehouse ga tegishli emas
                    return jsonify({'success': True, 'debts': [], 'exchange_rate': exchange_rate})

                logger.info(f"ğŸ” Debts query store_ids: {allowed_store_ids}")

                from sqlalchemy import bindparam
                query = text("""
                    SELECT
                        c.id as customer_id,
                        c.name as customer_name,
                        c.phone as customer_phone,
                        c.address as customer_address,
                        COALESCE(SUM(s.debt_usd), 0) as total_debt,
                        0 as paid_amount,
                        COALESCE(SUM(s.debt_usd), 0) as remaining_debt,
                        COALESCE(SUM(s.debt_amount), 0) as remaining_debt_uzs,
                        c.last_debt_payment_date as last_payment_date,
                        COALESCE(c.last_debt_payment_usd, 0) as last_payment_amount,
                        COALESCE(c.last_debt_payment_rate, 13000) as last_payment_rate,
                        COALESCE(c.last_debt_payment_uzs, 0) as last_payment_uzs,
                        MIN(s.payment_due_date) as nearest_due_date,
                        MAX(s.sale_date) as last_sale_date
                    FROM customers c
                    LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
                    WHERE c.store_id IN :store_ids
                    GROUP BY c.id, c.name, c.phone, c.address, c.last_debt_payment_date, c.last_debt_payment_usd, c.last_debt_payment_rate, c.last_debt_payment_uzs
                    HAVING COALESCE(SUM(s.debt_usd), 0) > 0
                    ORDER BY GREATEST(COALESCE(MAX(s.sale_date), '1970-01-01'), COALESCE(c.last_debt_payment_date, '1970-01-01')) DESC
                """).bindparams(bindparam('store_ids', expanding=True))
                result = db.session.execute(query, {'store_ids': allowed_store_ids})
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
                        COALESCE(SUM(s.debt_amount), 0) as remaining_debt_uzs,
                        c.last_debt_payment_date as last_payment_date,
                        COALESCE(c.last_debt_payment_usd, 0) as last_payment_amount,
                        COALESCE(c.last_debt_payment_rate, 13000) as last_payment_rate,
                        COALESCE(c.last_debt_payment_uzs, 0) as last_payment_uzs,
                        MIN(s.payment_due_date) as nearest_due_date,
                        MAX(s.sale_date) as last_sale_date
                    FROM customers c
                    LEFT JOIN sales s ON c.id = s.customer_id AND s.debt_usd > 0
                    GROUP BY c.id, c.name, c.phone, c.address, c.last_debt_payment_date, c.last_debt_payment_usd, c.last_debt_payment_rate, c.last_debt_payment_uzs
                    HAVING COALESCE(SUM(s.debt_usd), 0) > 0
                    ORDER BY GREATEST(COALESCE(MAX(s.sale_date), '1970-01-01'), COALESCE(c.last_debt_payment_date, '1970-01-01')) DESC
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
                'remaining_debt_uzs': float(row.remaining_debt_uzs) if row.remaining_debt_uzs else 0,
                'last_payment_date': row.last_payment_date.strftime('%Y-%m-%d %H:%M') if row.last_payment_date else None,
                'last_payment_amount': float(row.last_payment_amount) if row.last_payment_amount else 0,
                'last_payment_rate': float(row.last_payment_rate) if row.last_payment_rate else 13000,
                'last_payment_uzs': float(row.last_payment_uzs) if row.last_payment_uzs else 0,
                'nearest_due_date': row.nearest_due_date.strftime('%Y-%m-%d') if row.nearest_due_date else None
            })

        # Har bir mijozning qarzli savdolarini alohida olish (sale_due_dates)
        if debts:
            customer_ids = [d['customer_id'] for d in debts]

            # sale_due_dates: barcha joylashuvlardagi qarz savdolar (location filtersiz)
            sales_query = text("""
                SELECT s.id as sale_id, s.customer_id, s.debt_usd, s.payment_due_date, s.created_at
                FROM sales s
                WHERE s.customer_id = ANY(:customer_ids)
                  AND s.debt_usd > 0
                ORDER BY s.payment_due_date ASC NULLS LAST, s.created_at DESC
            """)
            sales_result = db.session.execute(sales_query, {'customer_ids': customer_ids})

            # customer_id bo'yicha guruhlash
            from collections import defaultdict
            sales_by_customer = defaultdict(list)
            for srow in sales_result:
                sales_by_customer[srow.customer_id].append({
                    'sale_id': srow.sale_id,
                    'debt_usd': float(srow.debt_usd),
                    'payment_due_date': srow.payment_due_date.strftime('%Y-%m-%d') if srow.payment_due_date else None,
                    'sale_date': srow.created_at.strftime('%d.%m.%Y') if srow.created_at else None
                })

            # Har bir debtga sale_due_dates qo'shish
            for debt in debts:
                debt['sale_due_dates'] = sales_by_customer.get(debt['customer_id'], [])

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


@app.route('/api/low-stock-alerts')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi', 'ombor_xodimi')
def api_low_stock_alerts():
    """Qoldig'i kam mahsulotlar ro'yxati (min_stock dan past)"""
    try:
        query = text("""
            SELECT p.id, p.name, p.unit_type,
                   COALESCE((
                       SELECT SUM(ws.quantity) FROM warehouse_stocks ws WHERE ws.product_id = p.id
                   ), 0) +
                   COALESCE((
                       SELECT SUM(ss.quantity) FROM store_stocks ss WHERE ss.product_id = p.id
                   ), 0) AS total_qty,
                   p.min_stock
            FROM products p
            WHERE p.is_active = TRUE
              AND (
                  COALESCE((SELECT SUM(ws.quantity) FROM warehouse_stocks ws WHERE ws.product_id = p.id), 0) +
                  COALESCE((SELECT SUM(ss.quantity) FROM store_stocks ss WHERE ss.product_id = p.id), 0)
              ) < p.min_stock
            ORDER BY total_qty ASC
            LIMIT 20
        """)
        result = db.session.execute(query)
        items = []
        for row in result:
            items.append({
                'id': row.id,
                'name': row.name,
                'unit_type': row.unit_type or 'dona',
                'quantity': float(row.total_qty),
                'min_stock': row.min_stock
            })
        return jsonify({'success': True, 'items': items})
    except Exception as e:
        app.logger.error(f"Low stock alerts xatosi: {str(e)}")
        return jsonify({'success': True, 'items': []})


@app.route('/api/notifications/dismiss', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi', 'ombor_xodimi')
def dismiss_notifications():
    """Bildirishnomalarni o'qildi deb belgilash â€” Flask session ga saqlanadi"""
    import time
    session['notif_dismissed_at'] = int(time.time() * 1000)  # ms timestamp
    session.modified = True
    return jsonify({'success': True})


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
        location_type = request.args.get('location_type', type=str, default='store')

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

            # Warehouse tanlansa, customers da warehouse_id maydoni yo'q
            if location_type == 'warehouse':
                return jsonify({'success': True, 'paid_debts': []})

            query = text("""
                SELECT
                    s.id as sale_id,
                    MAX(dp.payment_date) as payment_date,
                    s.created_at as sale_date,
                    c.name as customer_name,
                    s.total_amount as total_amount,
                    COALESCE(s.cash_usd, 0) as cash_usd,
                    COALESCE(s.click_usd, 0) as click_usd,
                    COALESCE(s.terminal_usd, 0) as terminal_usd
                FROM sales s
                JOIN customers c ON s.customer_id = c.id
                JOIN debt_payments dp ON dp.sale_id = s.id
                WHERE s.payment_status = 'paid'
                    AND s.debt_usd = 0
                    AND s.location_id = :location_id
                    AND s.location_type = 'store'
                GROUP BY s.id, c.name, s.created_at, s.total_amount, s.cash_usd, s.click_usd, s.terminal_usd
                ORDER BY MAX(dp.payment_date) DESC
                LIMIT 200
            """)
            result = db.session.execute(query, {'location_id': location_id})
        else:
            # Allowed locations bo'yicha filtrlash
            if allowed_location_ids is not None:
                if not allowed_location_ids:
                    return jsonify({'success': True, 'paid_debts': []})

                # Faqat store ID larini olish
                allowed_store_ids = extract_location_ids(
                    current_user.allowed_locations or [], 'store')
                if not allowed_store_ids:
                    return jsonify({'success': True, 'paid_debts': []})

                from sqlalchemy import bindparam
                query = text("""
                SELECT
                    s.id as sale_id,
                    MAX(dp.payment_date) as payment_date,
                    s.created_at as sale_date,
                    c.name as customer_name,
                    s.total_amount as total_amount,
                    COALESCE(s.cash_usd, 0) as cash_usd,
                    COALESCE(s.click_usd, 0) as click_usd,
                    COALESCE(s.terminal_usd, 0) as terminal_usd
                FROM sales s
                JOIN customers c ON s.customer_id = c.id
                JOIN debt_payments dp ON dp.sale_id = s.id
                WHERE s.payment_status = 'paid'
                    AND s.debt_usd = 0
                    AND s.location_id IN :store_ids
                    AND s.location_type = 'store'
                GROUP BY s.id, c.name, s.created_at, s.total_amount, s.cash_usd, s.click_usd, s.terminal_usd
                ORDER BY MAX(dp.payment_date) DESC
                LIMIT 200
            """).bindparams(bindparam('store_ids', expanding=True))
                result = db.session.execute(query, {'store_ids': allowed_store_ids})
            else:
                # Admin - barcha qarzlarni ko'radi
                query = text("""
                SELECT
                    s.id as sale_id,
                    MAX(dp.payment_date) as payment_date,
                    s.created_at as sale_date,
                    c.name as customer_name,
                    s.total_amount as total_amount,
                    COALESCE(s.cash_usd, 0) as cash_usd,
                    COALESCE(s.click_usd, 0) as click_usd,
                    COALESCE(s.terminal_usd, 0) as terminal_usd
                FROM sales s
                JOIN customers c ON s.customer_id = c.id
                JOIN debt_payments dp ON dp.sale_id = s.id
                WHERE s.payment_status = 'paid'
                    AND s.debt_usd = 0
                GROUP BY s.id, c.name, s.created_at, s.total_amount, s.cash_usd, s.click_usd, s.terminal_usd
                ORDER BY MAX(dp.payment_date) DESC
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
    """Qarz to'lovlar tarixi - to'lovlar tranzaksiya bo'yicha guruhlangan"""
    try:
        # Joriy foydalanuvchini olish
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        # Location filter parametri
        location_id = request.args.get('location_id', type=int)

        # Sotuvchi uchun ruxsat berilgan do'konlar bo'yicha filter
        allowed_store_ids = None
        if current_user.role == 'sotuvchi':
            allowed_locations = current_user.allowed_locations or []
            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            if not allowed_store_ids:
                return jsonify({'success': True, 'payments': []})

        # Raw SQL bilan tranzaksiya bo'yicha guruhlash
        location_filter = ""
        params = {}

        if allowed_store_ids is not None:
            # Sotuvchi: faqat o'z do'konlaridagi mijozlarning to'lovlari
            location_filter = "AND c.store_id = ANY(:store_ids)"
            params['store_ids'] = allowed_store_ids
        elif location_id:
            location_filter = "AND (s.location_id = :location_id OR dp.sale_id IS NULL)"
            params['location_id'] = location_id

        sql = text(f"""
            SELECT
                dp.customer_id,
                c.name AS customer_name,
                dp.payment_date,
                dp.received_by,
                SUM(dp.cash_usd) AS cash_usd,
                SUM(dp.click_usd) AS click_usd,
                SUM(dp.terminal_usd) AS terminal_usd,
                SUM(dp.total_usd) AS total_usd,
                MAX(dp.currency_rate) AS currency_rate,
                MAX(dp.notes) AS notes
            FROM debt_payments dp
            JOIN customers c ON dp.customer_id = c.id
            LEFT JOIN sales s ON dp.sale_id = s.id
            WHERE 1=1
            {location_filter}
            GROUP BY dp.customer_id, c.name, dp.payment_date, dp.received_by
            ORDER BY dp.payment_date DESC
        """)

        result = db.session.execute(sql, params)

        payments = []
        for row in result:
            payments.append({
                'customer_id': row.customer_id,
                'customer_name': row.customer_name or 'Unknown',
                'payment_date': row.payment_date.strftime('%Y-%m-%d %H:%M') if row.payment_date else None,
                'received_by': row.received_by or '',
                'cash_usd': float(row.cash_usd or 0),
                'click_usd': float(row.click_usd or 0),
                'terminal_usd': float(row.terminal_usd or 0),
                'total_usd': float(row.total_usd or 0),
                'currency_rate': float(row.currency_rate) if row.currency_rate else 0,
                'notes': row.notes or ''
            })

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
    """Mijozning batafsil qarz ma'lumotlari - qarz savdolari bilan"""
    try:
        # Mijoz ma'lumotlari
        customer = Customer.query.get_or_404(customer_id)

        # Faqat qarz mavjud savdolarni olish (pending emas) - barcha lokatsiyalar
        debt_sales = Sale.query.filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0,
            Sale.payment_status != 'pending'
        ).order_by(Sale.created_at.desc()).all()

        history = []
        total_debt = Decimal('0')

        for sale in debt_sales:
            # Savdo mahsulotlari
            items_info = []
            for item in sale.items:
                prod_name = 'Mahsulot'
                if item.product:
                    prod_name = item.product.name
                elif hasattr(item, 'product_name_snapshot') and item.product_name_snapshot:
                    prod_name = item.product_name_snapshot
                items_info.append({
                    'product_name': prod_name,
                    'quantity': float(item.quantity),
                    'unit_price': float(item.unit_price),
                    'total_price': float(item.total_price)
                })

            total_sale = float(sale.cash_usd or 0) + float(sale.click_usd or 0) + float(sale.terminal_usd or 0) + float(sale.debt_usd or 0)
            paid = float(sale.cash_usd or 0) + float(sale.click_usd or 0) + float(sale.terminal_usd or 0)

            history.append({
                'sale_id': sale.id,
                'sale_date': sale.created_at.strftime('%Y-%m-%d %H:%M') if sale.created_at else '',
                'total_usd': total_sale,
                'paid_usd': paid,
                'debt_usd': float(sale.debt_usd or 0),
                'payment_due_date': sale.payment_due_date.strftime('%Y-%m-%d') if sale.payment_due_date else None,
                'payment_status': sale.payment_status,
                'items': items_info,
                'items_text': ', '.join([f"{i['product_name']} ({i['quantity']:.0f})" for i in items_info]),
                # Eski format uchun backward compatibility
                'debt_amount': total_sale,
                'paid_amount': paid,
                'remaining': float(sale.debt_usd or 0)
            })

            total_debt += Decimal(str(sale.debt_usd or 0))

        remaining_debt = total_debt

        return jsonify({
            'success': True,
            'customer': {
                'id': customer.id,
                'name': customer.name,
                'phone': customer.phone,
                'address': customer.address
            },
            'total_debt': float(total_debt),
            'total_paid': 0,
            'remaining_debt': float(remaining_debt),
            'history': history
        })

    except Exception as e:
        app.logger.error(f"Qarz tafsilotlari API xatosi: {str(e)}")
        import traceback
        app.logger.error(traceback.format_exc())
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/api/debts/update-due-date', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_update_due_date():
    """Mijozning qarzli savdolari uchun payment_due_date yangilash (har bir savdo alohida)"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        sales_updates = data.get('sales', [])

        if not customer_id:
            return jsonify({'success': False, 'error': 'customer_id talab qilinadi'}), 400

        if not sales_updates:
            return jsonify({'success': False, 'error': 'sales massivi talab qilinadi'}), 400

        from datetime import datetime as dt_parse

        updated_count = 0
        for sale_update in sales_updates:
            sale_id = sale_update.get('sale_id')
            due_date_str = sale_update.get('payment_due_date')

            if not sale_id:
                continue

            # Parse date
            due_date = None
            if due_date_str:
                due_date = dt_parse.strptime(due_date_str, '%Y-%m-%d').date()

            # Faqat shu mijozning savdosini yangilash (xavfsizlik uchun)
            updated = Sale.query.filter(
                Sale.id == sale_id,
                Sale.customer_id == customer_id,
                Sale.debt_usd > 0
            ).update({'payment_due_date': due_date}, synchronize_session=False)

            updated_count += updated

        db.session.commit()

        return jsonify({
            'success': True,
            'updated_sales': updated_count
        })

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


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

        # Mijozning qarzli savdolarini topish (pending bo'lmaganlar)
        sales = Sale.query.filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0,
            Sale.payment_status != 'pending'
        ).order_by(Sale.created_at.asc()).all()

        if not sales:
            return jsonify({
                'success': False,
                'error': 'Qarzli savdolar topilmadi'
            }), 404

        remaining_payment = payment_usd
        total_uzs_paid = Decimal('0')  # Jami UZS to'lov (aniq hisoblash uchun)
        updated_sales = []
        sale_payment_records = []  # Har bir savdo uchun to'lov ma'lumotlari

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

            # Savdo kursi â€” _amount ustunlarni UZS da saqlash uchun
            sale_rate = sale.currency_rate or Decimal('12000')

            # Har bir to'lov turidan qancha ishlatish mumkin
            # 1. Naqd puldan
            cash_for_this = min(remaining_cash, payment_for_this_sale)
            if cash_for_this > 0:
                sale.cash_usd = (sale.cash_usd or Decimal('0')) + cash_for_this
                sale.cash_amount = sale.cash_usd * sale_rate  # UZS da saqlaymiz
                remaining_cash -= cash_for_this
                payment_for_this_sale -= cash_for_this

            # 2. Click dan
            click_for_this = min(remaining_click, payment_for_this_sale)
            if click_for_this > 0:
                sale.click_usd = (sale.click_usd or Decimal('0')) + click_for_this
                sale.click_amount = sale.click_usd * sale_rate  # UZS da saqlaymiz
                remaining_click -= click_for_this
                payment_for_this_sale -= click_for_this

            # 3. Terminal dan
            terminal_for_this = min(remaining_terminal, payment_for_this_sale)
            if terminal_for_this > 0:
                sale.terminal_usd = (sale.terminal_usd or Decimal('0')) + terminal_for_this
                sale.terminal_amount = sale.terminal_usd * sale_rate  # UZS da saqlaymiz
                remaining_terminal -= terminal_for_this
                payment_for_this_sale -= terminal_for_this

            # Jami to'langan summa
            total_paid = cash_for_this + click_for_this + terminal_for_this

            # Qarzni kamaytirish â€” agar to'liq to'langan bo'lsa, float xatosini oldini olish uchun
            # ayirish o'rniga to'g'ridan-to'g'ri 0 qo'yamiz
            if total_paid >= current_debt:
                sale.debt_usd = Decimal('0')
                sale.debt_amount = Decimal('0')
            else:
                new_debt = sale.debt_usd - total_paid
                # Mikro-qoldiqni nolga tenglashtirish (0.001 dan kichik bo'lsa)
                if new_debt < Decimal('0.001'):
                    new_debt = Decimal('0')
                sale.debt_usd = new_debt
                sale.debt_amount = new_debt * sale_rate  # UZS da saqlaymiz

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
            total_uzs_paid += total_paid * sale_rate  # Har bir savdo o'z kursi bilan
            updated_sales.append(sale.id)
            sale_payment_records.append({
                'sale_id': sale.id,
                'cash_usd': cash_for_this,
                'click_usd': click_for_this,
                'terminal_usd': terminal_for_this,
                'total_usd': total_paid
            })

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
                customer.last_debt_payment_uzs = 0
                customer.last_debt_payment_date = None
                customer.last_debt_payment_rate = 0
            else:
                # Agar hali qarz qolgan bo'lsa, oxirgi to'lov ma'lumotlarini yangilash
                customer.last_debt_payment_usd = payment_usd - remaining_payment
                customer.last_debt_payment_uzs = total_uzs_paid  # Aniq UZS (har savdo o'z kursi bilan)
                customer.last_debt_payment_date = db.func.current_timestamp()
                customer.last_debt_payment_rate = get_current_currency_rate()

            # Ortiqcha to'lov balansga qo'shiladi
            if remaining_payment > 0:
                old_balance = Decimal(str(customer.balance or 0))
                customer.balance = old_balance + remaining_payment
                logger.info(f"ğŸ’³ Mijoz #{customer_id} balansiga ${remaining_payment} qo'shildi (yangi balans: ${customer.balance})")

        # Qarz to'lovi tarixiga har bir yangilangan savdo uchun alohida yozuv qo'shish
        actual_paid = payment_usd - remaining_payment
        current_rate_dp = get_current_currency_rate()
        payment_time_dp = get_tashkent_time()
        notes_dp = (f"{len(updated_sales)} ta savdoning qarziga to'lov qilindi" +
                    (f", ${remaining_payment} balansga o'tkazildi" if remaining_payment > 0 else ""))

        debt_payment = None
        for record in sale_payment_records:
            dp = DebtPayment(
                customer_id=customer_id,
                sale_id=record['sale_id'],
                payment_date=payment_time_dp,
                cash_usd=record['cash_usd'],
                click_usd=record['click_usd'],
                terminal_usd=record['terminal_usd'],
                total_usd=record['total_usd'],
                currency_rate=current_rate_dp,
                received_by=session.get('user_name', 'Unknown'),
                notes=notes_dp
            )
            db.session.add(dp)
            if debt_payment is None:
                debt_payment = dp  # OperationHistory uchun birinchi yozuv

        # Agar hech qanday savdo yangilanmagan bo'lsa (to'liq ortiqcha to'lov)
        if debt_payment is None:
            debt_payment = DebtPayment(
                customer_id=customer_id,
                sale_id=None,
                payment_date=payment_time_dp,
                cash_usd=cash_usd,
                click_usd=click_usd,
                terminal_usd=terminal_usd,
                total_usd=actual_paid,
                currency_rate=current_rate_dp,
                received_by=session.get('user_name', 'Unknown'),
                notes=notes_dp
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
            'paid_amount': float(payment_usd - remaining_payment),
            'balance_added': float(remaining_payment),
            'new_balance': float(customer.balance) if customer else 0
        })

    except Exception as e:
        db.session.rollback()
        app.logger.error(f"Qarzga to'lov xatosi: {str(e)}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500


@app.route('/edit-user/<int:user_id>')
@role_required('admin', 'kassir')
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
def load_user_language():
    """Har requestda foydalanuvchi tilini bazadan yuklash - Samsung/mobil brauzerlar uchun"""
    try:
        user_id = session.get('user_id')
        if user_id:
            # Static fayllar uchun tekshirmash
            if request.endpoint and request.endpoint == 'static':
                return
            user_lang = Settings.query.filter_by(key=f'user_language_{user_id}').first()
            if user_lang and user_lang.value in ('uz_latin', 'uz_cyrillic', 'ru'):
                if session.get('language') != user_lang.value:
                    logger.info(f"ğŸŒ Til tuzatildi: session={session.get('language')} -> DB={user_lang.value} (user_id={user_id})")
                    session['language'] = user_lang.value
                    session.modified = True
    except Exception as e:
        logger.error(f"ğŸŒ Til yuklashda xato: {e}")


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

            # P3 fix: Har so'rovda DB ga bormaslik - 60 soniyalik cache
            import time as _time
            _now = _time.time()
            _last_checked = session.get('_session_checked_at', 0)
            if _now - _last_checked < 60:
                return  # 60 soniya ichida allaqachon tekshirilgan
            session['_session_checked_at'] = _now

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
                _wh_diff = float(new_quantity) - float(old_quantity)
                if _wh_diff > 0:
                    _wh_qty_str = f"{float(new_quantity):.0f} ({float(old_quantity):.0f}+{_wh_diff:.0f}={float(new_quantity):.0f})"
                elif _wh_diff < 0:
                    _wh_qty_str = f"{float(new_quantity):.0f} ({float(old_quantity):.0f}{_wh_diff:.0f}={float(new_quantity):.0f})"
                else:
                    _wh_qty_str = f"{float(new_quantity):.0f} (o'zgarmadi)"
                history = OperationHistory(
                    operation_type='edit_stock',
                    table_name='warehouse_stock',
                    record_id=stock.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"{new_product_name} tahrirlandi: miqdor {_wh_qty_str} ${new_sell_price:.2f}",
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
                _st_diff = float(new_quantity) - float(old_quantity)
                if _st_diff > 0:
                    _st_qty_str = f"{float(new_quantity):.0f} ({float(old_quantity):.0f}+{_st_diff:.0f}={float(new_quantity):.0f})"
                elif _st_diff < 0:
                    _st_qty_str = f"{float(new_quantity):.0f} ({float(old_quantity):.0f}{_st_diff:.0f}={float(new_quantity):.0f})"
                else:
                    _st_qty_str = f"{float(new_quantity):.0f} (o'zgarmadi)"
                history = OperationHistory(
                    operation_type='edit_stock',
                    table_name='store_stock',
                    record_id=stock.id,
                    user_id=session.get('user_id'),
                    username=session.get('username', 'Unknown'),
                    description=f"{new_product_name} tahrirlandi: miqdor {_st_qty_str} ${new_sell_price:.2f}",
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


@app.route('/api/edit_store_stock/<int:store_id>/<int:product_id>', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
@location_permission_required('store_id')
def api_edit_store_stock(store_id, product_id):
    """Modal orqali do'kon stokini tahrirlash (JSON API)"""
    try:
        stock = StoreStock.query.filter_by(
            store_id=store_id, product_id=product_id).first_or_404()

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Ma\'lumot topilmadi'}), 400

        new_product_name = data.get('productName', '').strip()
        new_barcode = data.get('barcode', '').strip()
        new_quantity = int(float(data.get('quantity', 0)))
        new_min_stock = int(float(data.get('minStock', 0)))
        new_cost_price = float(data.get('costPrice', 0))
        new_sell_price = float(data.get('sellPrice', 0))
        new_category_id = data.get('categoryId')

        if not new_product_name:
            return jsonify({'success': False, 'error': 'Mahsulot nomi bo\'sh bo\'lishi mumkin emas'}), 400
        if new_quantity < 0:
            return jsonify({'success': False, 'error': 'Miqdor manfiy bo\'lishi mumkin emas'}), 400
        if new_cost_price < 0 or new_sell_price < 0:
            return jsonify({'success': False, 'error': 'Narxlar manfiy bo\'lishi mumkin emas'}), 400
        if new_sell_price < new_cost_price:
            return jsonify({'success': False, 'error': 'Sotish narxi tan narxidan past bo\'lishi mumkin emas'}), 400

        if new_barcode:
            existing = Product.query.filter(
                Product.barcode == new_barcode,
                Product.id != product_id
            ).first()
            if existing:
                return jsonify({'success': False, 'error': f'{new_barcode} barcode allaqachon "{existing.name}" mahsulotida mavjud'}), 400

        old_quantity = stock.quantity
        cost_price = stock.product.cost_price
        sell_price = stock.product.sell_price

        stock.product.name = new_product_name
        stock.product.barcode = new_barcode if new_barcode else None
        stock.product.min_stock = new_min_stock
        stock.product.cost_price = Decimal(str(new_cost_price))
        stock.product.sell_price = Decimal(str(new_sell_price))
        stock.product.category_id = int(new_category_id) if new_category_id else None
        stock.quantity = new_quantity
        db.session.commit()

        try:
            store = Store.query.get(store_id)
            _diff = float(new_quantity) - float(old_quantity)
            qty_str = f"{float(new_quantity):.0f} ({float(old_quantity):.0f}{'+' if _diff >= 0 else ''}{_diff:.0f}={float(new_quantity):.0f})"
            history = OperationHistory(
                operation_type='edit_stock', table_name='store_stock',
                record_id=stock.id, user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{new_product_name} tahrirlandi: miqdor {qty_str} ${new_sell_price:.2f}",
                old_data={'quantity': str(old_quantity), 'cost_price': str(cost_price), 'sell_price': str(sell_price)},
                new_data={'quantity': str(new_quantity), 'cost_price': str(new_cost_price), 'sell_price': str(new_sell_price)},
                ip_address=request.remote_addr, location_id=store_id,
                location_type='store', location_name=store.name if store else 'Unknown', amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_err:
            logger.error(f"OperationHistory log xatoligi: {log_err}")

        return jsonify({'success': True, 'message': f'{new_product_name} muvaffaqiyatli yangilandi'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/edit_warehouse_stock/<int:warehouse_id>/<int:product_id>', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def api_edit_warehouse_stock(warehouse_id, product_id):
    """Modal orqali ombor stokini tahrirlash (JSON API)"""
    try:
        stock = WarehouseStock.query.filter_by(
            warehouse_id=warehouse_id, product_id=product_id).first_or_404()

        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'error': 'Ma\'lumot topilmadi'}), 400

        new_product_name = data.get('productName', '').strip()
        new_barcode = data.get('barcode', '').strip()
        new_quantity = int(float(data.get('quantity', 0)))
        new_min_stock = int(float(data.get('minStock', 0)))
        new_cost_price = float(data.get('costPrice', 0))
        new_sell_price = float(data.get('sellPrice', 0))
        new_category_id = data.get('categoryId')

        if not new_product_name:
            return jsonify({'success': False, 'error': 'Mahsulot nomi bo\'sh bo\'lishi mumkin emas'}), 400
        if new_quantity < 0:
            return jsonify({'success': False, 'error': 'Miqdor manfiy bo\'lishi mumkin emas'}), 400
        if new_cost_price < 0 or new_sell_price < 0:
            return jsonify({'success': False, 'error': 'Narxlar manfiy bo\'lishi mumkin emas'}), 400
        if new_sell_price < new_cost_price:
            return jsonify({'success': False, 'error': 'Sotish narxi tan narxidan past bo\'lishi mumkin emas'}), 400

        if new_barcode:
            existing = Product.query.filter(
                Product.barcode == new_barcode,
                Product.id != product_id
            ).first()
            if existing:
                return jsonify({'success': False, 'error': f'{new_barcode} barcode allaqachon "{existing.name}" mahsulotida mavjud'}), 400

        old_quantity = stock.quantity
        cost_price = stock.product.cost_price
        sell_price = stock.product.sell_price

        stock.product.name = new_product_name
        stock.product.barcode = new_barcode if new_barcode else None
        stock.product.min_stock = new_min_stock
        stock.product.cost_price = Decimal(str(new_cost_price))
        stock.product.sell_price = Decimal(str(new_sell_price))
        stock.product.category_id = int(new_category_id) if new_category_id else None
        stock.quantity = new_quantity
        db.session.commit()

        try:
            warehouse = Warehouse.query.get(warehouse_id)
            _diff = float(new_quantity) - float(old_quantity)
            qty_str = f"{float(new_quantity):.0f} ({float(old_quantity):.0f}{'+' if _diff >= 0 else ''}{_diff:.0f}={float(new_quantity):.0f})"
            history = OperationHistory(
                operation_type='edit_stock', table_name='warehouse_stock',
                record_id=stock.id, user_id=session.get('user_id'),
                username=session.get('username', 'Unknown'),
                description=f"{new_product_name} tahrirlandi: miqdor {qty_str} ${new_sell_price:.2f}",
                old_data={'quantity': str(old_quantity), 'cost_price': str(cost_price), 'sell_price': str(sell_price)},
                new_data={'quantity': str(new_quantity), 'cost_price': str(new_cost_price), 'sell_price': str(new_sell_price)},
                ip_address=request.remote_addr, location_id=warehouse_id,
                location_type='warehouse', location_name=warehouse.name if warehouse else 'Unknown', amount=None
            )
            db.session.add(history)
            db.session.commit()
        except Exception as log_err:
            logger.error(f"OperationHistory log xatoligi: {log_err}")

        return jsonify({'success': True, 'message': f'{new_product_name} muvaffaqiyatli yangilandi'})

    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


# Faqat store stock miqdorini yangilash (stock checking uchun)
@app.route('/api/update_store_stock/<int:store_id>/<int:product_id>',
           methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
            AND s.status IN ('active', 'in_progress')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
            WHERE s.status IN ('active', 'in_progress')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def end_stock_check():
    """Qoldiq tekshirish sessionini tugatish"""
    try:
        data = request.get_json()
        location_id = data.get('location_id')
        location_type = data.get('location_type')
        status = data.get('status', 'completed')  # 'completed' or 'cancelled'

        if status not in ['completed', 'cancelled']:
            return jsonify({'error': 'Noto\'g\'ri status'}), 400

        current_user_id = session.get('user_id')

        # Sessiyani tugatish - completed_by_user_id va completed_at maydonlarini ham yangilash
        db.session.execute(text("""
            UPDATE stock_check_sessions
            SET status = :status,
                updated_at = NOW(),
                completed_at = NOW(),
                completed_by_user_id = :completed_by_user_id
            WHERE user_id = :user_id
            AND location_id = :location_id
            AND location_type = :location_type
            AND status = 'active'
        """), {
            'user_id': current_user_id,
            'location_id': location_id,
            'location_type': location_type,
            'status': status,
            'completed_by_user_id': current_user_id
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
            WHERE status IN ('active', 'in_progress')
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
@role_required('admin')
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
@role_required('admin')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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

        # Sotuvchi va omborchi uchun transfer huquqi va joylashuv tekshirish
        if current_user.role in ('sotuvchi', 'omborchi'):
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

            # Sotuvchi va omborchi uchun from_location ruxsatini tekshirish
            if current_user.role in ('sotuvchi', 'omborchi'):
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
                                print(f"âœ… Transfer permission granted: {from_type}_{from_location_id} matches {loc}")
                                break
                        # Eski format: integer (faqat id, type noma'lum)
                        elif isinstance(loc, int):
                            if loc == from_location_id:
                                has_permission = True
                                print(f"âœ… Transfer permission granted (old format): location ID {from_location_id}")
                                break

                    if not has_permission:
                        print(
                            f"âŒ User {current_user.username} cannot transfer from location {from_location} (type: {from_type}, id: {from_location_id})")
                        print(f"âŒ Available transfer locations: {transfer_locations}")
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
            before_from_qty = 0
            if from_location.startswith('store_'):
                store_id = int(from_location.replace('store_', ''))
                store_stock = StoreStock.query.filter_by(
                    store_id=store_id,
                    product_id=product_id
                ).first()

                if not store_stock or store_stock.quantity < quantity:
                    return jsonify(
                        {'error': 'Do\'konda yetarli miqdor yo\'q'}), 400

                before_from_qty = float(store_stock.quantity)
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

                before_from_qty = float(warehouse_stock.quantity)
                warehouse_stock.quantity -= quantity
                # Miqdor 0 bo'lsa ham stockni saqlab qolamiz (o'chirmaymiz)

            # To location ga miqdorni qo'shish
            before_to_qty = 0
            if to_location.startswith('store_'):
                store_id = int(to_location.replace('store_', ''))
                store_stock = StoreStock.query.filter_by(
                    store_id=store_id,
                    product_id=product_id
                ).first()

                if store_stock:
                    before_to_qty = float(store_stock.quantity)
                    store_stock.quantity += quantity
                else:
                    # Yangi stock yaratish (StoreStock da cost_price va
                    # sell_price yo'q)
                    before_to_qty = 0
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
                    before_to_qty = float(warehouse_stock.quantity)
                    warehouse_stock.quantity += quantity
                else:
                    # Yangi stock yaratish (WarehouseStock da cost_price va
                    # sell_price yo'q)
                    before_to_qty = 0
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

            _qty = float(quantity)
            _from_after = before_from_qty - _qty
            _to_after = before_to_qty + _qty
            transfer_desc = (
                f"Transfer: {product.name} - "
                f"{from_location_name} {before_from_qty:.0f}-{_qty:.0f}={_from_after:.0f}"
                f" â†’ {to_location_name} {before_to_qty:.0f}+{_qty:.0f}={_to_after:.0f}"
            )
            operation = OperationHistory(
                operation_type='transfer',
                table_name='transfers',
                record_id=transfer_record.id,
                user_id=session.get('user_id'),
                username=session.get('username') or 'Admin',
                description=transfer_desc,
                old_data={
                    'from_location': from_location_name,
                    'from_location_type': from_type,
                    'from_qty_before': before_from_qty,
                    'to_qty_before': before_to_qty
                },
                new_data={
                    'product_id': product_id,
                    'product_name': product.name,
                    'quantity': float(quantity),
                    'to_location': to_location_name,
                    'to_location_type': to_type,
                    'from_qty_after': before_from_qty - float(quantity),
                    'to_qty_after': before_to_qty + float(quantity)
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def get_transfer_history_formatted():
    """Transfer tarixini formatlangan ko'rinishda qaytarish"""
    try:
        limit = request.args.get('limit', 50, type=int)
        limit = min(limit, 200)  # Maximum 200

        # So'nggi transferlarni olish
        transfers = Transfer.query.order_by(
            Transfer.created_at.desc()
        ).limit(limit * 10).all()  # Ko'proq olish, keyin guruhlash

        # M7 fix: N+1 oldini olish - barcha store/warehouse/product IDlarini bir marta yuklaymiz
        warehouse_ids = {t.from_location_id for t in transfers if t.from_location_type == 'warehouse'} | \
                        {t.to_location_id for t in transfers if t.to_location_type == 'warehouse'}
        store_ids = {t.from_location_id for t in transfers if t.from_location_type == 'store'} | \
                    {t.to_location_id for t in transfers if t.to_location_type == 'store'}
        product_ids = {t.product_id for t in transfers}

        warehouses_map = {w.id: w.name for w in Warehouse.query.filter(Warehouse.id.in_(warehouse_ids)).all()} if warehouse_ids else {}
        stores_map = {s.id: s.name for s in Store.query.filter(Store.id.in_(store_ids)).all()} if store_ids else {}
        products_map = {p.id: p.name for p in Product.query.filter(Product.id.in_(product_ids)).all()} if product_ids else {}

        # Transferlarni guruhlash - bir xil vaqt, from_location, to_location, user
        grouped_transfers = {}

        for transfer in transfers:
            # Joylashuv nomlarini map dan olish (DB ga bormasdan)
            from_location_name = "N/A"
            to_location_name = "N/A"

            if transfer.from_location_type == 'warehouse':
                from_location_name = warehouses_map.get(transfer.from_location_id, f"Ombor #{transfer.from_location_id}")
            elif transfer.from_location_type == 'store':
                from_location_name = stores_map.get(transfer.from_location_id, f"Dokon #{transfer.from_location_id}")

            if transfer.to_location_type == 'warehouse':
                to_location_name = warehouses_map.get(transfer.to_location_id, f"Ombor #{transfer.to_location_id}")
            elif transfer.to_location_type == 'store':
                to_location_name = stores_map.get(transfer.to_location_id, f"Dokon #{transfer.to_location_id}")

            # Mahsulot nomini map dan olish
            product_name = products_map.get(transfer.product_id, f"Mahsulot #{transfer.product_id}")

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
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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


@app.route('/api/pending-product-batch', methods=['GET', 'POST'])
@app.route('/api/pending-product-batch/<int:batch_id>', methods=['GET', 'PUT', 'DELETE'])
@role_required('admin', 'kassir', 'omborchi')
def manage_pending_product_batch(batch_id=None):
    """Tugallanmagan mahsulot qo'shish sessiyalarini boshqarish"""
    try:
        current_user = get_current_user()
        if not current_user:
            return jsonify({'error': 'Foydalanuvchi topilmadi'}), 401

        if request.method == 'GET':
            if batch_id:
                batch = PendingProductBatch.query.get(batch_id)
                if not batch:
                    return jsonify({'error': 'Sessiya topilmadi'}), 404
                if batch.user_id != current_user.id and current_user.role != 'admin':
                    return jsonify({'error': 'Ruxsat yo\'q'}), 403
                return jsonify({'success': True, 'batch': batch.to_dict()})
            else:
                # Foydalanuvchining barcha pending batchlarini qaytarish
                if current_user.role == 'admin':
                    batches = PendingProductBatch.query.order_by(PendingProductBatch.updated_at.desc()).all()
                else:
                    batches = PendingProductBatch.query.filter_by(user_id=current_user.id).order_by(PendingProductBatch.updated_at.desc()).all()
                return jsonify({'success': True, 'batches': [b.to_dict() for b in batches]})

        elif request.method == 'POST':
            data = request.get_json()
            batch = PendingProductBatch(
                user_id=current_user.id,
                items=data.get('items', [])
            )
            db.session.add(batch)
            db.session.commit()
            return jsonify({'success': True, 'batch': batch.to_dict()})

        elif request.method == 'PUT':
            if not batch_id:
                return jsonify({'error': 'Batch ID talab qilinadi'}), 400
            batch = PendingProductBatch.query.get(batch_id)
            if not batch:
                return jsonify({'error': 'Sessiya topilmadi'}), 404
            if batch.user_id != current_user.id and current_user.role != 'admin':
                return jsonify({'error': 'Ruxsat yo\'q'}), 403
            data = request.get_json()
            batch.items = data.get('items', batch.items)
            db.session.commit()
            return jsonify({'success': True, 'batch': batch.to_dict()})

        elif request.method == 'DELETE':
            if not batch_id:
                return jsonify({'error': 'Batch ID talab qilinadi'}), 400
            batch = PendingProductBatch.query.get(batch_id)
            if not batch:
                return jsonify({'error': 'Sessiya topilmadi'}), 404
            if batch.user_id != current_user.id and current_user.role != 'admin':
                return jsonify({'error': 'Ruxsat yo\'q'}), 403
            db.session.delete(batch)
            db.session.commit()
            return jsonify({'success': True})

    except Exception as e:
        db.session.rollback()
        logger.error(f"PendingProductBatch xatoligi: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/all-pending-transfers', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
                'from_location_id': pending.from_location_id,
                'from_location_type': pending.from_location_type,
                'to_location': to_location_name,
                'to_location_id': pending.to_location_id,
                'to_location_type': pending.to_location_type,
                'items': pending.items,
                'created_at': pending.created_at.isoformat() if pending.created_at else None,
                'updated_at': pending.updated_at.isoformat() if pending.updated_at else None,
                'can_manage': True  # Backend allaqachon user_can_manage_transfer bilan filterlaydi
            })

        return jsonify({
            'success': True,
            'pending_transfers': result
        })

    except Exception as e:
        logger.error(f"Tasdiqlanmagan transferlarni olishda xatolik: {str(e)}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/product/<int:product_id>', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
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
@role_required('admin')
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

        # P4 fix: N+1 oldini olish - barcha mijozlar uchun savdo yig'indilarini 1 ta query bilan olamiz
        from sqlalchemy import func as _func
        customer_ids = [c.id for c in customers]
        if customer_ids:
            sales_agg_query = db.session.query(
                Sale.customer_id,
                _func.count(Sale.id).label('total_sales'),
                _func.sum(Sale.total_amount).label('total_amount'),
                _func.sum(Sale.total_profit).label('total_profit'),
                _func.max(Sale.sale_date).label('last_sale_date')
            ).filter(Sale.customer_id.in_(customer_ids))

            if start_date:
                sales_agg_query = sales_agg_query.filter(Sale.sale_date >= start_date)
            if end_date:
                sales_agg_query = sales_agg_query.filter(Sale.sale_date <= end_date)

            sales_agg = {row.customer_id: row for row in sales_agg_query.group_by(Sale.customer_id).all()}

            # last_sale_date uchun filtrsiz alohida query (vaqt filtri qo'llanmagan holda)
            if time_filter != 'all':
                last_sale_rows = db.session.query(
                    Sale.customer_id,
                    _func.max(Sale.sale_date).label('last_sale_date')
                ).filter(Sale.customer_id.in_(customer_ids)).group_by(Sale.customer_id).all()
                last_sale_map = {row.customer_id: row.last_sale_date for row in last_sale_rows}
            else:
                last_sale_map = {cid: (sales_agg[cid].last_sale_date if cid in sales_agg else None) for cid in customer_ids}
        else:
            sales_agg = {}
            last_sale_map = {}

        for customer in customers:
            customer_dict = customer.to_dict()
            agg = sales_agg.get(customer.id)

            total_sales = int(agg.total_sales) if agg else 0
            total_amount = round(Decimal(str(agg.total_amount or 0)), 2) if agg else Decimal('0')
            total_profit = round(Decimal(str(agg.total_profit or 0)), 2) if agg else Decimal('0')
            last_sale_dt = last_sale_map.get(customer.id)

            customer_dict['total_sales'] = total_sales
            customer_dict['total_amount'] = float(total_amount)
            customer_dict['total_profit'] = float(total_profit)
            customer_dict['last_sale_date'] = last_sale_dt.strftime('%d.%m.%Y') if last_sale_dt else None

            # Vaqt filtri qo'llangan bo'lsa va savdo bo'lmasa - o'tkazib yuborish
            if time_filter != 'all' and total_sales == 0:
                continue

            result.append(customer_dict)

        logger.debug(f" Returning {len(result)} customers with sales data")
        print(f"ğŸ“Š Jami {len(result)} ta mijoz qaytarilmoqda")

        # Oxirgi savdo sanasiga ko'ra tartiblash (yangi savdo tepada, savdosizlar pastda)
        from datetime import datetime as _dt
        result.sort(
            key=lambda x: _dt.strptime(x['last_sale_date'], '%d.%m.%Y') if x.get('last_sale_date') else _dt.min,
            reverse=True
        )

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

        # Telefon raqami unikligini tekshirish (faqat shu dokon ichida)
        phone = data.get('phone', '').strip()
        store_id = data.get('store_id')
        if phone and store_id:
            existing = Customer.query.filter(
                Customer.phone == phone,
                Customer.store_id == store_id
            ).first()
            if existing:
                return jsonify({
                    'error': f'Bu telefon raqam ({phone}) allaqachon "{existing.name}" mijozida ro\'yxatdan o\'tgan'
                }), 400
        elif phone and not store_id:
            existing = Customer.query.filter(
                Customer.phone == phone,
                Customer.store_id == None
            ).first()
            if existing:
                return jsonify({
                    'error': f'Bu telefon raqam ({phone}) allaqachon "{existing.name}" mijozida ro\'yxatdan o\'tgan'
                }), 400

        # Store_id ni data'dan olish
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
                # extract_location_ids bilan eski va yangi formatlarni qo'llab-quvvatlash
                allowed_store_ids = extract_location_ids(allowed_locations, 'store')
                print(
                    f"ğŸ” Customer API - Checking if {store_id_int} in allowed_store_ids={allowed_store_ids} (raw: {allowed_locations})")
                if store_id_int not in allowed_store_ids:
                    print(
                        f"âŒ Customer API - Store {store_id_int} not in allowed store ids {allowed_store_ids}")
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
@role_required('admin', 'kassir')
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


@app.route('/api/customers/<int:customer_id>', methods=['PUT'])
@role_required('admin', 'kassir', 'sotuvchi')
def update_customer(customer_id):
    try:
        customer = Customer.query.get_or_404(customer_id)
        data = request.get_json()

        if not data or not data.get('name'):
            return jsonify({'error': 'Mijoz nomi talab qilinadi'}), 400

        # Telefon raqami unikligini tekshirish (o'zini hisobga olmasdan)
        phone = data.get('phone', '').strip()
        if phone:
            existing = Customer.query.filter(
                Customer.phone == phone,
                Customer.id != customer_id
            ).first()
            if existing:
                return jsonify({
                    'error': f'Bu telefon raqam ({phone}) allaqachon "{existing.name}" mijozida ro\'yxatdan o\'tgan'
                }), 400

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

        # Telefon raqam unikalligini tekshirish
        if data.get('phone'):
            clean_new = ''.join(filter(str.isdigit, data['phone']))
            if len(clean_new) >= 9:
                for u in User.query.filter(User.phone != None, User.phone != '').all():
                    if ''.join(filter(str.isdigit, u.phone))[-9:] == clean_new[-9:]:
                        return jsonify({'error': f'Bu telefon raqam allaqachon {u.username} foydalanuvchisiga biriktirilgan'}), 400

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

        logger.debug(f" Permissions: {permissions}")
        logger.debug(f" Stock check locations: {stock_check_locations}")
        logger.debug(f" Allowed locations: {allowed_locations}")
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
            stock_check_locations=stock_check_locations,
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


@app.route('/api/users/<int:user_id>/photo', methods=['POST'])
@role_required('admin', 'kassir')
def upload_user_photo(user_id):
    """Foydalanuvchi rasmini yuklash"""
    try:
        user = User.query.get_or_404(user_id)
        if 'photo' not in request.files:
            return jsonify({'error': 'Rasm fayli topilmadi'}), 400
        file = request.files['photo']
        if file.filename == '':
            return jsonify({'error': 'Fayl tanlanmagan'}), 400
        allowed_ext = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
        ext = file.filename.rsplit('.', 1)[-1].lower() if '.' in file.filename else ''
        if ext not in allowed_ext:
            return jsonify({'error': 'Faqat jpg, jpeg, png, gif, webp formatlari qabul qilinadi'}), 400
        filename = f"{user_id}.{ext}"
        # Eski rasmni o'chirish (boshqa extension bo'lsa)
        for old_ext in allowed_ext:
            old_path = os.path.join(app.config['UPLOAD_FOLDER'], f"{user_id}.{old_ext}")
            if os.path.exists(old_path):
                os.remove(old_path)
        file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
        user.photo = filename
        db.session.commit()
        # Agar joriy foydalanuvchi o'z rasmini yuklayotgan bo'lsa session'ni yangilash
        if session.get('user_id') == user_id:
            session['user_photo'] = f'/static/uploads/users/{filename}'
        return jsonify({'success': True, 'photo': f'/static/uploads/users/{filename}'}), 200
    except Exception as e:
        db.session.rollback()
        return jsonify({'error': str(e)}), 500


@app.route('/api/users/<int:user_id>/photo', methods=['DELETE'])
@role_required('admin', 'kassir')
def delete_user_photo(user_id):
    """Foydalanuvchi rasmini o'chirish"""
    try:
        user = User.query.get_or_404(user_id)
        allowed_ext = {'jpg', 'jpeg', 'png', 'gif', 'webp'}
        for ext in allowed_ext:
            path = os.path.join(app.config['UPLOAD_FOLDER'], f"{user_id}.{ext}")
            if os.path.exists(path):
                os.remove(path)
        user.photo = None
        db.session.commit()
        if session.get('user_id') == user_id:
            session['user_photo'] = None
        return jsonify({'success': True}), 200
    except Exception as e:
        db.session.rollback()
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
@role_required('admin', 'kassir')
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
            'stock_check_locations': user.stock_check_locations or [],
            'photo': f'/static/uploads/users/{user.photo}' if user.photo else None,
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

        # Telefon raqam unikalligini tekshirish (o'zi bundan mustasno)
        if data.get('phone'):
            clean_new = ''.join(filter(str.isdigit, data['phone']))
            if len(clean_new) >= 9:
                for u in User.query.filter(User.id != user_id, User.phone != None, User.phone != '').all():
                    if ''.join(filter(str.isdigit, u.phone))[-9:] == clean_new[-9:]:
                        return jsonify({'error': f'Bu telefon raqam allaqachon {u.username} foydalanuvchisiga biriktirilgan'}), 400

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

        if permissions:
            logger.debug(f" Updating permissions: {permissions}")
            user.permissions = permissions

        # Har doim alohida saqlash (aralashtirilmaydi)
        user.allowed_locations = allowed_locations
        logger.debug(f" Allowed locations: {allowed_locations}")

        user.transfer_locations = transfer_locations
        logger.debug(f" Transfer locations: {transfer_locations}")

        user.stock_check_locations = stock_check_locations
        logger.debug(f" Stock check locations: {stock_check_locations}")

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
        sale_id_filter = request.args.get('sale_id')  # Savdo ID bo'yicha qidiruv

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
            # Default: barcha tasdiqlangan savdolar (cancelled emas)
            # 'paid' (to'liq to'langan) + 'partial' (nasiya) + 'completed' (eski)
            query = Sale.query.filter(Sale.payment_status.in_(['paid', 'completed', 'partial']))
            logger.info("ğŸ“‹ Filter: paid + completed + partial (default)")

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

        # Savdo ID filtri
        if sale_id_filter:
            try:
                sale_id_int = int(sale_id_filter)
                query = query.filter(Sale.id == sale_id_int)
                print(f"ğŸ”¢ Sale ID filtri: {sale_id_int}")
            except (ValueError, TypeError):
                pass

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

        # Davrdagi qarzli mijozlar soni (debt_usd > 0 bo'lgan unique customerlar)
        debt_customers_count = stats_filtered_query.filter(
            Sale.debt_usd > 0,
            Sale.customer_id != None
        ).with_entities(func.count(func.distinct(Sale.customer_id))).scalar() or 0

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

        # Kirim summasi: osha davr mobaynida joylashuvlarga qo'shilgan mahsulotlar tan narxi
        incoming_q = db.session.query(
            func.sum(ProductAddHistory.cost_price * ProductAddHistory.quantity)
        )
        if stats_date_filter == 'today':
            today = get_tashkent_time().date()
            today_start = datetime.combine(today, datetime.min.time())
            today_end = datetime.combine(today, datetime.max.time())
            incoming_q = incoming_q.filter(
                ProductAddHistory.added_date >= today_start,
                ProductAddHistory.added_date <= today_end
            )
        else:
            if start_date:
                incoming_q = incoming_q.filter(ProductAddHistory.added_date >= start_date)
            if end_date:
                incoming_q = incoming_q.filter(ProductAddHistory.added_date <= end_date + ' 23:59:59')
        # Joylashuv filtri
        if location_filter and location_filter != 'all':
            if location_filter.startswith('store_'):
                _loc_id = int(location_filter.replace('store_', ''))
                _store = Store.query.get(_loc_id)
                if _store:
                    incoming_q = incoming_q.filter(
                        ProductAddHistory.location_name == _store.name,
                        ProductAddHistory.location_type == 'store'
                    )
            elif location_filter.startswith('warehouse_'):
                _loc_id = int(location_filter.replace('warehouse_', ''))
                _wh = Warehouse.query.get(_loc_id)
                if _wh:
                    incoming_q = incoming_q.filter(
                        ProductAddHistory.location_name == _wh.name,
                        ProductAddHistory.location_type == 'warehouse'
                    )
        total_cost = float(incoming_q.scalar() or 0)

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
        ).limit(10)

        for name, quantity, revenue in top_products_query.all():
            top_products.append({
                'name': name or 'Noma\'lum',
                'quantity': float(quantity or 0),
                'revenue': float(revenue or 0)
            })

        # Store + Warehouse performance - location_id/location_type asosida
        store_performance = []
        Warehouse_alias = aliased(Warehouse)
        Store_alias = aliased(Store)

        # Do'konlar (store)
        store_perf_query = db.session.query(
            Store_alias.name,
            func.count(Sale.id).label('sales'),
            func.sum(Sale.total_amount).label('revenue'),
            func.sum(Sale.total_profit).label('profit')
        ).join(
            Store_alias, db.and_(Sale.location_id == Store_alias.id, Sale.location_type == 'store')
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

        # Omborlar (warehouse)
        wh_perf_query = db.session.query(
            Warehouse_alias.name,
            func.count(Sale.id).label('sales'),
            func.sum(Sale.total_amount).label('revenue'),
            func.sum(Sale.total_profit).label('profit')
        ).join(
            Warehouse_alias, db.and_(Sale.location_id == Warehouse_alias.id, Sale.location_type == 'warehouse')
        ).filter(
            Sale.id.in_(select(sale_ids_subquery.c.id))
        ).group_by(
            Warehouse_alias.name
        ).order_by(
            func.sum(Sale.total_amount).desc()
        )
        for name, sales_count, revenue, profit in wh_perf_query.all():
            store_performance.append({
                'name': name or 'Noma\'lum',
                'sales': sales_count,
                'revenue': float(revenue or 0),
                'profit': float(profit or 0)
            })

        # Revenue bo'yicha tartiblash
        store_performance.sort(key=lambda x: x['revenue'], reverse=True)

        # Joylashuv Ã— To'lov usuli kombinatsiya breakdown
        Store_alias2 = aliased(Store)
        Warehouse_alias2 = aliased(Warehouse)
        loc_pm_dict = {}

        store_loc_pm = db.session.query(
            Store_alias2.name,
            func.sum(Sale.cash_usd).label('cash'),
            func.sum(Sale.click_usd).label('click'),
            func.sum(Sale.terminal_usd).label('terminal'),
            func.sum(Sale.debt_usd).label('debt'),
            func.sum(Sale.total_profit).label('profit')
        ).join(
            Store_alias2, db.and_(Sale.location_id == Store_alias2.id, Sale.location_type == 'store')
        ).filter(
            Sale.id.in_(select(sale_ids_subquery.c.id))
        ).group_by(Store_alias2.name).all()

        wh_loc_pm = db.session.query(
            Warehouse_alias2.name,
            func.sum(Sale.cash_usd).label('cash'),
            func.sum(Sale.click_usd).label('click'),
            func.sum(Sale.terminal_usd).label('terminal'),
            func.sum(Sale.debt_usd).label('debt'),
            func.sum(Sale.total_profit).label('profit')
        ).join(
            Warehouse_alias2, db.and_(Sale.location_id == Warehouse_alias2.id, Sale.location_type == 'warehouse')
        ).filter(
            Sale.id.in_(select(sale_ids_subquery.c.id))
        ).group_by(Warehouse_alias2.name).all()

        for name, cash, click, terminal, debt, profit in store_loc_pm:
            loc_name = name or 'Noma\'lum'
            payments = {}
            if float(cash or 0) > 0:
                payments['cash'] = float(cash)
            if float(click or 0) > 0:
                payments['click'] = float(click)
            if float(terminal or 0) > 0:
                payments['terminal'] = float(terminal)
            if float(debt or 0) > 0:
                payments['debt'] = float(debt)
            loc_pm_dict[('store', loc_name)] = {'payments': payments, 'profit': float(profit or 0)}

        for name, cash, click, terminal, debt, profit in wh_loc_pm:
            loc_name = name or 'Noma\'lum'
            payments = {}
            if float(cash or 0) > 0:
                payments['cash'] = float(cash)
            if float(click or 0) > 0:
                payments['click'] = float(click)
            if float(terminal or 0) > 0:
                payments['terminal'] = float(terminal)
            if float(debt or 0) > 0:
                payments['debt'] = float(debt)
            loc_pm_dict[('warehouse', loc_name)] = {'payments': payments, 'profit': float(profit or 0)}

        location_payment_breakdown = [
            {'name': k[1], 'location_type': k[0], 'payments': v['payments'], 'total': sum(v['payments'].values()), 'profit': v['profit']}
            for k, v in loc_pm_dict.items()
        ]
        location_payment_breakdown.sort(key=lambda x: x['total'], reverse=True)

        # Har bir joylashuv uchun xarajatlarni qo'shish
        total_expense_all = 0
        try:
            exp_q = db.session.query(
                Expense.location_type,
                Expense.location_id,
                func.coalesce(func.sum(Expense.amount_usd), 0).label('total_usd')
            ).filter(Expense.location_type.isnot(None), Expense.location_id.isnot(None))
            if start_date and start_date.strip():
                exp_q = exp_q.filter(Expense.expense_date >= start_date)
            if end_date and end_date.strip():
                exp_q = exp_q.filter(Expense.expense_date <= end_date + ' 23:59:59')
            if location_filter and location_filter != 'all':
                if location_filter.startswith('store_'):
                    _loc_id = int(location_filter.replace('store_', ''))
                    exp_q = exp_q.filter(Expense.location_type == 'store', Expense.location_id == _loc_id)
                elif location_filter.startswith('warehouse_'):
                    _loc_id = int(location_filter.replace('warehouse_', ''))
                    exp_q = exp_q.filter(Expense.location_type == 'warehouse', Expense.location_id == _loc_id)
            exp_q = exp_q.group_by(Expense.location_type, Expense.location_id)
            exp_by_loc = {}
            for loc_type, loc_id, total_usd in exp_q.all():
                if loc_type == 'store':
                    obj = Store.query.get(loc_id)
                else:
                    obj = Warehouse.query.get(loc_id)
                if obj:
                    exp_by_loc[(loc_type, obj.name)] = float(total_usd)

            # Savdosi yo'q ammo xarajati bor joylashuvlarni ham qo'shish
            existing_keys = {(item['location_type'], item['name']) for item in location_payment_breakdown}
            for (loc_type, loc_name), exp_amt in exp_by_loc.items():
                if (loc_type, loc_name) not in existing_keys:
                    location_payment_breakdown.append({
                        'name': loc_name,
                        'location_type': loc_type,
                        'payments': {},
                        'total': 0,
                        'profit': 0,
                        'expense': exp_amt
                    })

            for item in location_payment_breakdown:
                item['expense'] = exp_by_loc.get((item['location_type'], item['name']), 0)

            # Barcha xarajatlar jami (joylashuvdan qat'iy nazar)
            all_exp_q = db.session.query(func.coalesce(func.sum(Expense.amount_usd), 0))
            if start_date and start_date.strip():
                all_exp_q = all_exp_q.filter(Expense.expense_date >= start_date)
            if end_date and end_date.strip():
                all_exp_q = all_exp_q.filter(Expense.expense_date <= end_date + ' 23:59:59')
            if location_filter and location_filter != 'all':
                if location_filter.startswith('store_'):
                    _loc_id = int(location_filter.replace('store_', ''))
                    all_exp_q = all_exp_q.filter(Expense.location_type == 'store', Expense.location_id == _loc_id)
                elif location_filter.startswith('warehouse_'):
                    _loc_id = int(location_filter.replace('warehouse_', ''))
                    all_exp_q = all_exp_q.filter(Expense.location_type == 'warehouse', Expense.location_id == _loc_id)
            total_expense_all = float(all_exp_q.scalar() or 0)
        except Exception:
            for item in location_payment_breakdown:
                item['expense'] = 0
            total_expense_all = 0

        # Davrdagi qarzli mijozlar ro'yxati (faqat tanlangan davrda debt_usd > 0 bo'lgan)
        Customer_alias = aliased(Customer)
        debt_customers_query = db.session.query(
            Customer_alias.name,
            func.sum(Sale.debt_usd).label('period_debt')
        ).join(
            Customer_alias, Sale.customer_id == Customer_alias.id
        ).filter(
            Sale.id.in_(select(sale_ids_subquery.c.id)),
            Sale.debt_usd > 0
        ).group_by(
            Customer_alias.name
        ).order_by(
            func.sum(Sale.debt_usd).desc()
        )
        debt_customers = []
        for name, period_debt in debt_customers_query.all():
            debt_customers.append({
                'customer_name': name or 'Noma\'lum',
                'total_debt': float(period_debt or 0)
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
                    'total_cost': round(total_cost, 2),
                    'total_items': total_items,
                    'avg_order_value': round(avg_order_value, 2),
                    'profit_margin': round(profit_margin, 2),
                    'payment_methods': payment_methods,
                    'top_products': top_products,
                    'store_performance': store_performance,
                    'location_payment_breakdown': location_payment_breakdown,
                    'total_expense': round(total_expense_all, 2),
                    'debt_customers_count': debt_customers_count,
                    'debt_customers': debt_customers
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
        balance_used_fin = float(payment.get('balance_used', 0))
        
        # Balansdan foydalanilgan qism alohida saqlanadi (naqd ga qo'shilmaydi)
        cash_usd_fin = float(payment.get('cash_usd', 0))
        
        sale.cash_usd = Decimal(str(cash_usd_fin))
        sale.cash_amount = Decimal(str(payment.get('cash_uzs', 0)))
        sale.click_usd = Decimal(str(payment.get('click_usd', 0)))
        sale.click_amount = Decimal(str(payment.get('click_uzs', 0)))
        sale.terminal_usd = Decimal(str(payment.get('terminal_usd', 0)))
        sale.terminal_amount = Decimal(str(payment.get('terminal_uzs', 0)))
        sale.debt_usd = Decimal(str(payment.get('debt_usd', 0)))
        sale.debt_amount = Decimal(str(payment.get('debt_uzs', 0)))
        sale.balance_usd = Decimal(str(balance_used_fin))

        # Status va boshqa ma'lumotlarni yangilash
        sale.payment_status = payment_status
        sale.currency_rate = Decimal(str(exchange_rate))
        sale.sale_date = get_tashkent_time()  # Tasdiqlash vaqti

        # Qarz to'lash muddatini o'rnatish
        payment_due_date_str = data.get('payment_due_date')
        if payment_due_date_str:
            try:
                sale.payment_due_date = datetime.strptime(payment_due_date_str, '%Y-%m-%d').date()
                logger.info(f"ğŸ“… Finalize: payment_due_date saqlandi: {sale.payment_due_date}")
            except (ValueError, TypeError) as e:
                logger.warning(f"âš ï¸ Finalize: payment_due_date parse xatolik: {e}")
        elif payment_status in ('partial', 'debt') and sale.debt_usd > 0:
            # Qarz bor lekin muddat berilmagan - 7 kun default
            sale.payment_due_date = (get_tashkent_time() + timedelta(days=7)).date()
            logger.info(f"ğŸ“… Finalize: default 7 kun muddat: {sale.payment_due_date}")

        # Mijoz ID ni yangilash (agar kiritilgan bo'lsa)
        if customer_id:
            sale.customer_id = int(customer_id)

        # Mijoz balansidan foydalanilgan summani ayirish
        if balance_used_fin > 0:
            final_cid = int(customer_id) if customer_id else sale.customer_id
            if final_cid:
                fin_customer = Customer.query.get(final_cid)
                if fin_customer:
                    old_bal = Decimal(str(fin_customer.balance or 0))
                    fin_customer.balance = max(Decimal('0'), old_bal - Decimal(str(balance_used_fin)))
                    print(f"ğŸ’³ Finalize: Mijoz balansidan ${balance_used_fin} ayirildi. Yangi balans: ${float(fin_customer.balance)}")

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

                    # To'lov summalari (USD) â€” total = savdoning haqiqiy jami (balance ham kiritilgan)
                    balance_usd_fin = float(sale.balance_usd) if sale.balance_usd else 0
                    balance_uzs_fin = balance_usd_fin * float(sale.currency_rate) if sale.currency_rate else 0
                    total_usd = float(sale.total_amount) if sale.total_amount else 0
                    total_uzs = total_usd * float(sale.currency_rate) if sale.currency_rate else 0
                    paid_usd = float(sale.cash_usd) + float(sale.click_usd) + float(sale.terminal_usd) + balance_usd_fin
                    paid_uzs = float(sale.cash_amount) + float(sale.click_amount) + float(sale.terminal_amount) + balance_uzs_fin

                    # Savdo mahsulotlarini PDF uchun tayyorlash
                    seller_name = f"{sale.seller.first_name} {sale.seller.last_name}" if sale.seller else session.get('username', 'Sotuvchi')
                    seller_phone = format_phone_number(sale.seller.phone) if sale.seller and sale.seller.phone else ''

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
                        customer_phone=format_phone_number(customer.phone) if customer.phone else '',
                        total_amount_usd=total_usd,
                        paid_usd=paid_usd,
                        cash_usd=float(sale.cash_usd),
                        click_usd=float(sale.click_usd),
                        terminal_usd=float(sale.terminal_usd),
                        debt_usd=float(sale.debt_usd),
                        balance_uzs=balance_uzs_fin,
                        balance_usd=balance_usd_fin
                    )
                    logger.info(f"âœ… Telegram xabar va PDF yuborildi (finalize): {customer.name}")
            except Exception as telegram_error:
                logger.warning(f"âš ï¸ Telegram xabar yuborishda xatolik (finalize): {telegram_error}")
                # Telegram xatosi savdoni to'xtatmasin

        # OperationHistory ga har bir SaleItem uchun log yozish
        try:
            current_user_fin = get_current_user()
            fin_loc_name = ''
            if sale.location_type == 'store':
                s_obj = Store.query.get(sale.location_id)
                fin_loc_name = s_obj.name if s_obj else ''
            elif sale.location_type == 'warehouse':
                w_obj = Warehouse.query.get(sale.location_id)
                fin_loc_name = w_obj.name if w_obj else ''

            fin_username = (f"{current_user_fin.first_name} {current_user_fin.last_name}".strip()
                            if current_user_fin else session.get('username', 'System'))
            fin_user_id = current_user_fin.id if current_user_fin else session.get('user_id')

            for si in sale.items:
                p = si.product
                if not p:
                    continue
                fin_op = OperationHistory(
                    operation_type='sale',
                    table_name='products',
                    record_id=si.product_id,
                    user_id=fin_user_id,
                    username=fin_username,
                    description=f"Sotildi: {p.name} - {float(si.quantity):.0f} ta x ${float(si.unit_price):.2f} (Savdo #{sale.id})",
                    old_data=None,
                    new_data={
                        'sale_id': sale.id,
                        'product_id': si.product_id,
                        'quantity': float(si.quantity),
                        'unit_price': float(si.unit_price),
                        'total_price': float(si.total_price),
                    },
                    ip_address=request.remote_addr,
                    location_id=sale.location_id,
                    location_type=sale.location_type,
                    location_name=fin_loc_name,
                    amount=float(si.total_price * (sale.currency_rate or 1))
                )
                db.session.add(fin_op)
            db.session.commit()
            logger.info(f"Finalize savdo #{sale_id} uchun {len(sale.items)} ta OperationHistory yozildi")
        except Exception as log_err:
            logger.warning(f"Finalize OperationHistory log xatoligi: {log_err}")

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
@role_required('admin', 'kassir')
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
@role_required('admin', 'kassir')
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
        balance_used = float(payment_info.get('balance_used', 0))
        
        # Balansdan foydalanilgan summa alohida saqlanadi (cash_usd ga qo'shilmaydi)
        # cash_usd faqat haqiqiy naqd to'lovni ko'rsatadi

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

        # Server tomonida to'lov summasini mahsulotlar jami bilan solishtirish
        items_total_check = sum(
            float(item.get('unit_price') or item.get('price_usd') or item.get('price', 0)) *
            float(item.get('quantity', 0))
            for item in items
        )
        payment_total_check = cash_usd + click_usd + terminal_usd + debt_usd + balance_used
        if items_total_check > 0 and abs(payment_total_check - items_total_check) > 0.05:
            logger.warning(f"âš ï¸ To'lov farqi: to'lov=${payment_total_check:.4f}, mahsulot=${items_total_check:.4f}")
            return jsonify({
                'success': False,
                'error': f"To'lov summasi (${payment_total_check:.2f}) mahsulotlar narxiga (${items_total_check:.2f}) mos emas"
            }), 400

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
        elif balance_used > 0 and cash_usd == 0:
            payment_method = 'balance'
        elif cash_usd > 0:
            payment_method = 'cash'

        print(f"ğŸ’³ Payment method aniqlandi: {payment_method}")

        # Qarz to'lash muddati
        payment_due_date = None
        payment_due_date_str = data.get('payment_due_date')
        print(f"ğŸ“… DEBUG: payment_due_date_str = '{payment_due_date_str}', type = {type(payment_due_date_str)}, debt_usd = {debt_usd}")
        if payment_due_date_str and debt_usd > 0:
            try:
                from datetime import datetime as dt_parse
                payment_due_date = dt_parse.strptime(payment_due_date_str, '%Y-%m-%d').date()
                print(f"ğŸ“… âœ… Qarz to'lash muddati SAQLANDI: {payment_due_date}")
                logger.info(f"ğŸ“… Qarz to'lash muddati: {payment_due_date}")
            except (ValueError, TypeError):
                print(f"ğŸ“… âŒ Noto'g'ri sana formati: {payment_due_date_str}")
                logger.warning(f"âš ï¸ Noto'g'ri sana formati: {payment_due_date_str}")
        else:
            print(f"ğŸ“… âš ï¸ Muddat saqlanMADI: payment_due_date_str={payment_due_date_str}, debt_usd={debt_usd}")

        # _amount ustunlarida UZS qiymatlarini saqlaymiz
        # Agar frontend UZS yuborgan bo'lsa - o'sha ishlatiladi
        # Agar UZS kelmagan bo'lsa - USD * kurs bilan hisoblanadi
        cash_amount = cash_uzs if cash_uzs > 0 else round(cash_usd * current_rate)
        click_amount = click_uzs if click_uzs > 0 else round(click_usd * current_rate)
        terminal_amount = terminal_uzs if terminal_uzs > 0 else round(terminal_usd * current_rate)
        debt_amount = debt_uzs if debt_uzs > 0 else round(debt_usd * current_rate)

        print("ğŸ’µ To'lov summalari (DB'ga saqlanadi):")
        print(f"   Cash: ${cash_usd} USD = {cash_amount} UZS")
        print(f"   Click: ${click_usd} USD = {click_amount} UZS")
        print(f"   Terminal: ${terminal_usd} USD = {terminal_amount} UZS")
        print(f"   Debt: ${debt_usd} USD = {debt_amount} UZS")

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
            current_sale.payment_due_date = payment_due_date
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
                balance_usd=Decimal(str(balance_used)),
                notes=f'Multi-location savdo - {len(items)} ta mahsulot' if multi_location else None,
                currency_rate=current_rate,
                created_by=f'{current_user.first_name} {current_user.last_name}',
                payment_due_date=payment_due_date
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

            # UZS narxlarini saqlash (frontend dan keladi)
            unit_price_uzs = Decimal(str(item.get('price_uzs', 0) or 0))
            total_price_uzs = unit_price_uzs * quantity

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
                unit_price_uzs=unit_price_uzs,  # UZS da saqlash
                total_price_uzs=total_price_uzs,  # UZS da saqlash
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

        # Telegram xabar yuborish (yangi savdo yaratilganda yoki tahrirlanganda, mijoz telegram_chat_id bor bo'lsa)
        if final_customer_id:
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

                    # Valyuta kursi
                    tg_exchange_rate = float(current_sale.currency_rate) if current_sale.currency_rate else 12300

                    # To'lov summalari USD da (bazada USD saqlanadi)
                    tg_cash_usd = float(current_sale.cash_usd) if current_sale.cash_usd else 0
                    tg_click_usd = float(current_sale.click_usd) if current_sale.click_usd else 0
                    tg_terminal_usd = float(current_sale.terminal_usd) if current_sale.terminal_usd else 0
                    tg_debt_usd = float(current_sale.debt_usd) if current_sale.debt_usd else 0
                    tg_balance_usd = float(current_sale.balance_usd) if current_sale.balance_usd else 0
                    tg_total_usd = float(current_sale.total_amount) if current_sale.total_amount else 0
                    tg_paid_usd = tg_cash_usd + tg_click_usd + tg_terminal_usd + tg_balance_usd

                    # UZS ga konvertatsiya qilish
                    tg_cash_uzs = tg_cash_usd * tg_exchange_rate
                    tg_click_uzs = tg_click_usd * tg_exchange_rate
                    tg_terminal_uzs = tg_terminal_usd * tg_exchange_rate
                    tg_debt_uzs = tg_debt_usd * tg_exchange_rate
                    tg_balance_uzs = tg_balance_usd * tg_exchange_rate
                    tg_total_uzs = tg_total_usd * tg_exchange_rate
                    tg_paid_uzs = tg_paid_usd * tg_exchange_rate

                    # Sotuvchi ma'lumotlarini olish (PDF uchun)
                    seller_name = f"{current_sale.seller.first_name} {current_sale.seller.last_name}" if current_sale.seller else 'Sotuvchi'
                    seller_phone = format_phone_number(current_sale.seller.phone) if current_sale.seller and current_sale.seller.phone else ''

                    # Savdo mahsulotlarini PDF uchun tayyorlash
                    sale_items_for_pdf = []
                    for item in current_sale.items:
                        sale_items_for_pdf.append({
                            'name': item.product.name if item.product else 'Mahsulot',
                            'seller_name': seller_name,
                            'quantity': float(item.quantity),
                            'unit_price_uzs': float(item.unit_price) * tg_exchange_rate,
                            'total_uzs': float(item.total_price) * tg_exchange_rate,
                            'unit_price_usd': float(item.unit_price),
                            'total_usd': float(item.total_price),
                            'unit_price': float(item.unit_price) * tg_exchange_rate,  # Backward compatibility
                            'total': float(item.total_price) * tg_exchange_rate  # Backward compatibility
                        })

                    # Telegram xabar yuborish
                    bot.send_sale_notification_sync(
                        chat_id=customer.telegram_chat_id,
                        customer_name=customer.name,
                        customer_id=customer.id,
                        sale_date=current_sale.sale_date,
                        location_name=location_name,
                        total_amount_uzs=tg_total_uzs,
                        paid_uzs=tg_paid_uzs,
                        cash_uzs=tg_cash_uzs,
                        click_uzs=tg_click_uzs,
                        terminal_uzs=tg_terminal_uzs,
                        debt_uzs=tg_debt_uzs,
                        sale_id=current_sale.id,
                        sale_items=sale_items_for_pdf,
                        receipt_format=data.get('receipt_format', 'both'),
                        seller_phone=seller_phone,
                        customer_phone=format_phone_number(customer.phone) if customer.phone else '',
                        # USD qiymatlar
                        total_amount_usd=tg_total_usd,
                        paid_usd=tg_paid_usd,
                        cash_usd=tg_cash_usd,
                        click_usd=tg_click_usd,
                        terminal_usd=tg_terminal_usd,
                        debt_usd=tg_debt_usd,
                        balance_uzs=tg_balance_uzs,
                        balance_usd=tg_balance_usd
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
        
        # Mijoz balansidan ushbu savdoda ishlatilgan summani ayirish
        if balance_used > 0 and final_customer_id:
            sale_customer = Customer.query.get(final_customer_id)
            if sale_customer:
                old_bal = Decimal(str(sale_customer.balance or 0))
                deduct = Decimal(str(balance_used))
                sale_customer.balance = max(Decimal('0'), old_bal - deduct)
                print(f"ğŸ’³ Mijoz balansidan ${balance_used} ayirildi. Yangi balans: ${float(sale_customer.balance)}")
        
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

                # UZS narxlarini saqlash (frontend dan keladi)
                item_price_uzs = Decimal(str(item_data.get('price_uzs', 0) or 0))
                item_total_price_uzs = item_price_uzs * quantity

                sale_item = SaleItem(
                    sale_id=sale.id,
                    product_id=product.id,
                    quantity=quantity,
                    unit_price=unit_price_usd,  # USD da
                    total_price=quantity * unit_price_usd,  # USD da
                    unit_price_uzs=item_price_uzs,  # UZS da
                    total_price_uzs=item_total_price_uzs,  # UZS da
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

            # âœ… COMPLETELY DISABLED: Stock difference logic removed
            # Barcha stock operatsiyalari real-time API orqali boshqariladi:
            # - /api/reserve-stock - stokdan ayirish
            # - /api/return-stock - stokka qaytarish
            # Bu endpoint faqat sale ma'lumotlarini (items, prices, etc.) yangilaydi
            app.logger.info("â­ï¸ Stock difference DISABLED - stock managed by real-time API (/api/reserve-stock, /api/return-stock)")

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
        sale_total = float(sale.total_amount) if sale.total_amount else 0
        customer_name = sale.customer.name if sale.customer else 'Naqd mijoz'

        # Mahsulotlar ro'yxatini olish (o'chirishdan oldin)
        products_desc = ', '.join([f"{item.product.name} ({item.quantity} ta)" for item in sale.items if item.product])

        # OperationHistory ga yozish
        stock_returned = 'Ha' if return_stock else "Yo'q"
        operation = OperationHistory(
            operation_type='delete',
            user_id=current_user.id,
            record_id=sale_id,
            table_name='sales',
            description=f"Savdo o'chirildi #{sale_id}: {products_desc}. Mijoz: {customer_name}, Stock qaytarildi: {stock_returned}",
            amount=sale_total,
            location_type=sale.location_type,
            location_id=sale.location_id
        )
        db.session.add(operation)

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
        # Qarz to'lash muddati
        payment_due_date = None
        payment_due_date_str = data.get('payment_due_date')
        print(f"ğŸ“… PENDING: payment_due_date_str = '{payment_due_date_str}'")
        if payment_due_date_str:
            try:
                from datetime import datetime as dt_parse
                payment_due_date = dt_parse.strptime(payment_due_date_str, '%Y-%m-%d').date()
                print(f"ğŸ“… âœ… PENDING: Qarz muddati parsed: {payment_due_date}")
            except (ValueError, TypeError):
                print(f"ğŸ“… âŒ PENDING: Noto'g'ri sana: {payment_due_date_str}")

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
            created_by=f'{current_user.first_name} {current_user.last_name} - Pending',
            payment_due_date=payment_due_date
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

            # UZS narxlarini saqlash (frontend dan keladi)
            pending_unit_price_uzs = Decimal(str(item.get('price_uzs', 0) or 0))
            pending_total_price_uzs = pending_unit_price_uzs * Decimal(str(quantity))

            sale_item = SaleItem(
                sale_id=new_sale.id,
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price_usd,  # USD da
                total_price=total_price_usd,  # USD da
                unit_price_uzs=pending_unit_price_uzs,  # UZS da
                total_price_uzs=pending_total_price_uzs,  # UZS da
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

        # Har bir mahsulot uchun alohida OperationHistory yozuvi
        sale_username = f'{current_user.first_name} {current_user.last_name}'
        for sale_item in new_sale.items:
            item_amount_uzs = float(sale_item.total_price * new_sale.currency_rate) if sale_item.total_price else None
            op_item = OperationHistory(
                operation_type='sale',
                table_name='products',
                record_id=sale_item.product_id,
                user_id=current_user.id,
                username=sale_username,
                description=f"Sotildi: {sale_item.product.name} - {sale_item.quantity} ta Ã— ${sale_item.unit_price:.2f} (Savdo #{new_sale.id})",
                old_data=None,
                new_data={
                    'sale_id': new_sale.id,
                    'product_id': sale_item.product_id,
                    'quantity': float(sale_item.quantity),
                    'unit_price_usd': float(sale_item.unit_price),
                    'total_price_usd': float(sale_item.total_price),
                    'payment_status': 'pending',
                },
                ip_address=request.remote_addr,
                location_id=item_location_id,
                location_type=item_location_type,
                location_name=location_name,
                amount=item_amount_uzs
            )
            db.session.add(op_item)
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
            return None  # Kurs o'rnatilmagan
    except Exception:
        return None  # Xatolik bo'lsa None qaytarish


# Oxirgi operatsiyalarni saqlash (memory cache)
_last_operations = {}

# Idempotency keys - qayta bajarilmasligi uchun (1 soat saqlanadi)
_processed_idempotency_keys = {}

def _check_idempotency(key):
    """Agar bu key allaqachon bajarilgan bo'lsa True qaytaradi"""
    if not key:
        return False
    if key in _processed_idempotency_keys:
        return True
    return False

def _mark_idempotency(key):
    """Keyni bajarilgan deb belgilash"""
    if not key:
        return
    _processed_idempotency_keys[key] = get_tashkent_time()
    # Eski keylarni tozalash (1000 dan oshsa)
    if len(_processed_idempotency_keys) > 1000:
        cutoff = get_tashkent_time() - timedelta(hours=1)
        expired = [k for k, t in _processed_idempotency_keys.items() if t < cutoff]
        for k in expired:
            del _processed_idempotency_keys[k]

# API endpoint - Real-time stock rezerv qilish (korzinaga qo'shilganda)
@app.route('/api/reserve-stock', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_reserve_stock():
    """Mahsulot korzinaga qo'shilganda real-time stock'dan ayirish"""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = Decimal(str(data.get('quantity', 1)))
        location_id = data.get('location_id')
        location_type = data.get('location_type')
        idempotency_key = data.get('idempotency_key')

        # Idempotency tekshiruvi - bir xil so'rov ikki marta bajarilmaydi
        if _check_idempotency(idempotency_key):
            print(f"âœ… IDEMPOTENCY: {idempotency_key} allaqachon bajarilgan, qaytarish")
            return jsonify({'success': True, 'already_processed': True}), 200

        import traceback
        logger.debug(''.join(traceback.format_stack()[-5:-1]))
        print(f"\n{'=' * 80}")
        print("ğŸ“¦ RESERVE-STOCK API CHAQIRILDI:")
        print(f"   Product ID: {product_id}")
        print(f"   Quantity: {quantity}")
        print(f"   Location: {location_id} ({location_type})")
        print(f"   Idempotency Key: {idempotency_key}")
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

        # Idempotency keyni bajarilgan deb belgilash
        _mark_idempotency(idempotency_key)

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
@role_required('admin', 'kassir', 'sotuvchi')
def api_return_stock():
    """Mahsulot korzinadan o'chirilganda real-time stock'ga qaytarish"""
    try:
        data = request.get_json()
        product_id = data.get('product_id')
        quantity = Decimal(str(data.get('quantity', 1)))
        location_id = data.get('location_id')
        location_type = data.get('location_type')
        idempotency_key = data.get('idempotency_key')

        # Idempotency tekshiruvi - bir xil so'rov ikki marta bajarilmaydi
        if _check_idempotency(idempotency_key):
            print(f"âœ… IDEMPOTENCY: {idempotency_key} allaqachon bajarilgan, qaytarish")
            return jsonify({'success': True, 'already_processed': True}), 200

        print(f"\n{'=' * 80}")
        print("â†©ï¸ RETURN-STOCK API CHAQIRILDI:")
        print(f"   Product ID: {product_id}")
        print(f"   Quantity: {quantity}")
        print(f"   Location: {location_id} ({location_type})")
        print(f"   Idempotency Key: {idempotency_key}")
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

        # Idempotency keyni bajarilgan deb belgilash
        _mark_idempotency(idempotency_key)

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

            pending_upd_unit_uzs = Decimal(str(item.get('price_uzs', 0) or 0))
            pending_upd_total_uzs = pending_upd_unit_uzs * quantity

            sale_item = SaleItem(
                sale_id=sale_id,
                product_id=product_id,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                unit_price_uzs=pending_upd_unit_uzs,
                total_price_uzs=pending_upd_total_uzs,
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
            # Kurs o'rnatilmagan - xatolik qaytarish
            return jsonify({
                'success': False,
                'error': 'Valyuta kursi o\'rnatilmagan. Iltimos, avval kursni o\'rnating.',
                'rate': None
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

        # Get current active rate first
        current_rate = get_current_currency_rate()

        # Add currency_rate column with current rate as default
        if current_rate:
            db.session.execute(text("""
                ALTER TABLE sales
                ADD COLUMN currency_rate DECIMAL(15,4) DEFAULT :rate
            """), {'rate': current_rate})
        else:
            # Agar kurs o'rnatilmagan bo'lsa, nullable qilamiz
            db.session.execute(text("""
                ALTER TABLE sales
                ADD COLUMN currency_rate DECIMAL(15,4)
            """))
        db.session.commit()

        # Update all existing sales with current rate if available
        if current_rate:
            result = db.session.execute(text("""
                UPDATE sales
                SET currency_rate = :rate
                WHERE currency_rate IS NULL
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


# ==================== ERROR HANDLERS ====================
@app.errorhandler(404)
def page_not_found(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Sahifa topilmadi', 'success': False}), 404
    return render_template('base.html'), 404


@app.errorhandler(403)
def forbidden(e):
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Ruxsat yo\'q', 'success': False}), 403
    return redirect(url_for('login_page'))


@app.errorhandler(500)
def internal_server_error(e):
    logger.error(f"500 xatolik: {str(e)}")
    db.session.rollback()
    if request.path.startswith('/api/'):
        return jsonify({'error': 'Server xatoligi yuz berdi', 'success': False}), 500
    return render_template('base.html'), 500


# ==================== LOGIN SAHIFASI ====================
# Parol tiklash uchun vaqtinchalik kodlar (xotirada)
import threading as _threading
_reset_codes_lock = _threading.Lock()
_password_reset_codes = {}   # {phone: {code, user_id, username, expires_at}}
_reset_tokens = {}           # {token: {user_id, username, expires_at}}


def _cleanup_reset_codes():
    """Muddati o'tgan kodlarni tozalash"""
    now = datetime.now()
    with _reset_codes_lock:
        for k in list(_password_reset_codes.keys()):
            if _password_reset_codes[k]['expires_at'] < now:
                del _password_reset_codes[k]
        for k in list(_reset_tokens.keys()):
            if _reset_tokens[k]['expires_at'] < now:
                del _reset_tokens[k]


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
        session['user_photo'] = f"/static/uploads/users/{user.photo}" if user.photo else None
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

        # Foydalanuvchi tilini yuklash
        # Login sahifasida tanlangan til (session da bor, lekin DB da yo'q) â€” prioritet
        login_selected_lang = session.get('language')
        valid_languages = ['uz_latin', 'uz_cyrillic', 'ru']
        try:
            user_lang_setting = Settings.query.filter_by(key=f'user_language_{user.id}').first()
            if login_selected_lang and login_selected_lang in valid_languages:
                # Login sahifasida til tanlangan â€” uni ishlatamiz va DB ga saqlaymiz
                final_lang = login_selected_lang
                if user_lang_setting:
                    user_lang_setting.value = final_lang
                else:
                    db.session.add(Settings(
                        key=f'user_language_{user.id}',
                        value=final_lang,
                        description=f'Foydalanuvchi {user.id} uchun til'
                    ))
                db.session.commit()
                logger.info(f"ğŸŒ Login sahifasidagi til saqlandi: {final_lang}")
            elif user_lang_setting and user_lang_setting.value in valid_languages:
                # Login sahifasida til tanlanmagan â€” DB dan yuklaymiz
                final_lang = user_lang_setting.value
                logger.info(f"ğŸŒ Foydalanuvchi tili DB dan yuklandi: {final_lang}")
            else:
                final_lang = 'uz_latin'
            session['language'] = final_lang
        except Exception as e:
            logger.error(f"Til yuklashda xato: {e}")
            session['language'] = login_selected_lang if login_selected_lang in valid_languages else 'uz_latin'

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


@app.route('/api/forgot-password', methods=['POST'])
def api_forgot_password():
    """1-qadam: Telefon raqam orqali OTP yuborish"""
    try:
        data = request.get_json()
        phone_input = (data.get('phone') or '').strip()
        if not phone_input:
            return jsonify({'success': False, 'message': 'Telefon raqam kiritilmadi'}), 400

        clean_input = ''.join(filter(str.isdigit, phone_input))

        # User jadvalida telefon raqamni qidirish
        user = None
        all_users = User.query.filter_by(is_active=True).all()
        for u in all_users:
            if u.phone:
                clean_db = ''.join(filter(str.isdigit, u.phone))
                if len(clean_input) >= 9 and len(clean_db) >= 9 and clean_db[-9:] == clean_input[-9:]:
                    user = u
                    break

        if not user:
            return jsonify({'success': False, 'message': 'Bu telefon raqam tizimda topilmadi'}), 404

        if not user.telegram_chat_id:
            return jsonify({
                'success': False,
                'message': f'Telegram bog\'lanmagan. Avval @Sergeli143_bot ga /link_account yozing.'
            }), 400

        # 6 raqamli OTP yaratish va DBga saqlash
        import random as _random
        code = str(_random.randint(100000, 999999))
        expires_at = datetime.now() + timedelta(minutes=1)

        user.reset_code = code
        user.reset_code_expires_at = expires_at
        db.session.commit()

        # Telegram orqali kod yuborish (@Paroltiklash_bot)
        bot_token = os.getenv('TELEGRAM_RESET_BOT_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN')
        if bot_token:
            import requests as _req
            msg = (
                f"ğŸ” <b>PAROL TIKLASH KODI</b>\n"
                f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
                f"Tasdiqlash kodi: <b>{code}</b>\n\n"
                f"â± Amal qilish muddati: 1 daqiqa\n\n"
                f"<i>Agar siz so'ramagan bo'lsangiz, ushbu xabarni e'tiborsiz qoldiring.</i>"
            )
            _req.post(
                f"https://api.telegram.org/bot{bot_token}/sendMessage",
                json={'chat_id': user.telegram_chat_id, 'text': msg, 'parse_mode': 'HTML'},
                timeout=10
            )

        return jsonify({'success': True, 'message': 'Tasdiqlash kodi Telegram ga yuborildi'})

    except Exception as e:
        logger.error(f"forgot-password xatolik: {e}")
        return jsonify({'success': False, 'message': 'Server xatoligi'}), 500


@app.route('/api/verify-reset-code', methods=['POST'])
def api_verify_reset_code():
    """2-qadam: OTP kodni tekshirish va token qaytarish"""
    try:
        data = request.get_json()
        phone_input = (data.get('phone') or '').strip()
        code_input = (data.get('code') or '').strip()

        clean_input = ''.join(filter(str.isdigit, phone_input))

        # DBdan userni topish â€” telefon mos va reset_code = code_input bo'lgan userni top
        user = None
        fallback_user = None
        all_users = User.query.filter_by(is_active=True).all()
        for u in all_users:
            if u.phone:
                clean_db = ''.join(filter(str.isdigit, u.phone))
                if len(clean_input) >= 9 and len(clean_db) >= 9 and clean_db[-9:] == clean_input[-9:]:
                    if u.reset_code == code_input:
                        user = u
                        break
                    elif u.reset_code and fallback_user is None:
                        fallback_user = u

        if not user:
            user = fallback_user

        if not user or not user.reset_code:
            return jsonify({'success': False, 'message': 'Kod topilmadi yoki muddati o\'tgan'}), 400

        if user.reset_code_expires_at and datetime.now() > user.reset_code_expires_at:
            user.reset_code = None
            user.reset_code_expires_at = None
            db.session.commit()
            return jsonify({'success': False, 'message': 'Kod muddati o\'tgan, qayta so\'rang'}), 400

        if user.reset_code != code_input:
            return jsonify({'success': False, 'message': 'Kod noto\'g\'ri'}), 400

        # Kod to'g'ri â€” bir martalik token yaratish va DBga saqlash
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now() + timedelta(minutes=10)
        with _reset_codes_lock:
            _reset_tokens[token] = {
                'user_id': user.id,
                'username': user.username,
                'expires_at': expires_at
            }
        user.reset_code = None
        user.reset_code_expires_at = None
        db.session.commit()

        return jsonify({
            'success': True,
            'token': token,
            'username': user.username
        })

    except Exception as e:
        logger.error(f"verify-reset-code xatolik: {e}")
        return jsonify({'success': False, 'message': 'Server xatoligi'}), 500


@app.route('/api/reset-password', methods=['POST'])
def api_reset_password():
    """3-qadam: Yangi parolni saqlash"""
    try:
        _cleanup_reset_codes()
        data = request.get_json()
        token = (data.get('token') or '').strip()
        new_password = data.get('new_password') or ''
        confirm_password = data.get('confirm_password') or ''

        if not token:
            return jsonify({'success': False, 'message': 'Token topilmadi'}), 400

        with _reset_codes_lock:
            entry = _reset_tokens.get(token)

        if not entry:
            return jsonify({'success': False, 'message': 'Token noto\'g\'ri yoki muddati o\'tgan'}), 400

        if datetime.now() > entry['expires_at']:
            with _reset_codes_lock:
                _reset_tokens.pop(token, None)
            return jsonify({'success': False, 'message': 'Token muddati o\'tgan, qayta boshlang'}), 400

        if len(new_password) < 6:
            return jsonify({'success': False, 'message': 'Parol kamida 6 ta belgidan iborat bo\'lishi kerak'}), 400

        if new_password != confirm_password:
            return jsonify({'success': False, 'message': 'Parollar mos kelmadi'}), 400

        user = User.query.get(entry['user_id'])
        if not user:
            return jsonify({'success': False, 'message': 'Foydalanuvchi topilmadi'}), 404

        user.password = hash_password(new_password)
        db.session.commit()

        with _reset_codes_lock:
            _reset_tokens.pop(token, None)

        logger.info(f"âœ… Parol tiklandi: {user.username}")
        return jsonify({'success': True, 'message': 'Parol muvaffaqiyatli yangilandi'})

    except Exception as e:
        db.session.rollback()
        logger.error(f"reset-password xatolik: {e}")
        return jsonify({'success': False, 'message': 'Server xatoligi'}), 500


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

    # Tilni saqlab qolamiz â€” login sahifasida ham xuddi shunday til ko'rinsin
    lang = session.get('language', 'uz_latin')
    session.clear()
    session['language'] = lang
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
                COALESCE(SUM(si.unit_price * si.quantity), 0) as total_revenue,
                COALESCE(AVG(si.unit_price * si.quantity), 0) as avg_sale
            FROM sale_items si
            JOIN sales s ON si.sale_id = s.id
        """

        conditions = []
        params = {}

        if location_id:
            conditions.append("s.location_id = :location_id")
            params['location_id'] = int(location_id)

        if date_from:
            conditions.append("s.sale_date >= :date_from")
            params['date_from'] = date_from

        if date_to:
            conditions.append("s.sale_date <= :date_to")
            params['date_to'] = date_to

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
        top_params = {}

        if date_from:
            top_conditions.append("s.sale_date >= :date_from")
            top_params['date_from'] = date_from

        if date_to:
            top_conditions.append("s.sale_date <= :date_to")
            top_params['date_to'] = date_to

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
                    COALESCE(SUM(s.debt_usd), 0) as debt_total,
                    COALESCE(SUM(si_agg.total_uzs), 0) as period_total_uzs,
                    COALESCE(SUM(s.cash_amount), 0) as cash_uzs_total,
                    COALESCE(SUM(s.click_amount), 0) as click_uzs_total,
                    COALESCE(SUM(s.terminal_amount), 0) as terminal_uzs_total,
                    COALESCE(SUM(s.debt_amount), 0) as debt_uzs_total
                FROM sales s
                LEFT JOIN (
                    SELECT sale_id, SUM(total_price_uzs) as total_uzs
                    FROM sale_items
                    GROUP BY sale_id
                ) si_agg ON si_agg.sale_id = s.id
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
                    COALESCE(SUM(s.debt_usd), 0) as debt_total,
                    COALESCE(SUM(si_agg.total_uzs), 0) as period_total_uzs,
                    COALESCE(SUM(s.cash_amount), 0) as cash_uzs_total,
                    COALESCE(SUM(s.click_amount), 0) as click_uzs_total,
                    COALESCE(SUM(s.terminal_amount), 0) as terminal_uzs_total,
                    COALESCE(SUM(s.debt_amount), 0) as debt_uzs_total
                FROM sales s
                LEFT JOIN (
                    SELECT sale_id, SUM(total_price_uzs) as total_uzs
                    FROM sale_items
                    GROUP BY sale_id
                ) si_agg ON si_agg.sale_id = s.id
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
        amounts_uzs = []  # DB dan saqlangan UZS qiymatlar
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
                    'debt': float(row[7]) if row[7] else 0.0,
                    'amount_uzs': float(row[8]) if len(row) > 8 and row[8] else 0.0
                }

            # 0 dan 23 gacha barcha soatlarni qo'shamiz
            for hour in range(24):
                labels.append(f"{hour:02d}:00")
                if hour in hourly_data:
                    values.append(hourly_data[hour]['sales'])
                    amounts.append(hourly_data[hour]['amount'])
                    amounts_uzs.append(hourly_data[hour]['amount_uzs'])
                    profits.append(hourly_data[hour]['profit'])
                    debts.append(hourly_data[hour]['debt'])
                    cash_list.append(hourly_data[hour]['cash'])
                    click_list.append(hourly_data[hour]['click'])
                    terminal_list.append(hourly_data[hour]['terminal'])
                else:
                    values.append(0)
                    amounts.append(0.0)
                    amounts_uzs.append(0.0)
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
                amounts_uzs.append(float(row[8]) if len(row) > 8 and row[8]
                                   else 0.0)  # UZS summa (saqlangan)

        # To'lov turlarini hisoblash (USD va UZS)
        payment_totals = {
            'cash': sum(float(row[4]) if len(row) > 4 and row[4] else 0.0 for row in results),
            'click': sum(float(row[5]) if len(row) > 5 and row[5] else 0.0 for row in results),
            'terminal': sum(float(row[6]) if len(row) > 6 and row[6] else 0.0 for row in results),
            'debt': sum(float(row[7]) if len(row) > 7 and row[7] else 0.0 for row in results),
            'cash_uzs': sum(float(row[9]) if len(row) > 9 and row[9] else 0.0 for row in results),
            'click_uzs': sum(float(row[10]) if len(row) > 10 and row[10] else 0.0 for row in results),
            'terminal_uzs': sum(float(row[11]) if len(row) > 11 and row[11] else 0.0 for row in results),
            'debt_uzs': sum(float(row[12]) if len(row) > 12 and row[12] else 0.0 for row in results),
        }

        return jsonify({
            'labels': labels,
            'values': values,
            'amounts': amounts,
            'amounts_uzs': amounts_uzs,
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
        params = {}

        if date_from:
            conditions.append("s.sale_date >= :date_from")
            params['date_from'] = date_from

        if date_to:
            conditions.append("s.sale_date <= :date_to")
            params['date_to'] = date_to

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
                si.total_price as total_amount
            FROM sales s
            JOIN sale_items si ON s.id = si.sale_id
            JOIN products p ON si.product_id = p.id
            LEFT JOIN stores st ON s.location_id = st.id AND s.location_type = 'store'
            LEFT JOIN warehouses w ON s.location_id = w.id AND s.location_type = 'warehouse'
        """

        conditions = []
        params = {}

        if location_id:
            conditions.append("s.location_id = :location_id")
            params['location_id'] = int(location_id)

        if date_from:
            conditions.append("s.sale_date >= :date_from")
            params['date_from'] = date_from

        if date_to:
            conditions.append("s.sale_date <= :date_to")
            params['date_to'] = date_to

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        query += " ORDER BY s.sale_date DESC, s.id DESC LIMIT :limit"
        params['limit'] = int(limit)

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
            'auto_backup': False,
            'default_reminder_time': '10:00',  # Qarz eslatma default vaqti
            'telegram_group_name': 'DiamondCarAccesories',
            'telegram_group_link': 'https://t.me/DiamondCarAccesories',
            'telegram_bot_name': '@Sergeli143_bot',
            'telegram_bot_link': 'https://t.me/Sergeli143_bot',
        }

        # Bazadan sozlamalarni olish
        settings_data = {}
        settings_list = Settings.query.all()

        for setting in settings_list:
            # user_language_ kalitlarini chiqarib tashlash (per-user til sozlamalari)
            if setting.key.startswith('user_language_'):
                continue
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


@app.route('/api/settings/language', methods=['POST'])
def change_language():
    """Til o'zgartirish"""
    try:
        data = request.get_json()
        language = data.get('language', 'uz_latin')

        # Til qiymatini tekshirish
        valid_languages = ['uz_latin', 'uz_cyrillic', 'ru']
        if language not in valid_languages:
            return jsonify({'error': 'Noto\'g\'ri til kodi'}), 400

        # Session'da til ma'lumotini saqlash
        session['language'] = language
        session.permanent = True

        # Database'ga ham saqlash (agar foydalanuvchi tizimga kirgan bo'lsa)
        if 'user_id' in session:
            user_id = session['user_id']
            setting_key = f'user_language_{user_id}'

            setting = Settings.query.filter_by(key=setting_key).first()
            if setting:
                setting.value = language
                setting.updated_at = get_tashkent_time()
            else:
                setting = Settings(
                    key=setting_key,
                    value=language,
                    description=f'Foydalanuvchi {user_id} uchun til'
                )
                db.session.add(setting)

            db.session.commit()

        logger.info(f"Til o'zgartirildi: {language}")
        return jsonify({'success': True, 'language': language})

    except Exception as e:
        db.session.rollback()
        logger.error(f"Til o'zgartirishda xato: {e}")
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
            # language va user_language_ kalitlarini o'tkazib yuborish
            # (til faqat /api/settings/language orqali saqlanadi)
            if key == 'language' or key.startswith('user_language_'):
                continue

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


@app.route('/hisobot')
def hisobot():
    """Hisobot sahifasi"""
    if 'user_id' not in session:
        return redirect('/login')
    current_user = get_current_user()
    stores = Store.query.order_by(Store.name).all()
    warehouses = Warehouse.query.order_by(Warehouse.name).all()
    return render_template('hisobot.html', current_user=current_user, stores=stores, warehouses=warehouses)


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
    """Barcha templatelarga sozlamalarni va til tarjimalarini uzatish"""
    try:
        # stock_check_visible sozlamasini olish
        setting = Settings.query.filter_by(key='stock_check_visible').first()
        stock_check_visible = (setting.value.lower() == 'true'
                               if setting else True)

        # Hozirgi tilni olish - ISHONCHLI usul: bazadan o'qish
        current_language = session.get('language', 'uz_latin')
        
        # Bazadan foydalanuvchi tilini tekshirish (session yo'qolishi mumkin)
        user_id = session.get('user_id')
        if user_id:
            try:
                user_lang = Settings.query.filter_by(key=f'user_language_{user_id}').first()
                if user_lang and user_lang.value in ('uz_latin', 'uz_cyrillic', 'ru'):
                    current_language = user_lang.value
                    # Session'ni ham yangilash
                    if session.get('language') != current_language:
                        session['language'] = current_language
                        session.modified = True
            except Exception as lang_err:
                logger.debug(f"Til yuklashda xato (context): {lang_err}")

        # Tarjima lug'atini translations.py dan olish
        current_translations = TRANSLATIONS.get(current_language, TRANSLATIONS['uz_latin'])

        # Tarjima funksiyasi
        def t(key):
            """Kalit bo'yicha tarjimani qaytaradi"""
            return current_translations.get(key, key)

        # Qarz savdolar soni
        debt_sales_count = 0
        try:
            if session.get('user_id'):
                debt_sales_count = Sale.query.filter(
                    Sale.payment_status == 'partial',
                    Sale.debt_usd > 0
                ).count()
        except Exception:
            debt_sales_count = 0

        # Tasdiqlanmagan savdolar soni
        pending_sales_count = 0
        try:
            if session.get('user_id'):
                pending_sales_count = Sale.query.filter(
                    Sale.payment_status == 'pending'
                ).count()
        except Exception:
            pending_sales_count = 0

        # Jarayondagi transferlar soni
        pending_transfer_count = 0
        try:
            if session.get('user_id'):
                pending_transfer_count = PendingTransfer.query.count()
        except Exception:
            pending_transfer_count = 0

        # Aktiv qoldiq tekshirish sessiyalari soni
        stock_check_count = 0
        try:
            if session.get('user_id'):
                stock_check_count = StockCheckSession.query.filter(
                    StockCheckSession.status == 'active'
                ).count()
        except Exception:
            stock_check_count = 0

        # Jarayondagi mahsulot qo'shish sessiyalari soni
        pending_product_count = 0
        try:
            if session.get('user_id'):
                pending_product_count = PendingProductBatch.query.count()
        except Exception:
            pending_product_count = 0

        return {
            'stock_check_visible': stock_check_visible,
            'config': app.config,
            'current_language': current_language,
            't': t,  # Tarjima funksiyasi
            'translations': current_translations,
            'debt_sales_count': debt_sales_count,
            'pending_sales_count': pending_sales_count,
            'pending_transfer_count': pending_transfer_count,
            'stock_check_count': stock_check_count,
            'pending_product_count': pending_product_count
        }
    except Exception as e:
        logger.error(f"Context processor error: {e}")
        # Xato bo'lsa, standart qiymat
        return {
            'stock_check_visible': True,
            'config': app.config,
            'current_language': 'uz_latin',
            't': lambda key: key,
            'translations': {}
        }


# HTML sahifalarni keshlamaslik (mobil brauzerlar uchun til muammosini hal qilish)
@app.after_request
def add_no_cache_headers(response):
    """HTML javoblarni keshlamaslik - til tarjimasi to'g'ri ishlashi uchun"""
    if response.content_type and 'text/html' in response.content_type:
        response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        response.headers['Pragma'] = 'no-cache'
        response.headers['Expires'] = '0'
    return response


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
@role_required('admin', 'kassir', 'sotuvchi')
def api_send_debt_sms():
    """Qarzli mijozga Telegram eslatmasi yuborish"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        message_type = data.get('message_type', 'general')  # general, pre_reminder, due_today, overdue

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

        # Eng yaqin payment_due_date ni olish
        nearest_due = db.session.query(
            db.func.min(Sale.payment_due_date)
        ).filter(
            Sale.customer_id == customer_id,
            Sale.debt_usd > 0,
            Sale.payment_due_date.isnot(None)
        ).scalar()

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
                customer_id=customer_id,
                message_type=message_type,
                payment_due_date=nearest_due
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


# =====================================================================
# QARZ ESLATMA SCHEDULING API ENDPOINTS
# =====================================================================

@app.route('/api/debt-reminders', methods=['GET'])
@role_required('admin', 'kassir')
def api_get_debt_reminders():
    """Barcha qarz eslatmalarini olish"""
    try:
        customer_id = request.args.get('customer_id', type=int)
        status = request.args.get('status', 'active')  # active, sent, all

        query = DebtReminder.query

        if customer_id:
            query = query.filter_by(customer_id=customer_id)

        if status == 'active':
            query = query.filter_by(is_active=True, is_sent=False)
        elif status == 'sent':
            query = query.filter_by(is_sent=True)

        reminders = query.order_by(
            DebtReminder.reminder_date.asc(),
            DebtReminder.reminder_time.asc()
        ).all()

        return jsonify({
            'success': True,
            'reminders': [r.to_dict() for r in reminders]
        })
    except Exception as e:
        logger.error(f"Eslatmalarni olishda xatolik: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/debt-reminders', methods=['POST'])
@role_required('admin', 'kassir')
def api_create_debt_reminder():
    """Yangi qarz eslatmasi yaratish"""
    try:
        data = request.get_json()
        customer_id = data.get('customer_id')
        reminder_date = data.get('reminder_date')  # YYYY-MM-DD
        # Agar vaqt berilmagan bo'lsa Settings'dan default vaqtni olish
        default_time_setting = Settings.query.filter_by(key='default_reminder_time').first()
        default_time = default_time_setting.value if default_time_setting else '10:00'
        reminder_time = data.get('reminder_time', default_time)  # HH:MM
        message = data.get('message', '')

        if not customer_id or not reminder_date:
            return jsonify({'success': False, 'error': 'Mijoz va sana kiritilishi shart'}), 400

        # Mijozni tekshirish
        customer = Customer.query.get(customer_id)
        if not customer:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        if not customer.telegram_chat_id:
            return jsonify({'success': False, 'error': 'Mijozda Telegram ID yo\'q. Mijoz botga /start yuborishi kerak'}), 400

        # Sana va vaqtni parse qilish
        try:
            date_obj = datetime.strptime(reminder_date, '%Y-%m-%d').date()
            time_obj = datetime.strptime(reminder_time, '%H:%M').time()
        except ValueError:
            return jsonify({'success': False, 'error': 'Noto\'g\'ri sana yoki vaqt formati'}), 400

        # Duplicate tekshirish
        existing = DebtReminder.query.filter_by(
            customer_id=customer_id,
            reminder_date=date_obj,
            reminder_time=time_obj,
            is_active=True
        ).first()

        if existing:
            return jsonify({'success': False, 'error': 'Bu sana va vaqtda eslatma allaqachon mavjud'}), 409

        # User nomi
        user = None
        user_id = session.get('user_id')
        if user_id:
            user = User.query.get(user_id)

        # Eslatma yaratish
        reminder = DebtReminder(
            customer_id=customer_id,
            reminder_date=date_obj,
            reminder_time=time_obj,
            message=message,
            created_by=f"{user.first_name} {user.last_name}" if user else 'System'
        )

        db.session.add(reminder)
        db.session.commit()

        logger.info(f"âœ… Qarz eslatmasi yaratildi: {customer.name} - {date_obj} {time_obj}")

        return jsonify({
            'success': True,
            'message': f'{customer.name} uchun eslatma belgilandi: {date_obj.strftime("%d.%m.%Y")} {time_obj.strftime("%H:%M")}',
            'reminder': reminder.to_dict()
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Eslatma yaratishda xatolik: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/debt-reminders/<int:reminder_id>', methods=['DELETE'])
@role_required('admin', 'kassir')
def api_delete_debt_reminder(reminder_id):
    """Qarz eslatmasini o'chirish"""
    try:
        reminder = DebtReminder.query.get(reminder_id)
        if not reminder:
            return jsonify({'success': False, 'error': 'Eslatma topilmadi'}), 404

        reminder.is_active = False
        db.session.commit()

        return jsonify({'success': True, 'message': 'Eslatma o\'chirildi'})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Eslatma o'chirishda xatolik: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/debt-reminders/check-and-send', methods=['POST'])
@role_required('admin')
def api_check_and_send_reminders():
    """Vaqti kelgan eslatmalarni tekshirib yuborish (manual trigger)"""
    try:
        now = get_tashkent_time()
        today = now.date()
        current_time = now.time()

        # Bugungi va o'tgan, lekin yuborilmagan eslatmalarni olish
        reminders = DebtReminder.query.filter(
            DebtReminder.is_active == True,
            DebtReminder.is_sent == False,
            DebtReminder.reminder_date <= today
        ).all()

        sent_count = 0
        failed_count = 0

        for reminder in reminders:
            # Bugungi eslatmalar uchun vaqtni tekshirish
            if reminder.reminder_date == today and reminder.reminder_time > current_time:
                continue  # Hali vaqti kelmagan

            customer = Customer.query.get(reminder.customer_id)
            if not customer or not customer.telegram_chat_id:
                reminder.is_active = False
                continue

            # Mijozning hali qarzi bormi tekshirish
            remaining_debt = db.session.query(
                db.func.sum(Sale.debt_usd)
            ).filter(
                Sale.customer_id == reminder.customer_id,
                Sale.debt_usd > 0
            ).scalar() or 0

            if float(remaining_debt) <= 0:
                reminder.is_sent = True
                reminder.is_active = False
                continue

            # Telegram yuborish
            try:
                from debt_scheduler import get_scheduler_instance
                scheduler = get_scheduler_instance(app, db)

                rate = CurrencyRate.query.order_by(CurrencyRate.id.desc()).first()
                exchange_rate = float(rate.rate) if rate else 13000
                debt_uzs = float(remaining_debt) * exchange_rate

                success = scheduler.send_telegram_debt_reminder_sync(
                    chat_id=customer.telegram_chat_id,
                    customer_name=customer.name,
                    debt_usd=float(remaining_debt),
                    debt_uzs=debt_uzs,
                    location_name="Do'kon",
                    customer_id=customer.id
                )

                if success:
                    reminder.is_sent = True
                    reminder.sent_at = get_tashkent_time()
                    sent_count += 1
                else:
                    failed_count += 1

                import time
                time.sleep(1)

            except Exception as e:
                logger.error(f"Eslatma yuborishda xatolik ({customer.name}): {e}")
                failed_count += 1

        db.session.commit()

        return jsonify({
            'success': True,
            'sent': sent_count,
            'failed': failed_count,
            'message': f'{sent_count} ta eslatma yuborildi'
        })

    except Exception as e:
        db.session.rollback()
        logger.error(f"Eslatmalarni tekshirishda xatolik: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================
# HOSTING ADMIN PANEL ROUTES
# ============================================

@app.route('/api/hosting/widget/<token>')
def api_hosting_widget_status(token):
    """Mijoz saytidagi widget uchun public API (login kerak emas)"""
    try:
        client = HostingClient.query.filter_by(status_token=token, is_active=True).first()
        if not client:
            return jsonify({'success': False, 'error': 'Token noto\'g\'ri'}), 404

        balance = float(client.balance or 0)
        monthly_price = float(client.monthly_price_uzs or 0)
        today = get_tashkent_time().date()

        days_left = 0
        end_date = None
        status = 'overdue'

        if monthly_price > 0 and balance > 0:
            daily_price = monthly_price / 30
            days_left = int(balance / daily_price)
            end_date = (today + timedelta(days=days_left)).strftime('%d.%m.%Y')
            if days_left > 7:
                status = 'ok'
            elif days_left > 3:
                status = 'warning'
            else:
                status = 'danger'
        elif balance <= 0:
            status = 'overdue'
            days_left = 0

        # CORS header
        response = jsonify({
            'success': True,
            'name': client.name,
            'balance': balance,
            'balance_formatted': f"{balance:,.0f}".replace(',', ' '),
            'monthly_price': monthly_price,
            'monthly_formatted': f"{monthly_price:,.0f}".replace(',', ' '),
            'days_left': days_left,
            'end_date': end_date,
            'server_status': client.server_status,
            'status': status
        })
        response.headers['Access-Control-Allow-Origin'] = '*'
        return response
    except Exception as e:
        logger.error(f"Widget API xatosi: {e}")
        return jsonify({'success': False, 'error': 'Server xatosi'}), 500


@app.route('/hosting')
@role_required('admin')
def hosting_dashboard():
    """Hosting boshqaruv paneli"""
    return render_template('hosting_dashboard.html')


@app.route('/api/hosting/clients', methods=['GET'])
@role_required('admin')
def api_hosting_clients():
    """Hosting mijozlar ro'yxati"""
    try:
        clients = HostingClient.query.order_by(HostingClient.name).all()
        result = []
        for c in clients:
            data = c.to_dict()
            today = get_tashkent_time().date()

            # Balansga asosan to'lov muddatini hisoblash
            balance = float(c.balance or 0)
            monthly_price = float(c.monthly_price_uzs or 0)

            if monthly_price > 0 and balance > 0:
                # Balans necha kunga yetadi
                daily_price = monthly_price / 30
                balance_days = int(balance / daily_price)
                balance_end_date = today + timedelta(days=balance_days)
                data['balance_end_date'] = balance_end_date.strftime('%d.%m.%Y')
                data['balance_days_left'] = balance_days
                data['next_payment_date'] = balance_end_date.strftime('%d.%m.%Y')

                if balance_days < 0:
                    data['payment_status'] = 'overdue'
                elif balance_days <= 3:
                    data['payment_status'] = 'warning'
                elif balance_days <= 7:
                    data['payment_status'] = 'warning'
                else:
                    data['payment_status'] = 'ok'
                data['days_left'] = balance_days
            elif monthly_price > 0 and balance <= 0:
                data['balance_end_date'] = None
                data['balance_days_left'] = 0
                data['next_payment_date'] = today.strftime('%d.%m.%Y')
                data['payment_status'] = 'overdue'
                data['days_left'] = 0
            else:
                data['balance_end_date'] = None
                data['balance_days_left'] = None
                data['next_payment_date'] = None
                data['payment_status'] = 'never_paid'
                data['days_left'] = None

            # Oxirgi to'lov sanasi
            last_payment = HostingPayment.query.filter_by(
                client_id=c.id
            ).order_by(HostingPayment.payment_date.desc()).first()
            data['last_payment_date'] = last_payment.payment_date.strftime('%d.%m.%Y') if last_payment and last_payment.payment_date else None

            # Pending buyurtmalar soni
            pending_count = HostingPaymentOrder.query.filter(
                HostingPaymentOrder.client_id == c.id,
                HostingPaymentOrder.status.in_(['pending', 'client_confirmed', 'payment_matched'])
            ).count()
            data['pending_orders'] = pending_count

            result.append(data)

        return jsonify({'success': True, 'clients': result})
    except Exception as e:
        logger.error(f"Hosting clients xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/clients', methods=['POST'])
@role_required('admin')
def api_hosting_client_create():
    """Yangi hosting mijoz qo'shish"""
    try:
        data = request.get_json()
        if not data or not data.get('name'):
            return jsonify({'success': False, 'error': 'Ism kiritilmagan'}), 400

        client = HostingClient(
            name=data['name'],
            phone=data.get('phone'),
            telegram_chat_id=data.get('telegram_chat_id'),
            telegram_username=data.get('telegram_username'),
            droplet_id=data.get('droplet_id'),
            droplet_name=data.get('droplet_name'),
            server_ip=data.get('server_ip'),
            monthly_price_uzs=Decimal(str(data.get('monthly_price_uzs', 0))),
            payment_day=data.get('payment_day', 1),
            notes=data.get('notes'),
            status_token=secrets.token_hex(16)
        )
        db.session.add(client)
        db.session.commit()

        return jsonify({'success': True, 'client': client.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Hosting client yaratishda xato: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/clients/<int:client_id>', methods=['PUT'])
@role_required('admin')
def api_hosting_client_update(client_id):
    """Hosting mijozni tahrirlash"""
    try:
        client = HostingClient.query.get(client_id)
        if not client:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        data = request.get_json()
        if data.get('name'):
            client.name = data['name']
        if 'phone' in data:
            client.phone = data['phone']
        if 'telegram_chat_id' in data:
            client.telegram_chat_id = data['telegram_chat_id']
        if 'telegram_username' in data:
            client.telegram_username = data['telegram_username']
        if 'droplet_id' in data:
            client.droplet_id = data['droplet_id']
        if 'droplet_name' in data:
            client.droplet_name = data['droplet_name']
        if 'server_ip' in data:
            client.server_ip = data['server_ip']
        if 'monthly_price_uzs' in data:
            client.monthly_price_uzs = Decimal(str(data['monthly_price_uzs']))
        if 'payment_day' in data:
            client.payment_day = data['payment_day']
        if 'is_active' in data:
            client.is_active = data['is_active']
        if 'notes' in data:
            client.notes = data['notes']
        if 'balance' in data:
            client.balance = Decimal(str(data['balance']))

        db.session.commit()

        # Agar balans musbat bo'lsa va server o'chiq bo'lsa - avtomatik yoqish
        if float(client.balance or 0) > 0 and client.droplet_id and client.server_status in ('suspended', 'off'):
            try:
                from digitalocean_manager import DigitalOceanManager
                do_mgr = DigitalOceanManager()
                status = do_mgr.get_droplet_status(client.droplet_id)
                if status == 'off':
                    success = do_mgr.power_on(client.droplet_id)
                    if success:
                        client.server_status = 'active'
                        db.session.commit()
                        logger.info(f"ğŸŸ¢ Server yoqildi (mijoz tahrirlandi): {client.name} (droplet: {client.droplet_id})")
                elif status == 'active':
                    client.server_status = 'active'
                    db.session.commit()
            except Exception as e:
                logger.error(f"DO server yoqishda xato ({client.name}): {e}")

        return jsonify({'success': True, 'client': client.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Hosting client yangilashda xato: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/clients/<int:client_id>/add-balance', methods=['POST'])
@role_required('admin')
def api_hosting_client_add_balance(client_id):
    """Mijoz balansiga summa qo'shish"""
    try:
        client = HostingClient.query.get(client_id)
        if not client:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        data = request.get_json()
        amount = data.get('amount')
        if not amount or float(amount) <= 0:
            return jsonify({'success': False, 'error': 'Summa musbat bo\'lishi kerak'}), 400

        client.balance = (client.balance or Decimal('0')) + Decimal(str(amount))
        db.session.commit()

        # Server yoqish (agar o'chiq yoki suspended bo'lsa va balans musbat bo'lsa)
        server_msg = ""
        if client.droplet_id and float(client.balance) > 0 and client.server_status in ('suspended', 'off'):
            try:
                from digitalocean_manager import DigitalOceanManager
                do_mgr = DigitalOceanManager()
                status = do_mgr.get_droplet_status(client.droplet_id)
                if status == 'off':
                    success = do_mgr.power_on(client.droplet_id)
                    if success:
                        client.server_status = 'active'
                        db.session.commit()
                        server_msg = "Server avtomatik yoqildi"
                        logger.info(f"ğŸŸ¢ Server yoqildi (balans to'ldirildi): {client.name} (droplet: {client.droplet_id})")
                    else:
                        server_msg = "Server yoqishda xato - qo'lda yoqing"
                        logger.warning(f"âš ï¸ Server yoqishda xato: {client.name}")
                elif status == 'active':
                    client.server_status = 'active'
                    db.session.commit()
                    server_msg = "Server allaqachon yoqiq"
            except Exception as e:
                logger.error(f"DO server yoqishda xato ({client.name}): {e}")
                server_msg = f"Server yoqishda xato: {str(e)[:50]}"

        return jsonify({'success': True, 'balance': float(client.balance), 'server_msg': server_msg})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Hosting balance qo'shishda xato: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/clients/<int:client_id>', methods=['DELETE'])
@role_required('admin')
def api_hosting_client_delete(client_id):
    """Hosting mijozni o'chirish"""
    try:
        client = HostingClient.query.get(client_id)
        if not client:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        db.session.delete(client)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Hosting client o'chirishda xato: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/orders', methods=['GET'])
@role_required('admin')
def api_hosting_orders():
    """To'lov buyurtmalari ro'yxati"""
    try:
        status_filter = request.args.get('status')
        query = HostingPaymentOrder.query

        if status_filter:
            query = query.filter_by(status=status_filter)

        orders = query.order_by(HostingPaymentOrder.created_at.desc()).limit(100).all()
        result = [o.to_dict() for o in orders]

        return jsonify({'success': True, 'orders': result})
    except Exception as e:
        logger.error(f"Hosting orders xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/orders/<int:order_id>/approve', methods=['POST'])
@role_required('admin')
def api_hosting_order_approve(order_id):
    """Buyurtmani tasdiqlash (web paneldan)"""
    try:
        order = HostingPaymentOrder.query.get(order_id)
        if not order:
            return jsonify({'success': False, 'error': 'Buyurtma topilmadi'}), 404

        if order.status == 'approved':
            return jsonify({'success': False, 'error': 'Allaqachon tasdiqlangan'}), 400

        client = HostingClient.query.get(order.client_id)
        now = get_tashkent_time()

        # To'lov yaratish
        period_start = now.date()
        end_month = period_start.month + order.months
        end_year = period_start.year + (end_month - 1) // 12
        end_month = ((end_month - 1) % 12) + 1
        try:
            period_end = period_start.replace(year=end_year, month=end_month)
        except ValueError:
            import calendar
            last_day = calendar.monthrange(end_year, end_month)[1]
            period_end = period_start.replace(year=end_year, month=end_month, day=min(period_start.day, last_day))

        payment = HostingPayment(
            client_id=client.id,
            order_id=order.id,
            amount_uzs=order.amount_uzs,
            months_paid=order.months,
            payment_date=now,
            period_start=period_start,
            period_end=period_end,
            confirmed_by=session.get('user_name', 'admin')
        )
        db.session.add(payment)

        # Balansni yangilash
        client.balance = (client.balance or Decimal('0')) + order.amount_uzs

        order.status = 'approved'
        order.approved_at = now
        db.session.commit()

        # Server yoqish
        server_msg = ""
        if client and client.droplet_id:
            try:
                from digitalocean_manager import DigitalOceanManager
                do_mgr = DigitalOceanManager()
                status = do_mgr.get_droplet_status(client.droplet_id)
                if status == 'off':
                    do_mgr.power_on(client.droplet_id)
                    client.server_status = 'active'
                    db.session.commit()
                    server_msg = "Server yoqildi"
            except Exception as e:
                server_msg = f"Server yoqishda xato: {str(e)[:50]}"

        return jsonify({
            'success': True,
            'server_msg': server_msg,
            'payment': payment.to_dict()
        })
    except Exception as e:
        db.session.rollback()
        logger.error(f"Order approve xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/payments', methods=['GET'])
@role_required('admin')
def api_hosting_payments():
    """To'lovlar tarixi"""
    try:
        client_id = request.args.get('client_id', type=int)
        query = HostingPayment.query

        if client_id:
            query = query.filter_by(client_id=client_id)

        payments = query.order_by(HostingPayment.payment_date.desc()).limit(100).all()
        result = [p.to_dict() for p in payments]

        return jsonify({'success': True, 'payments': result})
    except Exception as e:
        logger.error(f"Hosting payments xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/payments', methods=['POST'])
@role_required('admin')
def api_hosting_payment_manual():
    """Qo'lda to'lov qo'shish"""
    try:
        data = request.get_json()
        if not data or not data.get('client_id') or not data.get('amount_uzs'):
            return jsonify({'success': False, 'error': 'client_id va amount_uzs kerak'}), 400

        client = HostingClient.query.get(data['client_id'])
        if not client:
            return jsonify({'success': False, 'error': 'Mijoz topilmadi'}), 404

        now = get_tashkent_time()
        months = data.get('months_paid', 1)
        period_start = now.date()
        end_month = period_start.month + months
        end_year = period_start.year + (end_month - 1) // 12
        end_month = ((end_month - 1) % 12) + 1
        try:
            period_end = period_start.replace(year=end_year, month=end_month)
        except ValueError:
            import calendar
            last_day = calendar.monthrange(end_year, end_month)[1]
            period_end = period_start.replace(year=end_year, month=end_month, day=min(period_start.day, last_day))

        payment = HostingPayment(
            client_id=client.id,
            amount_uzs=Decimal(str(data['amount_uzs'])),
            months_paid=months,
            payment_date=now,
            period_start=period_start,
            period_end=period_end,
            confirmed_by=session.get('user_name', 'admin'),
            notes=data.get('notes', 'Qo\'lda qo\'shildi')
        )
        db.session.add(payment)

        # Balansni yangilash
        client.balance = (client.balance or Decimal('0')) + Decimal(str(data['amount_uzs']))

        db.session.commit()

        # Server yoqish (agar o'chiq yoki suspended bo'lsa va balans musbat bo'lsa)
        server_msg = ""
        if client.droplet_id and float(client.balance) > 0 and client.server_status in ('suspended', 'off'):
            try:
                from digitalocean_manager import DigitalOceanManager
                do_mgr = DigitalOceanManager()
                status = do_mgr.get_droplet_status(client.droplet_id)
                if status == 'off':
                    success = do_mgr.power_on(client.droplet_id)
                    if success:
                        client.server_status = 'active'
                        db.session.commit()
                        server_msg = "Server avtomatik yoqildi"
                        logger.info(f"ğŸŸ¢ Server yoqildi (qo'lda to'lov): {client.name} (droplet: {client.droplet_id})")
                    else:
                        server_msg = "Server yoqishda xato - qo'lda yoqing"
                elif status == 'active':
                    client.server_status = 'active'
                    db.session.commit()
                    server_msg = "Server allaqachon yoqiq"
            except Exception as e:
                logger.error(f"DO server yoqishda xato ({client.name}): {e}")
                server_msg = f"Server yoqishda xato: {str(e)[:50]}"

        result = payment.to_dict()
        result['server_msg'] = server_msg
        return jsonify({'success': True, 'payment': result})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Manual payment xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/droplets', methods=['GET'])
@role_required('admin')
def api_hosting_droplets():
    """DigitalOcean dropletlar ro'yxati"""
    try:
        from digitalocean_manager import DigitalOceanManager
        do_mgr = DigitalOceanManager()

        if not do_mgr.is_token_valid():
            return jsonify({'success': False, 'error': 'DO API token noto\'g\'ri'}), 400

        droplets = do_mgr.get_all_droplets_info()
        return jsonify({'success': True, 'droplets': droplets})
    except Exception as e:
        logger.error(f"DO droplets xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/droplets/<int:droplet_id>/power', methods=['POST'])
@role_required('admin')
def api_hosting_droplet_power(droplet_id):
    """Droplet power on/off"""
    try:
        data = request.get_json()
        action = data.get('action', 'on')

        from digitalocean_manager import DigitalOceanManager
        do_mgr = DigitalOceanManager()

        if action == 'on':
            success = do_mgr.power_on(droplet_id)
        elif action == 'off':
            success = do_mgr.shutdown(droplet_id)
        elif action == 'reboot':
            success = do_mgr.reboot(droplet_id)
        else:
            return jsonify({'success': False, 'error': 'Noto\'g\'ri action'}), 400

        # Client statusini yangilash
        client = HostingClient.query.filter_by(droplet_id=droplet_id).first()
        if client and success:
            if action == 'on':
                client.server_status = 'active'
            elif action == 'off':
                client.server_status = 'off'
            db.session.commit()

        return jsonify({'success': success})
    except Exception as e:
        logger.error(f"Droplet power xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/hosting/stats', methods=['GET'])
@role_required('admin')
def api_hosting_stats():
    """Hosting statistikasi"""
    try:
        total_clients = HostingClient.query.filter_by(is_active=True).count()
        total_revenue = db.session.query(
            db.func.sum(HostingPayment.amount_uzs)
        ).scalar() or 0

        # Joriy oy tushumlari
        now = get_tashkent_time()
        month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        monthly_revenue = db.session.query(
            db.func.sum(HostingPayment.amount_uzs)
        ).filter(
            HostingPayment.payment_date >= month_start
        ).scalar() or 0

        # Pending buyurtmalar
        pending_orders = HostingPaymentOrder.query.filter(
            HostingPaymentOrder.status.in_(['pending', 'client_confirmed', 'payment_matched'])
        ).count()

        # Muddati o'tganlar
        today = now.date()
        overdue_clients = 0
        active_clients = HostingClient.query.filter_by(is_active=True).all()
        for client in active_clients:
            last_p = HostingPayment.query.filter_by(
                client_id=client.id
            ).order_by(HostingPayment.payment_date.desc()).first()
            if last_p and last_p.period_end and today > last_p.period_end:
                overdue_clients += 1
            elif not last_p:
                overdue_clients += 1

        return jsonify({
            'success': True,
            'stats': {
                'total_clients': total_clients,
                'total_revenue': float(total_revenue),
                'monthly_revenue': float(monthly_revenue),
                'pending_orders': pending_orders,
                'overdue_clients': overdue_clients
            }
        })
    except Exception as e:
        logger.error(f"Hosting stats xatosi: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# Monitoring routes qo'shish
try:
    from monitoring import setup_monitoring_routes
    setup_monitoring_routes(app, db)
    logger.info("âœ… Monitoring tizimi ishga tushdi")
except Exception as e:
    logger.warning(f"âš ï¸ Monitoring tizimi ishga tushmadi: {e}")


# AI Chat endpoint olib tashlandi


# ============================================
# XARAJATLAR (EXPENSES) ROUTES
# ============================================

@app.route('/xarajatlar')
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def xarajatlar():
    """Xarajatlar sahifasi"""
    current_user = get_current_user()
    if current_user and current_user.role not in ('admin', 'kassir'):
        allowed_locations = current_user.allowed_locations or []
        allowed_store_ids = extract_location_ids(allowed_locations, 'store')
        allowed_wh_ids = extract_location_ids(allowed_locations, 'warehouse')
        stores = [s.to_dict() for s in Store.query.filter(Store.id.in_(allowed_store_ids)).order_by(Store.name).all()] if allowed_store_ids else []
        warehouses = [w.to_dict() for w in Warehouse.query.filter(Warehouse.id.in_(allowed_wh_ids)).order_by(Warehouse.name).all()] if allowed_wh_ids else []
    else:
        stores = [s.to_dict() for s in Store.query.order_by(Store.name).all()]
        warehouses = [w.to_dict() for w in Warehouse.query.order_by(Warehouse.name).all()]
    # Kategoriyalar va dam olish kunlari - Settings jadvalidan
    cat_setting = Settings.query.filter_by(key='expense_categories').first()
    if cat_setting and cat_setting.value:
        import json as _json
        try:
            expense_categories = _json.loads(cat_setting.value)
        except Exception:
            expense_categories = ['Ijara', 'Maosh', 'Kommunal', 'Transport', 'Boshqa']
    else:
        expense_categories = ['Ijara', 'Maosh', 'Kommunal', 'Transport', 'Boshqa']
    rest_setting = Settings.query.filter_by(key='expense_rest_days').first()
    if rest_setting and rest_setting.value:
        try:
            expense_rest_days = _json.loads(rest_setting.value)
        except Exception:
            expense_rest_days = []
    else:
        expense_rest_days = []
    return render_template('xarajatlar.html', stores=stores, warehouses=warehouses,
                           expense_categories=expense_categories,
                           expense_rest_days=expense_rest_days)


@app.route('/api/expenses', methods=['GET'])
@role_required('admin', 'kassir', 'sotuvchi', 'omborchi')
def api_get_expenses():
    """Xarajatlar ro'yxati"""
    try:
        current_user = get_current_user()
        start_date = request.args.get('start_date')
        end_date = request.args.get('end_date')
        category = request.args.get('category')
        location_type = request.args.get('location_type')
        location_id = request.args.get('location_id')

        query = Expense.query

        # Sotuvchi faqat o'ziga ruxsat etilgan joylashuvlarni ko'radi
        if current_user and current_user.role not in ('admin', 'kassir'):
            allowed_locations = current_user.allowed_locations or []
            allowed_store_ids = extract_location_ids(allowed_locations, 'store')
            allowed_wh_ids = extract_location_ids(allowed_locations, 'warehouse')
            from sqlalchemy import or_, and_
            filters = []
            if allowed_store_ids:
                filters.append(and_(Expense.location_type == 'store', Expense.location_id.in_(allowed_store_ids)))
            if allowed_wh_ids:
                filters.append(and_(Expense.location_type == 'warehouse', Expense.location_id.in_(allowed_wh_ids)))
            if filters:
                query = query.filter(or_(*filters))
            else:
                query = query.filter(Expense.id == -1)  # hech narsa ko'rinmasin

        if start_date:
            query = query.filter(Expense.expense_date >= start_date)
        if end_date:
            query = query.filter(Expense.expense_date <= end_date + ' 23:59:59')
        if category and category != 'all':
            query = query.filter(Expense.category == category)
        if location_type:
            query = query.filter(Expense.location_type == location_type)
        if location_id:
            query = query.filter(Expense.location_id == int(location_id))

        expenses = query.order_by(Expense.expense_date.desc()).all()
        total_usd = sum(float(e.amount_usd or 0) for e in expenses)
        total_uzs = sum(float(e.amount_uzs or 0) for e in expenses)

        return jsonify({
            'success': True,
            'expenses': [e.to_dict() for e in expenses],
            'total_usd': round(total_usd, 2),
            'total_uzs': round(total_uzs, 2),
            'count': len(expenses)
        })
    except Exception as e:
        logger.error(f"Xarajatlar olishda xatolik: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/expenses', methods=['POST'])
@role_required('admin', 'kassir', 'sotuvchi')
def api_add_expense():
    """Yangi xarajat qo'shish"""
    try:
        current_user = get_current_user()
        data = request.get_json()

        if not data:
            return jsonify({'success': False, 'error': 'Ma\'lumot yuborilmadi'}), 400

        amount_usd = float(data.get('amount_usd') or 0)
        amount_uzs = float(data.get('amount_uzs') or 0)

        if amount_usd <= 0 and amount_uzs <= 0:
            return jsonify({'success': False, 'error': 'Summa kiritilishi shart'}), 400

        # Sotuvchi faqat o'z joylashuviga xarajat qo'sha oladi
        if current_user and current_user.role not in ('admin', 'kassir'):
            allowed_locations = current_user.allowed_locations or []
            req_type = data.get('location_type')
            req_id = data.get('location_id')
            if req_type and req_id:
                allowed_ids = extract_location_ids(allowed_locations, req_type)
                if allowed_ids is not None and int(req_id) not in allowed_ids:
                    return jsonify({'success': False, 'error': 'Bu joylashuv uchun ruxsat yo\'q'}), 403

        # Title: category dan auto yoki bo'sh string
        auto_title = (data.get('category') or 'Xarajat').strip()

        # Location
        location_type = data.get('location_type')
        location_id = data.get('location_id')
        location_name = None
        if location_type == 'store' and location_id:
            store = Store.query.get(int(location_id))
            location_name = store.name if store else None
        elif location_type == 'warehouse' and location_id:
            wh = Warehouse.query.get(int(location_id))
            location_name = wh.name if wh else None

        if data.get('is_monthly') and data.get('expense_month'):
            import calendar
            year, month = map(int, data['expense_month'].split('-'))
            days_in_month = calendar.monthrange(year, month)[1]
            rest_days = [int(d) for d in data.get('rest_days', [])]  # 0=Dushanba ... 6=Yakshanba
            working_days = [
                datetime(year, month, day)
                for day in range(1, days_in_month + 1)
                if datetime(year, month, day).weekday() not in rest_days
            ]
            count = len(working_days)
            if count == 0:
                return jsonify({'success': False, 'error': 'Bu oyda ish kuni topilmadi'}), 400
            daily_usd = round(amount_usd / count, 6) if amount_usd else 0
            daily_uzs = round(amount_uzs / count, 2) if amount_uzs else 0

            for expense_date in working_days:
                expense = Expense(
                    title=auto_title,
                    amount_usd=daily_usd,
                    amount_uzs=daily_uzs,
                    category=data.get('category', '').strip() or None,
                    description=data.get('description', '').strip() or None,
                    expense_date=expense_date,
                    created_by=current_user.username if current_user else 'unknown',
                    location_type=location_type or None,
                    location_id=int(location_id) if location_id else None,
                    location_name=location_name,
                )
                db.session.add(expense)
            db.session.commit()
            return jsonify({'success': True, 'monthly': True, 'days': count})

        expense_date_str = data.get('expense_date')
        expense_date = get_tashkent_time()
        if expense_date_str:
            try:
                parsed_date = datetime.strptime(expense_date_str, '%Y-%m-%d')
                now = get_tashkent_time()
                # Sanani foydalanuvchi tanlagan kun bilan almashtiramiz, vaqtni hozirgi saqlaymiz
                expense_date = now.replace(year=parsed_date.year, month=parsed_date.month, day=parsed_date.day)
            except ValueError:
                pass

        expense = Expense(
            title=auto_title,
            amount_usd=amount_usd,
            amount_uzs=amount_uzs,
            category=data.get('category', '').strip() or None,
            description=data.get('description', '').strip() or None,
            expense_date=expense_date,
            created_by=current_user.username if current_user else 'unknown',
            location_type=location_type or None,
            location_id=int(location_id) if location_id else None,
            location_name=location_name,
        )
        db.session.add(expense)
        db.session.commit()

        return jsonify({'success': True, 'expense': expense.to_dict()})
    except Exception as e:
        db.session.rollback()
        logger.error(f"Xarajat qo'shishda xatolik: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/expenses/<int:expense_id>', methods=['DELETE'])
@role_required('admin')
def api_delete_expense(expense_id):
    """Xarajatni o'chirish"""
    try:
        expense = Expense.query.get_or_404(expense_id)
        db.session.delete(expense)
        db.session.commit()
        return jsonify({'success': True})
    except Exception as e:
        db.session.rollback()
        return jsonify({'success': False, 'error': str(e)}), 500


if __name__ == '__main__':
    # Telegram bot scheduler ni ishga tushirish
    try:
        from debt_scheduler import init_debt_scheduler
        logger.info("ğŸ¤– Telegram bot scheduler ishga tushirilmoqda...")
        init_debt_scheduler(app, db)
        logger.info("âœ… Telegram bot scheduler ishga tushdi")
    except Exception as e:
        logger.warning(f"âš ï¸ Telegram bot scheduler ishga tushmadi: {e}")

    # Debug rejimi faqat development uchun - production'da False bo'lishi shart
    debug_mode = os.getenv('FLASK_DEBUG', 'False').lower() == 'true'
    app.run(debug=debug_mode, host='0.0.0.0', port=5000)
