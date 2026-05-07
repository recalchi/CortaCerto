"""
ContentForge — main UI (CustomTkinter, dark theme).
All worker→UI communication goes through queue.Queue.
Cancel button kills the running ffmpeg process via threading.Event.
Output screen shows a thumbnail carousel (5 variants, click to select).
"""
from __future__ import annotations

import os
import queue
import threading
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

from ..config import ProcessingConfig, Platform, SilenceStyle, PRESETS
from ..core.color_grade import ColorGrade, PRESET_CAPCUT
from ..pipeline import run_pipeline, PipelineResult
from ..ffmpeg_env import encoder_label

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

_PLATFORM_OPTIONS = [
    ("YouTube",     Platform.YOUTUBE),
    ("Reels/IG",    Platform.REELS),
    ("TikTok",      Platform.TIKTOK),
    ("Shorts",      Platform.SHORTS),
]
_SILENCE_OPTIONS = [
    ("Agressivo\n(≥ 600ms)",  SilenceStyle.AGGRESSIVE),
    ("Natural\n(≥ 900ms)",    SilenceStyle.NATURAL),
    ("Leve\n(≥ 1400ms)",      SilenceStyle.LIGHT),
]
_THEMES = ["dark", "fire", "gold", "purple"]


class ContentForgeApp:
    def __init__(self) -> None:
        self.root = ctk.CTk()
        self.root.title("ContentForge")
        self.root.geometry("1040x720")
        self.root.minsize(880, 620)

        # Set app icon
        self._set_icon()

        self.video_path: Optional[str] = None
        self._music_path: Optional[str] = None
        self.result:     Optional[PipelineResult] = None
        self._queue:     queue.Queue = queue.Queue()
        self._cancel_ev: threading.Event = threading.Event()
        self._thumb_imgs: list = []   # keep refs to prevent GC

        self._build_ui()
        self._poll_queue()

    def run(self) -> None:
        self.root.mainloop()

    # ── Icon ───────────────────────────────────────────────────────────────

    def _set_icon(self) -> None:
        root_dir  = Path(__file__).parent.parent.parent
        icon_png  = root_dir / "corta_certo_icon.png"
        icon_ico  = root_dir / "corta_certo_icon.ico"

        # Convert PNG → ICO once (needed for Windows taskbar)
        if icon_png.exists() and not icon_ico.exists():
            try:
                from PIL import Image
                img = Image.open(icon_png)
                img.save(str(icon_ico), format="ICO",
                         sizes=[(16,16),(32,32),(48,48),(64,64),(128,128)])
            except Exception:
                pass

        # iconbitmap is what Windows uses for the taskbar
        if icon_ico.exists():
            try:
                self.root.iconbitmap(str(icon_ico))
            except Exception:
                pass

        # iconphoto as backup (title-bar icon)
        if icon_png.exists():
            try:
                from PIL import Image, ImageTk
                img   = Image.open(icon_png).resize((32, 32))
                photo = ImageTk.PhotoImage(img)
                self.root.wm_iconphoto(True, photo)
                self._icon_ref = photo
            except Exception:
                pass

    # ── Layout ─────────────────────────────────────────────────────────────

    def _build_ui(self) -> None:
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_columnconfigure(1, weight=1)
        self._build_sidebar()
        self._build_content()

    def _build_sidebar(self) -> None:
        sb = ctk.CTkFrame(self.root, width=210, corner_radius=0)
        sb.grid(row=0, column=0, sticky="nsew")
        sb.grid_propagate(False)
        sb.grid_rowconfigure(9, weight=1)

        ctk.CTkLabel(sb, text="ContentForge",
                     font=ctk.CTkFont(size=20, weight="bold")).grid(
            row=0, column=0, padx=18, pady=(22, 2), sticky="w")
        ctk.CTkLabel(sb, text="Video Production", text_color="gray55").grid(
            row=1, column=0, padx=18, pady=(0, 18), sticky="w")

        self._nav: dict[str, ctk.CTkButton] = {}
        for i, (label, key) in enumerate([
            ("  Início",        "home"),
            ("  Configurações", "settings"),
            ("  Cor & Áudio",   "color"),
            ("  Processando",   "processing"),
            ("  Resultado",     "output"),
        ]):
            btn = ctk.CTkButton(
                sb, text=label, anchor="w", width=195, height=36,
                fg_color="transparent", hover_color=("gray70", "gray28"),
                font=ctk.CTkFont(size=14),
                command=lambda k=key: self._show(k),
            )
            btn.grid(row=2 + i, column=0, padx=10, pady=2)
            self._nav[key] = btn

        # GPU + segmentation backend labels at bottom
        sb.grid_rowconfigure(10, weight=0)
        self._gpu_lbl = ctk.CTkLabel(sb, text="GPU: detectando…",
                                      text_color="gray40", font=ctk.CTkFont(size=10))
        self._gpu_lbl.grid(row=9, column=0, padx=12, pady=(0, 2), sticky="sw")
        self._seg_lbl = ctk.CTkLabel(sb, text="Seg: detectando…",
                                      text_color="gray40", font=ctk.CTkFont(size=10))
        self._seg_lbl.grid(row=10, column=0, padx=12, pady=(0, 14), sticky="sw")
        self.root.after(800,  self._detect_seg_label)
        self.root.after(1500, self._detect_gpu_label)

    def _detect_gpu_label(self) -> None:
        def _task():
            lbl = encoder_label()
            self.root.after(0, lambda: self._gpu_lbl.configure(text=f"GPU: {lbl}"))
        threading.Thread(target=_task, daemon=True).start()

    def _detect_seg_label(self) -> None:
        def _task():
            try:
                from ..core.segmentation import get_backend
                backend = get_backend()
                colors = {"rembg": "#44cc88", "mediapipe": "#6699dd", "grabcut": "gray50"}
                color  = colors.get(backend, "gray50")
                self.root.after(0, lambda: self._seg_lbl.configure(
                    text=f"Seg: {backend}", text_color=color))
            except Exception:
                pass
        threading.Thread(target=_task, daemon=True).start()

    def _build_content(self) -> None:
        container = ctk.CTkFrame(self.root, fg_color="transparent")
        container.grid(row=0, column=1, sticky="nsew")
        container.grid_rowconfigure(0, weight=1)
        container.grid_columnconfigure(0, weight=1)

        self._screens: dict[str, ctk.CTkFrame] = {}
        for key, builder in [
            ("home",       self._build_home),
            ("settings",   self._build_settings),
            ("color",      self._build_color),
            ("processing", self._build_processing),
            ("output",     self._build_output),
        ]:
            frame = ctk.CTkFrame(container, fg_color="transparent")
            frame.grid(row=0, column=0, sticky="nsew")
            self._screens[key] = frame
            builder(frame)

        self._show("home")

    def _show(self, key: str) -> None:
        self._screens[key].tkraise()
        active = ("gray22", "#1a4d8c")
        for k, btn in self._nav.items():
            btn.configure(fg_color=active if k == key else "transparent")

    # ── Screen: Home ───────────────────────────────────────────────────────

    def _build_home(self, p: ctk.CTkFrame) -> None:
        p.grid_rowconfigure(5, weight=1)
        p.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(p, text="Selecione um vídeo",
                     font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, padx=30, pady=(26, 8), sticky="w")

        # Drop zone
        drop = ctk.CTkFrame(p, height=130, corner_radius=12,
                            border_width=2, border_color="gray35")
        drop.grid(row=1, column=0, sticky="ew", padx=30, pady=6)
        drop.grid_columnconfigure(0, weight=1)
        drop.grid_propagate(False)
        self._video_label = ctk.CTkLabel(
            drop, text="Nenhum vídeo selecionado  —  clique para escolher",
            text_color="gray50", font=ctk.CTkFont(size=13))
        self._video_label.grid(row=0, column=0, pady=(22, 6))
        ctk.CTkButton(drop, text="Escolher vídeo…", width=160,
                      command=self._pick_video).grid(row=1, column=0, pady=(0, 14))

        # Title / subtitle
        title_row = ctk.CTkFrame(p, fg_color="transparent")
        title_row.grid(row=2, column=0, sticky="ew", padx=30, pady=(8, 4))
        title_row.grid_columnconfigure(1, weight=1)
        title_row.grid_columnconfigure(3, weight=1)
        ctk.CTkLabel(title_row, text="Título thumbnail:").grid(
            row=0, column=0, sticky="w", padx=(0, 8))
        self._title_entry = ctk.CTkEntry(title_row,
                                         placeholder_text="Gerado automaticamente…")
        self._title_entry.grid(row=0, column=1, sticky="ew")
        ctk.CTkLabel(title_row, text="  Subtítulo:").grid(row=0, column=2, padx=(10, 8))
        self._subtitle_entry = ctk.CTkEntry(title_row, placeholder_text="Ex: CRONOLOGIA")
        self._subtitle_entry.grid(row=0, column=3, sticky="ew")

        # Platform
        pf = ctk.CTkFrame(p, fg_color="transparent")
        pf.grid(row=3, column=0, sticky="ew", padx=30, pady=(8, 4))
        ctk.CTkLabel(pf, text="Plataforma:").grid(row=0, column=0, sticky="w", padx=(0, 12))
        self._platform_var = ctk.StringVar(value=Platform.YOUTUBE.value)
        for i, (label, plat) in enumerate(_PLATFORM_OPTIONS):
            ctk.CTkRadioButton(pf, text=label, variable=self._platform_var,
                               value=plat.value).grid(row=0, column=i + 1, padx=10)

        # Music
        music_row = ctk.CTkFrame(p, fg_color="transparent")
        music_row.grid(row=4, column=0, sticky="ew", padx=30, pady=(6, 4))
        music_row.grid_columnconfigure(1, weight=1)
        ctk.CTkLabel(music_row, text="Música de fundo:").grid(
            row=0, column=0, padx=(0, 8))
        self._music_label = ctk.CTkLabel(music_row, text="Nenhuma",
                                          text_color="gray50")
        self._music_label.grid(row=0, column=1, sticky="w")
        ctk.CTkButton(music_row, text="Escolher…", width=100,
                      command=self._pick_music).grid(row=0, column=2, padx=(8, 0))
        ctk.CTkButton(music_row, text="✕", width=28, fg_color="transparent",
                      border_width=1,
                      command=self._clear_music).grid(row=0, column=3, padx=(4, 0))

        # Process button
        self._process_btn = ctk.CTkButton(
            p, text="▶  Processar Vídeo",
            font=ctk.CTkFont(size=16, weight="bold"),
            height=52, corner_radius=10, state="disabled",
            command=self._start)
        self._process_btn.grid(row=5, column=0, padx=30, pady=16, sticky="sew")

    # ── Screen: Settings ───────────────────────────────────────────────────

    def _build_settings(self, p: ctk.CTkFrame) -> None:
        p.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(p, text="Configurações",
                     font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=30, pady=(26, 12), sticky="w")

        # Silence style
        ctk.CTkLabel(p, text="Estilo de corte de silêncio:",
                     font=ctk.CTkFont(size=13)).grid(
            row=1, column=0, columnspan=3, padx=30, pady=(0, 6), sticky="w")
        self._silence_var = ctk.StringVar(value=SilenceStyle.NATURAL.value)
        style_frame = ctk.CTkFrame(p, fg_color="transparent")
        style_frame.grid(row=2, column=0, columnspan=3, padx=30, pady=(0, 14), sticky="w")
        for label, style in _SILENCE_OPTIONS:
            ctk.CTkRadioButton(style_frame, text=label, variable=self._silence_var,
                               value=style.value).pack(side="left", padx=(0, 22))

        # Sliders
        sliders = [
            ("Limiar de silêncio (dBFS):", "silence_db", -70, -10, -40, 1),
            ("Padding de áudio (ms):",      "padding",   0,  500, 150, 10),
        ]
        self._sliders: dict[str, ctk.CTkSlider] = {}
        self._slider_lbl: dict[str, ctk.CTkLabel] = {}
        for row, (label, key, lo, hi, default, step) in enumerate(sliders, start=3):
            ctk.CTkLabel(p, text=label, font=ctk.CTkFont(size=13)).grid(
                row=row, column=0, padx=30, pady=8, sticky="w")
            val_lbl = ctk.CTkLabel(p, text=str(default), width=50)
            val_lbl.grid(row=row, column=2, padx=(4, 30))
            sl = ctk.CTkSlider(
                p, from_=lo, to=hi, number_of_steps=int((hi - lo) / step),
                command=lambda v, k=key, lbl=val_lbl: self._on_slider(v, k, lbl))
            sl.set(default)
            sl.grid(row=row, column=1, padx=8, pady=8, sticky="ew")
            self._sliders[key] = sl
            self._slider_lbl[key] = val_lbl

        self._rm_silence_var  = ctk.BooleanVar(value=True)
        self._gen_thumb_var   = ctk.BooleanVar(value=True)
        self._gen_vert_var    = ctk.BooleanVar(value=False)
        self._zoom_var        = ctk.BooleanVar(value=True)
        self._transitions_var = ctk.BooleanVar(value=True)
        checks = [
            ("Remover silêncios",                          self._rm_silence_var),
            ("Gerar thumbnails (5 variações)",             self._gen_thumb_var),
            ("Gerar versão vertical 9:16",                 self._gen_vert_var),
            ("Efeitos de zoom seletivos em segmentos",     self._zoom_var),
            ("Transições de abertura / fechamento / meio", self._transitions_var),
        ]
        for row, (text, var) in enumerate(checks, start=6):
            ctk.CTkCheckBox(p, text=text, variable=var,
                            font=ctk.CTkFont(size=13)).grid(
                row=row, column=0, columnspan=3, padx=30, pady=4, sticky="w")

    def _on_slider(self, v: float, key: str, lbl: ctk.CTkLabel) -> None:
        lbl.configure(text=str(int(v)))

    # ── Screen: Color & Audio ──────────────────────────────────────────────

    # Slider accent colors: [bg_color (inactive), progress_color (filled)]
    _SLIDER_COLORS: dict[str, tuple[str, str]] = {
        "temp":        ("#2244aa", "#e08020"),   # blue → orange (temperature)
        "hue":         ("#aa22aa", "#22aa55"),   # purple → green (hue shift)
        "saturation":  ("#555555", "#dd2222"),   # gray → red (saturation)
        "contrast":    ("#222222", "#eeeeee"),   # dark → light
        "brightness":  ("#333333", "#ffdd44"),   # dark → yellow
        "shadows":     ("#111122", "#6688cc"),   # deep → mid blue
        "whites":      ("#888888", "#ffffff"),   # gray → white
        "blacks":      ("#000000", "#444444"),   # black → dark gray
        "sharpen":     ("#224422", "#44cc44"),   # dark green → bright green
        "bokeh":       ("#223366", "#6699dd"),   # dark blue gradient
    }

    def _build_color(self, p: ctk.CTkFrame) -> None:
        # Two-column layout: left = sliders, right = preview
        p.grid_rowconfigure(0, weight=1)
        p.grid_columnconfigure(0, weight=1)
        p.grid_columnconfigure(1, weight=0)

        # Left: scrollable sliders
        scroll = ctk.CTkScrollableFrame(p, fg_color="transparent", width=560)
        scroll.grid(row=0, column=0, sticky="nsew", padx=(0, 0), pady=0)
        scroll.grid_columnconfigure(1, weight=1)

        # Right: preview panel
        preview_panel = ctk.CTkFrame(p, width=300, fg_color=("gray88", "gray16"))
        preview_panel.grid(row=0, column=1, sticky="nsew", padx=(4, 0), pady=0)
        preview_panel.grid_propagate(False)
        preview_panel.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(preview_panel, text="Preview",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, pady=(12, 6))
        self._preview_img_lbl = ctk.CTkLabel(
            preview_panel, text="(selecione um vídeo\npara ver o preview)",
            text_color="gray50", height=170, width=280,
            fg_color=("gray80", "gray22"), corner_radius=6)
        self._preview_img_lbl.grid(row=1, column=0, padx=10, pady=(0, 8))

        ctk.CTkButton(preview_panel, text="Atualizar preview",
                      command=self._update_color_preview).grid(
            row=2, column=0, padx=10, pady=(0, 6), sticky="ew")

        # Preset save/load
        preset_row = ctk.CTkFrame(preview_panel, fg_color="transparent")
        preset_row.grid(row=3, column=0, padx=10, pady=(0, 8), sticky="ew")
        preset_row.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(preset_row, text="Preset:", width=55).grid(row=0, column=0)
        self._preset_name = ctk.CTkEntry(preset_row, placeholder_text="nome…")
        self._preset_name.grid(row=0, column=1, sticky="ew", padx=(4, 4))
        ctk.CTkButton(preset_row, text="Salvar", width=60,
                      command=self._save_preset).grid(row=0, column=2)

        self._preset_menu = ctk.CTkOptionMenu(
            preview_panel, values=["CapCut ref", "Cinematico", "Neutro"],
            command=self._load_preset)
        self._preset_menu.grid(row=4, column=0, padx=10, pady=(0, 10), sticky="ew")

        # ── Sliders (left panel) ─────────────────────────────────────────────
        ctk.CTkLabel(scroll, text="Cor & Efeitos",
                     font=ctk.CTkFont(size=22, weight="bold")).grid(
            row=0, column=0, columnspan=3, padx=24, pady=(18, 4), sticky="w")

        self._color_enabled = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text="Aplicar color grade",
                        variable=self._color_enabled,
                        command=self._update_color_preview).grid(
            row=1, column=0, columnspan=3, padx=24, pady=(0, 6), sticky="w")

        # color defs: (label, key, lo, hi, default)
        color_defs = [
            ("Temperatura",  "temp",       -100, 100, -10),
            ("Matiz (Hue)",  "hue",        -180, 180, -15),
            ("Saturacao",    "saturation", -100, 100,  10),
            ("Contraste",    "contrast",   -100, 100,  10),
            ("Brilho",       "brightness", -100, 100,  10),
            ("Sombras",      "shadows",    -100, 100,  -5),
            ("Brancos",      "whites",     -100, 100,  10),
            ("Pretos",       "blacks",     -100, 100,  -5),
            ("Nitidez",      "sharpen",      0,  100,   5),
        ]
        self._c_sliders: dict[str, ctk.CTkSlider] = {}
        self._c_labels:  dict[str, ctk.CTkLabel]  = {}

        def _make_slider_cb(lbl: ctk.CTkLabel) -> None:
            def _cb(v: float) -> None:
                lbl.configure(text=str(int(v)))
                self._schedule_preview_update()
            return _cb

        for row, (label, key, lo, hi, default) in enumerate(color_defs, start=2):
            fg_c, prog_c = self._SLIDER_COLORS.get(key, ("#555", "#aaa"))
            ctk.CTkLabel(scroll, text=label + ":", font=ctk.CTkFont(size=12),
                         width=110, anchor="w").grid(
                row=row, column=0, padx=(24, 4), pady=4, sticky="w")
            val_lbl = ctk.CTkLabel(scroll, text=str(default), width=40,
                                   font=ctk.CTkFont(size=12))
            val_lbl.grid(row=row, column=2, padx=(4, 24))
            sl = ctk.CTkSlider(
                scroll, from_=lo, to=hi, number_of_steps=hi - lo,
                button_color=prog_c, button_hover_color=prog_c,
                progress_color=prog_c, fg_color=fg_c,
                command=_make_slider_cb(val_lbl))
            sl.set(default)
            sl.grid(row=row, column=1, padx=4, pady=4, sticky="ew")
            self._c_sliders[key] = sl
            self._c_labels[key]  = val_lbl

        sep_row = len(color_defs) + 2

        # ── Bokeh (background blur) ──────────────────────────────────────────
        ctk.CTkLabel(scroll, text="Desfoque de fundo  (Bokeh)",
                     text_color="#6699dd", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=sep_row, column=0, columnspan=3, padx=24, pady=(14, 4), sticky="w")

        bok_lbl = ctk.CTkLabel(scroll, text="0%", width=40)
        bok_lbl.grid(row=sep_row + 1, column=2, padx=(4, 24))
        fg_c, prog_c = self._SLIDER_COLORS["bokeh"]
        def _bokeh_cb(v: float) -> None:
            bok_lbl.configure(text=f"{int(v)}%")
            self._schedule_preview_update()
        self._bokeh_slider = ctk.CTkSlider(
            scroll, from_=0, to=100, number_of_steps=100,
            button_color=prog_c, button_hover_color=prog_c,
            progress_color=prog_c, fg_color=fg_c,
            command=_bokeh_cb)
        self._bokeh_slider.set(0)
        self._bokeh_slider.grid(row=sep_row + 1, column=1, padx=4, pady=4, sticky="ew")
        ctk.CTkLabel(scroll, text="Intensidade:", font=ctk.CTkFont(size=12),
                     width=110, anchor="w").grid(
            row=sep_row + 1, column=0, padx=(24, 4), pady=4, sticky="w")

        # ── Audio ────────────────────────────────────────────────────────────
        ctk.CTkLabel(scroll, text="Audio",
                     text_color="#44cc88", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=sep_row + 3, column=0, columnspan=3, padx=24, pady=(14, 4), sticky="w")

        self._noise_var = ctk.BooleanVar(value=True)
        ctk.CTkCheckBox(scroll, text="Reducao de ruido  (afftdn + loudnorm)",
                        variable=self._noise_var).grid(
            row=sep_row + 4, column=0, columnspan=3, padx=24, pady=4, sticky="w")
        ctk.CTkLabel(scroll,
                     text="Volume normalizado automaticamente para -16 LUFS (sem clipping)",
                     text_color="gray50", font=ctk.CTkFont(size=10)).grid(
            row=sep_row + 5, column=0, columnspan=3, padx=24, sticky="w")

        # ── Thumbnail theme ───────────────────────────────────────────────────
        ctk.CTkLabel(scroll, text="Tema da Thumbnail",
                     text_color="#dd9933", font=ctk.CTkFont(size=12, weight="bold")).grid(
            row=sep_row + 7, column=0, columnspan=3, padx=24, pady=(14, 4), sticky="w")
        self._theme_var = ctk.StringVar(value="dark")
        theme_f = ctk.CTkFrame(scroll, fg_color="transparent")
        theme_f.grid(row=sep_row + 8, column=0, columnspan=3, padx=24, sticky="w")
        theme_colors = {"dark": "#1a4d8c", "fire": "#cc3300", "gold": "#cc9900", "purple": "#663399"}
        for t in _THEMES:
            ctk.CTkRadioButton(theme_f, text=t.capitalize(),
                               variable=self._theme_var, value=t,
                               fg_color=theme_colors[t],
                               hover_color=theme_colors[t]).pack(side="left", padx=8)

    def _schedule_preview_update(self) -> None:
        """Debounce preview: update 350ms after last slider change."""
        if hasattr(self, "_preview_timer"):
            self.root.after_cancel(self._preview_timer)
        self._preview_timer = self.root.after(350, self._update_color_preview)

    def _update_color_preview(self) -> None:
        """Apply current grade to a frame and show in preview panel."""
        if not self.video_path:
            return
        frame_path = getattr(self, "_preview_frame_path", None)

        def _worker() -> None:
            try:
                from PIL import Image
                from ..core.thumbnail import (
                    _extract_frame, apply_grade_preview,
                    apply_bokeh_pil, detect_person,
                )
                # Extract frame once and cache path
                if not frame_path or not os.path.exists(frame_path):
                    import tempfile
                    tmp = tempfile.mkdtemp(prefix="cf_prev_")
                    frame = _extract_frame(self.video_path, None)
                    fp    = os.path.join(tmp, "prev.jpg")
                    frame.save(fp)
                    self._preview_frame_path = fp
                    # Detect and cache face position
                    fx, fy, fs = detect_person(frame)
                    self._prev_face = (fx, fy, fs)
                else:
                    frame = Image.open(frame_path).convert("RGB")

                # Apply bokeh FIRST with face-aware mask
                bokeh = float(self._bokeh_slider.get()) / 100.0
                fx, fy, fs = getattr(self, "_prev_face", (0.50, 0.38, 0.22))
                if bokeh >= 0.05:
                    frame = apply_bokeh_pil(frame, bokeh, fx, fy, fs)

                # Then apply color grade
                grade  = self._build_color_grade()
                result = apply_grade_preview(frame, grade, bokeh_intensity=0.0)

                result.thumbnail((280, 158))
                ctk_img = ctk.CTkImage(light_image=result, dark_image=result, size=(280, 158))
                self._prev_ctk_img = ctk_img
                self.root.after(0, lambda: self._preview_img_lbl.configure(
                    image=ctk_img, text=""))
            except Exception:
                pass

        threading.Thread(target=_worker, daemon=True).start()

    def _build_color_grade(self) -> "ColorGrade":
        return ColorGrade(
            enabled     = self._color_enabled.get(),
            temperature = float(self._c_sliders["temp"].get()),
            hue         = float(self._c_sliders["hue"].get()),
            saturation  = float(self._c_sliders["saturation"].get()),
            contrast    = float(self._c_sliders["contrast"].get()),
            brightness  = float(self._c_sliders["brightness"].get()),
            shadows     = float(self._c_sliders["shadows"].get()),
            whites      = float(self._c_sliders["whites"].get()),
            blacks      = float(self._c_sliders["blacks"].get()),
            sharpen     = float(self._c_sliders["sharpen"].get()),
        )

    def _save_preset(self) -> None:
        import json
        name = self._preset_name.get().strip()
        if not name:
            messagebox.showwarning("Aviso", "Digite um nome para o preset.")
            return
        grade = self._build_color_grade()
        preset_data = {k: getattr(grade, k) for k in vars(grade) if k != "enabled"}
        presets_path = Path(__file__).parent.parent.parent / "presets.json"
        try:
            existing = json.loads(presets_path.read_text()) if presets_path.exists() else {}
        except Exception:
            existing = {}
        existing[name] = preset_data
        presets_path.write_text(json.dumps(existing, indent=2))
        # Update dropdown
        opts = ["CapCut ref", "Cinematico", "Neutro"] + [k for k in existing if k not in ("CapCut ref", "Cinematico", "Neutro")]
        self._preset_menu.configure(values=opts)
        messagebox.showinfo("Salvo", f"Preset '{name}' salvo.")

    def _load_preset(self, name: str) -> None:
        import json
        # Built-in presets
        builtins = {
            "CapCut ref":  dict(temperature=-10, hue=-15, saturation=10, contrast=10,
                                brightness=10, shadows=-5, whites=10, blacks=-5, sharpen=5),
            "Cinematico":  dict(temperature=-8,  hue=-8,  saturation=-5, contrast=15,
                                brightness=-5, shadows=-12, whites=5, blacks=-10, sharpen=3),
            "Neutro":      dict(temperature=0, hue=0, saturation=0, contrast=0,
                                brightness=0, shadows=0, whites=0, blacks=0, sharpen=0),
        }
        data = builtins.get(name)
        if not data:
            presets_path = Path(__file__).parent.parent.parent / "presets.json"
            try:
                data = json.loads(presets_path.read_text()).get(name)
            except Exception:
                data = None
        if not data:
            return
        for key, val in data.items():
            if key in self._c_sliders:
                self._c_sliders[key].set(float(val))
                self._c_labels[key].configure(text=str(int(float(val))))
        self._schedule_preview_update()

    # ── Screen: Processing ─────────────────────────────────────────────────

    def _build_processing(self, p: ctk.CTkFrame) -> None:
        p.grid_rowconfigure(3, weight=1)
        p.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(p, text="Processando…",
                     font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, padx=30, pady=(26, 10), sticky="w")

        self._progress_bar = ctk.CTkProgressBar(p, height=20, corner_radius=6)
        self._progress_bar.set(0)
        self._progress_bar.grid(row=1, column=0, padx=30, pady=(0, 4), sticky="ew")

        self._status_lbl = ctk.CTkLabel(p, text="Aguardando…",
                                         text_color="gray50", font=ctk.CTkFont(size=12))
        self._status_lbl.grid(row=2, column=0, padx=30, pady=(0, 6), sticky="w")

        self._log = ctk.CTkTextbox(
            p, state="disabled",
            font=ctk.CTkFont(family="Courier New", size=12),
            corner_radius=8)
        self._log.grid(row=3, column=0, padx=30, pady=(0, 8), sticky="nsew")

        self._cancel_btn = ctk.CTkButton(
            p, text="■  Cancelar", width=130,
            fg_color="#8b2020", hover_color="#5e1515",
            command=self._cancel)
        self._cancel_btn.grid(row=4, column=0, pady=(4, 20))

    # ── Screen: Output ─────────────────────────────────────────────────────

    def _build_output(self, p: ctk.CTkFrame) -> None:
        p.grid_rowconfigure(2, weight=1)
        p.grid_columnconfigure(0, weight=1)

        # Header
        ctk.CTkLabel(p, text="Resultado",
                     font=ctk.CTkFont(size=24, weight="bold")).grid(
            row=0, column=0, padx=30, pady=(22, 8), sticky="w")

        # Thumbnail carousel (5 slots)
        carousel = ctk.CTkFrame(p, fg_color=("gray88", "gray16"),
                                corner_radius=8, height=120)
        carousel.grid(row=1, column=0, padx=30, pady=(0, 10), sticky="ew")
        carousel.grid_propagate(False)
        carousel.grid_columnconfigure(list(range(5)), weight=1)

        self._thumb_btns: list[ctk.CTkButton] = []
        self._thumb_lbl_header = ctk.CTkLabel(
            carousel, text="Thumbnails — clique para selecionar como principal",
            text_color="gray50", font=ctk.CTkFont(size=11))
        self._thumb_lbl_header.grid(row=0, column=0, columnspan=5, pady=(8, 4))

        for i in range(5):
            btn = ctk.CTkButton(
                carousel, text=f"#{i+1}", width=150, height=80,
                fg_color=("gray80", "gray22"),
                hover_color=("gray70", "gray30"),
                corner_radius=6, state="disabled",
                command=lambda idx=i: self._select_thumb(idx),
            )
            btn.grid(row=1, column=i, padx=4, pady=(0, 8))
            self._thumb_btns.append(btn)

        # Body: stats + actions
        body = ctk.CTkFrame(p, fg_color="transparent")
        body.grid(row=2, column=0, padx=30, pady=(0, 16), sticky="nsew")
        body.grid_rowconfigure(0, weight=1)
        body.grid_columnconfigure(0, weight=1)

        self._output_box = ctk.CTkTextbox(body, state="disabled",
                                          font=ctk.CTkFont(size=13), corner_radius=8)
        self._output_box.grid(row=0, column=0, sticky="nsew", padx=(0, 10))

        right = ctk.CTkFrame(body, fg_color="transparent", width=210)
        right.grid(row=0, column=1, sticky="ns")
        right.grid_columnconfigure(0, weight=1)
        right.grid_propagate(False)

        ctk.CTkButton(right, text="📂  Abrir pasta de saída",
                      command=self._open_folder).grid(
            row=0, column=0, pady=6, sticky="ew")
        ctk.CTkButton(right, text="🔄  Processar outro vídeo",
                      fg_color="transparent", border_width=1,
                      command=lambda: self._show("home")).grid(
            row=1, column=0, pady=6, sticky="ew")

    # ── Actions ────────────────────────────────────────────────────────────

    def _pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecionar vídeo",
            filetypes=[("Vídeos", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v"),
                       ("Todos",  "*.*")])
        if not path:
            return
        self.video_path = path
        name = Path(path).name
        self._video_label.configure(text=f"[OK]  {name}",
                                    text_color=("gray10", "gray90"))
        self._process_btn.configure(state="normal")
        stem = Path(path).stem.replace("_", " ").replace("-", " ").title()
        if not self._title_entry.get():
            self._title_entry.delete(0, "end")
            self._title_entry.insert(0, stem)

    def _pick_music(self) -> None:
        path = filedialog.askopenfilename(
            title="Música de fundo",
            filetypes=[("Áudio", "*.mp3 *.wav *.aac *.m4a *.ogg"), ("Todos", "*.*")])
        if path:
            self._music_path = path
            self._music_label.configure(text=Path(path).name,
                                        text_color=("gray10", "gray90"))

    def _clear_music(self) -> None:
        self._music_path = None
        self._music_label.configure(text="Nenhuma", text_color="gray50")

    def _build_config(self) -> ProcessingConfig:
        plat_map  = {p.value: p for p in Platform}
        style_map = {s.value: s for s in SilenceStyle}
        return ProcessingConfig(
            silence_threshold_db = float(self._sliders["silence_db"].get()),
            silence_style        = style_map.get(self._silence_var.get(),
                                                  SilenceStyle.NATURAL),
            audio_padding_ms     = int(self._sliders["padding"].get()),
            platform             = plat_map.get(self._platform_var.get(),
                                                  Platform.YOUTUBE),
            remove_silence       = self._rm_silence_var.get(),
            generate_thumbnail   = self._gen_thumb_var.get(),
            generate_vertical    = self._gen_vert_var.get(),
            apply_zoom_effects   = self._zoom_var.get(),
            apply_transitions    = self._transitions_var.get(),
            color_grade          = self._build_color_grade(),
            noise_reduction      = self._noise_var.get(),
            bokeh_intensity      = float(self._bokeh_slider.get()) / 100.0,
            thumbnail_title      = self._title_entry.get().strip(),
            thumbnail_subtitle   = self._subtitle_entry.get().strip(),
            thumbnail_theme      = self._theme_var.get(),
            thumbnail_count      = 5,
            music_path           = self._music_path,
        )

    def _start(self) -> None:
        if not self.video_path:
            messagebox.showwarning("Aviso", "Selecione um vídeo primeiro.")
            return

        self._cancel_ev.clear()
        self._cancel_btn.configure(state="normal", text="■  Cancelar")
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._progress_bar.set(0)
        self._status_lbl.configure(text="Iniciando…")
        self._show("processing")

        config     = self._build_config()
        output_dir = str(Path(self.video_path).parent / "ContentForge_output")

        def worker() -> None:
            res = run_pipeline(
                self.video_path, output_dir, config,
                cancel=self._cancel_ev,
                on_progress=lambda msg, p: self._queue.put((msg, p)),
            )
            self._queue.put(("__DONE__", res))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel(self) -> None:
        self._cancel_ev.set()
        self._cancel_btn.configure(state="disabled", text="Cancelando…")
        self._log_append("Cancelamento solicitado — aguardando processo atual…")

    def _poll_queue(self) -> None:
        try:
            while True:
                msg, val = self._queue.get_nowait()
                if msg == "__DONE__":
                    self._on_done(val)
                else:
                    self._log_append(msg)
                    if isinstance(val, float) and 0.0 <= val <= 1.0:
                        self._progress_bar.set(val)
                        self._status_lbl.configure(text=msg)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _on_done(self, result: PipelineResult) -> None:
        self._cancel_btn.configure(state="disabled", text="■  Cancelar")
        if result.cancelled:
            messagebox.showinfo("Cancelado", "Processamento cancelado pelo usuário.")
            self._show("home")
        elif not result.success:
            messagebox.showerror("Erro", result.error or "Erro desconhecido.")
        else:
            self._progress_bar.set(1.0)
            self._status_lbl.configure(text="Concluído! ✓")
            self._populate_output(result)
            self._show("output")

    def _populate_output(self, result: PipelineResult) -> None:
        self.result = result

        # ── Thumbnail carousel ───────────────────────────────────────────
        self._thumb_imgs.clear()
        self._current_thumb_idx = 0

        for i, btn in enumerate(self._thumb_btns):
            if i < len(result.thumbnails_all):
                path = result.thumbnails_all[i]
                try:
                    from PIL import Image
                    img  = Image.open(path)
                    img.thumbnail((144, 81))
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                           size=(144, 81))
                    self._thumb_imgs.append(ctk_img)
                    btn.configure(image=ctk_img, text="", state="normal",
                                  fg_color=("gray80", "gray22"))
                except Exception:
                    btn.configure(state="disabled", text=f"#{i+1}")
            else:
                btn.configure(state="disabled", text=f"#{i+1}", image=None)

        # Highlight first
        if self._thumb_imgs:
            self._thumb_btns[0].configure(border_width=2,
                                           border_color=("blue", "#3a7ebf"))

        # ── Stats panel ──────────────────────────────────────────────────
        def fmt(s: float) -> str:
            m, sec = divmod(int(s), 60)
            return f"{m:02d}:{sec:02d}"

        try:
            from ..core.segmentation import get_backend as _seg_backend
            seg_backend = _seg_backend()
        except Exception:
            seg_backend = "n/a"

        lines: list[str] = [
            "── ESTATÍSTICAS ──────────────────────────────────",
            f"  Original:          {fmt(result.original_duration_s)}",
            f"  Final:             {fmt(result.final_duration_s)}",
            f"  Removido:          {fmt(result.silence_removed_s)}  ({result.compression_pct:.1f}%)",
            f"  Produção:          {fmt(result.production_time_s)}",
            f"  Segmentação:       {seg_backend}",
        ]
        if result.render_stats:
            rs = result.render_stats
            lines += [
                f"  Encoder:           {rs.encoder_used}",
                f"  Segmentos:         {rs.segments_total}",
                f"  Com zoom:          {rs.segments_zoomed}",
                f"  Com transição:     {rs.segments_transitioned}",
            ]
        lines += [
            "",
            "── ARQUIVOS ───────────────────────────────────────",
        ]
        for attr in ("main_video", "vertical_video"):
            v = getattr(result, attr, None)
            if v:
                lines.append(f"  {Path(v).name}")
        lines.append(f"  {len(result.thumbnails_all)} thumbnails geradas")
        lines.append(f"\n  Pasta: {result.output_dir}")

        self._output_box.configure(state="normal")
        self._output_box.delete("1.0", "end")
        self._output_box.insert("1.0", "\n".join(lines))
        self._output_box.configure(state="disabled")

    def _select_thumb(self, idx: int) -> None:
        """Mark selected thumbnail as main; highlight in carousel."""
        if not self.result or idx >= len(self.result.thumbnails_all):
            return
        # Update main thumbnail reference
        self.result.thumbnail = self.result.thumbnails_all[idx]
        # Reset all borders, highlight selected
        for i, btn in enumerate(self._thumb_btns):
            btn.configure(border_width=2 if i == idx else 0,
                          border_color=("blue", "#3a7ebf"))
        self._current_thumb_idx = idx

    def _open_folder(self) -> None:
        if self.result and os.path.isdir(self.result.output_dir):
            os.startfile(self.result.output_dir)

    def _log_append(self, msg: str) -> None:
        self._log.configure(state="normal")
        self._log.insert("end", f"→ {msg}\n")
        self._log.see("end")
        self._log.configure(state="disabled")
