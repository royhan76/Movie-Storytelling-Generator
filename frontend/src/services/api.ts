import type {
  DownloadFile,
  GenerateResponse,
  JobStatus,
  ProjectSummary,
  Storyboard,
} from "@/types/storyboard"

const API_BASE = import.meta.env.VITE_API_BASE ?? ""

function detailFromResponse(text: string, status: number): string {
  try {
    const parsed = JSON.parse(text) as { detail?: string }
    if (typeof parsed.detail === "string") return parsed.detail
  } catch {
    /* biarkan fallback di bawah */
  }
  if (status === 400) return "File subtitle tidak valid."
  if (status === 503) return "Gemini API Key belum dikonfigurasi."
  if (status === 502) return "Batas kuota Gemini API tercapai (Rate Limit / Quota Exceeded). Silakan tunggu 1-2 menit lalu coba lagi."
  if (status === 413) return "File subtitle terlalu besar."
  return `Terjadi kesalahan (HTTP ${status}).`
}

export class GenerateError extends Error {
  readonly traceback?: string
  readonly debugHint?: string

  constructor(message: string, traceback?: string, debugHint?: string) {
    super(message)
    this.name = "GenerateError"
    this.traceback = traceback
    this.debugHint = debugHint
  }
}

/**
 * Generate lewat job async + polling.
 *
 * Generate SRT panjang (1500+ cue) butuh 15-20 menit karena Gemini dipanggil
 * beberapa kali (analisis per chunk + penulisan + mungkin revisi). Endpoint
 * blocking akan kena timeout HTTP di browser, jadi pakai job + polling.
 */
export async function generateStoryboard(
  file: File,
  targetDurationMinutes: number,
  language: string = "id",
  onProgress: (status: JobStatus) => void,
): Promise<GenerateResponse> {
  const body = new FormData()
  body.append("subtitle", file)
  body.append("target_duration", String(targetDurationMinutes))
  body.append("language", language)

  const startRes = await fetch(`${API_BASE}/api/jobs`, { method: "POST", body })
  if (!startRes.ok) throw new Error(detailFromResponse(await startRes.text(), startRes.status))

  const { job_id: jobId } = (await startRes.json()) as { job_id: string }

  // Poll tiap 3 detik; job panjang tidak punya batas waktu atas.
  const POLL_MS = 3000
  for (;;) {
    await new Promise((resolve) => setTimeout(resolve, POLL_MS))

    const res = await fetch(`${API_BASE}/api/jobs/${jobId}`)
    if (!res.ok) throw new Error(detailFromResponse(await res.text(), res.status))
    const status = (await res.json()) as JobStatus
    onProgress(status)

    if (status.status === "error") {
      // Bawa traceback server supaya penyebabnya bisa dibaca langsung di UI,
      // bukan cuma "Gagal: AttributeError" tanpa konteks.
      throw new GenerateError(
        status.message || "Gagal membuat naskah.",
        status.traceback,
        status.debug_hint,
      )
    }
    if (status.status === "done" && status.project_id) {
      const sbRes = await fetch(`${API_BASE}/api/projects/${status.project_id}/storyboard.json`)
      if (!sbRes.ok) throw new Error("Storyboard tersimpan tapi gagal dibaca.")
      const storyboard = (await sbRes.json()) as Storyboard
      return { success: true, project_id: status.project_id, storyboard }
    }
  }
}

/** Generate blocking — hanya untuk subtitle pendek (butuh < 2 menit). */
export async function generateStoryboardBlocking(
  file: File,
  targetDurationMinutes: number,
  language: string = "id",
): Promise<GenerateResponse> {
  const body = new FormData()
  body.append("subtitle", file)
  body.append("target_duration", String(targetDurationMinutes))
  body.append("language", language)

  const res = await fetch(`${API_BASE}/api/generate`, { method: "POST", body })
  if (!res.ok) throw new Error(detailFromResponse(await res.text(), res.status))
  return (await res.json()) as GenerateResponse
}

/** Daftar project tersimpan, terbaru dulu (dari GET /api/projects). */
export async function listProjects(limit = 50): Promise<ProjectSummary[]> {
  const res = await fetch(`${API_BASE}/api/projects?limit=${limit}`)
  if (!res.ok) throw new Error(detailFromResponse(await res.text(), res.status))
  const data = (await res.json()) as { projects?: ProjectSummary[] }
  return data.projects ?? []
}

/** Ambil storyboard lengkap satu project supaya bisa langsung ditampilkan. */
export async function loadProject(projectId: string): Promise<Storyboard> {
  const res = await fetch(`${API_BASE}/api/projects/${projectId}/storyboard.json`)
  if (!res.ok) throw new Error(detailFromResponse(await res.text(), res.status))
  return (await res.json()) as Storyboard
}

export async function downloadProjectFile(
  projectId: string,
  file: DownloadFile,
): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`${API_BASE}/api/projects/${projectId}/${file}`)
  if (!res.ok) throw new Error(detailFromResponse(await res.text(), res.status))

  const disposition = res.headers.get("content-disposition") ?? ""
  const match = /filename="?([^";]+)"?/i.exec(disposition)
  const filename = match?.[1] ?? file
  return { blob: await res.blob(), filename }
}

export function triggerBrowserDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = filename
  document.body.appendChild(anchor)
  anchor.click()
  document.body.removeChild(anchor)
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}

export function formatSeconds(value: number): string {
  const total = Math.round(value)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`
}

export function summarizeStoryboard(sb: Storyboard) {
  return {
    sections: sb.summary.total_sections,
    clips: sb.summary.total_clips,
    clipDuration: sb.summary.total_clip_duration,
    voDuration: sb.project.estimated_voiceover_seconds,
    words: sb.project.estimated_word_count,
    target: sb.project.target_duration_minutes,
  }
}