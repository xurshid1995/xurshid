# -*- coding: utf-8 -*-
"""
Qarz Scheduler - Avtomatik qarz eslatmalari
Kunlik, haftalik va real-time qarz eslatmalarini yuborish
"""
import os
import logging
import asyncio
import time as time_module
from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import List, Dict, Optional
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from dotenv import load_dotenv

# Flask app va modellarni import qilish
import sys
sys.path.append(os.path.dirname(__file__))

from telegram_bot import get_bot_instance

load_dotenv()
logger = logging.getLogger(__name__)


class DebtScheduler:
    """Qarz eslatmalarini boshqarish tizimi"""
    
    def __init__(self, app=None, db=None):
        """
        Args:
            app: Flask application
            db: SQLAlchemy database instance
        """
        self.app = app
        self.db = db
        self.bot = get_bot_instance(db=db)  # db ni o'tkazamiz
        self.scheduler = BackgroundScheduler()
        
        # Sozlamalar
        self.daily_reminder_time = os.getenv('DEBT_REMINDER_TIME', '10:00')
        self.weekly_report_day = int(os.getenv('WEEKLY_REPORT_DAY', '1'))  # 1 = Dushanba
        self.minimum_debt_amount = float(os.getenv('MINIMUM_DEBT_AMOUNT', '1'))  # USD
        
        logger.info("✅ DebtScheduler initialized")
    
    def _get_customers_with_debt(self) -> List[Dict]:
        """
        Qarzli mijozlar ro'yxatini olish
        
        Returns:
            List[Dict]: Qarzli mijozlar ma'lumotlari
        """
        if not self.app or not self.db:
            logger.error("❌ Flask app yoki DB mavjud emas")
            return []
        
        with self.app.app_context():
            try:
                from app import Customer, Sale, Store, Warehouse
                
                # Qarzli savdolarni olish
                # FAQAT payment_due_date belgilanmagan qarzlar.
                # Muddati belgilangan qarzlar (ertaga, bugun, o'tgan) check_due_date_reminders tomonidan alohida boshqariladi.
                debts = self.db.session.query(
                    Sale.customer_id,
                    Sale.location_id,
                    Sale.location_type,
                    Sale.sale_date,
                    self.db.func.sum(Sale.debt_usd).label('total_debt_usd'),
                    self.db.func.sum(Sale.debt_amount).label('total_debt_uzs')
                ).filter(
                    Sale.payment_status == 'partial',
                    Sale.debt_usd > self.minimum_debt_amount,
                    Sale.payment_due_date.is_(None)
                ).group_by(
                    Sale.customer_id,
                    Sale.location_id,
                    Sale.location_type,
                    Sale.sale_date
                ).all()
                
                result = []
                for debt in debts:
                    if not debt.customer_id:
                        continue
                    
                    customer = Customer.query.get(debt.customer_id)
                    if not customer or not customer.telegram_chat_id:
                        continue
                    
                    # Location nomini olish
                    location_name = "Noma'lum"
                    if debt.location_type == 'store' and debt.location_id:
                        store = Store.query.get(debt.location_id)
                        location_name = store.name if store else "Do'kon"
                    elif debt.location_type == 'warehouse' and debt.location_id:
                        warehouse = Warehouse.query.get(debt.location_id)
                        location_name = warehouse.name if warehouse else "Ombor"
                    
                    result.append({
                        'customer_id': customer.id,
                        'customer_name': customer.name,
                        'phone': customer.phone,
                        'telegram_chat_id': customer.telegram_chat_id,
                        'debt_usd': float(debt.total_debt_usd or 0),
                        'debt_uzs': float(debt.total_debt_uzs or 0),
                        'location_name': location_name,
                        'sale_date': debt.sale_date
                    })
                
                logger.info(f"📊 {len(result)} ta qarzli mijoz topildi")
                return result
                
            except Exception as e:
                logger.error(f"❌ Qarzli mijozlarni olishda xatolik: {e}")
                return []
    
    def _get_reminder_time_from_db(self) -> str:
        """DB dan default_reminder_time ni o'qish, topilmasa .env dan"""
        if self.app:
            try:
                with self.app.app_context():
                    from app import Settings
                    setting = Settings.query.filter_by(key='default_reminder_time').first()
                    if setting and setting.value:
                        return setting.value
            except Exception:
                pass
        return os.getenv('DEBT_REMINDER_TIME', '10:00')

    def _check_and_run_daily_reminders(self):
        """Har daqiqa ishga tushadi: DB dagi vaqt bilan mos kelsa eslatmalar yuboradi"""
        try:
            reminder_time = self._get_reminder_time_from_db()
            hour, minute = map(int, reminder_time.split(':'))
            now = datetime.now()
            if now.hour == hour and now.minute == minute:
                logger.info(f"📅 Vaqt mos keldi ({reminder_time}) — kunlik eslatmalar yuborilmoqda...")
                self.send_daily_reminders()
        except Exception as e:
            logger.error(f"❌ Kunlik eslatma tekshirishda xatolik: {e}")

    def send_daily_reminders(self):
        """Kunlik qarz eslatmalarini yuborish (sinxron)"""
        logger.info("📅 Kunlik qarz eslatmalari — muddatli qarzlar tekshirilmoqda...")
        self.check_due_date_reminders()
    
    def send_weekly_report(self):
        """Haftalik hisobot yuborish (sinxron)"""
        logger.info("📊 Haftalik hisobot yuborilmoqda...")
        
        debts = self._get_customers_with_debt()
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.bot.send_debt_list_to_admin(debts))
            finally:
                loop.close()
        except Exception as e:
            logger.error(f"❌ Haftalik hisobot yuborishda xatolik: {e}")
        
        logger.info("✅ Haftalik hisobot yuborildi")
    
    async def send_instant_reminder(
        self,
        customer_id: int,
        debt_usd: float,
        debt_uzs: float,
        location_name: str,
        sale_date: Optional[datetime] = None
    ) -> bool:
        """
        Darhol qarz eslatmasi yuborish (savdodan keyin)
        
        Args:
            customer_id: Mijoz ID
            debt_usd: Qarz (USD)
            debt_uzs: Qarz (UZS)
            location_name: Do'kon/ombor nomi
            sale_date: Savdo sanasi
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.app or not self.db:
            return False
        
        with self.app.app_context():
            try:
                from app import Customer
                
                customer = Customer.query.get(customer_id)
                if not customer or not customer.telegram_chat_id:
                    logger.warning(
                        f"⚠️ Mijoz {customer_id} uchun telegram_chat_id yo'q"
                    )
                    return False
                
                return await self.bot.send_debt_reminder(
                    chat_id=customer.telegram_chat_id,
                    customer_name=customer.name,
                    debt_usd=debt_usd,
                    debt_uzs=debt_uzs,
                    location_name=location_name,
                    sale_date=sale_date
                )
                
            except Exception as e:
                logger.error(f"❌ Instant reminder yuborishda xatolik: {e}")
                return False
    
    def send_telegram_debt_reminder_sync(
        self,
        chat_id: int,
        customer_name: str,
        debt_usd: float,
        debt_uzs: float,
        location_name: str,
        sale_date: Optional[datetime] = None,
        customer_id: Optional[int] = None
    ) -> bool:
        """
        Sinxron telegram xabar yuborish (Flask route'lar uchun)
        
        Args:
            chat_id: Telegram chat ID
            customer_name: Mijoz ismi
            debt_usd: Qarz (USD)
            debt_uzs: Qarz (UZS)
            location_name: Do'kon/ombor nomi
            sale_date: Savdo sanasi
            customer_id: Mijoz ID (to'lov turlarini olish uchun)
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        try:
            # asyncio.run() har safar yangi loop yaratib, to'g'ri yopadi
            return asyncio.run(
                self.bot.send_debt_reminder(
                    chat_id=chat_id,
                    customer_name=customer_name,
                    debt_usd=debt_usd,
                    debt_uzs=debt_uzs,
                    location_name=location_name,
                    sale_date=sale_date,
                    customer_id=customer_id
                )
            )
        except Exception as e:
            logger.error(f"❌ Sync telegram xatolik: {e}")
            return False
    
    async def send_payment_notification(
        self,
        customer_id: int,
        paid_usd: float,
        paid_uzs: float,
        remaining_usd: float,
        remaining_uzs: float,
        location_name: str
    ) -> bool:
        """
        To'lov tasdiqlash xabarini yuborish
        
        Args:
            customer_id: Mijoz ID
            paid_usd: To'langan (USD)
            paid_uzs: To'langan (UZS)
            remaining_usd: Qolgan qarz (USD)
            remaining_uzs: Qolgan qarz (UZS)
            location_name: Do'kon/ombor nomi
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.app or not self.db:
            return False
        
        with self.app.app_context():
            try:
                from app import Customer
                
                customer = Customer.query.get(customer_id)
                if not customer or not customer.telegram_chat_id:
                    return False
                
                return await self.bot.send_payment_confirmation(
                    chat_id=customer.telegram_chat_id,
                    customer_name=customer.name,
                    paid_usd=paid_usd,
                    paid_uzs=paid_uzs,
                    remaining_usd=remaining_usd,
                    remaining_uzs=remaining_uzs,
                    location_name=location_name
                )
                
            except Exception as e:
                logger.error(f"❌ Payment notification yuborishda xatolik: {e}")
                return False
    
    def start(self):
        """Schedulerni ishga tushirish"""
        try:
            # Kunlik eslatmalar — har daqiqa DB dagi vaqtni tekshiradi
            self.scheduler.add_job(
                self._check_and_run_daily_reminders,
                CronTrigger(minute='*'),
                id='daily_reminders',
                name='Kunlik qarz eslatmalari (DB vaqti)',
                replace_existing=True
            )
            reminder_time = self._get_reminder_time_from_db()
            logger.info(f"✅ Kunlik eslatmalar: DB dan o'qiladi (hozir: {reminder_time})")
            
            # Individual eslatmalarni tekshirish (har 5 daqiqada)
            self.scheduler.add_job(
                self.check_scheduled_reminders,
                CronTrigger(minute='*/5'),
                id='scheduled_reminders',
                name='Belgilangan eslatmalarni tekshirish',
                replace_existing=True
            )
            logger.info("✅ Belgilangan eslatmalar: har 5 daqiqada tekshiriladi")
            # 09:20 muddatli eslatmalar olib tashlandi — endi 10:00 da send_daily_reminders ichida ishlaydi
            
            # Schedulerni boshlash
            self.scheduler.start()
            logger.info("✅ Scheduler ishga tushdi")
            
        except Exception as e:
            logger.error(f"❌ Scheduler ishga tushirishda xatolik: {e}")
    
    def check_scheduled_reminders(self):
        """Foydalanuvchi belgilagan eslatmalarni tekshirish va yuborish (sinxron)"""
        if not self.app or not self.db:
            logger.warning("⚠️ check_scheduled_reminders: app yoki db mavjud emas")
            return
        
        with self.app.app_context():
            try:
                from app import DebtReminder, Customer, Sale, CurrencyRate, get_tashkent_time
                
                now = get_tashkent_time()
                today = now.date()
                current_time = now.time()
                
                logger.info(f"🔍 Eslatmalar tekshirilmoqda: {today} {current_time}")
                
                # Vaqti kelgan eslatmalarni olish
                reminders = DebtReminder.query.filter(
                    DebtReminder.is_active == True,
                    DebtReminder.is_sent == False,
                    DebtReminder.reminder_date <= today
                ).all()
                
                logger.info(f"📋 Topilgan eslatmalar soni: {len(reminders)}")
                
                sent_count = 0
                
                for reminder in reminders:
                    # Bugungi eslatmalar uchun vaqtni tekshirish
                    if reminder.reminder_date == today and reminder.reminder_time > current_time:
                        logger.info(f"⏳ Hali vaqti kelmagan: {reminder.reminder_date} {reminder.reminder_time}")
                        continue  # Hali vaqti kelmagan
                    
                    customer = Customer.query.get(reminder.customer_id)
                    if not customer or not customer.telegram_chat_id:
                        logger.warning(f"⚠️ Mijoz topilmadi yoki telegram_chat_id yo'q: customer_id={reminder.customer_id}")
                        reminder.is_active = False
                        continue
                    
                    # Mijozning hali qarzi bormi va qaysi do'kondan
                    from app import Store, Warehouse
                    
                    debt_sales = Sale.query.filter(
                        Sale.customer_id == reminder.customer_id,
                        Sale.debt_usd > 0
                    ).all()
                    
                    remaining_debt = sum(float(s.debt_usd or 0) for s in debt_sales)
                    
                    if remaining_debt <= 0:
                        logger.info(f"✅ Qarz yo'q, eslatma o'chirildi: {customer.name}")
                        reminder.is_sent = True
                        reminder.is_active = False
                        continue
                    
                    # Do'kon nomini olish (birinchi qarzli savdodan)
                    location_name = "Do'kon"
                    if debt_sales:
                        sale = debt_sales[0]
                        if sale.location_type == 'store' and sale.location_id:
                            store = Store.query.get(sale.location_id)
                            if store:
                                location_name = store.name
                        elif sale.location_type == 'warehouse' and sale.location_id:
                            warehouse = Warehouse.query.get(sale.location_id)
                            if warehouse:
                                location_name = warehouse.name
                    
                    # Kurs
                    rate = CurrencyRate.query.order_by(CurrencyRate.id.desc()).first()
                    exchange_rate = float(rate.rate) if rate else 13000
                    debt_uzs = remaining_debt * exchange_rate
                    
                    logger.info(f"📨 Eslatma yuborilmoqda: {customer.name}, qarz: ${remaining_debt}, joy: {location_name}")
                    
                    # Telegram yuborish
                    try:
                        success = self.bot.send_debt_reminder_sync(
                            chat_id=customer.telegram_chat_id,
                            customer_name=customer.name,
                            debt_usd=remaining_debt,
                            debt_uzs=debt_uzs,
                            location_name=location_name,
                            customer_id=customer.id
                        )
                        
                        if success:
                            reminder.is_sent = True
                            reminder.sent_at = get_tashkent_time()
                            sent_count += 1
                            logger.info(f"✅ Belgilangan eslatma yuborildi: {customer.name}")
                        else:
                            logger.error(f"❌ Eslatma yuborilmadi (False): {customer.name}")
                        
                        time_module.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"❌ Eslatma yuborishda xatolik ({customer.name}): {e}")
                
                self.db.session.commit()
                
                if sent_count > 0:
                    logger.info(f"📊 Belgilangan eslatmalar: {sent_count} ta yuborildi")
                else:
                    logger.info(f"📊 Yuborish kerak bo'lgan eslatma yo'q")
                
            except Exception as e:
                self.db.session.rollback()
                logger.error(f"❌ Belgilangan eslatmalarni tekshirishda xatolik: {e}")
    
    def check_due_date_reminders(self):
        """
        Muddatli qarz eslatmalari:
        1. Muddatdan 1 kun oldin - oldindan ogohlantirish
        2. Muddat kuni - bugun to'lash kerak
        3. Muddat o'tgan - har kuni eslatma (qarz to'lanmaguncha)
        """
        if not self.app or not self.db:
            logger.warning("⚠️ check_due_date_reminders: app yoki db mavjud emas")
            return
        
        with self.app.app_context():
            try:
                from app import Sale, Customer, Store, Warehouse, CurrencyRate, get_tashkent_time
                
                now = get_tashkent_time()
                today = now.date()
                tomorrow = today + timedelta(days=1)
                
                logger.info(f"📅 Muddatli qarz eslatmalari tekshirilmoqda: {today}")
                
                # Qarzli savdolarni olish (payment_due_date belgilangan)
                debt_sales = Sale.query.filter(
                    Sale.debt_usd > 0,
                    Sale.payment_status == 'partial',
                    Sale.payment_due_date.isnot(None),
                    Sale.customer_id.isnot(None)
                ).all()
                
                logger.info(f"📋 Muddatli qarzlar: {len(debt_sales)} ta")
                
                # Kurs
                rate = CurrencyRate.query.order_by(CurrencyRate.id.desc()).first()
                exchange_rate = float(rate.rate) if rate else 13000
                
                sent_count = 0
                
                for sale in debt_sales:
                    customer = Customer.query.get(sale.customer_id)
                    if not customer or not customer.telegram_chat_id:
                        continue
                    
                    due_date = sale.payment_due_date
                    debt_usd = float(sale.debt_usd or 0)
                    debt_uzs = debt_usd * exchange_rate
                    
                    # Do'kon nomini olish
                    location_name = "Do'kon"
                    if sale.location_type == 'store' and sale.location_id:
                        store = Store.query.get(sale.location_id)
                        if store:
                            location_name = store.name
                    elif sale.location_type == 'warehouse' and sale.location_id:
                        warehouse = Warehouse.query.get(sale.location_id)
                        if warehouse:
                            location_name = warehouse.name
                    
                    # Qaysi xabar turini aniqlash
                    message_type = None
                    if due_date == tomorrow:
                        message_type = 'pre_reminder'  # 1 kun oldin
                    elif due_date == today:
                        message_type = 'due_today'  # Bugun muddat
                    elif due_date < today:
                        message_type = 'overdue'  # Muddat o'tgan
                    
                    if not message_type:
                        continue  # Hali muddat kelmagan
                    
                    # Xabar yuborish
                    try:
                        debt_usd_str = f"${debt_usd:,.2f}"
                        debt_uzs_str = f"{debt_uzs:,.0f}"
                        due_date_str = due_date.strftime('%d.%m.%Y')
                        today_str = today.strftime('%d.%m.%Y')
                        
                        if message_type == 'pre_reminder':
                            message = (
                                f"⚠️ <b>QARZ ESLATMASI</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"Hurmatli <b>{customer.name}</b>!\n\n"
                                f"📍 {location_name} dokonidan\n\n"
                                f"💵 Qarzingiz: <b>{debt_usd_str}</b>\n\n"
                                f"📅 Qarzingizni to'lash muddati <b>ertaga ({due_date_str})</b>\n\n"
                                f"Iltimos, ertaga qarzingizni to'lashni unutmang!\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"Qarz bu sizga omonat 🤝\n"
                                f"Rahmat! 🙏"
                            )
                        elif message_type == 'due_today':
                            message = (
                                f"💰 <b>QARZ TO'LASH MUDDATI BUGUN!</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"Hurmatli <b>{customer.name}</b>!\n\n"
                                f"📍 {location_name} dokonidan\n\n"
                                f"💵 Qarzingiz: <b>{debt_usd_str}</b>\n\n"
                                f"📅 Qarzingizni to'lash muddati <b>bugun ({today_str})</b>\n"
                                f"🔔 Iltimos, qarzingizni bugunoq to'lang!\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"Qarz bu sizga omonat 🤝\n"
                                f"Rahmat! 🙏"
                            )
                        elif message_type == 'overdue':
                            days_overdue = (today - due_date).days
                            message = (
                                f"🔴 <b>DIQQAT! QARZ MUDDATI O'TGAN!</b>\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                                f"Hurmatli <b>{customer.name}</b>!\n\n"
                                f"📍 {location_name} dokonidan\n\n"
                                f"💵 Qarzingiz: <b>{debt_usd_str}</b>\n\n"
                                f"📅 To'lash muddati: <b>{due_date_str}</b>\n"
                                f"❗ Muddatdan <b>{days_overdue} kun</b> o'tgan!\n\n"
                                f"🚨 Iltimos, qarzingizni imkon qadar tezroq to'lang!\n\n"
                                f"━━━━━━━━━━━━━━━━━━━━\n"
                                f"Qarz bu sizga omonat 🤝\n"
                                f"Rahmat! 🙏"
                            )
                        
                        # Telegram yuborish
                        url = f"https://api.telegram.org/bot{self.bot.token}/sendMessage"
                        payload = {
                            'chat_id': customer.telegram_chat_id,
                            'text': message,
                            'parse_mode': 'HTML'
                        }
                        
                        import requests
                        response = requests.post(url, json=payload, timeout=10)
                        
                        if response.status_code == 200:
                            sent_count += 1
                            logger.info(f"✅ Muddatli eslatma yuborildi ({message_type}): {customer.name}")
                        else:
                            logger.error(f"❌ Telegram xatosi ({customer.name}): {response.status_code}")
                        
                        time_module.sleep(1)
                        
                    except Exception as e:
                        logger.error(f"❌ Muddatli eslatma yuborishda xatolik ({customer.name}): {e}")
                
                if sent_count > 0:
                    logger.info(f"📊 Muddatli eslatmalar: {sent_count} ta yuborildi")
                else:
                    logger.info("📊 Muddatli eslatma yuborish kerak emas")

                # Adminlarga yig'ma xabar yuborish
                self._send_admin_due_date_summary(debt_sales, today, exchange_rate)

            except Exception as e:
                logger.error(f"❌ Muddatli eslatmalarni tekshirishda xatolik: {e}")

    def _send_admin_due_date_summary(self, debt_sales, today, exchange_rate):
        """Adminlarga bugungi va muddati o'tgan qarzlar haqida yig'ma xabar yuborish"""
        if not self.bot or not self.bot.admin_chat_ids:
            return

        try:
            from app import Customer, Store, Warehouse

            due_today_list = []
            overdue_list = []
            tomorrow = today + timedelta(days=1)
            pre_reminder_list = []

            for sale in debt_sales:
                customer = Customer.query.get(sale.customer_id)
                if not customer:
                    continue

                due_date = sale.payment_due_date
                debt_usd = float(sale.debt_usd or 0)

                location_name = "Do'kon"
                if sale.location_type == 'store' and sale.location_id:
                    store = Store.query.get(sale.location_id)
                    if store:
                        location_name = store.name
                elif sale.location_type == 'warehouse' and sale.location_id:
                    warehouse = Warehouse.query.get(sale.location_id)
                    if warehouse:
                        location_name = warehouse.name

                entry = {
                    'name': customer.name,
                    'phone': customer.phone or '—',
                    'debt_usd': debt_usd,
                    'location': location_name,
                    'due_date': due_date,
                }

                if due_date == today:
                    due_today_list.append(entry)
                elif due_date < today:
                    entry['days_overdue'] = (today - due_date).days
                    overdue_list.append(entry)
                elif due_date == tomorrow:
                    pre_reminder_list.append(entry)

            import requests

            def send_to_admins(text):
                url = f"https://api.telegram.org/bot{self.bot.token}/sendMessage"
                for chat_id in self.bot.admin_chat_ids:
                    try:
                        requests.post(url, json={
                            'chat_id': chat_id,
                            'text': text,
                            'parse_mode': 'HTML'
                        }, timeout=10)
                        time_module.sleep(0.3)
                    except Exception as e:
                        logger.error(f"❌ Admin xabar yuborishda xatolik: {e}")

            # Bugun to'lash muddati kelgan mijozlar
            if due_today_list:
                lines = [f"💰 <b>BUGUN TO'LOV MUDDATI KELGAN MIJOZLAR</b>\n<b>Sana: {today.strftime('%d.%m.%Y')}</b>\n{'━'*22}"]
                total = 0
                for i, e in enumerate(due_today_list, 1):
                    lines.append(f"\n{i}. <b>{e['name']}</b>\n   📞 {e['phone']}\n   💵 ${e['debt_usd']:,.2f} | 🏪 {e['location']}")
                    total += e['debt_usd']
                lines.append(f"\n{'━'*22}\nJami: <b>{len(due_today_list)} ta mijoz</b> | <b>${total:,.2f}</b>")
                send_to_admins("\n".join(lines))

            # Ertaga muddati keluvchi mijozlar
            if pre_reminder_list:
                lines = [f"⚠️ <b>ERTAGA TO'LOV MUDDATI KELADI</b>\n<b>Sana: {tomorrow.strftime('%d.%m.%Y')}</b>\n{'━'*22}"]
                total = 0
                for i, e in enumerate(pre_reminder_list, 1):
                    lines.append(f"\n{i}. <b>{e['name']}</b>\n   📞 {e['phone']}\n   💵 ${e['debt_usd']:,.2f} | 🏪 {e['location']}")
                    total += e['debt_usd']
                lines.append(f"\n{'━'*22}\nJami: <b>{len(pre_reminder_list)} ta mijoz</b> | <b>${total:,.2f}</b>")
                send_to_admins("\n".join(lines))

            # Muddati o'tgan mijozlar
            if overdue_list:
                overdue_list.sort(key=lambda x: x['days_overdue'], reverse=True)
                lines = [f"🔴 <b>MUDDATI O'TGAN QARZLAR</b>\n{'━'*22}"]
                total = 0
                for i, e in enumerate(overdue_list, 1):
                    lines.append(f"\n{i}. <b>{e['name']}</b>\n   📞 {e['phone']}\n   💵 ${e['debt_usd']:,.2f} | 🏪 {e['location']}\n   ❗ {e['days_overdue']} kun o'tgan ({e['due_date'].strftime('%d.%m.%Y')})")
                    total += e['debt_usd']
                lines.append(f"\n{'━'*22}\nJami: <b>{len(overdue_list)} ta mijoz</b> | <b>${total:,.2f}</b>")
                send_to_admins("\n".join(lines))

            logger.info("✅ Adminlarga yig'ma qarz xabari yuborildi")

        except Exception as e:
            logger.error(f"❌ Admin yig'ma xabar yuborishda xatolik: {e}")
    
    def stop(self):
        """Schedulerni to'xtatish"""
        if self.scheduler.running:
            self.scheduler.shutdown()
            logger.info("🛑 Scheduler to'xtatildi")


# Singleton instance
_scheduler_instance = None

def get_scheduler_instance(app=None, db=None) -> DebtScheduler:
    """Scheduler instanceni olish"""
    global _scheduler_instance
    if _scheduler_instance is None:
        _scheduler_instance = DebtScheduler(app=app, db=db)
    return _scheduler_instance


# Flask app integration
def init_debt_scheduler(app, db):
    """
    Flask app bilan integratsiya
    
    Usage:
        from debt_scheduler import init_debt_scheduler
        init_debt_scheduler(app, db)
    """
    scheduler = get_scheduler_instance(app=app, db=db)
    scheduler.start()
    
    # Cleanup on shutdown
    import atexit
    atexit.register(lambda: scheduler.stop())
    
    logger.info("✅ Debt Scheduler Flask app bilan integratsiya qilindi")
    return scheduler


if __name__ == "__main__":
    # Test
    print("🧪 Debt Scheduler test")
    print("⚠️ Flask app bilan ishlatish kerak")
    
    # Test uchun:
    # from app import app, db
    # scheduler = init_debt_scheduler(app, db)
    # 
    # # Test - darhol eslatma yuborish
    # import asyncio
    # asyncio.run(
    #     scheduler.send_instant_reminder(
    #         customer_id=1,
    #         debt_usd=100,
    #         debt_uzs=1300000,
    #         location_name="Test Do'kon"
    #     )
    # )
