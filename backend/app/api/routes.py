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
from app.schemas.storyboard import Storyboard
from app.services.duration_validator import DEFAULT_WPM
from app.services.clip_validator import enforce_segment_clip_budget, sync_segments_from_sections
from app.services.plan2_renderer import Plan2Renderer
from app.services.script_generator import (
    SECTION_MODE_MIN_WORDS,
    SECTION_PARTS,
    StoryPipeline,
    storyboard_to_markdown,
    storyboard_to_voiceover,
)
from app.services.srt_parser import SrtParser
from app.services.tts_service import (
    AVAILABLE_VOICES,
    generate_tts_sync,
    generate_storyboard_tts_segments,
    merge_audio_video,
)

# backend/app/api/routes.py -> parents[0]=api [1]=app [2]=backend [3]=project root
PROJECT_ROOT = Path(__file__).resolve().parents[3]

# .env dibaca dari project root. Utamakan file di backend/ kalau ada (dev),
# supaya creds lokal bisa menimpa shared config tanpa menyentuhnya.
load_dotenv(PROJECT_ROOT / "backend" / ".env", override=True)
load_dotenv(PROJECT_ROOT / ".env", override=False)

router = APIRouter(prefix="/api", tags=["plan1", "plan2", "tts"])

PROJECTS_DIR = PROJECT_ROOT / "projects"
PROJECTS_DIR.mkdir(parents=True, exist_ok=True)

MAX_SRT_BYTES = 20 * 1024 * 1024  # 20 MB

SUPPORTED_GEMINI_MODELS = (
    "gemini-3.8-flash",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
)

# Job generate yang sedang berjalan: job_id -> status/progress.
_JOBS: Dict[str, Dict[str, Any]] = {}
_PLAN2_JOBS: Dict[str, Dict[str, Any]] = {}

OUTPUT_FILES = {
    "storyboard.json": "application/json",
    "storyboard.md": "text/markdown; charset=utf-8",
    "voiceover.txt": "text/plain; charset=utf-8",
    "story_map.json": "application/json",
    "storytelling_no_audio.mp4": "video/mp4",
    "voiceover.mp3": "audio/mpeg",
    "final_storytelling.mp4": "video/mp4",
}


def _validate_gemini_model(model: str | None) -> str:
    selected = (model or os.getenv("GEMINI_MODEL", "gemini-3.6-flash")).strip()
    if selected not in SUPPORTED_GEMINI_MODELS:
        raise HTTPException(
            400,
            f"Model Gemini tidak didukung: {selected}. Pilih salah satu: {', '.join(SUPPORTED_GEMINI_MODELS)}",
        )
    return selected


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


def _persist_storyboard_artifacts(project_id: str, storyboard: Storyboard, report: Dict[str, Any] | None = None) -> Path:
    """Persist a storyboard after a post-generation step such as TTS preflight."""
    folder = PROJECTS_DIR / project_id
    folder.mkdir(parents=True, exist_ok=True)
    payload = storyboard.model_dump()
    if report is not None:
        payload["report"] = report
    (folder / "storyboard.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "storyboard.md").write_text(storyboard_to_markdown(storyboard), encoding="utf-8")
    (folder / "voiceover.txt").write_text(storyboard_to_voiceover(storyboard), encoding="utf-8")
    return folder


@router.get("/health")
def health() -> Dict[str, Any]:
    """Cek kesiapan service. Tidak memanggil Gemini — cukup membaca konfigurasi."""
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    return {
        "status": "ok",
        "gemini_configured": bool(api_key) and not api_key.startswith("YOUR_"),
        "gemini_model": os.getenv("GEMINI_MODEL", "gemini-3.6-flash"),
        "gemini_models": list(SUPPORTED_GEMINI_MODELS),
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
    language: str = Form("id"),
    gemini_model: str = Form(""),
) -> Dict[str, Any]:
    """Generate blocking (kompatibilitas). Untuk subtitle panjang, pakai /jobs."""
    if target_duration < 1 or target_duration > 180:
        raise HTTPException(400, "Target durasi harus antara 1 sampai 180 menit.")
    model = _validate_gemini_model(gemini_model)
    return await _run_generate(subtitle.filename or "film.srt", await subtitle.read(), target_duration, language, model)


@router.post("/jobs")
async def create_job(
    subtitle: UploadFile = File(...),
    target_duration: int = Form(15),
    language: str = Form("id"),
    gemini_model: str = Form(""),
) -> Dict[str, Any]:
    """Mulai generate di background, kembalikan job_id. Pemanggil poll /jobs/{id}.

    Subtitle panjang (1500+ cue) butuh belasan menit; pendekatan ini
    menghindari timeout HTTP di sisi browser.
    """
    if target_duration < 1 or target_duration > 180:
        raise HTTPException(400, "Target durasi harus antara 1 sampai 180 menit.")
    model = _validate_gemini_model(gemini_model)

    content = await subtitle.read()
    try:
        _srt_from_upload(content, subtitle.filename or "")
    except HTTPException as exc:
        raise exc

    provider = GeminiProvider(model_override=model)
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
        "language": language,
        "gemini_model": model,
    }

    asyncio.create_task(
        asyncio.to_thread(_job_worker, job_id, subtitle.filename or "film.srt", content, target_duration, language, model)
    )
    return {"success": True, "job_id": job_id}


@router.get("/jobs/{job_id}")
def job_status(job_id: str) -> Dict[str, Any]:
    job = _JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job tidak ditemukan.")
    return job


def _job_worker(job_id: str, filename: str, content: bytes, target_minutes: int, language: str = "id", gemini_model: str = "") -> None:
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
        result = _generate_sync(filename, content, target_minutes, language, progress, gemini_model)
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


async def _run_generate(filename: str, content: bytes, target_minutes: int, language: str = "id", gemini_model: str = "") -> Dict[str, Any]:
    return await asyncio.to_thread(_generate_sync, filename, content, target_minutes, language, None, gemini_model)


def _generate_sync(
    filename: str,
    content: bytes,
    target_minutes: int,
    language: str = "id",
    progress=None,
    gemini_model: str = "",
) -> Dict[str, Any]:
    started = time.time()

    srt_text = _srt_from_upload(content, filename)

    provider = GeminiProvider(model_override=_validate_gemini_model(gemini_model))
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
        result = pipeline.run(srt_text, target_minutes, filename, language=language, progress=progress)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(502, str(exc)) from exc

    storyboard = result["storyboard"]
    report = dict(result["report"])
    report["gemini_model"] = getattr(provider, "model", _validate_gemini_model(gemini_model))
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


@router.get("/projects/{project_id}/video")
def get_project_video(project_id: str) -> FileResponse:
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise HTTPException(404, "Project tidak ditemukan.")

    final_video = folder / "final_storytelling.mp4"
    if final_video.exists():
        return FileResponse(final_video, media_type="video/mp4", filename="final_storytelling.mp4")

    video_path = folder / "storytelling_no_audio.mp4"
    if not video_path.exists():
        raise HTTPException(404, "File storytelling_no_audio.mp4 belum dirender.")

    return FileResponse(video_path, media_type="video/mp4", filename="storytelling_no_audio.mp4")


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


# -------------------------------------------------------------------
# Plan 2 Endpoints (Video Cut, Transform, Sequence & Render)
# -------------------------------------------------------------------


@router.post("/plan2/render")
async def plan2_render(
    video: UploadFile = File(None),
    intro_video: UploadFile = File(None),
    video_path: str = Form(None),
    project_id: str = Form(...),
    voice: str = Form("id-ID-ArdiNeural"),
    include_tts: bool = Form(True),
) -> Dict[str, Any]:
    """Mulai job render Plan 2 (potong video, transform, concat, TTS, dan merge audio)."""
    proj_folder = PROJECTS_DIR / project_id
    if not proj_folder.is_dir():
        raise HTTPException(404, f"Project '{project_id}' tidak ditemukan.")

    sb_file = proj_folder / "storyboard.json"
    if not sb_file.exists():
        raise HTTPException(404, f"File storyboard.json tidak ditemukan untuk project '{project_id}'.")

    try:
        sb_data = json.loads(sb_file.read_text(encoding="utf-8"))
        storyboard = Storyboard.model_validate(sb_data)
    except Exception as exc:
        raise HTTPException(400, f"Gagal membaca storyboard.json: {exc}") from exc

    resolved_video_path: Path | None = None
    resolved_intro_path: Path | None = None

    if video_path and video_path.strip():
        resolved_video_path = Path(video_path.strip())
        if not resolved_video_path.exists() or not resolved_video_path.is_file():
            raise HTTPException(400, f"File video lokal '{video_path}' tidak ditemukan.")
    elif video:
        plan2_dir = proj_folder / "plan2"
        plan2_dir.mkdir(parents=True, exist_ok=True)
        filename = video.filename or "input_video.mp4"
        resolved_video_path = plan2_dir / filename
        with open(resolved_video_path, "wb") as buffer:
            while chunk := await video.read(1024 * 1024):
                buffer.write(chunk)
    else:
        raise HTTPException(400, "Harus menyertakan file video upload atau video_path lokal.")

    if intro_video:
        plan2_dir = proj_folder / "plan2"
        intro_dir = plan2_dir / "intro"
        intro_dir.mkdir(parents=True, exist_ok=True)
        intro_name = Path(intro_video.filename or "intro.mp4").name
        resolved_intro_path = intro_dir / intro_name
        with open(resolved_intro_path, "wb") as buffer:
            while chunk := await intro_video.read(1024 * 1024):
                buffer.write(chunk)

    job_id = uuid.uuid4().hex[:12]
    _PLAN2_JOBS[job_id] = {
        "job_id": job_id,
        "project_id": project_id,
        "status": "queued",
        "stage": "queued",
        "message": "Menunggu proses render Plan 2...",
        "progress": 0,
        "started_at": time.time(),
        "video_path": str(resolved_video_path),
        "intro_path": str(resolved_intro_path) if resolved_intro_path else None,
    }

    asyncio.create_task(
        asyncio.to_thread(
            _plan2_job_worker,
            job_id,
            resolved_video_path,
            resolved_intro_path,
            storyboard,
            project_id,
            voice,
            include_tts,
        )
    )

    return {"success": True, "job_id": job_id, "project_id": project_id}


@router.get("/plan2/jobs/{job_id}")
def plan2_job_status(job_id: str) -> Dict[str, Any]:
    job = _PLAN2_JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "Job Plan 2 tidak ditemukan.")
    return job


def _plan2_job_worker(
    job_id: str,
    video_path: Path,
    intro_path: Path | None,
    storyboard: Storyboard,
    project_id: str,
    voice: str = "id-ID-ArdiNeural",
    include_tts: bool = True,
) -> None:
    def progress(stage: str, data: Dict[str, Any]) -> None:
        job = _PLAN2_JOBS.get(job_id)
        if not job:
            return
        job["stage"] = stage
        if stage == "validate_video":
            if data.get("phase") == "start":
                job["progress"] = 5
                job["message"] = "Memeriksa metadata video film..."
            else:
                job["progress"] = 15
                job["message"] = "Validasi video selesai."
        elif stage == "validate_storyboard":
            if data.get("phase") == "start":
                job["progress"] = 18
                job["message"] = "Memvalidasi storyboard..."
            else:
                job["progress"] = 25
                job["message"] = f"Storyboard valid ({data.get('total_clips')} klip)."
        elif stage == "processing_clips":
            curr = data.get("current", 1)
            tot = data.get("total", 1)
            pct = 25 + int((curr / max(tot, 1)) * 55)
            job["progress"] = min(pct, 80)
            job["message"] = f"Memotong & transform clip {curr}/{tot} ({data.get('beat', '')})..."
            job["detail"] = data
        elif stage == "concat":
            if data.get("phase") == "start":
                job["progress"] = 82
                job["message"] = "Menggabungkan seluruh clip (concat demuxer)..."
            else:
                job["progress"] = 88
                job["message"] = "Penggabungan clip selesai."
        elif stage == "generate_tts":
            if data.get("phase") == "start":
                job["progress"] = 90
                job["message"] = f"Menggenerasi Voice Over TTS ({data.get('voice', '')})..."
            else:
                job["progress"] = 94
                job["message"] = "Voice Over TTS selesai digenerate."
        elif stage == "merge_audio":
            if data.get("phase") == "start":
                job["progress"] = 95
                job["message"] = "Memasang audio Voice Over ke video final..."
            else:
                job["progress"] = 98
                job["message"] = "Penggabungan audio dan video selesai."
        elif stage == "verify_final":
            if data.get("phase") == "start":
                job["progress"] = 99
                job["message"] = "Memverifikasi video final..."
            else:
                job["progress"] = 100
                job["message"] = "Verifikasi video final selesai."

    try:
        renderer = Plan2Renderer(PROJECTS_DIR)
        result = renderer.render(
            video_path=video_path,
            intro_path=intro_path,
            storyboard=storyboard,
            project_id=project_id,
            voice=voice,
            include_tts=include_tts,
            progress=progress,
        )
    except Exception as exc:
        job = _PLAN2_JOBS.get(job_id)
        if job:
            job.update(
                status="error",
                stage="error",
                message=f"Gagal render Plan 2: {exc}",
                progress=100,
                traceback=traceback.format_exc()[-6000:],
            )
    else:
        job = _PLAN2_JOBS.get(job_id)
        if job:
            elapsed = round(time.time() - job["started_at"], 1)
            job.update(
                status="done",
                stage="done",
                progress=100,
                message=f"Render Plan 2 selesai dalam {elapsed} detik!",
                output_path=result.get("output_path"),
                output_filename=result.get("output_filename"),
                report=result.get("report"),
                elapsed_sec=elapsed,
            )


# -------------------------------------------------------------------
# TTS & Audio-Video Merge Endpoints
# -------------------------------------------------------------------


@router.post("/projects/{project_id}/tts-preflight")
def generate_project_tts_preflight(
    project_id: str,
    voice: str = Form("id-ID-ArdiNeural"),
    rate: str = Form("+0%"),
    pitch: str = Form("+0Hz"),
) -> Dict[str, Any]:
    """Generate TTS per segment and persist exact audio durations for Plan 2."""
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise HTTPException(404, "Project tidak ditemukan.")

    sb_file = folder / "storyboard.json"
    if not sb_file.exists():
        raise HTTPException(404, "storyboard.json tidak ditemukan.")

    try:
        raw = json.loads(sb_file.read_text(encoding="utf-8"))
        storyboard = Storyboard.model_validate(raw)
        audio_path, durations = generate_storyboard_tts_segments(
            storyboard=storyboard,
            voice=voice,
            rate=rate,
            pitch=pitch,
            work_dir=folder / "tts_segments",
            final_output_path=folder / "voiceover.mp3",
        )
        # Propagate duration TTS ke nested segment UI, lalu pastikan jumlah
        # klip dihitung memakai durasi audio aktual (tanpa request Gemini).
        storyboard = sync_segments_from_sections(storyboard)
        clip_issues = enforce_segment_clip_budget(storyboard, use_audio_duration=True)
        storyboard = sync_segments_from_sections(storyboard)
    except Exception as exc:
        raise HTTPException(502, f"TTS preflight gagal: {exc}") from exc

    report = dict(raw.get("report") or {})
    report["tts_preflight"] = {
        "status": "ready",
        "voice": voice,
        "segment_count": len(durations),
        "audio_duration_sec": round(sum(durations.values()), 3),
    }
    if clip_issues:
        report.setdefault("issues", []).extend(clip_issues)
    _persist_storyboard_artifacts(project_id, storyboard, report)

    return {
        "success": True,
        "project_id": project_id,
        "audio_filename": "voiceover.mp3",
        "voice": voice,
        "durations": durations,
        "storyboard": storyboard.model_dump(),
    }


@router.get("/tts/voices")
def get_tts_voices() -> Dict[str, Any]:
    """Daftar model suara TTS yang tersedia."""
    return {"voices": AVAILABLE_VOICES}


@router.post("/projects/{project_id}/tts")
def generate_project_tts(
    project_id: str,
    voice: str = Form("id-ID-ArdiNeural"),
    rate: str = Form("+0%"),
    pitch: str = Form("+0Hz"),
) -> Dict[str, Any]:
    """Generate audio voiceover MP3 dari naskah voiceover project."""
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise HTTPException(404, "Project tidak ditemukan.")

    vo_file = folder / "voiceover.txt"
    sb_file = folder / "storyboard.json"

    text = ""
    storyboard: Storyboard | None = None
    if vo_file.exists():
        text = vo_file.read_text(encoding="utf-8")
    elif sb_file.exists():
        try:
            sb_data = json.loads(sb_file.read_text(encoding="utf-8"))
            storyboard = Storyboard.model_validate(sb_data)
            text = storyboard_to_voiceover(storyboard)
        except Exception:
            text = ""

    if storyboard is not None and storyboard.segments:
        try:
            output_audio_path, durations = generate_storyboard_tts_segments(
                storyboard=storyboard,
                voice=voice,
                rate=rate,
                pitch=pitch,
                work_dir=folder / "tts_segments",
                final_output_path=folder / "voiceover.mp3",
            )
            storyboard = sync_segments_from_sections(storyboard)
            clip_issues = enforce_segment_clip_budget(storyboard, use_audio_duration=True)
            storyboard = sync_segments_from_sections(storyboard)
            raw_report = json.loads(sb_file.read_text(encoding="utf-8")).get("report") or {}
            raw_report["tts_preflight"] = {
                "status": "ready",
                "voice": voice,
                "segment_count": len(durations),
                "audio_duration_sec": round(sum(durations.values()), 3),
            }
            if clip_issues:
                raw_report.setdefault("issues", []).extend(clip_issues)
            _persist_storyboard_artifacts(project_id, storyboard, raw_report)
            return {
                "success": True,
                "project_id": project_id,
                "audio_filename": output_audio_path.name,
                "voice": voice,
                "durations": durations,
            }
        except Exception as exc:
            raise HTTPException(500, f"Gagal generate TTS segment: {exc}") from exc

    if not text.strip():
        raise HTTPException(400, "Naskah voiceover tidak ditemukan untuk project ini.")

    output_audio_path = folder / "voiceover.mp3"

    try:
        generate_tts_sync(
            text=text,
            voice=voice,
            output_path=output_audio_path,
            rate=rate,
            pitch=pitch,
        )
    except Exception as exc:
        raise HTTPException(500, f"Gagal generate TTS: {exc}") from exc

    return {
        "success": True,
        "project_id": project_id,
        "audio_filename": "voiceover.mp3",
        "voice": voice,
    }


@router.post("/projects/{project_id}/merge-audio-video")
def merge_project_audio_video(project_id: str) -> Dict[str, Any]:
    """Gabungkan video storytelling_no_audio.mp4 + voiceover.mp3 menjadi final_storytelling.mp4."""
    folder = PROJECTS_DIR / project_id
    if not folder.is_dir():
        raise HTTPException(404, "Project tidak ditemukan.")

    video_path = folder / "storytelling_no_audio.mp4"
    if not video_path.exists():
        raise HTTPException(404, "Video storytelling_no_audio.mp4 belum dirender di Plan 2.")

    audio_path = folder / "voiceover.mp3"
    if not audio_path.exists():
        raise HTTPException(404, "Audio voiceover.mp3 belum digenerate dengan TTS.")

    output_path = folder / "final_storytelling.mp4"

    try:
        merge_audio_video(
            video_path=video_path,
            audio_path=audio_path,
            output_path=output_path,
        )
    except Exception as exc:
        raise HTTPException(500, f"Gagal menggabungkan audio dan video: {exc}") from exc

    return {
        "success": True,
        "project_id": project_id,
        "output_filename": "final_storytelling.mp4",
        "output_path": str(output_path),
    }
