#!/bin/bash
# Monitoring tizimini serverga deploy qilish skripti

echo "🚀 Monitoring tizimi deploy boshlandi..."
echo ""

# 1. Loyiha papkasiga o'tish
cd /var/www/sayt2025 || exit 1

# 2. Yangi kodlarni pull qilish
echo "📥 GitHub dan yangi kodlarni yuklab olish..."
git pull origin main

# 3. Virtual environment aktivlashtirish
echo "🐍 Virtual environment aktivlash..."
source venv/bin/activate

# 4. Yangi dependencies o'rnatish
echo "📦 psutil kutubxonasini o'rnatish..."
pip install psutil==5.9.0

# 5. check_logs.sh ga ruxsat berish
echo "🔐 check_logs.sh ga execute ruxsati berish..."
chmod +x check_logs.sh

# 6. Gunicorn service restart
echo "🔄 Gunicorn service ni restart qilish..."
sudo systemctl restart sayt2025

# 7. Service holatini tekshirish
echo ""
echo "✅ Service holati:"
sudo systemctl status sayt2025 --no-pager | head -15

echo ""
echo "🔍 Health check test:"
sleep 3
curl -s http://localhost:5000/health | python3 -m json.tool || echo "Health check kutilmoqda..."

echo ""
echo "✅ MONITORING TIZIMI MUVAFFAQIYATLI O'RNATILDI!"
echo ""
echo "📱 Quyidagi URL lardan foydalaning:"
echo "   - Health Check: https://sergeli0606.uz/health"
echo "   - Dashboard: https://sergeli0606.uz/monitoring/dashboard"
echo "   - Logs: ./check_logs.sh"
echo ""
