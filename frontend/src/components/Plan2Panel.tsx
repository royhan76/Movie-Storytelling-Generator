import { useEffect, useState } from "react"
import {
  getFinalVideoUrl,
  getProjectVideoUrl,
  getTtsVoices,
  listProjects,
  renderPlan2Video,
  type TtsVoice,
} from "@/services/api"
import type { Plan2JobStatus, ProjectSummary } from "@/types/storyboard"
import { Button } from "./ui/button"
import { Card } from "./ui/card"
import { Input } from "./ui/input"

interface Plan2PanelProps {
  currentProjectId?: string | null
}

export function Plan2Panel({ currentProjectId }: Plan2PanelProps) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [voices, setVoices] = useState<TtsVoice[]>([])
  const [selectedVoice, setSelectedVoice] = useState<string>("id-ID-ArdiNeural")
  const [includeTts, setIncludeTts] = useState<boolean>(true)
  const [selectedProjectId, setSelectedProjectId] = useState<string>(
    currentProjectId || "",
  )
  const [videoFile, setVideoFile] = useState<File | null>(null)
  const [introVideoFile, setIntroVideoFile] = useState<File | null>(null)
  const [videoPath, setVideoPath] = useState<string>("")
  const [rendering, setRendering] = useState<boolean>(false)
  const [progressStatus, setProgressStatus] = useState<Plan2JobStatus | null>(
    null,
  )
  const [errorMsg, setErrorMsg] = useState<string>("")
  const [renderedVideoUrl, setRenderedVideoUrl] = useState<string>("")

  // Load project history & TTS voices
  useEffect(() => {
    listProjects(50)
      .then((items) => {
        setProjects(items)
        if (!selectedProjectId && items.length > 0) {
          setSelectedProjectId(items[0].project_id)
        }
      })
      .catch(() => {})

    getTtsVoices()
      .then((items) => {
        setVoices(items)
        if (items.length > 0) setSelectedVoice(items[0].code)
      })
      .catch(() => {})
  }, [])

  useEffect(() => {
    if (currentProjectId) {
      setSelectedProjectId(currentProjectId)
    }
  }, [currentProjectId])

  const handleStartRender = async () => {
    if (!selectedProjectId) {
      setErrorMsg("Pilih project terlebih dahulu (dari Plan 1).")
      return
    }
    if (!videoFile && !videoPath.trim()) {
      setErrorMsg("Pilih file video film (.mp4, .mkv) atau masukkan jalur file lokal.")
      return
    }

    setErrorMsg("")
    setRendering(true)
    setProgressStatus(null)
    setRenderedVideoUrl("")

    try {
      const finalStatus = await renderPlan2Video(
        selectedProjectId,
        videoFile || undefined,
        introVideoFile || undefined,
        videoPath.trim() || undefined,
        selectedVoice,
        includeTts,
        (status) => setProgressStatus(status),
      )
      const isWithAudio = finalStatus.report?.has_audio ?? includeTts
      const url = isWithAudio
        ? `${getFinalVideoUrl(selectedProjectId)}?t=${Date.now()}`
        : `${getProjectVideoUrl(selectedProjectId)}?t=${Date.now()}`
      setRenderedVideoUrl(url)
      setProgressStatus(finalStatus)
    } catch (err) {
      setErrorMsg((err as Error).message || "Terjadi kesalahan saat render.")
    } finally {
      setRendering(false)
    }
  }

  return (
    <div className="space-y-6 max-w-4xl mx-auto p-4">
      <Card className="p-6 space-y-6 bg-slate-900 border-slate-800 text-slate-100">
        <div>
          <h2 className="text-xl font-bold text-amber-400">
            Plan 2 — Video Cut, Transform & Render Pipeline
          </h2>
          <p className="text-sm text-slate-400 mt-1">
            Memotong video film sesuai timestamps storyboard, menerapkan transform (baref, fz12, s65, s50, s35), dan menghasilkan video final tanpa audio.
          </p>
        </div>

        {/* Form Inputs */}
        <div className="space-y-4">
          {/* Project Selector */}
          <div>
            <label className="block text-sm font-medium text-slate-300 mb-1">
              Pilih Project (Storyboard Plan 1)
            </label>
            <select
              value={selectedProjectId}
              onChange={(e) => setSelectedProjectId(e.target.value)}
              disabled={rendering}
              className="w-full bg-slate-800 border border-slate-700 rounded-md p-2.5 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-amber-500"
            >
              {projects.length === 0 && (
                <option value="">Tidak ada project tersimpan</option>
              )}
              {projects.map((p) => (
                <option key={p.project_id} value={p.project_id}>
                  {p.title ? `${p.title} (${p.project_id})` : p.project_id} — {p.total_clips} klip
                </option>
              ))}
            </select>
          </div>

          {/* Video File Input */}
          <div className="space-y-3">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                File Video Film (Upload browser)
              </label>
              <Input
                type="file"
                accept="video/*,.mp4,.mkv,.mov,.avi,.webm"
                onChange={(e) => {
                  if (e.target.files?.[0]) {
                    setVideoFile(e.target.files[0])
                  }
                }}
                disabled={rendering}
                className="bg-slate-800 border-slate-700 text-slate-200 cursor-pointer"
              />
            </div>

            <div className="text-xs text-slate-500 text-center uppercase tracking-wider font-semibold">
              — atau —
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1">
                Jalur File Video Lokal (Diskon file besar multi-GB)
              </label>
              <Input
                type="text"
                placeholder="C:\Videos\movie.mp4 atau /path/to/movie.mp4"
                value={videoPath}
                onChange={(e) => setVideoPath(e.target.value)}
                disabled={rendering}
                className="bg-slate-800 border-slate-700 text-slate-200"
              />
            </div>

            <div className="pt-3 border-t border-slate-800">
              <label className="block text-sm font-medium text-amber-300 mb-1">
                Video Intro (opsional — diputar setelah Hook, sebelum Main Video)
              </label>
              <Input
                type="file"
                accept="video/*,.mp4,.mkv,.mov,.avi,.webm"
                onChange={(e) => setIntroVideoFile(e.target.files?.[0] || null)}
                disabled={rendering}
                className="bg-slate-800 border-slate-700 text-slate-200 cursor-pointer"
              />
              <p className="text-xs text-slate-500 mt-1">
                Audio intro dimatikan dan voice-over diberi jeda sesuai durasi intro.
              </p>
            </div>
          </div>

          {/* TTS Voice Selector (1-Click Integration) */}
          <div className="space-y-2 pt-3 border-t border-slate-800">
            <div className="flex items-center justify-between">
              <label className="text-sm font-medium text-slate-300">
                Suara Narator Voice Over (TTS)
              </label>
              <label className="flex items-center gap-2 text-xs text-amber-400 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={includeTts}
                  onChange={(e) => setIncludeTts(e.target.checked)}
                  disabled={rendering}
                  className="rounded border-slate-700 bg-slate-800 text-amber-500 focus:ring-amber-500"
                />
                <span>Sertakan Voice Over langsung (1-Click)</span>
              </label>
            </div>

            {includeTts && (
              <select
                value={selectedVoice}
                onChange={(e) => setSelectedVoice(e.target.value)}
                disabled={rendering}
                className="w-full bg-slate-800 border border-slate-700 rounded-md p-2.5 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-amber-500"
              >
                {voices.map((v) => (
                  <option key={v.code} value={v.code}>
                    {v.name}
                  </option>
                ))}
              </select>
            )}
          </div>

          {errorMsg && (
            <div className="p-3 bg-red-950/80 border border-red-800 rounded-md text-red-300 text-sm">
              ⚠️ {errorMsg}
            </div>
          )}

          {/* Submit Button */}
          <Button
            onClick={handleStartRender}
            disabled={rendering || !selectedProjectId}
            className="w-full bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold py-3 text-base shadow-lg"
          >
            {rendering
              ? "MENGOLAH VIDEO & VOICE OVER..."
              : includeTts
              ? "🎬 MULAI RENDER (VIDEO + VOICE OVER SEKALIGUS)"
              : "🎬 MULAI RENDER VIDEO ONLY (TANPA SUARA)"}
          </Button>
        </div>

        {/* Progress Display */}
        {progressStatus && (
          <div className="space-y-3 bg-slate-950/60 p-4 rounded-lg border border-slate-800">
            <div className="flex justify-between items-center text-sm font-medium">
              <span className="text-amber-400">{progressStatus.message}</span>
              <span className="text-slate-400">{progressStatus.progress}%</span>
            </div>

            <div className="w-full bg-slate-800 h-3 rounded-full overflow-hidden">
              <div
                className="bg-amber-500 h-full transition-all duration-300 ease-out"
                style={{ width: `${progressStatus.progress}%` }}
              />
            </div>

            {progressStatus.detail && (
              <div className="text-xs text-slate-400 flex justify-between">
                <span>
                  Klip: {String(progressStatus.detail.current || 0)} / {String(progressStatus.detail.total || 0)}
                </span>
                <span>Transform: {String(progressStatus.detail.trx || "-")}</span>
              </div>
            )}
          </div>
        )}

        {/* Video Preview & Download */}
        {renderedVideoUrl && (
          <div className="space-y-4 pt-4 border-t border-slate-800">
            <h3 className="text-lg font-semibold text-emerald-400 flex items-center gap-2">
              <span>✅</span> Video Storytelling Selesai Dirender (Lengkap dengan Voice Over Narasi)
            </h3>

            <div className="aspect-video bg-black rounded-lg overflow-hidden border border-slate-700">
              <video
                controls
                src={renderedVideoUrl}
                className="w-full h-full object-contain"
              >
                Browser Anda tidak mendukung player video HTML5.
              </video>
            </div>

            <div className="flex justify-end">
              <a
                href={renderedVideoUrl}
                download="storytelling.mp4"
                target="_blank"
                rel="noreferrer"
              >
                <Button className="bg-emerald-600 hover:bg-emerald-700 text-white font-medium">
                  ⬇️ DOWNLOAD STORYTELLING.MP4
                </Button>
              </a>
            </div>
          </div>
        )}
      </Card>
    </div>
  )
}
