import { useState } from "react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { Clipboard, Download, FileText, FolderTree, History, Loader2, Map as MapIcon } from "lucide-react"
import { HistoryPanel } from "@/components/HistoryPanel"
import type { Clip, DownloadFile, JobStatus, Storyboard } from "@/types/storyboard"
import {
  downloadProjectFile,
  formatSeconds,
  GenerateError,
  generateStoryboard,
  loadProject,
  summarizeStoryboard,
  triggerBrowserDownload,
} from "@/services/api"

const DOWNLOADS: Array<{ file: DownloadFile; label: string; icon: typeof FileText }> = [
  { file: "storyboard.md", label: "Markdown", icon: FileText },
  { file: "storyboard.json", label: "JSON", icon: Download },
  { file: "voiceover.txt", label: "Voice-over", icon: Clipboard },
  { file: "story_map.json", label: "Story Map", icon: MapIcon },
]

const STAGE_LABEL: Record<JobStatus["stage"], string> = {
  queued: "Menunggu",
  parse: "Baca subtitle",
  analysis: "Analisis cerita",
  script: "Tulis naskah + klip",
  validate: "Validasi",
  done: "Selesai",
  error: "Gagal",
}

function Stats({ sb }: { sb: Storyboard }) {
  const s = summarizeStoryboard(sb)
  const items = [
    { label: "Bagian", value: String(s.sections) },
    { label: "Klip", value: String(s.clips) },
    { label: "Durasi klip", value: formatSeconds(s.clipDuration) },
    { label: "Durasi VO", value: formatSeconds(s.voDuration) },
    { label: "Kata", value: s.words.toLocaleString("id-ID") },
    { label: "Target", value: `${s.target} mnt` },
  ]
  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-6">
      {items.map((it) => (
        <div key={it.label} className="rounded-md border border-slate-200 bg-slate-50 p-3">
          <p className="text-xs uppercase tracking-wide text-slate-500">{it.label}</p>
          <p className="mt-1 text-lg font-semibold tabular-nums text-slate-900">{it.value}</p>
        </div>
      ))}
    </div>
  )
}

function ProgressPanel({ job }: { job: JobStatus }) {
  const stages: JobStatus["stage"][] = ["parse", "analysis", "script", "validate"]
  const currentIdx = stages.indexOf(job.stage)

  return (
    <div className="space-y-4 rounded-md border border-slate-200 bg-slate-50 p-4">
      <div className="flex items-center justify-between">
        <span className="text-sm font-medium text-slate-700">{STAGE_LABEL[job.stage]}</span>
        <span className="text-sm tabular-nums text-slate-500">{job.progress}%</span>
      </div>

      <div className="h-2 overflow-hidden rounded-full bg-slate-200">
        <div
          className="h-full rounded-full bg-slate-900 transition-all duration-500"
          style={{ width: `${Math.max(job.progress, 2)}%` }}
        />
      </div>

      <ol className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {stages.map((stage, i) => (
          <li
            key={stage}
            className={
              i < currentIdx
                ? "text-slate-500"
                : i === currentIdx
                  ? "font-semibold text-slate-900"
                  : "text-slate-400"
            }
          >
            {i < currentIdx ? "✓ " : i === currentIdx ? "▸ " : ""}
            {STAGE_LABEL[stage]}
          </li>
        ))}
      </ol>

      <p className="text-sm text-slate-600">{job.message}</p>

      <p className="text-xs text-slate-400">
        Film panjang butuh 10-20 menit karena Gemini dipanggil beberapa kali. Jangan tutup tab ini.
      </p>
    </div>
  )
}

function ClipRow({ clip }: { clip: Clip }) {
  return (
    <tr className="border-b border-slate-100 last:border-0">
      <td className="px-2 py-1.5 font-mono text-xs">{clip.beat}</td>
      <td className="px-2 py-1.5 font-mono text-xs tabular-nums">{clip.start}</td>
      <td className="px-2 py-1.5 text-xs tabular-nums">{clip.src.toFixed(1)}s</td>
      <td className="px-2 py-1.5">
        <span className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[11px]">{clip.trx}</span>
      </td>
      <td className="px-2 py-1.5 text-xs tabular-nums">{clip.out.toFixed(1)}s</td>
    </tr>
  )
}

export default function App() {
  const [srtFile, setSrtFile] = useState<File | null>(null)
  const [target, setTarget] = useState(15)
  const [job, setJob] = useState<JobStatus | null>(null)
  const [downloading, setDownloading] = useState<DownloadFile | null>(null)
  const [projectId, setProjectId] = useState("")
  const [sb, setSb] = useState<Storyboard | null>(null)
  const [error, setError] = useState("")
  const [errorTrace, setErrorTrace] = useState("")
  const [showHistory, setShowHistory] = useState(false)
  const [openingId, setOpeningId] = useState("")

  const busy = job !== null && (job.status === "queued" || job.stage !== "done")

  async function handleGenerate() {
    if (!srtFile) {
      setError("Pilih file .srt terlebih dahulu.")
      return
    }
    if (target < 1 || target > 180) {
      setError("Target durasi harus antara 1 sampai 180 menit.")
      return
    }
    setError("")
    setSb(null)
    setProjectId("")
    setJob({
      job_id: "",
      status: "queued",
      stage: "queued",
      message: "Mengirim file...",
      progress: 0,
    })

    try {
      const data = await generateStoryboard(srtFile, target, setJob)
      setProjectId(data.project_id)
      setSb(data.storyboard)
      setErrorTrace("")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal membuat naskah. Silakan coba lagi.")
      // Simpan traceback server supaya penyebabnya bisa dibaca di UI, bukan
      // cuma pesan singkat yang tidak memberi petunjuk.
      setErrorTrace(e instanceof GenerateError ? (e.traceback ?? "") : "")
      setJob(null)
    }
  }

  async function handleOpenHistory(projectId: string) {
    setOpeningId(projectId)
    setError("")
    setErrorTrace("")
    try {
      const data = await loadProject(projectId)
      setProjectId(projectId)
      setSb(data)
      setJob(null)
      // Scroll ke hasil supaya user langsung lihat yang dia buka.
      window.scrollTo({ top: 0, behavior: "smooth" })
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal membuka project.")
    } finally {
      setOpeningId("")
    }
  }

  async function handleDownload(file: DownloadFile) {
    if (!projectId) return
    setDownloading(file)
    try {
      const { blob, filename } = await downloadProjectFile(projectId, file)
      triggerBrowserDownload(blob, filename)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal mengunduh file.")
    } finally {
      setDownloading(null)
    }
  }

  async function handleCopyVoiceover() {
    if (!sb) return
    const text = sb.sections.map((s) => s.voice_over).join("\n\n")
    try {
      await navigator.clipboard.writeText(text)
      setError("")
    } catch {
      setError("Browser menolak akses clipboard. Gunakan tombol Voice-over.")
    }
  }

  return (
    <div className="mx-auto min-h-screen max-w-6xl px-4 py-10">
      <header className="mb-8 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight text-slate-900">
            Movie Storytelling Generator
          </h1>
          <p className="mt-1 text-sm text-slate-500">
            SRT menjadi naskah voice-over Bahasa Indonesia plus blueprint klip untuk Plan 2.
          </p>
        </div>
        <Button
          type="button"
          variant={showHistory ? "default" : "outline"}
          onClick={() => setShowHistory((v) => !v)}
          aria-expanded={showHistory}
        >
          <History className="mr-2 h-4 w-4" aria-hidden />
          {showHistory ? "Tutup riwayat" : "Lihat riwayat"}
        </Button>
      </header>

      {showHistory && (
        <div className="mb-8">
          <HistoryPanel onOpen={handleOpenHistory} currentId={projectId} openingId={openingId} />
        </div>
      )}

      <Card className="mb-8">
        <CardHeader>
          <CardTitle>Input</CardTitle>
          <CardDescription>Upload subtitle film dan tentukan target durasi narasi.</CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          <div className="grid gap-5 sm:grid-cols-2">
            <div className="space-y-2">
              <Label htmlFor="srt">Subtitle (.srt)</Label>
              <Input
                id="srt"
                type="file"
                accept=".srt,text/plain"
                disabled={busy}
                onChange={(e) => setSrtFile(e.target.files?.[0] ?? null)}
              />
              {srtFile && (
                <p className="text-xs text-slate-500">
                  {srtFile.name} ({(srtFile.size / 1024).toFixed(1)} KB)
                </p>
              )}
            </div>
            <div className="space-y-2">
              <Label htmlFor="dur">Target Durasi (menit)</Label>
              <Input
                id="dur"
                type="number"
                min={1}
                max={180}
                value={target}
                disabled={busy}
                onChange={(e) => setTarget(Number(e.target.value))}
              />
              <p className="text-xs text-slate-500">
                Estimasi {Math.round(target * 150).toLocaleString("id-ID")} kata pada 150 kata/menit.
              </p>
            </div>
          </div>

          <Button size="lg" onClick={handleGenerate} disabled={busy}>
            {busy ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                Sedang memproses...
              </>
            ) : (
              <>
                <FolderTree className="h-4 w-4" />
                Generate Naskah
              </>
            )}
          </Button>

          {error && (
            <div role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <p>{error}</p>
              {errorTrace && (
                <details className="mt-2">
                  <summary className="cursor-pointer text-xs font-medium text-red-800">
                    Lihat detail error (traceback)
                  </summary>
                  <pre className="mt-1.5 max-h-64 overflow-auto whitespace-pre-wrap break-all rounded bg-white/70 p-2 font-mono text-[11px] leading-relaxed text-red-900">
                    {errorTrace}
                  </pre>
                  <p className="mt-1.5 text-xs text-red-800">
                    Jejak lengkap ada di{" "}
                    <span className="font-mono">backend/debug_log/</span> — respons mentah
                    Gemini tersimpan di sana per panggilan.
                  </p>
                </details>
              )}
            </div>
          )}
        </CardContent>
      </Card>

      {job && !sb && <ProgressPanel job={job} />}

      {sb && (
        <Card>
          <CardHeader className="gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <CardTitle>{sb.project.title || sb.project.source_subtitle}</CardTitle>
              <CardDescription>
                Project {projectId} · {sb.sections.length} bagian
                {sb.report?.elapsed_sec ? ` · ${(sb.report.elapsed_sec / 60).toFixed(1)} menit` : ""}
              </CardDescription>
            </div>
            <div className="flex flex-wrap gap-2">
              <Button variant="outline" size="sm" onClick={handleCopyVoiceover}>
                <Clipboard className="h-3.5 w-3.5" />
                Copy
              </Button>
              {DOWNLOADS.map(({ file, label, icon: Icon }) => (
                <Button
                  key={file}
                  variant="outline"
                  size="sm"
                  disabled={downloading !== null}
                  onClick={() => handleDownload(file)}
                >
                  {downloading === file ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Icon className="h-3.5 w-3.5" />
                  )}
                  {label}
                </Button>
              ))}
            </div>
          </CardHeader>

          <CardContent className="space-y-6">
            {sb.report?.warnings && sb.report.warnings.length > 0 && (
              <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
                {sb.report.warnings.join(" ")}
              </div>
            )}

            <Stats sb={sb} />

            {sb.sections.map((section) => (
              <section key={section.section_id} className="rounded-md border border-slate-200">
                <div className="flex items-center justify-between border-b border-slate-100 px-4 py-2.5">
                  <h2 className="text-sm font-semibold text-slate-900">Bagian {section.section_id}</h2>
                  <span className="text-xs text-slate-500">
                    {section.clip_count} klip · {formatSeconds(section.total_clip_duration)}
                  </span>
                </div>

                <div className="space-y-4 p-4">
                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">Visual</p>
                    <p className="text-sm text-slate-700">{section.visual}</p>
                  </div>

                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Naskah Voice-over
                    </p>
                    <p className="whitespace-pre-wrap text-sm leading-relaxed text-slate-800">
                      {section.voice_over}
                    </p>
                  </div>

                  <div className="space-y-1.5">
                    <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                      Daftar Klip
                    </p>
                    <div className="overflow-x-auto rounded border border-slate-200">
                      <table className="w-full text-left">
                        <thead className="bg-slate-50 text-xs text-slate-600">
                          <tr>
                            <th className="px-2 py-1.5 font-medium">beat</th>
                            <th className="px-2 py-1.5 font-medium">start</th>
                            <th className="px-2 py-1.5 font-medium">src</th>
                            <th className="px-2 py-1.5 font-medium">trx</th>
                            <th className="px-2 py-1.5 font-medium">out</th>
                          </tr>
                        </thead>
                        <tbody>
                          {section.clips.map((clip) => (
                            <ClipRow key={clip.clip_id} clip={clip} />
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                </div>
              </section>
            ))}

            <p className="text-xs text-slate-500">
              Timestamp klip adalah kandidat lokasi pada film, bukan jaminan frame visual tertentu.
              Verifikasi dilakukan di Plan 2.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  )
}