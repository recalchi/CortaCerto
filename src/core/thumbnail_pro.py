"""
Professional YouTube-style thumbnail engine (v2).

Pipeline (per frame):
  1. Score & pick best frames (frame_scoring.py)
  2. Segment subject with GrabCut (segmentation.py)
  3. Build artistic background:
       original heavily-blurred + diagonal gradient overlay + vignette
  4. Enhance subject:
       sharpen + contrast bump + subtle outer glow
  5. Place subject anchored bottom-right at 1.05× scale
  6. Compose left-side text block:
       big bold uppercase + black stroke + drop shadow
  7. Variant generator: 5 versions with rotated themes / zoom / accent colors

Designed to run on Python 3.14 with only OpenCV + Pillow (no MediaPipe / YOLO).
"""
from __future__ import annotations

import os
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np
from PIL import (
    Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps,
)

from .frame_scoring  import FrameScore, select_best_frames
from .segmentation   import segment_person
from .thumbnail      import _extract_frame, _load_font   # reuse helpers


# ── Color themes (background gradient + text accents) ───────────────────────

@dataclass
class ProTheme:
    name:        str
    bg_top:      tuple[int, int, int]
    bg_bottom:   tuple[int, int, int]
    text:        tuple[int, int, int]
    stroke:      tuple[int, int, int]
    accent:      tuple[int, int, int]
    glow:        tuple[int, int, int]


THEMES_PRO: dict[str, ProTheme] = {
    "ocean": ProTheme(
        name="ocean",
        bg_top=(8, 14, 50),  bg_bottom=(40, 100, 220),
        text=(255, 255, 255),  stroke=(0, 0, 0),
        accent=(255, 220, 0),  glow=(80, 160, 255),
    ),
    "fire": ProTheme(
        name="fire",
        bg_top=(50, 8, 8),  bg_bottom=(255, 110, 0),
        text=(255, 255, 230),  stroke=(20, 0, 0),
        accent=(255, 230, 80),  glow=(255, 100, 30),
    ),
    "purple": ProTheme(
        name="purple",
        bg_top=(20, 5, 50),  bg_bottom=(155, 50, 255),
        text=(255, 255, 255),  stroke=(0, 0, 0),
        accent=(255, 230, 80),  glow=(180, 100, 255),
    ),
    "gold": ProTheme(
        name="gold",
        bg_top=(40, 20, 0),  bg_bottom=(245, 180, 30),
        text=(20, 15, 0),  stroke=(255, 240, 200),
        accent=(255, 80, 50),  glow=(255, 200, 80),
    ),
    "noir": ProTheme(
        name="noir",
        bg_top=(8, 8, 12),  bg_bottom=(40, 40, 50),
        text=(255, 255, 255),  stroke=(0, 0, 0),
        accent=(255, 60, 60),  glow=(220, 30, 30),
    ),
}


# ── Public API ───────────────────────────────────────────────────────────────

def generate_thumbnails_pro(
    video_path:       str,
    output_dir:       str,
    base_name:        str,
    title:            str,
    subtitle:         str = "",
    count:            int = 5,
    size:             tuple[int, int] = (1280, 720),
    on_progress:      Optional[callable] = None,
) -> list[str]:
    """
    Generate `count` professional thumbnail variants.
    Each variant uses a different best-scored frame AND a different theme.
    """
    os.makedirs(output_dir, exist_ok=True)

    if on_progress:
        on_progress("Selecionando melhores frames…")

    candidates: list[FrameScore] = select_best_frames(
        video_path, count=max(count, 5),
        sample_every_s=1.5,
    )
    if not candidates:
        return []

    # Rotate through themes to maximise visual variety
    theme_names = ["ocean", "fire", "purple", "gold", "noir"]
    paths: list[str] = []

    for idx, score in enumerate(candidates[:count]):
        if on_progress:
            on_progress(f"Compondo thumbnail {idx + 1}/{count}…")

        try:
            theme = THEMES_PRO[theme_names[idx % len(theme_names)]]
            frame = _extract_frame(video_path, score.timestamp_s)
            canvas = _compose_thumbnail(
                frame, score, theme,
                title=title, subtitle=subtitle,
                size=size, variant_seed=idx,
            )
            out = os.path.join(output_dir, f"{base_name}_thumb_{idx + 1}.jpg")
            canvas.save(out, "JPEG", quality=94, optimize=True)
            paths.append(out)
        except Exception as exc:
            # Skip frames that fail (e.g. GrabCut edge cases)
            if on_progress:
                on_progress(f"Pulando thumbnail {idx + 1}: {type(exc).__name__}")
            continue

    return paths


# ── Composition pipeline ─────────────────────────────────────────────────────

def _compose_thumbnail(
    frame:        Image.Image,
    score:        FrameScore,
    theme:        ProTheme,
    title:        str,
    subtitle:     str,
    size:         tuple[int, int],
    variant_seed: int = 0,
) -> Image.Image:
    """Full per-thumbnail composition."""
    w, h = size
    canvas = Image.new("RGB", size, theme.bg_top)

    # ── Step 1: Background ──────────────────────────────────────────────────
    bg = _build_background(frame, size, theme, variant_seed)
    canvas.paste(bg, (0, 0))

    # ── Step 2: Segment subject ─────────────────────────────────────────────
    rgba_subject, _alpha = segment_person(
        frame, face_box=score.face_box, iterations=4, feather_radius=10
    )

    # ── Step 3: Enhance subject ─────────────────────────────────────────────
    rgba_subject = _enhance_subject(rgba_subject, theme)

    # ── Step 4: Place subject (right-anchored, slightly oversized) ──────────
    # Scale so subject height ≈ 95 % of canvas height
    sub_w, sub_h = rgba_subject.size
    scale = (h * 0.98) / sub_h
    new_w = int(sub_w * scale)
    new_h = int(sub_h * scale)
    rgba_subject = rgba_subject.resize((new_w, new_h), Image.LANCZOS)

    # Anchor: bottom-right with small horizontal offset based on variant
    offset_x = int(w * (0.04 + (variant_seed % 3) * 0.015))
    px = w - new_w + offset_x
    py = h - new_h + 10        # 10 px past bottom for anchored feeling
    canvas_rgba = canvas.convert("RGBA")
    canvas_rgba.alpha_composite(rgba_subject, (px, py))
    canvas = canvas_rgba.convert("RGB")

    # ── Step 5: Text block (left side) ──────────────────────────────────────
    canvas = _draw_text_block(canvas, title, subtitle, theme, variant_seed)

    # ── Step 6: Final polish ────────────────────────────────────────────────
    canvas = ImageEnhance.Sharpness(canvas).enhance(1.15)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.06)

    return canvas


# ── Background engine ────────────────────────────────────────────────────────

def _build_background(
    frame: Image.Image,
    size:  tuple[int, int],
    theme: ProTheme,
    seed:  int,
) -> Image.Image:
    """
    Layer 1 (bottom): heavy-blurred + darkened original frame.
    Layer 2: diagonal theme gradient at ~35 % opacity.
    Layer 3: vignette darkening the corners.
    """
    w, h = size

    # Resize-and-crop the frame to cover the full canvas
    base = _resize_cover(frame, size)

    # Heavy blur for bokeh-like bg, then darken
    bg = base.filter(ImageFilter.GaussianBlur(28))
    dark = Image.new("RGBA", size, (0, 0, 0, 130))
    bg = Image.alpha_composite(bg.convert("RGBA"), dark).convert("RGB")

    # Diagonal gradient overlay (top-left → bottom-right)
    grad = _diagonal_gradient(size, theme.bg_top, theme.bg_bottom)
    bg = Image.blend(bg, grad, alpha=0.55)

    # Vignette
    bg = _apply_vignette(bg, strength=0.65)

    return bg


def _resize_cover(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    tw, th = size
    w, h = img.size
    s    = max(tw / w, th / h)
    nw, nh = int(w * s), int(h * s)
    img    = img.resize((nw, nh), Image.LANCZOS)
    left   = (nw - tw) // 2
    top    = (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


def _diagonal_gradient(
    size: tuple[int, int],
    c1:   tuple[int, int, int],
    c2:   tuple[int, int, int],
) -> Image.Image:
    """Top-left=c1, bottom-right=c2 diagonal gradient. Numpy-vectorised."""
    w, h = size
    # t[y, x] = (x + y) / (w + h)
    xs = np.arange(w, dtype=np.float32)[None, :]
    ys = np.arange(h, dtype=np.float32)[:, None]
    t  = (xs + ys) / float(w + h)
    t  = t[..., None]                      # (h, w, 1)
    c1_arr = np.array(c1, dtype=np.float32)
    c2_arr = np.array(c2, dtype=np.float32)
    out = c1_arr + (c2_arr - c1_arr) * t   # (h, w, 3)
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8), "RGB")


def _apply_vignette(img: Image.Image, strength: float = 0.6) -> Image.Image:
    """Radial darkening from center to corners. Numpy-vectorised."""
    w, h = img.size
    xs = (np.arange(w, dtype=np.float32) - w / 2) / (w / 2)
    ys = (np.arange(h, dtype=np.float32) - h / 2) / (h / 2)
    dist = np.sqrt(xs[None, :] ** 2 + ys[:, None] ** 2)   # 0 at center, ~1.4 at corner
    # Smooth ramp: <0.5 = no darkening, 0.5..1.4 = ramp to full strength
    darken = np.clip((dist - 0.45) / 0.95, 0.0, 1.0) * strength  # (h, w)
    arr    = np.asarray(img, dtype=np.float32)
    arr   *= (1.0 - darken)[..., None]
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")


# ── Subject enhancement ──────────────────────────────────────────────────────

def _enhance_subject(rgba: Image.Image, theme: ProTheme) -> Image.Image:
    """Sharpen + contrast bump + subtle glow halo behind subject."""
    rgb_part   = rgba.convert("RGB")
    alpha_part = rgba.split()[-1]

    rgb_part = ImageEnhance.Sharpness(rgb_part).enhance(1.6)
    rgb_part = ImageEnhance.Contrast(rgb_part).enhance(1.18)
    rgb_part = ImageEnhance.Color(rgb_part).enhance(1.10)

    # Build glow halo: blurred dilated alpha tinted with theme.glow color
    halo_mask = alpha_part.filter(ImageFilter.MaxFilter(7))
    halo_mask = halo_mask.filter(ImageFilter.GaussianBlur(18))
    halo_color = Image.new("RGB", rgba.size, theme.glow)
    halo_rgba  = Image.merge("RGBA", (*halo_color.split(), halo_mask))

    # Composite: halo behind subject
    enhanced = Image.merge("RGBA", (*rgb_part.split(), alpha_part))
    canvas   = Image.new("RGBA", rgba.size, (0, 0, 0, 0))
    canvas.alpha_composite(halo_rgba)
    canvas.alpha_composite(enhanced)
    return canvas


# ── Text engine ──────────────────────────────────────────────────────────────

def _draw_text_block(
    canvas: Image.Image,
    title:  str,
    subtitle: str,
    theme:  ProTheme,
    seed:   int,
) -> Image.Image:
    """
    Big bold uppercase title with thick black stroke + drop shadow.
    Subtitle below in smaller weight, accent-colored.
    Position: left side, vertical center.
    """
    w, h = canvas.size
    out  = canvas.convert("RGBA")
    draw = ImageDraw.Draw(out)

    # Title properties
    title_text = title.upper().strip()
    max_text_w = int(w * 0.52)        # use only left ~52% of canvas

    # Pick auto font size: try sizes from large to small until fits ≤ 3 lines
    title_font, title_lines, line_height = _autofit_title(
        title_text, max_text_w, max_lines=3, max_size=120, min_size=58,
    )

    # Vertical centering
    total_h = line_height * len(title_lines)
    title_y = int(h * 0.28)
    if total_h > h * 0.55:
        title_y = int((h - total_h) / 2)

    # Draw with thick stroke + drop shadow per line
    title_x = int(w * 0.045)
    stroke_width = max(4, int(title_font.size * 0.07))

    for i, line in enumerate(title_lines):
        ly = title_y + i * line_height

        # Drop shadow (offset 4-5 px)
        shadow_offset = 5
        draw.text(
            (title_x + shadow_offset, ly + shadow_offset),
            line, font=title_font,
            fill=(0, 0, 0, 200),
            stroke_width=stroke_width,
            stroke_fill=(0, 0, 0, 255),
        )

        # Main title with stroke
        draw.text(
            (title_x, ly), line, font=title_font,
            fill=(*theme.text, 255),
            stroke_width=stroke_width,
            stroke_fill=(*theme.stroke, 255),
        )

    # Subtitle / badge
    if subtitle:
        sub_font = _load_font(36)
        sub_text = subtitle.upper()
        sub_y = title_y + total_h + 18

        # Small accent rectangle behind subtitle
        bbox    = sub_font.getbbox(sub_text)
        sub_w_p = bbox[2] - bbox[0] + 24
        sub_h_p = bbox[3] - bbox[1] + 14
        rect_layer = Image.new("RGBA", out.size, (0, 0, 0, 0))
        rd = ImageDraw.Draw(rect_layer)
        rd.rectangle(
            [(title_x - 4, sub_y - 4),
             (title_x - 4 + sub_w_p, sub_y - 4 + sub_h_p)],
            fill=(*theme.accent, 230),
        )
        out = Image.alpha_composite(out, rect_layer)
        draw = ImageDraw.Draw(out)
        draw.text((title_x + 8, sub_y), sub_text, font=sub_font,
                  fill=(0, 0, 0, 255))

    return out.convert("RGB")


def _autofit_title(
    text: str,
    max_width_px: int,
    max_lines: int = 3,
    max_size:  int = 120,
    min_size:  int = 58,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    """
    Find the largest font size where the title wraps into ≤ max_lines lines
    that all fit in max_width_px.  Returns (font, lines, line_height_px).
    """
    for size in range(max_size, min_size - 1, -4):
        font  = _load_font(size)
        lines = _wrap_to_width(text, font, max_width_px)
        if len(lines) <= max_lines and all(
            font.getbbox(ln)[2] <= max_width_px for ln in lines
        ):
            # Line height ≈ font size * 1.05 (tight)
            ascent, descent = font.getmetrics()
            return font, lines, int((ascent + descent) * 1.0)
    # Min size fallback
    font  = _load_font(min_size)
    lines = _wrap_to_width(text, font, max_width_px)[:max_lines]
    ascent, descent = font.getmetrics()
    return font, lines, int((ascent + descent) * 1.0)


def _wrap_to_width(
    text: str, font: ImageFont.FreeTypeFont, max_width: int
) -> list[str]:
    """Greedy word wrap; if a single word overflows, hard-break."""
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
