"""Skema data Plan 1.

Catatan: constraint `src <= 3.0` (PLAN1.md §11) sengaja TIDAK dipasang di
skema, karena output Gemini sering violate dan harus bisa dinormalisasi oleh
`clip_validator.normalize_clip`, bukan crash saat parsing. Penegakan aturan
occur di service layer, dan hasil akhir selalu dijamin <= 3.0.
"""
from typing import List, Literal

import re

from pydantic import BaseModel, Field, field_validator

Transform = Literal["baref", "fz12", "s65", "s50", "s35"]


class Clip(BaseModel):
    clip_id: int | None = None
    beat: str = Field(default="unknown", description="lowercase-kebab-case")
    start: str = Field(default="00:00:00", description="HH:MM:SS atau HH:MM:SS.mmm")
    src: float = Field(default=2.0, description="durasi sumber, 0 < src <= 3.0 (ditegakkan validator)")
    # str (bukan Literal): trx di luar daftar harus dinormalisasi validator, bukan crash
    trx: str = "baref"
    out: float = Field(default=2.0, gt=0, description="durasi hasil setelah transform")
    visual_hint: str = ""
    verification: str = "required"

    @field_validator("beat")
    @classmethod
    def _clean_beat(cls, v: str) -> str:
        text = str(v or "").strip().lower().replace("_", "-")
        # buang karakter yang bukan untuk label: sisakan a-z0-9 dan pemisah
        text = re.sub(r"[^a-z0-9\- ]", "", text)
        return "-".join(text.split()) or "unknown"

    @field_validator("start")
    @classmethod
    def _clean_start(cls, v: str) -> str:
        return str(v or "").strip() or "00:00:00"

    @field_validator("src", "out", mode="before")
    @classmethod
    def _coerce_number(cls, v):
        """Terima string numerik ("2.0", "2,5", "2s") dari Gemini.

        Nilai yang bukan angka diganti default, karena Gemini kadang mengirim
        null/""/"beberapa detik" — lebih baik diprediksi daripada crash.
        """
        if isinstance(v, bool):
            return 2.0
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, str):
            cleaned = v.strip().rstrip("sS").replace(",", ".")
            try:
                return float(cleaned)
            except ValueError:
                return 2.0
        return 2.0


class SrtEntry(BaseModel):
    index: int
    start: str
    end: str
    text: str


class ImportantEvent(BaseModel):
    event: str
    start: str = "00:00:00"
    end: str = "00:00:00"
    subtitle_ref: int | None = None


class StoryArc(BaseModel):
    opening: str = ""
    rising_action: str = ""
    midpoint: str = ""
    climax: str = ""
    ending: str = ""


class StoryAnalysis(BaseModel):
    title: str = ""
    characters: List = []
    setting: str = ""
    main_conflict: str = ""
    story_arc: StoryArc = StoryArc()
    important_events: List[ImportantEvent] = []


class ProjectInfo(BaseModel):
    title: str = ""
    source_subtitle: str = ""
    target_duration_minutes: int = 15
    estimated_voiceover_seconds: float = 900.0
    estimated_word_count: int = 2250
    language: str = "id"


class Section(BaseModel):
    section_id: int
    visual: str = ""
    voice_over: str = ""
    clips: List[Clip] = Field(default_factory=list)
    clip_count: int = 0
    total_clip_duration: float = 0.0
    segments: List["NarrationSegment"] = Field(default_factory=list)


class NarrationSegment(BaseModel):
    """Satu unit narasi yang menjadi batas sinkronisasi audio-visual Plan 2."""

    segment_id: int
    text: str = ""
    visual_cue: str = ""
    clips: List[Clip] = Field(default_factory=list)
    # Belum diketahui di Plan 1; Plan 2 mengisinya setelah TTS dibuat.
    audio_file: str | None = None
    audio_duration: float | None = None
    visual_duration: float = 0.0
    sync_status: str = "pending_tts"


class Summary(BaseModel):
    total_sections: int = 0
    total_clips: int = 0
    total_clip_duration: float = 0.0


class Storyboard(BaseModel):
    project: ProjectInfo = ProjectInfo()
    sections: List[Section] = Field(default_factory=list)
    # Flattened, canonical list untuk Plan 2. Sections tetap dipertahankan
    # sebagai format tampilan/kompatibilitas Plan 1 lama.
    segments: List[NarrationSegment] = Field(default_factory=list)
    summary: Summary = Summary()


class GenerateResponse(BaseModel):
    success: bool
    project_id: str
    storyboard: Storyboard | None = None
    report: dict = Field(default_factory=dict)
