-- supplier_payments jadvaliga naqd/click/terminal to'lov taqsimoti ustunlari
-- ESLATMA: app.py da create_tables() ichida idempotent ALTER TABLE orqali avtomatik qo'shiladi.

ALTER TABLE supplier_payments
    ADD COLUMN IF NOT EXISTS cash_usd DECIMAL(15, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS click_usd DECIMAL(15, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS terminal_usd DECIMAL(15, 2) DEFAULT 0,
    ADD COLUMN IF NOT EXISTS currency_rate DECIMAL(15, 4);
