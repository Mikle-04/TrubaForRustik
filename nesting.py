"""Раскладка деталей на листе.

Для каждой формы ищется лучший регулярный рисунок в прямоугольнике,
затем лист пробуется разрезать на две зоны с разными рисунками
(так дозаполняются остатки). Это не ПроНест, но близко к нему
для кругов, пластин и косынок.

Координаты в мм: x — вдоль длины листа, y — вдоль ширины.
Фигуры на выходе:
    ("circle", cx, cy, r)
    ("poly", [(x, y), ...])
"""

import math
from dataclasses import dataclass

EPS = 1e-6
SPLIT_STEP = 1.0  # шаг перебора линии раздела листа, мм


def fit(span, size, pitch):
    """Сколько деталей размера size с шагом pitch влезает в span."""
    if span + EPS < size:
        return 0
    return 1 + int((span - size + EPS) // pitch)


# ---------- Фланец (круг) ----------

@dataclass
class Circle:
    d: float

    def best(self, w, h, g):
        """«Шахматка» рядами вдоль одной из сторон: нечётные ряды сдвинуты на s."""
        d = self.d
        p = d + g
        best = (0, None)
        for tw, th, swap in ((w, h, False), (h, w, True)):
            n1 = fit(tw, d, p)
            if n1 == 0:
                continue
            # s = 0 — квадратная сетка; иначе максимальный сдвиг (до p/2),
            # при котором в сдвинутом ряду остаётся n1 или n1−1 кругов
            shifts = {0.0}
            for n in (n1, n1 - 1):
                if n >= 1:
                    shifts.add(max(0.0, min(p / 2, tw - d - (n - 1) * p)))
            for s in shifts:
                n2 = fit(tw - s, d, p)
                dy = math.sqrt(p * p - s * s)
                m = fit(th, d, dy)
                count = (m + 1) // 2 * n1 + m // 2 * n2
                if count > best[0]:
                    best = (count, (swap, s, dy, n1, n2, m))
        return best

    def build(self, params, g):
        swap, s, dy, n1, n2, m = params
        d = self.d
        p = d + g
        r = d / 2
        out = []
        for row in range(m):
            y = r + row * dy
            x0, n = (r, n1) if row % 2 == 0 else (r + s, n2)
            for i in range(n):
                x = x0 + i * p
                out.append(("circle", y, x, r) if swap else ("circle", x, y, r))
        return out


# ---------- Пластина (прямоугольник) ----------

@dataclass
class Plate:
    a: float
    b: float

    def best(self, w, h, g):
        best = (0, None)
        for pa, pb in ((self.a, self.b), (self.b, self.a)):
            nx, ny = fit(w, pa, pa + g), fit(h, pb, pb + g)
            if nx * ny > best[0]:
                best = (nx * ny, (pa, pb, nx, ny))
        return best

    def build(self, params, g):
        pa, pb, nx, ny = params
        out = []
        for i in range(nx):
            for j in range(ny):
                x, y = i * (pa + g), j * (pb + g)
                out.append(("poly", [(x, y), (x + pa, y), (x + pa, y + pb), (x, y + pb)]))
        return out


# ---------- Косынка (прямоугольный треугольник со срезанными углами) ----------

@dataclass
class Gusset:
    a: float          # катет 1
    b: float          # катет 2
    cut: float = 0.0  # срез всех трёх углов, мм

    def best(self, w, h, g):
        """Полосы из чередующихся треугольников «вверх-вниз», гипотенузы параллельны."""
        best = (0, None)
        for tw, th, swap in ((w, h, False), (h, w, True)):
            for la, lb in ((self.a, self.b), (self.b, self.a)):
                strips = fit(th, lb, lb + g)
                per_strip, dx = _gusset_strip(tw, la, lb, g)
                count = strips * per_strip
                if count > best[0]:
                    best = (count, (swap, la, lb, dx, strips, tw))
        return best

    def build(self, params, g):
        swap, la, lb, dx, strips, tw = params
        period = la + dx + g
        n_up = fit(tw, la, period)
        n_down = fit(tw - dx, la, period)
        out = []
        for j in range(strips):
            y = j * (lb + g)
            tris = []
            for k in range(n_up):
                x = k * period
                tris.append([(x, y), (x + la, y), (x, y + lb)])
            for k in range(n_down):
                x = k * period + dx
                tris.append([(x + la, y + lb), (x, y + lb), (x + la, y)])
            for tri in tris:
                pts = _cut_corners(tri, self.cut)
                if swap:
                    pts = [(py, px) for px, py in pts]
                out.append(("poly", pts))
        return out


def _gusset_strip(length, la, lb, g):
    """Сколько треугольников (катет la вдоль полосы, lb поперёк) в полосе длиной length.

    Перевёрнутый треугольник сдвинут на dx, чтобы между гипотенузами был зазор g.
    """
    dx = g * math.hypot(la, lb) / lb
    period = la + dx + g
    return fit(length, la, period) + fit(length - dx, la, period), dx


def _cut_corners(pts, c):
    if c <= 0:
        return pts
    out = []
    n = len(pts)
    for i, (vx, vy) in enumerate(pts):
        for nx, ny in (pts[i - 1], pts[(i + 1) % n]):
            ln = math.hypot(nx - vx, ny - vy)
            out.append((vx + c * (nx - vx) / ln, vy + c * (ny - vy) / ln))
    return out


# ---------- Раскладка на листе ----------

@dataclass
class Layout:
    count: int
    shapes: list


def nest(part, sheet_l, sheet_w, gap=5.0, margin=0.0):
    """Максимум деталей на листе: один рисунок или два рисунка в двух зонах."""
    w, h = sheet_l - 2 * margin, sheet_w - 2 * margin
    count, params = part.best(w, h, gap)
    plan = [(0.0, 0.0, params)] if params else []

    # Разрез поперёк длины (x) и поперёк ширины (y): зона 1 + зазор + зона 2
    step = getattr(part, "split_step", SPLIT_STEP)
    for axis, span in ((0, w), (1, h)):
        cut = step
        while cut < span - gap:
            rest = span - cut - gap
            if axis == 0:
                c1, p1 = part.best(cut, h, gap)
                c2, p2 = part.best(rest, h, gap)
                offset = (cut + gap, 0.0)
            else:
                c1, p1 = part.best(w, cut, gap)
                c2, p2 = part.best(w, rest, gap)
                offset = (0.0, cut + gap)
            if c1 + c2 > count:
                count = c1 + c2
                plan = [(0.0, 0.0, p) for p in (p1,) if p] + [(*offset, p) for p in (p2,) if p]
            cut += step

    shapes = []
    for ox, oy, params in plan:
        for shape in part.build(params, gap):
            shapes.append(_shift(shape, ox + margin, oy + margin))
    return Layout(count, shapes)


def _shift(shape, dx, dy):
    if shape[0] == "circle":
        _, x, y, r = shape
        return ("circle", x + dx, y + dy, r)
    return (shape[0], [(x + dx, y + dy) for x, y in shape[1]])
