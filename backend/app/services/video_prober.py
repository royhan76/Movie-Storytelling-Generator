"""Validasi dan probe metadata file video menggunakan FFprobe.

Mengekstrak:
- duration (detik)
- width, height
- fps (frame rate)
- video_codec
- audio_codec (None jika tidak ada audio stream)
"""
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, Optional


def find_executable(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"Binary '{name}' tidak ditemukan di PATH sistem. Pastikan FFmpeg/FFprobe terpasang.")
    return path


def parse_fps(fps_str: str) -> float:
    """Konversi string r_frame_rate ffprobe (contoh: '24/1' atau '30000/1001') ke float."""
    if not fps_str:
        return 30.0
    if "/" in fps_str:
        num, den = fps_str.split("/")
        try:
            den_f = float(den)
            return round(float(num) / den_f, 3) if den_f != 0 else 30.0
        except ValueError:
            return 30.0
    try:
        return float(fps_str)
    except ValueError:
        return 30.0


def probe_video(video_path: Path) -> Dict[str, Any]:
    """Jalankan ffprobe untuk membaca metadata file video."""
    video_path = Path(video_path)
    if not video_path.exists() or not video_path.is_file():
        raise ValueError(f"File video '{video_path}' tidak ditemukan.")

    ffprobe_bin = find_executable("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(video_path),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
        data = json.loads(res.stdout)
    except Exception as exc:
        raise ValueError(f"FFprobe gagal membaca metadata video '{video_path.name}': {exc}") from exc

    streams = data.get("streams") or []
    format_info = data.get("format") or {}

    video_stream: Optional[Dict[str, Any]] = None
    audio_stream: Optional[Dict[str, Any]] = None

    for stream in streams:
        codec_type = stream.get("codec_type")
        if codec_type == "video" and video_stream is None:
            video_stream = stream
        elif codec_type == "audio" and audio_stream is None:
            audio_stream = stream

    if video_stream is None:
        raise ValueError(f"File video '{video_path.name}' tidak memiliki video stream yang valid.")

    duration = 0.0
    if format_info.get("duration"):
        try:
            duration = float(format_info["duration"])
        except ValueError:
            duration = 0.0
    if duration <= 0 and video_stream.get("duration"):
        try:
            duration = float(video_stream["duration"])
        except ValueError:
            duration = 0.0

    if duration <= 0:
        raise ValueError(f"Durasi video '{video_path.name}' tidak valid ({duration}s).")

    width = int(video_stream.get("width") or 1920)
    height = int(video_stream.get("height") or 1080)
    fps = parse_fps(video_stream.get("r_frame_rate") or video_stream.get("avg_frame_rate") or "30/1")
    video_codec = str(video_stream.get("codec_name") or "h264")
    audio_codec = str(audio_stream.get("codec_name")) if audio_stream else None

    return {
        "path": str(video_path),
        "filename": video_path.name,
        "duration": duration,
        "width": width,
        "height": height,
        "fps": fps,
        "video_codec": video_codec,
        "audio_codec": audio_codec,
        "has_audio": audio_stream is not None,
    }


def probe_audio(audio_path: Path) -> Dict[str, Any]:
    """Jalankan ffprobe untuk membaca metadata file audio."""
    audio_path = Path(audio_path)
    if not audio_path.exists() or not audio_path.is_file():
        raise ValueError(f"File audio '{audio_path.name}' tidak ditemukan.")

    ffprobe_bin = find_executable("ffprobe")
    cmd = [
        ffprobe_bin,
        "-v",
        "quiet",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(audio_path),
    ]

    try:
        res = subprocess.run(cmd, capture_output=True, text=True, check=True, encoding="utf-8")
        data = json.loads(res.stdout)
    except Exception as exc:
        raise ValueError(f"FFprobe gagal membaca metadata audio '{audio_path.name}': {exc}") from exc

    streams = data.get("streams") or []
    format_info = data.get("format") or {}

    audio_stream: Optional[Dict[str, Any]] = None
    for stream in streams:
        if stream.get("codec_type") == "audio":
            audio_stream = stream
            break

    duration = 0.0
    if format_info.get("duration"):
        try:
            duration = float(format_info["duration"])
        except ValueError:
            duration = 0.0
    if duration <= 0 and audio_stream and audio_stream.get("duration"):
        try:
            duration = float(audio_stream["duration"])
        except ValueError:
            duration = 0.0

    if duration <= 0:
        raise ValueError(f"Durasi audio '{audio_path.name}' tidak valid ({duration}s).")

    audio_codec = str(audio_stream.get("codec_name")) if audio_stream else None

    return {
        "path": str(audio_path),
        "filename": audio_path.name,
        "duration": duration,
        "audio_codec": audio_codec,
    }

