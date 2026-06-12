"""MAX webhook FastAPI route."""

from __future__ import annotations

import asyncio
from json import JSONDecodeError
from typing import TYPE_CHECKING

from fastapi import APIRouter, Request, Response, status
from loguru import logger

from adapters.max.client import WEBHOOK_SECRET_HEADER
from adapters.max.handlers import handle_update

if TYPE_CHECKING:
    from fastapi import FastAPI

router = APIRouter()


def _log_update_task_result(task: asyncio.Task) -> None:
    if task.cancelled():
        return
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    if exc:
        logger.opt(exception=exc).error("Failed to process MAX update")


@router.post("/webhook")
async def max_webhook(request: Request) -> Response:
    settings = request.app.state.settings
    expected_secret = settings.max_webhook_secret
    received_secret = request.headers.get(WEBHOOK_SECRET_HEADER)

    if expected_secret and received_secret != expected_secret:
        logger.warning("Rejected webhook: invalid {}", WEBHOOK_SECRET_HEADER)
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        data = await request.json()
    except JSONDecodeError:
        logger.warning("Non-JSON body received on /webhook")
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    if not isinstance(data, dict):
        return Response(status_code=status.HTTP_400_BAD_REQUEST)

    logger.info("Webhook received: update_type={}", data.get("update_type"))

    task = asyncio.create_task(
        handle_update(
            data,
            max_client=request.app.state.max_client,
            bot_service=request.app.state.bot_service,
            bot_token=settings.max_bot_token,
        ),
        name=f"max-update-{data.get('update_type', 'unknown')}",
    )
    task.add_done_callback(_log_update_task_result)

    return Response(status_code=status.HTTP_200_OK)


def register_max_routes(app: FastAPI) -> None:
    app.include_router(router)
