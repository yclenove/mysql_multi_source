<script setup lang="ts">
import { ref, reactive, computed } from 'vue'
import {
  NTabs, NTabPane, NCard, NButton, NSpace, NInput, NInputNumber,
  NSelect, NAlert, NCollapse, NCollapseItem, NTag, useMessage,
  NDescriptions, NDescriptionsItem, NCheckbox,
} from 'naive-ui'
import { call, isOk, getMessage, extractMsg, btConfirm } from '@/api/plugin'

const msg = useMessage()
const output = ref('')
const loading = ref(false)
const lastResponse = ref<any>(null)
const lastMethod = ref('')
const lastLabel = ref('')

const resultFieldLabels: Record<string, string> = {
  source_id: '来源 ID',
  channel_name: '通道名',
  task_id: '任务 ID',
  worker_id: 'Worker ID',
  mode: '模式',
  effective_mode: '实际模式',
  status: '状态',
  current_step: '当前步骤',
  progress: '进度',
  retry_count: '重试次数',
  max_retry: '最大重试次数',
  error: '错误信息',
  error_type: '错误类型',
  message: '提示信息',
  code: '错误码',
  content: '内容',
  count: '数量',
  mode_count: '模式数量',
  running: '运行中来源',
  stopped: '已停止来源',
  errors: '异常来源',
  total_sources: '来源总数',
  total_tasks: '任务总数',
  pending_tasks: '待执行任务',
  live_tasks: '运行中任务',
  avg_duration: '平均耗时',
  success_count: '成功数',
  failed_count: '失败数',
  recommended_mode: '推荐模式',
  reason: '原因',
  enabled: '是否启用',
  gtid_mode: 'GTID 模式',
  actions: '修复动作',
  need_restart: '是否需要重启',
  restarted: '是否已重启',
  user: '账号',
  host: '主机',
  token: '握手 Token',
  expires_at: '过期时间',
  verified: '是否验证通过',
  profile_id: '配置单 ID',
  profile_b64: '配置单内容',
  signature: '签名',
  tool_name: '工具名',
  log_path: '日志路径',
  command: '命令',
  reachable: '是否可达',
}

const resultFieldPriority: Record<string, string[]> = {
  add_source: ['source_id', 'channel_name', 'message', 'code'],
  set_db_mappings: ['source_id', 'count', 'message', 'code'],
  create_bootstrap_task: ['task_id', 'source_id', 'channel_name', 'mode', 'status', 'progress', 'current_step'],
  trigger_bootstrap_task: ['task_id', 'worker_id', 'status', 'current_step', 'progress', 'message'],
  get_bootstrap_task: ['task_id', 'source_id', 'status', 'current_step', 'progress', 'retry_count', 'error_type', 'error'],
  get_task_logs: ['task_id', 'content'],
  overview_metrics: ['total_sources', 'running', 'stopped', 'errors', 'total_tasks', 'pending_tasks', 'live_tasks', 'success_count', 'failed_count', 'avg_duration'],
  wizard_dashboard_snapshot: ['total_sources', 'running', 'stopped', 'errors', 'live_tasks'],
  master_health_check: ['status', 'message', 'code'],
  master_auto_fix_preview: ['need_restart', 'actions'],
  master_auto_fix_apply: ['message', 'code'],
  master_create_repl_user: ['user', 'host', 'message', 'code'],
  check_bootstrap_tools: ['tool_name', 'message', 'code'],
  get_tool_install_log: ['content'],
  replica_verify_profile: ['verified', 'message', 'code'],
  replica_import_profile: ['source_id', 'message', 'code'],
  master_create_handshake: ['token', 'expires_at'],
  handshake_status: ['status', 'expires_at', 'message', 'code'],
  health_check: ['message', 'code'],
  get_gtid_status: ['gtid_mode', 'enabled'],
}

const responseStatus = computed<'success' | 'error' | 'info'>(() => {
  if (!lastResponse.value) return 'info'
  return lastResponse.value?.status === true ? 'success' : 'error'
})

const responseMessage = computed(() => {
  if (!lastResponse.value) return ''
  return getMessage(lastResponse.value) || (lastResponse.value?.status === true ? '执行成功' : '执行失败')
})

const responseData = computed(() => {
  if (!lastResponse.value) return null
  const raw = extractMsg(lastResponse.value)
  if (raw && typeof raw === 'object') return raw
  return null
})

const responseEntries = computed(() => {
  const data = responseData.value
  if (!data || Array.isArray(data)) return []
  const method = lastMethod.value
  const priority = resultFieldPriority[method] || []
  const seen = new Set<string>()
  const ordered: Array<[string, any]> = []

  for (const key of priority) {
    if (key in data) {
      ordered.push([key, (data as Record<string, any>)[key]])
      seen.add(key)
    }
  }

  for (const [key, value] of Object.entries(data)) {
    if (seen.has(key)) continue
    ordered.push([key, value])
  }

  return ordered.slice(0, 12)
})

const responseListPreview = computed(() => {
  const data = responseData.value
  if (!Array.isArray(data)) return []
  return data.slice(0, 10)
})

const responseHighlightCards = computed(() => {
  const data = responseData.value
  const method = lastMethod.value
  if (!data || Array.isArray(data)) return []

  const pick = (...keys: string[]) => keys
    .filter((key) => key in data)
    .map((key) => ({ key, label: resultFieldLabels[key] || key, value: formatValue((data as Record<string, any>)[key]) }))

  if (method === 'get_bootstrap_task') {
    return pick('task_id', 'status', 'current_step', 'progress', 'retry_count', 'error_type')
  }
  if (method === 'create_bootstrap_task' || method === 'trigger_bootstrap_task') {
    return pick('task_id', 'source_id', 'status', 'progress', 'worker_id')
  }
  if (method === 'overview_metrics' || method === 'wizard_dashboard_snapshot') {
    return pick('total_sources', 'running', 'stopped', 'errors', 'live_tasks', 'pending_tasks')
  }
  if (method === 'master_create_handshake') {
    return pick('token', 'expires_at')
  }
  if (method === 'master_auto_fix_preview') {
    return pick('need_restart', 'actions')
  }
  if (method === 'master_create_repl_user') {
    return pick('user', 'host')
  }
  if (method === 'get_gtid_status') {
    return pick('gtid_mode', 'enabled')
  }
  return []
})

const responseSectionTitle = computed(() => {
  if (responseHighlightCards.value.length) return '重点结果'
  if (responseEntries.value.length) return '结构化摘要'
  if (responseListPreview.value.length) return '列表预览'
  return '原始响应'
})

function getFieldLabel(key: string) {
  return resultFieldLabels[key] || key
}

function formatValue(value: any) {
  if (value === null || value === undefined || value === '') return '-'
  if (typeof value === 'string' || typeof value === 'number' || typeof value === 'boolean') return String(value)
  return JSON.stringify(value, null, 2)
}

const confirmMap: Record<string, { title: string; content: (data: Record<string, any>) => string }> = {
  cancel_bootstrap_task: {
    title: '取消初始化任务',
    content: (data) => `确认取消任务 ${data.task_id || '-'}？已开始的初始化流程会被标记为取消。`,
  },
  install_bootstrap_tool: {
    title: '安装初始化工具',
    content: (data) => `确认安装 ${data.tool_name || '所选工具'}？该操作会修改当前系统软件环境。`,
  },
  run_stress_wizard: {
    title: '开始压测',
    content: (data) => `确认开始压测？将创建 ${data.source_count || 0} 个来源、每个来源 ${data.task_per_source || 0} 个任务，可能占用较多系统资源。`,
  },
  master_auto_fix_apply: {
    title: '执行主库修复',
    content: (data) => `确认执行主库一键修复？这会修改主库复制相关配置${data.auto_restart === '1' ? '，并在需要时自动重启 MySQL' : ''}。`,
  },
  master_restart_mysql: {
    title: '重启 MySQL',
    content: () => '确认重启当前 MySQL 服务？短时间内会中断数据库连接。',
  },
  rollback_snapshot: {
    title: '回滚快照',
    content: (data) => `确认回滚快照 ${data.snapshot_id || '-'}？这会恢复历史配置，可能覆盖当前主库设置。`,
  },
}

async function ensureConfirmed(method: string, data: Record<string, any>) {
  const config = confirmMap[method]
  if (!config) return true
  return btConfirm(config.title, config.content(data))
}

function validateBeforeRun(method: string, data: Record<string, any>) {
  const requireFields = (fields: Array<[string, string]>) => {
    for (const [key, label] of fields) {
      if (!String(data[key] ?? '').trim()) {
        msg.warning(`请先填写${label}`)
        return false
      }
    }
    return true
  }

  if (method === 'add_source') {
    if (!requireFields([
      ['source_id', '来源 ID'],
      ['channel_name', '通道名'],
      ['master_host', '主库地址'],
      ['repl_user', '复制账号'],
      ['repl_password', '复制密码'],
    ])) return false
    const port = Number(data.master_port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      msg.warning('主库端口必须在 1-65535 之间')
      return false
    }
  }

  if (method === 'test_master_connection_direct') {
    if (!requireFields([
      ['master_host', '主库地址'],
      ['repl_user', '复制账号'],
      ['repl_password', '复制密码'],
    ])) return false
    const port = Number(data.master_port)
    if (!Number.isInteger(port) || port < 1 || port > 65535) {
      msg.warning('端口必须在 1-65535 之间')
      return false
    }
  }

  if (method === 'set_db_mappings') {
    if (!requireFields([
      ['source_id', '来源 ID'],
      ['mappings', '映射 JSON'],
    ])) return false
    try {
      const parsed = JSON.parse(String(data.mappings))
      if (!Array.isArray(parsed)) {
        msg.warning('映射 JSON 必须是数组格式')
        return false
      }
    } catch {
      msg.warning('映射 JSON 格式不正确')
      return false
    }
  }

  if (['list_db_mappings', 'create_bootstrap_task'].includes(method)) {
    if (!requireFields([['source_id', '来源 ID']])) return false
  }

  if (['trigger_bootstrap_task', 'get_bootstrap_task', 'get_task_logs', 'cancel_bootstrap_task'].includes(method)) {
    if (!requireFields([['task_id', '任务 ID']])) return false
  }

  if (['rollback_snapshot'].includes(method)) {
    if (!requireFields([['snapshot_id', '快照 ID']])) return false
  }

  if (['replica_verify_profile', 'replica_import_profile', 'master_create_handshake'].includes(method)) {
    if (!requireFields([['profile_b64', '配置单内容']])) return false
  }

  if (['replica_accept_handshake', 'handshake_status'].includes(method)) {
    if (!requireFields([['token', '握手 Token']])) return false
  }

  if (['master_create_repl_user', 'master_auto_fix_apply'].includes(method)) {
    if (!requireFields([
      ['repl_user', '复制账号'],
      ['repl_password', '复制密码'],
      ['replica_host', '允许连接主机'],
    ])) return false
    if (!/^[A-Za-z0-9_]{1,32}$/.test(String(data.repl_user).trim())) {
      msg.warning('复制账号仅允许字母、数字、下划线，最长 32 位')
      return false
    }
  }

  return true
}

async function run(method: string, data: Record<string, any> = {}, label = '') {
  if (!validateBeforeRun(method, data)) return
  if (!await ensureConfirmed(method, data)) return
  loading.value = true
  output.value = ''
  lastResponse.value = null
  lastMethod.value = method
  lastLabel.value = label || method
  try {
    const res = await call(method, data)
    lastResponse.value = res
    output.value = JSON.stringify(res, null, 2)
    if (isOk(res)) msg.success(label || method + ' 成功')
    else msg.error(getMessage(res) || method + ' 失败')
  } catch (e: any) {
    lastResponse.value = { status: false, msg: String(e) }
    output.value = String(e)
    msg.error(String(e))
  } finally {
    loading.value = false
  }
}

// === quick access forms ===
const sourceForm = reactive({
  source_id: '', channel_name: '', master_host: '', master_port: 3306,
  repl_user: '', repl_password: '',
})
const mappingForm = reactive({ source_id: '', mappings: '' })
const taskForm = reactive({ source_id: '', mode: 'auto', task_id: '' })
const masterForm = reactive({
  repl_user: '', repl_password: '', replica_host: '%', auto_restart: false,
})
const connectForm = reactive({
  master_host: '', master_port: 3306, repl_user: '', repl_password: '',
})
const profileB64 = ref('')
const hsToken = ref('')
const toolName = ref('xtrabackup')
const snapshotId = ref('')
const stressForm = reactive({ source_count: 1, task_per_source: 1, mode: 'auto' })

const modeOptions = [
  { label: 'auto', value: 'auto' },
  { label: 'physical', value: 'physical' },
  { label: 'logical', value: 'logical' },
]
const toolOptions = [
  { label: 'xtrabackup', value: 'xtrabackup' },
  { label: 'mariabackup', value: 'mariabackup' },
]
</script>

<template>
  <div class="mms-expert">
    <NTabs type="line" animated>
      <!-- Tab 1: Source management -->
      <NTabPane name="sources" tab="快速接入">
        <NCard title="添加主库来源" size="small">
          <NSpace vertical :size="8" style="max-width:420px">
            <NInput v-model:value="sourceForm.source_id" placeholder="来源 ID (如 m1)" />
            <NInput v-model:value="sourceForm.channel_name" placeholder="通道名 (如 ch_m1)" />
            <NInput v-model:value="sourceForm.master_host" placeholder="主库地址" />
            <NInputNumber v-model:value="sourceForm.master_port" :min="1" :max="65535" placeholder="端口" />
            <NInput v-model:value="sourceForm.repl_user" placeholder="复制账号" />
            <NInput v-model:value="sourceForm.repl_password" type="password" show-password-on="click" placeholder="复制密码" />
          </NSpace>
          <NButton type="primary" size="small" style="margin-top:8px" @click="run('add_source', sourceForm, '添加来源')">添加</NButton>
        </NCard>

        <NCard title="连接测试" size="small" style="margin-top:12px">
          <NSpace :size="8" style="max-width:420px" vertical>
            <NInput v-model:value="connectForm.master_host" placeholder="主库地址" />
            <NInputNumber v-model:value="connectForm.master_port" :min="1" :max="65535" />
            <NInput v-model:value="connectForm.repl_user" placeholder="账号" />
            <NInput v-model:value="connectForm.repl_password" type="password" show-password-on="click" placeholder="密码" />
          </NSpace>
          <NButton size="small" style="margin-top:8px" @click="run('test_master_connection_direct', connectForm, '连接测试')">测试</NButton>
        </NCard>

        <NCard title="库映射" size="small" style="margin-top:12px">
          <NSpace :size="8" style="max-width:420px" vertical>
            <NInput v-model:value="mappingForm.source_id" placeholder="来源 ID" />
            <NInput v-model:value="mappingForm.mappings" type="textarea" :rows="3" placeholder='[{"source_db":"xx","target_db":"yy"}]' />
          </NSpace>
          <NSpace :size="8" style="margin-top:8px">
            <NButton size="small" @click="run('set_db_mappings', mappingForm, '保存映射')">保存映射</NButton>
            <NButton size="small" @click="run('list_db_mappings', { source_id: mappingForm.source_id }, '读取映射')">载入映射</NButton>
          </NSpace>
        </NCard>
      </NTabPane>

      <!-- Tab 2: Bootstrap tasks -->
      <NTabPane name="tasks" tab="初始化任务">
        <NCard title="创建 / 执行任务" size="small">
          <NSpace :size="8" style="max-width:420px" vertical>
            <NInput v-model:value="taskForm.source_id" placeholder="来源 ID" />
            <NSelect v-model:value="taskForm.mode" :options="modeOptions" size="small" />
            <NInput v-model:value="taskForm.task_id" placeholder="任务 ID（执行/取消/详情时填）" />
          </NSpace>
          <NSpace :size="8" style="margin-top:8px" wrap>
            <NButton size="small" type="primary" @click="run('create_bootstrap_task', { source_id: taskForm.source_id, mode: taskForm.mode }, '创建任务')">创建任务</NButton>
            <NButton size="small" @click="run('trigger_bootstrap_task', { task_id: taskForm.task_id }, '触发执行')">后台执行</NButton>
            <NButton size="small" @click="run('get_bootstrap_task', { task_id: taskForm.task_id }, '任务详情')">任务详情</NButton>
            <NButton size="small" @click="run('get_task_logs', { task_id: taskForm.task_id }, '任务日志')">任务日志</NButton>
            <NButton size="small" type="warning" @click="run('cancel_bootstrap_task', { task_id: taskForm.task_id }, '取消任务')">取消任务</NButton>
            <NButton size="small" @click="run('get_bootstrap_tasks', {}, '任务列表')">全部任务</NButton>
            <NButton size="small" @click="run('recover_bootstrap_tasks', {}, '恢复卡死任务')">恢复卡死任务</NButton>
          </NSpace>
        </NCard>

        <NCollapse style="margin-top:12px">
          <NCollapseItem title="高级：工具安装与压测" name="adv">
            <NSpace :size="8" wrap>
              <NButton size="small" @click="run('check_bootstrap_tools', {}, '检测工具')">检测工具</NButton>
              <NSelect v-model:value="toolName" :options="toolOptions" size="small" style="width:160px" />
              <NButton size="small" @click="run('install_bootstrap_tool', { tool_name: toolName }, '安装工具')">安装工具</NButton>
              <NButton size="small" @click="run('get_tool_install_log', {}, '安装日志')">安装日志</NButton>
            </NSpace>
            <NSpace :size="8" wrap style="margin-top:8px">
              <NInputNumber v-model:value="stressForm.source_count" :min="1" :max="10" size="small" />
              <NInputNumber v-model:value="stressForm.task_per_source" :min="1" :max="10" size="small" />
              <NButton size="small" type="warning" @click="run('run_stress_wizard', stressForm, '压测')">开始压测</NButton>
              <NButton size="small" @click="run('get_stress_report', {}, '压测报告')">压测报告</NButton>
            </NSpace>
          </NCollapseItem>
        </NCollapse>
      </NTabPane>

      <!-- Tab 3: Monitoring -->
      <NTabPane name="monitor" tab="运行监控">
        <NSpace :size="8" wrap>
          <NButton size="small" @click="run('health_check', {}, '健康检查')">健康检查</NButton>
          <NButton size="small" @click="run('list_sources', {}, '来源列表')">刷新来源</NButton>
          <NButton size="small" @click="run('overview_metrics', {}, '总览指标')">总览指标</NButton>
          <NButton size="small" @click="run('get_gtid_status', {}, 'GTID 状态')">GTID 状态</NButton>
          <NButton size="small" @click="run('wizard_dashboard_snapshot', {}, '仪表盘快照')">仪表盘快照</NButton>
          <NButton size="small" @click="run('wizard_diagnose_all', {}, '全量诊断')">全量诊断</NButton>
        </NSpace>
      </NTabPane>

      <!-- Tab 4: Master ops -->
      <NTabPane name="master" tab="主库运维">
        <NCard title="主库检测与修复" size="small">
          <NSpace :size="8" wrap>
            <NButton size="small" @click="run('master_health_check', {}, '主库检测')">主库检测</NButton>
            <NButton size="small" @click="run('master_auto_fix_preview', {}, '修复预览')">修复预览</NButton>
            <NButton size="small" type="warning" @click="run('master_auto_fix_apply', { auto_restart: masterForm.auto_restart ? '1' : '0', repl_user: masterForm.repl_user, repl_password: masterForm.repl_password, replica_host: masterForm.replica_host }, '一键修复')">一键修复</NButton>
            <NButton size="small" type="error" @click="run('master_restart_mysql', {}, '重启MySQL')">重启 MySQL</NButton>
          </NSpace>
          <NSpace :size="8" vertical style="margin-top:8px; max-width:320px">
            <NCheckbox v-model:checked="masterForm.auto_restart">修复后自动重启</NCheckbox>
            <NInput v-model:value="masterForm.repl_user" placeholder="repl_user" size="small" />
            <NInput v-model:value="masterForm.repl_password" type="password" show-password-on="click" placeholder="密码" size="small" />
            <NInput v-model:value="masterForm.replica_host" placeholder="从库IP或%" size="small" />
            <NButton size="small" @click="run('master_create_repl_user', { repl_user: masterForm.repl_user, repl_password: masterForm.repl_password, replica_host: masterForm.replica_host }, '创建复制账号')">仅创建复制账号</NButton>
            <NButton size="small" @click="run('master_list_accounts', { limit: '300' }, '账号列表')">查看账号列表</NButton>
          </NSpace>
        </NCard>

        <NCard title="审计与快照" size="small" style="margin-top:12px">
          <NSpace :size="8" wrap>
            <NButton size="small" @click="run('list_audit_logs', {}, '审计日志')">审计日志</NButton>
            <NButton size="small" @click="run('list_change_snapshots', {}, '快照列表')">快照列表</NButton>
            <NInput v-model:value="snapshotId" placeholder="快照ID" size="small" style="width:200px" />
            <NButton size="small" type="warning" @click="run('rollback_snapshot', { snapshot_id: snapshotId }, '回滚快照')">回滚快照</NButton>
          </NSpace>
        </NCard>

        <NCard title="签名配置与握手" size="small" style="margin-top:12px">
          <NSpace :size="8" vertical style="max-width:420px">
            <NInput v-model:value="profileB64" type="textarea" :rows="3" placeholder="profile_b64" />
            <NSpace :size="8" wrap>
              <NButton size="small" @click="run('replica_verify_profile', { profile_b64: profileB64 }, '验证配置')">验证配置</NButton>
              <NButton size="small" @click="run('replica_import_profile', { profile_b64: profileB64 }, '导入配置')">导入配置</NButton>
            </NSpace>
            <NInput v-model:value="hsToken" placeholder="握手 Token" />
            <NSpace :size="8" wrap>
              <NButton size="small" @click="run('master_create_handshake', { profile_b64: profileB64 }, '创建握手')">创建握手</NButton>
              <NButton size="small" @click="run('replica_accept_handshake', { token: hsToken }, '接收握手')">接收握手</NButton>
              <NButton size="small" @click="run('handshake_status', { token: hsToken }, '握手状态')">握手状态</NButton>
            </NSpace>
          </NSpace>
        </NCard>
      </NTabPane>
    </NTabs>

    <!-- Output panel -->
    <NCard v-if="output" title="执行结果" size="small" style="margin-top:16px">
      <NAlert :type="responseStatus" :bordered="false" style="margin-bottom:12px">
        <strong>{{ lastLabel || lastMethod }}</strong>
        <span style="margin-left:8px">{{ responseMessage }}</span>
      </NAlert>

      <NDescriptions :column="2" size="small" bordered style="margin-bottom:12px">
        <NDescriptionsItem label="调用方法">{{ lastMethod || '-' }}</NDescriptionsItem>
        <NDescriptionsItem label="执行状态">
          <NTag size="small" :bordered="false" :type="responseStatus === 'success' ? 'success' : (responseStatus === 'error' ? 'error' : 'info')">
            {{ responseStatus === 'success' ? '成功' : responseStatus === 'error' ? '失败' : '待执行' }}
          </NTag>
        </NDescriptionsItem>
      </NDescriptions>

      <NCard v-if="responseHighlightCards.length" :title="responseSectionTitle" size="small" embedded style="margin-bottom:12px">
        <div class="mms-expert__cards">
          <div v-for="item in responseHighlightCards" :key="item.key" class="mms-expert__card">
            <div class="mms-expert__card-label">{{ item.label }}</div>
            <pre class="mms-expert__card-value">{{ item.value }}</pre>
          </div>
        </div>
      </NCard>

      <NCard v-if="responseEntries.length" title="结构化摘要" size="small" embedded style="margin-bottom:12px">
        <NDescriptions :column="1" size="small" bordered>
          <NDescriptionsItem v-for="([key, value]) in responseEntries" :key="key" :label="getFieldLabel(key)">
            <pre class="mms-expert__value">{{ formatValue(value) }}</pre>
          </NDescriptionsItem>
        </NDescriptions>
      </NCard>

      <NCard v-else-if="responseListPreview.length" title="列表预览" size="small" embedded style="margin-bottom:12px">
        <pre class="mms-expert__output">{{ JSON.stringify(responseListPreview, null, 2) }}</pre>
      </NCard>

      <NCard title="原始响应" size="small" embedded>
        <pre class="mms-expert__output">{{ output }}</pre>
      </NCard>
    </NCard>
  </div>
</template>

<style scoped>
.mms-expert {
  max-width: 960px;
  margin: 0 auto;
}

.mms-expert__output {
  max-height: 400px;
  overflow: auto;
  font-size: 12px;
  background: #f9f9f9;
  padding: 8px;
  border-radius: 4px;
  white-space: pre-wrap;
  word-break: break-all;
  margin: 0;
}

.mms-expert__value {
  margin: 0;
  white-space: pre-wrap;
  word-break: break-all;
  font-size: 12px;
  line-height: 1.5;
}

.mms-expert__cards {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
  gap: 10px;
}

.mms-expert__card {
  padding: 10px 12px;
  border: 1px solid #ececf2;
  border-radius: 8px;
  background: #fafafe;
}

.mms-expert__card-label {
  font-size: 12px;
  color: #7a7f8c;
  margin-bottom: 6px;
}

.mms-expert__card-value {
  margin: 0;
  font-size: 13px;
  color: #1f2430;
  line-height: 1.5;
  white-space: pre-wrap;
  word-break: break-word;
}
</style>
