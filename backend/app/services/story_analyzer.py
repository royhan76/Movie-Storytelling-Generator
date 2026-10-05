"""Analisis cerita: pembungkus GeminiProvider.generate_story_analysis.

Menyimpan analisis mentah dan menyediakan versi ringkas untuk konteks tahap 2.
"""
from typing import Any, Dict, List

from app.providers.gemini import GeminiProvider

# Jumlah minimum event yang diteruskan ke tahap 2. Ini yang pernah jadi 40
# tetap, padahal target 15 menit butuh ~2250 kata -- naskah tidak mungkin
# penuh dari 40 kejadian, hasilnya naskah pendek walau filmnya 100+ menit.
COMPRESS_MIN_EVENTS = 40

# Event per kata target. 15 menit = 2250 kata -> ~160 event.
EVENTS_PER_TARGET_WORD = 0.075

# Batas atas supaya prompt tahap 2 tidak melebihi context window.
COMPRESS_MAX_EVENTS = 160


def events_for_target(target_minutes: int, wpm: int = 125) -> int:
    """Berapa event yang perlu diteruskan untuk naskah `target_minutes` menit."""
    target_words = max(1, int(target_minutes) * wpm)
    wanted = int(target_words * EVENTS_PER_TARGET_WORD)
    return max(COMPRESS_MIN_EVENTS, min(COMPRESS_MAX_EVENTS, wanted))


class StoryAnalyzer:
    def __init__(self, provider: GeminiProvider) -> None:
        self.provider = provider

    def analyze(self, timeline: List[Dict[str, Any]], chunk_size: int = 350, language: str = "id") -> Dict[str, Any]:
        return self.provider.generate_story_analysis(timeline, chunk_size=chunk_size, language=language)

    @staticmethod
    def compress(analysis: Dict[str, Any], max_events: int = 0) -> Dict[str, Any]:
        """Versi ringkas supaya prompt tahap 2 tidak membludak.

        `max_events` wajib diskalakan dengan target durasi: terlalu sedikit event
        menghasilkan naskah yang pendek. Lihat `events_for_target`.
        """
        events = analysis.get("important_events") or []

        if max_events <= 0:
            max_events = COMPRESS_MIN_EVENTS
        else:
            max_events = max(COMPRESS_MIN_EVENTS, min(COMPRESS_MAX_EVENTS, max_events))

        # Jangan dipaksa melebihi jumlah event yang benar-benar ada.
        trimmed_events = [
            {
                "event": str(e.get("event", ""))[:220],
                "start": e.get("start", ""),
                "end": e.get("end", ""),
            }
            for e in events[:max_events]
            if isinstance(e, dict)
        ]
        arc = analysis.get("story_arc") or {}
        return {
            "title": analysis.get("title", ""),
            "characters": analysis.get("characters", [])[:12],
            "setting": analysis.get("setting", ""),
            "main_conflict": str(analysis.get("main_conflict", ""))[:600],
            "story_arc": {k: str(v)[:600] for k, v in arc.items()},
            "important_events": trimmed_events,
        }