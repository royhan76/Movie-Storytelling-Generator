"""Validasi + perbaikan storyboard hasil Gemini.

Tiga tanggung jawab:
1. Hard validation — src <= 3.0, trx valid, out > 0, start dalam timeline subtitle.
2. Normalization — hitung `out` dari `trx` kalau AI salah, clamp `src`, dedupe timestamp.
3. Duration solve — sesuaikan visual vs voice-over memakai prioritas transform.
"""
from typing import Dict, List, Optional, Tuple

from app.schemas.storyboard import Clip, Section, Storyboard

MAX_SRC = 3.0
MIN_SRC = 0.4

# out = f(src, trx). baref_identity, fz12 freeze, slow = src / speed
TRANSFORM_SPEED: Dict[str, Optional[float]] = {
    "baref": None,   # out = src
    "fz12": None,    # out = 1.2 (freeze frame)
    "s65": 0.65,
    "s50": 0.50,
    "s35": 0.35,
}
FREEZE_OUT = 1.2

# Prioritas pemakaian transform saat menutup selisih durasi (PLAN1.md §13)
SOLVER_ORDER = ["fz12", "s65", "s50", "s35"]

ALLOWED_BEATS = {
    "walk", "run", "fight", "confront", "silent-stare", "weapon-aim", "weapon-fire",
    "shout", "fall", "embrace", "child", "phone-call", "document", "village",
    "corpse", "crowd-panic", "photo-memory",
}


def out_for(src: float, trx: str) -> float:
    """Hitung durasi hasil dari src + trx."""
    speed = TRANSFORM_SPEED.get(trx)
    if trx == "fz12":
        return FREEZE_OUT
    if speed is None:
        return src
    return round(src / speed, 1)


def normalize_clip(clip: Clip) -> Clip:
    """Paksa satu clip masuk batasan PLAN1.md §11-§12."""
    # beat: lowercase-kebab-case
    beat = "-".join(str(clip.beat).strip().lower().replace("_", "-").split())

    # trx: hanya kode yang diizinkan
    trx = clip.trx if clip.trx in TRANSFORM_SPEED else "baref"

    # src: 0 < src <= 3.0
    try:
        src = float(clip.src)
    except (TypeError, ValueError):
        src = 2.0
    if src <= 0:
        src = 1.5
    src = min(src, MAX_SRC)
    src = max(src, MIN_SRC)

    # out: recompute dari trx supaya konsisten (AI sering salah hitung)
    out = out_for(src, trx)

    return Clip(clip_id=clip.clip_id, beat=beat, start=clip.start, src=round(src, 1), trx=trx, out=out)


def clamp_to_timeline(start: str, timeline_start: float, timeline_end: float, parser) -> str:
    """Pastikan start berada di dalam durasi subtitle (PLAN1.md §15.5)."""
    try:
        sec = parser.to_seconds(start)
    except Exception:
        sec = timeline_start
    if sec < timeline_start:
        sec = timeline_start
    if sec > max(timeline_start, timeline_end - MIN_SRC):
        sec = max(timeline_start, timeline_end - MIN_SRC)
    return parser.seconds_to_hhmmss(sec)


def validate_section(
    section: Section,
    parser,
    timeline_start: float,
    timeline_end: float,
    seen_starts: set,
) -> Tuple[Section, List[str]]:
    """Normalisasi + validasi satu section. Return (section, issues)."""
    issues: List[str] = []
    fixed: List[Clip] = []
    dropped = 0

    for raw in section.clips:
            clip = normalize_clip(raw)

            # clamp ke timeline DULU, baru cek duplikat — kalau dibalik, dua start
            # berbeda bisa sama-sama di-clamp ke batas lalu lolos sebagai "unik"
            clamped = clamp_to_timeline(clip.start, timeline_start, timeline_end, parser)
            if clamped != clip.start:
                issues.append(f"section {section.section_id}: start {clip.start} -> {clamped} (di luar timeline)")
                clip.start = clamped

            if clip.start in seen_starts:
                # PLAN1.md §15.7 — menghindari pengulangan timestamp
                dropped += 1
                continue

            seen_starts.add(clip.start)
            fixed.append(clip)

    section.clips = fixed

    if dropped:
        issues.append(f"section {section.section_id}: {dropped} clip duplikat timestamp dibuang")

    # kosongkan beat tidak dikenal di luar daftar contoh — tetap izinkan (PLAN1 §9)
    unknown = [c.beat for c in section.clips if c.beat not in ALLOWED_BEATS]
    if unknown:
        issues.append(f"section {section.section_id}: beat baru dipakai: {sorted(set(unknown))}")

    section.clip_count = len(section.clips)
    section.total_clip_duration = round(sum(c.out for c in section.clips), 1)
    return section, issues


def solve_durations(
    sb: Storyboard,
    tolerance_pct: float = 15.0,
    max_passes: int = 100,
) -> Tuple[Storyboard, Dict]:
    """Tutup selisih visual vs voice-over memakai prioritas transform (§13).

    Visual terlalu pendek  -> perlambat clip (baref -> s65 -> s50 -> s35).
    Visual terlalu panjang  -> turunkan clip terpanjang ke baref.

    Catatan: fz12 (freeze frame) sengaja TIDAK dipakai untuk menambah durasi.
    out fz12 selalu 1.2 detik, jadi untuk src > 1.2 justru MEMPERCEPAT clip
    (src 2.0 -> out 1.2). Freeze frame hanya relevan kalau naskah menyebut
    freeze, bukan sebagai alat menambah durasi visual.
    """
    vo_text = "\n".join(s.voice_over for s in sb.sections)
    words = len(vo_text.split())
    target_seconds = (words / 150.0) * 60.0 if words else 0.0

    all_clips = [c for s in sb.sections for c in s.clips]
    if not all_clips or target_seconds <= 0:
        return sb, {"adjusted": 0, "target_seconds": round(target_seconds, 1), "final_diff_pct": 0.0}

    passes = 0
    for _ in range(max_passes):
        current = sum(c.out for c in all_clips)
        diff = target_seconds - current
        diff_pct = abs(diff) / target_seconds * 100
        if diff_pct <= tolerance_pct:
            break
        passes += 1

        if diff > 0:
            # visual kurang -> ubah clip baref/s65/s50 ke slow motion lebih tinggi untuk menambah durasi visual
            candidate = next((c for c in reversed(all_clips) if c.trx in ("baref", "s65", "s50")), None)
            if not candidate:
                break
            next_trx = {"baref": "s65", "s65": "s50", "s50": "s35"}.get(candidate.trx, "s35")
            candidate.trx = next_trx
            candidate.out = out_for(candidate.src, next_trx)
        else:
            # visual kelebihan -> longest clip yang masih transform, turunkan ke baref
            transform_clips = [c for c in all_clips if c.trx != "baref"]
            if not transform_clips:
                break
            worst = max(transform_clips, key=lambda c: c.out)
            worst.trx = "baref"
            worst.out = out_for(worst.src, "baref")

    final_total = sum(c.out for c in all_clips)
    final_diff = abs(target_seconds - final_total)
    for section in sb.sections:
        section.clip_count = len(section.clips)
        section.total_clip_duration = round(sum(c.out for c in section.clips), 1)
    sb.summary.total_sections = len(sb.sections)
    sb.summary.total_clips = len(all_clips)
    sb.summary.total_clip_duration = round(final_total, 1)
    sb.project.estimated_word_count = words
    sb.project.estimated_voiceover_seconds = round(target_seconds, 1)

    return sb, {
        "adjusted": passes,
        "target_seconds": round(target_seconds, 1),
        "final_diff_pct": round(final_diff / target_seconds * 100, 1) if target_seconds else 0.0,
    }