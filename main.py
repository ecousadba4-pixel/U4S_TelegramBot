import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

import asyncpg
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from aiogram.types import KeyboardButton, ReplyKeyboardMarkup, Update
from dateutil.relativedelta import relativedelta
from fastapi import FastAPI, Request, Response, status
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator  # ✨ добавили
from json import JSONDecodeError
from urllib.parse import urlparse, urlunparse

from config import get_settings


class InterceptHandler(logging.Handler):
    """Redirect standard logging records to Loguru."""

    def emit(self, record: logging.LogRecord) -> None:  # type: ignore[override]
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno
        frame, depth = logging.currentframe(), 2
        while frame and frame.f_code.co_filename == logging.__file__:
            frame = frame.f_back
            depth += 1
        logger.opt(depth=depth, exception=record.exc_info).log(level, record.getMessage())


logging.basicConfig(handlers=[InterceptHandler()], level=0)
logger.remove()
logger.add(
    sys.stdout,
    level="INFO",
    format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {name}:{function}:{line} - {message}",
    enqueue=True,
    backtrace=True,
    diagnose=False,
)

# --- Константы ---

# Названия таблиц и столбцов
TABLE_BONUSES_BALANCE = "bonuses_balance"
COL_PHONE = "phone"
COL_FIRST_NAME = "first_name"
COL_LOYALTY_LEVEL = "loyalty_level"
COL_BONUS_BALANCES = "bonus_balances"
COL_LAST_DATE_VISIT = "last_date_visit"

TABLE_TELEGRAM_BOT_STATS = "telegram_bot_usage_stats"
COL_USER_ID = "user_id"
COL_PHONE_STATS = "phone"
COL_COMMAND = "command"

# Тексты сообщений и кнопок
MSG_START = "Нажмите кнопку Поделиться номером телефона внизу, чтобы узнать бонусный баланс."
BTN_SHARE_PHONE = "Поделиться номером телефона"
MSG_INVALID_CONTACT = "❌ Вы можете проверить информацию только для своего номера телефона."
MSG_NO_BONUS = "Бонусы для указанного номера не найдены."
MSG_BALANCE_TEMPLATE = "👋 {first_name}, у Вас накоплено бонусов {amount} рублей.\nВаш уровень лояльности — {level}."
MSG_EXPIRY_TEMPLATE = "\nСрок действия бонусов: до {date}."

# SQL запросы
SQL_FETCH_USER = f"""
SELECT {COL_FIRST_NAME}, {COL_LOYALTY_LEVEL}, {COL_BONUS_BALANCES}, {COL_LAST_DATE_VISIT}
FROM {TABLE_BONUSES_BALANCE}
WHERE {COL_PHONE} = $1
"""

SQL_LOG_USAGE = f"""
INSERT INTO {TABLE_TELEGRAM_BOT_STATS} ({COL_USER_ID}, {COL_PHONE_STATS}, {COL_COMMAND})
VALUES ($1, $2, $3)
"""

# --- /Константы ---


settings = get_settings()

bot = Bot(token=settings.telegram_bot_token)
dp = Dispatcher()


class BotService:
    def __init__(self, dsn: str, min_size: int, max_size: int):
        self._dsn = dsn
        self._min_size = min_size
        self._max_size = max_size
        self._pool: Optional[asyncpg.Pool] = None
        self._pool_lock = asyncio.Lock()

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
        digits = ''.join(ch for ch in (phone or "") if ch.isdigit())
        return digits[-10:] if len(digits) >= 10 else digits

    async def fetch_user_row(self, phone_number: str) -> Optional[asyncpg.Record]:
        """Получение строки пользователя по телефону."""
        clean_phone = self.normalize_phone(phone_number)
        if not clean_phone:
            return None
        query = SQL_FETCH_USER
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                return await conn.fetchrow(query, clean_phone)
        except RuntimeError:
            raise
        except Exception:
            logger.exception("Database query failed")
            return None

    def parse_guest_info(self, row: Optional[asyncpg.Record]) -> Optional[dict[str, Any]]:
        """Конвертация строки БД в user dict для выдачи в боте."""
        if not row:
            return None
        row_dict = dict(row)
        last_visit: Optional[datetime] = row_dict.get(COL_LAST_DATE_VISIT)
        if not last_visit:
            expire_date = "Неизвестно"
        else:
            try:
                expire_date = (last_visit + relativedelta(months=12)).strftime("%d.%m.%Y")
            except Exception as e:
                logger.warning(f"Failed to calculate expire date for {last_visit}: {e}")
                expire_date = "Неизвестно"
        return {
            "first_name": row_dict.get(COL_FIRST_NAME) or "Гость",
            "loyalty_level": row_dict.get(COL_LOYALTY_LEVEL) or "—",
            "bonus_balances": row_dict.get(COL_BONUS_BALANCES) or 0,
            "expire_date": expire_date,
        }

    async def get_guest_bonus(self, phone_number: str) -> Optional[dict[str, Any]]:
        """Единая точка входа во всю бизнес-логику выдачи бонусов."""
        if not phone_number:
            return None
        row = await self.fetch_user_row(phone_number)
        return self.parse_guest_info(row)

    async def log_usage_stat(self, user_id: int, phone: str, command: str) -> None:
        """Запись события использования бота."""
        query = SQL_LOG_USAGE
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                await conn.execute(query, user_id, phone, command)
        except Exception:
            logger.exception("Failed to log usage stat")

    @staticmethod
    def format_bonus_amount(value: Any) -> int:
        """Безопасное преобразование бонусного баланса к int."""
        try:
            return int(Decimal(str(value)))
        except (InvalidOperation, TypeError, ValueError):
            logger.warning("Could not convert bonus_balances '%s' to int", value)
            return 0


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_service = BotService(
        dsn=str(settings.database_url),
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
    )
    app.state.bot_service = bot_service
    app.state.settings = settings

    if settings.webhook_url:
        try:
            webhook_url = ensure_webhook_url(str(settings.webhook_url))
            logger.info("Setting Telegram webhook to %s", webhook_url)
            await bot.set_webhook(webhook_url)
            logger.info("Webhook set")
        except Exception:
            logger.exception("Failed to set webhook (continuing without webhook)")
    else:
        logger.warning("WEBHOOK_URL is not set; bot will not receive updates.")
    yield
    logger.info("Shutting down: deleting webhook and closing pool")
    try:
        await bot.delete_webhook()
    except Exception:
        logger.exception("Failed to delete webhook (ignoring)")
    await bot_service.close()


app = FastAPI(lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


def ensure_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url
    return urlunparse(parsed._replace(path="/webhook"))

@dp.message(CommandStart())
async def cmd_start(message: types.Message):
    keyboard = ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BTN_SHARE_PHONE, request_contact=True)]
        ],
        resize_keyboard=True
    )
    await message.answer(MSG_START, reply_markup=keyboard)


@dp.message(F.contact)
async def handle_contact(message: types.Message):
    # --- ПРОВЕРКА: принадлежит ли контакт отправителю ---
    if message.contact.user_id != message.from_user.id:
        await message.answer(MSG_INVALID_CONTACT)
        return
    # --- КОНЕЦ ПРОВЕРКИ ---

    phone_number = message.contact.phone_number
    user_id = message.from_user.id
    logger.info("Received contact from %s (user_id=%s)", phone_number, user_id)
    bot_service = app.state.bot_service

    # Записать событие
    try:
        await bot_service.log_usage_stat(user_id=user_id, phone=phone_number, command="contact")
    except Exception as e:  # Логируем ошибку логирования, но не прерываем основной процесс
        logger.error(f"Failed to log usage stat for user {user_id}: {e}")

    try:
        guest_info = await bot_service.get_guest_bonus(phone_number)
    except Exception as e:
        logger.error(f"Failed to fetch bonus info for phone {phone_number} (user_id={user_id}): {e}")
        await message.answer("Произошла ошибка при получении данных. Попробуйте позже.")
        return

    if not guest_info:
        await message.answer(MSG_NO_BONUS)
        return

    bonus_amount = bot_service.format_bonus_amount(guest_info['bonus_balances'])

    response_text = MSG_BALANCE_TEMPLATE.format(
        first_name=guest_info['first_name'],
        amount=bonus_amount,
        level=guest_info['loyalty_level']
    )
    if bonus_amount > 0:
        response_text += MSG_EXPIRY_TEMPLATE.format(date=guest_info['expire_date'])

    try:
        await message.answer(response_text)
    except Exception as e:
        logger.error(f"Failed to send response to user {user_id}: {e}")


@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        data = await request.json()
    except JSONDecodeError:
        logger.warning("Non-JSON body received on /webhook")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    logger.info("Webhook received: %s", data.get("message") or data.get("update_id"))
    try:
        update = Update(**data)
    except Exception:
        logger.exception("Failed to parse update")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)
    try:
        await dp.feed_update(bot, update)
    except Exception:
        logger.exception("Failed to feed update")
    return Response(status_code=status.HTTP_200_OK)


@app.get("/")
async def root():
    return {"status": "ok"}
