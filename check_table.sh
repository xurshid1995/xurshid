#!/bin/bash

echo "SALE_ITEMS TABLE STRUCTURE:"
sudo -u postgres psql xurshid_db -c "\d sale_items"

echo ""
echo "SAVDO 115 ITEMS:"
sudo -u postgres psql xurshid_db -c "SELECT * FROM sale_items WHERE sale_id = 115;"
