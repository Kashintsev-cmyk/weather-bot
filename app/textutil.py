"""Утилиты для работы с текстом сообщений."""

from __future__ import annotations

DEFAULT_SPLIT_LIMIT = 3900


def split_text(text: str, limit: int = DEFAULT_SPLIT_LIMIT) -> list[str]:
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
