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
from pdf_generator import generate_sale_receipt_pdf

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
            logger.warning("âš ï¸ TELEGRAM_BOT_TOKEN .env faylida sozlanmagan!")
        else:
            try:
                self.bot = Bot(token=self.token)
                logger.info("âœ… Telegram bot muvaffaqiyatli ishga tushdi")
            except Exception as e:
                logger.error(f"âŒ Telegram bot xatosi: {e}")

    def _parse_admin_ids(self) -> List[int]:
        """Admin chat ID larini parse qilish"""
        admin_ids_str = os.getenv('TELEGRAM_ADMIN_CHAT_IDS', '')
        if not admin_ids_str:
            return []

        try:
            return [int(id_.strip()) for id_ in admin_ids_str.split(',') if id_.strip()]
        except ValueError:
            logger.warning("âš ï¸ TELEGRAM_ADMIN_CHAT_IDS noto'g'ri formatda")
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
                payments.append(f"ğŸ’µ Naqd: ${cash:,.2f}")
            if click > 0:
                payments.append(f"ğŸ“± Click: ${click:,.2f}")
            if terminal > 0:
                payments.append(f"ğŸ’³ Terminal: ${terminal:,.2f}")

            if payments:
                return "\n\n<b>ğŸ“Š To'lov turlari:</b>\n" + "\n".join(payments)

            return ""

        except Exception as e:
            logger.error(f"âŒ To'lov ma'lumotlarini olishda xatolik: {e}")
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
                payments.append(f"ğŸ’µ Naqd: ${result.total_cash:,.2f}")
            if result.total_click > 0:
                payments.append(f"ğŸ“± Click: ${result.total_click:,.2f}")
            if result.total_terminal > 0:
                payments.append(f"ğŸ’³ Terminal: ${result.total_terminal:,.2f}")

            if payments:
                return "\n\n<b>ğŸ“Š To'lov turlari:</b>\n" + "\n".join(payments)

            return ""

        except Exception as e:
            logger.error(f"âŒ To'lov ma'lumotlarini olishda xatolik: {e}")
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
                date_str = f"\nğŸ“… Savdo sanasi: {sale_date.strftime('%d.%m.%Y')}"

            # Xabar matni (qarz eslatmasida to'lov turlari ko'rsatilmaydi)
            message = (
                f"ğŸ’° <b>QARZ ESLATMASI</b>\n\n"
                f"Hurmatli {customer_name}!\n\n"
                f"ğŸ“ Joylashuv: {location_name}\n\n"
                f"ğŸ’¸ Qarz: {debt_uzs:,.0f} so'm{date_str}\n\n"
                "Iltimos, qarzingizni to'lashni unutmang. Qarz bu omonat.\n"
                "Rahmat! ğŸ™"
            )

            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML'
            )

            logger.info(f"âœ… Qarz eslatmasi yuborildi: {customer_name} (Chat ID: {chat_id})")
            return True

        except TelegramError as e:
            logger.error(f"âŒ Telegram xatosi ({customer_name}): {e}")
            return False
        except Exception as e:
            logger.error(f"âŒ Xatolik ({customer_name}): {e}")
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
                date_str = f"\nğŸ“… Savdo sanasi: {sale_date.strftime('%d.%m.%Y')}"

            # Xabar matni (qarz eslatmasida to'lov turlari ko'rsatilmaydi)
            message = (
                f"ğŸ’° <b>QARZ ESLATMASI</b>\n\n"
                f"Hurmatli {customer_name}!\n\n"
                f"ğŸ“ Joylashuv: {location_name}\n\n"
                f"ğŸ’¸ Qarz: {debt_uzs_str}{date_str}\n\n"
                "Iltimos, qarzingizni to'lashni unutmang. Qarz bu omonat.\n"
                "Rahmat! ğŸ™"
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
                logger.info(f"âœ… Qarz eslatmasi yuborildi: {customer_name} (Chat ID: {chat_id})")
                return True
            else:
                logger.error(f"âŒ Telegram API xatosi ({customer_name}): {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"âŒ Xatolik ({customer_name}): {e}")
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
                    f"âœ… <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"ğŸ“ Joylashuv: {location_name}\n"
                    f"ğŸ’µ To'langan: ${paid_usd:,.2f}\n"
                    f"ğŸ’¸ To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"ğŸ‰ <b>Qarzingiz to'liq to'landi!</b>\n"
                    "Rahmat! ğŸ™ "
                )
            else:
                # Qisman to'lov
                message = (
                    f"âœ… <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"ğŸ“ Joylashuv: {location_name}\n"
                    f"ğŸ’¸ To'langan: {paid_uzs:,.0f} so'm\n\n"
                    f"ğŸ“Š <b>Qolgan qarz:</b>\n"
                    f"ğŸ’¸ {remaining_uzs:,.0f} so'm\n"
                    f"{payment_details}\n"
                    "Rahmat! ğŸ™ Iltimos qolgan qarzingizniham tez orada Tolang chunki qarz bu sizga omonat"
                )

            await self.bot.send_message(
                chat_id=chat_id,
                text=message,
                parse_mode='HTML'
            )

            logger.info(f"âœ… To'lov tasdiq xabari yuborildi: {customer_name}")
            return True

        except Exception as e:
            logger.error(f"âŒ To'lov tasdiq xabarida xatolik: {e}")
            return False

    def send_sale_notification_sync(
        self,
        chat_id: int,
        customer_name: str,
        customer_id: int,
        sale_date: datetime,
        location_name: str,
        total_amount_uzs: float,
        paid_uzs: float,
        cash_uzs: float = 0,
        click_uzs: float = 0,
        terminal_uzs: float = 0,
        debt_uzs: float = 0,
        sale_id: int = None,
        sale_items: list = None,
        receipt_format: str = 'both',  # 'usd', 'uzs', yoki 'both'
        seller_phone: str = '',
        customer_phone: str = '',
        total_amount_usd: float = 0,
        paid_usd: float = 0,
        cash_usd: float = 0,
        click_usd: float = 0,
        terminal_usd: float = 0,
        debt_usd: float = 0
    ) -> bool:
        """
        Savdo yakunlanganda mijozga xabar yuborish (sync versiya - Flask uchun)

        Args:
            chat_id: Telegram chat ID
            customer_name: Mijoz ismi
            customer_id: Mijoz ID (jami qarzni hisoblash uchun)
            sale_date: Savdo sanasi
            location_name: Do'kon/ombor nomi
            total_amount_uzs: Jami summa (UZS)
            paid_uzs: To'langan summa (UZS)
            cash_uzs: Naqd to'lov (UZS)
            click_uzs: Click to'lov (UZS)
            terminal_uzs: Terminal to'lov (UZS)
            debt_uzs: Qarz (UZS)
            sale_id: Savdo ID (PDF uchun)
            sale_items: Savdo mahsulotlari ro'yxati (PDF uchun)
            receipt_format: Chek formati ('usd', 'uzs', 'both')
            total_amount_usd: Jami summa (USD)
            paid_usd: To'langan summa (USD)
            cash_usd: Naqd to'lov (USD)
            click_usd: Click to'lov (USD)
            terminal_usd: Terminal to'lov (USD)
            debt_usd: Qarz (USD)

        Returns:
            bool: Yuborildi/yuborilmadi
        """
        if not self.token:
            logger.error("Bot token yo'q")
            return False

        try:
            # Savdo xabari (USD formatda)
            message = (
                f"ğŸ“… {sale_date.strftime('%d.%m.%Y %H:%M')}\n"
                f"ğŸ“ Do'kon: {location_name}dan\n"
                f"Savdo qildingiz\n\n"
                f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
                f"ğŸ’° Jami: ${total_amount_usd:,.2f}\n"
            )

            # To'lov ma'lumotlari
            if paid_usd > 0:
                message += f"âœ… To'langan: ${paid_usd:,.2f}\n"

                # To'lov turlarini ko'rsatish
                if cash_usd > 0:
                    message += f"   ğŸ’µ Naqd: ${cash_usd:,.2f}\n"
                if click_usd > 0:
                    message += f"   ğŸ“± Click: ${click_usd:,.2f}\n"
                if terminal_usd > 0:
                    message += f"   ğŸ’³ Terminal: ${terminal_usd:,.2f}\n"

            # Qarz ma'lumoti
            if debt_usd > 0:
                message += f"âš ï¸ Qarz: ${debt_usd:,.2f}\n"

            # Oldingi va jami qarzni hisoblash (database'dan) - har doim ko'rsatish
            previous_debt_usd = 0
            total_debt_usd = 0

            if customer_id:
                try:
                    from app import app, db
                    with app.app_context():
                        # Barcha qarzlarni (shu savdogacha) hisoblash - debt_usd dan
                        total_debt_result = db.session.execute(
                            text("""
                                SELECT COALESCE(SUM(debt_usd), 0) as total_debt_usd
                                FROM sales
                                WHERE customer_id = :customer_id
                                AND payment_status = 'partial'
                                AND debt_usd > 0
                            """),
                            {"customer_id": customer_id}
                        ).fetchone()

                        total_debt_usd = float(total_debt_result[0] or 0) if total_debt_result else 0

                        # Oldingi qarz = Jami qarz - Joriy savdo qarzi
                        previous_debt_usd = total_debt_usd - debt_usd

                        logger.info(f"ğŸ’° Qarz hisoblandi: previous=${previous_debt_usd:.2f}, total=${total_debt_usd:.2f}, current=${debt_usd:.2f}")

                except Exception as db_error:
                    logger.error(f"âŒ Jami qarzni olishda xatolik: {db_error}", exc_info=True)

            # Oldingi yoki jami qarz bo'lsa ko'rsatish
            if previous_debt_usd > 0 or total_debt_usd > 0:
                message += "\n"
                if previous_debt_usd > 0:
                    message += f"<b>ğŸ“‹ OLDINGI QARZ: ${previous_debt_usd:,.2f}</b>\n"
                message += f"<b>ğŸ’³ JAMI QARZ: ${total_debt_usd:,.2f}</b>\n"
                if total_debt_usd > 0:
                    message += "Qarzingizni vaqtida to'lashni unutmang Qarz bu sizga omonat\n"

            message += "\nRahmat! ğŸ™"

            # HTTP API orqali xabar yuborish
            url = f"https://api.telegram.org/bot{self.token}/sendMessage"
            payload = {
                'chat_id': chat_id,
                'text': message,
                'parse_mode': 'HTML'
            }

            response = requests.post(url, json=payload, timeout=10)

            if response.status_code == 200:
                logger.info(f"âœ… Savdo xabari yuborildi: {customer_name} (Chat ID: {chat_id})")
            else:
                logger.error(f"âŒ Telegram API xatosi ({customer_name}): {response.status_code} - {response.text}")
                return False

            # PDF chek yuborish
            if sale_id and sale_items:
                try:
                    # PDF yaratish uchun ma'lumotlar
                    pdf_data_uzs = {
                        'sale_id': sale_id,
                        'date': sale_date.strftime('%d.%m.%Y %H:%M'),
                        'customer_name': customer_name,
                        'customer_phone': customer_phone,
                        'seller_name': sale_items[0].get('seller_name', '') if sale_items and isinstance(sale_items[0], dict) else '',
                        'seller_phone': seller_phone,
                        'location': location_name,
                        'items': sale_items,
                        'total_amount_uzs': total_amount_uzs,
                        'paid_amount_uzs': paid_uzs,
                        'cash_uzs': cash_uzs,
                        'click_uzs': click_uzs,
                        'terminal_uzs': terminal_uzs,
                        'debt_uzs': debt_uzs,
                        'total_amount': total_amount_uzs,
                        'paid_amount': paid_uzs,
                        'cash': cash_uzs,
                        'click': click_uzs,
                        'terminal': terminal_uzs,
                        'debt': debt_uzs,
                        'phone': ''
                    }

                    pdf_data_usd = {
                        'sale_id': sale_id,
                        'date': sale_date.strftime('%d.%m.%Y %H:%M'),
                        'customer_name': customer_name,
                        'customer_phone': customer_phone,
                        'seller_name': sale_items[0].get('seller_name', '') if sale_items and isinstance(sale_items[0], dict) else '',
                        'seller_phone': seller_phone,
                        'location': location_name,
                        'items': sale_items,
                        'total_amount_usd': total_amount_usd,
                        'paid_amount_usd': paid_usd,
                        'cash_usd': cash_usd,
                        'click_usd': click_usd,
                        'terminal_usd': terminal_usd,
                        'debt_usd': debt_usd,
                        'total_amount': total_amount_usd,
                        'paid_amount': paid_usd,
                        'cash': cash_usd,
                        'click': click_usd,
                        'terminal': terminal_usd,
                        'debt': debt_usd,
                        'phone': ''
                    }

                    # Tanlangan formatga qarab PDF yaratish
                    pdf_paths = []

                    if receipt_format in ['uzs', 'both']:
                        pdf_path_uzs = generate_sale_receipt_pdf(pdf_data_uzs, currency='uzs')
                        pdf_paths.append(('UZS', pdf_path_uzs))

                    if receipt_format in ['usd', 'both']:
                        pdf_path_usd = generate_sale_receipt_pdf(pdf_data_usd, currency='usd')
                        pdf_paths.append(('USD', pdf_path_usd))

                    # PDF fayllarni yuborish
                    url_doc = f"https://api.telegram.org/bot{self.token}/sendDocument"
                    for currency_label, pdf_path in pdf_paths:
                        with open(pdf_path, 'rb') as pdf_file:
                            files = {'document': pdf_file}
                            data = {
                                'chat_id': chat_id,
                                'caption': f"ğŸ“„ Savdo cheki #{sale_id} ({currency_label})"
                            }
                            response_pdf = requests.post(url_doc, files=files, data=data, timeout=30)

                        # Temp faylni o'chirish
                        if os.path.exists(pdf_path):
                            os.remove(pdf_path)

                        if response_pdf.status_code == 200:
                            logger.info(f"âœ… {currency_label} PDF chek yuborildi: {customer_name}")
                        else:
                            logger.error(f"âŒ {currency_label} PDF yuborishda xatolik: {response_pdf.status_code}")

                except Exception as pdf_error:
                    logger.error(f"âŒ PDF yaratishda xatolik: {pdf_error}")

            return True

        except Exception as e:
            logger.error(f"âŒ Savdo xabarida xatolik ({customer_name}): {e}")
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
            # Aynan shu to'lovning turlarini ko'rsatish (USD)
            payment_details = ""
            if cash_uzs > 0 or click_uzs > 0 or terminal_uzs > 0:
                payment_details = "\n\n<b>ğŸ“Š To'lov turlari:</b>\n"
                # USD ga o'tkazish (cash_uzs parametri USD hisoblanadi)
                cash_usd = cash_uzs
                click_usd = click_uzs
                terminal_usd = terminal_uzs

                if cash_usd > 0:
                    payment_details += f"ğŸ’µ Naqd: ${cash_usd:,.2f}\n"
                if click_usd > 0:
                    payment_details += f"ğŸ“± Click: ${click_usd:,.2f}\n"
                if terminal_usd > 0:
                    payment_details += f"ğŸ’³ Terminal: ${terminal_usd:,.2f}"

            if remaining_usd <= 0:
                # Qarz to'liq to'landi
                message = (
                    f"âœ… <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"ğŸ’° Avvalgi qarz: ${previous_debt_usd:,.2f}\n\n"
                    f"âœ… To'langan: ${paid_usd:,.2f}\n\n"
                    f"ğŸ‰ <b>Qarzingiz to'liq to'landi!</b>\n\n"
                    "Rahmat! ğŸ™"
                )
            else:
                # Qisman to'lov
                message = (
                    f"âœ… <b>TO'LOV QABUL QILINDI</b>\n\n"
                    f"Hurmatli {customer_name}!\n\n"
                    f"ğŸ’° Avvalgi qarz: ${previous_debt_usd:,.2f}\n\n"
                    f"âœ… To'langan: ${paid_usd:,.2f}\n\n"
                    f"ğŸ“Š Qolgan qarz: ${remaining_usd:,.2f}{payment_details}\n\n"
                    "Rahmat! ğŸ™"
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
                logger.info(f"âœ… To'lov tasdiq xabari yuborildi: {customer_name} (Chat ID: {chat_id})")
                return True
            else:
                logger.error(f"âŒ Telegram API xatosi ({customer_name}): {response.status_code} - {response.text}")
                return False

        except Exception as e:
            logger.error(f"âŒ To'lov tasdiq xabarida xatolik ({customer_name}): {e}")
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
            logger.warning("âš ï¸ Admin chat IDs mavjud emas")
            return False

        if not debts_data:
            message = "ğŸ“Š <b>QARZLAR HISOBOTI</b>\n\nâœ… Hozirda qarz yo'q"
        else:
            total_debt_usd = sum(d['debt_usd'] for d in debts_data)
            total_debt_uzs = sum(d['debt_uzs'] for d in debts_data)

            message = (
                f"ğŸ“Š <b>QARZLAR HISOBOTI</b>\n"
                f"ğŸ“… Sana: {datetime.now().strftime('%d.%m.%Y %H:%M')}\n\n"
                f"ğŸ‘¥ Jami qarzlar: {len(debts_data)} ta\n"
                f"ï¿½ Umumiy qarz: {total_debt_uzs:,.0f} so'm\n\n"
                f"â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n\n"
            )

            # Har bir qarzni qo'shish (maksimum 20 ta)
            for i, debt in enumerate(debts_data[:20], 1):
                message += (
                    f"{i}. <b>{debt['customer_name']}</b>\n"
                    f"   ğŸ“ {debt['location_name']}\n"
                    f"   ğŸ’¸ {debt['debt_uzs']:,.0f} so'm\n"
                    f"   ğŸ“± {debt.get('phone', 'Telefon yo\'q')}\n\n"
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
                    logger.info(f"âœ… Admin {admin_id} ga hisobot yuborildi")
                except Exception as e:
                    logger.error(f"âŒ Admin {admin_id} ga yuborishda xatolik: {e}")
                    success = False

            return success

        except Exception as e:
            logger.error(f"âŒ Admin hisobotida xatolik: {e}")
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
                f"ğŸ“Š <b>KUNLIK HISOBOT</b>\n"
                f"ğŸ“… {datetime.now().strftime('%d.%m.%Y')}\n\n"
                f"ğŸ‘¥ Jami qarzlar: {total_debts} ta\n"
                f"ğŸ’µ Umumiy: ${total_amount_usd:,.2f}\n"
                f"ğŸ’¸ Umumiy: {total_amount_uzs:,.0f} so'm\n\n"
                f"ğŸ“ˆ Bugun yangi: {new_debts} ta\n"
                f"âœ… Bugun to'landi: {paid_today} ta\n"
            )

            for admin_id in self.admin_chat_ids:
                try:
                    await self.bot.send_message(
                        chat_id=admin_id,
                        text=message,
                        parse_mode='HTML'
                    )
                except Exception as e:
                    logger.error(f"âŒ Admin {admin_id} ga kunlik hisobot yuborishda xatolik: {e}")

            return True

        except Exception as e:
            logger.error(f"âŒ Kunlik hisobot yuborishda xatolik: {e}")
            return False


# Bot commandlari (agar mijozlar bot bilan interact qilishini hohlasangiz)
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start command handler with phone number button"""
    from app import app, db, Customer

    chat_id = update.effective_chat.id

    # Mijoz allaqachon ro'yxatdan o'tganmi tekshirish
    with app.app_context():
        try:
            customer = Customer.query.filter_by(telegram_chat_id=chat_id).first()

            if customer:
                # Mijoz allaqachon ro'yxatdan o'tgan - tugmalarni ko'rsatish
                keyboard = [
                    [KeyboardButton("ğŸ’° Qarzni tekshirish")],
                    [KeyboardButton("ğŸ“œ To'lov tarixi")]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                await update.message.reply_text(
                    f"Assalomu alaykum, {customer.name}! ğŸ‘‹\n\n"
                    f"Xush kelibsiz!\n\n"
                    f"Qarzingizni tekshirish yoki to'lov tarixini ko'rish uchun pastdagi tugmalardan foydalaning:",
                    reply_markup=reply_markup
                )
                return
        except Exception as e:
            logger.error(f"âŒ Start commandda mijozni tekshirishda xatolik: {e}")

    # Yangi mijoz - telefon raqam yuborish tugmasini ko'rsatish
    keyboard = [
        [KeyboardButton("ğŸ“± Telefon raqamni yuborish", request_contact=True)]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True)

    await update.message.reply_text(
        "Assalomu alaykum! ğŸ‘‹\n\n"
        "Bu qarz eslatmalari botidir.\n\n"
        "Qarzingizni tekshirish uchun telefon raqamingizni yuboring.\n\n"
        "Pastdagi tugmani bosing:",
        reply_markup=reply_markup
    )

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telefon raqam contact orqali kelganda"""
    from app import app, db, Customer

    contact = update.message.contact
    chat_id = update.effective_chat.id
    phone_number = contact.phone_number

    logger.info(f"ğŸ“± Contact qabul qilindi: Chat ID {chat_id}, Phone: {phone_number}")

    await _process_phone_verification(update, chat_id, phone_number)

async def handle_phone_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Telefon raqam matn sifatida kelganda"""
    from app import app, db, Customer

    chat_id = update.effective_chat.id
    phone_number = update.message.text.strip()

    logger.info(f"ğŸ“± Telefon raqam matn sifatida qabul qilindi: Chat ID {chat_id}, Phone: {phone_number}")

    await _process_phone_verification(update, chat_id, phone_number)

async def _process_phone_verification(update, chat_id, phone_number):
    """Telefon raqamni tasdiqlash jarayoni"""
    from app import app, db, Customer

    # Telefon raqamni tozalash
    phone = ''.join(filter(str.isdigit, phone_number))

    logger.info(f"ğŸ” Telefon qidirish: tozalangan raqam: {phone}")

    with app.app_context():
        try:
            # Mijozni qidirish
            customer = None
            all_customers = Customer.query.all()

            logger.info(f"ğŸ“Š Jami mijozlar soni: {len(all_customers)}")

            for cust in all_customers:
                if cust.phone:
                    clean_db_phone = ''.join(filter(str.isdigit, cust.phone))
                    # Oxirgi 9 raqamni solishtirish
                    if len(phone) >= 9 and len(clean_db_phone) >= 9:
                        if clean_db_phone[-9:] == phone[-9:]:
                            customer = cust
                            logger.info(f"âœ… Mijoz topildi: {customer.name} (ID: {customer.id}, Tel: {customer.phone})")
                            break

            if not customer:
                logger.warning(f"âŒ Mijoz topilmadi: {phone_number}")
                await update.message.reply_text(
                    "âŒ Sizning raqamingiz tizimda topilmadi.\n\n"
                    "Iltimos:\n"
                    "1. Raqamingizni to'g'ri yuborganingizni tekshiring\n"
                    "2. Yoki do'konga murojaat qiling\n\n"
                    "Telefon formati: +998901234567",
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

            logger.info(f"ğŸ” Tasdiqlash kodi yaratildi: {verification_code} mijoz {customer.name} (ID: {customer.id}) uchun")
            logger.info(f"ğŸ“ Verification codes dictionary: {verification_codes}")

            # Tasdiqlash kodini yuborish - yanada aniq ko'rsatmalar bilan
            await update.message.reply_text(
                f"âœ… Telefon raqam qabul qilindi!\n\n"
                f"Hurmatli <b>{customer.name}</b>!\n\n"
                f"Tasdiqlash uchun quyidagi 6 raqamli kodni kiriting:\n\n"
                f"ğŸ” <b><code>{verification_code}</code></b>\n\n"
                f"ğŸ’¡ Kodni ko'chirib oling va menga yuboring.",
                parse_mode='HTML',
                reply_markup=ReplyKeyboardRemove()
            )

            logger.info(f"â¡ï¸ Tasdiqlash kodi yuborildi Chat ID {chat_id} ga")

        except Exception as e:
            logger.error(f"âŒ Telefon tekshirishda xatolik: {e}", exc_info=True)
            await update.message.reply_text(
                "âŒ Xatolik yuz berdi. Iltimos, qaytadan urinib ko'ring.\n\n"
                "/start buyrug'ini boshing.",
                reply_markup=ReplyKeyboardRemove()
            )

async def handle_verification_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Tasdiqlash kodini tekshirish"""
    from app import app, db, Customer, Sale, Store, Warehouse

    chat_id = update.effective_chat.id
    code = update.message.text.strip()

    logger.info(f"ğŸ“¥ Kiritilgan kod: '{code}' Chat ID: {chat_id}")

    # Kod formatini tekshirish (6 ta raqam)
    if not code.isdigit() or len(code) != 6:
        logger.info(f"âš ï¸ Noto'g'ri kod formati: '{code}'")
        return

    # Tasdiqlash kodini tekshirish
    if chat_id not in verification_codes:
        logger.warning(f"âš ï¸ Chat ID {chat_id} uchun tasdiqlash kodi topilmadi")
        logger.info(f"ğŸ“ Mavjud verification codes: {list(verification_codes.keys())}")

        await update.message.reply_text(
            "âŒ Tasdiqlash kodi topilmadi.\n\n"
            "Iltimos, avval telefon raqamingizni yuboring:\n\n"
            "/start buyrug'ini bosing."
        )
        return

    saved_data = verification_codes[chat_id]
    logger.info(f"ğŸ” Saqlangan kod: {saved_data['code']}, Kiritilgan kod: {code}")

    if saved_data['code'] != code:
        logger.warning(f"âŒ Noto'g'ri kod kiritildi. Kutilgan: {saved_data['code']}, Kiritildi: {code}")
        await update.message.reply_text(
            "âŒ Noto'g'ri tasdiqlash kodi!\n\n"
            "Iltimos, yuborilgan 6 raqamli kodni to'g'ri kiriting.\n\n"
            "Agar kod yo'qolgan bo'lsa, /start dan qayta boshlang."
        )
        return

    logger.info("✅ Tasdiqlash kodi to'g'ri!")

    # Tasdiqlash muvaffaqiyatli
    customer_id = saved_data['customer_id']

    with app.app_context():
        try:
            customer = Customer.query.get(customer_id)
            if not customer:
                await update.message.reply_text("âŒ Xatolik: Mijoz topilmadi")
                return

            # Telegram chat ID ni saqlash
            customer.telegram_chat_id = chat_id
            db.session.commit()

            logger.info(f"âœ… Mijoz tasdiqlandi va telegram_chat_id saqlandi: {customer.name} (Chat ID: {chat_id})")

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
                [KeyboardButton("ğŸ’° Qarzni tekshirish")],
                [KeyboardButton("ğŸ“œ To'lov tarixi")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            if not debts:
                await update.message.reply_text(
                    f"âœ… Tasdiqlash muvaffaqiyatli!\n\n"
                    f"Assalomu alaykum, {customer.name}!\n\n"
                    f"ğŸ‰ Sizda qarz yo'q!\n\n"
                    "Rahmat! ğŸ™",
                    reply_markup=reply_markup
                )
                return

            # Qarzlar haqida xabar - faqat jami qarzni ko'rsatish (USD)
            total_usd = 0

            for debt in debts:
                debt_usd = float(debt.total_debt_usd or 0)
                total_usd += debt_usd

            message = (
                f"âœ… Tasdiqlash muvaffaqiyatli!\n\n"
                f"Assalomu alaykum, {customer.name}!\n\n"
                f"ğŸ’° <b>Sizning qarzingiz:</b>\n\n"
                f"ğŸ’¸ ${total_usd:,.2f}\n\n"
                "Iltimos, qarzingizni to'lashni unutmang.\n"
                "Rahmat! ğŸ™"
            )

            # Doimiy tugmalarni ko'rsatish
            keyboard = [
                [KeyboardButton("ğŸ’° Qarzni tekshirish")],
                [KeyboardButton("ğŸ“œ To'lov tarixi")]
            ]
            reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

            await update.message.reply_text(message, parse_mode='HTML', reply_markup=reply_markup)

        except Exception as e:
            logger.error(f"âŒ Tasdiqlashda xatolik: {e}")
            await update.message.reply_text("âŒ Xatolik yuz berdi")

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
                    [KeyboardButton("ğŸ“± Telefon raqamni yuborish", request_contact=True)]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                await update.message.reply_text(
                    "âŒ Siz hali ro'yxatdan o'tmagansiz.\n\n"
                    "Iltimos, telefon raqamingizni yuboring:",
                    reply_markup=reply_markup
                )
                return

            # Qarzlarni hisoblash - faqat USD dan
            debts_result = db.session.query(
                db.func.sum(Sale.debt_usd).label('total_debt_usd')
            ).filter(
                Sale.customer_id == customer.id,
                Sale.payment_status == 'partial',
                Sale.debt_usd > 0
            ).first()

            if not debts_result or not debts_result.total_debt_usd or debts_result.total_debt_usd <= 0:
                await update.message.reply_text(
                    f"Assalomu alaykum, {customer.name}!\n\n"
                    f"ğŸ‰ Sizda qarz yo'q!\n\n"
                    "Rahmat! ğŸ™"
                )
                return

            # Qarzlar haqida xabar - jami qarzni ko'rsatish (USD)
            total_debt_usd = float(debts_result.total_debt_usd or 0)

            message = (
                f"Assalomu alaykum, {customer.name}!\n\n"
                f"ğŸ’° <b>Sizning jami qarzingiz:</b>\n\n"
                f"ğŸ’¸ ${total_debt_usd:,.2f}\n\n"
                "Iltimos, qarzingizni to'lashni unutmang.\n"
                "Rahmat! ğŸ™"
            )

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"âŒ Qarzni tekshirishda xatolik: {e}")
            await update.message.reply_text("âŒ Xatolik yuz berdi")

async def payment_history_button(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """To'lov tarixi tugmasi bosilganda"""
    from app import app, db, Customer, DebtPayment

    chat_id = update.effective_chat.id

    with app.app_context():
        try:
            # Mijozni telegram_chat_id bo'yicha topish
            customer = Customer.query.filter_by(telegram_chat_id=chat_id).first()

            if not customer:
                # Telefon raqam tugmasini ko'rsatish
                keyboard = [
                    [KeyboardButton("ğŸ“± Telefon raqamni yuborish", request_contact=True)]
                ]
                reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

                await update.message.reply_text(
                    "âŒ Siz hali ro'yxatdan o'tmagansiz.\n\n"
                    "Iltimos, telefon raqamingizni yuboring:",
                    reply_markup=reply_markup
                )
                return

            # To'lov tarixini olish (oxirgi 10 ta)
            payments = DebtPayment.query.filter_by(
                customer_id=customer.id
            ).order_by(
                DebtPayment.payment_date.desc()
            ).limit(10).all()

            if not payments:
                await update.message.reply_text(
                    f"Assalomu alaykum, {customer.name}!\n\n"
                    f"ğŸ“œ To'lov tarixingiz topilmadi.\n\n"
                    f"Siz hali qarz to'lamagan yoki to'lovlar qayd qilinmagan."
                )
                return

            # To'lov tarixini formatlash (USD)
            message = (
                f"ğŸ“œ <b>To'lov tarixi</b>\n"
                f"Mijoz: {customer.name}\n\n"
            )

            for idx, payment in enumerate(payments, 1):
                payment_datetime = payment.payment_date.strftime('%d.%m.%Y %H:%M')
                payment_usd = float(payment.total_usd or 0)

                message += f"<b>{idx}.</b> {payment_datetime}\n"
                message += f"ğŸ’° ${payment_usd:,.2f}\n"

                # To'lov turlarini USD da alohida qatorlarda ko'rsatish
                if float(payment.cash_usd or 0) > 0:
                    message += f"   ğŸ’µ Naqd: ${float(payment.cash_usd):,.2f}\n"
                if float(payment.click_usd or 0) > 0:
                    message += f"   ğŸ“± Click: ${float(payment.click_usd):,.2f}\n"
                if float(payment.terminal_usd or 0) > 0:
                    message += f"   ğŸ’³ Terminal: ${float(payment.terminal_usd):,.2f}\n"

                if payment.notes:
                    message += f"ğŸ“ {payment.notes}\n"

                message += "\n"

            message += "Rahmat! ğŸ™"

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"âŒ To'lov tarixini olishda xatolik: {e}")
            await update.message.reply_text("âŒ Xatolik yuz berdi")

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Help command handler"""
    await update.message.reply_text(
        "ğŸ“± <b>Bot buyruqlari:</b>\n\n"
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
        "ğŸ“± Qarzingizni tekshirish uchun telefon raqamingizni yuboring.\n\n"
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

    logger.info(f"ğŸ“± Telefon qidirish: Chat ID {chat_id}, Kiritilgan: '{message_text}', Tozalangan: '{phone}'")

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

    logger.info(f"ğŸ” Qidirilayotgan formatlar: {possible_phones}")

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
                    logger.info(f"âœ… Mijoz topildi (to'g'ridan-to'g'ri): {customer.name} (ID: {customer.id}, Phone DB: '{customer.phone}')")
                    break

            # Agar topilmasa, barcha mijozlarni sanab chiqib, telefon raqamlarni tozalab qidirish
            if not customer:
                logger.info("   - Barcha mijozlar ichidan raqamni tozalab qidirilmoqda...")
                all_customers = Customer.query.all()
                for cust in all_customers:
                    if cust.phone:
                        # Database'dagi telefon raqamdan barcha belgilarni olib tashlash
                        clean_db_phone = ''.join(filter(str.isdigit, cust.phone))
                        # Oxirgi 9 raqamni solishtirish
                        if clean_db_phone[-9:] == phone[-9:]:
                            customer = cust
                            logger.info(f"âœ… Mijoz topildi (tozalab): {customer.name} (ID: {customer.id}, Phone DB: '{customer.phone}', Clean: '{clean_db_phone}')")
                            break

            if not customer:
                await update.message.reply_text(
                    "âŒ Sizning raqamingiz tizimda topilmadi.\n\n"
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
                    f"âœ… Assalomu alaykum, {customer.name}!\n\n"
                    f"ğŸ‰ Sizda qarz yo'q!\n\n"
                    "Rahmat! ğŸ™"
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
                    f"ğŸ“ {location_name}\n"
                    f"   ï¿½ {debt_uzs:,.0f} so'm"
                )

            message = (
                f"ğŸ’° <b>QARZ MA'LUMOTLARI</b>\n\n"
                f"Hurmatli {customer.name}!\n\n"
            )

            if len(debt_details) > 1:
                message += "<b>Qarzlar ro'yxati:</b>\n\n"
                message += "\n\n".join(debt_details)
                message += (
                    f"\n\nâ”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”â”\n"
                    f"<b>JAMI:</b>\n"
                    f"ğŸ’¸ {total_uzs:,.0f} so'm\n\n"
                )
            else:
                message += debt_details[0] + "\n\n"

            message += (
                "Iltimos, qarzingizni to'lashni unutmang.\n"
                "Rahmat! ğŸ™"
            )

            await update.message.reply_text(message, parse_mode='HTML')

        except Exception as e:
            logger.error(f"âŒ Qarz tekshirishda xatolik: {e}")
            await update.message.reply_text(
                "âŒ Xatolik yuz berdi. Iltimos, keyinroq urinib ko'ring."
            )

async def handle_unknown_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Noma'lum xabarlarga javob berish - faqat tugmalardan foydalanishni tavsiya qilish"""
    keyboard = [
        [KeyboardButton("ğŸ’° Qarzni tekshirish")],
        [KeyboardButton("ğŸ“œ To'lov tarixi")]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        "âš ï¸ Iltimos, quyidagi tugmalardan foydalaning:",
        reply_markup=reply_markup
    )


def create_telegram_app():
    """Telegram Application yaratish"""
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if not token:
        logger.error("âŒ TELEGRAM_BOT_TOKEN topilmadi")
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
                filters.TEXT & filters.Regex(r'^ğŸ’° Qarzni tekshirish$'),
                check_debt_button
            )
        )

        # "To'lov tarixi" tugmasi handler
        application.add_handler(
            MessageHandler(
                filters.TEXT & filters.Regex(r'^ğŸ“œ To\'lov tarixi$'),
                payment_history_button
            )
        )

        # Tasdiqlash kodi handler - 6 raqamli kod uchun
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\d{6}$'),
                handle_verification_code
            )
        )

        # Telefon raqam matn sifatida yuborilganda (masalan: +998901234567)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & filters.Regex(r'^\+?998?\d{9}$|^\d{9}$'),
                handle_phone_text
            )
        )

        # Message handler - oddiy xabarlarni rad etish (faqat tugmalardan foydalanish)
        application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND & ~filters.Regex(r'^\d{6}$') & ~filters.Regex(r'^ğŸ’° Qarzni tekshirish$') & ~filters.Regex(r'^ğŸ“œ To\'lov tarixi$') & ~filters.Regex(r'^\+?998?\d{9}$|^\d{9}$'),
                handle_unknown_message
            )
        )

        logger.info("âœ… Telegram application yaratildi")
        return application

    except Exception as e:
        logger.error(f"âŒ Telegram application yaratishda xatolik: {e}")
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

        print("âœ… Test xabar yuborildi")

    # asyncio.run(test_bot())
    print("âš ï¸ Test uchun chat_id ni o'zgartiring va ishga tushiring")
