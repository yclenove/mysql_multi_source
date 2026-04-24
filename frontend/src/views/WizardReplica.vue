<script setup lang="ts">
import { ref, reactive, computed, onUnmounted, watch, nextTick } from 'vue'
import {
  NSteps, NStep, NCard, NButton, NSpace, NTag, NInput, NInputNumber,
  NAlert, NIcon, useMessage, NSpin, NResult, NCheckbox,
  NRadioGroup, NRadioButton, NCollapse, NCollapseItem,
  NDescriptions, NDescriptionsItem, NProgress,
} from 'naive-ui'
import { CheckmarkCircleOutline, CloseCircleOutline, ArrowForwardOutline } from '@vicons/ionicons5'
import { call, isOk, getMessage, extractMsg } from '@/api/plugin'
import { useEnvStore } from '@/store/env'
import { scrollPluginTop } from '@/utils/scroll'

const env = useEnvStore()
const msg = useMessage()
const step = ref(1)
const loading = ref(false)
const startingReplication = ref(false)

const inputMode = ref<'profile' | 'manual'>('manual')
const profileText = ref('')
const form = reactive({
  master_host: '',
  master_port: 3306,
  repl_user: '',
  repl_password: '',
})
const profileVerified = ref(false)

async function verifyProfile() {
  if (!profileText.value.trim()) { msg.warning('请粘贴配置单'); return }
  loading.value = true
  try {
    const res = await call('replica_verify_profile', { profile_b64: profileText.value.trim() })
    if (isOk(res)) {
      const d = extractMsg(res)
      if (d.verified && d.payload) {
        form.master_host = d.payload.master_host || ''
        form.master_port = d.payload.master_port || 3306
        form.repl_user = d.payload.repl_user || ''
        form.repl_password = d.payload.repl_password || ''
        profileVerified.value = true
        msg.success('配置单验证通过')
      } else {
        msg.error('配置单格式不正确，请确认是否粘贴完整')
      }
    } else {
      msg.error(getMessage(res) || '验证失败')
    }
  } finally {
    loading.value = false
  }
}

const canProceed1 = computed(() =>
  form.master_host && form.master_port && form.repl_user && form.repl_password,
)

const checks = ref<Record<string, { ok: boolean; reason: string }>>({
  network: { ok: false, reason: '' },
  auth: { ok: false, reason: '' },
  gtid: { ok: false, reason: '' },
})
const allOk = ref(false)

async function runPreflight() {
  loading.value = true
  try {
    const res = await call('wizard_preflight_source', {
      master_host: form.master_host,
      master_port: String(form.master_port),
      repl_user: form.repl_user,
      repl_password: form.repl_password,
    })
    if (isOk(res)) {
      const d = extractMsg(res)
      checks.value = d.checks || checks.value
      allOk.value = d.all_ok || false
    } else {
      msg.error(getMessage(res) || '检查失败')
    }
  } finally {
    loading.value = false
  }
}

interface DbRow {
  name: string; size_mb: number; selected: boolean; target_db: string
}
const dbRows = ref<DbRow[]>([])
const sourceAlias = ref('m1')

async function loadDatabases() {
  loading.value = true
  try {
    const res = await call('wizard_list_master_dbs', {
      master_host: form.master_host,
      master_port: String(form.master_port),
      repl_user: form.repl_user,
      repl_password: form.repl_password,
    })
    if (isOk(res)) {
      const d = extractMsg(res)
      dbRows.value = (d.databases || []).map((db: any) => ({
        name: db.name, size_mb: db.size_mb, selected: false,
        target_db: `${sourceAlias.value}_${db.name}`,
      }))
    } else {
      msg.error(getMessage(res) || '读取库列表失败')
    }
  } finally {
    loading.value = false
  }
}

function refreshTargetNames() {
  dbRows.value.forEach((r) => {
    if (!r.target_db || r.target_db.includes('_' + r.name))
      r.target_db = `${sourceAlias.value}_${r.name}`
  })
}

const selectedDbs = computed(() => dbRows.value.filter((r) => r.selected))
const totalMb = computed(() => selectedDbs.value.reduce((s, r) => s + r.size_mb, 0))
const conflictCheckLoading = ref(false)
const conflictSummary = ref<{ ok: boolean; conflicts: any[]; checked_count: number } | null>(null)
const selectedMappingsPayload = computed(() => selectedDbs.value.map((r) => ({ source_db: r.name, target_db: r.target_db.trim() })))
const hasTargetDbConflicts = computed(() => Boolean(conflictSummary.value && conflictSummary.value.ok === false && conflictSummary.value.conflicts?.length))
const hasInvalidTargetDb = computed(() => selectedDbs.value.some((r) => !r.target_db.trim()))

const mode = ref('auto')
const recommendation = ref({ recommended_mode: '', reason: '', size_mb: 0, tools: {} as any })
const installLoading = ref<Record<string, boolean>>({})
const installLog = ref('')
const installWatching = ref(false)
let installLogTimer: ReturnType<typeof setInterval> | null = null

// ---------- Physical mode enable: SSH handshake (pubkey + metadata) ----------
const sshPubKey = ref('')
const sshHandshake = ref('')
const sshKeyLoading = ref(false)
const sshTestLoading = ref(false)
const sshTestResult = ref<{ ok: boolean; msg: string } | null>(null)

async function generateHandshake() {
  sshKeyLoading.value = true
  sshTestResult.value = null
  const res = await call('replica_export_handshake', {})
  sshKeyLoading.value = false
  if (isOk(res)) {
    const d = extractMsg(res) || {}
    sshPubKey.value = d.pub_key || ''
    sshHandshake.value = d.handshake_b64 || ''
    msg.success('握手单已生成，点击"复制握手单"粘贴到主库即可')
  } else {
    msg.error(getMessage(res) || '生成握手单失败')
  }
}

async function copyHandshake() {
  if (!sshHandshake.value) { await generateHandshake() }
  if (!sshHandshake.value) return
  try {
    await navigator.clipboard.writeText(sshHandshake.value)
    msg.success('握手单已复制，粘贴到主库"物理模式·粘贴握手单"')
  } catch {
    msg.info('请手动复制下方握手单文本')
  }
}

async function testSshToMaster() {
  if (!form.master_host) { msg.warning('请先在第 1 步填入主库地址'); return }
  sshTestLoading.value = true
  const res = await call('replica_test_ssh_to_master', { master_host: form.master_host })
  sshTestLoading.value = false
  if (isOk(res)) {
    sshTestResult.value = { ok: true, msg: '物理模式 SSH 免密正常，可选物理模式' }
  } else {
    sshTestResult.value = { ok: false, msg: getMessage(res) || 'SSH 测试失败' }
  }
}

const syncTaskStatus = ref<any>(null)
const syncTaskLog = ref('')
const syncWatching = ref(false)
let syncLogTimer: ReturnType<typeof setInterval> | null = null

function stopInstallWatch() {
  if (installLogTimer) {
    clearInterval(installLogTimer)
    installLogTimer = null
  }
  installWatching.value = false
}

function stopSyncWatch() {
  if (syncLogTimer) {
    clearInterval(syncLogTimer)
    syncLogTimer = null
  }
  syncWatching.value = false
}

async function refreshInstallLogSilent() {
  const res = await call('get_tool_install_log', {})
  if (isOk(res)) installLog.value = extractMsg(res)?.content || ''
}

async function refreshTaskRealtime(taskId: string) {
  const [taskRes, logRes] = await Promise.all([
    call('get_bootstrap_task', { task_id: taskId }),
    call('get_task_logs', { task_id: taskId }),
  ])
  if (isOk(taskRes)) syncTaskStatus.value = extractMsg(taskRes) || null
  if (isOk(logRes)) syncTaskLog.value = extractMsg(logRes) || ''

  const st = syncTaskStatus.value?.status
  if (st === 'done' || st === 'failed' || st === 'cancelled') stopSyncWatch()
}

async function loadRecommendation() {
  loading.value = true
  try {
    const res = await call('wizard_recommend_bootstrap', { size_mb: String(totalMb.value) })
    if (isOk(res)) {
      const d = extractMsg(res)
      recommendation.value = d
      mode.value = d.recommended_mode || 'auto'
    }
  } finally {
    loading.value = false
  }
}

async function installTool(tool: 'xtrabackup' | 'mariabackup') {
  installLoading.value[tool] = true
  installWatching.value = true
  installLog.value = ''
  await refreshInstallLogSilent()
  stopInstallWatch()
  installLogTimer = setInterval(() => { refreshInstallLogSilent() }, 1200)
  try {
    const res = await call('install_bootstrap_tool', { tool_name: tool })
    if (isOk(res)) {
      msg.success(`${tool} 安装成功`)
      await refreshInstallLogSilent()
      await loadRecommendation()
      return
    }
    const m = extractMsg(res)
    if (m && typeof m === 'object' && m.manual_cmd) {
      msg.warning(`自动安装失败，请手工执行：${m.manual_cmd}`)
    } else {
      msg.error(getMessage(res) || `安装 ${tool} 失败`)
    }
  } finally {
    stopInstallWatch()
    installLoading.value[tool] = false
  }
}

async function loadInstallLog() {
  loading.value = true
  try {
    const res = await call('get_tool_install_log', {})
    if (isOk(res)) {
      installLog.value = extractMsg(res)?.content || ''
      if (!installLog.value.trim()) msg.info('暂无安装日志')
    } else {
      msg.error(getMessage(res) || '读取安装日志失败')
    }
  } finally {
    loading.value = false
  }
}

const startResult = ref<any>(null)
const finalState = computed<'idle' | 'running' | 'done' | 'failed' | 'cancelled'>(() => {
  if (!startResult.value) return 'idle'
  const st = syncTaskStatus.value?.status
  if (st === 'done') return 'done'
  if (st === 'failed') return 'failed'
  if (st === 'cancelled') return 'cancelled'
  return 'running'
})

async function startReplication() {
  if (startingReplication.value) return
  if (!(await runConflictCheck(true))) return
  startingReplication.value = true
  loading.value = true
  syncTaskStatus.value = null
  syncTaskLog.value = ''
  stopSyncWatch()
  try {
    const mappings = selectedDbs.value.map((r) => ({ source_db: r.name, target_db: r.target_db }))
    const res = await call('wizard_start_replication', {
      master_host: form.master_host,
      master_port: String(form.master_port),
      repl_user: form.repl_user,
      repl_password: form.repl_password,
      source_id: sourceAlias.value,
      mappings: JSON.stringify(mappings),
      mode: mode.value,
      auto_start: '1',
    })
    if (isOk(res)) {
      startResult.value = extractMsg(res)
      const taskId = startResult.value?.task_id
      if (taskId) {
        syncWatching.value = true
        await refreshTaskRealtime(taskId)
        syncLogTimer = setInterval(() => { refreshTaskRealtime(taskId) }, 1500)
      }
      if (startResult.value?.source_existed) msg.info('已识别为重复接入：配置已更新，任务执行中')
      else msg.info('任务已提交，正在执行初始化')
      step.value = 5
    } else {
      msg.error(getMessage(res) || '启动失败')
    }
  } finally {
    loading.value = false
    startingReplication.value = false
  }
}

async function runConflictCheck(showSuccess = false) {
  if (selectedDbs.value.length === 0) {
    conflictSummary.value = null
    return true
  }
  if (hasInvalidTargetDb.value) {
    conflictSummary.value = {
      ok: false,
      conflicts: [{ message: '存在空的目标库名，请先补全后再检查。', type: 'invalid_target_db', target_db: '' }],
      checked_count: selectedDbs.value.length,
    }
    msg.warning('请先补全所有目标库名')
    return false
  }
  conflictCheckLoading.value = true
  try {
    const res = await call('check_target_db_conflicts', {
      exclude_source_id: sourceAlias.value,
      mappings: JSON.stringify(selectedMappingsPayload.value),
    })
    if (!isOk(res)) {
      msg.error(getMessage(res) || '冲突检查失败')
      return false
    }
    const data = extractMsg(res) || {}
    conflictSummary.value = {
      ok: Boolean(data.ok),
      conflicts: Array.isArray(data.conflicts) ? data.conflicts : [],
      checked_count: Number(data.checked_count || selectedDbs.value.length),
    }
    if (conflictSummary.value.ok) {
      if (showSuccess) msg.success('未发现目标库冲突')
      return true
    }
    msg.warning('发现目标库冲突，请先调整目标库名')
    return false
  } finally {
    conflictCheckLoading.value = false
  }
}

async function retryTaskNow() {
  const taskId = startResult.value?.task_id
  if (!taskId) return
  syncTaskLog.value = ''
  syncTaskStatus.value = null
  const res = await call('trigger_bootstrap_task', { task_id: taskId })
  if (isOk(res)) {
    msg.success('已触发重试')
    syncWatching.value = true
    await refreshTaskRealtime(taskId)
    stopSyncWatch()
    syncLogTimer = setInterval(() => { refreshTaskRealtime(taskId) }, 1500)
  } else {
    msg.error(getMessage(res) || '重试触发失败')
  }
}

function backToConfig() {
  stopSyncWatch()
  startResult.value = null
  syncTaskStatus.value = null
  syncTaskLog.value = ''
  step.value = 4
}

onUnmounted(() => {
  stopInstallWatch()
  stopSyncWatch()
})

async function goStep(n: number) {
  if (n === 4) {
    const ok = await runConflictCheck()
    if (!ok) return
  }
  step.value = n
  if (n === 2) runPreflight()
  if (n === 3) loadDatabases()
  if (n === 4) loadRecommendation()
}

watch(step, () => {
  nextTick(() => scrollPluginTop())
})

watch([sourceAlias, dbRows], () => {
  conflictSummary.value = null
}, { deep: true })

const checkLabels: Record<string, string> = { network: '网络连通', auth: '账号认证', gtid: 'GTID 兼容' }
</script>

<template>
  <div class="mms-wizard">
    <div class="mms-wizard__header">
      <h2 class="mms-wizard__title">帮我接入主库</h2>
      <p class="mms-wizard__desc">填写信息 → 检查连通性 → 选库 → 选策略 → 一键同步</p>
    </div>

    <NSteps :current="step" size="small" class="mms-wizard__steps">
      <NStep title="主库信息" />
      <NStep title="连通性" />
      <NStep title="选择库" />
      <NStep title="策略" />
      <NStep title="同步" />
    </NSteps>

    <NSpin :show="loading">
      <!-- Step 1 -->
      <div v-if="step === 1" class="mms-step-card">
        <div class="mms-step-card__header"><h3>填写主库信息</h3></div>

        <NRadioGroup v-model:value="inputMode" size="small" style="margin-bottom: 16px">
          <NRadioButton value="manual">手动填写</NRadioButton>
          <NRadioButton value="profile">粘贴配置单</NRadioButton>
        </NRadioGroup>

        <template v-if="inputMode === 'profile'">
          <NInput v-model:value="profileText" type="textarea" :rows="4" placeholder="将主库导出的配置单文本粘贴在这里" style="margin-bottom: 12px" />
          <NButton type="primary" @click="verifyProfile" :disabled="!profileText.trim()" :loading="loading">验证配置单</NButton>
          <NAlert v-if="profileVerified" type="success" :bordered="false" style="margin-top:12px">
            配置单验证通过，主库信息已自动填入。
          </NAlert>
        </template>

        <template v-if="inputMode === 'manual' || profileVerified">
          <div class="mms-form-group" style="margin-top: 16px">
            <div class="mms-field">
              <label>主库地址</label>
              <NInput v-model:value="form.master_host" placeholder="如 10.0.0.11" />
            </div>
            <div class="mms-field">
              <label>端口</label>
              <NInputNumber v-model:value="form.master_port" :min="1" :max="65535" style="width: 100%" />
            </div>
            <div class="mms-field">
              <label>复制账号</label>
              <NInput v-model:value="form.repl_user" placeholder="如 repl_user" />
            </div>
            <div class="mms-field">
              <label>复制密码</label>
              <NInput v-model:value="form.repl_password" type="password" show-password-on="click" placeholder="密码" />
            </div>
          </div>
        </template>

        <div class="mms-step-actions">
          <NButton type="primary" :disabled="!canProceed1" @click="goStep(2)">
            下一步：检查连通性
          </NButton>
        </div>
      </div>

      <!-- Step 2 -->
      <div v-if="step === 2" class="mms-step-card">
        <div class="mms-step-card__header"><h3>连通性检查</h3></div>

        <div class="mms-check-list">
          <div v-for="(label, key) in checkLabels" :key="key" class="mms-check-item">
            <div class="mms-check-item__status" :class="(checks as any)[key]?.ok ? 'mms-check-item__status--ok' : 'mms-check-item__status--fail'">
              {{ (checks as any)[key]?.ok ? '✓' : '✗' }}
            </div>
            <div class="mms-check-item__body">
              <div class="mms-check-item__name">{{ label }}</div>
              <div class="mms-check-item__detail">{{ (checks as any)[key]?.reason }}</div>
            </div>
          </div>
        </div>

        <NAlert v-if="!allOk" type="warning" :bordered="false" style="margin-top:12px">
          部分检查未通过，你仍可继续（可能导致后续步骤失败）。
        </NAlert>

        <div class="mms-step-actions">
          <NButton @click="step = 1">上一步</NButton>
          <NButton @click="runPreflight">重新检测</NButton>
          <NButton type="primary" @click="goStep(3)">下一步：选择库</NButton>
        </div>
      </div>

      <!-- Step 3 -->
      <div v-if="step === 3" class="mms-step-card">
        <div class="mms-step-card__header">
          <h3>选择要同步的库</h3>
          <NButton size="small" @click="loadDatabases">刷新列表</NButton>
        </div>

        <div class="mms-form-group" style="margin-bottom: 16px">
          <div class="mms-field" style="max-width: 260px">
            <label>主库代号</label>
            <NInput v-model:value="sourceAlias" placeholder="如 m1" @blur="refreshTargetNames" />
            <div class="mms-field__hint">从库目标库名自动拼成 <code>{{ sourceAlias }}_库名</code></div>
          </div>
        </div>

        <NAlert v-if="dbRows.length === 0 && !loading" type="info" :bordered="false" style="margin-bottom:12px">
          未获取到库列表，请检查连通性或权限。
        </NAlert>

        <div v-if="dbRows.length" class="mms-db-info">
          共 {{ dbRows.length }} 个库，已选 <strong>{{ selectedDbs.length }}</strong> 个
          （约 {{ totalMb.toFixed(1) }} MB）
        </div>

        <NAlert v-if="hasInvalidTargetDb" type="warning" :bordered="false" style="margin-bottom:12px">
          存在空的目标库名，继续前请先补全，避免把多个来源误同步到未知目标。
        </NAlert>

        <div v-if="dbRows.length" class="mms-db-grid">
          <div class="mms-db-grid__header">
            <NCheckbox
              :checked="selectedDbs.length === dbRows.length && dbRows.length > 0"
              :indeterminate="selectedDbs.length > 0 && selectedDbs.length < dbRows.length"
              @update:checked="(v: boolean) => dbRows.forEach(r => r.selected = v)"
            >全选</NCheckbox>
          </div>
          <div v-for="row in dbRows" :key="row.name" class="mms-db-row" :class="{ 'mms-db-row--selected': row.selected }">
            <NCheckbox v-model:checked="row.selected" />
            <div class="mms-db-row__name">{{ row.name }}</div>
            <div class="mms-db-row__size">{{ row.size_mb.toFixed(1) }} MB</div>
            <NInput v-model:value="row.target_db" size="small" placeholder="目标库名" class="mms-db-row__target" />
          </div>
        </div>

        <NAlert v-if="conflictSummary && !conflictSummary.ok" type="error" :bordered="false" style="margin-top:12px">
          检测到 {{ conflictSummary.conflicts.length }} 个目标库冲突，请调整后再继续。
          <div v-for="(conflict, idx) in conflictSummary.conflicts" :key="`${conflict.type}-${conflict.target_db}-${idx}`" class="mms-conflict-line">
            {{ conflict.target_db || '未填写目标库名' }}：{{ conflict.message }}
            <span v-if="conflict.source_ids?.length">（已被 {{ conflict.source_ids.join('、') }} 使用）</span>
          </div>
        </NAlert>

        <NAlert v-else-if="conflictSummary?.ok" type="success" :bordered="false" style="margin-top:12px">
          已检查 {{ conflictSummary.checked_count }} 条映射，未发现目标库冲突。
        </NAlert>

        <div class="mms-step-actions">
          <NButton @click="step = 2">上一步</NButton>
          <NButton :loading="conflictCheckLoading" @click="runConflictCheck(true)">检查目标库冲突</NButton>
          <NButton type="primary" :disabled="selectedDbs.length === 0 || hasInvalidTargetDb || hasTargetDbConflicts" @click="goStep(4)">
            下一步：初始化策略
          </NButton>
        </div>
      </div>

      <!-- Step 4 -->
      <div v-if="step === 4" class="mms-step-card">
        <div class="mms-step-card__header"><h3>初始化策略</h3></div>

        <NAlert type="info" :bordered="false" style="margin-bottom:16px">
          {{ recommendation.reason || '正在分析推荐策略...' }}
        </NAlert>

        <NRadioGroup v-model:value="mode" size="small">
          <NRadioButton value="auto">自动选择（推荐）</NRadioButton>
          <NRadioButton value="logical">逻辑（mysqldump）</NRadioButton>
          <NRadioButton value="physical">物理（xtrabackup）</NRadioButton>
        </NRadioGroup>

        <NCollapse style="margin-top:16px" :default-expanded-names="[]">
          <NCollapseItem title="启用物理模式（SSH 握手单）" name="ssh">
            <NAlert type="info" :bordered="false" style="margin-bottom:10px">
              点击"生成握手单"得到一串 base64 文本 → 复制 → 到主库"物理模式·粘贴握手单"粘贴 → 点击安装，即可一键开通物理模式。
              握手单里同时包含了公钥、从库 IP、来源标识，和主库配置单体验一致。
            </NAlert>
            <div style="display:flex; gap:8px; flex-wrap:wrap">
              <NButton type="primary" size="small" :loading="sshKeyLoading" @click="generateHandshake">
                生成握手单
              </NButton>
              <NButton size="small" :disabled="!sshHandshake" @click="copyHandshake">
                复制握手单
              </NButton>
              <NButton size="small" :loading="sshTestLoading" @click="testSshToMaster">
                测试到主库的 SSH
              </NButton>
            </div>
            <NInput
              v-if="sshHandshake"
              :value="sshHandshake"
              type="textarea"
              :rows="4"
              readonly
              placeholder="握手单（包含公钥+元信息）"
              style="margin-top:10px; font-family: ui-monospace,Menlo,monospace; font-size:12px"
            />
            <div v-if="sshPubKey" style="margin-top:6px; font-size:12px; color:#999">
              公钥指纹：{{ sshPubKey.split(' ').slice(0,2).join(' ').slice(0, 40) }}…
            </div>
            <NAlert
              v-if="sshTestResult"
              :type="sshTestResult.ok ? 'success' : 'warning'"
              :bordered="false"
              style="margin-top:10px"
            >
              {{ sshTestResult.msg }}
            </NAlert>
          </NCollapseItem>
          <NCollapseItem title="工具可用性详情" name="tools">
            <div class="mms-tool-grid">
              <div v-for="tool in ['mysqldump', 'mysql', 'xtrabackup', 'mariabackup']" :key="tool" class="mms-tool-item">
                <NIcon :size="16" :component="recommendation.tools?.[tool] ? CheckmarkCircleOutline : CloseCircleOutline" :color="recommendation.tools?.[tool] ? '#18a058' : '#d03050'" />
                <span>{{ tool }}</span>
                <NTag :type="recommendation.tools?.[tool] ? 'success' : 'error'" size="small" :bordered="false">
                  {{ recommendation.tools?.[tool] ? '已安装' : '未安装' }}
                </NTag>
                <NButton
                  v-if="(tool === 'xtrabackup' || tool === 'mariabackup') && !recommendation.tools?.[tool]"
                  size="tiny"
                  secondary
                  type="primary"
                  :loading="installLoading[tool]"
                  @click="installTool(tool as 'xtrabackup' | 'mariabackup')"
                >
                  立即安装
                </NButton>
              </div>
            </div>
            <div class="mms-tool-actions">
              <NButton size="tiny" @click="loadInstallLog">查看安装日志</NButton>
            </div>
            <NInput
              v-if="installLog || installWatching"
              :value="installLog"
              type="textarea"
              :rows="8"
              readonly
              :placeholder="installWatching ? '安装执行中，实时日志刷新中...' : '安装日志会显示在这里'"
              style="margin-top: 8px"
            />
          </NCollapseItem>
        </NCollapse>

        <div class="mms-step-actions">
          <NButton @click="step = 3">上一步</NButton>
          <NButton type="primary" :disabled="hasInvalidTargetDb || hasTargetDbConflicts" @click="step = 5">下一步：确认同步</NButton>
        </div>
      </div>

      <!-- Step 5 -->
      <div v-if="step === 5 && !startResult" class="mms-step-card">
        <div class="mms-step-card__header"><h3>确认并开始同步</h3></div>

        <div class="mms-confirm-grid">
          <div class="mms-confirm-row">
            <span class="mms-confirm-label">主库地址</span>
            <span class="mms-confirm-value">{{ form.master_host }}:{{ form.master_port }}</span>
          </div>
          <div class="mms-confirm-row">
            <span class="mms-confirm-label">复制账号</span>
            <span class="mms-confirm-value">{{ form.repl_user }}</span>
          </div>
          <div class="mms-confirm-row">
            <span class="mms-confirm-label">主库代号</span>
            <span class="mms-confirm-value">{{ sourceAlias }}</span>
          </div>
          <div class="mms-confirm-row">
            <span class="mms-confirm-label">同步库数</span>
            <span class="mms-confirm-value">{{ selectedDbs.length }} 个（约 {{ totalMb.toFixed(1) }} MB）</span>
          </div>
          <div class="mms-confirm-row">
            <span class="mms-confirm-label">初始化方式</span>
            <span class="mms-confirm-value">{{ mode === 'auto' ? '自动选择' : mode === 'logical' ? '逻辑 (mysqldump)' : '物理 (xtrabackup)' }}</span>
          </div>
          <div class="mms-confirm-row">
            <span class="mms-confirm-label">库映射</span>
            <div class="mms-confirm-value">
              <div v-for="d in selectedDbs" :key="d.name" style="font-size:12px">{{ d.name }} → {{ d.target_db }}</div>
            </div>
          </div>
        </div>

        <NAlert v-if="conflictSummary && !conflictSummary.ok" type="error" :bordered="false" style="margin-top:12px">
          当前映射仍存在冲突，开始同步按钮已阻止提交。请返回上一步修改目标库名。
        </NAlert>

        <div class="mms-step-actions">
          <NButton @click="step = 4">上一步</NButton>
          <NButton type="primary" size="large" :loading="startingReplication" :disabled="loading || startingReplication || hasInvalidTargetDb || hasTargetDbConflicts" @click="startReplication">
            开始同步
          </NButton>
        </div>
      </div>

      <div v-if="step === 5 && startResult" class="mms-step-card" style="margin-top: 12px">
        <div class="mms-step-card__header">
          <h3>实时执行输出</h3>
          <NTag size="small" :bordered="false" :type="syncTaskStatus?.status === 'done' ? 'success' : (syncTaskStatus?.status === 'failed' ? 'error' : 'info')">
            {{ syncTaskStatus?.status || 'running' }}
          </NTag>
        </div>
        <NDescriptions :column="2" size="small" bordered>
          <NDescriptionsItem label="任务ID">{{ startResult.task_id || '-' }}</NDescriptionsItem>
          <NDescriptionsItem label="当前步骤">{{ syncTaskStatus?.current_step || '等待执行' }}</NDescriptionsItem>
          <NDescriptionsItem label="进度">{{ syncTaskStatus?.progress ?? 0 }}%</NDescriptionsItem>
          <NDescriptionsItem label="重试次数">{{ syncTaskStatus?.retry_count ?? 0 }}</NDescriptionsItem>
        </NDescriptions>
        <NProgress
          type="line"
          :percentage="syncTaskStatus?.progress ?? 0"
          :status="syncTaskStatus?.status === 'done' ? 'success' : (syncTaskStatus?.status === 'failed' ? 'error' : 'info')"
          :indicator-placement="'inside'"
          style="margin-top: 8px"
        />
        <NAlert
          v-if="syncTaskStatus?.current_step && /导入.*中/.test(syncTaskStatus.current_step)"
          type="info"
          :bordered="false"
          size="small"
          style="margin-top: 8px"
        >
          大库首次导入耗时较长（1 GB 约 3–8 分钟），步骤文本里的 MB 数和百分比会实时刷新；无需人工干预。
        </NAlert>
        <NInput
          :value="syncTaskLog"
          type="textarea"
          :rows="10"
          readonly
          :placeholder="syncWatching ? '任务执行中，实时日志刷新中...' : '暂无任务日志输出'"
          style="margin-top: 10px"
        />
      </div>

      <NResult
        v-if="step === 5 && startResult && finalState === 'done'"
        status="success"
        title="接入成功！"
        :description="`来源 ${startResult.source_id} 已创建，任务 ${startResult.task_id} 已完成。`"
      >
        <template #footer>
          <NSpace>
            <NButton type="primary" @click="env.navigate('dashboard')">查看仪表盘</NButton>
            <NButton @click="env.navigate('landing')">返回首页</NButton>
          </NSpace>
        </template>
      </NResult>

      <NResult
        v-if="step === 5 && startResult && (finalState === 'running' || finalState === 'failed' || finalState === 'cancelled')"
        :status="finalState === 'running' ? 'info' : (finalState === 'failed' ? 'error' : 'warning')"
        :title="finalState === 'running' ? '任务执行中' : (finalState === 'failed' ? '任务执行失败' : '任务已取消')"
        :description="`来源 ${startResult.source_id}，任务 ${startResult.task_id}，状态：${syncTaskStatus?.current_step || finalState}`"
      >
        <template #footer>
          <NSpace>
            <NButton v-if="finalState === 'failed' || finalState === 'cancelled'" type="primary" @click="retryTaskNow">立即重试</NButton>
            <NButton v-if="finalState === 'running'" @click="env.navigate('dashboard')">去仪表盘继续看</NButton>
            <NButton @click="backToConfig">返回策略页</NButton>
          </NSpace>
        </template>
      </NResult>
    </NSpin>
  </div>
</template>

<style scoped>
.mms-wizard { max-width: 760px; margin: 0 auto; }
.mms-wizard__header { margin-bottom: 20px; }
.mms-wizard__title { font-size: 20px; font-weight: 700; color: #1a1a2e; margin: 0 0 4px; }
.mms-wizard__desc { font-size: 13px; color: #999; margin: 0; }
.mms-wizard__steps { margin-bottom: 24px; }
.mms-step-card { background: #fff; border: 1px solid #ebeef5; border-radius: 12px; padding: 24px; }
.mms-step-card__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 16px; }
.mms-step-card__header h3 { font-size: 16px; font-weight: 600; color: #1a1a2e; margin: 0; }
.mms-step-actions { display: flex; gap: 8px; margin-top: 20px; padding-top: 16px; border-top: 1px solid #f0f0f5; }
.mms-form-group { max-width: 440px; display: flex; flex-direction: column; gap: 12px; }
.mms-field label { display: block; font-size: 13px; font-weight: 600; color: #555; margin-bottom: 4px; }
.mms-field__hint { font-size: 11px; color: #999; margin-top: 4px; }
.mms-field__hint code { background: #e8e8ed; padding: 1px 5px; border-radius: 3px; font-size: 11px; }
.mms-check-list { display: flex; flex-direction: column; gap: 8px; }
.mms-check-item { display: flex; align-items: flex-start; gap: 10px; padding: 10px 12px; background: #fafafa; border-radius: 8px; }
.mms-check-item__status { width: 24px; height: 24px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 12px; font-weight: 700; flex-shrink: 0; color: #fff; }
.mms-check-item__status--ok { background: #18a058; }
.mms-check-item__status--fail { background: #d03050; }
.mms-check-item__body { flex: 1; }
.mms-check-item__name { font-size: 14px; font-weight: 600; color: #333; }
.mms-check-item__detail { font-size: 12px; color: #888; margin-top: 2px; }
.mms-db-info { font-size: 13px; color: #666; margin-bottom: 12px; }
.mms-db-grid { border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; }
.mms-db-grid__header { padding: 8px 12px; background: #f8f8fa; border-bottom: 1px solid #ebeef5; }
.mms-db-row { display: flex; align-items: center; gap: 10px; padding: 8px 12px; border-bottom: 1px solid #f5f5f5; transition: background 0.15s; }
.mms-db-row:last-child { border-bottom: none; }
.mms-db-row--selected { background: #f0faf5; }
.mms-db-row__name { flex: 1; font-size: 13px; font-weight: 500; }
.mms-db-row__size { font-size: 12px; color: #999; width: 80px; text-align: right; }
.mms-db-row__target { width: 180px; }
.mms-conflict-line { margin-top: 6px; font-size: 12px; line-height: 1.5; }
.mms-tool-grid { display: flex; flex-direction: column; gap: 6px; }
.mms-tool-item { display: flex; align-items: center; gap: 8px; font-size: 13px; }
.mms-confirm-grid { display: flex; flex-direction: column; gap: 0; border: 1px solid #ebeef5; border-radius: 8px; overflow: hidden; }
.mms-confirm-row { display: flex; padding: 10px 14px; border-bottom: 1px solid #f5f5f5; }
.mms-confirm-row:last-child { border-bottom: none; }
.mms-confirm-label { width: 100px; font-size: 13px; color: #888; flex-shrink: 0; }
.mms-confirm-value { font-size: 13px; color: #333; font-weight: 500; }
</style>
