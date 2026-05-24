"""Preview engine — async frame decoder with three-layer cache.

Architecture
───────────────────────────────────────────────────────────────
  Main thread ──request_frame()──► request queue
  Worker thread ──reads from queue──► raw frame cache → effects → rendered cache ──► on_frame_ready()
  Prefetch thread ──reads from prefetch queue──► pre-warms raw cache for adjacent frames

Three caches
  1. rendered_cache  – (frame_idx, settings_key) → PreviewFrame with PIL image (LRU, 64 entries)
  2. raw_cache       – frame_idx → BGR ndarray (LRU, 48 entries) — avoids re-seeking/decoding
  3. prefetch_queue  – frame indices to warm into raw_cache in background

Scrub mode
  PreviewSettings.proxy_scale < 1.0 → frame is downscaled before effects are applied,
  giving a faster render at lower resolution during rapid timeline scrubbing.
"""
from __future__ import annotations

import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image

from .color_grade import ColorGrade
from .subject_tracking import SubjectTracker
from .video_effects import apply_video_effects_bgr


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PreviewSettings:
    color_grade: ColorGrade
    bokeh_intensity: float
    proxy_scale: float = 1.0          # 1.0 = full-res; 0.5 = half during scrub
    request_token: tuple = ()

    def cache_key(self) -> tuple:
        grade = self.color_grade
        return (
            self.request_token,
            bool(grade.enabled),
            round(float(grade.temperature), 2),
            round(float(getattr(grade, "tint", 0.0)), 2),
            round(float(grade.hue), 2),
            round(float(grade.saturation), 2),
            round(float(getattr(grade, "vibrance", 0.0)), 2),
            round(float(grade.contrast), 2),
            round(float(grade.brightness), 2),
            round(float(grade.shadows), 2),
            round(float(grade.highlights), 2),
            round(float(grade.whites), 2),
            round(float(grade.blacks), 2),
            round(float(grade.sharpen), 2),
            # Color wheels (lift / gamma / gain per channel)
            round(float(getattr(grade, "lift_r",  0.0)), 2),
            round(float(getattr(grade, "lift_g",  0.0)), 2),
            round(float(getattr(grade, "lift_b",  0.0)), 2),
            round(float(getattr(grade, "gamma_r", 0.0)), 2),
            round(float(getattr(grade, "gamma_g", 0.0)), 2),
            round(float(getattr(grade, "gamma_b", 0.0)), 2),
            round(float(getattr(grade, "gain_r",  0.0)), 2),
            round(float(getattr(grade, "gain_g",  0.0)), 2),
            round(float(getattr(grade, "gain_b",  0.0)), 2),
            # 3D LUT
            str(getattr(grade, "lut_path", "")),
            # Proxy / bokeh
            round(float(self.bokeh_intensity), 3),
            round(float(self.proxy_scale), 2),
        )


@dataclass
class PreviewFrame:
    frame_index: int
    image: Image.Image
    render_ms: float
    backend: str
    settings_key: tuple
    is_proxy: bool = False            # True when rendered at reduced resolution


# ── Engine ───────────────────────────────────────────────────────────────────

class PreviewEngine:
    """Async video preview with raw-frame cache, prefetch, and proxy-scale support."""

    def __init__(
        self,
        on_frame_ready: Callable[[PreviewFrame], None],
        cache_size: int = 64,        # rendered LRU size  (was 24)
        raw_cache_size: int = 48,    # decoded BGR LRU size (new)
        prefetch_ahead: int = 3,     # frames to prefetch forward
    ) -> None:
        self.on_frame_ready = on_frame_ready
        self.cache_size = cache_size
        self.raw_cache_size = raw_cache_size
        self.prefetch_ahead = prefetch_ahead

        # Rendered-frame LRU: (frame_idx, settings_key) → PreviewFrame
        self._cache: OrderedDict[tuple, PreviewFrame] = OrderedDict()
        # Raw-frame LRU: frame_idx → BGR ndarray
        self._raw_cache: OrderedDict[int, np.ndarray] = OrderedDict()

        self._requests: queue.Queue[tuple[int, PreviewSettings, int]] = queue.Queue()
        self._prefetch_q: queue.Queue[tuple[int, int]] = queue.Queue(maxsize=8)

        self._stop = threading.Event()
        self._opened = threading.Event()
        self._path: Optional[str] = None
        self._tracker = SubjectTracker()

        # Two separate cv2 captures: worker + prefetch so they don't fight over seeks
        self._cap: Optional[cv2.VideoCapture] = None
        self._pf_cap: Optional[cv2.VideoCapture] = None

        self._last_frame_index: Optional[int] = None
        self._pf_last_frame_index: Optional[int] = None
        self._version = 0

        self._state_lock = threading.Lock()
        self._raw_lock = threading.Lock()
        self._meta_lock = threading.Lock()

        self.total_frames = 0
        self.fps = 30.0
        self.duration_s = 0.0

        self._worker = threading.Thread(target=self._run, daemon=True, name="preview-worker")
        self._prefetch_worker = threading.Thread(target=self._run_prefetch, daemon=True, name="preview-prefetch")
        self._worker.start()
        self._prefetch_worker.start()

    # ── Public API ────────────────────────────────────────────────────────────

    def open(self, path: str) -> None:
        self.close()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir o vídeo para preview: {path}")
        pf_cap = cv2.VideoCapture(path)  # separate handle for prefetch

        with self._state_lock:
            self._version += 1
            self._cap = cap
            self._pf_cap = pf_cap if pf_cap.isOpened() else None
            self._path = path
            self._tracker.reset()
            self._last_frame_index = None
            self._pf_last_frame_index = None
            self._cache.clear()

        with self._raw_lock:
            self._raw_cache.clear()

        with self._meta_lock:
            self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fps = max(1.0, cap.get(cv2.CAP_PROP_FPS))
            self.duration_s = self.total_frames / self.fps if self.total_frames > 0 else 0.0

        self._opened.set()

    def close(self) -> None:
        self._opened.clear()
        self._drain_queues()
        with self._state_lock:
            self._version += 1
            self._tracker.reset()
            self._cache.clear()
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            if self._pf_cap is not None:
                self._pf_cap.release()
                self._pf_cap = None
            self._last_frame_index = None
            self._pf_last_frame_index = None
        with self._raw_lock:
            self._raw_cache.clear()

    def stop(self) -> None:
        self._stop.set()
        self._opened.set()
        self.close()
        self._worker.join(timeout=2)
        self._prefetch_worker.join(timeout=2)

    def request_frame(self, frame_index: int, settings: PreviewSettings) -> None:
        """Request a rendered frame; serves from cache instantly if available."""
        cache_key = (frame_index, settings.cache_key())
        with self._state_lock:
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
        if cached is not None:
            self.on_frame_ready(cached)
            return
        with self._state_lock:
            version = self._version
        self._requests.put((frame_index, settings, version))

    def prefetch_frames(self, frame_indices: list[int]) -> None:
        """Hint that these frames will be needed soon (warms raw cache)."""
        with self._state_lock:
            version = self._version
        for fi in frame_indices:
            if fi < 0 or fi >= self.total_frames:
                continue
            with self._raw_lock:
                if fi in self._raw_cache:
                    continue
            try:
                self._prefetch_q.put_nowait((fi, version))
            except queue.Full:
                break

    # ── Background workers ────────────────────────────────────────────────────

    def _drain_queues(self) -> None:
        for q in (self._requests, self._prefetch_q):
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass

    def _run(self) -> None:
        """Main render worker — processes requests, applies effects."""
        while not self._stop.is_set():
            self._opened.wait(timeout=0.2)
            if not self._opened.is_set() or self._cap is None:
                continue

            try:
                frame_index, settings, version = self._requests.get(timeout=0.2)
                # Drain: keep only the latest request (skip intermediate seeks)
                while True:
                    try:
                        frame_index, settings, version = self._requests.get_nowait()
                    except queue.Empty:
                        break
            except queue.Empty:
                continue

            if self._cap is None:
                continue

            started = time.monotonic()

            # ── 1. decode raw frame ───────────────────────────────────────────
            ok, frame_bgr = self._read_frame(frame_index)
            if not ok or frame_bgr is None:
                continue

            # ── 2. optional proxy downscale ───────────────────────────────────
            is_proxy = False
            if settings.proxy_scale < 0.99:
                h, w = frame_bgr.shape[:2]
                nw = max(4, int(w * settings.proxy_scale))
                nh = max(4, int(h * settings.proxy_scale))
                frame_bgr = cv2.resize(frame_bgr, (nw, nh), interpolation=cv2.INTER_LINEAR)
                is_proxy = True

            # ── 3. apply color grade + effects ────────────────────────────────
            with self._state_lock:
                rendered, subject = apply_video_effects_bgr(
                    frame_bgr,
                    grade=settings.color_grade,
                    bokeh_intensity=settings.bokeh_intensity,
                    tracker=self._tracker,
                )

            rgb = cv2.cvtColor(rendered, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            backend = subject.backend if subject is not None else "color"
            if is_proxy:
                backend = f"{backend}·proxy"

            preview = PreviewFrame(
                frame_index=frame_index,
                image=image,
                render_ms=(time.monotonic() - started) * 1000.0,
                backend=backend,
                settings_key=settings.cache_key(),
                is_proxy=is_proxy,
            )

            cache_key = (frame_index, settings.cache_key())
            with self._state_lock:
                if version != self._version:
                    continue
                self._cache[cache_key] = preview
                self._cache.move_to_end(cache_key)
                while len(self._cache) > self.cache_size:
                    self._cache.popitem(last=False)

            self.on_frame_ready(preview)

            # ── 4. auto-prefetch next frames after non-proxy render ────────────
            if not is_proxy and not self._stop.is_set():
                with self._state_lock:
                    cur_version = self._version
                for delta in range(1, self.prefetch_ahead + 1):
                    nf = frame_index + delta
                    if nf < self.total_frames:
                        with self._raw_lock:
                            already_cached = nf in self._raw_cache
                        if not already_cached:
                            try:
                                self._prefetch_q.put_nowait((nf, cur_version))
                            except queue.Full:
                                break

    def _run_prefetch(self) -> None:
        """Prefetch worker — only warms raw frame cache, never calls on_frame_ready."""
        while not self._stop.is_set():
            try:
                frame_index, version = self._prefetch_q.get(timeout=0.3)
            except queue.Empty:
                continue

            with self._state_lock:
                if version != self._version:
                    continue
                pf_cap = self._pf_cap
            if pf_cap is None:
                continue

            with self._raw_lock:
                if frame_index in self._raw_cache:
                    continue  # already warmed

            # Seek and decode (using dedicated capture handle)
            try:
                if self._pf_last_frame_index is None or frame_index != self._pf_last_frame_index + 1:
                    pf_cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
                ok, frame_bgr = pf_cap.read()
                if ok and frame_bgr is not None:
                    self._pf_last_frame_index = frame_index
                    with self._raw_lock:
                        self._raw_cache[frame_index] = frame_bgr
                        self._raw_cache.move_to_end(frame_index)
                        while len(self._raw_cache) > self.raw_cache_size:
                            self._raw_cache.popitem(last=False)
            except Exception:
                pass

    # ── Frame reading (main worker only) ─────────────────────────────────────

    def _read_frame(self, frame_index: int) -> tuple[bool, Optional[np.ndarray]]:
        """Read a frame — checks raw cache first, then falls back to cv2."""
        # 1. raw cache hit
        with self._raw_lock:
            if frame_index in self._raw_cache:
                frame = self._raw_cache[frame_index].copy()
                self._raw_cache.move_to_end(frame_index)
                return True, frame

        # 2. decode from disk
        with self._state_lock:
            if self._cap is None:
                return False, None
            if self._last_frame_index is None or frame_index != self._last_frame_index + 1:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame_bgr = self._cap.read()
            if ok:
                self._last_frame_index = frame_index

        if not ok or frame_bgr is None:
            return False, None

        # 3. store in raw cache
        with self._raw_lock:
            self._raw_cache[frame_index] = frame_bgr.copy()
            self._raw_cache.move_to_end(frame_index)
            while len(self._raw_cache) > self.raw_cache_size:
                self._raw_cache.popitem(last=False)

        return True, frame_bgr
