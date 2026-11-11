"""Справочник листов 1500×6000: толщины, марки и массы."""

SHEET_W = 1500  # ширина листа, мм
SHEET_L = 6000  # длина листа, мм

# Стандартный ряд толщин, мм
THICKNESSES = [3, 4, 5, 6, 8, 10, 12, 14, 16, 18, 20, 22, 25, 28, 30, 32, 36, 40]

# Плотность, кг/м³ — по справочнику service-online.su/text/calc/
GRADES = {
    "09Г2С": 7850,
    "Ст3пс": 7800,
}


def sheet_mass(thickness, grade, width=SHEET_W, length=SHEET_L):
    """Масса листа, кг (до грамма, как в калькуляторе)."""
    volume = (width / 1000) * (length / 1000) * (thickness / 1000)
    return round(volume * GRADES[grade], 3)


def sheet_name(thickness, grade, width=SHEET_W, length=SHEET_L):
    return f"{thickness:g}×{width:g}×{length:g} {grade}"
