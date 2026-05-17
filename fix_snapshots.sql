UPDATE customer_timeline_snapshot
SET snapshot_data = snapshot_data || '{"payment_status": "debt"}'
WHERE event_type = 'sale'
  AND snapshot_data->>'payment_status' = 'partial'
  AND (snapshot_data->>'cash_usd')::float = 0
  AND (snapshot_data->>'click_usd')::float = 0
  AND (snapshot_data->>'terminal_usd')::float = 0
  AND (snapshot_data->>'balance_usd')::float = 0
  AND (snapshot_data->>'debt_usd')::float > 0;
