"""Сверка раскладки с количествами из ПроНеста (из Excel друга, лист 1500×6000)."""

import math
import sys
import time

from nesting import Circle, Gusset, nest
from sheets import SHEET_L, SHEET_W

GAP = float(sys.argv[1]) if len(sys.argv) > 1 else 5.0

FLANGES = [(260, 129), (295, 100), (310, 90), (350, 64), (400, 56), (420, 48), (420, 45),
           (440, 41), (460, 38), (495, 35), (500, 33), (520, 31), (530, 32), (540, 32),
           (580, 22), (600, 20), (650, 18), (680, 17), (680, 18), (800, 12)]

GUSSETS = [((120, 90), 868), ((150, 90), 748), ((130, 80), 836), ((30, 100), 1980),
           ((100, 100), 1128), ((100, 65), 1324), ((160, 105), 638), ((120, 180), 522),
           ((85, 85), 1568), ((60, 40), 3478), ((100, 55), 1881), ((90, 55), 2052),
           ((80, 65), 1922), ((200, 80), 702), ((400, 150), 196), ((200, 160), 384)]


def check_circles(layout, d):
    """Круги внутри листа и не ближе зазора друг к другу."""
    pts = [(x, y) for _, x, y, _ in layout.shapes]
    assert len(pts) == layout.count, (len(pts), layout.count)
    r = d / 2
    for x, y in pts:
        assert r - 1e-6 <= x <= SHEET_L - r + 1e-6 and r - 1e-6 <= y <= SHEET_W - r + 1e-6
    for i in range(len(pts)):
        for j in range(i + 1, len(pts)):
            assert math.dist(pts[i], pts[j]) >= d + GAP - 1e-6, (d, pts[i], pts[j])


def check_polys(layout):
    assert len(layout.shapes) == layout.count
    for _, pts in layout.shapes:
        for x, y in pts:
            assert -1e-6 <= x <= SHEET_L + 1e-6 and -1e-6 <= y <= SHEET_W + 1e-6


def report(title, rows):
    print(f"\n{title} (зазор {GAP:g} мм)")
    exact = 0
    for name, pronest, ours in rows:
        diff = ours - pronest
        exact += diff == 0
        print(f"  {name:>10}  ПроНест {pronest:5}  у нас {ours:5}  {diff:+5}  ({diff / pronest:+.0%})")
    print(f"  точно совпало {exact}/{len(rows)}")


def main():
    t = time.time()
    rows = []
    for d, n in FLANGES:
        lay = nest(Circle(d), SHEET_L, SHEET_W, GAP)
        check_circles(lay, d)
        rows.append((f"Ø{d}", n, lay.count))
    report("Фланцы", rows)

    rows = []
    for (a, b), n in GUSSETS:
        lay = nest(Gusset(a, b, 10), SHEET_L, SHEET_W, GAP)
        check_polys(lay)
        rows.append((f"{a}×{b}", n, lay.count))
    report("Косынки", rows)
    print(f"\n{time.time() - t:.1f} с")


if __name__ == "__main__":
    main()
