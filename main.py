import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from urllib.parse import urlparse, urlunparse

from fastapi import FastAPI
from loguru import logger
from prometheus_fastapi_instrumentator import Instrumentator

from adapters.max.client import MaxApiClient
from adapters.max.webhook import register_max_routes
from config import get_settings
from services.bonus_service import BotService

UPDATE_DELIVERY_TIMEOUT_SECONDS = 15


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

settings = get_settings()


def ensure_webhook_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.path and parsed.path != "/":
        return url
    return urlunparse(parsed._replace(path="/webhook"))


async def initialize_max_bot(app: FastAPI) -> None:
    max_client: MaxApiClient = app.state.max_client

    try:
        async with asyncio.timeout(UPDATE_DELIVERY_TIMEOUT_SECONDS):
            await max_client.set_bot_commands()
    except TimeoutError:
        logger.error("Timed out while registering MAX bot commands")
    except Exception:
        logger.exception("Failed to register MAX bot commands")

    if not settings.max_webhook_url:
        logger.warning("MAX_WEBHOOK_URL is not set; webhook subscription skipped")
        app.state.update_delivery_mode = "webhook_url_missing"
        return

    webhook_url = ensure_webhook_url(str(settings.max_webhook_url))
    try:
        async with asyncio.timeout(UPDATE_DELIVERY_TIMEOUT_SECONDS):
            await max_client.subscribe_webhook(webhook_url, settings.max_webhook_secret)
        app.state.update_delivery_mode = "webhook"
        logger.info("MAX webhook mode active: {}", webhook_url)
    except TimeoutError:
        logger.error("Timed out while registering MAX webhook subscription")
        app.state.update_delivery_mode = "max_api_unreachable"
    except Exception:
        logger.exception("Failed to register MAX webhook subscription")
        app.state.update_delivery_mode = "failed"


@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_service = BotService(
        dsn=str(settings.database_url),
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
    )
    max_client = MaxApiClient(
        api_url=settings.max_api_url,
        bot_token=settings.max_bot_token,
    )

    app.state.bot_service = bot_service
    app.state.max_client = max_client
    app.state.settings = settings
    app.state.update_delivery_mode = "starting"

    await initialize_max_bot(app)

    yield

    logger.info("Shutting down: closing MAX client and DB pool")
    await max_client.close()
    await bot_service.close()


app = FastAPI(lifespan=lifespan)
Instrumentator().instrument(app).expose(app, endpoint="/metrics")
register_max_routes(app)


@app.get("/")
async def root():
    return {
        "status": "ok",
        "messenger": "max",
        "update_delivery_mode": getattr(app.state, "update_delivery_mode", "unknown"),
    }
