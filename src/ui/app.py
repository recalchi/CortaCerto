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
from typing import Optional

import customtkinter as ctk
import cv2
import numpy as np
from tkinter import filedialog, messagebox
from PIL import Image, ImageDraw, ImageTk

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
except Exception:
    DND_FILES = "DND_Files"
    TkinterDnD = None

from ..config import ProcessingConfig, Platform, SilenceStyle, PRESETS
from ..core.audio_waveform import extract_waveform
from ..core.ai_assistant import AiSuggestionRequest, suggest_metadata
from ..core.color_grade import ColorGrade, PRESET_CAPCUT
from ..core.preview_engine import PreviewEngine, PreviewFrame, PreviewSettings
from ..core.timeline_manifest import build_timeline_manifest
from ..core.timeline_model import TimelineClip, TimelineModel, build_timeline_model
from ..pipeline import run_pipeline, PipelineResult
from ..ffmpeg_env import encoder_label, ffplay

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
TL_LABEL_W  = 74
TL_PAD_R    = 8
PROJECT_EXT = ".ccp"
PROJECT_LEGACY_EXT = ".cortacerto.json"
PROJECT_TRASH_DAYS = 30
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".m4v"}


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
        self._selected_clip_index: Optional[int] = None
        self._timeline_dirty = False
        self._timeline_undo_stack: list[tuple[list[TimelineClip], Optional[int], bool]] = []
        self._trim_drag: Optional[tuple[int, str]] = None
        self._hover_trim_handle: Optional[tuple[int, str]] = None
        self._trim_undo_captured = False
        self._trim_min_duration_s = 0.15
        self._waveform_zoom = 1.0
        self._tl_compact_var = tk.BooleanVar(value=True)
        self._clip_label_var = tk.StringVar(value="")
        self._clip_scale_var = tk.DoubleVar(value=100.0)
        self._clip_pos_x_var = tk.DoubleVar(value=0.0)
        self._clip_pos_y_var = tk.DoubleVar(value=0.0)
        self._clip_text_x_var = tk.DoubleVar(value=0.0)
        self._clip_text_y_var = tk.DoubleVar(value=72.0)
        self._clip_text_size_var = tk.DoubleVar(value=100.0)
        self._clip_volume_var = tk.DoubleVar(value=100.0)
        self._clip_transition_var = tk.StringVar(value="Corte")
        self._clip_chroma_var = tk.BooleanVar(value=False)
        self._clip_chroma_color_var = tk.StringVar(value="#00ff00")
        self._clip_chroma_tolerance_var = tk.DoubleVar(value=45.0)
        self._project_status_var = tk.StringVar(value="")
        self._chroma_picker_active = False
        self._preview_display_image: Optional[Image.Image] = None
        self._preview_display_box: tuple[int, int, int, int] = (0, 0, 0, 0)
        self._preview_drag: Optional[tuple[str, int, int, float, float, float]] = None
        self._preview_drag_moved = False
        self._preview_click_consumed = False
        self._media_listbox: Optional[tk.Listbox] = None
        self._clip_inspector_enabled = False
        self._clip_source_caps: dict[str, cv2.VideoCapture] = {}
        self._clip_source_meta: dict[str, tuple[float, int]] = {}
        self._export_modal = None
        self._export_stage_var = None
        self._export_msg_var = None
        self._export_stage_progress = None
        self._export_overall_progress = None

        self._show_project_launcher()
        self._poll_queue()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def run(self) -> None:
        self.root.mainloop()

    # -- Project launcher -----------------------------------------------------

    def _clear_root(self) -> None:
        for child in self.root.winfo_children():
            child.destroy()

    def _show_project_launcher(self) -> None:
        self._clear_root()
        self.root.title("CortaCerto - Projetos")
        self.root.geometry("980x620")
        self.root.minsize(860, 560)
        self.root.grid_rowconfigure(0, weight=1)
        self.root.grid_rowconfigure(1, weight=0)
        self.root.grid_columnconfigure(0, weight=1)

        shell = tk.Frame(self.root, bg=C_BG)
        shell.grid(row=0, column=0, sticky="nsew")
        shell.grid_rowconfigure(1, weight=1)
        shell.grid_columnconfigure(0, weight=1)

        tk.Label(
            shell,
            text="CortaCerto",
            bg=C_BG,
            fg=C_ACCENT2,
            font=("Segoe UI", 28, "bold"),
        ).grid(row=0, column=0, pady=(54, 6))
        tk.Label(
            shell,
            text="Projetos de edição",
            bg=C_BG,
            fg=C_MUTED,
            font=("Segoe UI", 13),
        ).grid(row=1, column=0, sticky="n", pady=(0, 26))

        panel = tk.Frame(shell, bg=C_PANEL, highlightthickness=1, highlightbackground=C_BORDER)
        panel.grid(row=1, column=0, sticky="n", pady=(70, 0), ipadx=28, ipady=24)
        panel.grid_columnconfigure(0, weight=1)

        self._launcher_media_var = tk.StringVar(value="Nenhuma mídia importada")
        media_row = tk.Frame(panel, bg=C_PANEL)
        media_row.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        media_row.grid_columnconfigure(0, weight=1)
        tk.Entry(
            media_row,
            textvariable=self._launcher_media_var,
            bg=C_SURFACE,
            fg=C_TEXT,
            insertbackground=C_TEXT,
            relief="flat",
            font=("Segoe UI", 10),
            width=34,
        ).grid(row=0, column=0, sticky="ew", ipady=6, padx=(0, 6))
        tk.Button(
            media_row,
            text="Importar mídia",
            command=self._import_launcher_media,
            bg=C_SURFACE,
            fg=C_TEXT,
            activebackground=C_BORDER,
            activeforeground=C_TEXT,
            relief="flat",
            padx=10,
            pady=6,
            font=("Segoe UI", 10),
            cursor="hand2",
            bd=0,
        ).grid(row=0, column=1)

        actions = [
            ("Novo projeto", self._create_project),
            ("Abrir projeto", self._open_project),
            ("Abrir vídeo rápido", self._quick_open_video),
            ("Restaurar projeto", self._restore_project_from_trash_dialog),
            ("Lixeira", self._open_project_trash),
        ]
        for row, (label, command) in enumerate(actions):
            tk.Button(
                panel,
                text=label,
                command=command,
                bg=C_ACCENT if row == 0 else C_SURFACE,
                fg="#ffffff" if row == 0 else C_TEXT,
                activebackground=C_ACCENT2 if row == 0 else C_BORDER,
                activeforeground="#ffffff",
                relief="flat",
                padx=24,
                pady=10,
                width=28,
                font=("Segoe UI", 11),
                cursor="hand2",
                bd=0,
            ).grid(row=row + 1, column=0, sticky="ew", pady=6)

        tk.Label(
            panel,
            text="Crie um projeto para manter nome, arquivo .ccp e fluxo de edição separados.",
            bg=C_PANEL,
            fg=C_MUTED,
            wraplength=360,
            justify="center",
            font=("Segoe UI", 9),
        ).grid(row=5, column=0, pady=(14, 0))

        removed = _cleanup_project_trash(_project_trash_dir())
        if removed:
            print(f"[PROJECT] Lixeira limpa: {removed} item(ns) com mais de {PROJECT_TRASH_DAYS} dias.")

    def _import_launcher_media(self) -> None:
        path = filedialog.askopenfilename(
            title="Importar mídia",
            filetypes=[("Vídeos", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v"),
                       ("Todos", "*.*")]
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
            metadata["video_path"] = self._launcher_media_path
            metadata["media_paths"] = [self._launcher_media_path]
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
            self._load_video(self._launcher_media_path)
        else:
            self._pick_video()

    def _open_project_editor(self, project_path: Optional[str]) -> None:
        self._reset_loaded_project_runtime()
        self.project_path = project_path
        metadata = _read_project_metadata(project_path) if project_path else {}
        self.project_name = str(metadata.get("name") or _project_name_from_path(project_path))
        self._pending_project_state = metadata
        self._project_media_paths = _project_media_paths_from_metadata(metadata)
        self._clear_root()
        self.root.geometry("1280x780")
        self.root.minsize(1000, 660)
        self._build_ui()
        self.root.title(f"CortaCerto - {self.project_name}")
        self._tb_status.configure(text=f"Projeto aberto: {self.project_name}")
        video_path = str(metadata.get("video_path") or "")
        if not video_path:
            video_path = _first_existing_media_path(self._project_media_paths) or ""
        if video_path and Path(video_path).exists():
            self.root.after(100, lambda path=video_path: self._load_video(path))
        elif video_path:
            self._tb_status.configure(text="Projeto aberto, mas o vídeo salvo não foi encontrado.")
            self._pending_project_state = {}

    def _reset_loaded_project_runtime(self) -> None:
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
        self._selected_clip_index = None
        self._timeline_dirty = False
        self._timeline_undo_stack.clear()
        self._trim_drag = None
        self._hover_trim_handle = None
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
        self.root.grid_rowconfigure(1, weight=1)
        self.root.grid_columnconfigure(0, weight=1)
        self._build_toolbar()
        self._build_body()
        self._bind_shortcuts()
        self._setup_drop_targets_reliable()

    def _bind_shortcuts(self) -> None:
        self.root.bind_all("<space>", self._shortcut_toggle_play)
        self.root.bind_all("<KeyPress-b>", self._shortcut_split)
        self.root.bind_all("<KeyPress-B>", self._shortcut_split)
        self.root.bind_all("<Delete>", self._shortcut_delete)
        self.root.bind_all("<BackSpace>", self._shortcut_delete)
        self.root.bind_all("<Control-z>", self._shortcut_undo)
        self.root.bind_all("<Control-Z>", self._shortcut_undo)

    def _shortcut_allowed(self, event: tk.Event) -> bool:
        widget = getattr(event, "widget", None)
        cls = widget.winfo_class() if widget is not None else ""
        return "Entry" not in cls and "Text" not in cls

    def _shortcut_toggle_play(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._toggle_play()
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

    def _shortcut_undo(self, event: tk.Event) -> str | None:
        if not self._shortcut_allowed(event):
            return None
        self._undo_timeline_action()
        return "break"

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
        if enabled:
            self._tb_status.configure(text="Arraste vídeos para o preview ou direto para a timeline.")
        else:
            self._tb_status.configure(text="Use Abrir vídeo para importar mídia.")

    def _on_drop_files(self, event: tk.Event) -> str:
        paths = _video_paths_from_drop(getattr(event, "data", ""))
        if paths:
            self._register_project_media(paths)
            self._load_video(paths[0])
            if len(paths) > 1:
                self._tb_status.configure(text=f"{len(paths)} vídeos adicionados ao projeto. Primeiro vídeo carregado.")
        else:
            self._tb_status.configure(text="Solte um arquivo de vídeo compatível.")
        return "break"

    def _on_timeline_drop_files(self, event: tk.Event) -> str:
        paths = _video_paths_from_drop(getattr(event, "data", ""))
        if not paths:
            self._tb_status.configure(text="Solte um arquivo de vídeo compatível na timeline.")
            return "break"
        self._register_project_media(paths)
        if not self.video_path or not self._timeline_model:
            self._load_video(paths[0])
            self._tb_status.configure(text="Vídeo principal carregado pela timeline.")
            return "break"
        time_s = self._timeline_time_from_event(event)
        inserted = 0
        for offset, path in enumerate(paths):
            if self._insert_media_path_at_time(path, min(self._duration_s, time_s + offset * 3.0), save=False):
                inserted += 1
        self._save_project_media_paths()
        self._tb_status.configure(text=f"{inserted} mídia(s) inserida(s) na timeline.")
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
            hdr, from_=1.0, to=6.0, number_of_steps=50, width=140,
            fg_color=C_SURFACE, progress_color=C_ACCENT, button_color=C_ACCENT2,
            command=self._on_timeline_zoom,
        )
        self._tl_zoom.set(1.0)
        self._tl_zoom.pack(side="right", padx=(8, 0))
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
        tk.Button(hdr, text="Desfazer", command=self._undo_timeline_action,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Dividir", command=self._split_selected_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))
        tk.Button(hdr, text="Excluir", command=self._delete_selected_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=8,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).pack(side="right", padx=(8, 0))

        self._tl_canvas = tk.Canvas(tl_outer, bg=TL_BG, height=120,
                                     highlightthickness=0, cursor="hand2")
        self._tl_canvas.grid(row=1, column=0, sticky="nsew", padx=4, pady=(2,4))
        self._tl_canvas.bind("<Configure>", lambda e: self._redraw_timeline())
        self._tl_canvas.bind("<ButtonPress-1>", self._tl_press)
        self._tl_canvas.bind("<B1-Motion>", self._tl_drag_motion)
        self._tl_canvas.bind("<ButtonRelease-1>", self._tl_release)
        self._tl_canvas.bind("<Motion>", self._tl_motion)
        self._tl_canvas.bind("<Leave>", self._tl_leave)

        self._tl_playhead = None
        self._redraw_timeline()

    def _tl_press(self, event: tk.Event) -> str | None:
        if self._duration_s <= 0:
            return None
        handle = self._trim_handle_at(event.x, event.y)
        if handle is not None:
            self._stop_playback(reset_button=True)
            self._selected_clip_index, edge = handle
            self._trim_drag = (self._selected_clip_index, edge)
            self._hover_trim_handle = handle
            self._trim_undo_captured = False
            self._redraw_timeline()
            self._tb_status.configure(text=f"Ajustando {_trim_edge_label(edge)}...")
            return "break"
        self._tl_click(event)
        return None

    def _tl_click(self, event: tk.Event) -> None:
        w   = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        time_s = self._timeline_click_time(event.x, track_x1, track_x2)
        time_s, snapped = self._snap_time_to_clip_edge(time_s)
        frame = self._time_to_frame(time_s)
        self._select_clip_at_time(time_s)
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
        if not self._trim_drag or not self._timeline_model:
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
            self._push_timeline_undo()
            self._trim_undo_captured = True
        clip.start_s = new_start
        clip.end_s = new_end
        self._selected_clip_index = index
        self._sync_manual_timeline(mark_dirty=True)
        self._timeline_dirty = True
        self._seek_to(self._time_to_frame(new_start if edge == "start" else new_end))
        snap_note = " | snap" if snapped else ""
        self._tb_status.configure(text=f"Corte ajustado: {_fmt(new_start)} - {_fmt(new_end)}.{snap_note}")
        return "break"

    def _tl_release(self, event: tk.Event) -> str | None:
        if not self._trim_drag:
            return None
        changed = self._trim_undo_captured
        self._trim_drag = None
        self._hover_trim_handle = None
        self._trim_undo_captured = False
        self._tl_canvas.configure(cursor="hand2")
        self._redraw_timeline()
        self._tb_status.configure(text="Corte ajustado." if changed else "Corte mantido.")
        return "break"

    def _tl_motion(self, event: tk.Event) -> None:
        if self._trim_drag:
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

    def _tl_leave(self, event: tk.Event) -> None:
        self._tl_canvas.configure(cursor="hand2")
        if self._hover_trim_handle and not self._trim_drag:
            self._hover_trim_handle = None
            self._redraw_timeline()

    # -- Properties panel ------------------------------------------------------

    def _on_timeline_zoom(self, value: float) -> None:
        self._waveform_zoom = float(value)
        self._redraw_timeline()
        self._refresh_project_status()

    def _adjust_timeline_zoom(self, delta: float) -> None:
        value = max(1.0, min(6.0, float(self._tl_zoom.get()) + float(delta)))
        self._tl_zoom.set(value)
        self._on_timeline_zoom(value)

    def _timeline_zoomed_bounds(self, x1: int, x2: int) -> tuple[int, int]:
        return x1, x2

    def _timeline_view_window(self, view_duration: float) -> tuple[float, float]:
        playhead_s = self._current_frame / max(1.0, self._fps)
        if self._timeline_compact_enabled():
            compact_ranges = self._compact_ranges_for_view()
            if compact_ranges:
                playhead_s = _compact_source_to_display_time(playhead_s, compact_ranges)
        return _timeline_zoom_window(view_duration, self._waveform_zoom, playhead_s)

    def _select_clip_at_time(self, time_s: float) -> None:
        self._selected_clip_index = self._clip_index_at_time(time_s)
        self._redraw_timeline()
        self._refresh_clip_inspector()

    def _split_selected_clip(self) -> None:
        if not self._timeline_model:
            self._tb_status.configure(text="Carregue um vídeo antes de dividir.")
            return
        self._stop_playback(reset_button=True)
        split_s = self._current_frame / max(1.0, self._fps)
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
        self._push_timeline_undo()
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
        if not self._timeline_model or self._selected_clip_index is None:
            self._tb_status.configure(text="Selecione um clipe na timeline para excluir.")
            return
        self._stop_playback(reset_button=True)
        current_time = self._current_frame / max(1.0, self._fps)
        self._push_timeline_undo()
        del self._timeline_model.video_track.clips[self._selected_clip_index]
        self._selected_clip_index = None
        self._sync_manual_timeline()
        self._timeline_dirty = True
        self._seek_to(self._time_to_frame(self._nearest_kept_time(current_time)))
        self._tb_status.configure(text="Clipe removido da timeline.")
        self._refresh_clip_inspector()

    def _push_timeline_undo(self) -> None:
        if not self._timeline_model:
            return
        clips = [
            _clone_timeline_clip(c)
            for c in self._timeline_model.video_track.clips
        ]
        self._timeline_undo_stack.append((clips, self._selected_clip_index, self._timeline_dirty))
        if len(self._timeline_undo_stack) > 50:
            self._timeline_undo_stack.pop(0)

    def _undo_timeline_action(self) -> None:
        if not self._timeline_model or not self._timeline_undo_stack:
            self._tb_status.configure(text="Nada para desfazer.")
            return
        self._stop_playback(reset_button=True)
        current_time = self._current_frame / max(1.0, self._fps)
        clips, selected_index, was_dirty = self._timeline_undo_stack.pop()
        self._timeline_model.video_track.clips = [
            _clone_timeline_clip(c) for c in clips
        ]
        self._selected_clip_index = selected_index
        self._timeline_dirty = was_dirty
        self._sync_manual_timeline(mark_dirty=was_dirty)
        self._seek_to(self._time_to_frame(self._nearest_kept_time(current_time)))
        self._tb_status.configure(text="Ação desfeita.")
        self._refresh_clip_inspector()

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

        label_w = TL_LABEL_W
        top = 8
        video_y1, video_y2 = top + 12, top + 40
        audio_y1, audio_y2 = top + 56, h - 18

        c.create_rectangle(0, 0, label_w, h, fill="#101015", outline="")
        c.create_text(label_w // 2, (video_y1 + video_y2) // 2, text="VÍDEO", fill=C_MUTED, font=("Segoe UI", 8, "bold"))
        c.create_text(label_w // 2, (audio_y1 + audio_y2) // 2, text="ÁUDIO", fill=C_MUTED, font=("Segoe UI", 8, "bold"))

        track_x1, track_x2 = self._timeline_track_bounds(w)
        draw_x1, draw_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        c.create_rectangle(track_x1, video_y1, track_x2, video_y2, fill="#1b2130", outline="")
        c.create_rectangle(track_x1, audio_y1, track_x2, audio_y2, fill="#171b24", outline="")
        if self._waveform_zoom > 1.001:
            c.create_text(track_x1 + 8, top + 3, text=f"{self._waveform_zoom:.2f}x", fill=C_MUTED, font=("Segoe UI", 8), anchor="w")

        clips = self._timeline_model.video_track.clips
        compact = self._timeline_compact_enabled()
        compact_ranges = _compact_clip_ranges(clips) if compact else []
        view_duration = (
            compact_ranges[-1][3]
            if compact and compact_ranges
            else self._duration_s
        )
        view_start, view_end = self._timeline_view_window(view_duration)

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
            c.create_rectangle(x1, video_y1 + 2, x2, video_y2 - 2, fill=TL_SPEECH, outline=outline, width=width)
            audio_fill = "#203449" if idx == self._selected_clip_index else "#1b2a3a"
            audio_outline = C_YELLOW if idx == self._selected_clip_index else "#26384a"
            c.create_rectangle(x1, audio_y1 + 2, x2, audio_y2 - 2, fill=audio_fill, outline=audio_outline, width=width)
            active_edge = _active_timeline_handle_edge(idx, self._selected_clip_index, self._trim_drag, self._hover_trim_handle)
            if active_edge is not None:
                if active_edge in ("both", "start"):
                    _draw_timeline_handle_zone(c, x1, video_y1, audio_y2, "start")
                if active_edge in ("both", "end"):
                    _draw_timeline_handle_zone(c, x2, video_y1, audio_y2, "end")
            if x2 - x1 > 56:
                c.create_text((x1 + x2) // 2, (video_y1 + video_y2) // 2, text=clip.label, fill="#d6e6ff", font=("Segoe UI", 8))
            if compact and idx > 0:
                c.create_line(x1, video_y1 + 1, x1, audio_y2 - 1, fill=TL_HEAD)

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

        if not compact:
            for start_s, end_s in self._timeline_model.removed_ranges:
                x1 = _timeline_view_time_to_x(start_s, view_start, view_end, draw_x1, draw_x2)
                x2 = _timeline_view_time_to_x(end_s, view_start, view_end, draw_x1, draw_x2)
                c.create_rectangle(x1, video_y1 + 6, x2, video_y2 - 6, fill=TL_SILENCE, outline="", stipple="gray50")
                c.create_rectangle(x1, audio_y1 + 4, x2, audio_y2 - 4, fill="#11151d", outline="", stipple="gray50")

        tick_step = max(1, int(self._duration_s / 12))
        tick_duration = view_duration if compact else self._duration_s
        tick_start = int(max(0, math.floor(view_start / tick_step) * tick_step))
        tick_end = int(min(tick_duration, math.ceil(view_end / tick_step) * tick_step))
        for t in range(tick_start, tick_end + 1, tick_step):
            x = _timeline_view_time_to_x(float(t), view_start, view_end, draw_x1, draw_x2)
            c.create_line(x, 4, x, h - 4, fill="#222734")
            mm, ss = divmod(t, 60)
            c.create_text(x, h - 7, text=f"{mm}:{ss:02d}", fill=C_MUTED, font=("Courier New", 8))

        current_time = self._current_frame / max(1.0, self._fps)
        if compact and compact_ranges:
            playhead_time = _compact_source_to_display_time(current_time, compact_ranges)
            px = _timeline_view_time_to_x(playhead_time, view_start, view_end, draw_x1, draw_x2)
        else:
            px = _timeline_view_time_to_x(current_time, view_start, view_end, draw_x1, draw_x2)
        self._tl_playhead = c.create_line(px, 2, px, h - 2, fill=TL_HEAD, width=2)

        kept = sum(clip.end_s - clip.start_s for clip in self._timeline_model.video_track.clips)
        mode = "compacta" if compact else "original"
        self._tl_info.configure(
            text=f"Mantido: {_fmt(kept)}  |  Cortado: {_fmt(self._timeline_model.saved_time_s)}  |  Vista: {mode}  |  Preview: {self._preview_backend}"
        )

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
            canvas.create_line(x, center_y - peak, x, center_y + peak, fill="#7dc0ff")

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
            return _compact_display_to_source_time(display_time, compact_ranges)
        view_start, view_end = self._timeline_view_window(self._duration_s)
        return _timeline_x_to_view_time(x, view_start, view_end, x1, x2)

    def _trim_handle_at(self, x: int, y: int) -> Optional[tuple[int, str]]:
        if not self._timeline_model:
            return None
        top = 8
        video_y1, video_y2 = top + 12, top + 40
        audio_y1, audio_y2 = top + 56, self._tl_canvas.winfo_height() - 18
        if not _timeline_handle_y_in_range(y, video_y1, video_y2, audio_y1, audio_y2):
            return None

        w = self._tl_canvas.winfo_width()
        track_x1, track_x2 = self._timeline_track_bounds(w)
        track_x1, track_x2 = self._timeline_zoomed_bounds(track_x1, track_x2)
        clips = self._timeline_model.video_track.clips
        compact_ranges = self._compact_ranges_for_view()
        view_duration = compact_ranges[-1][3] if compact_ranges else self._duration_s
        view_start, view_end = self._timeline_view_window(view_duration)
        handle_px = 8

        for idx, clip in enumerate(clips):
            if compact_ranges:
                _, _, start_s, end_s = compact_ranges[idx]
                x1 = _timeline_view_time_to_x(start_s, view_start, view_end, track_x1, track_x2)
                x2 = _timeline_view_time_to_x(end_s, view_start, view_end, track_x1, track_x2)
            else:
                x1 = _timeline_view_time_to_x(clip.start_s, view_start, view_end, track_x1, track_x2)
                x2 = _timeline_view_time_to_x(clip.end_s, view_start, view_end, track_x1, track_x2)
            edge = _timeline_handle_edge_at(x, x1, x2, handle_px)
            if edge:
                return idx, edge
        return None

    def _snap_time_to_clip_edge(self, time_s: float) -> tuple[float, bool]:
        if not self._timeline_model:
            return time_s, False
        threshold_s = max(1.0 / max(1.0, self._fps), 0.08)
        return _snap_time_to_edges_with_flag(
            time_s,
            _clip_edges(self._timeline_model.video_track.clips),
            threshold_s,
        )

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
        self._section(s, "CORTE DE SILÊNCIO", 9)
        self._rm_silence_var = tk.BooleanVar(value=False)
        self._check(s, "Ativar corte de silêncios", self._rm_silence_var, 10)

        sf = tk.Frame(s, bg=C_PANEL)
        sf.grid(row=11, column=0, sticky="ew", padx=10, pady=(0,4))
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
                               row=17 + row_off)

        # -- Bokeh ---------------------------------------------------------
        self._section(s, "BOKEH  (desfoque de fundo)", 24)
        self._bokeh_slider = self._prop_slider(
            s, "Intensidade", "bokeh", 0, 100, 0, 1, 25,
            suffix="%", color="#223366", prog="#6699dd")

        # -- Audio ---------------------------------------------------------
        self._section(s, "ÁUDIO", 26)
        self._noise_var = tk.BooleanVar(value=True)
        self._check(s, "Redução de ruído + loudnorm EBU R128", self._noise_var, 27)

        mf = tk.Frame(s, bg=C_PANEL)
        mf.grid(row=28, column=0, sticky="ew", padx=10, pady=(0,6))
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
        self._section(s, "SAÍDAS EXTRAS", 29)
        self._gen_thumb_var  = tk.BooleanVar(value=False)
        self._gen_vert_var   = tk.BooleanVar(value=False)
        self._check(s, "Gerar 5 thumbnails profissionais", self._gen_thumb_var, 30)
        self._check(s, "Gerar versão vertical 9:16", self._gen_vert_var, 31)

        # -- Preview update btn --------------------------------------------
        ctk.CTkButton(s, text="Atualizar preview",
                      height=32, corner_radius=6,
                      fg_color=C_SURFACE, hover_color=C_BORDER,
                      font=ctk.CTkFont(size=12),
                      command=self._update_color_preview).grid(
            row=32, column=0, padx=10, pady=(8,4), sticky="ew")

        self._build_editor_assets_panel(s, 33)

    def _build_editor_assets_panel(self, parent, row: int) -> None:
        self._section(parent, "MÍDIAS DO PROJETO", row)
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
        self._media_listbox.bind("<Double-Button-1>", lambda _e: self._load_selected_project_media())
        media_actions = tk.Frame(parent, bg=C_PANEL)
        media_actions.grid(row=row + 2, column=0, sticky="ew", padx=10, pady=(0, 4))
        media_actions.grid_columnconfigure(0, weight=1)
        media_actions.grid_columnconfigure(1, weight=1)
        media_actions.grid_columnconfigure(2, weight=1)
        media_actions.grid_columnconfigure(3, weight=1)
        tk.Button(media_actions, text="Adicionar mídia", command=self._add_project_media,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Button(media_actions, text="Usar no clipe", command=self._assign_selected_media_to_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=1, sticky="ew", padx=4)
        tk.Button(media_actions, text="Abrir principal", command=self._load_selected_project_media,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=2, sticky="ew", padx=4)
        tk.Button(media_actions, text="Inserir/substituir", command=self._insert_selected_media_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        self._section(parent, "CLIPE SELECIONADO", row + 3)
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
        self._clip_label_entry.grid(row=row + 4, column=0, sticky="ew", padx=10, pady=2)
        self._clip_label_entry.bind("<Return>", lambda _e: self._apply_clip_inspector())
        self._clip_label_entry.bind("<FocusOut>", lambda _e: self._apply_clip_inspector())

        self._clip_scale_label = self._inspector_slider(
            parent, "Escala do vídeo", self._clip_scale_var, 25, 300, row + 5, suffix="%"
        )
        self._clip_pos_x_label = self._inspector_slider(
            parent, "Posição X", self._clip_pos_x_var, -100, 100, row + 6, suffix="%"
        )
        self._clip_pos_y_label = self._inspector_slider(
            parent, "Posição Y", self._clip_pos_y_var, -100, 100, row + 7, suffix="%"
        )
        self._clip_volume_label = self._inspector_slider(
            parent, "Volume do clipe", self._clip_volume_var, 0, 200, row + 8, suffix="%"
        )
        self._clip_text_x_label = self._inspector_slider(
            parent, "Texto X", self._clip_text_x_var, -100, 100, row + 9, suffix="%"
        )
        self._clip_text_y_label = self._inspector_slider(
            parent, "Texto Y", self._clip_text_y_var, 0, 100, row + 10, suffix="%"
        )
        self._clip_text_size_label = self._inspector_slider(
            parent, "Tamanho texto", self._clip_text_size_var, 50, 220, row + 11, suffix="%"
        )
        tr = tk.Frame(parent, bg=C_PANEL)
        tr.grid(row=row + 12, column=0, sticky="ew", padx=10, pady=(2, 4))
        tr.grid_columnconfigure(1, weight=1)
        tk.Label(tr, text="Transição", bg=C_PANEL, fg=C_MUTED,
                 font=("Segoe UI", 9)).grid(row=0, column=0, sticky="w")
        ctk.CTkOptionMenu(
            tr,
            values=["Corte", "Fade", "Dissolver"],
            variable=self._clip_transition_var,
            command=lambda _v: self._apply_clip_inspector(),
            fg_color=C_SURFACE,
            button_color=C_ACCENT,
            text_color=C_TEXT,
            width=120,
            font=ctk.CTkFont(size=11),
        ).grid(row=0, column=1, sticky="e")
        clip_actions = tk.Frame(parent, bg=C_PANEL)
        clip_actions.grid(row=row + 13, column=0, sticky="ew", padx=10, pady=(0, 8))
        clip_actions.grid_columnconfigure(0, weight=1)
        clip_actions.grid_columnconfigure(1, weight=1)
        tk.Button(clip_actions, text="Texto no clipe", command=self._add_text_to_selected_clip,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=0, sticky="ew", padx=(0, 4))
        tk.Button(clip_actions, text="Aplicar transição", command=self._apply_clip_inspector,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=0, column=1, sticky="ew", padx=(4, 0))
        chroma = tk.Frame(parent, bg=C_PANEL)
        chroma.grid(row=row + 14, column=0, sticky="ew", padx=10, pady=(0, 4))
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
        ctk.CTkEntry(
            chroma,
            textvariable=self._clip_chroma_color_var,
            fg_color=C_SURFACE,
            border_color=C_BORDER,
            text_color=C_TEXT,
            font=ctk.CTkFont(size=10),
            width=82,
            height=26,
        ).grid(row=0, column=1, sticky="e", padx=(6, 0))
        tk.Button(chroma, text="Conta-gotas", command=self._arm_chroma_picker,
                  bg=C_SURFACE, fg=C_TEXT, relief="flat", padx=6,
                  font=("Segoe UI", 9), cursor="hand2", bd=0).grid(row=1, column=0, columnspan=2, sticky="ew", pady=(4, 0))
        self._clip_chroma_tolerance_label = self._inspector_slider(
            parent, "Tolerância chroma", self._clip_chroma_tolerance_var, 5, 160, row + 15
        )
        self._section(parent, "STATUS DO PROJETO", row + 16)
        tk.Label(
            parent,
            textvariable=self._project_status_var,
            bg=C_PANEL,
            fg=C_MUTED,
            justify="left",
            anchor="w",
            wraplength=260,
            font=("Segoe UI", 9),
        ).grid(row=row + 17, column=0, sticky="ew", padx=12, pady=(0, 10))
        self._refresh_media_list()
        self._refresh_clip_inspector()
        self._refresh_project_status()

    def _inspector_slider(self, parent, label: str, var: tk.DoubleVar, lo: int, hi: int, row: int, suffix: str = "") -> tk.Label:
        frame = tk.Frame(parent, bg=C_PANEL)
        frame.grid(row=row, column=0, sticky="ew", padx=10, pady=2)
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
            command=lambda _v: (value_lbl.configure(text=f"{int(var.get())}{suffix}"), self._apply_clip_inspector()),
        )
        slider.grid(row=0, column=1, sticky="ew", padx=6)
        return value_lbl

    def _refresh_media_list(self) -> None:
        if self._media_listbox is None:
            return
        self._media_listbox.delete(0, "end")
        for path in self._project_media_paths:
            self._media_listbox.insert("end", Path(path).name)
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

    def _add_project_media(self) -> None:
        path = filedialog.askopenfilename(
            title="Adicionar mídia ao projeto",
            filetypes=[("Vídeos", "*.mp4 *.mov *.MOV *.avi *.mkv *.webm *.m4v"), ("Todos", "*.*")]
        )
        if not path:
            return
        if not _is_video_path(path):
            messagebox.showwarning("Mídia incompatível", "Use um arquivo de vídeo compatível.")
            return
        self._register_project_media([path])
        if self.video_path:
            self._save_project_video_path(self.video_path)
        else:
            self._save_project_media_paths()
        self._refresh_media_list()
        self._tb_status.configure(text="Mídia adicionada ao projeto.")

    def _load_selected_project_media(self) -> None:
        path = self._selected_project_media_path()
        if not path:
            self._tb_status.configure(text="Selecione uma mídia do projeto.")
            return
        self._load_video(path)

    def _selected_timeline_clip(self) -> Optional[TimelineClip]:
        if not self._timeline_model or self._selected_clip_index is None:
            return None
        clips = self._timeline_model.video_track.clips
        if 0 <= self._selected_clip_index < len(clips):
            return clips[self._selected_clip_index]
        return None

    def _refresh_clip_inspector(self) -> None:
        clip = self._selected_timeline_clip()
        self._clip_inspector_enabled = False
        if clip is None:
            self._clip_label_var.set("")
            self._clip_scale_var.set(100.0)
            self._clip_pos_x_var.set(0.0)
            self._clip_pos_y_var.set(0.0)
            self._clip_text_x_var.set(0.0)
            self._clip_text_y_var.set(72.0)
            self._clip_text_size_var.set(100.0)
            self._clip_volume_var.set(100.0)
            self._clip_transition_var.set("Corte")
            self._clip_chroma_var.set(False)
            self._clip_chroma_color_var.set("#00ff00")
            self._clip_chroma_tolerance_var.set(45.0)
        else:
            self._clip_label_var.set(clip.text_overlay or clip.label)
            self._clip_scale_var.set(float(getattr(clip, "scale_pct", 100.0)))
            self._clip_pos_x_var.set(float(getattr(clip, "position_x_pct", 0.0)))
            self._clip_pos_y_var.set(float(getattr(clip, "position_y_pct", 0.0)))
            self._clip_text_x_var.set(float(getattr(clip, "text_position_x_pct", 0.0)))
            self._clip_text_y_var.set(float(getattr(clip, "text_position_y_pct", 72.0)))
            self._clip_text_size_var.set(float(getattr(clip, "text_size_pct", 100.0)))
            self._clip_volume_var.set(float(getattr(clip, "volume_pct", 100.0)))
            self._clip_transition_var.set(str(getattr(clip, "transition", "Corte") or "Corte"))
            self._clip_chroma_var.set(bool(getattr(clip, "chroma_enabled", False)))
            self._clip_chroma_color_var.set(str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"))
            self._clip_chroma_tolerance_var.set(float(getattr(clip, "chroma_tolerance", 45.0)))
        self._clip_inspector_enabled = True
        self._refresh_project_status()

    def _apply_clip_inspector(self) -> None:
        if not self._clip_inspector_enabled:
            return
        clip = self._selected_timeline_clip()
        if clip is None:
            return
        clip.label = self._clip_label_var.get().strip() or clip.label
        clip.scale_pct = float(self._clip_scale_var.get())
        clip.position_x_pct = float(self._clip_pos_x_var.get())
        clip.position_y_pct = float(self._clip_pos_y_var.get())
        clip.text_position_x_pct = float(self._clip_text_x_var.get())
        clip.text_position_y_pct = float(self._clip_text_y_var.get())
        clip.text_size_pct = float(self._clip_text_size_var.get())
        clip.volume_pct = float(self._clip_volume_var.get())
        clip.transition = self._clip_transition_var.get()
        clip.chroma_enabled = bool(self._clip_chroma_var.get())
        clip.chroma_color = _normalize_hex_color(self._clip_chroma_color_var.get())
        clip.chroma_tolerance = float(self._clip_chroma_tolerance_var.get())
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._tb_status.configure(text="Ajustes do clipe atualizados.")

    def _add_text_to_selected_clip(self) -> None:
        clip = self._selected_timeline_clip()
        if clip is None:
            self._tb_status.configure(text="Selecione um clipe para adicionar texto.")
            return
        text = self._clip_label_var.get().strip() or clip.label or "Texto"
        clip.text_overlay = text
        clip.label = text
        clip.text_position_x_pct = float(self._clip_text_x_var.get())
        clip.text_position_y_pct = float(self._clip_text_y_var.get())
        clip.text_size_pct = float(self._clip_text_size_var.get())
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._tb_status.configure(text="Texto associado ao clipe selecionado.")

    def _assign_selected_media_to_clip(self) -> None:
        path = self._selected_project_media_path()
        clip = self._selected_timeline_clip()
        if not path:
            self._tb_status.configure(text="Selecione uma mídia do projeto.")
            return
        if clip is None:
            self._tb_status.configure(text="Selecione um clipe da timeline.")
            return
        clip.source_path = path
        clip.label = Path(path).stem
        self._clip_label_var.set(clip.label)
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        self._save_project_media_paths()
        self._tb_status.configure(text="Mídia associada ao clipe selecionado.")

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
            self._tb_status.configure(text=f"Clipe inserido na timeline: {Path(path).name}.")
        return

    def _insert_media_path_at_time(self, path: str, start_s: float, save: bool = True) -> bool:
        if not self._timeline_model:
            return False
        if start_s >= self._duration_s - self._trim_min_duration_s:
            start_s = max(0.0, self._duration_s - min(3.0, self._duration_s))
        new_clips, selected_index = _insert_media_clip_replacing_range(
            self._timeline_model.video_track.clips,
            path,
            start_s,
            self._duration_s,
            clip_duration_s=3.0,
            min_duration_s=self._trim_min_duration_s,
        )
        if selected_index is None:
            self._tb_status.configure(text="Sem espaço na timeline para inserir esse clipe.")
            return False
        self._push_timeline_undo()
        self._timeline_model.video_track.clips = new_clips
        self._selected_clip_index = selected_index
        self._timeline_dirty = True
        self._sync_manual_timeline(mark_dirty=True)
        inserted = new_clips[selected_index]
        self._seek_to(self._time_to_frame(inserted.start_s))
        if save:
            self._save_project_media_paths()
        return True

    def _refresh_project_status(self) -> None:
        selected = self._selected_timeline_clip()
        clip_name = selected.label if selected else "nenhum"
        source = Path(selected.source_path).name if selected and selected.source_path else "mídia principal"
        clips = len(self._timeline_model.video_track.clips) if self._timeline_model else 0
        self._project_status_var.set(
            f"Mídias: {len(self._project_media_paths)}\n"
            f"Clipes: {clips}\n"
            f"Selecionado: {clip_name}\n"
            f"Origem do clipe: {source}\n"
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
        self._timeline_dirty = False
        self._timeline_undo_stack.clear()
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
            media_paths = _merge_media_paths(metadata.get("media_paths"), [video_path])
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
            ))
            metadata["timeline_manifest"] = build_timeline_manifest(
                self._timeline_model,
                self.project_name,
                self.video_path,
            )
            Path(self.project_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
            print(f"[PROJECT] Não foi possível atualizar retomada do projeto: {exc}")

    def _register_project_media(self, paths: list[str]) -> None:
        self._project_media_paths = _merge_media_paths(self._project_media_paths, paths)
        self._refresh_media_list()

    def _save_project_media_paths(self) -> None:
        if not self.project_path:
            return
        try:
            metadata = _read_project_metadata(self.project_path)
            metadata["media_paths"] = _merge_media_paths(metadata.get("media_paths"), self._project_media_paths)
            metadata["updated_at"] = int(time.time())
            Path(self.project_path).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as exc:
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
        self._timeline_dirty = bool(metadata.get("timeline_dirty", True))
        return segments

    def _restore_project_playhead_if_available(self) -> None:
        metadata = self._pending_project_state
        if not metadata:
            return
        current_time_s = _project_float(metadata.get("current_time_s"), default=0.0)
        self._seek_to(self._time_to_frame(current_time_s))

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
        self._draw_frame_at(self._current_frame)
        self._update_time_label()
        self._update_tl_playhead()
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
        clip = self._selected_timeline_clip()
        if clip is not None and _point_inside_display_box(self._preview_display_box, event_x, event_y):
            mode = _preview_control_hit(self._preview_display_box, event_x, event_y, clip) or "move"
            base_x = float(getattr(clip, "text_position_x_pct", 0.0)) if mode == "text" else float(getattr(clip, "position_x_pct", 0.0))
            base_y = float(getattr(clip, "text_position_y_pct", 72.0)) if mode == "text" else float(getattr(clip, "position_y_pct", 0.0))
            self._preview_drag = (
                mode,
                event_x,
                event_y,
                base_x,
                base_y,
                float(getattr(clip, "scale_pct", 100.0)),
            )
            action = "posicionar texto" if mode == "text" else ("redimensionar" if mode == "scale" else "posicionar")
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
        self._draw_frame_at(self._current_frame, fast=True)
        return "break"

    def _on_preview_release(self, event: tk.Event) -> str | None:
        if self._preview_click_consumed:
            self._preview_click_consumed = False
            return "break"
        if self._preview_drag:
            self._preview_drag = None
            if self._preview_drag_moved:
                self._sync_manual_timeline(mark_dirty=True)
                self._tb_status.configure(text="Posição do clipe atualizada no preview.")
                return "break"
        if not self._chroma_picker_active:
            self._toggle_play()
        return "break"

    def _arm_chroma_picker(self) -> None:
        self._chroma_picker_active = True
        self._tb_status.configure(text="Conta-gotas ativo: clique no preview para capturar a cor do chroma.")

    def _draw_frame_at(self, frame_idx: int, fast: bool = False) -> None:
        if not self.video_path:
            return
        self._current_frame = max(0, min(frame_idx, self._total_frames - 1))
        self._preview_request_id += 1
        if fast:
            settings = PreviewSettings(
                color_grade=ColorGrade(enabled=False),
                bokeh_intensity=0.0,
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
            request_token=("preview", self._preview_request_id),
        )
        self._preview_settings_key = settings.cache_key()
        self._tb_status.configure(text="Atualizando preview...")
        self._preview_engine.request_frame(self._current_frame, settings)

    def _on_preview_frame_ready(self, preview: PreviewFrame) -> None:
        self._queue.put(("__PREVIEW__", preview))

    def _draw_preview_clip_controls(
        self,
        canvas: tk.Canvas,
        display_box: tuple[int, int, int, int],
        active_clip: Optional[TimelineClip],
    ) -> None:
        selected = self._selected_timeline_clip()
        if selected is None or active_clip is not selected:
            return
        x, y, w, h = display_box
        if w <= 0 or h <= 0:
            return
        x2, y2 = x + w, y + h
        canvas.create_rectangle(x, y, x2, y2, outline="#ffcc44", width=2, tags=("frame", "preview-controls"))
        for name, (hx, hy) in _preview_control_handles(display_box, active_clip).items():
            fill = "#7dc0ff" if name == "text" else "#ffcc44"
            canvas.create_rectangle(
                hx - 5,
                hy - 5,
                hx + 5,
                hy + 5,
                fill=fill,
                outline="#111116",
                width=1,
                tags=("frame", "preview-controls"),
            )
        canvas.create_text(
            x + 8,
            y + 8,
            text="arraste para mover | canto para escala",
            anchor="nw",
            fill="#ffec99",
            font=("Segoe UI", 9),
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

        clip_time_s = self._current_frame / max(1.0, self._fps)
        active_clip = _clip_for_time(self._timeline_model, clip_time_s)
        preview_source = _preview_base_image_for_timeline(preview.image, self._timeline_model, active_clip)
        preview_source = self._clip_source_preview_image(active_clip, clip_time_s) or preview_source
        preview_image = _apply_clip_preview_options(preview_source, active_clip)
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
        self._draw_preview_clip_controls(c, self._preview_display_box, active_clip)
        c.itemconfigure(self._no_video_id, state="hidden")
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
            self._tb_status.configure(text=f"Preview {preview.backend}  |  {preview.render_ms:.0f} ms")
        print(
            f"[PREVIEW] Frame rendered successfully | "
            f"frame={preview.frame_index} backend={preview.backend} "
            f"render_ms={preview.render_ms:.0f}"
        )

    def _clip_source_preview_image(self, clip: Optional[TimelineClip], time_s: float) -> Optional[Image.Image]:
        path = str(getattr(clip, "source_path", "") or "") if clip else ""
        if not path or not Path(path).exists() or path == self.video_path:
            return None
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
        self._request_playback_frame()

    def _stop_playback(self, reset_button: bool = True) -> None:
        self._playing = False
        self._play_generation += 1
        self._play_target_frame = None
        self._preview_bootstrap_key = None
        self._play_audio_started = False
        self._stop_preview_audio()
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
            self._stop_playback(reset_button=True)
            return
        settings = PreviewSettings(
            color_grade=ColorGrade(enabled=False),
            bokeh_intensity=0.0,
            request_token=("playback", self._play_generation, target),
        )
        self._play_target_frame = target
        self._preview_bootstrap_key = settings.cache_key()
        self._preview_engine.request_frame(target, settings)

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
        if not self.video_path:
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
                    self._render_preview_frame(val)
                elif msg == "__TIMELINE_READY__":
                    video_path, analysis, timeline_model = val
                    if video_path == self.video_path:
                        restored = self._restore_project_timeline_if_available(timeline_model)
                        self._segments = restored if restored is not None else analysis.speech_segments
                        self._analysis_done = True
                        self._timeline_model = timeline_model
                        self._timeline_undo_stack.clear()
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
    return metadata


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


def _first_video_path_from_drop(drop_data: str) -> Optional[str]:
    paths = _video_paths_from_drop(drop_data)
    return paths[0] if paths else None


def _video_paths_from_drop(drop_data: str) -> list[str]:
    return _merge_media_paths([], [item.strip().strip("{}") for item in _split_drop_paths(drop_data) if _is_video_path(item)])


def _project_media_paths_from_metadata(metadata: dict[str, object]) -> list[str]:
    return _merge_media_paths(metadata.get("media_paths"), [str(metadata.get("video_path") or "")])


def _first_existing_media_path(media_paths: list[str]) -> Optional[str]:
    for path in media_paths:
        if Path(path).exists():
            return path
    return None


def _merge_media_paths(existing: object, new_paths: list[str]) -> list[str]:
    merged: list[str] = []
    raw_existing = existing if isinstance(existing, list) else []
    for path in [*raw_existing, *new_paths]:
        clean = str(path).strip().strip("{}")
        if not clean or not _is_video_path(clean):
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
    timeline_model.removed_ranges = _removed_ranges_from_segments(duration_s, segments)
    timeline_model.saved_time_s = sum(end - start for start, end in timeline_model.removed_ranges)


def _clone_timeline_clip(clip: TimelineClip) -> TimelineClip:
    return TimelineClip(
        clip.start_s,
        clip.end_s,
        clip.clip_type,
        clip.label,
        getattr(clip, "source_path", ""),
        float(getattr(clip, "scale_pct", 100.0)),
        float(getattr(clip, "volume_pct", 100.0)),
        str(getattr(clip, "transition", "Corte") or "Corte"),
        str(getattr(clip, "text_overlay", "") or ""),
        float(getattr(clip, "text_position_x_pct", 0.0)),
        float(getattr(clip, "text_position_y_pct", 72.0)),
        float(getattr(clip, "text_size_pct", 100.0)),
        bool(getattr(clip, "chroma_enabled", False)),
        str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"),
        float(getattr(clip, "chroma_tolerance", 45.0)),
        float(getattr(clip, "position_x_pct", 0.0)),
        float(getattr(clip, "position_y_pct", 0.0)),
    )


def _clip_options_from_timeline_model(timeline_model: Optional[TimelineModel]) -> list[dict[str, object]]:
    if timeline_model is None:
        return []
    return [
        {
            "start_s": float(clip.start_s),
            "end_s": float(clip.end_s),
            "label": clip.label,
            "source_path": getattr(clip, "source_path", ""),
            "scale_pct": float(getattr(clip, "scale_pct", 100.0)),
            "volume_pct": float(getattr(clip, "volume_pct", 100.0)),
            "transition": str(getattr(clip, "transition", "Corte") or "Corte"),
            "text_overlay": str(getattr(clip, "text_overlay", "") or ""),
            "text_position_x_pct": float(getattr(clip, "text_position_x_pct", 0.0)),
            "text_position_y_pct": float(getattr(clip, "text_position_y_pct", 72.0)),
            "text_size_pct": float(getattr(clip, "text_size_pct", 100.0)),
            "chroma_enabled": bool(getattr(clip, "chroma_enabled", False)),
            "chroma_color": str(getattr(clip, "chroma_color", "#00ff00") or "#00ff00"),
            "chroma_tolerance": float(getattr(clip, "chroma_tolerance", 45.0)),
            "position_x_pct": float(getattr(clip, "position_x_pct", 0.0)),
            "position_y_pct": float(getattr(clip, "position_y_pct", 0.0)),
        }
        for clip in timeline_model.video_track.clips
    ]


def _apply_clip_options_to_timeline_model(timeline_model: TimelineModel, raw_options: object) -> None:
    if not isinstance(raw_options, list):
        return
    for clip, raw in zip(timeline_model.video_track.clips, raw_options):
        if not isinstance(raw, dict):
            continue
        clip.label = str(raw.get("label") or clip.label)
        clip.source_path = str(raw.get("source_path") or "")
        clip.scale_pct = _project_float(raw.get("scale_pct"), 100.0)
        clip.volume_pct = _project_float(raw.get("volume_pct"), 100.0)
        clip.transition = str(raw.get("transition") or "Corte")
        clip.text_overlay = str(raw.get("text_overlay") or "")
        clip.text_position_x_pct = _project_float(raw.get("text_position_x_pct"), 0.0)
        clip.text_position_y_pct = _project_float(raw.get("text_position_y_pct"), 72.0)
        clip.text_size_pct = _project_float(raw.get("text_size_pct"), 100.0)
        clip.chroma_enabled = bool(raw.get("chroma_enabled", False))
        clip.chroma_color = _normalize_hex_color(str(raw.get("chroma_color") or "#00ff00"))
        clip.chroma_tolerance = _project_float(raw.get("chroma_tolerance"), 45.0)
        clip.position_x_pct = _project_float(raw.get("position_x_pct"), 0.0)
        clip.position_y_pct = _project_float(raw.get("position_y_pct"), 0.0)
    timeline_model.audio_track.clips = [_clone_timeline_clip(clip) for clip in timeline_model.video_track.clips]


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


def _clip_source_frame_index(clip: TimelineClip, timeline_time_s: float, fps: float, total_frames: int) -> int:
    offset_s = max(0.0, float(timeline_time_s) - float(clip.start_s))
    frame = int(round(offset_s * max(1.0, float(fps))))
    return max(0, min(max(0, int(total_frames) - 1), frame))


def _apply_clip_preview_options(image: Image.Image, clip: Optional[TimelineClip]) -> Image.Image:
    if clip is None:
        return image
    scale_pct = max(25.0, min(300.0, float(getattr(clip, "scale_pct", 100.0))))
    pos_x = _clamp_float(float(getattr(clip, "position_x_pct", 0.0)), -100.0, 100.0)
    pos_y = _clamp_float(float(getattr(clip, "position_y_pct", 0.0)), -100.0, 100.0)
    text_overlay = str(getattr(clip, "text_overlay", "") or "").strip()
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
        )
    return out


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
) -> Image.Image:
    out = image.copy()
    draw = ImageDraw.Draw(out)
    width, height = out.size
    font_scale = max(0.5, min(2.2, float(size_pct) / 100.0))
    text_x, text_y = _preview_text_anchor(width, height, pos_x_pct, pos_y_pct)
    pad = max(5, int(8 * font_scale))
    line_h = max(18, int(height * 0.045 * font_scale))
    text_w = min(width - 2 * pad, max(40, int(len(text[:80]) * line_h * 0.48)))
    draw.rounded_rectangle(
        (text_x - pad, text_y - pad, min(width - 1, text_x + text_w + pad), min(height - 1, text_y + line_h + pad)),
        radius=4,
        fill=(0, 0, 0),
    )
    draw.text((text_x, text_y), text[:80], fill=(255, 255, 255))
    return out


def _apply_chroma_key_preview(image: Image.Image, color: str, tolerance: float) -> Image.Image:
    target = np.array(_hex_to_rgb(_normalize_hex_color(color)), dtype=np.int16)
    arr = np.array(image.convert("RGB"), dtype=np.int16)
    diff = np.linalg.norm(arr - target, axis=2)
    mask = diff <= max(1.0, float(tolerance))
    if not mask.any():
        return image
    out = arr.astype(np.uint8)
    checker = ((np.indices(mask.shape).sum(axis=0) // 18) % 2) * 42 + 24
    out[mask] = np.stack([checker, checker, checker], axis=2)[mask].astype(np.uint8)
    return Image.fromarray(out, "RGB")


def _normalize_hex_color(value: str) -> str:
    text = str(value or "").strip()
    if not text.startswith("#"):
        text = f"#{text}"
    if re.fullmatch(r"#[0-9a-fA-F]{6}", text):
        return text.lower()
    return "#00ff00"


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


def _preview_control_handles(
    display_box: tuple[int, int, int, int],
    clip: Optional[TimelineClip] = None,
) -> dict[str, tuple[int, int]]:
    box_x, box_y, box_w, box_h = display_box
    handles = {"scale": (box_x + box_w, box_y + box_h)}
    if clip is not None and str(getattr(clip, "text_overlay", "") or "").strip():
        text_x, text_y = _preview_text_anchor(
            box_w,
            box_h,
            float(getattr(clip, "text_position_x_pct", 0.0)),
            float(getattr(clip, "text_position_y_pct", 72.0)),
        )
        handles["text"] = (box_x + text_x, box_y + text_y)
    return handles


def _preview_control_hit(
    display_box: tuple[int, int, int, int],
    x: int,
    y: int,
    clip: Optional[TimelineClip] = None,
    radius: int = 12,
) -> Optional[str]:
    if not _point_inside_display_box(display_box, x, y):
        return None
    for name, (hx, hy) in _preview_control_handles(display_box, clip).items():
        if abs(x - hx) <= radius and abs(y - hy) <= radius:
            return name
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
    zoom_value = max(1.0, float(zoom))
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

    inserted = TimelineClip(start, end, "speech", Path(source_path).stem, source_path=source_path)
    result.append(inserted)
    result.sort(key=lambda clip: (clip.start_s, clip.end_s))
    selected = next((idx for idx, clip in enumerate(result) if clip is inserted), None)
    return result, selected


def _clip_edges(clips: list[TimelineClip]) -> list[float]:
    edges: list[float] = []
    for clip in clips:
        edges.extend([clip.start_s, clip.end_s])
    return edges


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
) -> tuple[float, float]:
    clip = clips[index]
    prev_end = clips[index - 1].end_s if index > 0 else 0.0
    next_start = clips[index + 1].start_s if index + 1 < len(clips) else duration_s
    min_duration_s = max(0.01, min_duration_s)

    if edge == "start":
        new_start = max(prev_end, min(float(time_s), clip.end_s - min_duration_s))
        return new_start, clip.end_s
    if edge == "end":
        new_end = min(next_start, max(float(time_s), clip.start_s + min_duration_s))
        return clip.start_s, new_end
    raise ValueError(f"Borda de trim inválida: {edge}")


def _timeline_handle_edge_at(x: int, x1: int, x2: int, handle_px: int) -> Optional[str]:
    if abs(x - x1) <= handle_px:
        return "start"
    if abs(x - x2) <= handle_px:
        return "end"
    return None


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
