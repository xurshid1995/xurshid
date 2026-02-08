#!/bin/bash
# Serverga deploy qilish script'i

echo "🚀 Serverga deploy qilish boshlandi..."
echo ""

# 1. Loyiha papkasiga o'tish
echo "📁 Loyiha papkasiga o'tish..."
cd /var/www/sayt2025 || exit

# 2. Yangi kodlarni pull qilish
echo "⬇️ GitHub'dan yangi kodlarni pull qilish..."
git pull origin main

# 3. Virtual environment aktivlashtirish
echo "🔧 Virtual environment aktivlashtirish..."
source venv/bin/activate || exit

# 4. Dependencies yangilash (agar kerak bo'lsa)
echo "📦 Dependencies tekshirish..."
pip install -r requirements.txt --quiet

# 5. Telegram bot service'ni restart qilish (agar ishlab turgan bo'lsa)
echo "🤖 Telegram bot restart..."
if systemctl is-active --quiet telegram-bot; then
    sudo systemctl restart telegram-bot
    echo "✅ Telegram bot restart qilindi"
else
    echo "⚠️ Telegram bot service ishlamayapti"
fi

# 6. Gunicorn service restart
echo "🔄 Gunicorn service restart..."
sudo systemctl restart sayt2025

# 7. Service statusini tekshirish
echo ""
echo "📊 Service status:"
sudo systemctl status sayt2025 --no-pager -n 5

# 8. Telegram bot statusini tekshirish
echo ""
echo "🤖 Telegram bot status:"
if systemctl is-active --quiet telegram-bot; then
    sudo systemctl status telegram-bot --no-pager -n 5
fi

# 9. Health check
echo ""
echo "🏥 Health check..."
sleep 2
curl -s http://localhost:5000/health || echo "❌ Health check xato"

# 10. Success message
echo ""
echo "✅ Deploy muvaffaqiyatli yakunlandi!"
echo ""
echo "🌐 Website: https://sergeli0606.uz"
echo "📊 Monitoring: https://sergeli0606.uz/monitoring/dashboard"
echo ""
echo "📝 Loglarni tekshirish uchun:"
echo "   tail -f logs/app.log"
echo "   tail -f logs/telegram_bot.log"
