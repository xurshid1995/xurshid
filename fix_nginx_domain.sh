#!/bin/bash
# Nginx muammosini tuzatish

echo "=== Nginx konfiguratsiyani tuzatish ==="

# 1. server_names_hash_bucket_size ni yoqish
sudo sed -i 's/# server_names_hash_bucket_size 64;/server_names_hash_bucket_size 128;/' /etc/nginx/nginx.conf

# 2. Nginx konfiguratsiyani tekshirish
echo ""
echo "Nginx sintaksisini tekshirish:"
sudo nginx -t

# 3. Agar hammasi to'g'ri bo'lsa, restart qilish
if [ $? -eq 0 ]; then
    echo ""
    echo "Nginx ni restart qilish:"
    sudo systemctl restart nginx
    sudo systemctl status nginx --no-pager | head -20
else
    echo "XATO: Nginx konfiguratsiyada xatolik bor!"
fi

echo ""
echo "=== Test qilish ==="
curl -H "Host: ravon-qorakol.uz" http://localhost 2>&1 | head -10
