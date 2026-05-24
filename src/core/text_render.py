"""
Professional text rendering engine using Pillow.

Replaces the cv2.putText fallback with full support for:
  - Font family (system TrueType fonts + bundled fallbacks)
  - Bold / italic variants
  - Left / center / right alignment
  - Word-wrap to a max width percentage
  - Drop shadow with configurable offset and color
  - Stroke / outline
  - Background pill/box (rounded or rectangular)
  - Per-line spacing
  - Transparent composition onto cv2 BGR frames

All public functions accept and return numpy BGR arrays compatible with
effect_renderer.py and preview_engine.py.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

# Lazy Pillow import — already in requirements.txt
try:
    from PIL import Image, ImageDraw, ImageFont
    _PIL_AVAILABLE = True
except ImportError:
    _PIL_AVAILABLE = False


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass
class TextStyle:
    """All rendering parameters for a text overlay."""
    text:  str  = ""
    font_family: str  = "default"       # font name or "default"
    bold:        bool = False
    italic:      bool = False
    font_size:   int  = 40              # absolute px (scaled by resolution)
    color:       str  = "#ffffff"
    align:       str  = "center"        # "left" | "center" | "right"
    # Position (% of frame, 0-100)
    pos_x_pct:   float = 50.0
    pos_y_pct:   float = 82.0
    # Background
    bg_enabled:  bool  = True
    bg_color:    str   = "#000000"
    bg_alpha:    float = 0.65           # 0-1
    bg_padding:  int   = 10
    bg_rounded:  bool  = True
    # Shadow
    shadow_enabled: bool  = False
    shadow_color:   str   = "#000000"
    shadow_offset_x: int  = 2
    shadow_offset_y: int  = 2
    shadow_blur:    int   = 4
    # Stroke / outline
    stroke_enabled: bool = False
    stroke_color:   str  = "#000000"
    stroke_width:   int  = 2
    # Layout
    max_width_pct: float = 80.0         # % of frame width before word-wrap
    line_spacing:  float = 1.2
    # Scale multiplier (relative to font_size, 0.5-2.0)
    size_pct: float = 100.0


# ── Font resolution ────────────────────────────────────────────────────────────

_FONT_CACHE: dict[tuple, object] = {}

_SYSTEM_FONT_DIRS: list[Path] = []
if sys.platform == "win32":
    _SYSTEM_FONT_DIRS = [
        Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts",
        Path.home() / "AppData" / "Local" / "Microsoft" / "Windows" / "Fonts",
    ]
elif sys.platform == "darwin":
    _SYSTEM_FONT_DIRS = [
        Path("/Library/Fonts"),
        Path("/System/Library/Fonts"),
        Path.home() / "Library" / "Fonts",
    ]
else:
    _SYSTEM_FONT_DIRS = [
        Path("/usr/share/fonts"),
        Path("/usr/local/share/fonts"),
        Path.home() / ".local/share/fonts",
    ]

# Preferred fallback fonts by platform (bold candidates first)
_FALLBACK_FONTS: list[str] = [
    "arial.ttf", "Arial.ttf", "arialbd.ttf",                   # Windows
    "DejaVuSans.ttf", "DejaVuSans-Bold.ttf",                    # Linux
    "Helvetica.dfont", "HelveticaNeue.ttf",                     # macOS
    "LiberationSans-Regular.ttf", "LiberationSans-Bold.ttf",
    "FreeSans.ttf",
]

# Bundled fallback inside the package (ships with Pillow)
def _load_font(family: str, size: int, bold: bool, italic: bool) -> Optional[object]:
    if not _PIL_AVAILABLE:
        return None
    cache_key = (family.lower(), size, bold, italic)
    if cache_key in _FONT_CACHE:
        return _FONT_CACHE[cache_key]

    font = None
    # 1. Try explicit family name with variant suffix
    variants: list[str] = []
    if bold and italic:
        variants = [f"{family}bi.ttf", f"{family}bolditalic.ttf", f"{family}-BoldItalic.ttf"]
    elif bold:
        variants = [f"{family}bd.ttf", f"{family}b.ttf", f"{family}-Bold.ttf", f"{family}Bold.ttf"]
    elif italic:
        variants = [f"{family}i.ttf", f"{family}-Italic.ttf"]
    else:
        variants = [f"{family}.ttf", f"{family}-Regular.ttf"]
    # Also try exact name
    variants.append(family)

    for d in _SYSTEM_FONT_DIRS:
        if not d.exists():
            continue
        for variant in variants:
            candidate = d / variant
            if candidate.exists():
                try:
                    font = ImageFont.truetype(str(candidate), size)
                    break
                except Exception:
                    continue
        if font:
            break

    # 2. Pillow's own font lookup
    if not font:
        try:
            font = ImageFont.truetype(family, size)
        except Exception:
            pass

    # 3. Walk system dirs for fallback
    if not font and family.lower() == "default":
        for d in _SYSTEM_FONT_DIRS:
            for fname in _FALLBACK_FONTS:
                candidate = d / fname
                if candidate.exists():
                    try:
                        font = ImageFont.truetype(str(candidate), size)
                        break
                    except Exception:
                        continue
            if font:
                break

    # 4. PIL built-in bitmap font (no TrueType — lowest quality)
    if not font:
        try:
            font = ImageFont.load_default()
        except Exception:
            font = None

    _FONT_CACHE[cache_key] = font
    return font


# ── Colour helpers ─────────────────────────────────────────────────────────────

def _hex_to_rgba(hex_color: str, alpha: float = 1.0) -> tuple[int, int, int, int]:
    h = hex_color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        h = "ffffff"
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    a = max(0, min(255, int(alpha * 255)))
    return r, g, b, a


def _normalize_hex(value: str, fallback: str = "#ffffff") -> str:
    v = str(value or "").strip()
    if not v.startswith("#"):
        v = "#" + v
    h = v.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return fallback
    try:
        int(h, 16)
        return f"#{h.lower()}"
    except ValueError:
        return fallback


# ── Word-wrap ──────────────────────────────────────────────────────────────────

def _wrap_text(text: str, font: object, max_width_px: int) -> list[str]:
    """Wrap *text* to *max_width_px* using PIL text measurement."""
    if not _PIL_AVAILABLE or font is None:
        return [l.strip() for l in text.splitlines() if l.strip()][:4]

    lines: list[str] = []
    for paragraph in text.replace("\r\n", "\n").split("\n"):
        paragraph = paragraph.strip()
        if not paragraph:
            lines.append("")
            continue
        words = paragraph.split()
        current = words[0] if words else ""
        for word in words[1:]:
            test = f"{current} {word}"
            bbox = _text_bbox(test, font)
            if bbox[2] <= max_width_px:
                current = test
            else:
                lines.append(current)
                current = word
        lines.append(current)
    return lines[:6]  # cap at 6 lines max


def _text_bbox(text: str, font: object) -> tuple[int, int, int, int]:
    """Return (x0, y0, x1, y1) bounding box for text rendered with font."""
    if not _PIL_AVAILABLE:
        return (0, 0, len(text) * 8, 16)
    try:
        dummy = Image.new("RGBA", (1, 1))
        draw  = ImageDraw.Draw(dummy)
        bbox  = draw.textbbox((0, 0), text, font=font)
        return bbox
    except Exception:
        return (0, 0, len(text) * 8, 16)


# ── Main render function ───────────────────────────────────────────────────────

def render_text_on_frame(frame_bgr: np.ndarray, style: TextStyle) -> np.ndarray:
    """
    Composite *style* onto *frame_bgr* and return the result.
    Falls back to cv2.putText if Pillow is unavailable.
    """
    text = style.text.strip()
    if not text:
        return frame_bgr

    if not _PIL_AVAILABLE:
        return _fallback_cv2_render(frame_bgr, style)

    h, w = frame_bgr.shape[:2]
    font_size = max(10, int(style.font_size * (w / 1280.0) * (style.size_pct / 100.0)))
    font = _load_font(style.font_family, font_size, style.bold, style.italic)
    max_width_px = max(50, int(w * style.max_width_pct / 100.0))

    lines = _wrap_text(text, font, max_width_px)
    if not lines:
        return frame_bgr

    # Measure all lines
    line_bboxes = [_text_bbox(ln, font) for ln in lines]
    line_heights = [max(1, bb[3] - bb[1]) for bb in line_bboxes]
    line_widths  = [max(1, bb[2] - bb[0]) for bb in line_bboxes]
    block_w = max(line_widths) + 2 * style.bg_padding
    lh_base = max(line_heights) if line_heights else font_size
    line_step = int(lh_base * style.line_spacing)
    block_h = line_step * len(lines) + style.bg_padding

    # Anchor position (centre of block)
    anchor_x = int(w * style.pos_x_pct / 100.0)
    anchor_y = int(h * style.pos_y_pct / 100.0)

    left  = anchor_x - block_w // 2
    top   = anchor_y - block_h // 2
    right = left + block_w
    bot   = top + block_h

    # Create RGBA canvas
    canvas = Image.fromarray(cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGBA))
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # ── Background ────────────────────────────────────────────────────────────
    if style.bg_enabled:
        bg_rgba = _hex_to_rgba(_normalize_hex(style.bg_color, "#000000"), style.bg_alpha)
        pad = style.bg_padding
        if style.bg_rounded:
            radius = min(12, lh_base // 2)
            _draw_rounded_rect(draw, (left - pad, top - pad, right + pad, bot + pad), radius, bg_rgba)
        else:
            draw.rectangle((left - pad, top - pad, right + pad, bot + pad), fill=bg_rgba)

    # ── Text lines ────────────────────────────────────────────────────────────
    fg_rgba   = _hex_to_rgba(_normalize_hex(style.color, "#ffffff"))
    shad_rgba = _hex_to_rgba(_normalize_hex(style.shadow_color, "#000000"))
    strk_rgba = _hex_to_rgba(_normalize_hex(style.stroke_color, "#000000"))

    for idx, line in enumerate(lines):
        lw = line_widths[idx] if idx < len(line_widths) else block_w
        if style.align == "center":
            tx = anchor_x - lw // 2
        elif style.align == "right":
            tx = right - lw - style.bg_padding
        else:
            tx = left + style.bg_padding
        ty = top + idx * line_step

        # Shadow
        if style.shadow_enabled:
            sx = tx + style.shadow_offset_x
            sy = ty + style.shadow_offset_y
            if style.shadow_blur > 0:
                _draw_blurred_text(overlay, line, (sx, sy), font, shad_rgba, style.shadow_blur)
            else:
                draw.text((sx, sy), line, font=font, fill=shad_rgba)

        # Stroke
        if style.stroke_enabled and style.stroke_width > 0:
            for ox in range(-style.stroke_width, style.stroke_width + 1):
                for oy in range(-style.stroke_width, style.stroke_width + 1):
                    if ox == 0 and oy == 0:
                        continue
                    draw.text((tx + ox, ty + oy), line, font=font, fill=strk_rgba)

        # Main text
        draw.text((tx, ty), line, font=font, fill=fg_rgba)

    # Composite
    combined = Image.alpha_composite(canvas, overlay)
    result_bgr = cv2.cvtColor(np.array(combined), cv2.COLOR_RGBA2BGR)
    return result_bgr


def _draw_rounded_rect(
    draw: object,
    rect: tuple[int, int, int, int],
    radius: int,
    color: tuple[int, int, int, int],
) -> None:
    x0, y0, x1, y1 = rect
    r = max(1, radius)
    draw.rectangle([x0 + r, y0, x1 - r, y1], fill=color)
    draw.rectangle([x0, y0 + r, x1, y1 - r], fill=color)
    draw.ellipse([x0, y0, x0 + 2 * r, y0 + 2 * r], fill=color)
    draw.ellipse([x1 - 2 * r, y0, x1, y0 + 2 * r], fill=color)
    draw.ellipse([x0, y1 - 2 * r, x0 + 2 * r, y1], fill=color)
    draw.ellipse([x1 - 2 * r, y1 - 2 * r, x1, y1], fill=color)


def _draw_blurred_text(
    overlay: object,
    text: str,
    pos: tuple[int, int],
    font: object,
    color: tuple[int, int, int, int],
    blur: int,
) -> None:
    """Render text to a temp layer and gaussian-blur it for soft shadow."""
    tmp = Image.new("RGBA", overlay.size, (0, 0, 0, 0))
    d   = ImageDraw.Draw(tmp)
    d.text(pos, text, font=font, fill=color)
    tmp_arr = np.array(tmp)
    ksize   = max(1, blur * 2 + 1)
    blurred = cv2.GaussianBlur(tmp_arr, (ksize, ksize), 0)
    blurred_img = Image.fromarray(blurred)
    overlay.alpha_composite(blurred_img)


# ── cv2 fallback ───────────────────────────────────────────────────────────────

def _fallback_cv2_render(frame_bgr: np.ndarray, style: TextStyle) -> np.ndarray:
    """Minimal cv2.putText fallback when Pillow is unavailable."""
    h, w = frame_bgr.shape[:2]
    font_scale = max(0.35, min(2.5, w / 1280.0 * style.size_pct / 100.0))
    thickness  = 2
    fg_hex     = _normalize_hex(style.color, "#ffffff")
    r          = int(fg_hex[1:3], 16)
    g          = int(fg_hex[3:5], 16)
    b          = int(fg_hex[5:7], 16)
    fg_bgr     = (b, g, r)
    lines      = [l.strip() for l in style.text.splitlines() if l.strip()][:4]
    x          = int(w * style.pos_x_pct / 100.0)
    y          = int(h * style.pos_y_pct / 100.0)
    lh         = int(30 * font_scale)
    frame      = frame_bgr.copy()
    for i, line in enumerate(lines):
        cv2.putText(frame, line, (x, y + i * lh),
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale, fg_bgr, thickness, cv2.LINE_AA)
    return frame


# ── Style ↔ clip_option dict bridge ──────────────────────────────────────────

def style_from_clip_option(option: dict) -> TextStyle:
    """Build a TextStyle from a pipeline clip_option dict."""
    return TextStyle(
        text             = str(option.get("text_overlay") or "").strip(),
        font_family      = str(option.get("text_font") or "default"),
        bold             = bool(option.get("text_bold", False)),
        italic           = bool(option.get("text_italic", False)),
        size_pct         = float(option.get("text_size_pct") or 100.0),
        color            = str(option.get("text_color") or "#ffffff"),
        align            = str(option.get("text_align") or "center"),
        pos_x_pct        = float(option.get("text_position_x_pct") or 50.0),
        pos_y_pct        = float(option.get("text_position_y_pct") or 82.0),
        bg_enabled       = bool(option.get("text_background_enabled", True)),
        bg_color         = str(option.get("text_background_color") or "#000000"),
        bg_alpha         = float(option.get("text_background_alpha") or 0.65),
        bg_rounded       = bool(option.get("text_bg_rounded", True)),
        shadow_enabled   = bool(option.get("text_shadow_enabled", False)),
        shadow_color     = str(option.get("text_shadow_color") or "#000000"),
        shadow_offset_x  = int(option.get("text_shadow_offset_x") or 2),
        shadow_offset_y  = int(option.get("text_shadow_offset_y") or 2),
        shadow_blur      = int(option.get("text_shadow_blur") or 4),
        stroke_enabled   = bool(option.get("text_stroke_enabled", False)),
        stroke_color     = str(option.get("text_stroke_color") or "#000000"),
        stroke_width     = int(option.get("text_stroke_width") or 2),
        max_width_pct    = float(option.get("text_max_width_pct") or 80.0),
        line_spacing     = float(option.get("text_line_spacing") or 1.2),
    )


def style_to_clip_option_patch(style: TextStyle) -> dict:
    """Serialize a TextStyle back to clip_option dict fields."""
    return {
        "text_overlay":          style.text,
        "text_font":             style.font_family,
        "text_bold":             style.bold,
        "text_italic":           style.italic,
        "text_size_pct":         style.size_pct,
        "text_color":            style.color,
        "text_align":            style.align,
        "text_position_x_pct":   style.pos_x_pct,
        "text_position_y_pct":   style.pos_y_pct,
        "text_background_enabled": style.bg_enabled,
        "text_background_color": style.bg_color,
        "text_background_alpha": style.bg_alpha,
        "text_bg_rounded":       style.bg_rounded,
        "text_shadow_enabled":   style.shadow_enabled,
        "text_shadow_color":     style.shadow_color,
        "text_shadow_offset_x":  style.shadow_offset_x,
        "text_shadow_offset_y":  style.shadow_offset_y,
        "text_shadow_blur":      style.shadow_blur,
        "text_stroke_enabled":   style.stroke_enabled,
        "text_stroke_color":     style.stroke_color,
        "text_stroke_width":     style.stroke_width,
        "text_max_width_pct":    style.max_width_pct,
        "text_line_spacing":     style.line_spacing,
    }


# ── Available fonts ────────────────────────────────────────────────────────────

def list_system_fonts() -> list[str]:
    """Return readable font family names found on this system."""
    found: set[str] = set()
    for d in _SYSTEM_FONT_DIRS:
        if not d.exists():
            continue
        for f in d.rglob("*.ttf"):
            stem = f.stem.split("-")[0].split("_")[0]
            if stem:
                found.add(stem)
    return sorted(found) or ["default"]
