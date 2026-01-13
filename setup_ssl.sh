#!/bin/bash
# SSL sertifikat o'rnatish (Let's Encrypt)

echo "=== SSL Sertifikat O'rnatish ==="

# 1. Certbot o'rnatish
echo "1. Certbot o'rnatish..."
sudo apt update
sudo apt install -y certbot python3-certbot-nginx

# 2. SSL sertifikat olish
echo ""
echo "2. SSL sertifikat olish..."
sudo certbot --nginx -d ravon-qorakol.uz -d www.ravon-qorakol.uz --non-interactive --agree-tos --email jamshid@example.com --redirect

# 3. Avtomatik yangilash sozlash
echo ""
echo "3. Avtomatik yangilash sozlash..."
sudo systemctl enable certbot.timer
sudo systemctl start certbot.timer

# 4. Nginx test va restart
echo ""
echo "4. Nginx restart..."
sudo nginx -t
sudo systemctl restart nginx

echo ""
echo "=== ✅ SSL O'rnatildi! ==="
echo "Saytingiz: https://ravon-qorakol.uz"
echo ""
echo "SSL holati:"
sudo certbot certificates
