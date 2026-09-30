"""Проверки преобразования текста сводки в HTML Rich Message."""

from __future__ import annotations

from app.rich import text_to_rich_html


def test_first_line_becomes_heading() -> None:
    html = text_to_rich_html("Адлер — обобщённая сводка\nВторая строка")

    assert html.startswith("<h1>Адлер — обобщённая сводка</h1>")
    assert "<p>Вторая строка</p>" in html


def test_label_is_bolded() -> None:
    html = text_to_rich_html("Заголовок\n\nСейчас: +19 °C, дождь.")

    assert "<b>Сейчас:</b> +19 °C, дождь." in html


def test_section_heading_and_bullet_list() -> None:
    text = (
        "Прогноз по дням (обобщённо):\n"
        "\n"
        "• завтра — +16…+20 °C.\n"
        "  ощущается до +21 °C; осадки до 5.0 мм.\n"
        "• пятница — +13…+18 °C.\n"
    )
    html = text_to_rich_html(text)

    assert "<h3>Прогноз по дням (обобщённо)</h3>" in html
    assert "<ul>" in html
    assert "<li>завтра — +16…+20 °C.<br>ощущается до +21 °C; осадки до 5.0 мм.</li>" in html
    assert "<li>пятница — +13…+18 °C.</li>" in html


def test_events_grouped_as_heading_and_items() -> None:
    text = (
        "События (ближайшие сверху):\n"
        "\n"
        "— сегодня, 30 сентября:\n"
        "  ⚠️ Дождь · 13:00–16:00\n"
        "  до 3.2 мм/ч, вероятность 93%\n"
    )
    html = text_to_rich_html(text)

    assert "<h4>сегодня, 30 сентября</h4>" in html
    assert "<li>⚠️ Дождь · 13:00–16:00<br>до 3.2 мм/ч, вероятность 93%</li>" in html


def test_html_special_chars_escaped() -> None:
    html = text_to_rich_html("Море\n\nвода < 5 & волна > 1")

    assert "&lt;" in html and "&gt;" in html and "&amp;" in html


def test_empty_text_gives_empty_html() -> None:
    assert text_to_rich_html("") == ""
    assert text_to_rich_html("   ") == ""


def test_send_rich_falls_back_to_plain_text() -> None:
    """Если rich-отправка падает, сообщение уходит обычным текстом."""
    import asyncio

    from app.rich import send_rich

    class FakeBot:
        def __init__(self) -> None:
            self.rich_attempts: list[dict] = []
            self.plain: list[dict] = []

        async def send_rich_message(self, **kwargs):  # noqa: ANN003, ANN202 - тест
            self.rich_attempts.append(kwargs)
            raise RuntimeError("rich не поддержан")

        async def send_message(self, chat_id, text, reply_markup=None):  # noqa: ANN001, ANN202 - тест
            self.plain.append({"chat_id": chat_id, "text": text, "reply_markup": reply_markup})

    bot = FakeBot()
    asyncio.run(send_rich(bot, 42, "Заголовок\n\nСейчас: дождь."))

    assert len(bot.rich_attempts) == 1
    assert bot.plain == [
        {"chat_id": 42, "text": "Заголовок\n\nСейчас: дождь.", "reply_markup": None}
    ]


def test_send_rich_success_does_not_fall_back() -> None:
    """Успешная rich-отправка не дублируется обычным сообщением."""
    import asyncio

    from app.rich import send_rich

    class FakeBot:
        def __init__(self) -> None:
            self.rich = 0
            self.plain = 0

        async def send_rich_message(self, **kwargs):  # noqa: ANN003, ANN202 - тест
            self.rich += 1

        async def send_message(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202 - тест
            self.plain += 1

    bot = FakeBot()
    asyncio.run(send_rich(bot, 42, "Заголовок\n\nСейчас: дождь."))

    assert bot.rich == 1
    assert bot.plain == 0
