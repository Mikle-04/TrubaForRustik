"""Библиотека посчитанных деталей: хранится в «библиотека.json» рядом с программой."""

import json
import os
import re
import sys

SECTIONS = ["Фланцы", "Косынки", "Перегородки", "Другие детали"]

# Раздел по умолчанию для типа детали; поменять можно в библиотеке
DEFAULT_SECTION = {"Фланец": "Фланцы", "Косынка": "Косынки", "Пластина": "Перегородки"}


def library_path():
    """Рядом с .exe (или с app.py при запуске из исходников) — как шаблоны."""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "библиотека.json")


def load_library():
    """Список деталей [{name, section, …}]; нет файла или он битый — пустой список."""
    try:
        with open(library_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    items = [it for it in data if isinstance(it, dict) and it.get("name")] if isinstance(data, list) else []
    for it in items:
        if it.get("section") not in SECTIONS:
            it["section"] = SECTIONS[-1]
    return items


def save_library(items):
    with open(library_path(), "w", encoding="utf-8") as f:
        json.dump(items, f, ensure_ascii=False, indent=2)


NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def matches(item, query):
    """Поиск по словам: все слова запроса есть в названии или описании, в любом порядке.

    Число ищется как отдельное число («15» не находит 150, 1500 и 1,5),
    остальное — как кусок текста («кос» находит «косынка»). Запятая и точка равны.
    """
    text = f"{item['name']} {item.get('part', '')}".lower().replace(",", ".")
    for word in query.lower().replace(",", ".").split():
        if NUMBER.fullmatch(word):
            if not re.search(rf"(?<![\d.]){re.escape(word)}(?![\d]|\.\d)", text):
                return False
        elif word not in text:
            return False
    return True


def unique_name(name, items, skip=None):
    """name, а если такое уже есть — «name (2)», «name (3)»…; skip — сама переименовываемая деталь."""
    taken = {it["name"] for it in items if it is not skip}
    if name not in taken:
        return name
    n = 2
    while f"{name} ({n})" in taken:
        n += 1
    return f"{name} ({n})"
