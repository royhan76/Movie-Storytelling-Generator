"""Bangun object Storyboard dari JSON mentah Gemini.

Toleran terhadap data tidak lengkap: field hilang diisi default, clip yang
rusak dilewati, section kosong dibuang.
"""
from typing import Any, Dict, List

from app.schemas.storyboard import Clip, NarrationSegment, ProjectInfo, Section, Storyboard, Summary

VALID_TRX = {"baref", "fz12", "s65", "s50", "s35"}

DEFAULT_TRX = "baref"
DEFAULT_SRC = 2.0


def _clean_beat(value: Any) -> str:
    text = str(value or "").strip().lower().replace("_", "-")
    text = "-".join(text.split())
    return text or "unknown"


def _safe_float(value: Any, fallback: float) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return fallback
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return fallback
    return out


class StoryboardBuilder:
    def build(self, raw: Dict[str, Any]) -> Storyboard:
        sections: List[Section] = []
        segments: List[NarrationSegment] = []
        next_clip_id = 1

        def build_clip(raw_clip: Any) -> Clip | None:
            nonlocal next_clip_id
            if not isinstance(raw_clip, dict):
                return None
            start = str(raw_clip.get("start") or "").strip()
            if not start:
                return None
            src = _safe_float(raw_clip.get("src"), DEFAULT_SRC)
            trx = raw_clip.get("trx")
            trx = trx if trx in VALID_TRX else DEFAULT_TRX
            clip = Clip(
                clip_id=next_clip_id,
                beat=_clean_beat(raw_clip.get("beat")),
                start=start,
                src=src,
                trx=trx,
                out=_safe_float(raw_clip.get("out"), src),
                visual_hint=str(raw_clip.get("visual_hint") or ""),
                verification=str(raw_clip.get("verification") or "required"),
            )
            next_clip_id += 1
            return clip

        for pos, raw_section in enumerate(raw.get("sections") or [], start=1):
            voice_over = str(raw_section.get("voice_over") or "").strip()
            visual = str(raw_section.get("visual") or "").strip()
            raw_segments = raw_section.get("segments") or []
            if not voice_over and raw_segments:
                voice_over = " ".join(
                    str(segment.get("text") or segment.get("voice_over") or "").strip()
                    for segment in raw_segments
                    if isinstance(segment, dict)
                ).strip()
            if not voice_over:
                continue
            if len(voice_over) > 3000:
                trimmed = voice_over[:3000]
                last_punc = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
                voice_over = trimmed[: last_punc + 1] if last_punc > 100 else trimmed

            clips: List[Clip] = []
            section_segments: List[NarrationSegment] = []
            for seg_pos, raw_segment in enumerate(raw_segments, start=1):
                if not isinstance(raw_segment, dict):
                    continue
                segment_clips: List[Clip] = []
                for raw_clip in raw_segment.get("clips") or []:
                    clip = build_clip(raw_clip)
                    if clip is not None:
                        segment_clips.append(clip)
                        clips.append(clip)
                text = str(raw_segment.get("text") or raw_segment.get("voice_over") or "").strip()
                if text:
                    section_segments.append(
                        NarrationSegment(
                            segment_id=int(raw_segment.get("segment_id") or seg_pos),
                            text=text,
                            visual_cue=_clean_beat(raw_segment.get("visual_cue") or raw_segment.get("beat") or ""),
                            clips=segment_clips,
                            audio_file=raw_segment.get("audio_file"),
                            audio_duration=raw_segment.get("audio_duration"),
                        )
                    )

            # Kompatibilitas: Gemini lama hanya mengirim section.clips.
            if not clips:
                for raw_clip in raw_section.get("clips") or []:
                    clip = build_clip(raw_clip)
                    if clip is not None:
                        clips.append(clip)

            if not clips:
                continue

            section = Section(
                    section_id=int(raw_section.get("section_id") if raw_section.get("section_id") is not None else pos),
                    visual=visual or "Visual belum dideskripsikan.",
                    voice_over=voice_over,
                    clips=clips,
                    segments=section_segments,
                )
            # Jika model belum mengeluarkan segments, buat satu segment legacy
            # agar Plan 2 tetap punya unit TTS yang jelas.
            if not section.segments:
                section.segments = [
                    NarrationSegment(
                        segment_id=1,
                        text=voice_over,
                        visual_cue=_clean_beat(visual) or "section-visual",
                        clips=section.clips,
                    )
                ]
            sections.append(section)
            segments.extend(section.segments)

        if not sections:
            raise ValueError("Gemini tidak menghasilkan section yang valid.")

        project_raw = raw.get("project") or {}
        project = ProjectInfo(
            title=str(project_raw.get("title") or ""),
            source_subtitle=str(project_raw.get("source_subtitle") or ""),
            target_duration_minutes=int(_safe_float(project_raw.get("target_duration_minutes"), 15)),
            estimated_voiceover_seconds=_safe_float(project_raw.get("estimated_voiceover_seconds"), 900),
            estimated_word_count=int(_safe_float(project_raw.get("estimated_word_count"), 2250)),
            language=str(project_raw.get("language") or "id"),
        )

        # Segment id harus unik secara global agar file TTS Plan 2 tidak
        # menimpa file segment dari section lain.
        for idx, segment in enumerate(segments, start=1):
            segment.segment_id = idx

        return Storyboard(project=project, sections=sections, segments=segments, summary=Summary())


# alias kompatibilitas
StoryboardGenerator = StoryboardBuilder
