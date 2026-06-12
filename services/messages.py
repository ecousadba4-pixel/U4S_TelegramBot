"""User-facing message texts and button labels."""

CMD_START_NAME = "start"
CMD_START_DESCRIPTION = "Проверить баланс бонусов"
CALLBACK_START_PAYLOAD = "start"
CALLBACK_CHECK_BONUS_PAYLOAD = "check_bonus"

MSG_START = (
    "Нажмите «Поделиться номером телефона», чтобы узнать актуальный бонусный баланс."
)
BTN_CHECK_BONUS = "Проверить бонусы"
BTN_SHARE_PHONE = "Поделиться номером телефона"
MSG_INVALID_CONTACT = (
    "❌ Вы можете проверить информацию только для своего номера телефона."
)
MSG_NO_BONUS = "Бонусы для указанного номера не найдены."
MSG_BALANCE_TEMPLATE = (
    "👋 {first_name}, у Вас накоплено бонусов {amount} рублей.\n"
    "Ваш уровень лояльности — {level}."
)
MSG_EXPIRY_TEMPLATE = "\nСрок действия бонусов: до {date}."
