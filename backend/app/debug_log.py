"""
Jejak debug: simpan respons mentah Gemini per panggilan supaya error yang
output-dependent (mis. Gemini kirim field bertipe list) bisa dibaca langsung
dari disk, bukan ditebak dari traceback.

Aktif lewat GEMINI_DEBUG=1 di .env (default: aktif).
File ditulis ke backend/debug_log/<timestamp>-<label>.json
"""
import json
import os
import re
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

# app/debug_log.py -> parents[0]=app, [1]=backend, [2]=root project.
# Taruh di backend/debug_log supaya tidak tercampur dengan folder projects/.
DEBUG_DIR = Path(__file__).resolve().parents[1] / "debug_log"


def debug_enabled() -> bool:
    return os.getenv("GEMINI_DEBUG", "1").strip().lower() not in ("0", "false", "no")


def _safe_name(label: str) -> str:
    slug = re.sub(r"[^a-z0-9_-]+", "-", str(label).lower()).strip("-")
    return slug or "call"


# Field yang WAJIB object. Kalau Gemini kirim list di sini, pemanggil .get()
# akan melempar AttributeError -- sekarang itu ketahuan seketika dari log.
OBJECT_FIELDS = ("project", "story_arc")


def log_call(
    label: str,
    payload: Any,
    *,
    duration_sec: float = 0.0,
    error: Optional[BaseException] = None,
) -> Optional[str]:
    """Tulis satu panggilan Gemini ke disk. Tidak pernah gagalkan pipeline.

    Kegagalan logging diabaikan sepenuhnya: disk penuh atau path bermasalah
    tidak boleh menjatuhkan generate yang sedang jalan.
    """
    if not debug_enabled():
        return None
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        seq = len(list(DEBUG_DIR.glob(f"*-{_safe_name(label)}.json")))
        path = DEBUG_DIR / f"{stamp}-{seq:02d}-{_safe_name(label)}.json"

        body: Dict[str, Any] = {
            "label": label,
            "at": stamp,
            "duration_sec": round(duration_sec, 2),
            "model": os.getenv("GEMINI_MODEL", "?"),
        }
        try:
            body["payload"] = payload
        except Exception:  # noqa: BLE001
            body["payload"] = repr(payload)[:4000]

        if error is not None:
            body["error"] = f"{type(error).__name__}: {error}"
            body["traceback"] = traceback.format_exc()[-4000:]

        # Ringkas bentuk payload: field bertipe salah langsung kelihatan tanpa
        # harus scroll seluruh isi respons.
        if isinstance(payload, dict):
            body["shape"] = {
                key: (
                    f"list[{len(value)}]" if isinstance(value, list)
                    else f"dict[{len(value)}]" if isinstance(value, dict)
                    else type(value).__name__
                )
                for key, value in payload.items()
            }
            warnings = [
                f"{key} tiba sebagai {type(payload[key]).__name__} "
                f"(harusnya dict/object) -- .get() akan gagal"
                for key in OBJECT_FIELDS
                if isinstance(payload.get(key), list)
            ]
            if warnings:
                body["type_warnings"] = warnings

        path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        return path.name
    except Exception:  # noqa: BLE001
        return None


def log_stage(name: str, data: Any) -> None:
    """Catat milestone pipeline (parse/analysis/script/validate/save)."""
    if not debug_enabled():
        return
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3]
        path = DEBUG_DIR / f"{stamp}-stage-{_safe_name(name)}.txt"
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
    except Exception:  # noqa: BLE001
        return