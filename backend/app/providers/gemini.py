"""Client Gemini untuk 2 tahap: Story Analysis lalu Storytelling + Storyboard.

Menangani:
- chunking subtitle panjang (stage analysis per chunk, lalu digabung)
-Temperature & response schema JSON
- structured retry kalau JSON invalid
- revisi storyboard kalau durasi visual tidak nyambung dengan voice-over
"""
import json
import os
import re
import time
from typing import Any, Dict, List, Optional

from dotenv import load_dotenv

from app.debug_log import log_call

load_dotenv()

try:
    from google import genai
    from google.genai import types

    _HAS_GENAI = True
except ImportError:  # pragma: no cover
    _HAS_GENAI = False


# Marker harus spesifik. Angka "429" telanjang terlalu longgar: nomor apa pun
# di dalam pesan lain (mis. "429 frames", "line 429") akan salah dikira rate limit.
RATE_LIMIT_MARKERS = (
    "resource_exhausted",
    "resource exhausted",
    "too many requests",
    "rate limit",
    "rate_limit",
    "quota exceeded",
    "quota_exceeded",
    "status=429",
    "code=429",
)

# RetryInfo.retryDelay di balasan 429. Kalau > 60 detik, ini batas DAILY
# (Google free tier: 20 request/hari per model), bukan rate limit per menit.
# Menunggu 7 jam tidak masuk akal di dalam request HTTP.
DAILY_QUOTA_FLOOR_SEC = 60.0


def _is_rate_limit(exc: Exception) -> bool:
    """Deteksi 429 / resource exhausted dari exception google-genai."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


def _daily_quota_retry_delay(exc: Exception) -> Optional[float]:
    """Ambil retryDelay (detik) dari balasan 429, atau None kalau bukan itu.

    RetryInfo.retryDelay="28370s" (~7.9 jam) = kuota harian habis.
    RetryInfo.retryDelay="20s" = rate limit per menit, masih layak tunggu.
    """
    text = str(exc)
    match = re.search(r"retryDelay['\"]?\s*[:=]\s*['\"]?(\d+(?:\.\d+)?)s", text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _backoff_seconds(hit: int) -> float:
    """Jeda memanjang: 20s, 45s, 90s. Free tier pulih dalam hitungan menit."""
    return {1: 20, 2: 45, 3: 90}.get(hit, 90)


def _sleep(seconds: float) -> None:
    import time as _t

    _t.sleep(seconds)


def _hint_tag(hint: str) -> str:
    """Label singkat untuk file log hint: 'too-short' atau 'too-long'."""
    return "too-short" if "TERLALU PENDEK" in hint else "too-long"


def _seconds_between(lo: str, hi: str) -> float:
    """Selisih detik antara dua timestamp 'HH:MM:SS'. 0 kalau tidak valid."""

    def to_s(ts: str) -> float:
        parts = str(ts or "").split(":")
        if len(parts) != 3:
            return 0.0
        try:
            return int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            return 0.0

    return max(0.0, to_s(hi) - to_s(lo))


def _slice_timeline(
    timeline: List[Dict[str, Any]], lo: str, span_seconds: float
) -> List[Dict[str, Any]]:
    """Ambil cue-cue yang jatuh di dalam [lo, lo + span].

    Dipakai supaya tiap prompt bagian hanya melihat subtitle di rentang waktu
    bagian itu -- bukan seluruh film. Prompt jadi jauh lebih kecil dan Gemini
    lebih fokus pada isi yang benar.
    """
    lo_s = _seconds_between("00:00:00", lo)
    hi_s = lo_s + max(60.0, float(span_seconds))
    out = []
    for cue in timeline:
        start = cue.get("start")
        if not start:
            continue
        parts = str(start).split(":")
        if len(parts) != 3:
            continue
        try:
            s = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
        except ValueError:
            continue
        if lo_s <= s <= hi_s:
            out.append(cue)
    return out


def _extract_json(text: str) -> Dict[str, Any]:
    """Ambil objek JSON dari balasan model yang kadang dibungkus markdown fence."""
    if not text:
        raise ValueError("Balasan model kosong")
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    # fallback: ambil substring dari '{' pertama sampai '}' terakhir
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        return json.loads(cleaned[start : end + 1])
    raise ValueError("Parse JSON gagal")


class GeminiProvider:
    # Jumlah bagian naskah. Dipakai untuk memberi tahu Gemini berapa kata per
    # bagian, supaya total kata sesuai target (bukan tiap bagian ramping).
    SCRIPT_MIN_SECTIONS = 5
    SCRIPT_MAX_SECTIONS = 8

    # Free tier Google: kuota harian 20 request PER MODEL. Kalau satu model
    # habis, model lain punya kuota terpisah — jadi gagal daily di model
    # pertama bukan akhir dunia, asal ada model cadangan.
    DEFAULT_FALLBACKS = (
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-2.0-flash",
        "gemini-1.5-flash",
        "gemini-1.5-flash-8b",
        "gemini-3.6-flash",
        "gemini-3.5-flash",
        "gemini-3.1-flash-lite",
    )

    def __init__(self) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        primary = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
        fallbacks = [
            m.strip()
            for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",")
            if m.strip()
        ]
        if not fallbacks:
            fallbacks = [m for m in self.DEFAULT_FALLBACKS if m != primary]

        # primary dulu, lalu fallback. Buang duplikat tapi JAGA urutannya.
        models: List[str] = [primary]
        models.extend(m for m in fallbacks if m not in models)

        self.model = primary
        self.models = models
        self.client = None
        if _HAS_GENAI and self.api_key and not self.api_key.startswith("YOUR_"):
            self.client = genai.Client(api_key=self.api_key)

    def is_configured(self) -> bool:
        return self.client is not None

    # ------------------------------------------------------------------ #
    # plumbing
    # ------------------------------------------------------------------ #
    def _call(
        self,
        prompt: str,
        temperature: float = 0.3,
        json_mode: bool = True,
        label: str = "call",
    ) -> Dict[str, Any]:
        """Panggil Gemini, pindah model kalau kuota model pertama habis.

        Dua jenis 429 yang harus dibedakan:
        - retryDelay kecil (< 60s) = rate limit per menit -> tunggu di model sama.
        - retryDelay besar (> 60s) = kuota DAILY model itu habis -> pindah model.
          Menunggu 7 jam di dalam satu request HTTP tidak masuk akal, dan kuota
          harian tiap model terpisah, jadi pindah model adalah jalan keluar.

        `label` hanya untuk debug log (nama file jejak), tidak mengubah perilaku.
        """
        if not self.is_configured():
            raise RuntimeError("Gemini API Key belum dikonfigurasi.")

        config_kwargs: Dict[str, Any] = {"temperature": temperature}
        if json_mode:
            config_kwargs["response_mime_type"] = "application/json"

        last_error: Optional[Exception] = None
        daily_blocked: List[str] = []
        minute_hits = 0

        for model in self.models:
            if model in daily_blocked:
                continue

            for attempt in range(4):
                p = prompt
                if attempt > 0 and minute_hits == 0:
                    p = (
                        "PENTING: jawaban HARUS berupa JSON valid tanpa teks lain, "
                        "tanpa markdown fence, tanpa penjelasan.\n\n" + prompt
                    )
                call_started = time.time()
                resp = None  # di-set di try; dipakai di blok error untuk log
                try:
                    resp = self.client.models.generate_content(
                        model=model, contents=p, config=types.GenerateContentConfig(**config_kwargs)
                    )
                    parsed = _extract_json(resp.text)
                    log_call(
                        f"{label}-{model}",
                        parsed,
                        duration_sec=round(time.time() - call_started, 2),
                    )
                    if model != self.model:
                        # model utama habis kuota; pindah permanen untuk request ini.
                        self.model = model
                    return parsed
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    log_call(
                        f"{label}-{model}-ERROR",
                        {
                            "prompt_head": p[:1500],
                            "raw_response": (resp.text if resp is not None else None),
                        },
                        duration_sec=round(time.time() - call_started, 2),
                        error=exc,
                    )
                    if not _is_rate_limit(exc):
                        _sleep(min(2 ** attempt, 8))
                        continue

                    delay = _daily_quota_retry_delay(exc)
                    if delay is not None and delay >= DAILY_QUOTA_FLOOR_SEC:
                        # kuota harian model ini habis -> jangan coba lagi di model ini
                        daily_blocked.append(model)
                        break

                    minute_hits += 1
                    if minute_hits > 3:
                        break
                    _sleep(_backoff_seconds(minute_hits))

        quota_msg = (
            "Kuota Gemini harian habis untuk semua model yang dikonfigurasi. "
            "Kuota free tier 20 request per model per hari dan direset sekitar tengah malam. "
            "Coba lagi besok, atau set GEMINI_FALLBACK_MODELS untuk menambah model cadangan."
            if daily_blocked
            else "Gemini sedang membatasi request (rate limit per menit). Tunggu 1-2 menit lalu coba lagi."
        )
        raise RuntimeError(f"{quota_msg} ({last_error})")

    # ------------------------------------------------------------------ #
    # Tahap 1 — story analysis
    # ------------------------------------------------------------------ #
    ANALYZE_PROMPT = """Kamu adalah analis cerita film.
Input utama adalah subtitle film beserta timestamp.

Tugas:
1. Pahami keseluruhan alur cerita dari subtitle.
2. Identifikasi karakter utama dan perannya.
3. Identifikasi konflik utama.
4. TentukanAwakening arc: opening, rising_action, midpoint, climax, ending.
5. List kejadian penting dengan timestamp dari timeline subtitle.
6. Jangan mengarang kejadian yang tidak didukung subtitle.
7. Gunakan Bahasa Indonesia.

Output JSON valid dengan schema persis:
{
  "title": "judul film dalam Bahasa Indonesia",
  "characters": [{"name": "", "role": ""}],
  "setting": "",
  "main_conflict": "",
  "story_arc": {
    "opening": "",
    "rising_action": "",
    "midpoint": "",
    "climax": "",
    "ending": ""
  },
  "important_events": [
    {"event": "", "start": "HH:MM:SS", "end": "HH:MM:SS", "subtitle_ref": 0}
  ]
}

subtitle_ref = nomor baris subtitle yang mendukung kejadian itu.
Pastikan SEMUA timestamp yang kamu tulis benar-benar ada di timeline subtitle."""

    def generate_story_analysis(
        self, timeline: List[Dict[str, Any]], chunk_size: int = 350
    ) -> Dict[str, Any]:
        """Analisis seluruh subtitle. Subtitle panjang dipecah per chunk lalu digabung."""
        if not timeline:
            raise ValueError("Subtitle kosong")

        chunks = [timeline[i : i + chunk_size] for i in range(0, len(timeline), chunk_size)]
        if len(chunks) == 1:
            return self._call(self._render_prompt(chunks[0]), temperature=0.3, label="analysis")

        partials: List[Dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            partials.append(
                self._call(
                    self._render_prompt(chunk, chunk_label=f"Bagian {idx + 1} dari {len(chunks)}"),
                    temperature=0.3,
                    label=f"analysis-chunk{idx + 1}",
                )
            )

        return self._merge_partials(partials)

    def _render_prompt(self, chunk: List[Dict[str, Any]], chunk_label: str = "") -> str:
        lines = []
        for cue in chunk:
            lines.append(f'[{cue["i"]}] {cue["start"]} {cue["text"]}')
        header = f"({chunk_label})\n\n" if chunk_label else ""
        return f"{header}SUBTITLE:\n" + "\n".join(lines) + "\n\n" + self.ANALYZE_PROMPT

    MERGE_PROMPT = """Berikut analisis parsial dari beberapa bagian subtitle film yang sama.
Gabungkan menjadi SATU analisis cerita yang konsisten dan lengkap, tanpa duplikasi.

{partials}

PENTING:
-(events) yang duplikat harus digabung.
- story_arc harus mengikuti alur film SEBENARNYA.
- Gunakan Bahasa Indonesia.
- Output JSON valid dengan schema yang sama seperti input.
"""

    def _merge_partials(self, partials: List[Dict[str, Any]]) -> Dict[str, Any]:
        compact = json.dumps(partials, ensure_ascii=False)
        merged = self._call(
            self.MERGE_PROMPT.format(partials=compact), temperature=0.25, label="analysis-merge"
        )
        merged["important_events"] = _dedupe_events(merged.get("important_events", []))
        return merged

    # ------------------------------------------------------------------ #
    # Tahap 2 — storytelling + storyboard
    # ------------------------------------------------------------------ #
    SCRIPT_RULES = """Kamu adalah penulis storytelling film dan storyboard planner.

ATURAN UTAMA:
1. Pahami keseluruhan alur cerita dari story analysis.
2. Fokus kejadian penting, bukan membaca subtitle.
3. JANGAN ubah kronologi cerita.
4. JANGAN mengarang kejadian yang tidak didukung subtitle.
5. Gunakan Bahasa Indonesia.
6. Gaya cepat, dramatis, emosional, tegang — cocok untuk video recap/alur cerita.
7. Jangan terlalu banyak dialog langsung; tulis sebagai storyteller.
8. Voice-over harus padat dan sesuai target durasi.

ATURAN CLIP BLUEPRINT:
1. Setiap clip punya: beat, start, src, trx, out.
2. beat = lowercase-kebab-case (contoh: walk, run, confront, silent-stare, weapon-aim, embrace, child, photo-memory).
3. start HARUS diambil dari timestamp subtitle (HH:MM:SS). Ini kandidat lokasi klip, bukan klaim pasti.
4. src = durasi sumber, WAJIB 0 < src <= 3.0. JANGAN pernah lebih dari 3 detik.
5. trx hanya boleh: baref, fz12, s65, s50, s35.
6. out = durasi hasil SETELAH transform:
   - baref -> out = src
   - fz12  -> out = 1.2 (freeze frame)
   - s65   -> out = src / 0.65
   - s50   -> out = src / 0.50
   - s35   -> out = src / 0.35
7. Pilih momen yang mendukung kalimat voice-over.
8. Hindari pengulangan timestamp.
9. Visual mengikuti narasi, bukan daftar klip acak.
10. Jumlah klip mengikuti kepadatan cerita (bagian aksi lebih banyak, bagian tenang lebih sedikit).

ATURAN OUTPUT:
- Output HARUS JSON valid, tanpa teks lain.
- Ikuti schema persis di bawah."""

    def generate_storytelling(
        self,
        analysis: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        target_minutes: int,
        wpm: int = 150,
        revision_hint: str = "",
    ) -> Dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("Gemini API Key belum dikonfigurasi.")

        target_words = int(target_minutes * wpm)
        target_seconds = target_minutes * 60

        timeline_ref = "\n".join(f'[{c["i"]}] {c["start"]} {c["text"]}' for c in timeline)

        schema = {
            "project": {
                "title": analysis.get("title", ""),
                "source_subtitle": "",
                "target_duration_minutes": target_minutes,
                "estimated_voiceover_seconds": target_seconds,
                "estimated_word_count": target_words,
            },
            "sections": [
                {
                    "section_id": 1,
                    "visual": "deskripsi visual bagian ini",
                    "voice_over": "naskah narasi bagian ini",
                    "clips": [
                        {
                            "clip_id": 1,
                            "beat": "walk",
                            "start": "00:00:40",
                            "src": 2.0,
                            "trx": "baref",
                            "out": 2.0,
                            "subtitle_ref": 1,
                            "visual_hint": "apa yang kemungkinan terlihat di titik ini",
                        }
                    ],
                }
            ],
            "summary": {"total_sections": 0, "total_clips": 0, "total_clip_duration": 0.0},
        }

        revision_block = ""
        if revision_hint:
            revision_block = (
                "\n\nREVISI YANG DIMINTA (wajib diperbaiki pada hasil kali ini):\n"
                + revision_hint
                + "\n"
            )
            # Prompt penuh bisa ribuan karakter; log_call cuma menyimpan 1500
            # karakter pertama, jadi hint revisi ini tidak akan terlihat di log.
            # Catat terpisah supaya bisa dibaca pas debugging.
            log_call(f"revision-hint-{_hint_tag(revision_hint)}", {"hint": revision_hint})

        per_section_words = max(60, target_words // max(1, self.SCRIPT_MIN_SECTIONS))

        prompt = f"""{self.SCRIPT_RULES}

TARGET DURASI (WAJIB DIPENUHI):
- target durasi narasi: {target_minutes} menit = {target_seconds} detik
- total kata voice-over: {target_words} kata (+-15%)
- jumlah bagian: sekitar {self.SCRIPT_MIN_SECTIONS}-{self.SCRIPT_MAX_SECTIONS}
- kata voice-over per bagian: sekitar {per_section_words} kata

PENTING TENTANG JUMLAH KATA:
- Ini bukan ringkasan. Voice-over harus PENUH dan MENGISI {target_seconds} detik.
- Kalau total naskahnya cuma {target_words // 3} kata, itu GAGAL - masih terlalu pendek.
- Tulis narasi lengkap: setup, konflikasi, klimaks, resolusi.
- Yang ringkas adalah KALIMAT per bagian, bukan TOTAL kata seluruh naskah.

TOTAL DURASI VISUAL:
- jumlahkan seluruh `out` dari semua clip di semua bagian
- hasilnya harus mendekati {target_seconds} detik (+-15%)
- clip per bagian: sekitar {max(3, target_words // 250)} klip
- jika terlalu pendek: tambah jumlah clip DAN panjangkan narasi
- jika terlalu panjang: kurangi jumlah clip DAN pendekkan narasi
- jangan memaksakan dengan menghancurkan alur cerita

STORY ANALYSIS:
{json.dumps(analysis, ensure_ascii=False, indent=2)}

TIMELINE SUBTITLE (timestamp yang boleh dipakai untuk `start`):
{timeline_ref}
{revision_block}
SCHEMA OUTPUT:
{json.dumps(schema, ensure_ascii=False, indent=2)}
"""
        return self._call(prompt, temperature=0.45, label="storyboard")

    # ------------------------------------------------------------------ #
    # Tahap 2b — menulis naskah per bagian (chunking)
    # ------------------------------------------------------------------ #
    # Pengamatan dari run nyata: satu panggilan Gemini mentok di ~900 kata.
    # Target 15 menit butuh 2250 kata. Jadi naskah harus ditulis per bagian,
    # lalu disambung -- bukan satu prompt raksasa yang hasilnya dipotong diam-diam.
    SECTION_PROMPT = """Kamu adalah penulis storytelling film. Tulis SATU BAGIAN dari naskah.

{SCRIPT_RULES}

BAGIAN INI:
- bagian {part} dari {total} dalam naskah penuh.
- Fokus: {focus}
- Nada bagian sebelumnya: {prev_hint}
- Nada bagian berikutnya: {next_hint}

ATURAN KUANTITATIF (WAJIB):
- Voice-over bagian ini: {words} kata (toleransi +-{word_tol}%)
- BATAS KARAKTER: Voice-over bagian ini MAKSIMAL 3000 karakter (termasuk spasi). JANGAN MELEBIHI 3000 KARAKTER.
- Durasi baca bagian ini: {seconds} detik @ {wpm} kata/menit
- JUMLAH KATA ADALAH TARGET TEPAT, bukan saran. Tulis sampai mendekati angka itu.
{parts_hint}

TOTAL DURASI VISUAL BAGIAN INI:
- Jumlahkan seluruh `out` dari semua clip di bagian ini.
- Hasilnya HARUS mendekati {seconds} detik (+-15%).
- Pastikan menghasilkan CUKUP KLIP (bisa {target_clips} klip atau lebih) agar total durasinya menyamai voice-over.
- Jangan hanya menghasilkan sedikit klip lalu berhenti!

JANGAN mengulang isi bagian lain. Fokus hanya event di bagian ini.

EVENT YANG MENDUKUNG BAGIAN INI (gunakan HANYA event ini):
{events}

TIMELINE SUBTITLE (timestamp yang boleh dipakai untuk `start`):
{timeline_ref}

SCHEMA OUTPUT:
{schema}"""

    def generate_section(
        self,
        analysis: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        *,
        part: int,
        total_parts: int,
        words: int,
        wpm: int = 150,
        focus: str = "",
        prev_hint: str = "",
        next_hint: str = "",
        events: Optional[List[Dict[str, Any]]] = None,
        part_events: Optional[List[Dict[str, Any]]] = None,
        revision_hint: str = "",
    ) -> Dict[str, Any]:
        """Tulis satu bagian naskah dengan jatah kata spesifik.

        Ini yang menutup celah "~900 kata per panggilan": tiap bagian punya
        target kata sendiri, jadi total naskah bisa jauh melebihi batas itu.
        """
        if not self.is_configured():
            raise RuntimeError("Gemini API Key belum dikonfigurasi.")

        words = max(120, int(words))
        seconds = int(words / wpm * 60) if wpm else 0
        word_tol = 20

        use_events = part_events if part_events is not None else (events or [])
        events_ref = "\n".join(
            f'- {e.get("start", "?")} : {str(e.get("event", ""))[:180]}' for e in use_events
        ) or "(tidak ada event khusus — pakai timeline)"

        # Timeline dipangkas ke rentang bagian ini supaya prompt tidak blunder.
        if use_events:
            starts = [str(e.get("start", "")) for e in use_events if e.get("start")]
            if starts:
                lo, hi = min(starts), max(starts)
                span = max(0.0, _seconds_between(lo, hi) + 240.0)
                timeline = _slice_timeline(timeline, lo, span)

        timeline_ref = "\n".join(f'[{c["i"]}] {c["start"]} {c["text"]}' for c in timeline[:400])

        parts_hint = (
            "ELABORASI CERITA: Jangan hanya meringkas! Tulis variasi narasi yang kaya: deskripsi situasi, emosi tokoh, "
            "dan konsekuensi tiap kejadian. Panjangkan naskah dengan detail mendalam."
            if total_parts > 1
            else "Tulis narasi lengkap dari awal sampai akhir cerita dengan deskripsi yang mendalam."
        )

        schema = json.dumps(
            {
                "section": {
                    "section_id": part,
                    "visual": "deskripsi visual bagian ini",
                    "voice_over": f"naskah narasi bagian ini, sekitar {words} kata",
                    "clips": [
                        {
                            "clip_id": 1,
                            "beat": "walk",
                            "start": "00:00:40",
                            "src": 2.0,
                            "trx": "baref",
                            "out": 2.0,
                            "subtitle_ref": 1,
                            "visual_hint": "apa yang terlihat di titik ini",
                        }
                    ],
                }
            },
            ensure_ascii=False,
            indent=2,
        )

        revision_block = ""
        if revision_hint:
            revision_block = (
                "\nREVISI (wajib diperbaiki):\n" + revision_hint + "\n"
            )

        prompt = self.SECTION_PROMPT.format(
            SCRIPT_RULES=self.SCRIPT_RULES,
            part=part,
            total=total_parts,
            focus=focus or f"bagian ke-{part} dari cerita",
            prev_hint=prev_hint or "(ini bagian pertama)",
            next_hint=next_hint or "(ini bagian terakhir)",
            words=words,
            word_tol=word_tol,
            seconds=seconds,
            wpm=wpm,
            target_clips=max(5, seconds // 2),
            parts_hint=parts_hint,
            events=events_ref,
            timeline_ref=timeline_ref,
            schema=schema,
        ) + revision_block
        return self._call(prompt, temperature=0.5, label=f"section-part{part}")

    def revise_storyboard(
        self,
        current: Dict[str, Any],
        analysis: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        target_minutes: int,
        wpm: int,
        report: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Minta Gemini revisi storyboard berdasarkan laporan durasi."""
        est_vo = report.get("est_vo_sec", 0)
        total_out = report.get("total_clip_out", 0)
        direction = (
            "tambah jumlah klip atau pakai slow motion agar total durasi visual mendekati voice-over"
            if total_out < est_vo
            else "kurangi jumlah klip atau pakai baref agar total durasi visual mendekati voice-over"
        )
        hint = (
            f"Total durasi visual saat ini {total_out:.1f}s, "
            f"estimasi voice-over {est_vo:.1f}s (selisih {report.get('diff_pct', 0)}%). "
            f"{direction}."
        )
        return self.generate_storytelling(
            analysis, timeline, target_minutes, wpm=wpm, revision_hint=hint
        )


def _dedupe_events(events: List[Any]) -> List[Dict[str, Any]]:
    seen = set()
    out = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        key = (ev.get("start", ""), (ev.get("event", "") or "").strip().lower()[:40])
        if key in seen:
            continue
        seen.add(key)
        out.append(ev)
    return out