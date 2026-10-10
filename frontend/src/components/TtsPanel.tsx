import { useEffect, useState } from "react"
import {
  generateTtsVoiceover,
  getFinalVideoUrl,
  getTtsVoices,
  getVoiceoverAudioUrl,
  mergeAudioVideo,
  type TtsVoice,
} from "@/services/api"
import { Button } from "./ui/button"
import { Card } from "./ui/card"

interface TtsPanelProps {
  projectId: string
  hasVideo?: boolean
}

export function TtsPanel({ projectId, hasVideo = false }: TtsPanelProps) {
  const [voices, setVoices] = useState<TtsVoice[]>([])
  const [selectedVoice, setSelectedVoice] = useState<string>("id-ID-ArdiNeural")
  const [generating, setGenerating] = useState<boolean>(false)
  const [merging, setMerging] = useState<boolean>(false)
  const [audioUrl, setAudioUrl] = useState<string>("")
  const [finalVideoUrl, setFinalVideoUrl] = useState<string>("")
  const [errorMsg, setErrorMsg] = useState<string>("")
  const [successMsg, setSuccessMsg] = useState<string>("")

  useEffect(() => {
    getTtsVoices()
      .then((items) => {
        setVoices(items)
        if (items.length > 0) {
          setSelectedVoice(items[0].code)
        }
      })
      .catch(() => {})
  }, [])

  const handleGenerateTts = async () => {
    if (!projectId) {
      setErrorMsg("Pilih atau buat project terlebih dahulu.")
      return
    }

    setErrorMsg("")
    setSuccessMsg("")
    setGenerating(true)
    setAudioUrl("")

    try {
      await generateTtsVoiceover(projectId, selectedVoice)
      const url = `${getVoiceoverAudioUrl(projectId)}?t=${Date.now()}`
      setAudioUrl(url)
      setSuccessMsg("Voiceover MP3 berhasil digenerate!")
    } catch (err) {
      setErrorMsg((err as Error).message || "Gagal menghasilkan TTS.")
    } finally {
      setGenerating(false)
    }
  }

  const handleMergeAudioVideo = async () => {
    if (!projectId) return

    setErrorMsg("")
    setSuccessMsg("")
    setMerging(true)
    setFinalVideoUrl("")

    try {
      await mergeAudioVideo(projectId)
      const url = `${getFinalVideoUrl(projectId)}?t=${Date.now()}`
      setFinalVideoUrl(url)
      setSuccessMsg("Video final dengan Voice Over berhasil digabungkan!")
    } catch (err) {
      setErrorMsg((err as Error).message || "Gagal menggabungkan video dan audio.")
    } finally {
      setMerging(false)
    }
  }

  return (
    <Card className="p-6 space-y-5 bg-slate-900 border-slate-800 text-slate-100 mt-6">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <h3 className="text-lg font-bold text-amber-400 flex items-center gap-2">
            🎙️ Voice Over Text-to-Speech (TTS)
          </h3>
          <p className="text-sm text-slate-400 mt-1">
            Ubah naskah voiceover menjadi suara narasi MP3 berkualitas tinggi (Microsoft Edge Speech AI).
          </p>
        </div>
      </div>

      <div className="grid gap-4 sm:grid-cols-3 items-end">
        <div className="sm:col-span-2 space-y-1">
          <label className="block text-sm font-medium text-slate-300">
            Pilih Suara Narator / Voice Model
          </label>
          <select
            value={selectedVoice}
            onChange={(e) => setSelectedVoice(e.target.value)}
            disabled={generating}
            className="w-full bg-slate-800 border border-slate-700 rounded-md p-2.5 text-sm text-slate-200 focus:outline-none focus:ring-2 focus:ring-amber-500"
          >
            {voices.map((v) => (
              <option key={v.code} value={v.code}>
                {v.name}
              </option>
            ))}
          </select>
        </div>

        <Button
          onClick={handleGenerateTts}
          disabled={generating || !projectId}
          className="bg-amber-500 hover:bg-amber-600 text-slate-950 font-semibold py-2.5"
        >
          {generating ? "MEMPROSES SUARA..." : "GENERATE VOICE OVER (TTS)"}
        </Button>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/80 border border-red-800 rounded-md text-red-300 text-sm">
          ⚠️ {errorMsg}
        </div>
      )}

      {successMsg && (
        <div className="p-3 bg-emerald-950/80 border border-emerald-800 rounded-md text-emerald-300 text-sm">
          ✅ {successMsg}
        </div>
      )}

      {/* Audio Player & Download */}
      {audioUrl && (
        <div className="p-4 bg-slate-950/60 border border-slate-800 rounded-lg space-y-3">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <span className="text-sm font-medium text-slate-300">
              🔊 Audio Narasi Voiceover (voiceover.mp3):
            </span>
            <a
              href={audioUrl}
              download="voiceover.mp3"
              target="_blank"
              rel="noreferrer"
              className="text-xs text-amber-400 hover:underline font-semibold"
            >
              ⬇️ Download MP3
            </a>
          </div>

          <audio controls src={audioUrl} className="w-full">
            Browser Anda tidak mendukung player audio HTML5.
          </audio>
        </div>
      )}

      {/* Merge Video + Audio Button */}
      {hasVideo && (
        <div className="pt-4 border-t border-slate-800 flex flex-col sm:flex-row items-center justify-between gap-4">
          <div className="text-xs text-slate-400">
            Sudah mengolah video di Plan 2? Gabungkan video storytelling dengan voiceover MP3!
          </div>

          <Button
            onClick={handleMergeAudioVideo}
            disabled={merging || !audioUrl}
            className="bg-indigo-600 hover:bg-indigo-700 text-white font-semibold"
          >
            {merging ? "MENGGABUNGKAN..." : "🎬 GABUNGKAN VIDEO + VOICE OVER"}
          </Button>
        </div>
      )}

      {/* Final Video Player */}
      {finalVideoUrl && (
        <div className="p-4 bg-slate-950/80 border border-indigo-900 rounded-lg space-y-3 mt-4">
          <h4 className="text-md font-bold text-indigo-300 flex items-center gap-2">
            🎬 Video Storytelling Final Lengkap dengan Audio Voiceover!
          </h4>
          <div className="aspect-video bg-black rounded-lg overflow-hidden border border-slate-700">
            <video controls src={finalVideoUrl} className="w-full h-full object-contain">
              Browser Anda tidak mendukung player video.
            </video>
          </div>
          <div className="flex justify-end">
            <a href={finalVideoUrl} download="final_storytelling.mp4" target="_blank" rel="noreferrer">
              <Button className="bg-indigo-600 hover:bg-indigo-700 text-white font-medium text-sm">
                ⬇️ DOWNLOAD FINAL_STORYTELLING.MP4
              </Button>
            </a>
          </div>
        </div>
      )}
    </Card>
  )
}
