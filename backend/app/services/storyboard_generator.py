"""Bangun object Storyboard dari JSON mentah Gemini.

Toleran terhadap data tidak lengkap: field hilang diisi default, clip yang
rusak dilewati, section kosong dibuang.
"""
from typing import Any, Dict, List

from app.schemas.storyboard import Clip, ProjectInfo, Section, Storyboard, Summary

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
        next_clip_id = 1

        for pos, raw_section in enumerate(raw.get("sections") or [], start=1):
            voice_over = str(raw_section.get("voice_over") or "").strip()
            visual = str(raw_section.get("visual") or "").strip()
            if not voice_over:
                continue
            if len(voice_over) > 3000:
                trimmed = voice_over[:3000]
                last_punc = max(trimmed.rfind("."), trimmed.rfind("!"), trimmed.rfind("?"))
                voice_over = trimmed[: last_punc + 1] if last_punc > 100 else trimmed

            clips: List[Clip] = []
            for raw_clip in raw_section.get("clips") or []:
                if not isinstance(raw_clip, dict):
                    continue
                start = str(raw_clip.get("start") or "").strip()
                if not start:
                    continue
                trx = raw_clip.get("trx")
                trx = trx if trx in VALID_TRX else DEFAULT_TRX
                clips.append(
                    Clip(
                        clip_id=next_clip_id,
                        beat=_clean_beat(raw_clip.get("beat")),
                        start=start,
                        src=_safe_float(raw_clip.get("src"), DEFAULT_SRC),
                        trx=trx,
                        out=_safe_float(raw_clip.get("out"), _safe_float(raw_clip.get("src"), DEFAULT_SRC)),
                    )
                )
                next_clip_id += 1

            if not clips:
                continue

            sections.append(
                Section(
                    section_id=int(raw_section.get("section_id") if raw_section.get("section_id") is not None else pos),
                    visual=visual or "Visual belum dideskripsikan.",
                    voice_over=voice_over,
                    clips=clips,
                )
            )

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

        return Storyboard(project=project, sections=sections, summary=Summary())


# alias kompatibilitas
StoryboardGenerator = StoryboardBuilder