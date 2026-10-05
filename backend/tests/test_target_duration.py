"""Regression test: target durasi harus BENAR-BENAR dipatology.

Bug yang diuji: prompt menyuruh Gemini "gunakan kata SECARA SANGAT SAKAR" dan
recalc() hanya membandingkan voice-over vs klip -- tidak pernah vs target. Hasilnya
target 15 menit bisa dapat naskah 197 kata dan tetap dilaporkan ok=True.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.schemas.storyboard import Clip, ProjectInfo, Section, Storyboard, Summary
from app.services.duration_validator import TARGET_TOLERANCE_PCT, recalc

WPM = 150
fails = []


def check(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{' -> ' + str(extra) if extra else ''}")
    if not ok:
        fails.append(name)


def make_storyboard(target_minutes: int, words: int, clips: int | None = None) -> Storyboard:
    """Storyboard dengan voice-over setepatnya `words` kata.

    `clips` default: dipilih supaya total durasi visual ~= durasi voice-over,
    supaya test ini menguji(target-vs-VO saja dan tidak ikut gagal karena
    misalignment internal.
    """
    if clips is None:
        vo_sec = words / WPM * 60
        clips = max(4, int(vo_sec / 2.0 / 4) * 4)
    per_section = words // 4
    sections = []
    for i in range(4):
        vo = " ".join(["kata"] * per_section)
        sections.append(
            Section(
                section_id=i + 1,
                visual="visual",
                voice_over=vo,
                clips=[
                    Clip(clip_id=j + 1, beat="walk", start="00:00:00", src=2.0, trx="baref", out=2.0)
                    for j in range(clips // 4)
                ],
            )
        )
    sb = Storyboard(
        project=ProjectInfo(
            title="T",
            source_subtitle="s.srt",
            target_duration_minutes=target_minutes,
            estimated_voiceover_seconds=0,
            estimated_word_count=0,
        ),
        sections=sections,
        summary=Summary(),
    )
    return sb


# --- BUG: target 15 menit, naskah cuma 197 kata ---
sb = make_storyboard(target_minutes=15, words=197)
_, report = recalc(sb, WPM)

target_words_15 = 15 * WPM  # 2250
check("target 15m expects 2250 words", target_words_15 == 2250, target_words_15)
check("report knows target words", report["target_words"] == 2250, report["target_words"])
check("197 kata dihitung sebagai GAGAL", report["on_target"] is False, f"on_target={report['on_target']}")
check("197 kata TIDAK boleh ok", report["ok"] is False, f"ok={report['ok']}")
check(
    "target_diff_pct jauh di atas toleransi",
    report["target_diff_pct"] > TARGET_TOLERANCE_PCT,
    f"{report['target_diff_pct']}%",
)
check("target_seconds = 900", report["target_seconds"] == 900.0, report["target_seconds"])
check("est_vo_sec = 78.8 (197 kata @150wpm)", abs(report["est_vo_sec"] - 78.8) < 1.5, report["est_vo_sec"])

# --- naskah BENAR-benar 15 menit harus lolos ---
sb = make_storyboard(target_minutes=15, words=2250)
_, report_ok = recalc(sb, WPM)
check("2250 kata (tepat 15m) -> on_target", report_ok["on_target"] is True, f"{report_ok['target_diff_pct']}%")
check("2250 kata + klip senada -> ok", report_ok["ok"] is True, f"diff_internal={report_ok['diff_pct']}%")

# --- toleransi: 15 menit tolerir sampai 25% ---
sb = make_storyboard(target_minutes=15, words=int(2250 * 0.80))
_, r80 = recalc(sb, WPM)
check("80% dari target masih ditoleransi (dalam 25%)", r80["on_target"] is True, f"{r80['target_diff_pct']}%")

sb = make_storyboard(target_minutes=15, words=int(2250 * 0.60))
_, r60 = recalc(sb, WPM)
check("60% dari target DITOLAK", r60["on_target"] is False, f"{r60['target_diff_pct']}%")

# --- kasus user: 2 menit, 241 kata ---
# 241 dari 300 kata = 80% dari target, jadi MASIH dalam toleransi 25%.
# Yang gagal di kasus user bukan "persen dari target" murni, tapi kombinasi:
# naskah pendek DAN durasi visual jauh di bawahnya.
sb = make_storyboard(target_minutes=2, words=241)
_, r2 = recalc(sb, WPM)
check("target 2m = 300 kata", r2["target_words"] == 300, r2["target_words"])
check("241 kata: 80% target -> masih dalam toleransi 25%", r2["on_target"] is True, f"{r2['target_diff_pct']}%")
check("241 kata + klip senada = sah (semua syarat terpenuhi)", r2["ok"] is True, f"ok={r2['ok']}")

# 241 kata dengan 18 klip (kasus riil user): visual cuma ~36s vs VO ~96s -> gagal
sb = make_storyboard(target_minutes=2, words=241, clips=18)
_, r2b = recalc(sb, WPM)
check("241 kata + 18 klip: internal mismatch terdeteksi", r2b["diff_pct"] > 15.0, f"{r2b['diff_pct']}%")
check("241 kata + 18 klip TIDAK boleh ok", r2b["ok"] is False, f"ok={r2b['ok']}")

# --- internal alignment tetap bekerja seperti sebelumnya ---
sb = make_storyboard(target_minutes=2, words=300)
sb.sections[0].clips = sb.sections[0].clips[:1]  # klip banyak dibuang
_, r_align = recalc(sb, WPM)
check("internal diff_pct masih dihitung", r_align["diff_pct"] > 0, r_align["diff_pct"])
check("ok butuh internal SEJUGA cocok", r_align["ok"] is False, f"ok={r_align['ok']}")

# --- target 0 menit tidak boleh crash ---
sb = make_storyboard(target_minutes=0, words=100)
_, r_zero = recalc(sb, WPM)
check("target 0 menit tidak crash", isinstance(r_zero["ok"], bool), r_zero["ok"])
check("target 0 menit = on_target True (tidak ada target)", r_zero["on_target"] is True)

# --- prompt TIDAK BOLEH menyuruh naskahnya singkat ---
print("-" * 60)
from app.providers.gemini import GeminiProvider

prov = GeminiProvider.__new__(GeminiProvider)
prov.client = type("C", (), {"models": type("M", (), {"generate_content": staticmethod(
    lambda **_kw: type("R", (), {"text": "{}"})())})()})()
prov.api_key = "x"
prov.model = "m"
prov.models = ["m"]

captured = {}
def fake_call(prompt, temperature=0.3, json_mode=True, label="call"):
    captured["prompt"] = prompt
    return {}
prov._call = fake_call

for target in (2, 15):
    prov.generate_storytelling(
        {"title": "T", "important_events": []},
        [{"i": 1, "start": "00:00:00", "text": "halo"}],
        target,
        wpm=WPM,
    )
    p = captured["prompt"]
    lowered = p.lower()
    check(
        f"prompt {target}m tidak menyuruh 'sangat sakit/singkat'",
        "sangat sakit" not in lowered and "sangat singkat" not in lowered,
    )
    check(f"prompt {target}m minta total kata eksplisit", f"{target * WPM} kata" in p, f"{target*WPM} kata")
    check(f"prompt {target}m menyatakan bukan ringkasan", "bukan ringkasan" in lowered)
    check(f"prompt {target}m beri instructions per bagian", "per bagian" in lowered)

print("-" * 60)
if fails:
    print(f"{len(fails)} FAIL: {fails}")
    sys.exit(1)
print("ALL PASS")