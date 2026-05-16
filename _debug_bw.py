#!/usr/bin/env python3
import os, json, requests
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('DO_API_TOKEN')
print('Token exists:', bool(token), '| first 8:', token[:8] if token else 'NONE')

r = requests.get('https://api.digitalocean.com/v2/droplets?per_page=5',
    headers={'Authorization': f'Bearer {token}'}, timeout=15)
droplets = r.json().get('droplets', [])
print('Droplets:', len(droplets))
for d in droplets[:3]:
    print(f"  id={d['id']} name={d['name']} transfer={d.get('size',{}).get('transfer')}TB")

if droplets:
    did = droplets[0]['id']
    from datetime import datetime
    now = datetime.utcnow()
    start = int(datetime(now.year, now.month, 1).timestamp())
    end = int(now.timestamp())
    url = f'https://api.digitalocean.com/v2/monitoring/metrics/droplet/bandwidth?host_id={did}&interface=public&direction=outbound&start={start}&end={end}'
    r2 = requests.get(url, headers={'Authorization': f'Bearer {token}'}, timeout=15)
    print('Status:', r2.status_code)
    data = r2.json()
    print('Keys:', list(data.keys()))
    results = data.get('data', {}).get('result', [])
    print('Result count:', len(results))
    if results:
        vals = results[0].get('values', [])
        print('Values count:', len(vals))
        print('First 3 vals:', vals[:3])
        print('Last 3 vals:', vals[-3:])
        if vals:
            step = vals[1][0] - vals[0][0] if len(vals) > 1 else 300
            total_bits = sum(float(v[1]) * step for v in vals)
            gb = total_bits / 8 / 1e9
            print(f'Calculated GB: {gb:.4f}')
    else:
        print('RAW:', json.dumps(data)[:800])
