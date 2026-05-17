UPDATE sales SET payment_status = 'partial' WHERE id = 985 AND payment_status = 'debt';
UPDATE customer_timeline_snapshot SET snapshot_data = snapshot_data || '{"payment_status": "partial"}' WHERE event_type = 'sale' AND event_id = 985;
