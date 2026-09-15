"""
Ma'lumotlar bazasi qatlami (SQLite).

Bo'lim 6 va 18 (Maxfiylik) talablariga ko'ra faqat zarur minimal ma'lumot saqlanadi:
user_id, username, first/last name, violation_count, oxirgi violation vaqti, block_until,
va (agar foydalanuvchi ixtiyoriy yuborgan bo'lsa) phone_number.
18+ kontentning o'zi HECH QACHON bazaga yozilmaydi.
"""
import asyncio
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional

from .config import config

_SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    user_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    last_name TEXT,
    phone_number TEXT,           -- faqat ixtiyoriy yuborilgan bo'lsa to'ldiriladi
    violation_count INTEGER NOT NULL DEFAULT 0,
    last_violation_at REAL,
    block_until REAL,            -- unix timestamp; NULL yoki o'tmishda bo'lsa bloklanmagan
    couple_id TEXT
);

CREATE TABLE IF NOT EXISTS couple_memory (
    couple_id TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    PRIMARY KEY (couple_id, key)
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
    """Har bir chaqiruv asyncio.to_thread orqali blokловчи sqlite3 ustida ishlaydi.

    Kichik/o'rta yuklama uchun yetarli. Katta hajmda concurrent yozuv kerak bo'lsa,
    buni Postgres + asyncpg ga almashtiring (interfeys shu ko'rinishda qoladi).
    """

    def __init__(self, path: str = None):
        self.path = path or config.DB_PATH
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    # ---------- Foydalanuvchi ----------

    async def get_or_create_user(self, user_id: int, username: str = None,
                                  first_name: str = None, last_name: str = None) -> UserRecord:
        return await asyncio.to_thread(self._get_or_create_user_sync, user_id, username, first_name, last_name)

    def _get_or_create_user_sync(self, user_id, username, first_name, last_name) -> UserRecord:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO users (user_id, username, first_name, last_name, violation_count) "
                    "VALUES (?, ?, ?, ?, 0)",
                    (user_id, username, first_name, last_name),
                )
                row = conn.execute("SELECT * FROM users WHERE user_id=?", (user_id,)).fetchone()
            else:
                # profil ma'lumotlarini yangilab boramiz (username o'zgargan bo'lishi mumkin)
                conn.execute(
                    "UPDATE users SET username=?, first_name=?, last_name=? WHERE user_id=?",
                    (username, first_name, last_name, user_id),
                )
            return self._row_to_record(row)

    async def set_phone_number(self, user_id: int, phone_number: str):
        await asyncio.to_thread(self._exec, "UPDATE users SET phone_number=? WHERE user_id=?", (phone_number, user_id))

    async def get_user(self, user_id: int) -> Optional[UserRecord]:
        row = await asyncio.to_thread(self._fetchone, "SELECT * FROM users WHERE user_id=?", (user_id,))
        return self._row_to_record(row) if row else None

    async def get_user_by_username(self, username: str) -> Optional[UserRecord]:
        """Telegram username orqali foydalanuvchini topadi (faqat botga avval /start
        bosgan foydalanuvchilar topiladi — Telegram Bot API boshqacha yo'l bermaydi)."""
        username = username.lstrip("@").lower()
        row = await asyncio.to_thread(
            self._fetchone, "SELECT * FROM users WHERE lower(username)=?", (username,)
        )
        return self._row_to_record(row) if row else None

    # ---------- Qoidabuzarlik / bloklash ----------

    async def register_violation(self, user_id: int) -> int:
        """Violation counterni +1 qiladi va yangi qiymatni qaytaradi.

        20 daqiqalik vaqtinchalik blok BLOCK_THRESHOLD (2-marta)da shu yerda qo'llanadi.
        """
        def _run():
            with self._connect() as conn:
                conn.execute(
                    "UPDATE users SET violation_count = violation_count + 1, last_violation_at=? WHERE user_id=?",
                    (time.time(), user_id),
                )
                row = conn.execute("SELECT violation_count FROM users WHERE user_id=?", (user_id,)).fetchone()
                new_count = row["violation_count"]
                if new_count == config.BLOCK_THRESHOLD:
                    block_until = time.time() + config.TEMP_BLOCK_MINUTES * 60
                    conn.execute("UPDATE users SET block_until=? WHERE user_id=?", (block_until, user_id))
                return new_count
        return await asyncio.to_thread(_run)

    # ---------- Couple pairing & memory ----------

    async def link_couple(self, user_a: int, user_b: int) -> str:
        couple_id = f"{min(user_a, user_b)}_{max(user_a, user_b)}"
        await asyncio.to_thread(
            self._exec_many,
            "UPDATE users SET couple_id=? WHERE user_id=?",
            [(couple_id, user_a), (couple_id, user_b)],
        )
        return couple_id

    async def save_couple_memory(self, couple_id: str, key: str, value: str):
        await asyncio.to_thread(
            self._exec,
            "INSERT OR REPLACE INTO couple_memory (couple_id, key, value) VALUES (?, ?, ?)",
            (couple_id, key, value),
        )

    async def get_couple_memory(self, couple_id: str) -> dict:
        rows = await asyncio.to_thread(
            self._fetchall, "SELECT key, value FROM couple_memory WHERE couple_id=?", (couple_id,)
        )
        return {r["key"]: r["value"] for r in rows}

    # ---------- yordamchi metodlar ----------

    def _exec(self, query, params):
        with self._connect() as conn:
            conn.execute(query, params)

    def _exec_many(self, query, params_list):
        with self._connect() as conn:
            for params in params_list:
                conn.execute(query, params)

    def _fetchone(self, query, params):
        with self._connect() as conn:
            return conn.execute(query, params).fetchone()

    def _fetchall(self, query, params):
        with self._connect() as conn:
            return conn.execute(query, params).fetchall()

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
