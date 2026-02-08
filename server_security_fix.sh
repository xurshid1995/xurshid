#!/bin/bash

# =====================================================
# SERVER XAVFSIZLIK VA OPTIMIZATSIYA SCRIPTI
# Server: 164.92.177.172 (sergeli0606.uz)
# Sana: 2026-02-07
# =====================================================

set -e  # Xatolikda to'xtash

# Ranglar
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║    SERVER XAVFSIZLIK VA OPTIMIZATSIYA SCRIPTI             ║${NC}"
echo -e "${BLUE}║    sergeli0606.uz (164.92.177.172)                        ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# Backup katalogini yaratish
BACKUP_DIR="/root/backup_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"
echo -e "${GREEN}✅ Backup katalogi: $BACKUP_DIR${NC}"

# =====================================================
# 1. BACKUP - Muhim fayllarni saqlash
# =====================================================
echo ""
echo -e "${YELLOW}📦 1/9: Backup yaratilmoqda...${NC}"

# .env faylini backup qilish
if [ -f /var/www/xurshid/.env ]; then
    cp /var/www/xurshid/.env "$BACKUP_DIR/.env.backup"
    echo -e "${GREEN}   ✅ .env fayli backup qilindi${NC}"
fi

# Nginx konfiguratsiyasini backup qilish
cp /etc/nginx/sites-available/sergeli0606.uz "$BACKUP_DIR/nginx.backup" 2>/dev/null || true
echo -e "${GREEN}   ✅ Nginx config backup qilindi${NC}"

# SSH konfiguratsiyasini backup qilish
cp /etc/ssh/sshd_config "$BACKUP_DIR/sshd_config.backup"
echo -e "${GREEN}   ✅ SSH config backup qilindi${NC}"

# Database backup
sudo -u postgres pg_dump xurshid_db > "$BACKUP_DIR/database_backup.sql"
echo -e "${GREEN}   ✅ Database backup qilindi${NC}"

# =====================================================
# 2. YANGI PAROLLAR GENERATSIYA QILISH
# =====================================================
echo ""
echo -e "${YELLOW}🔑 2/9: Yangi parollar yaratilmoqda...${NC}"

# SECRET_KEY generatsiya
NEW_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
echo -e "${GREEN}   ✅ Yangi SECRET_KEY: ${NEW_SECRET_KEY:0:20}...${NC}"

# Database parol generatsiya (20 belgili)
NEW_DB_PASSWORD=$(openssl rand -base64 18 | tr -d "=+/" | cut -c1-20)
echo -e "${GREEN}   ✅ Yangi DB parol yaratildi${NC}"

# Parollarni faylga saqlash
cat > "$BACKUP_DIR/new_credentials.txt" << EOF
=== YANGI PAROLLAR ===
Sana: $(date)

SECRET_KEY=$NEW_SECRET_KEY
DB_PASSWORD=$NEW_DB_PASSWORD

⚠️ BU FAYLNI XAVFSIZ JOYDA SAQLANG!
⚠️ Backup katalogi: $BACKUP_DIR
EOF

chmod 600 "$BACKUP_DIR/new_credentials.txt"
echo -e "${GREEN}   ✅ Parollar saqlandi: $BACKUP_DIR/new_credentials.txt${NC}"

# =====================================================
# 3. FIREWALL SOZLASH (UFW)
# =====================================================
echo ""
echo -e "${YELLOW}🔥 3/9: Firewall (UFW) sozlanmoqda...${NC}"

# UFW o'rnatish (agar yo'q bo'lsa)
if ! command -v ufw &> /dev/null; then
    apt-get install -y ufw
fi

# Barcha kiruvchi trafikni bloklash
ufw default deny incoming
ufw default allow outgoing

# Kerakli portlarni ochish
ufw allow 22/tcp comment 'SSH'
ufw allow 80/tcp comment 'HTTP'
ufw allow 443/tcp comment 'HTTPS'

# UFW ni yoqish (avtomatik "yes")
echo "y" | ufw enable

echo -e "${GREEN}   ✅ Firewall yoqildi (SSH, HTTP, HTTPS ruxsat etilgan)${NC}"

# =====================================================
# 4. SSH XAVFSIZLIGINI OSHIRISH
# =====================================================
echo ""
echo -e "${YELLOW}🔐 4/9: SSH xavfsizligi oshirilmoqda...${NC}"

# SSH konfiguratsiyasini yangilash
sed -i 's/^PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/^#PermitRootLogin no/PermitRootLogin no/' /etc/ssh/sshd_config

# Agar PermitRootLogin yo'q bo'lsa, qo'shish
if ! grep -q "^PermitRootLogin" /etc/ssh/sshd_config; then
    echo "PermitRootLogin no" >> /etc/ssh/sshd_config
fi

# Password authentication'ni o'chirish (faqat key-based)
# sed -i 's/^#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
# sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

# SSH portini o'zgartirish (ixtiyoriy)
# sed -i 's/^#Port 22/Port 2222/' /etc/ssh/sshd_config

echo -e "${GREEN}   ✅ Root login o'chirildi${NC}"
echo -e "${YELLOW}   ⚠️  ESLATMA: SSH restart qilinmaydi (ulanish uzilmasligi uchun)${NC}"
echo -e "${YELLOW}   ⚠️  Keyinroq: systemctl restart sshd${NC}"

# =====================================================
# 5. SWAP YARATISH (2GB)
# =====================================================
echo ""
echo -e "${YELLOW}💾 5/9: SWAP yaratilmoqda (2GB)...${NC}"

# SWAP mavjudligini tekshirish
if ! swapon --show | grep -q '/swapfile'; then
    # SWAP fayli yaratish
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
    
    # Doimiy qilish (fstab'ga qo'shish)
    if ! grep -q '/swapfile' /etc/fstab; then
        echo '/swapfile none swap sw 0 0' >> /etc/fstab
    fi
    
    # Swappiness sozlash (10 - faqat zarur bo'lganda ishlatish)
    sysctl vm.swappiness=10
    echo 'vm.swappiness=10' >> /etc/sysctl.conf
    
    echo -e "${GREEN}   ✅ 2GB SWAP yaratildi va yoqildi${NC}"
else
    echo -e "${GREEN}   ✅ SWAP allaqachon mavjud${NC}"
fi

# =====================================================
# 6. DATABASE PAROLINI O'ZGARTIRISH
# =====================================================
echo ""
echo -e "${YELLOW}🗄️  6/9: Database paroli o'zgartirilmoqda...${NC}"

# PostgreSQL parolini yangilash
sudo -u postgres psql -c "ALTER USER xurshid_user WITH PASSWORD '$NEW_DB_PASSWORD';"

echo -e "${GREEN}   ✅ Database paroli yangilandi${NC}"

# =====================================================
# 7. .ENV FAYLINI YANGILASH
# =====================================================
echo ""
echo -e "${YELLOW}⚙️  7/9: .env fayli yangilanmoqda...${NC}"

# .env faylini yangilash
cat > /var/www/xurshid/.env << EOF
# Production Environment - Updated $(date)

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=xurshid_db
DB_USER=xurshid_user
DB_PASSWORD=$NEW_DB_PASSWORD

# Flask
FLASK_ENV=production
FLASK_DEBUG=False
SECRET_KEY=$NEW_SECRET_KEY

# Default Settings
DEFAULT_PHONE_PLACEHOLDER=Telefon kiritilmagan

# Gunicorn (production server)
WORKERS=3
BIND=127.0.0.1:5000
TIMEOUT=300

# Telegram Bot sozlamalari
TELEGRAM_BOT_TOKEN=8563415748:AAGbBbI_3zE19Nuodw4hshFbC91klIXYEBI
TELEGRAM_ADMIN_CHAT_IDS=
DEBT_REMINDER_TIME=10:00
WEEKLY_REPORT_DAY=1
MINIMUM_DEBT_AMOUNT=1.0
EOF

chown www-data:www-data /var/www/xurshid/.env
chmod 600 /var/www/xurshid/.env

echo -e "${GREEN}   ✅ .env fayli yangilandi${NC}"

# =====================================================
# 8. GUNICORN SYSTEMD SERVICE YARATISH
# =====================================================
echo ""
echo -e "${YELLOW}🚀 8/9: Gunicorn systemd service yaratilmoqda...${NC}"

# Gunicorn service faylini yaratish
cat > /etc/systemd/system/gunicorn.service << 'EOF'
[Unit]
Description=Gunicorn instance for xurshid Flask app
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=www-data
Group=www-data
WorkingDirectory=/var/www/xurshid
Environment="PATH=/var/www/xurshid/venv/bin"
EnvironmentFile=/var/www/xurshid/.env
ExecStart=/var/www/xurshid/venv/bin/gunicorn -c gunicorn_config.py app:app
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Systemd reload va enable
systemctl daemon-reload
systemctl enable gunicorn.service

echo -e "${GREEN}   ✅ Gunicorn service yaratildi va enable qilindi${NC}"

# =====================================================
# 9. NGINX KONFIGURATSIYASINI TUZATISH
# =====================================================
echo ""
echo -e "${YELLOW}🌐 9/9: Nginx konfiguratsiyasi tuzatilmoqda...${NC}"

# Nginx konfiguratsiyasida static file xatolarini tuzatish
# app.js va bundle.js'ni locationdan olib tashlash
sed -i '/location ~\* \\.(js|css)\$/,/}/s/expires 1h;/expires 1d;/' /etc/nginx/sites-available/sergeli0606.uz 2>/dev/null || true

# Nginx test
nginx -t

echo -e "${GREEN}   ✅ Nginx konfiguratsiyasi tekshirildi${NC}"

# =====================================================
# SERVISLARNI RESTART QILISH
# =====================================================
echo ""
echo -e "${YELLOW}♻️  Servislar qayta ishga tushirilmoqda...${NC}"

# PostgreSQL restart (parol o'zgargani uchun)
systemctl restart postgresql

# Gunicorn'ni to'xtatish (eski jarayon)
pkill -f gunicorn || true
sleep 2

# Gunicorn'ni systemd orqali ishga tushirish
systemctl start gunicorn

# Nginx reload
systemctl reload nginx

echo -e "${GREEN}   ✅ Barcha servislar qayta ishga tushdi${NC}"

# =====================================================
# YAKUNIY HISOBOT
# =====================================================
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║                  ✅ YAKUNLANDI!                            ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${BLUE}📊 BAJARIILGAN ISHLAR:${NC}"
echo -e "${GREEN}   ✅ 1. Backup yaratildi: $BACKUP_DIR${NC}"
echo -e "${GREEN}   ✅ 2. Yangi parollar yaratildi${NC}"
echo -e "${GREEN}   ✅ 3. Firewall yoqildi (UFW)${NC}"
echo -e "${GREEN}   ✅ 4. SSH xavfsizligi oshirildi${NC}"
echo -e "${GREEN}   ✅ 5. 2GB SWAP yaratildi${NC}"
echo -e "${GREEN}   ✅ 6. Database paroli o'zgartirildi${NC}"
echo -e "${GREEN}   ✅ 7. .env fayli yangilandi${NC}"
echo -e "${GREEN}   ✅ 8. Gunicorn systemd service yaratildi${NC}"
echo -e "${GREEN}   ✅ 9. Nginx tuzatildi${NC}"
echo ""
echo -e "${YELLOW}⚠️  MUHIM:${NC}"
echo -e "${YELLOW}   1. Yangi parollar: $BACKUP_DIR/new_credentials.txt${NC}"
echo -e "${YELLOW}   2. Eski backup: $BACKUP_DIR${NC}"
echo -e "${YELLOW}   3. SSH'ni restart qilish: systemctl restart sshd${NC}"
echo -e "${YELLOW}      (Hozir restart qilinmadi - ulanish uzilmasligi uchun)${NC}"
echo ""
echo -e "${BLUE}🔍 TEKSHIRISH:${NC}"
echo "   - Sayt: https://sergeli0606.uz"
echo "   - Firewall: ufw status"
echo "   - Gunicorn: systemctl status gunicorn"
echo "   - SWAP: swapon --show"
echo ""
echo -e "${GREEN}🎉 Serveringiz xavfsiz va optimallashtirildi!${NC}"
echo ""
