#!/usr/bin/env python3
"""CLI-режим: печатает или сохраняет обобщённую сводку без телеграма.

Примеры:
    python3 weather_report.py
    python3 weather_report.py --out hello.txt
    python3 weather_report.py --sea-only
    python3 weather_report.py --sections current,forecast --json report.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, replace
from pathlib import Path

from app.config import Settings
from app.events import detect_events
from app.formatting import (
    ALL_SECTIONS,
    SECTION_CURRENT,
    SECTION_EVENTS,
    SECTION_FORECAST,
    SECTION_MARINE,
    SECTION_NOTES,
    SECTION_SEA_FORECAST,
    SECTION_SOURCES,
    format_report,
)
from app.service import fetch_report

SEA_ONLY_SECTIONS = (
    SECTION_MARINE,
    SECTION_SEA_FORECAST,
    SECTION_SOURCES,
    SECTION_NOTES,
)


def build_parser() -> argparse.ArgumentParser:
    """Аргументы командной строки."""
    parser = argparse.ArgumentParser(
        description="Обобщённая сводка погоды и моря по нескольким площадкам.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--out",
        metavar="FILE",
        help="записать сводку в файл (иначе вывести в stdout)",
    )
    parser.add_argument(
        "--json",
        metavar="FILE",
        dest="json_path",
        help="сохранить нормализованные данные площадок и сводку в JSON",
    )
    parser.add_argument(
        "--sections",
        default=",".join(ALL_SECTIONS),
        help="разделы сводки через запятую: current,marine,events,forecast,sea_forecast,sources,notes",
    )
    parser.add_argument(
        "--sea-only",
        action="store_true",
        help="короткая сводка только по морю",
    )
    parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="сколько дней прогноза запрашивать (1..7)",
    )
    return parser


def resolve_sections(args: argparse.Namespace) -> tuple[str, ...]:
    """Определяет набор разделов сводки по аргументам."""
    if args.sea_only:
        return SEA_ONLY_SECTIONS
    sections = tuple(
        section.strip() for section in str(args.sections).split(",") if section.strip()
    )
    unknown = [section for section in sections if section not in ALL_SECTIONS]
    if unknown:
        raise SystemExit(
            f"Неизвестные разделы: {', '.join(unknown)}. "
            f"Доступны: {', '.join(ALL_SECTIONS)}"
        )
    return sections or ALL_SECTIONS


def render(report, sections: tuple[str, ...], settings: Settings | None = None) -> str:
    """Рендерит сводку; при наличии настроек добавляет раздел «События»."""
    if settings is None or SECTION_EVENTS not in sections:
        return format_report(report, sections)
    return format_report(
        report,
        sections,
        events=detect_events(report, settings),
        zone_name=settings.timezone,
    )


def dump_json(report, path: Path) -> None:
    """Сохраняет сводку в JSON (даты — строками)."""
    payload = asdict(report)
    payload = {key: value for key, value in payload.items() if key != "raw_results"}
    payload["sources"] = [asdict(result) for result in report.raw_results]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )


async def run(args: argparse.Namespace) -> int:
    settings = Settings.from_env()
    if args.days is not None:
        settings = replace(settings, forecast_days=max(1, min(7, args.days)))

    report = await fetch_report(settings)
    text = render(report, resolve_sections(args), settings)

    if args.out:
        Path(args.out).write_text(text + "\n", encoding="utf-8")
        print(f"Сводка записана в {args.out}")
    else:
        print(text)

    if args.json_path:
        dump_json(report, Path(args.json_path))
        print(f"JSON сохранён в {args.json_path}")

    if not report.sources_ok:
        print("Ни одна площадка не отдала данные.", file=sys.stderr)
        return 2
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())
