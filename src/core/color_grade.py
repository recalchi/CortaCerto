"""
Color grading via ffmpeg filter chains.
Default preset matches the CapCut reference:
  Temp:-10, Hue:-15, Sat:+10, Contrast:+10, Shadows:-5,
  Whites:+10, Blacks:-5, Brightness:+10, Sharpen:+5
"""
from __future__ import annotations
from dataclasses import dataclass


@dataclass
class ColorGrade:
    enabled: bool = True
    temperature: float = -10   # -100..100 (negative = cooler/bluer)
    tint: float = 0            # -100..100 (negative = greener)
    hue: float = -15           # degrees (-180..180)
    saturation: float = 10     # -100..100
    vibrance: float = 0        # -100..100
    contrast: float = 10       # -100..100
    brightness: float = 10     # -100..100
    shadows: float = -5        # -100..100
    whites: float = 10         # -100..100
    blacks: float = -5         # -100..100
    highlights: float = 0      # -100..100
    sharpen: float = 5         # 0..100


# Matches the CapCut reference screenshots
PRESET_CAPCUT = ColorGrade()

PRESET_NEUTRAL = ColorGrade(
    enabled=True,
    temperature=0, tint=0, hue=0, saturation=0, vibrance=0, contrast=0,
    brightness=0, shadows=0, whites=0, blacks=0,
    highlights=0, sharpen=0,
)


def build_filter(grade: ColorGrade) -> str:
    """
    Translate ColorGrade into a comma-separated ffmpeg -vf filter string.
    Returns empty string if grading is disabled.
    """
    if not grade.enabled:
        return ""

    parts: list[str] = []

    # 1. Hue rotation
    if abs(grade.hue) > 0.5:
        parts.append(f"hue=h={grade.hue:.1f}")

    # 2. eq: brightness / contrast / saturation
    #    CapCut -100..100 → ffmpeg eq:
    #      brightness  -1..1     (CapCut / 250)
    #      contrast    0.5..2.0  (1 + CapCut/100 * 0.8)
    #      saturation  0..3.0    (1 + CapCut/100)
    b = grade.brightness / 250.0
    c = 1.0 + grade.contrast / 100.0 * 0.8
    s = 1.0 + grade.saturation / 100.0
    eq_params: list[str] = []
    if abs(b) > 0.001:
        eq_params.append(f"brightness={b:.4f}")
    if abs(c - 1.0) > 0.005:
        eq_params.append(f"contrast={c:.4f}")
    if abs(s - 1.0) > 0.005:
        eq_params.append(f"saturation={s:.4f}")
    if eq_params:
        parts.append("eq=" + ":".join(eq_params))

    # 3. Temperature → colorbalance (negative = cooler: less red, more blue)
    if abs(grade.temperature) > 1:
        t = grade.temperature / 100.0 * 0.20  # map to ±0.20
        parts.append(
            f"colorbalance="
            f"rs={-t * 0.60:.4f}:rm={-t * 0.40:.4f}:"
            f"gs={-t * 0.30:.4f}:gm={-t * 0.20:.4f}:"
            f"bs={t * 0.60:.4f}:bm={t * 0.40:.4f}"
        )

    if abs(getattr(grade, "tint", 0.0)) > 1:
        tint = getattr(grade, "tint", 0.0) / 100.0 * 0.18
        parts.append(
            f"colorbalance="
            f"gm={-tint * 0.50:.4f}:gs={-tint * 0.35:.4f}:"
            f"rm={tint * 0.25:.4f}:bm={tint * 0.25:.4f}"
        )

    # 4. Tonal curve: blacks / shadows / highlights / whites
    need_curves = any(
        abs(v) > 1
        for v in [grade.blacks, grade.shadows, grade.highlights, grade.whites]
    )
    if need_curves:
        def adj(base: float, val: float) -> float:
            return max(0.0, min(1.0, base + val / 100.0 * 0.15))

        pts = [
            (0.00, 0.00),
            (0.05, adj(0.05, grade.blacks)),
            (0.25, adj(0.25, grade.shadows)),
            (0.75, adj(0.75, grade.highlights)),
            (0.85, adj(0.85, grade.whites)),
            (1.00, 1.00),
        ]
        curve = " ".join(f"{x:.2f}/{y:.3f}" for x, y in pts)
        parts.append(f"curves=master='{curve}'")

    if abs(getattr(grade, "vibrance", 0.0)) > 1:
        vib = 1.0 + getattr(grade, "vibrance", 0.0) / 100.0 * 0.6
        parts.append(f"eq=saturation={max(0.0, vib):.4f}")

    # 5. Sharpen via unsharp mask
    if grade.sharpen > 0.5:
        amount = grade.sharpen / 100.0 * 1.5  # 0..100 → 0..1.5
        parts.append(f"unsharp=5:5:{amount:.2f}:3:3:0")

    return ",".join(parts)
