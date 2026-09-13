"""
Asosiy JARVIS AI suhbat qatlami. Bu yerga faqat MODERATSIYADAN O'TGAN xabarlar keladi
(handlers.py da avval moderate_text chaqiriladi) — Bo'lim 7 dagi "Input Filter -> ... -> AI" oqimi.
"""
from pathlib import Path

import anthropic

from .config import config, CLOUD_AI_IMPLEMENTATION_NOTE

_PROMPT_PATH = Path(__file__).parent / "system_prompt.txt"
_BASE_SYSTEM_PROMPT = _PROMPT_PATH.read_text(encoding="utf-8")

# Foydalanuvchi so'ragan qo'shimcha tushuntirish asosiy promptga BIRIKTIRILADI (almashtirilmaydi).
FULL_SYSTEM_PROMPT = f"{_BASE_SYSTEM_PROMPT}\n\n---\n\n{CLOUD_AI_IMPLEMENTATION_NOTE}"

_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

# Har bir foydalanuvchi uchun oxirgi N xabarni xotirada saqlaymiz (oddiy in-memory tarix).
# Katta o'lchamda buni Redis/DB ga ko'chiring.
_HISTORY_LIMIT = 12
_conversation_history: dict[int, list[dict]] = {}


def _build_system_prompt(couple_memory: dict) -> str:
    if not couple_memory:
        return FULL_SYSTEM_PROMPT
    memory_lines = "\n".join(f"- {k}: {v}" for k, v in couple_memory.items())
    memory_block = (
        "\n\n---\n\nCOUPLE MEMORY (foydalanuvchi ataylab saqlagan ma'lumotlar, boshqasini o'ylab topma):\n"
        f"{memory_lines}"
    )
    return FULL_SYSTEM_PROMPT + memory_block


async def get_jarvis_response(user_id: int, user_message: str, couple_memory: dict = None) -> str:
    history = _conversation_history.setdefault(user_id, [])
    history.append({"role": "user", "content": user_message})
    history = history[-_HISTORY_LIMIT:]

    resp = _client.messages.create(
        model=config.JARVIS_MODEL,
        max_tokens=1024,
        system=_build_system_prompt(couple_memory or {}),
        messages=history,
    )
    reply = "".join(b.text for b in resp.content if b.type == "text").strip()

    history.append({"role": "assistant", "content": reply})
    _conversation_history[user_id] = history[-_HISTORY_LIMIT:]
    return reply


def reset_history(user_id: int):
    _conversation_history.pop(user_id, None)
