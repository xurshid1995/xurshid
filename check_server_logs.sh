#!/bin/bash

echo "=============================================================="
echo "PostgreSQL LOG TEKSHIRUVI - 2026-02-06 17:21 atrofida"
echo "=============================================================="

# PostgreSQL loglarni topish
LOG_DIR="/var/log/postgresql"

if [ -d "$LOG_DIR" ]; then
    echo "PostgreSQL log fayllari:"
    ls -lh "$LOG_DIR"/*.log 2>/dev/null | tail -5
    echo
    
    # 2026-02-06 17:21 atrofidagi UPDATE querylarni qidirish
    echo "UPDATE store_stocks querylar:"
    echo "--------------------------------------------------------------"
    grep -h "2026-02-06 17:2" "$LOG_DIR"/*.log 2>/dev/null | grep -i "UPDATE.*store_stocks" | head -20
    
    echo
    echo "Sa'y berildi 369 yoki 370 raqamlarini qidirish:"
    echo "--------------------------------------------------------------"
    grep -h "2026-02-06 17:2" "$LOG_DIR"/*.log 2>/dev/null | grep -E "(369|370)" | head -20
else
    echo "PostgreSQL log katalogi topilmadi: $LOG_DIR"
    echo "Boshqa joyda bo'lishi mumkin..."
fi

echo
echo "=============================================================="
echo "Gunicorn/Flask applicationning loglaridan qidirish:"
echo "=============================================================="

# Flask app logsni tekshirish
if [ -f "/var/log/flask/app.log" ]; then
    echo "Flask app log topildi, tekshirilmoqda..."
    grep "2026-02-06 17:2" /var/log/flask/app.log | tail -20
elif [ -f "/var/www/xurshid/app.log" ]; then
    echo "Flask app log topildi, tekshirilmoqda..."
    grep "2026-02-06 17:2" /var/www/xurshid/app.log | tail -20
else
    echo "Flask app log topilmadi"
    echo "Gunicorn journal loglardan qidirish..."
    journalctl -u gunicorn --since "2026-02-06 17:20" --until "2026-02-06 17:25" | grep -i "product\|stock\|369\|370"
fi

echo
echo "=============================================================="
echo "Yakuniy tavsiya:"
echo "=============================================================="
echo "1. PostgreSQL query logging yoqilmagan bo'lishi mumkin"
echo "2. Application level logging tekshirish kerak"
echo "3. Agar hech narsa topilmasa - bu BUG yoki tizim ichki muammo"
echo "=============================================================="
