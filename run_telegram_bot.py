#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Telegram Bot Standalone Server
Botni alohida ishga tushirish uchun
"""
import sys
import os
import asyncio
import logging
from pathlib import Path

# Loyiha ildizini sys.path ga qo'shish
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))

# Environment variables yuklash
from dotenv import load_dotenv
load_dotenv()

# Logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def main():
    """Botni ishga tushirish"""
    try:
        # Flask app va db ni import qilish
        from app import app, db
        from telegram_bot import create_telegram_app, create_reset_bot_app
        from debt_scheduler import init_debt_scheduler

        logger.info("🤖 Telegram Bot ishga tushirilmoqda...")

        # Scheduler ni ishga tushirish
        logger.info("📅 Scheduler ishga tushirilmoqda...")
        scheduler = init_debt_scheduler(app, db)

        # Asosiy bot (@Sergeli143_bot)
        logger.info("📱 Telegram application yaratilmoqda...")
        application = create_telegram_app()

        if not application:
            logger.error("❌ Telegram application yaratilmadi!")
            return

        # @Paroltiklash_bot
        reset_app = create_reset_bot_app()

        logger.info("✅ Telegram Bot tayyor!")
        logger.info("🔄 Bot polling rejimida ishlamoqda...")

        if reset_app:
            # Ikki botni parallel ishlatish (reset_app xato bersa ham asosiy bot ishlashda davom etadi)
            async def run_both():
                async with application:
                    await application.initialize()
                    await application.start()
                    await application.updater.start_polling(allowed_updates=["message", "callback_query", "inline_query"])
                    logger.info("✅ Asosiy bot ishlamoqda")

                    reset_started = False
                    try:
                        await reset_app.initialize()
                        await reset_app.start()
                        await reset_app.updater.start_polling(allowed_updates=["message"])
                        reset_started = True
                        logger.info("✅ Paroltiklash boti ham ishlamoqda")
                    except Exception as e:
                        logger.error(f"❌ Paroltiklash boti ishga tushmadi (asosiy bot ishlashda davom etadi): {e}")

                    # To'xtatish signalini kutish
                    import signal
                    stop_event = asyncio.Event()
                    loop = asyncio.get_running_loop()
                    for sig in (signal.SIGINT, signal.SIGTERM):
                        loop.add_signal_handler(sig, stop_event.set)
                    await stop_event.wait()
                    await application.updater.stop()
                    await application.stop()
                    if reset_started:
                        await reset_app.updater.stop()
                        await reset_app.stop()
            asyncio.run(run_both())
        else:
            # Faqat asosiy bot
            application.run_polling(
                allowed_updates=["message", "callback_query", "inline_query"]
            )

    except KeyboardInterrupt:
        logger.info("\n⛔ Bot to'xtatildi (Ctrl+C)")
        if 'scheduler' in locals():
            scheduler.stop()
    except Exception as e:
        logger.error(f"❌ Xatolik: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()
