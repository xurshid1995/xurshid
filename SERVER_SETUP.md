# Server Setup - 164.92.177.172 (www.sergeli0606.uz)

## 1. Server'ga kirish
```bash
ssh root@164.92.177.172
```

## 2. Tizimni yangilash
```bash
apt update && apt upgrade -y
```

## 3. Kerakli paketlarni o'rnatish
```bash
apt install -y python3 python3-pip python3-venv postgresql postgresql-contrib nginx git ufw certbot python3-certbot-nginx
```

## 4. PostgreSQL sozlash
```bash
# PostgreSQL ga kirish
sudo -u postgres psql

# Database va user yaratish
CREATE DATABASE xurshid_db;
CREATE USER xurshid_user WITH PASSWORD 'KUCHLI_PAROL_KIRITING';
ALTER ROLE xurshid_user SET client_encoding TO 'utf8';
ALTER ROLE xurshid_user SET default_transaction_isolation TO 'read committed';
ALTER ROLE xurshid_user SET timezone TO 'UTC';
GRANT ALL PRIVILEGES ON DATABASE xurshid_db TO xurshid_user;
\q
```

## 5. Loyihani deploy qilish
```bash
# Loyiha papkasini yaratish
mkdir -p /var/www/xurshid
cd /var/www/xurshid

# Git'dan clone qilish
git clone https://github.com/xurshid1995/xurshid.git .

# Virtual environment yaratish
python3 -m venv venv
source venv/bin/activate

# Dependencies o'rnatish
pip install -r requirements.txt
```

## 6. .env faylini sozlash
```bash
# .env fayl yaratish
nano .env
```

```bash
# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=xurshid_db
DB_USER=xurshid_user
DB_PASSWORD=KUCHLI_PAROL

# Flask
FLASK_ENV=production
FLASK_DEBUG=False
SECRET_KEY=$(python -c 'import secrets; print(secrets.token_hex(32))')

# Session
SESSION_COOKIE_SECURE=True
SESSION_COOKIE_HTTPONLY=True
SESSION_COOKIE_SAMESITE=None

# Server
SERVER_IP=164.92.177.172
DOMAIN=www.sergeli0606.uz

# Gunicorn
WORKERS=4
BIND=127.0.0.1:5000
TIMEOUT=300
```

## 7. Database migratsiyalarini bajarish
```bash
cd /var/www/xurshid
source venv/bin/activate

# Jadvallarni yaratish
python -c "from app import db; db.create_all(); print('✅ Database tables created')"
```

## 8. Logs papkasini yaratish
```bash
mkdir -p /var/www/xurshid/logs
chmod 755 /var/www/xurshid/logs
```

## 9. Systemd service sozlash
```bash
# Service fayl yaratish
sudo nano /etc/systemd/system/xurshid.service
```

```ini
[Unit]
Description=Xurshid Gunicorn Application
After=network.target postgresql.service
Wants=postgresql.service

[Service]
Type=notify
User=root
Group=root
WorkingDirectory=/var/www/xurshid
Environment="PATH=/var/www/xurshid/venv/bin"
ExecStart=/var/www/xurshid/venv/bin/gunicorn -c gunicorn_config.py app:app
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed
TimeoutStopSec=5
PrivateTmp=true
Restart=always
RestartSec=10

# Logging
StandardOutput=journal
StandardError=journal
SyslogIdentifier=xurshid

[Install]
WantedBy=multi-user.target
```

```bash
# Service'ni yoqish
sudo systemctl daemon-reload
sudo systemctl enable xurshid.service
sudo systemctl start xurshid.service
sudo systemctl status xurshid.service
```

## 10. Nginx sozlash
```bash
# Nginx konfiguratsiya fayl yaratish
sudo cp /var/www/xurshid/nginx_sergeli0606.conf /etc/nginx/sites-available/xurshid

# Symlink yaratish
sudo ln -s /etc/nginx/sites-available/xurshid /etc/nginx/sites-enabled/

# Default saytni o'chirish
sudo rm -f /etc/nginx/sites-enabled/default

# Nginx konfiguratsiyasini tekshirish
sudo nginx -t

# Nginx'ni qayta yuklash
sudo systemctl restart nginx
```

## 11. SSL sertifikatni o'rnatish (Let's Encrypt)
```bash
# Certbot bilan SSL o'rnatish
sudo certbot --nginx -d sergeli0606.uz -d www.sergeli0606.uz
```

Savollar:
- Email: sizning@email.com
- Terms: A (Agree)
- Share email: N
- Redirect HTTP to HTTPS: 2 (Yes)

## 12. Firewall sozlash
```bash
# UFW yoqish
sudo ufw allow OpenSSH
sudo ufw allow 'Nginx Full'
sudo ufw enable
sudo ufw status
```

## 13. SSL avtomatik yangilanishni tekshirish
```bash
# Certbot timer'ni tekshirish
sudo systemctl status certbot.timer

# Test yangilanish
sudo certbot renew --dry-run
```

## 14. Loyihani yangilash (deploy)
```bash
cd /var/www/xurshid
git pull
sudo systemctl restart xurshid.service
```

## 15. Tekshirish
```bash
# Service statusini ko'rish
sudo systemctl status xurshid.service

# Loglarni ko'rish
sudo journalctl -u xurshid.service -f

# Nginx loglarni ko'rish
sudo tail -f /var/log/nginx/access.log
sudo tail -f /var/log/nginx/error.log

# Gunicorn loglarni ko'rish
tail -f /var/www/xurshid/logs/error.log
tail -f /var/www/xurshid/logs/access.log

# Brauzerda ochish
# https://www.sergeli0606.uz
```

## Troubleshooting

### Service ishlamasa:
```bash
sudo journalctl -u xurshid.service -n 50 --no-pager
```

### Database ulanish muammosi:
```bash
# PostgreSQL ishlayotganini tekshirish
sudo systemctl status postgresql

# Database mavjudligini tekshirish
sudo -u postgres psql -l | grep xurshid
```

### Port band bo'lsa:
```bash
sudo lsof -i :5000
sudo kill -9 PID
```

### SSL muammosi:
```bash
sudo certbot certificates
sudo certbot renew --force-renewal
```

## Monitoring

### Server resurslarini kuzatish:
```bash
# CPU va xotira
htop

# Disk
df -h

# Network
netstat -tulpn | grep :5000
netstat -tulpn | grep :80
netstat -tulpn | grep :443
```

## Backup

### Database backup:
```bash
sudo -u postgres pg_dump xurshid_db > backup_$(date +%Y%m%d).sql
```

### Database restore:
```bash
sudo -u postgres psql xurshid_db < backup_20260129.sql
```

## Foydali buyruqlar

```bash
# Service'ni qayta yuklash
sudo systemctl restart xurshid.service

# Nginx'ni qayta yuklash
sudo systemctl reload nginx

# Loglarni tozalash
sudo journalctl --vacuum-time=7d

# Git'ni yangilash
cd /var/www/xurshid && git pull && sudo systemctl restart xurshid.service
```

## Xavfsizlik

1. ✅ SECRET_KEY kuchli bo'lishi kerak
2. ✅ Database parol kuchli bo'lishi kerak
3. ✅ UFW firewall yoqilgan
4. ✅ SSH port o'zgartirilgan (opsional)
5. ✅ SSL sertifikat o'rnatilgan
6. ✅ Session cookie secure
7. ✅ CSRF protection yoqilgan
8. ✅ Rate limiting yoqilgan

## Support

Server: 164.92.177.172
Domain: www.sergeli0606.uz
Location: DigitalOcean Frankfurt
