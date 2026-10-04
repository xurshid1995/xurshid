BEGIN;

ALTER TABLE supplier_purchases
    ADD COLUMN IF NOT EXISTS cost_currency_code VARCHAR(3),
    ADD COLUMN IF NOT EXISTS cost_price_original NUMERIC(24,5),
    ADD COLUMN IF NOT EXISTS receipt_exchange_rate NUMERIC(15,4);

WITH candidates AS (
    SELECT purchase.id AS purchase_id, history.id AS history_id,
           history.cost_currency_code, history.cost_price_original,
           history.receipt_exchange_rate,
           count(*) OVER (PARTITION BY purchase.id) AS purchase_matches,
           count(*) OVER (PARTITION BY history.id) AS history_matches
    FROM supplier_purchases purchase
    JOIN product_add_history history
      ON history.product_name = purchase.product_name
     AND history.quantity = purchase.quantity
     AND history.location_type IS NOT DISTINCT FROM purchase.location_type
     AND history.location_name IS NOT DISTINCT FROM purchase.location_name
     AND history.added_by IS NOT DISTINCT FROM purchase.added_by
     AND history.added_date BETWEEN purchase.created_at - interval '2 seconds'
                                AND purchase.created_at + interval '2 seconds'
    WHERE purchase.cost_currency_code IS NULL
      AND purchase.cost_price_original IS NULL
      AND purchase.receipt_exchange_rate IS NULL
      AND history.cost_currency_code IN ('USD', 'UZS')
      AND history.cost_price_original > 0
      AND history.receipt_exchange_rate > 0
      AND abs(purchase.cost_price - CASE
          WHEN history.cost_currency_code = 'UZS'
          THEN history.cost_price_original / history.receipt_exchange_rate
          ELSE history.cost_price_original END) <= 0.00005
)
UPDATE supplier_purchases purchase
SET cost_currency_code = matched.cost_currency_code,
    cost_price_original = matched.cost_price_original,
    receipt_exchange_rate = matched.receipt_exchange_rate
FROM candidates matched
WHERE purchase.id = matched.purchase_id
  AND matched.purchase_matches = 1
  AND matched.history_matches = 1;

COMMIT;