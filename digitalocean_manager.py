# -*- coding: utf-8 -*-
"""
DigitalOcean Server Manager
Dropletlarni boshqarish (power on/off, status tekshirish)
"""
import os
import logging
import requests
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)


class DigitalOceanManager:
    """DigitalOcean API orqali serverlarni boshqarish"""

    BASE_URL = "https://api.digitalocean.com/v2"

    def __init__(self, api_token: Optional[str] = None):
        self.api_token = api_token or os.getenv('DO_API_TOKEN')
        if not self.api_token:
            logger.warning("⚠️ DO_API_TOKEN sozlanmagan!")
        self.headers = {
            "Authorization": f"Bearer {self.api_token}",
            "Content-Type": "application/json"
        }

    def _request(self, method: str, endpoint: str, data: dict = None) -> Optional[Dict]:
        """DigitalOcean API ga so'rov yuborish"""
        url = f"{self.BASE_URL}/{endpoint}"
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self.headers,
                json=data,
                timeout=30
            )

            if response.status_code in [200, 201, 202, 204]:
                if response.content:
                    return response.json()
                return {"status": "success"}
            else:
                error_msg = response.json().get('message', response.text)
                logger.error(f"DO API xatosi ({response.status_code}): {error_msg}")
                return None

        except requests.exceptions.Timeout:
            logger.error(f"DO API timeout: {endpoint}")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"DO API ulanish xatosi: {endpoint}")
            return None
        except Exception as e:
            logger.error(f"DO API kutilmagan xato: {e}")
            return None

    # ==========================================
    # DROPLET OPERATSIYALARI
    # ==========================================

    def list_droplets(self) -> List[Dict]:
        """Barcha dropletlar ro'yxatini olish"""
        result = self._request("GET", "droplets?per_page=200")
        if result and 'droplets' in result:
            return result['droplets']
        return []

    def get_droplet(self, droplet_id: int) -> Optional[Dict]:
        """Bitta droplet ma'lumotini olish"""
        result = self._request("GET", f"droplets/{droplet_id}")
        if result and 'droplet' in result:
            return result['droplet']
        return None

    def get_droplet_status(self, droplet_id: int) -> Optional[str]:
        """Droplet holatini tekshirish (active, off, new, archive)"""
        droplet = self.get_droplet(droplet_id)
        if droplet:
            return droplet.get('status')
        return None

    def power_on(self, droplet_id: int) -> bool:
        """Serverni yoqish (power on)"""
        result = self._request("POST", f"droplets/{droplet_id}/actions", {
            "type": "power_on"
        })
        if result:
            logger.info(f"✅ Droplet {droplet_id} yoqilmoqda...")
            return True
        logger.error(f"❌ Droplet {droplet_id} yoqishda xato")
        return False

    def power_off(self, droplet_id: int) -> bool:
        """Serverni o'chirish (power off)"""
        result = self._request("POST", f"droplets/{droplet_id}/actions", {
            "type": "power_off"
        })
        if result:
            logger.info(f"⏹️ Droplet {droplet_id} o'chirilmoqda...")
            return True
        logger.error(f"❌ Droplet {droplet_id} o'chirishda xato")
        return False

    def shutdown(self, droplet_id: int) -> bool:
        """Serverni graceful shutdown qilish"""
        result = self._request("POST", f"droplets/{droplet_id}/actions", {
            "type": "shutdown"
        })
        if result:
            logger.info(f"🔄 Droplet {droplet_id} shutdown...")
            return True
        logger.error(f"❌ Droplet {droplet_id} shutdown xato")
        return False

    def reboot(self, droplet_id: int) -> bool:
        """Serverni qayta yuklash"""
        result = self._request("POST", f"droplets/{droplet_id}/actions", {
            "type": "reboot"
        })
        if result:
            logger.info(f"🔄 Droplet {droplet_id} reboot...")
            return True
        logger.error(f"❌ Droplet {droplet_id} reboot xato")
        return False

    # ==========================================
    # YORDAMCHI FUNKSIYALAR
    # ==========================================

    def get_droplet_info(self, droplet_id: int) -> Optional[Dict]:
        """Droplet haqida qisqa ma'lumot"""
        droplet = self.get_droplet(droplet_id)
        if not droplet:
            return None

        # IP manzilni olish
        ip_address = None
        networks = droplet.get('networks', {})
        for ip_info in networks.get('v4', []):
            if ip_info.get('type') == 'public':
                ip_address = ip_info.get('ip_address')
                break

        return {
            'id': droplet['id'],
            'name': droplet['name'],
            'status': droplet['status'],
            'ip_address': ip_address,
            'memory': droplet.get('memory'),
            'vcpus': droplet.get('vcpus'),
            'disk': droplet.get('disk'),
            'region': droplet.get('region', {}).get('name'),
            'image': droplet.get('image', {}).get('name'),
            'created_at': droplet.get('created_at')
        }

    def get_all_droplets_info(self) -> List[Dict]:
        """Barcha dropletlar haqida qisqa ma'lumot"""
        droplets = self.list_droplets()
        result = []
        for droplet in droplets:
            ip_address = None
            networks = droplet.get('networks', {})
            for ip_info in networks.get('v4', []):
                if ip_info.get('type') == 'public':
                    ip_address = ip_info.get('ip_address')
                    break

            result.append({
                'id': droplet['id'],
                'name': droplet['name'],
                'status': droplet['status'],
                'ip_address': ip_address,
                'memory': droplet.get('memory'),
                'vcpus': droplet.get('vcpus'),
                'region': droplet.get('region', {}).get('slug')
            })
        return result

    def is_token_valid(self) -> bool:
        """API token ishlaydimi tekshirish"""
        result = self._request("GET", "account")
        return result is not None

    # ==========================================
    # TRAFIK / BANDWIDTH MONITORING
    # ==========================================

    def _calc_bandwidth_gb(self, result: Optional[Dict]) -> float:
        """DO Monitoring API javobidan GB hisoblash (Mbps × step_interval → GB)"""
        if not result:
            return 0.0
        try:
            data = result.get('data', {})
            results_list = data.get('result', [])
            if not results_list:
                return 0.0
            values = results_list[0].get('values', [])
            if len(values) < 2:
                return 0.0
            # Qadamlar orasidagi vaqt (sekundda)
            step = values[1][0] - values[0][0]
            if step <= 0:
                step = 3279  # default ~55 daqiqa
            # DO returns Mbps → Mbits = Mbps × step → GB = Mbits / 8 / 1000
            total_mbits = sum(float(v[1]) * step for v in values)
            return total_mbits / 8 / 1_000  # Mbits → MB → GB
        except Exception as e:
            logger.error(f"Bandwidth hisoblashda xato: {e}")
            return 0.0

    def get_monthly_bandwidth_gb(self, droplet_id: int) -> Dict:
        """Joriy oy trafik ishlatilishini olish (inbound + outbound, GB)"""
        from datetime import datetime
        now = datetime.utcnow()
        month_start = datetime(now.year, now.month, 1)
        start_ts = int(month_start.timestamp())
        end_ts = int(now.timestamp())

        base = f"monitoring/metrics/droplet/bandwidth?host_id={droplet_id}&interface=public"
        outbound_raw = self._request("GET", f"{base}&direction=outbound&start={start_ts}&end={end_ts}")
        inbound_raw = self._request("GET", f"{base}&direction=inbound&start={start_ts}&end={end_ts}")

        outbound_gb = self._calc_bandwidth_gb(outbound_raw)
        inbound_gb = self._calc_bandwidth_gb(inbound_raw)
        used_gb = outbound_gb + inbound_gb

        # Limit: droplet size dan olish
        limit_gb = 0.0
        droplet = self.get_droplet(droplet_id)
        if droplet:
            transfer_tb = droplet.get('size', {}).get('transfer', 0) or 0
            limit_gb = float(transfer_tb) * 1024.0

        percent = round(used_gb / limit_gb * 100, 1) if limit_gb > 0 else 0.0

        return {
            'used_gb': round(used_gb, 2),
            'outbound_gb': round(outbound_gb, 2),
            'inbound_gb': round(inbound_gb, 2),
            'limit_gb': round(limit_gb, 0),
            'percent': min(percent, 100.0),
        }
