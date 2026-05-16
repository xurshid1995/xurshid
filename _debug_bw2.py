#!/usr/bin/env python3
"""DO bandwidth unit va haqiqiy trafik tekshirish"""
import os, requests, json
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('DO_API_TOKEN')
hdrs = {'Authorization': f'Bearer {token}'}

# Hamma dropletlar uchun trafik
droplet_ids = [540683342, 548050262, 570335878]

from datetime import datetime
now = datetime.utcnow()
start = int(datetime(now.year, now.month, 1).timestamp())
end = int(now.timestamp())

for did in droplet_ids:
    print(f'\n=== Droplet {did} ===')
    for direction in ['outbound', 'inbound']:
        url = f'https://api.digitalocean.com/v2/monitoring/metrics/droplet/bandwidth?host_id={did}&interface=public&direction={direction}&start={start}&end={end}'
        r = requests.get(url, headers=hdrs, timeout=15)
        data = r.json()
        results = data.get('data', {}).get('result', [])
        if not results:
            print(f'  {direction}: NO DATA')
            continue
        vals = results[0].get('values', [])
        if len(vals) < 2:
            print(f'  {direction}: {len(vals)} values')
            continue
        step = vals[1][0] - vals[0][0]
        floats = [float(v[1]) for v in vals]
        avg_val = sum(floats) / len(floats)
        max_val = max(floats)
        total_raw = sum(floats) * step
        
        print(f'  {direction}: {len(vals)} pts, step={step}s, avg={avg_val:.6f}, max={max_val:.6f}')
        print(f'    If B/s:   {total_raw/1e9:.4f} GB')
        print(f'    If KB/s:  {total_raw*1e3/1e9:.4f} GB')
        print(f'    If Mbps:  {total_raw*1e6/8/1e9:.4f} GB')
        print(f'    If Gbps:  {total_raw*1e9/8/1e9:.4f} GB')
