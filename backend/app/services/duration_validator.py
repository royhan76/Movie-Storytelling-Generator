"""Durasi voice-over dan ringkasan storyboard.

Kecepatan baca default 150 kata/menit, dapat diatur lewat env DEFAULT_WPM.
"""
import os
from typing import Dict, List, Tuple

from app.schemas.storyboard import Storyboard

DEFAULT_WPM = int(os.getenv("DEFAULT_WPM", "125"))


def word_count(text: str) -> int:
    return len([w for w in (text or "").split() if w.strip()])


def vo_duration_seconds(text: str, wpm: int = DEFAULT_WPM) -> float:
    if wpm <= 0:
        wpm = DEFAULT_WPM
    return (word_count(text) / wpm) * 60.0


# Toleransi durasi voice-over terhadap TARGET yang diminta user.
# Tanpa ini, naskah 15 menit yang hanya berisi 200 kata tetap lolos sebagai
# "ok" -- karena yang dibandingkan cuma VO vs klip, bukan VO vs target.
TARGET_TOLERANCE_PCT = 25.0


def recalc(storyboard: Storyboard, wpm: int = DEFAULT_WPM) -> Tuple[Storyboard, Dict]:
    """Hitung ulang clip_count, durasi, dan estimasi voice-over.

    Dua pemeriksaan terpisah, keduanya wajib lolos:
    1. keselarasan internal  : durasi visual  vs durasi voice-over
    2. keselarasan terhadap target : durasi voice-over vs durasi yang diminta user

    Yang kedua dulu tidak ada, jadi target 15 menit bisa berakhir dengan naskah
    200 kata tanpa satu pun peringatan.
    """
    for section in storyboard.sections:
        section.clip_count = len(section.clips)
        section.total_clip_duration = round(sum(c.out for c in section.clips), 1)

    all_clips: List = [c for s in storyboard.sections for c in s.clips]
    total_out = round(sum(c.out for c in all_clips), 1)

    storyboard.summary.total_sections = len(storyboard.sections)
    storyboard.summary.total_clips = len(all_clips)
    storyboard.summary.total_clip_duration = total_out

    full_vo = "\n".join(s.voice_over for s in storyboard.sections)
    words = word_count(full_vo)
    est = vo_duration_seconds(full_vo, wpm)

    storyboard.project.estimated_word_count = words
    storyboard.project.estimated_voiceover_seconds = round(est, 1)

    target_seconds = float(storyboard.project.target_duration_minutes or 0) * 60.0
    target_words = int(target_seconds / 60.0 * wpm) if wpm > 0 else 0

    # --- 1. keselarasan internal: visual vs voice-over ---
    diff = abs(est - total_out)
    diff_pct = (diff / est * 100) if est > 0 else 0.0

    # --- 2. keselarasan terhadap target: voice-over vs durasi yang diminta ---
    target_diff = abs(est - target_seconds)
    target_diff_pct = (target_diff / target_seconds * 100) if target_seconds > 0 else 0.0
    on_target = target_seconds <= 0 or target_diff_pct <= TARGET_TOLERANCE_PCT

    report = {
        "est_vo_sec": round(est, 1),
        "total_clip_out": total_out,
        "diff_sec": round(diff, 1),
        "diff_pct": round(diff_pct, 1),
        "target_seconds": round(target_seconds, 1),
        "target_words": target_words,
        "word_count": words,
        "target_diff_sec": round(target_diff, 1),
        "target_diff_pct": round(target_diff_pct, 1),
        "on_target": on_target,
        # ok hanya kalau internal SEJUGA sesuai target. Salah satu tidak cukup.
        "ok": (diff_pct <= 15.0) and on_target,
    }
    return storyboard, report