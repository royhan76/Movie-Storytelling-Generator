export type Clip = {
  clip_id: number
  beat: string
  start: string
  src: number
  trx: "baref" | "fz12" | "s65" | "s50" | "s35"
  out: number
  visual_hint?: string
}

export type Section = {
  section_id: number
  visual: string
  voice_over: string
  clips: Clip[]
  clip_count: number
  total_clip_duration: number
}

export type Storyboard = {
  project: {
    title: string
    source_subtitle: string
    target_duration_minutes: number
    estimated_voiceover_seconds: number
    estimated_word_count: number
  }
  sections: Section[]
  summary: {
    total_sections: number
    total_clips: number
    total_clip_duration: number
  }
  report?: {
    cue_count?: number
    attempts?: number
    diff_pct?: number
    ok?: boolean
    elapsed_sec?: number
    warnings?: string[]
  }
}

export type GenerateResponse = {
  success: boolean
  project_id: string
  storyboard: Storyboard
}

/** Status job generate yang dipoll dari /api/jobs/{id}. */
export type JobStatus = {
  job_id: string
  status: "queued" | "done" | "error"
  stage: "queued" | "parse" | "analysis" | "script" | "validate" | "done" | "error"
  message: string
  progress: number
  started_at?: number
  elapsed_sec?: number
  project_id?: string
  source_subtitle?: string
  target_duration?: number
  detail?: Record<string, unknown>
  /** Hanya saat status === "error": traceback penuh dari server. */
  traceback?: string
  /** Petunjuk ke file debug di backend/debug_log/. */
  debug_hint?: string
}

export type DownloadFile = "storyboard.md" | "storyboard.json" | "voiceover.txt" | "story_map.json"

/** Satu entri di daftar project tersimpan (dari GET /api/projects). */
export type ProjectSummary = {
  project_id: string
  title: string
  source_subtitle: string
  target_duration_minutes: number | null
  total_clips: number
  total_sections: number
}