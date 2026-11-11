"""Проверка чтения DXF на образцах из samples/ (python samples/make_samples.py)."""
import math
import os

from dxf_import import load_dxf

HERE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "samples")

# файл: (ширина, высота, отверстий, площадь мм² или None)
EXPECTED = {
    "1_flange_400.dxf": (400, 400, 9, math.pi * (200**2 - 75**2 - 8 * 11**2)),
    "2_plate_300x200_R20.dxf": (300, 200, 4, 300 * 200 - (4 - math.pi) * 20**2 - 4 * math.pi * 9**2),
    "3_bracket_L.dxf": (250, 180, 2, None),
    "4_spline_shape.dxf": (None, None, 0, None),
    "5_block_in_cm.dxf": (150, 85, 0, None),
    "6_part_in_frame.dxf": (200, 120, 1, 200 * 120 - math.pi * 25**2),
    "7_dirty_hexagon.dxf": (160, 160 * math.sqrt(3) / 2, 0, 3 * math.sqrt(3) / 2 * 80**2),
}

ok = True
for name, (w, h, holes, area) in EXPECTED.items():
    d = load_dxf(os.path.join(HERE, name))
    s = d.shapes[d.default]
    errs = []
    if w is not None and (abs(s.width - w) > 0.2 or abs(s.height - h) > 0.2):
        errs.append(f"габарит {s.width:.2f}×{s.height:.2f}, ждали {w}×{h:.2f}")
    if s.holes != holes:
        errs.append(f"отверстий {s.holes}, ждали {holes}")
    if area is not None and abs(s.area - area) / area > 0.002:
        errs.append(f"площадь {s.area:.1f}, ждали {area:.1f}")
    ok &= not errs
    status = "OK " if not errs else "ОШИБКА"
    print(f"{status} {name:28} {s.label():32} контуров в файле {len(d.shapes)}, "
          f"незамкнутых {d.open_chains}  {'; '.join(errs)}")
print("всё сходится" if ok else "есть ошибки")
