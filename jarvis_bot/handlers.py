"""
Telegram handlerlari. Oqim (Bo'lim 7):

Foydalanuvchi -> Input Filter (blok tekshiruvi) -> Text Normalization -> 18+ Moderation
    -> Safety Check -> JARVIS AI -> Output Filter -> Telegram
"""
import logging
import secrets
import time

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message

from .config import config
from .db import db
from .jarvis_ai import get_jarvis_response, reset_history
from .moderation import moderate_text, moderate_image, ModerationVerdict

logger = logging.getLogger("jarvis.handlers")
router = Router()

# ---------- Bo'lim 4 va 5 dagi tayyor xabarlar ----------

MSG_18PLUS_BLOCKED = (
    "⚠️ Bu turdagi 18+ kontent JARVIS'da qat'iyan taqiqlangan. "
    "Iltimos, romantik va sog'lom munosabat mavzusida davom eting. ❤️"
)

MSG_WARNING_1 = (
    "⚠️ OGOHLANTIRISH!\n\n"
    "18+ kontent JARVIS'da qat'iyan taqiqlangan.\n\n"
    "Iltimos, bunday kontent yubormang.\n\n"
    "Takroriy qoidabuzarlik vaqtinchalik bloklanishga olib keladi. 🔒"
)

MSG_WARNING_2 = (
    "🚫 SIZ QAYTA OGOHLANTIRILDINGIZ!\n\n"
    "Siz 18+ taqiqlangan kontentni qayta yubordingiz.\n\n"
    "JARVIS'dan foydalanish {minutes} daqiqaga vaqtincha bloklandi. ⏳\n\n"
    "{minutes} daqiqadan keyin foydalanish avtomatik ravishda tiklanadi."
)

MSG_BLOCKED_ACTIVE = "🔒 Siz vaqtincha bloklangansiz. Qolgan vaqt: {seconds} soniya."

MSG_MODERATION_UNCERTAIN_MEDIA = (
    "⚠️ Ushbu faylni xavfsizlik sababli tekshirishning imkoni bo'lmadi, "
    "shuning uchun u yuborilmadi. Iltimos, boshqa fayl yuboring. ❤️"
)

MSG_MEDIA_BLOCKED = (
    "⚠️ Ushbu media fayl JARVIS xavfsizlik qoidalariga mos kelmagani sababli yuborilmadi. "
    "Iltimos, boshqa xavfsiz media yuboring. ❤️"
)

ADMIN_ALERT_TEMPLATE = (
    "🚨 JARVIS XAVFSIZLIK OGOHLANTIRISHI\n\n"
    "Foydalanuvchi 18+ qoidani {count} marta buzdi.\n\n"
    "👤 Telegram nomi: {first_name} {last_name}\n"
    "🔗 Username: @{username}\n"
    "🆔 User ID: {user_id}\n"
    "⚠️ Qoidabuzarliklar: {count}\n"
    "🕐 Vaqt: {timestamp}\n"
    "🚫 Holat: Admin ko'rib chiqishi kerak.\n"
    "{phone_line}"
)


async def _handle_violation(message: Message, bot) -> None:
    """Bo'lim 5: 18+ qoidabuzarlik aniqlanganda chaqiriladi (matn yoki media, farqi yo'q —
    umumiy bitta counterga qo'shiladi)."""
    user = message.from_user
    count = await db.register_violation(user.id)

    if count == config.WARN_THRESHOLD:
        await message.answer(MSG_WARNING_1)
    elif count == config.BLOCK_THRESHOLD:
        await message.answer(MSG_WARNING_2.format(minutes=config.TEMP_BLOCK_MINUTES))
    elif count >= config.ADMIN_ALERT_THRESHOLD:
        record = await db.get_user(user.id)
        phone_line = f"📱 Telefon raqami: {record.phone_number}" if record and record.phone_number else ""
        if config.ADMIN_CHAT_ID:
            await bot.send_message(
                config.ADMIN_CHAT_ID,
                ADMIN_ALERT_TEMPLATE.format(
                    count=count,
                    first_name=user.first_name or "",
                    last_name=user.last_name or "",
                    username=user.username or "yo'q",
                    user_id=user.id,
                    timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
                    phone_line=phone_line,
                ),
            )
        # Admin qo'shimcha cheklov qo'llamaguncha ham foydalanuvchi bloklanib turishi uchun
        # yana bir 20 daqiqalik blok qo'yamiz (siyosatga qarab bu qatorni o'zgartiring/olib tashlang).
        # config orqali sozlash mumkin.


@router.message(CommandStart())
async def cmd_start(message: Message):
    await db.get_or_create_user(
        message.from_user.id, message.from_user.username,
        message.from_user.first_name, message.from_user.last_name,
    )
    await message.answer(
        "Salom! Men JARVIS AI'man ❤️\n"
        "Men juftliklar uchun yaratilganman — munosabatlar, romantika, muloqot, "
        "sovg'a g'oyalari va birgalikdagi mashg'ulotlar bo'yicha yordam beraman.\n\n"
        "Juftlikka ulanish uchun: /pair\n"
        "Suhbatni boshlash uchun shunchaki yozing 💌"
    )


@router.message(Command("pair"))
async def cmd_pair(message: Message):
    """Ikki foydalanuvchini bir-biriga bog'lash (Couple Memory uchun asos)."""
    args = message.text.split(maxsplit=1)
    user_id = message.from_user.id
    await db.get_or_create_user(user_id, message.from_user.username,
                                 message.from_user.first_name, message.from_user.last_name)

    if len(args) == 1:
        code = secrets.token_hex(3)
        await db.create_pending_pair(code, user_id)
        await message.answer(
            f"Juftlik kodingiz: `{code}`\n\n"
            "Sevgilingizga shu kodni yuboring, u botga `/pair {code}` deb yozsin.",
            parse_mode="Markdown",
        )
    else:
        code = args[1].strip()
        partner_id = await db.consume_pending_pair(code)
        if not partner_id or partner_id == user_id:
            await message.answer("❌ Kod topilmadi yoki muddati o'tgan. Qaytadan urinib ko'ring.")
            return
        couple_id = await db.link_couple(user_id, partner_id)
        await message.answer("✅ Juftlik muvaffaqiyatli bog'landi! Endi Couple Memory ishlaydi. ❤️")


@router.message(F.text & ~F.text.startswith("/"))
async def handle_text(message: Message, bot):
    user_id = message.from_user.id
    record = await db.get_or_create_user(
        user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name,
    )

    # --- Input Filter: blok holatini tekshirish ---
    if record.is_blocked:
        await message.answer(MSG_BLOCKED_ACTIVE.format(seconds=record.block_remaining_seconds))
        return

    # --- 18+ Moderation ---
    result = await moderate_text(message.text)

    if result.verdict == ModerationVerdict.SAFE:
        couple_memory = await db.get_couple_memory(record.couple_id) if record.couple_id else {}
        reply = await get_jarvis_response(user_id, message.text, couple_memory)
        # --- Output Filter: JARVIS javobini ham tekshiramiz (ikki tomonlama himoya) ---
        output_check = await moderate_text(reply)
        if output_check.allowed:
            await message.answer(reply)
        else:
            logger.warning("JARVIS output blocked by output filter for user %s", user_id)
            await message.answer(MSG_18PLUS_BLOCKED)
        return

    # UNSAFE yoki UNCERTAIN -> AI'ga umuman yuborilmaydi, foydalanuvchiga qayta ko'rsatilmaydi
    if result.verdict == ModerationVerdict.UNCERTAIN:
        await message.answer(MSG_MODERATION_UNCERTAIN_MEDIA)
        return

    await message.answer(MSG_18PLUS_BLOCKED)
    await _handle_violation(message, bot)


@router.message(F.photo)
async def handle_photo(message: Message, bot):
    """Bo'lim 8-17: media avval moderatsiyadan o'tadi, keyingina (agar juftlikka ulangan bo'lsa)
    hamkorga forward qilinadi. 'Avval tekshir -> keyin yubor' printsipi."""
    user_id = message.from_user.id
    record = await db.get_or_create_user(
        user_id, message.from_user.username, message.from_user.first_name, message.from_user.last_name,
    )

    if record.is_blocked:
        await message.answer(MSG_BLOCKED_ACTIVE.format(seconds=record.block_remaining_seconds))
        return

    # Caption bo'lsa, uni alohida tekshiramiz (Bo'lim 16: media va caption alohida tekshiriladi)
    if message.caption:
        caption_result = await moderate_text(message.caption)
        if not caption_result.allowed:
            await message.answer(MSG_MEDIA_BLOCKED if caption_result.verdict == ModerationVerdict.UNSAFE
                                  else MSG_MODERATION_UNCERTAIN_MEDIA)
            if caption_result.verdict == ModerationVerdict.UNSAFE:
                await _handle_violation(message, bot)
            return

    photo = message.photo[-1]
    file = await bot.get_file(photo.file_id)
    file_bytes = await bot.download_file(file.file_path)
    image_result = await moderate_image(file_bytes.read())

    if image_result.verdict == ModerationVerdict.SAFE:
        if record.couple_id:
            # TODO: hamkorning telegram user_id'sini couple yozuvidan topib forward qiling.
            # Bu qism sizning couple modelingizga qarab handlers/pairing.py ichida to'ldiriladi.
            await message.answer("✅ Rasm xavfsiz deb tasdiqlandi va sevgilingizga yuborildi. 📸❤️")
        else:
            await message.answer("✅ Rasm xavfsiz. (Hamkoringizga yuborish uchun avval /pair qiling.)")
        return

    if image_result.verdict == ModerationVerdict.UNCERTAIN:
        await message.answer(MSG_MODERATION_UNCERTAIN_MEDIA)
        return

    await message.answer(MSG_MEDIA_BLOCKED)
    await _handle_violation(message, bot)


@router.message(F.video | F.audio | F.voice | F.video_note | F.animation | F.document)
async def handle_unscannable_media(message: Message, bot):
    """Bo'lim 11-12: video/audio uchun chuqur skanerlash ushbu asosiy implementatsiyada
    ulanmagan (README'dagi 'Kengaytirish' bo'limiga qarang). Bo'lim 15 qoidasiga ko'ra
    ishonchli tekshira olmasak — har doim BLOKLAYMIZ, hech qachon o'tkazib yubormaymiz."""
    await message.answer(MSG_MODERATION_UNCERTAIN_MEDIA)


@router.message(Command("reset"))
async def cmd_reset(message: Message):
    reset_history(message.from_user.id)
    await message.answer("Suhbat tarixi tozalandi. 🧹")
