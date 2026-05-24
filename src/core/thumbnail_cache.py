"""Asynchronous video thumbnail generator with thread-safe LRU cache.

Usage::
    cache = ThumbnailCache(on_ready=lambda: root.after(0, redraw))
    img = cache.get(filepath, time_s, width, height)   # None if not ready yet
"""
from __future__ import annotations

import threading
import queue
from typing import Callable, Optional

try:
    import cv2
    import numpy as np
    from PIL import Image
    _CV2_OK = True
except ImportError:
    _CV2_OK = False

_CACHE_MAX = 512   # max cached thumbnails


class ThumbnailCache:
    """Thread-safe LRU thumbnail cache with background extraction."""

    def __init__(self, on_ready: Optional[Callable[[], None]] = None) -> None:
        self._cache: dict[tuple, "Image.Image"] = {}
        self._order: list[tuple] = []          # LRU order — oldest first
        self._pending: set[tuple] = set()
        self._lock = threading.Lock()
        self._queue: queue.Queue = queue.Queue()
        self._on_ready = on_ready
        self._worker = threading.Thread(target=self._work, daemon=True)
        self._worker.start()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get(
        self,
        filepath: str,
        time_s: float,
        width: int,
        height: int,
    ) -> "Optional[Image.Image]":
        """Return cached thumbnail or None (queues background extraction)."""
        key = (filepath, round(time_s, 3), width, height)
        with self._lock:
            if key in self._cache:
                # Move to end (most recently used)
                try:
                    self._order.remove(key)
                except ValueError:
                    pass
                self._order.append(key)
                return self._cache[key]
            if key not in self._pending:
                self._pending.add(key)
                self._queue.put(key)
        return None

    def clear(self) -> None:
        """Evict all cached thumbnails (e.g., when project changes)."""
        with self._lock:
            self._cache.clear()
            self._order.clear()
            self._pending.clear()

    def stop(self) -> None:
        """Shut down the background worker thread."""
        self._queue.put(None)

    # ------------------------------------------------------------------
    # Background worker
    # ------------------------------------------------------------------

    def _work(self) -> None:
        while True:
            key = self._queue.get()
            if key is None:
                break
            filepath, time_s, width, height = key
            img = self._extract(filepath, time_s, width, height)
            with self._lock:
                self._pending.discard(key)
                if img is not None:
                    # Evict oldest if at capacity
                    while len(self._cache) >= _CACHE_MAX and self._order:
                        oldest = self._order.pop(0)
                        self._cache.pop(oldest, None)
                    self._cache[key] = img
                    self._order.append(key)
            if img is not None and self._on_ready:
                try:
                    self._on_ready()
                except Exception:
                    pass

    def _extract(
        self,
        filepath: str,
        time_s: float,
        width: int,
        height: int,
    ) -> "Optional[Image.Image]":
        """Extract a single frame from *filepath* at *time_s*, return as PIL Image."""
        if not _CV2_OK or not filepath:
            return None
        try:
            cap = cv2.VideoCapture(filepath)
            if not cap.isOpened():
                return None
            cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, time_s) * 1000.0)
            ret, frame = cap.read()
            cap.release()
            if not ret or frame is None:
                return None
            frame_rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(frame_rgb)
            # Letterbox-fit into (width × height)
            pil_img.thumbnail((width, height), Image.LANCZOS)
            bg = Image.new("RGB", (width, height), (18, 14, 24))
            ox = (width - pil_img.width) // 2
            oy = (height - pil_img.height) // 2
            bg.paste(pil_img, (ox, oy))
            return bg
        except Exception:
            return None
