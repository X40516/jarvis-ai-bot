"""
Telegram handlerlari. Oqim (Bo'lim 7):

Foydalanuvchi -> Input Filter (blok tekshiruvi) -> Text Normalization -> 18+ Moderation
    -> Safety Check -> JARVIS AI -> Output Filter -> Telegram
"""
import logging
import time

from aiogram import Router, F
from aiogram.filters import CommandStart, Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton

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
        "Juftlikka ulanish uchun: /pair @username\n"
        "Suhbatni boshlash uchun shunchaki yozing 💌"
    )


@router.message(Command("pair"))
async def cmd_pair(message: Message, bot):
    """Ikki foydalanuvchini Telegram username orqali bog'lash so'rovini boshlaydi.

    Foydalanish: /pair @sevgilim_username

    Oqim:
    1. Foydalanuvchi /pair @username yozadi.
    2. Bot bazada shu username ro'yxatdan o'tganmi tekshiradi (ikkalasi ham botdan
       kamida bir marta /start bilan foydalangan bo'lishi kerak).
    3. Topilsa — hamkorga tasdiqlash so'rovi (✅/❌ tugmalar bilan) yuboriladi.
       Hech kim boshqasini o'z roziligisiz "juftlik"ka ulay olmaydi.
    4. Hamkor ✅ bossagina ikkala user_id bazada bog'lanadi.
    """
    args = message.text.split(maxsplit=1)
    user_id = message.from_user.id
    await db.get_or_create_user(user_id, message.from_user.username,
                                 message.from_user.first_name, message.from_user.last_name)

    if len(args) == 1 or not args[1].strip():
        await message.answer(
            "Juftlikka ulanish uchun sevgilingizning Telegram username'ini yuboring:\n\n"
            "`/pair @username`\n\n"
            "Eslatma: sevgilingiz botga kamida bir marta /start bosgan bo'lishi kerak. ❤️",
            parse_mode="Markdown",
        )
        return

    target_username = args[1].strip()
    if not target_username.startswith("@"):
        await message.answer("❌ Iltimos, username'ni @ belgisi bilan yuboring, masalan: `/pair @sevgilim`",
                              parse_mode="Markdown")
        return

    if target_username.lstrip("@").lower() == (message.from_user.username or "").lower():
        await message.answer("❌ O'zingiz bilan juftlasha olmaysiz 😅")
        return

    partner = await db.get_user_by_username(target_username)
    if not partner:
        await message.answer(
            f"❌ {target_username} topilmadi. U botga hali /start bosmagan bo'lishi mumkin — "
            "avval sevgilingiz botni ishga tushirsin, keyin qaytadan urinib ko'ring."
        )
        return

    # --- 2-qadam: so'rov yaratiladi (hali hech kim bog'lanmagan) ---
    await db.create_pair_request(requester_id=user_id, target_id=partner.user_id)

    # --- 3-qadam: hamkorga tasdiqlash so'rovi yuboriladi ---
    requester_label = f"@{message.from_user.username}" if message.from_user.username else message.from_user.first_name
    keyboard = InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Ha", callback_data=f"pair_accept:{user_id}"),
        InlineKeyboardButton(text="❌ Yo'q", callback_data=f"pair_reject:{user_id}"),
    ]])
    try:
        await bot.send_message(
            partner.user_id,
            f"💌 {requester_label} sizni juftlik sifatida qo'shmoqchi. Tasdiqlaysizmi?",
            reply_markup=keyboard,
        )
        await message.answer(f"✅ So'rov {target_username}ga yuborildi. Tasdiqlashini kuting. ⏳")
    except Exception:
        logger.warning("Hamkorga (%s) pairing so'rovini yuborib bo'lmadi", partner.user_id)
        await message.answer(
            "❌ Sevgilingizga so'rov yuborib bo'lmadi (u botni bloklagan bo'lishi mumkin)."
        )


@router.callback_query(F.data.startswith("pair_accept:"))
async def cb_pair_accept(callback: CallbackQuery, bot):
    """4-qadam: hamkor ✅ bosganda — ikkala user_id bazada bog'lanadi."""
    requester_id = int(callback.data.split(":", 1)[1])
    target_id = callback.from_user.id

    ok = await db.consume_pair_request(requester_id=requester_id, target_id=target_id)
    if not ok:
        await callback.answer("Bu so'rov muddati o'tgan yoki allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    await db.link_couple(requester_id, target_id)
    await callback.message.edit_text("✅ Juftlik muvaffaqiyatli bog'landi! Endi Couple Memory ishlaydi. ❤️")
    await callback.answer("Tasdiqlandi ✅")
    try:
        await bot.send_message(
            requester_id,
            f"✅ @{callback.from_user.username or callback.from_user.first_name} so'rovingizni tasdiqladi! "
            "Endi juftlik sifatida bog'landingiz. ❤️",
        )
    except Exception:
        logger.warning("So'rov yuboruvchiga (%s) tasdiqlash xabarini yuborib bo'lmadi", requester_id)


@router.callback_query(F.data.startswith("pair_reject:"))
async def cb_pair_reject(callback: CallbackQuery, bot):
    requester_id = int(callback.data.split(":", 1)[1])
    target_id = callback.from_user.id

    ok = await db.consume_pair_request(requester_id=requester_id, target_id=target_id)
    if not ok:
        await callback.answer("Bu so'rov muddati o'tgan yoki allaqachon ko'rib chiqilgan.", show_alert=True)
        return

    await callback.message.edit_text("❌ So'rov rad etildi.")
    await callback.answer("Rad etildi")
    try:
        await bot.send_message(requester_id, "❌ Sevgilingiz juftlik so'rovini rad etdi.")
    except Exception:
        logger.warning("So'rov yuboruvchiga (%s) rad etish xabarini yuborib bo'lmadi", requester_id)


def _get_partner_id(couple_id, user_id) -> int | None:
    """couple_id formati 'min_max' (ikkala user_id), shundan hamkornikini ajratib oladi."""
    if not couple_id:
        return None
    a, b = couple_id.split("_")
    a, b = int(a), int(b)
    return b if a == user_id else a


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
        partner_id = _get_partner_id(record.couple_id, user_id)
        if partner_id:
            try:
                await bot.send_photo(partner_id, photo.file_id, caption=message.caption)
                await message.answer("✅ Rasm xavfsiz deb tasdiqlandi va sevgilingizga yuborildi. 📸❤️")
            except Exception:
                logger.warning("Hamkorga (%s) rasm yuborib bo'lmadi", partner_id)
                await message.answer("✅ Rasm xavfsiz, lekin sevgilingizga yuborishda xatolik yuz berdi.")
        else:
            await message.answer("✅ Rasm xavfsiz. (Hamkoringizga yuborish uchun avval /pair @username qiling.)")
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
