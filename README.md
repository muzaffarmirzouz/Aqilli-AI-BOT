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
- Yagona uslub — «To'liq rasm». Rasm ostidagi tugmalar: format (1:1 / 4:5), matnni o'zgartirish
- Oxirgi tanlangan format eslab qolinadi
- Izoh: qalin sarlavha → «БАТАФСИЛ ЎҚИШ 👉 havola» → «Бизнинг саҳифаларга обуна бўлинг» + Telegram | Instagram | YouTube havolalari.
  Havolalar: `SOCIAL_TELEGRAM` (standart https://t.me/Namanganliklar_uz), `SOCIAL_INSTAGRAM`, `SOCIAL_YOUTUBE`
- Rasm pastida: chapda sana va 1 USD kursi; o'ngda veb-sayt, Telegram, Instagram, Facebook, YouTube belgilari va Namanganliklar.uz
- 📤 **Kanalga yuborish** — @Namanganliklar_uz yoki bot ulangan istalgan kanalga (yoki hammasiga) forward belgisisiz, toza post qilib yuboradi; tasdiqlashdan keyin yuboriladi, post havolasi qaytadi
  (bot o'sha kanalda «Xabar joylash» huquqiga ega admin bo'lishi kerak)

## 📘 Facebook va 📸 Instagram'ga joylash
«📤 Kanalga yuborish» ro'yxatida Telegram kanallaridan keyin «📘 Facebook», «📸 Instagram» va «🌐 Hammasiga» chiqadi.
- Yuborgandan keyin tugmalar joyida qoladi: yuborilgan joylar ✅ bilan belgilanadi, qayta bosilsa «avval yuborilgan» deydi; «🌐 Qolganlariga» — hali yuborilmaganlariga.
- **Facebook:** rasm + sarlavha + «БАТАФСИЛ ЎҚИШ 👉 maqola havolasi»
- **Instagram:** rasm + sarlavha + maqolaning **to'liq matni** (saytdan avtomatik olinadi, «Теглар», «Мавзуга доир» va boshqa sayt bloklarisiz).
  Instagram izohi 2200 belgigacha — uzun matn qisqartiriladi.
- Qo'lda yuborilgan rasmda: izohning **birinchi qatori — sarlavha**, keyingi qatorlar — Instagram uchun to'liq matn.
- Sozlash (Railway): `META_PAGE_ID`, `META_PAGE_TOKEN` (sahifaning uzoq muddatli tokeni).
- Instagram: `META_IG_TOKEN` — Instagram'ning o'z tokeni (ilova paneli → Instagram API → «API setup with Instagram login»
  → Generate token). Bunda Instagram Facebook sahifaga ulanishi shart emas. Token 60 kun yashaydi,
  bot uni haftada bir o'zi yangilaydi. `META_IG_TOKEN` bo'lmasa — sahifaga ulangan Instagram ishlatiladi.
- Instagram asosiy sahifaga ulanmasa — boshqa (portfelsiz) sahifangizga ulang va `META_IG_PAGE_ID`, `META_IG_PAGE_TOKEN` qo'ying
  (token: Graph API Explorer → `me/accounts` → o'sha sahifaning access_token).
- Tekshirish: `/meta`; maqoladan qanday matn olinishini ko'rish: `/matn https://namanganliklar.uz/news/123`

### 🔑 Token muddatsiz bo'lishi uchun (tavsiya)
Railway'ga bir marta qo'shing: `META_APP_ID` va `META_APP_SECRET`
(developers.facebook.com → ilova → **Настройки приложения → Основное**: «ID приложения» va «Секрет приложения» → «Показать»).
Keyin token kerak bo'lganda: Graph API Explorer → **Generate Access Token** (Namanganliklar.Uz, Namangam, Instagram belgilangan)
→ tokenni nusxalab botga `/token EAAB...` yuboring. Bot uni o'zi uzaytiradi, muddatsiz sahifa tokenlarini oladi va saqlaydi —
Extend qilish ham, Railway'ni o'zgartirish ham shart emas. `/meta` token muddatini ko'rsatadi («♾ muddatsiz»).
Token ishlamay qolsa (parol o'zgarsa, ilova ruxsati olib tashlansa) — bot adminlarga o'zi xabar beradi.

## 🎬 Video: Telegram → Facebook va Instagram
Botga kanal postining havolasini (`https://t.me/Namanganliklar_uz/1234`) yuboring yoki videoni forward qiling.
- Tik (9:16) video → Facebook **Reels**, yotiq (16:9) → Facebook **oddiy video**; Instagram'ga har doim **Reels**
- Instagram: izoh — videoning o'z izohi, o'zgarishsiz
- **Facebook:** videodagi bo'sh joylar/shablon avtomatik qirqiladi (masalan tik ramkaga solingan yotiq video → yotiq oddiy video),
  izoh oxiriga hashtaglar qo'shiladi: `#Namangan #Namanganliklar #tezkor #Yangiliklar #news` (o'zgartirish: `FB_HASHTAGS`)
- Chegara: 3 daqiqagacha. **Asl sifat (HD, 250 MB gacha):** Railway'ga `TG_API_ID` va `TG_API_HASH` qo'shing
  (https://my.telegram.org → API development tools) va kanal postining havolasini yuboring — bot videoni kanaldan asl
  sifatda oladi. Bular bo'lmasa — Telegram nusxasi, 20 MB gacha. Bot kanalda admin bo'lishi kerak
- Video 720p dan past bo'lsa bot ogohlantiradi: Facebook past sifatli videoni kam ko'rsatadi
- Joylangan joylar ✅ bilan belgilanadi, qayta bosilsa «avval joylangan» deydi

## 🌐 Saytdagi yangi maqolalar (avtomatik)
Bot namanganliklar.uz ni har 90 soniyada tekshiradi. Yangi maqola chiqsa — sarlavha va rasmdan tayyor yangilik
rasmini yasab **adminlarga** yuboradi (izohida maqola havolasi bilan). Kanalga faqat «📤 Kanalga yuborish»
bosilgandan keyin chiqadi.
- `/sayt` — holat, `/sayt off` — o'chirish, `/sayt on` — yoqish
- Qo'lda yuborilgan rasmda ham havola bo'lishi mumkin: izoh oxiriga maqola manzilini yozing
- Sozlamalar: `SITE_URL` (standart https://namanganliklar.uz), `SITE_POLL_SEC` (90),
  `SITE_IMAGE_SIZE` — rasm papkasi (standart `800x450` — saytdagi eng katta nusxa)

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

## 🔊 Yandex Stansiya — namoz vaqtida ovozli eslatma (faqat adminlar)
Kodi: `yandex/` papkasida (`station.py` — Yandex bilan bog'lanish, `kolonka.py` — bot paneli va eslatuvchi).
Namoz vaqti kirganda uydagi Yandex kolonka (Alisa) siz yozgan matnni ovoz chiqarib aytadi.
Sozlash: `/kolonka` yoki «⚙️ Admin panel» → «🔊 Yandex kolonka».
1. **🔑 Akkauntni ulash** — kompyuterda `passport.yandex.ru/profile` ni oching (kolonka ulangan akkaunt bilan),
   F12 → Network → F5 → birinchi qator → Request Headers → `Cookie:` qiymatini nusxalab botga yuboring
   (uzun bo'lsa .txt fayl qilib). Yoki «Get cookies.txt LOCALLY» kengaytmasi bilan yandex.ru cookie'larini
   eksport qilib, .txt (yoki JSON) faylni yuboring. Bot xabarni o'chiradi, cookie'dan bir marta Yandex tokenini
   (x-token, ~1 yil) oladi va bazada faqat tokenni saqlaydi; har ulanishda token bilan yangi sessiya ochiladi.
2. **📻 Kolonkani tanlash** — akkauntdagi kolonkalardan birini tanlang.
3. Har bir namozni bosib **matnini** yozasiz (100 belgigacha), yoqasiz/o'chirasiz, «🔈 Kolonkada eshitish» bilan sinaysiz.
   Standart: Bomdod, Peshin, Asr, Shom, Xufton yoqilgan; Quyosh o'chiq.
4. **▶️ Yoqish** — shundan keyin har kuni jadvaldagi vaqtlarda (Peshin — `PESHIN_FIXED`) kolonka gapiradi.
- Alisa o'zbek tilini bilmaydi: lotin matn avtomatik kirillga o'giriladi va rus ovozida o'qiladi
  («🔤 Kirill» tugmasi bilan o'chirish mumkin; ruscha matn yozsangiz ham bo'ladi).
- Bot Yandex akkauntida «Namoz eslatma bot» nomli stsenariy yaratadi — uni o'chirmang (o'chirilsa, qayta yaratadi).
- Yandex rasmiy API bermaydi; token bekor bo'lsa yoki Yandex o'zgartirsa, yuborishda xato bo'lsa adminlarga xabar keladi — qaytadan ulang.
