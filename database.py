# -*- coding: utf-8 -*-
"""Umumiy ma'lumotlar bazasi obyekti va yordamchi funksiyalar.

Bu modul app.py va models.py o'rtasidagi aylanma importlarni (circular import)
oldini olish uchun SQLAlchemy `db` obyektini hamda model va route'lar uchun
umumiy helperlarni saqlaydi.
"""
import os
import time
import logging
from datetime import datetime

import pytz
from flask_sqlalchemy import SQLAlchemy

# SQLAlchemy obyekti - app bilan keyinroq app.py da db.init_app(app) orqali bog'lanadi
db = SQLAlchemy()

logger = logging.getLogger(__name__)

# O'zbekiston vaqt zonasi
TASHKENT_TZ = pytz.timezone('Asia/Tashkent')


def get_tashkent_time():
    """O'zbekiston vaqtini qaytaradi"""
    return datetime.now(TASHKENT_TZ)


# Konstantalar
DEFAULT_PHONE_PLACEHOLDER = os.getenv('DEFAULT_PHONE_PLACEHOLDER', 'Telefon kiritilmagan')
CACHE_DURATION = 300  # 5 daqiqa

# Joylashuv nomi keshi - N+1 so'rovlarni kamaytirish uchun
_location_name_cache: dict = {}
_location_name_cache_time: dict = {}


def _get_location_name_cached(loc_type: str, loc_id: int) -> str:
    """Store yoki Warehouse nomini keshdan olish (5 daqiqa TTL).
    Transfer va SaleItem modellaridagi N+1 so'rovlarni hal qiladi."""
    # Aylanma importni oldini olish uchun lazy import (runtime'da chaqiriladi)
    from models import Store, Warehouse
    if not loc_id:
        return "Noma'lum"
    key = (loc_type, loc_id)
    now = time.time()
    if key in _location_name_cache and now - _location_name_cache_time.get(key, 0) < CACHE_DURATION:
        return _location_name_cache[key]
    if loc_type == 'store':
        obj = Store.query.get(loc_id)
        name = obj.name if obj else "Noma'lum do'kon"
    elif loc_type == 'warehouse':
        obj = Warehouse.query.get(loc_id)
        name = obj.name if obj else "Noma'lum ombor"
    else:
        name = "Noma'lum"
    _location_name_cache[key] = name
    _location_name_cache_time[key] = now
    return name
