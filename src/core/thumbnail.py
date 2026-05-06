"""
YouTube-style thumbnail generator — v3.

Layer order (bottom to top):
  0. Full frame — blurred (sigma 8) + heavy dark overlay → background depth
  1. Original frame — person zone only, minimal overlay → person stands out
  2. Gradient rectangle → text readability (ONLY on text side, never over person)
  3. Title + subtitle text with drop shadows

Person detection:
  - Primary: OpenCV Haar cascade face detection (fast, no models needed)
  - Fallback: edge-density heuristic (pure Python, no numpy needed)

Text side is OPPOSITE to where the detected face center is.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ..ffmpeg_env import ffmpeg, ffprobe


# ── Themes ──────────────────────────────────────────────────────────────────

THEMES: dict[str, dict] = {
    "dark": {
        "rect_top":    (8,  14,  42),
        "rect_bottom": (20, 80, 200),
        "accent":      (30, 110, 230),
        "title":       (255, 255, 255),
        "subtitle":    (180, 210, 255),
    },
    "fire": {
        "rect_top":    (60,  12,  4),
        "rect_bottom": (220, 60,  0),
        "accent":      (255, 90,  20),
        "title":       (255, 255, 255),
        "subtitle":    (255, 200, 130),
    },
    "gold": {
        "rect_top":    (30,  20,  0),
        "rect_bottom": (200, 148, 0),
        "accent":      (230, 180, 0),
        "title":       (255, 255, 210),
        "subtitle":    (220, 195, 100),
    },
    "purple": {
        "rect_top":    (20,  10,  42),
        "rect_bottom": (100, 40, 200),
        "accent":      (130, 60, 230),
        "title":       (255, 255, 255),
        "subtitle":    (200, 175, 255),
    },
}

_FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/impact.ttf",
    "C:/Windows/Fonts/calibrib.ttf",
    "C:/Windows/Fonts/Arial_Bold.ttf",
    "C:/Windows/Fonts/verdanab.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation/LiberationSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


# ── Public ───────────────────────────────────────────────────────────────────

def generate_thumbnail(
    video_path: str,
    output_path: str,
    title: str,
    subtitle: str = "",
    theme: str = "dark",
    frame_time: Optional[float] = None,
    size: Tuple[int, int] = (1280, 720),
) -> str:
    frame    = _extract_frame(video_path, frame_time)
    canvas   = _compose_layers(frame, size)
    title_up = title.upper()
    person_x = _detect_person_x(frame)
    text_left = person_x > 0.50   # text goes opposite of person
    canvas   = _draw_title_block(canvas, title_up, subtitle, theme, text_left)
    canvas.save(output_path, "JPEG", quality=95, optimize=True)
    return output_path


def generate_multi_thumbnails(
    video_path: str,
    output_dir: str,
    base_name: str,
    title: str,
    subtitle: str = "",
    theme: str = "dark",
    count: int = 5,
    size: Tuple[int, int] = (1280, 720),
) -> list[str]:
    duration = _get_duration(video_path)
    if duration <= 0:
        return []
    fractions = [0.10, 0.25, 0.40, 0.55, 0.70][:count]
    paths: list[str] = []
    for i, frac in enumerate(fractions):
        out = os.path.join(output_dir, f"{base_name}_thumb_{i + 1}.jpg")
        try:
            generate_thumbnail(video_path, out, title=title, subtitle=subtitle,
                               theme=theme, frame_time=duration * frac, size=size)
            paths.append(out)
        except Exception:
            pass
    return paths


# ── Person detection ─────────────────────────────────────────────────────────

def detect_person(img: Image.Image) -> tuple[float, float, float]:
    """
    Detect main person in frame.
    Returns (face_x, face_y, face_size_relative):
      face_x, face_y  — normalized 0-1 center of face
      face_size       — face width as fraction of image width
    Falls back to center heuristic if no face found.
    """
    try:
        import cv2
        import numpy as np

        img_np = np.array(img.convert("RGB"))
        gray   = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)
        cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )
        faces = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4,
            minSize=(int(img.width * 0.04), int(img.height * 0.04)),
        )
        if len(faces) > 0:
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            return (
                float(x + w / 2) / img.width,
                float(y + h / 2) / img.height,
                float(w) / img.width,
            )
    except Exception:
        pass

    # Edge-density heuristic for x; assume y = upper-center (speaker)
    face_x = _edge_density_x(img)
    return face_x, 0.38, 0.22   # y=38% (head-level), size 22%


def _edge_density_x(img: Image.Image) -> float:
    """Fast edge-density heuristic for horizontal person position."""
    COLS = 32
    small = img.resize((COLS, 18), Image.BOX).convert("L")
    pix   = list(small.getdata())
    scores = []
    for x in range(COLS):
        col  = [pix[y * COLS + x] for y in range(18)]
        mean = sum(col) / 18
        var  = sum((v - mean) ** 2 for v in col) / 18
        scores.append(var)
    total = sum(scores) or 1.0
    cx = sum(x * scores[x] for x in range(COLS)) / total / COLS
    return max(0.25, min(0.85, cx))


def _detect_person_x(img: Image.Image) -> float:
    """Thin wrapper: returns only the x coordinate."""
    x, _, _ = detect_person(img)
    return x


def apply_bokeh_pil(
    img: Image.Image,
    intensity: float,
    face_x: float = 0.50,
    face_y: float = 0.38,
    face_size: float = 0.22,
) -> Image.Image:
    """
    Apply background blur with person-aware soft elliptical mask.
    Person zone (face + body) stays sharp; background is blurred.
    """
    if intensity < 0.05:
        return img

    from PIL import ImageFilter as IF, ImageDraw as ID

    w, h  = img.size
    sigma = 2 + intensity * 14   # blur intensity

    blurred = img.filter(IF.GaussianBlur(sigma))

    # --- Build elliptical mask centred on body (face + torso) ---------------
    # Body centre: slightly below face centre to include shoulders/torso
    cx   = int(face_x * w)
    cy   = int((face_y + face_size * 1.4) * h)          # body centre
    rx   = int(max(0.20, face_size * 2.0) * w)          # horizontal radius
    ry   = int(max(0.40, face_size * 4.0) * h)          # vertical radius

    # White = keep sharp (person), Black = show blurred (background)
    mask  = Image.new("L", (w, h), 0)
    m_drw = ImageDraw.Draw(mask)

    # Concentric ellipses with rising alpha → soft feathered edge
    STEPS = 32
    for step in range(1, STEPS + 1):
        t     = step / STEPS
        alpha = int(255 * t)
        m_drw.ellipse([
            cx - int(rx * t), cy - int(ry * t),
            cx + int(rx * t), cy + int(ry * t),
        ], fill=alpha)

    # Soften the mask so the transition is invisible
    mask = mask.filter(IF.GaussianBlur(int(w * 0.035)))

    # Composite: inside mask = sharp original, outside = blurred
    return Image.composite(img, blurred, mask)


def detect_person_from_video(video_path: str, at_second: Optional[float] = None) -> tuple[float, float, float]:
    """Extract a frame and detect the person position."""
    try:
        frame = _extract_frame(video_path, at_second)
        return detect_person(frame)
    except Exception:
        return 0.50, 0.38, 0.22


def _detect_person_x_compat(img: Image.Image) -> float:
    """
    Return normalized horizontal center of the main subject (0.0=left, 1.0=right).
    Uses OpenCV face detection if available, else edge-density heuristic.
    """
    # ── Method 1: OpenCV face detection ────────────────────────────────────
    try:
        import cv2
        import numpy as np

        img_np = np.array(img.convert("RGB"))
        gray   = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

        cascade_path = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        cascade      = cv2.CascadeClassifier(cascade_path)
        faces        = cascade.detectMultiScale(
            gray, scaleFactor=1.1, minNeighbors=4,
            minSize=(int(img.width * 0.05), int(img.height * 0.05)),
        )

        if len(faces) > 0:
            # Largest face
            x, y, w, h = max(faces, key=lambda f: f[2] * f[3])
            return float(x + w / 2) / img.width

    except Exception:
        pass

    # ── Method 2: Edge-density heuristic ───────────────────────────────────
    # Resize for speed, find column with highest edge activity
    COLS = 32
    small = img.resize((COLS, 18), Image.BOX).convert("L")
    pix   = list(small.getdata())  # 32×18 = 576 values

    # Per-column variance (high variance → likely person boundary)
    scores = []
    for x in range(COLS):
        col  = [pix[y * COLS + x] for y in range(18)]
        mean = sum(col) / 18
        var  = sum((v - mean) ** 2 for v in col) / 18
        scores.append(var)

    total = sum(scores) or 1.0
    cx = sum(x * scores[x] for x in range(COLS)) / total / COLS
    # Clamp to 0.25–0.85 (avoid extreme edges)
    return max(0.25, min(0.85, cx))


# ── Layer composition ────────────────────────────────────────────────────────

def _compose_layers(frame: Image.Image, size: Tuple[int, int]) -> Image.Image:
    """
    Layer 0: Blurred + heavily darkened full frame (background).
    Layer 1: Sharp original, only in person zone (minimal overlay).
    Transition between zones uses a soft horizontal gradient mask.
    """
    w, h = size
    base = _resize_and_crop(frame, size)

    # ── Layer 0: background ──────────────────────────────────────────────────
    bg = base.copy().filter(ImageFilter.GaussianBlur(8))
    dark = Image.new("RGBA", size, (0, 0, 0, 165))
    layer0 = Image.alpha_composite(bg.convert("RGBA"), dark)

    # ── Layer 1: sharp person ────────────────────────────────────────────────
    # Detect person position to size the sharp zone
    person_x = _detect_person_x(base)
    text_left = person_x > 0.50

    # Sharp zone spans the person side (right or left)
    if text_left:
        sharp_start = int(w * max(0.30, person_x - 0.28))
        sharp_end   = w
    else:
        sharp_start = 0
        sharp_end   = int(w * min(0.70, person_x + 0.28))

    # Very slight overlay on sharp/person layer
    person_shade = Image.new("RGBA", size, (0, 0, 0, 20))
    layer1 = Image.alpha_composite(base.convert("RGBA"), person_shade)

    # ── Alpha mask: 0 = show background, 255 = show sharp ───────────────────
    mask      = Image.new("L", size, 0)
    m_draw    = ImageDraw.Draw(mask)
    fade_w    = int(w * 0.14)   # transition width

    for x in range(w):
        if text_left:
            if x < sharp_start - fade_w:
                alpha = 0
            elif x < sharp_start:
                alpha = int(255 * (x - (sharp_start - fade_w)) / fade_w)
            else:
                alpha = 255
        else:
            if x > sharp_end + fade_w:
                alpha = 0
            elif x > sharp_end:
                alpha = int(255 * (1.0 - (x - sharp_end) / fade_w))
            else:
                alpha = 255
        m_draw.line([(x, 0), (x, h)], fill=alpha)

    composite = Image.composite(layer1, layer0, mask)
    return composite.convert("RGB")


# ── Title block ───────────────────────────────────────────────────────────────

def _draw_title_block(
    canvas: Image.Image,
    title:  str,
    subtitle: str,
    theme: str,
    text_left: bool,
) -> Image.Image:
    colors = THEMES.get(theme, THEMES["dark"])
    w, h   = canvas.size

    # ── Rectangle boundaries (never enters the person zone) ─────────────────
    margin = 36
    if text_left:
        rx1 = margin
        rx2 = int(w * 0.52)
    else:
        rx1 = int(w * 0.48)
        rx2 = w - margin

    ry1 = int(h * 0.28)
    ry2 = int(h * 0.88)

    rect_w = rx2 - rx1
    rect_h = ry2 - ry1

    # ── Gradient rectangle ───────────────────────────────────────────────────
    rect_layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    r_draw     = ImageDraw.Draw(rect_layer)

    tr, tg, tb = colors["rect_top"]
    br, bg_, bb = colors["rect_bottom"]

    for y in range(rect_h):
        ratio  = y / rect_h
        rc     = int(tr + (br - tr) * ratio)
        gc     = int(tg + (bg_ - tg) * ratio)
        bc     = int(tb + (bb - tb) * ratio)
        alpha  = int(225 - 20 * ratio)
        r_draw.line([(rx1, ry1 + y), (rx2, ry1 + y)], fill=(rc, gc, bc, alpha))

    # Accent bar (on the outer edge of the text block)
    bar_x = rx1 if text_left else rx2 - 8
    r_draw.rectangle([(bar_x, ry1), (bar_x + 8, ry2)], fill=(*colors["accent"], 255))

    canvas = Image.alpha_composite(canvas.convert("RGBA"), rect_layer).convert("RGB")
    draw   = ImageDraw.Draw(canvas)

    # ── Fonts ────────────────────────────────────────────────────────────────
    title_font = _load_font(68)
    sub_font   = _load_font(36)
    shadow_col = (0, 0, 0)
    pad_x = (rx1 + 20) if text_left else (rx1 + 12)

    # ── Title (wrapped) ──────────────────────────────────────────────────────
    lines = _wrap_text(title, title_font, rect_w - 28)
    ty    = ry1 + 20
    for line in lines[:3]:   # max 3 lines
        draw.text((pad_x + 2, ty + 2), line, font=title_font, fill=shadow_col)
        draw.text((pad_x,     ty    ), line, font=title_font, fill=colors["title"])
        bbox = title_font.getbbox(line)
        ty  += (bbox[3] - bbox[1]) + 8

    # ── Subtitle ─────────────────────────────────────────────────────────────
    if subtitle:
        sy = min(ty + 10, ry2 - 52)
        draw.text((pad_x + 2, sy + 2), subtitle, font=sub_font, fill=shadow_col)
        draw.text((pad_x,     sy    ), subtitle, font=sub_font, fill=colors["subtitle"])

    return canvas


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_duration(video_path: str) -> float:
    r = subprocess.run(
        [ffprobe(), "-v", "quiet", "-show_entries", "format=duration",
         "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def _extract_frame(video_path: str, at_second: Optional[float]) -> Image.Image:
    if at_second is None:
        at_second = max(2.0, _get_duration(video_path) * 0.20)

    with tempfile.TemporaryDirectory() as tmp:
        frame_path = os.path.join(tmp, "frame.jpg")
        subprocess.run(
            [ffmpeg(), "-y", "-ss", f"{at_second:.3f}", "-i", video_path,
             "-vframes", "1", "-q:v", "2", frame_path],
            capture_output=True,
            check=True,
        )
        return Image.open(frame_path).convert("RGB").copy()


def _resize_and_crop(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    tw, th = size
    w,  h  = img.size
    scale  = max(tw / w, th / h)
    nw, nh = int(w * scale), int(h * scale)
    img    = img.resize((nw, nh), Image.LANCZOS)
    left   = (nw - tw) // 2
    top    = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _wrap_text(text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for word in words:
        test = (cur + " " + word).strip()
        if font.getbbox(test)[2] <= max_width:
            cur = test
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines or [text]


def _load_font(size: int) -> ImageFont.FreeTypeFont:
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                continue
    return ImageFont.load_default()


# ── PIL-based grade preview (fast, no ffmpeg needed) ─────────────────────────

def apply_grade_preview(
    img: Image.Image,
    grade,
    bokeh_intensity: float = 0.0,
) -> Image.Image:
    """
    Apply color grade + bokeh in PIL for real-time preview.
    Not identical to the ffmpeg version but accurate enough for visual feedback.
    """
    from PIL import ImageEnhance, ImageFilter

    # ── Bokeh first (global softening + edge re-sharpening) ─────────────────
    if bokeh_intensity >= 0.05:
        sigma  = 2 + bokeh_intensity * 10          # 2 → 12
        radius = max(1, int(3 + bokeh_intensity * 8))
        pct    = int(bokeh_intensity * 150)
        img = img.filter(ImageFilter.GaussianBlur(sigma))
        img = img.filter(ImageFilter.UnsharpMask(radius=radius, percent=pct, threshold=2))

    # ── Color grade ──────────────────────────────────────────────────────────
    if not getattr(grade, "enabled", True):
        return img

    if abs(grade.brightness) > 0.5:
        img = ImageEnhance.Brightness(img).enhance(
            max(0.1, 1.0 + grade.brightness / 250.0))

    if abs(grade.contrast) > 0.5:
        img = ImageEnhance.Contrast(img).enhance(
            max(0.1, 1.0 + grade.contrast / 100.0 * 0.8))

    if abs(grade.saturation) > 0.5:
        img = ImageEnhance.Color(img).enhance(
            max(0.0, 1.0 + grade.saturation / 100.0))

    if grade.sharpen > 0.5:
        img = ImageEnhance.Sharpness(img).enhance(
            1.0 + grade.sharpen / 100.0 * 1.5)

    return img
