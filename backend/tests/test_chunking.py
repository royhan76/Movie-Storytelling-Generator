"""Test chunking subtitle panjang (PLAN1.md §25).

Subtitle > chunk_size harus dipecah, dianalisis per chunk, lalu digabung
menjadi satu story map konsisten.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.providers import gemini as _g
from app.providers.gemini import (
    DAILY_QUOTA_FLOOR_SEC, GeminiProvider, _backoff_seconds, _daily_quota_retry_delay,
    _dedupe_events, _extract_json, _is_rate_limit,
)
from app.services.srt_parser import SrtParser

fails = []


def check(name, ok, extra=""):
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' -> ' + extra) if extra else ''}")
    if not ok:
        fails.append(name)


# --- deteksi rate limit ---
class Fake429(Exception):
    pass


check("429 RESOURCE_EXHAUSTED terdeteksi",
      _is_rate_limit(Fake429("429 RESOURCE_EXHAUSTED: quota exceeded")))
check("429 Too Many Requests terdeteksi",
      _is_rate_limit(Fake429("Too Many Requests")))
check("rate limit lowercase terdeteksi",
      _is_rate_limit(Fake429("Rate limit exceeded for model")))
check("error lain TIDAK dianggap rate limit",
      not _is_rate_limit(Fake429("Invalid JSON structure")))
check("ValueError biasa bukan rate limit",
      not _is_rate_limit(ValueError("Parse JSON gagal")))

# --- backoff memanjang ---
bo = [_backoff_seconds(i) for i in (1, 2, 3, 4)]
check("backoff memanjang", bo == sorted(bo) and bo[0] >= 15, str(bo))
check("backoff dibatasi 90s", max(bo) <= 90, str(bo))

# --- retry tidak diam-diam wedged: 429 lalu sukses harus tetap jalan ---
prov = GeminiProvider.__new__(GeminiProvider)
prov.api_key = "x"
prov.model = "test-model"
prov.client = object()

calls = {"n": 0}


class FlakyClient:
    class models:  # noqa: N801
        @staticmethod
        def generate_content(**_kwargs):
            calls["n"] += 1
            if calls["n"] <= 2:
                raise Fake429("429 RESOURCE_EXHAUSTED")
            class R:
                text = '{"ok": true}'
            return R()


prov.client = FlakyClient()
import app.providers.gemini as _g
_g._sleep = lambda _s: None  # jangan benar-benar menunggu di test
_g._backoff_seconds = lambda _h: 0

prov.models = ["model-a"]
prov.model = "model-a"
res = prov._call("prompt", json_mode=False)
check("retry 429 lalu sukses -> hasil dikembalikan", res == {"ok": True}, str(res))
check("retry butuh beberapa percobaan", calls["n"] == 3, f"{calls['n']}x")

# --- retryDelay: bedakan rate limit per menit vs kuota harian ---
daily = Fake429("{'error': {'code': 429, 'message': 'Quota exceeded', "
                "'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
                "'retryDelay': '28370s'}]}}")
minute = Fake429("{'error': {'code': 429, 'details': ["
                 "{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
                 "'retryDelay': '20s'}]}}")
check("retryDelay 28370s terbaca", _daily_quota_retry_delay(daily) == 28370.0,
      str(_daily_quota_retry_delay(daily)))
check("retryDelay 20s terbaca", _daily_quota_retry_delay(minute) == 20.0,
      str(_daily_quota_retry_delay(minute)))
check("retryDelay 28370s >= floor kuota harian",
      _daily_quota_retry_delay(daily) >= DAILY_QUOTA_FLOOR_SEC)
check("retryDelay 20s < floor kuota harian",
      _daily_quota_retry_delay(minute) < DAILY_QUOTA_FLOOR_SEC)
check("tanpa retryDelay -> None", _daily_quota_retry_delay(Fake429("Invalid JSON")) is None)

# --- fallback antar model saat kuota harian habis ---
prov2 = GeminiProvider.__new__(GeminiProvider)
prov2.api_key = "x"
prov2.model = "model-a"
prov2.models = ["model-a", "model-b", "model-c"]

used = []


class QuotaExhaustedClient:
    class models:  # noqa: N801
        @staticmethod
        def generate_content(model=None, **_kwargs):
            used.append(model)
            if model in ("model-a", "model-b"):
                raise Fake429(
                    "{'error': {'code': 429, 'message': 'Quota exceeded', "
                    "'details': [{'@type': 'type.googleapis.com/google.rpc.RetryInfo', "
                    "'retryDelay': '28370s'}]}}"
                )
            class R:
                text = '{"model": "' + model + '"}'
            return R()


prov2.client = QuotaExhaustedClient()
_g._sleep = lambda _s: None

r2 = prov2._call("prompt", json_mode=False)
check("fallback: pindah model saat kuota harian habis", r2.get("model") == "model-c", str(r2))
check("fallback: model ke-3 dicoba", "model-c" in used, str(used))
check("fallback: model yap yang gagal tidak diulang", used.count("model-a") == 1, str(used))
check("fallback: provider.models aktif pindah", prov2.model == "model-c", prov2.model)

# --- semua model habis -> pesan error jelas, bukan error mentah ---
prov3 = GeminiProvider.__new__(GeminiProvider)
prov3.api_key = "x"
prov3.model = "model-a"
prov3.models = ["model-a", "model-b"]
tried = []


class AllQuotaGone:
    class models:  # noqa: N801
        @staticmethod
        def generate_content(model=None, **_kwargs):
            tried.append(model)
            raise Fake429(
                "'code': 429, 'message': 'Quota exceeded', "
                "'details': [{'retryDelay': '28370s'}]"
            )


prov3.client = AllQuotaGone()
_g._sleep = lambda _s: None
try:
    prov3._call("prompt", json_mode=False)
    check("semua model habis -> RuntimeError", False, "tidak raise")
except RuntimeError as e:
    msg = str(e)
    check("semua model habis -> RuntimeError", True)
    check("pesan menyebut kuota harian", "kuota" in msg.lower() and "harian" in msg.lower(), msg[:80])
    check("pesan menyebut reset tengah malam", "tengah malam" in msg.lower())
    check("tidak ada teks exception mentah yang di-dump", "Fake429" not in msg, msg[:60])
check("semua model dicoba sekali masing-masing", sorted(tried) == ["model-a", "model-b"], str(tried))


# --- _extract_json: tolerate fence + teks tambahan ---
check("JSON polos", _extract_json('{"a": 1}') == {"a": 1})
check("JSON dalam fence", _extract_json('```json\n{"a": 2}\n```') == {"a": 2})
check("JSON dalam fence tanpa label", _extract_json('```\n{"a": 3}\n```') == {"a": 3})
check("JSON dengan teks sebelum/sesudah",
      _extract_json('Ini hasilnya:\n{"a": 4}\nSemoga membantu.') == {"a": 4})
try:
    _extract_json("bukan json sama sekali")
    check("JSON invalid -> exception", False)
except ValueError:
    check("JSON invalid -> exception", True)

# --- _dedupe_events ---
evs = [
    {"event": "Jatuh cinta", "start": "00:00:04"},
    {"event": "jatuh cinta", "start": "00:00:04"},
    {"event": "Lain", "start": "00:00:09"},
    "bukan dict",
]
check("dedupe event", len(_dedupe_events(evs)) == 2, str(len(_dedupe_events(evs))))

# --- chunking: subtitle panjang dipecah sesuai size ---
parser = SrtParser()
big_srt = "\n\n".join(
    f"{i}\n00:{i // 60:02d}:{i % 60:02d},000 --> 00:{i // 60:02d}:{i % 60 + 1:02d},000\nBaris nomor {i}."
    for i in range(1, 501)
)
entries = parser.parse(big_srt)
check("parse 500 cue", len(entries) == 500, str(len(entries)))

timeline = parser.build_timeline(entries)
chunk_size = 350
chunks = [timeline[i:i + chunk_size] for i in range(0, len(timeline), chunk_size)]
check("500 cue -> 2 chunk", len(chunks) == 2, str(len(chunks)))
check("chunk 1 = 350 cue", len(chunks[0]) == 350, str(len(chunks[0])))
check("chunk 2 = 150 cue", len(chunks[1]) == 150, str(len(chunks[1])))
check("semua cue terpakai", sum(len(c) for c in chunks) == 500)
check("tidak ada cue hilang", [c["i"] for c in chunks[0]][:2] == [1, 2]
      and chunks[1][0]["i"] == 351, str(chunks[1][0]["i"]))

# --- provider: single chunk vs multi chunk (stub _call) ---
prov = GeminiProvider.__new__(GeminiProvider)
seen = []

def fake_call(prompt, temperature=0.3, json_mode=True, label="call"):
    seen.append(prompt)
    return {
        "title": "T", "characters": [], "setting": "", "main_conflict": "",
        "story_arc": {"opening": "o", "rising_action": "r", "midpoint": "m",
                      "climax": "c", "ending": "e"},
        "important_events": [{"event": f"E{i}", "start": f"00:00:{i:02d}", "end": "00:00:00"} for i in range(5)],
    }

prov._call = fake_call
prov.generate_story_analysis(timeline[:10], chunk_size=350)
check("subtitle kecil -> 1 panggilan", len(seen) == 1, str(len(seen)))
check("prompt memuat subtitle", "Baris nomor 1." in seen[0])

seen.clear()
prov.generate_story_analysis(timeline, chunk_size=350)
check("subtitle besar -> 2 panggilan parse + 1 merge", len(seen) == 3, str(len(seen)))
check("tiap chunk dilabeli", any("Bagian 1 dari 2" in p for p in seen[:2]),
      str([("Bagian 1 dari 2" in p) for p in seen]))
check("merge menutup 2 analisis", seen[2].count('"title"') >= 2, str(seen[2].count('"title"')))

print("-" * 55)
if fails:
    print(f"FAIL ({len(fails)}): {fails}")
    raise SystemExit(1)
print("ALL PASS")