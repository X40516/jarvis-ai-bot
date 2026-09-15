"""
Markazlashgan konfiguratsiya. Barcha maxfiy qiymatlar (.env) fayldan o'qiladi.
"""
import os
from dotenv import load_dotenv

load_dotenv()


def _get_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val else default


class Config:
    # Telegram
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
    ADMIN_CHAT_ID: int = _get_int("ADMIN_CHAT_ID", 0)  # 3-marta qoidabuzarlikda xabar shu chatga boradi

    # Anthropic (Claude / "Cloud AI")
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    JARVIS_MODEL: str = os.getenv("JARVIS_MODEL", "claude-sonnet-4-6")
    MODERATION_MODEL: str = os.getenv("MODERATION_MODEL", "claude-sonnet-4-6")

    # Xavfsizlik siyosati
    TEMP_BLOCK_MINUTES: int = _get_int("TEMP_BLOCK_MINUTES", 20)  # 2-marta qoidabuzarlikda blok davomiyligi
    WARN_THRESHOLD: int = 1
    BLOCK_THRESHOLD: int = 2
    ADMIN_ALERT_THRESHOLD: int = 3

    # Ma'lumotlar bazasi (Postgres — Railway'dagi doimiy xizmat)
    DATABASE_URL: str = os.getenv("DATABASE_URL", "")


config = Config()

# --- "Cloud AI"ga (Claude'ga) yuboriladigan izoh (foydalanuvchi so'ragan qo'shimcha tushuntirish) ---
# Bu matn asosiy system promptga QO'SHIMCHA sifatida biriktiriladi (system_prompt.txt bilan almashtirilmaydi).
CLOUD_AI_IMPLEMENTATION_NOTE = """
Ushbu prompt JARVIS AI uchun asosiy system prompt hisoblanadi. Promptdagi xavfsizlik qoidalarini
backenddagi moderation, media filtering, violation counter, temporary blocking va admin notification
tizimlari bilan birgalikda amalga oshir. 18+ kontentni hech qanday formatda ikkinchi foydalanuvchiga
yetkazma. Media fayllarni yuborishdan oldin moderatsiyadan o'tkaz. Moderatsiya noaniq bo'lsa,
yuborishni blokla.
""".strip()
