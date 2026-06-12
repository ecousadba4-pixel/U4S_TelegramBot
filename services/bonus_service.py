"""Bonus lookup service backed by PostgreSQL."""

from __future__ import annotations

import asyncio
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import asyncpg
from dateutil.relativedelta import relativedelta
from loguru import logger

TABLE_BONUSES_BALANCE = "bonuses_balance"
COL_PHONE = "phone"
COL_FIRST_NAME = "first_name"
COL_LOYALTY_LEVEL = "loyalty_level"
COL_BONUS_BALANCES = "bonus_balances"
COL_LAST_DATE_VISIT = "last_date_visit"

# Legacy table name in PostgreSQL; schema is not renamed to avoid DB migration.
TABLE_BOT_USAGE_STATS = "telegram_bot_usage_stats"
COL_USER_ID = "user_id"
COL_PHONE_STATS = "phone"
COL_COMMAND = "command"

SQL_FETCH_USER = f"""
SELECT {COL_FIRST_NAME}, {COL_LOYALTY_LEVEL}, {COL_BONUS_BALANCES}, {COL_LAST_DATE_VISIT}
FROM {TABLE_BONUSES_BALANCE}
WHERE {COL_PHONE} = $1
"""

SQL_LOG_USAGE = f"""
INSERT INTO {TABLE_BOT_USAGE_STATS} ({COL_USER_ID}, {COL_PHONE_STATS}, {COL_COMMAND})
VALUES ($1, $2, $3)
"""


class BotService:
    def __init__(self, dsn: str, min_size: int, max_size: int):
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None
        self._pool_lock = asyncio.Lock()
        self._verified_phones: dict[int, str] = {}

    def _pool_active(self) -> bool:
        return bool(
            self._pool
            and not getattr(self._pool, "_closing", False)
            and not getattr(self._pool, "_closed", False)
        )

    async def _ensure_pool(self) -> asyncpg.Pool:
        if self._pool_active():
            return self._pool

        async with self._pool_lock:
            if self._pool_active():
                return self._pool
            logger.info("Creating DB pool")
            try:
                self._pool = await asyncpg.create_pool(
                    self._dsn,
                    min_size=self._min_size,
                    max_size=self._max_size,
                )
                logger.info("DB pool created")
            except Exception as exc:
                logger.exception("Failed to create DB pool")
                self._pool = None
                raise RuntimeError("Database pool is unavailable") from exc
            return self._pool

    async def close(self) -> None:
        async with self._pool_lock:
            if not self._pool_active():
                return
            try:
                await self._pool.close()
                logger.info("DB pool closed")
            except Exception:
                logger.exception("Failed to close DB pool")
            finally:
                self._pool = None

    @staticmethod
    def normalize_phone(phone: str) -> str:
        digits = "".join(ch for ch in (phone or "") if ch.isdigit())
        return digits[-10:] if len(digits) >= 10 else digits

    async def fetch_user_row(self, phone_number: str) -> Optional[asyncpg.Record]:
        clean_phone = self.normalize_phone(phone_number)
        if not clean_phone:
            return None
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                return await conn.fetchrow(SQL_FETCH_USER, clean_phone)
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Database query failed")
            return None

    def parse_guest_info(self, row: Optional[asyncpg.Record]) -> Optional[dict[str, Any]]:
        if not row:
            return None
        row_dict = dict(row)
        last_visit: Optional[datetime] = row_dict.get(COL_LAST_DATE_VISIT)
        if not last_visit:
            expire_date = "Неизвестно"
        else:
            try:
                expire_date = (last_visit + relativedelta(months=12)).strftime("%d.%m.%Y")
            except Exception as exc:
                logger.warning("Failed to calculate expire date for {}: {}", last_visit, exc)
                expire_date = "Неизвестно"
        return {
            "first_name": row_dict.get(COL_FIRST_NAME) or "Гость",
            "loyalty_level": row_dict.get(COL_LOYALTY_LEVEL) or "—",
            "bonus_balances": row_dict.get(COL_BONUS_BALANCES) or 0,
            "expire_date": expire_date,
        }

    async def get_guest_bonus(self, phone_number: str) -> Optional[dict[str, Any]]:
        if not phone_number:
            return None
        row = await self.fetch_user_row(phone_number)
        return self.parse_guest_info(row)

    async def log_usage_stat(self, user_id: int, phone: str, command: str) -> None:
        clean_phone = self.normalize_phone(phone)
        if not clean_phone:
            logger.warning("Skip usage stat for user {}: empty phone", user_id)
            return
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute(SQL_LOG_USAGE, user_id, clean_phone, command)
            logger.info(
                "Usage stat logged: user_id={} phone={} command={}",
                user_id,
                clean_phone,
                command,
            )
        except Exception:
            logger.exception("Failed to log usage stat")

    def remember_verified_phone(self, user_id: int, phone: str) -> None:
        self._verified_phones[user_id] = phone

    def get_verified_phone(self, user_id: int) -> Optional[str]:
        return self._verified_phones.get(user_id)

    @staticmethod
    def format_bonus_amount(value: Any) -> int:
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning("Could not convert bonus_balances '{}' to int", value)
            return 0


async def get_bonus_by_phone(
    bot_service: BotService,
    phone: str,
) -> Optional[dict[str, Any]]:
    """Return guest bonus info for the given phone number."""
    return await bot_service.get_guest_bonus(phone)
