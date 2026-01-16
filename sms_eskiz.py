# -*- coding: utf-8 -*-
"""
ESKIZ.UZ SMS xizmati
O'zbekiston uchun arzon va oson SMS API integratsiyasi
"""
import requests
import logging
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class EskizSMS:
    """ESKIZ.UZ SMS xizmati"""
    
    def __init__(self):
        self.base_url = "https://notify.eskiz.uz/api"
        self.email = os.getenv('ESKIZ_EMAIL')
        self.password = os.getenv('ESKIZ_PASSWORD')
        self.token = None
        self.token_expires = None
        
        if not self.email or not self.password:
            logger.warning("⚠️ ESKIZ email/parol .env faylida sozlanmagan!")
    
    def get_token(self):
        """Token olish (30 kun amal qiladi)"""
        # Agar token hali amal qilsa, qaytarish
        if self.token and self.token_expires and datetime.now() < self.token_expires:
            return self.token
        
        try:
            url = f"{self.base_url}/auth/login"
            data = {
                "email": self.email,
                "password": self.password
            }
            
            logger.info("🔑 ESKIZ token olinmoqda...")
            response = requests.post(url, data=data, timeout=10)
            result = response.json()
            
            if result.get('message') == 'token_generated':
                self.token = result['data']['token']
                # Token 30 kun amal qiladi, 29 kun saqlaymiz
                self.token_expires = datetime.now() + timedelta(days=29)
                logger.info("✅ ESKIZ token muvaffaqiyatli olindi")
                return self.token
            else:
                logger.error(f"❌ Token olishda xatolik: {result}")
                return None
                
        except Exception as e:
            logger.error(f"❌ Token olishda xatolik: {str(e)}")
            return None
    
    def format_phone(self, phone):
        """
        Telefon raqamini formatlash (998901234567 formatiga)
        
        Args:
            phone: Turli formatdagi telefon (998901234567, +998901234567, 901234567)
            
        Returns:
            str: Formatlangan telefon (998901234567) yoki None
        """
        if not phone:
            return None
        
        # Faqat raqamlarni qoldirish
        phone = ''.join(filter(str.isdigit, phone))
        
        # 998 qo'shish (agar yo'q bo'lsa)
        if not phone.startswith('998'):
            if len(phone) == 9:  # 901234567
                phone = '998' + phone
            else:
                logger.warning(f"⚠️ Noto'g'ri telefon format: {phone}")
                return None
        
        # Uzunlik tekshirish (998 + 9 raqam = 12)
        if len(phone) != 12:
            logger.warning(f"⚠️ Telefon 12 raqam bo'lishi kerak: {phone}")
            return None
            
        return phone
    
    def send_sms(self, phone, message, from_name="4546"):
        """
        SMS yuborish
        
        Args:
            phone: Telefon raqam (998901234567)
            message: SMS matni (160 belgigacha)
            from_name: Jo'natuvchi nomi (default: 4546 - bepul)
            
        Returns:
            dict: {success: bool, message_id: str, phone: str} yoki error
        """
        try:
            # Token olish
            token = self.get_token()
            if not token:
                return {'success': False, 'error': 'Token olishda xatolik'}
            
            # Telefon formatlash
            formatted_phone = self.format_phone(phone)
            if not formatted_phone:
                return {'success': False, 'error': 'Telefon raqam noto\'g\'ri formatda'}
            
            # SMS yuborish
            url = f"{self.base_url}/message/sms/send"
            headers = {"Authorization": f"Bearer {token}"}
            data = {
                "mobile_phone": formatted_phone,
                "message": message,
                "from": from_name,  # 4546 (default) yoki alfa-nom
                "callback_url": ""  # Webhook URL (ixtiyoriy)
            }
            
            logger.info(f"📱 SMS yuborilmoqda: {formatted_phone}")
            
            response = requests.post(url, headers=headers, data=data, timeout=15)
            result = response.json()
            
            if result.get('status') == 'success':
                logger.info(f"✅ SMS muvaffaqiyatli yuborildi: {formatted_phone}")
                return {
                    'success': True,
                    'message_id': result.get('id'),
                    'phone': formatted_phone,
                    'status': result.get('message', 'sent')
                }
            else:
                error = result.get('message', 'Noma\'lum xatolik')
                logger.error(f"❌ SMS yuborishda xatolik: {error}")
                return {'success': False, 'error': error}
                
        except requests.Timeout:
            logger.error("⏱️ SMS API timeout")
            return {'success': False, 'error': 'Timeout - server javob bermadi'}
        except Exception as e:
            logger.error(f"❌ SMS yuborishda xatolik: {str(e)}")
            return {'success': False, 'error': str(e)}
    
    def get_balance(self):
        """
        SMS balansni tekshirish
        
        Returns:
            dict: {limit: int, is_limit: bool} yoki None
        """
        try:
            token = self.get_token()
            if not token:
                return None
            
            url = f"{self.base_url}/user/get-limit"
            headers = {"Authorization": f"Bearer {token}"}
            
            response = requests.get(url, headers=headers, timeout=10)
            result = response.json()
            
            if result.get('status') == 'success':
                data = result['data']
                logger.info(f"💰 SMS Balans: {data}")
                return data
            return None
            
        except Exception as e:
            logger.error(f"❌ Balans tekshirishda xatolik: {str(e)}")
            return None
    
    def send_debt_reminder(self, phone, customer_name, debt_usd, rate=13000, location_name=None):
        """
        Qarz eslatmasi SMS yuborish
        
        Args:
            phone: Mijoz telefoni
            customer_name: Mijoz ismi
            debt_usd: Qarz miqdori (USD)
            rate: Valyuta kursi (UZS)
            location_name: Joylashuv nomi (do'kon/ombor)
            
        Returns:
            dict: SMS yuborish natijasi
        """
        debt_uzs = float(debt_usd) * rate
        
        if location_name:
            message = (
                f"Hurmatli {customer_name}\n"
                f"{location_name} dan qarzingiz\n"
                f"${debt_usd:.2f} ({debt_uzs:,.0f} so'm)\n"
                f"Iltimos tolovni amalga oshiring"
            )
        else:
            message = (
                f"Hurmatli {customer_name}\n"
                f"Qarzingiz\n"
                f"${debt_usd:.2f} ({debt_uzs:,.0f} so'm)\n"
                f"Iltimos tolovni amalga oshiring"
            )
        
        return self.send_sms(phone, message)
    
    def send_payment_confirmation(self, phone, customer_name, paid_usd, remaining_usd, rate=13000):
        """
        To'lov tasdiqlanishi SMS yuborish
        
        Args:
            phone: Mijoz telefoni
            customer_name: Mijoz ismi
            paid_usd: To'langan summa (USD)
            remaining_usd: Qolgan qarz (USD)
            rate: Valyuta kursi (UZS)
            
        Returns:
            dict: SMS yuborish natijasi
        """
        paid_uzs = float(paid_usd) * rate
        
        if remaining_usd > 0:
            remaining_uzs = float(remaining_usd) * rate
            message = (
                f"Hurmatli {customer_name}!\n"
                f"To'lovingiz qabul qilindi:\n"
                f"${paid_usd:.2f} ({paid_uzs:,.0f} so'm)\n"
                f"Qolgan qarz: ${remaining_usd:.2f}\n"
                f"Rahmat!"
            )
        else:
            message = (
                f"Hurmatli {customer_name}!\n"
                f"To'lovingiz qabul qilindi:\n"
                f"${paid_usd:.2f} ({paid_uzs:,.0f} so'm)\n"
                f"✅ Qarzingiz to'liq to'landi!\n"
                f"Hamkorlikdan minnatdormiz!"
            )
        
        return self.send_sms(phone, message)
    
    def send_bulk_sms(self, phone_list, message):
        """
        Ko'plab SMS yuborish (bulk)
        
        Args:
            phone_list: Telefon raqamlar ro'yxati
            message: SMS matni
            
        Returns:
            dict: {sent: int, failed: int, errors: list}
        """
        sent_count = 0
        failed_count = 0
        errors = []
        
        for phone in phone_list:
            result = self.send_sms(phone, message)
            
            if result.get('success'):
                sent_count += 1
            else:
                failed_count += 1
                errors.append({
                    'phone': phone,
                    'error': result.get('error')
                })
            
            # API rate limiting uchun kichik kechikish
            import time
            time.sleep(0.5)
        
        return {
            'success': True,
            'sent': sent_count,
            'failed': failed_count,
            'errors': errors
        }


# Global instance yaratish
eskiz_sms = EskizSMS()
