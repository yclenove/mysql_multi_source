import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { call, isOk, extractMsg } from '@/api/plugin'

export type AppMode = 'master_mode' | 'replica_mode' | 'unknown'
export type UiView = 'landing' | 'wizard_master' | 'wizard_replica' | 'dashboard' | 'diagnose' | 'expert'

export interface MasterSetup {
  configured: boolean
  configured_at: string
  repl_user: string
  health_ok: boolean
}

export const useEnvStore = defineStore('env', () => {
  const loading = ref(false)
  const savedMode = ref<AppMode>('unknown')
  const suggestedMode = ref<AppMode>('unknown')
  const mysqlVersion = ref('')
  const gtidEnabled = ref(false)
  const gtidMode = ref('')
  const tools = ref<Record<string, any>>({})
  const counts = ref({ sources: 0, running_sources: 0, bootstrap_tasks: 0, pending_tasks: 0 })
  const pluginVersion = ref('')
  const currentView = ref<UiView>('landing')
  const expertMode = ref(false)
  const masterSetup = ref<MasterSetup | null>(null)
  const serverIp = ref('')
  const mysqlPort = ref(3306)

  const activeMode = computed<AppMode>(() => savedMode.value)
  const isReplica = computed(() => savedMode.value === 'replica_mode')
  const isMaster = computed(() => savedMode.value === 'master_mode')
  const hasSources = computed(() => counts.value.sources > 0)
  const masterConfigured = computed(() => masterSetup.value?.configured === true)

  async function detectEnv() {
    loading.value = true
    try {
      const res = await call('wizard_detect_env')
      if (isOk(res)) {
        const d = extractMsg(res)
        savedMode.value = d.saved_mode || 'unknown'
        suggestedMode.value = d.suggested_mode || 'unknown'
        mysqlVersion.value = d.mysql_version || ''
        gtidEnabled.value = d.gtid?.enabled ?? false
        gtidMode.value = d.gtid?.mode ?? ''
        tools.value = d.tools || {}
        counts.value = d.counts || counts.value
        pluginVersion.value = d.plugin_version || ''
        masterSetup.value = d.master_setup || null
        serverIp.value = d.server_ip || ''
        mysqlPort.value = d.mysql_port || 3306
      }
    } finally {
      loading.value = false
    }
  }

  async function setMode(mode: AppMode) {
    const res = await call('set_running_mode', { mode })
    if (isOk(res)) {
      savedMode.value = mode
    }
    return res
  }

  function navigate(view: UiView) {
    currentView.value = view
  }

  function toggleExpert() {
    expertMode.value = !expertMode.value
    if (expertMode.value) {
      currentView.value = 'expert'
    } else {
      currentView.value = 'landing'
    }
  }

  return {
    loading, savedMode, suggestedMode, mysqlVersion, gtidEnabled, gtidMode,
    tools, counts, pluginVersion, currentView, expertMode, masterSetup,
    serverIp, mysqlPort,
    activeMode, isReplica, isMaster, hasSources, masterConfigured,
    detectEnv, setMode, navigate, toggleExpert,
  }
})
