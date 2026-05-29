#!/bin/bash
DIR="/var/backups/xurshid"
LOG="/var/log/xurshid_backup.log"
DATE=$(date +%Y%m%d_%H%M%S)
DATE_READABLE=$(date +'%Y-%m-%d %H:%M')

# Telegram sozlamalari
BOT_TOKEN="8563415748:AAGbBbI_3zE19Nuodw4hshFbC91klIXYEBI"
CHAT_ID="90869035"

send_telegram() {
    curl -s "https://api.telegram.org/bot$BOT_TOKEN/sendMessage" \
        -d "chat_id=$CHAT_ID" \
        -d "text=$1" \
        -d "parse_mode=HTML" > /dev/null 2>&1
}

send_file() {
    curl -s "https://api.telegram.org/bot$BOT_TOKEN/sendDocument" \
        -F "chat_id=$CHAT_ID" \
        -F "document=@$1" \
        -F "caption=$2" \
        -F "parse_mode=HTML" > /dev/null 2>&1
}

echo "[$DATE_READABLE] Backup boshlandi..." >> $LOG

# ── 1. SQL BACKUP ─────────────────────────────────────────────────────────────
sudo -u postgres pg_dump xurshid_db | gzip > $DIR/db_$DATE.sql.gz

if [ $? -eq 0 ]; then
    SIZE=$(du -sh $DIR/db_$DATE.sql.gz | cut -f1)
    COUNT=$(ls $DIR/*.sql.gz 2>/dev/null | wc -l)
    echo "[$DATE_READABLE] SQL backup muvaffaqiyatli: db_$DATE.sql.gz ($SIZE)" >> $LOG

    # Telegram'ga SQL fayl yuborish
    send_file "$DIR/db_$DATE.sql.gz" "✅ <b>SQL Backup muvaffaqiyatli!</b>
📅 Sana: $DATE_READABLE
💾 Hajm: $SIZE
🗂 Jami saqlangan: $COUNT ta"

    echo "[$DATE_READABLE] SQL fayl Telegram'ga yuborildi" >> $LOG
else
    echo "[$DATE_READABLE] XATO: SQL backup amalga oshmadi!" >> $LOG
    send_telegram "❌ <b>BACKUP XATO!</b>
📅 Sana: $DATE_READABLE
⚠️ Ma'lumotlar zaxiralanmadi! Tekshiring."
    exit 1
fi

# 30 kundan eski SQL backuplarni o'chirish
find $DIR -name "*.sql.gz" -mtime +30 -delete
echo "[$DATE_READABLE] Eski SQL backuplar tozalandi" >> $LOG

# ── 2. EXCEL HISOBOT ──────────────────────────────────────────────────────────
echo "[$DATE_READABLE] Excel hisobot yaratilmoqda..." >> $LOG
cd /var/www/xurshid
source venv/bin/activate
EXCEL_FILE=$(python /root/export_excel_backup.py 2>>$LOG)
EXCEL_EXIT=$?
deactivate

if [ $EXCEL_EXIT -eq 0 ] && [ -n "$EXCEL_FILE" ] && [ -f "$EXCEL_FILE" ]; then
    EXCEL_SIZE=$(du -sh "$EXCEL_FILE" | cut -f1)
    send_file "$EXCEL_FILE" "📊 <b>Excel Hisobot</b>
📅 Sana: $DATE_READABLE
💾 Hajm: $EXCEL_SIZE
📋 Varaqlar: Mahsulotlar, Mijozlar, Qarzlar, Sotuvlar (30k), Xarajatlar (30k)"
    echo "[$DATE_READABLE] Excel Telegram'ga yuborildi: $EXCEL_FILE ($EXCEL_SIZE)" >> $LOG
    # 7 kundan eski Excel fayllarni o'chirish
    find $DIR -name "hisobot_*.xlsx" -mtime +7 -delete
else
    echo "[$DATE_READABLE] Excel yaratishda xato (exit=$EXCEL_EXIT)" >> $LOG
    send_telegram "⚠️ <b>Excel hisobot xatosi!</b>
📅 Sana: $DATE_READABLE
SQL backup muvaffaqiyatli, lekin Excel yaratilmadi."
fi

echo "[$DATE_READABLE] Backup jarayoni tugadi" >> $LOG
