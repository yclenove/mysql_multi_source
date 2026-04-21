import { defineStore } from 'pinia'
import { ref } from 'vue'
import { call, isOk, extractMsg } from '@/api/plugin'

export interface SourceStatus {
  source_id: string
  channel_name: string
  master_host: string
  master_port: number
  repl_user: string
  repl_password_masked: string
  db_mappings: { source_db: string; target_db: string }[]
  status: {
    running: boolean
    io_running: string
    sql_running: string
    seconds_behind: number | null
    last_error: string
  }
  updated_at: number
}

export interface LiveTask {
  task_id: string
  source_id: string
  mode: string
  effective_mode?: string
  status: string
  current_step: string
  progress: number
  retry_count: number
  error?: string
  error_type?: string
  started_at?: number
  last_heartbeat?: number
}

export interface DashMetrics {
  total_sources: number
  running_sources: number
  stopped_sources: number
  error_sources: number
  bootstrap_tasks: number
  bootstrap_done: number
  bootstrap_failed: number
  avg_bootstrap_duration_seconds: number
}

export const useDashboardStore = defineStore('dashboard', () => {
  const sources = ref<SourceStatus[]>([])
  const liveTasks = ref<LiveTask[]>([])
  const metrics = ref<DashMetrics>({
    total_sources: 0, running_sources: 0, stopped_sources: 0, error_sources: 0,
    bootstrap_tasks: 0, bootstrap_done: 0, bootstrap_failed: 0, avg_bootstrap_duration_seconds: 0,
  })
  const mode = ref('replica_mode')
  const loading = ref(false)
  let pollTimer: ReturnType<typeof setInterval> | null = null

  async function refresh() {
    loading.value = true
    try {
      const res = await call('wizard_dashboard_snapshot')
      if (isOk(res)) {
        const d = extractMsg(res)
        sources.value = d.sources || []
        liveTasks.value = d.live_tasks || []
        metrics.value = d.metrics || metrics.value
        mode.value = d.mode || 'replica_mode'
      }
    } finally {
      loading.value = false
    }
  }

  function startPolling(intervalMs = 5000) {
    stopPolling()
    refresh()
    pollTimer = setInterval(refresh, intervalMs)
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer)
      pollTimer = null
    }
  }

  return { sources, liveTasks, metrics, mode, loading, refresh, startPolling, stopPolling }
})
