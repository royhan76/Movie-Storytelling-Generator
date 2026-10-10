"""Service Text-to-Speech (TTS) & Audio-Video Merger.

Menggunakan `edge-tts` (Microsoft Edge Speech TTS API) untuk menghasilkan
voiceover narasi MP3 berkualitas tinggi dengan berbagai pilihan suara
Bahasa Indonesia & Bahasa Inggris.
"""
import asyncio
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import edge_tts


AVAILABLE_VOICES = [
    # Bahasa Indonesia
    {"code": "id-ID-ArdiNeural", "name": "Indonesian — Ardi (Pria / Male)", "lang": "id"},
    {"code": "id-ID-GadisNeural", "name": "Indonesian — Gadis (Wanita / Female)", "lang": "id"},
    # English (US)
    {"code": "en-US-AvaNeural", "name": "English (US) — Ava (Female - Expressive)", "lang": "en"},
    {"code": "en-US-AndrewNeural", "name": "English (US) — Andrew (Male - Expressive)", "lang": "en"},
    {"code": "en-US-BrianNeural", "name": "English (US) — Brian (Male)", "lang": "en"},
    {"code": "en-US-EmmaNeural", "name": "English (US) — Emma (Female)", "lang": "en"},
    {"code": "en-US-GuyNeural", "name": "English (US) — Guy (Male)", "lang": "en"},
    {"code": "en-US-JennyNeural", "name": "English (US) — Jenny (Female)", "lang": "en"},
]


def clean_text_for_tts(text: str) -> str:
    """Bersihkan markdown, header, dan penanda section sebelum dikirim ke TTS."""
    if not text:
        return ""
    # Buang header markdown (contoh: # Header, ## Bagian 1)
    text = re.sub(r"^#+\s*.*$", "", text, flags=re.MULTILINE)
    # Buang bullet points & formatting markdown (*, **, _, ```)
    text = re.sub(r"[\*\_`]", "", text)
    # Buang baris kosong berlebih
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


async def generate_tts_async(
    text: str,
    voice: str = "id-ID-ArdiNeural",
    output_path: Optional[Path] = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Path:
    """Generate audio MP3 dari teks voiceover secara async."""
    cleaned_text = clean_text_for_tts(text)
    if not cleaned_text.strip():
        raise ValueError("Teks voiceover kosong, tidak dapat menghasilkan TTS.")

    if output_path is None:
        output_path = Path("voiceover.mp3")

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    communicate = edge_tts.Communicate(
        text=cleaned_text,
        voice=voice,
        rate=rate,
        pitch=pitch,
    )

    await communicate.save(str(output_path))

    if not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"Gagal menghasilkan file TTS pada '{output_path}'.")

    return output_path


def generate_tts_sync(
    text: str,
    voice: str = "id-ID-ArdiNeural",
    output_path: Optional[Path] = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Path:
    """Wrapper sync untuk generate_tts_async."""
    return asyncio.run(generate_tts_async(text, voice, output_path, rate, pitch))


def concat_audio_files(
    audio_paths: List[Path],
    output_path: Path,
) -> Path:
    """Gabungkan beberapa file audio MP3 menjadi satu file MP3."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("Binary 'ffmpeg' tidak ditemukan di PATH sistem.")

    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    concat_list_file = output_path.parent / "audio_concat.txt"
    lines = [f"file '{Path(p).resolve().as_posix()}'" for p in audio_paths]
    concat_list_file.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        ffmpeg_bin,
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_list_file),
        "-c",
        "copy",
        str(output_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg gagal merender audio concat: {res.stderr}")

    return output_path


def generate_storyboard_tts_sections(
    storyboard: Any,
    voice: str = "id-ID-ArdiNeural",
    work_dir: Optional[Path] = None,
    final_output_path: Optional[Path] = None,
) -> Tuple[Path, Dict[int, float]]:
    """Generate TTS per section, ukur durasi audio per section, dan gabungkan jadi 1 voiceover.mp3.

    Return (voiceover_mp3_path, {section_id: audio_duration_sec})
    """
    from app.services.video_prober import probe_audio

    if work_dir is None:
        work_dir = Path("tts_work")
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)

    if final_output_path is None:
        final_output_path = work_dir / "voiceover.mp3"

    sec_audio_paths: List[Path] = []
    section_durations: Dict[int, float] = {}

    for section in storyboard.sections:
        sec_id = section.section_id
        text = clean_text_for_tts(section.voice_over)
        if not text.strip():
            # Fallback jika voiceover section kosong
            text = "..."

        sec_out_path = work_dir / f"sec_{sec_id:03d}_voiceover.mp3"
        generate_tts_sync(text=text, voice=voice, output_path=sec_out_path)

        # Ukur durasi audio section menggunakan probe_audio
        try:
            meta = probe_audio(sec_out_path)
            dur = float(meta["duration"])
        except Exception:
            dur = 3.0

        section_durations[sec_id] = round(max(dur, 0.5), 2)
        sec_audio_paths.append(sec_out_path)

    # Concat all section audio files into voiceover.mp3
    concat_audio_files(sec_audio_paths, final_output_path)

    return final_output_path, section_durations


def generate_storyboard_tts_segments(
    storyboard: Any,
    voice: str = "id-ID-ArdiNeural",
    work_dir: Optional[Path] = None,
    final_output_path: Optional[Path] = None,
    rate: str = "+0%",
    pitch: str = "+0Hz",
) -> Tuple[Path, Dict[int, float]]:
    """Generate TTS per narration segment and write actual durations back.

    This is the Plan 1 preflight contract. The generated files are kept so
    Plan 2 can reuse the exact audio instead of regenerating text with a
    potentially different duration.
    """
    from app.services.video_prober import probe_audio

    if work_dir is None:
        work_dir = Path("tts_segments")
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    if final_output_path is None:
        final_output_path = work_dir.parent / "voiceover.mp3"

    segments = list(getattr(storyboard, "segments", []) or [])
    if not segments:
        # Backward-compatible storyboard: one segment per section.
        for section in getattr(storyboard, "sections", []) or []:
            segments.extend(getattr(section, "segments", []) or [])
    if not segments:
        raise ValueError("Storyboard tidak memiliki narration segment untuk TTS preflight.")

    audio_paths: List[Path] = []
    durations: Dict[int, float] = {}
    for segment in segments:
        text = clean_text_for_tts(getattr(segment, "text", ""))
        if not text.strip():
            raise ValueError(f"Segment {segment.segment_id} memiliki teks narasi kosong.")

        output_path = work_dir / f"segment_{int(segment.segment_id):04d}.mp3"
        generate_tts_sync(
            text=text,
            voice=voice,
            output_path=output_path,
            rate=rate,
            pitch=pitch,
        )
        meta = probe_audio(output_path)
        duration = round(max(float(meta.get("duration", 0.0)), 0.05), 3)
        relative_audio = output_path.relative_to(work_dir.parent).as_posix()

        segment.audio_file = relative_audio
        segment.audio_duration = duration
        segment.visual_duration = duration
        segment.sync_status = "tts_ready"
        durations[int(segment.segment_id)] = duration
        audio_paths.append(output_path)

    # Setelah preflight, angka ini bukan lagi estimasi WPM melainkan durasi
    # audio nyata yang akan dipakai Plan 2.
    storyboard.project.estimated_voiceover_seconds = round(sum(durations.values()), 3)
    concat_audio_files(audio_paths, Path(final_output_path))
    return Path(final_output_path), durations


def merge_audio_video(
    video_path: Path,
    audio_path: Path,
    output_path: Path,
) -> Path:
    """Gabungkan video (storytelling_no_audio.mp4) dan audio voiceover (voiceover.mp3) menggunakan FFmpeg."""
    ffmpeg_bin = shutil.which("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("Binary 'ffmpeg' tidak ditemukan di PATH sistem.")

    video_path = Path(video_path).resolve()
    audio_path = Path(audio_path).resolve()
    output_path = Path(output_path).resolve()

    if not video_path.exists():
        raise ValueError(f"File video '{video_path}' tidak ditemukan.")
    if not audio_path.exists():
        raise ValueError(f"File audio '{audio_path}' tidak ditemukan.")

    output_path.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        ffmpeg_bin,
        "-y",
        "-i",
        str(video_path),
        "-i",
        str(audio_path),
        "-c:v",
        "copy",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-shortest",
        str(output_path),
    ]

    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
        raise RuntimeError(f"FFmpeg gagal menggabungkan video dan audio: {res.stderr}")

    return output_path
