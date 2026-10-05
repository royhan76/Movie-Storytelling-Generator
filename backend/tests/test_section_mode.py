"""Regression test: jalur per-bagian harus MENJAGA total kata di atas batas per-panggilan.

Bug nyata dari file SAKAMOTO.DAYS (1549 cue, 125 menit): target 15 menit butuh
2250 kata, tapi satu panggilan Gemini mentok di ~900 kata (terukur: 79, 197,
858, 891, 719, 908). Hasil akhir 4-5 menit untuk target 15 menit.

Fix: tulis naskah per bagian lalu gabung.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.script_generator import (
    SECTION_MAX_CLIPS,
    SECTION_MODE_MIN_WORDS,
    SECTION_PARTS,
    StoryPipeline,
    _limit_clips,
)

fails = []


def check(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' -> ' + str(extra) if extra else ''}")
    if not ok:
        fails.append(name)


WPM = 150

# --- konstanta ---
check("SECTION_PARTS >= 2", SECTION_PARTS >= 2, SECTION_PARTS)
check("SECTION_MODE_MIN_WORDS di bawah target 15m (2250)",
      SECTION_MODE_MIN_WORDS < 2250, SECTION_MODE_MIN_WORDS)

# --- _limit_clips ---
check("_limit_clips kosong -> kosong", _limit_clips([]) == [])
check("_limit_clips membuang non-dict",
      len(_limit_clips([{"a": 1}, "b", 3, {"c": 2}])) == 2)
long_clips = [{"i": i} for i in range(200)]
check("_limit_clips membatasi jumlah", len(_limit_clips(long_clips)) <= SECTION_MAX_CLIPS,
      f"{len(_limit_clips(long_clips))} (cap {SECTION_MAX_CLIPS})")

# --- simulasi Gemini yang hanya bisa menulis ~900 kata per panggilan ---
def words_of(text: str) -> int:
    return len(text.split())


class FakeProvider:
    """Gemini yang secara wajar menulis paling banyak `per_call_cap` kata."""

    def __init__(self, per_call_cap=900):
        self.cap = per_call_cap
        self.calls = []

    def generate_section(self, analysis, timeline, *, part, total_parts, words,
                         wpm=150, focus="", prev_hint="", next_hint="",
                         part_events=None, revision_hint=""):
        self.calls.append({"part": part, "asked": words})
        # Gemini menulis sebesar yang bisa, tidak lebih dari yang diminta.
        wrote = min(words, self.cap)
        return {
            "section": {
                "section_id": part,
                "visual": f"visual bagian {part}",
                "voice_over": " ".join(["kata"] * wrote),
                "clips": [{"beat": "walk", "start": "00:00:00", "src": 2.0,
                            "trx": "baref", "out": 2.0}] * 5,
            }
        }


pipeline = StoryPipeline.__new__(StoryPipeline)
pipeline.wpm = WPM

timeline = [{"i": i, "start": f"00:0{i//60}:{i%60:02d}", "text": "x"} for i in range(100)]
analysis = {
    "title": "Sakamoto",
    "story_arc": {"setup": "a", "rising": "b", "mid": "c", "climax": "d", "ending": "e"},
    "important_events": [{"event": f"e{i}", "start": f"00:{i//60:02d}:00"} for i in range(40)],
}

# --- SKENARIO UTAMA: target 15 menit ---
target_minutes = 15
target_words = target_minutes * WPM  # 2250
prov = FakeProvider(per_call_cap=900)
pipeline.provider = prov

raw = pipeline._write_by_sections(analysis, timeline, target_minutes,
                                 SECTION_PARTS, target_words)

total_words = sum(words_of(s["voice_over"]) for s in raw["sections"])
check("semua bagian dipanggil", len(prov.calls) == SECTION_PARTS, len(prov.calls))
check("tidak ada bagian kosong", all(s.get("voice_over") for s in raw["sections"]),
      len(raw["sections"]))
check("section_id berurutan 1..N",
      [s["section_id"] for s in raw["sections"]] == list(range(1, len(raw["sections"]) + 1)),
      [s["section_id"] for s in raw["sections"]])

# INI inti testnya: total harus jauh MELAMPAUI batas per-panggilan.
check("total kata > batas per-panggilan (900)", total_words > 900, total_words)
check("total kata >= 50% target", total_words >= target_words * 0.5,
      f"{total_words}/{target_words}")
print(f"  -> total {total_words} kata = {total_words / WPM * 60 / 60:.1f} menit "
      f"(target {target_minutes} menit), {len(prov.calls)} panggilan x cap 900")

# tiap bagian dapat jatah yang wajar (tidak nol, tidak berlebihan)
asked = [c["asked"] for c in prov.calls]
check("jatah kata per bagian > 0", all(a > 0 for a in asked), asked)
check("jatah per bagian <= jitter tidak wajar", all(a < target_words for a in asked), asked)
check("semua bagian dapat jatah sama", len(set(asked)) == 1, asked)

# --- jalur lama: 1 panggilan tidak mungkin mencapai target ---
single = FakeProvider(per_call_cap=900)
check("1 panggilan hanya bisa 900 kata (cwd lama)", single.cap == 900)
check("900 << 2250 (itulah bug-nya)", 900 < target_words, f"900 < {target_words}")

# --- retry menaikkan jatah kata ---
prov2 = FakeProvider(per_call_cap=900)
pipeline.provider = prov2
raw_rev = pipeline._write_by_sections(analysis, timeline, target_minutes,
                                     SECTION_PARTS, target_words,
                                     revision_hint="NASKAH JAUH TERLALU PENDEK")
asked_rev = [c["asked"] for c in prov2.calls]
check("retry menaikkan jatah kata", asked_rev[0] > asked[0], f"{asked[0]} -> {asked_rev[0]}")

# --- hanya boleh satu section per panggilan ---
prov3 = FakeProvider(per_call_cap=900)
pipeline.provider = prov3
class MultiSection(FakeProvider):
    def generate_section(self, *a, **k):
        out = super().generate_section(*a, **k)
        # Gemini_bandit menambah section liar -- harus dibuang
        out["sections"] = [{"section_id": 99, "voice_over": "duplikat", "clips": []}]
        return out

pipeline.provider = MultiSection(per_call_cap=900)
raw_multi = pipeline._write_by_sections(analysis, timeline, target_minutes,
                                       SECTION_PARTS, target_words)
ids = [s["section_id"] for s in raw_multi["sections"]]
check("section liar dari Gemini dibuang", 99 not in ids, ids)
check("jumlah section == jumlah panggilan", len(ids) == SECTION_PARTS, len(ids))

# --- fallback tanpa event ---
no_events = {"title": "X", "story_arc": {}, "important_events": []}
pipeline.provider = FakeProvider(per_call_cap=900)
raw_no = pipeline._write_by_sections(no_events, timeline, target_minutes,
                                     SECTION_PARTS, target_words)
check("tanpa event tetap menghasilkan bagian", len(raw_no["sections"]) > 0,
      len(raw_no["sections"]))

# --- Semua bagian kosong = harus gagal keras, bukan diam-diam output kosong ---
class EmptyProvider(FakeProvider):
    def generate_section(self, *a, **k):
        return {"section": {"visual": "x", "voice_over": "", "clips": []}}

pipeline.provider = EmptyProvider(per_call_cap=900)
raw_empty = pipeline._write_by_sections(analysis, timeline, target_minutes,
                                        SECTION_PARTS, target_words)
check("semua kosong -> sections kosong (builder akan error jelas)", raw_empty["sections"] == [],
      len(raw_empty["sections"]))

# --- Gemini kirim section langsung (tanpa wrapper) ---
class DirectProvider(FakeProvider):
    def generate_section(self, *a, **k):
        out = super().generate_section(*a, **k)
        return out["section"]

pipeline.provider = DirectProvider(per_call_cap=900)
raw_direct = pipeline._write_by_sections(analysis, timeline, target_minutes,
                                         SECTION_PARTS, target_words)
check("section tanpa wrapper 'section' diterima", len(raw_direct["sections"]) > 0,
      len(raw_direct["sections"]))

print("-" * 60)
if fails:
    print(f"{len(fails)} FAIL: {fails}")
    sys.exit(1)
print("ALL PASS")