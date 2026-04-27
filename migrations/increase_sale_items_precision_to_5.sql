-- SaleItem jadvalidagi USD ustunlar precision ni 4 dan 5 ga oshirish
-- unit_price, total_price, cost_price, profit

ALTER TABLE sale_items
    ALTER COLUMN unit_price TYPE NUMERIC(10, 5),
    ALTER COLUMN total_price TYPE NUMERIC(12, 5),
    ALTER COLUMN cost_price TYPE NUMERIC(10, 5),
    ALTER COLUMN profit TYPE NUMERIC(12, 5);
