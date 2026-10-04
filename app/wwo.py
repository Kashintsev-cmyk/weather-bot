"""Русские описания кодов погоды World Weather Online (использует wttr.in).

wttr.in возвращает описания только на английском (даже при lang=ru),
поэтому берём числовой `weatherCode` и переводим его сами.
"""

from __future__ import annotations

# Код WWO -> описание на русском (наиболее употребимые коды).
WWO_DESCRIPTIONS: dict[int, str] = {
    113: "ясно",
    116: "переменная облачность",
    119: "облачно",
    122: "пасмурно",
    143: "дымка",
    176: "местами небольшой дождь",
    179: "местами небольшой снег",
    182: "местами мокрый снег",
    185: "местами слабая ледяная морось",
    200: "местами гроза",
    227: "позёмок",
    230: "метель",
    248: "туман",
    260: "ледяной туман",
    263: "местами слабая морось",
    266: "слабая морось",
    281: "ледяная морось",
    284: "сильная ледяная морось",
    293: "местами небольшой дождь",
    296: "небольшой дождь",
    299: "временами умеренный дождь",
    302: "умеренный дождь",
    305: "временами сильный дождь",
    308: "сильный дождь",
    311: "слабый ледяной дождь",
    314: "умеренный или сильный ледяной дождь",
    317: "слабый мокрый снег",
    320: "умеренный или сильный мокрый снег",
    323: "местами небольшой снег",
    326: "небольшой снег",
    329: "местами умеренный снег",
    332: "умеренный снег",
    335: "местами сильный снег",
    338: "сильный снег",
    350: "ледяная крупа",
    353: "небольшой ливневый дождь",
    356: "умеренный или сильный ливневый дождь",
    359: "ливень как из ведра",
    362: "небольшой ливневый мокрый снег",
    365: "умеренный или сильный ливневый мокрый снег",
    368: "небольшой снежный ливень",
    371: "умеренный или сильный снежный ливень",
    374: "небольшой ливень ледяной крупы",
    377: "умеренный или сильный ливень ледяной крупы",
    386: "местами небольшой дождь с грозой",
    389: "умеренный или сильный дождь с грозой",
    392: "местами небольшой снег с грозой",
    395: "умеренный или сильный снег с грозой",
}

# Род прилагательного интенсивности для каждого явления.
_INTENSITY_FORMS: dict[str, tuple[str, str, str]] = {
    "дождь": ("сильный", "умеренный", "слабый"),
    "ливневый дождь": ("сильный", "умеренный", "слабый"),
    "морось": ("сильная", "умеренная", "слабая"),
    "снег": ("сильный", "умеренный", "слабый"),
    "мокрый снег": ("сильный", "умеренный", "слабый"),
    "ледяной дождь": ("сильный", "умеренный", "слабый"),
    "ледяная морось": ("сильная", "умеренная", "слабая"),
    "снежный ливень": ("сильный", "умеренный", "слабый"),
}


def wwo_description(code: object) -> str | None:
    """Русское описание по числовому коду WWO; None — если код неизвестен."""
    try:
        normalized = int(code)  # type: ignore[call-overload]
    except (TypeError, ValueError):
        return None
    return WWO_DESCRIPTIONS.get(normalized)


def translate_en(description: object) -> str | None:
    """Грубый перевод английского описания (резерв, если код WWO неизвестен)."""
    if not isinstance(description, str):
        return None
    text = " ".join(description.lower().split())
    if not text:
        return None

    if "thunder" in text:
        if "snow" in text:
            return "гроза со снегом"
        if "rain" in text:
            return "гроза с дождём"
        return "гроза"
    if "blizzard" in text:
        return "метель"
    if "blowing snow" in text:
        return "позёмок"
    if "ice pellets" in text:
        return "ледяная крупа"
    if "sleet" in text:
        return _intensity(text, "мокрый снег", shower="ливневый " if "shower" in text else "")
    if "freezing rain" in text:
        return _intensity(text, "ледяной дождь")
    if "freezing drizzle" in text:
        return _intensity(text, "ледяная морось")
    if "drizzle" in text:
        return _intensity(text, "морось")
    if "snow" in text:
        return _intensity(text, "снег", shower="ливневый " if "shower" in text else "")
    if "shower" in text and "rain" in text:
        return _intensity(text, "ливневый дождь")
    if "rain" in text:
        return _intensity(text, "дождь")
    if "fog" in text:
        return "туман"
    if "mist" in text:
        return "дымка"
    if "overcast" in text:
        return "пасмурно"
    if "cloud" in text:
        return "облачно"
    if "clear" in text or "sunny" in text:
        return "ясно"
    return None


def _intensity(text: str, phenomenon: str, shower: str = "") -> str:
    """Собирает описание вида «местами слабый дождь» по английским маркерам."""
    forms = _INTENSITY_FORMS.get(phenomenon)
    if forms is None:
        forms = _INTENSITY_FORMS["дождь"]
    if "torrential" in text:
        # «ливень как из ведра» уже означает крайнюю интенсивность
        return "ливень как из ведра"
    if "heavy" in text:
        prefix = forms[0]
    elif "moderate" in text:
        prefix = forms[1]
    elif "light" in text or "slight" in text:
        prefix = forms[2]
    else:
        prefix = ""

    parts: list[str] = []
    if "patchy" in text:
        parts.append("местами")
    if prefix:
        parts.append(prefix)
    parts.append(f"{shower}{phenomenon}")
    return " ".join(parts)
