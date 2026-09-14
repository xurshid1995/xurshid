# -*- coding: utf-8 -*-
"""
Ovozli xabar orqali xarajat yozish - matnga aylantirish va parsing
Telegram/Flask'dan mustaqil, alohida test qilinadigan sof funksiyalar.
"""
import os
import re
import logging
from typing import Optional, List, Tuple, Dict

logger = logging.getLogger(__name__)

# So'z bilan aytilgan sonlarni raqamga aylantirish uchun lug'atlar
_UNITS = {
    'nol': 0, 'bir': 1, 'ikki': 2, 'uch': 3, "to'rt": 4, 'tort': 4, 'to`rt': 4,
    'besh': 5, 'olti': 6, 'yetti': 7, 'sakkiz': 8,
    "to'qqiz": 9, 'toqqiz': 9, "to`qqiz": 9,
}
_TENS = {
    "o'n": 10, 'on': 10, "o`n": 10, 'yigirma': 20, "o'ttiz": 30, 'ottiz': 30,
    "o`ttiz": 30, 'qirq': 40, 'ellik': 50, 'oltmish': 60, 'yetmish': 70,
    'sakson': 80, "to'qson": 90, 'toqson': 90, "to`qson": 90,
}
_NUMBER_WORDS = {**_UNITS, **_TENS}
_SCALE_WORDS = {'yuz': 100, 'ming': 1000, 'million': 1_000_000, 'milliard': 1_000_000_000}

_CURRENCY_WORDS = r"so['`]?m|sum|soum"


def _normalize_text(text: str) -> str:
    """Apostrof va katta-kichik harflarni bir xillashtirish"""
    text = text.lower().strip()
    text = text.replace('\u2018', "'").replace('\u2019', "'").replace('`', "'")
    return text


def _words_to_number(tokens: List[str]) -> Optional[int]:
    """So'z bilan yozilgan sonni butun songa aylantirish (masalan ['qirq', 'ming'] -> 40000)"""
    total = 0
    group = 0
    found = False
    for tok in tokens:
        tok = tok.strip(".,")
        if tok in _NUMBER_WORDS:
            group += _NUMBER_WORDS[tok]
            found = True
        elif tok == 'yuz':
            group = (group or 1) * 100
            found = True
        elif tok in ('ming', 'million', 'milliard'):
            scale = _SCALE_WORDS[tok]
            group = (group or 1) * scale
            total += group
            group = 0
            found = True
    total += group
    return total if found else None


def extract_amount_uzs(text: str) -> Optional[int]:
    """
    Matndan summani (so'mda) ajratib olish.
    Qo'llab-quvvatlanadi: "40 000 sum", "40000 so'm", "qirq ming so'm", "40 ming so'm"
    """
    text = _normalize_text(text)

    # 1) Raqam + (ming/million) + valyuta so'zi ("40 ming so'm", "40000 so'm")
    digit_pattern = rf"(\d[\d\s]*\d|\d)\s*(ming|million|milliard)?\s*(?:{_CURRENCY_WORDS})"
    match = re.search(digit_pattern, text)
    if match:
        digits = re.sub(r"\s+", "", match.group(1))
        try:
            amount = int(digits)
        except ValueError:
            amount = None
        if amount is not None:
            scale_word = match.group(2)
            if scale_word:
                amount *= _SCALE_WORDS[scale_word]
            return amount

    # 2) So'z bilan yozilgan son + valyuta so'zi ("qirq ming so'm")
    word_pattern = rf"([a-z'\s]+?)\s+(?:{_CURRENCY_WORDS})"
    match = re.search(word_pattern, text)
    if match:
        phrase = match.group(1).strip()
        tokens = phrase.split()
        # oxirgi 6 ta so'zni olish (haddan tashqari uzun jumlani chetlab o'tish uchun)
        candidate_tokens = tokens[-6:]
        # faqat son so'zlaridan iborat "quyruq"ni ajratish
        number_tokens = []
        for tok in reversed(candidate_tokens):
            if tok in _NUMBER_WORDS or tok in _SCALE_WORDS:
                number_tokens.insert(0, tok)
            else:
                break
        amount = _words_to_number(number_tokens)
        if amount:
            return amount

    # 3) Valyuta so'zi topilmasa - "x/y/z" ko'rinishidagi joylashuv kodlarini olib tashlab,
    #    qolgan eng katta raqam guruhini summani deb hisoblash (fallback)
    cleaned = re.sub(r"\d+(?:/\d+)+", " ", text)
    digit_groups = re.findall(r"\d[\d\s]*\d|\d", cleaned)
    if digit_groups:
        numbers = [int(re.sub(r"\s+", "", g)) for g in digit_groups]
        return max(numbers)

    return None


def find_matching_store(
    text: str, locations: List[Tuple[int, str, str]], threshold: float = 55.0
) -> List[Dict]:
    """
    Matnda tilga olingan do'kon/ombor nomini locations ro'yxati bilan taqqoslash (fuzzy match).
    locations: [(id, name, location_type), ...] - location_type: 'store' yoki 'warehouse'
    Qaytaradi: eng mos nomzodlar ro'yxati (score bo'yicha kamayish tartibida), har biri
    {'id', 'name', 'type', 'score'}
    """
    if not locations:
        return []

    from rapidfuzz import fuzz

    text_norm = _normalize_text(text)
    scored = []
    for loc_id, name, loc_type in locations:
        name_norm = _normalize_text(name)
        score = fuzz.partial_ratio(name_norm, text_norm)
        scored.append({'id': loc_id, 'name': name, 'type': loc_type, 'score': score})

    scored.sort(key=lambda s: s['score'], reverse=True)
    return [s for s in scored if s['score'] >= threshold]


def find_matching_category(text: str, known_categories: List[str], threshold: float = 65.0) -> Optional[str]:
    """Matndan mavjud xarajat kategoriyalaridan biriga mos kelganini topish (fuzzy match)"""
    if not known_categories:
        return None

    from rapidfuzz import fuzz

    text_norm = _normalize_text(text)
    best_cat = None
    best_score = 0
    for cat in known_categories:
        score = fuzz.partial_ratio(_normalize_text(cat), text_norm)
        if score > best_score:
            best_score = score
            best_cat = cat

    return best_cat if best_score >= threshold else None


def extract_category_phrase(text: str) -> Optional[str]:
    """
    Kategoriya mos kelmasa, "... uchun" iborasidan oldingi so'z(lar)ni kategoriya sifatida olish
    Masalan: "ovqatlanish uchun 40000 sum" -> "Ovqatlanish"
    """
    text = _normalize_text(text)
    match = re.search(r"([a-z'\s]{2,40}?)\s+uchun\b", text)
    if match:
        phrase = match.group(1).strip()
        # oxirgi 3 tadan ortiq bo'lmagan so'zni olish (juda uzun jumla bo'lib ketmasligi uchun)
        words = phrase.split()[-3:]
        phrase = " ".join(words)
        return phrase.capitalize() if phrase else None
    return None


def transcribe_voice_google(audio_bytes: bytes, sample_rate_hertz: int = 48000) -> Optional[str]:
    """
    Telegram OGG/OPUS ovozli xabarni Google Cloud Speech-to-Text orqali matnga aylantirish.
    GOOGLE_APPLICATION_CREDENTIALS environment o'zgaruvchisi (service account JSON fayl yo'li)
    sozlangan bo'lishi kerak.
    """
    try:
        from google.cloud import speech
    except ImportError:
        logger.error("❌ google-cloud-speech kutubxonasi o'rnatilmagan (pip install google-cloud-speech)")
        return None

    if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        logger.error("❌ GOOGLE_APPLICATION_CREDENTIALS sozlanmagan")
        return None

    try:
        client = speech.SpeechClient()
        audio = speech.RecognitionAudio(content=audio_bytes)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.OGG_OPUS,
            sample_rate_hertz=sample_rate_hertz,
            audio_channel_count=1,
            language_code="uz-UZ",
            alternative_language_codes=["ru-RU"],
            enable_automatic_punctuation=True,
        )
        response = client.recognize(config=config, audio=audio)
        if not response.results:
            return None

        text = " ".join(
            result.alternatives[0].transcript
            for result in response.results
            if result.alternatives
        )
        return text.strip() or None
    except Exception as e:
        logger.error(f"❌ Google Speech-to-Text xatolik: {e}")
        return None


# Gemini model nomlari tez-tez o'zgarib/eskirib turadi (404 yoki tor bepul kvota bilan 429),
# shu sabab bir nechta nomzod ketma-ket sinaladi - biri ishlamasa keyingisiga o'tiladi.
_GEMINI_MODEL_CANDIDATES = [
    "gemini-3.6-flash",
    "gemini-2.5-flash",
    "gemini-flash-latest",
    "gemini-flash-lite-latest",
]


def transcribe_voice_gemini(audio_bytes: bytes) -> Optional[str]:
    """
    Telegram OGG/OPUS ovozli xabarni Gemini API orqali matnga aylantirish.
    Faqat bitta GEMINI_API_KEY kerak (https://aistudio.google.com/apikey - bepul, kartasiz).
    """
    try:
        import google.generativeai as genai
    except ImportError:
        logger.error("❌ google-generativeai kutubxonasi o'rnatilmagan (pip install google-generativeai)")
        return None

    api_key = os.getenv('GEMINI_API_KEY')
    if not api_key:
        logger.error("❌ GEMINI_API_KEY sozlanmagan")
        return None

    genai.configure(api_key=api_key)
    prompt = (
        "Sen professional audio transkripsiya vositasisan. Ushbu ovozli xabarni ANIQ so'zma-so'z "
        "matnga aylantir. Til: o'zbekcha (lotin yozuvida), ba'zi so'zlar ruscha bo'lishi mumkin.\n"
        "QOIDALAR:\n"
        "- Faqat eshitilgan gapni yoz, hech qanday izoh, tarjima yoki tushuntirish qo'shma.\n"
        "- Barcha sonlarni (summalarni) albatta RAQAM bilan yoz, so'z bilan emas "
        "(masalan \"qirq ming\" emas, \"40000\" deb yoz).\n"
        "- Agar biror qism aniq eshitilmasa, eng yaqin ehtimoldagi so'zni yoz, lekin butunlay "
        "boshqa mavzudagi gap TO'QIMA.\n"
        "- Bu odatda do'kon/ombor nomi, xarajat sababi va summa haqidagi qisqa gap bo'ladi."
    )

    last_error = None
    for model_name in _GEMINI_MODEL_CANDIDATES:
        try:
            model = genai.GenerativeModel(model_name)
            response = model.generate_content(
                [{"mime_type": "audio/ogg", "data": audio_bytes}, prompt],
                # temperature=0 - "ijodiy" (hallucination) emas, imkon qadar so'zma-so'z natija
                generation_config={"temperature": 0},
                # Kvota/tarmoq xatosida uzoq (o'nlab soniyalik) avtomatik retry o'rniga tez xato qaytarish
                request_options={"timeout": 25},
            )
            text = (response.text or "").strip()
            if text:
                return text
        except Exception as e:
            last_error = e
            logger.warning(f"⚠️ Gemini model '{model_name}' ishlamadi, keyingisi sinaladi: {e}")
            continue

    logger.error(f"❌ Gemini transkripsiya xatolik (barcha modellar ishlamadi): {last_error}")
    return None



def transcribe_voice(audio_bytes: bytes) -> Optional[str]:
    """
    Mavjud sozlangan provayder orqali ovozni matnga aylantirish.
    Ustuvorlik: GOOGLE_APPLICATION_CREDENTIALS (Cloud Speech - maxsus ASR, aniqroq) ->
    GEMINI_API_KEY (umumiy LLM, sodda, lekin raqam/kategoriyada ko'proq adashadi).
    """
    if os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
        text = transcribe_voice_google(audio_bytes)
        if text:
            return text
    if os.getenv('GEMINI_API_KEY'):
        return transcribe_voice_gemini(audio_bytes)
    return None


def parse_voice_expense(
    text: str,
    locations: List[Tuple[int, str, str]],
    known_categories: Optional[List[str]] = None,
) -> Dict:
    """
    Tarjima qilingan matndan xarajat ma'lumotlarini ajratib olish.
    locations: [(id, name, location_type), ...] - do'kon va omborlar birgalikda

    Returns:
        {
            'raw_text': str,
            'amount_uzs': Optional[int],
            'store_candidates': List[{'id','name','type','score'}],
            'category': Optional[str],
        }
    """
    amount = extract_amount_uzs(text)
    store_candidates = find_matching_store(text, locations)
    category = find_matching_category(text, known_categories or [])
    if not category:
        category = extract_category_phrase(text)

    return {
        'raw_text': text,
        'amount_uzs': amount,
        'store_candidates': store_candidates,
        'category': category,
    }
