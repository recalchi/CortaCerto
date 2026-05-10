"""ContentForge desktop video editor UI."""
from __future__ import annotations

import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Optional

import customtkinter as ctk
from tkinter import filedialog, messagebox

from ..config import ProcessingConfig, Platform, SilenceStyle, PRESETS
from ..core.audio_waveform import extract_waveform
from ..core.color_grade import ColorGrade, PRESET_CAPCUT
from ..core.preview_engine import PreviewEngine, PreviewFrame, PreviewSettings
from ..core.timeline_model import TimelineModel, build_timeline_model
from ..pipeline import run_pipeline, PipelineResult
from ..ffmpeg_env import encoder_label

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

# -- Colors --------------------------------------------------------------------
C_BG        = "#1a1a1f"
C_PANEL     = "#22222a"
C_SURFACE   = "#2a2a35"
C_BORDER    = "#3a3a48"
C_ACCENT    = "#3a7ebf"
C_ACCENT2   = "#5599dd"
C_GREEN     = "#44cc88"
C_RED       = "#cc4444"
C_YELLOW    = "#ddaa44"
C_TEXT      = "#e8e8f0"
C_MUTED     = "#666678"

TL_SPEECH   = "#3a7ebf"   # timeline: fala (vai ficar)
TL_SILENCE  = "#2a2a35"   # timeline: silêncio (vai ser cortado)
TL_HEAD     = "#ffcc44"   # playhead
TL_BG       = "#18181e"


class ContentForgeApp:
    def __init__(self) -> None:
        self.root = ctk.CTk()
        self.root.title("ContentForge")
        self.root.geometry("1280x780")
        self.root.minsize(1000, 660)
        self.root.configure(fg_color=C_BG)

        self._set_icon()

        # State
        self.video_path:    Optional[str]            = None
        self._music_path:   Optional[str]            = None
        self.result:        Optional[PipelineResult] = None
        self._queue:        queue.Queue              = queue.Queue()
        self._cancel_ev:    threading.Event          = threading.Event()
        self._thumb_imgs:   list                     = []

        # Video player state
        self._preview_engine = PreviewEngine(self._on_preview_frame_ready)
        self._preview_settings_key: tuple = ()
        self._preview_backend = "preview"
        self._preview_render_ms = 0.0
        self._total_frames  = 0
        self._fps           = 30.0
        self._duration_s    = 0.0
        self._current_frame = 0
        self._playing       = False
        self._play_thread:  Optional[threading.Thread] = None

        # Analysis state (filled after background analysis)
        self._segments:     list[tuple[float,float]] = []
        self._analysis_done = False
        self._timeline_model: Optional[TimelineModel] = None
        self._waveform_zoom = 1.0
        self._export_modal = None
        self._export_stage_var = None
        self._export_msg_var = None
        self._export_stage_progress = None
        self._export_overall_progress = None

        self._build_ui()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    # -- Icon ------------------------------------------------------------------

    def _set_icon(self) -> None:
        root_dir = Path(__file__).parent.parent.parent
        ico = root_dir / "corta_certo_icon.ico"
        png = root_dir / "corta_certo_icon.png"
        if png.exists() and not ico.exists():
            try:
                from PIL import Image as _PIL
                _PIL.open(png).save(str(ico), format="ICO",
                                    sizes=[(16,16),(32,32),(48,48),(64,64)])
            except Exception:
                pass
        if ico.exists():
            try: self.root.iconbitmap(str(ico))
            except Exception: pass

    # -- Build UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._build_toolbar()
        self._build_body()

    # -- Toolbar ---------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = tk.Frame(self.root, bg="#111116", height=48)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(4, weight=1)

        # Logo
        tk.Label(tb, text="ContentForge", bg="#111116", fg=C_ACCENT2,
                 font=("Segoe UI", 13, "bold")).grid(row=0, column=0, padx=(14,18), pady=8)

        # Open
        self._open_btn = self._tb_btn(tb, "Abrir vídeo", self._pick_video,
                                       fg=C_TEXT)
        self._open_btn.grid(row=0, column=1, padx=4, pady=8)

        # Export
        self._export_btn = self._tb_btn(tb, "Exportar", self._start,
                                         fg="#ffffff", bg=C_ACCENT)
        self._export_btn.grid(row=0, column=2, padx=4, pady=8)
        self._export_btn.configure(state="disabled")

        # Cancel
        self._cancel_btn = self._tb_btn(tb, "Cancelar", self._cancel,
                                         fg="#ffffff", bg=C_RED)
        self._cancel_btn.grid(row=0, column=3, padx=(4, 16), pady=8)
        self._cancel_btn.configure(state="disabled")

        # Progress bar (hidden initially)
        self._tb_progress = ctk.CTkProgressBar(tb, height=4, width=180,
                                                progress_color=C_ACCENT)
        self._tb_progress.set(0)
        self._tb_progress.grid(row=0, column=4, padx=8, pady=20, sticky="ew")

        # Status
        self._tb_status = tk.Label(tb, text="Abra um vídeo para começar",
                                    bg="#111116", fg=C_MUTED,
                                    font=("Segoe UI", 10))
        self._tb_status.grid(row=0, column=5, padx=8)

        # GPU / Seg labels (right side)
        self._gpu_lbl = tk.Label(tb, text="Encode: verificando", bg="#111116", fg=C_MUTED,
                                  font=("Segoe UI", 9))
        self._gpu_lbl.grid(row=0, column=6, padx=(0,8))
        self._seg_lbl = tk.Label(tb, text="Seg: grabcut", bg="#111116", fg=C_MUTED,
                                  font=("Segoe UI", 9))
        self._seg_lbl.grid(row=0, column=7, padx=(0,14))

        self.root.after(800,  self._detect_seg_label)
        self.root.after(1500, self._detect_gpu_label)

    def _tb_btn(self, parent, text, cmd, fg=C_TEXT,
                bg=C_SURFACE) -> tk.Button:
        return tk.Button(parent, text=text, command=cmd,
                         bg=bg, fg=fg, activebackground=C_BORDER,
                         activeforeground=fg,
                         relief="flat", padx=12, pady=4,
                         font=("Segoe UI", 10), cursor="hand2",
                         bd=0, highlightthickness=0)

    # -- Body: preview + props -------------------------------------------------

    def _build_body(self) -> None:
        body = tk.Frame(self.root, bg=C_BG)
        body.grid(row=1, column=0, sticky="nsew")
        body.grid_rowconfigure(0, weight=3)
        body.grid_rowconfigure(1, weight=1)
        body.grid_columnconfigure(0, weight=1)
        body.grid_columnconfigure(1, weight=0)

        self._build_preview_area(body)
        self._build_timeline(body)
        self._build_properties(body)

    # -- Preview area ----------------------------------------------------------

    def _build_preview_area(self, parent: tk.Frame) -> None:
        area = tk.Frame(parent, bg=C_BG)
        area.grid(row=0, column=0, sticky="nsew", padx=(8,4), pady=(8,0))
        area.grid_rowconfigure(0, weight=1)
        area.grid_columnconfigure(0, weight=1)

        # Video canvas
        self._preview_canvas = tk.Canvas(area, bg="#0a0a0e",
                                          highlightthickness=1,
                                          highlightbackground=C_BORDER)
        self._preview_canvas.grid(row=0, column=0, sticky="nsew")
        self._preview_canvas.bind("<Configure>", self._on_preview_resize)
        self._preview_canvas.bind("<Button-1>", lambda e: self._toggle_play())

        # "No video" placeholder text
        self._no_video_id = self._preview_canvas.create_text(
            400, 200, text="Abra um vídeo para começar  (selecione um arquivo)",
            fill=C_MUTED, font=("Segoe UI", 14), anchor="center")
        self._preview_photo = None   # keeps ref

        # Transport controls
        ctrl = tk.Frame(area, bg=C_PANEL, height=40)
        ctrl.grid(row=1, column=0, sticky="ew", pady=(2, 0))
        ctrl.grid_propagate(False)
        ctrl.grid_columnconfigure(4, weight=1)

        # Seek bar
        self._seek_var = tk.DoubleVar(value=0)
        self._seek_bar = ctk.CTkSlider(ctrl, from_=0, to=1,
                                        variable=self._seek_var,
                                        command=self._on_seek,
                                        height=14,
                                        button_color=C_ACCENT,
                                        progress_color=C_ACCENT,
                                        fg_color=C_SURFACE)
        self._seek_bar.grid(row=0, column=0, columnspan=7,
                             sticky="ew", padx=8, pady=(4,0))

        btn = lambda text, cmd: tk.Button(ctrl, text=text, command=cmd,
                                           bg=C_PANEL, fg=C_TEXT,
                                           font=("Segoe UI", 11),
                                           relief="flat", padx=8, pady=2,
                                           cursor="hand2", bd=0,
                                           activebackground=C_SURFACE,
                                           activeforeground=C_TEXT)

        btn("Início", self._seek_start).grid(row=1, column=0, padx=(8,2), pady=2)
        self._play_btn = btn("▶", self._toggle_play)
        self._play_btn.grid(row=1, column=1, padx=2, pady=2)
        btn("Fim", self._seek_end).grid(row=1, column=2, padx=2, pady=2)

        self._time_lbl = tk.Label(ctrl, text="00:00 / 00:00",
                                   bg=C_PANEL, fg=C_MUTED,
                                   font=("Courier New", 10))
        self._time_lbl.grid(row=1, column=3, padx=12)

        # Volume (cosmetic for now)
        tk.Label(ctrl, text="Vol", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 10)).grid(row=1, column=4, padx=(0,4), sticky="e")

    # -- Timeline --------------------------------------------------------------

    def _build_timeline(self, parent: tk.Frame) -> None:
        tl_outer = tk.Frame(parent, bg=C_PANEL, bd=0)
        tl_outer.grid(row=1, column=0, sticky="nsew", padx=(8,4), pady=(4,8))
        tl_outer.grid_rowconfigure(1, weight=1)
        tl_outer.grid_columnconfigure(0, weight=1)

        hdr = tk.Frame(tl_outer, bg=C_PANEL)
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(4,0))
        tk.Label(hdr, text="TIMELINE", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9, "bold")).pack(side="left")
        self._tl_info = tk.Label(hdr, text="", bg=C_PANEL, fg=C_MUTED,
                                  font=("Segoe UI", 9))
        self._tl_info.pack(side="left", padx=12)
        self._tl_zoom = ctk.CTkSlider(
            hdr, from_=1.0, to=3.0, number_of_steps=20, width=140,
            fg_color=C_SURFACE, progress_color=C_ACCENT, button_color=C_ACCENT2,
            command=self._on_timeline_zoom,
        )
        self._tl_zoom.set(1.0)
        self._tl_zoom.pack(side="right", padx=(8, 0))

        self._tl_canvas = tk.Canvas(tl_outer, bg=TL_BG, height=120,
                                     highlightthickness=0, cursor="hand2")
        self._tl_canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2,4))
        self._tl_canvas.bind("<Configure>", lambda e: self._redraw_timeline())
        self._tl_canvas.bind("<Button-1>", self._tl_click)

        self._tl_playhead = None
        self._redraw_timeline()

    def _tl_click(self, event: tk.Event) -> None:
        if self._duration_s <= 0:
            return
        w   = self._tl_canvas.winfo_width()
        pct = max(0.0, min(1.0, event.x / w))
        frame = int(pct * self._total_frames)
        self._seek_to(frame)

    # -- Properties panel ------------------------------------------------------

    def _on_timeline_zoom(self, value: float) -> None:
        self._waveform_zoom = float(value)
        self._redraw_timeline()

    def _redraw_timeline(self) -> None:
        c = self._tl_canvas
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        c.create_rectangle(0, 0, w, h, fill="#14141a", outline="")
        if self._duration_s <= 0:
            c.create_text(w // 2, h // 2, text="Nenhum vídeo carregado", fill=C_MUTED, font=("Segoe UI", 10))
            return

        if not self._timeline_model:
            c.create_text(w // 2, h // 2, text="Analisando áudio e gerando waveform...", fill=C_MUTED, font=("Segoe UI", 10))
            return

        label_w = 74
        top = 8
        video_y1, video_y2 = top + 12, top + 40
        audio_y1, audio_y2 = top + 56, h - 18

        c.create_rectangle(0, 0, label_w, h, fill="#101015", outline="")
        c.create_text(label_w // 2, (video_y1 + video_y2) // 2, text="VÍDEO", fill=C_MUTED, font=("Segoe UI", 8, "bold"))
        c.create_text(label_w // 2, (audio_y1 + audio_y2) // 2, text="ÁUDIO", fill=C_MUTED, font=("Segoe UI", 8, "bold"))

        c.create_rectangle(label_w, video_y1, w - 8, video_y2, fill="#1b2130", outline="")
        c.create_rectangle(label_w, audio_y1, w - 8, audio_y2, fill="#171b24", outline="")

        for clip in self._timeline_model.video_track.clips:
            x1 = self._time_to_x(clip.start_s, label_w, w - 8)
            x2 = self._time_to_x(clip.end_s, label_w, w - 8)
            c.create_rectangle(x1, video_y1 + 2, x2, video_y2 - 2, fill=TL_SPEECH, outline="")
            if x2 - x1 > 56:
                c.create_text((x1 + x2) // 2, (video_y1 + video_y2) // 2, text=clip.label, fill="#d6e6ff", font=("Segoe UI", 8))

        for start_s, end_s in self._timeline_model.removed_ranges:
            x1 = self._time_to_x(start_s, label_w, w - 8)
            x2 = self._time_to_x(end_s, label_w, w - 8)
            c.create_rectangle(x1, video_y1 + 6, x2, video_y2 - 6, fill=TL_SILENCE, outline="", stipple="gray50")

        self._draw_waveform_track(c, self._timeline_model.waveform, label_w, w - 8, audio_y1, audio_y2)

        tick_step = max(1, int(self._duration_s / 12))
        for t in range(0, int(self._duration_s) + 1, tick_step):
            x = self._time_to_x(float(t), label_w, w - 8)
            c.create_line(x, 4, x, h - 4, fill="#222734")
            mm, ss = divmod(t, 60)
            c.create_text(x, h - 7, text=f"{mm}:{ss:02d}", fill=C_MUTED, font=("Courier New", 8))

        pos = self._current_frame / max(1, self._total_frames)
        px = int(label_w + pos * (w - label_w - 8))
        self._tl_playhead = c.create_line(px, 2, px, h - 2, fill=TL_HEAD, width=2)

        kept = sum(clip.end_s - clip.start_s for clip in self._timeline_model.video_track.clips)
        self._tl_info.configure(
            text=f"Mantido: {_fmt(kept)}  |  Cortado: {_fmt(self._timeline_model.saved_time_s)}  |  Tracks: 2  |  Preview: {self._preview_backend}"
        )

    def _draw_waveform_track(
        self,
        canvas: tk.Canvas,
        samples: list[float],
        x1: int,
        x2: int,
        y1: int,
        y2: int,
    ) -> None:
        if not samples:
            canvas.create_text((x1 + x2) // 2, (y1 + y2) // 2, text="Waveform indisponível", fill=C_MUTED, font=("Segoe UI", 9))
            return

        width = max(1, x2 - x1)
        half_h = (y2 - y1) / 2
        center_y = y1 + half_h
        visible = max(8, int(len(samples) / max(1.0, self._waveform_zoom)))
        stride = max(1, len(samples) // visible)
        bars = samples[::stride]
        bar_w = max(1, width / max(1, len(bars)))

        for idx, amp in enumerate(bars):
            x = x1 + idx * bar_w
            peak = max(1.0, amp * (half_h - 3))
            canvas.create_line(x, center_y - peak, x, center_y + peak, fill="#7dc0ff")

    def _time_to_x(self, time_s: float, x1: int, x2: int) -> int:
        span = max(1, x2 - x1)
        pct = 0.0 if self._duration_s <= 0 else max(0.0, min(1.0, time_s / self._duration_s))
        return int(x1 + pct * span)

    def _build_properties(self, parent: tk.Frame) -> None:
        props = tk.Frame(parent, bg=C_PANEL, width=300)
        props.grid(row=0, column=1, rowspan=2, sticky="nsew",
                   padx=(0, 8), pady=8)
        props.grid_propagate(False)
        props.grid_rowconfigure(0, weight=1)
        props.grid_columnconfigure(0, weight=1)

        scroll = ctk.CTkScrollableFrame(props, fg_color=C_PANEL,
                                         scrollbar_button_color=C_BORDER,
                                         scrollbar_button_hover_color=C_MUTED)
        scroll.grid(row=0, column=0, sticky="nsew")
        scroll.grid_columnconfigure(0, weight=1)

        s = scroll

        # -- Vídeo info ----------------------------------------------------
        self._section(s, "VÍDEO", 0)
        self._vid_info = self._info_label(s, "Nenhum vídeo selecionado", 1)

        # Título
        self._section(s, "TÍTULO DA THUMBNAIL", 2)
        self._title_entry = self._entry(s, "Título", 3)
        self._subtitle_entry = self._entry(s, "Subtítulo (ex: CRONOLOGIA)", 4)

        # Plataforma
        self._section(s, "PLATAFORMA", 5)
        pf = tk.Frame(s, bg=C_PANEL)
        pf.grid(row=6, column=0, sticky="ew", padx=10, pady=(0,6))
        self._platform_var = tk.StringVar(value=Platform.YOUTUBE.value)
        plat_opts = [("YouTube", Platform.YOUTUBE), ("Reels/IG", Platform.REELS),
                     ("TikTok",  Platform.TIKTOK),  ("Shorts",  Platform.SHORTS)]
        for i, (lbl, plat) in enumerate(plat_opts):
            tk.Radiobutton(pf, text=lbl, variable=self._platform_var,
                           value=plat.value, bg=C_PANEL, fg=C_TEXT,
                           selectcolor=C_SURFACE, activebackground=C_PANEL,
                           activeforeground=C_TEXT, font=("Segoe UI", 10),
                           relief="flat").grid(row=i//2, column=i%2, sticky="w", padx=4)

        # -- Corte de Silêncio ---------------------------------------------
        self._section(s, "CORTE DE SILÊNCIO", 7)
        self._rm_silence_var = tk.BooleanVar(value=True)
        self._check(s, "Ativar corte de silêncios", self._rm_silence_var, 8)

        sf = tk.Frame(s, bg=C_PANEL)
        sf.grid(row=9, column=0, sticky="ew", padx=10, pady=(0,4))
        self._silence_var = tk.StringVar(value=SilenceStyle.NATURAL.value)
        for i, (lbl, style) in enumerate([
            ("Agressivo", SilenceStyle.AGGRESSIVE),
            ("Natural",   SilenceStyle.NATURAL),
            ("Leve",      SilenceStyle.LIGHT),
        ]):
            tk.Radiobutton(sf, text=lbl, variable=self._silence_var,
                           value=style.value, bg=C_PANEL, fg=C_TEXT,
                           selectcolor=C_SURFACE, activebackground=C_PANEL,
                           activeforeground=C_TEXT, font=("Segoe UI", 10),
                           relief="flat").pack(side="left", padx=(0,8))

        self._sliders: dict[str, ctk.CTkSlider] = {}
        self._slider_lbl: dict[str, ctk.CTkLabel] = {}
        self._prop_slider(s, "Limiar de silêncio (dBFS)", "silence_db",
                          -70, -10, -40, 1, 10)
        self._prop_slider(s, "Padding de áudio (ms)", "padding",
                          0, 500, 150, 10, 11)

        # -- Color Grade ---------------------------------------------------
        self._section(s, "COLOR GRADE", 12)
        self._color_enabled = tk.BooleanVar(value=True)
        cf = tk.Frame(s, bg=C_PANEL)
        cf.grid(row=13, column=0, sticky="ew", padx=10, pady=(0,4))
        cf.grid_columnconfigure(1, weight=1)
        self._check_frame(cf, "Aplicar grade", self._color_enabled, 0,
                          command=self._schedule_preview)
        # Preset dropdown
        self._preset_var = tk.StringVar(value="CapCut ref")
        ctk.CTkOptionMenu(cf, values=["CapCut ref","Cinematico","Neutro"],
                          variable=self._preset_var,
                          command=self._load_preset,
                          fg_color=C_SURFACE, button_color=C_ACCENT,
                          text_color=C_TEXT, width=120,
                          font=ctk.CTkFont(size=11)).grid(
            row=0, column=1, padx=(8,0), sticky="e")

        self._c_sliders: dict[str, ctk.CTkSlider] = {}
        self._c_labels:  dict[str, ctk.CTkLabel]  = {}
        color_defs = [
            ("Temperatura",  "temp",       -100, 100, -10, "#3366cc","#dd8833"),
            ("Saturação",    "saturation", -100, 100,  10, "#444444","#dd3333"),
            ("Contraste",    "contrast",   -100, 100,  10, "#222222","#eeeeee"),
            ("Brilho",       "brightness", -100, 100,  10, "#333333","#ffdd44"),
            ("Sombras",      "shadows",    -100, 100,  -5, "#111122","#6688cc"),
            ("Nitidez",      "sharpen",      0,  100,   5, "#224422","#44cc44"),
        ]
        for row_off, (label, key, lo, hi, default, fc, pc) in enumerate(color_defs):
            self._color_slider(s, label, key, lo, hi, default, fc, pc,
                               row=14 + row_off)

        # -- Bokeh ---------------------------------------------------------
        self._section(s, "BOKEH  (desfoque de fundo)", 21)
        self._bokeh_slider = self._prop_slider(
            s, "Intensidade", "bokeh", 0, 100, 0, 1, 22,
            suffix="%", color="#223366", prog="#6699dd")

        # -- Audio ---------------------------------------------------------
        self._section(s, "ÁUDIO", 23)
        self._noise_var = tk.BooleanVar(value=True)
        self._check(s, "Redução de ruído + loudnorm EBU R128", self._noise_var, 24)

        mf = tk.Frame(s, bg=C_PANEL)
        mf.grid(row=25, column=0, sticky="ew", padx=10, pady=(0,6))
        mf.grid_columnconfigure(1, weight=1)
        tk.Label(mf, text="Música:", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 10)).grid(row=0, column=0, sticky="w")
        self._music_label = tk.Label(mf, text="Nenhuma", bg=C_PANEL,
                                      fg=C_MUTED, font=("Segoe UI", 9))
        self._music_label.grid(row=0, column=1, sticky="w", padx=6)
        tk.Button(mf, text="...", command=self._pick_music, bg=C_SURFACE,
                  fg=C_TEXT, relief="flat", padx=6, font=("Segoe UI", 9),
                  cursor="hand2", bd=0).grid(row=0, column=2, padx=2)
        tk.Button(mf, text="X", command=self._clear_music, bg=C_SURFACE,
                  fg=C_MUTED, relief="flat", padx=4, font=("Segoe UI", 9),
                  cursor="hand2", bd=0).grid(row=0, column=3)

        # -- Thumbnails ----------------------------------------------------
        self._section(s, "THUMBNAILS", 26)
        self._gen_thumb_var  = tk.BooleanVar(value=True)
        self._gen_vert_var   = tk.BooleanVar(value=False)
        self._check(s, "Gerar 5 thumbnails profissionais", self._gen_thumb_var, 27)
        self._check(s, "Versão vertical 9:16", self._gen_vert_var, 28)

        # -- Preview update btn --------------------------------------------
        ctk.CTkButton(s, text="Atualizar preview",
                      height=32, corner_radius=6,
                      fg_color=C_SURFACE, hover_color=C_BORDER,
                      font=ctk.CTkFont(size=12),
                      command=self._update_color_preview).grid(
            row=29, column=0, padx=10, pady=(8,4), sticky="ew")

    # -- Widget helpers --------------------------------------------------------

    def _section(self, parent, text: str, row: int) -> None:
        f = tk.Frame(parent, bg=C_BORDER, height=1)
        f.grid(row=row, column=0, sticky="ew", padx=10, pady=(10,0))
        tk.Label(parent, text=text, bg=C_PANEL, fg=C_ACCENT2,
                 font=("Segoe UI", 9, "bold")).grid(
            row=row, column=0, sticky="w", padx=12, pady=(8,2))

    def _info_label(self, parent, text: str, row: int) -> tk.Label:
        lbl = tk.Label(parent, text=text, bg=C_PANEL, fg=C_MUTED,
                       font=("Segoe UI", 9), anchor="w", wraplength=260,
                       justify="left")
        lbl.grid(row=row, column=0, sticky="ew", padx=12, pady=(0,4))
        return lbl

    def _entry(self, parent, placeholder: str, row: int) -> ctk.CTkEntry:
        e = ctk.CTkEntry(parent, placeholder_text=placeholder,
                          fg_color=C_SURFACE, border_color=C_BORDER,
                          text_color=C_TEXT, placeholder_text_color=C_MUTED,
                          font=ctk.CTkFont(size=11), height=30)
        e.grid(row=row, column=0, sticky="ew", padx=10, pady=2)
        return e

    def _check(self, parent, text: str, var: tk.BooleanVar, row: int,
               command=None) -> None:
        tk.Checkbutton(parent, text=text, variable=var, bg=C_PANEL, fg=C_TEXT,
                       selectcolor=C_SURFACE, activebackground=C_PANEL,
                       activeforeground=C_TEXT, font=("Segoe UI", 10),
                       relief="flat", command=command).grid(
            row=row, column=0, sticky="w", padx=12, pady=2)

    def _check_frame(self, parent, text: str, var: tk.BooleanVar, row: int,
                     command=None) -> None:
        tk.Checkbutton(parent, text=text, variable=var, bg=C_PANEL, fg=C_TEXT,
                       selectcolor=C_SURFACE, activebackground=C_PANEL,
                       activeforeground=C_TEXT, font=("Segoe UI", 10),
                       relief="flat", command=command).grid(
            row=row, column=0, sticky="w")

    def _prop_slider(self, parent, label: str, key: str,
                     lo, hi, default, step, row: int,
                     suffix="", color="#334", prog="#6699dd") -> ctk.CTkSlider:
        f = tk.Frame(parent, bg=C_PANEL)
        f.grid(row=row, column=0, sticky="ew", padx=10, pady=1)
        f.grid_columnconfigure(1, weight=1)
        tk.Label(f, text=label + ":", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9), width=18, anchor="w").grid(
            row=0, column=0, sticky="w")
        val_lbl = tk.Label(f, text=f"{int(default)}{suffix}",
                            bg=C_PANEL, fg=C_TEXT, font=("Courier New", 9),
                            width=6)
        val_lbl.grid(row=0, column=2, padx=(4,0))
        sl = ctk.CTkSlider(f, from_=lo, to=hi,
                            number_of_steps=int((hi-lo)/step),
                            height=14, button_color=prog,
                            progress_color=prog, fg_color=color,
                            command=lambda v, lbl=val_lbl, sfx=suffix:
                                lbl.configure(text=f"{int(v)}{sfx}"))
        sl.set(default)
        sl.grid(row=0, column=1, padx=4, sticky="ew")
        self._sliders[key] = sl
        return sl

    def _color_slider(self, parent, label, key, lo, hi, default,
                       fc, pc, row) -> None:
        f = tk.Frame(parent, bg=C_PANEL)
        f.grid(row=row, column=0, sticky="ew", padx=10, pady=1)
        f.grid_columnconfigure(1, weight=1)
        tk.Label(f, text=label + ":", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9), width=12, anchor="w").grid(
            row=0, column=0, sticky="w")
        val_lbl = tk.Label(f, text=str(int(default)),
                            bg=C_PANEL, fg=C_TEXT, font=("Courier New", 9),
                            width=4)
        val_lbl.grid(row=0, column=2, padx=(4,0))

        def _cb(v):
            val_lbl.configure(text=str(int(v)))
            self._schedule_preview()

        sl = ctk.CTkSlider(f, from_=lo, to=hi,
                            number_of_steps=hi - lo,
                            height=14, button_color=pc,
                            progress_color=pc, fg_color=fc,
                            command=_cb)
        sl.set(default)
        sl.grid(row=0, column=1, padx=4, sticky="ew")
        self._c_sliders[key] = sl
        self._c_labels[key]  = val_lbl

    # -- Video player ----------------------------------------------------------

    def _pick_video(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecionar vídeo",
            filetypes=[("Vídeos", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v"),
                       ("Todos",  "*.*")])
        if not path:
            return
        self._load_video(path)

    def _load_video(self, path: str) -> None:
        self._playing = False
        self._play_btn.configure(text="▶")
        try:
            self._preview_engine.open(path)
        except Exception as exc:
            self.video_path = None
            self._export_btn.configure(state="disabled")
            self._tb_status.configure(text=f"Erro ao abrir vídeo: {exc}")
            messagebox.showerror("Erro ao abrir vídeo", str(exc))
            return

        self.video_path    = path
        self._segments     = []
        self._analysis_done= False
        self._timeline_model = None
        self._total_frames = self._preview_engine.total_frames
        self._fps          = self._preview_engine.fps
        self._duration_s   = self._preview_engine.duration_s
        self._current_frame= 0
        print(f"[PREVIEW] Video loaded: {path}")

        name = Path(path).name
        size_mb = os.path.getsize(path) / 1_000_000
        self._vid_info.configure(
            text=f"{name}\n{_fmt(self._duration_s)}  |  {size_mb:.1f} MB  |  {self._fps:.1f} fps",
            fg=C_TEXT)

        # Auto-fill title
        stem = Path(path).stem.replace("_"," ").replace("-"," ").title()
        self._title_entry.delete(0, "end")
        self._title_entry.insert(0, stem)

        # Update UI
        self._export_btn.configure(state="normal")
        self._seek_bar.configure(to=max(1, self._total_frames - 1))
        self._seek_bar.set(0)
        self.root.title(f"ContentForge - {name}")
        self._tb_status.configure(text="Gerando preview e timeline...")

        # Show first frame
        self._draw_frame_at(0)
        self._redraw_timeline()
        self._update_time_label()

        # Background: analyze audio for timeline
        threading.Thread(target=self._bg_analyze, daemon=True).start()

    def _bg_analyze(self) -> None:
        """Analyze audio silences in background and update timeline."""
        if not self.video_path:
            return
        try:
            from ..core.analyzer import analyze_video
            from ..config import SilenceStyle
            _MS = {SilenceStyle.AGGRESSIVE: 600,
                   SilenceStyle.NATURAL:    900,
                   SilenceStyle.LIGHT:      1400}
            style = SilenceStyle(self._silence_var.get())
            analysis = analyze_video(
                self.video_path,
                silence_threshold_db=float(self._sliders["silence_db"].get()),
                min_silence_ms=_MS[style],
                audio_padding_ms=int(self._sliders["padding"].get()),
                min_segment_s=0.3,
            )
            self._segments = analysis.speech_segments
            self._analysis_done = True
            waveform = extract_waveform(self.video_path, self._duration_s, bins=420)
            self._timeline_model = build_timeline_model(
                self._duration_s,
                analysis.speech_segments,
                waveform=waveform,
            )
            self.root.after(0, self._redraw_timeline)
            self.root.after(0, lambda: self._tb_status.configure(text="Preview pronto. Timeline atualizada."))
        except Exception:
            pass

    def _on_seek(self, val: float) -> None:
        frame = int(float(val))
        self._seek_to(frame)

    def _seek_to(self, frame: int) -> None:
        was_playing = self._playing
        if was_playing:
            self._playing = False
            time.sleep(0.05)
        self._current_frame = max(0, min(frame, self._total_frames - 1))
        self._seek_bar.set(self._current_frame)
        self._draw_frame_at(self._current_frame)
        self._update_time_label()
        self._update_tl_playhead()
        if was_playing:
            self._playing = True
            self._play_thread = threading.Thread(
                target=self._play_loop, daemon=True)
            self._play_thread.start()

    def _seek_start(self) -> None:
        self._playing = False
        self._play_btn.configure(text="▶")
        self._seek_to(0)

    def _seek_end(self) -> None:
        self._playing = False
        self._play_btn.configure(text="▶")
        self._seek_to(self._total_frames - 1)

    def _update_time_label(self) -> None:
        cur = self._current_frame / max(1, self._fps)
        self._time_lbl.configure(
            text=f"{_fmt(cur)} / {_fmt(self._duration_s)}")

    def _update_tl_playhead(self) -> None:
        c   = self._tl_canvas
        w   = c.winfo_width()
        h   = c.winfo_height()
        pos = self._current_frame / max(1, self._total_frames)
        px  = int(pos * w)
        if self._tl_playhead:
            c.coords(self._tl_playhead, px, 2, px, h - 2)
        else:
            self._tl_playhead = c.create_line(
                px, 2, px, h - 2, fill=TL_HEAD, width=2)

    # -- Color grade helpers ---------------------------------------------------

    def _build_color_grade(self) -> "ColorGrade":
        return ColorGrade(
            enabled     = self._color_enabled.get(),
            temperature = float(self._c_sliders["temp"].get()),
            hue         = 0.0,
            saturation  = float(self._c_sliders["saturation"].get()),
            contrast    = float(self._c_sliders["contrast"].get()),
            brightness  = float(self._c_sliders["brightness"].get()),
            shadows     = float(self._c_sliders["shadows"].get()),
            whites      = 0.0,
            blacks      = 0.0,
            sharpen     = float(self._c_sliders["sharpen"].get()),
        )

    def _load_preset(self, name: str) -> None:
        builtins = {
            "CapCut ref": dict(temp=-10, saturation=10, contrast=10,
                               brightness=10, shadows=-5, sharpen=5),
            "Cinematico": dict(temp=-8,  saturation=-5, contrast=15,
                               brightness=-5, shadows=-12, sharpen=3),
            "Neutro":     dict(temp=0,   saturation=0,  contrast=0,
                               brightness=0,  shadows=0,  sharpen=0),
        }
        data = builtins.get(name, {})
        for key, val in data.items():
            if key in self._c_sliders:
                self._c_sliders[key].set(float(val))
                self._c_labels[key].configure(text=str(int(float(val))))
        self._schedule_preview()

    _preview_timer: Optional[str] = None

    def _schedule_preview(self) -> None:
        if self._preview_timer:
            self.root.after_cancel(self._preview_timer)
        self._preview_timer = self.root.after(400, self._update_color_preview)

    def _update_color_preview(self) -> None:
        if not self.video_path:
            return
        self._draw_frame_at(self._current_frame)

    # -- Music -----------------------------------------------------------------

    def _on_preview_resize(self, event=None) -> None:
        if self.video_path:
            self._draw_frame_at(self._current_frame)

    def _draw_frame_at(self, frame_idx: int) -> None:
        if not self.video_path:
            return
        self._current_frame = max(0, min(frame_idx, self._total_frames - 1))
        settings = PreviewSettings(
            color_grade=self._build_color_grade(),
            bokeh_intensity=float(self._sliders["bokeh"].get()) / 100.0,
        )
        self._preview_settings_key = settings.cache_key()
        self._tb_status.configure(text="Atualizando preview...")
        self._preview_engine.request_frame(self._current_frame, settings)

    def _on_preview_frame_ready(self, preview: PreviewFrame) -> None:
        self.root.after(0, self._render_preview_frame, preview)

    def _render_preview_frame(self, preview: PreviewFrame) -> None:
        if preview.frame_index != self._current_frame:
            return
        if preview.settings_key != self._preview_settings_key:
            return

        from PIL import ImageTk

        pil = preview.image
        cw = self._preview_canvas.winfo_width()
        ch = self._preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            cw, ch = 800, 450

        iw, ih = pil.size
        scale = min(cw / iw, ch / ih)
        nw, nh = int(iw * scale), int(ih * scale)
        pil = pil.resize((nw, nh), Image.LANCZOS)

        photo = ImageTk.PhotoImage(pil)
        self._preview_photo = photo
        self._preview_backend = preview.backend
        self._preview_render_ms = preview.render_ms

        c = self._preview_canvas
        c.delete("frame")
        x = (cw - nw) // 2
        y = (ch - nh) // 2
        c.create_image(x, y, image=photo, anchor="nw", tags="frame")
        c.itemconfigure(self._no_video_id, state="hidden")
        self._tb_status.configure(text=f"Preview {preview.backend}  |  {preview.render_ms:.0f} ms")
        print(
            f"[PREVIEW] Frame rendered successfully | "
            f"frame={preview.frame_index} backend={preview.backend} "
            f"render_ms={preview.render_ms:.0f}"
        )

    def _toggle_play(self) -> None:
        if not self.video_path:
            return
        if self._playing:
            self._playing = False
            self._play_btn.configure(text="▶")
        else:
            self._playing = True
            self._play_btn.configure(text="⏸")
            self._play_thread = threading.Thread(target=self._play_loop, daemon=True)
            self._play_thread.start()

    def _play_loop(self) -> None:
        interval = 1.0 / max(1.0, self._fps)
        while self._playing and self.video_path:
            t0 = time.monotonic()
            frame_idx = self._current_frame + 1
            if frame_idx >= self._total_frames:
                self._playing = False
                self.root.after(0, lambda: self._play_btn.configure(text="▶"))
                break

            self._current_frame = frame_idx
            self.root.after(0, self._draw_frame_at, frame_idx)
            self.root.after(0, self._update_time_label)
            self.root.after(0, self._update_tl_playhead)
            elapsed = time.monotonic() - t0
            time.sleep(max(0.001, interval - elapsed))

    def _pick_music(self) -> None:
        path = filedialog.askopenfilename(
            title="Música de fundo",
            filetypes=[("Áudio", "*.mp3 *.wav *.aac *.m4a *.ogg"),
                       ("Todos", "*.*")])
        if path:
            self._music_path = path
            self._music_label.configure(text=Path(path).name, fg=C_TEXT)

    def _clear_music(self) -> None:
        self._music_path = None
        self._music_label.configure(text="Nenhuma", fg=C_MUTED)

    # -- Labels ----------------------------------------------------------------

    def _detect_gpu_label(self) -> None:
        def _task():
            lbl = encoder_label()
            self.root.after(0, lambda: self._gpu_lbl.configure(text=f"Encode: {lbl}"))
        threading.Thread(target=_task, daemon=True).start()

    def _detect_seg_label(self) -> None:
        def _task():
            try:
                from ..core.segmentation import get_backend
                backend = get_backend()
                colors = {"rembg": C_GREEN, "mediapipe": C_ACCENT2,
                          "grabcut": C_MUTED}
                color = colors.get(backend, C_MUTED)
                self.root.after(0, lambda: self._seg_lbl.configure(
                    text=f"Seg: {backend}", fg=color))
            except Exception:
                pass
        threading.Thread(target=_task, daemon=True).start()

    # -- Pipeline --------------------------------------------------------------

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
            apply_zoom_effects   = True,
            apply_transitions    = True,
            color_grade          = self._build_color_grade(),
            noise_reduction      = self._noise_var.get(),
            bokeh_intensity      = float(self._sliders["bokeh"].get()) / 100.0,
            thumbnail_title      = self._title_entry.get().strip(),
            thumbnail_subtitle   = self._subtitle_entry.get().strip(),
            thumbnail_theme      = "dark",
            thumbnail_count      = 5,
            music_path           = self._music_path,
        )

    def _open_export_modal(self) -> None:
        if self._export_modal and self._export_modal.winfo_exists():
            self._export_modal.destroy()

        modal = ctk.CTkToplevel(self.root)
        modal.title("Exportação")
        modal.geometry("520x220")
        modal.resizable(False, False)
        modal.transient(self.root)
        modal.grab_set()

        self._export_modal = modal
        self._export_stage_var = tk.StringVar(value="Preparando exportação...")
        self._export_msg_var = tk.StringVar(value="Organizando pipeline")

        ctk.CTkLabel(modal, text="Exportando projeto", font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(18, 8))
        ctk.CTkLabel(modal, textvariable=self._export_stage_var, text_color=C_TEXT, font=ctk.CTkFont(size=14)).pack()
        ctk.CTkLabel(modal, textvariable=self._export_msg_var, text_color=C_MUTED, font=ctk.CTkFont(size=11)).pack(pady=(4, 12))

        self._export_overall_progress = ctk.CTkProgressBar(modal, height=12, width=420, progress_color=C_ACCENT)
        self._export_overall_progress.set(0)
        self._export_overall_progress.pack(pady=(0, 10))

        self._export_stage_progress = ctk.CTkProgressBar(modal, height=8, width=420, progress_color=C_GREEN)
        self._export_stage_progress.set(0)
        self._export_stage_progress.pack()

    def _update_export_modal(self, message: str, progress: float) -> None:
        if not self._export_modal or not self._export_modal.winfo_exists():
            return

        stage_text = message
        detail_text = message
        if message.startswith("[") and "]" in message:
            stage_text, detail_text = message.split("]", 1)
            stage_text = stage_text + "]"
            detail_text = detail_text.strip()

        if self._export_stage_var is not None:
            self._export_stage_var.set(stage_text or "Exportando")
        if self._export_msg_var is not None:
            self._export_msg_var.set(detail_text or "Processando")
        if self._export_overall_progress is not None and 0.0 <= progress <= 1.0:
            self._export_overall_progress.set(progress)

        stage_progress = progress
        if message.startswith("[") and "/" in message:
            try:
                head = message[1:message.index("]")]
                current, total = head.split("/")
                current_n = max(1, int(current))
                total_n = max(1, int(total))
                stage_progress = min(1.0, (progress * total_n) - (current_n - 1))
            except Exception:
                stage_progress = progress
        if self._export_stage_progress is not None:
            self._export_stage_progress.set(max(0.0, min(1.0, stage_progress)))

    def _close_export_modal(self) -> None:
        if self._export_modal and self._export_modal.winfo_exists():
            self._export_modal.destroy()
        self._export_modal = None

    def _start(self) -> None:
        if not self.video_path:
            messagebox.showwarning("Aviso", "Abra um vídeo primeiro.")
            return

        # Stop playback
        self._playing = False
        self._play_btn.configure(text="▶")

        self._cancel_ev.clear()
        self._export_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal", text="Cancelar")
        self._tb_progress.set(0)
        self._tb_status.configure(text="Iniciando pipeline...")
        self._open_export_modal()

        config     = self._build_config()
        output_dir = str(Path(self.video_path).parent / "ContentForge_output")

        def worker():
            res = run_pipeline(
                self.video_path, output_dir, config,
                cancel=self._cancel_ev,
                on_progress=lambda msg, p: self._queue.put((msg, p)),
            )
            self._queue.put(("__DONE__", res))

        threading.Thread(target=worker, daemon=True).start()

    def _cancel(self) -> None:
        self._cancel_ev.set()
        print("[CANCEL] Export cancel requested")
        self._cancel_btn.configure(state="disabled", text="Cancelando...")
        self._tb_status.configure(text="Cancelando...")
        self._update_export_modal("Cancelando exportação...", 0.0)
        self._queue.put(("[CANCEL] Export cancel requested", 0.0))

    def _poll_queue(self) -> None:
        try:
            while True:
                msg, val = self._queue.get_nowait()
                if msg == "__DONE__":
                    self._on_done(val)
                else:
                    self._tb_status.configure(text=msg[:80])
                    if isinstance(val, float) and 0.0 <= val <= 1.0:
                        self._tb_progress.set(val)
                        self._update_export_modal(msg, val)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _on_done(self, result: PipelineResult) -> None:
        self._cancel_btn.configure(state="disabled", text="Cancelar")
        self._export_btn.configure(state="normal")

        if result.cancelled:
            self._tb_status.configure(text="Cancelado.")
            self._tb_progress.set(0)
            self._close_export_modal()
            return

        if not result.success:
            self._tb_status.configure(text=f"Erro: {result.error}")
            self._close_export_modal()
            messagebox.showerror("Erro no processamento", result.error or "Erro desconhecido")
            return

        self._tb_progress.set(1.0)
        kept   = result.final_duration_s
        cut    = result.silence_removed_s
        ptime  = result.production_time_s
        enc    = result.render_stats.encoder_used if result.render_stats else "?"
        self._tb_status.configure(
            text=f"Concluído em {_fmt(ptime)}  |  "
                 f"Original: {_fmt(result.original_duration_s)}  ->  "
                 f"Final: {_fmt(kept)}  (-{result.compression_pct:.0f}%)  |  "
                 f"Encoder: {enc}")
        self._close_export_modal()

        # Show output thumbnails in a popup carousel
        self._show_output_popup(result)

    def _show_output_popup(self, result: PipelineResult) -> None:
        popup = ctk.CTkToplevel(self.root)
        popup.title("Resultado - ContentForge")
        popup.geometry("900x500")
        popup.grab_set()

        ctk.CTkLabel(popup, text="Exportação concluída!",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(pady=(18,4))

        # Stats
        rs = result.render_stats
        stats_txt = (
            f"Original: {_fmt(result.original_duration_s)}   ->   "
            f"Final: {_fmt(result.final_duration_s)}   "
            f"(-{result.compression_pct:.0f}%)   |   "
            f"Produção: {_fmt(result.production_time_s)}   |   "
            f"Encoder: {rs.encoder_used if rs else '?'}"
        )
        ctk.CTkLabel(popup, text=stats_txt, text_color=C_MUTED,
                     font=ctk.CTkFont(size=11)).pack(pady=(0,10))

        # Thumbnail carousel
        if result.thumbnails_all:
            ctk.CTkLabel(popup, text="Thumbnails geradas - clique para selecionar a principal",
                         text_color=C_MUTED, font=ctk.CTkFont(size=11)).pack()
            carousel = tk.Frame(popup, bg=C_SURFACE)
            carousel.pack(fill="x", padx=20, pady=8)

            self._thumb_imgs.clear()
            self._selected_thumb = tk.IntVar(value=0)

            for i, path in enumerate(result.thumbnails_all[:5]):
                try:
                    from PIL import Image
                    img = Image.open(path)
                    img.thumbnail((160, 90))
                    ctk_img = ctk.CTkImage(light_image=img, dark_image=img,
                                           size=(160, 90))
                    self._thumb_imgs.append(ctk_img)

                    frame = tk.Frame(carousel, bg=C_SURFACE)
                    frame.pack(side="left", padx=4, pady=4)

                    btn = tk.Button(frame, image=ctk_img,
                                    command=lambda idx=i, f=frame: self._select_thumb_popup(idx, f, carousel),
                                    bg=C_SURFACE, relief="solid", bd=2,
                                    cursor="hand2")
                    btn.pack()
                    tk.Label(frame, text=f"#{i+1}", bg=C_SURFACE,
                             fg=C_MUTED, font=("Segoe UI", 9)).pack()

                    if i == 0:
                        btn.configure(highlightbackground=C_ACCENT,
                                      highlightthickness=2)
                        result.thumbnail = path
                except Exception:
                    pass

        # Actions
        af = tk.Frame(popup, bg=popup.cget("bg") if hasattr(popup, "cget") else C_BG)
        af.pack(fill="x", padx=20, pady=12)

        ctk.CTkButton(af, text="Abrir pasta de saída",
                      command=lambda: os.startfile(result.output_dir),
                      height=38).pack(side="left", padx=4)
        ctk.CTkButton(af, text="Processar outro vídeo",
                      fg_color=C_SURFACE, hover_color=C_BORDER,
                      command=popup.destroy, height=38).pack(side="left", padx=4)
        ctk.CTkButton(af, text="Fechar", fg_color=C_SURFACE,
                      hover_color=C_BORDER,
                      command=popup.destroy, height=38).pack(side="right", padx=4)

        self._result_popup = result   # keep ref

    def _select_thumb_popup(self, idx: int, frame, carousel) -> None:
        if not hasattr(self, "_result_popup"):
            return
        self._result_popup.thumbnail = self._result_popup.thumbnails_all[idx]
        # Reset all borders
        for f in carousel.winfo_children():
            for w in f.winfo_children():
                if isinstance(w, tk.Button):
                    w.configure(bd=1)
        # Highlight selected
        for btn in frame.winfo_children():
            if isinstance(btn, tk.Button):
                btn.configure(bd=3)

    def _on_close(self) -> None:
        self._playing = False
        self._preview_engine.stop()
        self.root.destroy()


# -- Helpers -------------------------------------------------------------------

def _fmt(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m:02d}:{sec:02d}"
