# -*- coding: utf-8 -*-
"""
Hosting To'lov Bot - Telegram orqali hosting to'lovlarini boshqarish

3 bosqichli tekshiruv:
1. Mijoz "To'lov" → oylarni tanlaydi → karta raqami oladi
2. Mijoz pul o'tkazadi → "✅ To'ladim" bosadi
3. Card Xabar notification keladi → Admin tasdiqlaydi → Server yoqiladi
"""
import os
import re
import random
import string
import logging
import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional, Dict, List

from dotenv import load_dotenv
from telegram import (
    Bot, Update, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove,
    LabeledPrice
)
from telegram.ext import (
    Application, CommandHandler, ContextTypes,
    CallbackQueryHandler, MessageHandler, filters,
    ConversationHandler, PreCheckoutQueryHandler
)
from telegram.error import TelegramError
import pytz

load_dotenv()
logger = logging.getLogger(__name__)

TASHKENT_TZ = pytz.timezone('Asia/Tashkent')

def get_tashkent_time():
    return datetime.now(TASHKENT_TZ)


class HostingPaymentBot:
    """Hosting to'lovlari uchun Telegram Bot"""

    def __init__(self, db=None, app=None):
        self.token = os.getenv('HOSTING_BOT_TOKEN')
        self.admin_chat_id = int(os.getenv('HOSTING_ADMIN_CHAT_ID', '0'))
        self.card_number = os.getenv('HOSTING_CARD_NUMBER', '')
        self.card_owner = os.getenv('HOSTING_CARD_OWNER', '')
        self.card_xabar_chat_id = int(os.getenv('CARD_XABAR_CHAT_ID', '0'))  # Card Xabar bot forwarding chat
        self.db = db
        self.app = app
        self.bot = None

        # Telegram Payments - Click/Payme provider tokenlari
        self.click_provider_token = os.getenv('CLICK_PROVIDER_TOKEN', '')
        self.payme_provider_token = os.getenv('PAYME_PROVIDER_TOKEN', '')

        # Ixtiyoriy summa kiritish uchun kutish
        self.waiting_custom_amount = {}  # {chat_id: client_id}

        # Card Xabar matching uchun vaqt oynasi (daqiqa)
        self.match_window_minutes = int(os.getenv('PAYMENT_MATCH_WINDOW', '30'))

        if not self.token:
            logger.warning("⚠️ HOSTING_BOT_TOKEN sozlanmagan!")
        else:
            try:
                self.bot = Bot(token=self.token)
                logger.info("✅ Hosting Bot muvaffaqiyatli yaratildi")
            except Exception as e:
                logger.error(f"❌ Hosting Bot xatosi: {e}")

    # ==========================================
    # YORDAMCHI FUNKSIYALAR
    # ==========================================

    def _generate_order_code(self) -> str:
        """Unikal buyurtma kodi yaratish: HP-XXXX"""
        chars = string.ascii_uppercase + string.digits
        code = ''.join(random.choices(chars, k=6))
        return f"HP-{code}"

    def _format_money(self, amount) -> str:
        """Pulni formatlash: 1,500,000 so'm"""
        try:
            amount = int(float(amount))
            return f"{amount:,}".replace(',', ' ')
        except (ValueError, TypeError):
            return str(amount)

    def _get_client_by_chat_id(self, chat_id: int):
        """Telegram chat ID bo'yicha mijozni topish"""
        from app import HostingClient
        with self.app.app_context():
            return HostingClient.query.filter_by(
                telegram_chat_id=chat_id,
                is_active=True
            ).first()

    def _get_client_by_phone(self, phone: str):
        """Telefon raqam bo'yicha mijozni topish"""
        from app import HostingClient
        # Telefon raqamni normallashtirish - faqat raqamlarni olish
        digits = re.sub(r'\D', '', phone)
        # Oxirgi 9 ta raqamni olish (998XXXXXXXXX → XXXXXXXXX)
        if len(digits) >= 9:
            last9 = digits[-9:]
        else:
            last9 = digits

        with self.app.app_context():
            # Barcha aktiv mijozlarni tekshirish
            clients = HostingClient.query.filter_by(is_active=True).all()
            for client in clients:
                if client.phone:
                    client_digits = re.sub(r'\D', '', client.phone)
                    if len(client_digits) >= 9:
                        client_last9 = client_digits[-9:]
                    else:
                        client_last9 = client_digits
                    if client_last9 == last9:
                        return client
            return None

    def _get_client_by_id(self, client_id: int):
        """ID bo'yicha mijozni olish"""
        from app import HostingClient
        with self.app.app_context():
            return HostingClient.query.get(client_id)

    def _get_pending_orders(self, client_id: int = None, status: str = None):
        """Kutilayotgan buyurtmalarni olish"""
        from app import HostingPaymentOrder
        with self.app.app_context():
            query = HostingPaymentOrder.query
            if client_id:
                query = query.filter_by(client_id=client_id)
            if status:
                query = query.filter_by(status=status)
            return query.order_by(HostingPaymentOrder.created_at.desc()).all()

    # ==========================================
    # COMMAND HANDLERS
    # ==========================================

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Bot boshlash - /start"""
        chat_id = update.effective_chat.id
        user = update.effective_user

        # Ixtiyoriy summa kutish holatini tozalash
        self.waiting_custom_amount.pop(chat_id, None)

        client = self._get_client_by_chat_id(chat_id)

        if client:
            # Ro'yxatdan o'tgan mijoz
            keyboard = [
                [InlineKeyboardButton("💳 To'lov qilish", callback_data="payment_start")],
                [InlineKeyboardButton("📊 To'lov tarixi", callback_data="payment_history")],
                [InlineKeyboardButton("🖥️ Server holati", callback_data="server_status")],
                [InlineKeyboardButton("ℹ️ Ma'lumotlarim", callback_data="my_info")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"👋 Assalomu alaykum, {client.name}!\n\n"
                f"🖥️ Serveringiz: {client.droplet_name or 'N/A'}\n"
                f"💰 Oylik to'lov: {self._format_money(client.monthly_price_uzs)} so'm\n\n"
                f"Quyidagi tugmalardan birini tanlang:",
                reply_markup=reply_markup
            )
        else:
            # Ro'yxatdan o'tmagan - telefon raqamini so'rash
            keyboard = ReplyKeyboardMarkup(
                [[KeyboardButton("📞 Telefon raqamni yuborish", request_contact=True)]],
                resize_keyboard=True,
                one_time_keyboard=True
            )
            await update.message.reply_text(
                "👋 Assalomu alaykum!\n\n"
                "Tizimda ro'yxatdan o'tish uchun telefon raqamingizni yuboring.\n"
                "Pastdagi tugmani bosing 👇",
                reply_markup=keyboard
            )

    async def handle_contact(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Telefon raqam orqali mijozni avtomatik aniqlash"""
        from app import HostingClient, db as app_db

        contact = update.message.contact
        chat_id = update.effective_chat.id
        user = update.effective_user
        phone = contact.phone_number

        logger.info(f"📞 Kontakt qabul qilindi: {phone} (chat_id: {chat_id})")

        # Telefon raqam bo'yicha mijozni izlash
        client = self._get_client_by_phone(phone)

        if client:
            # Mijoz topildi - telegram ma'lumotlarini yangilash
            with self.app.app_context():
                db_client = HostingClient.query.get(client.id)
                db_client.telegram_chat_id = chat_id
                db_client.telegram_username = user.username
                app_db.session.commit()
                client_name = db_client.name
                client_droplet = db_client.droplet_name
                client_price = db_client.monthly_price_uzs

            logger.info(f"✅ Mijoz aniqlandi: {client_name} (ID: {client.id})")

            # Mijozga xabar
            keyboard = [
                [InlineKeyboardButton("💳 To'lov qilish", callback_data="payment_start")],
                [InlineKeyboardButton("📊 To'lov tarixi", callback_data="payment_history")],
                [InlineKeyboardButton("🖥️ Server holati", callback_data="server_status")],
                [InlineKeyboardButton("ℹ️ Ma'lumotlarim", callback_data="my_info")]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)

            await update.message.reply_text(
                f"✅ Muvaffaqiyatli ro'yxatdan o'tdingiz!\n\n"
                f"👤 Ism: {client_name}\n"
                f"🖥️ Server: {client_droplet or 'N/A'}\n"
                f"💰 Oylik to'lov: {self._format_money(client_price)} so'm\n\n"
                f"Quyidagi tugmalardan birini tanlang:",
                reply_markup=ReplyKeyboardRemove()
            )
            # Inline tugmalarni alohida yuborish
            await update.message.reply_text(
                "📋 Xizmatlar:",
                reply_markup=reply_markup
            )

            # Admin ga xabar
            if self.admin_chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text=(
                            f"✅ Mijoz avtomatik aniqlandi:\n\n"
                            f"👤 Ism: {client_name}\n"
                            f"📞 Telefon: {phone}\n"
                            f"🆔 Username: @{user.username or 'yoq'}\n"
                            f"🔑 Chat ID: {chat_id}"
                        )
                    )
                except Exception as e:
                    logger.error(f"Admin ga xabar yuborishda xato: {e}")
        else:
            # Mijoz topilmadi - admin ga yangi foydalanuvchi haqida xabar
            await update.message.reply_text(
                "⏳ Telefon raqamingiz qabul qilindi.\n\n"
                "Hozircha tizimda sizning ma'lumotlaringiz topilmadi.\n"
                "Admin sizni tizimga qo'shganidan keyin botdan foydalanishingiz mumkin bo'ladi.\n\n"
                "📞 Tez orada siz bilan bog'lanamiz.",
                reply_markup=ReplyKeyboardRemove()
            )

            if self.admin_chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text=(
                            f"🆕 Yangi foydalanuvchi telefon yubordi:\n\n"
                            f"👤 Ism: {user.first_name} {user.last_name or ''}\n"
                            f"📞 Telefon: {phone}\n"
                            f"🆔 Username: @{user.username or 'yoq'}\n"
                            f"🔑 Chat ID: {chat_id}\n\n"
                            f"Tizimga qo'shish uchun admin paneldan foydalaning."
                        )
                    )
                except Exception as e:
                    logger.error(f"Admin ga xabar yuborishda xato: {e}")

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Yordam - /help"""
        await update.message.reply_text(
            "📋 Bot buyruqlari:\n\n"
            "/start - Botni boshlash\n"
            "/pay - To'lov qilish\n"
            "/status - Server holati\n"
            "/history - To'lov tarixi\n"
            "/help - Yordam\n\n"
            "💡 To'lov qilish tartibi:\n"
            "1️⃣ /pay bosing yoki \"To'lov qilish\" tugmasini tanlang\n"
            "2️⃣ Nechi oylik to'lashni tanlang\n"
            "3️⃣ To'lov usulini tanlang:\n"
            "   📱 Click - bot ichida to'g'ridan-to'g'ri\n"
            "   📱 Payme - bot ichida to'g'ridan-to'g'ri\n"
            "   💳 Karta - karta raqamiga o'tkazish\n"
            "4️⃣ To'lov avtomatik tasdiqlanadi ✅"
        )

    async def cmd_pay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """To'lov boshlash - /pay"""
        chat_id = update.effective_chat.id
        client = self._get_client_by_chat_id(chat_id)

        if not client:
            await update.message.reply_text("❌ Siz tizimda ro'yxatdan o'tmagansiz.")
            return

        await self._show_payment_months(update.message, client)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Server holati - /status"""
        chat_id = update.effective_chat.id
        client = self._get_client_by_chat_id(chat_id)

        if not client:
            await update.message.reply_text("❌ Siz tizimda ro'yxatdan o'tmagansiz.")
            return

        await self._show_server_status(update.message, client)

    async def cmd_history(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """To'lov tarixi - /history"""
        chat_id = update.effective_chat.id
        client = self._get_client_by_chat_id(chat_id)

        if not client:
            await update.message.reply_text("❌ Siz tizimda ro'yxatdan o'tmagansiz.")
            return

        await self._show_payment_history(update.message, client)

    # ==========================================
    # CALLBACK HANDLERS
    # ==========================================

    async def handle_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Callback tugmalarni boshqarish"""
        query = update.callback_query
        await query.answer()

        data = query.data
        chat_id = update.effective_chat.id

        try:
            # Mijoz tugmalari
            if data == "payment_start":
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._show_payment_months(query.message, client, edit=True)

            elif data.startswith("pay_months_"):
                months = int(data.split("_")[2])
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._show_payment_method(query.message, client, months, edit=True)

            elif data == "pay_custom_amount":
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    self.waiting_custom_amount[chat_id] = client.id
                    await query.message.edit_text(
                        "✏️ Ixtiyoriy to'lov summasi\n\n"
                        "To'lamoqchi bo'lgan summangizni kiriting (so'mda).\n\n"
                        "Masalan: 500000 yoki 1000000\n\n"
                        "❌ Bekor qilish uchun /start bosing"
                    )

            elif data.startswith("paymethod_card_"):
                parts = data.split("_")
                months = int(parts[2])
                custom_amount = float(parts[3]) if len(parts) > 3 else None
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._create_payment_order(query.message, client, months, edit=True, custom_amount=custom_amount)

            elif data.startswith("paymethod_click_") or data.startswith("paymethod_payme_"):
                parts = data.split("_")
                provider = parts[1]  # click yoki payme
                months = int(parts[2])
                custom_amount = float(parts[3]) if len(parts) > 3 else None
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._send_telegram_invoice(query.message, client, months, provider, context, custom_amount=custom_amount)

            elif data.startswith("confirm_paid_"):
                order_code = data.split("confirm_paid_")[1]
                await self._client_confirms_payment(query.message, chat_id, order_code, edit=True)

            elif data.startswith("cancel_order_"):
                order_code = data.split("cancel_order_")[1]
                await self._cancel_order(query.message, chat_id, order_code, edit=True)

            elif data == "payment_history":
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._show_payment_history(query.message, client, edit=True)

            elif data == "server_status":
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._show_server_status(query.message, client, edit=True)

            elif data == "my_info":
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._show_client_info(query.message, client, edit=True)

            elif data == "back_to_menu":
                client = self._get_client_by_chat_id(chat_id)
                if client:
                    await self._show_main_menu(query.message, client, edit=True)

            # Admin tugmalari
            elif data.startswith("admin_approve_"):
                order_code = data.split("admin_approve_")[1]
                await self._admin_approve_payment(query.message, order_code, context)

            elif data.startswith("admin_reject_"):
                order_code = data.split("admin_reject_")[1]
                await self._admin_reject_payment(query.message, order_code, context)

            elif data.startswith("admin_select_client_"):
                # admin_select_client_HP-XXXX_CLIENT_ID
                parts = data.split("admin_select_client_")[1]
                order_code = parts.rsplit("_", 1)[0]
                client_id = int(parts.rsplit("_", 1)[1])
                await self._admin_match_to_client(query.message, order_code, client_id, context)

        except Exception as e:
            logger.error(f"Callback xatosi: {e}", exc_info=True)
            try:
                await query.message.reply_text(f"❌ Xatolik yuz berdi: {str(e)[:100]}")
            except Exception:
                pass

    # ==========================================
    # TO'LOV JARAYONI
    # ==========================================

    async def _show_main_menu(self, message, client, edit=False):
        """Asosiy menyu"""
        keyboard = [
            [InlineKeyboardButton("💳 To'lov qilish", callback_data="payment_start")],
            [InlineKeyboardButton("📊 To'lov tarixi", callback_data="payment_history")],
            [InlineKeyboardButton("🖥️ Server holati", callback_data="server_status")],
            [InlineKeyboardButton("ℹ️ Ma'lumotlarim", callback_data="my_info")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        text = (
            f"👋 {client.name}\n\n"
            f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
            f"💰 Oylik: {self._format_money(client.monthly_price_uzs)} so'm\n\n"
            f"Tanlang:"
        )

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    async def _show_payment_months(self, message, client, edit=False):
        """To'lov oylari tanlash"""
        price = float(client.monthly_price_uzs or 0)

        keyboard = []
        for months in [1, 2, 3, 6, 12]:
            total = price * months
            label = f"{months} oy - {self._format_money(total)} so'm"
            keyboard.append([InlineKeyboardButton(label, callback_data=f"pay_months_{months}")])

        # Ixtiyoriy summa tugmasi
        keyboard.append([InlineKeyboardButton("✏️ Boshqa summa kiritish", callback_data="pay_custom_amount")])
        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            f"💳 To'lov qilish\n\n"
            f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
            f"💰 Oylik narx: {self._format_money(price)} so'm\n\n"
            f"Nechi oylik to'laysiz yoki ixtiyoriy summa kiriting?"
        )

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    async def _show_payment_method(self, message, client, months: int, edit=False, custom_amount: float = None):
        """To'lov usulini tanlash - Click, Payme yoki Karta"""
        price = float(client.monthly_price_uzs or 0)
        if custom_amount:
            total = custom_amount
        else:
            total = price * months

        keyboard = []

        # custom_amount bo'lsa callback_data ga qo'shamiz
        amount_suffix = f"_{int(custom_amount)}" if custom_amount else ""

        # Click mavjud bo'lsa
        if self.click_provider_token:
            keyboard.append([InlineKeyboardButton(
                "📱 Click orqali to'lash",
                callback_data=f"paymethod_click_{months}{amount_suffix}"
            )])

        # Payme mavjud bo'lsa
        if self.payme_provider_token:
            keyboard.append([InlineKeyboardButton(
                "📱 Payme orqali to'lash",
                callback_data=f"paymethod_payme_{months}{amount_suffix}"
            )])

        # Karta orqali (har doim mavjud)
        if self.card_number:
            keyboard.append([InlineKeyboardButton(
                "💳 Karta orqali o'tkazish",
                callback_data=f"paymethod_card_{months}{amount_suffix}"
            )])

        keyboard.append([InlineKeyboardButton("⬅️ Orqaga", callback_data="payment_start")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        # Agar faqat karta mavjud bo'lsa, to'g'ridan-to'g'ri karta sahifasiga o'tish
        if not self.click_provider_token and not self.payme_provider_token:
            await self._create_payment_order(message, client, months, edit=edit, custom_amount=custom_amount)
            return

        if custom_amount:
            text = (
                f"💳 To'lov usulini tanlang\n\n"
                f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
                f"💰 Summa: {self._format_money(total)} so'm\n\n"
                f"Quyidagi to'lov usullaridan birini tanlang 👇"
            )
        else:
            text = (
                f"💳 To'lov usulini tanlang\n\n"
                f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
                f"📅 Davr: {months} oy\n"
                f"💰 Summa: {self._format_money(total)} so'm\n\n"
                f"Quyidagi to'lov usullaridan birini tanlang 👇"
            )

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    async def _send_telegram_invoice(
        self, message, client, months: int,
        provider: str, context: ContextTypes.DEFAULT_TYPE,
        custom_amount: float = None
    ):
        """Telegram Payments API orqali invoice yuborish (Click/Payme)"""
        from app import HostingPaymentOrder

        price = float(client.monthly_price_uzs or 0)
        if custom_amount:
            total = custom_amount
        else:
            total = price * months
        # Telegram UZS uchun tiyinda ishlaydi (1 so'm = 100 tiyin)
        total_tiyin = int(total * 100)

        # Provider tokenni aniqlash
        if provider == 'click':
            provider_token = self.click_provider_token
            provider_name = 'Click'
        elif provider == 'payme':
            provider_token = self.payme_provider_token
            provider_name = 'Payme'
        else:
            await message.reply_text("❌ Noto'g'ri to'lov usuli.")
            return

        if not provider_token:
            await message.reply_text(f"❌ {provider_name} to'lov tizimi hozircha sozlanmagan.")
            return

        # Order yaratish
        with self.app.app_context():
            # Mavjud pending orderni expired qilish
            existing = HostingPaymentOrder.query.filter_by(
                client_id=client.id,
                status='pending'
            ).first()
            if existing:
                existing.status = 'expired'
                self.db.session.commit()

            order_code = self._generate_order_code()
            while HostingPaymentOrder.query.filter_by(order_code=order_code).first():
                order_code = self._generate_order_code()

            order = HostingPaymentOrder(
                client_id=client.id,
                order_code=order_code,
                amount_uzs=Decimal(str(total)),
                months=months,
                status='pending',
                expires_at=get_tashkent_time() + timedelta(hours=24)
            )
            self.db.session.add(order)
            self.db.session.commit()

        # Telegram Invoice yuborish
        if custom_amount:
            title = "Hosting to'lov"
            description = (
                f"Server: {client.droplet_name or 'N/A'}\n"
                f"Mijoz: {client.name}\n"
                f"Summa: {self._format_money(total)} so'm"
            )
            label_text = "Hosting to'lov"
        else:
            title = f"Hosting - {months} oylik"
            description = (
                f"Server: {client.droplet_name or 'N/A'}\n"
                f"Mijoz: {client.name}\n"
                f"Davr: {months} oy"
            )
            label_text = f"Hosting {months} oy"

        prices = [LabeledPrice(
            label=label_text,
            amount=total_tiyin
        )]

        # payload ga order_code ni qo'yamiz - keyinroq successful_payment da ishlatiladi
        payload = f"{order_code}|{client.id}|{months}|{provider}"

        try:
            # Avvalgi xabarni o'chirish/yangilash
            try:
                await message.edit_text(
                    f"📱 {provider_name} orqali to'lov...\n\n"
                    f"💰 Summa: {self._format_money(total)} so'm\n"
                    f"📋 Buyurtma: #{order_code}\n\n"
                    f"⬇️ Quyidagi to'lov tugmasini bosing:"
                )
            except Exception:
                pass

            await context.bot.send_invoice(
                chat_id=message.chat_id,
                title=title,
                description=description,
                payload=payload,
                provider_token=provider_token,
                currency="UZS",
                prices=prices,
                need_name=False,
                need_phone_number=True,
                need_email=False,
                is_flexible=False,
                start_parameter=f"pay_{order_code}",
            )
            logger.info(f"✅ Invoice yuborildi: #{order_code} - {provider_name} - {total} so'm")

        except Exception as e:
            logger.error(f"❌ Invoice yuborishda xato: {e}", exc_info=True)
            keyboard = [
                [InlineKeyboardButton("💳 Karta orqali to'lash", callback_data=f"paymethod_card_{months}")],
                [InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]
            ]
            await message.reply_text(
                f"❌ {provider_name} orqali to'lov xatosi.\n\n"
                f"Iltimos, karta orqali to'lang yoki keyinroq urinib ko'ring.\n"
                f"Xato: {str(e)[:100]}",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

    async def handle_pre_checkout(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Pre-checkout so'rovi - to'lovni tasdiqlash/rad etish"""
        query = update.pre_checkout_query

        try:
            payload = query.invoice_payload
            parts = payload.split("|")

            if len(parts) != 4:
                await query.answer(ok=False, error_message="Noto'g'ri to'lov ma'lumotlari.")
                return

            order_code, client_id, months, provider = parts

            # Orderni tekshirish
            from app import HostingPaymentOrder
            with self.app.app_context():
                order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()

                if not order:
                    await query.answer(ok=False, error_message="Buyurtma topilmadi.")
                    return

                if order.status not in ['pending']:
                    await query.answer(ok=False, error_message="Bu buyurtma allaqachon qayta ishlangan.")
                    return

                if order.expires_at:
                    expires = order.expires_at
                    now = get_tashkent_time()
                    # timezone-naive vs aware muammosini hal qilish
                    if expires.tzinfo is None:
                        expires = TASHKENT_TZ.localize(expires)
                    if now > expires:
                        order.status = 'expired'
                        self.db.session.commit()
                        await query.answer(ok=False, error_message="Buyurtma muddati o'tgan.")
                        return

            # Hammasi OK - to'lovga ruxsat berish
            await query.answer(ok=True)
            logger.info(f"✅ Pre-checkout tasdiqlandi: #{order_code}")

        except Exception as e:
            logger.error(f"Pre-checkout xatosi: {e}", exc_info=True)
            await query.answer(ok=False, error_message="Tizim xatosi. Keyinroq urinib ko'ring.")

    async def handle_successful_payment(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Muvaffaqiyatli to'lov - avtomatik tasdiqlash va server yoqish"""
        from app import HostingPaymentOrder, HostingPayment, HostingClient
        from digitalocean_manager import DigitalOceanManager

        payment_info = update.message.successful_payment
        payload = payment_info.invoice_payload

        try:
            parts = payload.split("|")
            order_code = parts[0]
            client_id = int(parts[1])
            months = int(parts[2])
            provider = parts[3]

            provider_name = 'Click' if provider == 'click' else 'Payme'
            total_amount = payment_info.total_amount / 100  # tiyindan so'mga

            logger.info(f"💰 Muvaffaqiyatli to'lov: #{order_code} - {provider_name} - {total_amount} so'm")

            with self.app.app_context():
                order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()
                client = HostingClient.query.get(client_id)

                if not order or not client:
                    logger.error(f"Order yoki client topilmadi: {order_code}, {client_id}")
                    return

                # Order ni yangilash
                now = get_tashkent_time()
                order.status = 'approved'
                order.confirmed_at = now
                order.matched_at = now
                order.approved_at = now
                order.admin_notes = f"{provider_name} orqali avtomatik to'langan. Telegram Payment ID: {payment_info.telegram_payment_charge_id}"

                # To'lov yozuvini yaratish
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
                    order_id=order.id,
                    amount_uzs=Decimal(str(total_amount)),
                    months_paid=months,
                    payment_date=now,
                    period_start=period_start,
                    period_end=period_end,
                    confirmed_by=f'{provider_name}_auto',
                    notes=f"{provider_name} orqali to'langan. Charge ID: {payment_info.telegram_payment_charge_id}, Provider Charge ID: {payment_info.provider_payment_charge_id}"
                )
                self.db.session.add(payment)

                # Server yoqish (agar o'chiq bo'lsa)
                server_msg = ""
                if client.droplet_id:
                    try:
                        do_manager = DigitalOceanManager()
                        status = do_manager.get_droplet_status(client.droplet_id)
                        if status == 'off':
                            success = do_manager.power_on(client.droplet_id)
                            if success:
                                client.server_status = 'active'
                                server_msg = "\n🟢 Server avtomatik yoqildi!"
                            else:
                                server_msg = "\n⚠️ Server yoqishda xato - admin tekshiradi."
                        else:
                            server_msg = f"\n🖥️ Server holati: {status}"
                    except Exception as e:
                        logger.error(f"DO server yoqishda xato: {e}")
                        server_msg = "\n⚠️ Server yoqishda xato - admin tekshiradi."

                self.db.session.commit()

            # Mijozga muvaffaqiyat xabari
            keyboard = [[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="back_to_menu")]]
            await update.message.reply_text(
                f"✅ To'lov muvaffaqiyatli qabul qilindi!\n\n"
                f"📋 Buyurtma: #{order_code}\n"
                f"💳 To'lov usuli: {provider_name}\n"
                f"💰 Summa: {self._format_money(total_amount)} so'm\n"
                f"📅 Davr: {months} oy\n"
                f"📆 {period_start.strftime('%d.%m.%Y')} → {period_end.strftime('%d.%m.%Y')}\n"
                f"{server_msg}\n\n"
                f"Rahmat! 🙏",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )

            # Admin ga xabar
            if self.admin_chat_id:
                try:
                    await context.bot.send_message(
                        chat_id=self.admin_chat_id,
                        text=(
                            f"✅ AVTOMATIK TO'LOV QABUL QILINDI\n\n"
                            f"👤 Mijoz: {client.name}\n"
                            f"📋 Buyurtma: #{order_code}\n"
                            f"💳 Usul: {provider_name}\n"
                            f"💰 Summa: {self._format_money(total_amount)} so'm\n"
                            f"📅 Davr: {months} oy\n"
                            f"📆 {period_start.strftime('%d.%m.%Y')} → {period_end.strftime('%d.%m.%Y')}"
                            f"{server_msg}\n\n"
                            f"🆔 Telegram Payment: {payment_info.telegram_payment_charge_id}\n"
                            f"🆔 Provider: {payment_info.provider_payment_charge_id}"
                        )
                    )
                except Exception as e:
                    logger.error(f"Admin ga xabar yuborishda xato: {e}")

        except Exception as e:
            logger.error(f"Successful payment handler xatosi: {e}", exc_info=True)
            await update.message.reply_text(
                "✅ To'lov qabul qilindi!\n\n"
                "⚠️ Ma'lumotlarni saqlashda xatolik. Admin tekshiradi."
            )

    async def _create_payment_order(self, message, client, months: int, edit=False, custom_amount: float = None):
        """To'lov buyurtmasini yaratish va karta ma'lumotlarini ko'rsatish"""
        from app import HostingPaymentOrder

        price = float(client.monthly_price_uzs or 0)
        if custom_amount:
            total = custom_amount
        else:
            total = price * months

        with self.app.app_context():
            # Mavjud pending orderni tekshirish
            existing = HostingPaymentOrder.query.filter_by(
                client_id=client.id,
                status='pending'
            ).first()

            if existing:
                # Eski orderni o'chirish
                existing.status = 'expired'
                self.db.session.commit()

            # Yangi order yaratish
            order_code = self._generate_order_code()
            # Unikal ekanini tekshirish
            while HostingPaymentOrder.query.filter_by(order_code=order_code).first():
                order_code = self._generate_order_code()

            order = HostingPaymentOrder(
                client_id=client.id,
                order_code=order_code,
                amount_uzs=Decimal(str(total)),
                months=months,
                status='pending',
                expires_at=get_tashkent_time() + timedelta(hours=24)
            )
            self.db.session.add(order)
            self.db.session.commit()

            order_id = order.id

        # Karta raqamini formatlash
        card_display = self.card_number
        if len(card_display) == 16:
            card_display = f"{card_display[:4]} {card_display[4:8]} {card_display[8:12]} {card_display[12:]}"

        keyboard = [
            [InlineKeyboardButton("✅ To'ladim", callback_data=f"confirm_paid_{order_code}")],
            [InlineKeyboardButton("❌ Bekor qilish", callback_data=f"cancel_order_{order_code}")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        davr_text = f"📅 Davr: {months} oy" if not custom_amount else "✏️ Ixtiyoriy summa"

        text = (
            f"📋 Buyurtma #{order_code}\n\n"
            f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
            f"{davr_text}\n"
            f"💰 Summa: {self._format_money(total)} so'm\n\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"💳 Karta raqami:\n"
            f"<code>{card_display}</code>\n\n"
            f"👤 Karta egasi: {self.card_owner}\n"
            f"━━━━━━━━━━━━━━━━━━━━\n\n"
            f"📌 Yuqoridagi kartaga {self._format_money(total)} so'm o'tkazing,\n"
            f"keyin \"✅ To'ladim\" tugmasini bosing.\n\n"
            f"⏰ Buyurtma 24 soat ichida amal qiladi."
        )

        if edit:
            await message.edit_text(text, reply_markup=reply_markup, parse_mode='HTML')
        else:
            await message.reply_text(text, reply_markup=reply_markup, parse_mode='HTML')

    async def _client_confirms_payment(self, message, chat_id: int, order_code: str, edit=False):
        """Mijoz 'To'ladim' bosganida"""
        from app import HostingPaymentOrder, HostingClient

        with self.app.app_context():
            order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()

            if not order:
                text = "❌ Buyurtma topilmadi."
                if edit:
                    await message.edit_text(text)
                else:
                    await message.reply_text(text)
                return

            if order.status not in ['pending']:
                text = f"ℹ️ Bu buyurtma allaqachon {order.status} holatda."
                if edit:
                    await message.edit_text(text)
                else:
                    await message.reply_text(text)
                return

            # Statusni yangilash
            order.status = 'client_confirmed'
            order.confirmed_at = get_tashkent_time()
            self.db.session.commit()

            client = HostingClient.query.get(order.client_id)
            client_name = client.name if client else 'Noma\'lum'
            amount = float(order.amount_uzs)
            months = order.months

        # Mijozga javob
        keyboard = [[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = (
            f"✅ To'lov ma'lumotingiz qabul qilindi!\n\n"
            f"📋 Buyurtma: #{order_code}\n"
            f"💰 Summa: {self._format_money(amount)} so'm\n\n"
            f"⏳ Admin to'lovni tekshirib, tasdiqlaydi.\n"
            f"Tasdiqlanganidan keyin xabar olasiz."
        )

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

        # Admin ga xabar yuborish
        if self.admin_chat_id:
            admin_keyboard = [
                [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"admin_approve_{order_code}")],
                [InlineKeyboardButton("❌ Rad etish", callback_data=f"admin_reject_{order_code}")]
            ]
            admin_markup = InlineKeyboardMarkup(admin_keyboard)

            admin_text = (
                f"💳 YANGI TO'LOV TASDIQLASH\n\n"
                f"👤 Mijoz: {client_name}\n"
                f"📋 Buyurtma: #{order_code}\n"
                f"💰 Summa: {self._format_money(amount)} so'm\n"
                f"📅 Davr: {months} oy\n"
                f"🕐 Vaqt: {get_tashkent_time().strftime('%H:%M %d.%m.%Y')}\n\n"
                f"⚠️ Card Xabar xabarini kutib tasdiqlang!"
            )

            try:
                await self.bot.send_message(
                    chat_id=self.admin_chat_id,
                    text=admin_text,
                    reply_markup=admin_markup
                )
            except Exception as e:
                logger.error(f"Admin ga xabar yuborishda xato: {e}")

    async def _cancel_order(self, message, chat_id: int, order_code: str, edit=False):
        """Buyurtmani bekor qilish"""
        from app import HostingPaymentOrder

        with self.app.app_context():
            order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()

            if order and order.status in ['pending', 'client_confirmed']:
                order.status = 'expired'
                self.db.session.commit()

        keyboard = [[InlineKeyboardButton("⬅️ Bosh menyu", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        text = f"❌ Buyurtma #{order_code} bekor qilindi."

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    # ==========================================
    # IXTIYORIY SUMMA HANDLER
    # ==========================================

    async def _handle_custom_amount_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                          text: str, chat_id: int):
        """Mijoz ixtiyoriy summa kiritganida"""
        # Summani parse qilish
        amount_str = text.replace(' ', '').replace(',', '').replace('.', '').strip()

        # Faqat raqamlarni olish
        digits = re.sub(r'\D', '', amount_str)

        if not digits:
            await update.message.reply_text(
                "❌ Noto'g'ri format. Faqat raqam kiriting.\n\n"
                "Masalan: 500000 yoki 1 000 000\n\n"
                "Bekor qilish uchun /start bosing"
            )
            return

        amount = int(digits)

        # Minimum summa tekshirish
        if amount < 1000:
            await update.message.reply_text(
                "❌ Summa juda kichik. Kamida 1 000 so'm bo'lishi kerak.\n\n"
                "Qayta kiriting:"
            )
            return

        # Maximum summa tekshirish
        if amount > 100_000_000:
            await update.message.reply_text(
                "❌ Summa juda katta. Maksimum 100 000 000 so'm.\n\n"
                "Qayta kiriting:"
            )
            return

        # Kutish ro'yxatidan o'chirish
        client_id = self.waiting_custom_amount.pop(chat_id, None)

        client = self._get_client_by_chat_id(chat_id)
        if not client:
            await update.message.reply_text("❌ Siz tizimda ro'yxatdan o'tmagansiz.")
            return

        # To'lov usulini tanlash sahifasiga o'tish
        await self._show_payment_method(
            update.message, client, months=0,
            edit=False, custom_amount=float(amount)
        )

    # ==========================================
    # CARD XABAR HANDLER
    # ==========================================

    async def handle_card_xabar(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """
        Card Xabar botdan kelgan xabarni qayta ishlash.
        Shuningdek, ixtiyoriy summa kiritishni ham boshqaradi.

        Card Xabar formati (misol):
        💳 Karta: **** 1234
        ➕ Kirim: 150 000 so'm
        💰 Balans: 1 500 000 so'm
        🕐 12:30 01.03.2026
        """
        text = update.message.text or update.message.caption or ''
        chat_id = update.effective_chat.id

        # Ixtiyoriy summa kiritishni tekshirish
        if chat_id in self.waiting_custom_amount:
            await self._handle_custom_amount_input(update, context, text, chat_id)
            return

        # Faqat admin chatidan yoki belgilangan chatdan qabul qilish
        if chat_id != self.admin_chat_id and chat_id != self.card_xabar_chat_id:
            return

        # Kirim (tushum) xabarmi tekshirish
        if 'Kirim' not in text and 'kirim' not in text and '+' not in text:
            return

        # Summani ajratib olish
        amount = self._extract_amount(text)
        if not amount:
            return

        logger.info(f"Card Xabar: {amount} so'm kirim aniqlandi")

        await self._match_card_xabar_payment(amount, text, context)

    def _extract_amount(self, text: str) -> Optional[int]:
        """Card Xabar xabaridan summani ajratib olish"""
        # "Kirim: 150 000 so'm" yoki "+150 000" formatda
        patterns = [
            r'[Kk]irim[:\s]+([0-9\s]+)',
            r'\+\s*([0-9\s]+)\s*(?:so[\'ʻ]?m|сум|UZS)',
            r'([0-9\s]{4,})\s*(?:so[\'ʻ]?m|сум|UZS)',
        ]

        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                amount_str = match.group(1).replace(' ', '').strip()
                try:
                    amount = int(amount_str)
                    if amount >= 1000:  # Minimal summa tekshirish
                        return amount
                except ValueError:
                    continue

        return None

    async def _match_card_xabar_payment(self, amount: int, card_text: str, context: ContextTypes.DEFAULT_TYPE):
        """Card Xabar summasi bilan buyurtmalarni moslashtirish"""
        from app import HostingPaymentOrder, HostingClient

        with self.app.app_context():
            # client_confirmed statusdagi buyurtmalarni tekshirish
            now = get_tashkent_time()
            window_start = now - timedelta(minutes=self.match_window_minutes)

            confirmed_orders = HostingPaymentOrder.query.filter(
                HostingPaymentOrder.status == 'client_confirmed',
                HostingPaymentOrder.confirmed_at >= window_start,
            ).all()

            if not confirmed_orders:
                # Hech qanday "To'ladim" bosgan mijoz yo'q
                await context.bot.send_message(
                    chat_id=self.admin_chat_id,
                    text=(
                        f"💳 Card Xabar: {self._format_money(amount)} so'm kirim\n\n"
                        f"⚠️ Mos buyurtma topilmadi.\n"
                        f"Hech kim \"To'ladim\" bosmagan."
                    )
                )
                return

            # Summa bo'yicha moslashtirish (±1% yoki ±1000 so'm tolerantlik)
            matching_orders = []
            for order in confirmed_orders:
                order_amount = float(order.amount_uzs)
                tolerance = max(order_amount * 0.01, 1000)  # 1% yoki 1000, qaysi katta bo'lsa
                if abs(order_amount - amount) <= tolerance:
                    matching_orders.append(order)

            if len(matching_orders) == 0:
                # Summa mos kelmadi
                order_list = "\n".join([
                    f"  • #{o.order_code}: {self._format_money(o.amount_uzs)} so'm ({o.client.name if o.client else '?'})"
                    for o in confirmed_orders
                ])
                await context.bot.send_message(
                    chat_id=self.admin_chat_id,
                    text=(
                        f"💳 Card Xabar: {self._format_money(amount)} so'm kirim\n\n"
                        f"⚠️ Summa mos kelmadi!\n\n"
                        f"Kutilayotgan buyurtmalar:\n{order_list}"
                    )
                )

            elif len(matching_orders) == 1:
                # Aniq bir mijoz - avtomatik match
                order = matching_orders[0]
                order.status = 'payment_matched'
                order.matched_at = now
                order.card_xabar_amount = Decimal(str(amount))
                order.card_xabar_time = now
                order.card_xabar_message = card_text[:500]
                self.db.session.commit()

                client = order.client

                admin_keyboard = [
                    [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"admin_approve_{order.order_code}")],
                    [InlineKeyboardButton("❌ Rad etish", callback_data=f"admin_reject_{order.order_code}")]
                ]
                admin_markup = InlineKeyboardMarkup(admin_keyboard)

                await context.bot.send_message(
                    chat_id=self.admin_chat_id,
                    text=(
                        f"✅ TO'LOV MOSLANDI!\n\n"
                        f"👤 Mijoz: {client.name if client else '?'}\n"
                        f"📋 Buyurtma: #{order.order_code}\n"
                        f"💰 Buyurtma: {self._format_money(order.amount_uzs)} so'm\n"
                        f"💳 Card Xabar: {self._format_money(amount)} so'm\n"
                        f"📅 Davr: {order.months} oy\n\n"
                        f"Tasdiqlaysizmi?"
                    ),
                    reply_markup=admin_markup
                )

            else:
                # Bir nechta mos buyurtma - admin tanlashi kerak
                keyboard = []
                for order in matching_orders:
                    client = order.client
                    label = f"{client.name if client else '?'} - #{order.order_code}"
                    keyboard.append([InlineKeyboardButton(
                        label,
                        callback_data=f"admin_select_client_{order.order_code}_{order.client_id}"
                    )])

                reply_markup = InlineKeyboardMarkup(keyboard)

                await context.bot.send_message(
                    chat_id=self.admin_chat_id,
                    text=(
                        f"💳 Card Xabar: {self._format_money(amount)} so'm kirim\n\n"
                        f"⚠️ Bir nechta mos buyurtma topildi!\n"
                        f"Qaysi mijoz to'lagan?"
                    ),
                    reply_markup=reply_markup
                )

    # ==========================================
    # ADMIN FUNKSIYALARI
    # ==========================================

    async def _admin_approve_payment(self, message, order_code: str, context: ContextTypes.DEFAULT_TYPE):
        """Admin to'lovni tasdiqlashi"""
        from app import HostingPaymentOrder, HostingPayment, HostingClient
        from digitalocean_manager import DigitalOceanManager

        with self.app.app_context():
            order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()

            if not order:
                await message.edit_text("❌ Buyurtma topilmadi.")
                return

            if order.status == 'approved':
                await message.edit_text("ℹ️ Bu buyurtma allaqachon tasdiqlangan.")
                return

            client = HostingClient.query.get(order.client_id)
            if not client:
                await message.edit_text("❌ Mijoz topilmadi.")
                return

            # To'lov yozuvini yaratish
            now = get_tashkent_time()
            period_start = now.date()
            # Oy qo'shish uchun oddiy hisoblash
            end_month = period_start.month + order.months
            end_year = period_start.year + (end_month - 1) // 12
            end_month = ((end_month - 1) % 12) + 1
            try:
                period_end = period_start.replace(year=end_year, month=end_month)
            except ValueError:
                # Oy oxirgi kunlari uchun (masalan, 31 -> 28)
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
                confirmed_by='admin',
                notes=f"Buyurtma #{order_code} orqali tasdiqlangan"
            )
            self.db.session.add(payment)

            # Order ni yangilash
            order.status = 'approved'
            order.approved_at = now
            self.db.session.commit()

            # Server yoqish (agar o'chiq bo'lsa)
            server_msg = ""
            if client.droplet_id:
                try:
                    do_manager = DigitalOceanManager()
                    status = do_manager.get_droplet_status(client.droplet_id)
                    if status == 'off':
                        success = do_manager.power_on(client.droplet_id)
                        if success:
                            client.server_status = 'active'
                            self.db.session.commit()
                            server_msg = "\n🟢 Server yoqildi!"
                        else:
                            server_msg = "\n⚠️ Server yoqishda xato - qo'lda yoqing."
                    else:
                        server_msg = f"\n🖥️ Server holati: {status}"
                except Exception as e:
                    logger.error(f"DO server yoqishda xato: {e}")
                    server_msg = f"\n⚠️ Server yoqishda xato: {str(e)[:50]}"

        # Admin ga tasdiq
        await message.edit_text(
            f"✅ TO'LOV TASDIQLANDI!\n\n"
            f"👤 Mijoz: {client.name}\n"
            f"📋 Buyurtma: #{order_code}\n"
            f"💰 Summa: {self._format_money(order.amount_uzs)} so'm\n"
            f"📅 Davr: {order.months} oy\n"
            f"📆 {period_start.strftime('%d.%m.%Y')} → {period_end.strftime('%d.%m.%Y')}"
            f"{server_msg}"
        )

        # Mijozga xabar
        if client.telegram_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=client.telegram_chat_id,
                    text=(
                        f"✅ To'lovingiz tasdiqlandi!\n\n"
                        f"📋 Buyurtma: #{order_code}\n"
                        f"💰 Summa: {self._format_money(order.amount_uzs)} so'm\n"
                        f"📅 Davr: {order.months} oy\n"
                        f"📆 {period_start.strftime('%d.%m.%Y')} → {period_end.strftime('%d.%m.%Y')}\n\n"
                        f"🟢 Serveringiz faol!\n"
                        f"Rahmat! 🙏"
                    )
                )
            except Exception as e:
                logger.error(f"Mijozga xabar yuborishda xato: {e}")

    async def _admin_reject_payment(self, message, order_code: str, context: ContextTypes.DEFAULT_TYPE):
        """Admin to'lovni rad etishi"""
        from app import HostingPaymentOrder, HostingClient

        with self.app.app_context():
            order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()
            if not order:
                await message.edit_text("❌ Buyurtma topilmadi.")
                return

            client = HostingClient.query.get(order.client_id)
            order.status = 'rejected'
            order.admin_notes = 'Admin tomonidan rad etildi'
            self.db.session.commit()

        await message.edit_text(
            f"❌ Buyurtma #{order_code} rad etildi.\n"
            f"👤 Mijoz: {client.name if client else 'N/A'}"
        )

        # Mijozga xabar
        if client and client.telegram_chat_id:
            try:
                await context.bot.send_message(
                    chat_id=client.telegram_chat_id,
                    text=(
                        f"❌ Buyurtma #{order_code} rad etildi.\n\n"
                        f"To'lov tasdiqlanmadi. Iltimos, admin bilan bog'laning.\n"
                        f"Agar pul o'tkazgan bo'lsangiz, admin bilan aloqaga chiqing."
                    )
                )
            except Exception as e:
                logger.error(f"Mijozga xabar yuborishda xato: {e}")

    async def _admin_match_to_client(self, message, order_code: str, client_id: int, context: ContextTypes.DEFAULT_TYPE):
        """Admin bir nechta mos buyurtmalardan birini tanlashi"""
        from app import HostingPaymentOrder, HostingClient

        with self.app.app_context():
            order = HostingPaymentOrder.query.filter_by(order_code=order_code).first()
            if not order:
                await message.edit_text("❌ Buyurtma topilmadi.")
                return

            order.status = 'payment_matched'
            order.matched_at = get_tashkent_time()
            self.db.session.commit()

            client = HostingClient.query.get(client_id)

        admin_keyboard = [
            [InlineKeyboardButton("✅ Tasdiqlash", callback_data=f"admin_approve_{order_code}")],
            [InlineKeyboardButton("❌ Rad etish", callback_data=f"admin_reject_{order_code}")]
        ]
        admin_markup = InlineKeyboardMarkup(admin_keyboard)

        await message.edit_text(
            f"✅ Buyurtma #{order_code} → {client.name if client else '?'} ga moslandi.\n\n"
            f"💰 Summa: {self._format_money(order.amount_uzs)} so'm\n"
            f"📅 Davr: {order.months} oy\n\n"
            f"Tasdiqlaysizmi?",
            reply_markup=admin_markup
        )

    # ==========================================
    # MA'LUMOT KO'RSATISH
    # ==========================================

    async def _show_server_status(self, message, client, edit=False):
        """Server holatini ko'rsatish"""
        from digitalocean_manager import DigitalOceanManager

        status_text = "❓ Noma'lum"
        status_emoji = "❓"

        if client.droplet_id:
            try:
                do_manager = DigitalOceanManager()
                info = do_manager.get_droplet_info(client.droplet_id)
                if info:
                    status = info['status']
                    if status == 'active':
                        status_emoji = "🟢"
                        status_text = "Faol (yoqilgan)"
                    elif status == 'off':
                        status_emoji = "🔴"
                        status_text = "O'chirilgan"
                    else:
                        status_emoji = "🟡"
                        status_text = status

                    text = (
                        f"🖥️ Server holati\n\n"
                        f"{status_emoji} Holat: {status_text}\n"
                        f"📛 Nomi: {info['name']}\n"
                        f"🌐 IP: {info.get('ip_address', 'N/A')}\n"
                        f"💾 RAM: {info.get('memory', 'N/A')} MB\n"
                        f"🔧 CPU: {info.get('vcpus', 'N/A')} vCPU\n"
                        f"📀 Disk: {info.get('disk', 'N/A')} GB\n"
                        f"🌍 Region: {info.get('region', 'N/A')}"
                    )
                else:
                    text = "❌ Server ma'lumotlarini olishda xato"
            except Exception as e:
                text = f"❌ Xatolik: {str(e)[:100]}"
        else:
            text = (
                f"🖥️ Server holati\n\n"
                f"📛 Server: {client.droplet_name or 'N/A'}\n"
                f"🌐 IP: {client.server_ip or 'N/A'}\n"
                f"{status_emoji} Holat: {client.server_status or 'N/A'}"
            )

        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    async def _show_payment_history(self, message, client, edit=False):
        """To'lov tarixini ko'rsatish"""
        from app import HostingPayment

        with self.app.app_context():
            payments = HostingPayment.query.filter_by(
                client_id=client.id
            ).order_by(HostingPayment.payment_date.desc()).limit(10).all()

            if not payments:
                text = "📊 To'lov tarixi\n\nHali to'lovlar yo'q."
            else:
                lines = ["📊 To'lov tarixi\n"]
                for p in payments:
                    date_str = p.payment_date.strftime('%d.%m.%Y') if p.payment_date else '?'
                    period = ""
                    if p.period_start and p.period_end:
                        period = f" ({p.period_start.strftime('%d.%m')}→{p.period_end.strftime('%d.%m.%Y')})"
                    lines.append(
                        f"✅ {date_str} - {self._format_money(p.amount_uzs)} so'm"
                        f" ({p.months_paid} oy){period}"
                    )
                text = "\n".join(lines)

        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    async def _show_client_info(self, message, client, edit=False):
        """Mijoz ma'lumotlarini ko'rsatish"""
        text = (
            f"ℹ️ Sizning ma'lumotlaringiz\n\n"
            f"👤 Ism: {client.name}\n"
            f"📞 Telefon: {client.phone or 'N/A'}\n"
            f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
            f"🌐 IP: {client.server_ip or 'N/A'}\n"
            f"💰 Oylik: {self._format_money(client.monthly_price_uzs)} so'm\n"
            f"📅 To'lov kuni: Har oyning {client.payment_day}-kuni"
        )

        keyboard = [[InlineKeyboardButton("⬅️ Orqaga", callback_data="back_to_menu")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if edit:
            await message.edit_text(text, reply_markup=reply_markup)
        else:
            await message.reply_text(text, reply_markup=reply_markup)

    # ==========================================
    # KUNLIK TEKSHIRUVCHI (SCHEDULER)
    # ==========================================

    async def check_expired_orders(self):
        """Muddati o'tgan buyurtmalarni expired qilish"""
        from app import HostingPaymentOrder

        with self.app.app_context():
            now = get_tashkent_time()
            expired = HostingPaymentOrder.query.filter(
                HostingPaymentOrder.status.in_(['pending', 'client_confirmed']),
                HostingPaymentOrder.expires_at < now
            ).all()

            for order in expired:
                order.status = 'expired'
                logger.info(f"Buyurtma #{order.order_code} muddati o'tdi")

            if expired:
                self.db.session.commit()
                logger.info(f"{len(expired)} ta buyurtma expired")

    async def check_unpaid_clients(self):
        """To'lov qilmagan mijozlarni tekshirish va eslatma yuborish"""
        from app import HostingClient, HostingPayment

        with self.app.app_context():
            now = get_tashkent_time()
            today = now.date()

            active_clients = HostingClient.query.filter_by(is_active=True).all()

            for client in active_clients:
                if not client.telegram_chat_id:
                    continue

                # Oxirgi to'lovni tekshirish
                last_payment = HostingPayment.query.filter_by(
                    client_id=client.id
                ).order_by(HostingPayment.payment_date.desc()).first()

                should_remind = False

                if not last_payment:
                    # Hali hech qachon to'lamagan
                    should_remind = True
                elif last_payment.period_end and today > last_payment.period_end:
                    # Davr tugagan
                    should_remind = True
                elif last_payment.period_end:
                    # Davr tugashiga 3 kun qolgan
                    days_left = (last_payment.period_end - today).days
                    if days_left <= 3:
                        should_remind = True

                if should_remind:
                    try:
                        keyboard = [[InlineKeyboardButton(
                            "💳 To'lov qilish",
                            callback_data="payment_start"
                        )]]
                        reply_markup = InlineKeyboardMarkup(keyboard)

                        period_info = ""
                        if last_payment and last_payment.period_end:
                            days_left = (last_payment.period_end - today).days
                            if days_left <= 0:
                                period_info = f"\n⚠️ Hosting muddati {abs(days_left)} kun oldin tugagan!"
                            else:
                                period_info = f"\n⏰ Hosting muddati {days_left} kundan keyin tugaydi."

                        await self.bot.send_message(
                            chat_id=client.telegram_chat_id,
                            text=(
                                f"🔔 Hosting to'lov eslatmasi\n\n"
                                f"👤 {client.name}\n"
                                f"🖥️ Server: {client.droplet_name or 'N/A'}\n"
                                f"💰 Oylik: {self._format_money(client.monthly_price_uzs)} so'm"
                                f"{period_info}\n\n"
                                f"To'lov qilish uchun tugmani bosing 👇"
                            ),
                            reply_markup=reply_markup
                        )
                    except Exception as e:
                        logger.error(f"Eslatma yuborishda xato ({client.name}): {e}")

    async def auto_suspend_unpaid(self):
        """To'lov muddati o'tgan serverlarni o'chirish"""
        from app import HostingClient, HostingPayment
        from digitalocean_manager import DigitalOceanManager

        with self.app.app_context():
            now = get_tashkent_time()
            today = now.date()
            grace_days = int(os.getenv('HOSTING_GRACE_DAYS', '3'))  # 3 kun kutish

            active_clients = HostingClient.query.filter_by(
                is_active=True,
                server_status='active'
            ).all()

            do_manager = DigitalOceanManager()

            for client in active_clients:
                if not client.droplet_id:
                    continue

                last_payment = HostingPayment.query.filter_by(
                    client_id=client.id
                ).order_by(HostingPayment.payment_date.desc()).first()

                should_suspend = False

                if not last_payment:
                    # Hali to'lamagan - yaratilganidan 30+grace kun o'tgan bo'lsa
                    if client.created_at:
                        days_since = (today - client.created_at.date()).days
                        if days_since > 30 + grace_days:
                            should_suspend = True
                elif last_payment.period_end:
                    days_overdue = (today - last_payment.period_end).days
                    if days_overdue > grace_days:
                        should_suspend = True

                if should_suspend:
                    try:
                        success = do_manager.shutdown(client.droplet_id)
                        if success:
                            client.server_status = 'suspended'
                            self.db.session.commit()
                            logger.warning(f"⏹️ Server o'chirildi: {client.name} (droplet: {client.droplet_id})")

                            # Admin ga xabar
                            if self.admin_chat_id:
                                await self.bot.send_message(
                                    chat_id=self.admin_chat_id,
                                    text=(
                                        f"⏹️ SERVER O'CHIRILDI (to'lov muddati o'tgan)\n\n"
                                        f"👤 Mijoz: {client.name}\n"
                                        f"🖥️ Server: {client.droplet_name}\n"
                                        f"🌐 IP: {client.server_ip or 'N/A'}"
                                    )
                                )

                            # Mijozga xabar
                            if client.telegram_chat_id:
                                keyboard = [[InlineKeyboardButton(
                                    "💳 To'lov qilish",
                                    callback_data="payment_start"
                                )]]
                                await self.bot.send_message(
                                    chat_id=client.telegram_chat_id,
                                    text=(
                                        "⚠️ Serveringiz to'lov muddati o'tganligi sababli o'chirildi.\n\n"
                                        "💳 To'lov qilib, serverni qayta yoqing.\n"
                                        "To'lov qilish uchun tugmani bosing 👇"
                                    ),
                                    reply_markup=InlineKeyboardMarkup(keyboard)
                                )
                    except Exception as e:
                        logger.error(f"Server o'chirishda xato ({client.name}): {e}")


# ==========================================
# BOT YARATISH VA ISHGA TUSHIRISH
# ==========================================

def create_hosting_bot_app(db=None, app=None) -> Optional[Application]:
    """Hosting Bot Application yaratish"""
    token = os.getenv('HOSTING_BOT_TOKEN')

    if not token:
        logger.warning("⚠️ HOSTING_BOT_TOKEN sozlanmagan - hosting bot ishlamaydi")
        return None

    try:
        hosting_bot = HostingPaymentBot(db=db, app=app)

        application = Application.builder().token(token).build()

        # Command handlers
        application.add_handler(CommandHandler("start", hosting_bot.cmd_start))
        application.add_handler(CommandHandler("help", hosting_bot.cmd_help))
        application.add_handler(CommandHandler("pay", hosting_bot.cmd_pay))
        application.add_handler(CommandHandler("status", hosting_bot.cmd_status))
        application.add_handler(CommandHandler("history", hosting_bot.cmd_history))

        # Callback handler
        application.add_handler(CallbackQueryHandler(hosting_bot.handle_callback))

        # Telegram Payments handlers
        application.add_handler(PreCheckoutQueryHandler(hosting_bot.handle_pre_checkout))
        application.add_handler(MessageHandler(
            filters.SUCCESSFUL_PAYMENT,
            hosting_bot.handle_successful_payment
        ))

        # Contact handler - telefon raqam orqali auto-identifikatsiya
        application.add_handler(MessageHandler(
            filters.CONTACT,
            hosting_bot.handle_contact
        ))

        # Card Xabar message handler (forwarded messages yoki matn xabarlari)
        application.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            hosting_bot.handle_card_xabar
        ))

        # Payment provider ma'lumotlarini log qilish
        if hosting_bot.click_provider_token:
            logger.info("✅ Click to'lov tizimi sozlangan")
        if hosting_bot.payme_provider_token:
            logger.info("✅ Payme to'lov tizimi sozlangan")
        if not hosting_bot.click_provider_token and not hosting_bot.payme_provider_token:
            logger.info("ℹ️ Click/Payme sozlanmagan - faqat karta orqali to'lov")

        logger.info("✅ Hosting Bot application tayyor")
        return application

    except Exception as e:
        logger.error(f"❌ Hosting Bot yaratishda xato: {e}")
        return None
