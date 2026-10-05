import asyncio
import json
import os
import time
import traceback
import uuid
from pathlib import Path
from typing import Any, Dict

from dotenv import load_dotenv
from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.providers.gemini import GeminiProvider
from app.services.duration_validator import DEFAULT_WPM
from app.services.script_generator import (
    SECTION_MODE_MIN_WORDS,
    SECTION_PARTS,
    StoryPipeline,
    storyboard_to_markdown,
    storyboard_to_voiceover,
)
from app.services.srt_parser import SrtParser

# backend/app/api/routes.py -> parents[0]=api [1]=app [2]=backend [3]=project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# .env dibaca dari project root. Utamakan file di backend/ kalau ada (dev),
# supaya creds lokal bisa menimpa shared config tanpa menyentuhnya.
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=True)
load_dotenv(PROJECT_ROOT / ".env", override=False)

router = APIRouter(prefix="/api", tags=["plan1"])

PROJECTS_DIR = PROJECT_ROOT / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_SRT_BYTES = 20 * 1024 * 1024  # 20 MB

# Job generate yang sedang berjalan: job_id -> status/progress.
# In-memory saja. State final tetap ada di folder projects/, jadi job yang
# selesai tidak perlu disimpan selamanya di sini.
_JOBS: Dict[str, Dict[str, Any]] = {}

OUTPUT_FILES = {
    "storyboard.json": "application/json",
    "storyboard.md": "text/markdown; charset=utf-8",
    "voiceover.txt": "text/plain; charset=utf-8",
    "story_map.json": "application/json",
}


def _srt_from_upload(content: bytes, filename: str) -> str:
    if not filename.lower().endswith(".srt"):
        raise HTTPException(400, "Hanya file .srt yang diperbolehkan.")
    if not content.strip():
        raise HTTPException(400, "File subtitle kosong.")
    if len(content) > MAX_SRT_BYTES:
        raise HTTPException(400, "File subtitle terlalu besar (maks 20 MB).")
    text = content.decode("utf-8", errors="ignore")
    if "�" in text and "utf-8" not in text[:200].lower():
        text = content.decode("latin-1", errors="ignore")
    return text


def _persist(project_id: str, storyboard, analysis: Dict[str, Any], report: Dict[str, Any]) -> Path:
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)

    payload = storyboard.model_dump()
    payload["report"] = report
    (folder / "storyboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "storyboard.md").write_text(storyboard_to_markdown(storyboard), encoding="utf-8")
    (folder / "voiceover.txt").write_text(storyboard_to_voiceover(storyboard), encoding="utf-8")
    (folder / "story_map.json").write_text(
        json.dumps(analysis, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return folder


@router.get("/health")
def health() -> Dict[str, Any]:
    """Cek kesiapan service. Tidak memanggil Gemini — cukup membaca konfigurasi."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    return {
        "status": "ok",
        "gemini_configured": bool(api_key) and not api_key.startswith("YOUR_"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        "default_wpm": DEFAULT_WPM,
        "max_retry": int(os.getenv("MAX_RETRY", "2")),
        # Ditampilkan biar jelas server yang jalan kode baru atau belum:
        # target di atas script_part_threshold_words ditulis per-bagian.
        "script_part_threshold_words": SECTION_MODE_MIN_WORDS,
        "script_parts_when_split": SECTION_PARTS,
        "projects_dir": str(PROJECTS_DIR),
    }


@router.post("/generate")
async def generate(
    subtitle: UploadFile = File(...),
    target_duration: int = Form(15),
) -> Dict[str, Any]:
    """Generate blocking (kompatibilitas). Untuk subtitle panjang, pakai /jobs."""
    if target_duration < 1 or target_duration > 180:
        raise HTTPException(400, "Target durasi harus antara 1 sampai 180 menit.")
    return await _run_generate(subtitle.filename or "film.srt", await subtitle.read(), target_duration)


@router.post("/jobs")
async def create_job(
    subtitle: UploadFile = File(...),
    target_duration: int = Form(15),
) -> Dict[str, Any]:
    """Mulai generate di background, kembalikan job_id. Pemanggil poll /jobs/{id}.

    Subtitle panjang (1500+ cue) butuh belasan menit; pendekatan ini
    menghindari timeout HTTP di sisi browser.
    """
    if target_duration < 1 or target_duration > 180:
        raise HTTPException(400, "Target durasi harus antara 1 sampai 180 menit.")

    content = await subtitle.read()
    try:
        _srt_from_upload(content, subtitle.filename or "")
    except HTTPException as exc:
        raise exc

    provider = GeminiProvider()
    if not provider.is_configured():
        raise HTTPException(503, "Gemini API Key belum dikonfigurasi.")

    job_id = uuid.uuid4().hex[:12]
    _JOBS[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": "Menunggu proses...",
        "progress": 0,
        "started_at": time.time(),
        "source_subtitle": subtitle.filename or "film.srt",
        "target_duration": target_duration,
    }

    asyncio.create_task(
        asyncio.to_thread(_job_worker, job_id, subtitle.filename or "film.srt", content, target_duration)
    )
    return {"success": True, "job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    return job


def _job_worker(job_id: str, filename: str, content: bytes, target_minutes: int) -> None:
    def progress(stage: str, data: Dict[str, Any]) -> None:
        job = _JOBS.get(job_id)
        if not job:
            return
        job["stage"] = stage
        pct, msg = _stage_progress(stage, data)
        job["progress"] = pct
        job["message"] = msg
        job["detail"] = data

    try:
        result = _generate_sync(filename, content, target_minutes, progress)
    except HTTPException as exc:
        job = _JOBS.get(job_id)
        if job:
            job.update(status="error", stage="error", message=str(exc.detail), progress=100)
    except Exception as exc:  # noqa: BLE001
        job = _JOBS.get(job_id)
        if job:
            # Simpan traceback penuh + debug file terbaru. Job error yang
            # output-dependent (Gemini kirim field bertipe salah) tidak bisa
            # ditebak dari pesan error saja -- butuh jejak file di debug_log/.
            job.update(
                status="error",
                stage="error",
                message=f"Gagal: {type(exc).__name__}: {exc}",
                progress=100,
                traceback=traceback.format_exc()[-6000:],
                debug_hint=(
                    "Lihat backend/debug_log/ untuk respons mentah Gemini "
                    "yang menyebabkan error ini."
                ),
            )
    else:
        job = _JOBS.get(job_id)
        if job:
            elapsed = round(time.time() - job["started_at"], 1)
            job.update(
                status="done",
                stage="done",
                progress=100,
                message=f"Selesai dalam {elapsed / 60:.1f} menit.",
                project_id=result["project_id"],
                elapsed_sec=elapsed,
            )


async def _run_generate(filename: str, content: bytes, target_minutes: int) -> Dict[str, Any]:
    return await asyncio.to_thread(_generate_sync, filename, content, target_minutes, None)


def _generate_sync(
    filename: str,
    content: bytes,
    target_minutes: int,
    progress=None,
) -> Dict[str, Any]:
    started = time.time()

    srt_text = _srt_from_upload(content, filename)

    provider = GeminiProvider()
    if not provider.is_configured():
        raise HTTPException(503, "Gemini API Key belum dikonfigurasi.")

    parser = SrtParser()
    entries = parser.parse(srt_text)
    if not entries:
        raise HTTPException(400, "Subtitle gagal diparse. Pastikan format .srt valid.")

    pipeline = StoryPipeline(
        parser=parser,
        provider=provider,
        wpm=int(os.getenv("DEFAULT_WPM", str(DEFAULT_WPM))),
        max_retry=int(os.getenv("MAX_RETRY", "2")),
    )

    try:
        result = pipeline.run(srt_text, target_minutes, filename, progress=progress)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    storyboard = result["storyboard"]
    report = dict(result["report"])
    report["elapsed_sec"] = round(time.time() - started, 1)

    project_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    _persist(project_id, storyboard, result["analysis"], report)

    payload = storyboard.model_dump()
    payload["report"] = report
    return {"success": True, "project_id": project_id, "storyboard": payload}


def _stage_progress(stage: str, data: Dict[str, Any]) -> tuple[int, str]:
    if stage == "parse":
        if data.get("cue_count"):
            return (
                5,
                f"Subtitle diparse: {data['cue_count']} cue, "
                f"{data.get('duration_sec', 0) / 60:.1f} menit film",
            )
        return (2, "Membaca file subtitle...")
    if stage == "analysis":
        if data.get("phase") == "start":
            n = data.get("chunks", 1)
            return (15, f"Menganalisis cerita ({n} bagian subtitle)...")
        return (45, "Analisis cerita selesai.")
    if stage == "script":
        phase = data.get("phase")
        if phase == "start":
            return (50, "Menulis naskah + blueprint klip...")
        if phase == "attempt":
            return (55, f"Generating (percobaan {data.get('attempt')}/{data.get('max')})...")
        if phase == "retry_needed":
            return (65, f"Durasi meleset {data.get('diff_pct')}%, minta revisi...")
        return (75, "Naskah selesai.")
    if stage == "validate":
        return (95, "Memvalidasi clip dan durasi...")
    return (0, "")


@router.get("/projects")
def list_projects(limit: int = 20) -> Dict[str, Any]:
    if not PROJECTS_DIR.exists():
        return {"projects": []}
    items = []
    for folder in sorted(PROJECTS_DIR.iterdir(), reverse=True):
        if not folder.is_dir():
            continue
        sb_file = folder / "storyboard.json"
        meta: Dict[str, Any] = {"project_id": folder.name}
        if sb_file.exists():
            try:
                data = json.loads(sb_file.read_text(encoding="utf-8"))
                meta.update(
                    {
                        "title": data.get("project", {}).get("title", ""),
                        "source_subtitle": data.get("project", {}).get("source_subtitle", ""),
                        "target_duration_minutes": data.get("project", {}).get("target_duration_minutes"),
                        "total_clips": data.get("summary", {}).get("total_clips", 0),
                        "total_sections": data.get("summary", {}).get("total_sections", 0),
                    }
                )
            except (json.JSONDecodeError, OSError):
                pass
        items.append(meta)
        if len(items) >= limit:
            break
    return {"projects": items}


@router.get("/projects/{project_id}/{filename}")
def download(project_id: str, filename: str) -> FileResponse:
    if filename not in OUTPUT_FILES:
        raise HTTPException(404, f"File '{filename}' tidak dikenal.")

    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise HTTPException(404, "Project tidak ditemukan.")

    path = folder / filename
    if not path.exists():
        raise HTTPException(404, f"File '{filename}' tidak ditemukan.")

    return FileResponse(path, media_type=OUTPUT_FILES[filename], filename=filename)