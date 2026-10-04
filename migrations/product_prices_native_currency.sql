BEGIN;

LOCK TABLE products IN ACCESS EXCLUSIVE MODE;

DO $$
DECLARE
    original_columns INTEGER;
    ambiguous_ids TEXT;
BEGIN
    SELECT count(*) INTO original_columns
    FROM information_schema.columns
    WHERE table_schema = current_schema() AND table_name = 'products'
      AND column_name IN ('cost_price_original', 'sell_price_original');

    IF original_columns = 0 THEN
        RETURN;
    END IF;
    IF original_columns <> 2 THEN
        RAISE EXCEPTION 'Incomplete original price schema; migration aborted';
    END IF;

    IF EXISTS (
        SELECT 1 FROM products
        WHERE coalesce(cost_currency_code, 'USD') NOT IN ('USD', 'UZS')
           OR coalesce(sell_currency_code, 'USD') NOT IN ('USD', 'UZS')
           OR (cost_currency_code = 'UZS' AND
               (cost_price_original IS NULL OR cost_price_original <= 0
                OR receipt_exchange_rate IS NULL OR receipt_exchange_rate <= 0))
           OR (sell_currency_code = 'UZS' AND
               (sell_price_original IS NULL OR sell_price_original <= 0))
    ) THEN
        RAISE EXCEPTION 'Missing or invalid original prices/currency; migration aborted';
    END IF;

    SELECT string_agg(id::text, ', ' ORDER BY id) INTO ambiguous_ids
    FROM products
    WHERE cost_currency_code = 'UZS'
      AND abs(cost_price - cost_price_original / receipt_exchange_rate) > 0.000005;
    IF ambiguous_ids IS NOT NULL THEN
        RAISE EXCEPTION 'Native average cost needs reconciliation for product IDs: %', ambiguous_ids;
    END IF;

    ALTER TABLE products
        ALTER COLUMN cost_price TYPE NUMERIC(29,10),
        ALTER COLUMN sell_price TYPE NUMERIC(29,10),
        ALTER COLUMN last_batch_cost TYPE NUMERIC(29,10);

    UPDATE products SET
        cost_price = CASE WHEN cost_currency_code = 'UZS'
            THEN cost_price_original ELSE cost_price END,
        sell_price = CASE WHEN sell_currency_code = 'UZS'
            THEN sell_price_original ELSE sell_price END,
        last_batch_cost = CASE WHEN cost_currency_code = 'UZS'
            THEN cost_price_original ELSE last_batch_cost END,
        cost_currency_code = coalesce(cost_currency_code, 'USD'),
        sell_currency_code = coalesce(sell_currency_code, 'USD');

    ALTER TABLE products
        DROP COLUMN cost_price_original,
        DROP COLUMN sell_price_original;
END $$;

COMMIT;