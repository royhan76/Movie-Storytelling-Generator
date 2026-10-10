# PLAN 1 — SRT → Storytelling Script + Clip Blueprint

## 1. Tujuan

Plan 1 bertugas mengubah **file subtitle `.srt`** menjadi:

1. Naskah voice-over storytelling berbahasa Indonesia.
2. Pembagian cerita menjadi beberapa bagian/scene.
3. Daftar klip yang dibutuhkan untuk setiap bagian.
4. Timestamp sumber (`start`) yang menjadi kandidat lokasi klip di film.
5. Durasi sumber (`src`), transformasi (`trx`), dan durasi hasil (`out`).
6. Output machine-readable `storyboard.json` yang nantinya menjadi input utama **Plan 2**.

> **Penting:** Plan 1 tidak memotong video dan tidak membutuhkan file video.  
> Plan 1 hanya membaca timeline subtitle dan membuat blueprint.  
> Plan 2 nanti menerima video film + `storyboard.json` untuk benar-benar memotong dan memproses klip.

---

# 2. Input

### Wajib

- File subtitle `.srt`
- Target durasi video storytelling

Contoh:

```text
Subtitle   : film.srt
Durasi     : 15 menit
```

### UI

Buat halaman sederhana:

```text
┌──────────────────────────────────────────┐
│       MOVIE STORYTELLING GENERATOR       │
├──────────────────────────────────────────┤
│                                          │
│  Subtitle                                │
│  [ film.srt                    ] [Pilih] │
│                                          │
│  Target Durasi                           │
│  [ 15 ] menit                            │
│                                          │
│          [ GENERATE NASKAH ]             │
│                                          │
└──────────────────────────────────────────┘
```

Setelah proses selesai tampilkan:

- Ringkasan cerita
- Naskah voice-over
- Storyboard / daftar klip
- Jumlah klip
- Estimasi durasi
- Tombol `Copy`
- Tombol `Download Markdown`
- Tombol `Download JSON`

---

# 3. Teknologi

## Frontend

- React
- TypeScript
- Tailwind CSS
- shadcn/ui
- Lucide Icons

UI harus:

- Modern
- Minimal
- Responsive
- Nyaman digunakan di desktop
- Tidak terlalu banyak dekorasi

## Backend

- Python
- FastAPI

## Subtitle

Gunakan parser SRT Python.

Struktur data internal:

```json
{
  "index": 1,
  "start": "00:00:12.500",
  "end": "00:00:15.800",
  "text": "..."
}
```

## AI

Gunakan Gemini API.

Model dibuat configurable melalui `.env`:

```env
GEMINI_API_KEY=YOUR_KEY
GEMINI_MODEL=YOUR_MODEL
```

Jangan hard-code API key.

---

# 4. Alur Utama

```text
USER
 │
 │ upload film.srt
 │
 │ pilih target durasi
 ▼
SRT Parser
 │
 ▼
Subtitle Timeline
 │
 ▼
Gemini — Story Analysis
 │
 ▼
Plot / Character / Conflict / Climax / Ending
 │
 ▼
Gemini — Script + Storyboard Generator
 │
 ├── Voice-over
 ├── Visual description
 └── Clip blueprint
       │
       ▼
Duration Validator
       │
       ▼
Storyboard JSON
       │
       ├── storyboard.json
       └── storyboard.md
```

---

# 5. Proses AI

Sebaiknya proses Gemini dibuat menjadi **2 tahap**.

## Tahap 1 — Story Analysis

Gemini membaca seluruh subtitle dan membuat pemetaan cerita.

Hasil internal:

```json
{
  "title": "",
  "characters": [],
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
    {
      "event": "",
      "start": "00:00:00",
      "end": "00:00:00"
    }
  ]
}
```

Tujuannya supaya AI tidak langsung menulis naskah dari subtitle mentah tanpa memahami keseluruhan cerita.

---

# 6. Tahap 2 — Generate Storytelling

Gemini menerima:

- Subtitle
- Story analysis
- Target durasi

Kemudian menghasilkan storytelling yang sudah dipadatkan.

## Gaya narasi

Default:

- Bahasa Indonesia
- Cepat
- Dramatis
- Emosional
- Tegang
- Cocok untuk video recap/alur cerita
- Tidak terlalu banyak dialog langsung
- Fokus pada kejadian penting
- Tidak mengubah urutan cerita
- Tidak mengarang kejadian yang tidak didukung subtitle

Contoh gaya:

> Dulu, Sakamoto adalah pembunuh bayaran yang namanya ditakuti di seluruh Jepang. Namun semuanya berubah ketika dia jatuh cinta. Demi keluarganya, sang legenda memilih meninggalkan dunia pembunuhan dan menjalani kehidupan sederhana sebagai pemilik toko kelontong.

Narasi harus terasa seperti storyteller, bukan seperti membaca subtitle.

---

# 7. Target Durasi Voice-over

Gunakan estimasi jumlah kata.

Default:

```text
150 kata / menit
```

Contoh:

```text
10 menit  ≈ 1.500 kata
15 menit  ≈ 2.250 kata
20 menit  ≈ 3.000 kata
```

Sediakan konfigurasi:

```env
DEFAULT_WPM=150
```

Target tidak harus tepat sampai satu kata, tetapi usahakan berada dalam toleransi sekitar ±5–10%.

---

# 8. Struktur Output

Output utama dibagi menjadi beberapa bagian.

Contoh:

```text
Visual:
Sakamoto menjalani kehidupan ganda dari pembunuh bayaran legendaris
hingga menjadi pemilik toko kelontong yang ramah.

Daftar Klip:

| beat | start | src | trx | out |
|------|-------|-----|-----|-----|
| walk | 00:00:40 | 2.0s | baref | 2.0s |
| silent-stare | 00:00:50 | 1.6s | fz12 | 1.2s |
| confront | 00:01:52 | 2.0s | baref | 2.0s |
| weapon-aim | 00:02:21 | 2.0s | baref | 2.0s |
| photo-memory | 00:03:03 | 1.8s | fz12 | 1.2s |

Naskah Voice-over:

Dulu ada pembunuh bayaran legendaris bernama Sakamoto...
```

Di akhir:

```text
Jumlah klip bagian ini: 5
Total durasi klip bagian ini: 00:09.6
```

---

# 9. Format Clip

Setiap clip memiliki:

```json
{
  "beat": "walk",
  "start": "00:00:40",
  "src": 2.0,
  "trx": "baref",
  "out": 2.0
}
```

## Field

### `beat`

Label pendek untuk jenis visual.

Contoh:

```text
walk
run
fight
confront
silent-stare
weapon-aim
weapon-fire
shout
fall
embrace
child
phone-call
document
village
corpse
crowd-panic
photo-memory
```

Gunakan format:

```text
lowercase-kebab-case
```

Boleh membuat beat baru jika diperlukan.

---

# 10. Timestamp `start`

`start` adalah kandidat timestamp lokasi klip pada film.

Contoh:

```text
00:05:32
```

Karena Plan 1 hanya membaca subtitle, timestamp ini berasal dari **timeline subtitle** dan dianggap sebagai:

```text
candidate source timestamp
```

Bukan jaminan bahwa frame visual yang diinginkan benar-benar ada pada timestamp tersebut.

Plan 2 nanti akan melakukan proses sebenarnya terhadap video.

---

# 11. Aturan Durasi `src`

Ini sangat penting.

**Durasi sumber maksimum = 3 detik.**

```text
0 < src <= 3.0
```

Contoh valid:

```text
1.2s
1.5s
2.0s
2.5s
3.0s
```

Tidak boleh:

```text
4.0s
5.0s
```

sebagai `src`.

Namun durasi `out` boleh lebih dari 3 detik karena transformasi.

---

# 12. Transform (`trx`)

Gunakan kode sederhana seperti contoh user.

## `baref`

Normal speed.

```text
src = 2.0
trx = baref
out = 2.0
```

## `fz12`

Freeze frame sekitar 1.2 detik.

```text
src = 1.5
trx = fz12
out = 1.2
```

## `s65`

Slow motion 65%.

```text
src = 2.0
trx = s65
out ≈ 3.1
```

## `s50`

Slow motion 50%.

```text
src = 2.0
trx = s50
out = 4.0
```

## `s35`

Slow motion 35%.

```text
src = 2.0
trx = s35
out ≈ 5.7
```

Untuk output JSON, transform sebaiknya tetap disimpan dalam bentuk sederhana:

```json
{
  "trx": "s50"
}
```

Plan 2 yang menerjemahkan kode tersebut menjadi FFmpeg filter.

---

# 13. Duration Solver

Setelah AI membuat daftar clip, backend melakukan validasi dan penyesuaian.

Prioritas:

```text
1. baref
2. fz12
3. s65
4. s50
5. s35
```

Hindari penggunaan slow motion berlebihan.

Tujuan:

```text
total estimated visual duration
≈
estimated voice-over duration
```

Tetapi jangan memaksakan durasi dengan cara merusak alur.

Jika perbedaan durasi terlalu besar, backend boleh meminta Gemini melakukan revisi storyboard.

---

# 14. Jumlah Klip

Tidak menggunakan jumlah klip tetap.

Jumlah klip mengikuti kepadatan cerita.

Contoh untuk bagian 1:

```text
voice_over:
± 170 kata

clips:
10–15 clip
```

Bagian aksi dapat memiliki lebih banyak clip.

Bagian dialog atau adegan tenang dapat memiliki lebih sedikit clip.

---

# 15. Aturan Pemilihan Clip

Gemini harus:

1. Memprioritaskan kejadian penting.
2. Menghindari terlalu banyak clip yang menunjukkan hal sama.
3. Mengikuti kronologi film.
4. Menggunakan timestamp subtitle sebagai anchor.
5. Tidak membuat timestamp di luar durasi subtitle.
6. Memilih momen yang mendukung kalimat voice-over.
7. Menghindari pengulangan timestamp kecuali memang diperlukan.
8. Memakai clip pendek dan dinamis.
9. Menyimpan `src <= 3 detik`.
10. Menggunakan transform hanya jika membantu ritme.

---

# 16. Sinkronisasi Voice-over dan Visual

Setiap bagian memiliki:

```json
{
  "visual": "...",
  "voice_over": "...",
  "clips": []
}
```

Urutannya harus sesuai.

Contoh:

```text
Voice-over:
"Sakamoto dulunya adalah pembunuh paling ditakuti..."

Visual:
walk
weapon-aim
confront
silent-stare
```

Kemudian:

```text
Voice-over:
"Namun setelah jatuh cinta, dia memilih meninggalkan dunia hitam..."

Visual:
photo-memory
embrace
child
walk
```

Jadi visual mengikuti narasi, bukan sekadar daftar clip acak.

---

# 17. Schema `storyboard.json`

Gunakan struktur:

```json
{
  "project": {
    "title": "",
    "source_subtitle": "film.srt",
    "target_duration_minutes": 15,
    "estimated_voiceover_seconds": 900,
    "estimated_word_count": 2250
  },
  "sections": [
    {
      "section_id": 1,
      "visual": "Deskripsi visual bagian.",
      "voice_over": "Naskah narasi bagian ini.",
      "clips": [
        {
          "clip_id": 1,
          "beat": "walk",
          "start": "00:00:40",
          "src": 2.0,
          "trx": "baref",
          "out": 2.0
        },
        {
          "clip_id": 2,
          "beat": "silent-stare",
          "start": "00:00:50",
          "src": 1.6,
          "trx": "fz12",
          "out": 1.2
        }
      ],
      "clip_count": 2,
      "total_clip_duration": 3.2
    }
  ],
  "summary": {
    "total_sections": 1,
    "total_clips": 2,
    "total_clip_duration": 3.2
  }
}
```

---

# 18. Output Markdown

Selain JSON, generate:

```text
storyboard.md
```

Format harus mudah dibaca manusia dan mirip format kerja manual.

Contoh:

```markdown
# Storytelling Script

## Bagian 1

### Visual

Sakamoto menjalani kehidupan ganda...

### Daftar Klip

| beat | start | src | trx | out |
|------|-------|-----|-----|-----|
| walk | 00:00:40 | 2.0s | baref | 2.0s |
| silent-stare | 00:00:50 | 1.6s | fz12 | 1.2s |

### Naskah Voice-over

Dulu ada pembunuh bayaran legendaris...

**Jumlah klip bagian ini:** 2

**Total durasi klip bagian ini:** 00:03.2
```

---

# 19. Output Tambahan

Simpan juga:

```text
story_map.json
storyboard.json
storyboard.md
voiceover.txt
```

### `story_map.json`

Data analisis cerita internal.

### `storyboard.json`

Data utama untuk Plan 2.

### `storyboard.md`

Versi yang dibaca/edit manusia.

### `voiceover.txt`

Hanya naskah voice-over tanpa tabel.

Contoh:

```text
Dulu ada pembunuh bayaran legendaris...
...
```

---

# 20. Struktur Project

Rekomendasi:

```text
project/
│
├── backend/
│   ├── app/
│   │   ├── main.py
│   │   │
│   │   ├── api/
│   │   │   └── routes.py
│   │   │
│   │   ├── schemas/
│   │   │   ├── subtitle.py
│   │   │   └── storyboard.py
│   │   │
│   │   ├── services/
│   │   │   ├── srt_parser.py
│   │   │   ├── story_analyzer.py
│   │   │   ├── script_generator.py
│   │   │   ├── storyboard_generator.py
│   │   │   └── duration_validator.py
│   │   │
│   │   └── providers/
│   │       └── gemini.py
│   │
│   └── requirements.txt
│
├── frontend/
│   ├── src/
│   │   ├── components/
│   │   ├── pages/
│   │   ├── services/
│   │   └── types/
│   │
│   └── package.json
│
├── projects/
│
├── .env
├── .env.example
└── PLAN1.md
```

---

# 21. API Endpoint

Minimal:

### Generate

```http
POST /api/generate
```

Input:

```text
multipart/form-data

subtitle: film.srt
target_duration: 15
```

Response:

```json
{
  "success": true,
  "project_id": "abc123",
  "storyboard": {}
}
```

### Download Markdown

```http
GET /api/projects/{id}/storyboard.md
```

### Download JSON

```http
GET /api/projects/{id}/storyboard.json
```

### Download Voice-over

```http
GET /api/projects/{id}/voiceover.txt
```

---

# 22. Gemini Prompt Rules

Prompt harus mengandung aturan penting:

```text
Kamu adalah penulis storytelling film dan storyboard planner.

Input utama adalah subtitle film dengan timestamp.

Tugas:
1. Pahami keseluruhan alur cerita.
2. Identifikasi karakter utama.
3. Identifikasi konflik.
4. Identifikasi kejadian penting.
5. Susun ulang menjadi storytelling yang padat.
6. Jangan mengubah kronologi.
7. Jangan mengarang kejadian yang tidak didukung subtitle.
8. Gunakan Bahasa Indonesia.
9. Gunakan gaya cepat, dramatis, emosional dan tegang.
10. Buat voice-over sesuai target durasi.
11. Buat visual plan untuk setiap bagian.
12. Buat clip blueprint untuk setiap bagian.
13. Semua src maksimal 3 detik.
14. start harus berasal dari timeline subtitle.
15. trx harus menggunakan kode transform yang diperbolehkan.
16. out harus dihitung berdasarkan transform.
17. Output harus JSON valid.
```

---

# 23. Validasi Setelah Gemini

Backend wajib melakukan validation.

## Validasi SRT

```text
- file extension .srt
- subtitle berhasil diparse
- timestamp valid
- subtitle tidak kosong
```

## Validasi Storyboard

```text
- sections tidak kosong
- voice_over tidak kosong
- clips tidak kosong
- start valid
- start berada dalam timeline subtitle
- src > 0
- src <= 3.0
- trx valid
- out > 0
```

## Validasi Durasi

Hitung:

```text
estimated_voiceover_duration
total_clip_duration
```

Jika terlalu jauh:

```text
Gemini revision
```

Maksimal jumlah retry misalnya:

```text
2 kali
```

Jika tetap tidak memenuhi:

```text
tampilkan hasil dengan warning
```

Jangan melakukan infinite retry.

---

# 24. Error Handling

Jika Gemini gagal:

```text
Gagal membuat naskah.
Silakan coba lagi.
```

Jika JSON Gemini invalid:

```text
Parse JSON gagal.
Lakukan structured retry.
```

Jika subtitle terlalu panjang:

```text
Subtitle terlalu panjang untuk satu request.
Gunakan chunking otomatis.
```

Jika API key belum tersedia:

```text
Gemini API Key belum dikonfigurasi.
```

---

# 25. Chunking Subtitle

Film bisa memiliki subtitle sangat panjang.

Jangan selalu mengirim seluruh SRT sebagai satu prompt besar.

Gunakan strategi:

```text
SRT
 ↓
Parse
 ↓
Timeline chunks
 ↓
Story analysis global
 ↓
Compressed story map
 ↓
Generate final script
```

Story map digunakan sebagai konteks supaya hasil antar-chunk tetap konsisten.

Jangan memotong cerita hanya berdasarkan jumlah karakter tanpa memperhatikan batas scene/timestamp.

---

# 26. Prinsip Anti-Halusinasi

Karena input hanya subtitle dan **belum ada video**, AI tidak boleh mengklaim bahwa visual tertentu sudah pasti ada pada timestamp.

Contoh:

Jangan:

```text
00:05:32 pasti menunjukkan Sakamoto sedang memegang pistol.
```

Gunakan konsep:

```text
candidate source timestamp
```

dan:

```text
visual_hint
```

Contoh internal:

```json
{
  "beat": "weapon-aim",
  "start": "00:05:32",
  "visual_hint": "adegan konfrontasi yang berkaitan dengan ancaman",
  "verification": "required"
}
```

Plan 2 nantinya bertugas memverifikasi timestamp tersebut terhadap video asli.

---

# 27. Kontrak Dengan Plan 2

**Plan 2 tidak boleh bergantung pada format output yang berubah-ubah.**

File utama:

```text
storyboard.json
```

Plan 2 nanti menerima:

```text
movie.mp4
+
storyboard.json
```

Kemudian:

```text
storyboard.json
       │
       ▼
timestamp locator
       │
       ▼
video cutter
       │
       ▼
FFmpeg
       │
       ├── baref
       ├── freeze
       ├── slow 65%
       ├── slow 50%
       └── slow 35%
```

Plan 2 menghasilkan video/clip sequence berdasarkan blueprint tersebut.

**Jangan mengimplementasikan proses video cutting di Plan 1.**

---

# 28. MVP Plan 1

Versi pertama cukup memiliki:

### Input

- `.srt`
- target durasi

### Process

- parse SRT
- Gemini story analysis
- Gemini script generation
- storyboard generation
- duration validation

### Output

- `storyboard.json`
- `storyboard.md`
- `voiceover.txt`

### UI

- upload SRT
- input durasi
- generate
- preview hasil
- download output

Belum perlu:

- video upload
- FFmpeg
- video preview
- TTS
- voice cloning
- background music
- rendering video
- automatic clip cutting

---

# 29. Definition of Done

Plan 1 dianggap selesai jika:

- [ ] User bisa upload `.srt`
- [ ] User bisa menentukan target durasi
- [ ] Subtitle berhasil diparse
- [ ] Gemini memahami alur cerita
- [ ] Gemini menghasilkan storytelling Bahasa Indonesia
- [ ] Panjang naskah mengikuti target durasi
- [ ] Cerita tetap kronologis
- [ ] Tidak menambahkan kejadian yang tidak didukung subtitle
- [ ] Story dibagi menjadi beberapa bagian
- [ ] Setiap bagian mempunyai visual description
- [ ] Setiap bagian mempunyai voice-over
- [ ] Setiap bagian mempunyai daftar clip
- [ ] Setiap clip memiliki `beat`
- [ ] Setiap clip memiliki `start`
- [ ] `src` maksimal 3 detik
- [ ] Setiap clip memiliki `trx`
- [ ] Setiap clip memiliki `out`
- [ ] Total durasi dihitung otomatis
- [ ] `storyboard.json` valid
- [ ] `storyboard.md` mudah dibaca
- [ ] `voiceover.txt` tersedia
- [ ] Output siap digunakan sebagai input Plan 2

---

# 30. Batasan Plan 1

Plan 1 **TIDAK**:

- memotong video
- mencari file video
- melakukan scene detection pada video
- memeriksa frame video
- melakukan FFmpeg processing
- membuat audio
- melakukan TTS
- mencampur musik
- merender video final

Semua pekerjaan tersebut masuk **Plan 2** atau tahap berikutnya.

---

# 31. Hasil Akhir yang Diinginkan

User cukup melakukan:

```text
1. Pilih film.srt
2. Masukkan: 15 menit
3. Klik Generate Naskah
```

Sistem menghasilkan:

```text
story_map.json
storyboard.json
storyboard.md
voiceover.txt
```

Contoh alur akhirnya:

```text
FILM.SRT
   │
   ▼
┌─────────────────────┐
│   STORY ANALYSIS    │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│ STORYTELLING SCRIPT │
└──────────┬──────────┘
           ▼
┌─────────────────────┐
│  CLIP BLUEPRINT     │
│                     │
│ beat                 │
│ start                │
│ src                  │
│ trx                  │
│ out                  │
└──────────┬──────────┘
           ▼
    storyboard.json
           │
           ▼
       PLAN 2
```

**Fokus Plan 1 hanya sampai `storyboard.json` dan naskah storytelling selesai.**

---

# 32. Revisi Sinkronisasi Audio-Visual

Plan 1 wajib membagi naskah menjadi `segments` kecil berdasarkan satu gagasan atau perubahan visual. Segment menjadi penghubung antara narasi dan visual.

Plan 1 tidak boleh menganggap estimasi `150 kata/menit` sebagai durasi final. Estimasi tersebut hanya digunakan untuk perencanaan panjang naskah. Durasi final ditentukan setelah TTS dibuat di Plan 2.

Struktur segment minimal:

```json
{
  "segment_id": 1,
  "text": "Awalnya, pria itu memiliki tubuh yang sangat gemuk.",
  "visual_cue": "fat-character",
  "clips": [
    {
      "beat": "fat-character",
      "start": "00:02:10",
      "src": 2.5,
      "trx": "baref",
      "out": null,
      "verification": "required"
    }
  ]
}
```

Aturan baru:

1. Satu segment berisi satu gagasan visual yang jelas.
2. Jika narasi berpindah dari kondisi A ke kondisi B, buat segment berbeda.
3. Setiap segment memiliki `text`, `visual_cue`, dan satu atau lebih kandidat clip.
4. `src` setiap clip tetap lebih besar dari 0 dan maksimal 3 detik.
5. `out` boleh lebih dari 3 detik, tetapi nilai finalnya ditentukan Plan 2 berdasarkan durasi TTS.
6. Satu segment boleh memiliki beberapa clip yang relevan.
7. Plan 1 boleh memberi `suggested_trx`, tetapi Plan 2 berhak memilih kombinasi transform terbaik untuk memenuhi durasi audio.
8. Timestamp tetap merupakan kandidat sumber dari timeline subtitle, bukan jaminan visual.
9. Jangan mengisi kekurangan durasi dengan klip yang tidak berhubungan dengan narasi.

Output `storyboard.json` tetap menjadi kontrak Plan 2, dengan tambahan:

```json
{
  "segments": [
    {
      "segment_id": 1,
      "text": "...",
      "visual_cue": "fat-character",
      "clips": []
    }
  ]
}
```

Format lama `sections[].voice_over` tetap boleh dipertahankan untuk kompatibilitas, tetapi Plan 2 wajib menggunakan `segments` jika tersedia.

---

# 33. TTS Preflight Plan 1

Sebelum Plan 1 dinyatakan selesai, aplikasi menjalankan TTS per segment untuk mendapatkan durasi audio nyata. Tahap ini tidak memotong video.

```text
storyboard segments
    -> TTS per segment
    -> ukur audio_duration
    -> simpan segment_XXXX.mp3
    -> gabungkan voiceover.mp3
    -> simpan metadata timing ke storyboard.json
```

Setiap segment setelah preflight memiliki:

```json
{
  "audio_file": "tts_segments/segment_0001.mp3",
  "audio_duration": 3.42,
  "visual_duration": 3.42,
  "sync_status": "tts_ready"
}
```

Durasi TTS aktual menjadi sumber kebenaran Plan 2. Estimasi WPM hanya digunakan sebelum TTS untuk mengarahkan panjang naskah.

Jika TTS gagal, project tidak boleh dianggap fully synchronized. UI harus menampilkan error/warning dan user dapat menjalankan preflight ulang.

Plan 2 akan dibuat terpisah setelah format Plan 1 ini sudah stabil.
