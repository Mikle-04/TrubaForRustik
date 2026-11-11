"""Проверка раскроя произвольного контура: сравнение с точными формулами и отсутствие наложений."""
import math
import sys
import time

from shapely import affinity
from shapely.geometry import Point, Polygon, box
from shapely.strtree import STRtree

from free_nesting import FreeShape
from nesting import Circle, Gusset, Plate, nest

GAP = 5.0
L, W = 6000, 1500


def check_layout(layout, gap=GAP):
    """Детали внутри листа, между любыми двумя — не меньше зазора."""
    polys = [Polygon(s[1]) for s in layout.shapes if s[0] == "poly"]
    assert len(polys) == layout.count, (len(polys), layout.count)
    sheet = box(0, 0, L, W).buffer(1e-3)
    for p in polys:
        assert sheet.contains(p), "деталь вылезла за лист"
    tree = STRtree(polys)
    for i, p in enumerate(polys):
        for j in tree.query(p.buffer(gap)):
            if j > i:
                d = p.distance(polys[j])
                assert d >= gap - 1e-3, f"зазор {d:.3f} < {gap}"


def run(title, free_poly, exact_part):
    t = time.time()
    free = nest(FreeShape(free_poly), L, W, GAP)
    dt = time.time() - t
    check_layout(free)
    exact = nest(exact_part, L, W, GAP).count
    print(f"{title:28} контур {free.count:5}  формула {exact:5}  {dt:5.1f} с")


if __name__ == "__main__":
    run("Фланец Ø400", Point(0, 0).buffer(200, quad_segs=32), Circle(400))
    run("Фланец Ø260", Point(0, 0).buffer(130, quad_segs=32), Circle(260))
    run("Пластина 200×100", box(0, 0, 200, 100), Plate(200, 100))
    run("Косынка 130×80 без срезов", Polygon([(0, 0), (130, 0), (0, 80)]), Gusset(130, 80))
    run("Косынка 100×100 без срезов", Polygon([(0, 0), (100, 0), (0, 100)]), Gusset(100, 100))
    run("Полоса 1400×100", box(0, 0, 1400, 100), Plate(1400, 100))
    run("Г-образная 200×150", Polygon([(0, 0), (200, 0), (200, 40), (40, 40), (40, 150), (0, 150)]), Plate(200, 150))
