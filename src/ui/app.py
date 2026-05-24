"""CortaCerto desktop video editor UI."""
from __future__ import annotations

import os
import json
import queue
import re
import contextlib
import subprocess
import threading
import time
import tkinter as tk
import math
from pathlib import Path
from typing import Callable, Optional

import customtkinter as ctk
import cv2
import numpy as np
from tkinter import filedialog, messagebox, ttk
from PIL import Image, ImageDraw, ImageOps, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = "DND_Files"
    TkinterDnD = None

from ..config import ProcessingConfig, Platform, SilenceStyle, PRESETS
from ..core.audio_waveform import extract_waveform
from ..core.ai_assistant import AiSuggestionRequest, suggest_metadata
from ..core.color_grade import ColorGrade, PRESET_CAPCUT, PRESET_CINEMATICO, PRESET_NEUTRAL, PRESET_VINTAGE
from ..core.video_effects import clear_lut_cache
from ..core.error_log import install_error_hooks, record_error, record_error_message
from ..core.preview_engine import PreviewEngine, PreviewFrame, PreviewSettings
from ..core.timeline_manifest import build_timeline_manifest
from ..core.timeline_model import TimelineClip, TimelineModel, TimelineTrack, build_timeline_model
from ..core.composition import Composition, Track as CompTrack, Clip as CompClip, eval_keyframes
from ..core.composition_io import (
    composition_from_timeline_model,
    composition_to_timeline_model,
    composition_to_dict,
    composition_from_dict,
)
from ..core.thumbnail_cache import ThumbnailCache
from ..core.editor import get_video_duration
from ..pipeline import run_pipeline, PipelineResult
from ..ffmpeg_env import encoder_label, ffplay
from .project_manager import ProjectManagerScreen, register_recent_project

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

# -- New editor design colors -------------------------------------------------
ED_BG    = "#0a090c"    # app background
ED_HDR   = "#13111a"    # header bg
ED_PANEL = "#151219"    # panel bg
ED_SURF  = "#1a1820"    # surface
ED_SURF2 = "#201e2e"    # surface hover
ED_BORD  = "#2a2836"    # border
ED_ACC   = "#8B6BFF"    # accent purple
ED_ACC_S = "#1e1840"    # accent soft
ED_ATXT  = "#DCD0FF"    # accent text
ED_TXT   = "#ECE9F2"    # text primary
ED_TXT2  = "#9B97A8"    # text secondary
ED_TXT3  = "#706A7E"    # text tertiary
ED_TXT4  = "#534D62"    # text placeholder
ED_TL_BG = "#0d0b12"    # timeline bg

TL_SPEECH   = "#3a7ebf"   # timeline: fala (vai ficar)
TL_MEDIA    = "#2f8f70"   # timeline: video externo
TL_IMAGE    = "#b77a2d"   # timeline: imagem estatica
TL_SILENCE  = "#2a2a35"   # timeline: silêncio (vai ser cortado)
TL_HEAD     = "#ffcc44"   # playhead
TL_BG       = "#18181e"
TL_LABEL_W  = 74
TL_PAD_R    = 8
PROJECT_EXT = ".ccp"
PROJECT_LEGACY_EXT = ".cortacerto.json"
PROJECT_TRASH_DAYS = 30
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
MEDIA_EXTENSIONS = VIDEO_EXTENSIONS | IMAGE_EXTENSIONS
TL_ZOOM_MIN = 0.5
TL_ZOOM_DEFAULT = 1.0
TL_ZOOM_MAX = 8.0
TL_MARKER_COLOR  = "#FF9040"   # timeline marker flag colour
TL_THUMB_H       = 32          # thumbnail strip height inside video lane (px)
TL_EDIT_MODES    = [           # (key, label, tooltip)
    ("select", "S", "Seleção — arrasta e apara clipes normalmente"),
    ("ripple", "R", "Ripple — aparar empurra/puxa os clipes seguintes"),
    ("roll",   "L", "Roll — move o corte entre dois clipes adjacentes"),
    ("slip",   "P", "Slip — desliza o conteúdo dentro do clipe"),
    ("slide",  "D", "Slide — move o clipe ajustando os vizinhos"),
]


if TkinterDnD is not None:
    class _DnDCTk(ctk.CTk, TkinterDnD.DnDWrapper):
        def __init__(self, *args, **kwargs) -> None:
            super().__init__(*args, **kwargs)
            self.TkdndVersion = TkinterDnD._require(self)
else:
    _DnDCTk = None


def _create_root_window() -> ctk.CTk:
    if _DnDCTk is not None:
        return _DnDCTk()
    return ctk.CTk()


def _register_drop_target(widget: tk.Misc, callback) -> bool:
    try:
        if hasattr(widget, "drop_target_register") and hasattr(widget, "dnd_bind"):
            widget.drop_target_register(DND_FILES)
            widget.dnd_bind("<<Drop>>", callback)
            return True
        widget.tk.call("package", "require", "tkdnd")
        widget.tk.call("tkdnd::drop_target", "register", widget, "DND_Files")
        widget.bind("<<Drop>>", callback)
        return True
    except Exception:
        return False


def _drain_runtime_queue(runtime_queue: queue.Queue) -> int:
    drained = 0
    while True:
        try:
            runtime_queue.get_nowait()
        except queue.Empty:
            return drained
        drained += 1


# ── Pre-loader screen ─────────────────────────────────────────────────────────

class ProjectLoadingScreen(tk.Frame):
    """Splash screen shown while validating project media before opening editor."""

    _BG     = "#0c0b0f"
    _ACCENT = "#8B6BFF"
    _TXT    = "#ECE9F2"
    _TXT2   = "#9B97A8"
    _TXT3   = "#534D62"

    def __init__(
        self,
        root: tk.Tk,
        project_name: str,
        media_paths: list,
        on_ready: Callable,
    ) -> None:
        super().__init__(root, bg=self._BG)
        self.pack(fill="both", expand=True)
        self._root        = root
        self._on_ready    = on_ready
        self._media_paths = list(media_paths)
        self._cancelled   = False
        self._build(project_name)
        self.after(180, self._start_validation)

    def _build(self, project_name: str) -> None:
        mid = tk.Frame(self, bg=self._BG)
        mid.place(relx=0.5, rely=0.5, anchor="center")

        # Logo mark (same arc design as sidebar)
        bm = tk.Canvas(mid, width=52, height=52, bg=self._BG, highlightthickness=0)
        bm.pack()
        for i, color in enumerate(["#B89AFF", "#6B49FF", "#8B6BFF", "#B89AFF"]):
            bm.create_arc(0, 0, 52, 52, start=i * 90, extent=90,
                          fill=color, outline="")
        bm.create_rectangle(11, 11, 41, 41, fill=self._BG, outline="")
        bm.create_polygon(16, 13, 16, 39, 40, 26, fill=self._ACCENT, outline="")

        tk.Label(mid, text="CortaCerto", bg=self._BG, fg=self._TXT,
                 font=("Segoe UI", 17, "bold")).pack(pady=(14, 4))

        name = project_name if len(project_name) <= 40 else project_name[:37] + "…"
        tk.Label(mid, text=f"Abrindo  \"{name}\"…",
                 bg=self._BG, fg=self._TXT2,
                 font=("Segoe UI", 11)).pack(pady=(0, 22))

        # Progress bar
        bar_bg = tk.Frame(mid, bg="#1b1924", width=340, height=4)
        bar_bg.pack_propagate(False)
        bar_bg.pack()
        self._bar = tk.Canvas(bar_bg, bg="#1b1924", highlightthickness=0,
                              height=4, width=340)
        self._bar.pack()
        self._bar_fill = self._bar.create_rectangle(
            0, 0, 0, 4, fill=self._ACCENT, outline="")

        self._status_lbl = tk.Label(mid, text="Verificando arquivos…",
                                    bg=self._BG, fg=self._TXT3,
                                    font=("Segoe UI", 9))
        self._status_lbl.pack(pady=(10, 0))

    def _set_progress(self, pct: float, msg: str) -> None:
        try:
            self._bar.coords(self._bar_fill, 0, 0, int(340 * min(pct, 1.0)), 4)
            self._status_lbl.configure(text=msg)
        except Exception:
            pass

    def _start_validation(self) -> None:
        def _run() -> None:
            paths = self._media_paths
            total = max(len(paths), 1)
            for idx, p in enumerate(paths):
                if self._cancelled:
                    return
                pct = (idx + 1) / total * 0.85
                msg = f"Verificando: {Path(p).name}"
                try:
                    self.after(0, lambda p=pct, m=msg: self._set_progress(p, m))
                    Path(p).stat()           # quick existence check
                    time.sleep(0.04)
                except Exception:
                    pass
            try:
                self.after(0, lambda: self._set_progress(1.0, "Pronto!"))
                time.sleep(0.25)
                self.after(0, self._finish)
            except Exception:
                pass

        threading.Thread(target=_run, daemon=True).start()

    def _finish(self) -> None:
        if not self._cancelled:
            self._on_ready()

    def cancel(self) -> None:
        self._cancelled = True


class CortaCertoApp:
    def __init__(self) -> None:
        self.root = _create_root_window()
        self.root.title("CortaCerto")
        self.root.geometry("1280x780")
        self.root.minsize(1000, 660)
        self.root.configure(fg_color=C_BG)

        self._set_icon()

        # State
        self.video_path:    Optional[str]            = None
        self._music_path:   Optional[str]            = None
        self.result:        Optional[PipelineResult] = None
        self.project_path:  Optional[str]            = None
        self.project_name:  str                      = "Projeto sem nome"
        self._pending_project_state: dict[str, object] = {}
        self._project_media_paths: list[str]         = []
        self._launcher_media_path: Optional[str] = None
        self._launcher_media_var: Optional[tk.StringVar] = None
        self._queue:        queue.Queue              = queue.Queue()
        self._cancel_ev:    threading.Event          = threading.Event()
        self._thumb_imgs:   list                     = []

        # Video player state
        self._preview_engine = PreviewEngine(self._on_preview_frame_ready)
        self._preview_settings_key: tuple = ()
        self._preview_bootstrap_key: Optional[tuple] = None
        self._preview_request_id = 0
        self._preview_backend = "preview"
        self._preview_render_ms = 0.0
        self._total_frames  = 0
        self._fps           = 30.0
        self._duration_s    = 0.0
        self._current_frame = 0
        self._playing       = False
        self._shuttle_speed: float = 1.0   # 1.0=normal, 2.0=fast, 0.5=slow, negative=reverse
        self._play_after_id: Optional[str] = None
        self._play_target_frame: Optional[int] = None
        self._play_generation = 0
        self._play_started_at = 0.0
        self._play_start_frame = 0
        self._audio_proc: Optional[subprocess.Popen] = None
        self._play_audio_started = False

        # Analysis state (filled after background analysis)
        self._segments:     list[tuple[float,float]] = []
        self._analysis_done = False
        self._timeline_model: Optional[TimelineModel] = None
        self._composition:    Optional[Composition]   = None
        self._selected_clip_index: Optional[int] = None
        self._selected_clip_indices: set[int] = set()   # multi-select set
        self._tl_drag_rect: Optional[tuple[int, int, int, int]] = None  # x0,y0,x1,y1 for rect select
        self._tl_minimap: Optional[tk.Canvas] = None
        self._selected_text_index: Optional[int] = None
        self._selected_overlay_index: Optional[int] = None
        self._timeline_dirty = False
        self._timeline_undo_stack: list[tuple] = []   # (snapshot, label)
        self._timeline_redo_stack: list[tuple] = []   # (snapshot, label)
        self._last_undo_label: str = ""
        self._last_redo_label: str = ""
        self._trim_drag: Optional[tuple[int, str]] = None
        self._hover_trim_handle: Optional[tuple[int, str]] = None
        self._text_trim_drag: Optional[tuple[int, str]] = None
        self._hover_text_trim_handle: Optional[tuple[int, str]] = None
        self._overlay_trim_drag: Optional[tuple[int, str]] = None
        self._hover_overlay_trim_handle: Optional[tuple[int, str]] = None
        self._text_move_drag: Optional[tuple[int, float]] = None
        self._clip_move_drag: Optional[tuple[float, TimelineClip, list[TimelineClip]]] = None
        self._trim_undo_captured = False
        self._trim_min_duration_s = 0.15
        self._waveform_zoom = 1.0
        self._timeline_view_center_s: Optional[float] = None
        self._tl_compact_var = tk.BooleanVar(value=True)
        self._track_visual_visible_var = tk.BooleanVar(value=True)
        self._track_text_visible_var = tk.BooleanVar(value=True)
        self._track_audio_muted_var = tk.BooleanVar(value=False)
        self._clip_label_var = tk.StringVar(value="")
        self._clip_scale_var = tk.DoubleVar(value=100.0)
        self._clip_opacity_var = tk.DoubleVar(value=100.0)
        self._clip_pos_x_var = tk.DoubleVar(value=0.0)
        self._clip_pos_y_var = tk.DoubleVar(value=0.0)
        self._clip_text_x_var = tk.DoubleVar(value=0.0)
        self._clip_text_y_var = tk.DoubleVar(value=72.0)
        self._clip_text_size_var = tk.DoubleVar(value=100.0)
        self._clip_text_color_var = tk.StringVar(value="#ffffff")
        self._clip_text_bg_var = tk.BooleanVar(value=True)
        self._clip_text_bg_color_var = tk.StringVar(value="#000000")
        self._clip_volume_var = tk.DoubleVar(value=100.0)
        self._clip_pan_var = tk.DoubleVar(value=0.0)           # Etapa C — per-clip pan L/R
        self._clip_fade_in_var = tk.DoubleVar(value=0.0)       # Etapa C — fade-in seconds
        self._clip_fade_out_var = tk.DoubleVar(value=0.0)      # Etapa C — fade-out seconds
        self._clip_rotation_var = tk.DoubleVar(value=0.0)      # Etapa D — rotation degrees
        self._clip_blend_var = tk.StringVar(value="Normal")    # Etapa D — blend mode
        # Etapa E — per-clip crop
        self._clip_crop_top_var    = tk.DoubleVar(value=0.0)
        self._clip_crop_bottom_var = tk.DoubleVar(value=0.0)
        self._clip_crop_left_var   = tk.DoubleVar(value=0.0)
        self._clip_crop_right_var  = tk.DoubleVar(value=0.0)
        self._crf_var = tk.IntVar(value=18)                     # Etapa E — export CRF
        # Phase 1 — Left rail tab state
        self._left_active_tab: str = "midia"
        self._left_tab_frames: dict = {}
        self._left_tab_btns:   dict = {}
        # Transition quick-select vars (left tab Transições)
        self._left_trans_var     = tk.StringVar(value="Corte")
        self._left_trans_dur_var = tk.DoubleVar(value=0.4)
        # Auto-save timestamp var
        self._autosave_time_var  = tk.StringVar(value="")
        # Shared project-settings vars — initialised here so _build_left_tab_ajuste
        # (called during _ed_left_rail) can reference them before the settings panel runs
        self._rm_silence_var = tk.BooleanVar(value=False)
        self._silence_var    = tk.StringVar(value=SilenceStyle.NATURAL.value)
        self._platform_var   = tk.StringVar(value=Platform.YOUTUBE.value)
        # Etapa F — per-clip color correction
        self._clip_brightness_var = tk.DoubleVar(value=0.0)
        self._clip_contrast_var   = tk.DoubleVar(value=0.0)
        self._clip_saturation_var = tk.DoubleVar(value=0.0)
        self._clip_transition_var = tk.StringVar(value="Corte")
        self._clip_speed_var = tk.StringVar(value="1.0×")    # Etapa 6 — playback speed
        self._clip_chroma_var = tk.BooleanVar(value=False)
        self._clip_chroma_color_var = tk.StringVar(value="#00ff00")
        self._clip_chroma_tolerance_var = tk.DoubleVar(value=45.0)
        self._clip_duration_var = tk.DoubleVar(value=3.0)
        self._insert_duration_var = tk.DoubleVar(value=3.0)
        self._inspector_mode_var = tk.StringVar(value="Nada selecionado")
        self._project_status_var = tk.StringVar(value="")
        self._chroma_picker_active = False
        self._preview_display_image: Optional[Image.Image] = None
        self._preview_display_box: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._preview_drag: Optional[tuple[str, int, int, float, float, float]] = None
        self._preview_drag_moved = False
        self._preview_click_consumed = False
        self._media_listbox: Optional[tk.Listbox] = None
        self._media_drag_path: Optional[str] = None
        self._media_drag_preview_time: Optional[float] = None
        self._last_media_insert_snapped = False
        self._timeline_clipboard: Optional[TimelineClip] = None
        # Etapa 3 — Preview Engine + Cache/Proxy
        self._scrub_last_time: float = 0.0          # monotonic timestamp of last seek
        self._scrub_count: int = 0                  # rapid-seek counter
        self._is_scrubbing: bool = False            # True during rapid timeline scrubbing
        self._scrub_audio_after: Optional[str] = None  # Etapa C — debounce id for scrub burst
        # Etapa C — fade handle drag state: (clip_idx, "in"|"out", drag_origin_x, orig_fade_s)
        self._fade_drag: Optional[tuple[int, str, int, float]] = None
        # Etapa 2 — Timeline UI Profissional
        self._timeline_markers: list[float] = []           # marker times (seconds)
        self._marker_labels: dict[float, str] = {}         # optional label per marker
        self._loop_playback_var = tk.BooleanVar(value=False)
        self._loop_btn: Optional[tk.Button] = None
        self._tb_timecode: Optional[tk.Label] = None
        self._tl_edit_mode: str = "select"                 # select|ripple|roll|slip|slide
        self._tl_edit_mode_btns: dict[str, tk.Button] = {} # mode pill buttons
        self._thumb_cache: ThumbnailCache = ThumbnailCache(on_ready=self._on_thumb_ready)
        self._tl_thumb_refs: list = []                     # PhotoImage refs for gc safety
        # Etapa 4 — Color Grading Profissional
        # Color wheels: (dx, dy) normalized [-1..1] inside unit circle per region
        self._wheel_positions: dict[str, tuple[float, float]] = {
            "lift":  (0.0, 0.0),
            "gamma": (0.0, 0.0),
            "gain":  (0.0, 0.0),
        }
        self._wheel_canvases: dict[str, tk.Canvas] = {}
        self._wheel_photos:   dict[str, ImageTk.PhotoImage] = {}
        self._lut_path: str = ""
        self._lut_name_lbl: Optional[tk.Label] = None
        # Video scopes
        self._scopes_canvas:   Optional[tk.Canvas] = None
        self._scopes_photo:    Optional[ImageTk.PhotoImage] = None
        self._scopes_mode_var: tk.StringVar = tk.StringVar(value="hist")
        self._scope_tab_btns:  dict[str, tk.Button] = {}
        # Mixer (Etapa 5) — 4 fixed channels: Vídeo, Áudio, Música, Mestre
        _N_CH = 4
        self._mix_vol_vars:  list[tk.DoubleVar]  = [tk.DoubleVar(value=100.0) for _ in range(_N_CH)]
        self._mix_pan_vars:  list[tk.DoubleVar]  = [tk.DoubleVar(value=0.0)   for _ in range(_N_CH)]
        self._mix_mute_vars: list[tk.BooleanVar] = [tk.BooleanVar(value=False) for _ in range(_N_CH)]
        self._mix_solo_vars: list[tk.BooleanVar] = [tk.BooleanVar(value=False) for _ in range(_N_CH)]
        self._vu_canvases:   list[Optional[tk.Canvas]] = [None] * _N_CH
        self._vu_levels:     list[float] = [0.0] * _N_CH
        self._vu_peaks:      list[float] = [0.0] * _N_CH
        self._vu_peak_times: list[float] = [0.0] * _N_CH
        self._vu_anim_id:    Optional[str] = None
        self._mixer_block:   Optional[tk.Frame] = None
        self._mix_channel_names = ["Vídeo", "Áudio", "Música", "Mestre"]
        self._clip_inspector_enabled = False
        self._clip_inspector_rows: dict[int, list[tk.Widget]] = {}
        self._clip_text_content: Optional[tk.Text] = None
        self._clip_source_caps: dict[str, cv2.VideoCapture] = {}
        self._clip_source_meta: dict[str, tuple[float, int]] = {}
        self._export_modal = None
        self._export_stage_var = None
        self._export_msg_var = None
        self._fullscreen_preview_win: Optional[tk.Toplevel] = None   # Etapa D — fullscreen preview
        self._fullscreen_preview_photo = None                         # PhotoImage ref
        self._export_stage_progress = None
        self._export_overall_progress = None
        self._error_log_path = install_error_hooks(root=self.root, context_fn=self._error_context)
        print(f"[ERROR_LOG] Registro de erros: {self._error_log_path}")

        self._show_project_launcher()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    def _error_context(self) -> dict[str, object]:
        timeline = self._timeline_model
        return {
            "project_name": getattr(self, "project_name", "Projeto sem nome"),
            "project_ext": Path(self.project_path).suffix if getattr(self, "project_path", None) else "",
            "has_video": bool(getattr(self, "video_path", None)),
            "video_name": Path(self.video_path).name if getattr(self, "video_path", None) else "",
            "media_count": len(getattr(self, "_project_media_paths", []) or []),
            "timeline_ready": timeline is not None,
            "video_clip_count": len(timeline.video_track.clips) if timeline else 0,
            "text_clip_count": len(_timeline_text_clips(timeline)) if timeline else 0,
            "selected_clip_index": getattr(self, "_selected_clip_index", None),
            "selected_text_index": getattr(self, "_selected_text_index", None),
            "selected_overlay_index": getattr(self, "_selected_overlay_index", None),
            "current_frame": getattr(self, "_current_frame", 0),
            "fps": getattr(self, "_fps", 0.0),
            "playing": bool(getattr(self, "_playing", False)),
        }

    def _record_ui_error(self, exc: BaseException, where: str) -> None:
        try:
            path = record_error(exc, where=where, context=self._error_context())
            print(f"[ERROR_LOG] {where}: {path}")
        except Exception:
            pass

    def _record_ui_error_message(self, message: str, where: str) -> None:
        try:
            path = record_error_message(message, where=where, context=self._error_context())
            print(f"[ERROR_LOG] {where}: {path}")
        except Exception:
            pass

    # -- Project launcher (new full-featured manager) -------------------------

    def _clear_root(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()

    def _show_project_launcher(self) -> None:
        self._clear_root()
        self.root.title("CortaCerto")
        self.root.geometry("1180x720")
        self.root.minsize(900, 580)

        # Reset grid config from editor layout
        for r in range(10):
            self.root.grid_rowconfigure(r, weight=0)
        for c in range(10):
            self.root.grid_columnconfigure(c, weight=0)

        self._project_manager = ProjectManagerScreen(
            root=self.root,
            on_open=self._pm_open_project,
            on_create=self._pm_create_project,
            on_quick=self._quick_open_video,
            on_restore=self._restore_project_from_trash_dialog,
        )

        removed = _cleanup_project_trash(_project_trash_dir())
        if removed:
            print(f"[PROJECT] Lixeira limpa: {removed} item(ns) com mais de {PROJECT_TRASH_DAYS} dias.")

    def _pm_open_project(self, path: str) -> None:
        """Called by ProjectManagerScreen when user opens an existing project."""
        metadata   = _read_project_metadata(path) if path else {}
        proj_name  = str(metadata.get("name") or _project_name_from_path(path))
        media_paths = _project_media_paths_from_metadata(metadata)
        self._clear_root()
        self.root.title("CortaCerto")
        self.root.geometry("1180x720")
        ProjectLoadingScreen(
            self.root,
            project_name=proj_name,
            media_paths=media_paths,
            on_ready=lambda: self._open_project_editor(path),
        )

    def _pm_create_project(self, path: str, name: str,
                            category: str, template: str) -> None:
        """Called by ProjectManagerScreen when user creates a new project."""
        import json as _json
        metadata = _build_project_metadata(path)
        metadata["name"] = name
        metadata["category"] = category
        metadata["template"] = template
        if self._launcher_media_path:
            metadata = _project_metadata_with_launcher_media(
                metadata, self._launcher_media_path)
        Path(path).write_text(
            _json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8")
        self._clear_root()
        self.root.title("CortaCerto")
        self.root.geometry("1180x720")
        ProjectLoadingScreen(
            self.root,
            project_name=name,
            media_paths=[],
            on_ready=lambda: self._open_project_editor(path),
        )

    def _import_launcher_media(self) -> None:
        path = filedialog.askopenfilename(
            title="Importar midia",
            filetypes=[
                ("Midias", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v *.jpg *.jpeg *.png *.webp *.bmp"),
                ("Videos", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v"),
                ("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp"),
                ("Todos", "*.*"),
            ]
        )
        if not path:
            return
        self._launcher_media_path = path
        if self._launcher_media_var is not None:
            self._launcher_media_var.set(path)

    def _open_project_trash(self) -> None:
        trash_dir = _project_trash_dir()
        trash_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_project_trash(trash_dir)
        if os.name == "nt":
            os.startfile(str(trash_dir))
        else:
            messagebox.showinfo("Lixeira", str(trash_dir))

    def _restore_project_from_trash_dialog(self) -> None:
        trash_dir = _project_trash_dir()
        trash_dir.mkdir(parents=True, exist_ok=True)
        _cleanup_project_trash(trash_dir)
        source = filedialog.askopenfilename(
            title="Restaurar projeto da lixeira",
            initialdir=str(trash_dir),
            filetypes=[("Projeto CortaCerto", f"*{PROJECT_EXT}"), ("Projeto legado", f"*{PROJECT_LEGACY_EXT}"), ("JSON", "*.json")],
        )
        if not source:
            return
        destination_dir = filedialog.askdirectory(title="Escolha onde restaurar o projeto")
        if not destination_dir:
            return
        try:
            restored_to = _restore_project_from_trash(source, Path(destination_dir))
        except Exception as exc:
            self._record_ui_error(exc, "restore_project_from_trash")
            messagebox.showerror("Erro ao restaurar projeto", str(exc))
            return
        print(f"[PROJECT] Projeto restaurado: {restored_to}")
        self._open_project_editor(str(restored_to))

    def _trash_current_project(self) -> None:
        if not self.project_path:
            return
        if not messagebox.askyesno(
            "Excluir projeto",
            "Mover este projeto para a lixeira do CortaCerto?\n\nO vídeo original não será apagado.",
        ):
            return
        try:
            moved_to = _move_project_to_trash(self.project_path, _project_trash_dir())
        except Exception as exc:
            self._record_ui_error(exc, "trash_current_project")
            messagebox.showerror("Erro ao excluir projeto", str(exc))
            return
        print(f"[PROJECT] Projeto movido para a lixeira: {moved_to}")
        self.project_path = None
        self.project_name = "Projeto sem nome"
        self._reset_loaded_project_runtime()
        self._project_media_paths = []
        self._pending_project_state = {}
        self._show_project_launcher()

    def _create_project(self) -> None:
        path = filedialog.asksaveasfilename(
            title="Criar projeto CortaCerto",
            defaultextension=PROJECT_EXT,
            filetypes=[("Projeto CortaCerto", f"*{PROJECT_EXT}"), ("Projeto legado", f"*{PROJECT_LEGACY_EXT}"), ("JSON", "*.json")],
            initialfile=f"novo-projeto{PROJECT_EXT}",
        )
        if not path:
            return
        metadata = _build_project_metadata(path)
        if self._launcher_media_path:
            metadata = _project_metadata_with_launcher_media(metadata, self._launcher_media_path)
        Path(path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        self._open_project_editor(path)

    def _open_project(self) -> None:
        path = filedialog.askopenfilename(
            title="Abrir projeto CortaCerto",
            filetypes=[("Projeto CortaCerto", f"*{PROJECT_EXT}"), ("Projeto legado", f"*{PROJECT_LEGACY_EXT}"), ("JSON", "*.json"), ("Todos", "*.*")],
        )
        if path:
            self._open_project_editor(path)

    def _quick_open_video(self) -> None:
        self._open_project_editor(None)
        if self._launcher_media_path:
            if _is_video_path(self._launcher_media_path):
                self._load_video(self._launcher_media_path)
            else:
                self._register_project_media([self._launcher_media_path])
                self._tb_status.configure(text="Imagem adicionada a caixa de midia. Carregue um video principal para editar.")
        else:
            self._pick_video()

    def _open_project_editor(self, project_path: Optional[str]) -> None:
        self._reset_loaded_project_runtime()
        self.project_path = project_path
        metadata = _read_project_metadata(project_path) if project_path else {}
        self.project_name = str(metadata.get("name") or _project_name_from_path(project_path))
        self._pending_project_state = metadata
        self._project_media_paths = _project_media_paths_from_metadata(metadata)

        # Register in recent projects store
        if project_path:
            try:
                size_mb = Path(project_path).stat().st_size / (1024 * 1024)
            except Exception:
                size_mb = 0.0
            register_recent_project(
                project_path,
                name=self.project_name,
                category=str(metadata.get("category") or "youtube"),
                status=str(metadata.get("status") or "draft"),
                size_mb=size_mb,
            )

        self._clear_root()
        self.root.geometry("1280x780")
        self.root.minsize(1000, 660)
        self._build_ui()
        self._apply_track_options_from_metadata(metadata)
        self.root.title(f"CortaCerto - {self.project_name}")
        self._tb_status.configure(text=f"Projeto aberto: {self.project_name}")
        video_path = str(metadata.get("video_path") or "")
        if not video_path:
            video_path = _first_existing_video_path(self._project_media_paths) or ""
        if video_path and Path(video_path).exists():
            self.root.after(100, lambda path=video_path: self._load_video(path))
        elif video_path:
            self._tb_status.configure(text="Projeto aberto, mas o vídeo salvo não foi encontrado.")
            self._pending_project_state = {}

    def _reset_loaded_project_runtime(self) -> None:
        # Clear stale widget references BEFORE _clear_root destroys the old frame hierarchy
        self._clip_inspector_rows.clear()
        self._clip_inspector_enabled = False
        with contextlib.suppress(Exception):
            self._stop_playback(reset_button=False)
        with contextlib.suppress(Exception):
            self._stop_preview_audio()
        if self._play_after_id:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._play_after_id)
        if self._preview_timer:
            with contextlib.suppress(Exception):
                self.root.after_cancel(self._preview_timer)
        self._play_after_id = None
        self._preview_timer = None
        self._playing = False
        self._play_target_frame = None
        self._play_audio_started = False
        self._preview_request_id += 1
        self._preview_settings_key = ()
        self._preview_bootstrap_key = None
        self._preview_backend = "preview"
        self._preview_render_ms = 0.0
        self.video_path = None
        self.result = None
        self._music_path = None
        self._segments = []
        self._analysis_done = False
        self._timeline_model = None
        self._composition    = None
        self._scrub_count    = 0
        self._is_scrubbing   = False
        self._timeline_markers.clear()
        self._tl_edit_mode   = "select"
        self._tl_thumb_refs.clear()
        if hasattr(self, '_thumb_cache'):
            self._thumb_cache.clear()
        self._selected_clip_index = None
        self._selected_text_index = None
        self._selected_overlay_index = None
        self._timeline_dirty = False
        self._timeline_undo_stack.clear()
        self._timeline_redo_stack.clear()
        self._trim_drag = None
        self._hover_trim_handle = None
        self._text_trim_drag = None
        self._hover_text_trim_handle = None
        self._overlay_trim_drag = None
        self._hover_overlay_trim_handle = None
        self._text_move_drag = None
        self._clip_move_drag = None
        self._media_drag_path = None
        self._media_drag_preview_time = None
        self._drag_ghost_window = None
        self._tl_hover_thumb_window: Optional[tk.Toplevel] = None
        self._tl_hover_time_s: Optional[float] = None
        self._selected_overlay_track_idx: int = 0
        self._timeline_view_center_s = None
        self._track_visual_visible_var.set(True)
        self._track_text_visible_var.set(True)
        self._track_audio_muted_var.set(False)
        self._trim_undo_captured = False
        self._total_frames = 0
        self._fps = 30.0
        self._duration_s = 0.0
        self._current_frame = 0
        self._preview_display_image = None
        self._preview_display_box = (0, 0, 0, 0)
        self._preview_drag = None
        self._preview_drag_moved = False
        self._preview_click_consumed = False
        self._chroma_picker_active = False
        self._release_clip_source_caps()
        _drain_runtime_queue(self._queue)

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
        self.root.configure(fg_color=ED_BG)
        # Reset row config
        for r in range(5):
            self.root.grid_rowconfigure(r, weight=0, minsize=0)
        self.root.grid_rowconfigure(0, weight=0, minsize=56)   # header
        self.root.grid_rowconfigure(1, weight=1)               # workspace
        self.root.grid_rowconfigure(2, weight=0, minsize=232)  # timeline
        self.root.grid_columnconfigure(0, weight=1)
        self._ed_header()
        self._ed_workspace()
        self._ed_timeline_section()
        self._bind_shortcuts()
        self._setup_drop_targets_reliable()

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<space>", self._shortcut_toggle_play)
        self.root.bind_all("<KeyPress-b>", self._shortcut_split)
        self.root.bind_all("<KeyPress-B>", self._shortcut_split)
        self.root.bind_all("<Delete>", self._shortcut_delete)
        self.root.bind_all("<BackSpace>", self._shortcut_delete)
        self.root.bind_all("<Shift-Delete>", self._shortcut_ripple_delete)
        self.root.bind_all("<Control-z>", self._shortcut_undo)
        self.root.bind_all("<Control-Z>", self._shortcut_undo)
        self.root.bind_all("<Control-y>", self._shortcut_redo)
        self.root.bind_all("<Control-Y>", self._shortcut_redo)
        self.root.bind_all("<Control-Shift-Z>", self._shortcut_redo)
        self.root.bind_all("<Control-d>", self._shortcut_duplicate)
        self.root.bind_all("<Control-D>", self._shortcut_duplicate)
        self.root.bind_all("<Control-c>", self._shortcut_copy)
        self.root.bind_all("<Control-C>", self._shortcut_copy)
        self.root.bind_all("<Control-x>", self._shortcut_cut)
        self.root.bind_all("<Control-X>", self._shortcut_cut)
        self.root.bind_all("<Control-v>", self._shortcut_paste)
        self.root.bind_all("<Control-V>", self._shortcut_paste)
        self.root.bind_all("<Alt-Left>", lambda event: self._shortcut_nudge_selected(event, -1))
        self.root.bind_all("<Alt-Right>", lambda event: self._shortcut_nudge_selected(event, 1))
        self.root.bind_all("<Left>", lambda event: self._shortcut_seek_relative(event, -1))
        self.root.bind_all("<Right>", lambda event: self._shortcut_seek_relative(event, 1))
        self.root.bind_all("<Shift-Left>", lambda event: self._shortcut_seek_relative(event, -1, large_step=True))
        self.root.bind_all("<Shift-Right>", lambda event: self._shortcut_seek_relative(event, 1, large_step=True))
        self.root.bind_all("<Home>", self._shortcut_seek_start)
        self.root.bind_all("<End>", self._shortcut_seek_end)
        self.root.bind_all("<Control-plus>", lambda event: self._shortcut_timeline_zoom(event, 1))
        self.root.bind_all("<Control-equal>", lambda event: self._shortcut_timeline_zoom(event, 1))
        self.root.bind_all("<Control-minus>", lambda event: self._shortcut_timeline_zoom(event, -1))
        self.root.bind_all("<Control-0>", self._shortcut_timeline_fit)
        self.root.bind_all("<KeyPress-z>", self._shortcut_zoom_to_selection)
        self.root.bind_all("<KeyPress-Z>", self._shortcut_zoom_to_selection)
        self.root.bind_all("<KeyPress-m>", self._shortcut_add_marker)
        self.root.bind_all("<KeyPress-M>", self._shortcut_add_marker)
        self.root.bind_all("<Shift-m>",    self._shortcut_next_marker)
        self.root.bind_all("<Shift-M>",    self._shortcut_next_marker)
        self.root.bind("<KeyPress-j>", self._shortcut_shuttle_j)
        self.root.bind("<KeyPress-J>", self._shortcut_shuttle_j)
        self.root.bind("<KeyPress-k>", self._shortcut_shuttle_k)
        self.root.bind("<KeyPress-K>", self._shortcut_shuttle_k)
        self.root.bind("<KeyPress-l>", self._shortcut_shuttle_l)
        self.root.bind("<KeyPress-L>", self._shortcut_shuttle_l)
        self.root.bind("<Control-a>", self._shortcut_select_all)
        self.root.bind("<Control-A>", self._shortcut_select_all)

    def _shortcut_allowed(self, event: tk.Event) -> bool:
        widget = getattr(event, "widget", None)
        cls = widget.winfo_class() if widget is not None else ""
        return "Entry" not in cls and "Text" not in cls

    def _shortcut_toggle_play(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._toggle_play()
        return "break"

    _SHUTTLE_SPEEDS = [0.25, 0.5, 1.0, 2.0, 4.0]

    def _shortcut_shuttle_j(self, event: tk.Event) -> str | None:
        """J = step back 1 frame (or previous marker)."""
        if not self._shortcut_allowed(event):
            return None
        if self._playing:
            self._stop_playback(reset_button=True)
        target = max(0, self._current_frame - 1)
        self._seek_to(target)
        self._tb_status.configure(text=f"← Frame {target}")
        return "break"

    def _shortcut_shuttle_k(self, event: tk.Event) -> str | None:
        """K = pause / stop playback."""
        if not self._shortcut_allowed(event):
            return None
        if self._playing:
            self._stop_playback(reset_button=True)
            self._tb_status.configure(text="Pausado (K).")
        return "break"

    def _shortcut_shuttle_l(self, event: tk.Event) -> str | None:
        """L = play 1× → press again while playing = 2× → 4× → back to 1×."""
        if not self._shortcut_allowed(event):
            return None
        speeds = self._SHUTTLE_SPEEDS
        if not self._playing:
            self._shuttle_speed = 1.0
            self._toggle_play()
            self._tb_status.configure(text="▶ Reproduzindo 1× (L).")
        else:
            # Cycle through faster speeds
            try:
                idx = speeds.index(self._shuttle_speed)
                self._shuttle_speed = speeds[min(idx + 1, len(speeds) - 1)]
            except ValueError:
                self._shuttle_speed = 1.0
            self._tb_status.configure(text=f"▶ Velocidade {self._shuttle_speed}× (L).")
        return "break"

    def _shortcut_select_all(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event) or not self._timeline_model:
            return None
        n = len(self._timeline_model.video_track.clips)
        self._selected_clip_indices = set(range(n))
        if n > 0:
            self._selected_clip_index = n - 1
        self._redraw_timeline()
        self._tb_status.configure(text=f"Todos os {n} clipes selecionados (Ctrl+A).")
        return "break"

    def _shortcut_split(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._split_selected_clip()
        return "break"

    def _shortcut_delete(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._delete_selected_clip()
        return "break"

    def _shortcut_ripple_delete(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._ripple_delete_selected_clip()
        return "break"

    def _ripple_delete_selected_clip(self) -> None:
        """Delete selected speech clip and close the gap by shifting all later clips left."""
        if not self._timeline_model or self._selected_clip_index is None:
            self._tb_status.configure(text="Selecione um clipe de fala para excluir com ripple.")
            return
        idx = self._selected_clip_index
        clips = self._timeline_model.video_track.clips
        if idx < 0 or idx >= len(clips):
            return
        self._stop_playback(reset_button=True)
        current_time = self._current_frame / max(1.0, self._fps)
        self._push_timeline_undo(label="ripple delete")
        clip = clips[idx]
        gap = max(0.0, clip.end_s - clip.start_s)
        del clips[idx]
        for c in clips[idx:]:
            c.start_s = max(0.0, c.start_s - gap)
            c.end_s   = max(c.start_s, c.end_s - gap)
        self._selected_clip_index = None
        self._sync_manual_timeline()
        self._timeline_dirty = True
        self._seek_to(self._time_to_frame(self._nearest_kept_time(current_time)))
        self._tb_status.configure(text=f"Clipe removido com ripple ({gap:.2f}s fechado).")
        self._refresh_clip_inspector()

    def _shortcut_undo(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._undo_timeline_action()
        return "break"

    def _shortcut_redo(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._redo_timeline_action()
        return "break"

    def _shortcut_duplicate(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._duplicate_selected_timeline_item()
        return "break"

    def _shortcut_copy(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._copy_selected_timeline_item()
        return "break"

    def _shortcut_cut(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._cut_selected_timeline_item()
        return "break"

    def _shortcut_paste(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._paste_timeline_clipboard_at_playhead()
        return "break"

    def _shortcut_nudge_selected(self, event: tk.Event, direction: int) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        shift_pressed = bool(int(getattr(event, "state", 0)) & 0x0001)
        step_s = 1.0 if shift_pressed else max(1.0 / max(1.0, self._fps), 0.05)
        if self._nudge_selected_timeline_item(step_s * int(direction)):
            return "break"
        return None

    def _shortcut_seek_relative(self, event: tk.Event, direction: int, large_step: bool = False) -> str | None:
        if not self._shortcut_allowed(event) or self._total_frames <= 0:
            return None
        self._stop_playback(reset_button=True)
        frame = _relative_seek_frame(
            self._current_frame,
            direction,
            self._fps,
            self._total_frames,
            large_step=large_step,
        )
        self._seek_to(frame)
        self._tb_status.configure(text=f"Playhead: {_fmt(frame / max(1.0, self._fps))}.")
        return "break"

    def _shortcut_seek_start(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event) or self._total_frames <= 0:
            return None
        self._stop_playback(reset_button=True)
        self._seek_to(0)
        self._tb_status.configure(text="Playhead no inicio.")
        return "break"

    def _shortcut_seek_end(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event) or self._total_frames <= 0:
            return None
        self._stop_playback(reset_button=True)
        frame = max(0, self._total_frames - 1)
        self._seek_to(frame)
        self._tb_status.configure(text=f"Playhead: {_fmt(frame / max(1.0, self._fps))}.")
        return "break"

    def _shortcut_timeline_zoom(self, event: tk.Event, direction: int) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._adjust_timeline_zoom(0.25 * int(direction))
        return "break"

    def _shortcut_timeline_fit(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._set_timeline_zoom(TL_ZOOM_MIN, reset_view=True)
        self._tb_status.configure(text="Timeline em Ver tudo.")
        return "break"

    def _shortcut_zoom_to_selection(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._zoom_to_selected_clip()
        return "break"

    def _zoom_to_selected_clip(self) -> None:
        """Zoom and center the timeline so the selected clip fills ~80 % of the view."""
        if not self._timeline_model or self._duration_s <= 0:
            self._tb_status.configure(text="Nada selecionado para focar.")
            return
        clip = self._selected_timeline_clip()
        if clip is None:
            self._tb_status.configure(text="Selecione um clipe para focar a timeline.")
            return
        clip_dur = max(0.05, clip.end_s - clip.start_s)
        # Choose a zoom so the clip occupies ~80 % of the visible window
        desired_zoom = max(TL_ZOOM_MIN, min(TL_ZOOM_MAX, self._duration_s * 0.8 / clip_dur))
        center_s = (clip.start_s + clip.end_s) / 2.0
        self._timeline_view_center_s = center_s
        self._tl_zoom.set(desired_zoom)
        self._on_timeline_zoom(desired_zoom)
        self._tb_status.configure(text=f"Focado em '{clip.label}' ({_fmt(clip.start_s)}–{_fmt(clip.end_s)}).")

    def _setup_drop_targets(self) -> None:
        try:
            self.root.tk.call("package", "require", "tkdnd")
            for widget in (self.root, self._preview_canvas):
                widget.tk.call("tkdnd::drop_target", "register", widget, "DND_Files")
                widget.bind("<<Drop>>", self._on_drop_files)
            self._tl_canvas.tk.call("tkdnd::drop_target", "register", self._tl_canvas, "DND_Files")
            self._tl_canvas.bind("<<Drop>>", self._on_timeline_drop_files)
            self._tb_status.configure(text="Arraste um vídeo para o preview ou use Abrir vídeo.")
        except Exception:
            self._tb_status.configure(text="Use Abrir vídeo para importar mídia.")

    def _setup_drop_targets_reliable(self) -> None:
        enabled = False
        for widget in (self.root, self._preview_canvas):
            enabled = _register_drop_target(widget, self._on_drop_files) or enabled
        enabled = _register_drop_target(self._tl_canvas, self._on_timeline_drop_files) or enabled
        if self._media_listbox is not None:
            enabled = _register_drop_target(self._media_listbox, self._on_media_box_drop_files) or enabled
        if enabled:
            self._tb_status.configure(text="Arraste vídeos para o preview ou direto para a timeline.")
        else:
            self._tb_status.configure(text="Use Abrir vídeo para importar mídia.")

    def _on_drop_files(self, event: tk.Event) -> str:
        paths = _media_paths_from_drop(getattr(event, "data", ""))
        if paths:
            self._register_project_media(paths)
            first_video = next((path for path in paths if _is_video_path(path)), None)
            if first_video:
                self._load_video(first_video)
            else:
                self._save_project_media_paths()
                self._tb_status.configure(text=f"{len(paths)} imagem(ns) adicionada(s) ao projeto.")
            if first_video and len(paths) > 1:
                self._tb_status.configure(text=f"{len(paths)} vídeos adicionados ao projeto. Primeiro vídeo carregado.")
        else:
            self._tb_status.configure(text="Solte um arquivo de vídeo compatível.")
        return "break"

    def _on_timeline_drop_files(self, event: tk.Event) -> str:
        paths = _media_paths_from_drop(getattr(event, "data", ""))
        if not paths:
            self._tb_status.configure(text="Solte um arquivo de vídeo compatível na timeline.")
            return "break"
        self._register_project_media(paths)
        if not self.video_path or not self._timeline_model:
            first_video = next((path for path in paths if _is_video_path(path)), None)
            if first_video:
                self._load_video(first_video)
            else:
                self._save_project_media_paths()
                self._tb_status.configure(text="Imagem adicionada. Carregue um video principal antes de inserir na timeline.")
                return "break"
            self._tb_status.configure(text="Vídeo principal carregado pela timeline.")
            return "break"
        time_s = self._timeline_time_from_event(event)
        inserted = 0
        insert_duration = self._insert_duration_s()
        for offset, path in enumerate(paths):
            if self._insert_media_path_at_time(path, min(self._duration_s, time_s + offset * insert_duration), save=False):
                inserted += 1
        self._save_project_media_paths()
        self._tb_status.configure(text=f"{inserted} mídia(s) inserida(s) na timeline.")
        return "break"

    def _on_media_box_drop_files(self, event: tk.Event) -> str:
        paths = _media_paths_from_drop(getattr(event, "data", ""))
        if not paths:
            self._tb_status.configure(text="Solte videos ou imagens compativeis na caixa de midia.")
            return "break"
        added = self._register_project_media(paths)
        self._save_project_media_paths()
        videos, images = _project_media_counts(paths)
        if added:
            self._tb_status.configure(text=f"{added} midia(s) adicionada(s): {videos} video(s), {images} imagem(ns).")
        else:
            self._tb_status.configure(text="Essas midias ja estavam no projeto.")
        return "break"

    # -- Toolbar ---------------------------------------------------------------

    def _build_toolbar(self) -> None:
        tb = tk.Frame(self.root, bg="#111116", height=48)
        tb.grid(row=0, column=0, sticky="ew")
        tb.grid_propagate(False)
        tb.grid_columnconfigure(6, weight=1)

        # Logo
        tk.Label(tb, text="CortaCerto", bg="#111116", fg=C_ACCENT2,
                 font=("Segoe UI", 13, "bold")).grid(row=0, column=0, padx=(14,18), pady=8)
        tk.Label(tb, text=self.project_name, bg="#111116", fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=1, padx=(0, 10), pady=8)

        # Open
        self._open_btn = self._tb_btn(tb, "Abrir vídeo", self._pick_video,
                                       fg=C_TEXT)
        self._open_btn.grid(row=0, column=2, padx=4, pady=8)

        # Export
        self._export_btn = self._tb_btn(tb, "Exportar", self._start,
                                         fg="#ffffff", bg=C_ACCENT)
        self._export_btn.grid(row=0, column=3, padx=4, pady=8)
        self._export_btn.configure(state="disabled")

        # Cancel
        self._cancel_btn = self._tb_btn(tb, "Cancelar", self._cancel,
                                         fg="#ffffff", bg=C_RED)
        self._cancel_btn.grid(row=0, column=4, padx=(4, 16), pady=8)
        self._cancel_btn.configure(state="disabled")

        self._trash_project_btn = self._tb_btn(tb, "Excluir projeto", self._trash_current_project,
                                               fg="#ffffff", bg="#7a2e2e")
        self._trash_project_btn.grid(row=0, column=5, padx=(0, 10), pady=8)
        if not self.project_path:
            self._trash_project_btn.configure(state="disabled")

        # Progress bar (hidden initially)
        self._tb_progress = ctk.CTkProgressBar(tb, height=4, width=180,
                                                progress_color=C_ACCENT)
        self._tb_progress.set(0)
        self._tb_progress.grid(row=0, column=6, padx=8, pady=20, sticky="ew")

        # Status
        self._tb_status = tk.Label(tb, text="Abra um vídeo para começar",
                                    bg="#111116", fg=C_MUTED,
                                    font=("Segoe UI", 10))
        self._tb_status.grid(row=0, column=7, padx=8)

        # GPU / Seg labels (right side)
        self._gpu_lbl = tk.Label(tb, text="Encode: verificando", bg="#111116", fg=C_MUTED,
                                  font=("Segoe UI", 9))
        self._gpu_lbl.grid(row=0, column=8, padx=(0,8))
        self._seg_lbl = tk.Label(tb, text="Seg: grabcut", bg="#111116", fg=C_MUTED,
                                  font=("Segoe UI", 9))
        self._seg_lbl.grid(row=0, column=9, padx=(0,14))

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
        self._preview_canvas.bind("<ButtonPress-1>", self._on_preview_press)
        self._preview_canvas.bind("<B1-Motion>", self._on_preview_drag)
        self._preview_canvas.bind("<ButtonRelease-1>", self._on_preview_release)
        self._preview_canvas.bind("<Motion>", self._on_preview_motion)
        self._preview_canvas.bind("<Leave>", lambda e: self._preview_canvas.configure(cursor=""))

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

        # Etapa D — fullscreen preview button
        btn("⛶", self._toggle_fullscreen_preview).grid(row=1, column=5, padx=(8, 8), pady=2)

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
            hdr, from_=TL_ZOOM_MIN, to=TL_ZOOM_MAX, number_of_steps=75, width=170,
            fg_color=C_SURFACE, progress_color=C_ACCENT, button_color=C_ACCENT2,
            command=self._on_timeline_zoom,
        )
        self._tl_zoom.set(TL_ZOOM_DEFAULT)
        self._tl_zoom.pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Focar", command=self._zoom_to_selected_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="Encaixar", command=lambda: self._set_timeline_zoom(TL_ZOOM_MIN, reset_view=True),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="Ver tudo", command=lambda: self._set_timeline_zoom(TL_ZOOM_MIN, reset_view=True),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text=">", command=lambda: self._pan_timeline_view(1),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="<", command=lambda: self._pan_timeline_view(-1),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="+", command=lambda: self._adjust_timeline_zoom(0.25),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=7,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="-", command=lambda: self._adjust_timeline_zoom(-0.25),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Checkbutton(
            hdr,
            text="Juntar blocos",
            variable=self._tl_compact_var,
            command=self._redraw_timeline,
            bg=C_PANEL,
            fg=C_TEXT,
            selectcolor=C_SURFACE,
            activebackground=C_PANEL,
            activeforeground=C_TEXT,
            font=("Segoe UI", 9),
            relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Checkbutton(
            hdr,
            text="Visual",
            variable=self._track_visual_visible_var,
            command=self._on_track_control_changed,
            bg=C_PANEL,
            fg=C_TEXT,
            selectcolor=C_SURFACE,
            activebackground=C_PANEL,
            activeforeground=C_TEXT,
            font=("Segoe UI", 9),
            relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Checkbutton(
            hdr,
            text="Texto",
            variable=self._track_text_visible_var,
            command=self._on_track_control_changed,
            bg=C_PANEL,
            fg=C_TEXT,
            selectcolor=C_SURFACE,
            activebackground=C_PANEL,
            activeforeground=C_TEXT,
            font=("Segoe UI", 9),
            relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Checkbutton(
            hdr,
            text="Audio mute",
            variable=self._track_audio_muted_var,
            command=self._on_track_control_changed,
            bg=C_PANEL,
            fg=C_TEXT,
            selectcolor=C_SURFACE,
            activebackground=C_PANEL,
            activeforeground=C_TEXT,
            font=("Segoe UI", 9),
            relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Desfazer", command=self._undo_timeline_action,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Refazer", command=self._redo_timeline_action,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(4, 0))
        tk.Button(hdr, text="Duplicar", command=self._duplicate_selected_timeline_item,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Dividir", command=self._split_selected_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Excluir", command=self._delete_selected_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))

        self._tl_canvas = tk.Canvas(tl_outer, bg=TL_BG, height=190,
                                     highlightthickness=0, cursor="hand2")
        self._tl_canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2,4))
        self._tl_canvas.bind("<Configure>", lambda e: self._redraw_timeline())
        self._tl_canvas.bind("<ButtonPress-1>", self._tl_press)
        self._tl_canvas.bind("<B1-Motion>", self._tl_drag_motion)
        self._tl_canvas.bind("<ButtonRelease-1>", self._tl_release)
        self._tl_canvas.bind("<Motion>", self._tl_motion)
        self._tl_canvas.bind("<Leave>", self._tl_leave)
        self._tl_canvas.bind("<MouseWheel>", self._tl_mousewheel)
        self._tl_canvas.bind("<Shift-MouseWheel>", self._tl_shift_mousewheel)
        self._tl_canvas.bind("<Double-ButtonPress-1>", self._tl_double_click)

        self._tl_playhead = None
        self._redraw_timeline()

    # -- New editor layout methods --------------------------------------------

    def _ed_header(self) -> None:
        """Build the 56px header bar (row 0 of root)."""
        hdr = tk.Frame(self.root, bg=ED_HDR, height=56)
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(10, 0))
        hdr.grid_propagate(False)
        hdr.grid_columnconfigure(1, weight=1)

        # ── Left: navigation + undo + timecode ───────────────────────────────
        left = tk.Frame(hdr, bg=ED_HDR)
        left.grid(row=0, column=0, sticky="ns", padx=(0, 8), pady=4)

        tk.Button(
            left, text="← Voltar", command=self._show_project_launcher,
            bg=ED_SURF, fg=ED_TXT, activebackground=ED_SURF2, activeforeground=ED_TXT,
            relief="flat", padx=10, pady=5, font=("Segoe UI", 10),
            cursor="hand2", bd=0, highlightthickness=0,
        ).pack(side="left", padx=(0, 4))

        tk.Frame(left, bg=ED_BORD, width=1).pack(side="left", fill="y", pady=6)

        tk.Button(
            left, text="↩", command=self._undo_timeline_action,
            bg=ED_SURF, fg=ED_TXT, activebackground=ED_SURF2, activeforeground=ED_TXT,
            relief="flat", padx=9, pady=5, font=("Segoe UI", 12),
            cursor="hand2", bd=0, highlightthickness=0,
        ).pack(side="left", padx=(4, 1))

        tk.Button(
            left, text="↪", command=self._redo_timeline_action,
            bg=ED_SURF, fg=ED_TXT, activebackground=ED_SURF2, activeforeground=ED_TXT,
            relief="flat", padx=9, pady=5, font=("Segoe UI", 12),
            cursor="hand2", bd=0, highlightthickness=0,
        ).pack(side="left", padx=(1, 6))

        tk.Frame(left, bg=ED_BORD, width=1).pack(side="left", fill="y", pady=6)

        # Timecode display
        self._tb_timecode = tk.Label(
            left, text="00:00:00", bg=ED_HDR, fg=ED_TXT2,
            font=("Courier New", 11, "bold"))
        self._tb_timecode.pack(side="left", padx=(8, 4))

        # GPU/Seg info (small, below timecode)
        info_col = tk.Frame(left, bg=ED_HDR)
        info_col.pack(side="left", padx=(4, 0))
        self._gpu_lbl = tk.Label(info_col, text="GPU: —", bg=ED_HDR, fg=ED_TXT4, font=("Segoe UI", 7))
        self._gpu_lbl.pack(anchor="w")
        self._seg_lbl = tk.Label(info_col, text="Seg: —", bg=ED_HDR, fg=ED_TXT4, font=("Segoe UI", 7))
        self._seg_lbl.pack(anchor="w")
        tk.Frame(left, bg=ED_BORD, width=1).pack(side="left", fill="y", pady=6, padx=(8, 0))
        self._autosave_lbl = tk.Label(
            left, textvariable=self._autosave_time_var,
            bg=ED_HDR, fg=ED_TXT4, font=("Segoe UI", 8))
        self._autosave_lbl.pack(side="left", padx=(6, 0))

        # ── Center: tool palette ─────────────────────────────────────────────
        center = tk.Frame(hdr, bg=ED_HDR)
        center.grid(row=0, column=1, sticky="ns", pady=6)

        palette = tk.Frame(center, bg="#1a1628",
                           highlightbackground="#3a3060", highlightthickness=1)
        palette.pack()

        self._ed_tool_var = tk.StringVar(value="cursor")
        self._ed_tool_btns: dict[str, tk.Button] = {}

        tools = [
            ("cursor", "↖", "Seleção"),
            ("razor",  "✂", "Corte"),
            ("text",   "T",  "Texto"),
            ("image",  "⊞",  "Mídia"),
            ("fx",     "✦",  "Efeitos"),
        ]

        def _select_tool(name: str) -> None:
            self._ed_tool_var.set(name)
            for n, btn in self._ed_tool_btns.items():
                btn.configure(
                    bg=ED_ACC if n == name else "#1a1628",
                    fg="white" if n == name else ED_TXT2,
                )

        for name, icon, label in tools:
            is_active = (name == "cursor")
            cell = tk.Frame(palette, bg=ED_ACC if is_active else "#1a1628")
            cell.pack(side="left")
            btn = tk.Button(
                cell,
                text=f"{icon}\n{label}",
                command=lambda n=name: _select_tool(n),
                bg=ED_ACC if is_active else "#1a1628",
                fg="white" if is_active else ED_TXT2,
                activebackground=ED_SURF2, activeforeground="white",
                relief="flat", padx=10, pady=3,
                font=("Segoe UI", 9), cursor="hand2", bd=0,
                highlightthickness=0, justify="center",
            )
            btn.pack()
            self._ed_tool_btns[name] = btn

        # ── Right: status + transport + actions ──────────────────────────────
        right = tk.Frame(hdr, bg=ED_HDR)
        right.grid(row=0, column=2, sticky="ns", padx=(8, 0), pady=6)

        self._tb_status = tk.Label(
            right, text="Abra um vídeo para começar",
            bg=ED_HDR, fg=ED_TXT2,
            font=("Segoe UI", 9), anchor="e")
        self._tb_status.pack(side="left", padx=(0, 6))

        self._tb_progress = ctk.CTkProgressBar(
            right, height=4, width=120,
            progress_color=ED_ACC, fg_color=ED_SURF)
        self._tb_progress.set(0)
        self._tb_progress.pack(side="left", padx=(0, 8))

        tk.Frame(right, bg=ED_BORD, width=1).pack(side="left", fill="y", pady=6)

        # Loop toggle
        def _toggle_loop() -> None:
            val = self._loop_playback_var.get()
            self._loop_btn.configure(
                bg=ED_ACC if val else ED_SURF,
                fg="white" if val else ED_TXT2,
            )
            self._tb_status.configure(text="Loop ativado." if val else "Loop desativado.")
        self._loop_btn = tk.Button(
            right, text="⟳",
            command=lambda: (self._loop_playback_var.set(not self._loop_playback_var.get()), _toggle_loop()),
            bg=ED_SURF, fg=ED_TXT2,
            activebackground=ED_SURF2, activeforeground="white",
            relief="flat", padx=8, pady=5,
            font=("Segoe UI", 11), cursor="hand2", bd=0,
            highlightthickness=0,
        )
        self._loop_btn.pack(side="left", padx=(4, 2))

        self._play_btn = tk.Button(
            right, text="▶",
            command=self._toggle_play,
            bg="#ffffff", fg="#0a090c",
            activebackground="#dddddd", activeforeground="#0a090c",
            relief="flat", padx=11, pady=5,
            font=("Segoe UI", 12, "bold"), cursor="hand2", bd=0,
            highlightthickness=0,
        )
        self._play_btn.pack(side="left", padx=(2, 6))

        tk.Frame(right, bg=ED_BORD, width=1).pack(side="left", fill="y", pady=6)

        self._export_btn = tk.Button(
            right, text="⬆ Exportar",
            command=self._start,
            bg=ED_ACC, fg="white",
            activebackground=ED_SURF2, activeforeground="white",
            relief="flat", padx=14, pady=5,
            font=("Segoe UI", 10, "bold"), cursor="hand2", bd=0,
            highlightthickness=0, state="disabled",
        )
        self._export_btn.pack(side="left", padx=(6, 4))

        tk.Frame(right, bg=ED_BORD, width=1).pack(side="left", fill="y", pady=6)

        self._trash_project_btn = tk.Button(
            right, text="🗑",
            command=self._trash_current_project,
            bg=ED_SURF, fg="#ff6666",
            activebackground="#3a1515", activeforeground="#ff6666",
            relief="flat", padx=7, pady=5,
            font=("Segoe UI", 11), cursor="hand2", bd=0,
            highlightthickness=0,
        )
        self._trash_project_btn.pack(side="left", padx=(4, 1))
        if not self.project_path:
            self._trash_project_btn.configure(state="disabled")

        self._cancel_btn = tk.Button(
            right, text="✕",
            command=self._cancel,
            bg=ED_SURF, fg=ED_TXT3,
            activebackground="#3a1515", activeforeground="#ff6666",
            relief="flat", padx=7, pady=5,
            font=("Segoe UI", 11), cursor="hand2", bd=0,
            highlightthickness=0, state="disabled",
        )
        self._cancel_btn.pack(side="left", padx=(1, 0))

        self.root.after(800, self._detect_seg_label)

    def _ed_workspace(self) -> None:
        """Build the flex workspace row (row 1 of root)."""
        workspace = tk.Frame(self.root, bg=ED_BG)
        workspace.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)
        workspace.grid_rowconfigure(0, weight=1)
        workspace.grid_columnconfigure(0, weight=0, minsize=280)
        workspace.grid_columnconfigure(1, weight=1)
        workspace.grid_columnconfigure(2, weight=0, minsize=340)

        self._ed_left_rail(workspace)
        self._ed_center_stage(workspace)
        self._ed_right_rail(workspace)

        # Build properties panel into a hidden frame so all widget refs exist
        self._props_hidden_parent = tk.Frame(self.root, bg=ED_BG)
        # NOT packed/gridded — stays invisible but widgets still exist
        self._build_properties(self._props_hidden_parent)

    def _ed_left_rail(self, parent: tk.Frame) -> None:
        """Left rail with CapCut-style tab bar (Phase 1)."""
        rail = tk.Frame(parent, bg=ED_PANEL, width=280)
        rail.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        rail.grid_propagate(False)
        rail.grid_rowconfigure(1, weight=1)
        rail.grid_columnconfigure(0, weight=1)

        # ── Tab bar: 2 rows × 3 columns ──────────────────────────────────────
        tab_bar = tk.Frame(rail, bg="#0e0c14",
                           highlightbackground=ED_BORD, highlightthickness=1)
        tab_bar.grid(row=0, column=0, sticky="ew")
        for col in range(3):
            tab_bar.grid_columnconfigure(col, weight=1)

        _TABS = [
            ("midia",      "📁", "Mídia"),
            ("audio",      "🎵", "Áudio"),
            ("texto",      "T",  "Texto"),
            ("efeitos",    "✨", "Efeitos"),
            ("transicoes", "⬡",  "Tran."),
            ("ajuste",     "⚙", "Ajuste"),
        ]

        for idx, (tid, icon, label) in enumerate(_TABS):
            r, c = divmod(idx, 3)
            btn = tk.Button(
                tab_bar,
                text=f"{icon}\n{label}",
                command=lambda t=tid: self._left_switch_tab(t),
                bg=ED_ACC if tid == "midia" else "#0e0c14",
                fg=ED_TXT if tid == "midia" else ED_TXT3,
                activebackground=ED_SURF2, activeforeground=ED_TXT,
                relief="flat", padx=0, pady=5,
                font=("Segoe UI", 8), cursor="hand2", bd=0,
                highlightthickness=0,
            )
            btn.grid(row=r, column=c, sticky="ew", padx=1, pady=1)
            self._left_tab_btns[tid] = btn

        # ── Tab content area (stacked frames) ────────────────────────────────
        content = tk.Frame(rail, bg=ED_PANEL)
        content.grid(row=1, column=0, sticky="nsew")
        content.grid_rowconfigure(0, weight=1)
        content.grid_columnconfigure(0, weight=1)

        for tid, _, _ in _TABS:
            f = tk.Frame(content, bg=ED_PANEL)
            f.grid(row=0, column=0, sticky="nsew")
            self._left_tab_frames[tid] = f

        self._build_left_tab_midia(self._left_tab_frames["midia"])
        self._build_left_tab_audio(self._left_tab_frames["audio"])
        self._build_left_tab_texto(self._left_tab_frames["texto"])
        self._build_left_tab_efeitos(self._left_tab_frames["efeitos"])
        self._build_left_tab_transicoes(self._left_tab_frames["transicoes"])
        self._build_left_tab_ajuste(self._left_tab_frames["ajuste"])

        self._left_switch_tab("midia")

    def _left_switch_tab(self, tid: str) -> None:
        """Raise the selected left-rail tab frame and update button styles."""
        self._left_active_tab = tid
        if tid in self._left_tab_frames:
            self._left_tab_frames[tid].tkraise()
        for btn_id, btn in self._left_tab_btns.items():
            is_active = (btn_id == tid)
            btn.configure(
                bg=ED_ACC if is_active else "#0e0c14",
                fg=ED_TXT if is_active else ED_TXT3,
            )

    # ── Left tab content builders ─────────────────────────────────────────────

    def _build_left_tab_midia(self, parent: tk.Frame) -> None:
        """Tab Mídia — project media list (same as old left rail)."""
        parent.grid_rowconfigure(2, weight=1)
        parent.grid_columnconfigure(0, weight=1)

        # Header
        hdr = tk.Frame(parent, bg=ED_PANEL)
        hdr.grid(row=0, column=0, sticky="ew", padx=10, pady=(8, 4))
        tk.Label(hdr, text="Mídias do Projeto", bg=ED_PANEL, fg=ED_TXT,
                 font=("Segoe UI", 10, "bold")).pack(side="left")

        # Search
        sf = tk.Frame(parent, bg=ED_SURF,
                      highlightbackground=ED_BORD, highlightthickness=1)
        sf.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        tk.Label(sf, text="🔍", bg=ED_SURF, fg=ED_TXT3,
                 font=("Segoe UI", 9)).pack(side="left", padx=(6, 0))
        tk.Entry(sf, bg=ED_SURF, fg=ED_TXT, insertbackground=ED_TXT,
                 relief="flat", bd=0, font=("Segoe UI", 9)
                 ).pack(side="left", fill="x", expand=True, padx=6, pady=4)

        # Listbox
        lf = tk.Frame(parent, bg=ED_SURF,
                      highlightbackground=ED_BORD, highlightthickness=1)
        lf.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 4))
        lb_scroll = tk.Scrollbar(lf, orient="vertical",
                                  bg=ED_SURF, troughcolor=ED_SURF)
        lb_scroll.pack(side="right", fill="y")
        self._media_listbox = tk.Listbox(
            lf, bg=ED_SURF, fg=ED_TXT,
            selectbackground=ED_ACC, selectforeground="white",
            relief="flat", height=8, font=("Segoe UI", 9),
            activestyle="none", yscrollcommand=lb_scroll.set,
            highlightthickness=0, bd=0,
        )
        self._media_listbox.pack(side="left", fill="both", expand=True)
        lb_scroll.config(command=self._media_listbox.yview)
        self._media_listbox.bind("<Double-Button-1>",
            lambda _e: self._open_or_insert_selected_project_media())
        self._media_listbox.bind("<ButtonPress-1>", self._media_listbox_press)
        self._media_listbox.bind("<B1-Motion>",     self._media_listbox_drag)
        self._media_listbox.bind("<ButtonRelease-1>", self._media_listbox_release)

        # Action buttons
        acts = tk.Frame(parent, bg=ED_PANEL)
        acts.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        for col in range(3):
            acts.grid_columnconfigure(col, weight=1)

        def _lb_btn(text, cmd):
            return tk.Button(acts, text=text, command=cmd,
                             bg=ED_SURF, fg=ED_TXT,
                             activebackground=ED_SURF2, activeforeground=ED_TXT,
                             relief="flat", padx=6, pady=4,
                             font=("Segoe UI", 9), cursor="hand2", bd=0,
                             highlightthickness=0)

        _lb_btn("Adicionar", self._add_project_media).grid(
            row=0, column=0, sticky="ew", padx=(0, 2), pady=2)
        _lb_btn("Abrir", self._load_selected_project_media).grid(
            row=0, column=1, sticky="ew", padx=2, pady=2)
        _lb_btn("Inserir", self._insert_selected_media_clip).grid(
            row=0, column=2, sticky="ew", padx=(2, 0), pady=2)

    def _build_left_tab_audio(self, parent: tk.Frame) -> None:
        """Tab Áudio — music + main video volume controls."""
        parent.grid_columnconfigure(0, weight=1)

        def _sec(text, row):
            tk.Label(parent, text=text, bg=ED_PANEL, fg=ED_TXT3,
                     font=("Segoe UI", 8, "bold")).grid(
                row=row, column=0, sticky="w", padx=12, pady=(10, 2))

        _sec("MÚSICA DE FUNDO", 0)

        mf = tk.Frame(parent, bg=ED_PANEL)
        mf.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 4))
        mf.grid_columnconfigure(1, weight=1)
        tk.Label(mf, text="🎵", bg=ED_PANEL, fg=ED_TXT2,
                 font=("Segoe UI", 11)).grid(row=0, column=0, padx=(0, 4))
        self._left_music_label = tk.Label(
            mf, text="Nenhuma", bg=ED_PANEL, fg=ED_TXT3,
            font=("Segoe UI", 9), anchor="w")
        self._left_music_label.grid(row=0, column=1, sticky="ew")
        tk.Button(mf, text="...", command=self._left_pick_music,
                  bg=ED_SURF, fg=ED_TXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0,
                  highlightthickness=0).grid(row=0, column=2, padx=2)
        tk.Button(mf, text="✕", command=self._left_clear_music,
                  bg=ED_SURF, fg=ED_TXT3, relief="flat", padx=4,
                  font=("Segoe UI", 9), cursor="hand2", bd=0,
                  highlightthickness=0).grid(row=0, column=3)

        _sec("VOLUME DA MÚSICA", 2)
        vr = tk.Frame(parent, bg=ED_PANEL)
        vr.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))
        vr.grid_columnconfigure(1, weight=1)
        tk.Label(vr, text="🔊", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 10)).grid(row=0, column=0)
        _mv_lbl = tk.Label(vr, text="13%", bg=ED_PANEL, fg=ED_TXT3,
                           font=("Segoe UI", 9))
        _mv_lbl.grid(row=0, column=2, padx=(4, 0))
        ctk.CTkSlider(
            vr, from_=0, to=200, number_of_steps=200,
            variable=self._mix_vol_vars[self._MIX_MUSIC],
            height=14, button_color=ED_ACC, progress_color=ED_ACC,
            fg_color=ED_SURF,
            command=lambda v: _mv_lbl.configure(text=f"{int(float(v))}%"),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        _sec("VÍDEO PRINCIPAL", 4)
        vv = tk.Frame(parent, bg=ED_PANEL)
        vv.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 4))
        vv.grid_columnconfigure(1, weight=1)
        tk.Label(vv, text="🎬", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 10)).grid(row=0, column=0)
        _vv_lbl = tk.Label(vv, text="100%", bg=ED_PANEL, fg=ED_TXT3,
                           font=("Segoe UI", 9))
        _vv_lbl.grid(row=0, column=2, padx=(4, 0))
        ctk.CTkSlider(
            vv, from_=0, to=200, number_of_steps=200,
            variable=self._mix_vol_vars[self._MIX_VIDEO],
            height=14, button_color=ED_ACC, progress_color=ED_ACC,
            fg_color=ED_SURF,
            command=lambda v: _vv_lbl.configure(text=f"{int(float(v))}%"),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        tk.Checkbutton(
            parent, text="🔇  Silenciar áudio do vídeo",
            variable=self._mix_mute_vars[self._MIX_AUDIO],
            command=self._on_track_control_changed,
            bg=ED_PANEL, fg=ED_TXT2, selectcolor=ED_SURF,
            activebackground=ED_PANEL, activeforeground=ED_TXT,
            font=("Segoe UI", 9), relief="flat",
        ).grid(row=6, column=0, sticky="w", padx=14, pady=(0, 8))

    def _left_pick_music(self) -> None:
        self._pick_music()
        self._update_left_music_label()

    def _left_clear_music(self) -> None:
        self._clear_music()
        self._update_left_music_label()

    def _update_left_music_label(self) -> None:
        if not hasattr(self, "_left_music_label"):
            return
        if self._music_path:
            self._left_music_label.configure(
                text=Path(self._music_path).name, fg=ED_TXT)
        else:
            self._left_music_label.configure(text="Nenhuma", fg=ED_TXT3)

    def _build_left_tab_texto(self, parent: tk.Frame) -> None:
        """Tab Texto — add text clips + quick style presets."""
        parent.grid_columnconfigure(0, weight=1)

        tk.Label(parent, text="ADICIONAR TEXTO", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        tk.Button(
            parent, text="+ Criar Texto",
            command=self._add_text_at_playhead,
            bg=ED_ACC, fg="white",
            activebackground=ED_SURF2, activeforeground="white",
            relief="flat", padx=10, pady=8,
            font=("Segoe UI", 10, "bold"), cursor="hand2", bd=0,
            highlightthickness=0,
        ).grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 8))

        tk.Label(parent, text="PRESETS RÁPIDOS", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=2, column=0, sticky="w", padx=12, pady=(4, 4))

        presets_frame = tk.Frame(parent, bg=ED_PANEL)
        presets_frame.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 8))
        presets_frame.grid_columnconfigure((0, 1, 2), weight=1)

        _TEXT_PRESETS = [
            ("Título",   {"text_size_pct": 160, "text_position_y_pct": 20,
                          "text_background_enabled": False}),
            ("Legenda",  {"text_size_pct": 90,  "text_position_y_pct": 82,
                          "text_background_enabled": True}),
            ("Destaque", {"text_size_pct": 120, "text_position_y_pct": 50,
                          "text_background_enabled": True}),
        ]

        def _apply_text_preset(p: dict) -> None:
            for attr, val in p.items():
                if attr == "text_size_pct":
                    self._clip_text_size_var.set(float(val))
                elif attr == "text_position_y_pct":
                    self._clip_text_y_var.set(float(val))
                elif attr == "text_background_enabled":
                    self._clip_text_bg_var.set(bool(val))
            self._add_text_at_playhead()

        for col, (name, props) in enumerate(_TEXT_PRESETS):
            tk.Button(
                presets_frame, text=name,
                command=lambda p=props: _apply_text_preset(p),
                bg=ED_SURF, fg=ED_TXT,
                activebackground=ED_SURF2, activeforeground=ED_TXT,
                relief="flat", padx=4, pady=6,
                font=("Segoe UI", 9), cursor="hand2", bd=0,
                highlightthickness=0,
            ).grid(row=0, column=col, sticky="ew", padx=2)

        tk.Label(parent, text="POSICAO VERTICAL", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=4, column=0, sticky="w", padx=12, pady=(8, 2))

        pos_row = tk.Frame(parent, bg=ED_PANEL)
        pos_row.grid(row=5, column=0, sticky="ew", padx=10, pady=(0, 4))
        pos_row.grid_columnconfigure(1, weight=1)
        _py_lbl = tk.Label(pos_row, text="72%", bg=ED_PANEL, fg=ED_TXT3, font=("Segoe UI", 9))
        _py_lbl.grid(row=0, column=2, padx=(4, 0))
        tk.Label(pos_row, text="Y", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 9)).grid(row=0, column=0, padx=(0, 4))
        ctk.CTkSlider(
            pos_row, from_=0, to=100, number_of_steps=100,
            variable=self._clip_text_y_var,
            height=14, button_color=ED_ACC, progress_color=ED_ACC,
            fg_color=ED_SURF,
            command=lambda v: _py_lbl.configure(text=f"{int(float(v))}%"),
        ).grid(row=0, column=1, sticky="ew", padx=6)

    def _build_left_tab_efeitos(self, parent: tk.Frame) -> None:
        """Tab Efeitos — color grade presets grid."""
        parent.grid_columnconfigure(0, weight=1)

        tk.Label(parent, text="PRESETS VISUAIS", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        grid = tk.Frame(parent, bg=ED_PANEL)
        grid.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        for col in range(2):
            grid.grid_columnconfigure(col, weight=1)

        preset_names = list(_FX_PRESETS.keys())
        for idx, name in enumerate(preset_names):
            row, col = divmod(idx, 2)
            is_normal = (name == "Normal")
            tk.Button(
                grid, text=name,
                command=lambda n=name: self._apply_effect_preset(n),
                bg=ED_SURF2 if not is_normal else ED_ACC_S,
                fg=ED_TXT if not is_normal else ED_ATXT,
                activebackground=ED_ACC_S, activeforeground=ED_ATXT,
                relief="flat", bd=0,
                font=("Segoe UI", 9),
                cursor="hand2",
                padx=4, pady=8,
            ).grid(row=row, column=col, sticky="ew", padx=3, pady=3)

        tk.Label(parent, text="AJUSTE RÁPIDO", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=2, column=0, sticky="w", padx=12, pady=(8, 4))

        for _row_off, (_lbl, _key) in enumerate([
            ("Saturação",  "saturation"),
            ("Contraste",  "contrast"),
            ("Brilho",     "brightness"),
        ]):
            _fr = tk.Frame(parent, bg=ED_PANEL)
            _fr.grid(row=3 + _row_off, column=0, sticky="ew", padx=10, pady=2)
            _fr.grid_columnconfigure(1, weight=1)
            tk.Label(_fr, text=_lbl, bg=ED_PANEL, fg=ED_TXT3,
                     font=("Segoe UI", 8), width=9, anchor="w").grid(row=0, column=0)
            if _key in getattr(self, "_c_sliders", {}):
                _c_s = self._c_sliders[_key]
                ctk.CTkSlider(
                    _fr, from_=-100, to=100, number_of_steps=200,
                    variable=tk.DoubleVar(value=_c_s.get()),
                    height=12, button_color=ED_ACC, progress_color=ED_ACC,
                    fg_color=ED_SURF,
                    command=lambda v, ck=_key: self._sliders.get(ck, None) or None,
                ).grid(row=0, column=1, sticky="ew", padx=6)

    def _build_left_tab_transicoes(self, parent: tk.Frame) -> None:
        """Tab Transições — transition type picker + duration."""
        parent.grid_columnconfigure(0, weight=1)

        tk.Label(parent, text="TIPO DE TRANSICAO", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=0, column=0, sticky="w", padx=12, pady=(10, 4))

        _TRANS_TYPES = [
            ("Corte",       "Corte"),
            ("Fade ⬛",     "Fade"),
            ("Dissolve",    "Dissolve"),
            ("Slide →",     "Slide-D"),
            ("← Slide",     "Slide-E"),
            ("Zoom In",     "Zoom-In"),
            ("Zoom Out",    "Zoom-Out"),
            ("Flash",       "Flash"),
            ("Wipe →",      "Wipe-H"),
            ("Wipe ↓",      "Wipe-V"),
        ]

        tgrid = tk.Frame(parent, bg=ED_PANEL)
        tgrid.grid(row=1, column=0, sticky="ew", padx=8, pady=(0, 8))
        for col in range(2):
            tgrid.grid_columnconfigure(col, weight=1)

        self._trans_btn_refs: dict[str, tk.Button] = {}

        def _apply_trans(tid: str) -> None:
            self._left_trans_var.set(tid)
            # Apply to selected clip
            clip = self._selected_timeline_clip()
            if clip is not None:
                clip.transition = tid
                self._timeline_dirty = True
                self._sync_manual_timeline(mark_dirty=True)
                self._refresh_clip_inspector()
                self._tb_status.configure(text=f"Transição: {tid}")
            # Update button highlight
            for tt, tb in self._trans_btn_refs.items():
                is_sel = (tt == tid)
                tb.configure(
                    bg=ED_ACC_S if is_sel else ED_SURF,
                    fg=ED_ATXT if is_sel else ED_TXT,
                )

        for idx, (label, tid) in enumerate(_TRANS_TYPES):
            row, col = divmod(idx, 2)
            is_sel = (tid == self._left_trans_var.get())
            btn = tk.Button(
                tgrid, text=label,
                command=lambda t=tid: _apply_trans(t),
                bg=ED_ACC_S if is_sel else ED_SURF,
                fg=ED_ATXT if is_sel else ED_TXT,
                activebackground=ED_ACC_S, activeforeground=ED_ATXT,
                relief="flat", bd=0,
                font=("Segoe UI", 9),
                cursor="hand2",
                padx=4, pady=7,
            )
            btn.grid(row=row, column=col, sticky="ew", padx=3, pady=3)
            self._trans_btn_refs[tid] = btn

        tk.Label(parent, text="DURACAO (s)", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).grid(
            row=2, column=0, sticky="w", padx=12, pady=(4, 2))

        dur_row = tk.Frame(parent, bg=ED_PANEL)
        dur_row.grid(row=3, column=0, sticky="ew", padx=10, pady=(0, 6))
        dur_row.grid_columnconfigure(1, weight=1)
        _dur_lbl = tk.Label(dur_row, text="0.4s", bg=ED_PANEL, fg=ED_TXT3,
                            font=("Segoe UI", 9))
        _dur_lbl.grid(row=0, column=2, padx=(4, 0))
        tk.Label(dur_row, text="⏱", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 10)).grid(row=0, column=0)
        ctk.CTkSlider(
            dur_row, from_=0.1, to=2.0, number_of_steps=19,
            variable=self._left_trans_dur_var,
            height=14, button_color=ED_ACC, progress_color=ED_ACC,
            fg_color=ED_SURF,
            command=lambda v: (
                _dur_lbl.configure(text=f"{float(v):.1f}s"),
                self._apply_trans_duration(float(v)),
            ),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        tk.Button(
            parent, text="Aplicar a todos os clipes",
            command=self._apply_trans_to_all,
            bg=ED_SURF, fg=ED_TXT,
            activebackground=ED_SURF2, activeforeground=ED_TXT,
            relief="flat", padx=8, pady=6,
            font=("Segoe UI", 9), cursor="hand2", bd=0,
            highlightthickness=0,
        ).grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 8))

    def _apply_trans_duration(self, dur_s: float) -> None:
        clip = self._selected_timeline_clip()
        if clip is not None:
            clip.transition_duration_s = max(0.1, dur_s)
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)

    def _apply_trans_to_all(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um vídeo antes.")
            return
        tid = self._left_trans_var.get()
        dur = float(self._left_trans_dur_var.get())
        self._push_timeline_undo(label="transição em massa")
        for clip in self._timeline_model.video_track.clips:
            clip.transition = tid
            clip.transition_duration_s = dur
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._refresh_clip_inspector()
        self._tb_status.configure(text=f"Transição '{tid}' ({dur:.1f}s) aplicada a todos.")

    def _build_left_tab_ajuste(self, parent: tk.Frame) -> None:
        """Tab Ajuste — key project settings (silence + export)."""
        parent.grid_columnconfigure(0, weight=1)

        def _sec(text, row):
            tk.Label(parent, text=text, bg=ED_PANEL, fg=ED_TXT3,
                     font=("Segoe UI", 8, "bold")).grid(
                row=row, column=0, sticky="w", padx=12, pady=(10, 2))

        _sec("CORTE DE SILÊNCIO", 0)

        tk.Checkbutton(
            parent, text="Ativar corte automático",
            variable=self._rm_silence_var,
            bg=ED_PANEL, fg=ED_TXT2, selectcolor=ED_SURF,
            activebackground=ED_PANEL, activeforeground=ED_TXT,
            font=("Segoe UI", 9), relief="flat",
        ).grid(row=1, column=0, sticky="w", padx=12, pady=(0, 4))

        sf = tk.Frame(parent, bg=ED_PANEL)
        sf.grid(row=2, column=0, sticky="ew", padx=10, pady=(0, 4))
        for i, (lbl, style) in enumerate([
            ("Agressivo", SilenceStyle.AGGRESSIVE),
            ("Natural",   SilenceStyle.NATURAL),
            ("Leve",      SilenceStyle.LIGHT),
        ]):
            tk.Radiobutton(
                sf, text=lbl, variable=self._silence_var, value=style.value,
                bg=ED_PANEL, fg=ED_TXT2, selectcolor=ED_SURF,
                activebackground=ED_PANEL, activeforeground=ED_TXT,
                font=("Segoe UI", 9), relief="flat",
            ).pack(side="left", padx=(0, 6))

        _sec("PLATAFORMA", 3)
        pf = tk.Frame(parent, bg=ED_PANEL)
        pf.grid(row=4, column=0, sticky="ew", padx=10, pady=(0, 4))
        for i, (lbl, plat) in enumerate([
            ("YouTube", Platform.YOUTUBE), ("Reels", Platform.REELS),
            ("TikTok",  Platform.TIKTOK),  ("Shorts", Platform.SHORTS),
        ]):
            tk.Radiobutton(
                pf, text=lbl, variable=self._platform_var, value=plat.value,
                bg=ED_PANEL, fg=ED_TXT2, selectcolor=ED_SURF,
                activebackground=ED_PANEL, activeforeground=ED_TXT,
                font=("Segoe UI", 9), relief="flat",
            ).grid(row=i // 2, column=i % 2, sticky="w", padx=4)

        _sec("QUALIDADE EXPORT", 5)
        crf_row = tk.Frame(parent, bg=ED_PANEL)
        crf_row.grid(row=6, column=0, sticky="ew", padx=10, pady=(0, 4))
        crf_row.grid_columnconfigure(1, weight=1)
        _crf_disp = tk.Label(crf_row, text=f"CRF {self._crf_var.get()}",
                             bg=ED_PANEL, fg=ED_TXT3, font=("Segoe UI", 9))
        _crf_disp.grid(row=0, column=2, padx=(4, 0))
        tk.Label(crf_row, text="🎞", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 10)).grid(row=0, column=0)
        ctk.CTkSlider(
            crf_row, from_=15, to=28, number_of_steps=13,
            variable=self._crf_var,
            height=14, button_color=ED_ACC, progress_color=ED_ACC,
            fg_color=ED_SURF,
            command=lambda v: _crf_disp.configure(text=f"CRF {self._crf_var.get()}"),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        tk.Button(
            parent, text="▶ Analisar e cortar silêncios",
            command=self._pick_video_and_analyze if not getattr(self, "_analysis_done", False) else self._re_analyze,
            bg=ED_ACC, fg="white",
            activebackground=ED_SURF2, activeforeground="white",
            relief="flat", padx=10, pady=8,
            font=("Segoe UI", 9, "bold"), cursor="hand2", bd=0,
            highlightthickness=0,
        ).grid(row=7, column=0, sticky="ew", padx=10, pady=(8, 4))

    def _pick_video_and_analyze(self) -> None:
        """Shortcut to open video picker (used from Ajuste tab)."""
        if self.video_path:
            self._re_analyze() if hasattr(self, "_re_analyze") else None
        else:
            self._pick_video()

    def _ed_center_stage(self, parent: tk.Frame) -> None:
        """Center stage: preview canvas + transport (column 1 of workspace)."""
        stage = tk.Frame(parent, bg=ED_BG)
        stage.grid(row=0, column=1, sticky="nsew")
        stage.grid_rowconfigure(0, weight=1)
        stage.grid_rowconfigure(1, weight=0)
        stage.grid_rowconfigure(2, weight=0)
        stage.grid_columnconfigure(0, weight=1)

        # Preview canvas
        self._preview_canvas = tk.Canvas(
            stage, bg="#050407",
            highlightthickness=1,
            highlightbackground=ED_BORD,
        )
        self._preview_canvas.grid(row=0, column=0, sticky="nsew")
        self._preview_canvas.bind("<Configure>",       self._on_preview_resize)
        self._preview_canvas.bind("<ButtonPress-1>",   self._on_preview_press)
        self._preview_canvas.bind("<B1-Motion>",       self._on_preview_drag)
        self._preview_canvas.bind("<ButtonRelease-1>", self._on_preview_release)
        self._preview_canvas.bind("<Motion>",          self._on_preview_motion)
        self._preview_canvas.bind("<Leave>",           lambda e: self._preview_canvas.configure(cursor=""))

        self._no_video_id = self._preview_canvas.create_text(
            400, 200,
            text="Abra um vídeo para começar",
            fill=ED_TXT3, font=("Segoe UI", 14), anchor="center")
        self._preview_photo = None  # keeps PhotoImage ref

        # Seek bar
        self._seek_var = tk.DoubleVar(value=0)
        self._seek_bar = ctk.CTkSlider(
            stage, from_=0, to=1,
            variable=self._seek_var,
            command=self._on_seek,
            height=14,
            button_color=ED_ACC,
            progress_color=ED_ACC,
            fg_color=ED_SURF,
        )
        self._seek_bar.grid(row=1, column=0, sticky="ew", padx=8, pady=(4, 0))

        # Transport buttons row
        transport = tk.Frame(stage, bg=ED_BG)
        transport.grid(row=2, column=0, sticky="ew", pady=(2, 0))

        def _tb(text, cmd):
            return tk.Button(
                transport, text=text, command=cmd,
                bg=ED_BG, fg=ED_TXT,
                activebackground=ED_SURF, activeforeground=ED_TXT,
                relief="flat", padx=10, pady=4,
                font=("Segoe UI", 11), cursor="hand2", bd=0,
                highlightthickness=0)

        _tb("◀", self._seek_start).pack(side="left", padx=(8, 2))

        # NOTE: _play_btn is also created in _ed_header, but that header
        # button calls _toggle_play directly without being the tracked ref.
        # The transport play_btn IS self._play_btn (used for configure calls).
        # We reassign here to overwrite the header btn reference, which is fine
        # because the header play btn was already packed; it still works.
        self._play_btn = tk.Button(
            transport, text="▶",
            command=self._toggle_play,
            bg=ED_ACC, fg="white",
            activebackground=ED_SURF2, activeforeground="white",
            relief="flat", padx=12, pady=4,
            font=("Segoe UI", 12, "bold"), cursor="hand2", bd=0,
            highlightthickness=0,
        )
        self._play_btn.pack(side="left", padx=2)

        _tb("▶|", self._seek_end).pack(side="left", padx=2)

        self._time_lbl = tk.Label(
            transport, text="00:00 / 00:00",
            bg=ED_BG, fg=ED_TXT2,
            font=("Courier New", 10))
        self._time_lbl.pack(side="left", padx=12)

    def _ed_right_rail(self, parent: tk.Frame) -> None:
        """Right rail: design blocks (340px, column 2 of workspace)."""
        rail = tk.Frame(parent, bg=ED_BG, width=340)
        rail.grid(row=0, column=2, sticky="nsew", padx=(8, 0))
        rail.grid_propagate(False)

        right_scroll = ctk.CTkScrollableFrame(
            rail,
            fg_color=ED_BG,
            scrollbar_button_color=ED_BORD,
            scrollbar_button_hover_color=ED_TXT4,
        )
        right_scroll.pack(fill="both", expand=True)
        right_scroll.grid_columnconfigure(0, weight=1)

        # -- Block 1: Audio / Volume ------------------------------------------
        b1 = tk.Frame(right_scroll, bg=ED_PANEL,
                      highlightbackground=ED_BORD, highlightthickness=1)
        b1.pack(fill="x", padx=4, pady=(4, 4))
        b1.grid_columnconfigure(0, weight=1)

        # Tabs
        tabs1 = tk.Frame(b1, bg=ED_PANEL)
        tabs1.pack(fill="x", padx=10, pady=(10, 4))
        for tab_lbl in ["Vídeo", "Animação", "Rastreamento"]:
            is_active = (tab_lbl == "Vídeo")
            tk.Label(
                tabs1, text=tab_lbl,
                bg=ED_ACC_S if is_active else ED_PANEL,
                fg=ED_ATXT if is_active else ED_TXT3,
                font=("Segoe UI", 9, "bold" if is_active else "normal"),
                padx=8, pady=3, cursor="hand2",
            ).pack(side="left", padx=(0, 2))

        tk.Label(b1, text="Música", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8)).pack(anchor="w", padx=12, pady=(4, 0))
        tk.Label(b1, text="Volume", bg=ED_PANEL, fg=ED_TXT,
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=12)
        tk.Label(b1, text="75%", bg=ED_PANEL, fg=ED_TXT2,
                 font=("Segoe UI", 9)).pack(anchor="w", padx=12, pady=(0, 4))

        vol_row = tk.Frame(b1, bg=ED_PANEL)
        vol_row.pack(fill="x", padx=10, pady=(0, 6))
        tk.Label(vol_row, text="🔊", bg=ED_PANEL, fg=ED_TXT2,
                 font=("Segoe UI", 10)).pack(side="left")
        _vol_var = tk.DoubleVar(value=75)
        ctk.CTkSlider(vol_row, from_=0, to=100, variable=_vol_var,
                      height=12, button_color=ED_ACC,
                      progress_color=ED_ACC, fg_color=ED_SURF,
                      ).pack(side="left", fill="x", expand=True, padx=6)
        tk.Label(vol_row, text="75%", bg=ED_PANEL, fg=ED_TXT2,
                 font=("Segoe UI", 9)).pack(side="left")

        track_pill = tk.Frame(b1, bg=ED_SURF,
                              highlightbackground=ED_BORD, highlightthickness=1)
        track_pill.pack(fill="x", padx=10, pady=(0, 10))
        tk.Label(track_pill, text="≋ Study Chill Relax...", bg=ED_SURF, fg=ED_TXT2,
                 font=("Segoe UI", 9)).pack(side="left", padx=8, pady=4)

        # -- Block 2: Video Scopes (Etapa 4) ----------------------------------
        b2 = tk.Frame(right_scroll, bg=ED_PANEL,
                      highlightbackground=ED_BORD, highlightthickness=1)
        b2.pack(fill="x", padx=4, pady=(0, 4))

        # Header row with mode tabs
        scope_hdr = tk.Frame(b2, bg=ED_PANEL)
        scope_hdr.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(scope_hdr, text="ESCOPOS", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).pack(side="left")

        scope_tabs_frame = tk.Frame(scope_hdr, bg=ED_PANEL)
        scope_tabs_frame.pack(side="right")

        def _make_scope_btn(mode_val: str, label: str) -> tk.Button:
            def _cmd():
                self._scopes_mode_var.set(mode_val)
                _update_scope_tabs()
                if getattr(self, "_preview_display_image", None) is not None:
                    self._draw_scopes(self._preview_display_image)
            btn = tk.Button(
                scope_tabs_frame, text=label, command=_cmd,
                bg=ED_ACC if mode_val == "hist" else ED_SURF,
                fg=ED_TXT if mode_val == "hist" else ED_TXT3,
                relief="flat", padx=6, pady=2,
                font=("Segoe UI", 8), cursor="hand2", bd=0,
                highlightthickness=0,
            )
            btn.pack(side="left", padx=1)
            return btn

        def _update_scope_tabs() -> None:
            cur = self._scopes_mode_var.get()
            for m, b in self._scope_tab_btns.items():
                b.configure(
                    bg=ED_ACC if m == cur else ED_SURF,
                    fg=ED_TXT if m == cur else ED_TXT3,
                )

        for _sm, _sl in [("hist", "Hist"), ("wave", "Onda"), ("vector", "Vetor")]:
            self._scope_tab_btns[_sm] = _make_scope_btn(_sm, _sl)

        # Scopes canvas
        self._scopes_canvas = tk.Canvas(
            b2, bg="#050407", height=120,
            highlightthickness=0,
        )
        self._scopes_canvas.pack(fill="x", padx=10, pady=(0, 10))

        # -- Block 3: Mixer de Áudio (Etapa 5) --------------------------------
        b3 = tk.Frame(right_scroll, bg=ED_PANEL,
                      highlightbackground=ED_BORD, highlightthickness=1)
        b3.pack(fill="x", padx=4, pady=(0, 4))
        self._mixer_block = b3
        self._build_mixer_panel(b3)

        # -- Block 4: Efeitos Visuais Rápidos (Etapa 6) -----------------------
        b4 = tk.Frame(right_scroll, bg=ED_PANEL,
                      highlightbackground=ED_BORD, highlightthickness=1)
        b4.pack(fill="x", padx=4, pady=(0, 8))
        self._build_effects_presets_panel(b4)

    def _build_effects_presets_panel(self, parent: tk.Frame) -> None:
        """Block 4 — 8 quick visual effect preset buttons (Etapa 6)."""
        # Header
        hdr = tk.Frame(parent, bg=ED_PANEL)
        hdr.pack(fill="x", padx=10, pady=(8, 4))
        tk.Label(hdr, text="EFEITOS VISUAIS", bg=ED_PANEL, fg=ED_TXT2,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(hdr, text="preset rápido", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8)).pack(side="right")

        grid = tk.Frame(parent, bg=ED_PANEL)
        grid.pack(fill="x", padx=8, pady=(0, 10))
        for col in range(4):
            grid.grid_columnconfigure(col, weight=1)

        preset_names = list(_FX_PRESETS.keys())
        for idx, name in enumerate(preset_names):
            row, col = divmod(idx, 4)
            is_normal = (name == "Normal")
            bg = ED_SURF2 if not is_normal else ED_ACC_S
            fg = ED_TXT if not is_normal else ED_ATXT
            tk.Button(
                grid, text=name,
                command=lambda n=name: self._apply_effect_preset(n),
                bg=bg, fg=fg,
                activebackground=ED_ACC_S, activeforeground=ED_ATXT,
                relief="flat", bd=0,
                font=("Segoe UI", 8),
                cursor="hand2",
                padx=4, pady=5,
            ).grid(row=row, column=col, sticky="ew", padx=2, pady=2)

    def _apply_effect_preset(self, name: str) -> None:
        """Apply an Etapa 6 visual effects preset to the current color grade."""
        from ..core.color_grade import PRESET_CAPCUT
        overrides = _FX_PRESETS.get(name, {})
        if name == "Normal":
            # Reset to the capcut base preset
            self._load_preset("CapCut")
            self._tb_status.configure(text="Preset 'Normal' aplicado (CapCut base).")
            return

        # Apply overrides on top of current grade
        grade_fields = {
            "temperature", "tint", "hue", "saturation", "vibrance",
            "contrast", "brightness", "shadows", "highlights",
            "whites", "blacks", "sharpen",
            "lift_r", "lift_g", "lift_b",
            "gamma_r", "gamma_g", "gamma_b",
            "gain_r", "gain_g", "gain_b",
        }
        slider_map = {
            "temperature": "temp", "hue": "hue", "saturation": "sat",
            "vibrance": "vib", "contrast": "contrast", "brightness": "bright",
            "shadows": "shadows", "highlights": "highlights",
            "whites": "whites", "blacks": "blacks", "sharpen": "sharpen",
        }
        for field, val in overrides.items():
            if field in slider_map and slider_map[field] in self._sliders:
                self._sliders[slider_map[field]].set(float(val))
            elif field.startswith(("lift_", "gamma_", "gain_")):
                # Color wheel fields — update wheel position via delta
                region, channel = field.split("_", 1)  # e.g. "lift", "r"
                if region in self._wheel_positions:
                    dx, dy = self._wheel_positions[region]
                    # Map channel shift to (dx, dy) delta (approximate)
                    shift = float(val) / 50.0  # ±50 units → ±1.0 normalised
                    if channel == "r":
                        dx = max(-1.0, min(1.0, shift))
                    elif channel == "g":
                        dy = max(-1.0, min(1.0, -shift))
                    elif channel == "b":
                        dx = max(-1.0, min(1.0, -shift * 0.5))
                        dy = max(-1.0, min(1.0, shift * 0.5))
                    self._wheel_positions[region] = (dx, dy)
                    self._update_wheel_indicator(region)

        self._draw_frame_at(self._current_frame)
        self._tb_status.configure(text=f"Preset '{name}' aplicado.")

    def _ed_timeline_section(self) -> None:
        """Build the 232px timeline section (row 2 of root)."""
        tl_outer = tk.Frame(self.root, bg=ED_TL_BG, bd=0)
        tl_outer.grid(row=2, column=0, sticky="nsew", padx=10, pady=(0, 10))
        tl_outer.grid_rowconfigure(1, weight=1)
        tl_outer.grid_columnconfigure(0, weight=1)

        # Timeline header bar
        hdr = tk.Frame(tl_outer, bg=ED_TL_BG)
        hdr.grid(row=0, column=0, sticky="ew", padx=8, pady=(4, 0))

        tk.Label(hdr, text="TIMELINE", bg=ED_TL_BG, fg=ED_TXT3,
                 font=("Segoe UI", 9, "bold")).pack(side="left")

        # Edit-mode pills: S=select  R=ripple  L=roll  P=slip  D=slide
        _mode_frame = tk.Frame(hdr, bg=ED_SURF, bd=0, highlightthickness=0)
        _mode_frame.pack(side="left", padx=(10, 0))
        for _mode_key, _mode_lbl, _mode_tip in TL_EDIT_MODES:
            _is_active = (_mode_key == self._tl_edit_mode)
            _mbtn = tk.Button(
                _mode_frame, text=_mode_lbl,
                bg=ED_ACC if _is_active else ED_SURF,
                fg=ED_TXT if _is_active else ED_TXT3,
                activebackground=ED_SURF2, activeforeground=ED_TXT,
                relief="flat", padx=7, pady=1,
                font=("Segoe UI", 8, "bold"), cursor="hand2", bd=0,
                highlightthickness=0,
                command=lambda mk=_mode_key: self._set_tl_edit_mode(mk),
            )
            _mbtn.pack(side="left")
            self._tl_edit_mode_btns[_mode_key] = _mbtn

        self._tl_info = tk.Label(hdr, text="", bg=ED_TL_BG, fg=ED_TXT3,
                                  font=("Segoe UI", 9))
        self._tl_info.pack(side="left", padx=12)

        # Zoom slider
        self._tl_zoom = ctk.CTkSlider(
            hdr, from_=TL_ZOOM_MIN, to=TL_ZOOM_MAX,
            number_of_steps=75, width=150,
            fg_color=ED_SURF, progress_color=ED_ACC, button_color=ED_ACC,
            command=self._on_timeline_zoom,
        )
        self._tl_zoom.set(TL_ZOOM_DEFAULT)
        self._tl_zoom.pack(side="right", padx=(8, 0))

        def _hdr_btn(text, cmd):
            return tk.Button(
                hdr, text=text, command=cmd,
                bg=ED_SURF, fg=ED_TXT2,
                activebackground=ED_SURF2, activeforeground=ED_TXT,
                relief="flat", padx=7, pady=2,
                font=("Segoe UI", 9), cursor="hand2", bd=0,
                highlightthickness=0)

        _hdr_btn("Ver tudo", lambda: self._set_timeline_zoom(
            TL_ZOOM_MIN, reset_view=True)).pack(side="right", padx=(4, 0))
        _hdr_btn(">", lambda: self._pan_timeline_view(1)).pack(side="right", padx=(4, 0))
        _hdr_btn("<", lambda: self._pan_timeline_view(-1)).pack(side="right", padx=(4, 0))
        _hdr_btn("+", lambda: self._adjust_timeline_zoom(0.25)).pack(side="right", padx=(4, 0))
        _hdr_btn("-", lambda: self._adjust_timeline_zoom(-0.25)).pack(side="right", padx=(4, 0))
        _hdr_btn("Dividir", self._split_selected_clip).pack(side="right", padx=(8, 0))
        _hdr_btn("Excluir", self._delete_selected_clip).pack(side="right", padx=(4, 0))
        _hdr_btn("Duplicar", self._duplicate_selected_timeline_item).pack(side="right", padx=(4, 0))
        _hdr_btn("Refazer", self._redo_timeline_action).pack(side="right", padx=(4, 0))
        _hdr_btn("Desfazer", self._undo_timeline_action).pack(side="right", padx=(4, 0))

        tk.Checkbutton(
            hdr, text="Juntar", variable=self._tl_compact_var,
            command=self._redraw_timeline,
            bg=ED_TL_BG, fg=ED_TXT2, selectcolor=ED_SURF,
            activebackground=ED_TL_BG, activeforeground=ED_TXT,
            font=("Segoe UI", 9), relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Checkbutton(
            hdr, text="Visual", variable=self._track_visual_visible_var,
            command=self._on_track_control_changed,
            bg=ED_TL_BG, fg=ED_TXT2, selectcolor=ED_SURF,
            activebackground=ED_TL_BG, activeforeground=ED_TXT,
            font=("Segoe UI", 9), relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Checkbutton(
            hdr, text="Texto", variable=self._track_text_visible_var,
            command=self._on_track_control_changed,
            bg=ED_TL_BG, fg=ED_TXT2, selectcolor=ED_SURF,
            activebackground=ED_TL_BG, activeforeground=ED_TXT,
            font=("Segoe UI", 9), relief="flat",
        ).pack(side="right", padx=(8, 0))
        tk.Checkbutton(
            hdr, text="Mudo", variable=self._track_audio_muted_var,
            command=self._on_track_control_changed,
            bg=ED_TL_BG, fg=ED_TXT2, selectcolor=ED_SURF,
            activebackground=ED_TL_BG, activeforeground=ED_TXT,
            font=("Segoe UI", 9), relief="flat",
        ).pack(side="right", padx=(8, 0))

        # Timeline canvas
        self._tl_canvas = tk.Canvas(
            tl_outer, bg=ED_TL_BG, height=190,
            highlightthickness=0, cursor="hand2")
        self._tl_canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2, 4))
        self._tl_canvas.bind("<Configure>",        lambda e: self._redraw_timeline())
        self._tl_canvas.bind("<ButtonPress-1>",    self._tl_press)
        self._tl_canvas.bind("<B1-Motion>",        self._tl_drag_motion)
        self._tl_canvas.bind("<ButtonRelease-1>",  self._tl_release)
        self._tl_canvas.bind("<Motion>",           self._tl_motion)
        self._tl_canvas.bind("<Leave>",            self._tl_leave)
        self._tl_canvas.bind("<MouseWheel>",       self._tl_mousewheel)
        self._tl_canvas.bind("<Shift-MouseWheel>", self._tl_shift_mousewheel)
        self._tl_canvas.bind("<Button-3>",         self._tl_right_click)
        self._tl_canvas.bind("<Double-ButtonPress-1>", self._tl_double_click)

        # Mini-map: thin overview of full timeline
        self._tl_minimap = tk.Canvas(
            tl_outer,  # same parent as the main timeline
            height=18,
            bg="#0a0910",
            highlightthickness=0,
        )
        self._tl_minimap.grid(row=2, column=0, sticky="ew", padx=4, pady=(0, 2))
        self._tl_minimap.bind("<ButtonPress-1>", self._on_minimap_press)
        self._tl_minimap.bind("<B1-Motion>", self._on_minimap_drag)

        self._tl_playhead = None
        self._redraw_timeline()

    def _add_composition_video_track(self) -> None:
        """Add a new video track to the composition."""
        if self._composition is None:
            self._tb_status.configure(text="Abra um projeto antes de adicionar faixas.")
            return
        track = self._composition.add_video_track()
        # Also add to legacy TimelineModel for compatibility
        if self._timeline_model is not None:
            new_tt = TimelineTrack(name=track.name)
            self._timeline_model.extra_overlay_tracks.append(new_tt)
        self._redraw_timeline()
        self._tb_status.configure(text=f"Faixa de vídeo adicionada: {track.name}")

    def _add_composition_audio_track(self) -> None:
        """Add a new audio track to the composition."""
        if self._composition is None:
            self._tb_status.configure(text="Abra um projeto antes de adicionar faixas.")
            return
        track = self._composition.add_audio_track()
        self._redraw_timeline()
        self._tb_status.configure(text=f"Faixa de áudio adicionada: {track.name}")

    def _remove_composition_track(self, track_id: str) -> None:
        """Remove a track from the composition by ID."""
        if self._composition is None:
            return
        track = self._composition.track_by_id(track_id)
        if track is None:
            return
        if not self._composition.remove_track(track_id):
            return
        self._redraw_timeline()
        self._tb_status.configure(text=f"Faixa removida: {track.name}")

    def _on_track_control_changed(self) -> None:
        if self._track_audio_muted_var.get():
            self._stop_preview_audio()
            self._play_audio_started = False
        self._redraw_timeline()
        if self.video_path:
            self._draw_frame_at(self._current_frame, fast=True)
        self._tb_status.configure(text=_track_control_status(
            self._track_visual_visible_var.get(),
            self._track_text_visible_var.get(),
            self._track_audio_muted_var.get(),
        ))
        self._save_project_state()

    def _apply_track_options_from_metadata(self, metadata: dict[str, object]) -> None:
        options = _track_options_from_metadata(metadata)
        self._track_visual_visible_var.set(options["visual_visible"])
        self._track_text_visible_var.set(options["text_visible"])
        self._track_audio_muted_var.set(options["audio_muted"])

    def _tl_press(self, event: tk.Event) -> str | None:
        if self._duration_s <= 0:
            return None
        # ── Etapa C: fade handle drag ─────────────────────────────────────────
        fade_hit = self._fade_handle_at(event.x, event.y)
        if fade_hit is not None:
            self._stop_playback(reset_button=True)
            clip_idx, edge = fade_hit
            clips = self._timeline_model.video_track.clips
            orig_fade = float(getattr(clips[clip_idx], f"fade_{'in' if edge == 'in' else 'out'}_s", 0.0))
            self._fade_drag = (clip_idx, edge, event.x, orig_fade)
            self._redraw_timeline()
            self._tb_status.configure(text=f"Ajustando fade {'entrada' if edge == 'in' else 'saída'}...")
            return "break"
        text_handle = self._text_trim_handle_at(event.x, event.y)
        if text_handle is not None:
            self._stop_playback(reset_button=True)
            self._selected_text_index, edge = text_handle
            self._selected_clip_index = None
            self._selected_overlay_index = None
            self._text_trim_drag = (self._selected_text_index, edge)
            self._hover_text_trim_handle = text_handle
            self._trim_undo_captured = False
            self._redraw_timeline()
            self._refresh_clip_inspector()
            self._tb_status.configure(text=f"Ajustando texto: {_trim_edge_label(edge)}...")
            return "break"
        text_body = self._text_clip_body_at(event.x, event.y)
        if text_body is not None:
            self._stop_playback(reset_button=True)
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            text_clips = _timeline_text_clips(self._timeline_model)
            self._selected_text_index = text_body
            self._selected_clip_index = None
            self._selected_overlay_index = None
            self._text_move_drag = (text_body, time_s - text_clips[text_body].start_s)
            self._trim_undo_captured = False
            self._seek_to(self._time_to_frame(time_s))
            self._redraw_timeline()
            self._refresh_clip_inspector()
            self._tb_status.configure(text=f"Movendo Texto {text_body + 1}...")
            return "break"
        overlay_handle = self._overlay_trim_handle_at(event.x, event.y)
        if overlay_handle is not None:
            self._stop_playback(reset_button=True)
            self._selected_overlay_index, edge = overlay_handle
            self._selected_clip_index = None
            self._selected_text_index = None
            self._overlay_trim_drag = (self._selected_overlay_index, edge)
            self._hover_overlay_trim_handle = overlay_handle
            self._trim_undo_captured = False
            self._redraw_timeline()
            self._refresh_clip_inspector()
            self._tb_status.configure(text=f"Ajustando midia: {_trim_edge_label(edge)}...")
            return "break"
        handle = self._trim_handle_at(event.x, event.y)
        if handle is not None:
            self._stop_playback(reset_button=True)
            self._selected_clip_index, edge = handle
            self._selected_text_index = None
            self._selected_overlay_index = None
            self._trim_drag = (self._selected_clip_index, edge)
            self._hover_trim_handle = handle
            self._trim_undo_captured = False
            self._redraw_timeline()
            self._tb_status.configure(text=f"Ajustando {_trim_edge_label(edge)}...")
            return "break"
        media_body = self._media_clip_body_at(event.x, event.y)
        if media_body is not None:
            self._stop_playback(reset_button=True)
            clips = _timeline_overlay_clips(self._timeline_model)
            moving = _clone_timeline_clip(clips[media_body])
            base_clips = [_clone_timeline_clip(clip) for idx, clip in enumerate(clips) if idx != media_body]
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            self._selected_overlay_index = media_body
            self._selected_clip_index = None
            self._selected_text_index = None
            self._clip_move_drag = (time_s - moving.start_s, moving, base_clips)
            self._trim_undo_captured = False
            self._seek_to(self._time_to_frame(time_s))
            self._redraw_timeline()
            self._refresh_clip_inspector()
            self._tb_status.configure(text=f"Movendo midia: {Path(moving.source_path).name}.")
            return "break"
        # Shift+click: add/remove individual clip from multi-selection
        if event.state & 0x0001:  # Shift held
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            idx = self._clip_index_at_time(time_s)
            if idx is not None:
                if idx in self._selected_clip_indices:
                    self._selected_clip_indices.discard(idx)
                else:
                    self._selected_clip_indices.add(idx)
                    self._selected_clip_index = idx
                self._redraw_timeline()
                self._refresh_clip_inspector()
                self._tb_status.configure(text=f"{len(self._selected_clip_indices)} clipes selecionados.")
                return "break"
        self._tl_click(event)
        return None

    def _tl_click(self, event: tk.Event) -> None:
        w   = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        time_s = self._timeline_click_time(event.x, track_x1, track_x2)
        time_s, snapped = self._snap_time_to_clip_edge(time_s)
        frame = self._time_to_frame(time_s)
        lanes = _timeline_lane_layout(self._tl_canvas.winfo_height(), n_overlay_tracks=self._n_overlay_tracks())
        text_y1, text_y2 = lanes["text"]
        video_y1, video_y2 = lanes["video"]
        audio_y1, audio_y2 = lanes["audio"]
        in_any_overlay = any(
            _timeline_y_in_lane(int(event.y), v[0], v[1])
            for k, v in lanes.items()
            if k.startswith("overlay_")
        )
        if _timeline_y_in_lane(int(event.y), text_y1, text_y2):
            self._select_text_at_time(time_s)
        elif in_any_overlay:
            # Only select overlay if there's actually a clip at that time;
            # empty overlay lane click = seek only (deselect overlay)
            if self._timeline_model and _cycle_active_clip_index(
                _timeline_overlay_clips(self._timeline_model), time_s,
                self._selected_overlay_index
            ) is not None:
                self._select_overlay_at_time(time_s)
            else:
                if self._selected_overlay_index is not None:
                    self._selected_overlay_index = None
                    self._redraw_timeline()
                    self._refresh_clip_inspector()
        elif _timeline_y_in_lane(int(event.y), video_y1, video_y2):
            self._select_clip_at_time(time_s)
        elif _timeline_y_in_lane(int(event.y), audio_y1, audio_y2):
            self._tb_status.configure(text="Faixa de audio: use a faixa de video para mover ou ajustar o clipe.")
        self._seek_to(frame)
        if snapped:
            self._tb_status.configure(text=f"Playhead encaixado em {_fmt(time_s)}.")

    def _timeline_time_from_event(self, event: tk.Event) -> float:
        try:
            x = int(getattr(event, "x"))
        except (AttributeError, TypeError, ValueError):
            track_x1, track_x2 = self._timeline_track_bounds(self._tl_canvas.winfo_width())
            x = _timeline_time_to_x(self._current_frame / max(1.0, self._fps), self._duration_s, track_x1, track_x2)
        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        return self._timeline_click_time(x, track_x1, track_x2)

    def _tl_drag_motion(self, event: tk.Event) -> str | None:
        if not self._timeline_model:
            return None
        # ── Etapa C: fade handle drag ─────────────────────────────────────────
        if self._fade_drag is not None:
            clip_idx, edge, origin_x, orig_fade = self._fade_drag
            clips = self._timeline_model.video_track.clips
            if 0 <= clip_idx < len(clips):
                clip = clips[clip_idx]
                clip_dur = max(0.01, clip.end_s - clip.start_s)
                w = self._tl_canvas.winfo_width()
                track_x1, track_x2 = self._timeline_track_bounds(w)
                track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
                px_per_s = (track_x2 - track_x1) / max(0.01, self._duration_s)
                dx = event.x - origin_x
                delta_s = dx / max(1.0, px_per_s) * (1.0 if edge == "in" else -1.0)
                new_fade = max(0.0, min(clip_dur * 0.9, orig_fade + delta_s))
                if edge == "in":
                    clip.fade_in_s = new_fade
                    self._timeline_model.audio_track.clips[clip_idx].fade_in_s = new_fade
                else:
                    clip.fade_out_s = new_fade
                    self._timeline_model.audio_track.clips[clip_idx].fade_out_s = new_fade
                self._redraw_timeline()
                self._tb_status.configure(text=f"Fade {'entrada' if edge == 'in' else 'saída'}: {new_fade:.2f}s")
            return "break"
        if self._clip_move_drag:
            offset_s, moving, base_clips = self._clip_move_drag
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            duration_s = max(self._trim_min_duration_s, moving.end_s - moving.start_s)
            (new_start, new_end), snapped = _move_clip_bounds_with_snap(
                moving.start_s,
                moving.end_s,
                time_s - offset_s,
                self._duration_s,
                _clip_edges(self._timeline_model.video_track.clips),
                self._snap_threshold_s(),
            )
            inserted = _clone_timeline_clip(moving)
            inserted.start_s = new_start
            inserted.end_s = new_start + duration_s if new_end <= new_start else new_end
            new_clips = [*base_clips, inserted]
            new_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
            selected_index = new_clips.index(inserted)
            if not self._trim_undo_captured:
                self._push_timeline_undo(label="mover overlay")
                self._trim_undo_captured = True
            self._timeline_model.overlay_track.clips = new_clips
            self._selected_overlay_index = selected_index
            self._selected_clip_index = None
            self._selected_text_index = None
            self._sync_manual_timeline(mark_dirty=True)
            self._timeline_dirty = True
            self._seek_to(self._time_to_frame(inserted.start_s))
            self._refresh_clip_inspector()
            snap_note = " | snap" if snapped else ""
            self._tb_status.configure(text=f"Midia movida: {_fmt(inserted.start_s)} - {_fmt(inserted.end_s)}.{snap_note}")
            return "break"
        if self._overlay_trim_drag:
            index, edge = self._overlay_trim_drag
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if index >= len(overlay_clips):
                return "break"
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            time_s, snapped = self._snap_time_to_clip_edge(time_s)
            new_start, new_end = _trim_clip_bounds(
                overlay_clips,
                index,
                edge,
                time_s,
                self._duration_s,
                self._trim_min_duration_s,
                clamp_to_neighbors=False,
            )
            clip = overlay_clips[index]
            if not _trim_bounds_changed(clip.start_s, clip.end_s, new_start, new_end):
                return "break"
            if not self._trim_undo_captured:
                self._push_timeline_undo(label="ajustar borda")
                self._trim_undo_captured = True
            clip.start_s = new_start
            clip.end_s = new_end
            self._selected_overlay_index = index
            self._selected_clip_index = None
            self._selected_text_index = None
            self._sync_manual_timeline(mark_dirty=True)
            self._timeline_dirty = True
            self._seek_to(self._time_to_frame(new_start if edge == "start" else new_end))
            self._refresh_clip_inspector()
            snap_note = " | snap" if snapped else ""
            self._tb_status.configure(text=f"Midia ajustada: {_fmt(new_start)} - {_fmt(new_end)}.{snap_note}")
            return "break"
        if self._text_move_drag:
            index, offset_s = self._text_move_drag
            text_clips = _timeline_text_clips(self._timeline_model)
            if index >= len(text_clips):
                return "break"
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            clip = text_clips[index]
            (new_start, new_end), snapped = _move_clip_bounds_with_snap(
                clip.start_s,
                clip.end_s,
                time_s - offset_s,
                self._duration_s,
                _clip_edges(self._timeline_model.video_track.clips),
                self._snap_threshold_s(),
            )
            if not _trim_bounds_changed(clip.start_s, clip.end_s, new_start, new_end):
                return "break"
            if not self._trim_undo_captured:
                self._push_timeline_undo(label="mover texto")
                _clear_video_text_overlay_for_text_clip(self._timeline_model, clip)
                self._trim_undo_captured = True
            clip.start_s = new_start
            clip.end_s = new_end
            self._selected_text_index = index
            self._selected_clip_index = None
            _sync_text_clip_to_video_overlay(self._timeline_model, clip)
            self._sync_manual_timeline(mark_dirty=True)
            self._timeline_dirty = True
            self._seek_to(self._time_to_frame(new_start))
            self._refresh_clip_inspector()
            snap_note = " | snap" if snapped else ""
            self._tb_status.configure(text=f"Texto movido: {_fmt(new_start)} - {_fmt(new_end)}.{snap_note}")
            return "break"
        if self._text_trim_drag:
            index, edge = self._text_trim_drag
            text_clips = _timeline_text_clips(self._timeline_model)
            if index >= len(text_clips):
                return "break"
            w = self._tl_canvas.winfo_width()
            track_x1, track_x2 = self._timeline_track_bounds(w)
            time_s = self._timeline_click_time(event.x, track_x1, track_x2)
            time_s, snapped = self._snap_time_to_clip_edge(time_s)
            new_start, new_end = _trim_clip_bounds(
                text_clips,
                index,
                edge,
                time_s,
                self._duration_s,
                self._trim_min_duration_s,
                clamp_to_neighbors=False,
            )
            clip = text_clips[index]
            if not _trim_bounds_changed(clip.start_s, clip.end_s, new_start, new_end):
                return "break"
            if not self._trim_undo_captured:
                self._push_timeline_undo(label="ajustar borda")
                self._trim_undo_captured = True
            clip.start_s = new_start
            clip.end_s = new_end
            self._selected_text_index = index
            self._selected_clip_index = None
            _sync_text_clip_to_video_overlay(self._timeline_model, clip)
            self._sync_manual_timeline(mark_dirty=True)
            self._timeline_dirty = True
            self._seek_to(self._time_to_frame(new_start if edge == "start" else new_end))
            self._refresh_clip_inspector()
            snap_note = " | snap" if snapped else ""
            self._tb_status.configure(text=f"Texto ajustado: {_fmt(new_start)} - {_fmt(new_end)}.{snap_note}")
            return "break"
        if not self._trim_drag:
            return None
        index, edge = self._trim_drag
        if index >= len(self._timeline_model.video_track.clips):
            return "break"
        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        time_s = self._timeline_click_time(event.x, track_x1, track_x2)
        time_s, snapped = self._snap_time_to_clip_edge(time_s)
        clips = self._timeline_model.video_track.clips
        new_start, new_end = _trim_clip_bounds(
            clips,
            index,
            edge,
            time_s,
            self._duration_s,
            self._trim_min_duration_s,
        )
        clip = clips[index]
        if not _trim_bounds_changed(clip.start_s, clip.end_s, new_start, new_end):
            return "break"
        if not self._trim_undo_captured:
            self._push_timeline_undo(label="ajustar borda")
            self._trim_undo_captured = True
        # Ripple: compute delta before applying and shift subsequent clips
        if self._tl_edit_mode == "ripple":
            if edge == "end":
                delta_s = new_end - clip.end_s
                clip.start_s = new_start
                clip.end_s = new_end
                for future_clip in clips[index + 1:]:
                    future_clip.start_s = max(0.0, future_clip.start_s + delta_s)
                    future_clip.end_s   = max(future_clip.start_s + self._trim_min_duration_s,
                                              future_clip.end_s + delta_s)
            else:  # "start"
                delta_s = new_start - clip.start_s
                clip.start_s = new_start
                clip.end_s = new_end
                for prev_clip in clips[:index]:
                    prev_clip.start_s = max(0.0, prev_clip.start_s + delta_s)
                    prev_clip.end_s   = max(prev_clip.start_s + self._trim_min_duration_s,
                                            prev_clip.end_s + delta_s)
        elif self._tl_edit_mode == "roll":
            # Roll: move the cut point between this clip and next/prev,
            # keeping total duration constant (one side grows, other shrinks)
            if edge == "end" and index + 1 < len(clips):
                delta_s = new_end - clip.end_s
                next_clip = clips[index + 1]
                new_next_start = _clamp_float(
                    next_clip.start_s + delta_s,
                    clip.start_s + self._trim_min_duration_s,
                    next_clip.end_s - self._trim_min_duration_s,
                )
                clip.end_s = new_next_start
                clip.start_s = new_start
                next_clip.start_s = new_next_start
            elif edge == "start" and index > 0:
                delta_s = new_start - clip.start_s
                prev_clip = clips[index - 1]
                new_prev_end = _clamp_float(
                    prev_clip.end_s + delta_s,
                    prev_clip.start_s + self._trim_min_duration_s,
                    clip.end_s - self._trim_min_duration_s,
                )
                prev_clip.end_s = new_prev_end
                clip.start_s = new_prev_end
                clip.end_s = new_end
            else:
                clip.start_s = new_start
                clip.end_s = new_end
        elif self._tl_edit_mode == "slip":
            # Slip: keep clip position in timeline, shift source content window.
            # Both start_s and end_s shift together (same duration) clamped to [0, duration_s].
            delta_s = new_start - clip.start_s
            dur = clip.end_s - clip.start_s
            slipped_start = _clamp_float(clip.start_s + delta_s, 0.0, self._duration_s - dur)
            clip.start_s = slipped_start
            clip.end_s = slipped_start + dur
        elif self._tl_edit_mode == "slide":
            # Slide: move clip while adjusting neighbors to fill/close gap.
            delta_s = new_start - clip.start_s
            clip.start_s = new_start
            clip.end_s = new_end
            if index > 0:
                prev_clip = clips[index - 1]
                prev_clip.end_s = _clamp_float(
                    prev_clip.end_s + delta_s,
                    prev_clip.start_s + self._trim_min_duration_s,
                    self._duration_s,
                )
            if index + 1 < len(clips):
                next_clip = clips[index + 1]
                next_clip.start_s = _clamp_float(
                    next_clip.start_s + delta_s,
                    0.0,
                    next_clip.end_s - self._trim_min_duration_s,
                )
        else:  # select mode — plain trim
            clip.start_s = new_start
            clip.end_s = new_end
        self._selected_clip_index = index
        self._sync_manual_timeline(mark_dirty=True)
        self._timeline_dirty = True
        self._seek_to(self._time_to_frame(new_start if edge == "start" else new_end))
        snap_note = " | snap" if snapped else ""
        mode_note = {"ripple": " | ripple", "roll": " | roll", "slip": " | slip", "slide": " | slide"}.get(self._tl_edit_mode, "")
        self._tb_status.configure(text=f"Corte ajustado: {_fmt(new_start)} - {_fmt(new_end)}.{snap_note}{mode_note}")
        return "break"

    def _tl_release(self, event: tk.Event) -> str | None:
        if self._media_drag_path:
            return self._media_listbox_release(event)
        # ── Etapa C: fade drag release ────────────────────────────────────────
        if self._fade_drag is not None:
            clip_idx, edge, _, _ = self._fade_drag
            self._fade_drag = None
            self._tl_canvas.configure(cursor="hand2")
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._save_project_state()
            self._refresh_clip_inspector()
            self._redraw_timeline()
            self._tb_status.configure(text="Fade ajustado.")
            return "break"
        if not self._trim_drag and not self._text_trim_drag and not self._overlay_trim_drag and not self._text_move_drag and not self._clip_move_drag:
            return None
        changed = self._trim_undo_captured
        self._trim_drag = None
        self._hover_trim_handle = None
        self._text_trim_drag = None
        self._hover_text_trim_handle = None
        self._overlay_trim_drag = None
        self._hover_overlay_trim_handle = None
        self._text_move_drag = None
        self._clip_move_drag = None
        self._trim_undo_captured = False
        self._tl_canvas.configure(cursor="hand2")
        self._redraw_timeline()
        self._tb_status.configure(text="Elemento ajustado." if changed else "Elemento mantido.")
        return "break"

    def _tl_motion(self, event: tk.Event) -> None:
        if self._text_move_drag or self._clip_move_drag:
            self._tl_canvas.configure(cursor="fleur")
            return
        if self._fade_drag is not None or self._trim_drag or self._text_trim_drag or self._overlay_trim_drag:
            self._tl_canvas.configure(cursor="sb_h_double_arrow")
            return
        fade_hit = self._fade_handle_at(event.x, event.y)
        if fade_hit:
            self._tl_canvas.configure(cursor="sb_h_double_arrow")
            return
        text_handle = self._text_trim_handle_at(event.x, event.y)
        if text_handle != self._hover_text_trim_handle:
            self._hover_text_trim_handle = text_handle
            self._redraw_timeline()
        if text_handle:
            idx, edge = text_handle
            self._tb_status.configure(text=f"Arraste a {_trim_edge_label(edge)} do Texto {idx + 1}.")
            self._tl_canvas.configure(cursor="sb_h_double_arrow")
            return
        overlay_handle = self._overlay_trim_handle_at(event.x, event.y)
        if overlay_handle != self._hover_overlay_trim_handle:
            self._hover_overlay_trim_handle = overlay_handle
            self._redraw_timeline()
        if overlay_handle:
            idx, edge = overlay_handle
            self._tb_status.configure(text=f"Arraste a {_trim_edge_label(edge)} da Midia {idx + 1}.")
            self._tl_canvas.configure(cursor="sb_h_double_arrow")
            return
        handle = self._trim_handle_at(event.x, event.y)
        if handle != self._hover_trim_handle:
            self._hover_trim_handle = handle
            self._redraw_timeline()
        if handle:
            idx, edge = handle
            self._tb_status.configure(text=f"Arraste a {_trim_edge_label(edge)} do Clip {idx + 1}.")
        cursor = "sb_h_double_arrow" if handle else "hand2"
        self._tl_canvas.configure(cursor=cursor)
        # Hover thumbnail on base video lane
        if self._timeline_model and not self._playing:
            ch = self._tl_canvas.winfo_height()
            lanes = _timeline_lane_layout(ch, n_overlay_tracks=1 + len(getattr(self._timeline_model, 'extra_overlay_tracks', [])))
            video_y1, video_y2 = lanes["video"]
            if video_y1 <= event.y <= video_y2:
                w2 = self._tl_canvas.winfo_width()
                track_x1, track_x2 = self._timeline_track_bounds(w2)
                time_s = self._timeline_click_time(event.x, track_x1, track_x2)
                if abs((self._tl_hover_time_s or -999) - time_s) > 0.5:
                    self._tl_hover_time_s = time_s
                    self.root.after(150, lambda t=time_s: self._show_tl_hover_thumb(t))
            else:
                self._hide_tl_hover_thumb()

    def _tl_leave(self, event: tk.Event) -> None:
        self._tl_canvas.configure(cursor="hand2")
        if self._hover_trim_handle and not self._trim_drag:
            self._hover_trim_handle = None
            self._redraw_timeline()
        if self._hover_text_trim_handle and not self._text_trim_drag:
            self._hover_text_trim_handle = None
            self._redraw_timeline()
        if self._hover_overlay_trim_handle and not self._overlay_trim_drag:
            self._hover_overlay_trim_handle = None
            self._redraw_timeline()
        self._hide_tl_hover_thumb()

    # -- Properties panel ------------------------------------------------------

    def _on_timeline_zoom(self, value: float) -> None:
        self._waveform_zoom = float(value)
        if self._waveform_zoom <= 1.001:
            self._timeline_view_center_s = None
        elif self._timeline_view_center_s is None:
            self._timeline_view_center_s = self._timeline_display_playhead_time()
        self._redraw_timeline()
        self._refresh_project_status()

    def _adjust_timeline_zoom(self, delta: float) -> None:
        value = _timeline_zoom_step(float(self._tl_zoom.get()), float(delta))
        self._set_timeline_zoom(value)

    def _set_timeline_zoom(self, value: float, reset_view: bool = False) -> None:
        if reset_view:
            self._timeline_view_center_s = None
        self._tl_zoom.set(value)
        self._on_timeline_zoom(value)

    def _tl_mousewheel(self, event: tk.Event) -> str | None:
        if int(getattr(event, "state", 0)) & 0x0004:
            self._adjust_timeline_zoom(0.25 if int(getattr(event, "delta", 0)) > 0 else -0.25)
            return "break"
        return None

    def _tl_shift_mousewheel(self, event: tk.Event) -> str:
        direction = -1 if int(getattr(event, "delta", 0)) > 0 else 1
        self._pan_timeline_view(direction)
        return "break"

    def _pan_timeline_view(self, direction: int) -> None:
        if self._waveform_zoom <= 1.001:
            self._tb_status.configure(text="Aumente o zoom da timeline para deslocar a janela.")
            return
        view_duration = self._timeline_display_duration()
        self._timeline_view_center_s = _timeline_pan_center(
            self._timeline_view_center_s if self._timeline_view_center_s is not None else self._timeline_display_playhead_time(),
            view_duration,
            self._waveform_zoom,
            direction,
        )
        self._redraw_timeline()
        self._refresh_project_status()
        self._tb_status.configure(text=f"Timeline deslocada para {_fmt(self._timeline_view_center_s)}.")

    def _timeline_zoomed_bounds(self, x1: int, x2: int) -> tuple[int, int]:
        return x1, x2

    def _timeline_view_window(self, view_duration: float) -> tuple[float, float]:
        center_s = self._timeline_view_center_s
        if center_s is None:
            center_s = self._timeline_display_playhead_time()
        return _timeline_zoom_window(view_duration, self._waveform_zoom, center_s)

    def _timeline_display_duration(self) -> float:
        if self._timeline_compact_enabled():
            compact_ranges = self._compact_ranges_for_view()
            if compact_ranges:
                return compact_ranges[-1][3]
        return self._duration_s

    def _timeline_display_playhead_time(self) -> float:
        playhead_s = self._current_frame / max(1.0, self._fps)
        if self._timeline_compact_enabled():
            compact_ranges = self._compact_ranges_for_view()
            if compact_ranges:
                return _compact_source_to_display_time(playhead_s, compact_ranges)
        return playhead_s

    def _select_clip_at_time(self, time_s: float) -> None:
        self._selected_clip_indices.clear()
        self._selected_clip_index = self._clip_index_at_time(time_s)
        self._selected_text_index = None
        self._selected_overlay_index = None
        self._redraw_timeline()
        self._refresh_clip_inspector()

    def _select_text_at_time(self, time_s: float) -> None:
        self._selected_text_index = _cycle_active_clip_index(
            _timeline_text_clips(self._timeline_model),
            time_s,
            self._selected_text_index,
        ) if self._timeline_model else None
        self._selected_clip_index = None
        self._selected_overlay_index = None
        self._redraw_timeline()
        self._refresh_clip_inspector()

    def _select_overlay_at_time(self, time_s: float) -> None:
        self._selected_overlay_index = _cycle_active_clip_index(
            _timeline_overlay_clips(self._timeline_model),
            time_s,
            self._selected_overlay_index,
        ) if self._timeline_model else None
        self._selected_clip_index = None
        self._selected_text_index = None
        self._redraw_timeline()
        self._refresh_clip_inspector()

    def _split_selected_clip(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um vídeo antes de dividir.")
            return
        self._stop_playback(reset_button=True)
        split_s = self._current_frame / max(1.0, self._fps)
        if self._selected_overlay_index is not None:
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if 0 <= self._selected_overlay_index < len(overlay_clips):
                pieces = _split_clip_at_time(overlay_clips[self._selected_overlay_index], split_s, self._trim_min_duration_s)
                if pieces is None:
                    self._tb_status.configure(text="Posicione o playhead dentro do overlay para dividir.")
                    return
                self._push_timeline_undo(label="dividir clipe")
                overlay_clips[self._selected_overlay_index:self._selected_overlay_index + 1] = list(pieces)
                self._selected_overlay_index += 1
                self._selected_clip_index = None
                self._selected_text_index = None
                self._timeline_dirty = True
                self._sync_manual_timeline(mark_dirty=True)
                self._refresh_clip_inspector()
                self._redraw_timeline()
                self._tb_status.configure(text=f"Overlay dividido em {_fmt(split_s)}.")
                return
        if self._selected_text_index is not None:
            text_clips = _timeline_text_clips(self._timeline_model)
            if 0 <= self._selected_text_index < len(text_clips):
                source = text_clips[self._selected_text_index]
                pieces = _split_clip_at_time(source, split_s, self._trim_min_duration_s)
                if pieces is None:
                    self._tb_status.configure(text="Posicione o playhead dentro do texto para dividir.")
                    return
                self._push_timeline_undo(label="dividir clipe")
                _clear_video_text_overlay_for_text_clip(self._timeline_model, source)
                text_clips[self._selected_text_index:self._selected_text_index + 1] = list(pieces)
                self._selected_text_index += 1
                self._selected_clip_index = None
                self._selected_overlay_index = None
                _sync_text_clip_to_video_overlay(self._timeline_model, text_clips[self._selected_text_index])
                self._timeline_dirty = True
                self._sync_manual_timeline(mark_dirty=True)
                self._refresh_clip_inspector()
                self._redraw_timeline()
                self._tb_status.configure(text=f"Texto dividido em {_fmt(split_s)}.")
                return
        index = self._clip_index_at_time(split_s)
        if index is None:
            self._tb_status.configure(text="Posicione o playhead dentro de um clipe para dividir.")
            self._selected_clip_index = None
            self._redraw_timeline()
            return
        self._selected_clip_index = index
        clip = self._timeline_model.video_track.clips[self._selected_clip_index]
        if split_s <= clip.start_s + 0.15 or split_s >= clip.end_s - 0.15:
            self._tb_status.configure(text="Posicione o playhead dentro do clipe para dividir.")
            return False
        self._push_timeline_undo(label="dividir clipe")
        clips = self._timeline_model.video_track.clips
        left_clip = _clone_timeline_clip(clip)
        left_clip.end_s = split_s
        right_clip = _clone_timeline_clip(clip)
        right_clip.start_s = split_s
        right_clip.label = f"{clip.label} 2"
        clips[self._selected_clip_index:self._selected_clip_index + 1] = [
            left_clip,
            right_clip,
        ]
        self._sync_manual_timeline()
        self._timeline_dirty = True
        self._selected_clip_index += 1
        self._seek_to(self._current_frame)
        self._tb_status.configure(text=f"Clipe dividido em {_fmt(split_s)}.")

    def _delete_selected_clip(self) -> None:
        if self._timeline_model and self._selected_overlay_index is not None:
            self._push_timeline_undo(label="apagar clipe")
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if 0 <= self._selected_overlay_index < len(overlay_clips):
                del overlay_clips[self._selected_overlay_index]
            self._selected_overlay_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._refresh_clip_inspector()
            self._tb_status.configure(text="Overlay removido sem cortar o video base.")
            return
        if self._timeline_model and self._selected_text_index is not None:
            self._push_timeline_undo(label="apagar clipe")
            text_clips = _timeline_text_clips(self._timeline_model)
            if 0 <= self._selected_text_index < len(text_clips):
                _clear_video_text_overlay_for_text_clip(self._timeline_model, text_clips[self._selected_text_index])
                del text_clips[self._selected_text_index]
            self._selected_text_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._tb_status.configure(text="Texto removido da timeline.")
            return
        if not self._timeline_model or self._selected_clip_index is None:
            self._tb_status.configure(text="Selecione um clipe na timeline para excluir.")
            return
        self._stop_playback(reset_button=True)
        current_time = self._current_frame / max(1.0, self._fps)
        self._push_timeline_undo(label="apagar clipe")
        del self._timeline_model.video_track.clips[self._selected_clip_index]
        self._selected_clip_index = None
        self._sync_manual_timeline()
        self._timeline_dirty = True
        self._seek_to(self._time_to_frame(self._nearest_kept_time(current_time)))
        self._tb_status.configure(text="Clipe removido da timeline.")
        self._refresh_clip_inspector()

    def _move_selected_layer(self, direction: int) -> bool:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um video antes de ordenar camadas.")
            return False
        step = 1 if int(direction) > 0 else -1
        if self._selected_overlay_index is not None:
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if not 0 <= self._selected_overlay_index < len(overlay_clips):
                self._tb_status.configure(text="Selecione um overlay para mudar a camada.")
                return False
            self._push_timeline_undo(label="mover overlay")
            clip = overlay_clips[self._selected_overlay_index]
            clip.z_order = getattr(clip, "z_order", 0) + step
            self._selected_clip_index = None
            self._selected_text_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._refresh_clip_inspector()
            self._redraw_timeline()
            self._draw_frame_at(self._current_frame, fast=True)
            label = "Frente" if step > 0 else "Trás"
            self._tb_status.configure(text=f"Overlay movido para {label} (z={clip.z_order}).")
            return True
        if self._selected_text_index is not None:
            text_clips = _timeline_text_clips(self._timeline_model)
            if not _can_move_track_item_layer(len(text_clips), self._selected_text_index, direction):
                self._tb_status.configure(text="Texto ja esta no limite dessa camada.")
                return False
            self._push_timeline_undo(label="mover texto")
            moved, new_index = _move_track_item_layer(text_clips, self._selected_text_index, direction)
            if not moved:
                self._tb_status.configure(text="Texto ja esta no limite dessa camada.")
                return False
            self._selected_text_index = new_index
            self._selected_clip_index = None
            self._selected_overlay_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._refresh_clip_inspector()
            self._redraw_timeline()
            self._draw_frame_at(self._current_frame, fast=True)
            self._tb_status.configure(text="Ordem do texto atualizada.")
            return True
        self._tb_status.configure(text="Selecione texto ou overlay para mudar a ordem da camada.")
        return False

    def _duplicate_selected_timeline_item(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um video antes de duplicar.")
            return
        if self._selected_overlay_index is not None:
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if not 0 <= self._selected_overlay_index < len(overlay_clips):
                self._tb_status.configure(text="Selecione uma midia para duplicar.")
                return
            source = overlay_clips[self._selected_overlay_index]
            new_start, new_end = _duplicate_clip_bounds(source.start_s, source.end_s, self._duration_s)
            if not _trim_bounds_changed(source.start_s, source.end_s, new_start, new_end):
                self._tb_status.configure(text="Sem espaco para duplicar essa midia.")
                return
            self._push_timeline_undo(label="duplicar clipe")
            duplicate = _clone_timeline_clip(source)
            duplicate.start_s = new_start
            duplicate.end_s = new_end
            duplicate.label = f"{source.label or Path(source.source_path).stem or 'Midia'} copia"
            overlay_clips.append(duplicate)
            overlay_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
            self._selected_overlay_index = overlay_clips.index(duplicate)
            self._selected_clip_index = None
            self._selected_text_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._seek_to(self._time_to_frame(new_start))
            self._refresh_clip_inspector()
            self._tb_status.configure(text=f"Midia duplicada em {_fmt(new_start)}.")
            return
        if self._selected_text_index is not None:
            text_clips = _timeline_text_clips(self._timeline_model)
            if not 0 <= self._selected_text_index < len(text_clips):
                self._tb_status.configure(text="Selecione um texto para duplicar.")
                return
            source = text_clips[self._selected_text_index]
            new_start, new_end = _duplicate_clip_bounds(source.start_s, source.end_s, self._duration_s)
            if not _trim_bounds_changed(source.start_s, source.end_s, new_start, new_end):
                self._tb_status.configure(text="Sem espaco para duplicar esse texto.")
                return
            self._push_timeline_undo(label="duplicar clipe")
            duplicate = _clone_timeline_clip(source)
            duplicate.start_s = new_start
            duplicate.end_s = new_end
            duplicate.label = f"{source.label or 'Texto'} copia"
            text_clips.append(duplicate)
            text_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
            self._selected_text_index = text_clips.index(duplicate)
            self._selected_clip_index = None
            self._selected_overlay_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._seek_to(self._time_to_frame(new_start))
            self._refresh_clip_inspector()
            self._tb_status.configure(text=f"Texto duplicado em {_fmt(new_start)}.")
            return

        if self._selected_clip_index is None:
            self._tb_status.configure(text="Selecione texto ou midia externa para duplicar.")
            return
        clips = self._timeline_model.video_track.clips
        if not 0 <= self._selected_clip_index < len(clips):
            self._tb_status.configure(text="Selecione um clipe valido para duplicar.")
            return
        source = _clone_timeline_clip(clips[self._selected_clip_index])
        if not _is_movable_media_clip(source):
            self._tb_status.configure(text="A duplicacao direta vale para texto e midia externa.")
            return
        new_start, new_end = _duplicate_clip_bounds(source.start_s, source.end_s, self._duration_s)
        if not _trim_bounds_changed(source.start_s, source.end_s, new_start, new_end):
            self._tb_status.configure(text="Sem espaco para duplicar essa midia.")
            return
        new_clips, selected_index = _insert_media_clip_replacing_range(
            clips,
            source.source_path,
            new_start,
            self._duration_s,
            clip_duration_s=new_end - new_start,
            min_duration_s=self._trim_min_duration_s,
        )
        if selected_index is None:
            self._tb_status.configure(text="Sem espaco para duplicar essa midia.")
            return
        self._push_timeline_undo(label="duplicar clipe")
        duplicate = _clone_timeline_clip(source)
        duplicate.start_s = new_clips[selected_index].start_s
        duplicate.end_s = new_clips[selected_index].end_s
        duplicate.label = f"{source.label or Path(source.source_path).stem} copia"
        new_clips[selected_index] = duplicate
        self._timeline_model.video_track.clips = new_clips
        self._selected_clip_index = selected_index
        self._selected_text_index = None
        self._selected_overlay_index = None
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._seek_to(self._time_to_frame(duplicate.start_s))
        self._refresh_clip_inspector()
        self._tb_status.configure(text=f"Midia duplicada em {_fmt(duplicate.start_s)}.")

    def _duplicate_selected_clip_to_end(self) -> None:
        """Duplicate the selected video clip and place it at the end of the timeline."""
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um vídeo antes de duplicar.")
            return
        if self._selected_clip_index is None:
            self._tb_status.configure(text="Selecione um clipe de vídeo para duplicar ao final.")
            return
        clips = self._timeline_model.video_track.clips
        if not 0 <= self._selected_clip_index < len(clips):
            self._tb_status.configure(text="Selecione um clipe válido.")
            return
        src = clips[self._selected_clip_index]
        duration = max(0.05, src.end_s - src.start_s)
        last_end = max((c.end_s for c in clips), default=0.0)
        new_start = last_end
        new_end = new_start + duration
        if new_end > self._duration_s + 0.01:
            self._tb_status.configure(
                text=f"Sem espaço no final da timeline ({new_end:.1f}s > {self._duration_s:.1f}s)."
            )
            return
        self._push_timeline_undo(label="duplicar ao final")
        new_clip = _clone_timeline_clip(src)
        new_clip.start_s = new_start
        new_clip.end_s = min(new_end, self._duration_s)
        clips.append(new_clip)
        clips.sort(key=lambda c: c.start_s)
        # Sync audio track
        self._timeline_model.audio_track.clips = [
            _clone_timeline_clip(c) for c in clips
        ]
        self._selected_clip_index = clips.index(new_clip)
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._seek_to(self._time_to_frame(new_start))
        self._redraw_timeline()
        self._refresh_clip_inspector()
        self._tb_status.configure(
            text=f"Clipe duplicado ao final: {_fmt(new_start)} → {_fmt(new_end)}."
        )

    def _copy_selected_timeline_item(self) -> None:
        clip = self._selected_timeline_clip()
        if not _can_clipboard_timeline_clip(clip):
            self._timeline_clipboard = None
            self._tb_status.configure(text="Selecione texto ou midia externa para copiar.")
            return
        self._timeline_clipboard = _clone_timeline_clip(clip)
        self._tb_status.configure(text=f"Copiado: {clip.label or clip.clip_type}.")

    def _cut_selected_timeline_item(self) -> None:
        clip = self._selected_timeline_clip()
        if not _can_clipboard_timeline_clip(clip):
            self._timeline_clipboard = None
            self._tb_status.configure(text="Selecione texto ou midia externa para recortar.")
            return
        self._timeline_clipboard = _clone_timeline_clip(clip)
        self._delete_selected_clip()
        self._tb_status.configure(text=f"Recortado: {clip.label or clip.clip_type}.")

    def _paste_timeline_clipboard_at_playhead(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um video antes de colar.")
            return
        if self._timeline_clipboard is None:
            self._tb_status.configure(text="Copie um texto ou midia externa antes de colar.")
            return
        source = _clone_timeline_clip(self._timeline_clipboard)
        start_s = min(self._duration_s, max(0.0, self._current_frame / max(1.0, self._fps)))
        pasted = _paste_clip_at_time(source, start_s, self._duration_s, self._trim_min_duration_s)
        if pasted is None:
            self._tb_status.configure(text="Sem espaco para colar nesse ponto da timeline.")
            return
        self._push_timeline_undo(label="inserir mídia")
        if pasted.clip_type == "text":
            text_clips = _timeline_text_clips(self._timeline_model)
            text_clips.append(pasted)
            text_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
            self._selected_text_index = text_clips.index(pasted)
            self._selected_overlay_index = None
            self._selected_clip_index = None
            _sync_text_clip_to_video_overlay(self._timeline_model, pasted)
            status = "Texto colado"
        else:
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            overlay_clips.append(pasted)
            overlay_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
            self._selected_overlay_index = overlay_clips.index(pasted)
            self._selected_text_index = None
            self._selected_clip_index = None
            status = "Midia colada"
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._seek_to(self._time_to_frame(pasted.start_s))
        self._refresh_clip_inspector()
        self._tb_status.configure(text=f"{status} em {_fmt(pasted.start_s)}.")

    def _timeline_snapshot(self) -> tuple[
        list[TimelineClip],
        list[TimelineClip],
        list[TimelineClip],
        Optional[int],
        Optional[int],
        Optional[int],
        bool,
    ]:
        if not self._timeline_model:
            return [], [], [], None, None, None, False
        clips = [
            _clone_timeline_clip(c)
            for c in self._timeline_model.video_track.clips
        ]
        text_clips = [
            _clone_timeline_clip(c)
            for c in _timeline_text_clips(self._timeline_model)
        ]
        overlay_clips = [
            _clone_timeline_clip(c)
            for c in _timeline_overlay_clips(self._timeline_model)
        ]
        return (
            clips,
            text_clips,
            overlay_clips,
            self._selected_clip_index,
            self._selected_text_index,
            self._selected_overlay_index,
            self._timeline_dirty,
        )

    def _restore_timeline_snapshot(
        self,
        snapshot: tuple[
            list[TimelineClip],
            list[TimelineClip],
            list[TimelineClip],
            Optional[int],
            Optional[int],
            Optional[int],
            bool,
        ],
        current_time: float,
    ) -> None:
        if not self._timeline_model:
            return
        clips, text_clips, overlay_clips, selected_index, selected_text_index, selected_overlay_index, was_dirty = snapshot
        self._timeline_model.video_track.clips = [
            _clone_timeline_clip(c) for c in clips
        ]
        self._timeline_model.text_track.clips = [
            _clone_timeline_clip(c) for c in text_clips
        ]
        self._timeline_model.overlay_track.clips = [
            _clone_timeline_clip(c) for c in overlay_clips
        ]
        self._selected_clip_index = selected_index
        self._selected_text_index = selected_text_index
        self._selected_overlay_index = selected_overlay_index
        self._timeline_dirty = was_dirty
        self._sync_manual_timeline(mark_dirty=was_dirty)
        self._seek_to(self._time_to_frame(self._nearest_kept_time(current_time)))
        self._refresh_clip_inspector()

    def _push_timeline_undo(self, label: str = "") -> None:
        if not self._timeline_model:
            return
        self._timeline_undo_stack.append((self._timeline_snapshot(), label))
        self._timeline_redo_stack.clear()
        self._last_redo_label = ""
        if len(self._timeline_undo_stack) > 50:
            self._timeline_undo_stack.pop(0)

    def _undo_timeline_action(self) -> None:
        if not self._timeline_model or not self._timeline_undo_stack:
            self._tb_status.configure(text="Nada para desfazer.")
            return
        self._stop_playback(reset_button=True)
        current_time = self._current_frame / max(1.0, self._fps)
        snapshot, label = self._timeline_undo_stack[-1]
        self._timeline_redo_stack.append((self._timeline_snapshot(), label))
        self._timeline_undo_stack.pop()
        self._restore_timeline_snapshot(snapshot, current_time)
        msg = f"Desfeito: {label}" if label else "Ação desfeita."
        self._tb_status.configure(text=msg)
        self._last_undo_label = label
        self._refresh_clip_inspector()

    def _redo_timeline_action(self) -> None:
        if not self._timeline_model or not self._timeline_redo_stack:
            self._tb_status.configure(text="Nada para refazer.")
            return
        self._stop_playback(reset_button=True)
        current_time = self._current_frame / max(1.0, self._fps)
        snapshot, label = self._timeline_redo_stack[-1]
        self._timeline_undo_stack.append((self._timeline_snapshot(), label))
        if len(self._timeline_undo_stack) > 50:
            self._timeline_undo_stack.pop(0)
        self._timeline_redo_stack.pop()
        self._restore_timeline_snapshot(snapshot, current_time)
        msg = f"Refeito: {label}" if label else "Ação refeita."
        self._tb_status.configure(text=msg)
        self._last_redo_label = label

    def _sync_manual_timeline(self, mark_dirty: Optional[bool] = None) -> None:
        if not self._timeline_model:
            return
        clips = self._timeline_model.video_track.clips
        for idx, clip in enumerate(clips, start=1):
            clip.label = clip.label or f"Clip {idx}"
        self._timeline_model.audio_track.clips = [
            _clone_timeline_clip(c) for c in clips
        ]
        self._segments = [(c.start_s, c.end_s) for c in clips]
        self._timeline_model.removed_ranges = _removed_ranges_from_segments(self._duration_s, self._segments)
        self._timeline_model.saved_time_s = sum(end - start for start, end in self._timeline_model.removed_ranges)
        self._analysis_done = True
        if mark_dirty is not None:
            self._timeline_dirty = mark_dirty
        self._redraw_timeline()
        self._refresh_clip_inspector()
        self._save_project_state()

    def _redraw_timeline(self) -> None:
        c = self._tl_canvas
        c.delete("all")
        self._tl_playhead = None
        self._tl_thumb_refs.clear()   # release old PhotoImage refs
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or h < 10:
            return

        c.create_rectangle(0, 0, w, h, fill=ED_TL_BG, outline="")
        if self._duration_s <= 0:
            c.create_text(w // 2, h // 2, text="Nenhum vídeo carregado",
                          fill=ED_TXT3, font=("Segoe UI", 10))
            return

        if not self._timeline_model:
            c.create_text(w // 2, h // 2, text="Analisando áudio...",
                          fill=ED_TXT3, font=("Segoe UI", 10))
            return

        label_w = TL_LABEL_W
        top = 8
        comp = self._composition

        # ── lane layout ──────────────────────────────────────────────────────
        if comp is not None:
            raw_lanes = comp.lane_layout(h)
            req_h = comp.required_canvas_height()
        else:
            extra_overlay_tracks = getattr(self._timeline_model, 'extra_overlay_tracks', [])
            n_overlay = 1 + len(extra_overlay_tracks)
            req_h = 190 + max(0, len(extra_overlay_tracks)) * 30
            raw_lanes = None

        if req_h > h:
            c.configure(height=req_h)
            h = req_h

        if raw_lanes is None:
            extra_overlay_tracks = getattr(self._timeline_model, 'extra_overlay_tracks', [])
            n_overlay = 1 + len(extra_overlay_tracks)
            lanes = _timeline_lane_layout(h, n_overlay_tracks=n_overlay)
            text_y1, text_y2 = lanes["text"]
            overlay_y1, overlay_y2 = lanes["overlay"]
            video_y1, video_y2 = lanes["video"]
            audio_y1, audio_y2 = lanes["audio"]
        else:
            # Map composition tracks to lane coordinates
            lanes = _timeline_lane_layout(h)  # keep for compat refs
            # Override from composition
            for ld in raw_lanes:
                tk_obj = ld["track"]
                if tk_obj.track_type == "video" and tk_obj.index == 0:
                    lanes["video"] = (ld["y1"], ld["y2"])
                elif tk_obj.track_type == "audio" and tk_obj.index == 0:
                    lanes["audio"] = (ld["y1"], ld["y2"])
                elif tk_obj.name == "Texto":
                    lanes["text"] = (ld["y1"], ld["y2"])
                elif tk_obj.track_type == "video" and tk_obj.index == 2:
                    lanes["overlay"] = (ld["y1"], ld["y2"])
                    lanes["overlay_0"] = (ld["y1"], ld["y2"])
            text_y1, text_y2 = lanes.get("text", (8, 34))
            overlay_y1, overlay_y2 = lanes.get("overlay", (38, 64))
            video_y1, video_y2 = lanes.get("video", (68, 104))
            audio_y1, audio_y2 = lanes.get("audio", (108, 140))

        # ── gutter (left side) ────────────────────────────────────────────────
        c.create_rectangle(0, 0, label_w, h, fill="#0d0b14", outline="")
        c.create_line(label_w, 0, label_w, h, fill=ED_BORD)

        visual_visible = bool(self._track_visual_visible_var.get())
        text_visible   = bool(self._track_text_visible_var.get())
        audio_muted    = bool(self._track_audio_muted_var.get())

        if comp is not None:
            # Draw track labels from composition
            for ld in raw_lanes:
                trk = ld["track"]
                my = (ld["y1"] + ld["y2"]) // 2
                label_text = (trk.name or trk.id)[:9]
                if trk.track_type == "video":
                    lbl_color = ED_ATXT if trk.visible else ED_TXT4
                else:
                    lbl_color = ED_TXT2 if not trk.muted else ED_TXT4
                c.create_text(label_w // 2, my, text=label_text,
                              fill=lbl_color, font=("Segoe UI", 8, "bold"))
                # lock/mute indicators
                if trk.locked:
                    c.create_text(label_w - 8, my, text="L", font=("Segoe UI", 7), fill=ED_TXT4)
                if trk.muted or (trk.track_type == "audio" and audio_muted and trk.index == 0):
                    c.create_text(8, my, text="M", font=("Segoe UI", 7, "bold"), fill="#cc6644")
        else:
            extra_overlay_tracks = getattr(self._timeline_model, 'extra_overlay_tracks', [])
            c.create_text(label_w // 2, (text_y1 + text_y2) // 2, text="TEXTO",
                          fill="#d8ccff", font=("Segoe UI", 8, "bold"))
            c.create_text(label_w // 2, (overlay_y1 + overlay_y2) // 2, text="MIDIA",
                          fill="#c8e9dc", font=("Segoe UI", 8, "bold"))
            for _ei, _et in enumerate(extra_overlay_tracks):
                _eoy1, _eoy2 = lanes.get(f"overlay_{1 + _ei}", (0, 0))
                c.create_text(label_w // 2, (_eoy1 + _eoy2) // 2, text=f"MIDIA {2+_ei}",
                              fill="#c8e9dc", font=("Segoe UI", 8, "bold"))
            c.create_text(label_w // 2, (video_y1 + video_y2) // 2, text="BASE",
                          fill=ED_TXT3, font=("Segoe UI", 8, "bold"))
            c.create_text(label_w // 2, (audio_y1 + audio_y2) // 2, text="AUDIO",
                          fill=ED_TXT3, font=("Segoe UI", 8, "bold"))

        # ── Add-track buttons (bottom of gutter) ──────────────────────────────
        # NOTE: create_text does NOT support cursor= in tkinter/Windows; use tag_bind instead
        btn_y = h - 20
        c.create_text(8, btn_y, text="+V", fill=ED_ACC, font=("Segoe UI", 8, "bold"),
                      anchor="w", tags="btn_add_video")
        c.create_text(30, btn_y, text="+A", fill=ED_TXT2, font=("Segoe UI", 8, "bold"),
                      anchor="w", tags="btn_add_audio")
        c.tag_bind("btn_add_video", "<Button-1>", lambda _e: self._add_composition_video_track())
        c.tag_bind("btn_add_audio", "<Button-1>", lambda _e: self._add_composition_audio_track())
        c.tag_bind("btn_add_video", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("btn_add_video", "<Leave>", lambda _e: c.configure(cursor=""))
        c.tag_bind("btn_add_audio", "<Enter>", lambda _e: c.configure(cursor="hand2"))
        c.tag_bind("btn_add_audio", "<Leave>", lambda _e: c.configure(cursor=""))

        # ── track background rectangles ───────────────────────────────────────
        track_x1, track_x2 = self._timeline_track_bounds(w)
        draw_x1, draw_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)

        if comp is not None:
            for ld in raw_lanes:
                trk = ld["track"]
                y1, y2 = ld["y1"], ld["y2"]
                if trk.track_type == "video":
                    fill = "#1b2130" if visual_visible else "#151821"
                    outline_c = "#2a3142"
                else:
                    fill = "#171b24" if not audio_muted else "#151515"
                    outline_c = "#263044"
                c.create_rectangle(track_x1, y1, track_x2, y2, fill=fill, outline=outline_c)
        else:
            extra_overlay_tracks = getattr(self._timeline_model, 'extra_overlay_tracks', [])
            c.create_rectangle(track_x1, text_y1, track_x2, text_y2,
                               fill="#181521" if text_visible else "#121218", outline="#45386c", dash=(3,3))
            c.create_rectangle(track_x1, overlay_y1, track_x2, overlay_y2,
                               fill="#14211d" if visual_visible else "#121817", outline="#2f6a59", dash=(4,2))
            for _ei in range(len(extra_overlay_tracks)):
                _eoy1, _eoy2 = lanes.get(f"overlay_{1+_ei}", (0,0))
                c.create_rectangle(track_x1, _eoy1, track_x2, _eoy2,
                                   fill="#14211d" if visual_visible else "#121817", outline="#2f6a59", dash=(4,2))
            c.create_rectangle(track_x1, video_y1, track_x2, video_y2,
                               fill="#1b2130" if visual_visible else "#151821", outline="#2a3142")
            c.create_rectangle(track_x1, audio_y1, track_x2, audio_y2,
                               fill="#171b24" if not audio_muted else "#151515", outline="#263044")
            text_lane_note = "overlay vazado" if text_visible else "texto oculto no preview"
            c.create_text(track_x1 + 8, text_y1 + 2, text=text_lane_note, fill="#9c8ed0" if text_visible else "#6f687c", font=("Segoe UI", 7), anchor="nw")
            if not visual_visible:
                c.create_text(track_x1 + 8, overlay_y1 + 2, text="overlays visuais ocultos", fill="#6f7788", font=("Segoe UI", 7), anchor="nw")
            if audio_muted:
                c.create_text(track_x1 + 8, audio_y1 + 2, text="audio mutado no preview", fill="#777777", font=("Segoe UI", 7), anchor="nw")

        if self._waveform_zoom > 1.001:
            c.create_text(track_x1 + 8, top + 3, text=f"{self._waveform_zoom:.2f}x",
                          fill=ED_TXT3, font=("Segoe UI", 8), anchor="w")

        # ── timeline content (uses legacy model for all existing logic) ────────
        clips = self._timeline_model.video_track.clips
        compact = self._timeline_compact_enabled()
        compact_ranges = _compact_clip_ranges(clips) if compact else []
        view_duration = (
            compact_ranges[-1][3] if compact and compact_ranges else self._duration_s
        )
        view_start, view_end = self._timeline_view_window(view_duration)

        # ── base video clips ──────────────────────────────────────────────────
        for idx, clip in enumerate(clips):
            if compact and compact_ranges:
                _, _, display_start, display_end = compact_ranges[idx]
                x1 = _timeline_view_time_to_x(display_start, view_start, view_end, draw_x1, draw_x2)
                x2 = _timeline_view_time_to_x(display_end, view_start, view_end, draw_x1, draw_x2)
            else:
                x1 = _timeline_view_time_to_x(clip.start_s, view_start, view_end, draw_x1, draw_x2)
                x2 = _timeline_view_time_to_x(clip.end_s, view_start, view_end, draw_x1, draw_x2)
            outline = C_YELLOW if idx == self._selected_clip_index else ""
            width = 2 if idx == self._selected_clip_index else 1
            visual_stipple = "" if visual_visible else "gray50"
            c.create_rectangle(x1, video_y1 + 2, x2, video_y2 - 2,
                               fill=_timeline_clip_fill(clip), outline=outline,
                               width=width, stipple=visual_stipple)
            audio_fill = "#252525" if audio_muted else (
                "#203449" if idx == self._selected_clip_index else "#1b2a3a")
            audio_outline = C_YELLOW if idx == self._selected_clip_index else "#26384a"
            audio_stipple = "gray50" if audio_muted else ""
            c.create_rectangle(x1, audio_y1 + 2, x2, audio_y2 - 2,
                               fill=audio_fill, outline=audio_outline,
                               width=width, stipple=audio_stipple)
            active_edge = _active_timeline_handle_edge(
                idx, self._selected_clip_index, self._trim_drag, self._hover_trim_handle)
            if active_edge is not None:
                if active_edge in ("both", "start"):
                    _draw_timeline_handle_zone(c, x1, video_y1, video_y2, "start")
                if active_edge in ("both", "end"):
                    _draw_timeline_handle_zone(c, x2, video_y1, video_y2, "end")
            if compact and idx > 0:
                c.create_line(x1, video_y1 + 1, x1, audio_y2 - 1, fill=TL_HEAD)
            # ── thumbnail strip (drawn before label so label appears on top) ─
            if x2 - x1 > 12 and clip.source_path:
                self._draw_thumbnail_strip(
                    c, clip.source_path,
                    clip.start_s, clip.end_s,
                    x1, x2, video_y1 + 2, video_y2 - 2,
                )
            # ── clip label always on top ───────────────────────────────────
            if x2 - x1 > 56:
                c.create_text((x1+x2)//2, (video_y1+video_y2)//2, text=clip.label,
                              fill="#d6e6ff", font=("Segoe UI", 8))
            # Multi-select highlight
            if idx in self._selected_clip_indices and idx != self._selected_clip_index:
                c.create_rectangle(x1 + 1, video_y1 + 1, x2 - 1, video_y2 - 1,
                                   outline="#ff9944", width=2, fill="",
                                   tags="timeline")

        # ── overlay clips (base overlay track) ───────────────────────────────
        for overlay_idx, overlay_clip in enumerate(_timeline_overlay_clips(self._timeline_model)):
            if compact and compact_ranges:
                start_s = _compact_source_to_display_time(overlay_clip.start_s, compact_ranges)
                end_s   = _compact_source_to_display_time(overlay_clip.end_s,   compact_ranges)
            else:
                start_s, end_s = overlay_clip.start_s, overlay_clip.end_s
            x1 = _timeline_view_time_to_x(start_s, view_start, view_end, draw_x1, draw_x2)
            x2 = _timeline_view_time_to_x(end_s,   view_start, view_end, draw_x1, draw_x2)
            overlay_outline = C_YELLOW if overlay_idx == self._selected_overlay_index else "#9ee4c7"
            overlay_width   = 2 if overlay_idx == self._selected_overlay_index else 1
            overlay_stipple = "" if visual_visible else "gray50"
            c.create_rectangle(x1, overlay_y1 + 2, x2, overlay_y2 - 2,
                               fill=_timeline_clip_fill(overlay_clip),
                               outline=overlay_outline, width=overlay_width,
                               stipple=overlay_stipple)
            active_overlay_edge = _active_timeline_handle_edge(
                overlay_idx, self._selected_overlay_index,
                self._overlay_trim_drag, self._hover_overlay_trim_handle)
            if active_overlay_edge is not None:
                if active_overlay_edge in ("both", "start"):
                    _draw_timeline_handle_zone(c, x1, overlay_y1, overlay_y2, "start")
                if active_overlay_edge in ("both", "end"):
                    _draw_timeline_handle_zone(c, x2, overlay_y1, overlay_y2, "end")
            z = getattr(overlay_clip, "z_order", 0)
            label_text = overlay_clip.label or "Midia"
            if z != 0:
                label_text = f"{label_text} [z{z:+d}]"
            if x2 - x1 > 48:
                c.create_text((x1+x2)//2, (overlay_y1+overlay_y2)//2,
                              text=label_text,
                              fill="#dcfff2", font=("Segoe UI", 8))

        # ── extra overlay tracks ─────────────────────────────────────────────
        extra_overlay_tracks = getattr(self._timeline_model, 'extra_overlay_tracks', [])
        for extra_track_idx, extra_track in enumerate(extra_overlay_tracks):
            lane_key = f"overlay_{1 + extra_track_idx}"
            eoy1, eoy2 = lanes.get(lane_key, (0, 0))
            for clip_idx, clip in enumerate(extra_track.clips):
                if compact and compact_ranges:
                    start_s = _compact_source_to_display_time(clip.start_s, compact_ranges)
                    end_s   = _compact_source_to_display_time(clip.end_s,   compact_ranges)
                else:
                    start_s, end_s = clip.start_s, clip.end_s
                x1 = _timeline_view_time_to_x(start_s, view_start, view_end, draw_x1, draw_x2)
                x2 = _timeline_view_time_to_x(end_s,   view_start, view_end, draw_x1, draw_x2)
                is_selected = (
                    self._selected_overlay_track_idx == 1 + extra_track_idx
                    and clip_idx == self._selected_overlay_index
                )
                clip_outline = C_YELLOW if is_selected else "#9ee4c7"
                clip_width   = 2 if is_selected else 1
                clip_stipple = "" if visual_visible else "gray50"
                c.create_rectangle(x1, eoy1 + 2, x2, eoy2 - 2,
                                   fill=_timeline_clip_fill(clip),
                                   outline=clip_outline, width=clip_width,
                                   stipple=clip_stipple)
                if x2 - x1 > 48:
                    c.create_text((x1+x2)//2, (eoy1+eoy2)//2,
                                  text=clip.label or "Midia",
                                  fill="#dcfff2", font=("Segoe UI", 8))

        # ── composition: extra video tracks (V3+, beyond legacy overlay tracks) ──
        if comp is not None:
            legacy_video_count = 1 + 1 + len(extra_overlay_tracks)  # V1 + text + overlays
            for ld in raw_lanes:
                trk = ld["track"]
                if trk.track_type != "video":
                    continue
                if trk.index < legacy_video_count:
                    continue  # already drawn via legacy path above
                vy1, vy2 = ld["y1"], ld["y2"]
                for clip in trk.sorted_clips():
                    cx1 = _timeline_view_time_to_x(clip.start_s, view_start, view_end, draw_x1, draw_x2)
                    cx2 = _timeline_view_time_to_x(clip.end_s, view_start, view_end, draw_x1, draw_x2)
                    c.create_rectangle(cx1, vy1 + 2, cx2, vy2 - 2,
                                       fill=TL_MEDIA, outline="#6be8d2")
                    if cx2 - cx1 > 48:
                        c.create_text((cx1+cx2)//2, (vy1+vy2)//2,
                                      text=clip.label or "Clip",
                                      fill="#dcfff2", font=("Segoe UI", 8))

            # extra audio tracks (A2+)
            for ld in raw_lanes:
                trk = ld["track"]
                if trk.track_type != "audio" or trk.index == 0:
                    continue  # A1 already drawn via legacy path
                ay1, ay2 = ld["y1"], ld["y2"]
                for clip in trk.sorted_clips():
                    cx1 = _timeline_view_time_to_x(clip.start_s, view_start, view_end, draw_x1, draw_x2)
                    cx2 = _timeline_view_time_to_x(clip.end_s, view_start, view_end, draw_x1, draw_x2)
                    a_fill = "#1d2e3f" if not trk.muted else "#1a1a1a"
                    c.create_rectangle(cx1, ay1 + 2, cx2, ay2 - 2,
                                       fill=a_fill, outline="#3a5a7a")
                    if cx2 - cx1 > 48:
                        c.create_text((cx1+cx2)//2, (ay1+ay2)//2,
                                      text=clip.label or "Áudio",
                                      fill="#8ab8d8", font=("Segoe UI", 8))

        # ── text clips ───────────────────────────────────────────────────────
        for text_idx, text_clip in enumerate(_timeline_text_clips(self._timeline_model)):
            if compact and compact_ranges:
                start_s = _compact_source_to_display_time(text_clip.start_s, compact_ranges)
                end_s   = _compact_source_to_display_time(text_clip.end_s,   compact_ranges)
            else:
                start_s, end_s = text_clip.start_s, text_clip.end_s
            x1 = _timeline_view_time_to_x(start_s, view_start, view_end, draw_x1, draw_x2)
            x2 = _timeline_view_time_to_x(end_s,   view_start, view_end, draw_x1, draw_x2)
            text_outline = C_YELLOW if text_idx == self._selected_text_index else "#bca8ff"
            text_width   = 2 if text_idx == self._selected_text_index else 1
            text_fill    = "#6f4cc3" if text_visible and text_clip.text_background_enabled else "#514071"
            text_stipple = ("gray50" if not text_visible else
                           ("gray25" if not text_clip.text_background_enabled else ""))
            c.create_rectangle(x1, text_y1 + 2, x2, text_y2 - 2,
                               fill=text_fill, outline=text_outline,
                               width=text_width, stipple=text_stipple)
            active_text_edge = _active_timeline_handle_edge(
                text_idx, self._selected_text_index,
                self._text_trim_drag, self._hover_text_trim_handle)
            if active_text_edge is not None:
                if active_text_edge in ("both", "start"):
                    _draw_timeline_handle_zone(c, x1, text_y1, text_y2, "start")
                if active_text_edge in ("both", "end"):
                    _draw_timeline_handle_zone(c, x2, text_y1, text_y2, "end")
            if x2 - x1 > 40:
                label = str(text_clip.text_overlay or text_clip.label or "Texto")[:22]
                c.create_text((x1+x2)//2, (text_y1+text_y2)//2,
                              text=label, fill="#e0d0ff", font=("Segoe UI", 8))

        # ── snap guide lines when a drag is active ────────────────────────────
        if (
            self._trim_drag is not None
            or self._clip_move_drag is not None
            or self._text_move_drag is not None
        ):
            all_clips = (
                list(self._timeline_model.video_track.clips)
                + list(_timeline_text_clips(self._timeline_model))
                + list(_timeline_overlay_clips(self._timeline_model))
            )
            total_clips = len(all_clips)
            if self._waveform_zoom > 1 or total_clips > 1:
                seen_x: set[int] = set()
                guide_count = 0
                guide_times: list[float] = []
                for _gc in all_clips:
                    guide_times.extend((_gc.start_s, _gc.end_s))
                # Add markers and playhead as snap guides too
                guide_times.extend(self._timeline_markers)
                guide_times.append(self._timeline_display_playhead_time())
                for _gt in guide_times:
                    if compact and compact_ranges:
                        _gd = _compact_source_to_display_time(_gt, compact_ranges)
                    else:
                        _gd = _gt
                    _gx = _timeline_view_time_to_x(_gd, view_start, view_end, draw_x1, draw_x2)
                    if _gx not in seen_x:
                        seen_x.add(_gx)
                        _guide_col = TL_MARKER_COLOR if _gt in self._timeline_markers else "#ffe066"
                        c.create_line(_gx, top + 8, _gx, audio_y2, fill=_guide_col, width=1, dash=(4, 4))
                        guide_count += 1
                        if guide_count >= 24:
                            break

        # ── waveform ─────────────────────────────────────────────────────────
        self._draw_waveform_track(
            c,
            self._timeline_model.waveform,
            clips,
            compact_ranges,
            view_duration,
            view_start,
            view_end,
            draw_x1,
            draw_x2,
            audio_y1,
            audio_y2,
        )

        # ── silence/removed ranges ────────────────────────────────────────────
        if not compact:
            for start_s, end_s in self._timeline_model.removed_ranges:
                x1 = _timeline_view_time_to_x(start_s, view_start, view_end, draw_x1, draw_x2)
                x2 = _timeline_view_time_to_x(end_s, view_start, view_end, draw_x1, draw_x2)
                c.create_rectangle(x1, video_y1 + 6, x2, video_y2 - 6, fill=TL_SILENCE, outline="", stipple="gray50")
                c.create_rectangle(x1, audio_y1 + 4, x2, audio_y2 - 4, fill="#11151d", outline="", stipple="gray50")

        # ── tick marks ────────────────────────────────────────────────────────
        tick_step = max(1, int(self._duration_s / 12))
        tick_duration = view_duration if compact else self._duration_s
        tick_start = int(max(0, math.floor(view_start / tick_step) * tick_step))
        tick_end = int(min(tick_duration, math.ceil(view_end / tick_step) * tick_step))
        for t in range(tick_start, tick_end + 1, tick_step):
            x = _timeline_view_time_to_x(float(t), view_start, view_end, draw_x1, draw_x2)
            c.create_line(x, 4, x, h - 4, fill="#222734")
            mm, ss = divmod(t, 60)
            c.create_text(x, h - 7, text=f"{mm}:{ss:02d}", fill=C_MUTED, font=("Courier New", 8))

        # ── playhead ─────────────────────────────────────────────────────────
        playhead_time = self._timeline_display_playhead_time()
        if compact and compact_ranges:
            playhead_display = _compact_source_to_display_time(playhead_time, compact_ranges)
        else:
            playhead_display = playhead_time
        ph_x = _timeline_view_time_to_x(playhead_display, view_start, view_end, draw_x1, draw_x2)
        self._tl_playhead = c.create_line(ph_x, 0, ph_x, h, fill=TL_HEAD, width=2)
        c.create_polygon(ph_x - 6, 0, ph_x + 6, 0, ph_x, 8, fill=TL_HEAD, outline="")

        # ── timeline markers ─────────────────────────────────────────────────
        for marker_t in self._timeline_markers:
            if compact and compact_ranges:
                mt_display = _compact_source_to_display_time(marker_t, compact_ranges)
            else:
                mt_display = marker_t
            mx = _timeline_view_time_to_x(mt_display, view_start, view_end, draw_x1, draw_x2)
            if draw_x1 - 12 <= mx <= draw_x2 + 12:
                # Vertical guide line
                c.create_line(mx, 0, mx, h, fill=TL_MARKER_COLOR, width=1, dash=(3, 5))
                # Flag triangle at the top
                c.create_polygon(mx, 0, mx + 10, 0, mx, 10,
                                 fill=TL_MARKER_COLOR, outline="")
                # Small label with time
                mm_m, ss_m = divmod(int(marker_t), 60)
                c.create_text(mx + 12, 4, text=f"{mm_m}:{ss_m:02d}",
                              fill=TL_MARKER_COLOR, font=("Segoe UI", 7), anchor="nw")
                # Custom marker label (if any)
                marker_y1 = 0
                label = getattr(self, '_marker_labels', {}).get(marker_t, "")
                if label:
                    c.create_text(mx + 3, marker_y1 + 2, text=label, anchor="nw",
                                 fill="#ffb060", font=("Segoe UI", 7), tags="timeline")

        # ── drag drop marker ─────────────────────────────────────────────────
        if self._media_drag_preview_time is not None:
            drag_time = self._media_drag_preview_time
            if compact and compact_ranges:
                drag_time = _compact_source_to_display_time(drag_time, compact_ranges)
            drag_x = _timeline_view_time_to_x(drag_time, view_start, view_end, draw_x1, draw_x2)
            if track_x1 <= drag_x <= track_x2:
                _draw_timeline_drop_marker(c, drag_x, overlay_y1, overlay_y2,
                                           f"{Path(self._media_drag_path or '').name or 'Midia'}  {_fmt(self._media_drag_preview_time)}")

        # ── status bar info ───────────────────────────────────────────────────
        kept = sum(clip.end_s - clip.start_s for clip in self._timeline_model.video_track.clips)
        mode = "compacta" if compact else "original"
        self._tl_info.configure(
            text=f"Mantido: {_fmt(kept)}  |  Cortado: {_fmt(self._timeline_model.saved_time_s)}  |  Vista: {mode}  |  Preview: {self._preview_backend}"
        )

        with contextlib.suppress(Exception):
            self._redraw_minimap()

    def _draw_waveform_track(
        self,
        canvas: tk.Canvas,
        samples: list[float],
        clips: list[TimelineClip],
        compact_ranges: list[tuple[float, float, float, float]],
        view_duration: float,
        view_start: float,
        view_end: float,
        x1: int,
        x2: int,
        y1: int,
        y2: int,
    ) -> None:
        if not samples:
            canvas.create_text((x1 + x2) // 2, (y1 + y2) // 2, text="Waveform indisponível", fill=C_MUTED, font=("Segoe UI", 9))
            return

        if not clips:
            self._draw_waveform_segment(canvas, samples, 0.0, self._duration_s, x1, x2, y1, y2)
            return

        for idx, clip in enumerate(clips):
            if compact_ranges:
                _, _, display_start, display_end = compact_ranges[idx]
                sx1 = _timeline_view_time_to_x(display_start, view_start, view_end, x1, x2)
                sx2 = _timeline_view_time_to_x(display_end, view_start, view_end, x1, x2)
            else:
                sx1 = _timeline_view_time_to_x(clip.start_s, view_start, view_end, x1, x2)
                sx2 = _timeline_view_time_to_x(clip.end_s, view_start, view_end, x1, x2)
            self._draw_waveform_segment(canvas, samples, clip.start_s, clip.end_s, sx1, sx2, y1, y2)
            # ── Etapa C: fade handles ─────────────────────────────────────────
            self._draw_fade_handles(canvas, clip, idx, sx1, sx2, y1, y2, view_start, view_end, x1, x2)

    def _draw_waveform_segment(
        self,
        canvas: tk.Canvas,
        samples: list[float],
        start_s: float,
        end_s: float,
        x1: int,
        x2: int,
        y1: int,
        y2: int,
    ) -> None:
        start_idx, end_idx = _waveform_indices_for_time_range(
            len(samples),
            self._duration_s,
            start_s,
            end_s,
        )
        segment = samples[start_idx:end_idx]
        if not segment or x2 <= x1:
            return

        width = max(1, x2 - x1)
        half_h = (y2 - y1) / 2
        center_y = y1 + half_h
        visible = max(4, int(len(segment) / max(1.0, self._waveform_zoom)))
        stride = max(1, len(segment) // visible)
        bars = segment[::stride]
        bar_w = max(1, width / max(1, len(bars)))

        for idx, amp in enumerate(bars):
            x = x1 + idx * bar_w
            peak = max(1.0, amp * (half_h - 3))
            canvas.create_line(x, center_y - peak, x, center_y + peak, fill=_waveform_bar_color(amp))

    def _draw_fade_handles(
        self,
        canvas: tk.Canvas,
        clip: "TimelineClip",
        clip_idx: int,
        sx1: int, sx2: int,
        y1: int, y2: int,
        view_start: float, view_end: float,
        tx1: int, tx2: int,
    ) -> None:
        """Draw translucent fade-in / fade-out ramp + draggable handle triangle."""
        fade_in_s  = max(0.0, float(getattr(clip, "fade_in_s",  0.0)))
        fade_out_s = max(0.0, float(getattr(clip, "fade_out_s", 0.0)))
        clip_dur   = max(0.01, clip.end_s - clip.start_s)
        h = y2 - y1
        if h < 4:
            return

        def _t2x(t: float) -> int:
            return _timeline_view_time_to_x(t, view_start, view_end, tx1, tx2)

        # ── Fade-in ramp ──────────────────────────────────────────────────────
        if fade_in_s > 0.001:
            fi_end_s = clip.start_s + min(fade_in_s, clip_dur * 0.9)
            fx2 = _t2x(fi_end_s)
            fx2 = max(sx1 + 1, min(fx2, sx2))
            if fx2 > sx1:
                # Semi-transparent dark overlay using stipple for gradient feel
                canvas.create_polygon(
                    sx1, y1, fx2, y1, sx1, y2,
                    fill="#000000", stipple="gray50", outline=""
                )
                # Bright triangle handle at the fade-in end
                hw = max(5, min(10, fx2 - sx1))
                canvas.create_polygon(
                    fx2 - hw, y1, fx2, y1, fx2, y1 + hw,
                    fill="#55ccff", outline="#ffffff", width=1,
                    tags=(f"fade_in_{clip_idx}",),
                )

        # ── Fade-out ramp ─────────────────────────────────────────────────────
        if fade_out_s > 0.001:
            fo_start_s = clip.end_s - min(fade_out_s, clip_dur * 0.9)
            fox1 = _t2x(fo_start_s)
            fox1 = max(sx1, min(fox1, sx2 - 1))
            if fox1 < sx2:
                canvas.create_polygon(
                    fox1, y1, sx2, y1, sx2, y2,
                    fill="#000000", stipple="gray50", outline=""
                )
                hw = max(5, min(10, sx2 - fox1))
                canvas.create_polygon(
                    fox1, y1, fox1 + hw, y1, fox1, y1 + hw,
                    fill="#ffaa33", outline="#ffffff", width=1,
                    tags=(f"fade_out_{clip_idx}",),
                )

    def _time_to_x(self, time_s: float, x1: int, x2: int) -> int:
        return _timeline_time_to_x(time_s, self._duration_s, x1, x2)

    def _timeline_track_bounds(self, canvas_width: int) -> tuple[int, int]:
        return _timeline_track_bounds(canvas_width)

    def _x_to_time(self, x: int, x1: int, x2: int) -> float:
        return _timeline_x_to_time(x, self._duration_s, x1, x2)

    def _time_to_frame(self, time_s: float) -> int:
        return _time_to_frame(time_s, self._fps, self._total_frames)

    def _timeline_compact_enabled(self) -> bool:
        return bool(self._tl_compact_var.get()) and bool(
            self._timeline_model and self._timeline_model.video_track.clips
        )

    def _compact_ranges_for_view(self) -> list[tuple[float, float, float, float]]:
        if not self._timeline_compact_enabled() or not self._timeline_model:
            return []
        return _compact_clip_ranges(self._timeline_model.video_track.clips)

    def _timeline_click_time(self, x: int, x1: int, x2: int) -> float:
        x1, x2 = self._timeline_zoomed_bounds(x1, x2)
        compact_ranges = self._compact_ranges_for_view()
        if compact_ranges:
            view_duration = compact_ranges[-1][3]
            view_start, view_end = self._timeline_view_window(view_duration)
            display_time = _timeline_x_to_view_time(x, view_start, view_end, x1, x2)
            display_time = _clamp_float(display_time, 0.0, view_duration)
            return _compact_display_to_source_time(display_time, compact_ranges)
        view_start, view_end = self._timeline_view_window(self._duration_s)
        return _clamp_float(_timeline_x_to_view_time(x, view_start, view_end, x1, x2), 0.0, self._duration_s)

    def _n_overlay_tracks(self) -> int:
        """Number of overlay lane rows to use for lane layout (base + extra)."""
        return 1 + len(getattr(self._timeline_model, "extra_overlay_tracks", [])) if self._timeline_model else 1

    def _trim_handle_at(self, x: int, y: int) -> Optional[tuple[int, str]]:
        if not self._timeline_model:
            return None
        video_y1, video_y2 = _timeline_lane_layout(self._tl_canvas.winfo_height(), self._n_overlay_tracks())["video"]
        if not _timeline_y_in_lane(y, video_y1, video_y2, margin_px=6):
            return None

        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        clips = self._timeline_model.video_track.clips
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)

        for idx, clip in enumerate(clips):
            if compact_ranges:
                _, _, start_s, end_s = compact_ranges[idx]
                x1 = _timeline_view_time_to_x(start_s, view_start, view_end, track_x1, track_x2)
                x2 = _timeline_view_time_to_x(end_s, view_start, view_end, track_x1, track_x2)
            else:
                x1 = _timeline_view_time_to_x(clip.start_s, view_start, view_end, track_x1, track_x2)
                x2 = _timeline_view_time_to_x(clip.end_s, view_start, view_end, track_x1, track_x2)
            clip_w = max(1, x2 - x1)
            handle_px = max(6, min(14, clip_w // 5))
            edge = _timeline_handle_edge_at(x, x1, x2, handle_px)
            if edge:
                return idx, edge
        return None

    def _text_trim_handle_at(self, x: int, y: int) -> Optional[tuple[int, str]]:
        if not self._timeline_model:
            return None
        text_y1, text_y2 = _timeline_lane_layout(self._tl_canvas.winfo_height(), self._n_overlay_tracks())["text"]
        if not _timeline_y_in_lane(y, text_y1, text_y2, margin_px=6):
            return None

        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        text_clips = _timeline_text_clips(self._timeline_model)
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)
        handle_px = 14

        for idx, clip in enumerate(text_clips):
            if compact_ranges:
                start_s = _compact_source_to_display_time(clip.start_s, compact_ranges)
                end_s = _compact_source_to_display_time(clip.end_s, compact_ranges)
            else:
                start_s, end_s = clip.start_s, clip.end_s
            x1 = _timeline_view_time_to_x(start_s, view_start, view_end, track_x1, track_x2)
            x2 = _timeline_view_time_to_x(end_s, view_start, view_end, track_x1, track_x2)
            edge = _timeline_handle_edge_at(x, x1, x2, handle_px)
            if edge:
                return idx, edge
        return None

    def _overlay_trim_handle_at(self, x: int, y: int) -> Optional[tuple[int, str]]:
        if not self._timeline_model:
            return None
        lanes = _timeline_lane_layout(self._tl_canvas.winfo_height(), n_overlay_tracks=self._n_overlay_tracks())
        # Check if y falls in any overlay lane
        in_any_overlay = any(
            _timeline_y_in_lane(y, v[0], v[1], margin_px=6)
            for k, v in lanes.items()
            if k.startswith("overlay_")
        )
        if not in_any_overlay:
            return None

        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        overlay_clips = _timeline_overlay_clips(self._timeline_model)
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)

        for idx in range(len(overlay_clips) - 1, -1, -1):
            clip = overlay_clips[idx]
            if compact_ranges:
                start_s = _compact_source_to_display_time(clip.start_s, compact_ranges)
                end_s = _compact_source_to_display_time(clip.end_s, compact_ranges)
            else:
                start_s, end_s = clip.start_s, clip.end_s
            x1 = _timeline_view_time_to_x(start_s, view_start, view_end, track_x1, track_x2)
            x2 = _timeline_view_time_to_x(end_s, view_start, view_end, track_x1, track_x2)
            clip_w = max(1, x2 - x1)
            handle_px = max(6, min(14, clip_w // 5))
            edge = _timeline_handle_edge_at(x, x1, x2, handle_px)
            if edge:
                return idx, edge
        return None

    def _fade_handle_at(self, x: int, y: int) -> Optional[tuple[int, str]]:
        """Return (clip_idx, 'in'|'out') if x,y is within a fade handle triangle on the audio lane."""
        if not self._timeline_model:
            return None
        lanes = _timeline_lane_layout(self._tl_canvas.winfo_height(), self._n_overlay_tracks())
        audio_y1, audio_y2 = lanes.get("audio", (0, 0))
        if not _timeline_y_in_lane(y, audio_y1, audio_y2, margin_px=6):
            return None
        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        clips = self._timeline_model.video_track.clips
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)
        HIT = 14   # hit zone width in pixels around handle

        for idx, clip in enumerate(clips):
            if compact_ranges:
                _, _, cs, ce = compact_ranges[idx]
            else:
                cs, ce = clip.start_s, clip.end_s
            sx1 = _timeline_view_time_to_x(cs, view_start, view_end, track_x1, track_x2)
            sx2 = _timeline_view_time_to_x(ce, view_start, view_end, track_x1, track_x2)
            clip_dur = max(0.01, clip.end_s - clip.start_s)

            fade_in_s = max(0.0, float(getattr(clip, "fade_in_s", 0.0)))
            if fade_in_s > 0.001:
                fi_end = clip.start_s + min(fade_in_s, clip_dur * 0.9)
                fx = _timeline_view_time_to_x(fi_end, view_start, view_end, track_x1, track_x2)
                fx = max(sx1 + 1, min(fx, sx2))
                if abs(x - fx) <= HIT:
                    return idx, "in"

            fade_out_s = max(0.0, float(getattr(clip, "fade_out_s", 0.0)))
            if fade_out_s > 0.001:
                fo_start = clip.end_s - min(fade_out_s, clip_dur * 0.9)
                fox = _timeline_view_time_to_x(fo_start, view_start, view_end, track_x1, track_x2)
                fox = max(sx1, min(fox, sx2 - 1))
                if abs(x - fox) <= HIT:
                    return idx, "out"

        return None

    def _media_clip_body_at(self, x: int, y: int) -> Optional[int]:
        if not self._timeline_model:
            return None
        lanes = _timeline_lane_layout(self._tl_canvas.winfo_height(), n_overlay_tracks=self._n_overlay_tracks())
        in_any_overlay = any(
            _timeline_y_in_lane(y, v[0], v[1], margin_px=2)
            for k, v in lanes.items()
            if k.startswith("overlay_")
        )
        if not in_any_overlay:
            return None

        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        clips = _timeline_overlay_clips(self._timeline_model)
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)

        hit_indices: list[int] = []
        for idx in range(len(clips)):
            clip = clips[idx]
            if not _is_movable_media_clip(clip):
                continue
            if compact_ranges:
                start_s = _compact_source_to_display_time(clip.start_s, compact_ranges)
                end_s = _compact_source_to_display_time(clip.end_s, compact_ranges)
            else:
                start_s, end_s = clip.start_s, clip.end_s
            x1 = _timeline_view_time_to_x(start_s, view_start, view_end, track_x1, track_x2)
            x2 = _timeline_view_time_to_x(end_s, view_start, view_end, track_x1, track_x2)
            if min(x1, x2) <= x <= max(x1, x2):
                hit_indices.append(idx)
        return _cycle_index_in_order(hit_indices, self._selected_overlay_index)

    def _text_clip_body_at(self, x: int, y: int) -> Optional[int]:
        if not self._timeline_model:
            return None
        text_y1, text_y2 = _timeline_lane_layout(self._tl_canvas.winfo_height(), self._n_overlay_tracks())["text"]
        if not _timeline_y_in_lane(y, text_y1, text_y2, margin_px=2):
            return None

        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        text_clips = _timeline_text_clips(self._timeline_model)
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)

        hit_indices: list[int] = []
        for idx in range(len(text_clips)):
            clip = text_clips[idx]
            if compact_ranges:
                start_s = _compact_source_to_display_time(clip.start_s, compact_ranges)
                end_s = _compact_source_to_display_time(clip.end_s, compact_ranges)
            else:
                start_s, end_s = clip.start_s, clip.end_s
            x1 = _timeline_view_time_to_x(start_s, view_start, view_end, track_x1, track_x2)
            x2 = _timeline_view_time_to_x(end_s, view_start, view_end, track_x1, track_x2)
            if min(x1, x2) <= x <= max(x1, x2):
                hit_indices.append(idx)
        return _cycle_index_in_order(hit_indices, self._selected_text_index)

    # ── Etapa 2 helpers ───────────────────────────────────────────────────────

    def _on_thumb_ready(self) -> None:
        """Called from the thumbnail background thread when a frame is ready."""
        try:
            self.root.after(0, self._redraw_timeline)
        except Exception:
            pass

    def _draw_thumbnail_strip(
        self,
        canvas: tk.Canvas,
        source_path: str,
        clip_start_s: float,
        clip_end_s: float,
        px1: int, px2: int,
        py1: int, py2: int,
    ) -> None:
        """Tile thumbnails from *source_path* inside the clip rectangle.

        Only draws when at least one thumbnail is cached — never overwrites
        the coloured clip rectangle with a black background prematurely.
        """
        if not source_path or px2 <= px1 or py2 <= py1:
            return
        from PIL import ImageTk as _ITK
        lane_h = py2 - py1
        thumb_h = max(8, min(lane_h, TL_THUMB_H))
        thumb_w = max(8, int(thumb_h * 16 / 9))
        clip_dur = max(0.001, clip_end_s - clip_start_s)
        strip_w = px2 - px1
        n_thumbs = max(1, strip_w // thumb_w)
        # Clamp: don't request more than 1 thumb per 0.25 s
        n_thumbs = min(n_thumbs, max(1, int(clip_dur / 0.25)))
        time_step = clip_dur / n_thumbs

        # Collect ready thumbnails (also queues background fetch for missing ones)
        ready: list[tuple[int, "Image.Image"]] = []
        for ti in range(n_thumbs):
            t_s = clip_start_s + (ti + 0.5) * time_step
            pil_img = self._thumb_cache.get(source_path, t_s, thumb_w, thumb_h)
            if pil_img is not None:
                tx = px1 + ti * thumb_w
                ready.append((tx, pil_img))

        # Nothing ready yet — leave the clip rectangle colour showing through
        if not ready:
            return

        # Dark background only when we have actual thumbnails to show
        canvas.create_rectangle(px1, py1, px2, py2, fill="#0a0a0f", outline="")
        ty = py1 + (lane_h - thumb_h) // 2
        for tx, pil_img in ready:
            tk_img = _ITK.PhotoImage(pil_img)
            self._tl_thumb_refs.append(tk_img)
            canvas.create_image(tx, ty, image=tk_img, anchor="nw")

    def _set_tl_edit_mode(self, mode: str) -> None:
        """Switch timeline edit mode and refresh button states."""
        self._tl_edit_mode = mode
        for m_key, btn in self._tl_edit_mode_btns.items():
            active = m_key == mode
            btn.configure(
                bg=ED_ACC if active else ED_SURF,
                fg=ED_TXT if active else ED_TXT3,
            )
        mode_labels = {k: lbl for k, lbl, _ in TL_EDIT_MODES}
        self._tb_status.configure(text=f"Modo: {mode_labels.get(mode, mode)}")

    def _add_marker(self, time_s: Optional[float] = None) -> None:
        """Insert a timeline marker at *time_s* (defaults to playhead)."""
        if time_s is None:
            time_s = self._timeline_display_playhead_time()
        # Deduplicate (within 0.05 s)
        for mt in self._timeline_markers:
            if abs(mt - time_s) < 0.05:
                return
        self._timeline_markers.append(time_s)
        self._timeline_markers.sort()
        if not hasattr(self, '_marker_labels'):
            self._marker_labels = {}
        self._redraw_timeline()
        mm, ss = divmod(int(time_s), 60)
        self._tb_status.configure(text=f"Marcador adicionado: {mm}:{ss:02d}")

    def _remove_nearest_marker(self, time_s: float) -> None:
        """Remove the marker closest to *time_s* (within snap threshold)."""
        if not self._timeline_markers:
            return
        nearest = min(self._timeline_markers, key=lambda t: abs(t - time_s))
        if abs(nearest - time_s) <= self._snap_threshold_s() * 4:
            self._timeline_markers.remove(nearest)
            self._marker_labels.pop(nearest, None)
            self._redraw_timeline()

    def _rename_nearest_marker(self, time_s: float) -> None:
        """Open a dialog to rename the nearest marker."""
        if not self._timeline_markers:
            return
        nearest = min(self._timeline_markers, key=lambda t: abs(t - time_s))
        if abs(nearest - time_s) > self._snap_threshold_s() * 10:
            return
        from tkinter import simpledialog
        current = self._marker_labels.get(nearest, "")
        name = simpledialog.askstring(
            "Renomear Marcador",
            f"Nome para o marcador em {_fmt(nearest)}:",
            initialvalue=current,
            parent=self.root,
        )
        if name is not None:
            if name.strip():
                self._marker_labels[nearest] = name.strip()
            else:
                self._marker_labels.pop(nearest, None)
            self._redraw_timeline()
            self._tb_status.configure(text=f"Marcador renomeado: {name!r}." if name.strip() else "Rótulo do marcador removido.")

    def _jump_to_next_marker(self) -> None:
        """Seek to the next marker after the current playhead."""
        if not self._timeline_markers:
            return
        t = self._timeline_display_playhead_time()
        future = [m for m in self._timeline_markers if m > t + 0.05]
        if future:
            self._seek_to(self._time_to_frame(future[0]))
            self._redraw_timeline()

    def _jump_to_prev_marker(self) -> None:
        """Seek to the previous marker before the current playhead."""
        if not self._timeline_markers:
            return
        t = self._timeline_display_playhead_time()
        past = [m for m in self._timeline_markers if m < t - 0.05]
        if past:
            self._seek_to(self._time_to_frame(past[-1]))
            self._redraw_timeline()

    def _shortcut_add_marker(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._add_marker()
        return "break"

    def _shortcut_next_marker(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._jump_to_next_marker()
        return "break"

    def _redraw_minimap(self) -> None:
        """Draw a scaled-down overview of the full timeline with view window indicator."""
        if not hasattr(self, '_tl_minimap') or self._tl_minimap is None or not self._tl_minimap.winfo_exists():
            return
        c = self._tl_minimap
        c.delete("all")
        w = c.winfo_width()
        h = c.winfo_height()
        if w < 10 or self._duration_s <= 0:
            return

        def t2x(t: float) -> int:
            return int(t / self._duration_s * w)

        # Background
        c.create_rectangle(0, 0, w, h, fill="#0a0910", outline="")

        # Draw video clips
        if self._timeline_model:
            for clip in self._timeline_model.video_track.clips:
                x1, x2 = t2x(clip.start_s), t2x(clip.end_s)
                if x2 > x1:
                    fill = _timeline_clip_fill(clip)
                    c.create_rectangle(x1, 2, max(x1+1, x2), h - 2, fill=fill, outline="", tags="mini")
            # Draw overlay clips as thin strip at top
            for clip in _timeline_overlay_clips(self._timeline_model):
                x1, x2 = t2x(clip.start_s), t2x(clip.end_s)
                if x2 > x1:
                    c.create_rectangle(x1, 0, max(x1+1, x2), 3, fill="#9ee4c7", outline="", tags="mini")
            # Draw markers
            for mt in getattr(self, '_timeline_markers', []):
                mx = t2x(mt)
                c.create_line(mx, 0, mx, h, fill=TL_MARKER_COLOR, width=1, tags="mini")

        # Draw view window (visible range indicator)
        view_dur = self._duration_s
        view_start, view_end = self._timeline_view_window(view_dur)
        vx1 = int(view_start / view_dur * w) if view_dur > 0 else 0
        vx2 = int(view_end / view_dur * w) if view_dur > 0 else w
        c.create_rectangle(vx1, 0, vx2, h, outline="#8B6BFF", width=1,
                           fill="#8B6BFF22" if (vx2 - vx1) < w else "", tags="mini")

        # Playhead
        ph = t2x(self._current_frame / max(1.0, self._fps))
        c.create_line(ph, 0, ph, h, fill=TL_HEAD, width=1, tags="mini")

    def _on_minimap_press(self, event: tk.Event) -> None:
        """Click on minimap to seek to that time."""
        w = self._tl_minimap.winfo_width()
        if w <= 0 or self._duration_s <= 0:
            return
        time_s = _clamp_float(event.x / w * self._duration_s, 0.0, self._duration_s)
        # Also center the timeline view window on clicked position
        self._timeline_view_center_s = time_s
        self._seek_to(self._time_to_frame(time_s))

    def _on_minimap_drag(self, event: tk.Event) -> None:
        self._on_minimap_press(event)

    def _set_selected_clip_color(self, color: str) -> None:
        clip = self._selected_timeline_clip()
        if clip is None:
            return
        self._push_timeline_undo(label="cor do clipe")
        clip.color = color
        self._sync_manual_timeline(mark_dirty=True)
        self._redraw_timeline()
        self._tb_status.configure(text=f"Cor do clipe definida." if color else "Cor do clipe redefinida.")

    def _tl_right_click(self, event: tk.Event) -> None:
        """Right-click on timeline canvas: context menu for markers / edit mode."""
        if not self._timeline_model:
            return
        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        time_s = self._timeline_click_time(event.x, track_x1, track_x2)
        menu = tk.Menu(self.root, tearoff=0, bg=ED_SURF, fg=ED_TXT,
                       activebackground=ED_ACC, activeforeground=ED_TXT,
                       font=("Segoe UI", 9))
        menu.add_command(
            label=f"Adicionar marcador aqui  ({_fmt(time_s)})",
            command=lambda: self._add_marker(time_s))
        # Check if there's a nearby marker to remove
        near = [m for m in self._timeline_markers if abs(m - time_s) < self._snap_threshold_s() * 8]
        if near:
            menu.add_command(
                label=f"Remover marcador  ({_fmt(near[0])})",
                command=lambda t=near[0]: (self._timeline_markers.remove(t),
                                           self._marker_labels.pop(t, None),
                                           self._redraw_timeline()))
        menu.add_separator()
        if self._selected_clip_index is not None:
            menu.add_command(
                label="Duplicar clipe ao final da timeline",
                command=self._duplicate_selected_clip_to_end,
            )
        near_rename = [m for m in self._timeline_markers if abs(m - time_s) < self._snap_threshold_s() * 10]
        if near_rename:
            menu.add_command(
                label=f"Renomear marcador  ({_fmt(near_rename[0])})",
                command=lambda t=near_rename[0]: self._rename_nearest_marker(t))
        # Clip color
        if self._selected_clip_index is not None or self._selected_overlay_index is not None:
            color_menu = tk.Menu(menu, tearoff=0, bg=C_SURFACE, fg=C_TEXT)
            clip_colors = [
                ("Padrão",    ""),
                ("Azul",      "#3a7ebf"),
                ("Verde",     "#2f8f70"),
                ("Laranja",   "#b77a2d"),
                ("Roxo",      "#7a4fbf"),
                ("Vermelho",  "#bf3a3a"),
                ("Amarelo",   "#b89a20"),
                ("Rosa",      "#bf4a7a"),
            ]
            for color_name, color_val in clip_colors:
                color_menu.add_command(
                    label=color_name,
                    command=lambda v=color_val: self._set_selected_clip_color(v),
                )
            menu.add_cascade(label="Cor do clipe", menu=color_menu)
        menu.add_separator()
        for _mk, _mlbl, _mtip in TL_EDIT_MODES:
            _active = (_mk == self._tl_edit_mode)
            _prefix = "✓ " if _active else "    "
            menu.add_command(
                label=f"{_prefix}{_mlbl} — {_mtip[:50]}",
                command=lambda mk=_mk: self._set_tl_edit_mode(mk))
        menu.tk_popup(event.x_root, event.y_root)

    def _tl_double_click(self, event: tk.Event) -> None:
        """Double-click on a marker to rename it."""
        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        time_s = self._timeline_click_time(event.x, track_x1, track_x2)
        # Check if near a marker (top 24px of timeline = marker zone)
        if int(event.y) <= 24:
            self._rename_nearest_marker(time_s)

    def _snap_time_to_clip_edge(self, time_s: float) -> tuple[float, bool]:
        if not self._timeline_model:
            return time_s, False
        edges = list(_clip_edges(self._timeline_model.video_track.clips))
        # Also snap to markers and playhead
        edges.extend(self._timeline_markers)
        edges.append(self._timeline_display_playhead_time())
        return _snap_time_to_edges_with_flag(time_s, edges, self._snap_threshold_s())

    def _snap_threshold_s(self) -> float:
        return max(1.0 / max(1.0, self._fps), 0.08)

    def _clip_index_at_time(self, time_s: float) -> Optional[int]:
        if not self._timeline_model:
            return None
        for idx, clip in enumerate(self._timeline_model.video_track.clips):
            if clip.start_s <= time_s < clip.end_s:
                return idx
        if self._timeline_model.video_track.clips:
            last = self._timeline_model.video_track.clips[-1]
            if abs(time_s - last.end_s) < 0.001:
                return len(self._timeline_model.video_track.clips) - 1
        return None

    def _text_clip_index_at_time(self, time_s: float) -> Optional[int]:
        if not self._timeline_model:
            return None
        for idx, clip in enumerate(_timeline_text_clips(self._timeline_model)):
            if clip.start_s <= time_s < clip.end_s:
                return idx
        return None

    def _overlay_clip_index_at_time(self, time_s: float) -> Optional[int]:
        if not self._timeline_model:
            return None
        for idx in range(len(_timeline_overlay_clips(self._timeline_model)) - 1, -1, -1):
            clip = _timeline_overlay_clips(self._timeline_model)[idx]
            if clip.start_s <= time_s < clip.end_s:
                return idx
        return None

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
        self._description_text = tk.Text(
            s,
            height=4,
            bg=C_SURFACE,
            fg=C_TEXT,
            insertbackground=C_TEXT,
            relief="flat",
            wrap="word",
            font=("Segoe UI", 9),
        )
        self._description_text.grid(row=5, column=0, sticky="ew", padx=10, pady=(2, 4))
        tk.Button(s, text="Sugerir com IA", command=self._suggest_title_with_ai,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(
            row=6, column=0, sticky="ew", padx=10, pady=(2, 8))

        # Plataforma
        self._section(s, "PLATAFORMA", 7)
        pf = tk.Frame(s, bg=C_PANEL)
        pf.grid(row=8, column=0, sticky="ew", padx=10, pady=(0,6))
        # _platform_var already created in __init__; reuse it here
        plat_opts = [("YouTube", Platform.YOUTUBE), ("Reels/IG", Platform.REELS),
                     ("TikTok",  Platform.TIKTOK),  ("Shorts",  Platform.SHORTS)]
        for i, (lbl, plat) in enumerate(plat_opts):
            tk.Radiobutton(pf, text=lbl, variable=self._platform_var,
                           value=plat.value, bg=C_PANEL, fg=C_TEXT,
                           selectcolor=C_SURFACE, activebackground=C_PANEL,
                           activeforeground=C_TEXT, font=("Segoe UI", 10),
                           relief="flat").grid(row=i//2, column=i%2, sticky="w", padx=4)

        # -- Corte de Silêncio ---------------------------------------------
        self._section(s, "CORTE DE SILÊNCIO", 9)
        # _rm_silence_var already created in __init__; reuse it here
        self._check(s, "Ativar corte de silêncios", self._rm_silence_var, 10)

        sf = tk.Frame(s, bg=C_PANEL)
        sf.grid(row=11, column=0, sticky="ew", padx=10, pady=(0,4))
        # _silence_var already created in __init__; reuse it here
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
                          -70, -10, -40, 1, 12)
        self._prop_slider(s, "Padding de áudio (ms)", "padding",
                          0, 500, 150, 10, 13)
        self._prop_slider(s, "Fala mínima (ms)", "min_segment_ms",
                          200, 2000, 300, 100, 14)

        # -- Color Grade ---------------------------------------------------
        self._section(s, "COLOR GRADE", 15)
        self._color_enabled = tk.BooleanVar(value=True)
        cf = tk.Frame(s, bg=C_PANEL)
        cf.grid(row=16, column=0, sticky="ew", padx=10, pady=(0,4))
        cf.grid_columnconfigure(1, weight=1)
        self._check_frame(cf, "Aplicar grade", self._color_enabled, 0,
                          command=self._schedule_preview)
        # Preset dropdown
        self._preset_var = tk.StringVar(value="CapCut ref")
        ctk.CTkOptionMenu(cf, values=["CapCut ref","Cinematico","Neutro","Vintage"],
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
                               row=17 + row_off)

        # -- Extra color controls sub-frame at row 23 (avoids renumbering) --
        cg_extra = tk.Frame(s, bg=C_PANEL)
        cg_extra.grid(row=23, column=0, sticky="ew")
        cg_extra.grid_columnconfigure(0, weight=1)

        for _row_off, (_lbl, _key, _lo, _hi, _def, _fc, _pc) in enumerate([
            ("Tint",       "tint",       -100, 100,  0, "#2a3822", "#dd9944"),
            ("Realces",    "highlights", -100, 100,  0, "#33251a", "#ffaa44"),
            ("Brancos",    "whites",     -100, 100,  0, "#333322", "#eeeebb"),
            ("Negros",     "blacks",     -100, 100,  0, "#111111", "#777777"),
            ("Vivacidade", "vibrance",   -100, 100,  0, "#1a2233", "#44aadd"),
        ]):
            self._color_slider(cg_extra, _lbl, _key, _lo, _hi, _def, _fc, _pc,
                               row=_row_off)

        # LUT loader
        _lut_row = tk.Frame(cg_extra, bg=C_PANEL)
        _lut_row.grid(row=5, column=0, sticky="ew", padx=10, pady=(4, 2))
        _lut_row.grid_columnconfigure(1, weight=1)
        tk.Label(_lut_row, text="LUT .cube:", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9), anchor="w").grid(row=0, column=0, sticky="w")
        self._lut_name_lbl = tk.Label(
            _lut_row, text="Nenhum", bg=C_PANEL, fg=C_TEXT,
            font=("Segoe UI", 9), anchor="w",
        )
        self._lut_name_lbl.grid(row=0, column=1, sticky="ew", padx=4)
        tk.Button(_lut_row, text="...", command=self._pick_lut,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0,
                  ).grid(row=0, column=2, padx=(0, 2))
        tk.Button(_lut_row, text="×", command=self._clear_lut,
                  bg=C_SURFACE, fg=C_MUTED, relief="flat", padx=4,
                  font=("Segoe UI", 9), cursor="hand2", bd=0,
                  ).grid(row=0, column=3)

        # Colour wheels header
        _wh_hdr = tk.Frame(cg_extra, bg=C_PANEL)
        _wh_hdr.grid(row=6, column=0, sticky="ew", padx=10, pady=(10, 2))
        tk.Label(_wh_hdr, text="RODAS DE COR", bg=C_PANEL, fg=C_ACCENT2,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(_wh_hdr, text="(duplo-clique para resetar)",
                 bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 7)).pack(side="left", padx=6)

        # Three colour wheels: Lift / Gamma / Gain
        _wheels_row = tk.Frame(cg_extra, bg=C_PANEL)
        _wheels_row.grid(row=7, column=0, sticky="ew", padx=6, pady=(0, 8))
        for _wk, _wl in [("lift", "Sombras"), ("gamma", "Tom Médio"), ("gain", "Realces")]:
            _wf = tk.Frame(_wheels_row, bg=C_PANEL)
            _wf.pack(side="left", expand=True)
            _wc = tk.Canvas(
                _wf, width=80, height=80, bg="#0a0912",
                highlightthickness=1, highlightbackground=C_BORDER,
                cursor="crosshair",
            )
            _wc.pack(pady=(0, 2))
            self._wheel_canvases[_wk] = _wc
            self._init_color_wheel(_wc, _wk)
            tk.Label(_wf, text=_wl, bg=C_PANEL, fg=C_MUTED,
                     font=("Segoe UI", 8)).pack()

        # -- Bokeh ---------------------------------------------------------
        self._section(s, "BOKEH  (desfoque de fundo)", 24)
        self._bokeh_slider = self._prop_slider(
            s, "Intensidade", "bokeh", 0, 100, 0, 1, 25,
            suffix="%", color="#223366", prog="#6699dd")

        # -- Audio ---------------------------------------------------------
        self._section(s, "ÁUDIO", 26)
        self._noise_var = tk.BooleanVar(value=False)
        self._audio_normalize_var = tk.BooleanVar(value=True)
        self._audio_voice_filter_var = tk.BooleanVar(value=False)
        self._audio_compressor_var = tk.BooleanVar(value=False)
        self._check(s, "Redução de ruído leve", self._noise_var, 27)
        self._check(s, "Nivelamento automatico loudnorm", self._audio_normalize_var, 28)
        self._check(s, "Filtro de voz (corta graves/agudos)", self._audio_voice_filter_var, 29)
        self._check(s, "Compressao leve de fala", self._audio_compressor_var, 30)

        mf = tk.Frame(s, bg=C_PANEL)
        mf.grid(row=31, column=0, sticky="ew", padx=10, pady=(0,6))
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

        # -- Extra outputs -------------------------------------------------
        self._section(s, "SAÍDAS EXTRAS", 32)
        self._gen_thumb_var  = tk.BooleanVar(value=False)
        self._gen_vert_var   = tk.BooleanVar(value=False)
        self._check(s, "Gerar 5 thumbnails profissionais", self._gen_thumb_var, 33)
        self._check(s, "Gerar versão vertical 9:16", self._gen_vert_var, 34)

        # -- Export quality (CRF) -----------------------------------------
        _crf_frame = tk.Frame(s, bg=C_PANEL)
        _crf_frame.grid(row=35, column=0, sticky="ew", padx=10, pady=(4, 2))
        _crf_frame.grid_columnconfigure(1, weight=1)
        _crf_lbl = tk.Label(_crf_frame, text=f"CRF: {self._crf_var.get()}",
                            bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9))
        tk.Label(_crf_frame, text="Qualidade export", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        _crf_lbl.grid(row=0, column=2, sticky="e", padx=(4, 0))
        ctk.CTkSlider(
            _crf_frame, from_=15, to=28, number_of_steps=13,
            variable=self._crf_var, height=14,
            button_color=C_ACCENT, progress_color=C_ACCENT, fg_color=C_SURFACE,
            command=lambda _v: _crf_lbl.configure(text=f"CRF: {self._crf_var.get()}"),
        ).grid(row=0, column=1, sticky="ew", padx=6)

        # -- Preview update btn --------------------------------------------
        ctk.CTkButton(s, text="Atualizar preview",
                      height=32, corner_radius=6,
                      fg_color=C_SURFACE, hover_color=C_BORDER,
                      font=ctk.CTkFont(size=12),
                      command=self._update_color_preview).grid(
            row=36, column=0, padx=10, pady=(8,4), sticky="ew")

        self._build_editor_assets_panel(s, 37)

    def _build_editor_assets_panel(self, parent, row: int) -> None:
        # -- TEXTO section -------------------------------------------------
        self._section(parent, "TEXTO", row)

        from src.core.text_render import list_system_fonts

        # Text content input
        self._text_panel_entry = tk.Text(
            parent,
            height=3,
            width=24,
            bg=C_SURFACE,
            fg=C_TEXT,
            insertbackground=C_TEXT,
            relief="flat",
            wrap="word",
            font=("Segoe UI", 9),
        )
        self._text_panel_entry.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(4, 2))

        # Size slider
        self._text_panel_size_var = tk.IntVar(value=100)
        size_frame = tk.Frame(parent, bg=C_PANEL)
        size_frame.grid(row=row + 2, column=0, sticky="ew", padx=10, pady=2)
        size_frame.grid_columnconfigure(1, weight=1)
        tk.Label(size_frame, text="Tamanho:", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        _text_size_lbl = tk.Label(size_frame, text="100%", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9))
        _text_size_lbl.grid(row=0, column=2, sticky="e", padx=(4, 0))
        tk.Scale(
            size_frame,
            from_=50, to=200,
            orient="horizontal",
            variable=self._text_panel_size_var,
            bg=C_PANEL, fg=C_TEXT,
            highlightthickness=0, bd=0,
            troughcolor=C_SURFACE,
            command=lambda _v: _text_size_lbl.configure(text=f"{self._text_panel_size_var.get()}%"),
        ).grid(row=0, column=1, sticky="ew", padx=4)

        # Color picker
        self._text_panel_color = tk.StringVar(value="#ffffff")
        color_frame = tk.Frame(parent, bg=C_PANEL)
        color_frame.grid(row=row + 3, column=0, sticky="ew", padx=10, pady=2)
        color_frame.grid_columnconfigure(1, weight=1)
        tk.Label(color_frame, text="Cor:", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        _color_preview_btn = tk.Button(
            color_frame,
            textvariable=self._text_panel_color,
            bg="#ffffff", fg="#000000",
            relief="flat", padx=6,
            font=("Segoe UI", 9), cursor="hand2", bd=1,
        )
        _color_preview_btn.grid(row=0, column=1, sticky="ew", padx=(6, 0))
        def _pick_text_panel_color():
            from tkinter import colorchooser
            result = colorchooser.askcolor(color=self._text_panel_color.get(), title="Escolher cor do texto")
            if result and result[1]:
                self._text_panel_color.set(result[1])
                _color_preview_btn.configure(bg=result[1])
        _color_preview_btn.configure(command=_pick_text_panel_color)

        # Font dropdown
        self._text_panel_font_var = tk.StringVar(value="Arial")
        font_frame = tk.Frame(parent, bg=C_PANEL)
        font_frame.grid(row=row + 4, column=0, sticky="ew", padx=10, pady=2)
        font_frame.grid_columnconfigure(1, weight=1)
        tk.Label(font_frame, text="Fonte:", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        _font_combo = ttk.Combobox(
            font_frame,
            textvariable=self._text_panel_font_var,
            values=list_system_fonts(),
            state="readonly",
            font=("Segoe UI", 9),
            width=18,
        )
        _font_combo.grid(row=0, column=1, sticky="ew", padx=(6, 0))

        # Alignment
        self._text_panel_align_var = tk.StringVar(value="center")
        align_frame = tk.Frame(parent, bg=C_PANEL)
        align_frame.grid(row=row + 5, column=0, sticky="ew", padx=10, pady=2)
        tk.Label(align_frame, text="Alinhamento:", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9)).pack(side="left")
        for _align_val, _align_lbl in [("left", "◀"), ("center", "Centro"), ("right", "▶")]:
            tk.Radiobutton(
                align_frame,
                text=_align_lbl,
                variable=self._text_panel_align_var,
                value=_align_val,
                bg=C_PANEL, fg=C_TEXT,
                selectcolor=C_SURFACE,
                activebackground=C_PANEL,
                activeforeground=C_TEXT,
                font=("Segoe UI", 9),
                relief="flat",
            ).pack(side="left", padx=(4, 0))

        # Bold / Italic / Shadow / Stroke checkboxes
        self._text_panel_bold_var = tk.BooleanVar(value=False)
        self._text_panel_italic_var = tk.BooleanVar(value=False)
        self._text_panel_shadow_var = tk.BooleanVar(value=False)
        self._text_panel_stroke_var = tk.BooleanVar(value=False)
        checks_frame1 = tk.Frame(parent, bg=C_PANEL)
        checks_frame1.grid(row=row + 6, column=0, sticky="ew", padx=10, pady=(2, 0))
        for _var, _lbl in [(self._text_panel_bold_var, "Negrito"), (self._text_panel_italic_var, "Itálico")]:
            tk.Checkbutton(
                checks_frame1, text=_lbl, variable=_var,
                bg=C_PANEL, fg=C_TEXT, selectcolor=C_SURFACE,
                activebackground=C_PANEL, activeforeground=C_TEXT,
                font=("Segoe UI", 9), relief="flat",
            ).pack(side="left", padx=(0, 8))
        checks_frame2 = tk.Frame(parent, bg=C_PANEL)
        checks_frame2.grid(row=row + 7, column=0, sticky="ew", padx=10, pady=(0, 4))
        for _var, _lbl in [(self._text_panel_shadow_var, "Sombra"), (self._text_panel_stroke_var, "Contorno")]:
            tk.Checkbutton(
                checks_frame2, text=_lbl, variable=_var,
                bg=C_PANEL, fg=C_TEXT, selectcolor=C_SURFACE,
                activebackground=C_PANEL, activeforeground=C_TEXT,
                font=("Segoe UI", 9), relief="flat",
            ).pack(side="left", padx=(0, 8))

        # Insert button
        tk.Button(
            parent, text="Inserir na timeline",
            command=self._insert_text_clip_from_panel,
            bg=C_ACCENT, fg="#ffffff",
            relief="flat", padx=8,
            font=("Segoe UI", 9, "bold"), cursor="hand2", bd=0,
        ).grid(row=row + 8, column=0, sticky="ew", padx=10, pady=(2, 8))

        # Shift the remaining rows by 9
        row = row + 9

        # -- MÍDIAS DO PROJETO section -------------------------------------
        self._section(parent, "MÍDIAS DO PROJETO", row)
        if self._media_listbox is None:
            self._media_listbox = tk.Listbox(
                parent,
                bg=C_SURFACE,
                fg=C_TEXT,
                selectbackground=C_ACCENT,
                selectforeground="#ffffff",
                relief="flat",
                height=4,
                font=("Segoe UI", 9),
                activestyle="none",
            )
            self._media_listbox.grid(row=row + 1, column=0, sticky="ew", padx=10, pady=(4, 4))
            self._media_listbox.bind("<Double-Button-1>", lambda _e: self._open_or_insert_selected_project_media())
            self._media_listbox.bind("<ButtonPress-1>", self._media_listbox_press)
            self._media_listbox.bind("<B1-Motion>", self._media_listbox_drag)
            self._media_listbox.bind("<ButtonRelease-1>", self._media_listbox_release)
        media_actions = tk.Frame(parent, bg=C_PANEL)
        media_actions.grid(row=row + 2, column=0, sticky="ew", padx=10, pady=(0, 4))
        media_actions.grid_columnconfigure(0, weight=1)
        media_actions.grid_columnconfigure(1, weight=1)
        media_actions.grid_columnconfigure(2, weight=1)
        tk.Button(media_actions, text="Adicionar mídia", command=self._add_project_media,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Button(media_actions, text="Trocar overlay", command=self._assign_selected_media_to_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=1, sticky="ew", padx=4)
        tk.Button(media_actions, text="Abrir principal", command=self._load_selected_project_media,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        tk.Button(media_actions, text="Inserir na timeline", command=self._insert_selected_media_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=0, columnspan=2, sticky="ew", padx=(0, 4), pady=(4, 0))
        tk.Button(media_actions, text="Remover", command=self._remove_selected_project_media,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=2, sticky="ew", padx=(4, 0), pady=(4, 0))

        self._insert_duration_label = self._inspector_slider(
            parent, "Duração inserir", self._insert_duration_var, 1, 15, row + 3, suffix="s", command=self._refresh_project_status
        )

        self._section(parent, "CLIPE SELECIONADO", row + 4)
        mode_label = tk.Label(
            parent,
            textvariable=self._inspector_mode_var,
            bg=C_PANEL,
            fg=C_ACCENT2,
            anchor="w",
            font=("Segoe UI", 9, "bold"),
        )
        mode_label.grid(row=row + 4, column=0, sticky="ew", padx=12, pady=(16, 0))
        self._remember_clip_inspector_row(row + 4, mode_label)
        self._clip_label_entry = ctk.CTkEntry(
            parent,
            textvariable=self._clip_label_var,
            placeholder_text="Nome/texto do clipe",
            fg_color=C_SURFACE,
            border_color=C_BORDER,
            text_color=C_TEXT,
            placeholder_text_color=C_MUTED,
            font=ctk.CTkFont(size=11),
            height=30,
        )
        self._clip_label_entry.grid(row=row + 5, column=0, sticky="ew", padx=10, pady=2)
        self._remember_clip_inspector_row(row + 5, self._clip_label_entry)
        self._clip_label_entry.bind("<Return>", lambda _e: self._apply_clip_inspector())
        self._clip_label_entry.bind("<FocusOut>", lambda _e: self._apply_clip_inspector())

        self._clip_scale_label = self._inspector_slider(
            parent, "Escala do vídeo", self._clip_scale_var, 25, 300, row + 6, suffix="%"
        )
        self._clip_pos_x_label = self._inspector_slider(
            parent, "Posição X", self._clip_pos_x_var, -100, 100, row + 7, suffix="%"
        )
        self._clip_pos_y_label = self._inspector_slider(
            parent, "Posição Y", self._clip_pos_y_var, -100, 100, row + 8, suffix="%"
        )
        self._clip_volume_label = self._inspector_slider(
            parent, "Volume do clipe", self._clip_volume_var, 0, 200, row + 9, suffix="%"
        )
        self._clip_text_x_label = self._inspector_slider(
            parent, "Texto X", self._clip_text_x_var, -100, 100, row + 10, suffix="%"
        )
        self._clip_text_y_label = self._inspector_slider(
            parent, "Texto Y", self._clip_text_y_var, 0, 100, row + 11, suffix="%"
        )
        self._clip_text_size_label = self._inspector_slider(
            parent, "Tamanho texto", self._clip_text_size_var, 50, 220, row + 12, suffix="%"
        )
        text_bg = tk.Frame(parent, bg=C_PANEL)
        text_bg.grid(row=row + 13, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._remember_clip_inspector_row(row + 13, text_bg)
        text_bg.grid_columnconfigure(1, weight=1)
        text_bg.grid_columnconfigure(2, weight=1)
        tk.Checkbutton(
            text_bg,
            text="Fundo do texto",
            variable=self._clip_text_bg_var,
            command=self._apply_clip_inspector,
            bg=C_PANEL,
            fg=C_TEXT,
            selectcolor=C_SURFACE,
            activebackground=C_PANEL,
            activeforeground=C_TEXT,
            font=("Segoe UI", 9),
            relief="flat",
        ).grid(row=0, column=0, sticky="w")
        tk.Label(text_bg, text="Conteudo", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 8)).grid(row=1, column=0, sticky="nw", pady=(6, 0))
        self._clip_text_bg_color_entry = ctk.CTkEntry(
            text_bg,
            textvariable=self._clip_text_bg_color_var,
            fg_color=C_SURFACE,
            border_color=C_BORDER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=10),
            width=82,
            height=26,
        )
        self._clip_text_bg_color_entry.grid(row=0, column=1, sticky="e", padx=(6, 0))
        self._clip_text_bg_color_entry.bind("<Return>", lambda _e: self._apply_clip_inspector())
        self._clip_text_bg_color_entry.bind("<FocusOut>", lambda _e: self._apply_clip_inspector())
        self._clip_text_color_entry = ctk.CTkEntry(
            text_bg,
            textvariable=self._clip_text_color_var,
            fg_color=C_SURFACE,
            border_color=C_BORDER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=10),
            width=82,
            height=26,
        )
        self._clip_text_color_entry.grid(row=0, column=2, sticky="e", padx=(6, 0))
        self._clip_text_color_entry.bind("<Return>", lambda _e: self._apply_clip_inspector())
        self._clip_text_color_entry.bind("<FocusOut>", lambda _e: self._apply_clip_inspector())
        self._clip_text_content = tk.Text(
            text_bg,
            bg=C_SURFACE,
            fg=C_TEXT,
            insertbackground=C_TEXT,
            relief="flat",
            height=3,
            wrap="word",
            font=("Segoe UI", 9),
            undo=True,
        )
        self._clip_text_content.grid(row=1, column=1, columnspan=2, sticky="ew", padx=(6, 0), pady=(6, 0))
        self._clip_text_content.bind("<FocusOut>", lambda _e: self._apply_clip_inspector())
        self._clip_text_content.bind("<Control-Return>", lambda _e: (self._apply_clip_inspector(), "break")[1])
        swatches = tk.Frame(parent, bg=C_PANEL)
        swatches.grid(row=row + 14, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._remember_clip_inspector_row(row + 14, swatches)
        swatches.grid_columnconfigure(1, weight=1)
        tk.Label(swatches, text="Cor", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w")
        self._build_text_color_swatches(swatches, row=0, column=1, target="text")
        tk.Label(swatches, text="Fundo", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 8)).grid(row=1, column=0, sticky="w")
        self._build_text_color_swatches(swatches, row=1, column=1, target="background")
        tr = tk.Frame(parent, bg=C_PANEL)
        tr.grid(row=row + 15, column=0, sticky="ew", padx=10, pady=(2, 4))
        self._remember_clip_inspector_row(row + 15, tr)
        tr.grid_columnconfigure(1, weight=1)
        tk.Label(tr, text="Transição", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        ctk.CTkOptionMenu(
            tr,
            values=["Corte", "Fade", "Dissolver", "Wipe Esq", "Wipe Dir", "Zoom"],
            variable=self._clip_transition_var,
            command=lambda _v: self._apply_clip_inspector(),
            fg_color=C_SURFACE,
            button_color=C_ACCENT,
            text_color=C_TEXT,
            width=140,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, sticky="e")
        text_actions = tk.Frame(parent, bg=C_PANEL)
        text_actions.grid(row=row + 16, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._remember_clip_inspector_row(row + 16, text_actions)
        text_actions.grid_columnconfigure(0, weight=1)
        text_actions.grid_columnconfigure(1, weight=1)
        text_actions.grid_columnconfigure(2, weight=1)
        tk.Button(text_actions, text="Novo texto", command=self._add_text_at_playhead,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Button(text_actions, text="Aplicar texto", command=self._apply_clip_inspector,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=1, sticky="ew", padx=4)
        tk.Button(text_actions, text="Duplicar", command=self._duplicate_selected_timeline_item,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        tk.Button(text_actions, text="Trás", command=lambda: self._move_selected_layer(-1),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(4, 0))
        tk.Button(text_actions, text="Frente", command=lambda: self._move_selected_layer(1),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(4, 0), pady=(4, 0))
        visual_actions = tk.Frame(parent, bg=C_PANEL)
        visual_actions.grid(row=row + 17, column=0, sticky="ew", padx=10, pady=(0, 8))
        self._remember_clip_inspector_row(row + 17, visual_actions)
        visual_actions.grid_columnconfigure(0, weight=1)
        visual_actions.grid_columnconfigure(1, weight=1)
        visual_actions.grid_columnconfigure(2, weight=1)
        self._visual_primary_button = tk.Button(visual_actions, text="Aplicar visual", command=self._apply_clip_inspector,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0)
        self._visual_primary_button.grid(row=0, column=0, sticky="ew", padx=(0, 4))
        self._visual_apply_button = tk.Button(visual_actions, text="Aplicar ajustes", command=self._apply_clip_inspector,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0)
        self._visual_apply_button.grid(row=0, column=1, sticky="ew", padx=4)
        tk.Button(visual_actions, text="Duplicar", command=self._duplicate_selected_timeline_item,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=2, sticky="ew", padx=(4, 0))
        tk.Button(visual_actions, text="Trás", command=lambda: self._move_selected_layer(-1),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=0, sticky="ew", padx=(0, 4), pady=(4, 0))
        tk.Button(visual_actions, text="Frente", command=lambda: self._move_selected_layer(1),
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=1, columnspan=2, sticky="ew", padx=(4, 0), pady=(4, 0))
        chroma = tk.Frame(parent, bg=C_PANEL)
        chroma.grid(row=row + 18, column=0, sticky="ew", padx=10, pady=(0, 4))
        self._remember_clip_inspector_row(row + 18, chroma)
        chroma.grid_columnconfigure(1, weight=1)
        tk.Checkbutton(
            chroma,
            text="Chroma key",
            variable=self._clip_chroma_var,
            command=self._apply_clip_inspector,
            bg=C_PANEL,
            fg=C_TEXT,
            selectcolor=C_SURFACE,
            activebackground=C_PANEL,
            activeforeground=C_TEXT,
            font=("Segoe UI", 9),
            relief="flat",
        ).grid(row=0, column=0, sticky="w")
        self._clip_chroma_color_entry = ctk.CTkEntry(
            chroma,
            textvariable=self._clip_chroma_color_var,
            fg_color=C_SURFACE,
            border_color=C_BORDER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=10),
            width=82,
            height=26,
        )
        self._clip_chroma_color_entry.grid(row=0, column=1, sticky="e", padx=(6, 0))
        self._clip_chroma_color_entry.bind("<Return>", lambda _e: self._apply_clip_inspector())
        self._clip_chroma_color_entry.bind("<FocusOut>", lambda _e: self._apply_clip_inspector())
        tk.Button(chroma, text="Conta-gotas", command=self._arm_chroma_picker,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._clip_chroma_tolerance_label = self._inspector_slider(
            parent, "Tolerancia chroma", self._clip_chroma_tolerance_var, 5, 160, row + 19
        )
        self._clip_duration_label = self._inspector_slider(
            parent, "Duracao item", self._clip_duration_var, 1, 120, row + 20, suffix="s"
        )
        self._clip_opacity_label = self._inspector_slider(
            parent, "Opacidade", self._clip_opacity_var, 0, 100, row + 21, suffix="%"
        )
        # ── Etapa 6: Speed control ────────────────────────────────────────────
        sp_row = tk.Frame(parent, bg=C_PANEL)
        sp_row.grid(row=row + 22, column=0, sticky="ew", padx=10, pady=(2, 4))
        self._remember_clip_inspector_row(row + 22, sp_row)
        sp_row.grid_columnconfigure(1, weight=1)
        tk.Label(sp_row, text="Velocidade", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        _SPEED_OPTIONS = ["0.25×", "0.5×", "0.75×", "1.0×", "1.25×", "1.5×", "2.0×", "4.0×"]
        ctk.CTkOptionMenu(
            sp_row,
            values=_SPEED_OPTIONS,
            variable=self._clip_speed_var,
            command=lambda _v: self._apply_clip_inspector(),
            fg_color=C_SURFACE,
            button_color=C_ACCENT,
            text_color=C_TEXT,
            width=100,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, sticky="e")
        # ── Etapa C: Pan / Fade ───────────────────────────────────────────────
        self._clip_pan_label = self._inspector_slider(
            parent, "Pan L/R", self._clip_pan_var, -100, 100, row + 23, suffix=""
        )
        self._clip_fade_in_label = self._inspector_slider(
            parent, "Fade entrada", self._clip_fade_in_var, 0, 10, row + 24, suffix="s"
        )
        self._clip_fade_out_label = self._inspector_slider(
            parent, "Fade saída", self._clip_fade_out_var, 0, 10, row + 25, suffix="s"
        )
        # ── Etapa D: Rotation + Blend Mode ────────────────────────────────────
        self._clip_rotation_label = self._inspector_slider(
            parent, "Rotação (°)", self._clip_rotation_var, -180, 180, row + 26, suffix="°"
        )
        blend_row = tk.Frame(parent, bg=C_PANEL)
        blend_row.grid(row=row + 27, column=0, sticky="ew", padx=10, pady=(2, 4))
        self._remember_clip_inspector_row(row + 27, blend_row)
        blend_row.grid_columnconfigure(1, weight=1)
        tk.Label(blend_row, text="Modo de mistura", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        _BLEND_OPTIONS = ["Normal", "Screen", "Multiply", "Overlay", "Add", "Darken", "Lighten", "Soft Light"]
        ctk.CTkOptionMenu(
            blend_row,
            values=_BLEND_OPTIONS,
            variable=self._clip_blend_var,
            command=lambda _v: self._apply_clip_inspector(),
            fg_color=C_SURFACE,
            button_color=C_ACCENT,
            text_color=C_TEXT,
            width=130,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, sticky="e")
        # ── Etapa E: Per-clip Crop ────────────────────────────────────────────
        crop_frame = tk.Frame(parent, bg=C_PANEL)
        crop_frame.grid(row=row + 28, column=0, sticky="ew", padx=10, pady=(2, 4))
        self._remember_clip_inspector_row(row + 28, crop_frame)
        crop_frame.grid_columnconfigure((0, 1), weight=1)
        tk.Label(crop_frame, text="Recorte (%)", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 2))
        for _crop_lbl, _crop_var, _crop_r, _crop_c in (
            ("Cima",  self._clip_crop_top_var,    1, 0),
            ("Baixo", self._clip_crop_bottom_var, 1, 1),
            ("Esq",   self._clip_crop_left_var,   2, 0),
            ("Dir",   self._clip_crop_right_var,  2, 1),
        ):
            _sub = tk.Frame(crop_frame, bg=C_PANEL)
            _sub.grid(row=_crop_r, column=_crop_c, sticky="ew", padx=2, pady=1)
            _sub.grid_columnconfigure(1, weight=1)
            tk.Label(_sub, text=_crop_lbl, bg=C_PANEL, fg=C_MUTED,
                     font=("Segoe UI", 8)).grid(row=0, column=0, sticky="w")
            _vl = tk.Label(_sub, text="0%", bg=C_PANEL, fg=C_MUTED,
                           font=("Segoe UI", 8), width=4, anchor="e")
            _vl.grid(row=0, column=2, sticky="e")
            ctk.CTkSlider(
                _sub, from_=0, to=50, number_of_steps=50,
                variable=_crop_var, height=12,
                button_color=C_ACCENT, progress_color=C_ACCENT, fg_color=C_SURFACE,
                command=lambda _val, v=_crop_var, lw=_vl: (
                    lw.configure(text=f"{int(v.get())}%"),
                    self._apply_clip_inspector(),
                ),
            ).grid(row=0, column=1, sticky="ew", padx=4)
        # ── Etapa F: Color Correction ─────────────────────────────────────────
        self._clip_brightness_label = self._inspector_slider(
            parent, "Brilho", self._clip_brightness_var, -100, 100, row + 29, suffix=""
        )
        self._clip_contrast_label = self._inspector_slider(
            parent, "Contraste", self._clip_contrast_var, -100, 100, row + 30, suffix=""
        )
        self._clip_saturation_label = self._inspector_slider(
            parent, "Saturação", self._clip_saturation_var, -100, 100, row + 31, suffix=""
        )
        # ── Status do Projeto ─────────────────────────────────────────────────
        self._section(parent, "STATUS DO PROJETO", row + 32)
        tk.Label(
            parent,
            textvariable=self._project_status_var,
            bg=C_PANEL,
            fg=C_MUTED,
            justify="left",
            anchor="w",
            wraplength=260,
            font=("Segoe UI", 9),
        ).grid(row=row + 33, column=0, sticky="ew", padx=12, pady=(0, 10))
        self._refresh_media_list()
        self._refresh_clip_inspector()
        self._refresh_project_status()

    def _inspector_slider(self, parent, label: str, var: tk.DoubleVar, lo: int, hi: int, row: int, suffix: str = "", command=None) -> tk.Label:
        frame = tk.Frame(parent, bg=C_PANEL)
        frame.grid(row=row, column=0, sticky="ew", padx=10, pady=2)
        self._remember_clip_inspector_row(row, frame)
        frame.grid_columnconfigure(1, weight=1)
        value_lbl = tk.Label(frame, text=f"{int(var.get())}{suffix}", bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9))
        tk.Label(frame, text=label, bg=C_PANEL, fg=C_MUTED, font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        value_lbl.grid(row=0, column=2, sticky="e", padx=(6, 0))
        slider = ctk.CTkSlider(
            frame,
            from_=lo,
            to=hi,
            number_of_steps=hi - lo,
            variable=var,
            height=14,
            button_color=C_ACCENT,
            progress_color=C_ACCENT,
            fg_color=C_SURFACE,
            command=lambda _v: (value_lbl.configure(text=f"{int(var.get())}{suffix}"), command() if command else self._apply_clip_inspector()),
        )
        slider.grid(row=0, column=1, sticky="ew", padx=6)
        return value_lbl

    def _remember_clip_inspector_row(self, row: int, *widgets: tk.Widget) -> None:
        bucket = self._clip_inspector_rows.setdefault(int(row), [])
        for widget in widgets:
            if widget not in bucket:
                bucket.append(widget)

    def _set_clip_inspector_visible_rows(self, rows: set[int]) -> None:
        stale: list[tuple[int, tk.Widget]] = []
        for row, widgets in self._clip_inspector_rows.items():
            visible = row in rows
            for widget in widgets:
                try:
                    if visible:
                        widget.grid()
                    else:
                        widget.grid_remove()
                except Exception:
                    # Widget belongs to a destroyed parent (e.g. after new project opened)
                    stale.append((row, widget))
        # Evict stale references so they don't accumulate
        for row, widget in stale:
            bucket = self._clip_inspector_rows.get(row)
            if bucket and widget in bucket:
                bucket.remove(widget)

    def _clip_inspector_visible_rows(self, mode: str) -> set[int]:
        if not self._clip_inspector_rows:
            return set()
        return _clip_inspector_rows_for_mode(min(self._clip_inspector_rows), mode)

    def _build_text_color_swatches(self, parent: tk.Frame, row: int, column: int, target: str) -> None:
        frame = tk.Frame(parent, bg=C_PANEL)
        frame.grid(row=row, column=column, sticky="e")
        colors = ["#ffffff", "#ffee11", "#ff4d4d", "#4dd6ff", "#7cff7c", "#000000"]
        for idx, color in enumerate(colors):
            tk.Button(
                frame,
                text="",
                command=lambda value=color, kind=target: self._apply_text_color_preset(kind, value),
                bg=color,
                activebackground=color,
                relief="flat",
                width=2,
                height=1,
                cursor="hand2",
                bd=0,
                highlightthickness=1,
                highlightbackground=C_BORDER,
            ).grid(row=0, column=idx, padx=(3, 0))

    def _apply_text_color_preset(self, target: str, color: str) -> None:
        value = _normalize_hex_color(color, "#ffffff" if target == "text" else "#000000")
        if target == "background":
            self._clip_text_bg_var.set(True)
            self._clip_text_bg_color_var.set(value)
        else:
            self._clip_text_color_var.set(value)
        self._apply_clip_inspector()

    def _refresh_media_list(self) -> None:
        if self._media_listbox is None:
            return
        self._media_listbox.delete(0, "end")
        for path in self._project_media_paths:
            self._media_listbox.insert("end", _media_display_name(path))
        self._refresh_project_status()

    def _selected_project_media_path(self) -> Optional[str]:
        if self._media_listbox is None:
            return None
        selection = self._media_listbox.curselection()
        if not selection:
            return None
        index = int(selection[0])
        if 0 <= index < len(self._project_media_paths):
            return self._project_media_paths[index]
        return None

    def _show_drag_ghost(self, text: str, root_x: int, root_y: int) -> None:
        """Show or update the drag ghost window near the cursor."""
        if not hasattr(self, "_drag_ghost_window") or self._drag_ghost_window is None:
            win = tk.Toplevel(self.root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            win.attributes("-alpha", 0.85)
            lbl = tk.Label(win, text=text, bg="#1a1a2e", fg="#c8e9dc",
                           font=("Segoe UI", 9), padx=8, pady=4,
                           relief="flat", bd=0)
            lbl.pack()
            self._drag_ghost_window = win
            self._drag_ghost_label = lbl
        else:
            self._drag_ghost_label.configure(text=text)
        self._drag_ghost_window.geometry(f"+{root_x + 16}+{root_y + 16}")

    def _hide_drag_ghost(self) -> None:
        if hasattr(self, "_drag_ghost_window") and self._drag_ghost_window:
            self._drag_ghost_window.destroy()
            self._drag_ghost_window = None

    def _show_tl_hover_thumb(self, time_s: float) -> None:
        if abs((self._tl_hover_time_s or -9999) - time_s) > 0.6:
            return
        if not self.video_path or not os.path.isfile(self.video_path):
            return
        try:
            import cv2
            from PIL import Image, ImageTk
            cap = cv2.VideoCapture(self.video_path)
            cap.set(cv2.CAP_PROP_POS_MSEC, time_s * 1000)
            ret, frame = cap.read()
            cap.release()
            if not ret:
                return
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            img = Image.fromarray(frame_rgb).resize((160, 90), Image.LANCZOS)
            photo = ImageTk.PhotoImage(img)
            rx, ry = self.root.winfo_pointerxy()
            if self._tl_hover_thumb_window is None:
                win = tk.Toplevel(self.root)
                win.overrideredirect(True)
                win.attributes("-topmost", True)
                lbl = tk.Label(win, bg="#000000", bd=0)
                lbl.pack()
                self._tl_hover_thumb_window = win
                self._tl_hover_thumb_label = lbl
            self._tl_hover_thumb_label.configure(image=photo)
            self._tl_hover_thumb_label.image = photo  # keep reference
            self._tl_hover_thumb_window.geometry(f"160x90+{rx + 16}+{ry - 106}")
        except Exception:
            pass

    def _hide_tl_hover_thumb(self) -> None:
        if self._tl_hover_thumb_window is not None:
            self._tl_hover_thumb_window.destroy()
            self._tl_hover_thumb_window = None
        self._tl_hover_time_s = None

    def _media_listbox_press(self, event: tk.Event) -> None:
        self._media_drag_path = None
        self._media_drag_preview_time = None
        if self._media_listbox is None:
            return
        index = self._media_listbox.nearest(int(event.y))
        if 0 <= index < len(self._project_media_paths):
            self._media_listbox.selection_clear(0, "end")
            self._media_listbox.selection_set(index)
            self._media_drag_path = self._project_media_paths[index]

    def _media_listbox_drag(self, event: tk.Event) -> None:
        if self._media_drag_path:
            self.root.configure(cursor="hand2")
            filename = Path(self._media_drag_path).name
            self._show_drag_ghost(filename, int(event.x_root), int(event.y_root))
            time_s = self._timeline_drop_time_from_root_xy(int(event.x_root), int(event.y_root))
            if time_s is None:
                if self._media_drag_preview_time is not None:
                    self._media_drag_preview_time = None
                    self._redraw_timeline()
                self._tb_status.configure(text=f"Solte na timeline para inserir: {filename}")
                return
            time_s, snapped = self._snap_media_insert_start(time_s)
            if self._media_drag_preview_time is None or abs(self._media_drag_preview_time - time_s) > 0.03:
                self._media_drag_preview_time = time_s
                self._redraw_timeline()
            snap_note = " | snap" if snapped else ""
            self._tb_status.configure(text=f"Solte em {_fmt(time_s)}: {filename}{snap_note}")

    def _media_listbox_release(self, event: tk.Event) -> str | None:
        self._hide_drag_ghost()
        path = self._media_drag_path
        self._media_drag_path = None
        self._media_drag_preview_time = None
        self.root.configure(cursor="")
        self._redraw_timeline()
        if not path:
            return None
        if not self._timeline_model or not self.video_path:
            self._tb_status.configure(text="Carregue um video principal antes de soltar midia na timeline.")
            return None
        time_s = self._timeline_drop_time_from_root_xy(int(event.x_root), int(event.y_root))
        if time_s is None:
            return None
        if self._insert_media_path_at_time(path, time_s):
            snap_note = " com snap" if self._last_media_insert_snapped else ""
            self._tb_status.configure(text=f"Midia inserida na timeline{snap_note}: {Path(path).name}.")
            return "break"
        return None

    def _timeline_drop_time_from_root_xy(self, root_x: int, root_y: int) -> Optional[float]:
        if not self._timeline_model or not self.video_path:
            return None
        x = int(root_x) - self._tl_canvas.winfo_rootx()
        y = int(root_y) - self._tl_canvas.winfo_rooty()
        if not _point_inside_rect(x, y, 0, 0, self._tl_canvas.winfo_width(), self._tl_canvas.winfo_height()):
            return None
        return self._timeline_click_time(x, *self._timeline_track_bounds(self._tl_canvas.winfo_width()))

    def _add_project_media(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Adicionar midia ao projeto",
            filetypes=[
                ("Midias", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v *.jpg *.jpeg *.png *.webp *.bmp"),
                ("Videos", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v"),
                ("Imagens", "*.jpg *.jpeg *.png *.webp *.bmp"),
                ("Todos", "*.*"),
            ]
        )
        if not paths:
            return
        media_paths = _merge_media_paths([], list(paths))
        if not media_paths:
            messagebox.showwarning("Midia incompativel", "Use um arquivo de video ou imagem compativel.")
            return
        added = self._register_project_media(media_paths)
        if self.video_path:
            self._save_project_video_path(self.video_path)
        else:
            self._save_project_media_paths()
        self._refresh_media_list()
        self._tb_status.configure(text=f"{added} midia(s) adicionada(s) ao projeto." if added else "Midias ja estavam no projeto.")

    def _load_selected_project_media(self) -> None:
        path = self._selected_project_media_path()
        if not path:
            self._tb_status.configure(text="Selecione uma mídia do projeto.")
            return
        if _is_image_path(path):
            if self._timeline_model and self.video_path:
                start_s = min(self._duration_s, max(0.0, self._current_frame / max(1.0, self._fps)))
                if self._insert_media_path_at_time(path, start_s):
                    self._tb_status.configure(text=f"Imagem inserida no playhead: {Path(path).name}.")
                return
            self._tb_status.configure(text="Imagem selecionada. Carregue um video principal para inserir na timeline.")
            return
        self._load_video(path)

    def _open_or_insert_selected_project_media(self) -> None:
        path = self._selected_project_media_path()
        if not path:
            self._tb_status.configure(text="Selecione uma midia do projeto.")
            return
        if _should_double_click_insert_media(path, self.video_path, self._timeline_model is not None):
            start_s = min(self._duration_s, max(0.0, self._current_frame / max(1.0, self._fps)))
            if self._insert_media_path_at_time(path, start_s):
                self._tb_status.configure(text=f"Midia inserida no playhead: {Path(path).name}.")
            return
        self._load_selected_project_media()

    def _remove_selected_project_media(self) -> None:
        path = self._selected_project_media_path()
        if not path:
            self._tb_status.configure(text="Selecione uma midia do projeto para remover.")
            return
        if _same_media_path(path, self.video_path):
            self._tb_status.configure(text="Nao remova o video principal. Use Abrir principal para trocar.")
            return
        if _media_path_used_in_timeline(path, self._timeline_model):
            self._tb_status.configure(text="Midia em uso na timeline. Remova/substitua os clipes antes.")
            return
        before = len(self._project_media_paths)
        self._project_media_paths = _remove_media_path(self._project_media_paths, path)
        if len(self._project_media_paths) == before:
            self._tb_status.configure(text="Midia nao encontrada no projeto.")
            return
        self._save_project_media_paths()
        self._refresh_media_list()
        self._tb_status.configure(text=f"Midia removida do projeto: {Path(path).name}.")

    def _selected_timeline_clip(self) -> Optional[TimelineClip]:
        if not self._timeline_model:
            return None
        if self._selected_overlay_index is not None:
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if 0 <= self._selected_overlay_index < len(overlay_clips):
                return overlay_clips[self._selected_overlay_index]
        if self._selected_text_index is not None:
            text_clips = _timeline_text_clips(self._timeline_model)
            if 0 <= self._selected_text_index < len(text_clips):
                return text_clips[self._selected_text_index]
        if self._selected_clip_index is None:
            return None
        clips = self._timeline_model.video_track.clips
        if 0 <= self._selected_clip_index < len(clips):
            return clips[self._selected_clip_index]
        return None

    def _refresh_clip_inspector(self) -> None:
        clip = self._selected_timeline_clip()
        self._clip_inspector_enabled = False
        rows = self._clip_inspector_visible_rows("none")
        if clip is None:
            self._inspector_mode_var.set("Nada selecionado")
            if hasattr(self, "_clip_label_entry"):
                self._clip_label_entry.configure(placeholder_text="Selecione um item da timeline")
            self._clip_label_var.set("")
            self._set_clip_text_content("")
            self._clip_scale_var.set(100.0)
            self._clip_opacity_var.set(100.0)
            self._clip_pos_x_var.set(0.0)
            self._clip_pos_y_var.set(0.0)
            self._clip_text_x_var.set(0.0)
            self._clip_text_y_var.set(72.0)
            self._clip_text_size_var.set(100.0)
            self._clip_text_color_var.set("#ffffff")
            self._clip_text_bg_var.set(True)
            self._clip_text_bg_color_var.set("#000000")
            self._clip_volume_var.set(100.0)
            self._clip_pan_var.set(0.0)
            self._clip_fade_in_var.set(0.0)
            self._clip_fade_out_var.set(0.0)
            self._clip_rotation_var.set(0.0)
            self._clip_blend_var.set("Normal")
            self._clip_crop_top_var.set(0.0)
            self._clip_crop_bottom_var.set(0.0)
            self._clip_crop_left_var.set(0.0)
            self._clip_crop_right_var.set(0.0)
            self._clip_brightness_var.set(0.0)
            self._clip_contrast_var.set(0.0)
            self._clip_saturation_var.set(0.0)
            self._clip_transition_var.set("Corte")
            self._clip_speed_var.set("1.0×")
            self._clip_chroma_var.set(False)
            self._clip_chroma_color_var.set("#00ff00")
            self._clip_chroma_tolerance_var.set(45.0)
            self._clip_duration_var.set(self._insert_duration_s())
        else:
            if clip.clip_type == "text":
                self._inspector_mode_var.set("Editando TEXTO (camada vazada)")
                if hasattr(self, "_clip_label_entry"):
                    self._clip_label_entry.configure(placeholder_text="Nome curto do texto")
                rows = self._clip_inspector_visible_rows("text")
            elif _is_movable_media_clip(clip):
                self._inspector_mode_var.set("Editando MIDIA visual")
                if hasattr(self, "_clip_label_entry"):
                    self._clip_label_entry.configure(placeholder_text="Nome da midia")
                rows = self._clip_inspector_visible_rows("visual")
            else:
                self._inspector_mode_var.set("Editando CLIPE de fala")
                if hasattr(self, "_clip_label_entry"):
                    self._clip_label_entry.configure(placeholder_text="Nome do clipe")
                rows = self._clip_inspector_visible_rows("speech")
            self._clip_label_var.set(clip.text_overlay or clip.label)
            self._set_clip_text_content(str(getattr(clip, "text_overlay", "") or clip.label or ""))
            self._clip_scale_var.set(float(getattr(clip, "scale_pct", 100.0)))
            self._clip_opacity_var.set(float(getattr(clip, "opacity_pct", 100.0)))
            self._clip_pos_x_var.set(float(getattr(clip, "position_x_pct", 0.0)))
            self._clip_pos_y_var.set(float(getattr(clip, "position_y_pct", 0.0)))
            self._clip_text_x_var.set(float(getattr(clip, "text_position_x_pct", 0.0)))
            self._clip_text_y_var.set(float(getattr(clip, "text_position_y_pct", 72.0)))
            self._clip_text_size_var.set(float(getattr(clip, "text_size_pct", 100.0)))
            self._clip_text_color_var.set(_normalize_hex_color(str(getattr(clip, "text_color", "#ffffff") or "#ffffff"), "#ffffff"))
            self._clip_text_bg_var.set(bool(getattr(clip, "text_background_enabled", True)))
            self._clip_text_bg_color_var.set(_normalize_hex_color(str(getattr(clip, "text_background_color", "#000000") or "#000000"), "#000000"))
            self._clip_volume_var.set(float(getattr(clip, "volume_pct", 100.0)))
            self._clip_pan_var.set(float(getattr(clip, "pan_pct", 0.0)))
            self._clip_fade_in_var.set(float(getattr(clip, "fade_in_s", 0.0)))
            self._clip_fade_out_var.set(float(getattr(clip, "fade_out_s", 0.0)))
            self._clip_rotation_var.set(float(getattr(clip, "rotation_deg", 0.0)))
            self._clip_blend_var.set(str(getattr(clip, "blend_mode", "Normal") or "Normal"))
            self._clip_crop_top_var.set(float(getattr(clip, "crop_top_pct", 0.0)))
            self._clip_crop_bottom_var.set(float(getattr(clip, "crop_bottom_pct", 0.0)))
            self._clip_crop_left_var.set(float(getattr(clip, "crop_left_pct", 0.0)))
            self._clip_crop_right_var.set(float(getattr(clip, "crop_right_pct", 0.0)))
            self._clip_brightness_var.set(float(getattr(clip, "brightness", 0.0)))
            self._clip_contrast_var.set(float(getattr(clip, "contrast", 0.0)))
            self._clip_saturation_var.set(float(getattr(clip, "saturation", 0.0)))
            self._clip_transition_var.set(str(getattr(clip, "transition", "Corte") or "Corte"))
            self._clip_speed_var.set(_speed_float_to_str(float(getattr(clip, "speed_factor", 1.0))))
            self._clip_chroma_var.set(bool(getattr(clip, "chroma_enabled", False)))
            self._clip_chroma_color_var.set(str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"))
            self._clip_chroma_tolerance_var.set(float(getattr(clip, "chroma_tolerance", 45.0)))
            self._clip_duration_var.set(max(self._trim_min_duration_s, clip.end_s - clip.start_s))
        self._refresh_clip_inspector_actions(clip)
        self._set_clip_inspector_visible_rows(rows)
        self._clip_inspector_enabled = True
        self._refresh_project_status()

    def _set_clip_text_content(self, text: str) -> None:
        if self._clip_text_content is None:
            return
        self._clip_text_content.delete("1.0", "end")
        self._clip_text_content.insert("1.0", str(text or ""))

    def _clip_text_content_value(self) -> str:
        if self._clip_text_content is None:
            return self._clip_label_var.get()
        return self._clip_text_content.get("1.0", "end-1c").strip()

    def _refresh_clip_inspector_actions(self, clip: Optional[TimelineClip]) -> None:
        if not hasattr(self, "_visual_primary_button") or not hasattr(self, "_visual_apply_button"):
            return
        if clip is not None and clip.clip_type == "text":
            return
        if clip is not None and _is_movable_media_clip(clip):
            self._visual_primary_button.configure(text="Aplicar visual", command=self._apply_clip_inspector)
            self._visual_apply_button.configure(text="Trocar fonte", command=self._assign_selected_media_to_clip)
            return
        self._visual_primary_button.configure(text="Texto no clipe", command=self._add_text_to_selected_clip)
        self._visual_apply_button.configure(text="Aplicar ajustes", command=self._apply_clip_inspector)

    def _apply_clip_inspector(self) -> None:
        if not self._clip_inspector_enabled:
            return
        clip = self._selected_timeline_clip()
        if clip is None:
            return
        if clip.clip_type == "text":
            text = self._clip_text_content_value() or self._clip_label_var.get().strip() or clip.label or "Texto"
            clip.label = self._clip_label_var.get().strip() or text[:40] or "Texto"
            clip.text_overlay = text
            self._clip_label_var.set(clip.label)
            clip.text_position_x_pct = float(self._clip_text_x_var.get())
            clip.text_position_y_pct = float(self._clip_text_y_var.get())
            clip.text_size_pct = float(self._clip_text_size_var.get())
            clip.text_color = _normalize_hex_color(self._clip_text_color_var.get(), "#ffffff")
            clip.text_background_enabled = bool(self._clip_text_bg_var.get())
            clip.text_background_color = _normalize_hex_color(self._clip_text_bg_color_var.get(), "#000000")
            self._clip_text_color_var.set(clip.text_color)
            self._clip_text_bg_color_var.set(clip.text_background_color)
            self._apply_selected_clip_duration(clip)
            _sync_text_clip_to_video_overlay(self._timeline_model, clip)
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._after_inspector_update(redraw_timeline=True)
            self._tb_status.configure(text="Texto atualizado.")
            return
        clip.label = self._clip_label_var.get().strip() or clip.label
        clip.scale_pct = float(self._clip_scale_var.get())
        clip.opacity_pct = float(self._clip_opacity_var.get())
        clip.position_x_pct = float(self._clip_pos_x_var.get())
        clip.position_y_pct = float(self._clip_pos_y_var.get())
        clip.text_position_x_pct = float(self._clip_text_x_var.get())
        clip.text_position_y_pct = float(self._clip_text_y_var.get())
        clip.text_size_pct = float(self._clip_text_size_var.get())
        clip.text_color = _normalize_hex_color(self._clip_text_color_var.get(), "#ffffff")
        clip.text_background_enabled = bool(self._clip_text_bg_var.get())
        clip.text_background_color = _normalize_hex_color(self._clip_text_bg_color_var.get(), "#000000")
        self._clip_text_color_var.set(clip.text_color)
        self._clip_text_bg_color_var.set(clip.text_background_color)
        if clip.text_overlay.strip():
            _upsert_text_overlay_clip(self._timeline_model, clip)
        clip.volume_pct = float(self._clip_volume_var.get())
        clip.pan_pct = float(self._clip_pan_var.get())
        clip.fade_in_s = max(0.0, float(self._clip_fade_in_var.get()))
        clip.fade_out_s = max(0.0, float(self._clip_fade_out_var.get()))
        clip.rotation_deg = float(self._clip_rotation_var.get())
        clip.blend_mode = str(self._clip_blend_var.get() or "Normal")
        clip.crop_top_pct    = max(0.0, min(50.0, float(self._clip_crop_top_var.get())))
        clip.crop_bottom_pct = max(0.0, min(50.0, float(self._clip_crop_bottom_var.get())))
        clip.crop_left_pct   = max(0.0, min(50.0, float(self._clip_crop_left_var.get())))
        clip.crop_right_pct  = max(0.0, min(50.0, float(self._clip_crop_right_var.get())))
        clip.brightness = max(-100.0, min(100.0, float(self._clip_brightness_var.get())))
        clip.contrast   = max(-100.0, min(100.0, float(self._clip_contrast_var.get())))
        clip.saturation = max(-100.0, min(100.0, float(self._clip_saturation_var.get())))
        clip.transition = self._clip_transition_var.get()
        clip.speed_factor = _speed_str_to_float(self._clip_speed_var.get())
        clip.chroma_enabled = bool(self._clip_chroma_var.get())
        clip.chroma_color = _normalize_hex_color(self._clip_chroma_color_var.get())
        self._clip_chroma_color_var.set(clip.chroma_color)
        clip.chroma_tolerance = float(self._clip_chroma_tolerance_var.get())
        if _is_movable_media_clip(clip):
            self._apply_selected_clip_duration(clip)
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._after_inspector_update(redraw_timeline=True)
        self._tb_status.configure(text="Ajustes do clipe atualizados.")

    def _apply_selected_clip_duration(self, clip: TimelineClip) -> None:
        if clip.clip_type != "text" and not _is_movable_media_clip(clip):
            return
        clip.start_s, clip.end_s = _set_clip_duration_bounds(
            clip.start_s,
            float(self._clip_duration_var.get()),
            self._duration_s,
            self._trim_min_duration_s,
        )
        self._clip_duration_var.set(max(self._trim_min_duration_s, clip.end_s - clip.start_s))

    def _after_inspector_update(self, redraw_timeline: bool = False) -> None:
        if redraw_timeline:
            self._redraw_timeline()
        if self.video_path:
            self._draw_frame_at(self._current_frame, fast=True)
        self._save_project_state()

    def _add_text_to_selected_clip(self) -> None:
        clip = self._selected_timeline_clip()
        if clip is None:
            self._tb_status.configure(text="Selecione um clipe para adicionar texto.")
            return
        if clip.clip_type == "text":
            self._apply_clip_inspector()
            return
        self._push_timeline_undo(label="inserir texto")
        text = self._clip_label_var.get().strip() or clip.label or "Texto"
        clip.text_overlay = text
        clip.label = text
        clip.text_position_x_pct = float(self._clip_text_x_var.get())
        clip.text_position_y_pct = float(self._clip_text_y_var.get())
        clip.text_size_pct = float(self._clip_text_size_var.get())
        clip.text_color = _normalize_hex_color(self._clip_text_color_var.get(), "#ffffff")
        clip.text_background_enabled = bool(self._clip_text_bg_var.get())
        clip.text_background_color = _normalize_hex_color(self._clip_text_bg_color_var.get(), "#000000")
        _upsert_text_overlay_clip(self._timeline_model, clip)
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._tb_status.configure(text="Texto criado na track de texto e associado ao clipe selecionado.")

    def _add_text_at_playhead(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um video antes de criar texto.")
            return
        start = min(self._duration_s, max(0.0, self._current_frame / max(1.0, self._fps)))
        if start >= self._duration_s - self._trim_min_duration_s:
            start = max(0.0, self._duration_s - self._insert_duration_s())
        end = min(self._duration_s, start + max(self._trim_min_duration_s, self._insert_duration_s()))
        if end <= start + 0.01:
            self._tb_status.configure(text="Sem espaco na timeline para criar texto.")
            return
        self._push_timeline_undo(label="inserir texto")
        text = self._clip_label_var.get().strip() or "Texto"
        text_clip = TimelineClip(
            start,
            end,
            "text",
            text,
            text_overlay=text,
            text_position_x_pct=float(self._clip_text_x_var.get()),
            text_position_y_pct=float(self._clip_text_y_var.get()),
            text_size_pct=float(self._clip_text_size_var.get()),
            text_color=_normalize_hex_color(self._clip_text_color_var.get(), "#ffffff"),
            text_background_enabled=bool(self._clip_text_bg_var.get()),
            text_background_color=_normalize_hex_color(self._clip_text_bg_color_var.get(), "#000000"),
        )
        text_clips = _timeline_text_clips(self._timeline_model)
        text_clips.append(text_clip)
        text_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
        self._selected_text_index = text_clips.index(text_clip)
        self._selected_clip_index = None
        self._selected_overlay_index = None
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._seek_to(self._time_to_frame(start))
        self._tb_status.configure(text="Texto criado na timeline. Edite o conteudo no campo Nome/texto do clipe.")

    def _insert_text_clip_from_panel(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um vídeo antes de inserir texto.")
            return
        text = self._text_panel_entry.get("1.0", "end-1c").strip()
        if not text:
            self._tb_status.configure(text="Digite um texto antes de inserir.")
            return
        start_s = self._current_frame / max(1.0, self._fps)
        end_s = min(start_s + 3.0, self._duration_s)
        from src.core.timeline_model import TimelineClip
        clip = TimelineClip(
            start_s=start_s, end_s=end_s,
            clip_type="text", label=text[:20],
            text_overlay=text,
            text_color=self._text_panel_color.get(),
            text_font=self._text_panel_font_var.get(),
            text_align=self._text_panel_align_var.get(),
            text_bold=self._text_panel_bold_var.get(),
            text_italic=self._text_panel_italic_var.get(),
            text_shadow_enabled=self._text_panel_shadow_var.get(),
            text_stroke_enabled=self._text_panel_stroke_var.get(),
            text_size_pct=float(self._text_panel_size_var.get()),
        )
        self._push_timeline_undo(label="inserir texto")
        self._timeline_model.text_track.clips.append(clip)
        self._timeline_model.text_track.clips.sort(key=lambda c: c.start_s)
        self._selected_text_index = self._timeline_model.text_track.clips.index(clip)
        self._selected_clip_index = None
        self._selected_overlay_index = None
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._refresh_clip_inspector()
        self._redraw_timeline()
        self._tb_status.configure(text=f"Texto inserido em {_fmt(start_s)}: \"{text[:30]}\"")

    def _assign_selected_media_to_clip(self) -> None:
        path = self._selected_project_media_path()
        clip = self._selected_timeline_clip()
        if not path:
            self._tb_status.configure(text="Selecione uma mídia do projeto.")
            return
        if self._selected_overlay_index is not None and clip is not None:
            self._push_timeline_undo(label="mover overlay")
            clip.source_path = path
            clip.clip_type = _clip_type_for_source_path(path)
            clip.label = Path(path).stem
            self._clip_label_var.set(clip.label)
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._save_project_media_paths()
            self._refresh_clip_inspector()
            self._tb_status.configure(text="Midia do overlay selecionado substituida.")
            return
        start_s = clip.start_s if clip is not None else min(self._duration_s, max(0.0, self._current_frame / max(1.0, self._fps)))
        if self._insert_media_path_at_time(path, start_s):
            inserted = self._selected_timeline_clip()
            if clip is not None and inserted is not None and inserted.source_path:
                inserted.end_s = min(self._duration_s, max(inserted.start_s + self._trim_min_duration_s, clip.end_s))
                self._sync_manual_timeline(mark_dirty=True)
                self._save_project_media_paths()
            self._tb_status.configure(text="Midia inserida como camada superior, sem alterar o video base.")

    def _insert_selected_media_clip(self) -> None:
        path = self._selected_project_media_path()
        if not path:
            self._tb_status.configure(text="Selecione uma mídia do projeto.")
            return
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um vídeo principal antes de inserir clipes.")
            return
        start_s = min(self._duration_s, max(0.0, self._current_frame / max(1.0, self._fps)))
        if self._insert_media_path_at_time(path, start_s):
            self._tb_status.configure(text=f"Midia inserida em camada ({self._insert_duration_s():.0f}s): {Path(path).name}.")
        return

    def _insert_duration_s(self) -> float:
        return _clamp_float(float(self._insert_duration_var.get()), 1.0, 15.0)

    def _insert_media_path_at_time(self, path: str, start_s: float, save: bool = True) -> bool:
        if not self._timeline_model:
            return False
        start_s, snapped = self._snap_media_insert_start(start_s)
        self._last_media_insert_snapped = snapped
        if start_s >= self._duration_s - self._trim_min_duration_s:
            start_s = max(0.0, self._duration_s - min(3.0, self._duration_s))
        return self._insert_overlay_media_path_at_time(path, start_s, save=save)

    def _snap_media_insert_start(self, start_s: float) -> tuple[float, bool]:
        if not self._timeline_model:
            return float(start_s), False
        return _snap_insert_start_for_duration(
            float(start_s),
            max(self._trim_min_duration_s, self._insert_duration_s()),
            self._duration_s,
            _clip_edges(self._timeline_model.video_track.clips),
            self._snap_threshold_s(),
        )

    def _insert_overlay_media_path_at_time(self, path: str, start_s: float, save: bool = True) -> bool:
        if not self._timeline_model:
            return False
        # Try to use the actual media duration; fall back to user-set insert duration
        probed_duration = 0.0
        if path and os.path.isfile(path) and not _is_image_path(path):
            try:
                probed_duration = get_video_duration(path)
            except Exception:
                pass
        duration_s = max(self._trim_min_duration_s, probed_duration or self._insert_duration_s())
        end_s = min(self._duration_s, float(start_s) + duration_s)
        if end_s <= start_s + 0.01:
            self._tb_status.configure(text="Sem espaco na timeline para inserir esse overlay.")
            return False
        self._push_timeline_undo(label="mover overlay")
        overlay = TimelineClip(start_s, end_s, _clip_type_for_source_path(path), Path(path).stem, source_path=path)
        overlay_clips = _timeline_overlay_clips(self._timeline_model)
        overlay_clips.append(overlay)
        overlay_clips.sort(key=lambda clip: (clip.start_s, clip.end_s))
        self._selected_overlay_index = overlay_clips.index(overlay)
        self._selected_clip_index = None
        self._selected_text_index = None
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._seek_to(self._time_to_frame(overlay.start_s))
        if save:
            self._save_project_media_paths()
        return True

    def _insert_media_path_replacing_video_at_time(self, path: str, start_s: float, save: bool = True) -> bool:
        if not self._timeline_model:
            return False
        new_clips, selected_index = _insert_media_clip_replacing_range(
            self._timeline_model.video_track.clips,
            path,
            start_s,
            self._duration_s,
            clip_duration_s=self._insert_duration_s(),
            min_duration_s=self._trim_min_duration_s,
        )
        if selected_index is None:
            self._tb_status.configure(text="Sem espaço na timeline para inserir esse clipe.")
            return False
        self._push_timeline_undo(label="inserir mídia")
        self._timeline_model.video_track.clips = new_clips
        self._selected_clip_index = selected_index
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        inserted = new_clips[selected_index]
        self._seek_to(self._time_to_frame(inserted.start_s))
        if save:
            self._save_project_media_paths()
        return True

    def _nudge_selected_timeline_item(self, delta_s: float) -> bool:
        if not self._timeline_model:
            return False
        if self._selected_overlay_index is not None:
            overlay_clips = _timeline_overlay_clips(self._timeline_model)
            if not 0 <= self._selected_overlay_index < len(overlay_clips):
                return False
            clip = overlay_clips[self._selected_overlay_index]
            new_start, new_end = _nudge_clip_bounds(clip.start_s, clip.end_s, delta_s, self._duration_s)
            if not _trim_bounds_changed(clip.start_s, clip.end_s, new_start, new_end):
                return False
            self._push_timeline_undo(label="mover overlay")
            clip.start_s = new_start
            clip.end_s = new_end
            self._selected_clip_index = None
            self._selected_text_index = None
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._seek_to(self._time_to_frame(new_start))
            self._tb_status.configure(text=f"Midia deslocada para {_fmt(new_start)}.")
            return True
        if self._selected_text_index is not None:
            text_clips = _timeline_text_clips(self._timeline_model)
            if not 0 <= self._selected_text_index < len(text_clips):
                return False
            clip = text_clips[self._selected_text_index]
            new_start, new_end = _nudge_clip_bounds(clip.start_s, clip.end_s, delta_s, self._duration_s)
            if not _trim_bounds_changed(clip.start_s, clip.end_s, new_start, new_end):
                return False
            self._push_timeline_undo(label="mover texto")
            _clear_video_text_overlay_for_text_clip(self._timeline_model, clip)
            clip.start_s = new_start
            clip.end_s = new_end
            self._selected_overlay_index = None
            _sync_text_clip_to_video_overlay(self._timeline_model, clip)
            self._timeline_dirty = True
            self._sync_manual_timeline(mark_dirty=True)
            self._seek_to(self._time_to_frame(new_start))
            self._tb_status.configure(text=f"Texto deslocado para {_fmt(new_start)}.")
            return True

        if self._selected_clip_index is None:
            return False
        clips = self._timeline_model.video_track.clips
        if not 0 <= self._selected_clip_index < len(clips):
            return False
        moving = _clone_timeline_clip(clips[self._selected_clip_index])
        if not _is_movable_media_clip(moving):
            self._tb_status.configure(text="Selecione texto ou midia externa para ajustar com Alt+setas.")
            return False
        new_start, new_end = _nudge_clip_bounds(moving.start_s, moving.end_s, delta_s, self._duration_s)
        if not _trim_bounds_changed(moving.start_s, moving.end_s, new_start, new_end):
            return False
        base_clips = [_clone_timeline_clip(clip) for idx, clip in enumerate(clips) if idx != self._selected_clip_index]
        new_clips, selected_index = _insert_media_clip_replacing_range(
            base_clips,
            moving.source_path,
            new_start,
            self._duration_s,
            clip_duration_s=max(self._trim_min_duration_s, moving.end_s - moving.start_s),
            min_duration_s=self._trim_min_duration_s,
        )
        if selected_index is None:
            return False
        inserted = _clone_timeline_clip(moving)
        inserted.start_s = new_clips[selected_index].start_s
        inserted.end_s = new_clips[selected_index].end_s
        new_clips[selected_index] = inserted
        self._push_timeline_undo(label="mover overlay")
        self._timeline_model.video_track.clips = new_clips
        self._selected_clip_index = selected_index
        self._selected_text_index = None
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._seek_to(self._time_to_frame(inserted.start_s))
        self._tb_status.configure(text=f"Midia deslocada para {_fmt(inserted.start_s)}.")
        return True

    def _refresh_project_status(self) -> None:
        selected = self._selected_timeline_clip()
        clip_name = selected.label if selected else "nenhum"
        source = "track de texto" if selected and selected.clip_type == "text" else (Path(selected.source_path).name if selected and selected.source_path else "mídia principal")
        clips = len(self._timeline_model.video_track.clips) if self._timeline_model else 0
        videos, images = _project_media_counts(self._project_media_paths)
        self._project_status_var.set(
            f"Mídias: {len(self._project_media_paths)} ({videos} vídeo(s), {images} imagem(ns))\n"
            f"Clipes: {clips}\n"
            f"Selecionado: {clip_name}\n"
            f"Origem do clipe: {source}\n"
            f"Inserir na timeline: {self._insert_duration_s():.0f}s\n"
            f"Zoom timeline: {self._waveform_zoom:.2f}x"
        )

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

    def _suggest_title_with_ai(self) -> None:
        suggestion = suggest_metadata(
            AiSuggestionRequest(
                video_path=self.video_path or self.project_name,
                project_name=self.project_name,
                platform=self._platform_var.get() if hasattr(self, "_platform_var") else Platform.YOUTUBE.value,
            )
        )
        self._title_entry.delete(0, "end")
        self._title_entry.insert(0, suggestion.title)
        self._subtitle_entry.delete(0, "end")
        self._subtitle_entry.insert(0, suggestion.subtitle)
        self._description_text.delete("1.0", "end")
        self._description_text.insert("1.0", suggestion.description)
        self._tb_status.configure(text=f"Sugestão aplicada via {suggestion.provider}.")
        self._save_project_state()

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
        if not _is_video_path(path):
            self._tb_status.configure(text="Arquivo ignorado: formato de vídeo não suportado.")
            messagebox.showwarning("Mídia incompatível", "Use um arquivo de vídeo compatível.")
            return
        self._stop_playback(reset_button=True)
        self._play_btn.configure(text="▶")
        try:
            self._preview_engine.open(path)
        except Exception as exc:
            self._record_ui_error(exc, "load_video")
            self.video_path = None
            self._export_btn.configure(state="disabled")
            self._tb_status.configure(text=f"Erro ao abrir vídeo: {exc}")
            messagebox.showerror("Erro ao abrir vídeo", str(exc))
            return

        self.video_path    = path
        self._register_project_media([path])
        self._save_project_video_path(path)
        self._segments     = []
        self._analysis_done= False
        self._timeline_model = None
        self._composition    = None
        self._timeline_dirty = False
        self._timeline_undo_stack.clear()
        self._timeline_redo_stack.clear()
        self._total_frames = self._preview_engine.total_frames
        self._fps          = self._preview_engine.fps
        self._duration_s   = self._preview_engine.duration_s
        self._current_frame= 0
        print(f"[PREVIEW] Video loaded: {path}")

        name = Path(path).name
        size_mb = os.path.getsize(path) / 1_000_000
        self._vid_info.configure(
            text=f"{name}\n{_fmt(self._duration_s)}  |  {size_mb:.1f} MB  |  {self._fps:.1f} fps\nMídias no projeto: {len(self._project_media_paths)}",
            fg=C_TEXT)

        # Auto-fill title
        stem = Path(path).stem.replace("_"," ").replace("-"," ").title()
        self._title_entry.delete(0, "end")
        self._title_entry.insert(0, stem)

        # Update UI
        self._export_btn.configure(state="normal")
        self._seek_bar.configure(to=max(1, self._total_frames - 1))
        self._seek_bar.set(0)
        self.root.title(f"CortaCerto - {self.project_name} - {name}")
        self._tb_status.configure(text="Carregando primeiro frame...")

        # Show first frame
        self._draw_frame_at(0, fast=True)
        self.root.after(250, lambda: self.video_path == path and self._draw_frame_at(0))
        self._redraw_timeline()
        self._update_time_label()

        # Background: analyze audio for timeline
        from ..config import SilenceStyle
        _MS = {SilenceStyle.AGGRESSIVE: 600,
               SilenceStyle.NATURAL:    900,
               SilenceStyle.LIGHT:      1400}
        style = SilenceStyle(self._silence_var.get())
        threading.Thread(
            target=self._bg_analyze,
            args=(
                path,
                self._duration_s,
                float(self._sliders["silence_db"].get()),
                _MS[style],
                int(self._sliders["padding"].get()),
                float(self._sliders["min_segment_ms"].get()) / 1000.0,
            ),
            daemon=True,
        ).start()

    def _save_project_video_path(self, video_path: str) -> None:
        if not self.project_path:
            return
        try:
            metadata = _read_project_metadata(self.project_path)
            media_paths = _merge_media_paths(metadata.get("media_paths"), [*self._project_media_paths, video_path])
            self._project_media_paths = media_paths
            metadata.update(
                {
                    "app": "CortaCerto",
                    "version": int(metadata.get("version") or 1),
                    "name": self.project_name,
                    "slug": _safe_project_slug(self.project_name),
                    "video_path": video_path,
                    "media_paths": media_paths,
                    "updated_at": int(time.time()),
                }
            )
            Path(self.project_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self._record_ui_error(exc, "save_project_video_path")
            print(f"[PROJECT] Não foi possível salvar o projeto: {exc}")

    def _save_project_state(self) -> None:
        if not self.project_path or not self.video_path:
            return
        try:
            metadata = _read_project_metadata(self.project_path)
            metadata.update(_project_state_payload(
                project_name=self.project_name,
                video_path=self.video_path,
                media_paths=self._project_media_paths,
                title=self._title_entry.get().strip(),
                subtitle=self._subtitle_entry.get().strip(),
                description=self._description_text.get("1.0", "end").strip(),
                current_time_s=self._current_frame / max(1.0, self._fps),
                timeline_segments=self._segments,
                timeline_dirty=self._timeline_dirty,
                clip_options=_clip_options_from_timeline_model(self._timeline_model),
                text_options=_text_options_from_timeline_model(self._timeline_model),
                track_options=_track_options_payload(
                    self._track_visual_visible_var.get(),
                    self._track_text_visible_var.get(),
                    self._track_audio_muted_var.get(),
                ),
            ))
            metadata["timeline_manifest"] = build_timeline_manifest(
                self._timeline_model,
                self.project_name,
                self.video_path,
            )
            if self._composition is not None:
                metadata["composition_v2"] = composition_to_dict(self._composition)
            # Mixer state (Etapa 5)
            metadata["mixer_state"] = {
                "volumes": [float(v.get()) for v in self._mix_vol_vars],
                "pans":    [float(v.get()) for v in self._mix_pan_vars],
                "mutes":   [bool(v.get())  for v in self._mix_mute_vars],
                "solos":   [bool(v.get())  for v in self._mix_solo_vars],
            }
            Path(self.project_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
            self._autosave_time_var.set(f"💾 {time.strftime('%H:%M:%S')}")
        except Exception as exc:
            self._record_ui_error(exc, "save_project_state")
            print(f"[PROJECT] Não foi possível atualizar retomada do projeto: {exc}")

    def _register_project_media(self, paths: list[str]) -> int:
        before = len(self._project_media_paths)
        self._project_media_paths = _merge_media_paths(self._project_media_paths, paths)
        self._refresh_media_list()
        return len(self._project_media_paths) - before

    def _save_project_media_paths(self) -> None:
        if not self.project_path:
            return
        try:
            metadata = _read_project_metadata(self.project_path)
            metadata["media_paths"] = _merge_media_paths(metadata.get("media_paths"), self._project_media_paths)
            metadata["updated_at"] = int(time.time())
            Path(self.project_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            self._record_ui_error(exc, "save_project_media_paths")
            print(f"[PROJECT] Não foi possível salvar mídias do projeto: {exc}")

    def _restore_project_timeline_if_available(self, timeline_model: TimelineModel) -> Optional[list[tuple[float, float]]]:
        metadata = self._pending_project_state
        if not metadata or metadata.get("video_path") != self.video_path:
            return None
        segments = _project_segments_from_metadata(metadata, self._duration_s)
        if not segments:
            return None
        _apply_segments_to_timeline_model(timeline_model, self._duration_s, segments)
        _apply_clip_options_to_timeline_model(timeline_model, metadata.get("clip_options"))
        _apply_text_options_to_timeline_model(timeline_model, metadata.get("text_options"))
        self._timeline_dirty = bool(metadata.get("timeline_dirty", True))
        # Sync new composition model from migrated timeline
        if timeline_model is not None:
            self._composition = composition_from_timeline_model(
                timeline_model,
                name=getattr(self, "project_name", "Projeto"),
            )
        # Override with saved composition_v2 if present (takes priority over migration)
        comp_v2_data = metadata.get("composition_v2") or {}
        if comp_v2_data and isinstance(comp_v2_data, dict) and comp_v2_data.get("schema_version", 0) >= 2:
            try:
                self._composition = composition_from_dict(comp_v2_data)
            except Exception:
                pass  # keep the migrated version
        return segments

    def _restore_project_playhead_if_available(self) -> None:
        metadata = self._pending_project_state
        if not metadata:
            return
        current_time_s = _project_float(metadata.get("current_time_s"), default=0.0)
        self._seek_to(self._time_to_frame(current_time_s))
        # Restore mixer state (Etapa 5)
        mx = metadata.get("mixer_state")
        if isinstance(mx, dict):
            for i, v in enumerate((mx.get("volumes") or [])):
                if i < len(self._mix_vol_vars):
                    try:
                        self._mix_vol_vars[i].set(float(v))
                    except Exception:
                        pass
            for i, v in enumerate((mx.get("pans") or [])):
                if i < len(self._mix_pan_vars):
                    try:
                        self._mix_pan_vars[i].set(float(v))
                    except Exception:
                        pass
            for i, v in enumerate((mx.get("mutes") or [])):
                if i < len(self._mix_mute_vars):
                    try:
                        self._mix_mute_vars[i].set(bool(v))
                    except Exception:
                        pass

    def _bg_analyze(
        self,
        video_path: str,
        duration_s: float,
        silence_threshold_db: float,
        min_silence_ms: int,
        audio_padding_ms: int,
        min_segment_s: float,
    ) -> None:
        """Analyze audio silences in background and update timeline."""
        if not video_path:
            return
        try:
            from ..core.analyzer import analyze_video
            analysis = analyze_video(
                video_path,
                silence_threshold_db=silence_threshold_db,
                min_silence_ms=min_silence_ms,
                audio_padding_ms=audio_padding_ms,
                min_segment_s=min_segment_s,
            )
            waveform = extract_waveform(video_path, duration_s, bins=420)
            timeline_model = build_timeline_model(
                duration_s,
                analysis.speech_segments,
                waveform=waveform,
            )
            self._queue.put(("__TIMELINE_READY__", (video_path, analysis, timeline_model)))
        except Exception as exc:
            self._record_ui_error(exc, "background_timeline_analysis")
            self._queue.put(("__TIMELINE_ERROR__", (video_path, str(exc))))

    def _on_seek(self, val: float) -> None:
        frame = int(float(val))
        self._seek_to(frame)

    def _seek_to(self, frame: int) -> None:
        was_playing = self._playing
        if was_playing:
            self._stop_playback(reset_button=False)
        self._current_frame = max(0, min(frame, self._total_frames - 1))
        self._seek_bar.set(self._current_frame)

        # ── scrub detection (Etapa 3) ─────────────────────────────────────────
        now = time.monotonic()
        dt = now - self._scrub_last_time
        self._scrub_last_time = now
        if dt < 0.12:                           # rapid seeks ≤ 120 ms apart
            self._scrub_count = min(8, self._scrub_count + 1)
        else:
            self._scrub_count = max(0, self._scrub_count - 2)
        self._is_scrubbing = self._scrub_count >= 3
        # ── Etapa C: audio scrub burst ────────────────────────────────────────
        if self._is_scrubbing and not was_playing:
            self._schedule_scrub_audio()

        self._draw_frame_at(self._current_frame)
        self._update_time_label()
        self._update_tl_playhead()
        self._update_timecode()
        self._save_project_state()
        if was_playing:
            self._start_playback()

    def _seek_start(self) -> None:
        self._stop_playback(reset_button=True)
        self._seek_to(0)

    def _seek_end(self) -> None:
        self._stop_playback(reset_button=True)
        self._seek_to(self._total_frames - 1)

    def _update_time_label(self) -> None:
        cur = self._current_frame / max(1, self._fps)
        self._time_lbl.configure(
            text=f"{_fmt(cur)} / {_fmt(self._duration_s)}")

    def _update_timecode(self) -> None:
        if not hasattr(self, '_tb_timecode') or self._tb_timecode is None:
            return
        try:
            s = self._current_frame / max(1.0, self._fps)
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = int(s % 60)
            f = int((s % 1.0) * self._fps)
            self._tb_timecode.configure(text=f"{h:02d}:{m:02d}:{sec:02d}:{f:02d}")
        except Exception:
            pass

    def _update_tl_playhead(self) -> None:
        c   = self._tl_canvas
        w   = c.winfo_width()
        h   = c.winfo_height()
        if w < 10 or h < 10:
            return
        track_x1, track_x2 = self._timeline_track_bounds(w)
        current_time = self._current_frame / max(1.0, self._fps)
        compact_ranges = self._compact_ranges_for_view()
        if compact_ranges:
            view_duration = compact_ranges[-1][3]
            display_time = _compact_source_to_display_time(current_time, compact_ranges)
            view_start, view_end = self._timeline_view_window(view_duration)
            px = _timeline_view_time_to_x(display_time, view_start, view_end, track_x1, track_x2)
        else:
            view_start, view_end = self._timeline_view_window(self._duration_s)
            px = _timeline_view_time_to_x(current_time, view_start, view_end, track_x1, track_x2)
        if self._tl_playhead and c.find_withtag(self._tl_playhead):
            c.coords(self._tl_playhead, px, 2, px, h - 2)
        else:
            self._tl_playhead = c.create_line(
                px, 2, px, h - 2, fill=TL_HEAD, width=2)

    # -- Color grade helpers ---------------------------------------------------

    def _build_color_grade(self) -> "ColorGrade":
        def _sl(key: str, default: float = 0.0) -> float:
            sl = self._c_sliders.get(key)
            return float(sl.get()) if sl is not None else default

        def _wheel_to_rgb(dx: float, dy: float, max_v: float = 50.0) -> tuple[float, float, float]:
            dist = min(1.0, (dx ** 2 + dy ** 2) ** 0.5)
            if dist < 0.001:
                return 0.0, 0.0, 0.0
            angle = math.atan2(-dy, dx)
            s = dist * max_v
            TWO_PI_3 = 2.094395  # 2π/3
            return (math.cos(angle) * s,
                    math.cos(angle - TWO_PI_3) * s,
                    math.cos(angle + TWO_PI_3) * s)

        l_dx, l_dy = self._wheel_positions.get("lift",  (0.0, 0.0))
        g_dx, g_dy = self._wheel_positions.get("gamma", (0.0, 0.0))
        h_dx, h_dy = self._wheel_positions.get("gain",  (0.0, 0.0))
        l_r, l_g, l_b = _wheel_to_rgb(l_dx, l_dy)
        g_r, g_g, g_b = _wheel_to_rgb(g_dx, g_dy)
        h_r, h_g, h_b = _wheel_to_rgb(h_dx, h_dy)

        return ColorGrade(
            enabled     = self._color_enabled.get(),
            temperature = _sl("temp",       -10.0),
            tint        = _sl("tint",         0.0),
            hue         = 0.0,
            saturation  = _sl("saturation",  10.0),
            vibrance    = _sl("vibrance",     0.0),
            contrast    = _sl("contrast",    10.0),
            brightness  = _sl("brightness",  10.0),
            shadows     = _sl("shadows",     -5.0),
            highlights  = _sl("highlights",   0.0),
            whites      = _sl("whites",       0.0),
            blacks      = _sl("blacks",       0.0),
            sharpen     = _sl("sharpen",      5.0),
            lift_r=l_r,  lift_g=l_g,  lift_b=l_b,
            gamma_r=g_r, gamma_g=g_g, gamma_b=g_b,
            gain_r=h_r,  gain_g=h_g,  gain_b=h_b,
            lut_path=getattr(self, "_lut_path", ""),
        )

    def _load_preset(self, name: str) -> None:
        from ..core.color_grade import PRESET_CAPCUT, PRESET_NEUTRAL, PRESET_CINEMATICO, PRESET_VINTAGE
        preset_map = {
            "CapCut ref": PRESET_CAPCUT,
            "Cinematico": PRESET_CINEMATICO,
            "Neutro":     PRESET_NEUTRAL,
            "Vintage":    PRESET_VINTAGE,
        }
        preset = preset_map.get(name)
        if preset is None:
            return

        slider_fields = {
            "temp":       preset.temperature,
            "tint":       preset.tint,
            "saturation": preset.saturation,
            "vibrance":   preset.vibrance,
            "contrast":   preset.contrast,
            "brightness": preset.brightness,
            "shadows":    preset.shadows,
            "highlights": preset.highlights,
            "whites":     preset.whites,
            "blacks":     preset.blacks,
            "sharpen":    preset.sharpen,
        }
        for key, val in slider_fields.items():
            if key in self._c_sliders:
                self._c_sliders[key].set(float(val))
            if key in self._c_labels:
                self._c_labels[key].configure(text=str(int(float(val))))

        # Apply colour wheel values from preset
        def _rgb_to_wheel(r: float, g: float, b: float) -> tuple[float, float, float]:
            """Approximate inverse: map RGB offsets back to (dx, dy) on unit disc."""
            if abs(r) < 0.01 and abs(g) < 0.01 and abs(b) < 0.01:
                return 0.0, 0.0, 0.0
            # Estimate angle from dominant hue bias: project onto R axis
            dist = (r ** 2 + g ** 2 + b ** 2) ** 0.5 / 50.0  # normalise
            dist = min(1.0, dist)
            angle = math.atan2(-b + g, r - 0.5 * (g + b))
            return math.cos(angle) * dist, -math.sin(angle) * dist

        lft = _rgb_to_wheel(preset.lift_r, preset.lift_g, preset.lift_b)
        gma = _rgb_to_wheel(preset.gamma_r, preset.gamma_g, preset.gamma_b)
        gai = _rgb_to_wheel(preset.gain_r, preset.gain_g, preset.gain_b)
        self._wheel_positions = {
            "lift":  lft[:2],
            "gamma": gma[:2],
            "gain":  gai[:2],
        }
        for wk, wc in self._wheel_canvases.items():
            self._update_wheel_indicator(wc, wk)

        self._schedule_preview()

    # -- Colour wheels (Etapa 4) -----------------------------------------------

    def _init_color_wheel(self, canvas: tk.Canvas, wheel_key: str) -> None:
        """Draw wheel image, indicator, and bind drag events."""
        # Pre-render the HSV wheel once and cache it
        if "wheel_bg" not in self._wheel_photos:
            pil = _make_color_wheel_image(80)
            self._wheel_photos["wheel_bg"] = ImageTk.PhotoImage(pil)

        canvas.create_image(0, 0, image=self._wheel_photos["wheel_bg"], anchor="nw",
                            tags="wheel_bg")
        # Draw centre cross-hairs
        canvas.create_line(40, 36, 40, 44, fill="white", width=1, tags="crosshair")
        canvas.create_line(36, 40, 44, 40, fill="white", width=1, tags="crosshair")

        self._update_wheel_indicator(canvas, wheel_key)

        canvas.bind("<ButtonPress-1>",   lambda e, k=wheel_key, c=canvas: self._on_wheel_event(k, c, e))
        canvas.bind("<B1-Motion>",       lambda e, k=wheel_key, c=canvas: self._on_wheel_event(k, c, e))
        canvas.bind("<Double-Button-1>", lambda e, k=wheel_key, c=canvas: self._reset_wheel(k, c))

    def _on_wheel_event(self, wheel_key: str, canvas: tk.Canvas, event: tk.Event) -> None:
        """Handle click / drag on a colour wheel canvas."""
        cx = cy = 40.0
        radius = 36.0
        dx = (event.x - cx) / radius
        dy = (event.y - cy) / radius
        dist = (dx ** 2 + dy ** 2) ** 0.5
        if dist > 1.0:
            dx /= dist
            dy /= dist
        self._wheel_positions[wheel_key] = (dx, dy)
        self._update_wheel_indicator(canvas, wheel_key)
        self._schedule_preview()

    def _reset_wheel(self, wheel_key: str, canvas: tk.Canvas) -> None:
        """Double-click: reset wheel to neutral (centre)."""
        self._wheel_positions[wheel_key] = (0.0, 0.0)
        self._update_wheel_indicator(canvas, wheel_key)
        self._schedule_preview()

    def _update_wheel_indicator(self, canvas: tk.Canvas, wheel_key: str) -> None:
        """Redraw the indicator dot at the current wheel position."""
        cx = cy = 40
        radius = 36
        dx, dy = self._wheel_positions.get(wheel_key, (0.0, 0.0))
        ix = int(cx + dx * radius)
        iy = int(cy + dy * radius)
        canvas.delete("indicator")
        canvas.create_oval(
            ix - 4, iy - 4, ix + 4, iy + 4,
            fill="white", outline="#000000", width=1.5,
            tags="indicator",
        )

    # -- LUT (Etapa 4) ---------------------------------------------------------

    def _pick_lut(self) -> None:
        path = filedialog.askopenfilename(
            title="Selecionar LUT .cube",
            filetypes=[("LUT files", "*.cube *.CUBE"), ("Todos", "*.*")],
        )
        if not path:
            return
        self._lut_path = path
        clear_lut_cache(path)          # force reload if same file was changed
        name = Path(path).stem[:20]
        if self._lut_name_lbl is not None:
            self._lut_name_lbl.configure(text=name)
        self._schedule_preview()

    def _clear_lut(self) -> None:
        self._lut_path = ""
        if self._lut_name_lbl is not None:
            self._lut_name_lbl.configure(text="Nenhum")
        self._schedule_preview()

    # -- Video Scopes (Etapa 4) ------------------------------------------------

    def _draw_scopes(self, pil_img: "Image.Image") -> None:
        """Render histogram / waveform / vectorscope into the scopes canvas."""
        if self._scopes_canvas is None:
            return
        try:
            cw = self._scopes_canvas.winfo_width()
            ch = self._scopes_canvas.winfo_height()
            if cw < 10:
                cw = 300
            if ch < 10:
                ch = 120

            mode = self._scopes_mode_var.get()
            if mode == "wave":
                scope_img = _render_waveform(pil_img, width=cw, height=ch)
            elif mode == "vector":
                size = min(cw, ch)
                scope_img = _render_vectorscope(pil_img, size=size)
                # Centre on canvas
                if scope_img.width < cw:
                    bg = Image.new("RGB", (cw, ch), color=_SCOPE_BG)
                    ox = (cw - scope_img.width) // 2
                    oy = (ch - scope_img.height) // 2
                    bg.paste(scope_img, (ox, oy))
                    scope_img = bg
            else:
                scope_img = _render_histogram(pil_img, width=cw, height=ch)

            photo = ImageTk.PhotoImage(scope_img)
            self._scopes_photo = photo   # keep reference
            self._scopes_canvas.delete("all")
            self._scopes_canvas.create_image(0, 0, image=photo, anchor="nw")
        except Exception:
            pass

    # -- Mixer de Áudio (Etapa 5) ---------------------------------------------

    # Channel indices
    _MIX_VIDEO   = 0
    _MIX_AUDIO   = 1
    _MIX_MUSIC   = 2
    _MIX_MASTER  = 3

    def _build_mixer_panel(self, parent: tk.Frame) -> None:
        """Build the 4-channel mixer inside *parent*."""
        # Header
        hdr = tk.Frame(parent, bg=ED_PANEL)
        hdr.pack(fill="x", padx=10, pady=(8, 6))
        tk.Label(hdr, text="MIXER", bg=ED_PANEL, fg=ED_TXT3,
                 font=("Segoe UI", 8, "bold")).pack(side="left")
        tk.Label(hdr, text="Clique-duplo VU = reset picos",
                 bg=ED_PANEL, fg=ED_TXT4, font=("Segoe UI", 7)).pack(side="right")

        # Channel strips row
        strips = tk.Frame(parent, bg=ED_PANEL)
        strips.pack(fill="x", padx=6, pady=(0, 10))
        strips.grid_columnconfigure(tuple(range(4)), weight=1, uniform="ch")

        for i, name in enumerate(self._mix_channel_names):
            self._build_mixer_channel(strips, i, name, col=i)

    def _build_mixer_channel(self, parent: tk.Frame, ch_idx: int, name: str, col: int) -> None:
        """Build one vertical channel strip at *col* in the *parent* grid."""
        is_master = (ch_idx == self._MIX_MASTER)
        bg = ED_SURF if not is_master else "#18141e"
        brd = ED_BORD if not is_master else ED_ACC_S

        cf = tk.Frame(parent, bg=bg, highlightbackground=brd, highlightthickness=1)
        cf.grid(row=0, column=col, sticky="nsew", padx=2, pady=0)

        # Track name label
        tk.Label(cf, text=name, bg=bg, fg=ED_ACC if is_master else ED_TXT2,
                 font=("Segoe UI", 8, "bold" if is_master else "normal"),
                 anchor="center").pack(fill="x", pady=(4, 2))

        # VU meter canvas
        vu = tk.Canvas(cf, width=16, height=72, bg="#050407",
                       highlightthickness=0, cursor="hand2")
        vu.pack(pady=(0, 2))
        self._vu_canvases[ch_idx] = vu
        self._draw_vu(vu, 0.0, 0.0)
        vu.bind("<Double-Button-1>",
                lambda _e, i=ch_idx: self._reset_vu_peak(i))

        # Vertical fader (tk.Scale — CTkSlider doesn't support vertical)
        fader = tk.Scale(
            cf, from_=200, to=0, resolution=1,
            orient=tk.VERTICAL, length=70,
            variable=self._mix_vol_vars[ch_idx],
            bg=bg, fg=ED_TXT2,
            activebackground=ED_ACC_S,
            highlightthickness=0, bd=0, relief="flat",
            troughcolor="#1a1520",
            showvalue=False,
            command=lambda v, i=ch_idx: self._on_mix_fader_change(i),
        )
        fader.pack(pady=(0, 2))

        # Volume readout
        vol_lbl = tk.Label(cf, textvariable=self._mix_vol_vars[ch_idx],
                           bg=bg, fg=ED_TXT3, font=("Courier New", 7),
                           anchor="center", width=4)
        vol_lbl.pack()
        # Bind to format as int
        self._mix_vol_vars[ch_idx].trace_add("write", lambda *_, i=ch_idx, lbl=vol_lbl: None)

        # Pan slider
        pan = tk.Scale(
            cf, from_=-100, to=100, resolution=1,
            orient=tk.HORIZONTAL, length=60,
            variable=self._mix_pan_vars[ch_idx],
            bg=bg, fg=ED_TXT3,
            activebackground=ED_ACC_S,
            highlightthickness=0, bd=0, relief="flat",
            troughcolor="#1a1520",
            showvalue=False,
            command=lambda v, i=ch_idx: self._on_mix_pan_change(i),
        )
        pan.pack(pady=(2, 2))
        pan.bind("<Double-Button-1>",
                 lambda _e, i=ch_idx, s=pan: (self._mix_pan_vars[i].set(0.0), self._on_mix_pan_change(i)))

        # Mute / Solo buttons row
        ms_row = tk.Frame(cf, bg=bg)
        ms_row.pack(pady=(0, 4))

        def _mute_btn(frame, i):
            var = self._mix_mute_vars[i]
            def _toggle():
                var.set(not var.get())
                _refresh()
            btn = tk.Button(frame, text="M", command=_toggle,
                            bg="#993322" if var.get() else ED_SURF2,
                            fg="white", relief="flat", padx=4, pady=1,
                            font=("Segoe UI", 7, "bold"), cursor="hand2", bd=0,
                            highlightthickness=0, width=2)
            btn.pack(side="left", padx=1)

            def _refresh():
                btn.configure(bg="#ff4422" if var.get() else ED_SURF2)

            return btn

        def _solo_btn(frame, i):
            var = self._mix_solo_vars[i]
            def _toggle():
                var.set(not var.get())
                self._update_solo_state()
                _refresh()
            btn = tk.Button(frame, text="S", command=_toggle,
                            bg="#996600" if var.get() else ED_SURF2,
                            fg="white", relief="flat", padx=4, pady=1,
                            font=("Segoe UI", 7, "bold"), cursor="hand2", bd=0,
                            highlightthickness=0, width=2)
            btn.pack(side="left", padx=1)

            def _refresh():
                btn.configure(bg="#ffbb00" if var.get() else ED_SURF2)

            return btn

        _mute_btn(ms_row, ch_idx)
        _solo_btn(ms_row, ch_idx)

    def _on_mix_fader_change(self, ch_idx: int) -> None:
        """Called when any mixer fader moves."""
        # Video channel (0) maps to ffmpeg audio; no live re-render needed
        # Music channel (2) maps to background music volume
        # Master (3) applies global gain
        pass  # preview is not re-rendered on fader move to keep it fast

    def _on_mix_pan_change(self, ch_idx: int) -> None:
        pass

    def _update_solo_state(self) -> None:
        """If any solo is active, mute all non-soloed channels."""
        any_solo = any(v.get() for v in self._mix_solo_vars)
        # Just visual feedback; actual muting handled at export time
        _ = any_solo

    def _reset_vu_peak(self, ch_idx: int) -> None:
        self._vu_peaks[ch_idx] = 0.0
        vu = self._vu_canvases[ch_idx]
        if vu:
            self._draw_vu(vu, self._vu_levels[ch_idx], 0.0)

    def _draw_vu(self, canvas: tk.Canvas, level: float, peak: float) -> None:
        """Draw segmented VU bar on *canvas*. level & peak in 0..1.3 range."""
        canvas.delete("all")
        try:
            W = int(canvas.winfo_width())  or 16
            H = int(canvas.winfo_height()) or 72
        except Exception:
            W, H = 16, 72

        N = 18          # total segments
        seg_h = max(1, (H - N) // N)
        gap   = 1
        total_seg = N * (seg_h + gap)
        y_offset  = H - total_seg   # top padding

        lit = int(min(level, 1.25) / 1.25 * N)
        pk  = int(min(peak,  1.25) / 1.25 * N)

        for seg in range(N):
            y2 = H - seg * (seg_h + gap) - gap
            y1 = y2 - seg_h
            fraction = seg / N
            if fraction >= 0.80:
                off, on = "#3a1010", "#ff3333"   # red: top 20%
            elif fraction >= 0.65:
                off, on = "#2d2500", "#ffcc00"   # yellow: 65-80%
            else:
                off, on = "#0a1a0a", "#22cc44"   # green: bottom 65%
            fill = on if seg < lit else off
            canvas.create_rectangle(2, y1, W - 2, y2, fill=fill, outline="")

        # Peak hold tick
        if 0 < pk < N:
            py2 = H - pk * (seg_h + gap) - gap
            py1 = py2 - seg_h
            canvas.create_rectangle(2, py1, W - 2, py2, fill="white", outline="")

    def _start_vu_animation(self) -> None:
        if self._vu_anim_id is None:
            self._vu_tick()

    def _stop_vu_animation(self) -> None:
        if self._vu_anim_id is not None:
            try:
                self.root.after_cancel(self._vu_anim_id)
            except Exception:
                pass
            self._vu_anim_id = None
        # Decay all meters to zero
        for i, canvas in enumerate(self._vu_canvases):
            if canvas is not None:
                self._vu_levels[i] = 0.0
                self._vu_peaks[i]  = 0.0
                self._draw_vu(canvas, 0.0, 0.0)

    def _vu_tick(self) -> None:
        """Animation tick: update VU meters at ~12.5 fps (Etapa C: real waveform data)."""
        now = time.monotonic()
        any_solo = any(v.get() for v in self._mix_solo_vars)

        # ── sample real waveform amplitude near current playhead ──────────────
        waveform_amp = 0.0
        if self._timeline_model and self._timeline_model.waveform and self._duration_s > 0:
            waveform = self._timeline_model.waveform
            n = len(waveform)
            time_s = self._current_frame / max(1.0, self._fps)
            # Average a ~80 ms window around current frame for smoother reading
            window_s = 0.08
            i0 = max(0, int((time_s - window_s * 0.5) / self._duration_s * n))
            i1 = min(n, int((time_s + window_s * 0.5) / self._duration_s * n) + 1)
            window = waveform[i0:i1]
            if window:
                waveform_amp = min(1.25, sum(window) / len(window) * 1.4)

        for i, canvas in enumerate(self._vu_canvases):
            if canvas is None:
                continue
            muted   = self._mix_mute_vars[i].get()
            solo_on = self._mix_solo_vars[i].get()
            vol     = float(self._mix_vol_vars[i].get()) / 100.0

            effectively_muted = muted or (any_solo and not solo_on)

            if not self._playing or effectively_muted or vol < 0.001:
                target = 0.0
            else:
                # Use real waveform for video/audio channels; flat signal for music
                if i in (self._MIX_VIDEO, self._MIX_AUDIO if hasattr(self, '_MIX_AUDIO') else -1):
                    base = waveform_amp * vol
                else:
                    # Music channel: use vol-scaled constant (no waveform available)
                    base = min(1.0, vol * 0.72)
                master_vol = float(self._mix_vol_vars[self._MIX_MASTER].get()) / 100.0
                target = min(1.25, base * (master_vol if i != self._MIX_MASTER else 1.0))

            cur = self._vu_levels[i]
            # Attack fast, decay slower
            if target > cur:
                self._vu_levels[i] = cur + (target - cur) * 0.65
            else:
                self._vu_levels[i] = max(0.0, cur - 0.05)

            if self._vu_levels[i] > self._vu_peaks[i]:
                self._vu_peaks[i]  = self._vu_levels[i]
                self._vu_peak_times[i] = now
            elif now - self._vu_peak_times[i] > 2.0:
                self._vu_peaks[i] = max(0.0, self._vu_peaks[i] - 0.015)

            self._draw_vu(canvas, self._vu_levels[i], self._vu_peaks[i])

        self._vu_anim_id = self.root.after(80, self._vu_tick)

    def _mixer_volume_for_export(self) -> dict[str, float]:
        """Return per-channel volume fractions for ffmpeg pipeline (0..2)."""
        any_solo = any(v.get() for v in self._mix_solo_vars)
        master_gain = float(self._mix_vol_vars[self._MIX_MASTER].get()) / 100.0
        result: dict[str, float] = {}
        for i, key in enumerate(["video", "audio", "music"]):
            muted   = self._mix_mute_vars[i].get()
            solo_on = self._mix_solo_vars[i].get()
            vol     = float(self._mix_vol_vars[i].get()) / 100.0
            effectively_muted = muted or (any_solo and not solo_on)
            result[key] = 0.0 if effectively_muted else round(vol * master_gain, 4)
        return result

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

    def _on_preview_press(self, event: tk.Event) -> str | None:
        self._preview_drag = None
        self._preview_drag_moved = False
        self._preview_click_consumed = False
        event_x, event_y = int(event.x), int(event.y)
        if self._chroma_picker_active:
            self._preview_click_consumed = True
            color = _sample_preview_hex_color(
                self._preview_display_image,
                self._preview_display_box,
                event_x,
                event_y,
            )
            if color:
                self._clip_chroma_color_var.set(color)
                self._clip_chroma_var.set(True)
                self._apply_clip_inspector()
                self._tb_status.configure(text=f"Chroma definido pelo conta-gotas: {color}.")
            else:
                self._tb_status.configure(text="Clique dentro do frame para capturar a cor do chroma.")
            self._chroma_picker_active = False
            return "break"
        current_time_s = self._current_frame / max(1.0, self._fps)
        text_hit = _preview_text_clip_hit(
            self._timeline_model,
            current_time_s,
            self._preview_display_box,
            event_x,
            event_y,
        )
        if text_hit is not None and text_hit != self._selected_text_index:
            self._selected_text_index = text_hit
            self._selected_clip_index = None
            self._selected_overlay_index = None
            self._redraw_timeline()
            self._refresh_clip_inspector()
            self._draw_frame_at(self._current_frame, fast=True)
            self._tb_status.configure(text="Texto selecionado no preview.")
            return "break"
        # Click-to-select overlay clip in preview (topmost clip at that pixel)
        overlay_hit = _preview_overlay_clip_hit(
            self._timeline_model,
            current_time_s,
            self._preview_display_box,
            event_x,
            event_y,
        )
        if overlay_hit is not None and overlay_hit != self._selected_overlay_index:
            self._selected_overlay_index = overlay_hit
            self._selected_clip_index = None
            self._selected_text_index = None
            self._redraw_timeline()
            self._refresh_clip_inspector()
            self._draw_frame_at(self._current_frame, fast=True)
            self._tb_status.configure(text="Overlay selecionado no preview. Arraste para mover.")
            return "break"
        clip = self._selected_timeline_clip()
        if clip is not None and _point_inside_display_box(self._preview_display_box, event_x, event_y):
            if clip.clip_type == "text":
                left, top, right, bottom = _preview_text_display_bounds(self._preview_display_box, clip)
                text_control = _preview_control_hit(
                    self._preview_display_box,
                    event_x,
                    event_y,
                    clip,
                    include_scale=True,
                )
                if text_control is None and not (left <= event_x <= right and top <= event_y <= bottom):
                    self._preview_click_consumed = True
                    self._tb_status.configure(text="Texto selecionado. Arraste dentro da caixa do texto para mover.")
                    return "break"
            else:
                left, top, right, bottom = _preview_visual_display_bounds(self._preview_display_box, clip)
                visual_control = _preview_control_hit(
                    self._preview_display_box,
                    event_x,
                    event_y,
                    clip,
                    include_scale=True,
                )
                if visual_control is None and not (left <= event_x <= right and top <= event_y <= bottom):
                    self._preview_click_consumed = True
                    self._tb_status.configure(text="Midia selecionada. Arraste dentro da caixa visual para mover.")
                    return "break"
            mode = _preview_control_hit(
                self._preview_display_box,
                event_x,
                event_y,
                clip,
                include_scale=True,
            ) or ("text" if clip.clip_type == "text" else "move")
            base_x = float(getattr(clip, "text_position_x_pct", 0.0)) if mode == "text" else float(getattr(clip, "position_x_pct", 0.0))
            base_y = float(getattr(clip, "text_position_y_pct", 72.0)) if mode == "text" else float(getattr(clip, "position_y_pct", 0.0))
            base_scale = float(getattr(clip, "text_size_pct", 100.0)) if mode == "text_scale" else float(getattr(clip, "scale_pct", 100.0))
            self._preview_drag = (
                mode,
                event_x,
                event_y,
                base_x,
                base_y,
                base_scale,
            )
            action = (
                "redimensionar texto"
                if mode == "text_scale"
                else "posicionar texto"
                if mode == "text"
                else "redimensionar"
                if mode == "scale"
                else "posicionar"
            )
            self._tb_status.configure(text=f"Arraste no preview para {action} o clipe selecionado.")
            return "break"
        return None

    def _on_preview_drag(self, event: tk.Event) -> str | None:
        if not self._preview_drag:
            return None
        clip = self._selected_timeline_clip()
        if clip is None:
            self._preview_drag = None
            return None
        mode, start_x, start_y, base_x, base_y, base_scale = self._preview_drag
        _box_x, _box_y, box_w, box_h = self._preview_display_box
        if box_w <= 0 or box_h <= 0:
            return "break"
        dx = int(event.x) - start_x
        dy = int(event.y) - start_y
        if mode == "scale":
            delta = (dx + dy) / max(1, min(box_w, box_h)) * 120.0
            clip.scale_pct = _clamp_float(base_scale + delta, 25.0, 300.0)
            self._clip_scale_var.set(clip.scale_pct)
        elif mode == "text_scale":
            delta = (dx + dy) / max(1, min(box_w, box_h)) * 140.0
            clip.text_size_pct = _clamp_float(base_scale + delta, 50.0, 220.0)
            self._clip_text_size_var.set(clip.text_size_pct)
        elif mode == "text":
            dx_pct = dx / max(1, box_w) * 100.0
            dy_pct = dy / max(1, box_h) * 100.0
            clip.text_position_x_pct = _clamp_float(base_x + dx_pct, -100.0, 100.0)
            clip.text_position_y_pct = _clamp_float(base_y + dy_pct, 0.0, 100.0)
            self._clip_text_x_var.set(clip.text_position_x_pct)
            self._clip_text_y_var.set(clip.text_position_y_pct)
        else:
            dx_pct = dx / max(1, box_w) * 100.0
            dy_pct = dy / max(1, box_h) * 100.0
            clip.position_x_pct = _clamp_float(base_x + dx_pct, -100.0, 100.0)
            clip.position_y_pct = _clamp_float(base_y + dy_pct, -100.0, 100.0)
            self._clip_pos_x_var.set(clip.position_x_pct)
            self._clip_pos_y_var.set(clip.position_y_pct)
        self._timeline_dirty = True
        self._preview_drag_moved = True
        # Show appropriate cursor during drag
        drag_cursor = "sizing" if mode in ("scale", "text_scale") else "fleur"
        self._preview_canvas.configure(cursor=drag_cursor)
        self._draw_frame_at(self._current_frame, fast=True)
        return "break"

    def _on_preview_release(self, event: tk.Event) -> str | None:
        self._preview_canvas.configure(cursor="")
        if self._preview_click_consumed:
            self._preview_click_consumed = False
            return "break"
        if self._preview_drag:
            self._preview_drag = None
            if self._preview_drag_moved:
                clip = self._selected_timeline_clip()
                if self._timeline_model and clip is not None and clip.clip_type == "text":
                    _sync_text_clip_to_video_overlay(self._timeline_model, clip)
                self._sync_manual_timeline(mark_dirty=True)
                self._tb_status.configure(text="Posição do clipe atualizada no preview.")
                return "break"
        if not self._chroma_picker_active:
            self._toggle_play()
        return "break"

    def _on_preview_motion(self, event: tk.Event) -> None:
        """Update cursor to give feedback about what action a click/drag would start."""
        if self._preview_drag:
            return  # cursor already set during active drag
        if not self._preview_display_box:
            return
        x, y = int(event.x), int(event.y)
        if not _point_inside_display_box(self._preview_display_box, x, y):
            self._preview_canvas.configure(cursor="")
            return
        # 1. Check selected clip handles first
        clip = self._selected_timeline_clip()
        if clip is not None:
            hit = _preview_control_hit(self._preview_display_box, x, y, clip, include_scale=True)
            if hit in ("scale", "text_scale"):
                self._preview_canvas.configure(cursor="sizing")
                return
            if hit == "text":
                self._preview_canvas.configure(cursor="fleur")
                return
            # Inside clip body → move cursor
            if clip.clip_type == "text":
                left, top, right, bottom = _preview_text_display_bounds(self._preview_display_box, clip)
            else:
                left, top, right, bottom = _preview_visual_display_bounds(self._preview_display_box, clip)
            if left <= x <= right and top <= y <= bottom:
                self._preview_canvas.configure(cursor="fleur")
                return
        # 2. Check for any overlay or text clip under cursor (clickable)
        current_time_s = self._current_frame / max(1.0, self._fps)
        overlay_idx = _preview_overlay_clip_hit(
            self._timeline_model, current_time_s, self._preview_display_box, x, y
        )
        if overlay_idx is not None:
            self._preview_canvas.configure(cursor="hand2")
            return
        text_idx = _preview_text_clip_hit(
            self._timeline_model, current_time_s, self._preview_display_box, x, y
        )
        if text_idx is not None:
            self._preview_canvas.configure(cursor="hand2")
            return
        # Default: play/pause click
        self._preview_canvas.configure(cursor="")

    def _arm_chroma_picker(self) -> None:
        self._chroma_picker_active = True
        self._tb_status.configure(text="Conta-gotas ativo: clique no preview para capturar a cor do chroma.")

    def _draw_frame_at(self, frame_idx: int, fast: bool = False) -> None:
        if not self.video_path:
            return
        self._current_frame = max(0, min(frame_idx, self._total_frames - 1))
        self._preview_request_id += 1

        # During rapid scrubbing use proxy (50% res) for faster feedback (Etapa 3)
        proxy_scale = 0.5 if self._is_scrubbing else 1.0

        if fast:
            settings = PreviewSettings(
                color_grade=ColorGrade(enabled=False),
                bokeh_intensity=0.0,
                proxy_scale=proxy_scale,
                request_token=("bootstrap", self._preview_request_id),
            )
            self._preview_bootstrap_key = settings.cache_key()
            if not self._playing:
                self._tb_status.configure(text="Carregando primeiro frame...")
            self._preview_engine.request_frame(self._current_frame, settings)
            return

        settings = PreviewSettings(
            color_grade=self._build_color_grade(),
            bokeh_intensity=float(self._sliders["bokeh"].get()) / 100.0,
            proxy_scale=proxy_scale,
            request_token=("preview", self._preview_request_id),
        )
        self._preview_settings_key = settings.cache_key()
        self._tb_status.configure(
            text="Scrubbing..." if self._is_scrubbing else "Atualizando preview...")
        self._preview_engine.request_frame(self._current_frame, settings)

        # Prefetch adjacent frames into raw cache (Etapa 3)
        if not self._playing and not fast:
            ahead = [self._current_frame + i for i in range(1, 5)]
            self._preview_engine.prefetch_frames(ahead)

    def _on_preview_frame_ready(self, preview: PreviewFrame) -> None:
        # Direct dispatch to main thread via after(0) — eliminates poll latency entirely.
        # root.after() is thread-safe; the lambda captures the preview object.
        try:
            self.root.after(0, lambda p=preview: self._safe_render_preview_frame(p))
        except Exception:
            # Fallback for shutdown / widget destroyed
            self._queue.put(("__PREVIEW__", preview))

    def _safe_render_preview_frame(self, preview: PreviewFrame) -> None:
        """Wrapper around _render_preview_frame with error isolation."""
        try:
            self._render_preview_frame(preview)
        except Exception as ex:
            with contextlib.suppress(Exception):
                self._record_ui_error(ex, "_render_preview_frame")

    def _draw_preview_clip_controls(
        self,
        canvas: tk.Canvas,
        display_box: tuple[int, int, int, int],
        active_clip: Optional[TimelineClip],
        active_text_clip: Optional[TimelineClip],
    ) -> None:
        x, y, w, h = display_box
        if w <= 0 or h <= 0:
            return
        current_time_s = self._current_frame / max(1.0, self._fps)

        # ── Ghost outlines for all unselected overlay clips ────────────────────
        for ov_clip in _overlay_clips_for_time(self._timeline_model, current_time_s):
            if ov_clip is self._selected_timeline_clip():
                continue
            gl, gt, gr, gb = _preview_visual_display_bounds(display_box, ov_clip)
            gl, gt = max(x, gl), max(y, gt)
            gr, gb = min(x + w, gr), min(y + h, gb)
            if gr > gl and gb > gt:
                canvas.create_rectangle(
                    gl, gt, gr, gb,
                    outline="#ffffff",
                    width=1,
                    dash=(4, 4),
                    tags=("frame", "preview-controls"),
                )

        # ── Selected clip bounding box + handles ──────────────────────────────
        selected = self._selected_timeline_clip()
        if selected is None:
            return
        selected_is_video = active_clip is selected
        selected_is_text = active_text_clip is selected
        if not selected_is_video and not selected_is_text:
            return

        if selected.clip_type == "text":
            left, top, right, bottom = _preview_text_display_bounds(display_box, selected)
        else:
            left, top, right, bottom = _preview_visual_display_bounds(display_box, selected)

        control_x1 = max(x, left)
        control_y1 = max(y, top)
        control_x2 = min(x + w, right)
        control_y2 = min(y + h, bottom)

        # Outer glow (thicker, darker)
        canvas.create_rectangle(
            control_x1 - 1, control_y1 - 1, control_x2 + 1, control_y2 + 1,
            outline="#000000", width=3,
            tags=("frame", "preview-controls"),
        )
        # Main selection border
        canvas.create_rectangle(
            control_x1, control_y1, control_x2, control_y2,
            outline="#ffcc44", width=2,
            tags=("frame", "preview-controls"),
        )

        # Corner bracket decorations (L-shapes at corners for crisp selection feel)
        blen = min(12, max(4, (control_x2 - control_x1) // 6))
        for (cx, cy, sx, sy) in [
            (control_x1, control_y1, +1, +1),
            (control_x2, control_y1, -1, +1),
            (control_x1, control_y2, +1, -1),
            (control_x2, control_y2, -1, -1),
        ]:
            canvas.create_line(cx, cy, cx + sx * blen, cy, fill="#ffcc44", width=3, tags=("frame", "preview-controls"))
            canvas.create_line(cx, cy, cx, cy + sy * blen, fill="#ffcc44", width=3, tags=("frame", "preview-controls"))

        # Handles
        for name, (hx, hy) in _preview_control_handles(display_box, selected, include_scale=True).items():
            fill  = "#7dc0ff" if name == "text" else ("#bca8ff" if name == "text_scale" else "#ffcc44")
            r = 7  # handle half-size (14×14 total)
            # Shadow
            canvas.create_rectangle(hx - r + 1, hy - r + 1, hx + r + 1, hy + r + 1,
                                     fill="#000000", outline="", tags=("frame", "preview-controls"))
            # Handle body
            canvas.create_rectangle(hx - r, hy - r, hx + r, hy + r,
                                     fill=fill, outline="#ffffff", width=1,
                                     tags=("frame", "preview-controls"))
            # Icon inside: arrows for move/scale, dot for text anchor
            if name == "scale":
                canvas.create_text(hx, hy, text="↗", fill="#000000",
                                   font=("Segoe UI", 8, "bold"), tags=("frame", "preview-controls"))
            elif name == "text_scale":
                canvas.create_text(hx, hy, text="↗", fill="#000000",
                                   font=("Segoe UI", 8, "bold"), tags=("frame", "preview-controls"))
            else:
                canvas.create_oval(hx - 3, hy - 3, hx + 3, hy + 3,
                                   fill="#000000", outline="", tags=("frame", "preview-controls"))

        # Hint label
        hint = "mover texto" if selected.clip_type == "text" else "mover | canto: escala"
        canvas.create_text(
            control_x1 + 6, control_y1 + 6,
            text=hint, anchor="nw",
            fill="#ffec99", font=("Segoe UI", 8),
            tags=("frame", "preview-controls"),
        )

    def _render_preview_frame(self, preview: PreviewFrame) -> None:
        is_playback = (
            self._playing
            and preview.frame_index == self._play_target_frame
            and preview.settings_key == self._preview_bootstrap_key
        )
        if preview.frame_index != self._current_frame and not is_playback:
            return
        is_bootstrap = preview.settings_key == self._preview_bootstrap_key
        if preview.settings_key != self._preview_settings_key and not is_bootstrap and not is_playback:
            return

        cw = self._preview_canvas.winfo_width()
        ch = self._preview_canvas.winfo_height()
        if cw < 10 or ch < 10:
            cw, ch = 800, 450

        render_frame = _preview_render_frame_index(self._current_frame, preview.frame_index, is_playback)
        clip_time_s = render_frame / max(1.0, self._fps)
        base_clip = _clip_for_time(self._timeline_model, clip_time_s) if self._track_visual_visible_var.get() else None
        overlay_clips = _overlay_clips_for_time(self._timeline_model, clip_time_s) if self._track_visual_visible_var.get() else []
        overlay_clip = overlay_clips[-1] if overlay_clips else None
        active_clip = overlay_clip or base_clip
        active_text_clip = _text_clip_for_time(self._timeline_model, clip_time_s) if self._track_text_visible_var.get() else None
        preview_source = _preview_base_image_for_timeline(preview.image, self._timeline_model, base_clip)
        preview_image = _apply_clip_preview_options(
            preview_source,
            base_clip,
            include_text=active_text_clip is None,
        )
        for overlay_clip in overlay_clips:
            overlay_source = self._clip_source_preview_image(overlay_clip, clip_time_s, preview_image.size)
            if overlay_source is not None:
                preview_image = _compose_visual_overlay_preview(preview_image, overlay_source, overlay_clip)
        preview_image = _apply_text_clip_preview_options(preview_image, active_text_clip)
        pil = _fit_preview_image(preview_image, cw, ch)
        nw, nh = pil.size

        photo = ImageTk.PhotoImage(pil)
        self._preview_photo = photo
        self._preview_backend = preview.backend
        self._preview_render_ms = preview.render_ms
        self._preview_bootstrap_key = None
        if is_playback:
            previous_frame = self._current_frame
            crossed_cut = self._play_audio_started and self._playback_crossed_removed_range(
                previous_frame,
                preview.frame_index,
            )
            self._current_frame = preview.frame_index
            self._seek_bar.set(self._current_frame)
            self._update_time_label()
            self._update_tl_playhead()
            if crossed_cut:
                self._start_preview_audio(self._current_frame)
                self._play_started_at = time.monotonic()
                self._play_start_frame = self._current_frame
                print(f"[PREVIEW] Audio resynced after timeline cut at {_fmt(self._current_frame / max(1.0, self._fps))}.")
            elif not self._play_audio_started:
                self._start_preview_audio(self._current_frame)
                self._play_audio_started = True
                self._play_started_at = time.monotonic()
                self._play_start_frame = self._current_frame

        c = self._preview_canvas
        c.delete("frame")
        x = (cw - nw) // 2
        y = (ch - nh) // 2
        self._preview_display_image = pil.copy()
        self._preview_display_box = (x, y, nw, nh)
        c.create_image(x, y, image=photo, anchor="nw", tags="frame")
        self._draw_preview_clip_controls(c, self._preview_display_box, active_clip, active_text_clip)
        c.itemconfigure(self._no_video_id, state="hidden")

        # Update video scopes (skip during active playback for performance)
        if (
            self._scopes_canvas is not None
            and not self._playing
            and not getattr(preview, "is_proxy", False)
        ):
            try:
                self._draw_scopes(self._preview_display_image)
            except Exception:
                pass
        if is_playback:
            elapsed_s = max(0.001, time.monotonic() - self._play_started_at)
            effective_fps = _playback_effective_fps(
                self._play_start_frame,
                preview.frame_index,
                elapsed_s,
            )
            self._tb_status.configure(
                text=(
                    f"Reproduzindo | {_fmt(self._current_frame / max(1, self._fps))} / {_fmt(self._duration_s)} "
                    f"| {effective_fps:.1f} fps | frame {preview.frame_index + 1}/{self._total_frames} "
                    f"| {preview.render_ms:.0f} ms"
                )
            )
            self._schedule_next_playback_frame(preview.render_ms)
        else:
            proxy_tag = "  [proxy]" if getattr(preview, "is_proxy", False) else ""
            self._tb_status.configure(
                text=f"Preview {preview.backend}{proxy_tag}  |  {preview.render_ms:.0f} ms"
            )
        # Etapa D: update fullscreen preview window if open
        if self._fullscreen_preview_win and self._fullscreen_preview_win.winfo_exists():
            self._redraw_fullscreen_preview()

        print(
            f"[PREVIEW] Frame rendered successfully | "
            f"frame={preview.frame_index} backend={preview.backend} "
            f"render_ms={preview.render_ms:.0f}"
        )

    def _clip_source_preview_image(
        self,
        clip: Optional[TimelineClip],
        time_s: float,
        target_size: tuple[int, int] | None = None,
    ) -> Optional[Image.Image]:
        path = str(getattr(clip, "source_path", "") or "") if clip else ""
        if not path or not Path(path).exists() or path == self.video_path:
            return None
        if _is_image_path(path):
            return _image_source_preview_image(path, target_size)
        cap = self._clip_source_caps.get(path)
        if cap is None:
            cap = cv2.VideoCapture(path)
            if not cap.isOpened():
                with contextlib.suppress(Exception):
                    cap.release()
                return None
            self._clip_source_caps[path] = cap
            fps = max(1.0, cap.get(cv2.CAP_PROP_FPS))
            total = max(1, int(cap.get(cv2.CAP_PROP_FRAME_COUNT)))
            self._clip_source_meta[path] = (fps, total)
        fps, total = self._clip_source_meta.get(path, (30.0, 1))
        frame_index = _clip_source_frame_index(clip, time_s, fps, total)
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, frame_bgr = cap.read()
        if not ok or frame_bgr is None:
            return None
        return Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB))

    def _toggle_play(self) -> None:
        if not self.video_path:
            return
        if self._playing:
            self._stop_playback(reset_button=True)
        else:
            self._start_playback()

    def _start_playback(self) -> None:
        if not self.video_path:
            return
        self._play_generation += 1
        self._preview_settings_key = ()
        self._playing = True
        self._play_started_at = time.monotonic()
        self._play_start_frame = self._current_frame
        self._play_btn.configure(text="⏸")
        self._play_audio_started = False
        self._start_vu_animation()          # Etapa 5 — start VU meters
        self._request_playback_frame()

    def _stop_playback(self, reset_button: bool = True) -> None:
        self._playing = False
        self._play_generation += 1
        self._play_target_frame = None
        self._preview_bootstrap_key = None
        self._play_audio_started = False
        self._stop_preview_audio()
        self._stop_vu_animation()           # Etapa 5 — stop VU meters
        if self._play_after_id:
            try:
                self.root.after_cancel(self._play_after_id)
            except Exception:
                pass
            self._play_after_id = None
        if reset_button:
            self._play_btn.configure(text="▶")

    def _request_playback_frame(self) -> None:
        self._play_after_id = None
        if not self._playing or not self.video_path:
            return
        elapsed_s = time.monotonic() - self._play_started_at
        target = _playback_target_frame(
            self._play_start_frame,
            elapsed_s,
            self._fps,
            self._total_frames,
        )
        if target <= self._current_frame:
            target = self._current_frame + 1
        target = self._coerce_playback_frame_to_timeline(target)
        if target >= self._total_frames:
            if getattr(self, '_loop_playback_var', None) and self._loop_playback_var.get():
                self._seek_to(0)
                # continue playing from start
            else:
                self._stop_playback(reset_button=True)
            return
        # Stable request_token for playback — allows rendered-cache hits on re-play.
        # Color grade + bokeh are always disabled during playback for speed.
        settings = PreviewSettings(
            color_grade=ColorGrade(enabled=False),
            bokeh_intensity=0.0,
            request_token=("playback_raw",),
        )
        self._play_target_frame = target
        self._preview_bootstrap_key = settings.cache_key()
        self._preview_engine.request_frame(target, settings)
        # Pre-warm raw cache for next few frames while current frame decodes
        ahead = [target + d for d in range(1, 5) if target + d < self._total_frames]
        if ahead:
            self._preview_engine.prefetch_frames(ahead)

    def _coerce_playback_frame_to_timeline(self, frame: int) -> int:
        if not self._timeline_dirty or not self._timeline_model:
            return frame
        return _coerce_frame_to_segments(
            frame,
            self._fps,
            self._total_frames,
            [(clip.start_s, clip.end_s) for clip in self._timeline_model.video_track.clips],
            self._duration_s,
        )

    def _playback_crossed_removed_range(self, previous_frame: int, current_frame: int) -> bool:
        if not self._timeline_dirty or not self._timeline_model:
            return False
        return _playback_crosses_removed_range(
            previous_frame,
            current_frame,
            self._fps,
            [(clip.start_s, clip.end_s) for clip in self._timeline_model.video_track.clips],
            self._duration_s,
        )

    def _nearest_kept_time(self, time_s: float) -> float:
        if not self._timeline_model:
            return max(0.0, min(self._duration_s, time_s))
        return _coerce_time_to_segments(
            time_s,
            [(clip.start_s, clip.end_s) for clip in self._timeline_model.video_track.clips],
            self._duration_s,
        )

    def _schedule_next_playback_frame(self, render_ms: float) -> None:
        if not self._playing:
            return
        delay_ms = _playback_delay_ms(self._fps, render_ms)
        self._play_after_id = self.root.after(delay_ms, self._request_playback_frame)

    def _start_preview_audio(self, frame: Optional[int] = None) -> None:
        self._stop_preview_audio()
        if not self.video_path or self._track_audio_muted_var.get():
            return
        try:
            start_frame = self._current_frame if frame is None else frame
            start_s = start_frame / max(1.0, self._fps)
            startupinfo = None
            if os.name == "nt":
                startupinfo = subprocess.STARTUPINFO()
                startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
            self._audio_proc = subprocess.Popen(
                [
                    ffplay(),
                    "-nodisp",
                    "-vn",
                    "-autoexit",
                    "-loglevel", "error",
                    "-ss", f"{start_s:.3f}",
                    self.video_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                startupinfo=startupinfo,
            )
        except Exception as exc:
            self._audio_proc = None
            print(f"[PREVIEW] Áudio indisponível no preview: {exc}")

    def _stop_preview_audio(self) -> None:
        proc = self._audio_proc
        self._audio_proc = None
        if proc and proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=1)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

    def _schedule_scrub_audio(self) -> None:
        """Debounce-play a 0.15 s audio burst at the current scrub position (Etapa C)."""
        if self._scrub_audio_after:
            try:
                self.root.after_cancel(self._scrub_audio_after)
            except Exception:
                pass
            self._scrub_audio_after = None

        def _burst() -> None:
            self._scrub_audio_after = None
            if self._playing or not self.video_path or self._track_audio_muted_var.get():
                return
            # Stop any previous burst before starting a new one
            self._stop_preview_audio()
            time_s = self._current_frame / max(1.0, self._fps)
            try:
                startupinfo = None
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                self._audio_proc = subprocess.Popen(
                    [
                        ffplay(),
                        "-nodisp", "-vn", "-autoexit",
                        "-loglevel", "error",
                        "-t", "0.15",
                        "-ss", f"{time_s:.3f}",
                        self.video_path,
                    ],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    startupinfo=startupinfo,
                )
            except Exception:
                pass

        self._scrub_audio_after = self.root.after(110, _burst)

    # -- Fullscreen Preview (Etapa D) ------------------------------------------

    def _toggle_fullscreen_preview(self) -> None:
        """Open or close the floating fullscreen preview window."""
        if self._fullscreen_preview_win and self._fullscreen_preview_win.winfo_exists():
            self._fullscreen_preview_win.destroy()
            self._fullscreen_preview_win = None
            self._fullscreen_preview_photo = None
            return
        win = tk.Toplevel(self.root)
        win.title("Preview — CortaCerto")
        win.configure(bg="#0a0a0e")
        win.geometry("1280x720")
        win.resizable(True, True)
        self._fullscreen_preview_win = win
        canvas = tk.Canvas(win, bg="#0a0a0e", highlightthickness=0)
        canvas.pack(fill="both", expand=True)
        canvas.bind("<Configure>", lambda e: self._redraw_fullscreen_preview())
        # Store reference for drawing
        win._fs_canvas = canvas  # type: ignore[attr-defined]
        win.protocol("WM_DELETE_WINDOW", self._toggle_fullscreen_preview)
        # Draw current frame immediately
        self._redraw_fullscreen_preview()

    def _redraw_fullscreen_preview(self) -> None:
        """Push the current preview image to the fullscreen window."""
        win = self._fullscreen_preview_win
        if not win or not win.winfo_exists():
            return
        canvas = getattr(win, "_fs_canvas", None)
        if canvas is None or not canvas.winfo_exists():
            return
        img = self._preview_display_image
        if img is None:
            return
        cw = canvas.winfo_width() or 1280
        ch = canvas.winfo_height() or 720
        pil = _fit_preview_image(img, cw, ch)
        nw, nh = pil.size
        photo = ImageTk.PhotoImage(pil)
        self._fullscreen_preview_photo = photo   # keep ref
        canvas.delete("all")
        x = (cw - nw) // 2
        y = (ch - nh) // 2
        canvas.create_image(x, y, image=photo, anchor="nw")

    def _pick_music(self) -> None:
        path = filedialog.askopenfilename(
            title="Música de fundo",
            filetypes=[("Áudio", "*.mp3 *.wav *.aac *.m4a *.ogg"),
                       ("Todos", "*.*")])
        if path:
            self._music_path = path
            self._music_label.configure(text=Path(path).name, fg=C_TEXT)
            self._update_left_music_label()

    def _clear_music(self) -> None:
        self._music_path = None
        self._music_label.configure(text="Nenhuma", fg=C_MUTED)
        self._update_left_music_label()

    # -- Labels ----------------------------------------------------------------

    def _detect_gpu_label(self) -> None:
        def _task():
            lbl = encoder_label()
            self._queue.put(("__GPU_LABEL__", lbl))
        threading.Thread(target=_task, daemon=True).start()

    def _detect_seg_label(self) -> None:
        def _task():
            try:
                from ..core.segmentation import get_backend
                backend = get_backend()
                colors = {"rembg": C_GREEN, "mediapipe": C_ACCENT2,
                          "grabcut": C_MUTED}
                color = colors.get(backend, C_MUTED)
                self._queue.put(("__SEG_LABEL__", (backend, color)))
            except Exception:
                pass
        threading.Thread(target=_task, daemon=True).start()

    # -- Pipeline --------------------------------------------------------------

    def _build_config(self) -> ProcessingConfig:
        plat_map  = {p.value: p for p in Platform}
        style_map = {s.value: s for s in SilenceStyle}
        should_cut_timeline = self._rm_silence_var.get() or self._timeline_dirty
        manual_segments = (
            list(self._segments)
            if should_cut_timeline and self._analysis_done and self._timeline_model is not None
            else None
        )
        return ProcessingConfig(
            silence_threshold_db = float(self._sliders["silence_db"].get()),
            silence_style        = style_map.get(self._silence_var.get(),
                                                  SilenceStyle.NATURAL),
            audio_padding_ms     = int(self._sliders["padding"].get()),
            min_segment_s        = float(self._sliders["min_segment_ms"].get()) / 1000.0,
            platform             = plat_map.get(self._platform_var.get(),
                                                  Platform.YOUTUBE),
            remove_silence       = should_cut_timeline,
            generate_thumbnail   = self._gen_thumb_var.get(),
            generate_vertical    = self._gen_vert_var.get(),
            manual_segments      = manual_segments,
            clip_options         = _clip_options_from_timeline_model(self._timeline_model),
            track_options        = _track_options_payload(
                self._track_visual_visible_var.get(),
                self._track_text_visible_var.get(),
                self._track_audio_muted_var.get(),
            ),
            apply_zoom_effects   = True,
            apply_transitions    = True,
            color_grade          = self._build_color_grade(),
            noise_reduction      = self._noise_var.get(),
            audio_normalization  = self._audio_normalize_var.get(),
            audio_voice_filter   = self._audio_voice_filter_var.get(),
            audio_compressor     = self._audio_compressor_var.get(),
            bokeh_intensity      = float(self._sliders["bokeh"].get()) / 100.0,
            thumbnail_title      = self._title_entry.get().strip(),
            thumbnail_subtitle   = self._subtitle_entry.get().strip(),
            thumbnail_theme      = "dark",
            thumbnail_count      = 5,
            video_crf            = int(self._crf_var.get()),
            music_path           = self._music_path,
            # Mixer (Etapa 5)
            music_volume_pct     = float(self._mix_vol_vars[self._MIX_MUSIC].get()),
            video_volume_pct     = float(self._mix_vol_vars[self._MIX_VIDEO].get()),
            audio_muted_mixer    = bool(self._mix_mute_vars[self._MIX_AUDIO].get()),
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
        self._stop_playback(reset_button=True)
        self._play_btn.configure(text="▶")

        self._cancel_ev.clear()
        self._export_btn.configure(state="disabled")
        self._cancel_btn.configure(state="normal", text="Cancelar")
        self._tb_progress.set(0)
        self._tb_status.configure(text="Iniciando pipeline...")
        self._open_export_modal()

        config     = self._build_config()
        output_dir = str(Path(self.video_path).parent / "CortaCerto_output")

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
                elif msg == "__PREVIEW__":
                    # Fallback path — normally handled via after(0) direct dispatch.
                    try:
                        self._render_preview_frame(val)
                    except Exception as _pex:
                        print(f"[PREVIEW] render error: {_pex}")
                elif msg == "__TIMELINE_READY__":
                    video_path, analysis, timeline_model = val
                    if video_path == self.video_path:
                        restored = self._restore_project_timeline_if_available(timeline_model)
                        self._segments = restored if restored is not None else analysis.speech_segments
                        self._analysis_done = True
                        self._timeline_model = timeline_model
                        # Sync composition if not already set by _restore_project_timeline_if_available
                        if self._composition is None and self._timeline_model is not None:
                            self._composition = composition_from_timeline_model(
                                self._timeline_model,
                                name=getattr(self, "project_name", "Projeto"),
                            )
                        self._timeline_undo_stack.clear()
                        self._timeline_redo_stack.clear()
                        self._redraw_timeline()
                        self._restore_project_playhead_if_available()
                        self._tb_status.configure(
                            text="Projeto retomado. Timeline restaurada."
                            if restored is not None
                            else "Preview pronto. Timeline atualizada."
                        )
                        self._pending_project_state = {}
                elif msg == "__TIMELINE_ERROR__":
                    video_path, detail = val
                    if video_path == self.video_path:
                        self._tb_status.configure(text="Preview pronto. Timeline indisponível.")
                        self._record_ui_error_message(str(detail), "timeline_queue_error")
                        print(f"[TIMELINE] Falha ao analisar timeline: {detail}")
                elif msg == "__GPU_LABEL__":
                    self._gpu_lbl.configure(text=f"Encode: {val}")
                elif msg == "__SEG_LABEL__":
                    backend, color = val
                    self._seg_lbl.configure(text=f"Seg: {backend}", fg=color)
                else:
                    self._tb_status.configure(text=msg[:80])
                    if isinstance(val, float) and 0.0 <= val <= 1.0:
                        self._tb_progress.set(val)
                        self._update_export_modal(msg, val)
        except queue.Empty:
            pass
        except Exception as _qex:
            print(f"[POLL] queue error: {_qex}")
        finally:
            # Preview frames are dispatched directly (after(0)), so this only
            # needs to be fast enough for pipeline progress messages (export).
            self.root.after(40, self._poll_queue)

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
            self._record_ui_error_message(result.error or "Erro desconhecido", "pipeline_result_error")
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
        popup.title("Resultado - CortaCerto")
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
        self._stop_playback(reset_button=False)
        self._release_clip_source_caps()
        self._preview_engine.stop()
        self.root.destroy()

    def _release_clip_source_caps(self) -> None:
        for cap in self._clip_source_caps.values():
            with contextlib.suppress(Exception):
                cap.release()
        self._clip_source_caps.clear()
        self._clip_source_meta.clear()


# -- Helpers -------------------------------------------------------------------

def _fmt(s: float) -> str:
    m, sec = divmod(int(s), 60)
    return f"{m:02d}:{sec:02d}"


def _project_name_from_path(project_path: Optional[str]) -> str:
    if not project_path:
        return "Projeto rápido"
    name = Path(project_path).name
    if name.endswith(PROJECT_LEGACY_EXT):
        return name[:-len(PROJECT_LEGACY_EXT)]
    if name.endswith(PROJECT_EXT):
        return name[:-len(PROJECT_EXT)]
    return Path(project_path).stem


def _safe_project_slug(name: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.strip()).strip("-._")
    return slug or "projeto"


def _build_project_metadata(project_path: str) -> dict[str, object]:
    name = _project_name_from_path(project_path)
    return {
        "app": "CortaCerto",
        "version": 1,
        "name": name,
        "slug": _safe_project_slug(name),
        "video_path": None,
        "media_paths": [],
        "created_at": int(time.time()),
    }


def _project_metadata_with_launcher_media(metadata: dict[str, object], media_path: str) -> dict[str, object]:
    updated = dict(metadata)
    updated["media_paths"] = _merge_media_paths(updated.get("media_paths"), [media_path])
    if _is_video_path(media_path):
        updated["video_path"] = media_path
    return updated


def _project_state_payload(
    project_name: str,
    video_path: str,
    current_time_s: float,
    timeline_segments: list[tuple[float, float]],
    timeline_dirty: bool,
    media_paths: Optional[list[str]] = None,
    title: str = "",
    subtitle: str = "",
    description: str = "",
    clip_options: Optional[list[dict[str, object]]] = None,
    text_options: Optional[list[dict[str, object]]] = None,
    track_options: Optional[dict[str, object]] = None,
) -> dict[str, object]:
    return {
        "app": "CortaCerto",
        "version": 1,
        "name": project_name,
        "slug": _safe_project_slug(project_name),
        "video_path": video_path,
        "media_paths": _merge_media_paths(media_paths, [video_path]),
        "publish": {
            "title": str(title),
            "subtitle": str(subtitle),
            "description": str(description),
        },
        "current_time_s": max(0.0, float(current_time_s)),
        "timeline_segments": [
            {"start_s": float(start), "end_s": float(end)}
            for start, end in timeline_segments
            if float(end) > float(start)
        ],
        "clip_options": clip_options or [],
        "text_options": text_options or [],
        "track_options": _track_options_from_metadata({"track_options": track_options or {}}),
        "timeline_dirty": bool(timeline_dirty),
        "updated_at": int(time.time()),
    }


def _read_project_metadata(project_path: Optional[str]) -> dict[str, object]:
    if not project_path:
        return {}
    path = Path(project_path)
    if not path.exists():
        return _build_project_metadata(project_path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return _build_project_metadata(project_path)
    if not isinstance(data, dict):
        return _build_project_metadata(project_path)
    metadata = _build_project_metadata(project_path)
    metadata.update(data)
    metadata["name"] = str(metadata.get("name") or _project_name_from_path(project_path))
    metadata["slug"] = str(metadata.get("slug") or _safe_project_slug(str(metadata["name"])))
    metadata["media_paths"] = _merge_media_paths(metadata.get("media_paths"), [str(metadata.get("video_path") or "")])
    metadata["track_options"] = _track_options_from_metadata(metadata)
    return metadata


def _track_options_payload(visual_visible: bool, text_visible: bool, audio_muted: bool) -> dict[str, bool]:
    return {
        "visual_visible": bool(visual_visible),
        "text_visible": bool(text_visible),
        "audio_muted": bool(audio_muted),
    }


def _track_options_from_metadata(metadata: dict[str, object]) -> dict[str, bool]:
    raw = metadata.get("track_options")
    if not isinstance(raw, dict):
        raw = {}
    return _track_options_payload(
        raw.get("visual_visible", True) is not False,
        raw.get("text_visible", True) is not False,
        bool(raw.get("audio_muted", False)),
    )


def _project_trash_dir(base_dir: Optional[Path] = None) -> Path:
    root = base_dir or Path.home() / "CortaCerto"
    return root / "Lixeira"


def _cleanup_project_trash(trash_dir: Path, now_s: Optional[float] = None, days: int = PROJECT_TRASH_DAYS) -> int:
    if not trash_dir.exists():
        return 0
    now_s = time.time() if now_s is None else float(now_s)
    max_age_s = max(0, int(days)) * 24 * 60 * 60
    removed = 0
    for item in trash_dir.iterdir():
        try:
            if now_s - item.stat().st_mtime < max_age_s:
                continue
            if item.is_dir():
                import shutil
                shutil.rmtree(item)
            else:
                item.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _trash_destination_for(project_path: Path, trash_dir: Path) -> Path:
    return _unique_destination_for(project_path, trash_dir)


def _unique_destination_for(project_path: Path, target_dir: Path) -> Path:
    stem = project_path.stem
    suffix = project_path.suffix
    destination = target_dir / project_path.name
    index = 1
    while destination.exists():
        destination = target_dir / f"{stem}-{index}{suffix}"
        index += 1
    return destination


def _move_project_to_trash(project_path: str, trash_dir: Path) -> Path:
    source = Path(project_path)
    if not source.exists():
        raise FileNotFoundError(f"Projeto não encontrado: {source}")
    trash_dir.mkdir(parents=True, exist_ok=True)
    destination = _trash_destination_for(source, trash_dir)
    source.replace(destination)
    return destination


def _restore_project_from_trash(project_path: str, destination_dir: Path) -> Path:
    source = Path(project_path)
    if not source.exists():
        raise FileNotFoundError(f"Projeto não encontrado na lixeira: {source}")
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = _unique_destination_for(source, destination_dir)
    source.replace(destination)
    return destination


def _is_video_path(path: str) -> bool:
    return Path(str(path).strip().strip("{}")).suffix.lower() in VIDEO_EXTENSIONS


def _is_image_path(path: str) -> bool:
    return Path(str(path).strip().strip("{}")).suffix.lower() in IMAGE_EXTENSIONS


def _is_project_media_path(path: str) -> bool:
    return Path(str(path).strip().strip("{}")).suffix.lower() in MEDIA_EXTENSIONS


def _timeline_clip_fill(clip: TimelineClip) -> str:
    custom = getattr(clip, 'color', '') or ''
    if custom:
        return custom
    ct = str(getattr(clip, 'clip_type', '') or '')
    if ct == 'speech':    return TL_SPEECH
    if ct == 'image':     return TL_IMAGE
    if ct in ('media', 'video'): return TL_MEDIA
    if ct == 'silence':   return TL_SILENCE
    if ct == 'text':      return "#4a3a6a"
    # Legacy fallback: check source_path
    if _is_image_path(getattr(clip, "source_path", "")):
        return TL_IMAGE
    if getattr(clip, "source_path", ""):
        return TL_MEDIA
    return TL_SPEECH


def _is_movable_media_clip(clip: TimelineClip) -> bool:
    source_path = str(getattr(clip, "source_path", "") or "")
    return bool(source_path) and (clip.clip_type in {"image", "media"} or _is_project_media_path(source_path))


def _can_clipboard_timeline_clip(clip: Optional[TimelineClip]) -> bool:
    return bool(clip is not None and (clip.clip_type == "text" or _is_movable_media_clip(clip)))


def _should_double_click_insert_media(path: str, current_video_path: Optional[str], timeline_ready: bool) -> bool:
    if not timeline_ready or not _is_project_media_path(path):
        return False
    if _is_image_path(path):
        return True
    return _is_video_path(path) and str(path) != str(current_video_path or "")


def _same_media_path(left: Optional[str], right: Optional[str]) -> bool:
    return str(left or "").strip().strip("{}").casefold() == str(right or "").strip().strip("{}").casefold()


def _remove_media_path(media_paths: list[str], path: str) -> list[str]:
    return [item for item in media_paths if not _same_media_path(item, path)]


def _media_path_used_in_timeline(path: str, timeline_model: Optional[TimelineModel]) -> bool:
    if timeline_model is None:
        return False
    for clip in timeline_model.video_track.clips:
        if _same_media_path(getattr(clip, "source_path", ""), path):
            return True
    for clip in _timeline_overlay_clips(timeline_model):
        if _same_media_path(getattr(clip, "source_path", ""), path):
            return True
    return False


def _media_display_name(path: str) -> str:
    prefix = "[IMG]" if _is_image_path(path) else "[VID]" if _is_video_path(path) else "[MID]"
    return f"{prefix} {Path(str(path)).name}"


def _clip_inspector_rows_for_mode(base: int, mode: str) -> set[int]:
    common = {base}
    label = {base + 1}
    visual_transform = {base + 2, base + 3, base + 4}
    audio = {base + 5}
    text_controls = {base + 6, base + 7, base + 8, base + 9, base + 10}
    transition = {base + 11}
    text_actions = {base + 12}
    visual_actions = {base + 13}
    chroma = {base + 14, base + 15}
    duration = {base + 16}
    opacity = {base + 17}
    speed = {base + 18}    # Etapa 6 — row+22
    pan      = {base + 19}          # Etapa C — row+23 pan L/R
    fade     = {base + 20, base + 21}  # Etapa C — row+24/25 fade in/out
    rotation = {base + 22}          # Etapa D — row+26 rotation
    blend    = {base + 23}          # Etapa D — row+27 blend mode
    crop     = {base + 24}          # Etapa E — row+28 crop
    color    = {base + 25, base + 26, base + 27}  # Etapa F — row+29/30/31 brightness/contrast/saturation
    normalized = str(mode or "").strip().lower()
    if normalized == "text":
        return common | label | text_controls | text_actions | duration
    if normalized == "visual":
        return common | label | visual_transform | visual_actions | chroma | duration | opacity | pan | fade | rotation | blend | crop | color
    if normalized == "speech":
        return common | label | audio | transition | visual_actions | speed | pan | fade
    return common


def _set_clip_duration_bounds(start_s: float, requested_duration_s: float, timeline_duration_s: float, min_duration_s: float) -> tuple[float, float]:
    timeline_duration_s = max(float(timeline_duration_s), float(min_duration_s))
    min_duration_s = max(0.01, float(min_duration_s))
    start_s = _clamp_float(float(start_s), 0.0, max(0.0, timeline_duration_s - min_duration_s))
    duration_s = _clamp_float(float(requested_duration_s), min_duration_s, timeline_duration_s)
    end_s = min(timeline_duration_s, start_s + duration_s)
    if end_s - start_s < min_duration_s:
        start_s = max(0.0, timeline_duration_s - min_duration_s)
        end_s = timeline_duration_s
    return start_s, end_s


def _can_move_track_item_layer(length: int, index: int, direction: int) -> bool:
    if length <= 1 or not 0 <= index < length:
        return False
    step = 1 if int(direction) > 0 else -1
    target = index + step
    return 0 <= target < length


def _move_track_item_layer(items: list[object], index: int, direction: int) -> tuple[bool, int]:
    if not _can_move_track_item_layer(len(items), index, direction):
        return False, index
    step = 1 if int(direction) > 0 else -1
    target = index + step
    items[index], items[target] = items[target], items[index]
    return True, target


def _clip_type_for_source_path(source_path: str) -> str:
    if _is_image_path(source_path):
        return "image"
    if _is_video_path(source_path):
        return "media"
    return "speech"


def _project_media_counts(media_paths: list[str]) -> tuple[int, int]:
    videos = sum(1 for path in media_paths if _is_video_path(path))
    images = sum(1 for path in media_paths if _is_image_path(path))
    return videos, images


def _first_video_path_from_drop(drop_data: str) -> Optional[str]:
    paths = _video_paths_from_drop(drop_data)
    return paths[0] if paths else None


def _video_paths_from_drop(drop_data: str) -> list[str]:
    return _merge_media_paths([], [item.strip().strip("{}") for item in _split_drop_paths(drop_data) if _is_video_path(item)])


def _media_paths_from_drop(drop_data: str) -> list[str]:
    return _merge_media_paths([], [item.strip().strip("{}") for item in _split_drop_paths(drop_data) if _is_project_media_path(item)])


def _project_media_paths_from_metadata(metadata: dict[str, object]) -> list[str]:
    return _merge_media_paths(metadata.get("media_paths"), [str(metadata.get("video_path") or "")])


def _first_existing_video_path(media_paths: list[str]) -> Optional[str]:
    for path in media_paths:
        if _is_video_path(path) and Path(path).exists():
            return path
    return None


def _merge_media_paths(existing: object, new_paths: list[str]) -> list[str]:
    merged: list[str] = []
    raw_existing = existing if isinstance(existing, list) else []
    for path in [*raw_existing, *new_paths]:
        clean = str(path).strip().strip("{}")
        if not clean or not _is_project_media_path(clean):
            continue
        if clean not in merged:
            merged.append(clean)
    return merged


def _split_drop_paths(drop_data: str) -> list[str]:
    def clean(values: list[str]) -> list[str]:
        return [value.strip().strip("{}") for value in values if value.strip()]

    if not drop_data:
        return []
    try:
        root = tk.Tk()
        root.withdraw()
        values = list(root.tk.splitlist(drop_data))
        root.destroy()
        return clean(values)
    except Exception:
        return clean(re.findall(r"\{[^}]+\}|[^\s]+", drop_data))


def _project_float(value: object, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _project_segments_from_metadata(
    metadata: dict[str, object],
    duration_s: float,
) -> list[tuple[float, float]]:
    raw = metadata.get("timeline_segments")
    if not isinstance(raw, list):
        return []
    segments: list[tuple[float, float]] = []
    duration_s = max(0.0, float(duration_s))
    for item in raw:
        if not isinstance(item, dict):
            continue
        start_s = max(0.0, min(duration_s, _project_float(item.get("start_s"))))
        end_s = max(0.0, min(duration_s, _project_float(item.get("end_s"))))
        if end_s > start_s:
            segments.append((start_s, end_s))
    return segments


def _apply_segments_to_timeline_model(
    timeline_model: TimelineModel,
    duration_s: float,
    segments: list[tuple[float, float]],
) -> None:
    clips = [
        TimelineClip(start_s, end_s, "speech", f"Clip {idx}")
        for idx, (start_s, end_s) in enumerate(segments, start=1)
    ]
    timeline_model.video_track.clips = clips
    timeline_model.audio_track.clips = [
        TimelineClip(c.start_s, c.end_s, c.clip_type, c.label) for c in clips
    ]
    timeline_model.text_track.clips = []
    timeline_model.removed_ranges = _removed_ranges_from_segments(duration_s, segments)
    timeline_model.saved_time_s = sum(end - start for start, end in timeline_model.removed_ranges)


def _clone_timeline_clip(clip: TimelineClip) -> TimelineClip:
    return TimelineClip(
        start_s=clip.start_s,
        end_s=clip.end_s,
        clip_type=clip.clip_type,
        label=clip.label,
        source_path=getattr(clip, "source_path", ""),
        scale_pct=float(getattr(clip, "scale_pct", 100.0)),
        volume_pct=float(getattr(clip, "volume_pct", 100.0)),
        transition=str(getattr(clip, "transition", "Corte") or "Corte"),
        transition_duration_s=float(getattr(clip, "transition_duration_s", 0.4)),
        # ── legacy text fields ──────────────────────────────────────────────
        text_overlay=str(getattr(clip, "text_overlay", "") or ""),
        text_position_x_pct=float(getattr(clip, "text_position_x_pct", 50.0)),
        text_position_y_pct=float(getattr(clip, "text_position_y_pct", 82.0)),
        text_size_pct=float(getattr(clip, "text_size_pct", 100.0)),
        text_color=str(getattr(clip, "text_color", "#ffffff") or "#ffffff"),
        text_background_enabled=bool(getattr(clip, "text_background_enabled", True)),
        text_background_color=str(getattr(clip, "text_background_color", "#000000") or "#000000"),
        # ── extended text style ─────────────────────────────────────────────
        text_font=str(getattr(clip, "text_font", "default") or "default"),
        text_bold=bool(getattr(clip, "text_bold", False)),
        text_italic=bool(getattr(clip, "text_italic", False)),
        text_align=str(getattr(clip, "text_align", "center") or "center"),
        text_background_alpha=float(getattr(clip, "text_background_alpha", 0.65)),
        text_bg_rounded=bool(getattr(clip, "text_bg_rounded", True)),
        text_shadow_enabled=bool(getattr(clip, "text_shadow_enabled", False)),
        text_shadow_color=str(getattr(clip, "text_shadow_color", "#000000") or "#000000"),
        text_shadow_offset_x=int(getattr(clip, "text_shadow_offset_x", 2)),
        text_shadow_offset_y=int(getattr(clip, "text_shadow_offset_y", 2)),
        text_shadow_blur=int(getattr(clip, "text_shadow_blur", 4)),
        text_stroke_enabled=bool(getattr(clip, "text_stroke_enabled", False)),
        text_stroke_color=str(getattr(clip, "text_stroke_color", "#000000") or "#000000"),
        text_stroke_width=int(getattr(clip, "text_stroke_width", 2)),
        text_max_width_pct=float(getattr(clip, "text_max_width_pct", 80.0)),
        text_line_spacing=float(getattr(clip, "text_line_spacing", 1.2)),
        # ── chroma key ──────────────────────────────────────────────────────
        chroma_enabled=bool(getattr(clip, "chroma_enabled", False)),
        chroma_color=str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"),
        chroma_tolerance=float(getattr(clip, "chroma_tolerance", 45.0)),
        # ── overlay position / opacity ───────────────────────────────────────
        position_x_pct=float(getattr(clip, "position_x_pct", 0.0)),
        position_y_pct=float(getattr(clip, "position_y_pct", 0.0)),
        opacity_pct=float(getattr(clip, "opacity_pct", 100.0)),
        # ── speed ───────────────────────────────────────────────────────────
        speed_factor=float(getattr(clip, "speed_factor", 1.0)),
        # ── Etapa C: pan / fade ──────────────────────────────────────────────
        pan_pct=float(getattr(clip, "pan_pct", 0.0)),
        fade_in_s=float(getattr(clip, "fade_in_s", 0.0)),
        fade_out_s=float(getattr(clip, "fade_out_s", 0.0)),
        # ── Etapa D: rotation / blend ────────────────────────────────────────
        rotation_deg=float(getattr(clip, "rotation_deg", 0.0)),
        blend_mode=str(getattr(clip, "blend_mode", "Normal") or "Normal"),
        # ── Etapa E: crop ────────────────────────────────────────────────────
        crop_top_pct=float(getattr(clip, "crop_top_pct", 0.0)),
        crop_bottom_pct=float(getattr(clip, "crop_bottom_pct", 0.0)),
        crop_left_pct=float(getattr(clip, "crop_left_pct", 0.0)),
        crop_right_pct=float(getattr(clip, "crop_right_pct", 0.0)),
        # ── Etapa F: color correction ────────────────────────────────────────
        brightness=float(getattr(clip, "brightness", 0.0)),
        contrast=float(getattr(clip, "contrast", 0.0)),
        saturation=float(getattr(clip, "saturation", 0.0)),
    )


def _clip_options_from_timeline_model(timeline_model: Optional[TimelineModel]) -> list[dict[str, object]]:
    if timeline_model is None:
        return []
    options: list[dict[str, object]] = []
    for layer, clips in (
        ("base", timeline_model.video_track.clips),
        ("overlay", _timeline_overlay_clips(timeline_model)),
    ):
        for clip in clips:
            options.append({
            "start_s": float(clip.start_s),
            "end_s": float(clip.end_s),
            "layer": layer,
            "clip_type": str(getattr(clip, "clip_type", "speech") or "speech"),
            "label": clip.label,
            "source_path": getattr(clip, "source_path", ""),
            "scale_pct": float(getattr(clip, "scale_pct", 100.0)),
            "volume_pct": float(getattr(clip, "volume_pct", 100.0)),
            "transition": str(getattr(clip, "transition", "Corte") or "Corte"),
            "text_overlay": str(getattr(clip, "text_overlay", "") or ""),
            "text_position_x_pct": float(getattr(clip, "text_position_x_pct", 0.0)),
            "text_position_y_pct": float(getattr(clip, "text_position_y_pct", 72.0)),
            "text_size_pct": float(getattr(clip, "text_size_pct", 100.0)),
            "text_color": str(getattr(clip, "text_color", "#ffffff") or "#ffffff"),
            "text_background_enabled": bool(getattr(clip, "text_background_enabled", True)),
            "text_background_color": str(getattr(clip, "text_background_color", "#000000") or "#000000"),
            "chroma_enabled": bool(getattr(clip, "chroma_enabled", False)),
            "chroma_color": str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"),
            "chroma_tolerance": float(getattr(clip, "chroma_tolerance", 45.0)),
            "position_x_pct": float(getattr(clip, "position_x_pct", 0.0)),
            "position_y_pct": float(getattr(clip, "position_y_pct", 0.0)),
            "opacity_pct": float(getattr(clip, "opacity_pct", 100.0)),
            "speed_factor": float(getattr(clip, "speed_factor", 1.0)),
            "transition_duration_s": float(getattr(clip, "transition_duration_s", 0.4)),
            "z_order": int(getattr(clip, "z_order", 0)),
            # Etapa C
            "pan_pct": float(getattr(clip, "pan_pct", 0.0)),
            "fade_in_s": float(getattr(clip, "fade_in_s", 0.0)),
            "fade_out_s": float(getattr(clip, "fade_out_s", 0.0)),
            # Etapa D
            "rotation_deg": float(getattr(clip, "rotation_deg", 0.0)),
            "blend_mode": str(getattr(clip, "blend_mode", "Normal") or "Normal"),
            # Etapa E
            "crop_top_pct":    float(getattr(clip, "crop_top_pct",    0.0)),
            "crop_bottom_pct": float(getattr(clip, "crop_bottom_pct", 0.0)),
            "crop_left_pct":   float(getattr(clip, "crop_left_pct",   0.0)),
            "crop_right_pct":  float(getattr(clip, "crop_right_pct",  0.0)),
            # Etapa F
            "brightness": float(getattr(clip, "brightness", 0.0)),
            "contrast":   float(getattr(clip, "contrast",   0.0)),
            "saturation": float(getattr(clip, "saturation", 0.0)),
            })
    return options


def _text_options_from_timeline_model(timeline_model: Optional[TimelineModel]) -> list[dict[str, object]]:
    if timeline_model is None:
        return []
    return [
        {
            "start_s": float(clip.start_s),
            "end_s": float(clip.end_s),
            "label": clip.label,
            "text_overlay": str(getattr(clip, "text_overlay", "") or clip.label or ""),
            "text_position_x_pct": float(getattr(clip, "text_position_x_pct", 0.0)),
            "text_position_y_pct": float(getattr(clip, "text_position_y_pct", 72.0)),
            "text_size_pct": float(getattr(clip, "text_size_pct", 100.0)),
            "text_color": str(getattr(clip, "text_color", "#ffffff") or "#ffffff"),
            "text_background_enabled": bool(getattr(clip, "text_background_enabled", True)),
            "text_background_color": str(getattr(clip, "text_background_color", "#000000") or "#000000"),
        }
        for clip in _timeline_text_clips(timeline_model)
        if str(getattr(clip, "text_overlay", "") or clip.label or "").strip()
    ]


def _apply_clip_options_to_timeline_model(timeline_model: TimelineModel, raw_options: object) -> None:
    if not isinstance(raw_options, list):
        return
    base_count = len(timeline_model.video_track.clips)
    overlay_clips: list[TimelineClip] = []
    for idx, raw in enumerate(raw_options):
        if not isinstance(raw, dict):
            continue
        layer = str(raw.get("layer") or "").strip().lower()
        is_overlay = layer == "overlay" or (not layer and idx >= base_count)
        if not is_overlay and idx < base_count:
            clip = timeline_model.video_track.clips[idx]
        else:
            clip = TimelineClip(
                _project_float(raw.get("start_s"), 0.0),
                _project_float(raw.get("end_s"), 0.0),
                str(raw.get("clip_type") or "media"),
                str(raw.get("label") or ""),
            )
            overlay_clips.append(clip)
        clip.clip_type = str(raw.get("clip_type") or clip.clip_type or "speech")
        clip.label = str(raw.get("label") or clip.label)
        clip.source_path = str(raw.get("source_path") or "")
        if clip.source_path:
            clip.clip_type = _clip_type_for_source_path(clip.source_path)
        clip.scale_pct = _project_float(raw.get("scale_pct"), 100.0)
        clip.volume_pct = _project_float(raw.get("volume_pct"), 100.0)
        clip.transition = str(raw.get("transition") or "Corte")
        clip.text_overlay = str(raw.get("text_overlay") or "")
        clip.text_position_x_pct = _project_float(raw.get("text_position_x_pct"), 0.0)
        clip.text_position_y_pct = _project_float(raw.get("text_position_y_pct"), 72.0)
        clip.text_size_pct = _project_float(raw.get("text_size_pct"), 100.0)
        clip.text_color = _normalize_hex_color(str(raw.get("text_color") or "#ffffff"), "#ffffff")
        clip.text_background_enabled = bool(raw.get("text_background_enabled", True))
        clip.text_background_color = _normalize_hex_color(str(raw.get("text_background_color") or "#000000"), "#000000")
        clip.chroma_enabled = bool(raw.get("chroma_enabled", False))
        clip.chroma_color = _normalize_hex_color(str(raw.get("chroma_color") or "#00ff00"))
        clip.chroma_tolerance = _project_float(raw.get("chroma_tolerance"), 45.0)
        clip.position_x_pct = _project_float(raw.get("position_x_pct"), 0.0)
        clip.position_y_pct = _project_float(raw.get("position_y_pct"), 0.0)
        clip.opacity_pct = _project_float(raw.get("opacity_pct"), 100.0)
        clip.speed_factor = max(0.1, _project_float(raw.get("speed_factor"), 1.0))
        clip.transition_duration_s = max(0.1, _project_float(raw.get("transition_duration_s"), 0.4))
        clip.z_order = int(raw.get("z_order") or 0)
        # Etapa C
        clip.pan_pct = _project_float(raw.get("pan_pct"), 0.0)
        clip.fade_in_s = max(0.0, _project_float(raw.get("fade_in_s"), 0.0))
        clip.fade_out_s = max(0.0, _project_float(raw.get("fade_out_s"), 0.0))
        # Etapa D
        clip.rotation_deg = _project_float(raw.get("rotation_deg"), 0.0)
        clip.blend_mode = str(raw.get("blend_mode") or "Normal")
        # Etapa E
        clip.crop_top_pct    = max(0.0, min(50.0, _project_float(raw.get("crop_top_pct"),    0.0)))
        clip.crop_bottom_pct = max(0.0, min(50.0, _project_float(raw.get("crop_bottom_pct"), 0.0)))
        clip.crop_left_pct   = max(0.0, min(50.0, _project_float(raw.get("crop_left_pct"),   0.0)))
        clip.crop_right_pct  = max(0.0, min(50.0, _project_float(raw.get("crop_right_pct"),  0.0)))
        # Etapa F
        clip.brightness = max(-100.0, min(100.0, _project_float(raw.get("brightness"), 0.0)))
        clip.contrast   = max(-100.0, min(100.0, _project_float(raw.get("contrast"),   0.0)))
        clip.saturation = max(-100.0, min(100.0, _project_float(raw.get("saturation"), 0.0)))
    timeline_model.overlay_track.clips = overlay_clips
    timeline_model.audio_track.clips = [_clone_timeline_clip(clip) for clip in timeline_model.video_track.clips]
    _rebuild_text_track_from_video_text(timeline_model)


def _apply_text_options_to_timeline_model(timeline_model: TimelineModel, raw_options: object) -> None:
    if not isinstance(raw_options, list):
        return
    text_clips: list[TimelineClip] = []
    for raw in raw_options:
        if not isinstance(raw, dict):
            continue
        start_s = _project_float(raw.get("start_s"), 0.0)
        end_s = _project_float(raw.get("end_s"), start_s)
        text = str(raw.get("text_overlay") or raw.get("label") or "").strip()
        if end_s <= start_s or not text:
            continue
        text_clips.append(
            TimelineClip(
                start_s,
                end_s,
                "text",
                text,
                text_overlay=text,
                text_position_x_pct=_project_float(raw.get("text_position_x_pct"), 0.0),
                text_position_y_pct=_project_float(raw.get("text_position_y_pct"), 72.0),
                text_size_pct=_project_float(raw.get("text_size_pct"), 100.0),
                text_color=_normalize_hex_color(str(raw.get("text_color") or "#ffffff"), "#ffffff"),
                text_background_enabled=bool(raw.get("text_background_enabled", True)),
                text_background_color=_normalize_hex_color(str(raw.get("text_background_color") or "#000000"), "#000000"),
            )
        )
    if text_clips:
        timeline_model.text_track.clips = text_clips


def _timeline_text_clips(timeline_model: Optional[TimelineModel]) -> list[TimelineClip]:
    if timeline_model is None:
        return []
    track = getattr(timeline_model, "text_track", None)
    if track is None:
        timeline_model.text_track = TimelineTrack(name="Texto")
        return []
    return track.clips


def _timeline_overlay_clips(timeline_model: Optional[TimelineModel]) -> list[TimelineClip]:
    if timeline_model is None:
        return []
    track = getattr(timeline_model, "overlay_track", None)
    if track is None:
        timeline_model.overlay_track = TimelineTrack(name="Overlay")
        track = timeline_model.overlay_track
    return track.clips


def _rebuild_text_track_from_video_text(timeline_model: TimelineModel) -> None:
    timeline_model.text_track.clips = []
    for clip in timeline_model.video_track.clips:
        if str(getattr(clip, "text_overlay", "") or "").strip():
            _upsert_text_overlay_clip(timeline_model, clip)


def _upsert_text_overlay_clip(timeline_model: Optional[TimelineModel], video_clip: TimelineClip) -> None:
    if timeline_model is None:
        return
    text = str(getattr(video_clip, "text_overlay", "") or "").strip()
    if not text:
        return
    text_clips = _timeline_text_clips(timeline_model)
    for clip in text_clips:
        if abs(clip.start_s - video_clip.start_s) < 0.001 and abs(clip.end_s - video_clip.end_s) < 0.001:
            clip.label = text
            clip.text_overlay = text
            clip.text_position_x_pct = float(getattr(video_clip, "text_position_x_pct", 0.0))
            clip.text_position_y_pct = float(getattr(video_clip, "text_position_y_pct", 72.0))
            clip.text_size_pct = float(getattr(video_clip, "text_size_pct", 100.0))
            clip.text_color = str(getattr(video_clip, "text_color", "#ffffff") or "#ffffff")
            clip.text_background_enabled = bool(getattr(video_clip, "text_background_enabled", True))
            clip.text_background_color = str(getattr(video_clip, "text_background_color", "#000000") or "#000000")
            return
    text_clips.append(
        TimelineClip(
            video_clip.start_s,
            video_clip.end_s,
            "text",
            text,
            text_overlay=text,
            text_position_x_pct=float(getattr(video_clip, "text_position_x_pct", 0.0)),
            text_position_y_pct=float(getattr(video_clip, "text_position_y_pct", 72.0)),
            text_size_pct=float(getattr(video_clip, "text_size_pct", 100.0)),
            text_color=str(getattr(video_clip, "text_color", "#ffffff") or "#ffffff"),
            text_background_enabled=bool(getattr(video_clip, "text_background_enabled", True)),
            text_background_color=str(getattr(video_clip, "text_background_color", "#000000") or "#000000"),
        )
    )


def _sync_text_clip_to_video_overlay(timeline_model: Optional[TimelineModel], text_clip: TimelineClip) -> None:
    if timeline_model is None:
        return
    for clip in timeline_model.video_track.clips:
        if abs(clip.start_s - text_clip.start_s) < 0.001 and abs(clip.end_s - text_clip.end_s) < 0.001:
            text = str(getattr(text_clip, "text_overlay", "") or text_clip.label or "").strip()
            clip.text_overlay = text
            clip.text_position_x_pct = float(getattr(text_clip, "text_position_x_pct", 0.0))
            clip.text_position_y_pct = float(getattr(text_clip, "text_position_y_pct", 72.0))
            clip.text_size_pct = float(getattr(text_clip, "text_size_pct", 100.0))
            clip.text_color = str(getattr(text_clip, "text_color", "#ffffff") or "#ffffff")
            clip.text_background_enabled = bool(getattr(text_clip, "text_background_enabled", True))
            clip.text_background_color = str(getattr(text_clip, "text_background_color", "#000000") or "#000000")
            return


def _clear_video_text_overlay_for_text_clip(timeline_model: Optional[TimelineModel], text_clip: TimelineClip) -> None:
    if timeline_model is None:
        return
    for clip in timeline_model.video_track.clips:
        if abs(clip.start_s - text_clip.start_s) < 0.001 and abs(clip.end_s - text_clip.end_s) < 0.001:
            clip.text_overlay = ""
            return


def _clip_for_time(timeline_model: Optional[TimelineModel], time_s: float) -> Optional[TimelineClip]:
    if timeline_model is None:
        return None
    for clip in timeline_model.video_track.clips:
        if clip.start_s <= time_s < clip.end_s:
            return clip
    if timeline_model.video_track.clips:
        last = timeline_model.video_track.clips[-1]
        if abs(float(time_s) - last.end_s) < 0.001:
            return last
    return None


def _visual_clip_for_time(timeline_model: Optional[TimelineModel], time_s: float) -> Optional[TimelineClip]:
    if timeline_model is None:
        return None
    return _overlay_clip_for_time(timeline_model, time_s) or _clip_for_time(timeline_model, time_s)


def _overlay_clip_for_time(timeline_model: Optional[TimelineModel], time_s: float) -> Optional[TimelineClip]:
    clips = _overlay_clips_for_time(timeline_model, time_s)
    return clips[-1] if clips else None


def _overlay_clips_for_time(timeline_model: Optional[TimelineModel], time_s: float) -> list[TimelineClip]:
    if timeline_model is None:
        return []
    clips = [
        clip
        for clip in _timeline_overlay_clips(timeline_model)
        if clip.start_s <= time_s < clip.end_s
    ]
    # Sort by z_order so last element is always the topmost (rendered last = in front)
    clips.sort(key=lambda c: getattr(c, "z_order", 0))
    return clips


def _text_clip_for_time(timeline_model: Optional[TimelineModel], time_s: float) -> Optional[TimelineClip]:
    if timeline_model is None:
        return None
    for clip in reversed(_timeline_text_clips(timeline_model)):
        if clip.start_s <= time_s < clip.end_s:
            return clip
    return None


def _clip_source_frame_index(clip: TimelineClip, timeline_time_s: float, fps: float, total_frames: int) -> int:
    offset_s = max(0.0, float(timeline_time_s) - float(clip.start_s))
    frame = int(round(offset_s * max(1.0, float(fps))))
    return max(0, min(max(0, int(total_frames) - 1), frame))


def _apply_clip_preview_options(
    image: Image.Image,
    clip: Optional[TimelineClip],
    include_text: bool = True,
    chroma_background: Optional[Image.Image] = None,
) -> Image.Image:
    if clip is None:
        return image
    scale_pct = max(25.0, min(300.0, float(getattr(clip, "scale_pct", 100.0))))
    pos_x = _clamp_float(float(getattr(clip, "position_x_pct", 0.0)), -100.0, 100.0)
    pos_y = _clamp_float(float(getattr(clip, "position_y_pct", 0.0)), -100.0, 100.0)
    text_overlay = str(getattr(clip, "text_overlay", "") or "").strip() if include_text else ""
    chroma_enabled = bool(getattr(clip, "chroma_enabled", False))
    needs_scale = abs(scale_pct - 100.0) > 0.01
    needs_position = abs(pos_x) > 0.01 or abs(pos_y) > 0.01
    if not needs_scale and not needs_position and not text_overlay and not chroma_enabled:
        return image

    out = image.copy()
    if chroma_enabled:
        out = _apply_chroma_key_preview(
            out,
            str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"),
            float(getattr(clip, "chroma_tolerance", 45.0)),
            background=chroma_background,
        )
    if needs_scale or needs_position:
        out = _scale_preview_image_positioned(out, scale_pct, pos_x, pos_y)
    if text_overlay:
        out = _draw_preview_text_overlay(
            out,
            text_overlay,
            float(getattr(clip, "text_position_x_pct", 0.0)),
            float(getattr(clip, "text_position_y_pct", 72.0)),
            float(getattr(clip, "text_size_pct", 100.0)),
            str(getattr(clip, "text_color", "#ffffff") or "#ffffff"),
            bool(getattr(clip, "text_background_enabled", True)),
            str(getattr(clip, "text_background_color", "#000000") or "#000000"),
        )
    return out


def _apply_text_clip_preview_options(image: Image.Image, text_clip: Optional[TimelineClip]) -> Image.Image:
    if text_clip is None:
        return image
    text_overlay = str(getattr(text_clip, "text_overlay", "") or text_clip.label or "").strip()
    if not text_overlay:
        return image
    return _draw_preview_text_overlay(
        image,
        text_overlay,
        float(getattr(text_clip, "text_position_x_pct", 0.0)),
        float(getattr(text_clip, "text_position_y_pct", 72.0)),
        float(getattr(text_clip, "text_size_pct", 100.0)),
        str(getattr(text_clip, "text_color", "#ffffff") or "#ffffff"),
        bool(getattr(text_clip, "text_background_enabled", True)),
        str(getattr(text_clip, "text_background_color", "#000000") or "#000000"),
    )


def _image_source_preview_image(path: str, target_size: tuple[int, int] | None = None) -> Optional[Image.Image]:
    try:
        image = Image.open(path).convert("RGB")
    except Exception:
        return None
    if not target_size:
        return image
    width, height = target_size
    if width <= 0 or height <= 0:
        return image
    contained = ImageOps.contain(image, (width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), "black")
    x = (width - contained.width) // 2
    y = (height - contained.height) // 2
    canvas.paste(contained, (x, y))
    return canvas


def _fit_overlay_source_to_canvas(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    width, height = size
    if width <= 0 or height <= 0:
        return image.convert("RGB")
    contained = ImageOps.contain(image.convert("RGB"), (width, height), Image.LANCZOS)
    canvas = Image.new("RGB", (width, height), "black")
    x = (width - contained.width) // 2
    y = (height - contained.height) // 2
    canvas.paste(contained, (x, y))
    return canvas


def _compose_visual_overlay_preview(
    base: Image.Image,
    overlay_source: Image.Image,
    overlay_clip: TimelineClip,
) -> Image.Image:
    import numpy as np
    width, height = base.size
    if width <= 0 or height <= 0:
        return base
    scale_pct = max(25.0, min(300.0, float(getattr(overlay_clip, "scale_pct", 100.0))))
    chroma_enabled = bool(getattr(overlay_clip, "chroma_enabled", False))
    opacity = _clip_opacity_factor(overlay_clip)
    rotation_deg = float(getattr(overlay_clip, "rotation_deg", 0.0))
    blend_mode = str(getattr(overlay_clip, "blend_mode", "Normal") or "Normal")
    overlay = _fit_overlay_source_to_canvas(overlay_source, (width, height))

    # Etapa E: apply per-clip crop (each edge 0..50 %)
    _ct = float(getattr(overlay_clip, "crop_top_pct",    0.0))
    _cb = float(getattr(overlay_clip, "crop_bottom_pct", 0.0))
    _cl = float(getattr(overlay_clip, "crop_left_pct",   0.0))
    _cr = float(getattr(overlay_clip, "crop_right_pct",  0.0))
    if any(c > 0.1 for c in (_ct, _cb, _cl, _cr)):
        _ow, _oh = overlay.size
        _t = int(_oh * _ct / 100.0)
        _b = int(_oh * (1.0 - _cb / 100.0))
        _l = int(_ow * _cl / 100.0)
        _r = int(_ow * (1.0 - _cr / 100.0))
        if _r > _l and _b > _t:
            overlay = overlay.crop((_l, _t, _r, _b)).resize((_ow, _oh), Image.LANCZOS)

    # Etapa F: per-clip color correction (PIL ImageEnhance)
    _bri = float(getattr(overlay_clip, "brightness", 0.0))
    _con = float(getattr(overlay_clip, "contrast",   0.0))
    _sat = float(getattr(overlay_clip, "saturation", 0.0))
    if any(abs(v) > 0.5 for v in (_bri, _con, _sat)):
        from PIL import ImageEnhance
        if abs(_bri) > 0.5:
            overlay = ImageEnhance.Brightness(overlay).enhance((_bri + 100.0) / 100.0)
        if abs(_con) > 0.5:
            overlay = ImageEnhance.Contrast(overlay).enhance((_con + 100.0) / 100.0)
        if abs(_sat) > 0.5:
            overlay = ImageEnhance.Color(overlay).enhance((_sat + 100.0) / 100.0)

    # Apply rotation if needed
    if abs(rotation_deg) > 0.5:
        overlay = overlay.rotate(-rotation_deg, resample=Image.BICUBIC, expand=False)

    if scale_pct >= 100.0:
        rendered = _apply_clip_preview_options(
            overlay,
            overlay_clip,
            include_text=False,
            chroma_background=base,
        )
        return _blend_overlay_pil(base.convert("RGB"), rendered.convert("RGB"), opacity, blend_mode)

    left, top, right, bottom = _preview_visual_display_bounds((0, 0, width, height), overlay_clip)
    left = max(0, min(width, left))
    top = max(0, min(height, top))
    right = max(left, min(width, right))
    bottom = max(top, min(height, bottom))
    box_w = max(1, right - left)
    box_h = max(1, bottom - top)

    scaled_overlay = overlay.resize((box_w, box_h), Image.LANCZOS)
    if chroma_enabled:
        scaled_overlay = _apply_chroma_key_preview(
            scaled_overlay,
            str(getattr(overlay_clip, "chroma_color", "#00ff00") or "#00ff00"),
            float(getattr(overlay_clip, "chroma_tolerance", 45.0)),
            background=base.crop((left, top, right, bottom)),
        )

    out = base.copy().convert("RGB")
    background_crop = out.crop((left, top, right, bottom)).convert("RGB")
    composited = _blend_overlay_pil(background_crop, scaled_overlay.convert("RGB"), opacity, blend_mode)
    out.paste(composited, (left, top))
    return out


def _blend_overlay_pil(base: "Image.Image", overlay: "Image.Image", opacity: float, blend_mode: str) -> "Image.Image":
    """Blend *overlay* onto *base* with *opacity* and a given blend mode (Etapa D)."""
    import numpy as np
    opacity = max(0.0, min(1.0, float(opacity)))
    if opacity <= 0.0:
        return base
    if overlay.size != base.size:
        overlay = overlay.resize(base.size, Image.LANCZOS)
    b = np.asarray(base.convert("RGB"), dtype=np.float32) / 255.0
    o = np.asarray(overlay.convert("RGB"), dtype=np.float32) / 255.0
    m = blend_mode.strip().lower()
    if m == "screen":
        blended = 1.0 - (1.0 - b) * (1.0 - o)
    elif m == "multiply":
        blended = b * o
    elif m == "overlay":
        blended = np.where(b < 0.5, 2.0 * b * o, 1.0 - 2.0 * (1.0 - b) * (1.0 - o))
    elif m == "add":
        blended = np.clip(b + o, 0.0, 1.0)
    elif m == "darken":
        blended = np.minimum(b, o)
    elif m == "lighten":
        blended = np.maximum(b, o)
    elif m in ("soft light", "soft_light", "softlight"):
        blended = (1.0 - 2.0 * o) * b ** 2 + 2.0 * o * b
    else:
        blended = o  # Normal
    result = np.clip(b * (1.0 - opacity) + blended * opacity, 0.0, 1.0)
    return Image.fromarray((result * 255).astype(np.uint8), "RGB")


def _clip_opacity_factor(clip: TimelineClip) -> float:
    return _clamp_float(float(getattr(clip, "opacity_pct", 100.0)), 0.0, 100.0) / 100.0


def _scale_preview_image_centered(image: Image.Image, scale_pct: float) -> Image.Image:
    return _scale_preview_image_positioned(image, scale_pct, 0.0, 0.0)


def _scale_preview_image_positioned(
    image: Image.Image,
    scale_pct: float,
    pos_x_pct: float = 0.0,
    pos_y_pct: float = 0.0,
) -> Image.Image:
    width, height = image.size
    scale = max(0.25, min(3.0, scale_pct / 100.0))
    pos_x = _clamp_float(pos_x_pct, -100.0, 100.0) / 100.0
    pos_y = _clamp_float(pos_y_pct, -100.0, 100.0) / 100.0
    if abs(scale - 1.0) < 0.0001 and abs(pos_x) < 0.0001 and abs(pos_y) < 0.0001:
        return image
    if scale > 1.0:
        crop_w = max(1, int(width / scale))
        crop_h = max(1, int(height / scale))
        max_x = max(0, width - crop_w)
        max_y = max(0, height - crop_h)
        x1 = int(round(max_x / 2 + pos_x * max_x / 2))
        y1 = int(round(max_y / 2 + pos_y * max_y / 2))
        x1 = max(0, min(max_x, x1))
        y1 = max(0, min(max_y, y1))
        return image.crop((x1, y1, x1 + crop_w, y1 + crop_h)).resize((width, height), Image.LANCZOS)

    resized_w = max(1, int(width * scale))
    resized_h = max(1, int(height * scale))
    resized = image.resize((resized_w, resized_h), Image.LANCZOS)
    canvas = Image.new(image.mode, (width, height), "black")
    free_x = max(0, width - resized_w)
    free_y = max(0, height - resized_h)
    x1 = int(round(free_x / 2 + pos_x * free_x / 2))
    y1 = int(round(free_y / 2 + pos_y * free_y / 2))
    canvas.paste(resized, (max(0, min(free_x, x1)), max(0, min(free_y, y1))))
    return canvas


def _draw_preview_text_overlay(
    image: Image.Image,
    text: str,
    pos_x_pct: float = 0.0,
    pos_y_pct: float = 72.0,
    size_pct: float = 100.0,
    text_color: str = "#ffffff",
    background_enabled: bool = True,
    background_color: str = "#000000",
) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out)
    width, height = out.size
    font_scale = max(0.5, min(2.2, float(size_pct) / 100.0))
    text_x, text_y = _preview_text_anchor(width, height, pos_x_pct, pos_y_pct)
    pad = max(5, int(8 * font_scale))
    line_h = max(18, int(height * 0.045 * font_scale))
    lines = _preview_text_lines(text)
    text_w = min(width - 2 * pad, max(40, int(max(len(line) for line in lines) * line_h * 0.48)))
    text_h = line_h * len(lines)
    if background_enabled:
        draw.rounded_rectangle(
            (text_x - pad, text_y - pad, min(width - 1, text_x + text_w + pad), min(height - 1, text_y + text_h + pad)),
            radius=4,
            fill=_hex_to_rgb(_normalize_hex_color(background_color, "#000000")),
        )
    for idx, line in enumerate(lines):
        draw.text((text_x, text_y + idx * line_h), line, fill=_hex_to_rgb(_normalize_hex_color(text_color, "#ffffff")))
    return out


def _preview_text_lines(text: str, max_lines: int = 4, max_chars: int = 80) -> list[str]:
    lines = [line.strip() for line in str(text or "").replace("\r\n", "\n").split("\n") if line.strip()]
    if not lines:
        return [""]
    return [line[:max_chars] for line in lines[:max_lines]]


def _apply_chroma_key_preview(
    image: Image.Image,
    color: str,
    tolerance: float,
    background: Optional[Image.Image] = None,
) -> Image.Image:
    target = np.array(_hex_to_rgb(_normalize_hex_color(color)), dtype=np.int16)
    arr = np.array(image.convert("RGB"), dtype=np.int16)
    diff = np.linalg.norm(arr - target, axis=2)
    mask = diff <= max(1.0, float(tolerance))
    if not mask.any():
        return image
    out = arr.astype(np.uint8)
    if background is not None:
        bg = np.array(background.convert("RGB").resize(image.size), dtype=np.uint8)
        out[mask] = bg[mask]
        return Image.fromarray(out, "RGB")
    checker = ((np.indices(mask.shape).sum(axis=0) // 18) % 2) * 42 + 24
    out[mask] = np.stack([checker, checker, checker], axis=2)[mask].astype(np.uint8)
    return Image.fromarray(out, "RGB")


def _normalize_hex_color(value: str, default: str = "#00ff00") -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        text = f"#{text}"
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    return default


def _hex_to_rgb(value: str) -> tuple[int, int, int]:
    color = _normalize_hex_color(value)
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _sample_preview_hex_color(
    image: Optional[Image.Image],
    display_box: tuple[int, int, int, int],
    x: int,
    y: int,
) -> Optional[str]:
    if image is None:
        return None
    left, top, width, height = display_box
    if width <= 0 or height <= 0:
        return None
    if x < left or y < top or x >= left + width or y >= top + height:
        return None
    px = max(0, min(width - 1, x - left))
    py = max(0, min(height - 1, y - top))
    r, g, b = image.convert("RGB").getpixel((px, py))
    return f"#{r:02x}{g:02x}{b:02x}"


def _preview_base_image_for_timeline(
    preview_image: Image.Image,
    timeline_model: Optional[TimelineModel],
    active_clip: Optional[TimelineClip],
) -> Image.Image:
    if timeline_model is None or active_clip is not None:
        return preview_image
    return Image.new(preview_image.mode, preview_image.size, "black")


def _point_inside_display_box(display_box: tuple[int, int, int, int], x: int, y: int) -> bool:
    box_x, box_y, box_w, box_h = display_box
    return box_w > 0 and box_h > 0 and box_x <= x < box_x + box_w and box_y <= y < box_y + box_h


def _point_inside_rect(x: int, y: int, left: int, top: int, width: int, height: int) -> bool:
    return width > 0 and height > 0 and left <= x < left + width and top <= y < top + height


def _preview_control_handles(
    display_box: tuple[int, int, int, int],
    clip: Optional[TimelineClip] = None,
    include_scale: bool = True,
) -> dict[str, tuple[int, int]]:
    box_x, box_y, box_w, box_h = display_box
    handles: dict[str, tuple[int, int]] = {}
    if include_scale and (clip is None or getattr(clip, "clip_type", "") != "text"):
        _left, _top, right, bottom = _preview_visual_display_bounds(display_box, clip)
        handles["scale"] = (right, bottom)
    if clip is not None and str(getattr(clip, "text_overlay", "") or "").strip():
        text_x, text_y = _preview_text_anchor(
            box_w,
            box_h,
            float(getattr(clip, "text_position_x_pct", 0.0)),
            float(getattr(clip, "text_position_y_pct", 72.0)),
        )
        handles["text"] = (box_x + text_x, box_y + text_y)
        if include_scale and getattr(clip, "clip_type", "") == "text":
            _left, _top, right, bottom = _preview_text_display_bounds(display_box, clip)
            handles["text_scale"] = (right, bottom)
    return handles


def _preview_visual_display_bounds(
    display_box: tuple[int, int, int, int],
    clip: Optional[TimelineClip] = None,
) -> tuple[int, int, int, int]:
    box_x, box_y, box_w, box_h = display_box
    if box_w <= 0 or box_h <= 0:
        return box_x, box_y, box_x, box_y
    scale = max(0.25, min(3.0, float(getattr(clip, "scale_pct", 100.0)) / 100.0)) if clip is not None else 1.0
    if scale >= 1.0:
        return box_x, box_y, box_x + box_w, box_y + box_h
    pos_x = _clamp_float(float(getattr(clip, "position_x_pct", 0.0)), -100.0, 100.0) / 100.0 if clip is not None else 0.0
    pos_y = _clamp_float(float(getattr(clip, "position_y_pct", 0.0)), -100.0, 100.0) / 100.0 if clip is not None else 0.0
    visual_w = max(1, int(round(box_w * scale)))
    visual_h = max(1, int(round(box_h * scale)))
    free_x = max(0, box_w - visual_w)
    free_y = max(0, box_h - visual_h)
    left = box_x + int(round(free_x / 2 + pos_x * free_x / 2))
    top = box_y + int(round(free_y / 2 + pos_y * free_y / 2))
    return left, top, left + visual_w, top + visual_h


def _track_control_status(visual_visible: bool, text_visible: bool, audio_muted: bool) -> str:
    visual = "visual ativo" if visual_visible else "visual oculto"
    text = "texto ativo" if text_visible else "texto oculto"
    audio = "audio mutado" if audio_muted else "audio ativo"
    return f"Tracks: {visual} | {text} | {audio}."


def _preview_control_hit(
    display_box: tuple[int, int, int, int],
    x: int,
    y: int,
    clip: Optional[TimelineClip] = None,
    radius: int = 12,
    include_scale: bool = True,
) -> Optional[str]:
    if not _point_inside_display_box(display_box, x, y):
        return None
    for name, (hx, hy) in _preview_control_handles(display_box, clip, include_scale=include_scale).items():
        if abs(x - hx) <= radius and abs(y - hy) <= radius:
            return name
    return None


def _preview_text_display_bounds(
    display_box: tuple[int, int, int, int],
    text_clip: TimelineClip,
) -> tuple[int, int, int, int]:
    box_x, box_y, box_w, box_h = display_box
    text = str(getattr(text_clip, "text_overlay", "") or text_clip.label or "")
    font_scale = max(0.5, min(2.2, float(getattr(text_clip, "text_size_pct", 100.0)) / 100.0))
    text_x, text_y = _preview_text_anchor(
        box_w,
        box_h,
        float(getattr(text_clip, "text_position_x_pct", 0.0)),
        float(getattr(text_clip, "text_position_y_pct", 72.0)),
    )
    pad = max(5, int(8 * font_scale))
    line_h = max(18, int(box_h * 0.045 * font_scale))
    text_w = min(box_w - 2 * pad, max(40, int(len(text[:80]) * line_h * 0.48)))
    left = box_x + text_x - pad
    top = box_y + text_y - pad
    right = box_x + min(box_w - 1, text_x + text_w + pad)
    bottom = box_y + min(box_h - 1, text_y + line_h + pad)
    return left, top, right, bottom


def _preview_text_clip_hit(
    timeline_model: Optional[TimelineModel],
    time_s: float,
    display_box: tuple[int, int, int, int],
    x: int,
    y: int,
) -> Optional[int]:
    if timeline_model is None or not _point_inside_display_box(display_box, x, y):
        return None
    text_clips = _timeline_text_clips(timeline_model)
    for idx in range(len(text_clips) - 1, -1, -1):
        clip = text_clips[idx]
        if not (clip.start_s <= time_s < clip.end_s):
            continue
        left, top, right, bottom = _preview_text_display_bounds(display_box, clip)
        if left <= x <= right and top <= y <= bottom:
            return idx
    return None


def _preview_overlay_clip_hit(
    timeline_model: Optional[TimelineModel],
    time_s: float,
    display_box: tuple[int, int, int, int],
    x: int,
    y: int,
) -> Optional[int]:
    """Return the list index (in overlay_track.clips) of the topmost overlay
    whose visual bounds contain (x, y), or None.

    Clips are tested in reverse z_order so the frontmost (highest z_order)
    is reported first.
    """
    if timeline_model is None or not _point_inside_display_box(display_box, x, y):
        return None
    # _overlay_clips_for_time returns clips sorted by z_order ascending;
    # iterate in reverse so topmost is tested first.
    active_clips = _overlay_clips_for_time(timeline_model, time_s)
    all_clips = _timeline_overlay_clips(timeline_model)
    for clip in reversed(active_clips):
        left, top, right, bottom = _preview_visual_display_bounds(display_box, clip)
        if left <= x <= right and top <= y <= bottom:
            try:
                return all_clips.index(clip)
            except ValueError:
                pass
    return None


def _preview_text_anchor(width: int, height: int, pos_x_pct: float, pos_y_pct: float) -> tuple[int, int]:
    x_ratio = (_clamp_float(pos_x_pct, -100.0, 100.0) + 100.0) / 200.0
    y_ratio = _clamp_float(pos_y_pct, 0.0, 100.0) / 100.0
    return (
        int(round(max(0, width - 1) * x_ratio)),
        int(round(max(0, height - 1) * y_ratio)),
    )


def _clamp_float(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(value)))


def _fit_preview_image(image: Image.Image, canvas_w: int, canvas_h: int) -> Image.Image:
    """Resize a preview frame to fit inside the preview canvas."""
    iw, ih = image.size
    if iw <= 0 or ih <= 0:
        return image
    canvas_w = max(1, int(canvas_w))
    canvas_h = max(1, int(canvas_h))
    scale = min(canvas_w / iw, canvas_h / ih)
    nw = max(1, int(iw * scale))
    nh = max(1, int(ih * scale))
    return image.resize((nw, nh), Image.LANCZOS)


def _removed_ranges_from_segments(
    duration_s: float,
    segments: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    removed: list[tuple[float, float]] = []
    cursor = 0.0
    for start_s, end_s in sorted(segments):
        if start_s > cursor:
            removed.append((cursor, start_s))
        cursor = max(cursor, end_s)
    if cursor < duration_s:
        removed.append((cursor, duration_s))
    return removed


def _timeline_track_bounds(canvas_width: int) -> tuple[int, int]:
    return TL_LABEL_W, max(TL_LABEL_W + 1, canvas_width - TL_PAD_R)


def _timeline_x_to_time(x: int, duration_s: float, x1: int, x2: int) -> float:
    span = max(1, x2 - x1)
    pct = max(0.0, min(1.0, (x - x1) / span))
    return pct * max(0.0, duration_s)


def _timeline_time_to_x(time_s: float, duration_s: float, x1: int, x2: int) -> int:
    span = max(1, x2 - x1)
    pct = 0.0 if duration_s <= 0 else max(0.0, min(1.0, time_s / duration_s))
    return int(x1 + pct * span)


def _timeline_zoom_window(duration_s: float, zoom: float, center_s: float) -> tuple[float, float]:
    duration = max(0.0, float(duration_s))
    if duration <= 0:
        return 0.0, 0.0
    zoom_value = max(TL_ZOOM_MIN, float(zoom))
    if zoom_value < 0.999:
        padded_duration = duration / zoom_value
        pad = max(0.0, (padded_duration - duration) * 0.5)
        return -pad, duration + pad
    if zoom_value <= 1.001:
        return 0.0, duration
    visible = max(0.5, duration / zoom_value)
    margin = min(duration * 0.15, visible * 0.35)
    center = max(0.0, min(duration, float(center_s)))
    start = center - visible * 0.5
    end = center + visible * 0.5
    if start < 0:
        end += -start
        start = 0.0
    if end > duration:
        start -= end - duration
        end = duration
    start = max(0.0, start - margin)
    end = min(duration, end + margin)
    if end <= start:
        return 0.0, duration
    return start, end


def _timeline_zoom_step(current_zoom: float, delta: float) -> float:
    return max(TL_ZOOM_MIN, min(TL_ZOOM_MAX, float(current_zoom) + float(delta)))


def _timeline_pan_center(center_s: float, duration_s: float, zoom: float, direction: int, step_ratio: float = 0.35) -> float:
    duration = max(0.0, float(duration_s))
    if duration <= 0:
        return 0.0
    visible = duration / max(1.0, float(zoom))
    step = max(0.1, visible * max(0.05, float(step_ratio)))
    return max(0.0, min(duration, float(center_s) + step * int(direction)))


def _timeline_view_time_to_x(time_s: float, view_start_s: float, view_end_s: float, x1: int, x2: int) -> int:
    span = max(1, x2 - x1)
    duration = max(0.001, float(view_end_s) - float(view_start_s))
    pct = (float(time_s) - float(view_start_s)) / duration
    return int(x1 + pct * span)


def _timeline_x_to_view_time(x: int, view_start_s: float, view_end_s: float, x1: int, x2: int) -> float:
    span = max(1, x2 - x1)
    pct = max(0.0, min(1.0, (x - x1) / span))
    return float(view_start_s) + pct * max(0.0, float(view_end_s) - float(view_start_s))


def _time_to_frame(time_s: float, fps: float, total_frames: int) -> int:
    max_frame = max(0, total_frames - 1)
    return max(0, min(max_frame, int(time_s * max(1.0, fps))))


def _compact_clip_ranges(
    clips: list[TimelineClip],
) -> list[tuple[float, float, float, float]]:
    ranges: list[tuple[float, float, float, float]] = []
    cursor = 0.0
    for clip in clips:
        duration = max(0.0, clip.end_s - clip.start_s)
        if duration <= 0:
            continue
        display_start = cursor
        display_end = cursor + duration
        ranges.append((clip.start_s, clip.end_s, display_start, display_end))
        cursor = display_end
    return ranges


def _compact_display_to_source_time(
    display_time_s: float,
    ranges: list[tuple[float, float, float, float]],
) -> float:
    if not ranges:
        return 0.0
    display_time_s = max(0.0, min(ranges[-1][3], display_time_s))
    for source_start, source_end, display_start, display_end in ranges:
        if display_start <= display_time_s <= display_end:
            return min(source_end, source_start + (display_time_s - display_start))
    return ranges[-1][1]


def _compact_source_to_display_time(
    source_time_s: float,
    ranges: list[tuple[float, float, float, float]],
) -> float:
    if not ranges:
        return 0.0
    if source_time_s <= ranges[0][0]:
        return ranges[0][2]
    for source_start, source_end, display_start, display_end in ranges:
        if source_start <= source_time_s <= source_end:
            return min(display_end, display_start + (source_time_s - source_start))
        if source_time_s < source_start:
            return display_start
    return ranges[-1][3]


def _clip_insert_index(clips: list[TimelineClip], start_s: float) -> int:
    for idx, clip in enumerate(clips):
        if start_s < clip.start_s:
            return idx
        if clip.start_s <= start_s < clip.end_s:
            return idx + 1
    return len(clips)


def _insert_media_clip_replacing_range(
    clips: list[TimelineClip],
    source_path: str,
    start_s: float,
    duration_s: float,
    clip_duration_s: float = 3.0,
    min_duration_s: float = 0.15,
) -> tuple[list[TimelineClip], Optional[int]]:
    if duration_s <= 0:
        return [_clone_timeline_clip(clip) for clip in clips], None
    start = max(0.0, min(float(start_s), max(0.0, float(duration_s) - float(min_duration_s))))
    end = min(float(duration_s), start + max(float(min_duration_s), float(clip_duration_s)))
    if end <= start + 0.01:
        return [_clone_timeline_clip(clip) for clip in clips], None

    result: list[TimelineClip] = []
    for clip in clips:
        if clip.end_s <= start or clip.start_s >= end:
            result.append(_clone_timeline_clip(clip))
            continue
        if clip.start_s < start and start - clip.start_s >= min_duration_s:
            left = _clone_timeline_clip(clip)
            left.end_s = start
            result.append(left)
        if clip.end_s > end and clip.end_s - end >= min_duration_s:
            right = _clone_timeline_clip(clip)
            right.start_s = end
            result.append(right)

    inserted = TimelineClip(start, end, _clip_type_for_source_path(source_path), Path(source_path).stem, source_path=source_path)
    result.append(inserted)
    result.sort(key=lambda clip: (clip.start_s, clip.end_s))
    selected = next((idx for idx, clip in enumerate(result) if clip is inserted), None)
    return result, selected


def _clip_edges(clips: list[TimelineClip]) -> list[float]:
    edges: list[float] = []
    for clip in clips:
        edges.extend([clip.start_s, clip.end_s])
    return edges


def _active_clip_indices_at_time(clips: list[TimelineClip], time_s: float) -> list[int]:
    return [idx for idx, clip in enumerate(clips) if clip.start_s <= time_s < clip.end_s]


def _cycle_active_clip_index(clips: list[TimelineClip], time_s: float, current_index: Optional[int]) -> Optional[int]:
    active = _active_clip_indices_at_time(clips, time_s)
    return _cycle_index_in_order(active, current_index)


def _cycle_index_in_order(indices: list[int], current_index: Optional[int]) -> Optional[int]:
    if not indices:
        return None
    ordered = list(indices)
    if current_index in ordered and len(ordered) > 1:
        position = ordered.index(int(current_index))
        return ordered[position - 1] if position > 0 else ordered[-1]
    return ordered[-1]


def _waveform_indices_for_time_range(
    sample_count: int,
    duration_s: float,
    start_s: float,
    end_s: float,
) -> tuple[int, int]:
    if sample_count <= 0 or duration_s <= 0:
        return 0, 0
    start_ratio = max(0.0, min(1.0, float(start_s) / duration_s))
    end_ratio = max(0.0, min(1.0, float(end_s) / duration_s))
    if end_ratio <= start_ratio:
        return 0, 0
    start_idx = max(0, min(sample_count - 1, int(start_ratio * sample_count)))
    end_idx = max(start_idx + 1, min(sample_count, math.ceil(end_ratio * sample_count)))
    return start_idx, end_idx


def _trim_clip_bounds(
    clips: list[TimelineClip],
    index: int,
    edge: str,
    time_s: float,
    duration_s: float,
    min_duration_s: float,
    clamp_to_neighbors: bool = True,
) -> tuple[float, float]:
    clip = clips[index]
    prev_end = clips[index - 1].end_s if clamp_to_neighbors and index > 0 else 0.0
    next_start = clips[index + 1].start_s if clamp_to_neighbors and index + 1 < len(clips) else duration_s
    min_duration_s = max(0.01, min_duration_s)

    if edge == "start":
        new_start = max(prev_end, min(float(time_s), clip.end_s - min_duration_s))
        return new_start, clip.end_s
    if edge == "end":
        new_end = min(next_start, max(float(time_s), clip.start_s + min_duration_s))
        return clip.start_s, new_end
    raise ValueError(f"Borda de trim inválida: {edge}")


def _move_clip_bounds(start_s: float, end_s: float, target_start_s: float, duration_s: float) -> tuple[float, float]:
    duration = max(0.01, float(end_s) - float(start_s))
    timeline_duration = max(duration, float(duration_s))
    new_start = max(0.0, min(float(target_start_s), timeline_duration - duration))
    return new_start, new_start + duration


def _move_clip_bounds_with_snap(
    start_s: float,
    end_s: float,
    target_start_s: float,
    duration_s: float,
    edges: list[float],
    threshold_s: float,
) -> tuple[tuple[float, float], bool]:
    clip_duration = max(0.01, float(end_s) - float(start_s))
    target = float(target_start_s)
    candidates: list[float] = []
    for edge in edges:
        edge_s = float(edge)
        candidates.append(edge_s)
        candidates.append(edge_s - clip_duration)
    snapped_target, snapped = _snap_time_to_edges_with_flag(target, candidates, threshold_s)
    return _move_clip_bounds(start_s, end_s, snapped_target, duration_s), snapped


def _snap_insert_start_for_duration(
    start_s: float,
    clip_duration_s: float,
    timeline_duration_s: float,
    edges: list[float],
    threshold_s: float,
) -> tuple[float, bool]:
    duration = max(0.01, float(clip_duration_s))
    (new_start, _new_end), snapped = _move_clip_bounds_with_snap(
        0.0,
        duration,
        float(start_s),
        timeline_duration_s,
        edges,
        threshold_s,
    )
    return new_start, snapped


def _nudge_clip_bounds(start_s: float, end_s: float, delta_s: float, duration_s: float) -> tuple[float, float]:
    return _move_clip_bounds(start_s, end_s, float(start_s) + float(delta_s), duration_s)


def _duplicate_clip_bounds(start_s: float, end_s: float, duration_s: float) -> tuple[float, float]:
    clip_duration = max(0.01, float(end_s) - float(start_s))
    target_start = float(end_s)
    if target_start + clip_duration > float(duration_s):
        target_start = float(start_s) - clip_duration
    return _move_clip_bounds(start_s, end_s, target_start, duration_s)


def _paste_clip_at_time(clip: TimelineClip, start_s: float, duration_s: float, min_duration_s: float) -> Optional[TimelineClip]:
    clip_duration = max(float(min_duration_s), float(clip.end_s) - float(clip.start_s))
    if float(duration_s) < float(min_duration_s):
        return None
    start = max(0.0, min(float(start_s), max(0.0, float(duration_s) - clip_duration)))
    end = min(float(duration_s), start + clip_duration)
    if end <= start + float(min_duration_s) - 1e-6:
        return None
    pasted = _clone_timeline_clip(clip)
    pasted.start_s = start
    pasted.end_s = end
    if not str(pasted.label or "").endswith(" copia"):
        pasted.label = f"{pasted.label or 'Item'} copia"
    return pasted


def _split_clip_at_time(clip: TimelineClip, split_s: float, min_duration_s: float) -> Optional[tuple[TimelineClip, TimelineClip]]:
    min_duration = max(0.01, float(min_duration_s))
    split = float(split_s)
    if split <= float(clip.start_s) + min_duration or split >= float(clip.end_s) - min_duration:
        return None
    left = _clone_timeline_clip(clip)
    right = _clone_timeline_clip(clip)
    left.end_s = split
    right.start_s = split
    right.label = f"{clip.label or 'Item'} 2"
    return left, right


def _timeline_handle_edge_at(x: int, x1: int, x2: int, handle_px: int) -> Optional[str]:
    if abs(x - x1) <= handle_px:
        return "start"
    if abs(x - x2) <= handle_px:
        return "end"
    if x1 <= x <= x1 + handle_px * 2:
        return "start"
    if x2 - handle_px * 2 <= x <= x2:
        return "end"
    return None


def _timeline_lane_layout(canvas_height: int, n_overlay_tracks: int = 1) -> dict[str, tuple[int, int]]:
    top = 8
    h = max(128, int(canvas_height))
    n = max(1, int(n_overlay_tracks))
    text_y1, text_y2 = top + 12, top + 38
    # Stack overlay lanes: each 26px tall with 4px gap
    overlay_lane_h = 26
    overlay_gap = 4
    result: dict[str, tuple[int, int]] = {"text": (text_y1, text_y2)}
    cursor = text_y2 + 12  # gap between text and first overlay
    for i in range(n):
        oy1 = cursor
        oy2 = cursor + overlay_lane_h
        result[f"overlay_{i}"] = (oy1, oy2)
        cursor = oy2 + overlay_gap
    # video below all overlay lanes
    video_y1 = cursor + (overlay_gap * 2)
    video_y2 = video_y1 + 32
    audio_y1 = video_y2 + 14
    audio_y2 = max(audio_y1 + 24, h - 18)
    result["video"] = (video_y1, video_y2)
    result["audio"] = (audio_y1, audio_y2)
    # Backward compat: "overlay" maps to "overlay_0"
    result["overlay"] = result["overlay_0"]
    return result


def _timeline_y_in_lane(y: int, y1: int, y2: int, margin_px: int = 0) -> bool:
    return y1 - margin_px <= int(y) <= y2 + margin_px


def _timeline_handle_y_in_range(
    y: int,
    video_y1: int,
    video_y2: int,
    audio_y1: int,
    audio_y2: int,
    margin_px: int = 6,
) -> bool:
    return video_y1 - margin_px <= y <= audio_y2 + margin_px and not video_y2 + margin_px < y < audio_y1 - margin_px


def _active_timeline_handle_edge(
    index: int,
    selected_index: Optional[int],
    trim_drag: Optional[tuple[int, str]],
    hover_handle: Optional[tuple[int, str]],
) -> Optional[str]:
    if trim_drag and trim_drag[0] == index:
        return trim_drag[1]
    if hover_handle and hover_handle[0] == index:
        return hover_handle[1]
    if selected_index == index:
        return "both"
    return None


def _trim_edge_label(edge: str) -> str:
    return "borda inicial" if edge == "start" else "borda final"


def _draw_timeline_handle_zone(canvas: tk.Canvas, x: int, y1: int, y2: int, edge: str) -> None:
    color = "#ffd34d" if edge == "start" else "#ffb347"
    canvas.create_rectangle(x - 8, y1, x + 8, y2, fill="#3a3218", outline=color, width=1, stipple="gray25")
    canvas.create_rectangle(x - 3, y1, x + 3, y2, fill=TL_HEAD, outline="")
    canvas.create_line(x, y1, x, y2, fill="#fff2a8", width=1)


def _draw_timeline_drop_marker(canvas: tk.Canvas, x: int, y1: int, y2: int, label: str) -> None:
    canvas.create_line(x, y1 - 4, x, y2 + 4, fill="#6fffd2", width=2)
    canvas.create_polygon(x, y1 - 7, x - 6, y1 - 1, x + 6, y1 - 1, fill="#6fffd2", outline="")
    text = str(label or "Midia")[:42]
    pad = 5
    font = ("Segoe UI", 8, "bold")
    text_id = canvas.create_text(x + 8, y1 - 9, text=text, fill="#071512", font=font, anchor="w")
    bbox = canvas.bbox(text_id)
    if bbox:
        left, top, right, bottom = bbox
        canvas.create_rectangle(left - pad, top - 2, right + pad, bottom + 2, fill="#6fffd2", outline="")
        canvas.tag_raise(text_id)


def _trim_bounds_changed(
    old_start: float,
    old_end: float,
    new_start: float,
    new_end: float,
    epsilon: float = 1e-6,
) -> bool:
    return abs(float(old_start) - float(new_start)) > epsilon or abs(float(old_end) - float(new_end)) > epsilon


def _snap_time_to_edges(time_s: float, edges: list[float], threshold_s: float) -> float:
    snapped_time, _snapped = _snap_time_to_edges_with_flag(time_s, edges, threshold_s)
    return snapped_time


def _snap_time_to_edges_with_flag(time_s: float, edges: list[float], threshold_s: float) -> tuple[float, bool]:
    if not edges:
        return time_s, False
    nearest = min(edges, key=lambda edge: abs(edge - time_s))
    if abs(nearest - time_s) <= threshold_s:
        return nearest, nearest != time_s
    return time_s, False


def _coerce_time_to_segments(
    time_s: float,
    segments: list[tuple[float, float]],
    duration_s: float,
) -> float:
    duration_s = max(0.0, duration_s)
    time_s = max(0.0, min(duration_s, time_s))
    valid = [(start, end) for start, end in sorted(segments) if end > start]
    if not valid:
        return duration_s
    for start, end in valid:
        if start <= time_s <= end:
            return time_s
        if time_s < start:
            return start
    return valid[-1][1]


def _coerce_frame_to_segments(
    frame: int,
    fps: float,
    total_frames: int,
    segments: list[tuple[float, float]],
    duration_s: float,
) -> int:
    time_s = frame / max(1.0, fps)
    kept_time = _coerce_time_to_segments(time_s, segments, duration_s)
    return _time_to_frame(kept_time, fps, total_frames)


def _playback_delay_ms(fps: float, render_ms: float) -> int:
    frame_budget_ms = 1000.0 / max(1.0, float(fps))
    return max(1, int(frame_budget_ms - max(0.0, float(render_ms))))


def _relative_seek_frame(
    current_frame: int,
    direction: int,
    fps: float,
    total_frames: int,
    large_step: bool = False,
) -> int:
    limit = max(0, int(total_frames) - 1)
    if limit <= 0:
        return 0
    step = max(1, int(round(float(fps)))) if large_step else 1
    return max(0, min(limit, int(current_frame) + step * int(direction)))


def _waveform_bar_color(amp: float) -> str:
    """Return a color for a waveform bar based on its normalized amplitude."""
    if amp < 0.3:
        return "#4a9eff"
    if amp < 0.6:
        return "#5dd47a"
    if amp < 0.85:
        return "#f5c842"
    return "#e05050"


def _preview_render_frame_index(current_frame: int, preview_frame_index: int, is_playback: bool) -> int:
    return int(preview_frame_index) if is_playback else int(current_frame)


def _playback_effective_fps(start_frame: int, current_frame: int, elapsed_s: float) -> float:
    if elapsed_s <= 0:
        return 0.0
    return max(0, current_frame - start_frame) / elapsed_s


def _playback_crosses_removed_range(
    previous_frame: int,
    current_frame: int,
    fps: float,
    segments: list[tuple[float, float]],
    duration_s: float,
    epsilon_s: float = 1e-4,
) -> bool:
    if current_frame <= previous_frame:
        return False
    previous_time = previous_frame / max(1.0, fps)
    current_time = current_frame / max(1.0, fps)
    for start_s, end_s in _removed_ranges_from_segments(duration_s, segments):
        if end_s <= start_s:
            continue
        if previous_time <= start_s + epsilon_s and current_time >= end_s - epsilon_s:
            return True
        if start_s < previous_time < end_s and current_time >= end_s - epsilon_s:
            return True
    return False


def _playback_target_frame(
    start_frame: int,
    elapsed_s: float,
    fps: float,
    total_frames: int,
) -> int:
    elapsed_frames = int(max(0.0, elapsed_s) * max(1.0, float(fps)))
    return min(max(0, total_frames - 1), max(0, start_frame) + elapsed_frames + 1)


# ── Etapa 4 — Video Scopes & Color Wheel rendering ────────────────────────────

_SCOPE_BG = (10, 9, 18)


def _scope_bg_img(width: int, height: int) -> np.ndarray:
    img = np.empty((height, width, 3), dtype=np.uint8)
    img[:] = _SCOPE_BG
    return img


def _render_histogram(img: Image.Image, width: int = 300, height: int = 120) -> Image.Image:
    """RGB histogram: red/green/blue curves over a dark canvas."""
    small = img.resize(
        (min(img.width, 400), min(img.height, 225)),
        Image.BILINEAR,
    ).convert("RGB")
    arr = np.array(small, dtype=np.float32)

    scope = _scope_bg_img(width, height)

    for ch, color in enumerate(
        [(160, 60, 60), (60, 150, 60), (60, 80, 180)]
    ):
        hist, _ = np.histogram(arr[..., ch].ravel(), bins=256, range=(0.0, 255.0))
        peak = hist.max()
        if peak == 0:
            continue
        hist_norm = hist / float(peak)
        for i in range(256):
            x = int(round(i * (width - 1) / 255.0))
            bar_h = int(hist_norm[i] * (height - 4))
            if bar_h <= 0:
                continue
            y0 = max(0, height - bar_h)
            x0 = max(0, x - 1)
            x1 = min(width, x + 2)
            scope[y0:height, x0:x1] = np.maximum(
                scope[y0:height, x0:x1], color
            )

    # Draw zero and peak markers
    scope[height - 1, :] = (40, 40, 55)
    scope[0, :] = (40, 40, 55)
    scope[:, 0] = (40, 40, 55)
    scope[:, width - 1] = (40, 40, 55)

    return Image.fromarray(scope)


def _render_waveform(img: Image.Image, width: int = 300, height: int = 120) -> Image.Image:
    """Luma waveform: brightness of each column plotted on Y axis."""
    small = img.resize((width, max(1, img.height // 4 + 1)), Image.BILINEAR).convert("RGB")
    arr = np.array(small, dtype=np.float32) / 255.0
    luma = arr[..., 0] * 0.2126 + arr[..., 1] * 0.7152 + arr[..., 2] * 0.0722

    scope = _scope_bg_img(width, height)

    for x in range(min(width, luma.shape[1])):
        col = luma[:, x]
        for val in col:
            y = int((1.0 - float(val)) * (height - 2))
            y = max(0, min(height - 1, y))
            cur = scope[y, x].astype(np.int32)
            scope[y, x] = np.clip(cur + [30, 120, 30], 0, 220).astype(np.uint8)

    # IRE grid at 10%, 50%, 90%
    for ire in (0.10, 0.50, 0.90):
        gy = int((1.0 - ire) * (height - 2))
        scope[gy, ::4] = (55, 55, 70)

    scope[height - 1, :] = (40, 40, 55)
    scope[0, :] = (40, 40, 55)
    return Image.fromarray(scope)


def _render_vectorscope(img: Image.Image, size: int = 120) -> Image.Image:
    """YCbCr vectorscope: Cb on X, Cr on Y."""
    small = img.resize((min(img.width, 120), min(img.height, 80)), Image.BILINEAR).convert("RGB")
    arr = np.array(small, dtype=np.uint8)
    ycbcr = cv2.cvtColor(arr, cv2.COLOR_RGB2YCrCb)  # [Y, Cr, Cb]

    cb = ycbcr[..., 2].ravel().astype(np.float32) / 255.0  # 0..1 centered at ~0.5
    cr = ycbcr[..., 1].ravel().astype(np.float32) / 255.0

    scope = _scope_bg_img(size, size)
    cx = cy = size // 2
    r = size // 2 - 4

    # Reference circle
    for angle_deg in range(0, 360, 2):
        ax = cx + int(r * math.cos(math.radians(angle_deg)))
        ay = cy + int(r * math.sin(math.radians(angle_deg)))
        if 0 <= ax < size and 0 <= ay < size:
            scope[ay, ax] = (45, 40, 60)

    # Hue target boxes (approximate positions)
    for hue_label, hue_deg in [("R", 0), ("Y", 60), ("G", 120), ("C", 180), ("B", 240), ("M", 300)]:
        sa = 0.75  # ~75% saturation
        bx = cx + int(sa * r * math.cos(math.radians(hue_deg)))
        by = cy + int(sa * r * math.sin(math.radians(hue_deg)))
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                nx, ny = bx + dx, by + dy
                if 0 <= nx < size and 0 <= ny < size:
                    scope[ny, nx] = (80, 70, 100)

    # Plot pixels
    for u, v in zip(cb, cr):
        # Center Cb/Cr around 0.5 → map to [-1..1]
        ux = (u - 0.5) * 2.0
        vy = (v - 0.5) * 2.0
        px = int(cx + ux * r)
        py = int(cy + vy * r)
        if 0 <= px < size and 0 <= py < size:
            cur = scope[py, px].astype(np.int32)
            scope[py, px] = np.clip(cur + [20, 180, 30], 0, 255).astype(np.uint8)

    return Image.fromarray(scope)


# ── Etapa 6 — Speed helpers ───────────────────────────────────────────────────

_SPEED_STEPS = [0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 4.0]


def _speed_float_to_str(v: float) -> str:
    """Return the closest speed label string for display in the inspector."""
    closest = min(_SPEED_STEPS, key=lambda x: abs(x - float(v)))
    return f"{closest:g}×"


def _speed_str_to_float(s: str) -> float:
    """Parse a speed label like '0.5×' → 0.5."""
    try:
        return float(str(s).rstrip("×").strip())
    except (ValueError, AttributeError):
        return 1.0


# ── Etapa 6 — Effects Presets ─────────────────────────────────────────────────

# Each preset: dict of ColorGrade field → value (overrides only listed fields).
# "Normal" resets to PRESET_CAPCUT defaults.
_FX_PRESETS: dict[str, dict[str, float]] = {
    "Normal":      {},   # handled specially — restores PRESET_CAPCUT
    "P&B":         {"saturation": 0.0, "contrast": 1.08, "brightness": 2.0},
    "Frio":        {"temperature": -25.0, "saturation": 1.08, "highlights": 0.05},
    "Quente":      {"temperature": 25.0, "saturation": 1.05, "contrast": 1.03},
    "Teal & Lrj":  {
        "lift_b": 10.0, "lift_g": 5.0,
        "gain_r": 12.0, "gain_g": -5.0,
        "saturation": 1.15, "contrast": 1.05,
    },
    "Vintage":     {
        "temperature": 15.0, "saturation": 0.82,
        "contrast": 1.06, "blacks": 0.04,
        "lift_r": 8.0, "gain_b": -8.0,
    },
    "Glow":        {
        "highlights": 0.18, "brightness": 6.0,
        "saturation": 1.12, "sharpen": 30.0,
    },
    "Matte":       {
        "blacks": 0.08, "shadows": 0.06,
        "contrast": 1.04, "saturation": 0.88,
    },
}


def _make_color_wheel_image(size: int = 80) -> Image.Image:
    """Create a circular HSV colour wheel as PIL Image (background = ED_BG dark)."""
    cy = cx = size / 2.0
    y_idx, x_idx = np.mgrid[0:size, 0:size].astype(np.float32)
    dx = (x_idx - cx) / (cx - 1.0)
    dy = (y_idx - cy) / (cy - 1.0)
    r = np.sqrt(dx ** 2 + dy ** 2)

    # Hue: 0..179 for OpenCV, angle from +X axis
    h = (np.degrees(np.arctan2(-dy, dx)) % 360.0 / 2.0).astype(np.uint8)
    s = np.clip(r * 255.0, 0, 255).astype(np.uint8)
    v = np.full_like(s, 210)  # slightly dimmed for dark UI

    hsv = np.stack([h, s, v], axis=-1)
    rgb = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)

    # Mask outside-circle pixels to background colour
    mask = r > 1.0
    rgb[mask] = [10, 9, 18]

    return Image.fromarray(rgb)
