"""
Ma'lumotlar bazasi qatlami (PostgreSQL, asyncpg orqali).

Railway'dagi doimiy Postgres xizmatiga ulanadi — shuning uchun qayta deploy yoki
qayta ishga tushirishlarda ma'lumotlar (foydalanuvchilar, qoidabuzarliklar, juftliklar,
Couple Memory) YO'QOLMAYDI, ilgarigi vaqtinchalik SQLite'dan farqli o'laroq.

Bo'lim 6 va 18 (Maxfiylik) talablariga ko'ra faqat zarur minimal ma'lumot saqlanadi:
user_id, username, first/last name, violation_count, oxirgi violation vaqti, block_until,
va (agar foydalanuvchi ixtiyoriy yuborgan bo'lsa) phone_number.
18+ kontentning o'zi HECH QACHON bazaga yozilmaydi.
"""
import time
from dataclasses import dataclass
from typing import Optional

import asyncpg

from .config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id BIGINT PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    phone_number TEXT,                 -- faqat ixtiyoriy yuborilgan bo'lsa to'ldiriladi
    violation_count INTEGER NOT NULL DEFAULT 0,
    last_violation_at DOUBLE PRECISION,
    block_until DOUBLE PRECISION,      -- unix timestamp; NULL yoki o'tmishda bo'lsa bloklanmagan
    couple_id TEXT
);

CREATE TABLE IF NOT EXISTS couple_memory (
    couple_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (couple_id, key)
);

CREATE TABLE IF NOT EXISTS pair_requests (
    requester_id BIGINT NOT NULL,
    target_id BIGINT NOT NULL,
    created_at DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (requester_id, target_id)
);
"""


@dataclass
class UserRecord:
    user_id: int
    username: Optional[str]
    first_name: Optional[str]
    last_name: Optional[str]
    phone_number: Optional[str]
    violation_count: int
    last_violation_at: Optional[float]
    block_until: Optional[float]
    couple_id: Optional[str]

    @property
    def is_blocked(self) -> bool:
        return bool(self.block_until and self.block_until > time.time())

    @property
    def block_remaining_seconds(self) -> int:
        if not self.is_blocked:
            return 0
        return max(0, int(self.block_until - time.time()))


class Database:
    """asyncpg connection pool ustida ishlaydi. `init()` main.py ichida, polling
    boshlanishidan oldin bir marta chaqiriladi."""

    def __init__(self, dsn: str = None):
        self.dsn = dsn or config.DATABASE_URL
        self._pool: Optional[asyncpg.Pool] = None

    async def init(self):
        if not self.dsn:
            raise RuntimeError("DATABASE_URL .env yoki Railway o'zgaruvchilarida topilmadi.")
        self._pool = await asyncpg.create_pool(self.dsn, min_size=1, max_size=10)
        async with self._pool.acquire() as conn:
            await conn.execute(_SCHEMA)

    async def close(self):
        if self._pool:
            await self._pool.close()

    # ---------- Foydalanuvchi ----------

    async def get_or_create_user(self, user_id: int, username: str = None,
                                  first_name: str = None, last_name: str = None) -> UserRecord:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                INSERT INTO users (user_id, username, first_name, last_name, violation_count)
                VALUES ($1, $2, $3, $4, 0)
                ON CONFLICT (user_id) DO UPDATE
                    SET username=EXCLUDED.username,
                        first_name=EXCLUDED.first_name,
                        last_name=EXCLUDED.last_name
                RETURNING *
                """,
                user_id, username, first_name, last_name,
            )
            return self._row_to_record(row)

    async def set_phone_number(self, user_id: int, phone_number: str):
        async with self._pool.acquire() as conn:
            await conn.execute("UPDATE users SET phone_number=$1 WHERE user_id=$2", phone_number, user_id)

    async def get_user(self, user_id: int) -> Optional[UserRecord]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE user_id=$1", user_id)
            return self._row_to_record(row) if row else None

    async def get_user_by_username(self, username: str) -> Optional[UserRecord]:
        """Telegram username orqali foydalanuvchini topadi (faqat botga avval /start
        bosgan foydalanuvchilar topiladi — Telegram Bot API boshqacha yo'l bermaydi)."""
        username = username.lstrip("@").lower()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM users WHERE lower(username)=$1", username)
            return self._row_to_record(row) if row else None

    # ---------- Qoidabuzarlik / bloklash ----------

    async def register_violation(self, user_id: int) -> int:
        """Violation counterni +1 qiladi va yangi qiymatni qaytaradi.

        20 daqiqalik vaqtinchalik blok BLOCK_THRESHOLD (2-marta)da shu yerda qo'llanadi.
        """
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                new_count = await conn.fetchval(
                    "UPDATE users SET violation_count = violation_count + 1, last_violation_at=$1 "
                    "WHERE user_id=$2 RETURNING violation_count",
                    time.time(), user_id,
                )
                if new_count == config.BLOCK_THRESHOLD:
                    block_until = time.time() + config.TEMP_BLOCK_MINUTES * 60
                    await conn.execute("UPDATE users SET block_until=$1 WHERE user_id=$2", block_until, user_id)
                return new_count

    # ---------- Couple pairing & memory ----------

    async def create_pair_request(self, requester_id: int, target_id: int):
        """/pair @username bosilganda so'rov yaratiladi — hali hech kim bog'lanmagan,
        faqat tasdiqlash kutilmoqda."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO pair_requests (requester_id, target_id, created_at) VALUES ($1, $2, $3)
                ON CONFLICT (requester_id, target_id) DO UPDATE SET created_at=EXCLUDED.created_at
                """,
                requester_id, target_id, time.time(),
            )

    async def consume_pair_request(self, requester_id: int, target_id: int) -> bool:
        """Callback (✅/❌) bosilganda chaqiriladi. So'rov haqiqatan mavjud bo'lsagina
        True qaytaradi va uni bazadan o'chiradi (bir marta ishlatiladi, replay'ga qarshi)."""
        async with self._pool.acquire() as conn:
            deleted = await conn.fetchval(
                "DELETE FROM pair_requests WHERE requester_id=$1 AND target_id=$2 RETURNING 1",
                requester_id, target_id,
            )
            return deleted is not None

    async def link_couple(self, user_a: int, user_b: int) -> str:
        couple_id = f"{min(user_a, user_b)}_{max(user_a, user_b)}"
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE users SET couple_id=$1 WHERE user_id=$2", couple_id, user_a)
                await conn.execute("UPDATE users SET couple_id=$1 WHERE user_id=$2", couple_id, user_b)
        return couple_id

    async def save_couple_memory(self, couple_id: str, key: str, value: str):
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO couple_memory (couple_id, key, value) VALUES ($1, $2, $3)
                ON CONFLICT (couple_id, key) DO UPDATE SET value=EXCLUDED.value
                """,
                couple_id, key, value,
            )

    async def get_couple_memory(self, couple_id: str) -> dict:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT key, value FROM couple_memory WHERE couple_id=$1", couple_id)
            return {r["key"]: r["value"] for r in rows}

    # ---------- yordamchi metodlar ----------

    @staticmethod
    def _row_to_record(row) -> UserRecord:
        return UserRecord(
            user_id=row["user_id"],
            username=row["username"],
            first_name=row["first_name"],
            last_name=row["last_name"],
            phone_number=row["phone_number"],
            violation_count=row["violation_count"],
            last_violation_at=row["last_violation_at"],
            block_until=row["block_until"],
            couple_id=row["couple_id"],
        )


db = Database()
