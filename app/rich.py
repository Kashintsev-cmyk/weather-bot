"""Оформление сводки как Rich Message Telegram.

Источник истины остаётся обычный текст (его формирует `app.formatting`):
он же уходит в CLI, тесты и как резервный вариант. Здесь текст разбирается
на заголовки, абзацы и списки и превращается в HTML для Rich Message.

Отправка: `build_rich_message` оборачивает HTML в `InputRichMessage`;
сам вызов Telegram — в `app.bot` (с откатом на обычный текст, если rich
не поддержан или вернул ошибку).
"""

from __future__ import annotations

import html
import logging
import re

logger = logging.getLogger(__name__)

# Строка вида «Сейчас: …» или «Море: …» — жирним подпись до двоеточия.
_LABEL_RE = re.compile(r"^([А-ЯЁA-Z][\wА-Яа-яЁё ]{0,14}):")

# Заголовки разделов: короткая строка, заканчивающаяся двоеточием.
_HEADING_LIMIT = 64


def _escape(text: str) -> str:
    """Экранирование HTML-спецсимволов."""
    return html.escape(text, quote=False)


def _paragraph_line(line: str) -> str:
    """Строка абзаца: экранирование + жирная подпись в начале («Сейчас:»)."""
    text = line.strip()
    match = _LABEL_RE.match(text)
    if not match:
        return _escape(text)
    label = f"{match.group(1)}:"
    return f"<b>{_escape(label)}</b>{_escape(text[len(label):])}"


def text_to_rich_html(text: str) -> str:
    """Преобразует текст сводки в HTML для Rich Message.

    Правила разбора (по структуре вывода `format_report`):
      - первая непустая строка — заголовок `<h1>`;
      - строка «Заголовок:» (без продолжения) — `<h3>`;
      - строка «— Событие:» — `<h4>`;
      - строки «• …» и «— …» — элементы списка `<ul>`;
      - отступы (продолжения) присоединяются к текущему элементу/абзацу.

    Пустой текст даёт пустую строку (отправлять нечего).
    """
    if not text or not text.strip():
        return ""

    blocks: list[str] = []
    paragraph: list[str] = []
    items: list[list[str]] = []

    def flush_paragraph() -> None:
        if paragraph:
            joined = "<br>".join(_paragraph_line(line) for line in paragraph)
            blocks.append(f"<p>{joined}</p>")
            paragraph.clear()

    def flush_items() -> None:
        if items:
            body = "".join(
                "<li>" + "<br>".join(_paragraph_line(line) for line in item) + "</li>"
                for item in items
            )
            blocks.append(f"<ul>{body}</ul>")
            items.clear()

    def flush_all() -> None:
        flush_items()
        flush_paragraph()

    def _is_icon_lead(line: str) -> bool:
        """Строка начинается с иконки/символа, а не с буквы или цифры."""
        return bool(line) and not line[0].isalnum()

    title_done = False
    in_group = False
    for raw in text.splitlines():
        if not raw.strip():
            # Пустая строка закрывает абзац, но список продолжается:
            # пункты одного раздела не должны рассыпаться на много <ul>.
            flush_paragraph()
            in_group = False
            continue

        stripped = raw.strip()
        indented = raw[:1].isspace()

        if not indented:
            in_group = False
            # «— Заголовок:» — подзаголовок группы (например, день событий).
            if stripped.startswith("— ") and stripped.endswith(":"):
                flush_all()
                blocks.append(f"<h4>{_escape(stripped[2:-1])}</h4>")
                in_group = True
                continue
            # Короткая строка с двоеточием на конце — заголовок раздела.
            if stripped.endswith(":") and len(stripped) <= _HEADING_LIMIT:
                flush_all()
                blocks.append(f"<h3>{_escape(stripped[:-1])}</h3>")
                title_done = True
                continue
            # Первая непустая строка сводки — её заголовок.
            if not title_done:
                blocks.append(f"<h1>{_escape(stripped)}</h1>")
                title_done = True
                continue
            if stripped.startswith("• "):
                flush_paragraph()
                items.append([stripped[2:]])
                continue
            if stripped.startswith("— "):
                flush_all()
                items.append([stripped[2:]])
                continue
            flush_items()
            paragraph.append(stripped)
            continue

        # Отступ. Внутри группы событий так начинается новый пункт (иконка)
        # или его продолжение; в остальных случаях — продолжение текущего.
        if in_group:
            if not items or _is_icon_lead(stripped):
                items.append([stripped])
            else:
                items[-1].append(stripped)
        elif items:
            items[-1].append(stripped)
        else:
            paragraph.append(stripped)

    flush_all()
    return "".join(blocks)


def build_rich_message(text: str):
    """Оборачивает текст сводки в `InputRichMessage` (HTML).

    Импорт aiogram ленивый, чтобы модуль оставался доступен без него
    (например, для тестов чистого HTML-преобразования).
    """
    from aiogram.types import InputRichMessage

    return InputRichMessage(html=text_to_rich_html(text))


async def send_rich(
    bot,
    chat_id: int,
    text: str,
    reply_markup=None,
) -> None:
    """Отправляет текст как Rich Message, с откатом на обычный текст.

    Rich не поддержан в старых клиентах/на старом Bot API — в этом случае
    сообщение всё равно должно дойти, поэтому ловим любую ошибку и отсылаем
    исходный текст (он не пустой и читаемый).
    """
    html_text = text_to_rich_html(text)
    if html_text:
        try:
            await bot.send_rich_message(
                chat_id=chat_id,
                rich_message=build_rich_message(text),
                reply_markup=reply_markup,
            )
            return
        except Exception:  # noqa: BLE001 - откатываемся на обычный текст
            logger.warning("Rich-сообщение не ушло, откат на текст", exc_info=True)
    await bot.send_message(chat_id, text, reply_markup=reply_markup)
