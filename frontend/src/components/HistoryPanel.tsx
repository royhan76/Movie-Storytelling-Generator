import { useEffect, useState } from "react"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card"
import { History, Loader2, RefreshCw } from "lucide-react"
import type { ProjectSummary } from "@/types/storyboard"
import { listProjects } from "@/services/api"

/** Format project_id (20261003-233816-ea6bf9) jadi waktu yang enak dibaca. */
function formatProjectTime(id: string): string {
  const m = /^(\d{4})(\d{2})(\d{2})-(\d{2})(\d{2})(\d{2})/.exec(id)
  if (!m) return id
  // Groups: 1=YYYY 2=MM 3=DD 4=HH 5=mm 6=ss. Cetak hari/bulan/tahun,
  // bukan tahun/bulan/hari -- urutan Groups bukan urutan tampilan.
  const [, year, month, day, hour, minute] = m
  return `${day}/${month}/${year} ${hour}:${minute}`
}

type Props = {
  /** Dipanggil saat user memilih project dari riwayat. */
  onOpen: (projectId: string) => void
  /** Project yang sedang ditampilkan, biar bisa ditandai. */
  currentId?: string
  /** Project yang sedang di-fetch dari server (tampil spinner). */
  openingId?: string
}

export function HistoryPanel({ onOpen, currentId, openingId = "" }: Props) {
  const [projects, setProjects] = useState<ProjectSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState("")

  async function refresh() {
    setLoading(true)
    setError("")
    try {
      setProjects(await listProjects())
    } catch (e) {
      setError(e instanceof Error ? e.message : "Gagal memuat riwayat.")
    } finally {
      setLoading(false)
    }
  }

  // Muat sekali saat panel pertama kali dibuka.
  useEffect(() => {
    void refresh()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <Card>
      <CardHeader className="gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <CardTitle className="flex items-center gap-2">
            <History className="h-5 w-5" aria-hidden />
            Riwayat Project
          </CardTitle>
          <CardDescription>
            {projects.length > 0
              ? `${projects.length} project tersimpan. Klik untuk membuka lagi.`
              : "Belum ada project tersimpan."}
          </CardDescription>
        </div>
        <Button
          type="button"
          variant="outline"
          onClick={() => void refresh()}
          disabled={loading}
        >
          {loading ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" aria-hidden />
          ) : (
            <RefreshCw className="mr-2 h-4 w-4" aria-hidden />
          )}
          Muat ulang
        </Button>
      </CardHeader>

      <CardContent>
        {error && (
          <p role="alert" className="rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </p>
        )}

        {!error && projects.length === 0 && !loading && (
          <p className="rounded-md border border-dashed border-slate-300 px-3 py-6 text-center text-sm text-slate-500">
            Riwayat kosong. Generate naskah pertama dulu.
          </p>
        )}

        {projects.length > 0 && (
          <ul className="divide-y divide-slate-100">
            {projects.map((p) => {
              const isCurrent = p.project_id === currentId
              const label = p.title || p.source_subtitle || "(tanpa judul)"
              return (
                <li key={p.project_id}>
                  <button
                    type="button"
                    onClick={() => onOpen(p.project_id)}
                    aria-current={isCurrent ? "true" : undefined}
                    className={`flex w-full items-center justify-between gap-4 px-2 py-3 text-left transition hover:bg-slate-50 ${
                      isCurrent ? "bg-slate-50" : ""
                    }`}
                  >
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-slate-900">
                        {label}
                        {isCurrent && (
                          <span className="ml-2 rounded bg-slate-900 px-1.5 py-0.5 text-[10px] font-normal uppercase tracking-wide text-white">
                            dibuka
                          </span>
                        )}
                      </span>
                      <span className="mt-0.5 block truncate text-xs text-slate-500">
                        {formatProjectTime(p.project_id)}
                        {p.target_duration_minutes ? ` · target ${p.target_duration_minutes} mnt` : ""}
                        {p.total_sections ? ` · ${p.total_sections} bagian` : ""}
                        {p.total_clips ? ` · ${p.total_clips} klip` : ""}
                      </span>
                    </span>
                    {openingId === p.project_id && (
                      <Loader2 className="h-4 w-4 shrink-0 animate-spin text-slate-400" aria-hidden />
                    )}
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </CardContent>
    </Card>
  )
}