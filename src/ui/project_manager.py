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
import subprocess
import time
import tkinter as tk
import uuid
from dataclasses import dataclass, field, asdict
from pathlib import Path
from tkinter import filedialog, messagebox, simpledialog
from typing import Callable, Optional

from src.core.app_settings import general_settings, remember_default_save_dir
from src.core.project_usage import usage_summary
from src.core.user_profile import (
    UserProfile,
    active_profile,
    authenticate_profile,
    create_profile,
    is_master,
    list_profiles,
    lock_profile,
    profile_is_unlocked,
    profile_remember_local,
    remove_profile,
    set_active_profile,
    set_profile_password,
    set_profile_remember_local,
    upsert_profile,
)

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


def _apply_saved_theme() -> None:
    """Apply the saved web/editor theme to the Tk project manager tokens."""
    global BG0, BG1, BG2, SURF, SURF2, SURF3, BORD, BORD_S, TXT, TXT2, TXT3, TXT4, ACCENT, ACC_S, HEADER
    theme = str(general_settings().get("ui_theme") or "violet").lower()
    palettes = {
        "graphite": {
            "BG0": "#08090b", "BG1": "#0d0f13", "BG2": "#181b22",
            "SURF": "#111318", "SURF2": "#1f242d", "SURF3": "#252b35",
            "BORD": "#252a33", "BORD_S": "#333a46", "ACCENT": "#a8b3c5",
            "ACC_S": "#1b2028", "HEADER": "#12151b",
        },
        "midnight": {
            "BG0": "#050812", "BG1": "#070b16", "BG2": "#0f1830",
            "SURF": "#0a1020", "SURF2": "#132044", "SURF3": "#192a55",
            "BORD": "#1b2b4e", "BORD_S": "#27416f", "ACCENT": "#69b7ff",
            "ACC_S": "#10233d", "HEADER": "#091023",
        },
        "emerald": {
            "BG0": "#050c0a", "BG1": "#07100d", "BG2": "#122019",
            "SURF": "#0d1310", "SURF2": "#17291f", "SURF3": "#1d3428",
            "BORD": "#1d3028", "BORD_S": "#2c4a3d", "ACCENT": "#50d9ad",
            "ACC_S": "#10291f", "HEADER": "#0b1712",
        },
    }
    palette = palettes.get(theme)
    if not palette:
        return
    for key, value in palette.items():
        globals()[key] = value


_apply_saved_theme()

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
    preview_video_path: str = ""
    owner_user_id: str = ""
    deleted_at: float = 0.0

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


def _hours_label(seconds: float) -> str:
    seconds = max(0.0, float(seconds or 0.0))
    if seconds >= 3600:
        return f"{seconds / 3600:.1f}h"
    if seconds >= 60:
        return f"{int(seconds / 60)}min"
    return f"{int(seconds)}s"


def _load_recent_projects() -> list[ProjectEntry]:
    try:
        raw = json.loads(_recent_path().read_text(encoding="utf-8"))
        entries = [ProjectEntry(**e) for e in raw.get("projects", [])]
        fresh = [e for e in entries if e.exists()]
        for e in fresh:
            meta = _read_project_metadata(e.path)
            if meta.get("name") and (meta.get("name_from_project") or not e.name or e.name == Path(e.path).stem):
                e.name = str(meta["name"])
            if meta.get("duration_s"): e.duration_s = float(meta["duration_s"])
            if meta.get("clips_count"): e.clips_count = int(meta["clips_count"])
            if meta.get("preview_video_path"): e.preview_video_path = str(meta["preview_video_path"])
            if meta.get("owner_user_id"): e.owner_user_id = str(meta["owner_user_id"])
            if not e.size_mb:
                try:
                    e.size_mb = Path(e.path).stat().st_size / (1024 * 1024)
                except Exception:
                    pass
        return fresh
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
    category: str = "",
    status: str = "",
    duration_s: float = 0.0,
    clips_count: int = 0,
    size_mb: float = 0.0,
    owner_user_id: str = "",
) -> None:
    """Call this every time a project is opened/created to update the recent list."""
    meta = _read_project_metadata(path)
    name = name or meta.get("name", "")
    requested_category = category
    requested_status = status
    category = category or meta.get("category", "youtube")
    status = status or meta.get("status", "draft")
    duration_s = duration_s or float(meta.get("duration_s", 0.0) or 0.0)
    clips_count = clips_count or int(meta.get("clips_count", 0) or 0)
    preview_video_path = str(meta.get("preview_video_path", "") or "")
    owner_user_id = owner_user_id or str(meta.get("owner_user_id") or active_profile().id)
    if not size_mb:
        try:
            size_mb = Path(path).stat().st_size / (1024 * 1024)
        except Exception:
            size_mb = 0.0
    entries = _load_recent_projects()
    existing = {e.path: e for e in entries}
    seed = int(hashlib.md5(path.encode()).hexdigest(), 16) % 10000
    if path in existing:
        e = existing[path]
        e.name = name or e.name
        if requested_category or not e.category:
            e.category = category or e.category
        if requested_status or not e.status:
            e.status = status or e.status
        e.opened_at = time.time()
        e.updated_at = time.time()
        e.deleted_at = 0.0
        if duration_s: e.duration_s = duration_s
        if clips_count: e.clips_count = clips_count
        if size_mb:     e.size_mb = size_mb
        if preview_video_path: e.preview_video_path = preview_video_path
        if owner_user_id: e.owner_user_id = owner_user_id
    else:
        existing[path] = ProjectEntry(
            path=path, name=name or Path(path).stem,
            category=category, status=status,
            opened_at=time.time(), updated_at=time.time(),
            duration_s=duration_s, clips_count=clips_count, size_mb=size_mb,
            thumb_seed=seed, wave_seed=(seed * 7 + 13) % 10000,
            preview_video_path=preview_video_path,
            owner_user_id=owner_user_id,
            deleted_at=0.0,
        )
    # Sort by opened_at desc, keep max 50
    merged = sorted(existing.values(), key=lambda e: e.opened_at, reverse=True)[:50]
    _save_recent_projects(merged)


def _read_project_metadata(path: str) -> dict[str, object]:
    """Best-effort metadata read for .ccproj/.json cards."""
    p = Path(path)
    if not p.is_file():
        return {}
    if p.suffix.lower() not in {".ccproj", ".ccp", ".json"}:
        return {"preview_video_path": str(p)}
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}
    tracks = [
        raw.get("video_track", {}).get("clips", []),
        raw.get("audio_track", {}).get("clips", []),
        raw.get("text_track", {}).get("clips", []),
        raw.get("overlay_track", {}).get("clips", []),
    ]
    for key in ("extra_video_tracks", "extra_audio_tracks", "extra_overlay_tracks"):
        for track in raw.get(key, []) or []:
            tracks.append(track.get("clips", []) or [])
    all_clips = [c for group in tracks for c in group if isinstance(c, dict)]
    video_path = str(raw.get("videoPath") or "")
    if not video_path:
        for clip in all_clips:
            if clip.get("clip_type") in {"speech", "video", "video_overlay"} and clip.get("source_path"):
                video_path = str(clip.get("source_path"))
                break
    return {
        "name": raw.get("_projectName") or raw.get("name") or p.stem,
        "name_from_project": bool(raw.get("_projectName")),
        "category": raw.get("_category") or raw.get("category") or "youtube",
        "status": raw.get("_status") or "edit",
        "duration_s": float(raw.get("duration_s") or 0.0),
        "clips_count": len(all_clips),
        "preview_video_path": video_path if Path(video_path).is_file() else "",
        "owner_user_id": raw.get("_owner_user_id") or raw.get("owner_user_id") or "",
    }


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


def _thumb_cache_dir() -> Path:
    path = _app_data_dir() / "project_thumbs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _extract_video_frame(entry: ProjectEntry, w: int, h: int) -> Optional[object]:
    """Return a rectangular PhotoImage extracted from the project's video."""
    if not _PIL:
        return None
    video_path = entry.preview_video_path
    if not video_path and Path(entry.path).suffix.lower() not in {".ccproj", ".ccp", ".json"}:
        video_path = entry.path
    if not video_path or not Path(video_path).is_file():
        return None
    key = hashlib.md5(f"{video_path}|{Path(video_path).stat().st_mtime}|{w}x{h}".encode()).hexdigest()
    out = _thumb_cache_dir() / f"{key}.jpg"
    if not out.exists():
        try:
            from src.ffmpeg_env import ffmpeg
            seek = max(0.1, min(8.0, (entry.duration_s or 8.0) * 0.25))
            subprocess.run(
                [
                    ffmpeg(), "-y", "-ss", f"{seek:.3f}", "-i", video_path,
                    "-frames:v", "1",
                    "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
                    "-q:v", "4",
                    str(out),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=8,
                check=True,
            )
        except Exception:
            return None
    try:
        img = Image.open(out).convert("RGB")
        if img.size != (w, h):
            img = img.resize((w, h), Image.LANCZOS)
        return ImageTk.PhotoImage(img)
    except Exception:
        return None


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
        self._initial_dir  = self._default_save_dir()
        self._path_touched = False

        self._build()
        self._center(parent)

    def _default_save_dir(self) -> str:
        configured = str(general_settings().get("default_save_dir") or "").strip()
        if configured and Path(configured).is_dir():
            return configured
        fallback = Path.home() / "Videos" / "CortaCerto"
        fallback.mkdir(parents=True, exist_ok=True)
        return str(fallback)

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
        self._path_var.set(str(Path(self._initial_dir) / self._category_var.get() / "novo-projeto.ccproj"))
        self._name_var.trace_add("write", lambda *_: self._sync_default_path())
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
            defaultextension=".ccproj",
            filetypes=[("Projeto CortaCerto", "*.ccproj"), ("Projeto legado", "*.ccp"), ("Todos", "*.*")],
            initialdir=self._initial_dir,
            initialfile=(self._name_var.get() or "novo-projeto") + ".ccproj",
        )
        if path:
            self._path_var.set(path)
            self._initial_dir = str(Path(path).parent)
            self._path_touched = True

    def _select_cat(self, cid: str) -> None:
        self._category_var.set(cid)
        self._sync_default_path(force=not self._path_touched)
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
        try:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            pass
        remember_default_save_dir(path)
        self.destroy()
        self._on_create(path, name, cat, tmpl)

    def _sync_default_path(self, force: bool = False) -> None:
        if self._path_touched and not force:
            return
        raw_name = self._name_var.get().strip() or "novo-projeto"
        safe_name = "".join(ch if ch.isalnum() or ch in " -_." else "-" for ch in raw_name).strip(" .") or "novo-projeto"
        category = self._category_var.get() or "youtube"
        self._path_var.set(str(Path(self._initial_dir) / category / f"{safe_name}.ccproj"))


# ── Project card widget ───────────────────────────────────────────────────────

class ProjectCard(tk.Canvas):
    """Project card with rounded corners drawn on a Canvas."""

    THUMB_H = 128
    RADIUS  = 18

    def __init__(self, parent: tk.Widget, entry: ProjectEntry,
                 on_open: Callable[[ProjectEntry], None],
                 on_context: Optional[Callable[[ProjectEntry, int, int], None]] = None,
                 zoom: float = 1.0,
                 accent: str = ACCENT) -> None:
        super().__init__(parent, bg=BG0, highlightthickness=0, cursor="hand2")
        self._entry   = entry
        self._on_open = on_open
        self._on_context = on_context
        self._accent  = accent
        self._zoom = max(0.85, min(1.25, float(zoom or 1.0)))
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
        self._thumb_h = int(self.THUMB_H * self._zoom)
        self._thumb = tk.Canvas(self._frame, bg="#1a1820",
                                highlightthickness=0, height=self._thumb_h)
        self._thumb.pack(fill="x")
        self._thumb.bind("<Configure>", self._draw_thumb)

        # ── Card body ─────────────────────────────────────────────────────────
        body = tk.Frame(self._frame, bg=SURF, padx=12)
        body.pack(fill="x", pady=(8, 10))

        tk.Label(body, text=e.name, bg=SURF, fg=TXT,
                 font=("Segoe UI", max(10, int(11 * self._zoom)), "bold"),
                 anchor="w", wraplength=int(190 * self._zoom), justify="left").pack(fill="x")

        meta = tk.Frame(body, bg=SURF)
        meta.pack(fill="x", pady=(4, 0))

        tk.Label(meta, text=f"Editado {e.edited_label()}", bg=SURF,
                 fg=TXT3, font=("Segoe UI", max(8, int(9 * self._zoom)))).pack(side="left")
        if e.clips_count > 0:
            tk.Label(meta, text=" · ", bg=SURF, fg=TXT4,
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(meta, text=f"{e.clips_count} clipes", bg=SURF, fg=TXT3,
                     font=("Segoe UI", max(8, int(9 * self._zoom)))).pack(side="left")

        st_label, st_bg, st_fg = STATUS.get(e.status, STATUS["draft"])
        tk.Label(meta, text=st_label, bg=st_bg, fg=st_fg,
                 font=("Segoe UI", max(7, int(8 * self._zoom))), padx=6, pady=2).pack(side="right")

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
        h = self._thumb_h
        if w < 4:
            return

        e = self._entry
        self._thumb_photo = _extract_video_frame(e, w, h)
        if self._thumb_photo is not None:
            c.create_image(0, 0, anchor="nw", image=self._thumb_photo)
        else:
            hue_a, hue_b = CAT_HUE.get(e.category, (260, 240))
            for y in range(h):
                t = y / h
                hue = hue_a + (hue_b - hue_a) * t
                r, g, b = _hsl_to_rgb(hue, 0.38, 0.20 - t * 0.05)
                c.create_line(0, y, w, y, fill=f"#{r:02x}{g:02x}{b:02x}")

            for i in range(0, w + h, 26):
                c.create_line(i, 0, 0, i, fill="#ffffff", stipple="gray12")

        scrim_h = 40
        for y in range(scrim_h):
            alpha = int((y / scrim_h) * 160)
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

        def on_context(e):
            if self._on_context:
                self._on_context(self._entry, e.x_root, e.y_root)

        for w in [self] + _all_widgets(self._frame):
            w.bind("<Enter>", on_enter)
            w.bind("<Leave>", on_leave)
            w.bind("<Button-1>", on_click)
            w.bind("<Button-3>", on_context)

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
        self._project_zoom = tk.DoubleVar(value=1.0)
        self._profile: UserProfile = active_profile()
        self._toast_id: Optional[str] = None
        self._toast_lbl: Optional[tk.Label] = None
        self._chip_btns: dict[str, tk.Label] = {}
        self._card_refs: list[ProjectCard] = []
        self._avatar_canvas: Optional[tk.Canvas] = None
        self._avatar_name_lbl: Optional[tk.Label] = None

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

        # Local profile avatar
        profile_btn = tk.Frame(sb, bg=BG1, cursor="hand2")
        profile_btn.pack(fill="x", pady=(8, 14))
        av = tk.Canvas(profile_btn, width=38, height=38, bg=BG1, highlightthickness=0, cursor="hand2")
        av.pack(anchor="center")
        name_lbl = tk.Label(profile_btn, text="", bg=BG1, fg=TXT3, font=("Segoe UI", 7), cursor="hand2")
        name_lbl.pack(anchor="center", pady=(2, 0))
        self._avatar_canvas = av
        self._avatar_name_lbl = name_lbl
        self._refresh_profile_avatar()
        for w in (profile_btn, av, name_lbl):
            w.bind("<Button-1>", lambda _e: self._show_profile_dialog())
            w.bind("<Enter>", lambda _e, f=profile_btn, l=name_lbl: (f.configure(bg=SURF), l.configure(bg=SURF, fg=TXT)))
            w.bind("<Leave>", lambda _e, f=profile_btn, l=name_lbl: (f.configure(bg=BG1), l.configure(bg=BG1, fg=TXT3)))

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

    def _refresh_profile_avatar(self) -> None:
        if self._avatar_canvas:
            self._draw_profile_avatar(self._avatar_canvas, self._profile, 38)
        if self._avatar_name_lbl:
            name = self._profile.name.strip() or "Editor"
            self._avatar_name_lbl.configure(text=name[:10])

    def _draw_profile_avatar(self, canvas: tk.Canvas, profile: UserProfile, size: int) -> None:
        canvas.delete("all")
        avatar_path = Path(profile.avatar_path) if profile.avatar_path else None
        if avatar_path and avatar_path.is_file() and _PIL:
            try:
                img = Image.open(avatar_path).convert("RGB")
                rotation = float(getattr(profile, "avatar_rotation_deg", 0.0) or 0.0)
                if abs(rotation) > 0.01:
                    img = img.rotate(-rotation, expand=True, resample=Image.BICUBIC)
                zoom = max(1.0, min(3.0, float(getattr(profile, "avatar_zoom", 1.0) or 1.0)))
                scale = max(size / img.width, size / img.height) * zoom
                rw = max(size, int(img.width * scale))
                rh = max(size, int(img.height * scale))
                img = img.resize((rw, rh), Image.LANCZOS)
                max_x = max(0, rw - size)
                max_y = max(0, rh - size)
                ox = max(-1.0, min(1.0, float(getattr(profile, "avatar_offset_x", 0.0) or 0.0)))
                oy = max(-1.0, min(1.0, float(getattr(profile, "avatar_offset_y", 0.0) or 0.0)))
                left = int((max_x / 2) + (ox * max_x / 2))
                top = int((max_y / 2) + (oy * max_y / 2))
                img = img.crop((left, top, left + size, top + size))
                mask = Image.new("L", (size, size), 0)
                draw = ImageDraw.Draw(mask)
                draw.ellipse((0, 0, size - 1, size - 1), fill=255)
                img.putalpha(mask)
                photo = ImageTk.PhotoImage(img)
                canvas._photo = photo
                canvas.create_image(0, 0, anchor="nw", image=photo)
                canvas.create_oval(1, 1, size - 2, size - 2, outline=BORD_S)
                return
            except Exception:
                pass
        canvas.create_oval(1, 1, size - 2, size - 2, fill="#2a2535", outline=BORD_S)
        canvas.create_text(
            size // 2,
            size // 2,
            text=profile.initials(),
            fill=TXT,
            font=("Segoe UI", max(9, size // 4), "bold"),
        )

    def _open_avatar_editor(
        self,
        parent: tk.Toplevel,
        profile: UserProfile,
    ) -> tuple[str, float, float, float, float] | None:
        if not _PIL:
            path = filedialog.askopenfilename(
                title="Escolher avatar",
                filetypes=[("Imagens", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("Todos", "*.*")],
                parent=parent,
            )
            return (path, 1.0, 0.0, 0.0, 0.0) if path else None

        path = filedialog.askopenfilename(
            title="Escolher avatar",
            filetypes=[("Imagens", "*.png;*.jpg;*.jpeg;*.webp;*.bmp"), ("Todos", "*.*")],
            parent=parent,
        )
        if not path:
            return None

        dlg = tk.Toplevel(parent)
        dlg.title("Enquadrar avatar")
        dlg.configure(bg=BG2)
        dlg.resizable(False, False)
        dlg.transient(parent)
        dlg.grab_set()

        result: dict[str, object] = {}
        zoom_var = tk.DoubleVar(value=max(1.2, min(3.0, float(getattr(profile, "avatar_zoom", 1.2) or 1.2))))
        x_var = tk.DoubleVar(value=max(-1.0, min(1.0, float(getattr(profile, "avatar_offset_x", 0.0) or 0.0))))
        y_var = tk.DoubleVar(value=max(-1.0, min(1.0, float(getattr(profile, "avatar_offset_y", 0.0) or 0.0))))
        rotate_var = tk.DoubleVar(value=max(-180.0, min(180.0, float(getattr(profile, "avatar_rotation_deg", 0.0) or 0.0))))

        wrap = tk.Frame(dlg, bg=BG2, padx=18, pady=18)
        wrap.pack(fill="both", expand=True)
        tk.Label(wrap, text="Enquadre sua foto", bg=BG2, fg=TXT,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")
        tk.Label(wrap, text="Ajuste o zoom e a posição antes de salvar o avatar.",
                 bg=BG2, fg=TXT3, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 12))

        preview = tk.Canvas(wrap, width=172, height=172, bg=BG2, highlightthickness=0)
        preview.pack(pady=(0, 12))

        def draw_preview(_event=None) -> None:
            temp = UserProfile(
                id=profile.id,
                name=profile.name,
                email=profile.email,
                avatar_path=path,
                plan=profile.plan,
                role=profile.role,
                status=profile.status,
                avatar_zoom=zoom_var.get(),
                avatar_offset_x=x_var.get(),
                avatar_offset_y=y_var.get(),
                avatar_rotation_deg=rotate_var.get(),
            )
            self._draw_profile_avatar(preview, temp, 172)

        def slider(label: str, var: tk.DoubleVar, low: float, high: float) -> None:
            row = tk.Frame(wrap, bg=BG2)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=BG2, fg=TXT3, font=("Segoe UI", 8, "bold"), width=9, anchor="w").pack(side="left")
            tk.Scale(
                row,
                from_=low,
                to=high,
                resolution=0.05,
                orient="horizontal",
                variable=var,
                command=draw_preview,
                bg=BG2,
                fg=TXT3,
                troughcolor=SURF3,
                activebackground=ACCENT,
                highlightthickness=0,
                showvalue=False,
                length=220,
                sliderlength=14,
            ).pack(side="left", fill="x", expand=True)

        slider("Zoom", zoom_var, 1.0, 3.0)
        slider("Lateral", x_var, -1.0, 1.0)
        slider("Altura", y_var, -1.0, 1.0)
        slider("Girar", rotate_var, -180.0, 180.0)
        tk.Label(wrap, text="Dica: para mover altura/lateral com mais liberdade, aumente um pouco o zoom.",
                 bg=BG2, fg=TXT4, font=("Segoe UI", 8)).pack(anchor="w", pady=(4, 0))

        btns = tk.Frame(wrap, bg=BG2)
        btns.pack(fill="x", pady=(14, 0))

        def confirm() -> None:
            result["value"] = (path, zoom_var.get(), x_var.get(), y_var.get(), rotate_var.get())
            dlg.destroy()

        tk.Button(btns, text="Cancelar", command=dlg.destroy, bg=SURF, fg=TXT2,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="right")
        tk.Button(btns, text="Usar avatar", command=confirm, bg=ACCENT, fg="#fff",
                  activebackground="#7055dd", activeforeground="#fff", relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="right", padx=(0, 8))

        draw_preview()
        self._root.wait_window(dlg)
        value = result.get("value")
        return value if isinstance(value, tuple) else None

    def _profile_projects(self, include_deleted: bool = False) -> list[ProjectEntry]:
        active_id = self._profile.id
        known_ids = {p.id for p in list_profiles()}
        return [
            e for e in self._projects
            if (include_deleted or not e.deleted_at)
            and (not e.owner_user_id or e.owner_user_id == active_id or e.owner_user_id not in known_ids)
        ]

    def _trash_projects(self) -> list[ProjectEntry]:
        return [e for e in self._profile_projects(include_deleted=True) if e.deleted_at]

    def _profile_auth_label(self, profile: UserProfile) -> str:
        if str(profile.status or "").lower() == "suspended":
            return "Usuario suspenso"
        if not profile.auth_enabled:
            return "Sem senha local"
        if profile_remember_local(profile.id):
            return "Mantido conectado neste PC"
        if profile_is_unlocked(profile.id):
            return "Sessao desbloqueada"
        return "Senha local ativa"

    def _profile_role_label(self, profile: UserProfile) -> str:
        return "MASTER" if is_master(profile) else "USUARIO"

    def _show_profile_dialog(self) -> None:
        dlg = tk.Toplevel(self._root)
        dlg.title("Conta e perfil")
        dlg.configure(bg=BG2)
        dlg.resizable(False, False)
        dlg.transient(self._root)
        dlg.grab_set()

        profiles = list_profiles()
        selected_id = tk.StringVar(value=self._profile.id)
        login_password = tk.StringVar()
        remember_var = tk.BooleanVar(value=profile_remember_local(self._profile.id))
        tab_var = tk.StringVar(value="profile" if profile_is_unlocked(self._profile.id) or not self._profile.auth_enabled else "access")

        wrap = tk.Frame(dlg, bg=BG2, padx=18, pady=18)
        wrap.pack(fill="both", expand=True)
        header = tk.Frame(wrap, bg=BG2)
        header.pack(fill="x")
        tk.Label(header, text="Conta CortaCerto", bg=BG2, fg=TXT,
                 font=("Segoe UI", 17, "bold")).pack(side="left")
        tk.Button(header, text="Fechar", command=dlg.destroy, bg=SURF, fg=TXT2,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=10, pady=5).pack(side="right")
        tk.Label(wrap, text="Login local, perfil e gerenciamento preparados para migrar para Firebase/NoSQL.",
                 bg=BG2, fg=TXT3, font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 14))

        tabbar = tk.Frame(wrap, bg=BG2)
        tabbar.pack(fill="x", pady=(0, 12))
        pages = tk.Frame(wrap, bg=BG2)
        pages.pack(fill="both", expand=True)
        tab_buttons: dict[str, tk.Button] = {}
        page_frames: dict[str, tk.Frame] = {}

        def tab_button(key: str, label: str) -> None:
            btn = tk.Button(tabbar, text=label, bg=SURF, fg=TXT2,
                            activebackground=SURF2, activeforeground=TXT,
                            relief="flat", cursor="hand2", padx=14, pady=7,
                            command=lambda k=key: show_tab(k))
            btn.pack(side="left", padx=(0, 6))
            tab_buttons[key] = btn

        tab_button("access", "Entrar / cadastrar")
        tab_button("profile", "Meu perfil")
        if is_master(self._profile):
            tab_button("master", "Usuarios")

        def make_page(key: str) -> tk.Frame:
            frame = tk.Frame(pages, bg=BG2)
            page_frames[key] = frame
            return frame

        def show_tab(key: str) -> None:
            if key == "master" and not is_master(active_profile()):
                key = "profile"
            tab_var.set(key)
            for frame in page_frames.values():
                frame.pack_forget()
            if key in page_frames:
                page_frames[key].pack(fill="both", expand=True)
            for tab_key, btn in tab_buttons.items():
                active = tab_key == key
                btn.configure(bg=ACCENT if active else SURF, fg="#fff" if active else TXT2)

        def profile_by_id(profile_id: str) -> UserProfile | None:
            return next((p for p in list_profiles() if p.id == profile_id), None)

        def refresh_self() -> None:
            self._profile = active_profile()
            self._refresh_profile_avatar()
            self._refresh_content()

        def label_for(profile: UserProfile) -> str:
            prefix = "* " if profile.id == active_profile().id else ""
            status = " suspenso" if str(profile.status or "").lower() == "suspended" else ""
            return f"{prefix}{profile.name or 'Editor'}  [{self._profile_role_label(profile)}]{status}"

        access = make_page("access")
        access.grid_columnconfigure(0, weight=1)
        access.grid_columnconfigure(1, weight=1)
        tk.Label(access, text="Escolha a conta", bg=BG2, fg=TXT3,
                 font=("Segoe UI", 8, "bold")).grid(row=0, column=0, sticky="w")
        access_list = tk.Listbox(access, height=8, bg=SURF, fg=TXT,
                                 selectbackground=ACCENT, relief="flat",
                                 highlightthickness=1, highlightbackground=BORD)
        access_list.grid(row=1, column=0, rowspan=7, sticky="nsew", padx=(0, 14))
        login_box = tk.Frame(access, bg=SURF, highlightthickness=1, highlightbackground=BORD)
        login_box.grid(row=1, column=1, sticky="new")
        tk.Label(login_box, text="Acesso local", bg=SURF, fg=TXT,
                 font=("Segoe UI", 13, "bold")).pack(anchor="w", padx=14, pady=(14, 2))
        tk.Label(login_box, text="Use senha somente se a conta estiver protegida.",
                 bg=SURF, fg=TXT3, font=("Segoe UI", 9)).pack(anchor="w", padx=14, pady=(0, 10))
        tk.Entry(login_box, textvariable=login_password, show="*", bg=BG2, fg=TXT,
                 insertbackground=TXT, relief="flat", highlightthickness=1,
                 highlightbackground=BORD, font=("Segoe UI", 10)).pack(fill="x", padx=14, ipady=6)
        tk.Checkbutton(login_box, text="Manter conectado neste PC", variable=remember_var,
                       bg=SURF, fg=TXT2, selectcolor=BG2, activebackground=SURF,
                       activeforeground=TXT, font=("Segoe UI", 9)).pack(anchor="w", padx=10, pady=(8, 8))
        access_status = tk.Label(login_box, text="", bg=SURF, fg=TXT3, font=("Segoe UI", 8))
        access_status.pack(anchor="w", padx=14, pady=(0, 12))
        login_actions = tk.Frame(login_box, bg=SURF)
        login_actions.pack(fill="x", padx=14, pady=(0, 14))

        def selected_access_profile() -> UserProfile | None:
            sel = access_list.curselection()
            if not sel:
                return None
            current = list_profiles()
            if sel[0] >= len(current):
                return None
            return current[sel[0]]

        def reload_access(select_id: str = "") -> None:
            profiles_now = list_profiles()
            access_list.delete(0, "end")
            target_ix = 0
            for ix, profile in enumerate(profiles_now):
                access_list.insert("end", label_for(profile))
                if profile.id == (select_id or selected_id.get()):
                    target_ix = ix
            if profiles_now:
                access_list.selection_set(target_ix)
                access_list.activate(target_ix)
                selected_id.set(profiles_now[target_ix].id)
                access_status.configure(text=self._profile_auth_label(profiles_now[target_ix]))

        def on_access_select(_event=None) -> None:
            target = selected_access_profile()
            if not target:
                return
            selected_id.set(target.id)
            remember_var.set(profile_remember_local(target.id))
            access_status.configure(text=self._profile_auth_label(target))

        def do_login() -> None:
            target = selected_access_profile()
            if not target:
                return
            if str(target.status or "").lower() == "suspended":
                messagebox.showwarning("Usuario suspenso", "Este usuario esta suspenso.", parent=dlg)
                return
            if target.auth_enabled and not authenticate_profile(target.id, login_password.get(), remember_local=remember_var.get()):
                messagebox.showwarning("Senha invalida", "Digite a senha local deste usuario.", parent=dlg)
                return
            if not target.auth_enabled:
                set_active_profile(target.id)
                set_profile_remember_local(target.id, remember_var.get())
            login_password.set("")
            refresh_self()
            reload_access(self._profile.id)
            load_profile_page()
            load_master_page()
            show_tab("profile")
            self._show_toast(f"Conta ativa: {self._profile.name}")

        def create_account() -> None:
            try:
                profile = create_profile("Novo usuario", role="member", make_active=True)
                refresh_self()
                reload_access(profile.id)
                load_profile_page()
                load_master_page()
                show_tab("profile")
            except Exception as exc:
                messagebox.showerror("Cadastro", str(exc), parent=dlg)

        tk.Button(login_actions, text="Entrar", command=do_login, bg=ACCENT, fg="#fff",
                  activebackground="#7055dd", activeforeground="#fff", relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left", padx=(0, 8))
        tk.Button(login_actions, text="Cadastrar conta", command=create_account, bg=SURF3, fg=TXT,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left")
        access_list.bind("<<ListboxSelect>>", on_access_select)

        profile_page = make_page("profile")
        profile_page.grid_columnconfigure(1, weight=1)
        profile_preview = tk.Canvas(profile_page, width=96, height=96, bg=BG2, highlightthickness=0)
        profile_preview.grid(row=0, column=0, rowspan=4, sticky="nw", padx=(0, 18), pady=(0, 10))
        profile_title = tk.Label(profile_page, text="", bg=BG2, fg=TXT, font=("Segoe UI", 15, "bold"))
        profile_title.grid(row=0, column=1, sticky="w")
        profile_meta = tk.Label(profile_page, text="", bg=BG2, fg=TXT3, font=("Segoe UI", 9))
        profile_meta.grid(row=1, column=1, sticky="w", pady=(2, 12))
        profile_name = tk.StringVar()
        profile_email = tk.StringVar()
        profile_plan = tk.StringVar()
        profile_password = tk.StringVar()

        def field(parent: tk.Frame, row: int, label: str, var: tk.StringVar, show: str = "") -> None:
            tk.Label(parent, text=label.upper(), bg=BG2, fg=TXT3,
                     font=("Segoe UI", 8, "bold")).grid(row=row, column=0, columnspan=2, sticky="w", pady=(5, 2))
            tk.Entry(parent, textvariable=var, show=show, bg=SURF, fg=TXT,
                     insertbackground=TXT, relief="flat", highlightthickness=1,
                     highlightbackground=BORD, font=("Segoe UI", 10)).grid(row=row + 1, column=0, columnspan=2, sticky="ew", ipady=6)

        form = tk.Frame(profile_page, bg=BG2)
        form.grid(row=4, column=0, columnspan=2, sticky="ew")
        form.grid_columnconfigure(0, weight=1)
        form.grid_columnconfigure(1, weight=1)
        field(form, 0, "Nome", profile_name)
        field(form, 2, "Email", profile_email)
        field(form, 4, "Plano local", profile_plan)
        field(form, 6, "Nova senha local", profile_password, show="*")
        profile_keep = tk.BooleanVar(value=profile_remember_local(self._profile.id))
        tk.Checkbutton(form, text="Manter conectado neste PC", variable=profile_keep,
                       bg=BG2, fg=TXT2, selectcolor=SURF, activebackground=BG2,
                       activeforeground=TXT, font=("Segoe UI", 9)).grid(row=8, column=0, columnspan=2, sticky="w", pady=(8, 0))
        profile_actions = tk.Frame(profile_page, bg=BG2)
        profile_actions.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        def load_profile_page() -> None:
            profile = active_profile()
            profile_name.set(profile.name)
            profile_email.set(profile.email)
            profile_plan.set(profile.plan or "Local")
            profile_password.set("")
            profile_keep.set(profile_remember_local(profile.id))
            self._draw_profile_avatar(profile_preview, profile, 96)
            profile_title.configure(text=profile.name or "Editor")
            profile_meta.configure(text=f"{self._profile_role_label(profile)} | {self._profile_auth_label(profile)} | {profile.id}")

        def edit_current_avatar() -> None:
            current = active_profile()
            result = self._open_avatar_editor(dlg, current)
            if not result:
                return
            path, zoom, ox, oy, rotation = result
            current.avatar_path = path
            current.avatar_zoom = float(zoom)
            current.avatar_offset_x = float(ox)
            current.avatar_offset_y = float(oy)
            current.avatar_rotation_deg = float(rotation)
            upsert_profile(current, actor=current, make_active=True)
            refresh_self()
            load_profile_page()

        def save_current_profile() -> None:
            current = active_profile()
            if current.auth_enabled and not profile_is_unlocked(current.id):
                messagebox.showwarning("Conta bloqueada", "Entre na conta antes de editar o perfil.", parent=dlg)
                return
            current.name = (profile_name.get() or "Editor").strip()
            current.email = profile_email.get().strip()
            current.plan = (profile_plan.get() or "Local").strip()
            current = upsert_profile(current, actor=current, make_active=True)
            if profile_password.get().strip():
                current = set_profile_password(current, profile_password.get(), actor=current, make_active=True)
                authenticate_profile(current.id, profile_password.get(), remember_local=profile_keep.get())
            else:
                set_profile_remember_local(current.id, profile_keep.get())
            refresh_self()
            load_profile_page()
            reload_access(current.id)
            self._show_toast("Perfil salvo.")

        def logout_current() -> None:
            lock_profile(active_profile().id)
            load_profile_page()
            reload_access(active_profile().id)
            show_tab("access")
            self._show_toast("Conta bloqueada.")

        def promote_current_master() -> None:
            current = active_profile()
            current.role = "master"
            current.plan = "Master"
            upsert_profile(current, actor=current, make_active=True)
            refresh_self()
            load_profile_page()
            if "master" not in tab_buttons:
                tab_button("master", "Usuarios")
            load_master_page()
            self._show_toast("Conta definida como MASTER.")

        tk.Button(profile_actions, text="Editar avatar", command=edit_current_avatar, bg=SURF, fg=TXT2,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left", padx=(0, 8))
        tk.Button(profile_actions, text="Salvar perfil", command=save_current_profile, bg=ACCENT, fg="#fff",
                  activebackground="#7055dd", activeforeground="#fff", relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left", padx=(0, 8))
        tk.Button(profile_actions, text="Logout", command=logout_current, bg=SURF, fg=TXT2,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left", padx=(0, 8))
        if not is_master(self._profile):
            tk.Button(profile_actions, text="Definir como MASTER", command=promote_current_master, bg=SURF3, fg=TXT,
                      activebackground=SURF2, activeforeground=TXT, relief="flat",
                      cursor="hand2", padx=12, pady=6).pack(side="left")

        master_page = make_page("master")
        master_page.grid_columnconfigure(0, weight=1)
        master_page.grid_columnconfigure(1, weight=1)
        tk.Label(master_page, text="Gestao local de usuarios", bg=BG2, fg=TXT,
                 font=("Segoe UI", 14, "bold")).grid(row=0, column=0, columnspan=2, sticky="w")
        tk.Label(master_page, text="Controle de nivel/status. Depois esta estrutura pode virar colecao `users` no Firebase.",
                 bg=BG2, fg=TXT3, font=("Segoe UI", 9)).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 12))
        master_list = tk.Listbox(master_page, height=9, bg=SURF, fg=TXT,
                                 selectbackground=ACCENT, relief="flat",
                                 highlightthickness=1, highlightbackground=BORD)
        master_list.grid(row=2, column=0, rowspan=8, sticky="nsew", padx=(0, 14))
        admin_name = tk.StringVar()
        admin_email = tk.StringVar()
        admin_plan = tk.StringVar()
        admin_role = tk.StringVar(value="member")
        admin_status = tk.StringVar(value="active")

        admin_form = tk.Frame(master_page, bg=BG2)
        admin_form.grid(row=2, column=1, sticky="new")
        admin_form.grid_columnconfigure(0, weight=1)
        field(admin_form, 0, "Nome", admin_name)
        field(admin_form, 2, "Email", admin_email)
        field(admin_form, 4, "Plano", admin_plan)
        tk.Label(admin_form, text="NIVEL", bg=BG2, fg=TXT3, font=("Segoe UI", 8, "bold")).grid(row=6, column=0, sticky="w", pady=(5, 2))
        tk.OptionMenu(admin_form, admin_role, "master", "member").grid(row=7, column=0, sticky="ew")
        tk.Label(admin_form, text="STATUS", bg=BG2, fg=TXT3, font=("Segoe UI", 8, "bold")).grid(row=8, column=0, sticky="w", pady=(5, 2))
        tk.OptionMenu(admin_form, admin_status, "active", "suspended").grid(row=9, column=0, sticky="ew")
        admin_actions = tk.Frame(master_page, bg=BG2)
        admin_actions.grid(row=10, column=0, columnspan=2, sticky="ew", pady=(14, 0))

        def selected_admin_profile() -> UserProfile | None:
            sel = master_list.curselection()
            current = list_profiles()
            if not sel or sel[0] >= len(current):
                return None
            return current[sel[0]]

        def load_master_page(select_id: str = "") -> None:
            master_list.delete(0, "end")
            current = list_profiles()
            target_ix = 0
            for ix, profile in enumerate(current):
                master_list.insert("end", label_for(profile))
                if profile.id == (select_id or selected_id.get()):
                    target_ix = ix
            if current:
                master_list.selection_set(target_ix)
                master_list.activate(target_ix)
                profile = current[target_ix]
                selected_id.set(profile.id)
                admin_name.set(profile.name)
                admin_email.set(profile.email)
                admin_plan.set(profile.plan)
                admin_role.set(profile.role or "member")
                admin_status.set(profile.status or "active")

        def on_master_select(_event=None) -> None:
            target = selected_admin_profile()
            if not target:
                return
            selected_id.set(target.id)
            admin_name.set(target.name)
            admin_email.set(target.email)
            admin_plan.set(target.plan)
            admin_role.set(target.role or "member")
            admin_status.set(target.status or "active")

        def save_admin_user() -> None:
            if not is_master(active_profile()):
                return
            target = selected_admin_profile()
            if not target:
                return
            target.name = (admin_name.get() or "Editor").strip()
            target.email = admin_email.get().strip()
            target.plan = (admin_plan.get() or "Local").strip()
            target.role = admin_role.get()
            target.status = admin_status.get()
            try:
                upsert_profile(target, actor=active_profile(), make_active=False)
            except Exception as exc:
                messagebox.showerror("Usuarios", str(exc), parent=dlg)
                return
            load_master_page(target.id)
            reload_access(active_profile().id)
            self._show_toast("Usuario atualizado.")

        def new_admin_user() -> None:
            try:
                profile = create_profile("Novo usuario", role="member", make_active=False)
                load_master_page(profile.id)
                reload_access(active_profile().id)
            except Exception as exc:
                messagebox.showerror("Usuarios", str(exc), parent=dlg)

        def remove_admin_user() -> None:
            target = selected_admin_profile()
            if not target:
                return
            if not messagebox.askyesno("Remover usuario", "Remover usuario local? Projetos nao serao apagados.", parent=dlg):
                return
            try:
                remove_profile(target.id)
            except Exception as exc:
                messagebox.showerror("Usuarios", str(exc), parent=dlg)
                return
            refresh_self()
            load_master_page(active_profile().id)
            reload_access(active_profile().id)

        master_list.bind("<<ListboxSelect>>", on_master_select)
        tk.Button(admin_actions, text="Novo usuario", command=new_admin_user, bg=SURF, fg=TXT2,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left", padx=(0, 8))
        tk.Button(admin_actions, text="Salvar usuario", command=save_admin_user, bg=ACCENT, fg="#fff",
                  activebackground="#7055dd", activeforeground="#fff", relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left", padx=(0, 8))
        tk.Button(admin_actions, text="Remover", command=remove_admin_user, bg=SURF, fg=TXT2,
                  activebackground=SURF2, activeforeground=TXT, relief="flat",
                  cursor="hand2", padx=12, pady=6).pack(side="left")

        reload_access(self._profile.id)
        load_profile_page()
        load_master_page(self._profile.id)
        show_tab(tab_var.get())
        try:
            dlg.update_idletasks()
            x = self._root.winfo_rootx() + max(80, self._root.winfo_width() // 2 - dlg.winfo_width() // 2)
            y = self._root.winfo_rooty() + max(40, self._root.winfo_height() // 2 - dlg.winfo_height() // 2)
            dlg.geometry(f"+{x}+{y}")
        except Exception:
            pass

    def _load_projects(self) -> None:
        self._profile = active_profile()
        self._refresh_profile_avatar()
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

        items = [e for e in self._profile_projects() if e.exists()]
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
        profile_projects = self._profile_projects()
        counts: dict[str, int] = {"all": len(profile_projects)}
        for e in profile_projects:
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
            self._render_trash()
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

        profile_projects = self._profile_projects()
        editing_count = sum(1 for e in profile_projects if e.status == "edit")
        first_name = (self._profile.name.strip().split() or ["Editor"])[0]
        sub = f"{greet}, {first_name}"
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

        profile_projects = self._profile_projects()
        editing   = sum(1 for e in profile_projects if e.status == "edit")
        total_dur = sum(e.duration_s for e in profile_projects)
        dur_label = f"{total_dur / 3600:.1f}h" if total_dur >= 3600 else f"{int(total_dur / 60)}min"
        usage = usage_summary()
        latest = usage.get("latest_project") or {}
        edit_hours_label = _hours_label(float(usage.get("total_seconds") or 0.0))
        latest_seconds = float(latest.get("total_seconds") or 0.0) if isinstance(latest, dict) else 0.0
        latest_name = str(latest.get("name") or "sem projeto") if isinstance(latest, dict) else "sem projeto"
        if len(latest_name) > 22:
            latest_name = latest_name[:19] + "..."

        cards = [
            ("Em edição",     str(editing),   f"+{min(editing, 2)} essa semana",   ACCENT),
            ("Tempo cortado", dur_label,       "do material",                        OK),
            ("Horas em edição", edit_hours_label, "tempo total no editor",            WARN),
            ("Último projeto", _hours_label(latest_seconds), latest_name,             TXT3),
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
        profile_projects = self._profile_projects()
        editing_count = sum(1 for e in profile_projects if e.status in ("edit", "review"))
        final_count   = sum(1 for e in profile_projects if e.status == "final")
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

        zoom_frm = tk.Frame(row, bg=BG0)
        zoom_frm.pack(side="right", padx=(0, 12))
        tk.Label(zoom_frm, text="Zoom", bg=BG0, fg=TXT4,
                 font=("Segoe UI", 8)).pack(side="left", padx=(0, 5))
        zoom = tk.Scale(
            zoom_frm,
            from_=0.85,
            to=1.25,
            resolution=0.05,
            orient="horizontal",
            variable=self._project_zoom,
            command=lambda _v: None,
            bg=BG0,
            fg=TXT3,
            troughcolor=SURF3,
            activebackground=ACCENT,
            highlightthickness=0,
            showvalue=False,
            length=92,
            sliderlength=12,
            width=6,
        )
        zoom.bind("<ButtonRelease-1>", lambda _e: self._refresh_content())
        zoom.pack(side="left")

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
            card = ProjectCard(
                grid,
                entry,
                self._open_project,
                self._show_project_context,
                zoom=float(self._project_zoom.get() or 1.0),
            )
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
        card_min = max(180, int(210 * float(self._project_zoom.get() or 1.0)))
        cols = max(1, min(5, w // card_min))
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

        # Mini thumb (real frame when available; gradient fallback)
        thumb_c = tk.Canvas(row, width=56, height=36, highlightthickness=0,
                            bg="#1a1820")
        thumb_c.grid(row=0, column=0, padx=(12, 8), pady=8)
        thumb_photo = _extract_video_frame(entry, 56, 36)
        if thumb_photo is not None:
            thumb_c._photo = thumb_photo
            thumb_c.create_image(0, 0, anchor="nw", image=thumb_photo)
        else:
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
            w.bind("<Button-3>", lambda e, en=entry: self._show_project_context(en, e.x_root, e.y_root))
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

    def _render_trash(self) -> None:
        inner = self._inner
        hero = tk.Frame(inner, bg=BG0)
        hero.pack(fill="x", padx=28, pady=(28, 18))
        tk.Label(hero, text="LIXEIRA", bg=BG0, fg=TXT3,
                 font=("Segoe UI", 9)).pack(anchor="w")
        tk.Label(hero, text="Projetos removidos da lista.",
                 bg=BG0, fg=TXT, font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(4, 0))
        tk.Label(hero, text="Restaure projetos para voltar ao fluxo ou remova definitivamente do indice local.",
                 bg=BG0, fg=TXT2, font=("Segoe UI", 11), wraplength=560).pack(anchor="w", pady=(6, 0))

        projects = sorted(self._trash_projects(), key=lambda e: e.deleted_at, reverse=True)
        if not projects:
            empty = tk.Frame(inner, bg=BG0, highlightthickness=1, highlightbackground=BORD_S)
            empty.pack(fill="x", padx=28, pady=(0, 40))
            tk.Label(empty, text="Lixeira vazia", bg=BG0, fg=TXT2,
                     font=("Segoe UI", 12, "bold")).pack(pady=(30, 4))
            tk.Label(empty, text="Projetos removidos aparecem aqui antes de sair do indice.",
                     bg=BG0, fg=TXT3, font=("Segoe UI", 10)).pack(pady=(0, 30))
            return

        actions = tk.Frame(inner, bg=BG0)
        actions.pack(fill="x", padx=28, pady=(0, 12))
        self._quick_action_btn(actions, "Restaurar todos", self._restore_all_projects)
        self._quick_action_btn(actions, "Esvaziar lixeira", self._empty_trash)
        self._render_list_section(inner, projects)

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
        register_recent_project(path, name=name, category=category, status="draft", owner_user_id=self._profile.id)
        self._on_create(path, name, category, template)

    def _open_project(self, entry: ProjectEntry) -> None:
        if entry.deleted_at:
            if messagebox.askyesno("Projeto na lixeira", "Restaurar este projeto antes de abrir?", parent=self._root):
                self._restore_project_entry(entry)
            else:
                return
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
            status=entry.status, owner_user_id=entry.owner_user_id or self._profile.id,
        )
        self._on_open(entry.path)

    def _show_project_context(self, entry: ProjectEntry, x_root: int, y_root: int) -> None:
        menu = tk.Menu(self._root, tearoff=0, bg=SURF, fg=TXT, activebackground=SURF2,
                       activeforeground=TXT, borderwidth=0)
        if entry.deleted_at:
            menu.add_command(label="Restaurar projeto", command=lambda: self._restore_project_entry(entry))
            menu.add_command(label="Abrir pasta", command=lambda: self._open_project_folder(entry))
            menu.add_command(label="Copiar caminho", command=lambda: self._copy_project_path(entry))
            menu.add_separator()
            menu.add_command(label="Excluir definitivamente do indice", command=lambda: self._delete_project_index_entry(entry))
        else:
            menu.add_command(label="Abrir projeto", command=lambda: self._open_project(entry))
            menu.add_command(label="Abrir pasta", command=lambda: self._open_project_folder(entry))
            menu.add_command(label="Copiar caminho", command=lambda: self._copy_project_path(entry))
            menu.add_separator()

            status_menu = tk.Menu(menu, tearoff=0, bg=SURF, fg=TXT, activebackground=SURF2,
                                  activeforeground=TXT, borderwidth=0)
            for sid, (label, _bg, _fg) in STATUS.items():
                status_menu.add_command(
                    label=label,
                    command=lambda s=sid: self._update_project_entry(entry, status=s),
                )
            menu.add_cascade(label="Definir status", menu=status_menu)

            cat_menu = tk.Menu(menu, tearoff=0, bg=SURF, fg=TXT, activebackground=SURF2,
                               activeforeground=TXT, borderwidth=0)
            for cid, label, _ha, _hb in CATEGORIES:
                if cid == "all":
                    continue
                cat_menu.add_command(
                    label=label,
                    command=lambda c=cid: self._update_project_entry(entry, category=c),
                )
            menu.add_cascade(label="Mover para categoria", menu=cat_menu)
            menu.add_command(label="Renomear na lista", command=lambda: self._rename_project_entry(entry))
            menu.add_separator()
            menu.add_command(label="Mover para lixeira", command=lambda: self._remove_project_entry(entry))
        try:
            menu.tk_popup(x_root, y_root)
        finally:
            menu.grab_release()

    def _open_project_folder(self, entry: ProjectEntry) -> None:
        folder = Path(entry.path).parent
        if not folder.exists():
            messagebox.showwarning("Pasta não encontrada", str(folder), parent=self._root)
            return
        try:
            os.startfile(str(folder))
        except Exception as exc:
            messagebox.showerror("Falha ao abrir pasta", str(exc), parent=self._root)

    def _copy_project_path(self, entry: ProjectEntry) -> None:
        self._root.clipboard_clear()
        self._root.clipboard_append(entry.path)
        self._show_toast("Caminho do projeto copiado.")

    def _rename_project_entry(self, entry: ProjectEntry) -> None:
        name = simpledialog.askstring("Renomear projeto", "Nome do projeto:", initialvalue=entry.name, parent=self._root)
        if not name:
            return
        self._update_project_entry(entry, name=name.strip())

    def _update_project_entry(
        self,
        entry: ProjectEntry,
        name: Optional[str] = None,
        category: Optional[str] = None,
        status: Optional[str] = None,
    ) -> None:
        if name:
            entry.name = name
        if category:
            entry.category = category
        if status:
            entry.status = status
        if not entry.owner_user_id:
            entry.owner_user_id = self._profile.id
        entry.deleted_at = 0.0
        entry.updated_at = time.time()
        self._write_project_metadata(entry)
        _save_recent_projects(self._projects)
        self._refresh_content()

    def _write_project_metadata(self, entry: ProjectEntry) -> None:
        path = Path(entry.path)
        if path.suffix.lower() not in {".ccproj", ".ccp", ".json"} or not path.is_file():
            return
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw["_projectName"] = entry.name
            raw["_category"] = entry.category
            raw["_status"] = entry.status
            raw["_owner_user_id"] = entry.owner_user_id or self._profile.id
            raw["_updated_at"] = entry.updated_at
            path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _remove_project_entry(self, entry: ProjectEntry) -> None:
        entry.deleted_at = time.time()
        entry.updated_at = time.time()
        _save_recent_projects(self._projects)
        self._refresh_content()
        self._show_toast("Projeto movido para a lixeira.")

    def _restore_project_entry(self, entry: ProjectEntry) -> None:
        entry.deleted_at = 0.0
        entry.updated_at = time.time()
        _save_recent_projects(self._projects)
        self._refresh_content()
        self._show_toast("Projeto restaurado.")

    def _delete_project_index_entry(self, entry: ProjectEntry) -> None:
        if not messagebox.askyesno("Excluir do indice", "Remover este projeto definitivamente da lista local? O arquivo nao sera apagado.", parent=self._root):
            return
        self._projects = [e for e in self._projects if e.path != entry.path]
        _save_recent_projects(self._projects)
        self._refresh_content()
        self._show_toast("Projeto removido do indice local.")

    def _restore_all_projects(self) -> None:
        for entry in self._trash_projects():
            entry.deleted_at = 0.0
            entry.updated_at = time.time()
        _save_recent_projects(self._projects)
        self._refresh_content()
        self._show_toast("Lixeira restaurada.")

    def _empty_trash(self) -> None:
        if not self._trash_projects():
            return
        if not messagebox.askyesno("Esvaziar lixeira", "Remover todos os projetos da lixeira do indice local? Arquivos nao serao apagados.", parent=self._root):
            return
        trash_paths = {e.path for e in self._trash_projects()}
        self._projects = [e for e in self._projects if e.path not in trash_paths]
        _save_recent_projects(self._projects)
        self._refresh_content()
        self._show_toast("Lixeira esvaziada.")

    def _open_existing(self) -> None:
        path = filedialog.askopenfilename(
            title="Abrir projeto CortaCerto",
            filetypes=[("Projeto CortaCerto", "*.ccp"),
                       ("Projeto legado", "*.cortacerto.json"),
                       ("JSON", "*.json"), ("Todos", "*.*")],
        )
        if path:
            register_recent_project(path, owner_user_id=self._profile.id)
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
