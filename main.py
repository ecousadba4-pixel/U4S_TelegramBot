import asyncio
import logging
import re
import sys
from contextlib import asynccontextmanager, suppress
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
from telethon import Button as TelethonButton, TelegramClient, connection, events
from telethon.tl import types as telethon_types
from urllib.parse import parse_qs, urlparse, urlunparse

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
UPDATE_DELIVERY_TIMEOUT_SECONDS = 15

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
    app.state.polling_task = None
    app.state.delivery_task = None
    app.state.mtproto_client = None
    app.state.update_delivery_mode = "starting"
    delivery_task = asyncio.create_task(
        initialize_update_delivery(app),
        name="telegram-update-delivery-init",
    )
    delivery_task.add_done_callback(log_delivery_task_result)
    app.state.delivery_task = delivery_task
    yield
    logger.info("Shutting down: stopping update delivery and closing pool")
    try:
        await stop_update_delivery(app)
    except Exception:
        logger.exception("Failed to stop update delivery cleanly")
    try:
        await bot_service.close()
    finally:
        await bot.session.close()


app = FastAPI(lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")


def ensure_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url
    return urlunparse(parsed._replace(path="/webhook"))


def parse_mtproxy_link(proxy_link: str) -> tuple[str, int, str]:
    parsed = urlparse(proxy_link)
    params = parse_qs(parsed.query)
    server = params.get("server", [None])[0]
    port_raw = params.get("port", [None])[0]
    secret = params.get("secret", [None])[0]
    if not server or not port_raw or not secret:
        raise ValueError("TELEGRAM_MTPROXY_LINK must include server, port and secret")
    try:
        port = int(port_raw)
    except ValueError as exc:
        raise ValueError("MTProto proxy port must be an integer") from exc
    return server, port, secret


def build_mtproto_client() -> TelegramClient:
    client_kwargs: dict[str, Any] = {}
    if settings.telegram_mtproxy_link:
        server, port, secret = parse_mtproxy_link(settings.telegram_mtproxy_link)
        logger.info("Using MTProto proxy {}:{}", server, port)
        client_kwargs["connection"] = connection.ConnectionTcpMTProxyRandomizedIntermediate
        client_kwargs["proxy"] = (server, port, secret)
    return TelegramClient(
        "mtproto-bot-session",
        settings.telegram_api_id,
        settings.telegram_api_hash,
        **client_kwargs,
    )


def log_polling_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.opt(exception=exc).error("Polling task stopped unexpectedly")


def log_delivery_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.opt(exception=exc).error("Update delivery init task stopped unexpectedly")


async def initialize_update_delivery(app: FastAPI) -> None:
    try:
        await start_update_delivery(app)
    except Exception:
        logger.exception("Failed to initialize update delivery")
        app.state.update_delivery_mode = "failed"


async def build_bonus_response(bot_service: BotService, user_id: int, phone_number: str) -> str:
    try:
        await bot_service.log_usage_stat(user_id=user_id, phone=phone_number, command="contact")
    except Exception as exc:
        logger.error("Failed to log usage stat for user {}: {}", user_id, exc)

    try:
        guest_info = await bot_service.get_guest_bonus(phone_number)
    except Exception as exc:
        logger.error(
            "Failed to fetch bonus info for phone {} (user_id={}): {}",
            phone_number,
            user_id,
            exc,
        )
        raise RuntimeError("Произошла ошибка при получении данных. Попробуйте позже.") from exc

    if not guest_info:
        return MSG_NO_BONUS

    bonus_amount = bot_service.format_bonus_amount(guest_info["bonus_balances"])
    response_text = MSG_BALANCE_TEMPLATE.format(
        first_name=guest_info["first_name"],
        amount=bonus_amount,
        level=guest_info["loyalty_level"],
    )
    if bonus_amount > 0:
        response_text += MSG_EXPIRY_TEMPLATE.format(date=guest_info["expire_date"])
    return response_text


def register_mtproto_handlers(app: FastAPI, client: TelegramClient) -> None:
    @client.on(events.NewMessage(incoming=True, pattern=r"^/start(?:@\w+)?(?:\s|$)"))
    async def mtproto_cmd_start(event: events.NewMessage.Event) -> None:
        await event.respond(
            MSG_START,
            buttons=[[TelethonButton.request_phone(BTN_SHARE_PHONE)]],
        )

    @client.on(events.NewMessage(incoming=True))
    async def mtproto_handle_contact(event: events.NewMessage.Event) -> None:
        media = event.message.media
        if not isinstance(media, telethon_types.MessageMediaContact):
            return

        sender = await event.get_sender()
        sender_id = getattr(sender, "id", None) or 0
        if media.user_id and sender_id and media.user_id != sender_id:
            await event.respond(MSG_INVALID_CONTACT)
            return

        phone_number = media.phone_number
        logger.info("Received MTProto contact from {} (user_id={})", phone_number, sender_id)
        bot_service = app.state.bot_service
        try:
            response_text = await build_bonus_response(
                bot_service=bot_service,
                user_id=int(sender_id),
                phone_number=phone_number,
            )
        except RuntimeError as exc:
            await event.respond(str(exc))
            return

        await event.respond(response_text)


async def start_mtproto_mode(app: FastAPI) -> None:
    logger.info("Starting MTProto transport")
    try:
        client = build_mtproto_client()
    except ValueError:
        logger.exception("Failed to parse MTProto proxy settings")
        app.state.update_delivery_mode = "mtproto_invalid_proxy"
        return

    register_mtproto_handlers(app, client)
    app.state.mtproto_client = client

    try:
        async with asyncio.timeout(UPDATE_DELIVERY_TIMEOUT_SECONDS):
            await client.start(bot_token=settings.telegram_bot_token)
        me = await client.get_me()
        logger.info("MTProto bot started as {}", getattr(me, "username", None) or getattr(me, "id", "unknown"))
        app.state.update_delivery_mode = "mtproto"
    except TimeoutError:
        logger.error("Timed out while starting MTProto transport")
        app.state.update_delivery_mode = "telegram_mtproto_unreachable"
    except Exception:
        logger.exception("Failed to start MTProto transport")
        app.state.update_delivery_mode = "telegram_mtproto_failed"


async def start_polling_mode(app: FastAPI, reason: str) -> None:
    logger.warning("Starting polling mode: {}", reason)
    try:
        async with asyncio.timeout(UPDATE_DELIVERY_TIMEOUT_SECONDS):
            await bot.delete_webhook(drop_pending_updates=False)
    except TimeoutError:
        logger.error(
            "Timed out while deleting webhook before polling startup; Telegram API is unreachable"
        )
        app.state.update_delivery_mode = "telegram_api_unreachable"
        return
    except Exception:
        logger.exception("Failed to delete webhook before polling startup")
        app.state.update_delivery_mode = "telegram_api_unreachable"
        return
    polling_task = asyncio.create_task(
        dp.start_polling(bot, handle_signals=False),
        name="telegram-bot-polling",
    )
    polling_task.add_done_callback(log_polling_task_result)
    app.state.polling_task = polling_task
    app.state.update_delivery_mode = "polling"


async def start_update_delivery(app: FastAPI) -> None:
    if settings.telegram_transport == "mtproto":
        await start_mtproto_mode(app)
        return

    if settings.webhook_url:
        webhook_url = ensure_webhook_url(str(settings.webhook_url))
        try:
            logger.info("Starting webhook mode: {}", webhook_url)
            async with asyncio.timeout(UPDATE_DELIVERY_TIMEOUT_SECONDS):
                await bot.set_webhook(webhook_url)
                webhook_info = await bot.get_webhook_info()
            logger.info(
                "Webhook active: url={} pending_updates={} last_error_message={}",
                webhook_info.url,
                webhook_info.pending_update_count,
                webhook_info.last_error_message or "none",
            )
            app.state.update_delivery_mode = "webhook"
            return
        except Exception:
            logger.exception("Failed to set webhook")
            await start_polling_mode(app, "webhook setup failed")
            return

    await start_polling_mode(app, "WEBHOOK_URL is not set")


async def stop_update_delivery(app: FastAPI) -> None:
    delivery_task = getattr(app.state, "delivery_task", None)
    if delivery_task and not delivery_task.done():
        logger.info("Cancelling update delivery init task")
        delivery_task.cancel()
        with suppress(asyncio.CancelledError):
            await delivery_task
    app.state.delivery_task = None

    mtproto_client = getattr(app.state, "mtproto_client", None)
    if mtproto_client is not None:
        logger.info("Disconnecting MTProto client")
        await mtproto_client.disconnect()
        app.state.mtproto_client = None

    polling_task = getattr(app.state, "polling_task", None)
    mode = getattr(app.state, "update_delivery_mode", "unknown")
    if polling_task is None:
        logger.info("Update delivery stopped in {} mode; webhook registration kept as-is", mode)
        return

    logger.info("Stopping polling task")
    polling_task.cancel()
    with suppress(asyncio.CancelledError):
        await polling_task
    app.state.polling_task = None

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
    logger.info("Received contact from {} (user_id={})", phone_number, user_id)
    bot_service = app.state.bot_service
    try:
        response_text = await build_bonus_response(
            bot_service=bot_service,
            user_id=user_id,
            phone_number=phone_number,
        )
    except RuntimeError as exc:
        await message.answer(str(exc))
        return

    try:
        await message.answer(response_text)
    except Exception as exc:
        logger.error("Failed to send response to user {}: {}", user_id, exc)


@app.post("/webhook")
async def telegram_webhook(request: Request):
    if settings.telegram_transport != "bot_api":
        return Response(status_code=status.HTTP_404_NOT_FOUND)
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
    return {
        "status": "ok",
        "telegram_transport": settings.telegram_transport,
        "update_delivery_mode": getattr(app.state, "update_delivery_mode", "unknown"),
    }
