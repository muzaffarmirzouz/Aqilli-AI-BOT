# Namoz vaqtlari boti

Hamma foydalanishi mumkin bo'lgan Telegram bot:
- 🕌 **Namoz vaqtlari** — bugun / ertaga, keyingi namozgacha qolgan vaqt, rasm ko'rinishida
- 🌤 **Ob-havo** — hozirgi va ertangi (Open-Meteo)
- 💵 **Valyuta kursi** — Markaziy bank (USD, EUR, RUB), ertangi kurs e'lon qilingan bo'lsa u ham
- 🔔 **Har kuni 21:00** da ertangi kun: 3 ta rasm (albom) — namoz vaqtlari, ob-havo, valyuta kursi
- 🌤 / 💵 tugmalari ham Namanganliklar.uz logoli rasm bilan javob beradi
- 📢 **Kanal/guruhga admin qilinsa** — har kuni 21:00 da namoz vaqtlari rasmi chiqadi. Ob-havo va dollar kursi rasmlari faqat kanal panelida yoqilsa qo'shiladi (standart: o'chiq).
  Rasmda har doim Namanganliklar.uz nomi va logosi bo'ladi (kanal nomi yozilmaydi).

## 📰 Yangilik rasmi (faqat adminlar)
Botga rasm yuboring, izohiga sarlavha yozing — Namanganliklar.uz shablonidagi tayyor rasm qaytadi, izohida sarlavha qalin (jirniy) yozuvda.
- Urg'u (qizil): `*so'z*` · Teg (qizil yorliq): `[Tezkor] Sarlavha` · Kirill va lotin ikkalasi ham ishlaydi
- Rasm ostidagi tugmalar: uslub (To'liq rasm / Panel), format (1:1 / 4:5), matnni o'zgartirish
- Oxirgi tanlangan uslub va format eslab qolinadi
- 📤 **Kanalga yuborish** — @Namanganliklar_uz yoki bot ulangan istalgan kanalga (yoki hammasiga) forward belgisisiz, toza post qilib yuboradi; tasdiqlashdan keyin yuboriladi, post havolasi qaytadi
  (bot o'sha kanalda «Xabar joylash» huquqiga ega admin bo'lishi kerak)

## Majburiy obuna
Bot faqat **@Namanganliklar_uz** kanaliga obuna bo'lganlar uchun ishlaydi (adminlar tekshirilmaydi).
- ⚠️ Bot **@Namanganliklar_uz kanalida admin** bo'lishi shart — aks holda obunani tekshira olmaydi
  (bu holda bot hammaga ochiq ishlaydi va adminlarga ogohlantirish keladi).
- 21:00 dagi xabar ham faqat obunachilarga boradi.
- Kanalni o'zgartirish: `REQUIRED_CHANNEL=@boshqa_kanal`; o'chirish: `REQUIRED_CHANNEL=` (bo'sh).

## Kanal rasmini sozlash
Botda «📢 Kanalimga ulash» → kanal nomi (⚙️) bosiladi:
- 🎨 **Rasm rangi** — 6 xil: Zumrad, Tungi ko'k, Bordo, Qahva, Qora, Oq-oltin
- 📣 **Reklama matni** — reklama joyiga yoziladigan matn (bo'sh bo'lsa «Reklamangiz uchun joy»)
- 📞 **Bog'lanish** — reklama ostidagi kontakt (standart: kanal @username; `-` — ko'rsatmaslik)
- 🖼 **Reklama rasmi** — reklama joyiga to'liq rasm (gorizontal, ~4:1)
- 🌤 **Ob-havo rasmi** / 💵 **Kurs rasmi** — yoqish/o'chirish (standart: o'chiq)
- 🗑 **Reklamani tozalash**, 👁 **Ko'rinishni ko'rish**, 📤 **Hozir kanalga sinov post**
Har o'zgarishdan keyin bot yangi ko'rinishni sizga yuboradi.

## Ma'lumot manbalari
| Ma'lumot | Manba |
|---|---|
| Namoz vaqtlari | **Faqat Namangan, oylik jadvaldan** (islom.uz). `prayer_data/namangan_2026-10.csv` — oktyabr 2026. Internetdan olinmaydi |
| Ob-havo | api.open-meteo.com (kalit kerak emas) |
| Valyuta | cbu.uz (Markaziy bank rasmiy kursi) |

## Har oy jadvalni yangilash
- Jadval tugashidan **5 kun oldin** har kuni 20:30 da adminlarga «Namoz vaqtlarini yangilang!» xabari keladi.
- Ertangi kun vaqti umuman bo'lmasa — 🚨 ogohlantirish keladi va kanallarga rasm **chiqmaydi** (noto'g'ri rasm chiqmasligi uchun).
- Yangi oyni botning o'ziga yuborasiz (qayta deploy kerak emas):
  ```
  /oylik 2026-11
  1 bomdod quyosh peshin asr shom xufton
  2 ...
  ```
  Har qatorda: kun, bomdod, quyosh, peshin, asr, shom, xufton. islom.uz jadvalidan to'g'ridan-to'g'ri
  nusxa olsangiz ham bo'ladi — hafta kuni, ishroq va tahajjud ustunlari o'zi tashlab yuboriladi.
  Bot har qatorni tekshiradi va noto'g'ri qatorlarni ko'rsatadi.
  Peshin ustunida nima bo'lishidan qat'i nazar, peshin har doim **12:35** (takbir +10) chiqadi.
- Bitta kunni tuzatish: `/vaqt namangan 2026-10-07 04:58 06:16 12:35 16:01 17:50 19:04`
- Muqobil: `prayer_data/namangan_YYYY-MM.csv` fayl qo'shib deploy qilish.

## Railway'ga joylash
1. Fayllarni yangi GitHub repoga yuklang.
2. Railway → New Project → Deploy from GitHub repo. `Dockerfile` avtomatik ishlatiladi (shriftlar ham shu yerda yuklanadi).
3. **Variables:**
   - `BOT_TOKEN` — @BotFather dan
   - `ADMIN_IDS` — `123456789,987654321`
   - `DB_PATH` — `/app/data/bot.db`
4. **Volume** qo'shing: Mount path `/app/data` (aks holda har deployda foydalanuvchilar o'chib ketadi).
5. Deploy. Botga `/start` bosing, keyin `/sinov` bilan tekshiring.

### Ixtiyoriy sozlamalar
| O'zgaruvchi | Standart | Izoh |
|---|---|---|
| `SEND_AT` | `21:00` | Kechki yuborish vaqti (Toshkent) |
| `PRECHECK_MIN` | `30` | Necha daqiqa oldin tekshirish |
| `REMIND_DAYS` | `5` | Jadval tugashidan necha kun oldin eslatish |
| `PESHIN_FIXED` | `12:35` | Peshin har kuni shu vaqtda (jadvaldagi peshin o'rniga). Bo'sh qoldirilsa — jadvaldagi vaqt |
| `TAKBIR` | `40,0,10,10,0,10` | Bomdod, quyosh, peshin, asr, shom, xufton. `0` — ko'rsatilmaydi |

## Lokal ishga tushirish
```bash
pip install -r requirements.txt
BOT_TOKEN=... ADMIN_IDS=... python main.py
```
