"""Телеграм-бот: команды и кнопки для обобщённой сводки по погоде и морю."""

from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .config import Settings
from .formatting import (
    SECTION_CURRENT,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_MARINE,
    SECTION_NOTES,
    SECTION_SEA_FORECAST,
    SECTION_SOURCES,
    format_report,
)
from .lockfile import AlreadyRunningError, acquire_lock
from .notify import Notifier, build_notifier
from .service import ReportCache
from .subscribers import StateStore
from .timeutil import local_now

logger = logging.getLogger(__name__)

router = Router()

WEATHER_SECTIONS = (SECTION_CURRENT, SECTION_FORECAST, SECTION_NOTES)
SEA_SECTIONS = (SECTION_MARINE, SECTION_SEA_FORECAST, SECTION_NOTES)
EVENT_SECTIONS = (SECTION_CURRENT, SECTION_EVENTS, SECTION_NOTES)
FULL_SECTIONS = (
    SECTION_CURRENT,
    SECTION_MARINE,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_SEA_FORECAST,
    SECTION_SOURCES,
    SECTION_NOTES,
)

MAX_MESSAGE_LENGTH = 3900

HELP_TEXT = (
    "Что я умею:\n"
    "/weather — погода сейчас и прогноз по дням\n"
    "/sea — состояние моря: вода, волна, зыбь и прогноз волнения\n"
    "/events — что опасного ждёт в ближайшие часы: дождь, гроза, ветер, "
    "волнение, туман, заморозки, землетрясения\n"
    "/full — полная обобщённая сводка с разбором площадок и расхождений\n"
    "/subscribe — подписаться на утреннюю сводку и оповещения о событиях\n"
    "/unsubscribe — отключить рассылку\n"
    "/status — что с рассылкой и подпиской\n"
    "/whoami — узнать свой chat_id (для белого списка)\n\n"
    "Данные собираются параллельно с Open-Meteo, OpenWeatherMap, wttr.in, "
    "Open-Meteo Marine API и USGS (землетрясения). Числа в сводке — медиана "
    "по площадкам, отдельно показан разброс, если источники расходятся."
)


def build_keyboard() -> InlineKeyboardMarkup:
    """Кнопки для быстрого запроса разделов сводки."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="🌤 Погода", callback_data="report:weather"),
                InlineKeyboardButton(text="🌊 Море", callback_data="report:sea"),
            ],
            [InlineKeyboardButton(text="📋 Полная сводка", callback_data="report:full")],
        ]
    )


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    """Приветствие и кнопки."""
    await message.answer(
        f"Привет! Я собираю погоду и состояние моря для точки "
        f"{settings.city} ({settings.coords_label}) с нескольких площадок "
        f"и выдаю обобщённую сводку.\n\n{HELP_TEXT}",
        reply_markup=build_keyboard(),
    )


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    """Справка по командам."""
    if not _is_allowed(message, settings):
        return
    await message.answer(HELP_TEXT, reply_markup=build_keyboard())


@router.message(Command("whoami"))
async def cmd_whoami(message: Message) -> None:
    """Показывает chat_id — удобно для TELEGRAM_ALLOWED_CHAT_IDS."""
    user_id = message.from_user.id if message.from_user else "нет"
    await message.answer(f"chat_id: {message.chat.id}\nuser_id: {user_id}")


@router.message(Command("weather"))
async def cmd_weather(
    message: Message, settings: Settings, cache: ReportCache
) -> None:
    """Погода сейчас и прогноз по дням."""
    if not _is_allowed(message, settings):
        return
    await _send_sections(message, cache, WEATHER_SECTIONS)


@router.message(Command("sea"))
async def cmd_sea(message: Message, settings: Settings, cache: ReportCache) -> None:
    """Температура воды, волнение и прогноз по морю."""
    if not _is_allowed(message, settings):
        return
    await _send_sections(message, cache, SEA_SECTIONS)


@router.message(Command("full"))
async def cmd_full(message: Message, settings: Settings, cache: ReportCache, notifier: Notifier) -> None:
    """Полная сводка со всеми разделами, включая события."""
    if not _is_allowed(message, settings):
        return
    await _send_sections(message, cache, FULL_SECTIONS, notifier)


@router.message(Command("events"))
async def cmd_events(
    message: Message, settings: Settings, cache: ReportCache, notifier: Notifier
) -> None:
    """Список событий в ближайшем прогнозе."""
    if not _is_allowed(message, settings):
        return
    try:
        report = await cache.get()
    except Exception as exc:  # noqa: BLE001 - сообщаем пользователю о любой ошибке
        logger.exception("Не удалось собрать сводку для списка событий")
        await message.answer(f"Не удалось собрать сводку: {exc}")
        return

    moment = local_now(settings.timezone)
    lines = [
        f"События до {settings.notify_event_horizon_hours} ч вперёд "
        f"({settings.timezone_label}):",
        "",
        notifier.events_text(report, moment),
        "",
        f"Оповещения приходят за {settings.notify_event_lead_minutes} мин до начала.",
    ]
    for chunk in _split("\n".join(lines)):
        await message.answer(chunk)


@router.message(Command("subscribe"))
async def cmd_subscribe(message: Message, settings: Settings, store: StateStore) -> None:
    """Подписка на утреннюю сводку и оповещения."""
    if not _is_allowed(message, settings):
        return
    added = store.subscribe(message.chat.id)
    if added:
        logger.info("Новая подписка: chat_id=%s", message.chat.id)
    prefix = "Подписка оформлена." if added else "Вы уже подписаны."
    await message.answer(
        f"{prefix}\n\n"
        f"• Утренняя сводка — ежедневно в {settings.notify_daily_time} "
        f"({settings.timezone_label}).\n"
        f"• Оповещения — за {settings.notify_event_lead_minutes} мин до дождя, "
        f"грозы, града, сильного ветра, тумана, жары, заморозков, резкого "
        f"похолодания, шторма и свежих землетрясений.\n\n"
        f"Отключить: /unsubscribe"
    )


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(message: Message, settings: Settings, store: StateStore) -> None:
    """Отписка от рассылки."""
    if not _is_allowed(message, settings):
        return
    removed = store.unsubscribe(message.chat.id)
    if removed:
        logger.info("Отписка: chat_id=%s", message.chat.id)
    text = (
        "Рассылка отключена."
        if removed
        else "Вы и так не были подписаны. Включить: /subscribe"
    )
    await message.answer(text)


@router.message(Command("status"))
async def cmd_status(message: Message, settings: Settings, store: StateStore) -> None:
    """Состояние рассылки и подписки."""
    if not _is_allowed(message, settings):
        return
    moment = local_now(settings.timezone)
    alerts_today = store.state.alerts.get(moment.date().isoformat(), [])
    daily_mark = store.state.daily.get(moment.date().isoformat(), "ещё не отправлялась")
    subscribers = store.subscribers()
    lines = [
        "Рассылка:",
        f"— ежедневная сводка: {settings.notify_daily_time} ({settings.timezone_label})",
        f"— сегодня: {daily_mark}",
        f"— оповещения: {'включены' if settings.notify_events_enabled else 'выключены'}, "
        f"за {settings.notify_event_lead_minutes} мин, горизонт "
        f"{settings.notify_event_horizon_hours} ч",
        f"— отправлено оповещений сегодня: {len(alerts_today)}",
        f"— подписчиков: {len(subscribers)}",
        f"— вы подписаны: {'да' if store.is_subscribed(message.chat.id) else 'нет'}",
        f"— файл состояния: {settings.state_file}",
    ]
    await message.answer("\n".join(lines))


@router.callback_query(F.data.startswith("report:"))
async def on_report_button(
    callback: CallbackQuery, settings: Settings, cache: ReportCache, notifier: Notifier
) -> None:
    """Обработка инлайн-кнопок."""
    sections_by_action = {
        "weather": WEATHER_SECTIONS,
        "sea": SEA_SECTIONS,
        "full": FULL_SECTIONS,
    }
    action = (callback.data or "").split(":", 1)[-1]
    sections = sections_by_action.get(action)
    if callback.message is None or sections is None:
        await callback.answer("Раздел не найден", show_alert=False)
        return
    if not settings.is_allowed_chat(callback.message.chat.id):
        await callback.answer("Доступ ограничен", show_alert=True)
        return

    await callback.answer("Собираю данные…")
    await _send_sections(callback.message, cache, sections, notifier)


@router.message(F.text.startswith("/"))
async def on_unknown_command(message: Message) -> None:
    """Подсказка при неизвестной команде."""
    await message.answer(f"Не знаю такой команды.\n\n{HELP_TEXT}")


def _is_allowed(message: Message, settings: Settings) -> bool:
    """Проверка необязательного белого списка chat_id."""
    if settings.is_allowed_chat(message.chat.id):
        return True
    logger.warning("Отклонён запрос от chat_id=%s", message.chat.id)
    return False


async def _send_sections(
    message: Message,
    cache: ReportCache,
    sections: tuple[str, ...],
    notifier: Notifier | None = None,
) -> None:
    """Формирует сводку по разделам и отправляет её (с разбивкой по 3900 символов)."""
    try:
        await message.bot.send_chat_action(message.chat.id, "typing")
        report = await cache.get()
    except Exception as exc:  # noqa: BLE001 - сообщаем пользователю о любой ошибке
        logger.exception("Не удалось собрать сводку")
        await message.answer(f"Не удалось собрать сводку: {exc}")
        return

    if notifier is None:
        text = format_report(report, sections)
    else:
        text = format_report(
            report,
            sections,
            events=notifier.events(report),
            zone_name=notifier.settings.timezone,
        )

    for chunk in _split(text):
        await message.answer(chunk)


def _split(text: str, limit: int = MAX_MESSAGE_LENGTH) -> list[str]:
    """Делит текст на части не длиннее limit, по возможности по строкам."""
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) > limit:
            if current:
                chunks.append(current)
            current = line if len(line) <= limit else line[:limit]
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def create_dispatcher(
    settings: Settings,
    cache: ReportCache | None = None,
    notifier: Notifier | None = None,
) -> Dispatcher:
    """Собирает Dispatcher с настройками, кэшем сводки и рассылкой."""
    report_cache = cache or ReportCache(settings)
    dispatcher = Dispatcher()
    dispatcher["settings"] = settings
    dispatcher["cache"] = report_cache
    notifier = notifier or build_notifier(settings, cache=report_cache)
    dispatcher["notifier"] = notifier
    dispatcher["store"] = notifier.store
    dispatcher.include_router(router)
    return dispatcher


async def main() -> None:
    """Запуск бота в режиме long polling вместе с рассылкой."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        raise SystemExit(
            "Не задан TELEGRAM_BOT_TOKEN. Скопируйте .env.example в .env и заполните ключи."
        )

    try:
        lock = acquire_lock(settings.state_file.with_name("bot.lock"))
    except AlreadyRunningError as exc:
        raise SystemExit(str(exc)) from exc

    bot = Bot(token=settings.telegram_bot_token)
    cache = ReportCache(settings)

    async def send(chat_id: int, text: str) -> None:
        """Отправка сообщения рассылки в Telegram."""
        await bot.send_message(chat_id, text)

    notifier = build_notifier(settings, sender=send, cache=cache)
    dispatcher = create_dispatcher(settings, cache=cache, notifier=notifier)
    logger.info(
        "Бот запущен: %s (%s); площадки: Open-Meteo, OpenWeatherMap, wttr.in, "
        "Open-Meteo Marine, USGS",
        settings.city,
        settings.coords_label,
    )
    notification_task = asyncio.create_task(notifier.run())
    try:
        await bot.delete_webhook(drop_pending_updates=True)
        await dispatcher.start_polling(
            bot, allowed_updates=dispatcher.resolve_used_update_types()
        )
    finally:
        notification_task.cancel()
        with suppress(asyncio.CancelledError):
            await notification_task
        await bot.session.close()
        lock.close()

