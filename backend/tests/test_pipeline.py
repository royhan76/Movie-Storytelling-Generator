"""Test end-to-end pipeline Plan 1 dengan Gemini di-stub.

Mensimulasikan Gemini yang__: kirim src > 3.0, trx ilegal, start di luar
timeline, timestamp duplikat, dan durasi wildly meleset. Backend harus
menormalisasi semuanya, bukan crash.

Run: python -m tests.test_pipeline
"""
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) if False else None
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient

from app.api import routes
from app.main import app

SRT = """1
00:00:00,000 --> 00:00:04,000
Sakamoto adalah pembunuh bayaran legendaris yang ditakuti di seluruh Jepang.

2
00:00:04,000 --> 00:00:08,000
Suatu hari dia bertemu seorang gadis dan perlahanentially jatuh cinta.

3
00:00:08,000 --> 00:00:12,000
Demi keluarganya, ia memilih meninggalkan dunia membunuh dan membuka toko kelontong.

4
00:00:12,000 --> 00:00:16,000
Tapi masa lalunya tidak pernah benar-benar pergi.
"""

# Fixture SRT di atas cuma 16 detik. Dengan validasi "target maksimal 1.5x durasi
# film" yang baru, target 2 menit otomatis ditolak -- jadi test happy-path butuh
# SRT yang memang cukup panjang. 20 cue x 15 detik = 300 detik (5 menit), cukup
# untuk target 2 menit.
_LONG_LINES = [
    "Sakamoto adalah pembunuh bayaran legendaris yang ditakuti di seluruh Jepang.",
    "Reputasinya membuat namanya disebut-sebut di setiap gang emeritus.",
    "Suatu hari dia bertemu seorang gadis dan perlahan jatuh cinta.",
    "Untuk pertama kalinya dia merasakan kedamaian yang wajar.",
    "Demi keluarganya, ia memilih meninggalkan dunia membunuh.",
    "Ia membuka toko kelontong kecil di Oatundle.",
    "Tetangganya menganggap dia sebagai orang baik.",
    "Rumah kecil itu, jauh lebih kecil dari yang biasa.",
    "Tapi masa lalunya tidak pernah benar-benar pergi.",
    "Orang-orang lama mulai muncul di depan toko barunya.",
    "Satu per satu mereka datang dengan langkah yang berat.",
    "Sakamoto harus memilih: tenang atau bertarung.",
    "Ia memilih tenang setiap kali bisa.",
    "Tapi tenang selalu punya harga yang harus dibayar.",
    "Suatu malam, seseorang datang membawa kabar yang besar.",
    "Ruangan itu kembali sunyi setelah orang pergi.",
    "Sakamoto berdiri sendiri di depan rak kosong.",
    "Ia tahu damai ini hanya sementara.",
    "Untuk terakhir kalinya ia berdiri di depan pintu itu.",
    "Untuk pertama kalinya ia merasa benar-benar hidup.",
]


def build_long_srt(n_cues: int = 20, per_cue_sec: int = 15) -> str:
    """SRT dengan durasi cukup panjang untuk target 2 menit."""
    blocks = []
    for i in range(n_cues):
        start = i * per_cue_sec
        end = start + per_cue_sec
        text = _LONG_LINES[i % len(_LONG_LINES)]
        blocks.append(
            f"{i + 1}\n"
            f"{start // 3600:02d}:{(start // 60) % 60:02d}:{start % 60:02d},000 --> "
            f"{end // 3600:02d}:{(end // 60) % 60:02d}:{end % 60:02d},000\n"
            f"{text}"
        )
    return "\n\n".join(blocks) + "\n"


SRT_LONG = build_long_srt(20, 15)  # 300 detik = 5 menit

ANALYSIS = {
    "title": "Sakamoto: Pembunuh yang Pilih Damai",
    "characters": [{"name": "Sakamoto", "role": "protagonis"}],
    "setting": "Jepang, kota kecil",
    "main_conflict": "Masa lalu sebagai pembunuh vs kehidupan baru",
    "story_arc": {"opening": "a", "rising_action": "b", "midpoint": "c", "climax": "d", "ending": "e"},
    "important_events": [
        {"event": "Diperkenalkan", "start": "00:00:00", "end": "00:00:04"},
        {"event": "Jatuh cinta", "start": "00:00:04", "end": "00:00:08"},
    ],
}

# Gemini sengaja bermasalah: src > 3, trx ilegal, beat jelek, start crazy,
# ada duplikat, dan satu section tanpa voice_over.
MESSY_STORYBOARD = {
    "project": {"title": "Sakamoto: Pembunuh yang Pilih Damai"},
    "sections": [
        {
            "section_id": 1,
            "visual": "Pembunuh legendaris berjalan di malam hari",
            "voice_over": (
                "Dulu, Sakamoto adalah pembunuh bayaran yang namanya ditakuti di seluruh Jepang. "
                "Setiap malam ia keluar dari kegelapan untuk menyelesaikan target yang tak pernah terlihat. "
                "Tidak ada yang tahu wajah aslinya, tidak ada yang bisa menghentikannya. "
                "Di dunia itu, ia adalah puncak yang paling ditakuti."
            ),
            "clips": [
                {"clip_id": 1, "beat": "Walk!", "start": "00:00:00", "src": 4.5, "trx": "baref", "out": 4.5},
                {"clip_id": 2, "beat": "Silent Stare", "start": "00:00:02", "src": 2.0, "trx": "zoom99", "out": 7.0},
                {"clip_id": 3, "beat": "weapon-aim", "start": "01:45:00", "src": 2.0, "trx": "s50", "out": 9.9},
                {"clip_id": 4, "beat": "fall", "start": "00:00:02", "src": 1.5, "trx": "fz12", "out": 1.2},
                {"clip_id": 5, "beat": "bogus", "start": None, "src": 2.0, "trx": "baref", "out": 2.0},
                {"clip_id": 6, "beat": "shout", "start": "00:00:03", "src": "-1", "trx": "baref", "out": 2.0},
            ],
        },
        {
            "section_id": 2,
            "visual": "Tanpa voice-over — harus dibuang builder",
            "voice_over": "",
            "clips": [{"beat": "walk", "start": "00:00:05", "src": 2, "trx": "baref", "out": 2}],
        },
        {
            "section_id": 3,
            "visual": "Sakamoto memilih hidup sederhana",
            "voice_over": (
                "Tapi semuanya berubah ketika dia bertemu seorang gadis. "
                "Untuk pertama kalinya, ia merasa punya sesuatu yang bisa hilang. "
                "Demi keluarganya, sang legenda memilih meninggalkan dunia membunuh. "
                "Ia membuka toko kelontong kecil dan belajar tertawa lagi."
            ),
            "clips": [
                {"beat": "silent-stare", "start": "00:00:06", "src": 2.0, "trx": "baref", "out": 2.0},
                {"beat": "embrace", "start": "00:00:08", "src": 3.0, "trx": "s65", "out": 3.0},
                {"beat": "child", "start": "00:00:10", "src": 2.0, "trx": "fz12", "out": 1.2},
                {"beat": "village", "start": "00:00:12", "src": 1.2, "trx": "baref", "out": 1.2},
            ],
        },
    ],
    "summary": {},
}


class StubGemini:
    """Mencatat semua panggilan; dipakai SATU instance bersama."""

    calls: list = []

    def __init__(self):
        self.chunk_sizes: list = []

    def is_configured(self):
        return True

    def generate_story_analysis(self, timeline, chunk_size=350, language: str = "id", **kwargs):
        self.calls.append(("analysis", len(timeline)))
        self.chunk_sizes.append(len(timeline))
        return ANALYSIS

    def generate_storytelling(self, analysis, timeline, target_minutes, wpm=150, revision_hint="", language: str = "id", **kwargs):
        self.calls.append(("script", revision_hint))
        raw = json.loads(json.dumps(MESSY_STORYBOARD))
        raw["project"]["target_duration_minutes"] = target_minutes
        return raw

    def generate_hook(self, analysis, timeline, wpm=150, language: str = "id", **kwargs):
        return {}

    def generate_section(self, analysis, timeline, **kwargs):
        return {}


def main():
    StubGemini.calls = []          # reset di antara run
    stub = StubGemini()
    tmp = Path(tempfile.mkdtemp(prefix="alur_pipeline_"))
    routes.GeminiProvider = lambda *a, **k: stub   # instance sama dipakai semua panggilan
    routes.PROJECTS_DIR = tmp

    client = TestClient(app)
    fails = []

    def check(name, ok, extra=""):
        print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' -> ' + extra) if extra else ''}")
        if not ok:
            fails.append(name)

    # ---- health ----
    r = client.get("/api/health")
    ok = r.status_code == 200 and "gemini_configured" in r.json()
    check("health endpoint", ok)

    # ---- validasi input ----
    r = client.post("/api/generate", files={"subtitle": ("a.txt", b"x", "text/plain")},
                    data={"target_duration": 2})
    check("reject non-.srt", r.status_code == 400, str(r.status_code))

    r = client.post("/api/generate", files={"subtitle": ("a.srt", b"   ", "text/plain")},
                    data={"target_duration": 2})
    check("reject srt kosong", r.status_code == 400, str(r.status_code))

    r = client.post("/api/generate", files={"subtitle": ("a.srt", b"acak", "text/plain")},
                    data={"target_duration": 2})
    check("reject srt tidak bisa diparse", r.status_code == 400, str(r.status_code))

    for bad in [0, 500, -1]:
        r = client.post("/api/generate", files={"subtitle": ("a.srt", SRT.encode(), "text/plain")},
                        data={"target_duration": bad})
        check(f"reject target_duration={bad}", r.status_code == 400, str(r.status_code))

    # ---- target melebihi panjang film harus ditolak dengan pesan jelas ----
    # SRT cuma 16 detik; target 15 menit = 56x panjang film. Dulu ini lolos
    # dan menghasilkan naskah 200 detik yang sama sekali tidak sesuai.
    r = client.post("/api/generate",
                    files={"subtitle": ("tiny.srt", SRT.encode("utf-8"), "text/plain")},
                    data={"target_duration": 15})
    detail = r.json().get("detail", "")
    check("reject target jauh melebihi durasi film", r.status_code == 400, str(r.status_code))
    check("pesan menyebut durasi film", "Durasi film" in detail, detail[:60])
    check("pesan menyebut batas maksimal", "maksimal" in detail, detail[:60])
    check("pesan memberi saran turunkan target", "Turunkan target" in detail, detail[:60])

    # Target 1 menit untuk film 16 detik = 3.75x, juga harus ditolak.
    r = client.post("/api/generate",
                    files={"subtitle": ("tiny.srt", SRT.encode("utf-8"), "text/plain")},
                    data={"target_duration": 1})
    check("reject target 3.75x film", r.status_code == 400, str(r.status_code))

    # SRT_LONG = 300 detik; target 2 menit (120s) = 0.4x, harus LOLOS.
    r = client.post("/api/generate",
                    files={"subtitle": ("long.srt", SRT_LONG.encode("utf-8"), "text/plain")},
                    data={"target_duration": 2})
    check("target 0.4x film HARUS lolos", r.status_code == 200, str(r.status_code))

    # ---- happy path dengan data kotor ----
    r = client.post("/api/generate",
                    files={"subtitle": ("sakamoto.srt", SRT_LONG.encode("utf-8"), "text/plain")},
                    data={"target_duration": 2})
    check("generate dengan data kotor", r.status_code == 200, str(r.status_code))
    if r.status_code != 200:
        print(r.text[:500])
        return 1

    data = r.json()
    pid, sb = data["project_id"], data["storyboard"]
    report = sb.get("report", {})
    clips = [c for s in sb["sections"] for c in s["clips"]]

    print(f"     sections={len(sb['sections'])} clips={len(clips)} "
          f"cues={report.get('cue_count')} attempts={report.get('attempts')}")

    # ---- normalisasi data kotor ----
    check("section tanpa voice_over dibuang", len(sb["sections"]) == 2, f"{len(sb['sections'])} section")
    check("clip tanpa start dibuang", all(c["start"] for c in clips))
    check("SEMUA src <= 3.0", all(0 < c["src"] <= 3.0 for c in clips),
          f"max src={max((c['src'] for c in clips), default=0)}")
    check("SEMUA trx valid", all(c["trx"] in {"baref", "fz12", "s65", "s50", "s35"} for c in clips),
          str(sorted({c["trx"] for c in clips})))
    check("SEMUA out > 0", all(c["out"] > 0 for c in clips))
    check("beat lowercase-kebab", all(re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", c["beat"]) for c in clips),
          str(sorted({c["beat"] for c in clips})))
    ids = [c["clip_id"] for c in clips]
    check("clip_id unik", len(set(ids)) == len(ids), str(ids))
    check("clip_id berurutan", ids == sorted(ids), str(ids))
    check("clip_id rapat 1..N (tidak ada celah)", ids == list(range(1, len(clips) + 1)), str(ids))

    # out harus = f(src, trx)
    def expect_out(src, trx):
        return 1.2 if trx == "fz12" else {"baref": src, "s65": src / 0.65, "s50": src * 2, "s35": src / 0.35}[trx]
    ok_out = all(abs(c["out"] - expect_out(c["src"], c["trx"])) < 0.06 for c in clips)
    check("out = f(src, trx) untuk semua clip", ok_out,
          str([(c["src"], c["trx"], c["out"], round(expect_out(c["src"], c["trx"]), 1)) for c in clips][:3]))

    # start harus berada di dalam timeline subtitle (0..300s untuk SRT_LONG)
    # -- dan TIDAK boleh menunjuk ke tepat akhir film, itu di luar jangkauan.
    def sec(ts):
        h, m, s = ts.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)

    timeline_end = 300.0
    ok_range = all(0 <= sec(c["start"]) <= timeline_end for c in clips)
    check("SEMUA start dalam timeline subtitle", ok_range,
          str(sorted({c["start"] for c in clips})))

    starts = [c["start"] for c in clips]
    check("tidak ada timestamp duplikat", len(starts) == len(set(starts)))

    # ---- aritmetika ----
    ok_arith = True
    for s in sb["sections"]:
        real_dur = round(sum(c["out"] for c in s["clips"]), 1)
        if s["clip_count"] != len(s["clips"]) or abs(s["total_clip_duration"] - real_dur) > 0.06:
            ok_arith = False
    check("clip_count & total_clip_duration konsisten", ok_arith)
    check("summary konsisten", sb["summary"]["total_clips"] == len(clips)
          and sb["summary"]["total_sections"] == len(sb["sections"]))

    words = sb["project"]["estimated_word_count"]
    est = sb["project"]["estimated_voiceover_seconds"]
    check("estimasi WPM konsisten", words > 0 and abs(est - words / 150 * 60) < 1.0,
          f"{words} kata -> {est}s")

    # ---- file output ----
    folder = tmp / pid
    for fname in ["storyboard.json", "storyboard.md", "voiceover.txt", "story_map.json"]:
        p = folder / fname
        check(f"file {fname}", p.exists() and p.stat().st_size > 20,
              f"{p.stat().st_size if p.exists() else 0} bytes")

    for fname in ["storyboard.json", "storyboard.md", "voiceover.txt", "story_map.json"]:
        r = client.get(f"/api/projects/{pid}/{fname}")
        check(f"GET {fname}", r.status_code == 200 and len(r.content) > 20, str(r.status_code))

    check("GET file tidak dikenal -> 404",
          client.get(f"/api/projects/{pid}/hack.py").status_code == 404)
    check("GET project tak ada -> 404",
          client.get("/api/projects/xxx/storyboard.json").status_code == 404)

    r = client.get("/api/projects")
    check("list projects", r.status_code == 200 and len(r.json()["projects"]) >= 1)

    # ---- markdown & voiceover consistency ----
    md = (folder / "storyboard.md").read_text(encoding="utf-8")
    check("md berisi heading Bagian", "## Bagian 1" in md)
    check("md berisi header tabel", "| beat | start | src | trx | out |" in md)
    vo = (folder / "voiceover.txt").read_text(encoding="utf-8").strip()
    check("voiceover.txt = concat VO", vo == "\n\n".join(s["voice_over"] for s in sb["sections"]).strip())

    # ---- report ----
    check("report punya cue_count", report.get("cue_count") == 20, str(report.get("cue_count")))
    check("report punya diff_pct", isinstance(report.get("diff_pct"), (int, float)))
    check("report punya warnings list", isinstance(report.get("warnings"), list))

    # ---- retry: hint revisi diteruskan ke Gemini ----
    script_calls = [c for c in stub.calls if c[0] == "script"]
    check("tahap 2 dipanggil minimal 1x", len(script_calls) >= 1, f"{len(script_calls)}x")
    if report.get("diff_pct", 0) > 15:
        check("hint revisi terkirim saat durasi meleset",
              any(c[1] for c in script_calls), "ada hint")
    check("tahap 1 dipanggil", any(c[0] == "analysis" for c in stub.calls))

    # ---- job async flow (tidak ada timeout) ----
    stub.calls.clear()
    r = client.post("/api/jobs",
                    files={"subtitle": ("sakamoto.srt", SRT_LONG.encode("utf-8"), "text/plain")},
                    data={"target_duration": 2})
    ok_job = r.status_code == 200 and "job_id" in r.json()
    check("POST /api/jobs -> 200 + job_id", ok_job, str(r.status_code))

    if ok_job:
        job_id = r.json()["job_id"]
        import time as _time
        deadline = _time.time() + 25
        status = {}
        while _time.time() < deadline:
            status = client.get(f"/api/jobs/{job_id}").json()
            if status.get("status") in ("done", "error"):
                break
            _time.sleep(0.4)

        check("job selesai jadi done", status.get("status") == "done",
              f"{status.get('status')} - {status.get('message')}")
        check("job punya project_id", bool(status.get("project_id")), str(status.get("project_id")))
        check("job punya elapsed_sec", isinstance(status.get("elapsed_sec"), (int, float)))
        check("job punya progress 100", status.get("progress") == 100, str(status.get("progress")))
        check("job punya message", bool(status.get("message")))

        if status.get("project_id"):
            pid2 = status["project_id"]
            r2 = client.get(f"/api/projects/{pid2}/storyboard.json")
            check("storyboard.json hasil job bisa diunduh", r2.status_code == 200, str(r2.status_code))
            if r2.status_code == 200:
                sb2 = r2.json()
                clips2 = [c for s in sb2["sections"] for c in s["clips"]]
                check("storyboard job punya report", "report" in sb2)
                check("clip hasil job tetap valid (src<=3)",
                      all(0 < c["src"] <= 3.0 for c in clips2), f"max={max((c['src'] for c in clips2), default=0)}")

    check("GET job tidak dikenal -> 404",
          client.get("/api/jobs/tidak-ada").status_code == 404)

    # ---- blocking endpoint tetap jalan ----
    r = client.post("/api/generate",
                    files={"subtitle": ("sakamoto.srt", SRT_LONG.encode("utf-8"), "text/plain")},
                    data={"target_duration": 2})
    check("POST /api/generate (blocking) tetap jalan", r.status_code == 200, str(r.status_code))

    shutil.rmtree(tmp, ignore_errors=True)
    print("-" * 55)
    if fails:
        print(f"FAIL ({len(fails)}): {fails}")
        return 1
    print("ALL PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())