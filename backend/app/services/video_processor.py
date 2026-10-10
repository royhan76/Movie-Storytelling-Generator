"""Video Processor Engine (FFmpeg).

Tugas:
1. Extract source clip dari timestamp start sampai start + src.
2. Apply transform: baref, fz12, s65, s50, s35.
3. Normalize video resolution, FPS (30 FPS), pixel format (yuv420p), codec (h264).
4. Concat all processed clips dalam urutan storyboard.
5. Hapus audio stream (-an) pada video final.
"""
import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Optional

from app.debug_log import log_call


def find_ffmpeg() -> str:
    path = shutil.which("ffmpeg")
    if not path:
        raise RuntimeError("Binary 'ffmpeg' tidak ditemukan di PATH sistem.")
    return path


def parse_timestamp_sec(ts_str: str) -> float:
    """Ubah HH:MM:SS atau HH:MM:SS.mmm menjadi detik (float)."""
    if not ts_str:
        return 0.0
    text = str(ts_str).strip()
    parts = text.split(":")
    if len(parts) == 3:
        try:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0
    elif len(parts) == 2:
        try:
            return float(parts[0]) * 60 + float(parts[1])
        except ValueError:
            return 0.0
    try:
        return float(text)
    except ValueError:
        return 0.0


class FFmpegProcessor:
    def __init__(
        self,
        target_width: int = 1920,
        target_height: int = 1080,
        target_fps: float = 30.0,
        preset: str = "medium",
        crf: int = 20,
    ) -> None:
        self.ffmpeg_bin = find_ffmpeg()
        self.target_width = target_width
        self.target_height = target_height
        self.target_fps = target_fps
        self.preset = preset
        self.crf = crf

    def extract_source_clip(
        self,
        video_path: Path,
        start_sec: float,
        src_sec: float,
        output_path: Path,
    ) -> Path:
        """Potong bagian video sumber dari start_sec sepanjang src_sec."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)

        # Fast seek (-ss sebelum -i) + accurate trim
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-ss",
            f"{start_sec:.3f}",
            "-i",
            str(video_path),
            "-t",
            f"{src_sec:.3f}",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "18",
            "-an",
            str(output_path),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(
                f"FFmpeg gagal memotong clip {start_sec}s -> {start_sec+src_sec}s: {res.stderr}"
            )
        return output_path

    def process_and_transform_clip(
        self,
        source_clip_path: Path,
        output_clip_path: Path,
        trx: str,
        src_duration: float,
        target_out_duration: float,
    ) -> Path:
        """Terapkan transform (baref, fz12, s65, s50, s35) dan normalisasi video format."""
        output_clip_path = Path(output_clip_path)
        output_clip_path.parent.mkdir(parents=True, exist_ok=True)

        w, h, fps = self.target_width, self.target_height, self.target_fps

        # Base filter: scale & pad ke target resolution (letterbox jika rasio beda) + force fps + yuv420p
        scale_filter = (
            f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
            f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={fps},"
            f"format=yuv420p"
        )

        vf_chain = []

        if trx == "fz12":
            # Freeze frame: ambil frame pertama dan loop / freeze selama target_out_duration (default 1.2s)
            freeze_dur = target_out_duration if target_out_duration > 0 else 1.2
            vf_chain.append(
                f"loop=loop=-1:size=1:start=0,trim=duration={freeze_dur:.3f},setpts=PTS-STARTPTS,{scale_filter}"
            )
        else:
            # Presisi durasi: jika target_out_duration diset, gunakan pts_factor = target_out_duration / src_duration
            if target_out_duration > 0 and src_duration > 0:
                pts_factor = target_out_duration / src_duration
            elif trx in ("s65", "s50", "s35"):
                speed_map = {"s65": 0.65, "s50": 0.50, "s35": 0.35}
                pts_factor = 1.0 / speed_map.get(trx, 1.0)
            else:
                pts_factor = 1.0
            vf_chain.append(f"setpts={pts_factor:.4f}*PTS,{scale_filter}")

        vf_str = ",".join(vf_chain)

        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-i",
            str(source_clip_path),
            "-vf",
            vf_str,
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-an",
            str(output_clip_path),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or not output_clip_path.exists() or output_clip_path.stat().st_size == 0:
            raise RuntimeError(
                f"FFmpeg gagal menerapkan transform '{trx}' pada clip '{source_clip_path.name}': {res.stderr}"
            )
        return output_clip_path

    def concat_clips(
        self,
        processed_clip_paths: List[Path],
        output_video_path: Path,
        concat_txt_path: Path,
    ) -> Path:
        """Gabungkan semua clip yang sudah diproses menjadi satu video final tanpa audio."""
        output_video_path = Path(output_video_path)
        output_video_path.parent.mkdir(parents=True, exist_ok=True)
        concat_txt_path = Path(concat_txt_path)
        concat_txt_path.parent.mkdir(parents=True, exist_ok=True)

        lines = [f"file '{Path(p).resolve().as_posix()}'" for p in processed_clip_paths]
        concat_txt_path.write_text("\n".join(lines), encoding="utf-8")

        # Concat demuxer fast merge + ensure audio removed (-an)
        cmd = [
            self.ffmpeg_bin,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_txt_path),
            "-c:v",
            "libx264",
            "-preset",
            self.preset,
            "-crf",
            str(self.crf),
            "-pix_fmt",
            "yuv420p",
            "-an",
            str(output_video_path),
        ]

        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or not output_video_path.exists() or output_video_path.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg gagal merender video final (concat): {res.stderr}")

        return output_video_path

    def normalize_video(
        self,
        input_path: Path,
        output_path: Path,
        duration: float | None = None,
    ) -> Path:
        """Normalisasi video eksternal (mis. intro) ke format concat Plan 2."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        scale_filter = (
            f"scale={self.target_width}:{self.target_height}:force_original_aspect_ratio=decrease,"
            f"pad={self.target_width}:{self.target_height}:(ow-iw)/2:(oh-ih)/2:black,"
            f"fps={self.target_fps},format=yuv420p"
        )
        cmd = [self.ffmpeg_bin, "-y", "-i", str(input_path)]
        if duration and duration > 0:
            cmd.extend(["-t", f"{duration:.3f}"])
        cmd.extend([
            "-vf", scale_filter,
            "-c:v", "libx264", "-preset", self.preset, "-crf", str(self.crf),
            "-an", str(output_path),
        ])
        res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0 or not output_path.exists() or output_path.stat().st_size == 0:
            raise RuntimeError(f"FFmpeg gagal menormalisasi video intro: {res.stderr}")
        return output_path
