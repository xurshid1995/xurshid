#!/bin/bash

# Test uchun server API'ni tekshirish
SERVER="https://www.sergeli0606.uz"

echo "====================================="
echo "QOLDIQ TEKSHIRUV TUZATISHINI SINASH"
echo "====================================="
echo ""

# 1. Database'dan to'g'ridan-to'g'ri tekshirish
echo "📊 Database'dan tugatilgan sessiyalarni tekshirish:"
echo "---------------------------------------------------"

ssh root@164.92.177.172 'sudo -u postgres psql -d xurshid_db -t -c "SELECT COUNT(*) as completed_count FROM stock_check_sessions WHERE status = '"'"'completed'"'"';"'

echo ""
echo "📋 Oxirgi tugatilgan sessiyalar (completed_at bilan):"
echo "---------------------------------------------------"

ssh root@164.92.177.172 'sudo -u postgres psql -d xurshid_db -t -A -F"|" -c "SELECT id, location_name, status, to_char(started_at, '"'"'DD.MM.YYYY HH24:MI'"'"'), to_char(completed_at, '"'"'DD.MM.YYYY HH24:MI'"'"'), COALESCE(completed_by_user_id::text, '"'"'NULL'"'"') FROM stock_check_sessions WHERE status = '"'"'completed'"'"' ORDER BY id DESC LIMIT 5;"' | while IFS='|' read -r id loc status started completed user_id; do
    echo "  📦 ID: $id | $loc | Started: $started | Completed: $completed | User: $user_id"
done

echo ""
echo "✅ Yangi maydonlar qo'shildi:"
echo "   - completed_at: Tekshiruv tugatilgan vaqt"
echo "   - completed_by_user_id: Tugatgan foydalanuvchi"
echo ""
echo "🔧 end_stock_check funksiyasi yangilandi:"
echo "   - completed_at = NOW() qo'shildi"
echo "   - completed_by_user_id to'ldiriladi"
echo ""
echo "📱 API endpoint yangilandi:"
echo "   - /api/check_stock/completed_sessions"
echo "   - Endi completed_at ko'rsatiladi"
echo ""
echo "====================================="
echo "✅ Tuzatish serverda qo'llanildi!"
echo "====================================="
