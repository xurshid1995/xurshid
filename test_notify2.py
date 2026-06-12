#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Test eslatma xabarini adminga yuborish"""
import os, sys, requests

# .env faylni qo'lda o'qish
env_file = '/var/www/xurshid/.env'
env_vars = {}
try:
    with open(env_file, 'r') as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, _, v = line.partition('=')
                env_vars[k.strip()] = v.strip().strip('"').strip("'")
except Exception as e:
    print(f'ERROR .env: {e}')
    sys.exit(1)

token = env_vars.get('TELEGRAM_BOT_TOKEN', '')
admin_ids_str = env_vars.get('TELEGRAM_ADMIN_CHAT_IDS', '')

print('Token:', token[:20] + '...' if len(token) > 20 else '(empty)')
print('Admin IDs:', admin_ids_str)

if not token or not admin_ids_str:
    print('ERROR: token yoki admin_ids yoq!')
    sys.exit(1)

admin_ids = [int(x.strip()) for x in admin_ids_str.split(',') if x.strip()]
url = f'https://api.telegram.org/bot{token}/sendMessage'

msg = (
    "⚠️ <b>ERTAGA TO'LOV MUDDATI KELADI</b>\n"
    "<b>Sana: 13.06.2026</b>\n"
    "─" * 22 + "\n"
    "1. <b>Alisher aka</b>\n"
    "   📞 +998(97) 772-04-01\n"
    "   💵 $550.00 | 🏪 Sergeli 1/4/3\n"
    "─" * 22 + "\n"
    "Jami: <b>1 ta mijoz</b> | <b>$550.00</b>\n\n"
    "🔴 <b>MUDDATI O'TGAN QARZLAR</b>\n"
    "─" * 22 + "\n"
    "1. <b>Sarvar Aka Lampichka</b>\n"
    "   📞 +998(90) 948-84-85\n"
    "   💵 $285.00 | 🏪 Sergeli 1/4/3\n"
    "   ❗ 68 kun o'tgan (05.04.2026)\n"
    "─" * 22 + "\n"
    "Jami: <b>1 ta mijoz</b> | <b>$285.00</b>"
)

for chat_id in admin_ids:
    r = requests.post(url, json={'chat_id': chat_id, 'text': msg, 'parse_mode': 'HTML'}, timeout=10)
    resp = r.json()
    print(f'Chat {chat_id}: status={r.status_code} ok={resp.get("ok")} err={resp.get("description","")}')
