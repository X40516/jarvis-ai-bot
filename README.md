# JARVIS AI — Telegram Bot

Juftliklar uchun romantik/munosabat yordamchisi. Python + [aiogram 3](https://docs.aiogram.dev/) +
[Anthropic Claude API](https://docs.claude.com/).

## Ishga tushirish

```bash
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env              # so'ng .env ichiga o'z tokenlaringizni yozing
python main.py
```

`.env` faylida kamida ikkitasi shart:
- `BOT_TOKEN` — @BotFather'dan olingan token
- `ANTHROPIC_API_KEY` — Claude API kaliti

`ADMIN_CHAT_ID` ni to'ldirmasangiz, 3-marta qoidabuzarlikdagi admin xabari faqat log'ga yoziladi
(yuborilmaydi), lekin bot yiqilmaydi.

## Arxitektura (spetsifikatsiyaga mos)

```
jarvis_bot/
├── config.py          # .env dan sozlamalar + "Cloud AI"ga qo'shiladigan implementatsiya izohi
├── system_prompt.txt  # asosiy JARVIS system prompt (o'zbek tilida, siz bergan matn)
├── db.py              # SQLite: users, violation_count, block_until, couple_id, couple_memory
├── moderation.py       # matn va rasm moderatsiyasi (Claude), fail-closed
├── jarvis_ai.py       # asosiy suhbat: system_prompt.txt + implementatsiya izohi + couple memory
└── handlers.py        # aiogram handlerlari: /start, /pair, matn, rasm, video/audio/boshqa media
main.py                # kirish nuqtasi
```

Xabar oqimi (Bo'lim 7):

```
Foydalanuvchi -> blok tekshiruvi -> 18+ Moderation -> JARVIS AI -> Output Filter -> Telegram
```

Media oqimi (Bo'lim 8-17):

```
Media -> Caption moderatsiyasi -> Rasm moderatsiyasi (vision) -> ✅ Safe -> hamkorga yuborish
                                                                -> ❌/noaniq -> BLOKLANADI
```

## Violation counter va bloklash (Bo'lim 5)

| Marta | Amal |
|---|---|
| 1 | Ogohlantirish xabari, `violation_count = 1` |
| 2 | `TEMP_BLOCK_MINUTES` (standart 20 daqiqa) vaqtinchalik blok |
| 3+ | `ADMIN_CHAT_ID`ga Telegram user ma'lumotlari bilan xabar |

Counter matn va media uchun **bitta umumiy** ustunda saqlanadi (`users.violation_count`),
spetsifikatsiyaning 5-bo'limiga mos.

## Maxfiylik (Bo'lim 18)

- 18+ kontentning o'zi (matn, rasm, hech qanday format) bazaga **hech qachon** yozilmaydi —
  faqat "unsafe" bayrog'i va vaqt belgisi saqlanadi.
- Telefon raqami faqat foydalanuvchi o'zi ixtiyoriy yuborgan bo'lsa saqlanadi
  (`db.set_phone_number` — buni, masalan, contact-share handlerida chaqiring).
- Couple Memory faqat foydalanuvchi aniq aytib saqlashni so'ragan narsalarni saqlaydi
  (`db.save_couple_memory`) — JARVIS hech narsani o'zi to'qib chiqarmaydi.

## Muhim cheklov: video/audio chuqur skanerlash

Spetsifikatsiyaning 11-12-bo'limlari video/audio uchun ko'p kadrli va nutqni tahlil qiluvchi
chuqur tekshiruvni talab qiladi. Bu asosiy implementatsiyada **ulanmagan** — sabab: bu alohida
pipeline talab qiladi (masalan, video kadrlarini ffmpeg bilan ajratib har birini vision
moderatsiyasidan o'tkazish, audio uchun nutqni matnga aylantirib keyin matn moderatsiyasidan
o'tkazish).

Hozircha `handle_unscannable_media` bu turdagi har qanday faylni **har doim bloklaydi**
(Bo'lim 15: "noaniq bo'lsa — blokla" qoidasiga muvofiq — xato tomonga og'ib ketmaslik uchun eng
xavfsiz variant). Buni ishlab chiqarishga chiqarishdan oldin quyidagilarni qo'shishni tavsiya
qilaman:

1. **Video**: `ffmpeg` bilan bir necha kadr ajratib olish → har birini `moderate_image()`ga
   yuborish → audio yo'lagini transkripsiya qilish (masalan, Whisper API) → matnni
   `moderate_text()`ga yuborish.
2. **Audio/ovozli xabar**: transkripsiya → `moderate_text()`.
3. Ikkalasi ham xavfsiz chiqsagina forward qilinsin — aks holda yoki noaniq bo'lsa, bloklansin.

## Couple pairing va media forward

`/pair` orqali ikki foydalanuvchi bir-biriga bog'lanadi (`db.link_couple`). `handle_photo`
ichidagi `# TODO` qatorida — rasm xavfsiz deb topilgandan so'ng uni hamkorning
`chat_id`siga qanday forward qilishni loyihangizning couple-modeliga moslab yozib qo'ying
(hamkor `user_id`sini `users.couple_id` orqali topib, `bot.send_photo(partner_id, photo.file_id)`
chaqirish kifoya).

## Kengaytirish g'oyalari

- SQLite o'rniga Postgres (yuqori parallellik uchun) — `Database` interfeysi shu ko'rinishda
  qoladi, faqat ichki `_connect`/so'rovlarni almashtirasiz.
- Suhbat tarixini xotira o'rniga Redis/DB'ga ko'chirish (bot qayta ishga tushganda yo'qolmasligi
  uchun).
- Admin uchun `/stats`, `/unblock <user_id>` kabi qo'shimcha buyruqlar.
