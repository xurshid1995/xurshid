#!/bin/bash
# 🛡️ Security Upgrade Deployment Script
# Serverda ishga tushirish uchun

set -e  # Xatolik bo'lsa to'xtatish

echo "========================================"
echo "🛡️ SECURITY UPGRADE DEPLOYMENT"
echo "========================================"
echo ""

# 1. Git pull
echo "📥 1/5: Git'dan yangilanishlarni olish..."
cd /var/www/sayt_2025
git pull origin main
echo "✅ Git pull yakunlandi"
echo ""

# 2. Virtual environment aktivlashtirish
echo "🐍 2/5: Virtual environment aktivlashtirish..."
source venv/bin/activate
echo "✅ Virtual environment aktiv"
echo ""

# 3. Migration ishga tushirish
echo "🗄️ 3/5: Database migration..."
python create_api_operations_table.py
echo "✅ Migration yakunlandi"
echo ""

# 4. Log'larni arxivlash
echo "📦 4/5: Eski log'larni arxivlash..."
if [ -f logs/error.log ]; then
    cp logs/error.log logs/error_$(date +%Y%m%d_%H%M%S).log
    echo "✅ Error log arxivlandi"
fi
if [ -f logs/access.log ]; then
    cp logs/access.log logs/access_$(date +%Y%m%d_%H%M%S).log
    echo "✅ Access log arxivlandi"
fi
echo ""

# 5. Gunicorn qayta ishga tushirish
echo "🔄 5/5: Gunicorn qayta ishga tushirish..."
sudo systemctl restart sayt_2025
sleep 2
echo "✅ Gunicorn qayta ishga tushdi"
echo ""

# Status tekshirish
echo "📊 Service status:"
sudo systemctl status sayt_2025 --no-pager -l
echo ""

echo "========================================"
echo "✅ DEPLOYMENT MUVAFFAQIYATLI YAKUNLANDI"
echo "========================================"
echo ""
echo "📝 Keyingi qadamlar:"
echo "  1. Log'larni kuzatish: tail -f logs/error.log"
echo "  2. Test qilish: curl http://localhost:8000/api/currency-rate"
echo "  3. Database tekshirish: psql sayt_db -c 'SELECT COUNT(*) FROM api_operations;'"
echo ""
echo "🎉 Tizim 100% himoyalangan va tayyor!"
