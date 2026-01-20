# -*- coding: utf-8 -*-
"""
Telegram Bot - Qarz eslatmalari tizimi
Mijozlarga qarz haqida avtomatik xabar yuborish
"""
import os
import logging
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict
from telegram import Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, CallbackQueryHandler
from telegram.error import TelegramError
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class DebtTelegramBot:
    """Qarz eslatmalari uchun Telegram Bot"""
    
    def __init__(self):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN')
        self.admin_chat_ids = self._parse_admin_ids()
        self.bot = None
        
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
    
    async def send_debt_reminder(
        self,
        chat_id: int,
        customer_name: str,
        debt_usd: float,
        debt_uzs: float,
        location_name: str,
        sale_date: Optional[datetime] = None
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
            
            # Xabar matni
            message = (
                f"💰 <b>QARZ ESLATMASI</b>\n\n"
                f"Hurmatli {customer_name}!\n\n"
                f"📍 Joylashuv: {location_name}\n"
                f"💵 Qarz: {debt_usd_str}\n"
                f"💸 Qarz: {debt_uzs_str}{date_str}\n\n"
                f"Iltimos, qarzingizni to'lashni unutmang.\n"
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
    
    async def send_payment_confirmation(
        self,
        chat_id: int,
        customer_name: str,
        paid_usd: float,
        paid_uzs: float,
        remaining_usd: float,
        remaining_uzs: float,
        location_name: str
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
            if remaining_usd <= 0:
                # Qarz to'liq to'landi
                message = (
                    f"✅ <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"📍 Joylashuv: {location_name}\n"
                    f"💵 To'langan: ${paid_usd:,.2f}\n"
                    f"💸 To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"🎉 <b>Qarzingiz to'liq to'landi!</b>\n"
                    f"Rahmat! 🙏"
                )
            else:
                # Qisman to'lov
                message = (
                    f"✅ <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"📍 Joylashuv: {location_name}\n"
                    f"💵 To'langan: ${paid_usd:,.2f}\n"
                    f"💸 To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"📊 <b>Qolgan qarz:</b>\n"
                    f"💵 ${remaining_usd:,.2f}\n"
                    f"💸 {remaining_uzs:,.0f} so'm\n\n"
                    f"Rahmat! 🙏"
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
                f"💵 Umumiy qarz: ${total_debt_usd:,.2f}\n"
                f"💸 Umumiy qarz: {total_debt_uzs:,.0f} so'm\n\n"
                f"━━━━━━━━━━━━━━━━━━━\n\n"
            )
            
            # Har bir qarzni qo'shish (maksimum 20 ta)
            for i, debt in enumerate(debts_data[:20], 1):
                message += (
                    f"{i}. <b>{debt['customer_name']}</b>\n"
                    f"   📍 {debt['location_name']}\n"
                    f"   💵 ${debt['debt_usd']:,.2f}\n"
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
    """Start command handler"""
    await update.message.reply_text(
        "Assalomu alaykum! 👋\n\n"
        "Bu qarz eslatmalari botidir.\n"
        "Qarzingizni tekshirish uchun telefon raqamingizni yuboring."
    )

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
            # Mijozni qidirish
            customer = None
            for p in possible_phones:
                logger.info(f"   - Format: '{p}' bilan qidirilmoqda...")
                customer = Customer.query.filter(
                    db.or_(
                        Customer.phone == p,
                        Customer.phone.like(f"%{phone[-9:]}")
                    )
                ).first()
                if customer:
                    logger.info(f"✅ Mijoz topildi: {customer.name} (ID: {customer.id}, Phone DB: '{customer.phone}')")
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
                    f"   💵 ${debt_usd:,.2f}\n"
                    f"   💸 {debt_uzs:,.0f} so'm"
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
                    f"💵 ${total_usd:,.2f}\n"
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
        
        # Message handler - telefon raqam uchun
        from telegram.ext import MessageHandler, filters
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
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

def get_bot_instance() -> DebtTelegramBot:
    """Bot instanceni olish (singleton pattern)"""
    global _bot_instance
    if _bot_instance is None:
        _bot_instance = DebtTelegramBot()
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
