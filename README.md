# MAX Messenger Bonus Bot

HTTP-сервис для проверки бонусного баланса по номеру телефона через чат-бота MAX Messenger.

## Возможности

- Webhook-интеграция с [MAX Bot API](https://dev.max.ru/docs-api)
- Сценарий: запуск бота → кнопка «Поделиться номером телефона» → поиск в PostgreSQL → ответ с балансом
- Валидация контакта через HMAC-SHA256 (как в документации MAX)
- Prometheus-метрики на `/metrics`
- Health-check на `GET /`

## Архитектура

```
MAX platform-api.max.ru
        │ POST /webhook (Update)
        ▼
adapters/max/webhook.py → handlers.py → services/responses.py
        │                                      │
        └──── POST /messages ──────────────────┘
                                               ▼
                                    services/bonus_service.py → PostgreSQL
```

## Переменные окружения

| Переменная | Обязательна | Описание |
|------------|-------------|----------|
| `MAX_BOT_TOKEN` | да | Токен бота из MAX для партнёров |
| `MAX_WEBHOOK_SECRET` | да | Секрет 5–256 символов `[a-zA-Z0-9_-]` |
| `MAX_WEBHOOK_URL` | да (production) | HTTPS URL webhook, например `https://your-domain.com/webhook` |
| `MAX_API_URL` | нет | По умолчанию `https://platform-api.max.ru` |
| `DATABASE_URL` | да | PostgreSQL DSN |
| `PORT` | нет | Порт HTTP-сервера (по умолчанию `8000`) |
| `POOL_MIN_SIZE` | нет | Мин. размер пула БД (по умолчанию `1`) |
| `POOL_MAX_SIZE` | нет | Макс. размер пула БД (по умолчанию `10`) |

Пример `.env`:

```env
MAX_BOT_TOKEN=your_max_bot_token
MAX_WEBHOOK_SECRET=your_secret_123
MAX_WEBHOOK_URL=https://your-domain.com/webhook
MAX_API_URL=https://platform-api.max.ru
DATABASE_URL=postgresql://user:pass@localhost:5432/dbname
PORT=8000
```

## Запуск

### Локально

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000
```

### Docker

```bash
docker build -t u4s-max-bot .
docker run -p 8000:8000 --env-file .env u4s-max-bot
```

Production: reverse proxy с TLS на порту 443. MAX принимает webhook только по HTTPS с сертификатом от доверенного CA ([документация](https://dev.max.ru/docs-api/methods/POST/subscriptions)).

При старте приложение регистрирует подписку `POST /subscriptions` с типами событий `bot_started` и `message_created`.

## Примеры webhook MAX

### Входящий `bot_started`

```json
{
  "update_type": "bot_started",
  "timestamp": 1737500130100,
  "user": {"user_id": 12345, "first_name": "Иван", "is_bot": false}
}
```

Ответ сервера: `200 OK`. Бот отправляет приветствие с кнопкой `request_contact`.

### Входящий `message_created` (контакт)

```json
{
  "update_type": "message_created",
  "timestamp": 1737500200000,
  "message": {
    "sender": {"user_id": 12345, "first_name": "Иван"},
    "body": {
      "attachments": [{
        "type": "contact",
        "payload": {
          "vcf_info": "BEGIN:VCARD\\r\\nVERSION:3.0\\r\\nTEL;TYPE=cell:79991234567\\r\\nFN:Ivan\\r\\nEND:VCARD\\r\\n",
          "hash": "computed_hmac_hex"
        }
      }]
    }
  }
}
```

Заголовок запроса: `X-Max-Bot-Api-Secret: {MAX_WEBHOOK_SECRET}`

Ответ сервера: `200 OK`. Бот отправляет текст с балансом или «Бонусы не найдены».

### Исходящий ответ бота (через MAX API)

```json
{
  "message": {
    "body": {
      "text": "👋 Иван, у Вас накоплено бонусов 1250 рублей.\nВаш уровень лояльности — gold."
    }
  }
}
```

## SQL-изменения

Не требуются. Используются существующие таблицы `bonuses_balance` и `telegram_bot_usage_stats`.

## Тестирование

```bash
pytest tests/ -v
```

Ручная проверка:

1. Создать бота на [platform MAX](https://dev.max.ru/docs/chatbots/bots-create)
2. Задать env-переменные и запустить сервис с публичным HTTPS URL
3. Запустить бота в MAX → нажать «Поделиться контактом» → проверить ответ

## Требования

- Python 3.10+
- PostgreSQL с таблицей `bonuses_balance`
