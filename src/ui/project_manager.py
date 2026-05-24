"""
Project Manager Screen for CortaCerto.

Dark-themed project management interface shown on startup.
Matches the handoff design: sidebar nav, hero, stats, category filters,
project cards with thumbnails + waveforms, list view, new-project modal.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import time
import tkinter as tk
from dataclasses import dataclass, field, asdict
from pathlib import Path
from tkinter import filedialog, messagebox
from typing import Callable, Optional

try:
    from PIL import Image, ImageDraw, ImageTk
    _PIL = True
except ImportError:
    _PIL = False

# ── Design tokens (matched to handoff CSS vars) ───────────────────────────────
BG0     = "#0c0b0f"   # deepest background
BG1     = "#131217"   # sidebar
BG2     = "#1c1b22"   # raised surfaces
SURF    = "#151319"   # card surface  (rgba(255,255,255,0.035) on BG0)
SURF2   = "#1b1924"   # card hover    (rgba(255,255,255,0.06))
SURF3   = "#222030"   # active surface(rgba(255,255,255,0.085))
BORD    = "#1f1d27"   # border        (rgba(255,255,255,0.07))
BORD_S  = "#2c2a38"   # strong border (rgba(255,255,255,0.12))
TXT     = "#ECE9F2"   # primary text
TXT2    = "#9B97A8"   # 66% text
TXT3    = "#706A7E"   # 42% text
TXT4    = "#534D62"   # 28% text
ACCENT  = "#8B6BFF"   # violet accent
ACC_S   = "#201C3D"   # accent-soft bg
OK      = "#5ECA8A"   # green
OK_S    = "#142B20"   # green bg
WARN    = "#E8B45A"   # amber
WARN_S  = "#2D2212"   # amber bg
HEADER  = "#18161e"   # header bg (semi-transparent look)

FONT_SAN = ("Segoe UI", 10)
FONT_MON = ("Consolas", 10)

# Status definitions  (label, bg, fg)
STATUS = {
    "edit":   ("Em edição",  ACC_S,  "#C7B7FF"),
    "review": ("Em revisão", WARN_S, WARN),
    "final":  ("Finalizado", OK_S,   OK),
    "draft":  ("Rascunho",   SURF3,  TXT2),
}

# Category definitions (id, label, hue-a, hue-b)
CATEGORIES = [
    ("all",     "Todos",   260, 240),
    ("youtube", "YouTube",  25,  15),
    ("shorts",  "Shorts",  310, 270),
    ("podcast", "Podcast", 280, 260),
    ("curso",   "Curso",   195, 215),
    ("review",  "Review",  165, 200),
    ("vlog",    "Vlog",    330, 280),
]
CAT_HUE = {c[0]: (c[2], c[3]) for c in CATEGORIES}

TEMPLATES = [
    ("blank",              "Em branco"),
    ("podcast-talkshow",   "Podcast — Talkshow"),
    ("talking-head-1080",  "Talking Head 1080p"),
    ("shorts-9x16",        "Shorts 9:16"),
    ("review-product",     "Review de Produto"),
    ("course-lesson",      "Aula de Curso"),
]


# ── Recent-projects store ─────────────────────────────────────────────────────

def _app_data_dir() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / "CortaCerto"
    base.mkdir(parents=True, exist_ok=True)
    return base


def _recent_path() -> Path:
    return _app_data_dir() / "recent_projects.json"


@dataclass
class ProjectEntry:
    path: str
    name: str
    category: str  = "youtube"
    status: str    = "draft"
    opened_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    duration_s: float = 0.0
    clips_count: int  = 0
    size_mb: float    = 0.0
    thumb_seed: int   = 0
    wave_seed: int    = 0

    def exists(self) -> bool:
        return Path(self.path).is_file()

    def edited_label(self) -> str:
        """Human-readable 'edited X ago' string."""
        delta = time.time() - self.updated_at
        if delta < 3600:
            mins = max(1, int(delta / 60))
            return f"há {mins} min" if mins == 1 else f"há {mins} min"
        if delta < 86400:
            hours = int(delta / 3600)
            return f"há {hours}h"
        days = int(delta / 86400)
        if days == 1:
            return "ontem"
        if days < 30:
            return f"há {days} dias"
        months = int(days / 30)
        return f"há {months} mês" if months == 1 else f"há {months} meses"

    def section_key(self) -> str:
        delta = time.time() - self.updated_at
        if delta < 86400 * 2:
            return "recent"
        if delta < 86400 * 8:
            return "all"
        return "old"

    def size_label(self) -> str:
        if self.size_mb < 1:
            return f"{int(self.size_mb * 1024)} KB"
        if self.size_mb < 1024:
            return f"{self.size_mb:.1f} MB"
        return f"{self.size_mb / 1024:.1f} GB"

    def duration_label(self) -> str:
        s = int(self.duration_s)
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        if h:
            return f"{h:02d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"


def _load_recent_projects() -> list[ProjectEntry]:
    try:
        raw = json.loads(_recent_path().read_text(encoding="utf-8"))
        entries = [ProjectEntry(**e) for e in raw.get("projects", [])]
        return [e for e in entries if e.exists()]
    except Exception:
        return []


def _save_recent_projects(entries: list[ProjectEntry]) -> None:
    try:
        _recent_path().write_text(
            json.dumps({"projects": [asdict(e) for e in entries]}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass


def register_recent_project(
    path: str,
    name: str = "",
    category: str = "youtube",
    status: str = "draft",
    duration_s: float = 0.0,
    clips_count: int = 0,
    size_mb: float = 0.0,
) -> None:
    """Call this every time a project is opened/created to update the recent list."""
    entries = _load_recent_projects()
    existing = {e.path: e for e in entries}
    seed = int(hashlib.md5(path.encode()).hexdigest(), 16) % 10000
    if path in existing:
        e = existing[path]
        e.name = name or e.name
        e.category = category or e.category
        e.status = status or e.status
        e.opened_at = time.time()
        e.updated_at = time.time()
        if duration_s: e.duration_s = duration_s
        if clips_count: e.clips_count = clips_count
        if size_mb:     e.size_mb = size_mb
    else:
        existing[path] = ProjectEntry(
            path=path, name=name or Path(path).stem,
            category=category, status=status,
            opened_at=time.time(), updated_at=time.time(),
            duration_s=duration_s, clips_count=clips_count, size_mb=size_mb,
            thumb_seed=seed, wave_seed=(seed * 7 + 13) % 10000,
        )
    # Sort by opened_at desc, keep max 50
    merged = sorted(existing.values(), key=lambda e: e.opened_at, reverse=True)[:50]
    _save_recent_projects(merged)


# ── Thumbnail + waveform generation ──────────────────────────────────────────

def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    """HSL (0-360, 0-1, 0-1) → RGB (0-255)."""
    h /= 360.0
    if s == 0:
        v = int(l * 255)
        return v, v, v
    q = l * (1 + s) if l < 0.5 else l + s - l * s
    p = 2 * l - q
    def f(t):
        t = t % 1.0
        if t < 1/6: return p + (q-p)*6*t
        if t < 1/2: return q
        if t < 2/3: return p + (q-p)*(2/3-t)*6
        return p
    return int(f(h+1/3)*255), int(f(h)*255), int(f(h-1/3)*255)


def _make_thumbnail(category: str, seed: int, w: int = 260, h: int = 163) -> Optional[object]:
    """Generate a PIL PhotoImage gradient thumbnail for a project card."""
    if not _PIL:
        return None
    hue_a, hue_b = CAT_HUE.get(category, (260, 240))
    img = Image.new("RGB", (w, h))
    draw = ImageDraw.Draw(img)
    # Draw vertical gradient (hue_a at top, hue_b at bottom)
    for y in range(h):
        t = y / h
        hue = hue_a + (hue_b - hue_a) * t
        r, g, b = _hsl_to_rgb(hue, 0.40, 0.20 - t * 0.06)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    # Subtle diagonal stripe pattern
    rng = random.Random(seed)
    for i in range(0, w + h, 28):
        alpha = int(255 * 0.05)
        draw.line([(i, 0), (0, i)], fill=(255, 255, 255, alpha), width=1)
    # Soft center glow
    for dy in range(h):
        t = 1 - abs(dy / h - 0.5) * 2
        boost = int(t * t * 18)
        row = list(img.crop((0, dy, w, dy + 1)).getdata())
        row = [(min(255, px[0]+boost), min(255, px[1]+boost), min(255, px[2]+boost)) for px in row]
        img.paste(Image.new("RGB", (w, 1), (0, 0, 0)), (0, dy))
        for x, px in enumerate(row):
            img.putpixel((x, dy), px)
    # Category label (bottom-left)
    try:
        from PIL import ImageFont
        font = ImageFont.truetype("segoeui.ttf", 9)
    except Exception:
        font = None
    label = category.upper()
    draw.text((10, h - 20), label, fill=(255, 255, 255, 140), font=font)
    return ImageTk.PhotoImage(img)


def _gen_wave(seed: int, bars: int = 52) -> list[float]:
    """Generate a speech-like waveform from a seed."""
    rng = random.Random(seed)
    out = []
    for i in range(bars):
        env = 0.4 + 0.6 * math.sin((i / bars) * math.pi * (1 + rng.random() * 2))
        h = max(0.08, min(1.0, env * (0.3 + rng.random() * 0.9)))
        out.append(h)
    return out


# ── Color helpers ─────────────────────────────────────────────────────────────

def _blend(hex_color: str, alpha: float, bg: str = BG0) -> str:
    """Blend hex_color over bg at alpha (0-1), return hex result."""
    def parse(h):
        h = h.lstrip("#")
        return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    fr, fg, fb = parse(hex_color)
    br, bg_, bb = parse(bg)
    r = int(br + (fr - br) * alpha)
    g = int(bg_ + (fg - bg_) * alpha)
    b = int(bb + (fb - bb) * alpha)
    return f"#{r:02x}{g:02x}{b:02x}"


# ── Canvas rounded-rectangle helper ─────────────────────────────────────────

def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                r: int = 10, **kw) -> int:
    """Draw a smooth rounded rectangle on *canvas* using create_polygon."""
    r = max(0, min(r, int((x2 - x1) / 2), int((y2 - y1) / 2)))
    pts = [
        x1 + r, y1,    x2 - r, y1,
        x2,     y1,    x2,     y1 + r,
        x2,     y2 - r, x2,    y2,
        x2 - r, y2,    x1 + r, y2,
        x1,     y2,    x1,     y2 - r,
        x1,     y1 + r, x1,    y1,
    ]
    return canvas.create_polygon(pts, smooth=True, **kw)


# ── New Project Dialog ────────────────────────────────────────────────────────

class NewProjectDialog(tk.Toplevel):
    """Modal dialog: choose name, category, template → create project."""

    def __init__(self, parent: tk.Widget, on_create: Callable[[str, str, str, str], None]) -> None:
        super().__init__(parent)
        self._on_create = on_create
        self.title("Novo projeto")
        self.configure(bg=BG2)
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self._name_var     = tk.StringVar()
        self._category_var = tk.StringVar(value="youtube")
        self._template_var = tk.StringVar(value="blank")
        self._path_var     = tk.StringVar()

        self._build()
        self._center(parent)

    def _center(self, parent: tk.Widget) -> None:
        self.update_idletasks()
        pw = parent.winfo_rootx() + parent.winfo_width() // 2
        ph = parent.winfo_rooty() + parent.winfo_height() // 2
        w, h = self.winfo_width(), self.winfo_height()
        self.geometry(f"{w}x{h}+{pw - w//2}+{ph - h//2}")

    def _field_label(self, parent: tk.Widget, text: str) -> None:
        tk.Label(parent, text=text.upper(), bg=BG2, fg=TXT3,
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", pady=(0, 4))

    def _build(self) -> None:
        pad = {"padx": 26, "pady": 0}
        # Title
        tk.Label(self, text="Novo projeto", bg=BG2, fg=TXT,
                 font=("Segoe UI", 20)).pack(anchor="w", padx=26, pady=(22, 2))
        tk.Label(self, text="Configure e comece a editar. Ajustes ficam disponíveis depois.",
                 bg=BG2, fg=TXT3, font=("Segoe UI", 10), wraplength=440).pack(anchor="w", padx=26, pady=(0, 18))

        sep = tk.Frame(self, bg=BORD, height=1)
        sep.pack(fill="x", padx=0, pady=0)

        body = tk.Frame(self, bg=BG2)
        body.pack(fill="both", expand=True, padx=26, pady=18)

        # Project name
        self._field_label(body, "Nome do projeto")
        name_entry = tk.Entry(body, textvariable=self._name_var, bg=SURF2, fg=TXT,
                              insertbackground=TXT, relief="flat", font=("Segoe UI", 12),
                              highlightthickness=1, highlightbackground=BORD,
                              highlightcolor=ACCENT)
        name_entry.pack(fill="x", ipady=7, pady=(0, 16))
        name_entry.focus_set()

        # Save path
        self._field_label(body, "Local de salvamento")
        path_row = tk.Frame(body, bg=BG2)
        path_row.pack(fill="x", pady=(0, 16))
        path_entry = tk.Entry(path_row, textvariable=self._path_var, bg=SURF2, fg=TXT2,
                              insertbackground=TXT, relief="flat", font=("Segoe UI", 10),
                              highlightthickness=1, highlightbackground=BORD,
                              highlightcolor=ACCENT)
        path_entry.grid(row=0, column=0, sticky="ew", ipady=6)
        path_row.grid_columnconfigure(0, weight=1)
        browse_btn = tk.Button(path_row, text="Escolher…", bg=SURF3, fg=TXT2,
                               activebackground=BORD_S, activeforeground=TXT,
                               relief="flat", font=("Segoe UI", 10), cursor="hand2",
                               padx=10, pady=0, command=self._browse_path)
        browse_btn.grid(row=0, column=1, padx=(6, 0), ipady=6)

        # Category picker (2 × 3 grid)
        self._field_label(body, "Categoria")
        cat_frame = tk.Frame(body, bg=BG2)
        cat_frame.pack(fill="x", pady=(0, 16))
        cats = [
            ("youtube", "YouTube",  "▶"),
            ("shorts",  "Shorts",   "📱"),
            ("podcast", "Podcast",  "🎙"),
            ("curso",   "Curso",    "📖"),
            ("review",  "Review",   "⭐"),
            ("vlog",    "Vlog",     "🎬"),
        ]
        self._cat_btns: dict[str, tk.Frame] = {}
        for i, (cid, label, icon) in enumerate(cats):
            col, row = i % 3, i // 3
            frm = tk.Frame(cat_frame, bg=SURF, cursor="hand2",
                           highlightthickness=1, highlightbackground=BORD)
            frm.grid(row=row, column=col, padx=(0, 6) if col < 2 else 0,
                     pady=(0, 6), sticky="ew")
            cat_frame.grid_columnconfigure(col, weight=1)
            icon_lbl = tk.Label(frm, text=icon, bg=SURF, fg=TXT3, font=("Segoe UI", 12))
            icon_lbl.pack(anchor="w", padx=8, pady=(7, 0))
            text_lbl = tk.Label(frm, text=label, bg=SURF, fg=TXT2, font=("Segoe UI", 10))
            text_lbl.pack(anchor="w", padx=8, pady=(0, 7))
            self._cat_btns[cid] = frm
            for w in (frm, icon_lbl, text_lbl):
                w.bind("<Button-1>", lambda e, c=cid: self._select_cat(c))
                w.bind("<Enter>", lambda e, f=frm, c=cid: self._cat_hover(f, c, True))
                w.bind("<Leave>", lambda e, f=frm, c=cid: self._cat_hover(f, c, False))
        self._select_cat("youtube")

        # Template picker
        self._field_label(body, "Modelo")
        tmpl_menu = tk.OptionMenu(body, self._template_var,
                                  *[t[0] for t in TEMPLATES])
        tmpl_menu.configure(bg=SURF2, fg=TXT, activebackground=SURF3,
                            activeforeground=TXT, relief="flat",
                            font=("Segoe UI", 11), highlightthickness=0,
                            indicatoron=True, bd=0)
        tmpl_menu["menu"].configure(bg=BG2, fg=TXT, activebackground=SURF3,
                                    activeforeground=TXT, relief="flat")
        tmpl_menu.pack(fill="x", ipady=4)
        # Update menu labels to friendly names
        menu = tmpl_menu["menu"]
        menu.delete(0, "end")
        for tid, tlabel in TEMPLATES:
            menu.add_command(label=tlabel,
                             command=lambda v=tid: self._template_var.set(v))

        # Actions
        sep2 = tk.Frame(self, bg=BORD, height=1)
        sep2.pack(fill="x")
        act = tk.Frame(self, bg=BG2)
        act.pack(fill="x", padx=26, pady=14)
        tk.Button(act, text="Cancelar", command=self.destroy,
                  bg=BG2, fg=TXT2, activebackground=SURF2, activeforeground=TXT,
                  relief="flat", font=("Segoe UI", 11), cursor="hand2",
                  padx=16, pady=6).pack(side="right", padx=(8, 0))
        create_btn = tk.Button(act, text="+ Criar projeto", command=self._submit,
                               bg=ACCENT, fg="#ffffff", activebackground="#7055dd",
                               activeforeground="#ffffff", relief="flat",
                               font=("Segoe UI", 11, "bold"), cursor="hand2",
                               padx=18, pady=6)
        create_btn.pack(side="right")

        self.bind("<Return>", lambda e: self._submit())
        self.bind("<Escape>", lambda e: self.destroy())

    def _browse_path(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Salvar projeto como",
            defaultextension=".ccp",
            filetypes=[("Projeto CortaCerto", "*.ccp"), ("Todos", "*.*")],
            initialfile=(self._name_var.get() or "novo-projeto") + ".ccp",
        )
        if path:
            self._path_var.set(path)

    def _select_cat(self, cid: str) -> None:
        self._category_var.set(cid)
        for k, frm in self._cat_btns.items():
            if k == cid:
                frm.configure(bg=ACC_S, highlightbackground=ACCENT)
                for c in frm.winfo_children():
                    c.configure(bg=ACC_S, fg="#DCD0FF")
            else:
                frm.configure(bg=SURF, highlightbackground=BORD)
                for c in frm.winfo_children():
                    c.configure(bg=SURF, fg=TXT3)

    def _cat_hover(self, frm: tk.Frame, cid: str, entering: bool) -> None:
        if self._category_var.get() == cid:
            return
        bg = SURF2 if entering else SURF
        frm.configure(bg=bg)
        for c in frm.winfo_children():
            c.configure(bg=bg)

    def _submit(self) -> None:
        name = self._name_var.get().strip() or "Sem título"
        path = self._path_var.get().strip()
        cat  = self._category_var.get()
        tmpl = self._template_var.get()
        if not path:
            messagebox.showwarning("Caminho necessário",
                                   "Escolha onde salvar o projeto.", parent=self)
            return
        self.destroy()
        self._on_create(path, name, cat, tmpl)


# ── Project card widget ───────────────────────────────────────────────────────

class ProjectCard(tk.Canvas):
    """Project card with rounded corners drawn on a Canvas."""

    THUMB_H = 128
    RADIUS  = 12

    def __init__(self, parent: tk.Widget, entry: ProjectEntry,
                 on_open: Callable[[ProjectEntry], None],
                 accent: str = ACCENT) -> None:
        super().__init__(parent, bg=BG0, highlightthickness=0, cursor="hand2")
        self._entry   = entry
        self._on_open = on_open
        self._accent  = accent
        self._hover   = False
        self._leave_id: Optional[str] = None

        # Inner content frame embedded in canvas
        self._frame = tk.Frame(self, bg=SURF)
        self._frame_id = self.create_window(1, 1, anchor="nw", window=self._frame)

        self._build_content()
        self.bind("<Configure>", self._on_canvas_resize)
        self._frame.bind("<Configure>", self._on_frame_resize)
        self._bind_hover()

    def _build_content(self) -> None:
        e = self._entry
        # ── Thumbnail canvas ──────────────────────────────────────────────────
        self._thumb = tk.Canvas(self._frame, bg="#1a1820",
                                highlightthickness=0, height=self.THUMB_H)
        self._thumb.pack(fill="x")
        self._thumb.bind("<Configure>", self._draw_thumb)

        # ── Card body ─────────────────────────────────────────────────────────
        body = tk.Frame(self._frame, bg=SURF, padx=12)
        body.pack(fill="x", pady=(8, 10))

        tk.Label(body, text=e.name, bg=SURF, fg=TXT,
                 font=("Segoe UI", 11, "bold"),
                 anchor="w", wraplength=190, justify="left").pack(fill="x")

        meta = tk.Frame(body, bg=SURF)
        meta.pack(fill="x", pady=(4, 0))

        tk.Label(meta, text=f"Editado {e.edited_label()}", bg=SURF,
                 fg=TXT3, font=("Segoe UI", 9)).pack(side="left")
        if e.clips_count > 0:
            tk.Label(meta, text=" · ", bg=SURF, fg=TXT4,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(meta, text=f"{e.clips_count} clipes", bg=SURF, fg=TXT3,
                     font=("Segoe UI", 9)).pack(side="left")

        st_label, st_bg, st_fg = STATUS.get(e.status, STATUS["draft"])
        tk.Label(meta, text=st_label, bg=st_bg, fg=st_fg,
                 font=("Segoe UI", 8), padx=6, pady=2).pack(side="right")

    # ── Canvas / frame resize ─────────────────────────────────────────────────

    def _on_canvas_resize(self, event: tk.Event) -> None:
        self._redraw()

    def _on_frame_resize(self, event: tk.Event) -> None:
        self._redraw()

    def _redraw(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        w = self.winfo_width()
        fh = self._frame.winfo_reqheight()
        if w < 4 or fh < 4:
            return
        h = fh + 2
        bg = SURF2 if self._hover else SURF
        bd = BORD_S if self._hover else BORD
        self.delete("card_bg")
        _round_rect(self, 0, 0, w - 1, h - 1, r=self.RADIUS,
                    fill=bg, outline=bd, tags="card_bg")
        self.tag_lower("card_bg")
        self.itemconfigure(self._frame_id, width=w - 2)

    # ── Thumbnail drawing ─────────────────────────────────────────────────────

    def _draw_thumb(self, event: Optional[tk.Event] = None) -> None:
        try:
            if not self.winfo_exists():
                return
        except Exception:
            return
        c = self._thumb
        try:
            c.delete("all")
        except Exception:
            return
        w = c.winfo_width()
        h = self.THUMB_H
        if w < 4:
            return

        e = self._entry
        hue_a, hue_b = CAT_HUE.get(e.category, (260, 240))
        for y in range(h):
            t = y / h
            hue = hue_a + (hue_b - hue_a) * t
            r, g, b = _hsl_to_rgb(hue, 0.38, 0.20 - t * 0.05)
            c.create_line(0, y, w, y, fill=f"#{r:02x}{g:02x}{b:02x}")

        rng = random.Random(e.thumb_seed)
        for i in range(0, w + h, 26):
            c.create_line(i, 0, 0, i, fill="#ffffff", stipple="gray12")

        scrim_h = 40
        for y in range(scrim_h):
            alpha = int((y / scrim_h) * 160)
            rng.random()
            c.create_line(0, h - scrim_h + y, w, h - scrim_h + y,
                          fill=f"#{alpha//4:02x}{alpha//6:02x}{alpha//4:02x}")

        wave = _gen_wave(e.wave_seed, bars=min(48, w // 5))
        bar_w = max(2, (w - 16) // len(wave) - 1)
        gap   = max(1, (w - 16 - len(wave) * bar_w) // len(wave))
        wave_h, wave_y = 28, h - 28 - 6
        for i, amp in enumerate(wave):
            x = 8 + i * (bar_w + gap)
            bh = int(amp * wave_h)
            by = wave_y + (wave_h - bh)
            c.create_rectangle(x, by, x + bar_w, wave_y + wave_h,
                                fill=self._accent, outline="",
                                stipple="" if amp > 0.5 else "gray50")

        dur = e.duration_label()
        if dur != "00:00":
            c.create_rectangle(w - 52, 6, w - 6, 22, fill="#000000",
                                outline="", stipple="gray50")
            c.create_text(w - 29, 14, text=dur, fill="#ffffff",
                          font=("Consolas", 8, "bold"), anchor="center")

        cat_label = (e.category or "video").upper()
        c.create_rectangle(6, 6, 6 + len(cat_label) * 6 + 14, 22,
                            fill="#000000", outline="", stipple="gray50")
        c.create_oval(12, 11, 17, 16, fill=self._accent, outline="")
        c.create_text(22, 14, text=cat_label, fill="#ffffff",
                      font=("Segoe UI", 7, "bold"), anchor="w")

    # ── Hover interaction ─────────────────────────────────────────────────────

    def _bind_hover(self) -> None:
        def on_enter(e):
            if self._leave_id:
                self.after_cancel(self._leave_id)
                self._leave_id = None
            if not self._hover:
                self._hover = True
                _recursive_bg(self._frame, SURF2)
                self._redraw()

        def do_leave():
            self._leave_id = None
            try:
                px, py = self.winfo_pointerxy()
                rx, ry = self.winfo_rootx(), self.winfo_rooty()
                cw, ch = self.winfo_width(), self.winfo_height()
                if rx <= px <= rx + cw and ry <= py <= ry + ch:
                    return
            except Exception:
                pass
            self._hover = False
            _recursive_bg(self._frame, SURF)
            self._redraw()

        def on_leave(e):
            if self._leave_id:
                self.after_cancel(self._leave_id)
            self._leave_id = self.after(12, do_leave)

        def on_click(e):
            self._on_open(self._entry)

        for w in [self] + _all_widgets(self._frame):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)

    def refresh_thumb(self) -> None:
        try:
            if self.winfo_exists():
                self._draw_thumb()
        except Exception:
            pass


def _all_widgets(w: tk.Widget) -> list[tk.Widget]:
    result = [w]
    for c in w.winfo_children():
        result.extend(_all_widgets(c))
    return result


def _recursive_bg(w: tk.Widget, bg: str) -> None:
    try:
        if isinstance(w, (tk.Frame, tk.Label)):
            w.configure(bg=bg)
        for c in w.winfo_children():
            _recursive_bg(c, bg)
    except Exception:
        pass


# ── Main Project Manager Screen ───────────────────────────────────────────────

class ProjectManagerScreen(tk.Frame):
    """
    Full-featured project management screen.

    Parameters
    ----------
    root       : The Tk root window.
    on_open    : Called with (project_path: str) when user opens a project.
    on_create  : Called with (path, name, category, template) for new projects.
    on_quick   : Called with () for "Abrir vídeo rápido".
    on_restore : Called with () for "Restaurar projeto".
    """

    SIDEBAR_W = 96
    HEADER_H  = 68

    def __init__(
        self,
        root: tk.Tk,
        on_open: Callable[[str], None],
        on_create: Callable[[str, str, str, str], None],
        on_quick: Callable[[], None],
        on_restore: Callable[[], None],
    ) -> None:
        super().__init__(root, bg=BG0)
        self.pack(fill="both", expand=True)

        self._root      = root
        self._on_open   = on_open
        self._on_create = on_create
        self._on_quick  = on_quick
        self._on_restore = on_restore

        self._projects: list[ProjectEntry] = []
        self._filter_cat  = tk.StringVar(value="all")
        self._search_var  = tk.StringVar()
        self._view_mode   = tk.StringVar(value="grid")   # "grid" | "list"
        self._section     = tk.StringVar(value="home")
        self._sort_mode   = tk.StringVar(value="recent") # "recent"|"name"|"size"
        self._toast_id: Optional[str] = None
        self._toast_lbl: Optional[tk.Label] = None
        self._chip_btns: dict[str, tk.Label] = {}
        self._card_refs: list[ProjectCard] = []

        self._search_var.trace_add("write", lambda *_: self._refresh_content())

        self._build_layout()
        self._load_projects()

    # ── Layout shell ─────────────────────────────────────────────────────────

    def _build_layout(self) -> None:
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._sidebar = self._make_sidebar()
        self._sidebar.grid(row=0, column=0, sticky="nsw")

        main = tk.Frame(self, bg=BG0)
        main.grid(row=0, column=1, sticky="nsew")
        main.grid_rowconfigure(1, weight=1)
        main.grid_columnconfigure(0, weight=1)

        self._header = self._make_header(main)
        self._header.grid(row=0, column=0, sticky="ew")

        self._content_wrap = tk.Frame(main, bg=BG0)
        self._content_wrap.grid(row=1, column=0, sticky="nsew")
        self._content_wrap.grid_rowconfigure(0, weight=1)
        self._content_wrap.grid_columnconfigure(0, weight=1)

        self._build_scroll_area()

    def _make_sidebar(self) -> tk.Frame:
        sb = tk.Frame(self, bg=BG1, width=self.SIDEBAR_W)
        sb.pack_propagate(False)
        sb.grid_propagate(False)

        # Brand mark
        brand = tk.Frame(sb, bg=BG1)
        brand.pack(fill="x", padx=10, pady=(16, 0))
        bm = tk.Canvas(brand, width=28, height=28, bg=BG1,
                       highlightthickness=0)
        bm.pack(anchor="center")
        # Conic-ish gradient mark: 4 arcs approximation
        for i, color in enumerate(["#B89AFF", "#6B49FF", "#8B6BFF", "#B89AFF"]):
            start = i * 90
            bm.create_arc(0, 0, 28, 28, start=start, extent=90,
                          fill=color, outline="")
        # Inner dark square
        bm.create_rectangle(6, 6, 22, 22, fill=BG1, outline="")
        # Play triangle
        bm.create_polygon(9, 8, 9, 20, 20, 14, fill="#8B6BFF", outline="")

        # Separator
        tk.Frame(sb, bg=BORD, height=1).pack(fill="x", padx=10, pady=(12, 8))

        nav_items = [
            ("home",      "Início",    "⌂"),
            ("projects",  "Projetos",  "◫"),
            ("templates", "Modelos",   "⊞"),
            ("media",     "Mídia",     "▤"),
        ]
        self._nav_btns: dict[str, tk.Frame] = {}
        for sid, label, icon in nav_items:
            self._nav_btns[sid] = self._make_nav_item(sb, sid, label, icon)

        # Spacer
        tk.Frame(sb, bg=BG1).pack(fill="both", expand=True)

        # Bottom nav
        for sid, label, icon in [("trash", "Lixeira", "🗑")]:
            self._nav_btns[sid] = self._make_nav_item(sb, sid, label, icon)

        # Avatar placeholder
        av = tk.Canvas(sb, width=36, height=36, bg=BG1, highlightthickness=0)
        av.pack(pady=(8, 14))
        av.create_oval(0, 0, 36, 36, fill="#2a2535", outline=BORD_S)
        av.create_text(18, 18, text="JM", fill=TXT, font=("Segoe UI", 10, "bold"))

        self._set_nav_active("home")
        return sb

    def _make_nav_item(self, parent: tk.Widget, sid: str,
                       label: str, icon: str) -> tk.Frame:
        frm = tk.Frame(parent, bg=BG1, cursor="hand2")
        frm.pack(fill="x", padx=6, pady=2)
        icon_lbl = tk.Label(frm, text=icon, bg=BG1, fg=TXT3,
                            font=("Segoe UI", 14))
        icon_lbl.pack(pady=(6, 0))
        text_lbl = tk.Label(frm, text=label, bg=BG1, fg=TXT3,
                            font=("Segoe UI", 8))
        text_lbl.pack(pady=(0, 6))
        for w in (frm, icon_lbl, text_lbl):
            w.bind("<Button-1>", lambda e, s=sid: self._nav_click(s))
            w.bind("<Enter>", lambda e, f=frm, i=icon_lbl, t=text_lbl, s=sid:
                   self._nav_hover(f, i, t, s, True))
            w.bind("<Leave>", lambda e, f=frm, i=icon_lbl, t=text_lbl, s=sid:
                   self._nav_hover(f, i, t, s, False))
        return frm

    def _nav_hover(self, frm, icon_lbl, text_lbl, sid, entering):
        if self._section.get() == sid:
            return
        bg = SURF if entering else BG1
        fg = TXT2 if entering else TXT3
        frm.configure(bg=bg)
        icon_lbl.configure(bg=bg, fg=fg)
        text_lbl.configure(bg=bg, fg=fg)

    def _set_nav_active(self, sid: str) -> None:
        for k, frm in self._nav_btns.items():
            children = frm.winfo_children()
            active = k == sid
            bg = SURF2 if active else BG1
            fg = TXT if active else TXT3
            frm.configure(bg=bg, highlightthickness=1 if active else 0,
                          highlightbackground=BORD if active else BG1)
            for c in children:
                c.configure(bg=bg, fg=fg)

    def _nav_click(self, sid: str) -> None:
        self._section.set(sid)
        self._set_nav_active(sid)
        self._refresh_content()

    def _make_header(self, parent: tk.Widget) -> tk.Frame:
        hdr = tk.Frame(parent, bg=HEADER, height=self.HEADER_H)
        hdr.pack_propagate(False)
        hdr.grid_propagate(False)

        # Border bottom
        tk.Frame(parent, bg=BORD, height=1).grid(row=0, column=0, sticky="ew",
                                                   pady=(self.HEADER_H, 0))

        inner = tk.Frame(hdr, bg=HEADER)
        inner.pack(fill="both", expand=True, padx=22)

        # Search (centered)
        search_wrap = tk.Frame(inner, bg=SURF, highlightthickness=1,
                               highlightbackground=BORD)
        search_wrap.pack(side="left", fill="y", pady=14, ipady=0,
                         expand=True, padx=(0, 12))
        tk.Label(search_wrap, text="🔍", bg=SURF, fg=TXT3,
                 font=("Segoe UI", 10)).pack(side="left", padx=(8, 0))
        search_entry = tk.Entry(search_wrap, textvariable=self._search_var,
                                bg=SURF, fg=TXT, insertbackground=TXT,
                                relief="flat", font=("Segoe UI", 11),
                                highlightthickness=0)
        search_entry.pack(side="left", fill="both", expand=True, padx=6)
        search_entry.insert(0, "")
        search_entry.configure(fg=TXT)
        tk.Label(search_wrap, text="⌘K", bg=SURF, fg=TXT4,
                 font=("Consolas", 9)).pack(side="right", padx=8)

        # Right-side controls
        right = tk.Frame(inner, bg=HEADER)
        right.pack(side="right")

        # New project button
        new_btn = tk.Button(right, text="+ Novo projeto",
                            command=self._show_new_dialog,
                            bg=ACCENT, fg="#ffffff",
                            activebackground="#7055dd",
                            activeforeground="#ffffff",
                            relief="flat", font=("Segoe UI", 11, "bold"),
                            cursor="hand2", padx=14, pady=6)
        new_btn.pack(side="left", pady=14)

        # View toggle
        vtog = tk.Frame(right, bg=SURF, highlightthickness=1,
                        highlightbackground=BORD)
        vtog.pack(side="left", padx=(10, 0), pady=14)
        self._grid_btn = tk.Button(vtog, text="⊞", bg=SURF3, fg=TXT,
                                   activebackground=SURF3, relief="flat",
                                   font=("Segoe UI", 12), width=2,
                                   cursor="hand2",
                                   command=lambda: self._set_view("grid"))
        self._grid_btn.pack(side="left", padx=2, pady=2)
        self._list_btn = tk.Button(vtog, text="≡", bg=SURF, fg=TXT3,
                                   activebackground=SURF2, relief="flat",
                                   font=("Segoe UI", 12), width=2,
                                   cursor="hand2",
                                   command=lambda: self._set_view("list"))
        self._list_btn.pack(side="left", padx=2, pady=2)

        return hdr

    def _set_view(self, mode: str) -> None:
        self._view_mode.set(mode)
        active_bg, inactive_bg = SURF3, SURF
        active_fg, inactive_fg = TXT, TXT3
        if mode == "grid":
            self._grid_btn.configure(bg=active_bg, fg=active_fg)
            self._list_btn.configure(bg=inactive_bg, fg=inactive_fg)
        else:
            self._grid_btn.configure(bg=inactive_bg, fg=inactive_fg)
            self._list_btn.configure(bg=active_bg, fg=active_fg)
        self._refresh_content()

    def _build_scroll_area(self) -> None:
        """Create canvas + scrollbar for the main content area."""
        canvas = tk.Canvas(self._content_wrap, bg=BG0,
                           highlightthickness=0, bd=0)
        vbar = tk.Scrollbar(self._content_wrap, orient="vertical",
                            command=canvas.yview, bg=BG0,
                            troughcolor=BG0, bd=0, highlightthickness=0)
        canvas.configure(yscrollcommand=vbar.set)
        vbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        self._scroll_canvas = canvas
        self._scroll_vbar = vbar

        # Inner frame
        self._inner = tk.Frame(canvas, bg=BG0)
        self._inner_id = canvas.create_window(0, 0, anchor="nw",
                                              window=self._inner)

        self._inner.bind("<Configure>", self._on_inner_configure)
        canvas.bind("<Configure>", self._on_canvas_configure)
        canvas.bind("<MouseWheel>", self._on_mousewheel)
        canvas.bind("<Button-4>", self._on_mousewheel)
        canvas.bind("<Button-5>", self._on_mousewheel)

    def _on_inner_configure(self, event: tk.Event) -> None:
        self._scroll_canvas.configure(
            scrollregion=self._scroll_canvas.bbox("all"))

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._scroll_canvas.itemconfigure(
            self._inner_id, width=event.width)
        self._draw_bg_grid()

    def _draw_bg_grid(self) -> None:
        """Draw a subtle ambient dot-grid on the scroll canvas background."""
        try:
            c = self._scroll_canvas
            c.delete("bg_grid")
            w = c.winfo_width()
            h = max(c.winfo_height(), 3200)
            step = 44
            col  = "#100f15"   # barely visible above BG0 (#0c0b0f)
            for x in range(step, w, step):
                c.create_line(x, 0, x, h, fill=col, tags="bg_grid", dash=(1, 4))
            for y in range(step, h, step):
                c.create_line(0, y, w, y, fill=col, tags="bg_grid", dash=(1, 4))
            c.tag_lower("bg_grid")
        except Exception:
            pass

    def _on_mousewheel(self, event: tk.Event) -> None:
        if event.num == 4:
            self._scroll_canvas.yview_scroll(-1, "units")
        elif event.num == 5:
            self._scroll_canvas.yview_scroll(1, "units")
        else:
            self._scroll_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Data ──────────────────────────────────────────────────────────────────

    def _load_projects(self) -> None:
        self._projects = _load_recent_projects()
        self._refresh_content()

    def reload(self) -> None:
        """Reload recent projects from disk (call after creating/opening)."""
        self._load_projects()

    # ── Filtering ─────────────────────────────────────────────────────────────

    def _filtered_projects(self) -> list[ProjectEntry]:
        cat   = self._filter_cat.get()
        query = self._search_var.get().lower().strip()
        sort  = self._sort_mode.get()

        items = [e for e in self._projects if e.exists()]
        if cat == "__edit":
            items = [e for e in items if e.status in ("edit", "review")]
        elif cat == "__final":
            items = [e for e in items if e.status == "final"]
        elif cat != "all":
            items = [e for e in items if e.category == cat]
        if query:
            items = [e for e in items if query in e.name.lower()
                     or query in e.category.lower()]
        if sort == "name":
            items.sort(key=lambda e: e.name.lower())
        elif sort == "size":
            items.sort(key=lambda e: e.size_mb, reverse=True)
        else:
            items.sort(key=lambda e: e.opened_at, reverse=True)
        return items

    def _count_by_cat(self) -> dict[str, int]:
        counts: dict[str, int] = {"all": len(self._projects)}
        for e in self._projects:
            counts[e.category] = counts.get(e.category, 0) + 1
        return counts

    # ── Content rendering ─────────────────────────────────────────────────────

    def _clear_inner(self) -> None:
        for w in self._inner.winfo_children():
            w.destroy()
        self._card_refs.clear()
        self._chip_btns.clear()

    def _refresh_content(self) -> None:
        self._clear_inner()
        sec = self._section.get()
        if sec in ("home", "projects"):
            self._render_home()
        elif sec == "templates":
            self._render_templates()
        elif sec == "media":
            self._render_placeholder("Mídia", "Suas importações de vídeo, áudio e imagens em um lugar.")
        elif sec == "trash":
            self._render_placeholder("Lixeira", "Projetos excluídos permanecem aqui por 30 dias.")
        self._scroll_canvas.yview_moveto(0)

    def _render_home(self) -> None:
        inner = self._inner

        # Hero
        self._render_hero(inner)

        # Stats row
        self._render_stats(inner)

        # Filter row
        self._render_filter_row(inner)

        # Projects
        filtered = self._filtered_projects()
        view = self._view_mode.get()

        if not filtered:
            empty = tk.Frame(inner, bg=BG0, highlightthickness=1,
                             highlightbackground=BORD_S)
            empty.pack(fill="x", padx=28, pady=(0, 40))
            tk.Label(empty, text="○", bg=BG0, fg=TXT4,
                     font=("Segoe UI", 28)).pack(pady=(32, 4))
            q = self._search_var.get()
            msg = f'Nada encontrado para "{q}"' if q else "Nenhum projeto ainda."
            tk.Label(empty, text=msg, bg=BG0, fg=TXT2,
                     font=("Segoe UI", 12)).pack(pady=(0, 4))
            tk.Label(empty, text="Crie um novo projeto ou abra um existente.",
                     bg=BG0, fg=TXT3, font=("Segoe UI", 10)).pack(pady=(0, 32))
            # Quick actions
            act = tk.Frame(inner, bg=BG0)
            act.pack(pady=(8, 32))
            self._quick_action_btn(act, "+ Novo projeto", self._show_new_dialog, primary=True)
            self._quick_action_btn(act, "Abrir projeto…", self._open_existing)
            self._quick_action_btn(act, "Abrir vídeo rápido", self._on_quick)
            return

        if view == "grid":
            sections = [
                ("recent", "Projetos recentes",  "Continuar de onde parou"),
                ("all",    "Todos os projetos",   "Em revisão e finalizados"),
                ("old",    "Projetos antigos",     "Arquivados há mais de 7 dias"),
            ]
            for sec_key, sec_title, sec_sub in sections:
                group = [e for e in filtered if e.section_key() == sec_key]
                if not group:
                    continue
                self._render_section(inner, sec_title, sec_sub, group, len(group))
        else:
            self._render_list_section(inner, filtered)

    def _render_hero(self, parent: tk.Widget) -> None:
        hero = tk.Frame(parent, bg=BG0)
        hero.pack(fill="x", padx=28, pady=(28, 0))

        hour = time.localtime().tm_hour
        if hour < 6:
            greet = "Boa madrugada"
        elif hour < 12:
            greet = "Bom dia"
        elif hour < 19:
            greet = "Boa tarde"
        else:
            greet = "Boa noite"

        editing_count = sum(1 for e in self._projects if e.status == "edit")
        sub = f"{greet}, Editor"
        if editing_count:
            sub += f" — você tem {editing_count} projeto{'s' if editing_count > 1 else ''} em andamento."
        tk.Label(hero, text=sub, bg=BG0, fg=TXT3,
                 font=("Segoe UI", 11)).pack(anchor="w", pady=(0, 6))

        tk.Label(hero, text="Seus projetos,  prontos para continuar.",
                 bg=BG0, fg=TXT, font=("Segoe UI", 22, "bold"),
                 wraplength=700, justify="left").pack(anchor="w")

        tk.Label(hero,
                 text="De ideias brutas a histórias que conectam. Selecione um projeto "
                      "para retomar de onde parou ou crie algo novo.",
                 bg=BG0, fg=TXT2, font=("Segoe UI", 11),
                 wraplength=520, justify="left").pack(anchor="w", pady=(8, 0))

    def _render_stats(self, parent: tk.Widget) -> None:
        frm = tk.Frame(parent, bg=BG0)
        frm.pack(fill="x", padx=28, pady=(20, 4))
        frm.grid_columnconfigure((0, 1, 2, 3), weight=1)

        editing   = sum(1 for e in self._projects if e.status == "edit")
        total_dur = sum(e.duration_s for e in self._projects)
        dur_label = f"{total_dur / 3600:.1f}h" if total_dur >= 3600 else f"{int(total_dur / 60)}min"

        cards = [
            ("Em edição",     str(editing),   f"+{min(editing, 2)} essa semana",   ACCENT),
            ("Tempo cortado", dur_label,       "do material",                        OK),
            ("Comentários",   "—",             "não rastreado",                      TXT3),
            ("Exportar",      "—",             "fila vazia",                         TXT3),
        ]
        for col, (label, value, delta, accent_col) in enumerate(cards):
            c = tk.Frame(frm, bg=SURF, highlightthickness=1,
                         highlightbackground=BORD)
            c.grid(row=0, column=col, sticky="ew",
                   padx=(0, 10) if col < 3 else 0, pady=4, ipadx=2)
            # top accent line
            tk.Frame(c, bg=accent_col, height=2).pack(fill="x")
            tk.Label(c, text=label.upper(), bg=SURF, fg=TXT3,
                     font=("Segoe UI", 8)).pack(anchor="w", padx=14, pady=(8, 0))
            tk.Label(c, text=value, bg=SURF, fg=TXT,
                     font=("Segoe UI", 24, "bold")).pack(anchor="w", padx=14)
            tk.Label(c, text=delta, bg=SURF, fg=TXT3,
                     font=("Consolas", 9)).pack(anchor="w", padx=14, pady=(0, 10))

    def _render_filter_row(self, parent: tk.Widget) -> None:
        row = tk.Frame(parent, bg=BG0)
        row.pack(fill="x", padx=28, pady=(12, 4))

        chips_frame = tk.Frame(row, bg=BG0)
        chips_frame.pack(side="left", fill="y")

        counts = self._count_by_cat()
        for cid, clabel, _, _ in CATEGORIES:
            count = counts.get(cid, 0)
            is_active = self._filter_cat.get() == cid
            chip = self._make_chip(chips_frame, cid, clabel, count, is_active)
            chip.pack(side="left", padx=(0, 6))
            self._chip_btns[cid] = chip

        # Divider
        tk.Frame(chips_frame, bg=BORD_S, width=1).pack(
            side="left", fill="y", padx=(6, 10), pady=4)

        # Status filter chips
        editing_count = sum(1 for e in self._projects if e.status in ("edit", "review"))
        final_count   = sum(1 for e in self._projects if e.status == "final")
        for cid, clabel, count in (
            ("__edit",  "Em edição",   editing_count),
            ("__final", "Finalizados", final_count),
        ):
            is_active = self._filter_cat.get() == cid
            chip = self._make_status_chip(chips_frame, cid, clabel, count, is_active)
            chip.pack(side="left", padx=(0, 6))
            self._chip_btns[cid] = chip

        # Sort control
        sort_frm = tk.Frame(row, bg=SURF, highlightthickness=1,
                            highlightbackground=BORD, cursor="hand2")
        sort_frm.pack(side="right")
        sort_labels = {"recent": "↓ Mais recentes", "name": "↓ Por nome",
                       "size": "↓ Por tamanho"}
        self._sort_lbl = tk.Label(sort_frm, text=sort_labels[self._sort_mode.get()],
                                  bg=SURF, fg=TXT2, font=("Segoe UI", 10),
                                  padx=10, pady=4, cursor="hand2")
        self._sort_lbl.pack()
        self._sort_lbl.bind("<Button-1>", self._cycle_sort)
        sort_frm.bind("<Button-1>", self._cycle_sort)

    def _make_chip(self, parent: tk.Widget, cid: str, label: str,
                   count: int, active: bool) -> tk.Frame:
        bg  = ACC_S if active else SURF
        fg  = "#DCD0FF" if active else TXT2
        brd = _blend(ACCENT, 0.4) if active else BORD

        frm = tk.Frame(parent, bg=bg, cursor="hand2",
                       highlightthickness=1, highlightbackground=brd)
        text = f"{label}  {count}" if count else label
        lbl = tk.Label(frm, text=text, bg=bg, fg=fg,
                       font=("Segoe UI", 10), padx=10, pady=4)
        lbl.pack()
        for w in (frm, lbl):
            w.bind("<Button-1>", lambda e, c=cid: self._set_cat(c))
            w.bind("<Enter>", lambda e, f=frm, l=lbl, c=cid: self._chip_hover(f, l, c, True))
            w.bind("<Leave>", lambda e, f=frm, l=lbl, c=cid: self._chip_hover(f, l, c, False))
        return frm

    def _make_status_chip(self, parent: tk.Widget, cid: str, label: str,
                          count: int, active: bool) -> tk.Frame:
        """Status-filter chip with a dashed-style border (lighter color)."""
        bg  = SURF3 if active else BG0
        fg  = TXT  if active else TXT3
        brd = _blend(ACCENT, 0.55) if active else TXT4

        frm = tk.Frame(parent, bg=bg, cursor="hand2",
                       highlightthickness=1, highlightbackground=brd)
        text = f"{label}  {count}" if count else label
        lbl_w = tk.Label(frm, text=text, bg=bg, fg=fg,
                         font=("Segoe UI", 10), padx=10, pady=4)
        lbl_w.pack()
        for w in (frm, lbl_w):
            w.bind("<Button-1>", lambda e, c=cid: self._set_cat(c))
            w.bind("<Enter>",
                   lambda e, f=frm, l=lbl_w, c=cid: self._chip_hover(f, l, c, True))
            w.bind("<Leave>",
                   lambda e, f=frm, l=lbl_w, c=cid: self._chip_hover(f, l, c, False))
        return frm

    def _chip_hover(self, frm, lbl, cid, entering) -> None:
        if self._filter_cat.get() == cid:
            return
        bg = SURF2 if entering else SURF
        frm.configure(bg=bg)
        lbl.configure(bg=bg)

    def _set_cat(self, cid: str) -> None:
        self._filter_cat.set(cid)
        self._refresh_content()

    def _cycle_sort(self, event=None) -> None:
        modes = ["recent", "name", "size"]
        cur = self._sort_mode.get()
        nxt = modes[(modes.index(cur) + 1) % len(modes)]
        self._sort_mode.set(nxt)
        labels = {"recent": "↓ Mais recentes", "name": "↓ Por nome",
                  "size": "↓ Por tamanho"}
        if self._sort_lbl:
            self._sort_lbl.configure(text=labels[nxt])
        self._refresh_content()

    def _render_section(self, parent: tk.Widget, title: str, subtitle: str,
                        projects: list[ProjectEntry], count: int) -> None:
        sec = tk.Frame(parent, bg=BG0)
        sec.pack(fill="x", padx=28, pady=(16, 0))

        head = tk.Frame(sec, bg=BG0)
        head.pack(fill="x", pady=(0, 10))
        title_lbl = tk.Label(head, text=title, bg=BG0, fg=TXT,
                             font=("Segoe UI", 14, "bold"))
        title_lbl.pack(side="left")
        pill = tk.Label(head, text=str(count), bg=SURF2, fg=TXT3,
                        font=("Consolas", 9), padx=5, pady=2)
        pill.pack(side="left", padx=6)
        tk.Label(head, text=subtitle, bg=BG0, fg=TXT3,
                 font=("Segoe UI", 10)).pack(side="right")

        grid = tk.Frame(sec, bg=BG0)
        grid.pack(fill="x")
        grid.bind("<Configure>", lambda e, g=grid, ps=projects: self._reflow_grid(g, ps))

        self._populate_grid(grid, projects)

    def _populate_grid(self, grid: tk.Frame, projects: list[ProjectEntry],
                       cols: int = 4) -> None:
        for w in grid.winfo_children():
            w.destroy()
        for col in range(cols):
            grid.grid_columnconfigure(col, weight=1)
        for idx, entry in enumerate(projects):
            col = idx % cols
            row = idx // cols
            card = ProjectCard(grid, entry, self._open_project)
            card.grid(row=row, column=col, sticky="nsew",
                      padx=(0, 10) if col < cols - 1 else 0,
                      pady=(0, 12))
            self._card_refs.append(card)
        # Capture refs at schedule-time so a subsequent _clear_inner() doesn't
        # cause the callback to call refresh_thumb() on destroyed widgets.
        refs_snapshot = list(self._card_refs)
        grid.after(50, lambda refs=refs_snapshot: [c.refresh_thumb() for c in refs])

    def _reflow_grid(self, grid: tk.Frame, projects: list[ProjectEntry]) -> None:
        w = grid.winfo_width()
        cols = max(1, min(5, w // 210))
        # Only re-populate if column count changed
        current_cols = getattr(grid, "_last_cols", -1)
        if cols != current_cols:
            grid._last_cols = cols
            self._populate_grid(grid, projects, cols)

    def _render_list_section(self, parent: tk.Widget,
                             projects: list[ProjectEntry]) -> None:
        sec = tk.Frame(parent, bg=BG0)
        sec.pack(fill="x", padx=28, pady=(16, 32))

        # Header row
        hdr = tk.Frame(sec, bg=SURF, highlightthickness=1,
                       highlightbackground=BORD)
        hdr.pack(fill="x")
        hdr.grid_columnconfigure(1, weight=1)
        for col, (text, w) in enumerate([("", 60), ("Projeto", 0),
                                          ("Duração", 90), ("Editado", 100),
                                          ("Status", 90), ("Tamanho", 80)]):
            tk.Label(hdr, text=text.upper(), bg=SURF, fg=TXT3,
                     font=("Segoe UI", 8), width=w // 8 if w else 0,
                     anchor="w").grid(row=0, column=col, sticky="ew",
                                      padx=(12 if col == 0 else 6, 6),
                                      pady=6)

        for entry in projects:
            self._make_list_row(sec, entry)

    def _make_list_row(self, parent: tk.Widget, entry: ProjectEntry) -> None:
        row = tk.Frame(parent, bg=SURF, cursor="hand2",
                       highlightthickness=0)
        row.pack(fill="x", pady=(1, 0))
        row.grid_columnconfigure(1, weight=1)

        # Mini thumb (colored square)
        thumb_c = tk.Canvas(row, width=56, height=36, highlightthickness=0,
                            bg="#1a1820")
        thumb_c.grid(row=0, column=0, padx=(12, 8), pady=8)
        hue_a, hue_b = CAT_HUE.get(entry.category, (260, 240))
        for y in range(36):
            t = y / 36
            hue = hue_a + (hue_b - hue_a) * t
            r, g, b = _hsl_to_rgb(hue, 0.38, 0.22)
            thumb_c.create_line(0, y, 56, y, fill=f"#{r:02x}{g:02x}{b:02x}")

        # Name + sub
        info = tk.Frame(row, bg=SURF)
        info.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        tk.Label(info, text=entry.name, bg=SURF, fg=TXT,
                 font=("Segoe UI", 11, "bold"), anchor="w").pack(anchor="w")
        sub = f"{entry.category} · {entry.clips_count} clipes"
        tk.Label(info, text=sub, bg=SURF, fg=TXT3,
                 font=("Segoe UI", 9), anchor="w").pack(anchor="w")

        # Duration
        tk.Label(row, text=entry.duration_label(), bg=SURF, fg=TXT2,
                 font=("Consolas", 10), width=7).grid(row=0, column=2, padx=6)
        # Edited
        tk.Label(row, text=f"Editado {entry.edited_label()}", bg=SURF, fg=TXT3,
                 font=("Segoe UI", 9), width=11).grid(row=0, column=3, padx=6)
        # Status badge
        st_label, st_bg, st_fg = STATUS.get(entry.status, STATUS["draft"])
        tk.Label(row, text=st_label, bg=st_bg, fg=st_fg,
                 font=("Segoe UI", 9), padx=6, pady=2).grid(row=0, column=4, padx=6)
        # Size
        tk.Label(row, text=entry.size_label(), bg=SURF, fg=TXT2,
                 font=("Consolas", 9), width=8).grid(row=0, column=5, padx=(6, 12))

        for w in _all_widgets(row):
            w.bind("<Button-1>", lambda e, en=entry: self._open_project(en))
            w.bind("<Enter>", lambda e, r=row: r.configure(bg=SURF2))
            w.bind("<Leave>", lambda e, r=row: r.configure(bg=SURF))

    def _render_templates(self) -> None:
        inner = self._inner
        hero = tk.Frame(inner, bg=BG0)
        hero.pack(fill="x", padx=28, pady=(28, 20))
        tk.Label(hero, text="BIBLIOTECA", bg=BG0, fg=TXT3,
                 font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(hero, text="Modelos para começar rápido.", bg=BG0, fg=TXT,
                 font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(hero, text="Presets de timeline, gradação e tipografia. Aplique em um clique.",
                 bg=BG0, fg=TXT2, font=("Segoe UI", 11), wraplength=500).pack(anchor="w", pady=(6, 0))

        grid = tk.Frame(inner, bg=BG0)
        grid.pack(fill="x", padx=28, pady=(0, 40))
        template_cards = [
            ("podcast", "Podcast — Talkshow 2 câmeras",       "2 trilhas de vídeo"),
            ("curso",   "Talking Head 1080 — Educacional",     "1 câmera + legendas"),
            ("shorts",  "Shorts 9:16 — Cinemático",            "Vertical + LUT"),
            ("review",  "Review Tech — Produto + B-roll",      "3 ângulos"),
            ("youtube", "Vlog — Cortes rápidos",               "Música + cortes"),
            ("youtube", "Multicam — Estúdio 4 ângulos",       "Sync + multicam"),
        ]
        num_rows = (len(template_cards) + 2) // 3
        for col in range(3):
            grid.grid_columnconfigure(col, weight=1)
        for row_idx in range(num_rows):
            grid.grid_rowconfigure(row_idx, weight=1)
        for i, (cat, title, sub) in enumerate(template_cards):
            col, row = i % 3, i // 3
            c = tk.Frame(grid, bg=SURF, highlightthickness=1,
                         highlightbackground=BORD, cursor="hand2")
            c.grid(row=row, column=col, sticky="nsew",
                   padx=(0, 12) if col < 2 else 0, pady=(0, 12))
            # Thumb
            thumb_c = tk.Canvas(c, height=100, bg="#1a1820",
                                 highlightthickness=0)
            thumb_c.pack(fill="x")
            thumb_c.bind("<Configure>",
                         lambda e, cv=thumb_c, ct=cat, s=i:
                         self._draw_template_thumb(cv, ct, s))
            # Body
            bd = tk.Frame(c, bg=SURF, padx=12)
            bd.pack(fill="x", pady=(8, 12))
            tk.Label(bd, text=title, bg=SURF, fg=TXT,
                     font=("Segoe UI", 11, "bold"), wraplength=200,
                     justify="left").pack(anchor="w")
            tk.Label(bd, text=sub, bg=SURF, fg=TXT3,
                     font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 0))
            use_btn = tk.Button(bd, text="Usar modelo →",
                                bg=ACC_S, fg="#DCD0FF",
                                activebackground=ACCENT, activeforeground="#fff",
                                relief="flat", font=("Segoe UI", 9),
                                cursor="hand2", padx=8, pady=3,
                                command=lambda t=title: self._use_template(t))
            use_btn.pack(anchor="w", pady=(8, 0))
            for w in _all_widgets(c):
                w.bind("<Enter>", lambda e, f=c: f.configure(bg=SURF2, highlightbackground=BORD_S))
                w.bind("<Leave>", lambda e, f=c: f.configure(bg=SURF, highlightbackground=BORD))

    def _draw_template_thumb(self, canvas: tk.Canvas, cat: str, seed: int) -> None:
        canvas.delete("all")
        w = canvas.winfo_width()
        h = 100
        if w < 4:
            return
        hue_a, hue_b = CAT_HUE.get(cat, (260, 240))
        for y in range(h):
            t = y / h
            hue = hue_a + (hue_b - hue_a) * t
            r, g, b = _hsl_to_rgb(hue, 0.40, 0.22 - t * 0.06)
            canvas.create_line(0, y, w, y, fill=f"#{r:02x}{g:02x}{b:02x}")
        canvas.create_text(w // 2, h // 2, text="Modelo",
                           fill="rgba(255,255,255,0.3)" if False else "#ffffff",
                           font=("Segoe UI", 10), stipple="gray50")

    def _use_template(self, title: str) -> None:
        self._show_toast(f'Modelo "{title}" — crie um projeto para aplicar.')
        self._show_new_dialog()

    def _render_placeholder(self, title: str, body: str) -> None:
        inner = self._inner
        hero = tk.Frame(inner, bg=BG0)
        hero.pack(fill="x", padx=28, pady=(28, 20))
        tk.Label(hero, text=title.upper(), bg=BG0, fg=TXT3,
                 font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(hero, text=title, bg=BG0, fg=TXT,
                 font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(hero, text=body, bg=BG0, fg=TXT2,
                 font=("Segoe UI", 11), wraplength=500).pack(anchor="w", pady=(6, 0))

        empty = tk.Frame(inner, bg=BG0, highlightthickness=1,
                         highlightbackground=BORD_S)
        empty.pack(fill="x", padx=28, pady=(0, 40))
        tk.Label(empty, text="○", bg=BG0, fg=TXT4,
                 font=("Segoe UI", 28)).pack(pady=(32, 4))
        tk.Label(empty, text="Em breve nesta vista", bg=BG0, fg=TXT2,
                 font=("Segoe UI", 12)).pack(pady=(0, 32))

    # ── Actions ───────────────────────────────────────────────────────────────

    def _quick_action_btn(self, parent: tk.Widget, text: str,
                          cmd: Callable, primary: bool = False) -> tk.Button:
        bg  = ACCENT if primary else SURF
        fg  = "#ffffff" if primary else TXT2
        abg = "#7055dd" if primary else SURF2
        b = tk.Button(parent, text=text, command=cmd, bg=bg, fg=fg,
                      activebackground=abg, activeforeground=fg,
                      relief="flat", font=("Segoe UI", 11),
                      cursor="hand2", padx=18, pady=8)
        b.pack(side="left", padx=(0, 10) if primary else 0)
        return b

    def _show_new_dialog(self) -> None:
        dialog = NewProjectDialog(self._root, self._on_create_project)
        self._root.wait_window(dialog)

    def _on_create_project(self, path: str, name: str,
                            category: str, template: str) -> None:
        register_recent_project(path, name=name, category=category, status="draft")
        self._on_create(path, name, category, template)

    def _open_project(self, entry: ProjectEntry) -> None:
        if not entry.exists():
            messagebox.showwarning(
                "Projeto não encontrado",
                f"O arquivo não existe mais:\n{entry.path}",
                parent=self._root,
            )
            self._projects = [e for e in self._projects if e.path != entry.path]
            _save_recent_projects(self._projects)
            self._refresh_content()
            return
        register_recent_project(
            entry.path, name=entry.name, category=entry.category,
            status=entry.status,
        )
        self._on_open(entry.path)

    def _open_existing(self) -> None:
        path = filedialog.askopenfilename(
            title="Abrir projeto CortaCerto",
            filetypes=[("Projeto CortaCerto", "*.ccp"),
                       ("Projeto legado", "*.cortacerto.json"),
                       ("JSON", "*.json"), ("Todos", "*.*")],
        )
        if path:
            register_recent_project(path)
            self._on_open(path)

    def _show_toast(self, message: str, duration_ms: int = 2500) -> None:
        if self._toast_lbl:
            with __import__("contextlib").suppress(Exception):
                self._toast_lbl.destroy()
        if self._toast_id:
            with __import__("contextlib").suppress(Exception):
                self._root.after_cancel(self._toast_id)
        toast = tk.Label(self._root, text=f"✓  {message}",
                         bg=BG2, fg=TXT, font=("Segoe UI", 11),
                         padx=16, pady=8,
                         highlightthickness=1, highlightbackground=BORD_S)
        toast.place(relx=0.5, rely=0.96, anchor="s")
        self._toast_lbl = toast
        self._toast_id = self._root.after(duration_ms, self._hide_toast)

    def _hide_toast(self) -> None:
        if self._toast_lbl:
            with __import__("contextlib").suppress(Exception):
                self._toast_lbl.destroy()
        self._toast_lbl = None
        self._toast_id = None
