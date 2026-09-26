# USD va UZS narxlari

## Hisoblash qoidalari

- Mahsulot tan narxi va sotuv narxi uchun USD yoki UZS alohida tanlanadi.
- `native_*` maydonlar asl summalardir. Eski `cost_price`, `sell_price`,
  `unit_price` va `debt_usd` maydonlari moslik uchun USD ekvivalentlari bo'lib qoladi.
- UZS sotuv narxi kurs o'zgarganda o'zgarmaydi. Yangi savdodagi USD ekvivalenti
  yangi kurs bilan hisoblanadi. USD sotuv narxi uchun teskari qoida ishlaydi.
- Kirim tarixi va savdo qatorlari asl summa, valyuta va operatsiya kursini saqlaydi.
  Eski kirim tannarxi yangi kurs bilan qayta baholanmaydi.
- Mijoz qarzi `native_debt_usd` va `native_debt_uzs` da mustaqil saqlanadi.
  Bu ikki maydonni ikki marta konvertatsiya qilib qo'shmang.
- Qisman to'lovda avval yopiladigan qarz valyutasi tanlanadi; shu valyuta ichida
  eski savdolar birinchi yopiladi. Naqd, Click, Terminal uchun to'lov valyutasi
  alohida tanlanadi. To'lovning boshqa maydoni uning ekvivalentidir.
- Qarz to'lovi o'z kursi bilan saqlanadi. `DebtPayment.native_allocation` haqiqiy
  to'langan summalar, yopilgan USD/UZS qarz va tarixiy USD taqsimotini saqlaydi.
- Qaytarishda avval qaytarilgan mahsulot valyutasidagi shu savdo qarzi kamayadi.
  Qolgan to'langan qism naqd qaytariladi yoki USD ekvivalentida balansga o'tadi.
  USD/UZS savdoda "qarzdan" qaytarishdan ortgan summa balansga o'tadi.
- Qarz to'lovlari mavjud savdoni qayta yaratib tahrirlash bloklangan. Avval
  tegishli qarz to'lovlarini bekor qilish kerak. To'lov bekor qilinsa, aynan
  o'sha to'lov yopgan asl valyutadagi qarz tiklanadi.

## Eski ma'lumotlar va integratsiyalar

Eski yozuvlarda yangi summalar `NULL` qoladi va USD sifatida talqin qilinadi.
Eski savdolarni bugungi kurs bilan to'ldirish yoki qayta hisoblash kerak emas.
Yetkazib beruvchi qarzi, mijoz balansi va umumiy moliyaviy hisobotlar USD hisob
asosidan foydalanadi. Ular asl USD qarzi emas, hisob ekvivalenti hisoblanadi.
Telegram savdo/to'lov xabarlari, mijoz va admin qarz tugmalari hamda avtomatik
eslatmalar asl USD/UZS qarzlarini ko'rsatadi. PDF cheklarida asl mahsulot narxi,
haqiqiy to'lov valyutasi va asl qarz tarkibi bor; jami konvertatsiya qilingan
summa ekvivalent deb belgilanadi. Kunlik Excel eksportida asl narx/valyuta,
alohida USD/UZS qarz va tarixiy kurs ustunlari mavjud.

## Bazaga qo'llash

1. PostgreSQL bazasining zaxira nusxasini oling va tiklash mumkinligini tekshiring.
2. Avval staging bazada `migrations/add_native_currencies.sql` ni bajaring:
   `psql -v ON_ERROR_STOP=1 -d xurshid_db -f migrations/add_native_currencies.sql`.
3. Migratsiya muvaffaqiyatli tugagandan keyingina yangi ilova kodini ishga tushiring.
   `db.create_all()` mavjud jadvallarga bu ustunlarni qo'shmaydi.
4. Kirim, aralash savdo, qarz to'lovi, bekor qilish, qaytarish va hisobotlarni tekshiring.

Migratsiya bitta transaction ichida ishlaydi va qayta bajarishga mos yozilgan.
Eski narxlar va qarz summalarini yangilamaydi. Lock olish 10 sekund, statement
bajarilishi 5 minut bilan cheklangan. Xato bo'lsa transaction bekor bo'ladi.
Oddiy push-deploy skripti bu migratsiyani bajarmaydi; avval migratsiya, keyin
yangi CRM va Telegram bot versiyasi ishga tushirilishi kerak.

## Mahalliy tekshiruv

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_currency_accounting.py
.\.venv\Scripts\python.exe tests/check_currency_workflow.py
.\.venv\Scripts\python.exe tests/check_currency_workflow.py --browser
.\.venv\Scripts\python.exe tests/check_currency_postgres.py
```

Brauzer sinovi uchun Playwright va Chromium kerak. Workflow skripti vaqtinchalik
SQLite bazasi va test administratori bilan ishlaydi, haqiqiy bazaga ulanmaydi.
Telegram transporti mock qilinadi: testlar haqiqiy mijozlarga xabar yubormaydi.
PostgreSQL sinovi alohida vaqtinchalik localhost klasterini yaratadi, migratsiyani
ikki marta bajaradi, eski summalar o'zgarmaganini tekshiradi va real API/Excel
oqimini sinaydi. Windows uchun PostgreSQL 17 bin katalogi standart; boshqa
joylashuv uchun `--bin` bering. Sinov oxirida klaster to'xtatilib o'chiriladi.
Autentifikatsiya va haqiqiy Telegram tarmoq yetkazilishi bu testlarda sinovdan
o'tmaydi. Jonli bazaga o'tishda zaxirani alohida bazaga tiklab tekshirish zarur.

`--serve --port 5057` faqat localhostda test ma'lumotli ko'rinishni ishga tushiradi.
Bu test serverni ommaviy tarmoqqa ochmang va production uchun ishlatmang.