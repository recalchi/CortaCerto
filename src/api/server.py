from __future__ import annotations

import asyncio
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

import uvicorn
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from pydantic import BaseModel

app = FastAPI(title="CortaCerto API", version="1.0.0")

# Serve React production build if available (avoids file:// CORS issues)
_DIST_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "web", "dist")
if os.path.isdir(_DIST_DIR):
    from fastapi.staticfiles import StaticFiles as _SF

    @app.get("/")
    async def _root():
        from fastapi.responses import FileResponse
        return FileResponse(os.path.join(_DIST_DIR, "index.html"))

    # Mount assets AFTER API routes so /api/... routes take priority
    app.mount("/assets", _SF(directory=os.path.join(_DIST_DIR, "assets")), name="assets")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Content-Range", "Accept-Ranges", "Content-Length"],
)

# -- In-memory project state ---------------------------------------------------
_current_project: dict | None = None
_project_lock = threading.Lock()

# -- pywebview window reference (set by webview_app after window is created) ---
# When set, file dialogs use webview's native API (runs on the GUI main thread).
# When None (dev/browser mode), falls back to a subprocess-based tkinter call.
_webview_window = None

def set_webview_window(window) -> None:
    global _webview_window
    _webview_window = window


def _wv_open_dialog_type():
    """Return the correct open-dialog constant for the installed pywebview version."""
    try:
        from webview import FileDialog
        return FileDialog.OPEN          # pywebview ≥ 4.x
    except (ImportError, AttributeError):
        import webview
        return webview.OPEN_DIALOG      # pywebview < 4.x (deprecated)


def _wv_save_dialog_type():
    """Return the correct save-dialog constant for the installed pywebview version."""
    try:
        from webview import FileDialog
        return FileDialog.SAVE          # pywebview ≥ 4.x
    except (ImportError, AttributeError):
        import webview
        return webview.SAVE_DIALOG      # pywebview < 4.x (deprecated)


def _native_open_dialog(title: str, filetypes_wv: tuple, filetypes_tk: list) -> str:
    """Open a native file-open dialog.  Thread-safe; works from any thread."""
    if _webview_window is not None:
        try:
            paths = _webview_window.create_file_dialog(
                _wv_open_dialog_type(),
                allow_multiple=False,
                file_types=filetypes_wv,
            )
            return paths[0] if paths else ""
        except Exception:
            pass   # fall through to subprocess fallback

    # Subprocess fallback: spawn a fresh Python process so tkinter runs on its own
    # main thread (avoids the "main thread is not in main loop" crash).
    return _subprocess_tk_dialog("open", {"title": title, "filetypes": filetypes_tk})


def _native_save_dialog(title: str, default_name: str,
                        filetypes_wv: tuple, filetypes_tk: list,
                        default_ext: str, initial_dir: str = "") -> str:
    """Open a native file-save dialog.  Thread-safe; works from any thread."""
    if _webview_window is not None:
        try:
            paths = _webview_window.create_file_dialog(
                _wv_save_dialog_type(),
                save_filename=default_name,
                file_types=filetypes_wv,
            )
            return paths[0] if paths else ""
        except Exception:
            pass

    return _subprocess_tk_dialog("save", {
        "title": title,
        "initialfile": default_name,
        **({"initialdir": initial_dir} if initial_dir else {}),
        "filetypes": filetypes_tk,
        "defaultextension": default_ext,
    })


def _subprocess_tk_dialog(dialog_type: str, kwargs: dict) -> str:
    """Run a tkinter dialog in a fresh subprocess to avoid main-thread restrictions."""
    import json as _json
    script = (
        "import tkinter as tk, json, sys\n"
        "from tkinter import filedialog\n"
        "root = tk.Tk(); root.withdraw(); root.lift()\n"
        "root.attributes('-topmost', True)\n"
        f"kwargs = json.loads({_json.dumps(_json.dumps(kwargs))})\n"
        "if kwargs.get('filetypes'):\n"
        "    kwargs['filetypes'] = [tuple(f) for f in kwargs['filetypes']]\n"
        "dialog_type = " + repr(dialog_type) + "\n"
        "if dialog_type == 'open':\n"
        "    path = filedialog.askopenfilename(**kwargs)\n"
        "else:\n"
        "    path = filedialog.asksaveasfilename(**kwargs)\n"
        "root.destroy()\n"
        "print(path or '', end='')\n"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True, text=True, timeout=300,
        )
        return result.stdout.strip()
    except Exception:
        return ""

# -- Analysis cache: (normpath, mtime_ns, silence_style) → timeline_response ---
# Avoids re-running ffmpeg silence detection on files that haven't changed.
_analysis_cache: dict[str, dict] = {}
_CACHE_MAX = 20   # keep at most 20 entries

def _cache_key(path: str, silence_style: str, auto_cut: bool = True) -> str:
    try:
        mtime_ns = os.stat(path).st_mtime_ns
    except OSError:
        mtime_ns = 0
    style = silence_style if auto_cut else "raw"
    return f"{path}::{mtime_ns}::{style}"

# -- H.264 proxy (WebView2 / Chromium cannot decode H.265 without Windows codec) ---
# When the source video uses HEVC/H.265 (or VP9/AV1), we transcode to H.264 in a
# background thread so the <video> element can play it.  Transcoded files are
# cached in the system temp directory; they persist across runs so we only encode once.

_proxy_jobs: dict[str, dict] = {}   # md5(normpath) → {status, proxy_path, detail?}
_proxy_lock = threading.Lock()

# Codecs that WebView2 cannot play without extra Windows codec packs
_NEEDS_PROXY = frozenset({"hevc", "h265", "vp9", "vp8", "av1", "mpeg4", "vc1"})
_WEB_SAFE_CONTAINERS = frozenset({".mp4", ".m4v"})
_WEB_SAFE_H264_PIX_FMTS = frozenset({"yuv420p", "yuvj420p"})


def _get_video_stream_info(path: str) -> dict[str, str]:
    """Return stream fields used to decide if the WebView preview needs a proxy."""
    from src.ffmpeg_env import ffprobe
    try:
        r = subprocess.run(
            [
                ffprobe(), "-v", "quiet", "-select_streams", "v:0",
                "-show_entries", "stream=codec_name,pix_fmt,profile",
                "-of", "json", path,
            ],
            capture_output=True, text=True, timeout=10,
        )
        data = json.loads(r.stdout or "{}")
        stream = (data.get("streams") or [{}])[0]
        return {
            "codec": str(stream.get("codec_name") or "unknown").lower(),
            "pix_fmt": str(stream.get("pix_fmt") or "").lower(),
            "profile": str(stream.get("profile") or "").lower(),
        }
    except Exception:
        return {"codec": "unknown", "pix_fmt": "", "profile": ""}


def _needs_preview_proxy(video_path: str, info: dict[str, str]) -> bool:
    codec = info.get("codec", "unknown")
    pix_fmt = info.get("pix_fmt", "")
    suffix = Path(video_path).suffix.lower()
    if codec in _NEEDS_PROXY:
        return True
    if suffix not in _WEB_SAFE_CONTAINERS:
        return True
    if codec != "h264":
        return True
    if pix_fmt and pix_fmt not in _WEB_SAFE_H264_PIX_FMTS:
        return True
    return False

def _get_video_codec(path: str) -> str:
    """Return the primary video-stream codec (e.g. 'h264', 'hevc'). Fast – header only."""
    return _get_video_stream_info(path)["codec"]

def _run_proxy_transcode(video_path: str, proxy_path: str, key: str) -> None:
    """Transcode to H.264/AAC in a daemon thread. Caps at 720p for preview speed."""
    from src.ffmpeg_env import ffmpeg
    try:
        cmd = [
            ffmpeg(), "-y", "-i", video_path,
            "-vf", "scale=-2:720",         # limit preview to 720p (fast transcode)
            "-c:v", "libx264",
            "-preset", "ultrafast",
            "-crf", "26",
            "-c:a", "aac", "-b:a", "128k",
            "-movflags", "+faststart",
            proxy_path,
        ]
        result = subprocess.run(cmd, capture_output=True, timeout=1800)
        with _proxy_lock:
            if result.returncode == 0 and os.path.isfile(proxy_path):
                _proxy_jobs[key]["status"] = "ready"
            else:
                _proxy_jobs[key]["status"] = "error"
                _proxy_jobs[key]["detail"] = result.stderr.decode(errors="replace")[-400:]
    except Exception as e:
        with _proxy_lock:
            _proxy_jobs[key]["status"] = "error"
            _proxy_jobs[key]["detail"] = str(e)

def _start_proxy_if_needed(video_path: str) -> tuple[str, str, str]:
    """Check codec; start background transcode if necessary.

    Returns (codec, proxy_status, proxy_path).
    proxy_status is 'not_needed' | 'transcoding' | 'ready' | 'error'.
    """
    info = _get_video_stream_info(video_path)
    codec = info["codec"]
    if not _needs_preview_proxy(video_path, info):
        return codec, "not_needed", ""

    key = hashlib.md5(os.path.normpath(video_path).encode()).hexdigest()
    proxy_path = os.path.join(tempfile.gettempdir(), f"cc_proxy_{key}.mp4")

    with _proxy_lock:
        if key in _proxy_jobs:
            job = _proxy_jobs[key]
            return codec, job["status"], proxy_path
        # Check if a previous run already created the file
        if os.path.isfile(proxy_path):
            _proxy_jobs[key] = {"status": "ready", "proxy_path": proxy_path}
            return codec, "ready", proxy_path
        _proxy_jobs[key] = {"status": "transcoding", "proxy_path": proxy_path}

    t = threading.Thread(
        target=_run_proxy_transcode,
        args=(video_path, proxy_path, key),
        daemon=True,
    )
    t.start()
    return codec, "transcoding", proxy_path


# -- Models --------------------------------------------------------------------
class OpenProjectRequest(BaseModel):
    path: str
    silence_style: str = "natural"   # "aggressive" | "natural" | "light"
    auto_cut: bool = False           # when False, import as a single clip with full duration (CapCut-style)

class OpenFileDialogRequest(BaseModel):
    type: str = "video"

class OpenSaveDialogRequest(BaseModel):
    default_name: str = "output.mp4"
    type: str = "video"   # "video" | "project"
    initial_dir: str = ""

class SaveProjectRequest(BaseModel):
    path: str
    project: dict

class StockSearchRequest(BaseModel):
    provider: str
    query: str
    media_type: str = "image"
    per_page: int = 12

class StockDownloadRequest(BaseModel):
    asset: dict

class StockSettingsRequest(BaseModel):
    values: dict[str, str]

class ApiSettingsRequest(BaseModel):
    values: dict[str, str]

# -- Helpers -------------------------------------------------------------------

def _clip_to_dict(clip, track_name: str, video_path: str = "") -> dict:
    return {
        "id": f"{track_name}_{clip.start_s:.3f}_{uuid.uuid4().hex[:6]}",
        "start_s": clip.start_s,
        "end_s": clip.end_s,
        "clip_type": clip.clip_type,
        "label": clip.label,
        # Prefer clip's own source_path; fall back to the project video
        "source_path": clip.source_path or video_path,
        "volume_pct": clip.volume_pct,
        "scale_pct": clip.scale_pct,
        "transition": clip.transition,
        "text_overlay":          clip.text_overlay,
        "text_position_x_pct":   getattr(clip, "text_position_x_pct",  0.0),
        "text_position_y_pct":   getattr(clip, "text_position_y_pct",  72.0),
        "text_size_pct":         getattr(clip, "text_size_pct",         100.0),
        "text_color":            getattr(clip, "text_color",            "#ffffff"),
        "text_bold":             getattr(clip, "text_bold",             False),
        "text_italic":           getattr(clip, "text_italic",           False),
        "text_align":            getattr(clip, "text_align",            "center"),
        "chroma_enabled":        getattr(clip, "chroma_enabled",        False),
        "chroma_color":          getattr(clip, "chroma_color",          "#00ff00"),
        "chroma_tolerance":      getattr(clip, "chroma_tolerance",      45.0),
        "brightness": clip.brightness,
        "contrast": clip.contrast,
        "saturation": clip.saturation,
        "crop_top_pct": clip.crop_top_pct,
        "crop_bottom_pct": clip.crop_bottom_pct,
        "crop_left_pct": clip.crop_left_pct,
        "crop_right_pct": clip.crop_right_pct,
        "speed_factor": clip.speed_factor,
        "rotation_deg": getattr(clip, "rotation_deg", 0),
        "blend_mode":   getattr(clip, "blend_mode",   "Normal"),
        "opacity_pct":  getattr(clip, "opacity_pct",  100),
        "z_order": clip.z_order,
    }

def _track_to_dict(track, track_name: str, video_path: str = "") -> dict:
    return {
        "name": track.name,
        "clips": [_clip_to_dict(c, track_name, video_path) for c in track.clips],
    }

def _timeline_to_response(timeline, video_path: str) -> dict:
    return {
        "loaded": True,
        "videoPath": video_path,
        "duration_s": timeline.duration_s,
        "waveform": timeline.waveform[:500],
        "video_track":   _track_to_dict(timeline.video_track,   "video",   video_path),
        "audio_track":   _track_to_dict(timeline.audio_track,   "audio",   video_path),
        "text_track":    _track_to_dict(timeline.text_track,    "text",    video_path),
        "overlay_track": _track_to_dict(timeline.overlay_track, "overlay", video_path),
        # Multi-track (Phase 2b): expose parallel video/audio tracks to the frontend.
        # Tracks come back in display order (base track first, then extras).
        "extra_video_tracks": [
            _track_to_dict(t, f"video_{i+1}", video_path)
            for i, t in enumerate(getattr(timeline, "extra_video_tracks", []) or [])
        ],
        "extra_audio_tracks": [
            _track_to_dict(t, f"audio_{i+1}", video_path)
            for i, t in enumerate(getattr(timeline, "extra_audio_tracks", []) or [])
        ],
        "removed_ranges": timeline.removed_ranges,
        "saved_time_s": timeline.saved_time_s,
    }

# -- Routes --------------------------------------------------------------------

@app.get("/api/health")
async def health():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/api/encoder-info")
def encoder_info():
    """Detect and return the best available H.264 encoder on this machine."""
    try:
        from src.ffmpeg_env import detect_video_encoder, encoder_label
        name, _ = detect_video_encoder()
        label   = encoder_label()
        return {"encoder": name, "label": label}
    except Exception as e:
        return {"encoder": "libx264", "label": f"CPU (x264) — {e}"}

@app.get("/api/video-proxy-status")
def video_proxy_status(path: str):
    """Poll proxy transcode progress. Returns {status, proxy_path}."""
    key = hashlib.md5(os.path.normpath(path).encode()).hexdigest()
    proxy_path = os.path.join(tempfile.gettempdir(), f"cc_proxy_{key}.mp4")
    with _proxy_lock:
        job = _proxy_jobs.get(key)
    if not job:
        # Check if file exists from a previous session
        if os.path.isfile(proxy_path):
            return {"status": "ready", "proxy_path": proxy_path}
        return {"status": "not_started", "proxy_path": ""}
    return {"status": job["status"], "proxy_path": job.get("proxy_path", "")}


@app.get("/api/audio-waveform")
async def get_audio_waveform(path: str, bins: int = 300):
    """Extract a waveform from an audio file (mp3, wav, aac, flac…).

    Returns {samples: [...], duration_s: N} — used by the frontend to render
    per-clip waveform bars for imported music tracks (F5).
    """
    import os as _os
    norm_path = _os.path.normpath(path)
    if not _os.path.isfile(norm_path):
        raise HTTPException(status_code=404, detail="Arquivo não encontrado")
    try:
        from src.core.audio_waveform import extract_waveform
        from src.core.analyzer import _get_duration
        loop = asyncio.get_event_loop()
        duration_s = await loop.run_in_executor(None, lambda: _get_duration(norm_path))
        wf = await loop.run_in_executor(
            None,
            lambda: extract_waveform(norm_path, duration_s=duration_s, bins=bins)
        )
        return {"samples": wf.samples, "duration_s": wf.duration_s}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/stock/settings")
def get_stock_settings():
    from src.core.stock_assets import stock_settings
    return stock_settings()


@app.post("/api/stock/settings")
def save_stock_settings(req: StockSettingsRequest):
    from src.core.stock_assets import update_stock_settings
    return update_stock_settings(req.values)


@app.post("/api/stock/search")
def search_stock(req: StockSearchRequest):
    from src.core.stock_assets import search_stock_assets
    try:
        return {
            "items": search_stock_assets(
                req.provider,
                req.query,
                req.media_type,
                req.per_page,
            )
        }
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/stock/downloads")
def stock_downloads():
    from src.core.stock_assets import list_downloaded_assets
    return {"items": list_downloaded_assets()}


@app.post("/api/stock/download")
def stock_download(req: StockDownloadRequest):
    from src.core.stock_assets import download_stock_asset
    try:
        return download_stock_asset(req.asset)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.get("/api/settings")
def get_app_settings():
    from src.api_settings import load_api_credentials
    from src.core.app_settings import general_settings
    from src.core.api_usage import openai_usage_settings
    from src.core.stock_assets import stock_settings
    return {
        "general": general_settings(),
        "stock": stock_settings(),
        "openai": openai_usage_settings(),
        "configured_apis": [
            {
                "name": credential.name,
                "masked": credential.masked_value,
                "source": credential.source,
            }
            for credential in load_api_credentials()
        ],
    }


@app.post("/api/settings")
def save_app_settings(req: ApiSettingsRequest):
    from src.api_settings import update_env_file
    from src.core.app_settings import GENERAL_SETTING_ENV_NAMES
    from src.core.api_usage import OPENAI_USAGE_ENV_NAMES
    from src.core.stock_assets import STOCK_ENV_NAMES
    allowed = set(STOCK_ENV_NAMES) | set(OPENAI_USAGE_ENV_NAMES) | set(GENERAL_SETTING_ENV_NAMES)
    clean = {
        key: str(value).strip()
        for key, value in req.values.items()
        if key in allowed and str(value).strip()
    }
    if clean:
        update_env_file(clean)
    return get_app_settings()


@app.get("/api/openai/usage")
def get_openai_usage(limit: int = 50):
    from src.core.api_usage import openai_usage_summary
    return openai_usage_summary(limit=limit)


@app.post("/api/cache/clear")
def clear_app_cache():
    from src.core.app_settings import clear_cache
    global _thumb_cache
    _analysis_cache.clear()
    _thumb_cache.clear()
    return {"cache": clear_cache()}


@app.post("/api/open-file-dialog")
def open_file_dialog(req: OpenFileDialogRequest):
    """Open a native file-open dialog.

    Uses pywebview's create_file_dialog (safe from any thread) when running as
    a desktop app.  Falls back to a subprocess-based tkinter call in dev/browser mode.
    Sync `def` so FastAPI routes it through its thread-pool executor (non-blocking
    for the uvicorn event loop).
    """
    if req.type == "video":
        path = _native_open_dialog(
            title="Abrir vídeo",
            filetypes_wv=("Vídeo (*.mp4;*.mov;*.avi;*.mkv;*.webm;*.m4v)", "Todos os arquivos (*.*)",),
            filetypes_tk=[("Vídeo", "*.mp4 *.mov *.avi *.mkv *.webm *.m4v"), ("Todos", "*.*")],
        )
    elif req.type == "audio":
        path = _native_open_dialog(
            title="Abrir áudio",
            filetypes_wv=("Áudio (*.mp3;*.wav;*.aac;*.m4a;*.flac;*.ogg;*.opus;*.wma)", "Todos os arquivos (*.*)",),
            filetypes_tk=[("Áudio", "*.mp3 *.wav *.aac *.m4a *.flac *.ogg *.opus *.wma"), ("Todos", "*.*")],
        )
    elif req.type == "image":
        path = _native_open_dialog(
            title="Abrir imagem",
            filetypes_wv=("Imagens (*.png;*.jpg;*.jpeg;*.webp;*.gif;*.bmp)", "Todos os arquivos (*.*)",),
            filetypes_tk=[("Imagens", "*.png *.jpg *.jpeg *.webp *.gif *.bmp"), ("Todos", "*.*")],
        )
    elif req.type == "project":
        path = _native_open_dialog(
            title="Abrir projeto CortaCerto",
            filetypes_wv=("Projeto CortaCerto (*.ccproj)", "Todos os arquivos (*.*)",),
            filetypes_tk=[("Projeto CortaCerto", "*.ccproj"), ("Todos", "*.*")],
        )
    else:
        path = _native_open_dialog(
            title="Abrir arquivo",
            filetypes_wv=("Todos os arquivos (*.*)",),
            filetypes_tk=[("Todos", "*.*")],
        )
    return {"path": path or ""}


@app.post("/api/open-save-dialog")
def open_save_dialog(req: OpenSaveDialogRequest):
    """Open a native file-save dialog.

    Same threading strategy as open_file_dialog.
    """
    from src.core.app_settings import general_settings
    configured_dir = str(req.initial_dir or general_settings().get("default_save_dir") or "").strip()
    initial_dir = configured_dir if configured_dir and os.path.isdir(configured_dir) else ""
    if req.type == "project":
        path = _native_save_dialog(
            title="Salvar projeto CortaCerto",
            default_name=req.default_name,
            filetypes_wv=("Projeto CortaCerto (*.ccproj)", "Todos os arquivos (*.*)",),
            filetypes_tk=[("Projeto CortaCerto", "*.ccproj"), ("Todos", "*.*")],
            default_ext=".ccproj",
            initial_dir=initial_dir,
        )
    else:
        path = _native_save_dialog(
            title="Salvar vídeo exportado",
            default_name=req.default_name,
            filetypes_wv=("MP4 (*.mp4)", "MOV (*.mov)", "Todos os arquivos (*.*)",),
            filetypes_tk=[("MP4", "*.mp4"), ("MOV", "*.mov"), ("Todos", "*.*")],
            default_ext=".mp4",
            initial_dir=initial_dir,
        )
    return {"path": path or ""}

@app.post("/api/save-project")
def save_project(req: SaveProjectRequest):
    """Persist the current frontend project state as a JSON file (.ccproj)."""
    save_path = os.path.normpath(req.path)
    try:
        with open(save_path, "w", encoding="utf-8") as f:
            json.dump(req.project, f, ensure_ascii=False, indent=2)
        return {"ok": True, "path": save_path}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao salvar: {e}")

@app.post("/api/load-project")
def load_saved_project(req: OpenProjectRequest):
    """Load a previously saved .ccproj JSON file and return it as project state."""
    path = os.path.normpath(req.path)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {path}")
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erro ao carregar: {e}")

_STYLE_PARAMS = {
    "aggressive": dict(silence_threshold_db=-35, min_silence_ms=150),
    "natural":    dict(silence_threshold_db=-40, min_silence_ms=400),
    "light":      dict(silence_threshold_db=-45, min_silence_ms=800),
}


@app.post("/api/open-project")
async def open_project(req: OpenProjectRequest):
    """Analyse the video and return initial timeline state.

    Results are cached by (path, mtime, silence_style) so re-opening the same
    file with the same settings is instant.
    """
    global _current_project
    path = os.path.normpath(req.path)

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {path}")

    # Cache hit — return previous result immediately without re-running ffmpeg
    ck = _cache_key(path, req.silence_style, req.auto_cut)
    if ck in _analysis_cache:
        cached = _analysis_cache[ck]
        with _project_lock:
            _current_project = cached["project"]
        # Re-check proxy status (may have finished since last open)
        resp = dict(cached["response"])
        codec, proxy_status, proxy_path = _start_proxy_if_needed(path)
        resp["video_codec"]  = codec
        resp["proxy_status"] = proxy_status
        resp["proxy_path"]   = proxy_path
        return resp

    try:
        from src.core.audio_waveform import extract_waveform
        from src.core.analyzer import analyze_video, _get_duration
        from src.core.timeline_model import build_timeline_model
        from src.config import ProcessingConfig

        cfg = ProcessingConfig()
        loop = asyncio.get_event_loop()

        # Fast path (CapCut-style): no silence analysis — single clip with full duration.
        if not req.auto_cut:
            duration_s = await loop.run_in_executor(None, lambda: _get_duration(path))
            waveform   = await loop.run_in_executor(
                None, lambda: extract_waveform(path, duration_s=duration_s, bins=500)
            )
            analysis = None
            segments = [(0.0, duration_s)]
        else:
            style_params = _STYLE_PARAMS.get(req.silence_style, _STYLE_PARAMS["natural"])

            # O1: Probe duration first (fast header read), then run silence-detect
            # and waveform extraction IN PARALLEL to halve analysis time.
            duration_s = await loop.run_in_executor(None, lambda: _get_duration(path))

            analysis_future = loop.run_in_executor(
                None,
                lambda: analyze_video(
                    path,
                    silence_threshold_db=style_params["silence_threshold_db"],
                    min_silence_ms=style_params["min_silence_ms"],
                    audio_padding_ms=cfg.audio_padding_ms,
                    min_segment_s=cfg.min_segment_s,
                )
            )
            waveform_future = loop.run_in_executor(
                None,
                lambda: extract_waveform(path, duration_s=duration_s, bins=500)
            )
            analysis, waveform = await asyncio.gather(analysis_future, waveform_future)

            duration_s = analysis.duration_s  # use the authoritative value from analysis
            segments   = analysis.speech_segments or [(0.0, duration_s)]

        timeline = build_timeline_model(duration_s, segments, waveform, source_path=path)
        proj_state = {
            "timeline":   timeline,
            "path":       path,
            "duration_s": duration_s,
            "analysis":   analysis,
        }
        response = _timeline_to_response(timeline, path)

        # Detect codec + start proxy in background (non-blocking — doesn't delay response)
        codec, proxy_status, proxy_path = await loop.run_in_executor(
            None, lambda: _start_proxy_if_needed(path)
        )
        response["video_codec"]   = codec
        response["proxy_status"]  = proxy_status
        response["proxy_path"]    = proxy_path

        # Store in server state and cache
        with _project_lock:
            _current_project = proj_state

        # Evict oldest entry if cache is full
        if len(_analysis_cache) >= _CACHE_MAX:
            oldest = next(iter(_analysis_cache))
            del _analysis_cache[oldest]
        _analysis_cache[ck] = {"project": proj_state, "response": response}

        return response
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")

@app.post("/api/analyze-video")
async def analyze_video_only(req: OpenProjectRequest):
    """Analyse a video and return clip data WITHOUT replacing the current project.

    Used by the multi-video feature: the frontend appends the returned clips
    to the existing timeline at the requested offset.
    """
    path = os.path.normpath(req.path)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {path}")

    # Check analysis cache first
    ck = _cache_key(path, req.silence_style, req.auto_cut)
    if ck in _analysis_cache:
        cached_proj = _analysis_cache[ck]["project"]
        tc_list = cached_proj["timeline"].video_track.clips
        cached_wf = _analysis_cache[ck]["response"].get("waveform", [])
        # Re-check proxy status (may have changed since last analyze)
        codec, proxy_status, proxy_path = _start_proxy_if_needed(path)
        return {
            "video_path":   path,
            "duration_s":   cached_proj["duration_s"],
            "clips": [{"start_s": c.start_s, "end_s": c.end_s, "source_path": path, "label": c.label}
                      for c in tc_list],
            "waveform":     cached_wf,
            "video_codec":  codec,
            "proxy_status": proxy_status,
            "proxy_path":   proxy_path,
        }

    try:
        from src.core.audio_waveform import extract_waveform
        from src.core.analyzer import analyze_video, _get_duration
        from src.config import ProcessingConfig

        cfg = ProcessingConfig()
        loop = asyncio.get_event_loop()

        if not req.auto_cut:
            duration_s = await loop.run_in_executor(None, lambda: _get_duration(path))
            waveform = await loop.run_in_executor(
                None, lambda: extract_waveform(path, duration_s=duration_s, bins=500)
            )
            segments = [(0.0, duration_s)]
        else:
            style_params = _STYLE_PARAMS.get(req.silence_style, _STYLE_PARAMS["natural"])

            # O1: parallel analysis + waveform
            dur_probe = await loop.run_in_executor(None, lambda: _get_duration(path))
            analysis_future = loop.run_in_executor(
                None,
                lambda: analyze_video(
                    path,
                    silence_threshold_db=style_params["silence_threshold_db"],
                    min_silence_ms=style_params["min_silence_ms"],
                    audio_padding_ms=cfg.audio_padding_ms,
                    min_segment_s=cfg.min_segment_s,
                )
            )
            waveform_future = loop.run_in_executor(
                None,
                lambda: extract_waveform(path, duration_s=dur_probe, bins=500)
            )
            analysis, waveform = await asyncio.gather(analysis_future, waveform_future)
            duration_s = analysis.duration_s
            segments   = analysis.speech_segments or [(0.0, duration_s)]

        # Friendly label: use file basename instead of generic "Clip N" when there's only one segment.
        base_label = os.path.splitext(os.path.basename(path))[0]
        clips = [
            {
                "start_s":     s,
                "end_s":       e,
                "source_path": path,
                "label":       base_label if len(segments) == 1 else f"{base_label} ({i+1})",
            }
            for i, (s, e) in enumerate(segments)
        ]
        # Also kick off proxy transcode for HEVC/VP9/AV1 — without this, the
        # frontend's <video> element gets a black preview when the appended
        # source uses a codec WebView2 can't decode natively.
        codec, proxy_status, proxy_path = await loop.run_in_executor(
            None, lambda: _start_proxy_if_needed(path)
        )
        return {
            "video_path":   path,
            "duration_s":   duration_s,
            "clips":        clips,
            "waveform":     (waveform.samples if waveform else [])[:500],
            "video_codec":  codec,
            "proxy_status": proxy_status,
            "proxy_path":   proxy_path,
        }
    except HTTPException:
        raise
    except Exception as e:
        import traceback
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}\n{traceback.format_exc()}")


class TranscribeRequest(BaseModel):
    path: str
    language: Optional[str] = None   # ISO-639-1 (pt, en…); None = auto-detect
    provider: str = "auto"


@app.post("/api/transcribe")
async def transcribe_endpoint(req: TranscribeRequest):
    """Run speech-to-text on the video. Returns list of timed segments."""
    path = os.path.normpath(req.path)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail=f"Arquivo não encontrado: {path}")
    try:
        from src.core.transcriber import transcribe_video, TranscriptionUnavailable, available_providers
        from src.api_settings import load_env_file
        # Resolve OpenAI key from env (preferred) or .env file (fallback). Without this
        # the transcriber would only see env vars and miss the .env that the project
        # ships with — same convention used elsewhere via load_api_credentials.
        openai_key = os.environ.get("OPENAI_API_KEY") or load_env_file().get("OPENAI_API_KEY", "")
        providers = available_providers()
        if not providers and not openai_key:
            raise HTTPException(
                status_code=503,
                detail=(
                    "Nenhum provider de transcrição disponível. Instale: "
                    "pip install faster-whisper  OU defina OPENAI_API_KEY"
                ),
            )
        loop = asyncio.get_event_loop()
        transcript = await loop.run_in_executor(
            None,
            lambda: transcribe_video(
                path,
                provider=req.provider,
                language=req.language,
                openai_api_key=openai_key or None,
            ),
        )
        return {
            "language": transcript.language,
            "provider": transcript.provider,
            "segments": [
                {"start_s": s.start_s, "end_s": s.end_s, "text": s.text}
                for s in transcript.segments
            ],
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"{type(e).__name__}: {e}")


@app.get("/api/transcribe-status")
async def transcribe_status():
    """Report which transcription providers are available right now."""
    try:
        from src.core.transcriber import available_providers
        return {"providers": available_providers()}
    except Exception as e:
        return {"providers": [], "error": str(e)}


@app.get("/api/project")
async def get_project():
    with _project_lock:
        if _current_project is None:
            return {"loaded": False}
        return _timeline_to_response(_current_project["timeline"], _current_project["path"])


class TrackOperationRequest(BaseModel):
    type: str          # "video" | "audio" | "overlay"
    index: Optional[int] = None   # required for remove


@app.post("/api/add-track")
def add_track(req: TrackOperationRequest):
    """Add a parallel extra track of the given type. Returns the updated project."""
    with _project_lock:
        if _current_project is None:
            raise HTTPException(status_code=400, detail="Nenhum projeto carregado")
        timeline = _current_project["timeline"]
        if req.type == "video":
            timeline.add_video_track()
        elif req.type == "audio":
            timeline.add_audio_track()
        elif req.type == "overlay":
            timeline.add_overlay_track()
        else:
            raise HTTPException(status_code=400, detail=f"Tipo de faixa inválido: {req.type}")
        return _timeline_to_response(timeline, _current_project["path"])


@app.post("/api/remove-track")
def remove_track(req: TrackOperationRequest):
    """Remove the Nth extra track of a given type (0-based; main track cannot be removed)."""
    if req.index is None:
        raise HTTPException(status_code=400, detail="index é obrigatório para remoção")
    with _project_lock:
        if _current_project is None:
            raise HTTPException(status_code=400, detail="Nenhum projeto carregado")
        timeline = _current_project["timeline"]
        ok = False
        if req.type == "video":
            ok = timeline.remove_video_track(req.index)
        elif req.type == "audio":
            ok = timeline.remove_audio_track(req.index)
        elif req.type == "overlay":
            ok = timeline.remove_overlay_track(req.index)
        else:
            raise HTTPException(status_code=400, detail=f"Tipo de faixa inválido: {req.type}")
        if not ok:
            raise HTTPException(status_code=404, detail=f"Faixa {req.type}[{req.index}] não encontrada")
        return _timeline_to_response(timeline, _current_project["path"])

@app.get("/api/thumb")
async def get_thumbnail(path: str, t: float = 0.0, w: int = 120):
    """Extract a single video frame at time `t` seconds, return as JPEG.
    Uses ffmpeg seek + scale filter; result is cached in memory."""
    import hashlib
    cache_key = hashlib.md5(f"{path}:{t:.3f}:{w}".encode()).hexdigest()
    if cache_key in _thumb_cache:
        return Response(content=_thumb_cache[cache_key], media_type="image/jpeg")

    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")

    from src.ffmpeg_env import ffmpeg as ffmpeg_bin
    import subprocess
    cmd = [
        ffmpeg_bin(), "-ss", f"{t:.3f}", "-i", path,
        "-vframes", "1",
        "-vf", f"scale={w}:-2",
        "-f", "image2", "-vcodec", "mjpeg", "-q:v", "5",
        "pipe:1",
    ]
    result = subprocess.run(cmd, capture_output=True, timeout=10)
    if result.returncode != 0 or not result.stdout:
        raise HTTPException(status_code=500, detail="Frame extraction failed")

    _thumb_cache[cache_key] = result.stdout
    if len(_thumb_cache) > 500:  # simple LRU eviction
        oldest = next(iter(_thumb_cache))
        del _thumb_cache[oldest]

    return Response(
        content=result.stdout,
        media_type="image/jpeg",
        headers={"Cache-Control": "public, max-age=86400"},
    )

_thumb_cache: dict[str, bytes] = {}

@app.get("/api/serve-file")
async def serve_file(request: Request, path: str):
    """Stream a local file with HTTP Range support (required for video seeking)."""
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File not found")

    file_size = os.path.getsize(path)
    mime, _ = mimetypes.guess_type(path)
    mime = mime or "application/octet-stream"

    range_header = request.headers.get("range")

    if range_header:
        # Parse "bytes=start-end"
        try:
            range_val = range_header.replace("bytes=", "")
            parts = range_val.split("-")
            start = int(parts[0]) if parts[0] else 0
            end = int(parts[1]) if len(parts) > 1 and parts[1] else file_size - 1
        except (ValueError, IndexError):
            start, end = 0, file_size - 1

        # Clamp
        start = max(0, min(start, file_size - 1))
        end   = max(start, min(end, file_size - 1))
        chunk_size = end - start + 1

        def iter_range():
            with open(path, "rb") as f:
                f.seek(start)
                remaining = chunk_size
                while remaining > 0:
                    data = f.read(min(65536, remaining))
                    if not data:
                        break
                    remaining -= len(data)
                    yield data

        return StreamingResponse(
            iter_range(),
            status_code=206,
            media_type=mime,
            headers={
                "Content-Range":  f"bytes {start}-{end}/{file_size}",
                "Accept-Ranges":  "bytes",
                "Content-Length": str(chunk_size),
            },
        )

    # Full file
    def iter_file():
        with open(path, "rb") as f:
            yield from iter(lambda: f.read(65536), b"")

    return StreamingResponse(
        iter_file(),
        media_type=mime,
        headers={
            "Accept-Ranges":  "bytes",
            "Content-Length": str(file_size),
        },
    )

@app.websocket("/ws/render")
async def ws_render(ws: WebSocket):
    await ws.accept()
    cancel_event = threading.Event()
    try:
        data = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
        output_path:    str        = data.get("output_path",  "")
        # Per-clip edits sent by the frontend (may include splits/trims/Inspector changes)
        frontend_clips: list[dict] = data.get("clips",        [])
        # Optional background music track from audio import
        music_path:     str        = data.get("music_path",   "") or ""
        # Tracks the user has muted in the timeline header
        muted_tracks:   list[str]  = data.get("muted_tracks", []) or []
        # Text/caption clips from text_track (burnt-in with drawtext)
        text_clips:     list[dict] = data.get("text_clips",   []) or []
        # Image overlay clips from overlay_track (burnt-in with ffmpeg overlay filter)
        image_clips:    list[dict] = data.get("image_clips",  []) or []
        # Export quality (H.264 CRF — lower = better quality)
        export_crf:     int        = int(data.get("crf", 18))
        # ffmpeg encoder preset (speed/compression trade-off)
        export_preset:  str        = str(data.get("preset", "fast"))
        # Audio normalization toggle
        normalize_audio: bool      = bool(data.get("normalize_audio", True))
        # Target platform — legacy aspect-ratio crop (youtube=16:9, reels/tiktok/shorts=9:16)
        export_platform: str       = str(data.get("platform", "youtube"))
        # New: explicit aspect-ratio overrides the legacy platform when set
        export_aspect_ratio = data.get("aspect_ratio")   # str or None

        with _project_lock:
            proj = _current_project

        if not proj:
            await ws.send_json({"type": "error", "detail": "Nenhum projeto carregado"})
            return
        if not output_path:
            await ws.send_json({"type": "error", "detail": "Caminho de saída não informado"})
            return

        timeline  = proj["timeline"]
        video_path: str   = proj["path"]
        duration_s: float = proj.get("duration_s", timeline.duration_s)

        # Build segments + per_clip from frontend clips when available (handles splits/trims).
        # TimelineClip has no id field, so we match purely by position / frontend order.
        _CLIP_DEFAULTS = dict(
            speed_factor=1.0, transition="Corte", transition_duration_s=0.4,
            volume_pct=100.0, pan_pct=0.0, fade_in_s=0.0, fade_out_s=0.0,
            brightness=0.0, contrast=0.0, saturation=0.0,
            crop_top_pct=0.0, crop_bottom_pct=0.0, crop_left_pct=0.0, crop_right_pct=0.0,
            scale_pct=100.0, opacity_pct=100.0, rotation_deg=0.0,
            text_overlay="", text_position_x_pct=0.0, text_position_y_pct=72.0,
            text_size_pct=100.0, text_color="#ffffff", text_bold=False,
            text_italic=False, text_align="center",
            chroma_enabled=False, chroma_color="#00ff00", chroma_tolerance=45.0,
        )

        if frontend_clips:
            valid_fe = [c for c in frontend_clips if "start_s" in c and "end_s" in c]
            segments    = [(c["start_s"], c["end_s"]) for c in valid_fe]
            per_clip    = [
                {k: c.get(k, v) for k, v in _CLIP_DEFAULTS.items()}
                for c in valid_fe
            ]
            # Multi-source: per-clip source file path and project-time offset
            source_paths   = [c.get("source_path") or None for c in valid_fe]
            source_offsets = [float(c.get("source_offset_s") or 0.0) for c in valid_fe]
        else:
            # Fallback: reconstruct from server-side timeline (no frontend data)
            tc_list = timeline.video_track.clips
            segments = [(c.start_s, c.end_s) for c in tc_list]
            per_clip = [
                {k: getattr(c, k, v) for k, v in _CLIP_DEFAULTS.items()}
                for c in tc_list
            ]
            source_paths   = [getattr(c, "source_path", None) for c in tc_list]
            source_offsets = [float(getattr(c, "source_offset_s", 0.0) or 0.0) for c in tc_list]

        if not segments:
            await ws.send_json({"type": "error", "detail": "Nenhum clipe na timeline"})
            return

        # Apply muted tracks: if audio track is muted, zero out speech audio + skip music
        if "audio" in muted_tracks:
            for clip_d in per_clip:
                clip_d["volume_pct"] = 0.0
            music_path = ""  # don't mix background music either

        loop = asyncio.get_event_loop()
        progress_queue: asyncio.Queue = asyncio.Queue()

        def on_progress(msg: str, pct: float):
            asyncio.run_coroutine_threadsafe(
                progress_queue.put({"type": "progress", "value": round(pct * 100, 1), "message": msg}),
                loop,
            )

        def run_render():
            try:
                from src.core.analyzer import AudioAnalysis
                from src.core.editor import cut_silence

                silence_total = sum(
                    e - s for s, e in timeline.removed_ranges
                ) if timeline.removed_ranges else 0.0
                silence_ratio = silence_total / duration_s if duration_s > 0 else 0.0

                analysis = AudioAnalysis(
                    duration_s=duration_s,
                    speech_segments=segments,
                    silence_ratio=silence_ratio,
                )

                cut_silence(
                    video_path=video_path,
                    analysis=analysis,
                    output_path=output_path,
                    on_progress=on_progress,
                    per_clip_data=per_clip,
                    cancel=cancel_event,
                    music_path=music_path if music_path and os.path.isfile(music_path) else None,
                    text_clips=text_clips if text_clips else None,
                    image_clips=image_clips if image_clips else None,
                    crf=export_crf,
                    preset=export_preset,
                    normalize_audio=normalize_audio,
                    platform=export_platform,
                    aspect_ratio=export_aspect_ratio,
                    source_paths=source_paths,
                    source_offsets=source_offsets,
                )
                asyncio.run_coroutine_threadsafe(
                    progress_queue.put({"type": "done", "path": output_path}),
                    loop,
                )
            except Exception as exc:
                import traceback
                asyncio.run_coroutine_threadsafe(
                    progress_queue.put({"type": "error", "detail": str(exc), "traceback": traceback.format_exc()}),
                    loop,
                )

        thread = threading.Thread(target=run_render, daemon=True, name="CortaCertoRender")
        thread.start()

        # Pump progress messages to WebSocket.
        # Uses a short 10s poll so we send keep-alive pings frequently enough
        # to prevent proxy/WebView2 from closing the connection during long renders
        # (a 1-hour export can take minutes between ffmpeg progress updates).
        while True:
            try:
                msg = await asyncio.wait_for(progress_queue.get(), timeout=10.0)
                await ws.send_json(msg)
                if msg.get("type") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                # Keep-alive ping — tell the frontend we're still rendering
                await ws.send_json({"type": "ping"})

    except WebSocketDisconnect:
        cancel_event.set()
    except asyncio.TimeoutError:
        await ws.send_json({"type": "error", "detail": "Timeout aguardando dados de render"})
    except Exception as exc:
        try:
            await ws.send_json({"type": "error", "detail": str(exc)})
        except Exception:
            pass

# -- Server runner -------------------------------------------------------------

def _free_port(port: int) -> None:
    """Kill any process currently listening on *port* (Windows + Unix)."""
    import socket
    import subprocess

    # Quick check: is the port actually in use?
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        in_use = s.connect_ex(("127.0.0.1", port)) == 0
    if not in_use:
        return

    print(f"[STARTUP] Porta {port} ocupada — encerrando processo anterior…")

    # Windows: netstat -ano | findstr LISTENING
    if os.name == "nt":
        try:
            out = subprocess.check_output(
                ["netstat", "-ano"],
                text=True, stderr=subprocess.DEVNULL, timeout=6,
            )
            for line in out.splitlines():
                if f":{port}" in line and "LISTENING" in line:
                    pid = line.split()[-1]
                    subprocess.run(
                        ["taskkill", "/F", "/PID", pid],
                        capture_output=True, timeout=5,
                    )
                    time.sleep(0.6)
                    break
        except Exception as exc:
            print(f"[STARTUP] Aviso: não foi possível encerrar processo na porta {port}: {exc}")
    else:
        # Unix: fuser / lsof
        try:
            subprocess.run(
                ["fuser", "-k", f"{port}/tcp"],
                capture_output=True, timeout=5,
            )
            time.sleep(0.4)
        except Exception:
            try:
                out = subprocess.check_output(
                    ["lsof", "-ti", f"tcp:{port}"],
                    text=True, stderr=subprocess.DEVNULL, timeout=5,
                )
                for pid in out.split():
                    subprocess.run(["kill", "-9", pid.strip()], capture_output=True)
                time.sleep(0.4)
            except Exception:
                pass


_uvicorn_server: uvicorn.Server | None = None


def stop_server() -> None:
    """Gracefully stop the uvicorn server (called on window close)."""
    global _uvicorn_server
    if _uvicorn_server is not None:
        _uvicorn_server.should_exit = True


def _silence_asyncio_connection_reset(loop: asyncio.AbstractEventLoop) -> None:
    """Install a loop-level exception handler that swallows ConnectionResetError.

    Used as a defence-in-depth alongside the logging filter (which catches
    most cases). Kept as a separate, testable function.
    """
    default_handler = loop.get_exception_handler()

    def handler(_loop, context):
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
            return
        msg = context.get("message", "")
        if "WinError 10054" in msg or "WinError 10053" in msg:
            return
        if default_handler is not None:
            default_handler(_loop, context)
        else:
            _loop.default_exception_handler(context)

    loop.set_exception_handler(handler)


def _install_global_connection_reset_filter() -> None:
    """Install a logging filter that drops ConnectionReset noise process-wide.

    The asyncio `default_exception_handler` ultimately calls
    `logger.error(msg, exc_info=exc_info)`. The MESSAGE never contains
    'ConnectionResetError' — only the formatted traceback does — so a naive
    message-text filter never matches. We check `record.exc_info` instead.

    This runs at module-import time so it's active before uvicorn starts its
    event loop (unlike the per-loop handler which only catches loops created
    via the patched path).
    """
    import logging

    class _ConnResetFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            # record.exc_info = (type, value, traceback) when the log was made
            # with logger.error(msg, exc_info=...) — asyncio does exactly that.
            if record.exc_info:
                exc = record.exc_info[1]
                if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)):
                    return False
            msg = record.getMessage()
            if "WinError 10054" in msg or "WinError 10053" in msg:
                return False
            # Sometimes the wrapper message itself starts with "Exception in callback"
            # and the real error is in exc_text already formatted by the handler
            if record.exc_text and (
                "ConnectionResetError" in record.exc_text
                or "ConnectionAbortedError" in record.exc_text
                or "WinError 10054" in record.exc_text
                or "WinError 10053" in record.exc_text
            ):
                return False
            return True

    # Attach to every logger that might emit the noise. asyncio uses
    # logging.getLogger("asyncio"). Uvicorn's protocol loggers also pass it through.
    for name in ("asyncio", "uvicorn", "uvicorn.error", "uvicorn.access"):
        logging.getLogger(name).addFilter(_ConnResetFilter())


# Install the filter immediately at import time
_install_global_connection_reset_filter()


def run_server(host: str = "127.0.0.1", port: int = 7472):
    """Start uvicorn in a background daemon thread.

    Automatically frees the port if a previous instance is still running.
    """
    global _uvicorn_server

    _free_port(port)

    config = uvicorn.Config(app, host=host, port=port, log_level="warning")
    server = uvicorn.Server(config)
    _uvicorn_server = server

    t = threading.Thread(target=server.run, daemon=True, name="CortaCertoAPI")
    t.start()
    # Wait until server is ready (up to 5 s)
    import urllib.request
    for _ in range(50):
        try:
            urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    return t
