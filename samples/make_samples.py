"""Генерирует тестовые DXF с особенностями, которые встречаются в выгрузке из Компаса."""

import math
import os
import random

import ezdxf

HERE = os.path.dirname(os.path.abspath(__file__))


def new_doc(units=4):
    doc = ezdxf.new("R2010", setup=True)
    doc.header["$INSUNITS"] = units
    doc.layers.add("Осевые", linetype="CENTER")
    return doc


def save(doc, name):
    doc.saveas(os.path.join(HERE, name))


def flange():
    """Фланец Ø400, центральное отверстие Ø150, 8 отверстий Ø22 на Ø340, осевые, размер."""
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_circle((0, 0), 200)
    msp.add_circle((0, 0), 75)
    for i in range(8):
        a = math.radians(i * 45)
        msp.add_circle((170 * math.cos(a), 170 * math.sin(a)), 11)
    msp.add_line((-220, 0), (220, 0), dxfattribs={"layer": "Осевые"})
    msp.add_line((0, -220), (0, 220), dxfattribs={"layer": "Осевые"})
    msp.add_circle((0, 0), 170, dxfattribs={"linetype": "CENTER"})
    dim = msp.add_linear_dim(base=(0, 240), p1=(-200, 0), p2=(200, 0))
    dim.render()
    msp.add_text("Фланец", dxfattribs={"height": 10}).set_placement((-30, -260))
    save(doc, "1_flange_400.dxf")


def plate_fillets():
    """Пластина 300×200 со скруглениями R20 из отрезков и дуг вперемешку, 4 отверстия Ø18."""
    doc = new_doc()
    msp = doc.modelspace()
    w, h, r = 300, 200, 20
    items = [
        ("line", (r, 0), (w - r, 0)), ("line", (w, r), (w, h - r)),
        ("line", (w - r, h), (r, h)), ("line", (0, h - r), (0, r)),
        ("arc", (w - r, r), 270, 360), ("arc", (w - r, h - r), 0, 90),
        ("arc", (r, h - r), 90, 180), ("arc", (r, r), 180, 270),
    ]
    random.seed(3)
    random.shuffle(items)
    for kind, a, b, *rest in items:
        if kind == "line":
            if random.random() < 0.5:
                a, b = b, a     # часть отрезков «задом наперёд»
            msp.add_line(a, b)
        else:
            msp.add_arc(a, r, b, rest[0])
    for x, y in ((40, 40), (260, 40), (260, 160), (40, 160)):
        msp.add_circle((x, y), 9)
    save(doc, "2_plate_300x200_R20.dxf")


def bracket():
    """Г-образный кронштейн одной полилинией со скруглением внутреннего угла (bulge)."""
    doc = new_doc()
    msp = doc.modelspace()
    bulge = math.tan(math.radians(90) / 4)
    pts = [(0, 0), (250, 0), (250, 60), (75, 60, -bulge), (60, 75), (60, 180), (0, 180)]
    msp.add_lwpolyline([(p[0], p[1], 0, 0, p[2] if len(p) > 2 else 0) for p in pts],
                       format="xyseb", close=True)
    msp.add_circle((30, 150), 8)
    msp.add_circle((220, 30), 8)
    save(doc, "3_bracket_L.dxf")


def spline():
    """Фигурная деталь: замкнутый сплайн."""
    doc = new_doc()
    msp = doc.modelspace()
    pts = [(0, 0), (120, -30), (240, 0), (260, 80), (180, 140), (120, 100), (60, 140), (-20, 80), (0, 0)]
    msp.add_spline(pts).closed = True
    save(doc, "4_spline_shape.dxf")


def block_cm():
    """Деталь внутри блока; единицы файла — сантиметры (косынка 15×9 см со срезами 1 см)."""
    doc = new_doc(units=5)
    blk = doc.blocks.new("DETAL")
    blk.add_lwpolyline([(1, 0), (14, 0), (15, 0.6), (1.7, 8.5), (0, 8), (0, 1)], close=True)
    doc.modelspace().add_blockref("DETAL", (100, 100))
    save(doc, "5_block_in_cm.dxf")


def with_frame():
    """Деталь (пластина 200×120 с отверстием) внутри рамки чертежа А3."""
    doc = new_doc()
    msp = doc.modelspace()
    msp.add_lwpolyline([(0, 0), (420, 0), (420, 297), (0, 297)], close=True)
    msp.add_lwpolyline([(20, 5), (415, 5), (415, 292), (20, 292)], close=True)
    msp.add_lwpolyline([(230, 5), (415, 5), (415, 60), (230, 60)], close=True)  # штамп
    msp.add_lwpolyline([(60, 100), (260, 100), (260, 220), (60, 220)], close=True)
    msp.add_circle((160, 160), 25)
    save(doc, "6_part_in_frame.dxf")


def dirty():
    """Шестиугольник 160 мм из отрезков: концы не сходятся на 0,03 мм, одна сторона продублирована."""
    doc = new_doc()
    msp = doc.modelspace()
    pts = [(80 * math.cos(math.radians(a)), 80 * math.sin(math.radians(a))) for a in range(0, 360, 60)]
    for i in range(6):
        a, b = pts[i], pts[(i + 1) % 6]
        msp.add_line(a, (b[0] + 0.03, b[1] - 0.02))
    msp.add_line(pts[0], pts[1])
    save(doc, "7_dirty_hexagon.dxf")


if __name__ == "__main__":
    for make in (flange, plate_fillets, bracket, spline, block_cm, with_frame, dirty):
        make()
    print("готово:", sorted(f for f in os.listdir(HERE) if f.endswith(".dxf")))
