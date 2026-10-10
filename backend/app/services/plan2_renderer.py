"""Orchestrator Pipeline Plan 2 — Render Video Storytelling (No Audio).

Alur:
1. Probe & validasi video sumber (FFprobe).
2. Validasi storyboard.json & konversi timestamp HH:MM:SS ke detik.
3. Setup working directory & file state.json (untuk resume/retry).
4. Potong clip sumber (extract) & terapkan transform (baref, fz12, s65, s50, s35).
5. Normalisasi resolusi (1920x1080 default), FPS (30), codec (H.264, yuv420p).
6. Concat seluruh clip dalam urutan storyboard.
7. Hapus audio stream (-an).
8. Validasi video final (FFprobe) & kembalikan metadata hasil.
"""
import json
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from app.debug_log import log_call
from app.schemas.storyboard import Storyboard
from app.services.script_generator import storyboard_to_voiceover
from app.services.tts_service import (
    generate_storyboard_tts_sections,
    generate_tts_sync,
    merge_audio_video,
)
from app.services.video_prober import probe_video
from app.services.video_processor import FFmpegProcessor, parse_timestamp_sec


class Plan2Renderer:
    def __init__(self, projects_dir: Path) -> None:
        self.projects_dir = Path(projects_dir)

    def render(
        self,
        video_path: Path,
        storyboard: Storyboard,
        project_id: str,
        voice: str = "id-ID-ArdiNeural",
        include_tts: bool = True,
        progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Jalankan pipeline render Plan 2 penuh (video + tts presisi per-section + merge audio) dengan resume/retry."""
        started_at = time.time()

        def emit(stage: str, **data: Any) -> None:
            if progress:
                progress(stage, data)

        video_path = Path(video_path).resolve()
        emit("validate_video", phase="start", path=str(video_path))

        # ---- Tahap 1: Probe Video ----
        video_meta = probe_video(video_path)
        video_duration = video_meta["duration"]
        emit("validate_video", phase="done", meta=video_meta)

        # ---- Tahap 2: Setup Directories & Section TTS ----
        proj_dir = self.projects_dir / project_id
        work_dir = proj_dir / "plan2" / "work"
        source_dir = work_dir / "source"
        processed_dir = work_dir / "processed"
        output_dir = proj_dir

        source_dir.mkdir(parents=True, exist_ok=True)
        processed_dir.mkdir(parents=True, exist_ok=True)

        audio_path: Optional[Path] = None
        section_audio_durations: Dict[int, float] = {}

        preflight_segments = list(getattr(storyboard, "segments", []) or [])
        preflight_ready = bool(
            include_tts
            and preflight_segments
            and all(segment.audio_file and segment.audio_duration for segment in preflight_segments)
            and (proj_dir / "voiceover.mp3").exists()
        )

        if preflight_ready:
            # Reuse Plan 1's exact audio. Regenerating TTS here could produce
            # different durations and move every visual boundary.
            audio_path = proj_dir / "voiceover.mp3"
            section_audio_durations = {}
            emit(
                "generate_tts",
                phase="done",
                reused=True,
                segment_count=len(preflight_segments),
                message="Memakai TTS preflight Plan 1.",
            )
        elif include_tts:
            emit("generate_tts", phase="start", voice=voice)
            audio_path, section_audio_durations = generate_storyboard_tts_sections(
                storyboard=storyboard,
                voice=voice,
                work_dir=work_dir / "tts",
                final_output_path=proj_dir / "voiceover.mp3",
            )
            emit("generate_tts", phase="done", durations=section_audio_durations)

        # ---- Tahap 3: Collect, Validate & Align Clips ----
        emit("validate_storyboard", phase="start")
        flat_clips: List[Dict[str, Any]] = []
        seq_index = 1

        for section in storyboard.sections:
            units = list(getattr(section, "segments", []) or [])
            if not units:
                # Legacy storyboard: treat one section as one sync unit.
                units = [None]

            for segment in units:
                sec_clips = list(segment.clips) if segment is not None else list(section.clips)
                if not sec_clips:
                    continue
                segment_audio_dur = float(segment.audio_duration or 0) if segment is not None else 0.0
                section_audio_dur = section_audio_durations.get(section.section_id)
                target_sync_dur = segment_audio_dur or section_audio_dur
                visual_dur = sum(float(c.out) for c in sec_clips)

                # Sync is solved per narration segment, not across a whole
                # section. This is what makes a visual state change at the
                # same boundary where the narrator changes topic.
                scale_factor = (target_sync_dur / visual_dur) if (target_sync_dur and visual_dur > 0) else 1.0

                for clip in sec_clips:
                    start_sec = parse_timestamp_sec(clip.start)
                    src_sec = float(clip.src)
                    original_out_sec = float(clip.out)
                    adjusted_out_sec = round(original_out_sec * scale_factor, 2)
                    trx = str(clip.trx or "baref").lower()

                    # Validasi timestamp vs durasi video
                    if start_sec >= video_duration:
                        raise ValueError(
                            f"Clip {seq_index} ({clip.beat}) timestamp start '{clip.start}' ({start_sec:.1f}s) "
                            f"melebihi durasi video ({video_duration:.1f}s)."
                        )

                    if start_sec + src_sec > video_duration:
                        excess = (start_sec + src_sec) - video_duration
                        if excess <= 2.0:
                            src_sec = max(0.5, video_duration - start_sec)
                        else:
                            raise ValueError(
                                f"Clip {seq_index} ({clip.beat}) timestamp {clip.start} + src {clip.src}s "
                                f"melebihi akhir video ({video_duration:.1f}s)."
                            )

                    flat_clips.append(
                        {
                            "seq_index": seq_index,
                            "section_id": section.section_id,
                            "segment_id": segment.segment_id if segment is not None else None,
                            "clip_id": clip.clip_id,
                            "beat": clip.beat,
                            "start_str": clip.start,
                            "start_sec": start_sec,
                            "src_sec": src_sec,
                            "trx": trx,
                            "out_sec": adjusted_out_sec,
                        }
                    )
                    seq_index += 1

        if not flat_clips:
            raise ValueError("Storyboard tidak memiliki clip yang valid untuk dirender.")

        total_clips = len(flat_clips)
        target_total_duration = sum(c["out_sec"] for c in flat_clips)
        emit(
            "validate_storyboard",
            phase="done",
            total_clips=total_clips,
            target_duration=target_total_duration,
        )

        state_file = work_dir / "state.json"
        state: Dict[str, str] = {}
        if state_file.exists():
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except Exception:
                state = {}

        # Setup FFmpeg engine: mengikuti resolusi video sumber (atau 1920x1080 default)
        target_w = video_meta["width"] if video_meta["width"] > 0 else 1920
        target_h = video_meta["height"] if video_meta["height"] > 0 else 1080
        processor = FFmpegProcessor(
            target_width=target_w,
            target_height=target_h,
            target_fps=30.0,
            preset="medium",
            crf=20,
        )

        processed_paths: List[Path] = []

        # ---- Tahap 4: Process Each Clip ----
        for item in flat_clips:
            idx = item["seq_index"]
            clip_key = f"clip_{idx:04d}"

            src_path = source_dir / f"{clip_key}_source.mp4"
            proc_path = processed_dir / f"{clip_key}.mp4"

            emit(
                "processing_clips",
                phase="clip",
                current=idx,
                total=total_clips,
                beat=item["beat"],
                trx=item["trx"],
            )

            cache_sig = f"done_{item['out_sec']:.2f}_{item['trx']}_{item['src_sec']:.2f}"

            # Check if clip is already completed (resume support with parameter signature matching)
            if state.get(clip_key) in ("done", cache_sig) and state.get(clip_key) == cache_sig and proc_path.exists() and proc_path.stat().st_size > 0:
                processed_paths.append(proc_path)
                continue

            try:
                # Step 4a: Extract raw segment
                processor.extract_source_clip(
                    video_path=video_path,
                    start_sec=item["start_sec"],
                    src_sec=item["src_sec"],
                    output_path=src_path,
                )

                # Step 4b: Apply transform & normalize dengan presisi target_out_duration
                processor.process_and_transform_clip(
                    source_clip_path=src_path,
                    output_clip_path=proc_path,
                    trx=item["trx"],
                    src_duration=item["src_sec"],
                    target_out_duration=item["out_sec"],
                )

                state[clip_key] = cache_sig
                state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                processed_paths.append(proc_path)
            except Exception as exc:
                state[clip_key] = "failed"
                state_file.write_text(json.dumps(state, indent=2), encoding="utf-8")
                raise RuntimeError(
                    f"Gagal memproses Clip #{idx} (beat: '{item['beat']}', start: '{item['start_str']}'): {exc}"
                ) from exc

        # ---- Tahap 5: Concat Clips ----
        emit("concat", phase="start", total_clips=total_clips)

        concat_txt = work_dir / "concat.txt"
        no_audio_video_path = output_dir / "storytelling_no_audio.mp4"

        processor.concat_clips(
            processed_clip_paths=processed_paths,
            output_video_path=no_audio_video_path,
            concat_txt_path=concat_txt,
        )

        emit("concat", phase="done")

        final_video_path = no_audio_video_path

        # ---- Tahap 6: Merge Audio & Video (1-Click Presisi) ----
        if include_tts and audio_path and audio_path.exists():
            emit("merge_audio", phase="start")
            merged_video_path = output_dir / "final_storytelling.mp4"
            merge_audio_video(
                video_path=no_audio_video_path,
                audio_path=audio_path,
                output_path=merged_video_path,
            )
            final_video_path = merged_video_path
            emit("merge_audio", phase="done")

        # ---- Tahap 7: Final Verification ----
        emit("verify_final", phase="start")
        final_meta = probe_video(final_video_path)

        actual_duration = final_meta["duration"]
        elapsed_sec = round(time.time() - started_at, 1)

        diff_pct = abs(actual_duration - target_total_duration) / target_total_duration * 100.0 if target_total_duration > 0 else 0.0

        report = {
            "project_id": project_id,
            "source_video": video_meta["filename"],
            "total_clips": total_clips,
            "target_duration_sec": round(target_total_duration, 1),
            "actual_duration_sec": round(actual_duration, 1),
            "duration_diff_pct": round(diff_pct, 1),
            "resolution": f"{final_meta['width']}x{final_meta['height']}",
            "fps": final_meta["fps"],
            "has_audio": final_meta["has_audio"],
            "output_file": final_video_path.name,
            "output_path": str(final_video_path),
            "elapsed_sec": elapsed_sec,
        }

        log_call("plan2-render-success", report)
        emit("verify_final", phase="done", report=report)

        return {
            "success": True,
            "project_id": project_id,
            "output_path": str(final_video_path),
            "output_filename": final_video_path.name,
            "has_audio": final_meta["has_audio"],
            "report": report,
        }
