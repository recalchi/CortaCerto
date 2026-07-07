from __future__ import annotations

import os
import json
import hmac
import base64
import hashlib
import secrets
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


def profile_store_path() -> Path:
    base = Path(os.environ.get("APPDATA", Path.home())) / "CortaCerto"
    base.mkdir(parents=True, exist_ok=True)
    return base / "user_profiles.json"


@dataclass
class UserProfile:
    id: str
    name: str = "Editor"
    email: str = ""
    avatar_path: str = ""
    plan: str = "Local"
    role: str = "member"
    status: str = "active"
    avatar_zoom: float = 1.0
    avatar_offset_x: float = 0.0
    avatar_offset_y: float = 0.0
    avatar_rotation_deg: float = 0.0
    auth_enabled: bool = False
    password_hash: str = ""
    password_salt: str = ""
    last_login_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0

    def initials(self) -> str:
        parts = [p for p in self.name.replace("@", " ").split() if p]
        if not parts:
            return "ED"
        if len(parts) == 1:
            return parts[0][:2].upper()
        return f"{parts[0][0]}{parts[-1][0]}".upper()


def _default_profile() -> UserProfile:
    now = time.time()
    return UserProfile(id=f"local_{uuid.uuid4().hex[:10]}", role="master", created_at=now, updated_at=now)


def _profile_from_raw(raw: dict[str, Any]) -> UserProfile:
    fields = UserProfile.__dataclass_fields__
    clean = {key: raw.get(key) for key in fields if key in raw}
    profile = UserProfile(**clean)
    return profile


def load_profile_store(path: Path | None = None) -> dict[str, Any]:
    store_path = path or profile_store_path()
    try:
        raw = json.loads(store_path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("invalid profile store")
    except Exception:
        profile = _default_profile()
        raw = {"active_profile_id": profile.id, "profiles": [asdict(profile)]}
        save_profile_store(raw, store_path)
    profiles = raw.get("profiles")
    if not isinstance(profiles, list) or not profiles:
        profile = _default_profile()
        raw["profiles"] = [asdict(profile)]
        raw["active_profile_id"] = profile.id
        save_profile_store(raw, store_path)
    if not raw.get("active_profile_id"):
        raw["active_profile_id"] = raw["profiles"][0].get("id")
    migrated = False
    has_master = any(str(p.get("role") or "").lower() == "master" for p in raw.get("profiles", []))
    for ix, profile in enumerate(raw.get("profiles", [])):
        if not profile.get("role"):
            profile["role"] = "master" if ix == 0 and not has_master else "member"
            migrated = True
        if not profile.get("status"):
            profile["status"] = "active"
            migrated = True
    if not has_master and raw.get("profiles"):
        raw["profiles"][0]["role"] = "master"
        migrated = True
    if migrated:
        save_profile_store(raw, store_path)
    return raw


def save_profile_store(store: dict[str, Any], path: Path | None = None) -> None:
    store_path = path or profile_store_path()
    store_path.parent.mkdir(parents=True, exist_ok=True)
    store_path.write_text(json.dumps(store, ensure_ascii=False, indent=2), encoding="utf-8")


def list_profiles() -> list[UserProfile]:
    store = load_profile_store()
    out: list[UserProfile] = []
    for raw in store.get("profiles", []):
        try:
            out.append(_profile_from_raw(raw))
        except TypeError:
            continue
    if not out:
        profile = _default_profile()
        save_profile_store({"active_profile_id": profile.id, "profiles": [asdict(profile)]})
        return [profile]
    return out


def active_profile() -> UserProfile:
    store = load_profile_store()
    active_id = str(store.get("active_profile_id") or "")
    profiles = list_profiles()
    for profile in profiles:
        if profile.id == active_id:
            return profile
    return profiles[0]


def is_master(profile: UserProfile | None = None) -> bool:
    target = profile or active_profile()
    return str(target.role or "").strip().lower() == "master"


def set_active_profile(profile_id: str) -> UserProfile:
    store = load_profile_store()
    profiles = list_profiles()
    ids = {p.id for p in profiles}
    if profile_id not in ids:
        raise ValueError("perfil nao encontrado")
    store["active_profile_id"] = profile_id
    save_profile_store(store)
    return active_profile()


def profile_is_unlocked(profile_id: str) -> bool:
    store = load_profile_store()
    sessions = store.get("sessions") or {}
    session = sessions.get(profile_id, {})
    return bool(session.get("unlocked") or session.get("remember_local"))


def profile_remember_local(profile_id: str) -> bool:
    store = load_profile_store()
    sessions = store.get("sessions") or {}
    return bool((sessions.get(profile_id) or {}).get("remember_local"))


def set_profile_remember_local(profile_id: str, remember: bool) -> None:
    store = load_profile_store()
    sessions = dict(store.get("sessions") or {})
    current = dict(sessions.get(profile_id) or {})
    current["remember_local"] = bool(remember)
    current["remembered_at" if remember else "remember_cleared_at"] = time.time()
    sessions[profile_id] = current
    store["sessions"] = sessions
    save_profile_store(store)


def lock_profile(profile_id: str | None = None) -> None:
    store = load_profile_store()
    target = profile_id or str(store.get("active_profile_id") or "")
    sessions = dict(store.get("sessions") or {})
    if target in sessions:
        sessions[target] = {
            **sessions[target],
            "unlocked": False,
            "remember_local": False,
            "locked_at": time.time(),
        }
    store["sessions"] = sessions
    save_profile_store(store)


def authenticate_profile(profile_id: str, password: str, remember_local: bool = False) -> bool:
    profile = next((p for p in list_profiles() if p.id == profile_id), None)
    if not profile:
        return False
    if str(profile.status or "").strip().lower() == "suspended":
        return False
    if not profile.auth_enabled:
        set_active_profile(profile_id)
        set_profile_remember_local(profile_id, remember_local)
        return True
    if not verify_password(profile, password):
        return False
    profile.last_login_at = time.time()
    upsert_profile(profile, actor=profile)
    store = load_profile_store()
    sessions = dict(store.get("sessions") or {})
    sessions[profile_id] = {
        "unlocked": True,
        "unlocked_at": profile.last_login_at,
        "remember_local": bool(remember_local),
        **({"remembered_at": profile.last_login_at} if remember_local else {}),
    }
    store["sessions"] = sessions
    store["active_profile_id"] = profile_id
    save_profile_store(store)
    return True


def set_profile_password(
    profile: UserProfile,
    password: str,
    actor: UserProfile | None = None,
    make_active: bool = True,
) -> UserProfile:
    clean = str(password or "")
    if not clean:
        profile.auth_enabled = False
        profile.password_hash = ""
        profile.password_salt = ""
        return upsert_profile(profile, actor=actor, make_active=make_active)
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", clean.encode("utf-8"), salt, 200_000)
    profile.auth_enabled = True
    profile.password_salt = base64.b64encode(salt).decode("ascii")
    profile.password_hash = base64.b64encode(digest).decode("ascii")
    return upsert_profile(profile, actor=actor, make_active=make_active)


def verify_password(profile: UserProfile, password: str) -> bool:
    if not profile.auth_enabled:
        return True
    try:
        salt = base64.b64decode(profile.password_salt.encode("ascii"))
        expected = base64.b64decode(profile.password_hash.encode("ascii"))
    except Exception:
        return False
    digest = hashlib.pbkdf2_hmac("sha256", str(password or "").encode("utf-8"), salt, 200_000)
    return hmac.compare_digest(digest, expected)


def upsert_profile(
    profile: UserProfile,
    actor: UserProfile | None = None,
    make_active: bool = True,
) -> UserProfile:
    store = load_profile_store()
    profiles = list_profiles()
    actor = actor or active_profile()
    existing = next((item for item in profiles if item.id == profile.id), None)
    if existing and existing.id != actor.id and not is_master(actor):
        raise PermissionError("apenas master pode alterar outros usuarios")
    if existing and existing.role == "master" and profile.role != "master":
        masters = [item for item in profiles if item.role == "master" and item.id != existing.id]
        if not masters:
            raise ValueError("nao e permitido remover o ultimo usuario master")
    now = time.time()
    profile.updated_at = now
    if not profile.created_at:
        profile.created_at = now
    replaced = False
    next_profiles: list[dict[str, Any]] = []
    for item in profiles:
        if item.id == profile.id:
            next_profiles.append(asdict(profile))
            replaced = True
        else:
            next_profiles.append(asdict(item))
    if not replaced:
        next_profiles.append(asdict(profile))
    store["profiles"] = next_profiles
    if make_active:
        store["active_profile_id"] = profile.id
    elif not store.get("active_profile_id"):
        store["active_profile_id"] = actor.id
    save_profile_store(store)
    return profile


def create_profile(
    name: str = "Editor",
    email: str = "",
    role: str = "member",
    make_active: bool = True,
) -> UserProfile:
    profiles = list_profiles()
    actor = active_profile()
    if profiles and not is_master(actor):
        raise PermissionError("apenas master pode criar outros usuarios")
    profile = _default_profile()
    profile.name = (name or "Editor").strip() or "Editor"
    profile.email = (email or "").strip()
    profile.role = "master" if not profiles else (role if role in {"master", "member"} else "member")
    profile.plan = "Master" if profile.role == "master" else "Local"
    return upsert_profile(profile, actor=actor, make_active=make_active)


def remove_profile(profile_id: str) -> UserProfile:
    store = load_profile_store()
    actor = active_profile()
    if profile_id != actor.id and not is_master(actor):
        raise PermissionError("apenas master pode remover outros usuarios")
    current_profiles = list_profiles()
    target = next((p for p in current_profiles if p.id == profile_id), None)
    if target and target.role == "master":
        remaining_masters = [p for p in current_profiles if p.id != profile_id and p.role == "master"]
        if not remaining_masters:
            raise ValueError("nao e permitido remover o ultimo usuario master")
    profiles = [p for p in current_profiles if p.id != profile_id]
    if not profiles:
        profiles = [_default_profile()]
    active_id = str(store.get("active_profile_id") or "")
    if active_id == profile_id:
        active_id = profiles[0].id
    store["profiles"] = [asdict(p) for p in profiles]
    store["active_profile_id"] = active_id
    save_profile_store(store)
    return active_profile()
