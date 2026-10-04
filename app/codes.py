"""Коды погоды: приведение кодов площадок к единым кодам WMO.

Open-Meteo отдаёт коды WMO как есть, OpenWeatherMap — числовые id условий,
wttr.in (World Weather Online) — свои коды. Здесь они приводятся к общей
шкале WMO, чтобы обобщение и детектор событий работали одинаково.
"""

from __future__ import annotations

WMO_CLEAR = frozenset({0, 1})
WMO_CLOUDY = frozenset({2, 3})
WMO_FOG = frozenset({45, 48})
WMO_DRIZZLE = frozenset({51, 53, 55, 56, 57})
WMO_RAIN = frozenset({61, 63, 65, 66, 67, 80, 81, 82})
WMO_SNOW = frozenset({71, 73, 75, 77, 85, 86})
WMO_THUNDER = frozenset({95, 96, 99})
WMO_HAIL = frozenset({96, 99})
WMO_HEAVY = frozenset({65, 67, 75, 82, 86, 96, 99})

# --- OpenWeatherMap: id условий -> WMO ---
OWM_TO_WMO: dict[int, int] = {
    200: 95, 201: 95, 202: 99, 210: 95, 211: 95, 212: 99, 221: 95,
    230: 95, 231: 95, 232: 99,
    300: 51, 301: 53, 302: 55, 310: 56, 311: 57, 312: 57, 313: 57,
    314: 57, 321: 57,
    500: 61, 501: 63, 502: 65, 503: 65, 504: 65, 511: 66, 520: 80,
    521: 80, 522: 82, 531: 80,
    600: 71, 601: 73, 602: 75, 611: 66, 612: 66, 613: 66, 615: 73,
    616: 73, 620: 85, 621: 73, 622: 86,
    701: 45, 711: 45, 721: 45, 731: 48, 741: 45, 751: 48, 761: 48,
    762: 48, 771: 65, 781: 99,
    800: 0, 801: 1, 802: 2, 803: 3, 804: 3,
}

# --- wttr.in (WWO) -> WMO ---
WWO_TO_WMO: dict[int, int] = {
    113: 0, 116: 1, 119: 2, 122: 3, 143: 45, 248: 45, 260: 48,
    176: 61, 263: 51, 266: 53, 281: 56, 284: 57, 293: 61, 296: 61,
    299: 63, 302: 63, 305: 65, 308: 65, 311: 57, 314: 57, 353: 80,
    356: 81, 359: 82, 362: 66, 365: 66, 386: 95, 389: 96, 395: 95,
    179: 66, 182: 66, 185: 56, 227: 75, 230: 75, 320: 71, 323: 85,
    326: 71, 329: 73, 332: 73, 335: 75, 338: 75, 350: 77, 368: 85,
    371: 86, 374: 85, 377: 86,
}


def _as_int(code: object) -> int | None:
    """Приводит код к int, если это возможно."""
    if code is None or isinstance(code, bool):
        return None
    try:
        return int(float(code))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def wmo_from_owm(condition_id: object) -> int | None:
    """Код условия OpenWeatherMap -> код WMO."""
    code = _as_int(condition_id)
    if code is None:
        return None
    if code in OWM_TO_WMO:
        return OWM_TO_WMO[code]
    for base in (200, 300, 500, 600, 700, 800):
        if base <= code < base + 100:
            return OWM_TO_WMO.get(base)
    return None


def wmo_from_wwo(code: object) -> int | None:
    """Код World Weather Online (wttr.in) -> код WMO."""
    value = _as_int(code)
    return None if value is None else WWO_TO_WMO.get(value)


def normalize_wmo(code: object) -> int | None:
    """Проверяет, что код лежит в известной шкале WMO (0..99)."""
    value = _as_int(code)
    if value is None or not 0 <= value <= 99:
        return None
    return value


def wmo_is_precipitation(code: int | None) -> bool:
    """Есть ли по коду осадки (дождь, снег, гроза, морось)."""
    if code is None:
        return False
    return code in WMO_DRIZZLE or code in WMO_RAIN or code in WMO_SNOW or code in WMO_THUNDER


def wmo_is_snow(code: int | None) -> bool:
    """Снег или снежная крупа."""
    return code is not None and code in WMO_SNOW


def wmo_is_thunder(code: int | None) -> bool:
    """Гроза (в том числе с градом)."""
    return code is not None and code in WMO_THUNDER


def wmo_is_hail(code: int | None) -> bool:
    """Гроза с градом."""
    return code is not None and code in WMO_HAIL


def wmo_is_fog(code: int | None) -> bool:
    """Туман или ледяной туман."""
    return code is not None and code in WMO_FOG


def wmo_is_heavy(code: int | None) -> bool:
    """Сильные осадки по коду (ливень, сильный снег, гроза с градом)."""
    return code is not None and code in WMO_HEAVY


def wmo_kind(code: int | None) -> str:
    """Тип осадков по коду: snow, thunder, rain, fog или none."""
    if wmo_is_snow(code):
        return "snow"
    if wmo_is_thunder(code):
        return "thunder"
    if code is not None and (code in WMO_RAIN or code in WMO_DRIZZLE):
        return "rain"
    if wmo_is_fog(code):
        return "fog"
    return "none"
