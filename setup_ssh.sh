#!/bin/bash
# SSH port 2222 sozlash va SSH kalit qo'shish skripti
# Bu skript DigitalOcean Console orqali ishga tushiriladi

echo "=== SSH sozlash boshlandi ==="

# 1. Port 2222 qo'shish
echo "Port 22" > /etc/ssh/sshd_config.d/99-custom-port.conf
echo "Port 2222" >> /etc/ssh/sshd_config.d/99-custom-port.conf
echo "[OK] Port 22 va 2222 qo'shildi"

# 2. sshd_config dagi Port qatorlarini kommentga olish (override bo'lmasligi uchun)
sed -i 's/^Port 22$/#Port 22/' /etc/ssh/sshd_config
sed -i 's/^Port 2222$/#Port 2222/' /etc/ssh/sshd_config
echo "[OK] sshd_config da Port qatorlari kommentga olindi"

# 3. PasswordAuthentication yoqish
echo "PasswordAuthentication yes" > /etc/ssh/sshd_config.d/60-cloudimg-settings.conf
echo "[OK] PasswordAuthentication yoqildi"

# 4. SSH kalit qo'shish
mkdir -p /root/.ssh
curl -s https://github.com/xurshid1995.keys >> /root/.ssh/authorized_keys
chmod 600 /root/.ssh/authorized_keys
chmod 700 /root/.ssh
echo "[OK] GitHub SSH kalit qo'shildi"

# 5. UFW port 2222 ochish
ufw allow 2222/tcp
echo "[OK] UFW port 2222 ochildi"

# 6. SSH qayta ishga tushirish
systemctl restart ssh
echo "[OK] SSH qayta ishga tushdi"

# 7. Tekshirish
echo ""
echo "=== NATIJA ==="
ss -tlnp | grep ssh
echo ""
echo "Agar yuqorida :22 va :2222 ko'rinsa - hammasi tayyor!"
echo "=== SSH sozlash tugadi ==="
