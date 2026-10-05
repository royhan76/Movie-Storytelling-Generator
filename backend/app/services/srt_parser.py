"""Parser SRT yang tolerate format varied.

Split per penanda timecode (bukan per baris kosong) supaya SRT dengan
baris kosong di antara tiap baris tetap ter-parse. Lihat references/
srt-parser-blank-lines.md di skill windows-service-launcher.
"""
import re
from typing import List, Tuple
from app.schemas.storyboard import SrtEntry

TIMECODE_RE = re.compile(
    r"(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})\s*-->\s*(\d{1,2}):(\d{2}):(\d{2})[.,](\d{1,3})"
)


def _ms(h: str, m: str, s: str, ms: str) -> int:
    return int(h) * 3600000 + int(m) * 60000 + int(s) * 1000 + int(ms.ljust(3, "0"))


class SrtParser:
    def parse(self, content: str) -> List[SrtEntry]:
        if not content:
            return []
        normalized = (
            content.replace("\r\n", "\n").replace("\r", "\n").replace("﻿", "").strip()
        )

        blocks: List[List[str]] = []
        cur: List[str] = []
        for line in normalized.split("\n"):
            if TIMECODE_RE.search(line):
                if cur:
                    blocks.append(cur)
                cur = [line]
            elif cur:
                cur.append(line)
        if cur:
            blocks.append(cur)

        entries: List[SrtEntry] = []
        for block in blocks:
            if not any(TIMECODE_RE.search(x) for x in block):
                continue
            tc_line_idx = next(i for i, x in enumerate(block) if TIMECODE_RE.search(x))
            m = TIMECODE_RE.search(block[tc_line_idx])
            start = f"{int(m.group(1)):02d}:{m.group(2)}:{m.group(3)}.{m.group(4).ljust(3, '0')}"
            end = f"{int(m.group(5)):02d}:{m.group(6)}:{m.group(7)}.{m.group(8).ljust(3, '0')}"

            # buang penomoran cue di posisi mana pun
            text_lines = []
            for line in block[tc_line_idx + 1 :]:
                s = line.strip()
                if not s:
                    continue
                if re.fullmatch(r"\d{1,4}", s):
                    continue
                text_lines.append(s)
            text = self._clean(" ".join(text_lines))
            if text:
                entries.append(SrtEntry(index=len(entries) + 1, start=start, end=end, text=text))

        return self._dedupe_starts(entries)

    def _dedupe_starts(self, entries: List[SrtEntry]) -> List[SrtEntry]:
        out: List[SrtEntry] = []
        prev_start = -1
        for e in entries:
            st = self.to_ms(e.start)
            if st <= prev_start:
                continue
            prev_start = st
            out.append(e)
        return out

    def _clean(self, text: str) -> str:
        text = re.sub(r"<[^>]+>", " ", text)          # tag <i>/<font>
        text = re.sub(r"\{[^}]+\}", " ", text)        # ASS override
        text = re.sub(r"\(.*?\)", " ", text)          # annotationIncidental
        text = re.sub(r"\s+", " ", text)
        return text.strip()

    def to_ms(self, ts: str) -> int:
        """Parse 'HH:MM:SS,mmm', 'HH:MM:SS.mmm', atau 'HH:MM:SS' (tanpa pecahan detik)."""
        text = str(ts or "").strip().replace(",", ".")
        m = re.match(
            r"^(\d{1,3}):(\d{1,2}):(\d{1,2})(?:\.(\d{1,3}))?$", text
        )
        if not m:
            return 0
        return _ms(m.group(1), m.group(2), m.group(3), m.group(4) or "0")

    def to_seconds(self, ts: str) -> float:
        return self.to_ms(ts) / 1000.0

    def seconds_to_hhmmss(self, secs: float) -> str:
        total = int(round(secs))
        return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"

    def timeline_bounds(self, entries: List[SrtEntry]) -> Tuple[float, float]:
        if not entries:
            return 0.0, 0.0
        starts = [self.to_seconds(e.start) for e in entries]
        ends = [self.to_seconds(e.end) for e in entries]
        return min(starts), max(ends)

    def nearest_cue(self, entries: List[SrtEntry], seconds: float) -> SrtEntry:
        """Cari cue paling dekat ke detik tertentu (untuk anchor timestamp klip)."""
        best = entries[0]
        best_d = abs(self.to_seconds(best.start) - seconds)
        for e in entries:
            d = abs(self.to_seconds(e.start) - seconds)
            if d < best_d:
                best, best_d = e, d
        return best

    def build_timeline(self, entries: List[SrtEntry]) -> List[dict]:
        """Timeline ringkas untuk AI: index, start, end, text."""
        return [
            {"i": e.index, "start": e.start, "end": e.end, "text": e.text}
            for e in entries
        ]