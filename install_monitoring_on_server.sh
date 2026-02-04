#!/bin/bash
# Bu faylni serverda ishga tushiring

echo "🚀 Monitoring tizimi o'rnatish boshlandi..."
echo ""

cd /var/www/sayt2025

echo "📥 Git pull..."
git pull origin main

echo ""
echo "🐍 Virtual environment..."
source venv/bin/activate

echo ""
echo "📦 psutil o'rnatish..."
pip install psutil==5.9.0

echo ""
echo "🔐 check_logs.sh ruxsati..."
chmod +x check_logs.sh

echo ""
echo "🔄 Service restart..."
sudo systemctl restart sayt2025

echo ""
echo "✅ Service status:"
sudo systemctl status sayt2025 --no-pager | head -20

echo ""
echo "🔍 Health check:"
sleep 3
curl -s http://localhost:5000/health

echo ""
echo "✅ O'RNATISH TUGADI!"
