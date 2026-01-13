#!/bin/bash
echo "=== TASDIQLASHDAN OLDIN ==="
sudo -u postgres psql sayt_db -c "SELECT id, customer_id, payment_status, to_char(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date, cash_usd, click_usd, terminal_usd, debt_usd FROM sales ORDER BY id DESC LIMIT 3;"

echo ""
echo "=== 5 SEKUND KUTILMOQDA... PENDING SAVDONI TASDIQLANG ==="
sleep 5

echo ""
echo "=== TASDIQLASHDAN KEYIN ==="
sudo -u postgres psql sayt_db -c "SELECT id, customer_id, payment_status, to_char(sale_date, 'YYYY-MM-DD HH24:MI:SS') as sale_date, to_char(created_at, 'YYYY-MM-DD HH24:MI:SS') as created_at, cash_usd, click_usd, terminal_usd, debt_usd FROM sales ORDER BY id DESC LIMIT 3;"

echo ""
echo "=== PAYMENT STATUS TAQSIMOTI ==="
sudo -u postgres psql sayt_db -c "SELECT payment_status, COUNT(*) FROM sales GROUP BY payment_status;"
