"""Ensure ffmpeg/ffprobe are available to the Python process."""
import os
import shutil
import subprocess
import winreg
from pathlib import Path


_FFMPEG_BIN: str | None = None

_FALLBACK_LOCATIONS = [
    # WinGet/App Execution Aliases
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps",
    # WinGet (Gyan.FFmpeg)
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages",
    # Chocolatey
    Path("C:/ProgramData/chocolatey/bin"),
    # Scoop
    Path(os.environ.get("USERPROFILE", "")) / "scoop/shims",
    Path("C:/ProgramData/scoop/shims"),
    # Manual installs
    Path("C:/ffmpeg/bin"),
    Path("C:/Program Files/ffmpeg/bin"),
    Path("C:/tools/ffmpeg/bin"),
]


def _read_registry_path() -> str:
    """Read User PATH from registry (picks up winget changes without restart)."""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, "Path")
            return value
    except Exception:
        return ""


def _find_in_winget_packages(base: Path) -> str | None:
    """Search WinGet package directory for any ffmpeg.exe."""
    if not base.is_dir():
        return None
    for candidate in base.glob("Gyan.FFmpeg*/**/ffmpeg.exe"):
        return str(candidate.parent)
    return None


def _append_path(path: Path | str) -> None:
    path_s = str(path)
    current = os.environ.get("PATH", "")
    parts = [p.lower() for p in current.split(os.pathsep) if p]
    if path_s.lower() not in parts:
        os.environ["PATH"] = path_s + os.pathsep + current


def ensure_ffmpeg() -> str:
    """
    Return the resolved path to the ffmpeg executable.
    Also patches os.environ["PATH"] so subprocess calls find it.
    Raises RuntimeError if ffmpeg cannot be found.
    """
    global _FFMPEG_BIN
    if _FFMPEG_BIN:
        return _FFMPEG_BIN

    # 1. Merge current process PATH with User registry PATH
    user_path = _read_registry_path()
    current_path = os.environ.get("PATH", "")
    windows_apps = str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WindowsApps")
    merged = current_path + os.pathsep + user_path + os.pathsep + windows_apps
    os.environ["PATH"] = merged

    found = shutil.which("ffmpeg")
    if found:
        _FFMPEG_BIN = found
        return found

    # 2. Scan fallback locations
    for loc in _FALLBACK_LOCATIONS:
        if loc.is_dir() and (loc / "ffmpeg.exe").exists():
            _append_path(loc)
            _FFMPEG_BIN = str(loc / "ffmpeg.exe")
            return _FFMPEG_BIN

    # 3. WinGet glob scan
    winget_base = Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft/WinGet/Packages"
    winget_bin = _find_in_winget_packages(winget_base)
    if winget_bin:
        _append_path(winget_bin)
        _FFMPEG_BIN = str(Path(winget_bin) / "ffmpeg.exe")
        return _FFMPEG_BIN

    raise RuntimeError(
        "ffmpeg não encontrado.\n\n"
        "Instale via: winget install --id Gyan.FFmpeg\n"
        "Depois reinicie o terminal e tente novamente."
    )


def ffmpeg() -> str:
    return ensure_ffmpeg()


def ffprobe() -> str:
    ensure_ffmpeg()
    probe = shutil.which("ffprobe")
    if probe:
        return probe
    bin_dir = Path(ensure_ffmpeg()).parent
    probe_path = bin_dir / "ffprobe.exe"
    if probe_path.exists():
        return str(probe_path)
    raise RuntimeError("ffprobe não encontrado.")


# -- GPU / encoder detection --------------------------------------------------

_ENCODER_CACHE: tuple[str, list[str]] | None = None


def detect_video_encoder(force: bool = False) -> tuple[str, list[str]]:
    """
    Detect best available H.264 encoder.
    Returns (encoder_name, extra_ffmpeg_args).
    Order: NVENC - AMF - QSV - libx264 (CPU fallback).
    Result is cached after first call.
    """
    global _ENCODER_CACHE
    if _ENCODER_CACHE and not force:
        return _ENCODER_CACHE

    ensure_ffmpeg()
    _base = [
        ffmpeg(), "-y", "-f", "lavfi", "-i", "color=black:s=64x64:d=0.1",
        "-frames:v", "3",
    ]

    # NVENC requires minimum ~256x144; use 320x240 to be safe.
    # Use mp4 output; some encoders fail with -f null.
    null_out = "NUL" if os.name == "nt" else "/dev/null"
    _base = [
        ffmpeg(), "-y", "-f", "lavfi",
        "-i", "color=black:s=320x240:r=30:d=1",
        "-frames:v", "30",
    ]
    candidates = [
        ("h264_nvenc", ["-preset", "p4", "-cq", "19", "-b:v", "0"]),
        ("h264_amf",   ["-quality", "balanced", "-qp_i", "19", "-qp_p", "21"]),
        ("h264_qsv",   ["-global_quality", "19", "-preset", "medium"]),
    ]
    for name, extra in candidates:
        try:
            cmd = _base + ["-c:v", name] + extra + ["-f", "mp4", "-movflags", "frag_keyframe", null_out]
            r = subprocess.run(cmd, capture_output=True, timeout=12)
            if r.returncode == 0:
                _ENCODER_CACHE = (name, extra)
                return _ENCODER_CACHE
        except Exception:
            continue

    _ENCODER_CACHE = ("libx264", ["-crf", "18", "-preset", "fast"])
    return _ENCODER_CACHE


def encoder_label() -> str:
    name, _ = detect_video_encoder()
    labels = {
        "h264_nvenc": "NVIDIA NVENC",
        "h264_amf":   "AMD AMF",
        "h264_qsv":   "Intel QSV",
        "libx264":    "CPU (x264)",
    }
    return labels.get(name, name)
