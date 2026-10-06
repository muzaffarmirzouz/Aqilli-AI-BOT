# Namoz vaqtlari boti

Hamma foydalanishi mumkin bo'lgan Telegram bot:
- 🕌 **Namoz vaqtlari** — bugun / ertaga, keyingi namozgacha qolgan vaqt, rasm ko'rinishida
- 🌤 **Ob-havo** — hozirgi va ertangi (Open-Meteo)
- 💵 **Valyuta kursi** — Markaziy bank (USD, EUR, RUB), ertangi kurs e'lon qilingan bo'lsa u ham
- 🔔 **Har kuni 21:00** da ertangi namoz vaqtlari + ob-havo + dollar kursi
- 📢 **Kanal/guruhga admin qilinsa** — har kuni 21:00 da ertangi kun posteri (rasm) avtomatik chiqadi.
  Rasmda kanal nomi va @username yoziladi. Kanal egasi botda hududni tanlaydi.

## Ma'lumot manbalari
| Ma'lumot | Manba |
|---|---|
| Namoz vaqtlari | islomapi.uz (islom.uz — O'zbekiston musulmonlari idorasi taqvimi). Oylik taqvim bazaga saqlanadi |
| Ob-havo | api.open-meteo.com (kalit kerak emas) |
| Valyuta | cbu.uz (Markaziy bank rasmiy kursi) |

### Xatoga yo'l qo'ymaslik uchun himoya
1. Har bir kunning 6 vaqti tekshiriladi: format to'g'ri va ketma-ket o'sib borishi shart. Aks holda qabul qilinmaydi.
2. **20:30 da** (yuborishdan 30 daqiqa oldin) ertangi vaqtlar tekshiriladi. Biror hudud topilmasa, adminlarga xabar keladi.
3. Vaqt topilmagan hudud kanallariga **rasm umuman chiqmaydi** (noto'g'ri rasm chiqmasligi uchun).
4. Admin istalgan kunni qo'lda kiritishi mumkin — qo'lda kiritilgan vaqt avtomatikdan ustun turadi:
   `/vaqt namangan 2026-10-07 04:58 06:16 12:35 16:02 17:50 19:04`

## Admin buyruqlari (`/admin`)
- `/stat` — statistika
- `/tekshir namangan 2026-10-07` — vaqtlar va manbasi (islomapi yoki manual)
- `/vaqt ...` — vaqtni qo'lda kiritish/tuzatish
- `/sinov` — kechki xabar va rasmni faqat o'zingizga yuboradi
- `/hozir_yubor` — kechki yuborishni hozir hammaga ishga tushiradi
- `/xabar` — biror xabarga reply qilib yozing → hamma foydalanuvchiga nusxa ketadi

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
| `TAKBIR` | `40,0,10,10,10,10` | Bomdod, quyosh, peshin, asr, shom, xufton. `0` — ko'rsatilmaydi |
| `AYAH_TEXT`, `AYAH_SOURCE` | Niso, 103 | Posterdagi oyat |

## Lokal ishga tushirish
```bash
pip install -r requirements.txt
BOT_TOKEN=... ADMIN_IDS=... python main.py
```
