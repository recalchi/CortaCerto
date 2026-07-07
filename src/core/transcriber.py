"""
Video transcription — speech-to-text producing timed segments.

Provider auto-detection order:
  1. OpenAI Whisper API  — if OPENAI_API_KEY configured (best quality, requires internet)
  2. faster-whisper      — local, fast, no internet (pip install faster-whisper)
  3. openai-whisper      — local, reference model (pip install openai-whisper)
  4. Raises TranscriptionUnavailable if none available

SRT / VTT helpers are pure functions with no external dependencies.
Burn-in is handled by pipeline.py via ffmpeg.
"""
from __future__ import annotations

import io
import json
import os
import re
import subprocess
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional


class TranscriptionUnavailable(RuntimeError):
    """Raised when no transcription provider is available."""


# ── Data model ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TranscriptSegment:
    start_s:    float
    end_s:      float
    text:       str
    confidence: float = 1.0


@dataclass
class Transcript:
    segments:  list[TranscriptSegment] = field(default_factory=list)
    language:  str = ""
    provider:  str = "unknown"

    @property
    def full_text(self) -> str:
        return " ".join(s.text.strip() for s in self.segments)

    @property
    def duration_s(self) -> float:
        return self.segments[-1].end_s if self.segments else 0.0


# ── SRT / VTT export ──────────────────────────────────────────────────────────

def _fmt_srt_time(seconds: float) -> str:
    ms    = int(round(seconds * 1000))
    h     = ms // 3_600_000; ms %= 3_600_000
    m     = ms // 60_000;    ms %= 60_000
    s     = ms // 1_000;     ms %= 1_000
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _fmt_vtt_time(seconds: float) -> str:
    return _fmt_srt_time(seconds).replace(",", ".")


def to_srt(transcript: Transcript) -> str:
    lines: list[str] = []
    for idx, seg in enumerate(transcript.segments, start=1):
        lines.append(str(idx))
        lines.append(f"{_fmt_srt_time(seg.start_s)} --> {_fmt_srt_time(seg.end_s)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def to_vtt(transcript: Transcript) -> str:
    lines = ["WEBVTT", ""]
    for seg in transcript.segments:
        lines.append(f"{_fmt_vtt_time(seg.start_s)} --> {_fmt_vtt_time(seg.end_s)}")
        lines.append(seg.text.strip())
        lines.append("")
    return "\n".join(lines)


def write_srt(transcript: Transcript, path: str) -> str:
    Path(path).write_text(to_srt(transcript), encoding="utf-8")
    return path


def write_vtt(transcript: Transcript, path: str) -> str:
    Path(path).write_text(to_vtt(transcript), encoding="utf-8")
    return path


# ── Provider: OpenAI Whisper API ───────────────────────────────────────────────

_OPENAI_TRANSCRIPTION_URL = "https://api.openai.com/v1/audio/transcriptions"


def _transcribe_openai_api(
    audio_path: str,
    api_key: str,
    language: Optional[str] = None,
) -> Transcript:
    """Call OpenAI Whisper API using multipart/form-data via urllib."""
    boundary = "----CortaCertoBoundary"
    file_data = Path(audio_path).read_bytes()
    filename  = Path(audio_path).name

    parts: list[bytes] = []
    # model field
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\nwhisper-1\r\n".encode()
    )
    # response_format: verbose_json gives us timestamps
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"response_format\"\r\n\r\nverbose_json\r\n".encode()
    )
    # timestamp_granularities
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"timestamp_granularities[]\"\r\n\r\nsegment\r\n".encode()
    )
    if language:
        parts.append(
            f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\n{language}\r\n".encode()
        )
    # file field
    parts.append(
        f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: audio/mpeg\r\n\r\n".encode() + file_data + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())

    body = b"".join(parts)
    req  = urllib.request.Request(
        _OPENAI_TRANSCRIPTION_URL,
        data=body,
        headers={
            "Authorization":  f"Bearer {api_key}",
            "Content-Type":   f"multipart/form-data; boundary={boundary}",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        payload = ""
        try:
            payload = exc.read().decode("utf-8", errors="replace")
        except Exception:
            payload = ""
        lower = payload.lower()
        if exc.code in (401, 403) or "invalid_api_key" in lower:
            raise TranscriptionUnavailable(
                "OPENAI_API_KEY invalida ou sem permissao para transcricao."
            ) from exc
        if exc.code == 429:
            raise TranscriptionUnavailable(
                "Limite de uso da API atingido (HTTP 429)."
            ) from exc
        raise TranscriptionUnavailable(
            f"Falha na API de transcricao (HTTP {exc.code})."
        ) from exc

    segments = []
    for seg in data.get("segments", []):
        segments.append(TranscriptSegment(
            start_s=float(seg["start"]),
            end_s=float(seg["end"]),
            text=str(seg["text"]).strip(),
        ))
    transcript = Transcript(
        segments=segments,
        language=data.get("language", ""),
        provider="openai-whisper-api",
    )
    try:
        from src.core.api_usage import record_openai_usage
        audio_seconds = max((segment.end_s for segment in segments), default=0.0)
        record_openai_usage(
            feature="transcricao",
            model="whisper-1",
            audio_seconds=audio_seconds,
            ok=True,
        )
    except Exception:
        pass
    return transcript


# ── Provider: faster-whisper (local) ──────────────────────────────────────────

def _transcribe_faster_whisper(
    audio_path: str,
    model_size: str = "small",
    language: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Transcript:
    try:
        from faster_whisper import WhisperModel  # type: ignore
    except ImportError as exc:
        raise TranscriptionUnavailable(
            "faster-whisper não instalado. Execute: pip install faster-whisper"
        ) from exc

    if on_progress:
        on_progress(f"Carregando modelo Whisper ({model_size})…")
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    segs, info = model.transcribe(audio_path, language=language, beam_size=5)

    segments: list[TranscriptSegment] = []
    for i, seg in enumerate(segs):
        if on_progress and i % 10 == 0:
            on_progress(f"Transcrevendo… segmento {i + 1}")
        segments.append(TranscriptSegment(
            start_s=float(seg.start),
            end_s=float(seg.end),
            text=str(seg.text).strip(),
            confidence=float(getattr(seg, "avg_logprob", 0.0)),
        ))
    return Transcript(
        segments=segments,
        language=getattr(info, "language", ""),
        provider=f"faster-whisper:{model_size}",
    )


# ── Provider: openai-whisper (local) ──────────────────────────────────────────

def _transcribe_openai_whisper(
    audio_path: str,
    model_size: str = "small",
    language: Optional[str] = None,
    on_progress: Optional[Callable[[str], None]] = None,
) -> Transcript:
    try:
        import whisper  # type: ignore
    except ImportError as exc:
        raise TranscriptionUnavailable(
            "openai-whisper não instalado. Execute: pip install openai-whisper"
        ) from exc

    if on_progress:
        on_progress(f"Carregando modelo Whisper ({model_size})…")
    model = whisper.load_model(model_size)
    if on_progress:
        on_progress("Transcrevendo áudio…")
    result = model.transcribe(
        audio_path,
        language=language,
        verbose=False,
    )
    segments: list[TranscriptSegment] = []
    for seg in result.get("segments", []):
        segments.append(TranscriptSegment(
            start_s=float(seg["start"]),
            end_s=float(seg["end"]),
            text=str(seg["text"]).strip(),
        ))
    return Transcript(
        segments=segments,
        language=result.get("language", ""),
        provider=f"whisper:{model_size}",
    )


# ── Audio extraction helper ────────────────────────────────────────────────────

def _extract_audio_for_transcription(
    video_path: str,
    ffmpeg_cmd: str = "ffmpeg",
) -> str:
    """Extract audio to a temporary WAV file (16 kHz mono, optimal for Whisper)."""
    tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    tmp.close()
    subprocess.run(
        [
            ffmpeg_cmd, "-y", "-i", video_path,
            "-ac", "1", "-ar", "16000",
            "-acodec", "pcm_s16le",
            tmp.name,
        ],
        check=True,
        capture_output=True,
    )
    return tmp.name


# ── Public API ─────────────────────────────────────────────────────────────────

def transcribe_video(
    video_path: str,
    provider: str = "auto",
    model_size: str = "small",
    language: Optional[str] = None,
    openai_api_key: Optional[str] = None,
    ffmpeg_cmd: str = "ffmpeg",
    on_progress: Optional[Callable[[str], None]] = None,
) -> Transcript:
    """
    Transcribe *video_path*.

    provider values:
      "auto"            — try openai-api > faster-whisper > openai-whisper
      "openai-api"      — OpenAI Whisper API (requires key)
      "faster-whisper"  — local faster-whisper
      "whisper"         — local openai-whisper
    """
    if on_progress:
        on_progress("Extraindo áudio para transcrição…")
    audio_tmp: Optional[str] = None
    try:
        audio_tmp = _extract_audio_for_transcription(video_path, ffmpeg_cmd)

        if provider in ("auto", "openai-api"):
            key = openai_api_key or os.environ.get("OPENAI_API_KEY", "")
            if key and provider != "auto" or (provider == "auto" and key):
                if on_progress:
                    on_progress("Transcrevendo via OpenAI Whisper API…")
                try:
                    return _transcribe_openai_api(audio_tmp, key, language)
                except Exception:
                    if provider == "openai-api":
                        raise

        if provider in ("auto", "faster-whisper"):
            try:
                return _transcribe_faster_whisper(audio_tmp, model_size, language, on_progress)
            except TranscriptionUnavailable:
                if provider == "faster-whisper":
                    raise
            except Exception:
                if provider == "faster-whisper":
                    raise

        if provider in ("auto", "whisper"):
            try:
                return _transcribe_openai_whisper(audio_tmp, model_size, language, on_progress)
            except TranscriptionUnavailable:
                if provider == "whisper":
                    raise
            except Exception:
                if provider == "whisper":
                    raise

        raise TranscriptionUnavailable(
            "Nenhum provider de transcrição disponível.\n"
            "Instale um: pip install faster-whisper  OU  pip install openai-whisper\n"
            "Ou configure OPENAI_API_KEY para usar a API do OpenAI."
        )
    finally:
        if audio_tmp:
            try:
                os.unlink(audio_tmp)
            except OSError:
                pass


def available_providers(openai_api_key: Optional[str] = None) -> list[str]:
    """Return names of transcription providers that can be used right now."""
    found: list[str] = []
    if openai_api_key or os.environ.get("OPENAI_API_KEY"):
        found.append("openai-api")
    try:
        import faster_whisper  # type: ignore  # noqa: F401
        found.append("faster-whisper")
    except ImportError:
        pass
    try:
        import whisper  # type: ignore  # noqa: F401
        found.append("whisper")
    except ImportError:
        pass
    return found
