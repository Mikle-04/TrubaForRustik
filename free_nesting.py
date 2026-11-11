"""Раскрой детали произвольной формы (контур из DXF).

На лист кладутся одинаковые детали, поэтому раскладка строится как «плитка»:
1. Деталь раздувается на половину зазора — так раздутые копии могут касаться
   друг друга, а между настоящими деталями остаётся ровно зазор.
2. Для каждого угла поворота считается область запрещённых сдвигов
   (сумма Минковского, NFP): сдвиг копии внутрь неё даёт наложение.
3. По ней находится плотная решётка: шаг вдоль ряда и сдвиг следующего ряда.
4. Пробуются одиночная деталь и пара «деталь + копия, повёрнутая на 180°»
   (так сцепляются треугольники, Г-образные детали и т.п.).
Лучшие плитки по плотности потом раскладываются по листу (nesting.nest).
"""

import math
from dataclasses import dataclass

import numpy as np
import pyclipper
from shapely import affinity
from shapely.geometry import LineString, MultiPoint, MultiPolygon, Point, Polygon

from nesting import check_cancel, fit

SCALE = 100            # pyclipper работает в целых числах: сотые доли мм
SIMPLIFY = 0.05        # мм — упрощение контура для раскроя
MAX_VERTICES = 80      # больше — сумма Минковского для невыпуклых деталей считается долго
ANGLE_STEP = 10        # грубый перебор углов, °
REFINE_ANGLES = 2      # сколько лучших углов уточнять
REFINE_STEP = 2        # шаг уточнения, °
SHIFT_SAMPLES = 24     # перебор сдвига соседнего ряда
PAIR_SAMPLES = 72      # перебор положения второй детали в паре
TOP_TILES = 8          # сколько лучших плиток пробовать на листе


@dataclass
class Tile:
    parts: list       # настоящие детали (с отверстиями) в координатах плитки
    spans: list       # (от, до) по x раздутой копии каждой детали
    width: float      # габарит раздутой плитки
    height: float
    pitch: float      # шаг вдоль ряда (ось x)
    shift: float      # сдвиг каждого следующего ряда
    row: float        # шаг между рядами
    density: float    # доля площади, занятая деталями

    def slots(self, w, r):
        """Плитки ряда r: (x, номера деталей). По краям ряда, где целая плитка
        не влезает, докладываются отдельные детали из неё (они стоят на месте
        плитки решётки, поэтому с соседями не пересекаются)."""
        offset = (r * self.shift) % self.pitch
        n = fit(w - offset, self.width, self.pitch)
        everyone = list(range(len(self.parts)))
        out = [(offset + k * self.pitch, everyone) for k in range(n)]
        if len(self.parts) > 1:
            for x in (offset - self.pitch, offset + n * self.pitch):
                some = [i for i, (a, b) in enumerate(self.spans) if x + a >= -1e-9 and x + b <= w + 1e-9]
                if some:
                    out.append((x, some))
        return out

    def count(self, w, h):
        rows = fit(h, self.height, self.row)
        if self.shift == 0 and len(self.parts) == 1:
            return rows * fit(w, self.width, self.pitch)
        return sum(len(ids) for r in range(rows) for _, ids in self.slots(w, r))


class FreeShape:
    split_step = 5.0   # раскрой сложнее — линию раздела листа перебираем крупнее

    def __init__(self, polygon):
        self.polygon = polygon                                  # для картинки
        self.outer = Polygon(polygon.exterior).simplify(SIMPLIFY)  # для раскроя
        self._gap = None
        self.tiles = []

    def prepare(self, g):
        if self._gap != g:
            self.tiles = best_tiles(self.outer, self.polygon, g)
            self._gap = g

    def best(self, w, h, g):
        self.prepare(g)
        # раздутые детали выступают на g/2 — область тоже расширяем на g/2 с каждой стороны
        w, h = w + g, h + g
        best = (0, None)
        for i, tile in enumerate(self.tiles):
            # ряды вдоль x или вся раскладка, повёрнутая на 90° (ряды вдоль y)
            for turned, (tw, th) in ((False, (w, h)), (True, (h, w))):
                n = tile.count(tw, th)
                if n > best[0]:
                    best = (n, (i, tw, th, turned))
        return best

    def build(self, params, g):
        i, w, h, turned = params
        t = self.tiles[i]
        out = []

        def place(pts, dx, dy):
            pts = [(px + dx, py + dy) for px, py in pts]
            # поворот на 90° против часовой: (u, v) → (ширина − v, u)
            return [(h - g - v, u) for u, v in pts] if turned else pts

        for r in range(fit(h, t.height, t.row)):
            dy = r * t.row - g / 2
            for x, ids in t.slots(w, r):
                dx = x - g / 2
                for part in (t.parts[j] for j in ids):
                    out.append(("poly", place(part.exterior.coords, dx, dy)))
                    for hole in part.interiors:
                        out.append(("hole", place(hole.coords, dx, dy)))
        return out


def best_tiles(outer, real, g):
    # Раздутие на g/2 плюс запас: многоугольник раздутия срезает скругления (до 0,012 мм),
    # а упрощение (меньше вершин — в разы быстрее) срезает до tol. Запас = tol + 0,02,
    # поэтому зазор между деталями никогда не меньше заданного, максимум чуть больше.
    tol = SIMPLIFY
    while True:
        base = outer.buffer(g / 2 + 0.02 + tol, quad_segs=8).simplify(tol)
        if len(base.exterior.coords) <= MAX_VERTICES or tol >= 2:
            break
        tol *= 1.5
    tiles, done_full = {}, set()

    def try_angle(angle, full):
        """full — пробовать все положения второй детали в паре, иначе только самую компактную."""
        check_cancel()
        if angle in tiles and (not full or angle in done_full):
            return
        if full:
            done_full.add(angle)
        a = affinity.rotate(base, angle, origin=(0, 0))
        r = affinity.rotate(real, angle, origin=(0, 0))
        found = [make_tile([a], [r])]
        if sym180:
            tiles[angle] = [x for x in found if x]
            return
        b = affinity.rotate(a, 180, origin=(0, 0))
        rb = affinity.rotate(r, 180, origin=(0, 0))
        for t in _pair_offsets(a, b, aligned=full):
            found.append(make_tile([a, affinity.translate(b, *t)], [r, affinity.translate(rb, *t)]))
        tiles[angle] = [x for x in found if x]

    # Симметрия: круг — хватит одного угла; квадрат — углов до 90°;
    # фигура, переходящая в себя при повороте на 180°, — пары ничего не дают
    sym180 = _same_after_turn(base, 180)
    if _same_after_turn(base, ANGLE_STEP):
        angles = [0]
    elif _same_after_turn(base, 90):
        angles = range(0, 90, ANGLE_STEP)
    else:
        angles = range(0, 180, ANGLE_STEP)
    axis = [a for a in (0, 90) if a in angles]
    for angle in angles:
        try_angle(angle, full=angle in axis)
    if len(angles) == 1:
        return [t for ts in tiles.values() for t in ts]
    ranked = sorted(tiles, key=lambda a: -max((t.density for t in tiles[a]), default=0))
    for angle in ranked[:REFINE_ANGLES]:
        for d in range(-ANGLE_STEP + REFINE_STEP, ANGLE_STEP, REFINE_STEP):
            try_angle((angle + d) % 180, full=True)

    # лучшие по плотности + всегда варианты без поворота и на 90°: на прямоугольный
    # лист они часто ложатся лучше, чем более плотная, но косая решётка
    unique = {}
    for t in sorted((t for ts in tiles.values() for t in ts), key=lambda t: -t.density):
        key = tuple(round(v, 1) for v in (t.width, t.height, t.pitch, t.shift, t.row))
        unique.setdefault(key, t)
    result = list(unique.values())[:TOP_TILES]
    return result + [t for a in axis for t in tiles[a] if t not in result]


def _same_after_turn(poly, angle, tol=0.2):
    """Совпадает ли фигура сама с собой после поворота (с точностью до сдвига)."""
    turned = affinity.rotate(poly, angle, origin="centroid")
    return poly.hausdorff_distance(turned) < tol


def make_tile(inflated, real):
    region = Region(_union([nfp_of(a, b) for a in inflated for b in inflated]))
    pitch = _pitch(region)
    if pitch is None:
        return None
    shift, row = _row(region, pitch)
    if row is None:
        return None
    # габарит по вершинам: объединение в GEOS 3.14 на касающихся фигурах иногда врёт
    pts = np.vstack([np.array(p.exterior.coords) for p in inflated])
    (minx, miny), (maxx, maxy) = pts.min(axis=0), pts.max(axis=0)
    parts = [affinity.translate(p, -minx, -miny) for p in real]
    spans = [(p.bounds[0] - minx, p.bounds[2] - minx) for p in inflated]
    area = sum(p.area for p in real)
    return Tile(parts, spans, maxx - minx, maxy - miny, pitch, shift, row, area / (pitch * row))


def nfp_of(a, b):
    """Сдвиги t, при которых b + t накладывается на a (контур без внутренних «карманов»)."""
    if _convex(a) and _convex(b):
        # для выпуклых — выпуклая оболочка всех разностей вершин
        pa, pb = np.array(a.exterior.coords), np.array(b.exterior.coords)
        return MultiPoint((pa[:, None, :] - pb[None, :, :]).reshape(-1, 2)).convex_hull
    pb = [(-x, -y) for x, y in _int_path(b)]
    return _clipper_union(pyclipper.MinkowskiSum(pb, _int_path(a), True))


def _union(polys):
    """Объединение без GEOS: в 3.14 оно на касающихся фигурах иногда даёт неверный результат."""
    paths = []
    for p in polys:
        for gm in _pieces(p):
            paths.append(_int_path(gm))
    return _clipper_union(paths)


def _clipper_union(paths):
    """Объединение в целых числах; внутренние «дыры» и островки в них отбрасываются."""
    clipper = pyclipper.Pyclipper()
    clipper.AddPaths(paths, pyclipper.PT_SUBJECT, True)
    merged = clipper.Execute(pyclipper.CT_UNION, pyclipper.PFT_NONZERO, pyclipper.PFT_NONZERO)
    outers = sorted((s for s in merged if len(s) >= 3 and pyclipper.Orientation(s)),
                    key=lambda s: -abs(pyclipper.Area(s)))
    kept = []
    for path in outers:
        if not any(pyclipper.PointInPolygon(path[0], k) == 1 for k in kept):
            kept.append(path)
    polys = [Polygon([(x / SCALE, y / SCALE) for x, y in k]) for k in kept]
    return polys[0] if len(polys) == 1 else MultiPolygon(polys)


def _convex(poly):
    return poly.area >= poly.convex_hull.area * (1 - 1e-9)


def _int_path(poly):
    return [(round(x * SCALE), round(y * SCALE)) for x, y in poly.exterior.coords[:-1]]


def _pieces(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if hasattr(geom, "geoms") else [geom]


class Region:
    """Запрещённая область (NFP) как набор рёбер. Сечения прямыми и проверка точки
    считаются напрямую по рёбрам (numpy, правило чёт-нечет) — быстро и без GEOS."""

    def __init__(self, geom):
        edges = []
        for poly in _pieces(geom):
            c = np.array(poly.exterior.coords)
            edges.append(np.hstack([c[:-1], c[1:]]))
        self.edges = np.vstack(edges)
        self.bounds = geom.bounds

    def _cut(self, x_line, at):
        e = self.edges
        a0, b0, a1, b1 = (e[:, 0], e[:, 1], e[:, 2], e[:, 3]) if x_line else (e[:, 1], e[:, 0], e[:, 3], e[:, 2])
        m = (a0 <= at) != (a1 <= at)
        v = np.sort(b0[m] + (at - a0[m]) * (b1[m] - b0[m]) / (a1[m] - a0[m]))
        return list(zip(v[0::2], v[1::2]))

    def vertical(self, x):
        """Участки прямой x = const внутри области: [(y0, y1), ...]."""
        return self._cut(True, x)

    def horizontal(self, y):
        return self._cut(False, y)

    def contains(self, x, y, eps=1e-7):
        """Точка строго внутри (на границе — можно: детали только касаются)."""
        return any(y0 + eps < y < y1 - eps for y0, y1 in self.vertical(x))


def _right_end(region, x):
    """Правый конец участка оси x внутри области, на котором лежит точка (x, 0)."""
    for x0, x1 in region.horizontal(0.0):
        if x0 - 1e-9 <= x < x1:
            return x1
    return x


def _pitch(region):
    """Наименьший шаг p вдоль x, при котором точки k·p не попадают внутрь области."""
    p = _right_end(region, 0.0)
    if p <= 1e-6:
        return None
    for _ in range(30):
        for k in range(2, int(region.bounds[2] / p) + 2):
            if region.contains(k * p, 0.0):
                p = _right_end(region, k * p) / k
                break
        else:
            return p
    return p


def _row(region, p):
    """Лучший сдвиг ряда (s, dy): соседний ряд как можно ниже без наложений."""
    minx, miny, maxx, maxy = region.bounds
    # ряды ближе четверти высоты детали — вырожденная решётка (и бесконечная проверка)
    min_row = (maxy - miny) / 8

    def lowest(s):
        intervals = []
        for k in range(math.floor((minx - s) / p), math.ceil((maxx - s) / p) + 1):
            intervals += [(y0, y1) for y0, y1 in region.vertical(s + k * p) if y1 > 0]
        y = min_row
        for y0, y1 in sorted(intervals):
            if y0 <= y + 1e-9 and y1 > y:
                y = y1
        for _ in range(60):
            if _valid(region, p, s, y):
                return y
            y = max(y * 1.01, y + 0.1)
        return None

    best = (None, None)
    for i in range(SHIFT_SAMPLES):
        s = p * i / SHIFT_SAMPLES
        y = lowest(s)
        if y is not None and (best[1] is None or y < best[1] - 1e-9):
            best = (s, y)
    if best[1] is None:
        return None, None
    s0, step = best[0], p / SHIFT_SAMPLES
    for s in np.linspace(s0 - step, s0 + step, 11):
        s = s % p
        y = lowest(s)
        if y is not None and y < best[1] - 1e-9:
            best = (s, y)
    return best


def _valid(region, p, s, dy):
    """Ни одна точка решётки (j·s + k·p, j·dy), j ≥ 1, не попадает внутрь области."""
    minx, _, maxx, maxy = region.bounds
    j = 1
    while j * dy < maxy:
        xs = j * s
        for k in range(math.floor((minx - xs) / p), math.ceil((maxx - xs) / p) + 1):
            if region.contains(xs + k * p, j * dy):
                return False
        j += 1
    return True


def _pair_offsets(a, b, aligned=True):
    """Куда поставить повёрнутую копию b вплотную к a. Кандидаты: самая компактная пара
    (наименьшая выпуклая оболочка) и пары, выровненные с a по одному из краёв габарита
    (так ложатся косынки «вверх-вниз» в одну полосу)."""
    edge = nfp_of(a, b)
    ring = edge.exterior if edge.geom_type == "Polygon" else max(edge.geoms, key=lambda g: g.area).exterior
    ca, cb = np.array(a.exterior.coords), np.array(b.exterior.coords)

    def hull(dist):
        pt = ring.interpolate(dist % ring.length)
        return MultiPoint(np.vstack([ca, cb + (pt.x, pt.y)])).convex_hull.area

    marks = [ring.project(Point(c)) for c in ring.coords[:-1]]
    marks += [ring.length * i / PAIR_SAMPLES for i in range(PAIR_SAMPLES)]
    best = min(marks, key=hull)
    step = ring.length / PAIR_SAMPLES
    best = min(np.linspace(best - step, best + step, 21), key=hull)
    pt = ring.interpolate(best % ring.length)
    found = [(pt.x, pt.y)]
    if not aligned:
        return found

    ax0, ay0, ax1, ay1 = a.bounds
    bx0, by0, bx1, by1 = b.bounds
    minx, miny, maxx, maxy = edge.bounds
    lines = [LineString([(minx - 1, y), (maxx + 1, y)]) for y in (ay0 - by0, ay1 - by1)]
    lines += [LineString([(x, miny - 1), (x, maxy + 1)]) for x in (ax0 - bx0, ax1 - bx1)]
    for line in lines:
        try:
            cross = _pieces(ring.intersection(line))
        except Exception:      # GEOS иногда спотыкается на почти касающихся отрезках
            continue
        for gm in cross:
            for c in getattr(gm, "coords", []):
                if all(math.dist(c, f) > 0.01 for f in found):
                    found.append((c[0], c[1]))
    return found
