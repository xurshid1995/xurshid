# -*- coding: utf-8 -*-
"""
Telegram Bot - Qarz eslatmalari tizimi
Mijozlarga qarz haqida avtomatik xabar yuborish
"""
import os
import logging
import requests
import random
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict
from sqlalchemy import text
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler, MessageHandler, filters
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

# Tasdiqlash kodlari uchun xotira (production'da Redis yoki DB ishlatish kerak)
verification_codes = {}

class DebtTelegramBot:
    """Qarz eslatmalari uchun Telegram Bot"""
    
    def __init__(self, db=None):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.admin_chat_ids = self._parse_admin_ids()
        self.bot = None
        self.db = db  # Database instance
        
        if not self.token:
            logger.warning("⚠️ TELEGRAM_BOT_TOKEN .env faylida sozlanmagan!")
        else:
            try:
                self.bot = Bot(token=self.token)
                logger.info("✅ Telegram bot muvaffaqiyatli ishga tushdi")
            except Exception as e:
                logger.error(f"❌ Telegram bot xatosi: {e}")
    
    def _parse_admin_ids(self) -> List[int]:
        """Admin chat ID larini parse qilish"""
        admin_ids_str = os.getenv('TELEGRAM_ADMIN_CHAT_IDS', '')
        if not admin_ids_str:
            return []
        
        try:
            return [int(id_.strip()) for id_ in admin_ids_str.split(',') if id_.strip()]
        except ValueError:
            logger.warning("⚠️ TELEGRAM_ADMIN_CHAT_IDS noto'g'ri formatda")
            return []
    
    def _get_payment_details_sync(self, customer_id: int) -> str:
        """
        Mijozning qarzli savdolari bo'yicha to'lov turlarini olish (sync versiya)
        
        Args:
            customer_id: Mijoz ID
            
        Returns:
            str: To'lov turlari HTML formati
        """
        try:
            if not self.db:
                return ""
            
            result = self.db.session.execute(
                text("""
                    SELECT 
                        COALESCE(SUM(cash_usd), 0) as total_cash,
                        COALESCE(SUM(click_usd), 0) as total_click,
                        COALESCE(SUM(terminal_usd), 0) as total_terminal
                    FROM sales
                    WHERE customer_id = :customer_id AND debt_usd > 0
                """),
                {"customer_id": customer_id}
            ).fetchone()
            
            if not result:
                return ""
            
            cash = float(result[0] or 0)
            click = float(result[1] or 0)
            terminal = float(result[2] or 0)
            
            # To'lov turlarini formatlash
            payments = []
            if cash > 0:
                payments.append(f"💵 Naqd: ${cash:,.2f}")
            if click > 0:
                payments.append(f"📱 Click: ${click:,.2f}")
            if terminal > 0:
                payments.append(f"💳 Terminal: ${terminal:,.2f}")
            
            if payments:
                return "\n\n<b>📊 To'lov turlari:</b>\n" + "\n".join(payments)
            
            return ""
            
        except Exception as e:
            logger.error(f"❌ To'lov ma'lumotlarini olishda xatolik: {e}")
            return ""
    
    async def _get_payment_details(self, customer_id: int) -> str:
        """
        Mijozning qarzli savdolari bo'yicha to'lov turlarini olish
        
        Args:
            customer_id: Mijoz ID
            
        Returns:
            str: To'lov turlari HTML formati
        """
        try:
            # Database'dan to'lov ma'lumotlarini olish
            if not hasattr(self, 'db') or not self.db:
                return ""
            
            query = text("""
                SELECT 
                    COALESCE(SUM(cash_usd), 0) as total_cash,
                    COALESCE(SUM(click_usd), 0) as total_click,
                    COALESCE(SUM(terminal_usd), 0) as total_terminal,
                    COALESCE(SUM(debt_usd), 0) as total_debt
                FROM sales
                WHERE customer_id = :customer_id AND debt_usd > 0
            """)
            
            result = self.db.session.execute(query, {'customer_id': customer_id}).fetchone()
            
            if not result:
                return ""
            
            # To'lov turlarini formatlash (faqat to'lov turlarini ko'rsatish, qarzni emas)
            payments = []
            if result.total_cash > 0:
                payments.append(f"💵 Naqd: ${result.total_cash:,.2f}")
            if result.total_click > 0:
                payments.append(f"📱 Click: ${result.total_click:,.2f}")
            if result.total_terminal > 0:
                payments.append(f"💳 Terminal: ${result.total_terminal:,.2f}")
            
            if payments:
                return "\n\n<b>📊 To'lov turlari:</b>\n" + "\n".join(payments)
            
            return ""
            
        except Exception as e:
            logger.error(f"❌ To'lov ma'lumotlarini olishda xatolik: {e}")
            return ""
    
    async def send_debt_reminder(
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
        Mijozga qarz eslatmasi yuborish
        
        Args:
            chat_id: Telegram chat ID
            customer_name: Mijoz ismi
            debt_usd: Qarz miqdori (USD)
            debt_uzs: Qarz miqdori (UZS)
            location_name: Do'kon/ombor nomi
            sale_date: Savdo sanasi
            customer_id: Mijoz ID (to'lov turlarini olish uchun)
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.bot:
            logger.error("Bot ishga tushmagan")
            return False
        
        try:
            # Qarz miqdorini formatlash
            debt_usd_str = f"${debt_usd:,.2f}"
            debt_uzs_str = f"{debt_uzs:,.0f} so'm"
            
            # Sana formatlash
            date_str = ""
            if sale_date:
                date_str = f"\n📅 Savdo sanasi: {sale_date.strftime('%d.%m.%Y')}"
            
            # Xabar matni (qarz eslatmasida to'lov turlari ko'rsatilmaydi)
            message = (
                f"💰 <b>QARZ ESLATMASI</b>\n\n"
                f"Hurmatli {customer_name}!\n\n"
                f"📍 Joylashuv: {location_name}\n\n"
                f"💸 Qarz: {debt_uzs:,.0f} so'm{date_str}\n\n"
                f"Iltimos, qarzingizni to'lashni unutmang. Qarz bu omonat.\n"
                f"Rahmat! 🙏"
            )
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML'
            )
            
            logger.info(f"✅ Qarz eslatmasi yuborildi: {customer_name} (Chat ID: {chat_id})")
            return True
            
        except TelegramError as e:
            logger.error(f"❌ Telegram xatosi ({customer_name}): {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Xatolik ({customer_name}): {e}")
            return False
    
    def send_debt_reminder_sync(
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
        Mijozga qarz eslatmasi yuborish (sync versiya - Flask uchun)
        
        Args:
            chat_id: Telegram chat ID
            customer_name: Mijoz ismi
            debt_usd: Qarz miqdori (USD)
            debt_uzs: Qarz miqdori (UZS)
            location_name: Do'kon/ombor nomi
            sale_date: Savdo sanasi
            customer_id: Mijoz ID (to'lov turlarini ko'rsatish uchun)
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.token:
            logger.error("Bot token yo'q")
            return False
        
        try:
            # Qarz miqdorini formatlash
            debt_usd_str = f"${debt_usd:,.2f}"
            debt_uzs_str = f"{debt_uzs:,.0f} so'm"
            
            # Sana formatlash
            date_str = ""
            if sale_date:
                date_str = f"\n📅 Savdo sanasi: {sale_date.strftime('%d.%m.%Y')}"
            
            # Xabar matni (qarz eslatmasida to'lov turlari ko'rsatilmaydi)
            message = (
                f"💰 <b>QARZ ESLATMASI</b>\n\n"
                f"Hurmatli {customer_name}!\n\n"
                f"📍 Joylashuv: {location_name}\n\n"
                f"💸 Qarz: {debt_uzs_str}{date_str}\n\n"
                f"Iltimos, qarzingizni to'lashni unutmang. Qarz bu omonat.\n"
                f"Rahmat! 🙏"
            )
            
            # HTTP API orqali yuborish
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"✅ Qarz eslatmasi yuborildi: {customer_name} (Chat ID: {chat_id})")
                return True
            else:
                logger.error(f"❌ Telegram API xatosi ({customer_name}): {response.status_code} - {response.text}")
                return False
            
        except Exception as e:
            logger.error(f"❌ Xatolik ({customer_name}): {e}")
            return False
    
    async def send_payment_confirmation(
        self,
        chat_id: int,
        customer_name: str,
        paid_usd: float,
        paid_uzs: float,
        remaining_usd: float,
        remaining_uzs: float,
        location_name: str,
        customer_id: Optional[int] = None
    ) -> bool:
        """
        To'lov tasdiqlash xabarini yuborish
        
        Args:
            chat_id: Telegram chat ID
            customer_name: Mijoz ismi
            paid_usd: To'langan summa (USD)
            paid_uzs: To'langan summa (UZS)
            remaining_usd: Qolgan qarz (USD)
            remaining_uzs: Qolgan qarz (UZS)
            location_name: Do'kon/ombor nomi
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.bot:
            return False
        
        try:
            payment_details = ""
            if customer_id and remaining_usd > 0:
                payment_details = await self._get_payment_details(customer_id)
            
            if remaining_usd <= 0:
                # Qarz to'liq to'landi
                message = (
                    f"✅ <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"📍 Joylashuv: {location_name}\n"
                    f"💵 To'langan: ${paid_usd:,.2f}\n"
                    f"💸 To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"🎉 <b>Qarzingiz to'liq to'landi!</b>\n"
                    f"Rahmat! 🙏 "
                )
            else:
                # Qisman to'lov
                message = (
                    f"✅ <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"📍 Joylashuv: {location_name}\n"
                    f"💸 To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"📊 <b>Qolgan qarz:</b>\n"
                    f"💸 {remaining_uzs:,.0f} so'm\n"
                    f"{payment_details}\n"
                    f"Rahmat! 🙏 Iltimos qolgan qarzingizniham tez orada Tolang chunki qarz bu sizga omonat"
                )
            
            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML'
            )
            
            logger.info(f"✅ To'lov tasdiq xabari yuborildi: {customer_name}")
            return True
            
        except Exception as e:
            logger.error(f"❌ To'lov tasdiq xabarida xatolik: {e}")
            return False
    
    def send_payment_confirmation_sync(
        self,
        chat_id: int,
        customer_name: str,
        previous_debt_usd: float,
        previous_debt_uzs: float,
        paid_usd: float,
        paid_uzs: float,
        remaining_usd: float,
        remaining_uzs: float,
        customer_id: Optional[int] = None,
        cash_uzs: float = 0,
        click_uzs: float = 0,
        terminal_uzs: float = 0
    ) -> bool:
        """
        To'lov tasdiqlash xabarini yuborish (sync versiya - Flask uchun)
        
        Args:
            chat_id: Telegram chat ID
            customer_name: Mijoz ismi
            previous_debt_usd: Avvalgi qarz (USD)
            previous_debt_uzs: Avvalgi qarz (UZS)
            paid_usd: To'langan summa (USD)
            paid_uzs: To'langan summa (UZS)
            remaining_usd: Qolgan qarz (USD)
            remaining_uzs: Qolgan qarz (UZS)
            customer_id: Mijoz ID (to'lov turlarini ko'rsatish uchun)
            cash_uzs: Naqd to'lov (UZS)
            click_uzs: Click to'lov (UZS)
            terminal_uzs: Terminal to'lov (UZS)
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.token:
            logger.error("Bot token yo'q")
            return False
        
        try:
            # Aynan shu to'lovning turlarini ko'rsatish
            payment_details = ""
            if cash_uzs > 0 or click_uzs > 0 or terminal_uzs > 0:
                payment_details = "\n\n<b>📊 To'lov turlari:</b>\n"
                if cash_uzs > 0:
                    payment_details += f"💵 Naqd: {cash_uzs:,.0f} so'm\n"
                if click_uzs > 0:
                    payment_details += f"📱 Click: {click_uzs:,.0f} so'm\n"
                if terminal_uzs > 0:
                    payment_details += f"💳 Terminal: {terminal_uzs:,.0f} so'm"
            
            if remaining_usd <= 0:
                # Qarz to'liq to'landi
                message = (
                    f"✅ <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"💰 Avvalgi qarz: {previous_debt_uzs:,.0f} so'm\n\n"
                    f"✅ To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"🎉 <b>Qarzingiz to'liq to'landi!</b>\n\n"
                    f"Rahmat! 🙏"
                )
            else:
                # Qisman to'lov
                message = (
                    f"✅ <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"💰 Avvalgi qarz: {previous_debt_uzs:,.0f} so'm\n\n"
                    f"✅ To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"📊 Qolgan qarz: {remaining_uzs:,.0f} so'm{payment_details}\n\n"
                    f"Rahmat! 🙏"
                )
            
            # HTTP API orqali yuborish
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }
            
            response = requests.post(url, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"✅ To'lov tasdiq xabari yuborildi: {customer_name} (Chat ID: {chat_id})")
                return True
            else:
                logger.error(f"❌ Telegram API xatosi ({customer_name}): {response.status_code} - {response.text}")
                return False
            
        except Exception as e:
            logger.error(f"❌ To'lov tasdiq xabarida xatolik ({customer_name}): {e}")
            return False
    
    async def send_debt_list_to_admin(self, debts_data: List[Dict]) -> bool:
        """
        Adminlarga qarzlar ro'yxatini yuborish
        
        Args:
            debts_data: Qarzlar ro'yxati
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.bot or not self.admin_chat_ids:
            logger.warning("⚠️ Admin chat IDs mavjud emas")
            return False
        
        if not debts_data:
            message = "📊 <b>QARZLAR HISOBOTI</b>\n\n✅ Hozirda qarz yo'q"
        else:
            total_debt_usd = sum(d['debt_usd'] for d in debts_data)
            total_debt_uzs = sum(d['debt_uzs'] for d in debts_data)
            
            message = (
                f"📊 <b>QARZLAR HISOBOTI</b>\n"
                f"📅 Sana: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                f"👥 Jami qarzlar: {len(debts_data)} ta\n"
                f"� Umumiy qarz: {total_debt_uzs:,.0f} so'm\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
            )
            
            # Har bir qarzni qo'shish (maksimum 20 ta)
            for i, debt in enumerate(debts_data[:20], 1):
                message += (
                    f"{i}. <b>{debt['customer_name']}</b>\n"
                    f"   📍 {debt['location_name']}\n"
                    f"   💸 {debt['debt_uzs']:,.0f} so'm\n"
                    f"   📱 {debt.get('phone', 'Telefon yo\'q')}\n\n"
                )
            
            if len(debts_data) > 20:
                message += f"\n... va yana {len(debts_data) - 20} ta qarz"
        
        try:
            success = True
            for admin_id in self.admin_chat_ids:
                try:
                    await self.bot.send_message(
                        chat_id=admin_id,
                        text=message,
                        parse_mode='HTML'
                    )
                    logger.info(f"✅ Admin {admin_id} ga hisobot yuborildi")
                except Exception as e:
                    logger.error(f"❌ Admin {admin_id} ga yuborishda xatolik: {e}")
                    success = False
            
            return success
            
        except Exception as e:
            logger.error(f"❌ Admin hisobotida xatolik: {e}")
            return False
    
    async def send_daily_summary(
        self,
        total_debts: int,
        total_amount_usd: float,
        total_amount_uzs: float,
        new_debts: int = 0,
        paid_today: int = 0
    ) -> bool:
        """
        Kunlik xulosa yuborish (adminlarga)
        
        Args:
            total_debts: Jami qarzlar soni
            total_amount_usd: Jami qarz (USD)
            total_amount_uzs: Jami qarz (UZS)
            new_debts: Bugun yangi qarzlar
            paid_today: Bugun to'langan qarzlar
            
        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.bot or not self.admin_chat_ids:
            return False
        
        try:
            message = (
                f"📊 <b>KUNLIK HISOBOT</b>\n"
                f"📅 {datetime.now().strftime('%d.%m.%Y')}\n\n"
                f"👥 Jami qarzlar: {total_debts} ta\n"
                f"💵 Umumiy: ${total_amount_usd:,.2f}\n"
                f"💸 Umumiy: {total_amount_uzs:,.0f} so'm\n\n"
                f"📈 Bugun yangi: {new_debts} ta\n"
                f"✅ Bugun to'landi: {paid_today} ta\n"
            )
            
            for admin_id in self.admin_chat_ids:
                try:
                    await self.bot.send_message(
                        chat_id=admin_id,
                        text=message,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"❌ Admin {admin_id} ga kunlik hisobot yuborishda xatolik: {e}")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Kunlik hisobot yuborishda xatolik: {e}")
            return False


# Bot commandlari (agar mijozlar bot bilan interact qilishini hohlasangiz)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler with phone number button"""
    # Telefon raqam yuborish tugmasi
    keyboard = [
        [KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)
    
    await update.message.reply_text(
        "Assalomu alaykum! 👋\n\n"
        "Bu qarz eslatmalari botidir.\n\n"
        "Qarzingizni tekshirish uchun pastdagi tugmani bosing:",
        reply_markup=reply_markup
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telefon raqam contact orqali kelganda"""
    from app import app, db, Customer
    
    contact = update.message.contact
    chat_id = update.effective_chat.id
    phone_number = contact.phone_number
    
    logger.info(f"📱 Contact qabul qilindi: Chat ID {chat_id}, Phone: {phone_number}")
    
    # Telefon raqamni tozalash
    phone = ''.join(filter(str.isdigit, phone_number))
    
    with app.app_context():
        try:
            # Mijozni qidirish
            customer = None
            all_customers = Customer.query.all()
            
            for cust in all_customers:
                if cust.phone:
                    clean_db_phone = ''.join(filter(str.isdigit, cust.phone))
                    if clean_db_phone[-9:] == phone[-9:]:
                        customer = cust
                        logger.info(f"✅ Mijoz topildi: {customer.name} (ID: {customer.id})")
                        break
            
            if not customer:
                await update.message.reply_text(
                    "❌ Sizning raqamingiz tizimda topilmadi.\n\n"
                    "Iltimos, do'konga murojaat qiling.",
                    reply_markup=ReplyKeyboardRemove()
                )
                return
            
            # Tasdiqlash kodini generatsiya qilish
            verification_code = str(random.randint(100000, 999999))
            verification_codes[chat_id] = {
                'code': verification_code,
                'customer_id': customer.id,
                'phone': phone_number
            }
            
            logger.info(f"🔐 Tasdiqlash kodi yaratildi: {verification_code} for customer {customer.id}")
            
            # Tasdiqlash kodini yuborish
            await update.message.reply_text(
                f"✅ Telefon raqam tasdiqlandi!\n\n"
                f"🔐 Tasdiqlash kodi: <code>{verification_code}</code>\n\n"
                f"Ushbu kodni kiriting:",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardRemove()
            )
            
        except Exception as e:
            logger.error(f"❌ Contact handle qilishda xatolik: {e}")
            await update.message.reply_text(
                "❌ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.",
                reply_markup=ReplyKeyboardRemove()
            )

async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tasdiqlash kodini tekshirish"""
    from app import app, db, Customer, Sale, Store, Warehouse
    
    chat_id = update.effective_chat.id
    code = update.message.text.strip()
    
    # Kod formatini tekshirish (6 ta raqam)
    if not code.isdigit() or len(code) != 6:
        return
    
    # Tasdiqlash kodini tekshirish
    if chat_id not in verification_codes:
        await update.message.reply_text(
            "❌ Avval telefon raqamingizni yuboring.\n\n"
            "/start ni bosing."
        )
        return
    
    saved_data = verification_codes[chat_id]
    
    if saved_data['code'] != code:
        await update.message.reply_text(
            "❌ Noto'g'ri tasdiqlash kodi!\n\n"
            "Iltimos, to'g'ri kodni kiriting."
        )
        return
    
    # Tasdiqlash muvaffaqiyatli
    customer_id = saved_data['customer_id']
    
    with app.app_context():
        try:
            customer = Customer.query.get(customer_id)
            if not customer:
                await update.message.reply_text("❌ Xatolik: Mijoz topilmadi")
                return
            
            # Telegram chat ID ni saqlash
            customer.telegram_chat_id = chat_id
            db.session.commit()
            
            logger.info(f"✅ Mijoz tasdiqlandi va telegram_chat_id saqlandi: {customer.name} (Chat ID: {chat_id})")
            
            # Tasdiqlash kodini o'chirish
            del verification_codes[chat_id]
            
            # Qarzlarni ko'rsatish
            debts = db.session.query(
                Sale.location_id,
                Sale.location_type,
                db.func.sum(Sale.debt_usd).label('total_debt_usd'),
                db.func.sum(Sale.debt_amount).label('total_debt_uzs')
            ).filter(
                Sale.customer_id == customer.id,
                Sale.payment_status == 'partial',
                Sale.debt_usd > 0
            ).group_by(
                Sale.location_id,
                Sale.location_type
            ).all()
            
            # Doimiy tugmalarni tayyorlash
            keyboard = [
                [KeyboardButton("💰 Qarzni tekshirish")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            if not debts:
                await update.message.reply_text(
                    f"✅ Tasdiqlash muvaffaqiyatli!\n\n"
                    f"Assalomu alaykum, {customer.name}!\n\n"
                    f"🎉 Sizda qarz yo'q!\n\n"
                    f"Rahmat! 🙏",
                    reply_markup=reply_markup
                )
                return
            
            # Qarzlar haqida xabar - faqat jami qarzni ko'rsatish
            total_usd = 0
            total_uzs = 0
            
            for debt in debts:
                debt_usd = float(debt.total_debt_usd or 0)
                debt_uzs = float(debt.total_debt_uzs or 0)
                total_usd += debt_usd
                total_uzs += debt_uzs
            
            message = (
                f"✅ Tasdiqlash muvaffaqiyatli!\n\n"
                f"Assalomu alaykum, {customer.name}!\n\n"
                f"💰 <b>Sizning qarzingiz:</b>\n\n"
                f"💸 {total_uzs:,.0f} so'm\n\n"
                f"Iltimos, qarzingizni to'lashni unutmang.\n"
                f"Rahmat! 🙏"
            )
            
            # Doimiy tugmalarni ko'rsatish
            keyboard = [
                [KeyboardButton("💰 Qarzni tekshirish")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
            
            await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup)
            
        except Exception as e:
            logger.error(f"❌ Tasdiqlashda xatolik: {e}")
            await update.message.reply_text("❌ Xatolik yuz berdi")

async def check_debt_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Qarzni tekshirish tugmasi bosilganda"""
    from app import app, db, Customer, Sale, Store, Warehouse
    
    chat_id = update.effective_chat.id
    
    with app.app_context():
        try:
            # Mijozni telegram_chat_id bo'yicha topish
            customer = Customer.query.filter_by(telegram_chat_id=chat_id).first()
            
            if not customer:
                # Telefon raqam tugmasini ko'rsatish
                keyboard = [
                    [KeyboardButton("📱 Telefon raqamni yuborish", request_contact=True)]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
                
                await update.message.reply_text(
                    "❌ Siz hali ro'yxatdan o'tmagansiz.\n\n"
                    "Iltimos, telefon raqamingizni yuboring:",
                    reply_markup=reply_markup
                )
                return
            
            # Qarzlarni hisoblash
            debts = db.session.query(
                Sale.location_id,
                Sale.location_type,
                db.func.sum(Sale.debt_usd).label('total_debt_usd'),
                db.func.sum(Sale.debt_amount).label('total_debt_uzs')
            ).filter(
                Sale.customer_id == customer.id,
                Sale.payment_status == 'partial',
                Sale.debt_usd > 0
            ).group_by(
                Sale.location_id,
                Sale.location_type
            ).all()
            
            if not debts:
                await update.message.reply_text(
                    f"Assalomu alaykum, {customer.name}!\n\n"
                    f"🎉 Sizda qarz yo'q!\n\n"
                    f"Rahmat! 🙏"
                )
                return
            
            # Qarzlar haqida xabar - faqat jami qarzni ko'rsatish
            total_usd = 0
            total_uzs = 0
            
            for debt in debts:
                debt_usd = float(debt.total_debt_usd or 0)
                debt_uzs = float(debt.total_debt_uzs or 0)
                total_usd += debt_usd
                total_uzs += debt_uzs
            
            message = (
                f"Assalomu alaykum, {customer.name}!\n\n"
                f"💰 <b>Sizning qarzingiz:</b>\n\n"
                f"💸 {total_uzs:,.0f} so'm\n\n"
                f"Iltimos, qarzingizni to'lashni unutmang.\n"
                f"Rahmat! 🙏"
            )
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"❌ Qarzni tekshirishda xatolik: {e}")
            await update.message.reply_text("❌ Xatolik yuz berdi")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    await update.message.reply_text(
        "📱 <b>Bot buyruqlari:</b>\n\n"
        "/start - Botni boshlash\n"
        "/help - Yordam\n"
        "/mydebt - Qarzimni ko'rish\n\n"
        "Savollar uchun: +998 XX XXX XX XX",
        parse_mode='HTML'
    )

async def my_debt_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mijoz qarzini ko'rsatish"""
    chat_id = update.effective_chat.id
    
    await update.message.reply_text(
        "📱 Qarzingizni tekshirish uchun telefon raqamingizni yuboring.\n\n"
        "Format: +998901234567\n"
        "yoki: 998901234567\n"
        "yoki: 901234567"
    )

async def handle_phone_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telefon raqam orqali qarzni tekshirish"""
    from app import app, db, Customer, Sale
    
    chat_id = update.effective_chat.id
    message_text = update.message.text.strip()
    
    # Telefon raqamni tozalash
    phone = ''.join(filter(str.isdigit, message_text))
    
    logger.info(f"📱 Telefon qidirish: Chat ID {chat_id}, Kiritilgan: '{message_text}', Tozalangan: '{phone}'")
    
    # Turli formatlarni qabul qilish
    possible_phones = []
    if len(phone) == 12 and phone.startswith('998'):
        possible_phones.append(f"+{phone}")
        possible_phones.append(phone)
        possible_phones.append(phone[3:])  # 998 ni olib tashlash
    elif len(phone) == 9:
        possible_phones.append(f"+998{phone}")
        possible_phones.append(f"998{phone}")
        possible_phones.append(phone)
    else:
        possible_phones.append(message_text)
    
    logger.info(f"🔍 Qidirilayotgan formatlar: {possible_phones}")
    
    with app.app_context():
        try:
            # Mijozni qidirish - database'dagi telefon raqamlarni tozalash bilan
            customer = None
            
            # Birinchi usul: to'g'ridan-to'g'ri qidirish
            for p in possible_phones:
                logger.info(f"   - Format: '{p}' bilan qidirilmoqda...")
                customer = Customer.query.filter(
                    db.or_(
                        Customer.phone == p,
                        Customer.phone.like(f"%{phone[-9:]}")
                    )
                ).first()
                if customer:
                    logger.info(f"✅ Mijoz topildi (to'g'ridan-to'g'ri): {customer.name} (ID: {customer.id}, Phone DB: '{customer.phone}')")
                    break
            
            # Agar topilmasa, barcha mijozlarni sanab chiqib, telefon raqamlarni tozalab qidirish
            if not customer:
                logger.info(f"   - Barcha mijozlar ichidan raqamni tozalab qidirilmoqda...")
                all_customers = Customer.query.all()
                for cust in all_customers:
                    if cust.phone:
                        # Database'dagi telefon raqamdan barcha belgilarni olib tashlash
                        clean_db_phone = ''.join(filter(str.isdigit, cust.phone))
                        # Oxirgi 9 raqamni solishtirish
                        if clean_db_phone[-9:] == phone[-9:]:
                            customer = cust
                            logger.info(f"✅ Mijoz topildi (tozalab): {customer.name} (ID: {customer.id}, Phone DB: '{customer.phone}', Clean: '{clean_db_phone}')")
                            break
            
            if not customer:
                await update.message.reply_text(
                    "❌ Sizning raqamingiz tizimda topilmadi.\n\n"
                    "Iltimos, to'g'ri telefon raqam kiriting yoki "
                    "do'konga murojaat qiling."
                )
                return
            
            # Mijoz telegram_chat_id ni yangilash
            if not customer.telegram_chat_id or customer.telegram_chat_id != chat_id:
                customer.telegram_chat_id = chat_id
                db.session.commit()
            
            # Qarzlarni hisoblash
            debts = db.session.query(
                Sale.location_id,
                Sale.location_type,
                db.func.sum(Sale.debt_usd).label('total_debt_usd'),
                db.func.sum(Sale.debt_amount).label('total_debt_uzs')
            ).filter(
                Sale.customer_id == customer.id,
                Sale.payment_status == 'partial',
                Sale.debt_usd > 0
            ).group_by(
                Sale.location_id,
                Sale.location_type
            ).all()
            
            if not debts:
                await update.message.reply_text(
                    f"✅ Assalomu alaykum, {customer.name}!\n\n"
                    f"🎉 Sizda qarz yo'q!\n\n"
                    f"Rahmat! 🙏"
                )
                return
            
            # Qarzlar haqida xabar
            from app import Store, Warehouse
            
            total_usd = 0
            total_uzs = 0
            debt_details = []
            
            for debt in debts:
                debt_usd = float(debt.total_debt_usd or 0)
                debt_uzs = float(debt.total_debt_uzs or 0)
                total_usd += debt_usd
                total_uzs += debt_uzs
                
                # Location nomini olish
                location_name = "Do'kon"
                if debt.location_type == 'store' and debt.location_id:
                    store = Store.query.get(debt.location_id)
                    location_name = store.name if store else "Do'kon"
                elif debt.location_type == 'warehouse' and debt.location_id:
                    warehouse = Warehouse.query.get(debt.location_id)
                    location_name = warehouse.name if warehouse else "Ombor"
                
                debt_details.append(
                    f"📍 {location_name}\n"
                    f"   � {debt_uzs:,.0f} so'm"
                )
            
            message = (
                f"💰 <b>QARZ MA'LUMOTLARI</b>\n\n"
                f"Hurmatli {customer.name}!\n\n"
            )
            
            if len(debt_details) > 1:
                message += "<b>Qarzlar ro'yxati:</b>\n\n"
                message += "\n\n".join(debt_details)
                message += (
                    f"\n\n━━━━━━━━━━━━━━━━━━━\n"
                    f"<b>JAMI:</b>\n"
                    f"💸 {total_uzs:,.0f} so'm\n\n"
                )
            else:
                message += debt_details[0] + "\n\n"
            
            message += (
                f"Iltimos, qarzingizni to'lashni unutmang.\n"
                f"Rahmat! 🙏"
            )
            
            await update.message.reply_text(message, parse_mode='HTML')
            
        except Exception as e:
            logger.error(f"❌ Qarz tekshirishda xatolik: {e}")
            await update.message.reply_text(
                "❌ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."
            )


def create_telegram_app():
    """Telegram Application yaratish"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("❌ TELEGRAM_BOT_TOKEN topilmadi")
        return None
    
    try:
        # Application yaratish
        application = Application.builder().token(token).build()
        
        # Command handlerlar
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("mydebt", my_debt_command))
        
        # Contact handler - telefon raqam tugmasi uchun
        application.add_handler(MessageHandler(filters.CONTACT, handle_contact))
        
        # "Qarzni tekshirish" tugmasi handler
        application.add_handler(
            MessageHandler(
                filters.TEXT & filters.Regex(r'^💰 Qarzni tekshirish$'),
                check_debt_button
            )
        )
        
        # Tasdiqlash kodi handler - 6 raqamli kod uchun
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d{6}$'),
                handle_verification_code
            )
        )
        
        # Message handler - oddiy telefon raqam yozib yuborish uchun (eski usul)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^\d{6}$') & ~filters.Regex(r'^💰 Qarzni tekshirish$'),
                handle_phone_number
            )
        )
        
        logger.info("✅ Telegram application yaratildi")
        return application
        
    except Exception as e:
        logger.error(f"❌ Telegram application yaratishda xatolik: {e}")
        return None


# Singleton instance
_bot_instance = None

def get_bot_instance(db=None) -> DebtTelegramBot:
    """Bot instanceni olish (singleton pattern)"""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = DebtTelegramBot(db=db)
    elif db and not _bot_instance.db:
        # Agar db berilgan bo'lsa, yangilash
        _bot_instance.db = db
    return _bot_instance


if __name__ == "__main__":
    # Test
    import asyncio
    
    async def test_bot():
        bot = get_bot_instance()
        
        # Test xabar yuborish (chat_id ni o'zgartiring)
        test_chat_id = 123456789  # O'z chat ID ingizni kiriting
        
        await bot.send_debt_reminder(
            chat_id=test_chat_id,
            customer_name="Test Mijoz",
            debt_usd=150.50,
            debt_uzs=1956500,
            location_name="Test Do'kon",
            sale_date=datetime.now()
        )
        
        print("✅ Test xabar yuborildi")
    
    # asyncio.run(test_bot())
    print("⚠️ Test uchun chat_id ni o'zgartiring va ishga tushiring")
