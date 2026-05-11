from __future__ import annotations

import queue
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Optional

import cv2
from PIL import Image

from .color_grade import ColorGrade
from .subject_tracking import SubjectTracker
from .video_effects import apply_video_effects_bgr


@dataclass(frozen=True)
class PreviewSettings:
    color_grade: ColorGrade
    bokeh_intensity: float
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
            round(float(self.bokeh_intensity), 3),
        )


@dataclass
class PreviewFrame:
    frame_index: int
    image: Image.Image
    render_ms: float
    backend: str
    settings_key: tuple


class PreviewEngine:
    def __init__(
        self,
        on_frame_ready: Callable[[PreviewFrame], None],
        cache_size: int = 24,
    ) -> None:
        self.on_frame_ready = on_frame_ready
        self.cache_size = cache_size
        self._requests: queue.Queue[tuple[int, PreviewSettings, int]] = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()
        self._opened = threading.Event()
        self._path: Optional[str] = None
        self._cache: OrderedDict[tuple[int, tuple], PreviewFrame] = OrderedDict()
        self._tracker = SubjectTracker()
        self._cap = None
        self._last_frame_index: Optional[int] = None
        self._version = 0
        self._state_lock = threading.Lock()
        self._meta_lock = threading.Lock()
        self.total_frames = 0
        self.fps = 30.0
        self.duration_s = 0.0
        self._worker.start()

    def open(self, path: str) -> None:
        self.close()
        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            raise RuntimeError(f"Não foi possível abrir o vídeo para preview: {path}")
        with self._state_lock:
            self._version += 1
            self._cap = cap
            self._path = path
            self._tracker.reset()
            self._last_frame_index = None
        with self._meta_lock:
            self.total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
            self.fps = max(1.0, cap.get(cv2.CAP_PROP_FPS))
            self.duration_s = self.total_frames / self.fps if self.total_frames > 0 else 0.0
        with self._state_lock:
            self._cache.clear()
        self._opened.set()

    def close(self) -> None:
        self._opened.clear()
        self._drain_requests()
        with self._state_lock:
            self._version += 1
            self._tracker.reset()
            self._cache.clear()
            if self._cap is not None:
                self._cap.release()
                self._cap = None
            self._last_frame_index = None

    def stop(self) -> None:
        self._stop.set()
        self._opened.set()
        self.close()
        self._worker.join(timeout=2)

    def _drain_requests(self) -> None:
        try:
            while True:
                self._requests.get_nowait()
        except queue.Empty:
            pass

    def request_frame(self, frame_index: int, settings: PreviewSettings) -> None:
        cache_key = (frame_index, settings.cache_key())
        with self._state_lock:
            version = self._version
            cached = self._cache.get(cache_key)
            if cached is not None:
                self._cache.move_to_end(cache_key)
        if cached is not None:
            self.on_frame_ready(cached)
            return
        self._requests.put((frame_index, settings, version))

    def _run(self) -> None:
        while not self._stop.is_set():
            self._opened.wait(timeout=0.2)
            if not self._opened.is_set() or self._cap is None:
                continue

            try:
                frame_index, settings, version = self._requests.get(timeout=0.2)
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
            ok, frame_bgr = self._read_frame(frame_index)
            if not ok or frame_bgr is None:
                continue

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
            preview = PreviewFrame(
                frame_index=frame_index,
                image=image,
                render_ms=(time.monotonic() - started) * 1000.0,
                backend=backend,
                settings_key=settings.cache_key(),
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

    def _read_frame(self, frame_index: int) -> tuple[bool, object]:
        with self._state_lock:
            if self._cap is None:
                return False, None
            if self._last_frame_index is None or frame_index != self._last_frame_index + 1:
                self._cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame_bgr = self._cap.read()
            if ok:
                self._last_frame_index = frame_index
            return ok, frame_bgr
