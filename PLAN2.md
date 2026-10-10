# PLAN 2 — Video → Cut, Transform, Sequence & Render

## 1. Tujuan

Plan 2 adalah tahap produksi video setelah **Plan 1 selesai**.

Input utama:

```text
movie.mp4 / movie.mkv / movie.mov / movie.avi / format video lain
+
storyboard.json dari Plan 1
```

Tugas Plan 2:

1. Memasukkan video film.
2. Membaca `storyboard.json`.
3. Mengambil bagian video berdasarkan `start` dan `src`.
4. Menerapkan transform sesuai `trx`.
5. Menyesuaikan durasi hasil dengan `out`.
6. Menyusun semua clip sesuai urutan storyboard.
7. Menggabungkan seluruh clip.
8. Menghapus/mengabaikan audio sumber.
9. Generate dan gabungkan TTS voice-over per segment, lalu render menjadi satu video final yang sinkron dengan audio.

> **Fokus utama Plan 2 adalah proses video editing otomatis.**
>
> Pada revisi sinkronisasi, Plan 2 membuat TTS voice-over per segment. Musik dan audio mixing lanjutan tetap berada di tahap berikutnya.

Catatan: aturan lama yang menyebut output tanpa audio hanya berlaku untuk preview intermediate. Aturan sinkronisasi pada Bagian 45 adalah aturan final dan menjadi prioritas.

---

# 2. Input

## 2.1 Video Film

User memilih file:

```text
movie.mp4
```

Format yang sebaiknya didukung:

```text
.mp4
.mkv
.mov
.avi
.webm
.m4v
```

Namun proses internal menggunakan **FFmpeg** sehingga format yang didukung bergantung pada codec FFmpeg.

## 2.2 Storyboard

User memasukkan:

```text
storyboard.json
```

File ini berasal dari Plan 1.

Contoh:

```json
{
  "project": {
    "target_duration_minutes": 15
  },
  "sections": [
    {
      "section_id": 1,
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
      ]
    }
  ]
}
```

---

# 3. Alur Utama

```text
VIDEO FILM
     │
     ▼
STORYBOARD.JSON
     │
     ▼
VALIDASI INPUT
     │
     ▼
BACA SEMUA CLIP
     │
     ▼
AMBIL SOURCE CLIP
     │
     ▼
APPLY TRANSFORM
     │
     ├── baref
     ├── fz12
     ├── s65
     ├── s50
     └── s35
     │
     ▼
NORMALISASI VIDEO
     │
     ▼
SUSUN SESUAI URUTAN
     │
     ▼
CONCAT / MERGE
     │
     ▼
REMOVE AUDIO
     │
     ▼
FINAL RENDER
     │
     ▼
storytelling.mp4
```

---

# 4. Teknologi

## Backend

Gunakan:

- Python
- FastAPI
- FFmpeg

## Video Processing

**FFmpeg menjadi engine utama.**

Jangan menggunakan MoviePy sebagai engine utama.

Alasan:

- FFmpeg lebih cepat.
- Stabil untuk processing video panjang.
- Lebih cocok untuk pipeline production.
- Filter video lebih lengkap.
- Mudah melakukan trim, setpts, tpad, freeze, concat dan audio removal.

Python bertugas sebagai:

```text
orchestrator
```

FFmpeg bertugas sebagai:

```text
video processing engine
```

---

# 5. UI

Halaman Plan 2:

```text
┌──────────────────────────────────────────────┐
│          VIDEO STORYTELLING RENDERER         │
├──────────────────────────────────────────────┤
│                                              │
│  Video Film                                  │
│  [ movie.mp4                         ]       │
│                                              │
│  Storyboard                                  │
│  [ storyboard.json                   ]       │
│                                              │
│  Output                                      │
│  [ storytelling_no_audio.mp4          ]       │
│                                              │
│           [ MULAI PROSES ]                   │
│                                              │
└──────────────────────────────────────────────┘
```

Setelah proses dimulai tampilkan progress:

```text
Memeriksa video...
████████░░░░░░░░ 40%

Memotong clip 18/57...
████████████░░░░ 70%

Menerapkan transform...
██████████████░░ 85%

Menggabungkan video...
████████████████ 100%
```

---

# 6. Tahap 1 — Validasi Video

Sebelum melakukan cutting, ambil metadata menggunakan FFprobe.

Informasi yang dibutuhkan:

```text
duration
width
height
fps
video codec
audio codec
pixel format
```

Contoh:

```json
{
  "duration": 7200.25,
  "width": 1920,
  "height": 1080,
  "fps": 24,
  "video_codec": "h264",
  "audio_codec": "aac"
}
```

Validasi:

```text
video duration > 0
video stream tersedia
resolution valid
fps valid
```

Audio tidak wajib ada.

Karena output Plan 2 memang:

```text
VIDEO ONLY
```

---

# 7. Tahap 2 — Validasi Storyboard

Backend membaca semua section dan clip.

Setiap clip harus memiliki:

```text
clip_id
beat
start
src
trx
out
```

Validasi:

```text
src > 0
src <= 3.0
out > 0
start >= 00:00:00
start + src <= video duration
trx valid
```

Transform yang diperbolehkan:

```text
baref
fz12
s65
s50
s35
```

Jika Plan 1 menggunakan transform tambahan di masa depan, registry transform dapat diperluas.

---

# 8. Tahap 3 — Convert Timestamp

Storyboard menggunakan:

```text
HH:MM:SS
```

atau:

```text
HH:MM:SS.mmm
```

Backend mengubahnya menjadi detik.

Contoh:

```text
00:05:32
```

menjadi:

```text
332.0
```

Kemudian:

```text
start = 332.0
src   = 2.0
```

berarti mengambil:

```text
332.0 → 334.0
```

---

# 9. Tahap 4 — Source Clip Extraction

Setiap clip diproses berdasarkan:

```text
start
+
src
```

Contoh:

```json
{
  "start": "00:05:32",
  "src": 2.0
}
```

FFmpeg mengambil:

```text
00:05:32 → 00:05:34
```

Setiap clip menjadi file sementara.

Contoh:

```text
work/
├── clip_0001_source.mp4
├── clip_0002_source.mp4
├── clip_0003_source.mp4
└── ...
```

---

# 10. Prinsip Penting: Jangan Langsung Menimpa Source

Setiap clip sebaiknya memiliki pipeline:

```text
SOURCE
  │
  ▼
TRIM
  │
  ▼
TRANSFORM
  │
  ▼
NORMALIZE
  │
  ▼
READY CLIP
```

Contoh:

```text
clip_001_source.mp4
        ↓
clip_001_processed.mp4
```

Dengan begitu debugging jauh lebih mudah.

---

# 11. Transform `baref`

`baref` berarti:

```text
normal speed
```

Tidak ada slow motion dan tidak ada freeze.

Contoh:

```text
src = 2.0
trx = baref
out = 2.0
```

Pipeline:

```text
trim 2 detik
↓
normal speed
↓
output 2 detik
```

---

# 12. Transform Slow Motion

Transform:

```text
s65
s50
s35
```

Artinya speed video:

```text
s65 = 65%
s50 = 50%
s35 = 35%
```

Formula:

```text
output_duration = src_duration / speed
```

Contoh:

```text
src = 2.0
speed = 0.50

2.0 / 0.50 = 4.0 detik
```

Contoh:

```text
src = 2.0
speed = 0.65

2.0 / 0.65 = 3.08 detik
```

Contoh:

```text
src = 2.0
speed = 0.35

2.0 / 0.35 = 5.71 detik
```

---

# 13. Slow Motion Dengan FFmpeg

Gunakan filter:

```text
setpts
```

Contoh konsep:

```text
s50
=
setpts=2.0*PTS
```

Untuk:

```text
s65
=
setpts=(1/0.65)*PTS
```

Untuk:

```text
s35
=
setpts=(1/0.35)*PTS
```

Audio tidak perlu diproses karena output akhir memang tanpa audio.

---

# 14. Transform Freeze Frame

Contoh:

```text
trx = fz12
```

Artinya frame tertentu dibuat diam/freeze untuk durasi sekitar:

```text
1.2 detik
```

Konsep:

```text
source clip
     │
     ▼
ambil frame
     │
     ▼
freeze frame
     │
     ▼
1.2 detik
```

Implementasi dapat menggunakan FFmpeg:

```text
tpad
```

atau metode:

```text
select frame
+
loop
+
setpts
```

Pilih metode yang paling stabil setelah testing.

---

# 15. Freeze Tidak Harus Menghasilkan Durasi Source

Contoh:

```text
src = 1.6
trx = fz12
out = 1.2
```

Artinya:

```text
ambil source pada timestamp
↓
ambil frame
↓
buat frame diam
↓
durasi final 1.2 detik
```

Jadi:

```text
src
```

adalah data source dari film.

Sedangkan:

```text
out
```

adalah durasi clip setelah transform.

---

# 16. Gunakan `out` Sebagai Target Final

Walaupun durasi transform bisa dihitung dari `src` dan `trx`, nilai:

```text
out
```

tetap menjadi target final.

Contoh:

```json
{
  "src": 2.0,
  "trx": "s65",
  "out": 3.1
}
```

Render harus menghasilkan sekitar:

```text
3.1 detik
```

Jika hasil FFmpeg:

```text
3.08 detik
```

normalisasi dapat menyesuaikannya ke target.

---

# 17. Rounding Durasi

Jangan mengejar presisi milidetik secara berlebihan.

Gunakan toleransi kecil:

```text
±0.05 detik
```

Contoh:

```text
target = 3.10
actual = 3.08
```

masih dianggap valid.

Namun jika:

```text
target = 3.10
actual = 2.50
```

harus dianggap gagal.

---

# 18. Normalisasi Semua Clip

Ini adalah bagian sangat penting.

Source film bisa memiliki:

```text
24 fps
25 fps
30 fps
60 fps
```

Resolusi juga bisa berbeda.

Sebelum concat, semua clip harus dinormalisasi.

Target default:

```text
1920x1080
30 fps
yuv420p
```

Namun sebaiknya resolution output mengikuti video sumber.

Misalnya source:

```text
1920x1080
```

maka output:

```text
1920x1080
```

Jika source:

```text
1280x720
```

maka output:

```text
1280x720
```

Jangan melakukan upscale tanpa kebutuhan.

---

# 19. Target Video Profile

Default:

```text
H.264
MP4
yuv420p
30 fps
```

Contoh:

```text
codec: libx264
pixel format: yuv420p
container: mp4
```

Preset dapat dibuat configurable:

```env
VIDEO_PRESET=medium
VIDEO_CRF=18
```

Untuk preview bisa:

```text
CRF 23
```

Untuk final:

```text
CRF 18–20
```

---

# 20. Urutan Clip

Urutan **harus mengikuti urutan `storyboard.json`**.

Contoh:

```text
section 1
  clip 1
  clip 2
  clip 3

section 2
  clip 4
  clip 5
  clip 6
```

Bukan mengikuti timestamp asli film.

Ini sangat penting.

Contoh:

```text
clip 1 → source 00:10:20
clip 2 → source 00:03:12
clip 3 → source 00:15:40
```

Tetap output:

```text
clip 1
↓
clip 2
↓
clip 3
```

Karena storyboard sudah menentukan urutan storytelling.

---

# 21. Clip Tidak Harus Berurutan Dalam Film

Jangan pernah melakukan:

```text
sort by start timestamp
```

karena itu akan merusak storytelling.

Yang benar:

```text
sort by storyboard order
```

---

# 22. Penomoran Internal

Setelah storyboard dibaca, buat sequence:

```text
0001
0002
0003
0004
...
```

Contoh:

```text
clip_0001.mp4
clip_0002.mp4
clip_0003.mp4
clip_0004.mp4
```

Nomor ini berdasarkan urutan output.

---

# 23. Struktur Working Directory

Contoh:

```text
projects/
└── project_001/
    │
    ├── input/
    │   ├── movie.mp4
    │   └── storyboard.json
    │
    ├── work/
    │   ├── source/
    │   │   ├── clip_0001_source.mp4
    │   │   ├── clip_0002_source.mp4
    │   │   └── ...
    │   │
    │   ├── processed/
    │   │   ├── clip_0001.mp4
    │   │   ├── clip_0002.mp4
    │   │   └── ...
    │   │
    │   └── concat.txt
    │
    └── output/
        └── storytelling_no_audio.mp4
```

---

# 24. Concat

Setelah semua clip selesai:

```text
processed/
   │
   ├── clip_0001.mp4
   ├── clip_0002.mp4
   ├── clip_0003.mp4
   └── clip_0004.mp4
             │
             ▼
         concat.txt
             │
             ▼
           FFmpeg
             │
             ▼
storytelling_no_audio.mp4
```

Contoh:

```text
file 'clip_0001.mp4'
file 'clip_0002.mp4'
file 'clip_0003.mp4'
file 'clip_0004.mp4'
```

---

# 25. Audio

Ini adalah aturan wajib Plan 2:

## Output tidak boleh mempunyai audio.

Jika video sumber memiliki:

```text
video + audio
```

maka audio harus dibuang.

Gunakan konsep:

```text
-an
```

pada final render.

Hasil:

```text
storytelling_no_audio.mp4
```

hanya memiliki:

```text
Video Stream
```

Tidak ada:

```text
Audio Stream
```

---

# 26. Tidak Ada Audio Processing

Plan 2 tidak melakukan:

- TTS
- Voice-over
- Music
- SFX
- Audio mixing
- Volume adjustment
- Audio ducking
- Audio normalization

Semua itu sengaja dikeluarkan dari scope.

User akan menambahkan audio pada tahap berikutnya.

---

# 27. Render Final

Setelah concat:

```text
processed clips
      ↓
concat
      ↓
remove audio
      ↓
encode
      ↓
final mp4
```

Output:

```text
storytelling_no_audio.mp4
```

---

# 28. Validasi Final Video

Setelah render selesai, jalankan FFprobe lagi.

Validasi:

```text
file exists
video stream exists
audio stream DOES NOT exist
duration > 0
resolution valid
codec valid
```

Contoh hasil yang diharapkan:

```text
Video: H.264
Resolution: 1920x1080
FPS: 30
Duration: 14:52
Audio: NONE
```

---

# 29. Duration Final

Hitung:

```text
final_duration
```

dari video sebenarnya.

Bandingkan dengan:

```text
sum(out)
```

Harus sangat dekat.

Contoh:

```text
Storyboard:
00:14:52.30

Rendered:
00:14:52.28
```

Valid.

Jika perbedaannya terlalu besar:

```text
WARNING
```

Jangan langsung dianggap sukses.

---

# 30. Error Handling

## Video tidak valid

```text
Video tidak dapat dibaca.
```

## Storyboard invalid

```text
Storyboard JSON tidak valid.
```

## Timestamp di luar video

Contoh:

```text
start = 01:35:20
video duration = 01:20:00
```

Maka:

```text
ERROR:
Clip 17 berada di luar durasi video.
```

Jangan diam-diam mengambil timestamp lain.

## Source terlalu pendek

Jika:

```text
start + src > video duration
```

maka:

```text
ERROR
```

atau jika hanya selisih kecil, beri opsi:

```text
Clamp source duration
```

Tetapi default sebaiknya **error**, karena Plan 1 seharusnya menghasilkan timestamp yang valid.

---

# 31. Missing Clip

Jika FFmpeg gagal memotong satu clip:

```text
clip_0012
```

jangan langsung membuat video final tanpa clip tersebut.

Default:

```text
FAIL SAFE
```

Artinya:

```text
1 clip gagal
↓
render dihentikan
↓
user diberi informasi
```

Contoh:

```text
Render gagal.

Clip:
0012

Beat:
weapon-fire

Start:
00:07:09

Reason:
FFmpeg gagal membaca source segment.
```

---

# 32. Resume / Retry

Karena render film panjang bisa memakan waktu, jangan selalu mengulang dari awal.

Simpan status:

```json
{
  "clip_0001": "done",
  "clip_0002": "done",
  "clip_0003": "done",
  "clip_0004": "failed"
}
```

Jika retry:

```text
clip_0001 → skip
clip_0002 → skip
clip_0003 → skip
clip_0004 → render ulang
```

Ini menghemat waktu.

---

# 33. Progress Tracking

Backend harus mengirim progress ke frontend.

Contoh:

```text
Preparing       5%
Extracting     30%
Transforming   55%
Normalizing    70%
Concatenating  90%
Finalizing     98%
Completed     100%
```

Untuk MVP dapat menggunakan polling:

```text
GET /api/projects/{id}/status
```

Jika nanti dibutuhkan realtime, dapat diganti WebSocket/SSE.

---

# 34. API

## Upload Video + Storyboard

```http
POST /api/render
```

Input:

```text
video
storyboard
```

Response:

```json
{
  "success": true,
  "project_id": "project_001"
}
```

## Status

```http
GET /api/render/{project_id}/status
```

Response:

```json
{
  "status": "processing",
  "progress": 67,
  "current_clip": 38,
  "total_clips": 57
}
```

## Download

```http
GET /api/render/{project_id}/download
```

Output:

```text
storytelling_no_audio.mp4
```

---

# 35. Pipeline Internal

Backend membuat pipeline seperti:

```text
load_storyboard()
        ↓
probe_video()
        ↓
validate_storyboard()
        ↓
create_project_workspace()
        ↓
for each clip:
        ↓
extract_source()
        ↓
apply_transform()
        ↓
normalize_clip()
        ↓
validate_clip()
        ↓
save_processed_clip()
        ↓
generate_concat_file()
        ↓
concat()
        ↓
remove_audio()
        ↓
final_encode()
        ↓
probe_final()
        ↓
validate_final()
        ↓
DONE
```

---

# 36. Jangan Menggunakan AI Untuk Memotong Video

Plan 2 tidak perlu Gemini untuk proses cutting.

Gemini sudah bekerja pada Plan 1.

Plan 2 harus bersifat deterministic:

```text
storyboard
+
video
=
hasil video
```

Jika storyboard mengatakan:

```text
start = 00:07:09
src = 2.0
trx = s65
```

maka engine melakukan tepat itu.

Tidak boleh AI tiba-tiba memilih timestamp lain.

---

# 37. Hubungan Plan 1 dan Plan 2

```text
                  PLAN 1
        ┌────────────────────────┐
        │        film.srt        │
        └───────────┬────────────┘
                    │
                    ▼
             Gemini Analysis
                    │
                    ▼
          Storytelling Script
                    │
                    ▼
             storyboard.json
                    │
                    │
                    ▼
                  PLAN 2
        ┌────────────────────────┐
        │        movie.mp4       │
        └───────────┬────────────┘
                    │
                    ▼
            Read storyboard
                    │
                    ▼
             Cut source clips
                    │
                    ▼
              Apply transform
                    │
                    ▼
              Normalize clips
                    │
                    ▼
             Order by storyboard
                    │
                    ▼
                  Concat
                    │
                    ▼
               Remove audio
                    │
                    ▼
                 Render
                    │
                    ▼
       storytelling_no_audio.mp4
```

---

# 38. Contoh Nyata

Storyboard:

```text
| beat          | start    | src | trx  | out |
|---------------|----------|-----|------|-----|
| walk          | 00:00:40 | 2.0 | baref| 2.0 |
| silent-stare  | 00:00:50 | 1.6 | fz12 | 1.2 |
| weapon-fire   | 00:07:09 | 2.0 | s65  | 3.1 |
| corpse        | 00:09:36 | 2.0 | s50  | 4.0 |
```

Engine melakukan:

```text
1. movie.mp4
      ↓
2. cut 00:00:40 → 00:00:42
      ↓
   normal
      ↓
   clip_0001 = 2.0s

3. cut 00:00:50 → 00:00:51.6
      ↓
   freeze
      ↓
   clip_0002 = 1.2s

4. cut 00:07:09 → 00:07:11
      ↓
   slow 65%
      ↓
   clip_0003 ≈ 3.1s

5. cut 00:09:36 → 00:09:38
      ↓
   slow 50%
      ↓
   clip_0004 = 4.0s

6. susun:
   0001 → 0002 → 0003 → 0004

7. remove audio

8. render:
   storytelling_no_audio.mp4
```

---

# 39. Prinsip Editing

Prioritas utama:

```text
1. Ketepatan timestamp
2. Ketepatan durasi
3. Ketepatan transform
4. Urutan storyboard
5. Konsistensi format video
6. Kecepatan render
```

Jangan mengorbankan timestamp hanya untuk mempercantik hasil.

Plan 2 adalah **executor** dari storyboard.

---

# 40. MVP

Versi pertama cukup:

### Input

- Video
- `storyboard.json`

### Processing

- FFprobe
- Trim
- Slow motion
- Freeze frame
- Normal speed
- Normalize
- Concat
- Remove audio
- Final render

### Output

```text
storytelling_no_audio.mp4
```

### UI

- Video upload
- Storyboard upload
- Start render
- Progress
- Download result

---

# 41. Belum Masuk Scope

Jangan implementasikan dulu:

- Voice-over
- TTS
- AI voice
- Background music
- Sound effect
- Subtitle burn-in
- Automatic visual search
- Gemini video analysis
- Scene detection AI
- Face recognition
- Auto crop
- Auto reframing
- YouTube upload
- Thumbnail generation

Fokus hanya pada:

```text
CUT
+
TRANSFORM
+
ORDER
+
RENDER
```

---

# 42. Definition of Done

Plan 2 selesai jika:

- [ ] User dapat memasukkan video film.
- [ ] User dapat memasukkan `storyboard.json`.
- [ ] FFprobe berhasil membaca video.
- [ ] Storyboard berhasil divalidasi.
- [ ] Semua timestamp dapat dikonversi.
- [ ] Source clip dapat dipotong.
- [ ] `src <= 3 detik` tervalidasi.
- [ ] `baref` bekerja.
- [ ] `s65` bekerja.
- [ ] `s50` bekerja.
- [ ] `s35` bekerja.
- [ ] Freeze frame bekerja.
- [ ] `out` sesuai target.
- [ ] Semua clip dinormalisasi.
- [ ] Clip disusun berdasarkan urutan storyboard.
- [ ] Clip tidak diurutkan berdasarkan timestamp film.
- [ ] Semua clip berhasil di-concat.
- [ ] Audio sumber dibuang.
- [ ] Final video hanya mempunyai video stream.
- [ ] Durasi final divalidasi.
- [ ] Output MP4 berhasil dibuat.
- [ ] Progress dapat dilihat user.
- [ ] Error clip dapat diketahui.
- [ ] Render dapat di-retry tanpa mengulang clip yang sudah berhasil.

---

# 43. Hasil Akhir

User melakukan:

```text
1. Upload movie.mp4
2. Upload storyboard.json
3. Klik "Mulai Render"
```

Sistem:

```text
movie.mp4
    +
storyboard.json
    │
    ▼
CUT
    │
    ▼
TRANSFORM
    │
    ▼
NORMALIZE
    │
    ▼
ORDER
    │
    ▼
CONCAT
    │
    ▼
REMOVE AUDIO
    │
    ▼
RENDER
    │
    ▼
storytelling_no_audio.mp4
```

Output akhir adalah **video storytelling tanpa audio**, siap masuk ke tahap berikutnya untuk diberi voice-over, musik, atau audio lainnya.

---

# 44. Prinsip Final Plan 2

**Plan 1 menentukan apa yang harus dibuat.**

```text
storyboard.json
```

**Plan 2 mengeksekusi blueprint tersebut.**

```text
movie.mp4
+
storyboard.json
```

menjadi:

```text
storytelling_no_audio.mp4
```

Tidak ada keputusan cerita baru di Plan 2.

Tidak ada AI yang mengubah urutan.

Tidak ada pemilihan timestamp baru.

Plan 2 harus menjadi **video editing engine yang deterministic dan dapat diprediksi**.

---

# 45. Revisi Sinkronisasi Audio-Visual

Bagian ini menggantikan aturan lama yang menyatakan Plan 2 tidak membuat audio. Pada versi sinkronisasi baru, Plan 2 wajib membuat TTS voice-over per `segment`, mengukur durasi audio sebenarnya, lalu menjadikan durasi tersebut sebagai timeline utama video.

## Prinsip Utama

Urutan proses yang benar:

```text
storyboard.json
       |
       v
baca segments
       |
       v
generate TTS per segment
       |
       v
ukur durasi audio sebenarnya
       |
       v
rencanakan clip visual sampai durasi audio terpenuhi
       |
       v
potong source clip
       |
       v
apply transform dan normalize
       |
       v
gabungkan video segment + audio segment
       |
       v
validasi sinkronisasi
       |
       v
render final
```

Estimasi WPM dari Plan 1 hanya digunakan untuk perencanaan naskah. Estimasi tersebut tidak boleh dipakai sebagai timing final.

## Segment Audio

Setiap segment Plan 1 harus menghasilkan satu audio TTS terpisah:

```text
segment_0001.wav
segment_0002.wav
segment_0003.wav
```

Metadata runtime:

```json
{
  "segment_id": 1,
  "text": "Awalnya, pria itu memiliki tubuh yang sangat gemuk.",
  "audio_file": "segment_0001.wav",
  "audio_duration": 3.42,
  "visual_cue": "fat-character",
  "clips": []
}
```

Durasi `audio_duration` adalah sumber kebenaran untuk segment tersebut.

## Aturan Durasi Clip

Aturan source tetap wajib:

```text
0 < src <= 3.0 detik
```

Namun `out` boleh lebih dari 3 detik karena:

- slow motion;
- freeze frame;
- beberapa clip relevan dalam satu segment;
- clip pendukung yang masih sesuai dengan `visual_cue`.

Contoh narasi berdurasi 5 detik:

```text
clip 1: src 2.0, trx s65,  out 3.08
clip 2: src 1.0, trx fz12, out 1.20
clip 3: src 0.7, trx baref, out 0.70
total visual: 4.98 detik
```

Nilai tersebut valid jika durasi audio adalah 5.00 detik dengan toleransi 0.05 detik.

## Visual Matching

Plan 2 tidak boleh mengisi kekurangan durasi menggunakan clip acak. Prioritas pengisian durasi:

1. Tambahkan clip lain yang memiliki `visual_cue` sama atau masih berkaitan.
2. Gunakan `baref`.
3. Gunakan slow motion secara terbatas.
4. Gunakan freeze frame untuk momen penekanan.
5. Gunakan reaction shot atau establishing shot yang relevan.

Jika narasi berpindah dari karakter gemuk ke karakter kurus, segment dan clip juga harus berpindah pada batas audio yang sama. Clip karakter kurus tidak boleh muncul ketika audio masih menjelaskan karakter gemuk.

## Sync Planner

Untuk setiap segment, backend menghitung:

```text
target = audio_duration
actual = jumlah seluruh out clip
error = abs(target - actual)
```

Segment valid jika:

```text
error <= 0.05 detik
```

Jika `actual` kurang, backend memilih clip relevan tambahan atau transform yang sesuai. Jika `actual` berlebih, backend memangkas clip terakhir atau menyesuaikan transform. Jika tidak dapat memenuhi durasi tanpa merusak makna, proses berhenti dengan warning/error yang jelas.

## Timeline Final

Timeline video dan audio dibentuk dari segment yang sama:

```text
00:00.00 - 00:03.42  audio segment 1 + visual fat-character
00:03.42 - 00:07.60  audio segment 2 + visual thin-character
```

Video tidak boleh dirender sebagai satu rangkaian clip terlebih dahulu lalu audio ditambahkan tanpa perhitungan ulang. Setiap batas pergantian visual harus mengikuti batas segment audio.

## Validasi Audio-Visual

Setelah render, backend wajib memeriksa:

- jumlah segment audio dan video sama;
- durasi video tiap segment sesuai durasi audio;
- total durasi audio dan video berada dalam toleransi;
- urutan segment tidak berubah;
- visual cue setiap clip sesuai segment;
- tidak ada clip dengan `src > 3.0`;
- audio narration terdengar pada bagian video yang benar.

Output final sekarang:

```text
storytelling.mp4
```

Jika diperlukan preview tanpa audio, backend boleh menyimpan:

```text
storytelling_no_audio.mp4
```

Audio TTS final harus berasal dari penggabungan seluruh audio segment dengan urutan storyboard yang sama.

---

# 46. Konsumsi TTS Preflight Plan 1

Jika `storyboard.json` memiliki `segments[].audio_file` dan `segments[].audio_duration`, Plan 2 wajib memakai file `voiceover.mp3` dan audio segment yang sudah dibuat Plan 1.

Plan 2 tidak boleh membuat ulang TTS karena hasil generate ulang dapat memiliki durasi berbeda dan menggeser batas visual.

Durasi visual dihitung per segment:

```text
target segment = segment.audio_duration
actual segment = jumlah out klip segment
scale = target segment / actual segment
```

`scale` hanya digunakan untuk menyesuaikan hasil transform video. Urutan segment tetap sama dan setiap segment hanya boleh memakai klip yang relevan dengan `visual_cue`-nya.

Jika preflight tidak tersedia, Plan 2 boleh memakai fallback TTS lama per section, tetapi status render harus diberi warning bahwa sinkronisasi segment belum presisi.
