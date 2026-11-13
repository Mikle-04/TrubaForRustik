"""Расчёт заготовки, угара, отхода и металлолома по раскладке на листе."""

import json
import math
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sv_ttk

from calc import calculate
from dxf_import import DxfError, load_dxf
from free_nesting import FreeShape
from library import DEFAULT_SECTION, SECTIONS, load_library, matches, save_library, unique_name
from nesting import CANCEL, Cancelled, Circle, Gusset, Plate, Trapezoid, _gusset_points, nest
from sheets import GRADES, SHEET_L, SHEET_W, THICKNESSES, sheet_mass, sheet_name

PART_TYPES = {
    "Фланец": [("Диаметр, мм", "400"), ("Центр. отверстие Ø, мм", "0"),
               ("Отверстия Ø, мм", "0"), ("Кол-во отверстий, шт", "0")],
    "Пластина": [("Длина, мм", "200"), ("Ширина, мм", "100"), ("Ширина вверху, мм", "100")],
    "Косынка": [("Катет 1, мм", "130"), ("Катет 2, мм", "80"), ("Срез углов, мм", "10")],
    "DXF": [],  # контур из файла — свои поля, см. _build_dxf_fields
}

# Отступы от краёв листа в порядке, в котором их принимает nest(); стороны — как на картинке
SIDES = [("left", "слева"), ("right", "справа"), ("top", "сверху"), ("bottom", "снизу")]

METRICS = [
    ("blank", "Масса заготовки", "Мз"),
    ("burn", "Угар", "У"),
    ("waste", "Отход", "Ро"),
    ("scrap", "Металлолом", "Мт"),
]

APP_NAME = "Neptun 3000"
FONT = "Segoe UI"
ACCENT = "#005fb8"
ERROR = "#c42b1c"
OK = "#0f7b0f"
MUTED = "#5f6b7a"


def resource(path):
    """Путь к файлу из assets — и при запуске из исходников, и внутри собранного .exe."""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, path)


def templates_path():
    """Файл шаблонов раскладки — рядом с .exe (или с app.py при запуске из исходников)."""
    base = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
            else os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "шаблоны.json")


def load_templates():
    """{имя: {"gap": "…", "left": "…", "right": "…", "top": "…", "bottom": "…"}};
    нет файла или он битый — пустой список."""
    try:
        with open(templates_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    templates = {}
    for name, t in data.items():
        if not isinstance(t, dict):
            continue
        if "margin" in t:  # первые шаблоны: один отступ на все стороны
            t = {"gap": t.get("gap", ""), **{key: t["margin"] for key, _ in SIDES}}
        templates[name] = t
    return templates


def save_templates(templates):
    with open(templates_path(), "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


def fmt(value, digits=3):
    return f"{value:.{digits}f}".replace(".", ",")


def short(value):
    """Число без лишних нулей и с запятой: 14 → «14», 2.5 → «2,5»."""
    return f"{value:g}".replace(".", ",")


def num(text, name):
    try:
        value = float(text.replace(",", ".").strip())
    except ValueError:
        raise ValueError(f"«{name}»: введите число")
    if value < 0:
        raise ValueError(f"«{name}»: не может быть отрицательным")
    return value


def part_area(kind, values):
    """Площадь детали по размерам, мм² (без отверстий)."""
    if kind == "Фланец":
        d, center, hole, holes = values
        if holes != int(holes):
            raise ValueError("Кол-во отверстий — целое число")
        if center >= d or hole >= d:
            raise ValueError("Отверстие больше фланца")
        area = math.pi / 4 * (d * d - center * center - holes * hole * hole)
        if area <= 0:
            raise ValueError("Отверстия занимают весь фланец")
        return area
    if kind == "Пластина":
        if len(values) == 3:  # трапеция: длина × средняя ширина
            return values[0] * (values[1] + values[2]) / 2
        return values[0] * values[1]
    a, b, cut = values
    pts = _gusset_points(a, b, cut)
    return abs(sum(x1 * y2 - x2 * y1 for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]))) / 2


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_NAME)
        height = min(860, self.winfo_screenheight() - 80)
        self.geometry(f"1200x{height}")
        self.minsize(1000, 560)
        sv_ttk.set_theme("light")
        self._styles()
        self._edit_hotkeys()
        self.layout = None
        self.busy = False
        self.dxf = None           # загруженный чертёж (DxfDrawing)
        self.dxf_path = None
        self.free_parts = {}      # (файл, контур, время изменения) → FreeShape: раскрой кэшируется
        self.values = None
        self.last_entry = None    # последний результат — для кнопки «В библиотеку»
        self.library = load_library()
        self.report = ""

        header = ttk.Frame(self, padding=(20, 12, 20, 0))
        header.pack(fill="x")
        ttk.Label(header, text=APP_NAME, style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="заготовка, угар, отход и металлолом по раскладке деталей",
                  style="Muted.TLabel").pack(side="left", padx=(12, 0), pady=(8, 0))

        footer = ttk.Frame(self, padding=(20, 0, 20, 10))
        footer.pack(side="bottom", fill="x")
        link = ttk.Label(footer, text="От разработчика", style="Link.TLabel", cursor="hand2")
        link.pack(side="left")
        link.bind("<Button-1>", lambda _: self.show_developer())
        self.developer = None

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=20, pady=(8, 8))
        calc_tab = ttk.Frame(tabs, padding=12)
        sheets_tab = ttk.Frame(tabs, padding=12)
        library_tab = ttk.Frame(tabs, padding=12)
        tabs.add(calc_tab, text="  Расчёт  ")
        tabs.add(sheets_tab, text="  Листы  ")
        tabs.add(library_tab, text="  Библиотека  ")
        self._build_calc_tab(calc_tab)
        self._build_sheets_tab(sheets_tab)
        self._build_library_tab(library_tab)
        # Enter — «Рассчитать», но только на вкладке расчёта
        self.bind("<Return>", lambda _: self.run() if tabs.index("current") == 0 else None)

    def _styles(self):
        s = ttk.Style(self)
        s.configure("Title.TLabel", font=(FONT, 20, "bold"))
        s.configure("Muted.TLabel", font=(FONT, 10), foreground=MUTED)
        s.configure("Section.TLabel", font=(FONT, 12, "bold"))
        s.configure("Caption.TLabel", font=(FONT, 10), foreground=MUTED)
        s.configure("Value.TLabel", font=(FONT, 22, "bold"))
        s.configure("Sub.TLabel", font=(FONT, 10), foreground=MUTED)
        s.configure("Info.TLabel", font=(FONT, 11))
        s.configure("Error.TLabel", font=(FONT, 10), foreground=ERROR)
        s.configure("Ok.TLabel", font=(FONT, 10), foreground=OK)
        s.configure("Big.Accent.TButton", font=(FONT, 11, "bold"))
        s.configure("Big.TButton", font=(FONT, 11, "bold"))
        s.configure("PinValue.TLabel", font=(FONT, 16, "bold"))
        s.configure("Link.TLabel", font=(FONT, 10, "underline"), foreground=ACCENT)

    def _edit_hotkeys(self):
        """Ctrl+A/C/V/X во всех полях при любой раскладке.

        В Tk Ctrl+A в поле переводит курсор в начало строки, а при русской раскладке
        Ctrl+A/C/V/X не срабатывают вовсе: Tk видит «ф», «с», «м», «ч». Поэтому клавишу
        определяем по физическому коду (Windows virtual-key), а не по символу.
        """
        virtual = {67: "<<Copy>>", 86: "<<Paste>>", 88: "<<Cut>>"}

        def hotkey(event):
            w = event.widget
            if event.keycode == 65:
                w.select_range(0, "end")
                w.icursor("end")
                return "break"
            if event.keycode in virtual:
                w.event_generate(virtual[event.keycode])
                return "break"
            return None

        for cls in ("TEntry", "TCombobox"):
            for seq in ("<Control-KeyPress>", "<Control-Key-a>", "<Control-Key-A>"):
                self.bind_class(cls, seq, hotkey)

    def show_developer(self):
        """Страница «От разработчика» поверх всего окна; «Назад» или Esc — вернуться."""
        if self.developer is None:
            page = ttk.Frame(self, padding=12)
            ttk.Button(page, text="←  Назад", command=self.hide_developer).pack(anchor="nw")
            self.developer_photo = tk.PhotoImage(file=resource(os.path.join("assets", "developer.png")))
            ttk.Label(page, image=self.developer_photo).pack(expand=True)
            self.developer = page
        self.developer.place(x=0, y=0, relwidth=1, relheight=1)
        self.developer.lift()
        self.bind("<Escape>", lambda _: self.hide_developer())

    def hide_developer(self):
        if self.developer is not None:
            self.developer.place_forget()
        self.unbind("<Escape>")

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=10)
        ttk.Label(card, text=title, style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 4))
        return card

    def _entry(self, parent, row, label, var, width=12):
        """Подпись + поле в строке row; возвращает (подпись, поле)."""
        caption = ttk.Label(parent, text=label)
        caption.grid(row=row, column=0, sticky="w", pady=1, padx=(0, 10))
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, columnspan=3, sticky="w", pady=1)
        return caption, entry

    # ---------- Вкладка «Расчёт» ----------

    def _build_calc_tab(self, root):
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        bg = ttk.Style(self).lookup("TFrame", "background") or "#fafafa"
        form = self._scrollable_column(root, bg)
        self._build_sheet_card(form)
        self._build_part_card(form)
        self._build_nest_card(form)
        # во время расчёта над кнопкой — «Идёт расчёт… N с», а сама кнопка становится «Отмена»
        self.progress = ttk.Label(form, style="Info.TLabel", anchor="center")
        self.run_btn = ttk.Button(form, text="Рассчитать", style="Big.Accent.TButton",
                                  command=lambda: self.cancel() if self.busy else self.run())
        self.run_btn.pack(fill="x", pady=(8, 0), ipady=4)

        self._build_result_card(root)

        preview = self._card(root, "Раскладка на листе")
        preview.grid(row=1, column=1, sticky="nsew", pady=(12, 0))
        preview.columnconfigure(0, weight=1)
        preview.rowconfigure(1, weight=1)
        self.canvas = tk.Canvas(preview, background=bg, highlightthickness=0, height=160)
        self.canvas.grid(row=1, column=0, columnspan=3, sticky="nsew")
        self.canvas.bind("<Configure>", lambda _: self._draw())

    def _scrollable_column(self, root, bg):
        """Левая колонка с полями; полоса прокрутки появляется, только если не влезает."""
        holder = ttk.Frame(root)
        holder.grid(row=0, column=0, rowspan=2, sticky="nsw", padx=(0, 14))
        holder.rowconfigure(0, weight=1)
        canvas = tk.Canvas(holder, background=bg, highlightthickness=0)
        canvas.grid(row=0, column=0, sticky="ns")
        bar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=bar.set)
        form = ttk.Frame(canvas)
        canvas.create_window(0, 0, window=form, anchor="nw")

        def sync(_=None):
            canvas.configure(scrollregion=(0, 0, form.winfo_reqwidth(), form.winfo_reqheight()),
                             width=form.winfo_reqwidth())
            if form.winfo_reqheight() > canvas.winfo_height() > 1:
                bar.grid(row=0, column=1, sticky="ns", padx=(4, 0))
            else:
                bar.grid_remove()
                canvas.yview_moveto(0)

        def wheel(event):
            if bar.winfo_ismapped():
                canvas.yview_scroll(int(-event.delta / 120), "units")

        form.bind("<Configure>", sync)
        canvas.bind("<Configure>", sync)
        holder.bind("<Enter>", lambda _: self.bind_all("<MouseWheel>", wheel))
        holder.bind("<Leave>", lambda _: self.unbind_all("<MouseWheel>"))
        return form

    def _build_sheet_card(self, form):
        card = self._card(form, "Лист")
        card.pack(fill="x")
        self.thickness = tk.StringVar(value="14")
        self.grade = tk.StringVar(value="09Г2С")
        self.sheet_w = tk.StringVar(value=str(SHEET_W))
        self.sheet_l = tk.StringVar(value=str(SHEET_L))
        self.sheet_m = tk.StringVar()
        ttk.Label(card, text="Толщина, мм").grid(row=1, column=0, sticky="w", pady=1)
        ttk.Combobox(card, textvariable=self.thickness, width=10, values=[str(t) for t in THICKNESSES]
                     ).grid(row=1, column=1, columnspan=3, sticky="w", pady=1)
        ttk.Label(card, text="Марка стали").grid(row=2, column=0, sticky="w", pady=1)
        ttk.Combobox(card, textvariable=self.grade, width=10, state="readonly", values=list(GRADES)
                     ).grid(row=2, column=1, columnspan=3, sticky="w", pady=1)
        ttk.Label(card, text="Ширина × длина, мм").grid(row=3, column=0, sticky="w", pady=1, padx=(0, 10))
        ttk.Entry(card, textvariable=self.sheet_w, width=6).grid(row=3, column=1, sticky="w", pady=1)
        ttk.Label(card, text="×").grid(row=3, column=2, padx=4)
        ttk.Entry(card, textvariable=self.sheet_l, width=6).grid(row=3, column=3, sticky="w", pady=1)
        self._entry(card, 4, "Масса листа, кг", self.sheet_m)
        ttk.Button(card, text="↺", width=3, command=self._update_sheet_mass).grid(
            row=4, column=4, sticky="w", padx=(6, 0))
        for var in (self.thickness, self.grade, self.sheet_w, self.sheet_l):
            var.trace_add("write", lambda *_: self._update_sheet_mass())
        for var in (self.thickness, self.grade):
            var.trace_add("write", lambda *_: self._update_mass())
        self._update_sheet_mass()

    def _build_part_card(self, form):
        card = self._card(form, "Деталь")
        card.pack(fill="x", pady=(8, 0))
        self.part_type = tk.StringVar(value="Фланец")
        types = ttk.Frame(card)
        types.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 4))
        for name in PART_TYPES:
            ttk.Radiobutton(types, text=name, value=name, variable=self.part_type,
                            style="Toggle.TButton", command=self._show_dims, width=8
                            ).pack(side="left", padx=(0, 6))
        # Поля размеров всех типов создаются сразу (строки 2–5), видны только поля выбранного
        self.dims, self.dim_widgets = {}, {}
        for name, fields in PART_TYPES.items():
            self.dims[name] = [tk.StringVar(value=v) for _, v in fields]
            self.dim_widgets[name] = [w for i, ((label, _), var) in enumerate(zip(fields, self.dims[name]))
                                      for w in self._entry(card, 2 + i, label, var)]
        self.dim_widgets["DXF"] = self._build_dxf_fields(card)
        # Пластина с разными основаниями (коническая) — трапеция; третье поле только при включённом
        self.trapezoid = tk.BooleanVar(value=False)
        switch = ttk.Checkbutton(card, text="Разные основания (трапеция)", variable=self.trapezoid,
                                 style="Switch.TCheckbutton",
                                 command=lambda: (self._update_plate_fields(), self._update_mass()))
        switch.grid(row=5, column=0, columnspan=4, sticky="w", pady=(4, 0))
        self.dim_widgets["Пластина"].append(switch)
        for var in (v for vs in self.dims.values() for v in vs):
            var.trace_add("write", lambda *_: self._update_mass())
        self.part_mass = tk.StringVar()
        self.drawing_qty = tk.StringVar(value="1")
        # строки 2–5 — под размеры детали (до четырёх полей)
        self._entry(card, 6, "Масса детали, кг", self.part_mass)
        ttk.Button(card, text="↺", width=3, command=self._reset_mass).grid(
            row=6, column=4, sticky="w", padx=(6, 0))
        self.mass_info = ttk.Label(card, style="Muted.TLabel", justify="left")
        self.mass_info.grid(row=7, column=0, columnspan=5, sticky="w", pady=1)
        self._entry(card, 8, "Кол-во на чертеже, шт", self.drawing_qty)
        # Масса считается по размерам (у DXF — по контуру), пока её не впишут руками
        self.mass_auto = True
        self._mass_by_program = False
        self.part_mass.trace_add("write", lambda *_: self._mass_edited())
        self._show_dims()

    def _build_dxf_fields(self, card):
        """Строки 2–4 для детали из DXF: файл, выбор контура, площадь и масса."""
        open_btn = ttk.Button(card, text="Открыть DXF…", command=self._open_dxf)
        open_btn.grid(row=2, column=0, sticky="w", pady=1)
        self.dxf_file = ttk.Label(card, text="файл не выбран", style="Muted.TLabel")
        self.dxf_file.grid(row=2, column=1, columnspan=4, sticky="w", pady=1)
        contour = ttk.Label(card, text="Контур")
        contour.grid(row=3, column=0, sticky="w", pady=1)
        self.dxf_box = ttk.Combobox(card, state="readonly", width=26)
        self.dxf_box.grid(row=3, column=1, columnspan=4, sticky="w", pady=1)
        self.dxf_box.bind("<<ComboboxSelected>>", lambda _: self._update_dxf_info())
        self.dxf_info = ttk.Label(card, style="Muted.TLabel", justify="left")
        self.dxf_info.grid(row=4, column=0, columnspan=5, sticky="w", pady=1)
        return [open_btn, self.dxf_file, contour, self.dxf_box, self.dxf_info]

    def _open_dxf(self):
        path = filedialog.askopenfilename(title="Деталь в DXF",
                                          filetypes=[("Чертёж DXF", "*.dxf"), ("Все файлы", "*.*")])
        if path:
            self.load_dxf_file(path)

    def load_dxf_file(self, path):
        try:
            drawing = load_dxf(path)
        except DxfError as e:
            self.error.configure(text=f"DXF: {e}")
            return
        self.error.configure(text="")
        self.dxf, self.dxf_path = drawing, path
        self.dxf_file.configure(text=drawing.name)
        self.dxf_box.configure(values=[f"{i + 1}. {s.label()}" for i, s in enumerate(drawing.shapes)])
        self.dxf_box.current(drawing.default)
        self.mass_auto = True
        self._update_dxf_info()

    def _dxf_shape(self):
        if not self.dxf:
            return None
        return self.dxf.shapes[max(self.dxf_box.current(), 0)]

    def _dxf_mass(self, shape):
        """Масса по контуру: площадь (без отверстий) × толщина × плотность."""
        thickness = num(self.thickness.get(), "Толщина")
        return shape.area * thickness * GRADES[self.grade.get()] / 1e9

    def _update_dxf_info(self):
        """Площадь, масса по контуру и замечания по файлу; масса сама уходит в поле."""
        shape = self._dxf_shape()
        if shape is None:
            return
        lines = [f"Площадь {fmt(shape.area / 1e6, 4)} м²"]
        try:
            mass = self._dxf_mass(shape)
            lines[0] += f" · масса по контуру {fmt(mass)} кг"
            if self.mass_auto and self.part_type.get() == "DXF":
                self._mass_by_program = True
                self.part_mass.set(fmt(mass))
                self._mass_by_program = False
        except ValueError:
            pass
        if not self.mass_auto:
            lines.append("В расчёте — масса, вписанная вручную (↺ — вернуть по контуру)")
        if len(self.dxf.shapes) > 1:
            lines.append(f"В файле контуров: {len(self.dxf.shapes)}, выбран {self.dxf_box.current() + 1}")
        if self.dxf.bends:
            lines.append(f"Линий гиба: {self.dxf.bends} — не режутся, пропущены")
        if self.dxf.open_chains:
            lines.append(f"Пропущено незамкнутых линий: {self.dxf.open_chains}")
        self.dxf_info.configure(text="\n".join(lines))

    def _mass_edited(self):
        """Масса изменена руками — больше не подменяем её расчётной."""
        if self._mass_by_program or (self.part_type.get() == "DXF" and not self.dxf):
            return
        self.mass_auto = False
        self._update_mass()

    def _reset_mass(self):
        self.mass_auto = True
        self._update_mass()

    def _dim_values(self, kind):
        """Размеры детали из полей; пустое поле отверстий фланца — нет отверстий."""
        fields = list(zip(PART_TYPES[kind], self.dims[kind]))
        if kind == "Пластина" and not self.trapezoid.get():
            fields = fields[:2]
        return [num(v.get().strip() or ("0" if kind == "Фланец" and i else ""), label)
                for i, ((label, _), v) in enumerate(fields)]

    def _update_mass(self):
        """Масса по размерам (у DXF — по контуру) уходит в поле, пока её не вписали руками."""
        kind = self.part_type.get()
        if kind == "DXF":
            self.mass_info.grid_remove()
            self._update_dxf_info()
            return
        self.mass_info.grid()
        try:
            thickness = num(self.thickness.get(), "Толщина")
            mass = part_area(kind, self._dim_values(kind)) * thickness * GRADES[self.grade.get()] / 1e9
        except (ValueError, ZeroDivisionError):
            self.mass_info.configure(text="")
            return
        if self.mass_auto:
            self._mass_by_program = True
            self.part_mass.set(fmt(mass))
            self._mass_by_program = False
        text = f"По размерам: {fmt(mass)} кг"
        if not self.mass_auto:
            text += "\nВ расчёте — масса, вписанная вручную (↺ — вернуть по размерам)"
        self.mass_info.configure(text=text)

    def _build_nest_card(self, form):
        card = self._card(form, "Раскладка")
        card.pack(fill="x", pady=(8, 0))
        self.gap = tk.StringVar(value="5")
        self.margins = {key: tk.StringVar(value="0") for key, _ in SIDES}
        # Шаблон — сохранённые зазор и отступы; имя вписывается прямо в список
        self.templates = load_templates()
        self.template = tk.StringVar()
        ttk.Label(card, text="Шаблон").grid(row=1, column=0, sticky="w", pady=1)
        self.template_box = ttk.Combobox(card, textvariable=self.template, width=22,
                                         values=sorted(self.templates))
        self.template_box.grid(row=1, column=1, columnspan=3, sticky="w", pady=1)
        self.template_box.bind("<<ComboboxSelected>>", lambda _: self._apply_template())
        buttons = ttk.Frame(card)
        buttons.grid(row=2, column=1, columnspan=4, sticky="w", pady=(2, 4))
        ttk.Button(buttons, text="Сохранить", command=self._save_template).pack(side="left")
        ttk.Button(buttons, text="Удалить", command=self._delete_template).pack(side="left", padx=(6, 0))
        self.template_note = ttk.Label(buttons, style="Ok.TLabel")
        self.template_note.pack(side="left", padx=(8, 0))
        self._entry(card, 3, "Зазор между деталями, мм", self.gap)
        # отступы парами, как «Ширина × длина» у листа
        for row, pair in ((4, SIDES[:2]), (5, SIDES[2:])):
            (k1, n1), (k2, n2) = pair
            ttk.Label(card, text=f"Отступ {n1} / {n2}, мм").grid(
                row=row, column=0, sticky="w", pady=1, padx=(0, 10))
            fields = ttk.Frame(card)
            fields.grid(row=row, column=1, columnspan=3, sticky="w", pady=1)
            ttk.Entry(fields, textvariable=self.margins[k1], width=6).pack(side="left")
            ttk.Label(fields, text="/").pack(side="left", padx=4)
            ttk.Entry(fields, textvariable=self.margins[k2], width=6).pack(side="left")
        # Общий рез: соседние детали режутся одной линией — без зазора в местах касания
        self.common = tk.BooleanVar(value=False)
        self.common_check = ttk.Checkbutton(card, text="Общий рез", variable=self.common,
                                            style="Switch.TCheckbutton")
        self.common_check.grid(row=6, column=0, sticky="w", pady=(6, 0))
        self.common_note = ttk.Label(card, style="Caption.TLabel")
        self.common_note.grid(row=6, column=1, columnspan=3, sticky="w", pady=(6, 0))
        self.manual = tk.BooleanVar(value=False)
        self.manual_count = tk.StringVar()
        ttk.Checkbutton(card, text="Кол-во на листе вручную", variable=self.manual,
                        style="Switch.TCheckbutton", command=self._toggle_manual
                        ).grid(row=7, column=0, sticky="w", pady=(6, 2))
        self.manual_entry = ttk.Entry(card, textvariable=self.manual_count, width=12, state="disabled")
        self.manual_entry.grid(row=7, column=1, columnspan=3, sticky="w", pady=(6, 2))
        self._update_common()

    def _apply_template(self):
        """Выбран шаблон — его зазор и отступы уходят в поля; править их можно, шаблон не меняется."""
        t = self.templates.get(self.template.get())
        if t:
            self.gap.set(t.get("gap", ""))
            for key, _ in SIDES:
                self.margins[key].set(t.get(key, "0"))
            self.template_note.configure(text="")

    def _read_margins(self):
        """(слева, справа, сверху, снизу) в мм; пустое поле — без отступа."""
        return tuple(num(self.margins[key].get().strip() or "0", f"Отступ {name}") for key, name in SIDES)

    @staticmethod
    def _as_template(gap, margins):
        return {"gap": short(gap), **{key: short(m) for (key, _), m in zip(SIDES, margins)}}

    def _template_values(self):
        return self._as_template(num(self.gap.get(), "Зазор"), self._read_margins())

    def _save_template(self):
        self.error.configure(text="")
        name = self.template.get().strip()
        if not name:
            self.error.configure(text="Впишите имя шаблона в поле «Шаблон»")
            return
        try:
            values = self._template_values()
        except ValueError as e:
            self.error.configure(text=str(e))
            return
        if name in self.templates and self.templates[name] != values and not messagebox.askyesno(
                APP_NAME, f"Шаблон «{name}» уже есть. Заменить его текущими значениями?", parent=self):
            return
        templates = {**self.templates, name: values}
        try:
            save_templates(templates)
        except OSError as e:
            self.error.configure(text=f"Не удалось сохранить шаблон: {e}")
            return
        self.templates = templates
        self.template.set(name)
        self.template_box.configure(values=sorted(templates))
        self.template_note.configure(text="✓ Сохранено")

    def _delete_template(self):
        self.error.configure(text="")
        name = self.template.get().strip()
        if name not in self.templates:
            self.error.configure(text="Выберите в списке шаблон, который нужно удалить")
            return
        if not messagebox.askyesno(APP_NAME, f"Удалить шаблон «{name}»?", parent=self):
            return
        templates = {k: v for k, v in self.templates.items() if k != name}
        try:
            save_templates(templates)
        except OSError as e:
            self.error.configure(text=f"Не удалось удалить шаблон: {e}")
            return
        self.templates = templates
        self.template.set("")
        self.template_box.configure(values=sorted(templates))
        self.template_note.configure(text="✓ Удалено")

    def _nest_title(self, gap, margins):
        """«зазор 14 мм, отступы: слева 10, справа 10, сверху 20, снизу 0 мм (шаблон «…»)»."""
        if len(set(margins)) == 1:
            text = f"зазор {short(gap)} мм, отступ {short(margins[0])} мм со всех сторон"
        else:
            sides = ", ".join(f"{name} {short(m)}" for (_, name), m in zip(SIDES, margins))
            text = f"зазор {short(gap)} мм, отступы: {sides} мм"
        name = self.template.get().strip()
        if self.templates.get(name) == self._as_template(gap, margins):
            text += f" (шаблон «{name}»)"
        if self._common_on():
            text += ", общий рез"
        return text

    def _build_result_card(self, root):
        card = self._card(root, "Результат")
        card.grid(row=0, column=1, sticky="nsew")
        card.columnconfigure((0, 1, 2, 3), weight=1, uniform="metric")

        self.summary = ttk.Label(card, style="Info.TLabel", justify="left",
                                 text="Заполните данные слева и нажмите «Рассчитать» (или Enter)")
        self.summary.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 10))

        self.metric_labels = {}
        for col, (key, title, short) in enumerate(METRICS):
            tile = ttk.Frame(card, style="Card.TFrame", padding=(12, 10))
            tile.grid(row=2, column=col, sticky="nsew", padx=(0 if col == 0 else 8, 0))
            ttk.Label(tile, text=f"{title} · {short}", style="Caption.TLabel").pack(anchor="w")
            value = ttk.Label(tile, text="—", style="Value.TLabel")
            value.pack(anchor="w")
            sub = ttk.Label(tile, text="на 1 деталь", style="Sub.TLabel")
            sub.pack(anchor="w")
            self.metric_labels[key] = (value, sub)

        self.warning = ttk.Label(card, style="Error.TLabel", justify="left")
        self.warning.grid(row=3, column=0, columnspan=4, sticky="w", pady=(8, 0))
        # ошибки ввода — тут же, под результатом
        self.error = self.warning
        card.bind("<Configure>", lambda e: (self.warning.configure(wraplength=e.width - 40),
                                            self.summary.configure(wraplength=e.width - 40)))

        buttons = ttk.Frame(card)
        buttons.grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Копировать текст", command=self.copy_text).pack(side="left")
        ttk.Button(buttons, text="Копировать для Excel", command=self.copy_excel).pack(
            side="left", padx=8)
        ttk.Button(buttons, text="Окно поверх всех", command=self.toggle_pin).pack(side="left")
        ttk.Button(buttons, text="В библиотеку", command=self.add_to_library).pack(side="left", padx=(6, 0))
        self.copied = ttk.Label(buttons, style="Ok.TLabel")
        self.copied.pack(side="left", padx=8)
        self.pin = None

    def _show_dims(self):
        for name, widgets in self.dim_widgets.items():
            for w in widgets:
                if name == self.part_type.get():
                    w.grid()
                else:
                    w.grid_remove()
        self._update_plate_fields()
        # другая деталь — масса снова по её размерам
        self.mass_auto = True
        self._update_mass()
        if hasattr(self, "common_check"):
            self._update_common()

    def _update_plate_fields(self):
        """Трапеция: «Ширина» становится «Ширина внизу», появляется «Ширина вверху»."""
        widgets = self.dim_widgets["Пластина"]
        on = self.trapezoid.get()
        widgets[2].configure(text="Ширина внизу, мм" if on else "Ширина, мм")
        for w in widgets[4:6]:
            if on and self.part_type.get() == "Пластина":
                w.grid()
            else:
                w.grid_remove()

    def _update_common(self):
        """Что даёт общий рез для выбранной детали; у кругов общей линии нет — выключен."""
        notes = {
            "Фланец": "у кругов нет общей линии",
            "Пластина": "пластины встык",
            "Косынка": "пары без зазора по гипотенузе",
            "DXF": "пары без зазора в местах касания",
        }
        kind = self.part_type.get()
        self.common_check.configure(state="disabled" if kind == "Фланец" else "normal")
        self.common_note.configure(text=notes[kind])

    def _common_on(self):
        return self.common.get() and self.part_type.get() != "Фланец"

    def _toggle_manual(self):
        self.manual_entry.configure(state="normal" if self.manual.get() else "disabled")

    def _sheet_params(self):
        thickness = num(self.thickness.get(), "Толщина")
        width = num(self.sheet_w.get(), "Ширина листа")
        length = num(self.sheet_l.get(), "Длина листа")
        if 0 in (thickness, width, length):
            raise ValueError("Толщина и размеры листа должны быть больше нуля")
        return thickness, self.grade.get(), width, length

    def _update_sheet_mass(self):
        """Масса по формуле; при ошибке ввода (ещё печатают) поле просто очищается."""
        try:
            thickness, grade, width, length = self._sheet_params()
            self.sheet_m.set(fmt(sheet_mass(thickness, grade, width, length)))
        except ValueError:
            self.sheet_m.set("")

    def _make_part(self):
        kind = self.part_type.get()
        if kind == "DXF":
            shape = self._dxf_shape()
            if shape is None:
                raise ValueError("Выберите DXF-файл с деталью")
            key = (self.dxf_path, self.dxf_box.current(), os.path.getmtime(self.dxf_path))
            part = self.free_parts.setdefault(key, FreeShape(shape.polygon))
            part.common = self._common_on()
            return part, f"{self.dxf.name} ({shape.width:.0f}×{shape.height:.0f})"
        values = self._dim_values(kind)
        if any(v == 0 for v in values[:1 if kind == "Фланец" else 2]):
            raise ValueError("Размеры детали должны быть больше нуля")
        if kind == "Фланец":
            d, center, hole, holes = values
            part_area(kind, values)
            title = f"фланец Ø{d:g}"
            if center:
                title += f", центр Ø{center:g}"
            if hole and holes:
                title += f", {holes:g} отв. Ø{hole:g}"
            return Circle(d), title
        if kind == "Пластина":
            if len(values) == 3 and values[1] != values[2]:
                length, bottom, top = values
                if top == 0:
                    raise ValueError("Размеры детали должны быть больше нуля")
                return (Trapezoid(length, bottom, top, self._common_on()),
                        f"пластина {length:g}×{bottom:g}/{top:g} (трапеция)")
            return Plate(values[0], values[1], self._common_on()), f"пластина {values[0]:g}×{values[1]:g}"
        a, b, cut = values
        if cut >= min(a, b):
            raise ValueError("Срез углов слишком большой для такой косынки")
        return Gusset(a, b, cut, common=self._common_on()), f"косынка {a:g}×{b:g}, срез углов {cut:g}"

    def run(self):
        self.error.configure(text="")
        self.copied.configure(text="")
        try:
            thickness, grade, width, length = self._sheet_params()
            m_sheet = num(self.sheet_m.get(), "Масса листа")
            if m_sheet == 0:
                raise ValueError("Укажите массу листа")
            part, part_title = self._make_part()
            kind = self.part_type.get()
            part_mass = num(self.part_mass.get(), "Масса детали")
            qty = num(self.drawing_qty.get(), "Количество на чертеже")
            gap = num(self.gap.get(), "Зазор")
            margins = self._read_margins()
            if part_mass == 0:
                raise ValueError("Укажите массу детали")
            if margins[0] + margins[1] >= length or margins[2] + margins[3] >= width:
                raise ValueError("Отступы от краёв не оставляют места на листе")
            nest_title = self._nest_title(gap, margins)
            manual = int(num(self.manual_count.get(), "Количество на листе")) if self.manual.get() else None
        except ValueError as e:
            self.error.configure(text=str(e))
            return

        # Раскрой сложной детали считается секунды — в фоне, чтобы окно не зависало
        if self.busy:
            return
        self.busy = True
        self.configure(cursor="watch")
        previous = self.summary.cget("text")
        self.summary.configure(text="Идёт расчёт…")
        CANCEL.clear()
        self.run_btn.configure(text="Отмена", style="Big.TButton", state="normal")
        self.progress.pack(fill="x", pady=(6, 0), before=self.run_btn)
        started = time.monotonic()
        result = {}

        def work():
            try:
                result["layout"] = nest(part, length, width, gap, margins)
            except Cancelled:
                result["cancelled"] = True
            except Exception as e:
                result["error"] = e

        thread = threading.Thread(target=work, daemon=True)
        thread.start()

        def wait():
            if thread.is_alive():
                seconds = int(time.monotonic() - started)
                text = "Отменяю…" if CANCEL.is_set() else f"Идёт расчёт… {seconds} с"
                self.progress.configure(text=text)
                self.after(100, wait)
                return
            self.busy = False
            self.configure(cursor="")
            self.progress.pack_forget()
            self.run_btn.configure(text="Рассчитать", style="Big.Accent.TButton", state="normal")
            if "cancelled" in result:
                # прошлый результат остаётся на экране как был
                self.summary.configure(text=previous)
                self.error.configure(text="Расчёт отменён")
                return
            if "error" in result:
                self.summary.configure(text="")
                self.error.configure(text=f"Не удалось разложить деталь: {result['error']}")
                return
            self.layout = result["layout"]
            self.sheet_size = (length, width)
            self.sheet_margins = margins
            self._draw()
            self._show_result(thickness, grade, width, length, m_sheet, part_title, part_mass, qty, manual,
                              nest_title, kind)

        wait()

    def cancel(self):
        CANCEL.set()
        self.run_btn.configure(state="disabled")

    def _show_result(self, thickness, grade, width, length, m_sheet, part_title, part_mass, qty, manual,
                     nest_title, kind):
        self.last_entry = None
        count = manual if manual is not None else self.layout.count
        source = (f"вручную (программа разложила {self.layout.count})" if manual is not None
                  else "раскладка программы")
        if count == 0:
            self.summary.configure(text="")
            self.error.configure(text="Деталь не помещается на лист")
            return

        title = sheet_name(thickness, grade, width, length)
        r = calculate(m_sheet, count, part_mass)
        self.values = [title, m_sheet, part_title, part_mass, count,
                       r.blank, r.burn, r.waste, r.scrap, qty]
        self.last_entry = {
            "name": f"{part_title[:1].upper()}{part_title[1:]} · {thickness:g} мм {grade}",
            "section": DEFAULT_SECTION.get(kind, SECTIONS[-1]),
            "sheet": title, "sheet_mass": m_sheet, "part": part_title, "count": count, "nest": nest_title,
            "mass": part_mass, "blank": r.blank, "burn": r.burn, "waste": r.waste, "scrap": r.scrap,
            "qty": qty, "saved": time.strftime("%d.%m.%Y"),
        }
        formula_mass = sheet_mass(thickness, grade, width, length)
        mass_note = ("" if abs(m_sheet - formula_mass) < 0.0005
                     else f" (вручную, по формуле {fmt(formula_mass)})")

        self.summary.configure(text=(
            f"Лист {title} — {fmt(m_sheet)} кг{mass_note}\n"
            f"Деталь: {part_title} — {fmt(part_mass)} кг\n"
            f"На листе: {count} шт — {source}\n"
            f"Раскладка: {nest_title}"))
        for key, _, _ in METRICS:
            value, sub = self.metric_labels[key]
            v = getattr(r, key)
            value.configure(text=f"{fmt(v)} кг", foreground=ERROR if v < 0 else "")
            sub.configure(text="на 1 деталь" if qty == 1
                          else f"на 1 деталь\n{qty:g} шт: {fmt(v * qty)} кг")
        self.warning.configure(text="" if r.waste >= 0 else
                               "⚠ Отход отрицательный: масса детали больше массы заготовки. "
                               "Проверьте массу детали, толщину листа или количество на листе.")

        lines = [
            f"Лист:        {title}",
            f"Масса листа: {fmt(m_sheet)} кг{mass_note}",
            f"Деталь:      {part_title}",
            f"Масса детали (Мд): {fmt(part_mass)} кг",
            f"На листе:    {count} шт — {source}",
            f"Раскладка:   {nest_title}",
            "",
            "На 1 деталь:",
        ] + [f"  {t} ({s}) = {fmt(getattr(r, k))} кг" for k, t, s in METRICS]
        if qty != 1:
            lines += ["", f"На {qty:g} шт по чертежу:",
                      f"  Масса детали (Мд) = {fmt(part_mass * qty)} кг"] + [
                f"  {t} ({s}) = {fmt(getattr(r, k) * qty)} кг" for k, t, s in METRICS]
        self.report = "\n".join(lines)
        self._update_pin()

    # ---------- Маленькое окно результата поверх всех окон ----------

    def toggle_pin(self):
        """Окно с результатом поверх всех программ — чтобы переписывать цифры, не переключаясь."""
        if self.pin is not None:
            self._close_pin()
            return
        pin = tk.Toplevel(self)
        pin.title(f"{APP_NAME} — результат")
        pin.attributes("-topmost", True)
        pin.configure(background=ttk.Style(self).lookup("TFrame", "background") or "#fafafa")
        pin.protocol("WM_DELETE_WINDOW", self._close_pin)
        pin.geometry(f"+{self.winfo_rootx() + self.winfo_width() - 380}+{self.winfo_rooty() + 40}")
        self.pin = pin
        self.pin_body = ttk.Frame(pin, padding=12)
        self.pin_body.pack(fill="both", expand=True)
        self._update_pin()

    def _close_pin(self):
        if self.pin is not None:
            self.pin.destroy()
            self.pin = None

    def _update_pin(self):
        if self.pin is None:
            return
        body = self.pin_body
        for child in body.winfo_children():
            child.destroy()
        if not self.values:
            ttk.Label(body, text="Нажмите «Рассчитать» в программе —\nрезультат появится здесь",
                      style="Muted.TLabel", justify="left").grid(row=0, column=0)
            return
        sheet, m_sheet, part, m_part, count, blank, burn, waste, scrap, qty = self.values
        ttk.Label(body, style="Muted.TLabel", justify="left",
                  text=f"{sheet}\n{part}\nНа листе: {count} шт"
                  ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 8))
        self._value_grid(body, 1, m_part, (blank, burn, waste, scrap), qty)

    def _value_grid(self, body, top, m_part, metrics, qty):
        """Мд, Мз, У, Ро, Мт на 1 шт и на qty шт, начиная со строки top; клик по числу — копировать."""
        status = ttk.Label(body, style="Muted.TLabel", text="Клик по числу — скопировать")
        cols = [("на 1 шт", 1)] + ([(f"на {qty:g} шт", qty)] if qty != 1 else [])
        if len(cols) > 1:
            for c, (title, _) in enumerate(cols, start=1):
                ttk.Label(body, text=title, style="Caption.TLabel").grid(
                    row=top, column=c, sticky="e", padx=(16, 0))
        rows = [("Масса детали", "Мд", m_part)] + [
            (name, short, v) for (_, name, short), v in zip(METRICS, metrics)]
        for row, (name, short, v) in enumerate(rows, start=top + 1):
            ttk.Label(body, text=f"{name} · {short}", style="Info.TLabel").grid(row=row, column=0, sticky="w")
            for c, (_, k) in enumerate(cols, start=1):
                text = fmt(v * k)
                value = ttk.Label(body, text=text, style="PinValue.TLabel", cursor="hand2",
                                  foreground=ERROR if v < 0 else "")
                value.grid(row=row, column=c, sticky="e", padx=(16, 0))
                value.bind("<Button-1>", lambda _, t=text, n=short: (
                    self.clipboard_clear(), self.clipboard_append(t),
                    status.configure(text=f"✓ Скопировано: {n} = {t}", style="Ok.TLabel")))
        status.grid(row=len(rows) + top + 1, column=0, columnspan=3, sticky="w", pady=(8, 0))

    def _to_clipboard(self, text, note):
        self.clipboard_clear()
        self.clipboard_append(text)
        self.copied.configure(text=note)

    def copy_text(self):
        if self.report:
            self._to_clipboard(self.report, "✓ Скопировано")

    def copy_excel(self):
        """Одна строка через Tab — вставляется в Excel по ячейкам."""
        if not self.values:
            return
        sheet, m_sheet, part, m_part, count, blank, burn, waste, scrap, qty = self.values
        header = ["Лист", "Масса листа", "Деталь", "Мд", "Кол-во на листе",
                  "Мз", "У", "Ро", "Мт", "Кол-во на чертеже", "Ро × кол-во", "Мт × кол-во"]
        row = [sheet, fmt(m_sheet), part, fmt(m_part), str(count),
               fmt(blank), fmt(burn), fmt(waste), fmt(scrap), f"{qty:g}",
               fmt(waste * qty), fmt(scrap * qty)]
        self._to_clipboard("\t".join(header) + "\n" + "\t".join(row),
                           "✓ Скопировано — вставьте в Excel (Ctrl+V)")

    def _draw(self):
        c = self.canvas
        c.delete("all")
        cw, ch = c.winfo_width(), c.winfo_height()
        sheet_l, sheet_w = getattr(self, "sheet_size", (SHEET_L, SHEET_W))
        pad = 4
        scale = min((cw - 2 * pad) / sheet_l, (ch - 2 * pad - 22) / sheet_w)
        if scale <= 0:
            return
        ox, oy = pad, pad
        c.create_rectangle(ox, oy, ox + sheet_l * scale, oy + sheet_w * scale,
                           fill="#e8edf2", outline="#9aa7b4")
        if not self.layout:
            c.create_text(ox + sheet_l * scale / 2, oy + sheet_w * scale / 2,
                          text="Здесь появится раскладка", fill=MUTED, font=(FONT, 11))
            return
        left, right, top, bottom = getattr(self, "sheet_margins", (0, 0, 0, 0))
        if any((left, right, top, bottom)):
            # пунктир — граница отступов от краёв листа
            c.create_rectangle(ox + left * scale, oy + top * scale,
                               ox + (sheet_l - right) * scale, oy + (sheet_w - bottom) * scale,
                               outline=MUTED, dash=(4, 3))
        for shape in self.layout.shapes:
            if shape[0] == "circle":
                _, x, y, r = shape
                c.create_oval(ox + (x - r) * scale, oy + (y - r) * scale,
                              ox + (x + r) * scale, oy + (y + r) * scale,
                              fill="#cfe3fa", outline=ACCENT)
            else:
                pts = [v for x, y in shape[1] for v in (ox + x * scale, oy + y * scale)]
                hole = shape[0] == "hole"
                c.create_polygon(pts, fill="#e8edf2" if hole else "#cfe3fa", outline=ACCENT)
        c.create_text(ox, oy + sheet_w * scale + 6, anchor="nw", fill=MUTED, font=(FONT, 10),
                      text=f"Лист {sheet_l:g}×{sheet_w:g} мм · программа разложила "
                           f"{self.layout.count} шт")

    # ---------- Вкладка «Листы» ----------

    def _build_sheets_tab(self, root):
        card = self._card(root, f"Массы листов {SHEET_W}×{SHEET_L} мм")
        card.pack(anchor="nw")
        cols = ["Толщина, мм"] + [f"{g}, кг" for g in GRADES]
        tree = ttk.Treeview(card, columns=cols, show="headings", height=len(THICKNESSES))
        for col in cols:
            tree.heading(col, text=col)
            tree.column(col, width=150, anchor="center")
        rows = []
        for t in THICKNESSES:
            row = [str(t)] + [fmt(sheet_mass(t, g)) for g in GRADES]
            rows.append(row)
            tree.insert("", "end", values=row)
        tree.grid(row=1, column=0, columnspan=3, sticky="w")
        ttk.Label(card, style="Muted.TLabel",
                  text="Плотность: " + ", ".join(f"{g} — {d} кг/м³" for g, d in GRADES.items())
                  ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))
        note = ttk.Label(card, style="Ok.TLabel")
        ttk.Button(card, text="Копировать таблицу для Excel", command=lambda: (
            self.clipboard_clear(),
            self.clipboard_append("\n".join("\t".join(r) for r in [cols] + rows)),
            note.configure(text="✓ Скопировано"),
        )).grid(row=3, column=0, sticky="w", pady=(8, 0))
        note.grid(row=3, column=1, sticky="w", padx=8, pady=(8, 0))

    # ---------- Вкладка «Библиотека» ----------

    def add_to_library(self):
        """Посчитанная деталь — в библиотеку; название придумывается само, его можно поменять там."""
        if not self.last_entry:
            self.copied.configure(text="Сначала нажмите «Рассчитать»")
            return
        entry = dict(self.last_entry, name=unique_name(self.last_entry["name"], self.library))
        self.library.append(entry)
        if not self._save_library():
            self.library.pop()
            return
        self.copied.configure(text=f"✓ В библиотеке: {entry['name']}")
        self._fill_library(select=entry)

    def _save_library(self):
        try:
            save_library(self.library)
            return True
        except OSError as e:
            messagebox.showerror(APP_NAME, f"Не удалось сохранить библиотеку: {e}")
            return False

    def _build_library_tab(self, root):
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)

        top = ttk.Frame(root)
        top.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 8))
        ttk.Label(top, text="Поиск").pack(side="left")
        self.lib_search = tk.StringVar()
        ttk.Entry(top, textvariable=self.lib_search, width=28).pack(side="left", padx=(8, 8))
        self.lib_found = ttk.Label(top, style="Muted.TLabel", width=18)
        self.lib_found.pack(side="left", padx=(0, 8))
        self.lib_search.trace_add("write", lambda *_: self._fill_library())
        self.lib_filter = tk.StringVar(value="Все")
        for name in ["Все"] + SECTIONS:
            ttk.Radiobutton(top, text=name, value=name, variable=self.lib_filter, style="Toggle.TButton",
                            command=self._fill_library).pack(side="left", padx=(0, 6))

        holder = ttk.Frame(root)
        holder.grid(row=1, column=0, sticky="nsew")
        holder.columnconfigure(0, weight=1)
        holder.rowconfigure(0, weight=1)
        cols = [("mass", "Мд, кг")] + [(key, f"{s}, кг") for key, _, s in METRICS]
        tree = ttk.Treeview(holder, columns=[c for c, _ in cols], selectmode="browse")
        tree.heading("#0", text="Название")
        tree.column("#0", width=260, minwidth=180, stretch=True)
        for c, title in cols:
            tree.heading(c, text=title)
            tree.column(c, width=70, minwidth=60, anchor="e", stretch=False)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        tree.bind("<<TreeviewSelect>>", lambda _: self._show_library_item())
        self.lib_tree = tree

        card = self._card(root, "Деталь")
        card.grid(row=1, column=1, sticky="nsew", padx=(12, 0))
        self.lib_item = None
        self.lib_body = ttk.Frame(card)
        self.lib_body.grid(row=1, column=0, columnspan=4, sticky="nsew")
        self._fill_library()

    def _fill_library(self, select=None):
        """Список по разделам с учётом поиска и фильтра; select — какую деталь выделить."""
        tree = self.lib_tree
        select = select or self.lib_item
        tree.delete(*tree.get_children())
        query = self.lib_search.get().strip()
        shown = SECTIONS if self.lib_filter.get() == "Все" else [self.lib_filter.get()]
        found = total = 0
        for section in shown:
            in_section = [(i, it) for i, it in enumerate(self.library) if it["section"] == section]
            items = [(i, it) for i, it in in_section if matches(it, query)]
            total += len(in_section)
            found += len(items)
            if query and not items:
                continue
            tree.insert("", "end", iid=section, text=f"{section} ({len(items)})", open=True)
            for i, it in sorted(items, key=lambda p: p[1]["name"].lower()):
                tree.insert(section, "end", iid=f"item{i}", text=it["name"],
                            values=[fmt(it.get(k, 0)) for k in ("mass", "blank", "burn", "waste", "scrap")])
        self.lib_found.configure(text=f"Найдено {found} из {total}" if query else f"Всего {total}")
        if select is not None and any(it is select for it in self.library):
            iid = f"item{next(i for i, it in enumerate(self.library) if it is select)}"
            if tree.exists(iid):
                tree.selection_set(iid)
                tree.see(iid)
                return
        self._show_library_item()

    def _selected_library_item(self):
        sel = self.lib_tree.selection()
        if not sel or not sel[0].startswith("item"):
            return None
        return self.library[int(sel[0][4:])]

    def _show_library_item(self):
        """Карточка выбранной детали: название, раздел, количество и значения."""
        item = self._selected_library_item()
        if item is not None and item is self.lib_item:
            return
        self.lib_item = item
        body = self.lib_body
        for child in body.winfo_children():
            child.destroy()
        if item is None:
            text = ("Библиотека пуста. Посчитайте деталь на вкладке «Расчёт»\nи нажмите «В библиотеку»."
                    if not self.library else "Выберите деталь в списке")
            ttk.Label(body, text=text, style="Muted.TLabel", justify="left").grid(row=0, column=0, sticky="w")
            return

        ttk.Label(body, text="Название").grid(row=0, column=0, sticky="w", pady=1)
        name = tk.StringVar(value=item["name"])
        name_entry = ttk.Entry(body, textvariable=name, width=40)
        name_entry.grid(row=0, column=1, columnspan=2, sticky="ew", pady=1, padx=(10, 0))
        note = ttk.Label(body, style="Ok.TLabel")

        def rename(_=None):
            new = name.get().strip()
            if not new:
                note.configure(text="Название не может быть пустым", style="Error.TLabel")
                return "break"
            if new != item["name"]:
                old = item["name"]
                item["name"] = unique_name(new, self.library, skip=item)
                if not self._save_library():
                    item["name"] = old
                    return "break"
                name.set(item["name"])
                self._fill_library(select=item)   # та же деталь — карточка не перерисовывается
            note.configure(text="✓ Сохранено", style="Ok.TLabel")
            return "break"

        name_entry.bind("<Return>", rename)
        ttk.Button(body, text="Переименовать", command=rename).grid(row=1, column=1, sticky="w", padx=(10, 0))
        note.grid(row=1, column=2, sticky="w", padx=(8, 0))

        ttk.Label(body, text="Раздел").grid(row=2, column=0, sticky="w", pady=(6, 1))
        section = tk.StringVar(value=item["section"])
        box = ttk.Combobox(body, textvariable=section, values=SECTIONS, state="readonly", width=18)
        box.grid(row=2, column=1, sticky="w", pady=(6, 1), padx=(10, 0))

        def move(_):
            item["section"] = section.get()
            if self._save_library():
                self._fill_library(select=item)

        box.bind("<<ComboboxSelected>>", move)

        ttk.Label(body, style="Muted.TLabel", justify="left", text=(
            f"{item.get('sheet', '')}\n{item.get('part', '')} · на листе {item.get('count', '')} шт\n"
            f"Раскладка: {item.get('nest', '')}\nДобавлено {item.get('saved', '')}")
                  ).grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 8))

        ttk.Label(body, text="Количество, шт").grid(row=4, column=0, sticky="w", pady=1)
        qty = tk.StringVar(value=short(item.get("qty", 1)))
        ttk.Entry(body, textvariable=qty, width=8).grid(row=4, column=1, sticky="w", pady=1, padx=(10, 0))
        values = ttk.Frame(body)
        values.grid(row=5, column=0, columnspan=3, sticky="w", pady=(8, 0))

        def show_values(*_):
            try:
                q = num(qty.get(), "Количество")
            except ValueError:
                return
            if q == 0:
                return
            for child in values.winfo_children():
                child.destroy()
            self._value_grid(values, 0, item["mass"], [item[k] for k in ("blank", "burn", "waste", "scrap")], q)
            if q != item.get("qty"):
                item["qty"] = q
                self._save_library()

        qty.trace_add("write", show_values)
        show_values()

        def delete():
            if messagebox.askyesno(APP_NAME, f"Удалить «{item['name']}» из библиотеки?"):
                self.library.remove(item)
                self._save_library()
                self.lib_item = None
                self._fill_library()

        ttk.Button(body, text="Удалить из библиотеки", command=delete).grid(
            row=6, column=0, columnspan=2, sticky="w", pady=(12, 0))


if __name__ == "__main__":
    App().mainloop()
