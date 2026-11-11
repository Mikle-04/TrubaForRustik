"""Загрузка детали из DXF (Компас, AutoCAD и др.).

Из файла берутся только линии контура: отрезки, дуги, окружности, эллипсы,
сплайны и полилинии (в том числе внутри блоков). Размеры, тексты, штриховки
и осевые линии пропускаются. Отрезки сшиваются в замкнутые контуры; внешний
контур — деталь, контуры внутри него — отверстия.
"""

import math
import os
from dataclasses import dataclass

from ezdxf import path as ezpath
from ezdxf import recover
from shapely import affinity
from shapely.geometry import Polygon
from shapely.ops import unary_union

CONTOUR_TYPES = {"LINE", "ARC", "CIRCLE", "ELLIPSE", "SPLINE", "LWPOLYLINE", "POLYLINE"}
# Осевые, штриховые и т.п. — не контур
SKIP_LINETYPES = ("AXIS", "CENTER", "DASH", "HIDDEN", "PHANTOM", "DOT", "ОСЕВ", "ШТРИХ", "ПУНКТИР")
# $INSUNITS → множитель в мм (0 — не указано, считаем мм)
UNIT_SCALE = {0: 1.0, 1: 25.4, 2: 304.8, 4: 1.0, 5: 10.0, 6: 1000.0}

FLATTEN = 0.05      # мм — точность разбиения дуг на отрезки
SNAP = 0.05         # мм — допуск стыковки концов отрезков
MIN_AREA = 1.0      # мм² — контуры меньше отбрасываются
MAX_BLOCK_DEPTH = 10


@dataclass
class DxfShape:
    polygon: Polygon   # деталь с отверстиями, левый нижний угол габарита в (0, 0)
    width: float
    height: float
    holes: int

    @property
    def area(self):
        return self.polygon.area

    def label(self):
        holes = f", отверстий: {self.holes}" if self.holes else ""
        return f"{self.width:.1f} × {self.height:.1f} мм{holes}"


@dataclass
class DxfDrawing:
    name: str
    shapes: list        # кандидаты в деталь, от большего к меньшему
    default: int        # какой контур выбрать по умолчанию
    open_chains: int    # сколько незамкнутых цепочек отброшено


class DxfError(Exception):
    pass


def load_dxf(file_path):
    try:
        doc, _ = recover.readfile(file_path)
    except IOError:
        raise DxfError("Не удалось открыть файл")
    except Exception:
        raise DxfError("Файл повреждён или это не DXF")
    scale = UNIT_SCALE.get(doc.header.get("$INSUNITS", 0), 1.0)

    closed, open_ = [], []
    for entity in _contour_entities(doc, doc.modelspace(), 0):
        try:
            pts = [(v.x * scale, v.y * scale)
                   for v in ezpath.make_path(entity).flattening(FLATTEN * scale)]
        except Exception:
            continue
        pts = _dedupe(pts)
        if len(pts) < 2:
            continue
        if len(pts) >= 3 and math.dist(pts[0], pts[-1]) <= SNAP:
            closed.append(pts)
        else:
            open_.append(pts)

    rings, dropped = _stitch(open_)
    rings += closed
    polygons = []
    for ring in rings:
        if len(ring) < 3:
            continue
        poly = Polygon(ring).buffer(0)  # чинит самопересечения
        if poly.geom_type == "MultiPolygon":
            poly = max(poly.geoms, key=lambda p: p.area)
        if poly.area >= MIN_AREA:
            polygons.append(Polygon(poly.exterior))
    if not polygons:
        raise DxfError("В файле не найдено ни одного замкнутого контура")

    polygons = _unique(sorted(polygons, key=lambda p: -p.area))
    parent = _hierarchy(polygons)
    children = {i: [j for j, p in enumerate(parent) if p == i] for i in range(len(polygons))}

    shapes = []
    for i, outer in enumerate(polygons):
        holes = [polygons[j] for j in children[i]]
        poly = outer.difference(unary_union(holes)) if holes else outer
        if poly.geom_type != "Polygon":
            poly = max(poly.geoms, key=lambda p: p.area)
        minx, miny, maxx, maxy = poly.bounds
        poly = affinity.translate(poly, -minx, -miny)
        shapes.append(DxfShape(poly, maxx - minx, maxy - miny, len(holes)))

    # По умолчанию — самый большой контур, внутри которого нет вложенных деталей
    # (иначе это рамка чертежа: деталь внутри неё)
    default = next((i for i in range(len(polygons))
                    if not any(children[j] for j in children[i])), 0)
    return DxfDrawing(os.path.basename(file_path), shapes, default, dropped)


def _contour_entities(doc, entities, depth):
    for e in entities:
        kind = e.dxftype()
        if kind == "INSERT" and depth < MAX_BLOCK_DEPTH:
            try:
                yield from _contour_entities(doc, e.virtual_entities(), depth + 1)
            except Exception:
                continue
        elif kind in CONTOUR_TYPES and not _is_aux_line(doc, e):
            yield e


def _is_aux_line(doc, e):
    linetype = e.dxf.get("linetype", "BYLAYER")
    if linetype.upper() in ("BYLAYER", "BYBLOCK"):
        try:
            linetype = doc.layers.get(e.dxf.get("layer", "0")).dxf.linetype
        except Exception:
            linetype = ""
    return any(key in linetype.upper() for key in SKIP_LINETYPES)


def _dedupe(pts):
    out = [pts[0]]
    for p in pts[1:]:
        if math.dist(p, out[-1]) > 1e-9:
            out.append(p)
    return out


def _stitch(chains):
    """Сшивает незамкнутые цепочки в контуры по совпадающим (в пределах SNAP) концам."""
    nodes = []          # координаты узлов
    grid = {}           # ячейка сетки → номера узлов

    def node(p):
        cx, cy = int(p[0] // SNAP), int(p[1] // SNAP)
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for n in grid.get((cx + dx, cy + dy), ()):
                    if math.dist(nodes[n], p) <= SNAP:
                        return n
        nodes.append(p)
        grid.setdefault((cx, cy), []).append(len(nodes) - 1)
        return len(nodes) - 1

    edges, seen = [], set()
    for pts in chains:
        a, b = node(pts[0]), node(pts[-1])
        if a == b:
            continue
        # дубли (одна и та же линия дважды) отбрасываем
        mid = pts[len(pts) // 2]
        key = (min(a, b), max(a, b), round(mid[0], 1), round(mid[1], 1))
        if key in seen:
            continue
        seen.add(key)
        edges.append((a, b, pts))

    at = {}
    for i, (a, b, _) in enumerate(edges):
        at.setdefault(a, []).append(i)
        at.setdefault(b, []).append(i)

    used = [False] * len(edges)
    rings, dropped = [], 0
    for start in range(len(edges)):
        if used[start]:
            continue
        used[start] = True
        a, b, pts = edges[start]
        ring, first, cur = list(pts), a, b
        while cur != first:
            nxt = next((i for i in at[cur] if not used[i]), None)
            if nxt is None:
                break
            used[nxt] = True
            na, nb, npts = edges[nxt]
            seg = npts if na == cur else npts[::-1]
            ring.extend(seg[1:])
            cur = nb if na == cur else na
        if cur == first:
            rings.append(ring)
        else:
            dropped += 1
    return rings, dropped


def _unique(polygons):
    """Убирает совпадающие контуры (например, окружность, нарисованную дважды)."""
    out = []
    for p in polygons:
        if not any(abs(p.area - q.area) < 0.01 * q.area and p.symmetric_difference(q).area < 0.01 * q.area
                   for q in out):
            out.append(p)
    return out


def _hierarchy(polygons):
    """parent[i] — наименьший контур, внутри которого лежит контур i (или None)."""
    parent = []
    for i, p in enumerate(polygons):
        point = p.representative_point()
        owner = None
        for j in range(i - 1, -1, -1):      # отсортированы по убыванию площади
            if polygons[j].area > p.area and polygons[j].contains(point) and polygons[j].contains(p.boundary):
                owner = j
                break
        parent.append(owner)
    return parent
