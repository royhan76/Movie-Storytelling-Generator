"""Generate naskah storytelling + blueprint klip dari subtitle.

Pipelines:
    SRT -> timeline -> Story Analysis -> Storytelling+Storyboard -> revisi -> output
"""
import json
import os
from typing import Any, Callable, Dict, List, Optional

from app.debug_log import log_call
from app.providers.gemini import GeminiProvider
from app.schemas.storyboard import Storyboard
from app.services.clip_validator import (
    enforce_segment_clip_budget,
    solve_durations,
    sync_segments_from_sections,
    validate_section,
)
from app.services.duration_validator import DEFAULT_WPM, recalc
from app.services.srt_parser import SrtParser
from app.services.story_analyzer import StoryAnalyzer, events_for_target
from app.services.storyboard_generator import StoryboardBuilder

DURATION_TOLERANCE_PCT = 15.0

# Berapa cue yang dianalisis per panggilan Gemini. Subtitle 1549 cue = 5 chunk.
ANALYSIS_CHUNK = max(350, int(os.getenv("GEMINI_ANALYSIS_CHUNK", "350")))

# Tahap 2: subtitle yang sangat panjang dipotong jadi beberapa bagian cerita,
# lalu disambung. Kalau semua subtitle dikirim utuh ke satu prompt, request-nya
# bisa lebih besar dari context window dan modeling jadi lambat sekali.
SCRIPT_CHUNK_THRESHOLD = 600
SCRIPT_CHUNK_SIZE = 600

# Batas kata yang bisa ditulis dalam SATU panggilan Gemini. Dari run nyata:
# 79, 197, 858, 891, 719, 908 kata -- jadi mentok di ~900.
# Target di atas ini harus ditulis per-bagian lalu disambung.
SECTION_MODE_MIN_WORDS = 1000

# Berapa bagian naskah untuk target panjang. 2250 kata / 750 per bagian = 3.
SECTION_PARTS = 4

# Jatah kata per bagian. Diberi sedikit ruang supaya totalnya >= target.
SECTION_WORD_BUFFER = 1.12

# Batas klip per section. Target besar butuh klip lebih banyak agar durasi sinkron.
SECTION_MAX_CLIPS = 120


def _limit_clips(clips: List[Any]) -> List[Any]:
    """Buang clip berlebih per section dan buang yang bukan dict."""
    out: List[Any] = []
    for clip in clips:
        if not isinstance(clip, dict):
            continue
        out.append(clip)
        if len(out) >= SECTION_MAX_CLIPS:
            break
    return out


class StoryPipeline:
    def __init__(
        self,
        parser: SrtParser,
        provider: GeminiProvider,
        wpm: int = DEFAULT_WPM,
        max_retry: int = 2,
    ) -> None:
        self.parser = parser
        self.provider = provider
        self.wpm = wpm
        self.max_retry = max_retry
        self.builder = StoryboardBuilder()
        self.analyzer = StoryAnalyzer(provider)

    def run(
        self,
        srt_content: str,
        target_minutes: int,
        source_name: str,
        language: str = "id",
        progress: Optional[Callable[[str, Dict[str, Any]], None]] = None,
    ) -> Dict[str, Any]:
        """Jalankan pipeline penuh.

        `progress(stage, data)` dipanggil setiap tahap selesai supaya UI bisa
        menampilkan Status — generate subtitle panjang butuh belasan menit.
        """
        def emit(stage: str, **data: Any) -> None:
            if progress:
                progress(stage, data)

        emit("parse", cue_count=0)
        entries = self.parser.parse(srt_content)
        if not entries:
            raise ValueError("Subtitle gagal diparse atau kosong.")

        timeline = self.parser.build_timeline(entries)
        timeline_start, timeline_end = self.parser.timeline_bounds(entries)

        # --- Validasi target vs panjang film -------------------------------
        # Narasi 15 menit butuh ~2250 kata. Kalau filmnya cuma 2.6 menit
        # (156 detik), itu 5.8x lebih panjang dari film itu sendiri -- tidak
        # mungkin dicapai tanpa mengulang adegan atau mengarang isi. Menolak
        # di sini jauh lebih baik daripada diam-diam menghasilkan naskah 200
        # detik yang tidak sesuai permintaan.
        target_seconds = float(target_minutes) * 60.0
        MAX_NARRATION_RATIO = 1.5
        if timeline_end > 0:
            max_seconds = timeline_end * MAX_NARRATION_RATIO
            if target_seconds > max_seconds:
                max_minutes = max(1, int(max_seconds // 60))
                raise ValueError(
                    f"Target {target_minutes} menit tidak mungkin untuk film ini. "
                    f"Durasi film cuma {int(timeline_end // 60)} menit "
                    f"{int(timeline_end % 60)} detik, jadi maksimal narasi sekitar "
                    f"{max_minutes} menit. Turunkan target atau pakai film yang lebih panjang."
                )
        chunk_count = max(1, -(-len(timeline) // ANALYSIS_CHUNK))
        emit(
            "parse",
            cue_count=len(entries),
            duration_sec=round(timeline_end - timeline_start, 1),
            chunks=chunk_count,
        )

        # ---- Tahap 1: story analysis -------------------------------- #
        emit("analysis", phase="start", chunks=chunk_count)
        analysis = self.analyzer.analyze(timeline, chunk_size=ANALYSIS_CHUNK, language=language)
        # Jumlah event ikut target: naskah 15 menit tidak mungkin ditulis dari
        # 40 kejadian. Tanpa ini, film 100+ menit jadi naskah < 1 menit.
        max_events = events_for_target(target_minutes, self.wpm)
        compressed = self.analyzer.compress(analysis, max_events=max_events)
        emit(
            "analysis",
            phase="done",
            events_kept=max_events,
            events_available=len(analysis.get("important_events") or []),
        )

        # ---- Tahap 2: storytelling + storyboard --------------------- #
        #
        # Dua jalur. Target besar (> SECTION_MODE_MIN_WORDS) pakai jalur
        # per-bagian: satu panggilan Gemini mentok ~900 kata, jadi naskah
        # 2250 kata harus ditulis sebagai beberapa bagian lalu disambung.
        # Target kecil tetap satu panggilan -- lebih cepat dan cukup.
        target_words_total = int(target_minutes * self.wpm)
        use_sections = target_words_total > SECTION_MODE_MIN_WORDS
        # Skala jumlah bagian secara dinamis (~300 kata/bagian) supaya Gemini
        # menghasilkan klip penuh di tiap bagian tanpa memotong JSON.
        parts = max(SECTION_PARTS, min(8, target_words_total // 300)) if use_sections else 1
        emit(
            "script",
            phase="start",
            parts=parts,
            target_words=target_words_total,
            mode="per-bagian" if use_sections else "tunggal",
        )

        best: Optional[Dict[str, Any]] = None
        best_report: Optional[Dict[str, Any]] = None
        attempts = 0
        warnings: List[str] = []

        for attempt in range(self.max_retry + 1):
            attempts = attempt + 1
            emit("script", phase="attempt", attempt=attempts, max=self.max_retry + 1)

            if use_sections:
                raw = self._write_by_sections(
                    compressed,
                    timeline,
                    target_minutes,
                    parts,
                    target_words_total,
                    revision_hint=self._hint_from(best_report)
                    if best_report and not best_report["ok"]
                    else "",
                    language=language,
                    progress=lambda i, total: emit(
                        "script", phase="part", part=i, total=total
                    ),
                )
            else:
                raw = self.provider.generate_storytelling(
                    compressed,
                    timeline,
                    target_minutes,
                    wpm=self.wpm,
                    revision_hint=self._hint_from(best_report)
                    if best_report and not best_report["ok"]
                    else "",
                    language=language,
                )

            # Log payload mentah SEBELUM build: kalau Gemini mengirim field
            # bertipe salah (mis. project jadi list), build() akan melempar
            # AttributeError dan respons aslinya hilang. File ini yang
            # memungkinkan perbaikan tanpa menebak.
            log_call(
                f"raw-storyboard-attempt{attempts}",
                raw,
                duration_sec=0.0,
            )

            candidate = self.builder.build(raw)
            candidate = self._ensure_hook(candidate, compressed, timeline, language=language)
            candidate.project.title = candidate.project.title or compressed.get("title", "")
            candidate.project.source_subtitle = source_name
            candidate.project.target_duration_minutes = target_minutes
            candidate.project.language = language

            seen: set = set()
            all_issues: List[str] = []
            for section in candidate.sections:
                _, issues = validate_section(section, self.parser, timeline_start, timeline_end, seen)
                all_issues.extend(issues)

            candidate = sync_segments_from_sections(candidate)
            all_issues.extend(enforce_segment_clip_budget(candidate, wpm=self.wpm))
            candidate = sync_segments_from_sections(candidate)

            candidate, report = recalc(candidate, self.wpm)
            candidate, solve_report = solve_durations(candidate, tolerance_pct=DURATION_TOLERANCE_PCT)
            # Solver lama boleh memilih slow-motion untuk mengejar total durasi.
            # Tegakkan lagi batas 3 detik per unit setelah solver selesai.
            all_issues.extend(enforce_segment_clip_budget(candidate, wpm=self.wpm))
            candidate = sync_segments_from_sections(candidate)
            candidate, report = recalc(candidate, self.wpm)
            report["issues"] = all_issues
            report["attempts"] = attempts

            if best_report is None or report["diff_pct"] < best_report["diff_pct"]:
                best, best_report = candidate, report

            # Berhenti kalau target durasi sudah tercapai. Selector di baris
            # atas hanya membandingkan alignment internal (voice-over vs klip),
            # sehingga naskah yang sudah 94% dari target tetap memicu retry --
            # dan tiap retry di jalur per-bagian berarti 4 panggilan Gemini.
            # Dengan kuota 20 request/hari per model, itu pemborosan mahal
            # untuk sesuatu yang tidak bisa diperbaiki dengan menulis ulang.
            if report["ok"]:
                break
            emit("script", phase="retry_needed", diff_pct=report["diff_pct"])

        emit("validate", phase="done")
        assert best is not None and best_report is not None

        # Penomoran ulang: clip yang dibuang (duplikat/tanpa start) menyisakan
        # celah pada clip_id. Rapikan supaya Plan 2 melihat id rapat 1..N.
        next_id = 1
        for section in best.sections:
            for clip in section.clips:
                clip.clip_id = next_id
                next_id += 1

        best_report["attempts"] = attempts
        best_report["solver"] = solve_report
        if not best_report["ok"]:
            warnings.append(
                f"Durasi visual {best_report['total_clip_out']}s vs voice-over "
                f"{best_report['est_vo_sec']}s (selisih {best_report['diff_pct']}%). "
                "Hasil tetap dipakai dengan peringatan."
            )
        best_report["warnings"] = warnings
        best_report["cue_count"] = len(entries)
        best_report["timeline"] = [timeline_start, timeline_end]

        return {"storyboard": best, "analysis": analysis, "report": best_report}

    def _ensure_hook(
        self,
        candidate: Storyboard,
        compressed: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        language: str = "id",
    ) -> Storyboard:
        """Pastikan Bagian 0 (Hook Pembuka) minimal 60s ada di urutan pertama storyboard."""
        has_hook = any(s.section_id == 0 for s in candidate.sections)
        if has_hook:
            return candidate

        try:
            hook_payload = self.provider.generate_hook(compressed, timeline, wpm=self.wpm, language=language)
            raw_hook = hook_payload.get("section") if isinstance(hook_payload, dict) else None
            if not isinstance(raw_hook, dict):
                raw_hook = hook_payload if isinstance(hook_payload, dict) else {}
            if raw_hook.get("voice_over"):
                raw_hook = dict(raw_hook)
                raw_hook["section_id"] = 0
                raw_hook["clips"] = _limit_clips(raw_hook.get("clips") or [])
                raw_dict = candidate.model_dump()
                raw_dict["sections"].insert(0, raw_hook)
                return self.builder.build(raw_dict)
        except Exception as exc:
            log_call("hook-ensure-error", {"error": str(exc)})
        return candidate

    def _write_by_sections(
        self,
        compressed: Dict[str, Any],
        timeline: List[Dict[str, Any]],
        target_minutes: int,
        parts: int,
        target_words_total: int,
        revision_hint: str = "",
        language: str = "id",
        progress=None,
    ) -> Dict[str, Any]:
        """Tulis naskah per bagian lalu gabung jadi satu storyboard.

        Ini yang menutup celah "Gemini mentok ~900 kata per panggilan". Tiap
        bagian dapat jatah kata sendiri (target_words_total / parts), jadi total
        naskah bisa jauh melebihi batas per-panggilan.

        Event dianalisis dibagi rata ke tiap bagian supaya tidak ada bagian yang
        dapat bahan lebih banyak dari yang lain -- itu yang bikin panjang naskah
        tidak merata.
        """
        events = list(compressed.get("important_events") or [])
        if not events:
            # Tidak ada event: pecah timeline sendiri per bagian.
            per = max(1, len(timeline) // parts)
            chunks = [timeline[i * per : (i + 1) * per] for i in range(parts)]
        else:
            per = max(1, -(-len(events) // parts))
            chunks = [events[i * per : (i + 1) * per] for i in range(parts)]

        words_each = int((target_words_total / parts) * SECTION_WORD_BUFFER)

        # Kalau percobaan sebelumnya menghasilkan naskah kurang, naikkan jatah
        # kata per bagian secara proporsional. Ini yang membuat loop retry
        # benar-benar bergerak menuju target, bukan mengulang jawaban yang sama.
        if revision_hint:
            for marker in ("TERLALU PENDEK", "kurang", "GAGAL"):
                if marker in revision_hint:
                    words_each = int(words_each * 1.6)
                    break

        sections: List[Dict[str, Any]] = []

        # ---- Section 0: Hook Pembuka (Minimal 60 Detik) ----
        try:
            hook_payload = self.provider.generate_hook(compressed, timeline, wpm=self.wpm, language=language)
            raw_hook = hook_payload.get("section") if isinstance(hook_payload, dict) else None
            if not isinstance(raw_hook, dict):
                raw_hook = hook_payload if isinstance(hook_payload, dict) else {}
            if raw_hook.get("voice_over"):
                raw_hook = dict(raw_hook)
                raw_hook["section_id"] = 0
                raw_hook["clips"] = _limit_clips(raw_hook.get("clips") or [])
                sections.append(raw_hook)
        except Exception:
            pass

        arc = compressed.get("story_arc") or {}
        arc_keys = list(arc.keys())

        for idx in range(parts):
            chunk = chunks[idx]
            if not chunk:
                continue

            # Fokus tiap bagian diambil dari story_arc supaya alur cerita utuh.
            focus = str(arc.get(arc_keys[idx], ""))[:220] if idx < len(arc_keys) else ""
            prev_hint = (
                f"menutup bagian sebelumnya ({sections[-1].get('visual', '')[:90]})"
                if sections
                else "(ini bagian pertama, bangun kesan awal)"
            )
            next_hint = (
                "menyiapkan bagian berikutnya"
                if idx + 1 < parts
                else "(ini bagian terakhir, tutup dengan resolusi)"
            )

            payload = self.provider.generate_section(
                compressed,
                timeline,
                part=idx + 1,
                total_parts=parts,
                words=words_each,
                wpm=self.wpm,
                focus=focus,
                prev_hint=prev_hint,
                next_hint=next_hint,
                part_events=[e for e in chunk if isinstance(e, dict)],
                revision_hint=revision_hint,
                language=language,
            )
            if progress:
                progress(idx + 1, parts)

            # Gemini bisa membungkus hasil dalam {"section": {...}} atau
            # langsung objek section. Terima keduanya.
            raw_section = payload.get("section") if isinstance(payload, dict) else None
            if not isinstance(raw_section, dict):
                raw_section = payload if isinstance(payload, dict) else {}
            if not raw_section.get("voice_over"):
                continue

            raw_section = dict(raw_section)
            raw_section["section_id"] = len(sections) if sections and sections[0].get("section_id") == 0 else len(sections) + 1
            # Satu panggilan = satu section; jangan biarkan Gemini menambah
            # bagian lain yang nanti berduplikasi.
            raw_section["clips"] = _limit_clips(raw_section.get("clips") or [])
            sections.append(raw_section)

        return {
            "project": {
                "title": compressed.get("title", ""),
                "source_subtitle": "",
                "target_duration_minutes": target_minutes,
                "estimated_voiceover_seconds": target_minutes * 60,
                "estimated_word_count": target_words_total,
                "language": language,
            },
            "sections": sections,
            "summary": {"total_sections": len(sections), "total_clips": 0, "total_clip_duration": 0.0},
        }

    @staticmethod
    def _hint_from(report: Optional[Dict[str, Any]]) -> str:
        if not report:
            return ""
        est = report.get("est_vo_sec", 0)
        total = report.get("total_clip_out", 0)
        pct = report.get("diff_pct", 0)
        target_pct = report.get("target_diff_pct", 0)
        words = report.get("word_count", 0)
        target_words = report.get("target_words", 0)

        # Naskah terlalu pendek vs target: koreksi INI dulu, bukan via klip,
        # karena menambah klip tidak menambah durasi voice-over. Kalau naskahnya
        # cuma 200 kata untuk target 2250 kata, revisi harus menambah NARASI.
        parts: List[str] = []
        if target_pct > 25.0 and target_words:
            if words < target_words:
                parts.append(
                    f"NASKAH TERLALU PENDEK. Total voice-over hanya {words} kata "
                    f"({est:.0f}s), target {target_words} kata ({target_words * 0.4:.0f}s). "
                    f"TAMBAHKAN narasi secara substantif — panjangkan tiap bagian."
                )
            else:
                parts.append(
                    f"Naskah terlalu panjang: {words} kata, target {target_words} kata. "
                    f"Rampingkan tanpa kehilangan inti cerita."
                )

        if pct > 15.0 and est > 0:
            if total < est:
                needed_clips = max(10, int((est - total) / 2.5))
                parts.append(
                    f"JUMLAH KLIP VISUAL JAUH TERLALU SEDIKIT! Total durasi visual hanya {total:.1f}s "
                    f"sedangkan voice-over {est:.0f}s (selisih {pct:.1f}%). "
                    f"WAJIB TAMBAHKAN minimal {needed_clips} klip visual baru dengan timestamp subtitle yang belum dipakai!"
                )
            else:
                parts.append(
                    f"Total durasi visual {total:.1f}s kelebihan vs voice-over {est:.0f}s. "
                    f"Kurangi jumlah klip atau persingkat src."
                )

        return " ".join(parts)


def storyboard_to_markdown(sb) -> str:
    lines = [f"# Storytelling Script — {sb.project.title or sb.project.source_subtitle}", ""]
    lines.append(f"- Subtitle: `{sb.project.source_subtitle}`")
    lines.append(f"- Target durasi: {sb.project.target_duration_minutes} menit")
    lines.append(f"- Estimasi voice-over: {sb.project.estimated_voiceover_seconds}s "
                 f"({sb.project.estimated_word_count} kata)")
    lines.append(f"- Total klip: {sb.summary.total_clips} | total durasi klip: "
                 f"{sb.summary.total_clip_duration}s")
    lines.append("")

    for section in sb.sections:
        sec_title = "Bagian 0 (Hook / Teaser Pembuka)" if section.section_id == 0 else f"Bagian {section.section_id}"
        lines.append(f"## {sec_title}")
        lines.append("")
        lines.append("### Visual")
        lines.append("")
        lines.append(section.visual)
        lines.append("")
        lines.append("### Daftar Klip")
        lines.append("")
        lines.append("| beat | start | src | trx | out |")
        lines.append("|------|-------|-----|-----|-----|")
        for clip in section.clips:
            lines.append(f"| {clip.beat} | {clip.start} | {clip.src:.1f}s | {clip.trx} | {clip.out:.1f}s |")
        lines.append("")
        lines.append("### Naskah Voice-over")
        lines.append("")
        lines.append(section.voice_over)
        lines.append("")
        if section.segments:
            lines.append("### Segment Sinkronisasi Audio-Visual")
            lines.append("")
            lines.append("| segment | visual cue | narasi | audio duration | status |")
            lines.append("|---------|-------------|--------|----------------|--------|")
            for segment in section.segments:
                duration = "pending TTS" if segment.audio_duration is None else f"{segment.audio_duration:.2f}s"
                text = segment.text.replace("|", "\\|").replace("\n", " ")
                lines.append(
                    f"| {segment.segment_id} | {segment.visual_cue} | {text} | {duration} | {segment.sync_status} |"
                )
            lines.append("")
        lines.append(f"**Jumlah klip bagian ini:** {section.clip_count}")
        lines.append(f"**Total durasi klip bagian ini:** {section.total_clip_duration}s")
        lines.append("")

    return "\n".join(lines)


def storyboard_to_voiceover(sb) -> str:
    return "\n\n".join(s.voice_over for s in sb.sections).strip() + "\n"
