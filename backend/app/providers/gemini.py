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
    "status=503",
    "code=503",
    "unavailable",
    "high demand",
)

# RetryInfo.retryDelay di balasan 429. Kalau > 60 detik, ini batas DAILY
# (Google free tier: 20 request/hari per model), bukan rate limit per menit.
# Menunggu 7 jam tidak masuk akal di dalam request HTTP.
DAILY_QUOTA_FLOOR_SEC = 60.0

# Error-error ini adalah masalah konfigurasi/model, bukan rate limit. Jangan
# di-retry karena hanya membakar request dan sering membuat error akhirnya
# salah dilaporkan sebagai kuota habis.
MODEL_CONFIG_ERROR_MARKERS = (
    "not found",
    "not_found",
    "unsupported for generatecontent",
    "unsupported for generate content",
    "invalid_argument",
    "invalid argument",
    "permission_denied",
    "permission denied",
    "unauthenticated",
    "api key not valid",
    "status=401",
    "status=403",
    "status=404",
    "code=401",
    "code=403",
    "code=404",
)


def _is_rate_limit(exc: Exception) -> bool:
    """Deteksi 429 / resource exhausted dari exception google-genai."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in RATE_LIMIT_MARKERS)


def _is_model_config_error(exc: Exception) -> bool:
    """Deteksi model/API yang tidak tersedia atau tidak diizinkan."""
    text = f"{type(exc).__name__}: {exc}".lower()
    return any(marker in text for marker in MODEL_CONFIG_ERROR_MARKERS)


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
        "gemini-3.8-flash",
        "gemini-3.6-flash",
        "gemini-3.5-flash-lite",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
    )
    # Dipakai untuk membuang fallback lama dari .env, misalnya gemini-1.5-pro.
    # Daftar ini sengaja mengikuti model yang ditawarkan UI saat ini.
    SUPPORTED_MODELS = frozenset(DEFAULT_FALLBACKS)

    def __init__(self, model_override: str | None = None) -> None:
        self.api_key = os.getenv("GEMINI_API_KEY", "").strip()
        primary = os.getenv("GEMINI_MODEL", "gemini-3.6-flash").strip() or "gemini-3.6-flash"
        if model_override and model_override.strip():
            primary = model_override.strip()
        # Konfigurasi lama tidak boleh membuat request ke model yang sudah
        # retired. Jika model dari .env lama, pakai model aktif pertama.
        if primary not in self.SUPPORTED_MODELS:
            primary = self.DEFAULT_FALLBACKS[0]
        fallbacks = [
            m.strip()
            for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",")
            if m.strip() and m.strip() in self.SUPPORTED_MODELS
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
        config_errors: Dict[str, Exception] = {}
        other_errors: List[Exception] = []
        minute_hits = 0
        try:
            max_attempts = max(1, int(os.getenv("GEMINI_API_RETRIES", "3")) + 1)
        except ValueError:
            max_attempts = 4

        for model in self.models:
            if model in daily_blocked:
                continue

            for attempt in range(max_attempts):
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
                        if _is_model_config_error(exc):
                            config_errors[model] = exc
                            # Model invalid/retired/unauthorized tidak akan
                            # berubah dengan retry; langsung pindah fallback.
                            break
                        other_errors.append(exc)
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

        if config_errors and not daily_blocked and not other_errors:
            models = ", ".join(config_errors)
            raise RuntimeError(
                f"Model Gemini tidak tersedia/ditolak: {models}. "
                "Pilih model aktif di UI atau perbarui GEMINI_MODEL. "
                f"Detail: {next(iter(config_errors.values()))}"
            )
        if daily_blocked and not config_errors and not other_errors:
            raise RuntimeError(
                "Kuota Gemini harian terdeteksi habis pada model: "
                f"{', '.join(daily_blocked)}. Batas dan waktu reset mengikuti project Google AI Studio "
                "(umumnya sekitar tengah malam waktu Pacific); "
                "API key baru pada project yang sama tidak menambah kuota."
            )
        if config_errors and daily_blocked:
            raise RuntimeError(
                "Request Gemini gagal: sebagian model kehabisan kuota dan sebagian tidak tersedia. "
                f"Model kuota habis: {', '.join(daily_blocked)}; "
                f"model tidak tersedia: {', '.join(config_errors)}. "
                "Pilih model aktif dan pastikan API key berasal dari project yang benar."
            )
        raise RuntimeError(
            "Gemini gagal memproses request setelah percobaan terbatas. "
            "Periksa model, API key/project, atau tunggu rate limit pulih. "
            f"Detail terakhir: {last_error}"
        )

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

    ANALYZE_PROMPT_EN = """You are a movie story analyst.
The primary input is movie subtitles with timestamps.

Task:
1. Understand the complete story arc from the subtitles.
2. Identify main characters and their roles.
3. Identify the main conflict.
4. Determine story arc: opening, rising_action, midpoint, climax, ending.
5. List key events with timestamps from the subtitle timeline.
6. Do NOT invent events not supported by the subtitles.
7. CRITICAL LANGUAGE DIRECTIVE: Write all titles, character descriptions, main conflict, story arc, and important events ENTIRELY IN US ENGLISH.

Output valid JSON with exact schema:
{
  "title": "movie title in US English",
  "characters": [{"name": "", "role": ""}],
  "setting": "movie setting in US English",
  "main_conflict": "main conflict in US English",
  "story_arc": {
    "opening": "",
    "rising_action": "",
    "midpoint": "",
    "climax": "",
    "ending": ""
  },
  "important_events": [
    {"event": "key event description in US English", "start": "HH:MM:SS", "end": "HH:MM:SS", "subtitle_ref": 0}
  ]
}

subtitle_ref = line number of subtitle supporting that event.
Ensure ALL timestamps you write actually exist in the subtitle timeline."""

    def generate_story_analysis(
        self, timeline: List[Dict[str, Any]], chunk_size: int = 350, language: str = "id"
    ) -> Dict[str, Any]:
        """Analisis seluruh subtitle. Subtitle panjang dipecah per chunk lalu digabung."""
        if not timeline:
            raise ValueError("Subtitle kosong")

        chunks = [timeline[i : i + chunk_size] for i in range(0, len(timeline), chunk_size)]
        if len(chunks) == 1:
            return self._call(self._render_prompt(chunks[0], language=language), temperature=0.3, label="analysis")

        partials: List[Dict[str, Any]] = []
        for idx, chunk in enumerate(chunks):
            partials.append(
                self._call(
                    self._render_prompt(chunk, chunk_label=f"Bagian {idx + 1} dari {len(chunks)}", language=language),
                    temperature=0.3,
                    label=f"analysis-chunk{idx + 1}",
                )
            )

        return self._merge_partials(partials, language=language)

    def _render_prompt(self, chunk: List[Dict[str, Any]], chunk_label: str = "", language: str = "id") -> str:
        lines = []
        for cue in chunk:
            lines.append(f'[{cue["i"]}] {cue["start"]} {cue["text"]}')
        header = f"({chunk_label})\n\n" if chunk_label else ""
        prompt_text = self.ANALYZE_PROMPT_EN if language == "en" else self.ANALYZE_PROMPT
        return f"{header}SUBTITLE:\n" + "\n".join(lines) + "\n\n" + prompt_text

    MERGE_PROMPT = """Berikut analisis parsial dari beberapa bagian subtitle film yang sama.
Gabungkan menjadi SATU analisis cerita yang konsisten dan lengkap, tanpa duplikasi.

{partials}

PENTING:
-(events) yang duplikat harus digabung.
- story_arc harus mengikuti alur film SEBENARNYA.
- Gunakan Bahasa Indonesia.
- Output JSON valid dengan schema yang sama seperti input.
"""

    MERGE_PROMPT_EN = """Here are partial story analyses from different sections of the same movie subtitle.
Combine them into ONE consistent and complete story analysis without duplicates.

{partials}

CRITICAL INSTRUCTIONS:
- Duplicate events must be merged.
- story_arc must follow the ACTUAL movie plot flow.
- Write ALL fields (title, characters, setting, main_conflict, story_arc, important_events) ENTIRELY IN US ENGLISH.
- Output valid JSON matching the exact same schema as input.
"""

    def _merge_partials(self, partials: List[Dict[str, Any]], language: str = "id") -> Dict[str, Any]:
        compact = json.dumps(partials, ensure_ascii=False)
        merge_prompt = self.MERGE_PROMPT_EN if language == "en" else self.MERGE_PROMPT
        merged = self._call(
            merge_prompt.format(partials=compact), temperature=0.25, label="analysis-merge"
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

ATURAN RELEVANSI VISUAL DENGAN NARASI (SANGAT PENTING):
1. Setiap klip visual HARUS secara langsung menampilkan adegan/peristiwa yang dibicarakan oleh narasi `voice_over` pada bagian tersebut.
2. CARA MEMILIH TIMESTAMP `start`:
   - Baca kalimat narasi `voice_over` yang kamu tulis.
   - Cari baris pada `TIMELINE SUBTITLE` di bagian ini yang berisi dialog/aksi/karakter yang paling sesuai dengan kalimat narasi itu.
   - Gunakan timestamp `start` dari baris subtitle tersebut sebagai `start` klip!
   - DILARANG KERAS menggunakan timestamp dari bagian film lain yang tidak relevan dengan narasi bagian ini!
3. `visual_hint`: Tuliskan deskripsi visual singkat yang menjelaskan apa yang tampak pada timestamp tersebut (misal: "Shin dan Sakamoto memasuki lorong laboratorium").

ATURAN CLIP BLUEPRINT:
1. Setiap clip punya: beat, start, src, trx, out.
2. beat = lowercase-kebab-case (contoh: walk, run, confront, silent-stare, weapon-aim, embrace, child, photo-memory).
3. start HARUS diambil dari timestamp subtitle (HH:MM:SS) yang relevan dengan narasi di atas.
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

ATURAN SEGMENT SINKRONISASI AUDIO-VISUAL:
1. Pecah `voice_over` menjadi beberapa `segments`; satu segment hanya boleh memuat satu gagasan visual.
2. Jika narasi berpindah dari kondisi A ke B (misalnya karakter gemuk menjadi kurus), WAJIB buat segment baru.
3. Setiap segment wajib memiliki `segment_id`, `text`, `visual_cue`, dan daftar `clips` yang relevan.
4. Satu segment boleh memiliki beberapa clip; setiap `src` tetap maksimal 3 detik.
5. Jumlah clip WAJIB minimal `ceil(perkiraan_durasi_narasi / 3)`. Narasi 17 detik harus memiliki minimal 6 clip; narasi 6-8 detik minimal 2-3 clip.
6. `out` setiap clip juga WAJIB <= 3 detik. DILARANG memperpanjang satu clip menjadi 5-7 detik dengan slow motion atau freeze.
7. `audio_duration` belum diketahui sebelum TTS, jadi isi `audio_duration` dengan null dan gunakan jumlah kata sebagai perkiraan durasi.
8. Jangan memasukkan clip dari kondisi visual lain hanya untuk mengisi durasi.

ATURAN OUTPUT:
- Output HARUS JSON valid, tanpa teks lain.
- Ikuti schema persis di bawah."""

    def get_script_rules(self, language: str = "id") -> str:
        if language == "en":
            return """You are a professional movie story scriptwriter and storyboard planner.

CRITICAL LANGUAGE DIRECTIVE:
THE USER REQUESTED US ENGLISH OUTPUT.
ALL NARRATION VOICE-OVER SCRIPTS (`voice_over`), SECTION VISUAL DESCRIPTIONS (`visual`), PROJECT TITLES (`title`), AND CLIP VISUAL HINTS (`visual_hint`) MUST BE WRITTEN 100% IN US ENGLISH.
DO NOT OUTPUT ANY INDONESIAN WORDS OR SENTENCES IN `voice_over`, `visual`, `title`, OR `visual_hint`.

MAIN RULES:
1. Understand the full storyline from the story analysis.
2. Focus on key events rather than just reading subtitles.
3. DO NOT change the story chronology.
4. DO NOT invent events not supported by the subtitles.
5. Write all voice-over scripts, visual descriptions, titles, and hints in US English.
6. Fast-paced, dramatic, emotional, tense narrator style — suitable for movie recap / storytelling videos.
7. Avoid too much direct character dialogue; write as an engaging storyteller.
8. Voice-over must be rich, detailed, and match the target duration.

CLIP BLUEPRINT RULES:
1. Every clip has: beat, start, src, trx, out.
2. beat = lowercase-kebab-case (e.g., walk, run, confront, silent-stare, weapon-aim, embrace, child, photo-memory).
3. start MUST be taken from subtitle timestamps (HH:MM:SS).
4. src = source duration, MUST be 0 < src <= 3.0. NEVER exceed 3 seconds.
5. trx can only be: baref, fz12, s65, s50, s35.
6. out = duration result AFTER transform:
   - baref -> out = src
   - fz12  -> out = 1.2 (freeze frame)
   - s65   -> out = src / 0.65
   - s50   -> out = src / 0.50
   - s35   -> out = src / 0.35
7. Pick visual moments that directly support the voice-over sentence.
8. Avoid repeating timestamps.
9. Visuals follow the narration flow, not a random clip list.
10. Clip quantity matches story density (action parts more clips, quiet parts fewer).

AUDIO-VISUAL SEGMENT RULES:
1. Split `voice_over` into `segments`; each segment contains one visual idea.
2. If narration changes from state A to state B (for example fat character becomes thin), create a new segment.
3. Each segment must contain `segment_id`, `text`, `visual_cue`, and relevant `clips`.
4. A segment may contain multiple clips; each `src` must remain at most 3 seconds.
5. Minimum clip count is `ceil(estimated_narration_duration / 3)`. A 17-second narration needs at least 6 clips; a 6-8 second narration needs at least 2-3 clips.
6. Each clip `out` MUST also be <= 3 seconds. NEVER stretch one clip to 5-7 seconds using slow motion or freeze.
7. `audio_duration` is unknown before TTS, so set it to null and estimate from word count.
8. Never add an unrelated clip just to fill duration.

OUTPUT RULES:
- Output MUST be valid JSON, with no markdown fences or extra text.
- Follow the exact schema below."""
        return self.SCRIPT_RULES

    def generate_storytelling(
        self,
        analysis: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        target_minutes: int,
        wpm: int = 125,
        revision_hint: str = "",
        language: str = "id",
    ) -> Dict[str, Any]:
        if not self.is_configured():
            raise RuntimeError("Gemini API Key belum dikonfigurasi.")

        target_words = int(target_minutes * wpm)
        target_seconds = target_minutes * 60

        timeline_ref = "\n".join(f'[{c["i"]}] {c["start"]} {c["text"]}' for c in timeline)

        visual_desc = "visual description for this section in US English" if language == "en" else "deskripsi visual bagian ini"
        vo_desc = "narration voice-over script for this section in US English" if language == "en" else "naskah narasi bagian ini"
        hint_desc = "what is likely visible at this point" if language == "en" else "apa yang kemungkinan terlihat di titik ini"

        schema = {
            "project": {
                "title": analysis.get("title", ""),
                "source_subtitle": "",
                "target_duration_minutes": target_minutes,
                "estimated_voiceover_seconds": target_seconds,
                "estimated_word_count": target_words,
                "language": language,
            },
            "sections": [
                {
                    "section_id": 1,
                    "visual": visual_desc,
                    "voice_over": vo_desc,
                    "clips": [
                        {
                            "clip_id": 1,
                            "beat": "walk",
                            "start": "00:00:40",
                            "src": 2.0,
                            "trx": "baref",
                            "out": 2.0,
                            "subtitle_ref": 1,
                            "visual_hint": hint_desc,
                        }
                    ],
                    "segments": [
                        {
                            "segment_id": 1,
                            "text": "one complete narration sentence for this visual beat",
                            "visual_cue": "fat-character",
                            "audio_duration": None,
                            "clips": [
                                {
                                    "beat": "fat-character",
                                    "start": "00:00:40",
                                    "src": 2.0,
                                    "trx": "baref",
                                    "out": 2.0,
                                    "visual_hint": hint_desc,
                                    "verification": "required"
                                }
                            ]
                        }
                    ]
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
            log_call(f"revision-hint-{_hint_tag(revision_hint)}", {"hint": revision_hint})

        per_section_words = max(60, target_words // max(1, self.SCRIPT_MIN_SECTIONS))

        if language == "en":
            prompt = f"""{self.get_script_rules(language=language)}

TARGET DURATION (CRITICAL REQUIREMENTS):
- target narration duration: {target_minutes} minutes = {target_seconds} seconds
- total voice-over word count: {target_words} words (+-15%)
- number of sections: around {self.SCRIPT_MIN_SECTIONS}-{self.SCRIPT_MAX_SECTIONS}
- voice-over words per section: around {per_section_words} words

CRITICAL LANGUAGE & CONTENT RULES:
- ALL VOICE-OVER SCRIPTS, VISUAL DESCRIPTIONS, TITLES, AND HINTS MUST BE WRITTEN 100% IN US ENGLISH.
- This is NOT a brief summary. The voice-over MUST be complete and fill {target_seconds} seconds.
- Write full engaging storytelling: setup, complication, climax, resolution.

STORY ANALYSIS:
{json.dumps(analysis, ensure_ascii=False, indent=2)}

SUBTITLE TIMELINE:
{timeline_ref}
{revision_block}
OUTPUT SCHEMA:
{json.dumps(schema, ensure_ascii=False, indent=2)}
"""
        else:
            prompt = f"""{self.get_script_rules(language=language)}

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

    SECTION_PROMPT_EN = """You are a movie storytelling scriptwriter. Write ONE SECTION of the script.

{SCRIPT_RULES}

THIS SECTION:
- Section {part} of {total} in the full script.
- Focus: {focus}
- Tone of previous section: {prev_hint}
- Tone of next section: {next_hint}

QUANTITATIVE RULES (STRICT):
- Voice-over for this section: {words} words (tolerance +-{word_tol}%)
- CHARACTER LIMIT: Voice-over MAXIMUM 3000 characters (including spaces). DO NOT EXCEED 3000 CHARACTERS.
- Target reading duration: {seconds} seconds @ {wpm} words/minute
- WORD COUNT IS AN EXACT TARGET. Write until close to that number.
- CRITICAL DIRECTIVE: WRITE ALL VOICE-OVER SCRIPTS AND VISUAL DESCRIPTIONS 100% IN US ENGLISH.
{parts_hint}

TOTAL VISUAL DURATION FOR THIS SECTION:
- Sum all `out` values of clips in this section.
- Must be close to {seconds} seconds (+-15%).
- Ensure enough clips (e.g. {target_clips} clips or more) so total duration matches voice-over.

DO NOT repeat other sections. Focus ONLY on events assigned to this section.

EVENTS SUPPORTING THIS SECTION:
{events}

SUBTITLE TIMELINE:
{timeline_ref}

OUTPUT SCHEMA:
{schema}"""

    def generate_section(
        self,
        analysis: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        *,
        part: int,
        total_parts: int,
        words: int,
        wpm: int = 125,
        focus: str = "",
        prev_hint: str = "",
        next_hint: str = "",
        events: Optional[List[Dict[str, Any]]] = None,
        part_events: Optional[List[Dict[str, Any]]] = None,
        revision_hint: str = "",
        language: str = "id",
    ) -> Dict[str, Any]:
        """Tulis satu bagian naskah dengan jatah kata spesifik."""
        if not self.is_configured():
            raise RuntimeError("Gemini API Key belum dikonfigurasi.")

        words = max(120, int(words))
        seconds = int(words / wpm * 60) if wpm else 0
        word_tol = 20

        use_events = part_events if part_events is not None else (events or [])
        events_ref = "\n".join(
            f'- {e.get("start", "?")} : {str(e.get("event", ""))[:180]}' for e in use_events
        ) or ("(no specific events — use timeline)" if language == "en" else "(tidak ada event khusus — pakai timeline)")

        if use_events:
            starts = [str(e.get("start", "")) for e in use_events if e.get("start")]
            if starts:
                lo, hi = min(starts), max(starts)
                span = max(0.0, _seconds_between(lo, hi) + 240.0)
                timeline = _slice_timeline(timeline, lo, span)

        timeline_ref = "\n".join(f'[{c["i"]}] {c["start"]} {c["text"]}' for c in timeline[:400])

        if language == "en":
            parts_hint = (
                "STORY ELABORATION: Do not just summarize! Write rich narrative descriptions of situation, emotions, and consequences."
                if total_parts > 1
                else "Write full narrative from start to finish with deep description."
            )
        else:
            parts_hint = (
                "ELABORASI CERITA: Jangan hanya meringkas! Tulis variasi narasi yang kaya: deskripsi situasi, emosi tokoh, "
                "dan konsekuensi tiap kejadian. Panjangkan naskah dengan detail mendalam."
                if total_parts > 1
                else "Tulis narasi lengkap dari awal sampai akhir cerita dengan deskripsi yang mendalam."
            )

        visual_desc = "visual description for this section in US English" if language == "en" else "deskripsi visual bagian ini"
        vo_desc = f"narration voice-over script for this section in US English, around {words} words" if language == "en" else f"naskah narasi bagian ini, sekitar {words} kata"
        hint_desc = "what is visible at this point" if language == "en" else "apa yang terlihat di titik ini"

        schema = json.dumps(
            {
                "section": {
                    "section_id": part,
                    "visual": visual_desc,
                    "voice_over": vo_desc,
                    "clips": [
                        {
                            "clip_id": 1,
                            "beat": "walk",
                            "start": "00:00:40",
                            "src": 2.0,
                            "trx": "baref",
                            "out": 2.0,
                            "subtitle_ref": 1,
                            "visual_hint": hint_desc,
                        }
                    ],
                    "segments": [
                        {
                            "segment_id": 1,
                            "text": "one complete narration sentence for this visual beat",
                            "visual_cue": "fat-character",
                            "audio_duration": None,
                            "clips": [
                                {
                                    "beat": "fat-character",
                                    "start": "00:00:40",
                                    "src": 2.0,
                                    "trx": "baref",
                                    "out": 2.0,
                                    "visual_hint": hint_desc,
                                    "verification": "required"
                                }
                            ]
                        }
                    ]
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

        prompt_template = self.SECTION_PROMPT_EN if language == "en" else self.SECTION_PROMPT
        prompt = prompt_template.format(
            SCRIPT_RULES=self.get_script_rules(language=language),
            part=part,
            total=total_parts,
            focus=focus or (f"part {part} of story" if language == "en" else f"bagian ke-{part} dari cerita"),
            prev_hint=prev_hint or ("(this is the first part)" if language == "en" else "(ini bagian pertama)"),
            next_hint=next_hint or ("(this is the last part)" if language == "en" else "(ini bagian terakhir)"),
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

    HOOK_PROMPT = """Kamu adalah master penulis Hook video storytelling / recap film kelas dunia (viral retention hook creator).

{SCRIPT_RULES}

TUGAS UTAMA:
Buat BAGIAN 0 (HOOK PEMBUKA) berdurasi MINIMAL 60 DETIK (~160-250 kata).
Hook ini wajib dipasang di paling awal video sebelum cerita utama dimulai, khusus untuk meledakkan rasa penasaran penonton (Stop-Scrolling Retention Hook).

FORMULA NASKAH HOOK POWERFUL & DRAMATIS (WAJIB MINIMAL 60 DETIK / 160 KATA):
1. PUNCHLINE EMOSIONAL & KONTRAS MELEDAK: Langsung lempar penonton ke puncak bahaya/klimaks paling mengerikan atau pertaruhan terbesar karakter utama.
2. TEKNIK IN MEDIA RES: Jangan mulai dari awal cerita! Ceritakan pertaruhan nyawa, rahasia kelam, atau kehancuran yang akan datang.
3. PERTANYAAN/PERNYATAAN MEMIKAT (WAH FACTOR): Buat penonton terperangah dengan kontras ekstrem.
4. NADA BICARA: Tegang, cepat, dramatis, penuh intrik, dan bikin merinding.
5. PANJANG NARASI: WAJIB minimal 160 kata (sekitar 60-75 detik pembacaan).

ATURAN KLIP VISUAL HOOK (HIGH-PACED TEASER):
1. Wajib hasilkan MINIMAL 20-35 KLIP visual berdurasi pendek dan sangat cepat (1.0s - 2.5s).
2. Ambil kandidat timestamp `start` HANYA dari momen-momen puncak pertarungan, ledakan, ancaman, atau adegan paling ikonik/tegang dari timeline subtitle.
3. Total durasi visual (`out`) HARUS menyamai atau melebihi 60 detik.

TIMELINE SUBTITLE (timestamp yang boleh dipakai untuk `start`):
{timeline_ref}

SCHEMA OUTPUT:
{schema}"""

    HOOK_PROMPT_EN = """You are a master viral video storytelling hook creator.

{SCRIPT_RULES}

PRIMARY TASK:
Create SECTION 0 (OPENING HOOK) with a MINIMUM DURATION OF 60 SECONDS (~160-250 words).
This hook MUST be placed at the very beginning of the video before the main story starts (Stop-Scrolling Retention Hook).
CRITICAL: WRITE ALL NARRATION & VISUAL DESCRIPTIONS ENTIRELY IN US ENGLISH.

POWERFUL & DRAMATIC HOOK FORMULA (MINIMUM 60 SECONDS / 160+ WORDS):
1. EXPLOSIVE EMOTIONAL PUNCHLINE & CONTRAST: Throw audience straight into the peak of danger/climax or protagonist's highest stakes.
2. IN MEDIA RES TECHNIQUE: Do NOT start at the beginning! Tell the life-or-death stakes, dark secrets, or impending disaster.
3. CAPTIVATING QUESTION / STATEMENT (WAH FACTOR): Stun audience with extreme contrast.
4. TONE: Tense, fast-paced, dramatic, chilling, intriguing.
5. NARRATION LENGTH: MINIMUM 160 words (~60-75 seconds reading time).

VISUAL CLIP BLUEPRINT FOR HOOK (HIGH-PACED TEASER):
1. MUST produce AT LEAST 20-35 short, fast visual clips (1.0s - 2.5s).
2. Take timestamp `start` ONLY from peak fight, explosion, threat, or iconic tense moments in subtitle timeline.
3. Total visual duration (`out`) MUST equal or exceed 60 seconds.

SUBTITLE TIMELINE:
{timeline_ref}

OUTPUT SCHEMA:
{schema}"""

    def generate_hook(
        self,
        analysis: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        wpm: int = 125,
        language: str = "id",
    ) -> Dict[str, Any]:
        """Generate Bagian 0 (Hook) minimal 60 detik di paling awal naskah."""
        if not self.is_configured():
            raise RuntimeError("Gemini API Key belum dikonfigurasi.")

        timeline_ref = "\n".join(f'[{c["i"]}] {c["start"]} {c["text"]}' for c in timeline[:600])

        visual_desc = "Opening 60-second visual teaser, highly tense and captivating" if language == "en" else "Teaser visual pembuka berdurasi 60 detik yang sangat tegang dan memikat penonton"
        vo_desc = "Opening viral hook narration script (minimum 160 words / 60+ seconds) in US English..." if language == "en" else "Naskah narasi hook pembuka minimal 150 kata berdurasi 60 detik yang memicu penasaran..."
        hint_desc = "climax fight / most tense scene" if language == "en" else "adegan klimaks / pertarungan paling tegang"

        schema = json.dumps(
            {
                "section": {
                    "section_id": 0,
                    "visual": visual_desc,
                    "voice_over": vo_desc,
                    "clips": [
                        {
                            "clip_id": 1,
                            "beat": "confront",
                            "start": "00:05:00",
                            "src": 2.0,
                            "trx": "baref",
                            "out": 2.0,
                            "subtitle_ref": 1,
                            "visual_hint": hint_desc,
                        }
                    ],
                }
            },
            ensure_ascii=False,
            indent=2,
        )

        prompt_template = self.HOOK_PROMPT_EN if language == "en" else self.HOOK_PROMPT
        prompt = prompt_template.format(
            SCRIPT_RULES=self.get_script_rules(language=language),
            timeline_ref=timeline_ref,
            schema=schema,
        )
        return self._call(prompt, temperature=0.5, label="section-hook")

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
