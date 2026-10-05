"""Test regression clip validator: src <= 3.0, trx, timeline, dedupe, solver."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.schemas.storyboard import Clip, Section, Storyboard, ProjectInfo, Summary
from app.services.clip_validator import (
    MAX_SRC, normalize_clip, out_for, validate_section, solve_durations,
)
from app.services.srt_parser import SrtParser

parser = SrtParser()
fails = []


def check(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' -> ' + extra) if extra else ''}")
    if not ok:
        fails.append(name)


# --- out_for per transform (PLAN1.md §12) ---
check("baref out = src", out_for(2.0, "baref") == 2.0)
check("fz12 out = 1.2", out_for(1.5, "fz12") == 1.2)
check("s65 out = src/0.65", out_for(2.0, "s65") == 3.1, str(out_for(2.0, "s65")))
check("s50 out = src/0.50", out_for(2.0, "s50") == 4.0)
check("s35 out = src/0.35", out_for(2.0, "s35") == 5.7, str(out_for(2.0, "s35")))

# --- normalize: clamp src ---
check("src 4.0 di-clamp ke 3.0", normalize_clip(Clip(beat="walk", start="00:00:01", src=4.0, trx="baref", out=4.0)).src == MAX_SRC)
check("src 0 di-clamp", normalize_clip(Clip(beat="walk", start="00:00:01", src=0, trx="baref", out=1)).src > 0)
check("src negatif di-clamp", normalize_clip(Clip(beat="walk", start="00:00:01", src=-2, trx="baref", out=1)).src > 0)
check("trx ilegal -> baref", normalize_clip(Clip(beat="walk", start="00:00:01", src=2, trx="zoom99", out=2)).trx == "baref")
check("beat di-normalize", normalize_clip(Clip(beat="Silent Stare!", start="00:00:01", src=2, trx="baref", out=2)).beat == "silent-stare")
check("out dihitung ulang dari trx",
      normalize_clip(Clip(beat="walk", start="00:00:01", src=2.0, trx="s50", out=99.0)).out == 4.0)
check("src non-numeric -> default", normalize_clip(Clip(beat="walk", start="00:00:01", src="abc", trx="baref", out=2)).src > 0)

# --- validate_section: dedupe + timeline clamp ---
tl_start, tl_end = parser.timeline_bounds(parser.parse(
    "1\n00:00:05,000 --> 00:00:20,000\nA\n\n2\n00:00:21,000 --> 00:00:40,000\nB\n"
))
sec = Section(section_id=1, visual="v", voice_over="n", clips=[
    Clip(clip_id=1, beat="walk", start="00:00:10", src=2.0, trx="baref", out=2.0),
    Clip(clip_id=2, beat="run", start="00:00:10", src=2.0, trx="baref", out=2.0),   # duplikat
    Clip(clip_id=3, beat="fall", start="01:30:00", src=2.0, trx="baref", out=2.0),  # di luar timeline
    Clip(clip_id=4, beat="fight", start="00:00:00", src=2.0, trx="baref", out=2.0),  # sebelum timeline
])
sec, issues = validate_section(sec, parser, tl_start, tl_end, set())
check("duplikat timestamp dibuang", len(sec.clips) == 3, f"{len(sec.clips)} clip")
check("start di luar atas di-clamp", any("di luar timeline" in i for i in issues))
check("semua start dalam [5s, 40s]", all(
    5 <= parser.to_seconds(c.start) <= 40 for c in sec.clips))
check("clip_count dihitung", sec.clip_count == len(sec.clips))
check("total durasi dihitung", sec.total_clip_duration == 6.0)

# --- invariant: tidak ada src > 3.0 setelah validate ---
allbig = Section(section_id=1, visual="v", voice_over="n", clips=[
    Clip(clip_id=i, beat="walk", start=f"00:00:{i:02d}", src=5.0, trx="baref", out=5.0) for i in range(1, 6)
])
allbig, _ = validate_section(allbig, parser, 0.0, 100.0, set())
check("semua src <= 3.0 setelah validate", all(c.src <= 3.0 for c in allbig.clips))

# --- solver:visual kurang -> naik lewat fz12 ---
sb = Storyboard(project=ProjectInfo(target_duration_minutes=2), summary=Summary(), sections=[
    Section(section_id=1, visual="v", voice_over=" ".join(["kata"] * 220), clips=[
        Clip(clip_id=i, beat="walk", start=f"00:00:{i:02d}", src=2.0, trx="baref", out=2.0) for i in range(1, 4)
    ])
])
sb, rep = solve_durations(sb)
check("solver menambah durasi saat kurang", rep["adjusted"] > 0, f"adjusted={rep['adjusted']}, diff={rep['final_diff_pct']}%")
check("solver pakai perlambatan (bukan fz12)", any(c.trx in ("s65", "s50", "s35") for c in sb.sections[0].clips),
      str([c.trx for c in sb.sections[0].clips]))
check("solver tidak pakai fz12 untuk menambah durasi (out fz12=1.2s < src=2.0)",
      not any(c.trx == "fz12" for c in sb.sections[0].clips),
      str([c.trx for c in sb.sections[0].clips]))
check("solver summary konsisten", sb.summary.total_clip_duration == round(sum(c.out for c in sb.sections[0].clips), 1))

# --- solver: visual kelebihan -> turun ke baref (batas bawah = clip_count x src) ---
sb2 = Storyboard(project=ProjectInfo(target_duration_minutes=1), summary=Summary(), sections=[
    Section(section_id=1, visual="v", voice_over="kata kata", clips=[
        Clip(clip_id=i, beat="walk", start=f"00:00:{i:02d}", src=3.0, trx="s35", out=8.6) for i in range(1, 6)
    ])
])
before_total = sum(c.out for c in sb2.sections[0].clips)
sb2, rep2 = solve_durations(sb2)
after_total = sum(c.out for c in sb2.sections[0].clips)

check("solver memangkas durasi saat kelebihan", after_total < before_total,
      f"{before_total}s -> {after_total}s")
check("solver turun ke baref", all(c.trx == "baref" for c in sb2.sections[0].clips))
# baref adalah batas bawah: out = src, jadi total tak mungkin di bawah n_clip * src
floor = sum(c.src for c in sb2.sections[0].clips)
check("solver berhenti di batas bawah baref", after_total == floor, f"{after_total}s == {floor}s")
# selisih masih besar karena 5 clip x 3s tak mungkin menutup 2 kata narasi
# -> itu|report warning| pekerjaan revisi Gemini (retry), bukan tugas solver
check("selisih besar tetap dilaporkan sbg warning", rep2["final_diff_pct"] > 100,
      f"diff={rep2['final_diff_pct']}% -> report warning")

# --- solver: ceiling saat visual jauh kurang (butuh lebih banyak clip, bukan trx) ---
sb3 = Storyboard(project=ProjectInfo(target_duration_minutes=2), summary=Summary(), sections=[
    Section(section_id=1, visual="v", voice_over=" ".join(["kata"] * 220), clips=[
        Clip(clip_id=i, beat="walk", start=f"00:00:{i:02d}", src=2.0, trx="baref", out=2.0) for i in range(1, 4)
    ])
])
sb3, rep3 = solve_durations(sb3)
ceiling = sum(
    (1.2 if c.trx == "fz12" else c.src / 0.35 if c.trx == "s35" else c.src)
    for c in sb3.sections[0].clips
)
check("solver tidak melebihi ceiling transform", sum(c.out for c in sb3.sections[0].clips) <= ceiling + 0.1,
      f"total={sum(c.out for c in sb3.sections[0].clips)}s ceiling={ceiling:.1f}s")

print("-" * 50)
if fails:
    print(f"FAIL ({len(fails)}): {fails}")
    raise SystemExit(1)
print("ALL PASS")