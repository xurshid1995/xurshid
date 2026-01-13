#!/bin/bash
sudo -u postgres psql jamshid_db -f /tmp/check_pending_sales.sql
