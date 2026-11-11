"""Расчёт заготовки, угара, отхода и металлолома."""

from dataclasses import dataclass

BURN_RATE = 0.02   # угар — 2 % от массы детали
SCRAP_RATE = 0.8   # металлолом — 80 % от отхода


@dataclass
class Result:
    blank: float   # Мз — масса заготовки
    burn: float    # У — угар
    waste: float   # Ро — отход
    scrap: float   # Мт — металлолом


def calculate(sheet_mass, count_on_sheet, part_mass):
    """Мз = Мл / N;  У = 0.02·Мд;  Ро = Мз − Мд − У;  Мт = 0.8·Ро (на 1 деталь)."""
    blank = sheet_mass / count_on_sheet
    burn = part_mass * BURN_RATE
    waste = blank - part_mass - burn
    scrap = waste * SCRAP_RATE
    return Result(blank, burn, waste, scrap)
