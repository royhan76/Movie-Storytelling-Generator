# Alur Cerita — Plan 1

SRT film → naskah voice-over Bahasa Indonesia + blueprint klip (`storyboard.json`).

Ruang lingkup Plan 1 **berakhir di storyboard**. Tidak ada cutting video,
FFmpeg, TTS, atau musik di sini — itu Plan 2.

## Jalankan

Double-click:

- `MULAI_ALUR_CERITA.bat` — start backend (8012) + frontend (5173), buka browser
- `STOP_ALUR_CERITA.bat` — stop keduanya

Lalu buka http://localhost:5173, pilih file `.srt`, masukkan target durasi
(menit), klik **Generate Naskah**.

Manual ( kalau perlu):

```bash
# backend
cd backend && ../venv/Scripts/python.exe -m uvicorn app.main:app --port 8012

# frontend
cd frontend && npm run dev

# test
cd backend && ../venv/Scripts/python.exe -m tests.run_all
```

## Konfigurasi

`.env` di root project (sudah terisi untuk project ini):

| Variabel | Default | Guna |
|---|---|---|
| `GEMINI_API_KEY` | — | Wajib. Dari https://aistudio.google.com/apikey |
| `GEMINI_MODEL` | `gemini-2.5-flash` | Model tahap analysis + storytelling |
| `DEFAULT_WPM` | `150` | Kata/menit untuk estimasi durasi voice-over |
| `MAX_RETRY` | `2` | Berapa kali Gemini diminta revisi storyboard |

## Output

Setiap generate membuat folder `projects/<tanggal>-<id>/`:

| File | Isi |
|---|---|
| `story_map.json` | Analisis cerita: karakter, konflik, arc, kejadian penting |
| `storyboard.json` | **Input utama Plan 2** — sections + clips + report |
| `storyboard.md` | Versi manusia, format seperti kerja manual |
| `voiceover.txt` | Naskah narasi saja, tanpa tabel |

## Format clip

```json
{ "clip_id": 1, "beat": "walk", "start": "00:00:40", "src": 2.0, "trx": "baref", "out": 2.0 }
```

`start` adalah **kandidat** lokasi klip dari timeline subtitle — bukan jaminan
frame tertentu ada di sana. Verifikasi Against video dilakukan di Plan 2.

Aturan yang dijamin backend:

- `src` selalu `0 < src <= 3.0` (dibatasi, bukan dipercaya ke AI)
- `trx` hanya `baref`, `fz12`, `s65`, `s50`, `s35`; nilai lain dipetakan ke `baref`
- `out` selalu dihitung ulang dari `src` + `trx`:
  - `baref` → `src`
  - `fz12` → `1.2` (freeze frame)
  - `s65` → `src / 0.65`
  - `s50` → `src / 0.50`
  - `s35` → `src / 0.35`
- `start` selalu di dalam rentang subtitle, dan duplikat dibuang
- `clip_id` rapat 1..N tanpa celah

## Pipeline

```
SRT → parser → timeline → Gemini Tahap 1 (analisis cerita)
                          → Gemini Tahap 2 (naskah + storyboard)
                          → validasi + normalisasi + duration solver
                          → 4 file di projects/
```

Subtitle panjang dipecah per chunk (350 cue), dianalisis terpisah, lalu
digabung jadi satu story map supaya hasil antar-chunk konsisten.

Kalau total durasi visual meleset > 15% dari estimasi voice-over, Gemini
diminta revisi (maks `MAX_RETRY` kali). Selisih yang tak tertutup dilaporkan
sebagai `report.warnings`, bukan dipaksa dengan merusak alur.

## API

| Method | Endpoint | Fungsi |
|---|---|---|
| `GET` | `/api/health` | Status service + apakah Gemini terkonfigurasi |
| `POST` | `/api/generate` | `multipart`: `subtitle` (.srt) + `target_duration` (menit) |
| `GET` | `/api/projects` | Daftar project tersimpan |
| `GET` | `/api/projects/{id}/storyboard.json` | Download blueprint (input Plan 2) |
| `GET` | `/api/projects/{id}/storyboard.md` | Download markdown |
| `GET` | `/api/projects/{id}/voiceover.txt` | Download naskah |
| `GET` | `/api/projects/{id}/story_map.json` | Download analisis cerita |

## Test

```bash
cd backend && ../venv/Scripts/python.exe -m tests.run_all
```

- `test_srt_parser` — parser SRT tahan format varied (CRLF, tanpa indeks, baris kosong berlebih, angka bocor, format titik)
- `test_clip_validator` — clamp `src`, normalisasi `trx`/`beat`, dedupe timestamp, duration solver
- `test_chunking` — ekstraksi JSON dari balasan ber-fence, chunking subtitle panjang
- `test_pipeline` — end-to-end dengan Gemini di-stub, memakai input sengaja rusak