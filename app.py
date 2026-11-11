"""Расчёт заготовки, угара, отхода и металлолома по раскладке на листе."""

import tkinter as tk
from tkinter import ttk

import sv_ttk

from calc import calculate
from nesting import Circle, Gusset, Plate, nest
from sheets import GRADES, SHEET_L, SHEET_W, THICKNESSES, sheet_mass, sheet_name

PART_TYPES = {
    "Фланец": [("Диаметр, мм", "400")],
    "Пластина": [("Длина, мм", "200"), ("Ширина, мм", "100")],
    "Косынка": [("Катет 1, мм", "130"), ("Катет 2, мм", "80"), ("Срез углов, мм", "10")],
}

METRICS = [
    ("blank", "Масса заготовки", "Мз"),
    ("burn", "Угар", "У"),
    ("waste", "Отход", "Ро"),
    ("scrap", "Металлолом", "Мт"),
]

FONT = "Segoe UI"
ACCENT = "#005fb8"
ERROR = "#c42b1c"
OK = "#0f7b0f"
MUTED = "#5f6b7a"


def fmt(value, digits=3):
    return f"{value:.{digits}f}".replace(".", ",")


def num(text, name):
    try:
        value = float(text.replace(",", ".").strip())
    except ValueError:
        raise ValueError(f"«{name}»: введите число")
    if value < 0:
        raise ValueError(f"«{name}»: не может быть отрицательным")
    return value


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Раскрой листа")
        height = min(860, self.winfo_screenheight() - 80)
        self.geometry(f"1200x{height}")
        self.minsize(1000, 560)
        sv_ttk.set_theme("light")
        self._styles()
        self.layout = None
        self.values = None
        self.report = ""

        header = ttk.Frame(self, padding=(20, 12, 20, 0))
        header.pack(fill="x")
        ttk.Label(header, text="Раскрой листа", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="заготовка, угар, отход и металлолом по раскладке деталей",
                  style="Muted.TLabel").pack(side="left", padx=(12, 0), pady=(8, 0))

        tabs = ttk.Notebook(self)
        tabs.pack(fill="both", expand=True, padx=20, pady=(8, 16))
        calc_tab = ttk.Frame(tabs, padding=12)
        sheets_tab = ttk.Frame(tabs, padding=12)
        tabs.add(calc_tab, text="  Расчёт  ")
        tabs.add(sheets_tab, text="  Листы  ")
        self._build_calc_tab(calc_tab)
        self._build_sheets_tab(sheets_tab)
        self.bind("<Return>", lambda _: self.run())

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

    def _card(self, parent, title):
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        ttk.Label(card, text=title, style="Section.TLabel").grid(
            row=0, column=0, columnspan=4, sticky="w", pady=(0, 6))
        return card

    def _entry(self, parent, row, label, var, width=12):
        """Подпись + поле в строке row; возвращает (подпись, поле)."""
        caption = ttk.Label(parent, text=label)
        caption.grid(row=row, column=0, sticky="w", pady=2, padx=(0, 10))
        entry = ttk.Entry(parent, textvariable=var, width=width)
        entry.grid(row=row, column=1, columnspan=3, sticky="w", pady=2)
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
        ttk.Button(form, text="Рассчитать", style="Big.Accent.TButton",
                   command=self.run).pack(fill="x", pady=(10, 0), ipady=4)

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
        ttk.Label(card, text="Толщина, мм").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Combobox(card, textvariable=self.thickness, width=10, values=[str(t) for t in THICKNESSES]
                     ).grid(row=1, column=1, columnspan=3, sticky="w", pady=2)
        ttk.Label(card, text="Марка стали").grid(row=2, column=0, sticky="w", pady=2)
        ttk.Combobox(card, textvariable=self.grade, width=10, state="readonly", values=list(GRADES)
                     ).grid(row=2, column=1, columnspan=3, sticky="w", pady=2)
        ttk.Label(card, text="Ширина × длина, мм").grid(row=3, column=0, sticky="w", pady=2, padx=(0, 10))
        ttk.Entry(card, textvariable=self.sheet_w, width=6).grid(row=3, column=1, sticky="w", pady=2)
        ttk.Label(card, text="×").grid(row=3, column=2, padx=4)
        ttk.Entry(card, textvariable=self.sheet_l, width=6).grid(row=3, column=3, sticky="w", pady=2)
        self._entry(card, 4, "Масса листа, кг", self.sheet_m)
        ttk.Button(card, text="↺", width=3, command=self._update_sheet_mass).grid(
            row=4, column=4, sticky="w", padx=(6, 0))
        for var in (self.thickness, self.grade, self.sheet_w, self.sheet_l):
            var.trace_add("write", lambda *_: self._update_sheet_mass())
        self._update_sheet_mass()

    def _build_part_card(self, form):
        card = self._card(form, "Деталь")
        card.pack(fill="x", pady=(12, 0))
        self.part_type = tk.StringVar(value="Фланец")
        types = ttk.Frame(card)
        types.grid(row=1, column=0, columnspan=4, sticky="w", pady=(0, 4))
        for name in PART_TYPES:
            ttk.Radiobutton(types, text=name, value=name, variable=self.part_type,
                            style="Toggle.TButton", command=self._show_dims, width=9
                            ).pack(side="left", padx=(0, 6))
        # Поля размеров всех типов создаются сразу (строки 2–4), видны только поля выбранного
        self.dims, self.dim_widgets = {}, {}
        for name, fields in PART_TYPES.items():
            self.dims[name] = [tk.StringVar(value=v) for _, v in fields]
            self.dim_widgets[name] = [w for i, ((label, _), var) in enumerate(zip(fields, self.dims[name]))
                                      for w in self._entry(card, 2 + i, label, var)]
        self._show_dims()
        self.part_mass = tk.StringVar(value="11")
        self.drawing_qty = tk.StringVar(value="1")
        # строки 2–4 — под размеры детали (до трёх полей)
        self._entry(card, 5, "Масса по чертежу, кг", self.part_mass)
        self._entry(card, 6, "Кол-во на чертеже, шт", self.drawing_qty)

    def _build_nest_card(self, form):
        card = self._card(form, "Раскладка")
        card.pack(fill="x", pady=(12, 0))
        self.gap = tk.StringVar(value="5")
        self.margin = tk.StringVar(value="0")
        self._entry(card, 1, "Зазор между деталями, мм", self.gap)
        self._entry(card, 2, "Отступ от края, мм", self.margin)
        self.manual = tk.BooleanVar(value=False)
        self.manual_count = tk.StringVar()
        ttk.Checkbutton(card, text="Кол-во на листе вручную", variable=self.manual,
                        style="Switch.TCheckbutton", command=self._toggle_manual
                        ).grid(row=3, column=0, sticky="w", pady=(6, 2))
        self.manual_entry = ttk.Entry(card, textvariable=self.manual_count, width=12, state="disabled")
        self.manual_entry.grid(row=3, column=1, columnspan=3, sticky="w", pady=(6, 2))

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
        card.bind("<Configure>", lambda e: self.warning.configure(wraplength=e.width - 40))

        buttons = ttk.Frame(card)
        buttons.grid(row=4, column=0, columnspan=4, sticky="w", pady=(10, 0))
        ttk.Button(buttons, text="Копировать текст", command=self.copy_text).pack(side="left")
        ttk.Button(buttons, text="Копировать для Excel", command=self.copy_excel).pack(
            side="left", padx=8)
        self.copied = ttk.Label(buttons, style="Ok.TLabel")
        self.copied.pack(side="left", padx=4)

    def _show_dims(self):
        for name, widgets in self.dim_widgets.items():
            for w in widgets:
                if name == self.part_type.get():
                    w.grid()
                else:
                    w.grid_remove()

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
        values = [num(v.get(), label) for (label, _), v in zip(PART_TYPES[kind], self.dims[kind])]
        if any(v == 0 for v in values[:2]):
            raise ValueError("Размеры детали должны быть больше нуля")
        if kind == "Фланец":
            return Circle(values[0]), f"фланец Ø{values[0]:g}"
        if kind == "Пластина":
            return Plate(values[0], values[1]), f"пластина {values[0]:g}×{values[1]:g}"
        a, b, cut = values
        if cut * 2 >= min(a, b):
            raise ValueError("Срез углов слишком большой для такой косынки")
        return Gusset(a, b, cut), f"косынка {a:g}×{b:g}, срез углов {cut:g}"

    def run(self):
        self.error.configure(text="")
        self.copied.configure(text="")
        try:
            thickness, grade, width, length = self._sheet_params()
            m_sheet = num(self.sheet_m.get(), "Масса листа")
            if m_sheet == 0:
                raise ValueError("Укажите массу листа")
            part, part_title = self._make_part()
            part_mass = num(self.part_mass.get(), "Масса детали")
            qty = num(self.drawing_qty.get(), "Количество на чертеже")
            gap = num(self.gap.get(), "Зазор")
            margin = num(self.margin.get(), "Отступ от края")
            if part_mass == 0:
                raise ValueError("Укажите массу детали")
            manual = int(num(self.manual_count.get(), "Количество на листе")) if self.manual.get() else None
        except ValueError as e:
            self.error.configure(text=str(e))
            return

        self.configure(cursor="watch")
        self.update_idletasks()
        try:
            self.layout = nest(part, length, width, gap, margin)
            self.sheet_size = (length, width)
        finally:
            self.configure(cursor="")
        self._draw()

        count = manual if manual is not None else self.layout.count
        source = (f"вручную (программа разложила {self.layout.count})" if manual is not None
                  else "раскладка программы")
        if count == 0:
            self.error.configure(text="Деталь не помещается на лист")
            return

        title = sheet_name(thickness, grade, width, length)
        r = calculate(m_sheet, count, part_mass)
        self.values = [title, m_sheet, part_title, part_mass, count,
                       r.blank, r.burn, r.waste, r.scrap, qty]
        formula_mass = sheet_mass(thickness, grade, width, length)
        mass_note = ("" if abs(m_sheet - formula_mass) < 0.0005
                     else f" (вручную, по формуле {fmt(formula_mass)})")

        self.summary.configure(text=(
            f"Лист {title} — {fmt(m_sheet)} кг{mass_note}\n"
            f"Деталь: {part_title} — {fmt(part_mass)} кг\n"
            f"На листе: {count} шт — {source}"))
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
            "",
            "На 1 деталь:",
        ] + [f"  {t} ({s}) = {fmt(getattr(r, k))} кг" for k, t, s in METRICS]
        if qty != 1:
            lines += ["", f"На {qty:g} шт по чертежу:"] + [
                f"  {t} ({s}) = {fmt(getattr(r, k) * qty)} кг" for k, t, s in METRICS]
        self.report = "\n".join(lines)

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
        for shape in self.layout.shapes:
            if shape[0] == "circle":
                _, x, y, r = shape
                c.create_oval(ox + (x - r) * scale, oy + (y - r) * scale,
                              ox + (x + r) * scale, oy + (y + r) * scale,
                              fill="#cfe3fa", outline=ACCENT)
            else:
                pts = [v for x, y in shape[1] for v in (ox + x * scale, oy + y * scale)]
                c.create_polygon(pts, fill="#cfe3fa", outline=ACCENT)
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


if __name__ == "__main__":
    App().mainloop()
