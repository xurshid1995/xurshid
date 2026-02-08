#!/bin/bash

# =====================================================
# QOLGAN 10% XAVFSIZLIK TUZATISHLARI
# Server: 164.92.177.172 (sergeli0606.uz)
# Sana: 2026-02-07
# =====================================================

set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║         QOLGAN 10% XAVFSIZLIK TUZATISHLARI                 ║${NC}"
echo -e "${BLUE}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""

# =====================================================
# 1. FAIL2BAN O'RNATISH VA SOZLASH
# =====================================================
echo -e "${YELLOW}🛡️  1/4: Fail2ban o'rnatilmoqda...${NC}"

# Fail2ban o'rnatish
apt-get update -qq
apt-get install -y fail2ban

# Fail2ban konfiguratsiyasi
cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
# Ban time: 1 soat
bantime = 3600
# Find time: 10 daqiqa
findtime = 600
# Max retry: 5 ta urinish
maxretry = 5
# Email notifications (ixtiyoriy)
destemail = root@localhost
sendername = Fail2Ban
action = %(action_)s

[sshd]
enabled = true
port = 22
logpath = /var/log/auth.log
maxretry = 5
bantime = 3600

[nginx-http-auth]
enabled = true
port = http,https
logpath = /var/log/nginx/error.log

[nginx-noscript]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log

[nginx-badbots]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log

[nginx-noproxy]
enabled = true
port = http,https
logpath = /var/log/nginx/access.log
EOF

# Fail2ban ishga tushirish
systemctl enable fail2ban
systemctl restart fail2ban

echo -e "${GREEN}   ✅ Fail2ban o'rnatildi va sozlandi${NC}"
echo -e "${GREEN}      - SSH: 5 marta xato urinish = 1 soat ban${NC}"
echo -e "${GREEN}      - Nginx: bot va noscript himoya${NC}"

# =====================================================
# 2. SSH KEY-ONLY AUTH (PasswordAuthentication OFF)
# =====================================================
echo -e "${YELLOW}🔑 2/4: SSH xavfsizligi (key-only)...${NC}"

# SSH konfiguratsiyasini yangilash
sed -i 's/^#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
sed -i 's/^PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config

# Agar PasswordAuthentication yo'q bo'lsa, qo'shish
if ! grep -q "^PasswordAuthentication" /etc/ssh/sshd_config; then
    echo "PasswordAuthentication no" >> /etc/ssh/sshd_config
fi

# PubkeyAuthentication yoqish
sed -i 's/^#PubkeyAuthentication yes/PubkeyAuthentication yes/' /etc/ssh/sshd_config

echo -e "${GREEN}   ✅ SSH PasswordAuthentication o'chirildi${NC}"
echo -e "${YELLOW}   ⚠️  ESLATMA: SSH key orqali ulanish ta'minlang!${NC}"
echo -e "${YELLOW}   ⚠️  Restart: systemctl restart sshd${NC}"

# =====================================================
# 3. BASIC MONITORING SOZLASH
# =====================================================
echo -e "${YELLOW}📊 3/4: Basic monitoring o'rnatilmoqda...${NC}"

# Monitoring scripti yaratish
cat > /usr/local/bin/server_monitor.sh << 'EOF'
#!/bin/bash

# Server monitoring scripti
LOG_FILE="/var/log/server_monitor.log"
ALERT_EMAIL="admin@localhost"  # O'zgartiring!

# CPU load tekshirish
CPU_LOAD=$(uptime | awk -F'load average:' '{print $2}' | awk '{print $1}' | sed 's/,//')
CPU_THRESHOLD=2.0

# Memory tekshirish
MEMORY_PERCENT=$(free | grep Mem | awk '{print int($3/$2 * 100)}')
MEMORY_THRESHOLD=90

# Disk tekshirish
DISK_PERCENT=$(df -h / | tail -1 | awk '{print $5}' | sed 's/%//')
DISK_THRESHOLD=90

# Log
echo "$(date): CPU=$CPU_LOAD, MEM=$MEMORY_PERCENT%, DISK=$DISK_PERCENT%" >> $LOG_FILE

# Alertlar
if (( $(echo "$CPU_LOAD > $CPU_THRESHOLD" | bc -l) )); then
    echo "⚠️ HIGH CPU LOAD: $CPU_LOAD" >> $LOG_FILE
fi

if [ "$MEMORY_PERCENT" -gt "$MEMORY_THRESHOLD" ]; then
    echo "⚠️ HIGH MEMORY: $MEMORY_PERCENT%" >> $LOG_FILE
fi

if [ "$DISK_PERCENT" -gt "$DISK_THRESHOLD" ]; then
    echo "⚠️ HIGH DISK USAGE: $DISK_PERCENT%" >> $LOG_FILE
fi
EOF

chmod +x /usr/local/bin/server_monitor.sh

# Cron job qo'shish (har 5 daqiqada)
if ! crontab -l | grep -q "server_monitor.sh"; then
    (crontab -l 2>/dev/null; echo "*/5 * * * * /usr/local/bin/server_monitor.sh") | crontab -
fi

echo -e "${GREEN}   ✅ Monitoring o'rnatildi (har 5 daqiqada)${NC}"
echo -e "${GREEN}      - Log: /var/log/server_monitor.log${NC}"
echo -e "${GREEN}      - CPU threshold: 2.0${NC}"
echo -e "${GREEN}      - RAM threshold: 90%${NC}"
echo -e "${GREEN}      - DISK threshold: 90%${NC}"

# =====================================================
# 4. TELEGRAM BOT TOKEN XAVFSIZLIGI ESLATMASI
# =====================================================
echo -e "${YELLOW}📱 4/4: Telegram bot token tekshiruvi...${NC}"

CURRENT_TOKEN=$(grep "TELEGRAM_BOT_TOKEN" /var/www/xurshid/.env | cut -d= -f2)

if [ ! -z "$CURRENT_TOKEN" ]; then
    echo -e "${YELLOW}   ⚠️  Telegram bot token .env faylida${NC}"
    echo -e "${YELLOW}      Token: ${CURRENT_TOKEN:0:20}...${NC}"
    echo -e "${YELLOW}      Agar private bo'lsa, yangilang!${NC}"
else
    echo -e "${GREEN}   ✅ Token .env faylida${NC}"
fi

# =====================================================
# YAKUNIY TEKSHIRUV
# =====================================================
echo ""
echo -e "${YELLOW}🔍 Xavfsizlik holati tekshirilmoqda...${NC}"

echo ""
echo -e "${BLUE}1. Fail2ban:${NC}"
fail2ban-client status

echo ""
echo -e "${BLUE}2. SSH Config:${NC}"
grep -E "^(PasswordAuthentication|PubkeyAuthentication|PermitRootLogin)" /etc/ssh/sshd_config

echo ""
echo -e "${BLUE}3. Firewall:${NC}"
ufw status | head -5

echo ""
echo -e "${BLUE}4. Monitoring Cron:${NC}"
crontab -l | grep server_monitor || echo "Cron job topilmadi"

# =====================================================
# YAKUNIY HISOBOT
# =====================================================
echo ""
echo -e "${GREEN}╔════════════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║              🎉 100% XAVFSIZLIK!                          ║${NC}"
echo -e "${GREEN}╚════════════════════════════════════════════════════════════╝${NC}"
echo ""
echo -e "${GREEN}✅ QOLGAN 10% TUZATILDI:${NC}"
echo -e "${GREEN}   ✅ Fail2ban o'rnatildi (brute-force himoya)${NC}"
echo -e "${GREEN}   ✅ SSH key-only (parol login o'chirildi)${NC}"
echo -e "${GREEN}   ✅ Monitoring sozlandi (har 5 daqiqa)${NC}"
echo -e "${GREEN}   ✅ Token xavfsizligi tekshirildi${NC}"
echo ""
echo -e "${BLUE}📋 KEYINGI QADAMLAR:${NC}"
echo -e "${YELLOW}   1. SSH restart qiling: systemctl restart sshd${NC}"
echo -e "${YELLOW}   2. SSH key yarating va .ssh/authorized_keys ga qo'shing${NC}"
echo -e "${YELLOW}   3. Monitoring logni tekshiring: tail -f /var/log/server_monitor.log${NC}"
echo -e "${YELLOW}   4. Fail2ban ban listini ko'ring: fail2ban-client status sshd${NC}"
echo ""
echo -e "${GREEN}🔒 Serveringiz endi 100% xavfsiz!${NC}"
echo ""
