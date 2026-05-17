UPDATE customer_timeline_snapshot
SET snapshot_data = snapshot_data || '{"payment_status": "partial"}'
WHERE event_type = 'sale'
  AND event_id IN (982, 983, 984)
  AND snapshot_data->>'payment_status' = 'debt';
