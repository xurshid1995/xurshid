BEGIN;

ALTER TABLE products
    ADD COLUMN IF NOT EXISTS cost_currency_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS sell_currency_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS cost_price_original NUMERIC(24,5),
    ADD COLUMN IF NOT EXISTS sell_price_original NUMERIC(24,5),
    ADD COLUMN IF NOT EXISTS receipt_exchange_rate NUMERIC(15,4);

ALTER TABLE product_add_history
    ADD COLUMN IF NOT EXISTS cost_currency_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS sell_currency_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS cost_price_original NUMERIC(24,5),
    ADD COLUMN IF NOT EXISTS sell_price_original NUMERIC(24,5),
    ADD COLUMN IF NOT EXISTS receipt_exchange_rate NUMERIC(15,4);

ALTER TABLE sale_items
    ADD COLUMN IF NOT EXISTS price_currency_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS unit_price_original NUMERIC(24,5);

COMMIT;