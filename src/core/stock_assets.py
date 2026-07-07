from __future__ import annotations

import json
import math
import mimetypes
import os
import random
import re
import wave
import time
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.api_settings import load_env_file, mask_secret, update_env_file


STOCK_ENV_NAMES = [
    "PEXELS_API_KEY",
    "PIXABAY_API_KEY",
    "UNSPLASH_APP_ID",
    "UNSPLASH_ACCESS_KEY",
    "UNSPLASH_SECRET_KEY",
    "FREESOUND_API_KEY",
    "FREESOUND_CLIENT_ID",
    "FREESOUND_CLIENT_SECRET",
]


@dataclass(frozen=True)
class StockProvider:
    id: str
    label: str
    media_kinds: tuple[str, ...]
    required_env: tuple[str, ...]


PROVIDERS: dict[str, StockProvider] = {
    "cortacerto": StockProvider("cortacerto", "CortaCerto", ("audio",), ()),
    "pexels": StockProvider("pexels", "Pexels", ("video", "image"), ("PEXELS_API_KEY",)),
    "pixabay": StockProvider("pixabay", "Pixabay", ("video", "image"), ("PIXABAY_API_KEY",)),
    "unsplash": StockProvider("unsplash", "Unsplash", ("image",), ("UNSPLASH_ACCESS_KEY",)),
    "freesound": StockProvider("freesound", "Freesound", ("audio",), ("FREESOUND_API_KEY",)),
}

BUILTIN_SFX: tuple[dict[str, Any], ...] = (
    {
        "id": "cc_magic_sparkle_open",
        "title": "Magic Sparkle Open",
        "author": "CortaCerto",
        "duration_s": 1.25,
        "category": "magic",
    },
    {
        "id": "cc_typing_text_fast",
        "title": "Typing Text Fast",
        "author": "CortaCerto",
        "duration_s": 1.35,
        "category": "typing",
    },
    {
        "id": "cc_paper_page_turn",
        "title": "Paper Page Turn",
        "author": "CortaCerto",
        "duration_s": 1.05,
        "category": "paper",
    },
    {
        "id": "cc_pencil_draw_reveal",
        "title": "Pencil Draw Reveal",
        "author": "CortaCerto",
        "duration_s": 1.45,
        "category": "draw",
    },
    {
        "id": "cc_soft_whoosh_text",
        "title": "Soft Whoosh Text",
        "author": "CortaCerto",
        "duration_s": 0.85,
        "category": "transition",
    },
    {
        "id": "cc_pop_bubble_ui",
        "title": "Pop Bubble UI",
        "author": "CortaCerto",
        "duration_s": 0.55,
        "category": "ui",
    },
    {
        "id": "cc_notification_chime",
        "title": "Notification Chime",
        "author": "CortaCerto",
        "duration_s": 1.0,
        "category": "ui",
    },
)


def stock_cache_root() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / "CortaCerto" / "assets" / "stock"
    return Path.home() / ".cortacerto" / "assets" / "stock"


def stock_settings(env_file: Path = Path(".env")) -> dict[str, Any]:
    values = load_env_file(env_file)
    providers = []
    for provider in PROVIDERS.values():
        configured = all(bool(values.get(name) or os.environ.get(name)) for name in provider.required_env)
        providers.append({
            "id": provider.id,
            "label": provider.label,
            "media_kinds": list(provider.media_kinds),
            "configured": configured,
            "required_env": list(provider.required_env),
        })
    keys = {
        name: {
            "configured": bool(os.environ.get(name) or values.get(name)),
            "masked": mask_secret(os.environ.get(name) or values.get(name) or ""),
        }
        for name in STOCK_ENV_NAMES
    }
    return {"providers": providers, "keys": keys, "cache_root": str(stock_cache_root())}


def update_stock_settings(updates: dict[str, str], env_file: Path = Path(".env")) -> dict[str, Any]:
    clean = {
        key: str(value).strip()
        for key, value in updates.items()
        if key in STOCK_ENV_NAMES and str(value).strip()
    }
    if clean:
        update_env_file(clean, env_file)
    return stock_settings(env_file)


def search_stock_assets(provider: str, query: str, media_type: str, per_page: int = 12) -> list[dict[str, Any]]:
    provider = provider.lower().strip()
    query = query.strip()
    media_type = media_type.lower().strip()
    per_page = max(1, min(int(per_page or 12), 24))
    if provider not in PROVIDERS:
        raise ValueError("Fonte de midia desconhecida")
    if not query:
        return []
    if provider == "cortacerto":
        _ensure_builtin_sfx_assets()
        q = query.lower()
        return [
            asset for asset in list_downloaded_assets()
            if asset.get("provider") == "cortacerto"
            and asset.get("type") == "audio"
            and (
                q in str(asset.get("title") or "").lower()
                or q in str(asset.get("category") or "").lower()
                or q in "generated local sfx magic typing paper draw transition ui"
            )
        ][:per_page]

    if provider == "pexels":
        return _search_pexels(query, media_type, per_page)
    if provider == "pixabay":
        return _search_pixabay(query, media_type, per_page)
    if provider == "unsplash":
        return _search_unsplash(query, media_type, per_page)
    if provider == "freesound":
        return _search_freesound(query, media_type, per_page)
    return []


def list_downloaded_assets() -> list[dict[str, Any]]:
    _ensure_builtin_sfx_assets()
    assets: list[dict[str, Any]] = []
    for metadata_path in stock_cache_root().glob("*/*/*.json"):
        try:
            data = json.loads(metadata_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        local_path = data.get("local_path")
        if local_path and Path(local_path).is_file():
            assets.append(data)
    assets.sort(key=lambda item: str(item.get("downloaded_at") or ""), reverse=True)
    return assets


def _ensure_builtin_sfx_assets() -> None:
    root = stock_cache_root() / "cortacerto" / "audio"
    root.mkdir(parents=True, exist_ok=True)
    for spec in BUILTIN_SFX:
        asset_id = str(spec["id"])
        wav_path = root / f"{asset_id}.wav"
        meta_path = wav_path.with_suffix(".wav.json")
        duration_s = float(spec["duration_s"])
        if not wav_path.is_file():
            _write_builtin_sfx_wav(wav_path, asset_id, duration_s)
        metadata = {
            "id": asset_id,
            "provider": "cortacerto",
            "type": "audio",
            "title": str(spec["title"]),
            "author": str(spec["author"]),
            "license": "CortaCerto Generated SFX - royalty-free local",
            "source_url": "local://cortacerto/generated-sfx",
            "download_url": "",
            "thumbnail_url": "",
            "duration_s": duration_s,
            "width": None,
            "height": None,
            "local_path": str(wav_path),
            "metadata_path": str(meta_path),
            "downloaded_at": "builtin",
            "category": str(spec.get("category") or "sfx"),
        }
        try:
            meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            continue


def _write_builtin_sfx_wav(path: Path, asset_id: str, duration_s: float) -> None:
    sample_rate = 44100
    total = max(1, int(sample_rate * duration_s))
    rnd = random.Random(asset_id)
    samples: list[int] = []
    for i in range(total):
        t = i / sample_rate
        x = 0.0
        if "magic" in asset_id:
            env = max(0.0, 1.0 - t / duration_s)
            x += 0.34 * env * math.sin(2 * math.pi * (880 + 260 * t) * t)
            x += 0.22 * env * math.sin(2 * math.pi * (1320 + 520 * t) * t)
            if int(t * 24) % 5 == 0:
                x += 0.08 * env * (rnd.random() * 2 - 1)
        elif "typing" in asset_id:
            hit = int(t * 15)
            local = (t * 15) - hit
            if local < 0.055:
                env = (1 - local / 0.055) ** 2
                x += 0.46 * env * (rnd.random() * 2 - 1)
                x += 0.15 * env * math.sin(2 * math.pi * 2300 * t)
        elif "paper" in asset_id:
            env = math.sin(min(1.0, t / duration_s) * math.pi)
            sweep = 400 + 1800 * (t / duration_s)
            x += 0.16 * env * math.sin(2 * math.pi * sweep * t)
            x += 0.28 * env * (rnd.random() * 2 - 1)
        elif "pencil" in asset_id:
            env = 0.55 + 0.45 * math.sin(2 * math.pi * 5 * t)
            x += 0.24 * env * (rnd.random() * 2 - 1)
            x += 0.08 * math.sin(2 * math.pi * 1750 * t)
        elif "whoosh" in asset_id:
            p = t / duration_s
            env = math.sin(p * math.pi)
            x += 0.33 * env * (rnd.random() * 2 - 1)
            x += 0.18 * env * math.sin(2 * math.pi * (250 + 1700 * p) * t)
        elif "pop" in asset_id:
            env = max(0.0, 1.0 - t / duration_s) ** 4
            x += 0.48 * env * math.sin(2 * math.pi * (220 + 900 * t) * t)
        elif "chime" in asset_id:
            env = max(0.0, 1.0 - t / duration_s) ** 1.6
            x += 0.28 * env * math.sin(2 * math.pi * 660 * t)
            x += 0.22 * env * math.sin(2 * math.pi * 990 * t)
            x += 0.14 * env * math.sin(2 * math.pi * 1320 * t)
        else:
            x = 0.15 * math.sin(2 * math.pi * 440 * t)
        samples.append(int(max(-1.0, min(1.0, x)) * 32767))

    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(int(v).to_bytes(2, "little", signed=True) for v in samples))


def download_stock_asset(asset: dict[str, Any]) -> dict[str, Any]:
    provider = str(asset.get("provider") or "").lower()
    media_type = str(asset.get("type") or "image").lower()
    url = str(asset.get("download_url") or asset.get("preview_url") or "")
    if provider not in PROVIDERS:
        raise ValueError("Fonte de midia desconhecida")
    if media_type not in {"video", "image", "audio"}:
        raise ValueError("Tipo de midia invalido")
    if not url.startswith(("http://", "https://")):
        raise ValueError("URL de download invalida")

    root = stock_cache_root() / provider / media_type
    root.mkdir(parents=True, exist_ok=True)
    ext = _extension_from_url(url, media_type)
    safe_name = _safe_filename(str(asset.get("title") or asset.get("id") or uuid.uuid4().hex))
    asset_id = _safe_filename(str(asset.get("id") or uuid.uuid4().hex))
    local_path = root / f"{safe_name}-{asset_id}{ext}"

    request = urllib.request.Request(url, headers={"User-Agent": "CortaCerto/1.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        local_path.write_bytes(response.read())

    metadata = {
        "id": str(asset.get("id") or asset_id),
        "provider": provider,
        "type": media_type,
        "title": str(asset.get("title") or local_path.stem),
        "author": str(asset.get("author") or ""),
        "license": str(asset.get("license") or ""),
        "source_url": str(asset.get("source_url") or ""),
        "download_url": url,
        "thumbnail_url": str(asset.get("thumbnail_url") or ""),
        "duration_s": _optional_float(asset.get("duration_s")),
        "width": _optional_int(asset.get("width")),
        "height": _optional_int(asset.get("height")),
        "local_path": str(local_path),
        "metadata_path": str(local_path.with_suffix(local_path.suffix + ".json")),
        "downloaded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    Path(metadata["metadata_path"]).write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def _env_value(name: str) -> str:
    return str(os.environ.get(name) or load_env_file().get(name) or "").strip()


def _get_json(url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
    request = urllib.request.Request(url, headers=headers or {"User-Agent": "CortaCerto/1.0"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8", errors="replace"))


def _search_pexels(query: str, media_type: str, per_page: int) -> list[dict[str, Any]]:
    key = _env_value("PEXELS_API_KEY")
    if not key:
        raise PermissionError("Configure PEXELS_API_KEY")
    headers = {"Authorization": key, "User-Agent": "CortaCerto/1.0"}
    q = urllib.parse.urlencode({"query": query, "per_page": per_page, "orientation": "landscape"})
    if media_type == "video":
        data = _get_json(f"https://api.pexels.com/videos/search?{q}", headers)
        return [_pexels_video(item) for item in data.get("videos", [])]
    data = _get_json(f"https://api.pexels.com/v1/search?{q}", headers)
    return [_pexels_photo(item) for item in data.get("photos", [])]


def _search_pixabay(query: str, media_type: str, per_page: int) -> list[dict[str, Any]]:
    key = _env_value("PIXABAY_API_KEY")
    if not key:
        raise PermissionError("Configure PIXABAY_API_KEY")
    per_page = max(3, per_page)
    params = {"key": key, "q": query, "per_page": per_page, "safesearch": "true"}
    if media_type == "video":
        data = _get_json(f"https://pixabay.com/api/videos/?{urllib.parse.urlencode(params)}")
        return [_pixabay_video(item) for item in data.get("hits", [])]
    params["image_type"] = "photo"
    data = _get_json(f"https://pixabay.com/api/?{urllib.parse.urlencode(params)}")
    return [_pixabay_image(item) for item in data.get("hits", [])]


def _search_unsplash(query: str, media_type: str, per_page: int) -> list[dict[str, Any]]:
    if media_type != "image":
        return []
    key = _env_value("UNSPLASH_ACCESS_KEY")
    if not key:
        raise PermissionError("Configure UNSPLASH_ACCESS_KEY")
    params = {"query": query, "per_page": per_page, "client_id": key}
    data = _get_json(f"https://api.unsplash.com/search/photos?{urllib.parse.urlencode(params)}")
    return [_unsplash_photo(item) for item in data.get("results", [])]


def _search_freesound(query: str, media_type: str, per_page: int) -> list[dict[str, Any]]:
    if media_type != "audio":
        return []
    key = _env_value("FREESOUND_API_KEY")
    if not key:
        raise PermissionError("Configure FREESOUND_API_KEY")
    params = {
        "query": query,
        "page_size": per_page,
        "fields": "id,name,username,license,duration,previews,url",
        "token": key,
    }
    data = _get_json(f"https://freesound.org/apiv2/search/text/?{urllib.parse.urlencode(params)}")
    return [_freesound_audio(item) for item in data.get("results", [])]


def _pexels_video(item: dict[str, Any]) -> dict[str, Any]:
    files = sorted(item.get("video_files") or [], key=lambda f: int(f.get("width") or 0), reverse=True)
    best = next((f for f in files if str(f.get("file_type", "")).startswith("video/")), files[0] if files else {})
    return _asset(
        provider="pexels", media_type="video", asset_id=item.get("id"), title="Video Pexels",
        author=(item.get("user") or {}).get("name"), license_name="Pexels License",
        source_url=item.get("url"), download_url=best.get("link"), thumbnail_url=item.get("image"),
        duration_s=item.get("duration"), width=best.get("width") or item.get("width"), height=best.get("height") or item.get("height"),
    )


def _pexels_photo(item: dict[str, Any]) -> dict[str, Any]:
    src = item.get("src") or {}
    return _asset("pexels", "image", item.get("id"), item.get("alt") or "Foto Pexels",
                  item.get("photographer"), "Pexels License", item.get("url"),
                  src.get("large2x") or src.get("large") or src.get("original"),
                  src.get("medium") or src.get("small"), width=item.get("width"), height=item.get("height"))


def _pixabay_video(item: dict[str, Any]) -> dict[str, Any]:
    videos = item.get("videos") or {}
    best = videos.get("medium") or videos.get("small") or videos.get("tiny") or videos.get("large") or {}
    return _asset("pixabay", "video", item.get("id"), item.get("tags") or "Video Pixabay",
                  item.get("user"), "Pixabay Content License", item.get("pageURL"),
                  best.get("url"), item.get("picture_id") and f"https://i.vimeocdn.com/video/{item.get('picture_id')}_640x360.jpg",
                  duration_s=item.get("duration"), width=best.get("width"), height=best.get("height"))


def _pixabay_image(item: dict[str, Any]) -> dict[str, Any]:
    return _asset("pixabay", "image", item.get("id"), item.get("tags") or "Imagem Pixabay",
                  item.get("user"), "Pixabay Content License", item.get("pageURL"),
                  item.get("largeImageURL") or item.get("webformatURL"), item.get("previewURL"),
                  width=item.get("imageWidth"), height=item.get("imageHeight"))


def _unsplash_photo(item: dict[str, Any]) -> dict[str, Any]:
    urls = item.get("urls") or {}
    user = item.get("user") or {}
    return _asset("unsplash", "image", item.get("id"), (item.get("alt_description") or item.get("description") or "Foto Unsplash"),
                  user.get("name"), "Unsplash License", (item.get("links") or {}).get("html"),
                  urls.get("full") or urls.get("regular") or urls.get("raw"), urls.get("small"),
                  width=item.get("width"), height=item.get("height"))


def _freesound_audio(item: dict[str, Any]) -> dict[str, Any]:
    previews = item.get("previews") or {}
    url = previews.get("preview-hq-mp3") or previews.get("preview-lq-mp3") or previews.get("preview-hq-ogg")
    return _asset("freesound", "audio", item.get("id"), item.get("name") or "Som Freesound",
                  item.get("username"), item.get("license"), item.get("url"), url, "",
                  duration_s=item.get("duration"))


def _asset(
    provider: str, media_type: str, asset_id: Any, title: Any, author: Any,
    license_name: Any, source_url: Any, download_url: Any, thumbnail_url: Any = "",
    duration_s: Any = None, width: Any = None, height: Any = None,
) -> dict[str, Any]:
    return {
        "id": str(asset_id or uuid.uuid4().hex),
        "provider": provider,
        "type": media_type,
        "title": str(title or "Asset"),
        "author": str(author or ""),
        "license": str(license_name or ""),
        "source_url": str(source_url or ""),
        "download_url": str(download_url or ""),
        "thumbnail_url": str(thumbnail_url or ""),
        "duration_s": _optional_float(duration_s),
        "width": _optional_int(width),
        "height": _optional_int(height),
    }


def _optional_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None and value != "" else None
    except (TypeError, ValueError):
        return None


def _safe_filename(value: str) -> str:
    safe = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower()).strip("-")
    return safe[:80] or uuid.uuid4().hex


def _extension_from_url(url: str, media_type: str) -> str:
    parsed = urllib.parse.urlparse(url)
    ext = Path(parsed.path).suffix.lower()
    if ext and len(ext) <= 8:
        return ext
    guessed = mimetypes.guess_extension(mimetypes.guess_type(url)[0] or "")
    if guessed:
        return guessed
    return {"video": ".mp4", "audio": ".mp3", "image": ".jpg"}.get(media_type, ".bin")
