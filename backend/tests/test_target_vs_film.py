"""Regression test: target vs panjang film, dan skalanya event kompresi.

Dua bug nyata yang ditemukan dari generate sungguhan:
1. Target 15 menit untuk film 2.6 menit (156s) = 5.8x panjang film. Tidak
   mungkin dicapai; dulu aplikasi diam-diam menghasilkan naskah 200 detik.
2. `StoryAnalyzer.compress()` memotong event ke 40 tetap. Film 124 menit jadi
   naskah 78 detik karena Gemini cuma diberi 40 kejadian dari ribuan.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.story_analyzer import (
    COMPRESS_MAX_EVENTS, COMPRESS_MIN_EVENTS, StoryAnalyzer, events_for_target,
)

fails = []


def check(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' -> ' + str(extra) if extra else ''}")
    if not ok:
        fails.append(name)


# --- BUG 1: events_for_target harus ikut skala dengan target ---
check("target 2m -> minimal 40 event", events_for_target(2) == COMPRESS_MIN_EVENTS, events_for_target(2))
e5 = events_for_target(5)
e15 = events_for_target(15)
check("target 5m > target 2m", e5 > events_for_target(2), f"{events_for_target(2)} -> {e5}")
check("target 15m > target 5m", e15 > e5, f"{e5} -> {e15}")
check("target 15m >= 100 event (bukan 40)", e15 >= 100, e15)
check("target 15m dibatasi max 160", e15 <= COMPRESS_MAX_EVENTS, e15)

# durasi sangat panjang tidak boleh meledakkan prompt
e180 = events_for_target(180)
check("target 180m tetap <= max", e180 <= COMPRESS_MAX_EVENTS, e180)

# wpm lain harus ikut dihitung -- tapi hanya kalau belum kena plafon.
# wpm 150 untuk 15 menit = 2250 kata * 0.075 = 168 -> sudah di-cap 160,
# jadi wpm 300 juga kena plafon yang sama. Yang dicek di sini adalah
# events_for_target() memakai wpm, bukan hardcode.
check("events_for_target memakai wpm (tidak hardcode)",
      events_for_target(5, wpm=150) < events_for_target(5, wpm=300),
      f"5m wpm150={events_for_target(5, wpm=150)} wpm300={events_for_target(5, wpm=300)}")
check("keduanya tetap <= plafon", events_for_target(5, wpm=300) <= COMPRESS_MAX_EVENTS,
      events_for_target(5, wpm=300))

# --- BUG 2: compress() harus menjatah sesuai max_events ---
def fake_analysis(n_events: int):
    return {
        "title": "T",
        "characters": ["A"],
        "setting": "S",
        "main_conflict": "C",
        "story_arc": {"setup": "x"},
        "important_events": [
            {"event": f"e{i}", "start": f"00:00:{i:02d}", "end": f"00:00:{i:02d}"}
            for i in range(n_events)
        ],
    }


# compress() adalah staticmethod -- panggil langsung lewat kelas.
check("compress tidak crash saat events kosong",
      StoryAnalyzer.compress(fake_analysis(0))["important_events"] == [])

c40 = StoryAnalyzer.compress(fake_analysis(500), max_events=40)
check("max_events=40 -> tepat 40 event", len(c40["important_events"]) == 40, len(c40["important_events"]))

c124 = StoryAnalyzer.compress(fake_analysis(500), max_events=events_for_target(15))
check("target 15m -> ~124 event diteruskan", len(c124["important_events"]) == events_for_target(15),
      len(c124["important_events"]))

#Event yang tersedia lebih sedikit dari jatah: jangan dipaksa/duplikat
c_small = StoryAnalyzer.compress(fake_analysis(10), max_events=124)
check("event kurang dari jatah -> dipakai semua, tidak dipaksa", len(c_small["important_events"]) == 10,
      len(c_small["important_events"]))

# jatah di bawah minimum harus dinaikkan ke minimum
c_low = StoryAnalyzer.compress(fake_analysis(500), max_events=5)
check("max_events di bawah minimum -> dinaikkan ke 40", len(c_low["important_events"]) == 40,
      len(c_low["important_events"]))

# jatah di atas maximum harus dipotong ke maximum
c_high = StoryAnalyzer.compress(fake_analysis(500), max_events=9999)
check("max_events di atas max -> dipotong ke 160", len(c_high["important_events"]) == COMPRESS_MAX_EVENTS,
      len(c_high["important_events"]))

# default (max_events=0) = minimum, bukan crash
c_def = StoryAnalyzer.compress(fake_analysis(500))
check("default max_events=0 -> 40 event", len(c_def["important_events"]) == 40, len(c_def["important_events"]))

# --- struktur hasil tidak berubah ---
keys = set(c124.keys())
check("compress pertahankan semua key", keys == {
    "title", "characters", "setting", "main_conflict", "story_arc", "important_events"}, sorted(keys))
ev = c124["important_events"][0]
check("event punya key yang benar", set(ev.keys()) == {"event", "start", "end"}, sorted(ev.keys()))

print("-" * 60)
if fails:
    print(f"{len(fails)} FAIL: {fails}")
    sys.exit(1)
print("ALL PASS")