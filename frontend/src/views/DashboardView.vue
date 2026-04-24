<script setup lang="ts">
import { onMounted, onBeforeUnmount, onUnmounted, ref, watch } from 'vue'
import {
  NButton, NSpace, NTag, NProgress, NIcon,
  NEmpty, NCollapse, NCollapseItem, NModal, NInput,
  useMessage, NSpin,
} from 'naive-ui'
import { PlayOutline, PauseOutline, TrashOutline } from '@vicons/ionicons5'
import { useDashboardStore, type SourceStatus } from '@/store/dashboard'
import { call, isOk, btConfirm, getMessage, extractMsg } from '@/api/plugin'
import { useEnvStore } from '@/store/env'

const dash = useDashboardStore()
const env = useEnvStore()
const msg = useMessage()
const installLoading = ref<Record<string, boolean>>({})
const sourceActionLoading = ref<Record<string, boolean>>({})
const logVisible = ref(false)
const logTaskId = ref('')
const logTaskStep = ref('')
const logTaskStatus = ref('')
const logText = ref('')
let logTimer: ReturnType<typeof setInterval> | null = null
let logRequestSeq = 0

onMounted(() => dash.startPolling(5000))
onBeforeUnmount(() => {
  logVisible.value = false
  stopLogPolling()
})
onUnmounted(() => {
  dash.stopPolling()
  stopLogPolling()
  logVisible.value = false
})

watch(
  () => env.currentView,
  (v) => {
    if (v !== 'dashboard') {
      logVisible.value = false
      stopLogPolling()
    }
  },
)

const taskFor = (sid: string) => dash.liveTasks.find((t) => t.source_id === sid)
const isPendingTask = (sid: string) => taskFor(sid)?.status === 'pending'
function bootstrapOf(sid: string) {
  const t = taskFor(sid)
  if (!t) return null
  if (t.status === 'running' || t.status === 'pending') return t
  return null
}

function healthColor(src: any): string {
  if (!src) return '#f0a020'
  const bs = bootstrapOf(src.source_id)
  if (bs) return '#2080f0'
  const s = src.status || {}
  if (s.running) return '#18a058'
  if (s.last_error) return '#d03050'
  return '#f0a020'
}
function healthText(src: any): string {
  if (!src) return '未知'
  const bs = bootstrapOf(src.source_id)
  if (bs) {
    if (bs.status === 'pending') return '等待初始化'
    const pct = typeof bs.progress === 'number' ? bs.progress : 0
    return `初始化中 ${pct}%`
  }
  const s = src.status || {}
  if (s.running) return '运行中'
  if (s.last_error) return '异常'
  return '已停止'
}
function threadTagType(sid: string, val: string): 'success' | 'error' | 'info' {
  if (bootstrapOf(sid)) return 'info'
  return val === 'Yes' ? 'success' : 'error'
}
function threadTagText(sid: string, val: string): string {
  if (bootstrapOf(sid)) return '初始化中'
  return val || '-'
}
function delayText(s: SourceStatus['status']): string {
  if (s.seconds_behind === null || s.seconds_behind === undefined) return '-'
  return `${s.seconds_behind}s`
}

async function startSource(sid: string) {
  if (sourceActionLoading.value[sid]) return
  sourceActionLoading.value[sid] = true
  const liveTask = taskFor(sid)
  try {
    if (liveTask && liveTask.status === 'pending') {
      const trig = await call('trigger_bootstrap_task', { task_id: liveTask.task_id })
      if (isOk(trig)) {
        msg.success('初始化任务已触发，点击"查看日志"跟踪进度')
        dash.refresh()
        return
      }
      msg.error(getMessage(trig) || '触发初始化任务失败')
      return
    }

    const res = await call('start_channel', { source_id: sid })
    if (isOk(res)) {
      msg.success('通道已启动')
      dash.refresh()
    }
    else msg.error(getMessage(res) || '启动失败')
  } finally {
    sourceActionLoading.value[sid] = false
  }
}
async function stopSource(sid: string) {
  if (sourceActionLoading.value[sid]) return
  if (!await btConfirm('暂停复制', `确认暂停 ${sid}？`)) return
  sourceActionLoading.value[sid] = true
  try {
    const res = await call('stop_channel', { source_id: sid })
    if (isOk(res)) { msg.success('通道已暂停'); dash.refresh() }
    else msg.error(getMessage(res) || '暂停失败')
  } finally {
    sourceActionLoading.value[sid] = false
  }
}
async function removeSource(sid: string) {
  if (sourceActionLoading.value[sid]) return
  if (!await btConfirm('移除来源', `确认移除 ${sid}？通道将停止并删除配置。`)) return
  sourceActionLoading.value[sid] = true
  try {
    const res = await call('remove_source', { source_id: sid })
    if (isOk(res)) { msg.success('已移除'); dash.refresh() }
    else msg.error(getMessage(res) || '移除失败')
  } finally {
    sourceActionLoading.value[sid] = false
  }
}
function stopLogPolling() {
  if (logTimer) {
    clearInterval(logTimer)
    logTimer = null
  }
}

async function refreshTaskLog(taskId: string) {
  const seq = ++logRequestSeq
  const [taskRes, logRes] = await Promise.all([
    call('get_bootstrap_task', { task_id: taskId }),
    call('get_task_logs', { task_id: taskId }),
  ])
  if (seq !== logRequestSeq || logTaskId.value !== taskId || !logVisible.value) return
  if (isOk(taskRes)) {
    const t = taskRes.msg || {}
    logTaskStatus.value = t.status || ''
    logTaskStep.value = t.current_step || ''
  }
  if (isOk(logRes)) {
    logText.value = (logRes.msg || '') as string
  }
  if (['done', 'failed', 'cancelled'].includes(logTaskStatus.value)) {
    stopLogPolling()
  }
}

function openTaskLog(taskId: string) {
  if (!taskId) return
  logRequestSeq += 1
  logVisible.value = true
  logTaskId.value = taskId
  logTaskStatus.value = ''
  logTaskStep.value = ''
  logText.value = ''
  stopLogPolling()
  refreshTaskLog(taskId)
  logTimer = setInterval(() => { refreshTaskLog(taskId) }, 1200)
}

async function installTool(tool: 'xtrabackup' | 'mariabackup') {
  installLoading.value[tool] = true
  try {
    const res = await call('install_bootstrap_tool', { tool_name: tool })
    if (isOk(res)) {
      msg.success(`${tool} 安装成功`)
      return
    }
    msg.error(getMessage(res) || `安装 ${tool} 失败`)
  } finally {
    installLoading.value[tool] = false
  }
}

const registerPanelLoading = ref(false)
async function registerPanelDbs() {
  registerPanelLoading.value = true
  try {
    const res = await call('register_existing_target_dbs')
    if (isOk(res)) {
      const d = extractMsg(res) || {}
      const regCount = (d.registered || []).length
      if (regCount > 0) {
        msg.success(`已注册 ${regCount} 个同步库到宝塔「数据库」列表，刷新 MySQL 管理页即可看到`)
      } else {
        msg.info('所有同步库都已在宝塔「数据库」列表中')
      }
    } else {
      msg.error(getMessage(res) || '注册到宝塔数据库列表失败')
    }
  } finally {
    registerPanelLoading.value = false
  }
}
</script>

<template>
  <div class="mms-dash">
    <div class="mms-dash__header">
      <h2 class="mms-dash__title">复制仪表盘</h2>
      <NSpace :size="6">
        <NButton size="small" type="primary" ghost :loading="registerPanelLoading" @click="registerPanelDbs"
                 title="将已同步的目标库写入宝塔「数据库」列表，让面板能看到同步库">
          同步到面板数据库列表
        </NButton>
        <NButton size="small" @click="installTool('xtrabackup')" :loading="installLoading.xtrabackup">安装 xtrabackup</NButton>
        <NButton size="small" @click="installTool('mariabackup')" :loading="installLoading.mariabackup">安装 mariabackup</NButton>
        <NButton size="small" @click="dash.refresh">刷新</NButton>
      </NSpace>
    </div>

    <NSpin :show="dash.loading && dash.sources.length === 0">
      <!-- Metrics bar -->
      <div class="mms-metrics">
        <div class="mms-metric">
          <div class="mms-metric__value">{{ dash.metrics.total_sources }}</div>
          <div class="mms-metric__label">来源总数</div>
        </div>
        <div class="mms-metric">
          <div class="mms-metric__value" style="color:#18a058">{{ dash.metrics.running_sources }}</div>
          <div class="mms-metric__label">运行中</div>
        </div>
        <div class="mms-metric">
          <div class="mms-metric__value" style="color:#d03050">{{ dash.metrics.error_sources }}</div>
          <div class="mms-metric__label">异常</div>
        </div>
        <div class="mms-metric">
          <div class="mms-metric__value" style="color:#2080f0">{{ dash.metrics.bootstrap_done }}</div>
          <div class="mms-metric__label">任务完成</div>
        </div>
      </div>

      <NEmpty v-if="dash.sources.length === 0 && !dash.loading" description="暂无复制来源，请先接入主库" style="margin: 40px 0" />

      <!-- Source cards -->
      <div class="mms-source-list">
        <div v-for="src in dash.sources" :key="src.source_id" class="mms-source-card" :style="{ borderLeftColor: healthColor(src) }">
          <div class="mms-source-card__header">
            <div class="mms-source-card__title">
              <div class="mms-source-card__dot" :style="{ background: healthColor(src) }"></div>
              <strong>{{ src.source_id }}</strong>
              <span class="mms-source-card__host">{{ src.master_host }}:{{ src.master_port }}</span>
              <NTag :bordered="false" size="small" round :style="{ background: healthColor(src) + '15', color: healthColor(src) }">
                {{ healthText(src) }}
              </NTag>
            </div>
            <NSpace :size="4">
              <NButton v-if="bootstrapOf(src.source_id)" size="tiny" type="info" secondary disabled>
                <template #icon><NIcon :component="PlayOutline" /></template>初始化中…
              </NButton>
              <NButton v-else-if="!src.status.running" size="tiny" type="success" secondary :loading="sourceActionLoading[src.source_id]" :disabled="sourceActionLoading[src.source_id]" @click.stop="startSource(src.source_id)">
                <template #icon><NIcon :component="PlayOutline" /></template>{{ isPendingTask(src.source_id) ? '继续初始化' : '启动通道' }}
              </NButton>
              <NButton v-else size="tiny" type="warning" secondary :loading="sourceActionLoading[src.source_id]" :disabled="sourceActionLoading[src.source_id]" @click.stop="stopSource(src.source_id)">
                <template #icon><NIcon :component="PauseOutline" /></template>暂停
              </NButton>
              <NButton size="tiny" type="error" secondary :loading="sourceActionLoading[src.source_id]" :disabled="sourceActionLoading[src.source_id]" @click.stop="removeSource(src.source_id)">
                <template #icon><NIcon :component="TrashOutline" /></template>
              </NButton>
            </NSpace>
          </div>

          <div class="mms-source-card__metrics">
            <div class="mms-source-metric">
              <span class="mms-source-metric__label">IO 线程</span>
              <NTag :type="threadTagType(src.source_id, src.status.io_running)" size="small" :bordered="false">
                {{ threadTagText(src.source_id, src.status.io_running) }}
              </NTag>
            </div>
            <div class="mms-source-metric">
              <span class="mms-source-metric__label">SQL 线程</span>
              <NTag :type="threadTagType(src.source_id, src.status.sql_running)" size="small" :bordered="false">
                {{ threadTagText(src.source_id, src.status.sql_running) }}
              </NTag>
            </div>
            <div class="mms-source-metric">
              <span class="mms-source-metric__label">延迟</span>
              <span class="mms-source-metric__value">{{ bootstrapOf(src.source_id) ? '初始化中' : delayText(src.status) }}</span>
            </div>
          </div>

          <template v-if="taskFor(src.source_id)">
            <div class="mms-source-card__task">
              任务 {{ taskFor(src.source_id)!.task_id }} · {{ taskFor(src.source_id)!.current_step }}
              <NButton text type="primary" size="tiny" style="margin-left: 8px" @click="openTaskLog(taskFor(src.source_id)!.task_id)">
                查看日志
              </NButton>
            </div>
            <div v-if="taskFor(src.source_id)!.error" class="mms-source-card__error">
              任务错误：{{ taskFor(src.source_id)!.error }}
            </div>
            <NProgress type="line" :percentage="taskFor(src.source_id)!.progress" :status="taskFor(src.source_id)!.status === 'running' ? 'info' : 'warning'" :show-indicator="true" />
          </template>

          <div v-if="src.status.last_error" class="mms-source-card__error">{{ src.status.last_error }}</div>

          <NCollapse style="margin-top:8px" :default-expanded-names="[]">
            <NCollapseItem title="详情" name="detail">
              <div class="mms-detail-grid">
                <div class="mms-detail-row"><span class="mms-detail-label">通道名</span><span>{{ src.channel_name }}</span></div>
                <div class="mms-detail-row"><span class="mms-detail-label">复制账号</span><span>{{ src.repl_user }}</span></div>
                <div class="mms-detail-row">
                  <span class="mms-detail-label">库映射</span>
                  <span v-if="!src.db_mappings?.length" style="color:#999">未配置</span>
                  <span v-else>
                    <span v-for="(m, i) in src.db_mappings" :key="i">{{ m.source_db }} → {{ m.target_db }}{{ i < src.db_mappings.length - 1 ? '，' : '' }}</span>
                  </span>
                </div>
              </div>
            </NCollapseItem>
          </NCollapse>
        </div>
      </div>
    </NSpin>

    <NModal
      v-model:show="logVisible"
      preset="card"
      style="width: 760px"
      title="任务实时日志"
      @after-leave="stopLogPolling"
    >
      <div style="font-size: 12px; color: #666; margin-bottom: 8px">
        任务ID: {{ logTaskId }} ｜ 状态: {{ logTaskStatus || '-' }} ｜ 步骤: {{ logTaskStep || '-' }}
      </div>
      <NInput
        :value="logText"
        type="textarea"
        :rows="18"
        readonly
        placeholder="正在拉取任务输出..."
      />
    </NModal>
  </div>
</template>

<style scoped>
.mms-dash { max-width: 900px; margin: 0 auto; }
.mms-dash__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 20px; }
.mms-dash__title { font-size: 20px; font-weight: 700; color: #1a1a2e; margin: 0; }
.mms-metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 24px; }
.mms-metric { text-align: center; padding: 16px 12px; background: #f8f8fa; border-radius: 10px; }
.mms-metric__value { font-size: 28px; font-weight: 700; line-height: 1.2; }
.mms-metric__label { font-size: 12px; color: #999; margin-top: 2px; }
.mms-source-list { display: flex; flex-direction: column; gap: 12px; }
.mms-source-card { background: #fff; border: 1px solid #ebeef5; border-left: 3px solid; border-radius: 10px; padding: 16px 18px; }
.mms-source-card__header { display: flex; align-items: center; justify-content: space-between; margin-bottom: 12px; }
.mms-source-card__title { display: flex; align-items: center; gap: 8px; }
.mms-source-card__dot { width: 8px; height: 8px; border-radius: 50%; }
.mms-source-card__host { font-size: 12px; color: #999; }
.mms-source-card__metrics { display: flex; gap: 24px; margin-bottom: 8px; }
.mms-source-metric { display: flex; align-items: center; gap: 6px; }
.mms-source-metric__label { font-size: 12px; color: #999; }
.mms-source-metric__value { font-size: 14px; font-weight: 600; }
.mms-source-card__task { font-size: 12px; color: #666; margin-bottom: 4px; }
.mms-source-card__error { margin-top: 8px; padding: 8px 10px; background: #fff5f5; border-radius: 6px; color: #d03050; font-size: 12px; word-break: break-all; }
.mms-detail-grid { display: flex; flex-direction: column; gap: 4px; }
.mms-detail-row { display: flex; font-size: 13px; padding: 4px 0; }
.mms-detail-label { width: 80px; color: #999; flex-shrink: 0; }
</style>
