#!/usr/bin/env python3
"""Точка входа телеграм-бота.

Запуск:                 python3 run_bot.py
Проверка токена:        python3 run_bot.py --check
События в консоль:      python3 run_bot.py --events
Прогон рассылки:        python3 run_bot.py --notify-dry
Проверка отправки:      python3 run_bot.py --notify-test
"""

from __future__ import annotations

import argparse
import asyncio
import sys

import httpx

from app.config import Settings
from app.events import detect_events
from app.notify import build_notifier
from app.service import fetch_report
from app.timeutil import local_now


async def check_token() -> int:
    """Проверяет TELEGRAM_BOT_TOKEN методом getMe."""
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN не задан — заполните .env (см. .env.example)")
        return 2

    try:
        async with httpx.AsyncClient(timeout=settings.request_timeout) as client:
            response = await client.get(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getMe"
            )
    except httpx.RequestError as exc:
        print(f"Не удалось связаться с Telegram API: {exc}")
        return 3

    try:
        payload = response.json()
    except ValueError:
        print(f"Telegram вернул не-JSON ответ (HTTP {response.status_code})")
        return 3

    if payload.get("ok"):
        user = payload["result"]
        print(f"Токен рабочий: @{user.get('username')} (id {user.get('id')})")
        return 0
    print(f"Токен не принят: {payload.get('description', response.status_code)}")
    return 1


async def run_bot() -> None:
    """Запускает polling (импорт aiogram — только в этом режиме)."""
    from app.bot import main as bot_main

    await bot_main()


async def show_events() -> int:
    """Печатает события в окне прогноза без обращения к Telegram."""
    settings = Settings.from_env()
    report = await fetch_report(settings)
    events = detect_events(report, settings)
    if not events:
        print("Событий в ближайшем прогнозе нет.")
        return 0

    moment = local_now(settings.timezone)
    print(
        f"{settings.city}: события на {settings.notify_event_horizon_hours} ч "
        f"вперёд (сейчас {moment:%d.%m.%Y %H:%M}, {settings.timezone_label})"
    )
    for event in events:
        start = event.window_label(settings.timezone)
        print(f"[{event.level_label:^7}] {event.kind:<12} {start:<20} {event.summary}")
        if event.details:
            print(f"{'':>22}{event.details}")
    print(f"\nВсего событий: {len(events)}")
    return 0


async def notify_dry_run() -> int:
    """Один проход рассылки в сухом режиме: показывает, что было бы отправлено."""
    settings = Settings.from_env()
    notifier = build_notifier(settings)
    subscribers = notifier.subscribers
    if not subscribers:
        print(
            "Подписчиков нет. Отправьте бота в чат и вызовите /subscribe "
            f"(либо задайте TELEGRAM_ALLOWED_CHAT_IDS). Файл: {settings.state_file}"
        )
        return 1

    moment = local_now(settings.timezone)
    will_send_daily = notifier.daily_due(moment)
    delivered = await notifier.tick(now=moment, mark=False)
    print(f"Подписчики: {', '.join(str(item) for item in subscribers)}")
    print(
        f"Утренняя сводка в {settings.notify_daily_time} "
        f"({settings.timezone_label}): "
        f"{'отправлена' if will_send_daily else 'не по расписанию'}"
    )
    if delivered:
        for delivery in delivered:
            print(f"— {delivery.kind} → chat_id {delivery.chat_id}: {delivery.text.splitlines()[0]}")
    else:
        print("— новых отправок нет")
    return 0


async def notify_test() -> int:
    """Отправляет подписчикам проверочное сообщение через Telegram."""
    settings = Settings.from_env()
    if not settings.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN не задан — отправка невозможна")
        return 2

    from aiogram import Bot

    bot = Bot(token=settings.telegram_bot_token)
    notifier = build_notifier(settings, sender=_telegram_sender(bot))
    try:
        delivered = await notifier.send_test()
    finally:
        await bot.session.close()

    if not delivered:
        print(
            "Подписчиков нет: отправьте бота в чат и вызовите /subscribe "
            "или заполните TELEGRAM_ALLOWED_CHAT_IDS"
        )
        return 1
    print(
        f"Проверочное сообщение отправлено: "
        f"{', '.join(str(item.chat_id) for item in delivered)}"
    )
    return 0


def _telegram_sender(bot):
    """Оборачивает отправку Rich-сообщения (с откатом на текст) для рассылки."""
    from app.rich import send_rich

    async def sender(chat_id: int, text: str) -> None:
        await send_rich(bot, chat_id, text)

    return sender


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Телеграм-бот погоды для Адлера: обобщённая сводка с нескольких площадок."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="проверить TELEGRAM_BOT_TOKEN через getMe и выйти",
    )
    parser.add_argument(
        "--events",
        action="store_true",
        help="напечатать события ближайших часов и выйти (без Telegram)",
    )
    parser.add_argument(
        "--notify-dry",
        action="store_true",
        help="один проход рассылки без отправки: показать, что ушло бы подписчикам",
    )
    parser.add_argument(
        "--notify-test",
        action="store_true",
        help="отправить подписчикам проверочное сообщение",
    )
    args = parser.parse_args()

    if args.check:
        return asyncio.run(check_token())
    if args.events:
        return asyncio.run(show_events())
    if args.notify_dry:
        return asyncio.run(notify_dry_run())
    if args.notify_test:
        return asyncio.run(notify_test())

    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        print("Остановлено пользователем")
    return 0


if __name__ == "__main__":
    sys.exit(main())
