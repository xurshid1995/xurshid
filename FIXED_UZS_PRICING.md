# USD va qat'iy UZS mahsulot narxlari

## Narx qoidalari

- Mahsulot kirimida tan narx va sotish narxining valyutasi alohida tanlanadi.
- USD narxlar avvalgidek ishlaydi. Eski yozuvlarda yangi ustunlar NULL qoladi va USD sifatida talqin qilinadi.
- UZS sotish narxi qat'iy: 6000 UZS kurs 12000 bo'lsa 0.50 USD, kurs 12500 bo'lsa 0.48 USD.
- Kirimda asl narxlar va o'sha kirim kursi ProductAddHistory ichida saqlanadi.
- Product.cost_price USD hisob tannarxi bo'lib qoladi. Yangi kirim uning kursida USDga aylantiriladi va qoldiq bilan og'irlikli o'rtachalanadi. Keyingi kurs o'zgarishi eski tannarxni qayta hisoblamaydi.
- Product.cost_price_original oxirgi kirimning asl birlik narxi; u omborning o'rtacha UZS tannarxi emas.
- Savdo va pending yozuvlarida asl narx, narx valyutasi, USD va UZS summalari saqlanadi; kurs Sale.currency_rate ichida.
- UZS qatnashgan savat yakunlanishidan oldin faol kurs qayta olinadi. Kurs to'lov vaqtida yana o'zgarsa, server tasdiqlashni rad etadi; to'lov oynasini qayta ochish kerak.
- Yakunlangan savdo va foyda joriy kurs almashganda qayta hisoblanmaydi.
- Mijoz va yetkazib beruvchi qarzlari, to'lovlar va hisobotlar mavjud USD hisobida qoladi. Bu o'zgarish qarzning asl valyutada yuritilishini qo'shmaydi.
- Eski dollar-only stock tahrirlash oynalarida UZS narxini almashtirish bloklanadi; boshqa maydonlar saqlangan narxlar bilan tahrirlanishi mumkin. Yangi UZS kirimlar /add_product_session orqali kiritiladi. Eski /api/products POST yangi UZS narxlar uchun ishlatilmaydi.

## Yetkazib beruvchidan kelgan mahsulotlar

- SupplierPurchase ham kirimning asl tan narxi, valyutasi va kursini alohida saqlaydi. Sahifada asl valyuta asosiy, ikkinchisi kirim kursidagi ekvivalent sifatida ko'rsatiladi.
- Masalan, 5000 UZS x 10 = 50000 UZS; to'rt xonagacha yaxlitlangan USD birlik narxi orqali qayta hisoblanmaydi. Guruhning USD va UZS jamisi alohida yig'iladi.
- Kurs yoki mahsulotning hozirgi narxi o'zgarsa, kirim summalari o'zgarmaydi. Eski USD hisob, qarz va to'lov ustunlari qayta baholanmaydi.
- `migrations/add_supplier_receipt_price_snapshots.sql` qo'shimcha ustunlarni yaratadi va ProductAddHistory bilan narx, miqdor, joy, foydalanuvchi hamda ikki soniya ichidagi vaqt bo'yicha birma-bir mos yozuvlarni tiklaydi. Noaniq yoki asl narxi yo'q eski yozuvlarga tegmaydi. Takroran bajarish mumkin.
- Asl ma'lumoti yo'q eski kirimlarda saqlangan USD jami va joriy kursdagi UZS ekvivalenti ishlatiladi; joriy Product narxidan tarix to'qilmaydi.

## Migratsiya

`migrations/add_fixed_uzs_product_prices.sql` tranzaksiya ichida qo'shimcha NULL ustunlarni yaratadi. Eski pul maydonlarini UPDATE qilmaydi. Takroran qo'llash mumkin.

Oldingi tajribaviy native-currency ustunlari mavjud bo'lsa ham ular qayta ishlatilmaydi: ushbu modulning ustun nomlari alohida. U yerdagi eski ma'lumotlar avtomatik ko'chirilmaydi.

Serverga chiqarishda:

1. Kod versiyasini va PostgreSQL bazasini zaxiralash.
2. Migratsiyani ilova yangi kod bilan ishga tushishidan oldin qo'llash.
3. Faqat fast-forward orqali kodni yangilash; serverdagi untracked fayllarni saqlash.
4. Model import qiladigan Flask va Telegram jarayonlarini qayta ishga tushirish.
5. Xizmatlar holati, HTTP javoblari va yangi ustunlarni tekshirish.

UZS yozuvlar yaratilgach, faqat kodni eski versiyaga qaytarish to'g'ri emas: eski kod qat'iy UZS narxlarni tushunmaydi. Qaytarishda kod va ma'lumotlar birgalikda muvofiqlashtirilishi kerak.

## Testlar

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p 'test_product*.py' -v
```

Brauzer sinovi uchun `RUN_BROWSER=1` muhit o'zgaruvchisi va Playwright Chromium kerak. Testlar odatda faqat vaqtinchalik SQLite bazasida ishlaydi.

PostgreSQL sinovi `PRICING_TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:<port>/pricing_test` orqali ishlaydi. Bu baza faqat testga tegishli bo'lishi shart: testlar jadvallarini yaratadi va o'chiradi. Boshqa host yoki baza nomi rad etiladi. PostgreSQL testi migratsiyani ikki marta qo'llaydi, eski qiymatlar saqlanishi va takroriy kirimning o'rtacha tannarxini tekshiradi.