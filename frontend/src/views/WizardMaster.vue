<script setup lang="ts">
import { ref, reactive, onMounted, computed, watch, nextTick } from 'vue'
import {
  NSteps, NStep, NButton, NSpace, NInput, NInputNumber, NCheckbox, NAlert,
  NIcon, useMessage, NSpin, NResult, NSelect,
} from 'naive-ui'
import {
  CopyOutline, CheckmarkCircleOutline, ShieldCheckmarkOutline,
  RefreshOutline, ArrowForwardOutline,
} from '@vicons/ionicons5'
import { call, isOk, getMessage, extractMsg } from '@/api/plugin'
import { useEnvStore } from '@/store/env'
import { scrollPluginTop } from '@/utils/scroll'

const env = useEnvStore()
const msg = useMessage()

const viewMode = ref<'summary' | 'wizard'>('wizard')
const step = ref(1)
const loading = ref(false)

const healthItems = ref<any[]>([])
const healthSummary = ref({ ok: 0, warn: 0, fail: 0 })
const healthError = ref('')
const healthChecked = ref(false)

async function runHealthCheck() {
  loading.value = true
  healthError.value = ''
  const res = await call('master_health_check')
  loading.value = false
  healthChecked.value = true
  if (isOk(res)) {
    const d = extractMsg(res)
    healthItems.value = d?.items || []
    healthSummary.value = d?.summary || { ok: 0, warn: 0, fail: 0 }
    if (healthItems.value.length === 0) {
      healthError.value = '未获取到检测结果，请确认 MySQL 正在运行'
    }
  } else {
    healthError.value = getMessage(res) || '环境检测失败，请确认 MySQL 服务正常运行'
  }
}

const allHealthOk = computed(() => healthSummary.value.fail === 0 && healthItems.value.length > 0)

const previewActions = ref<string[]>([])
const previewError = ref('')
const needRestart = ref(false)

async function runPreview() {
  loading.value = true
  previewError.value = ''
  const res = await call('master_auto_fix_preview')
  loading.value = false
  if (isOk(res)) {
    const d = extractMsg(res)
    previewActions.value = d?.actions || []
    needRestart.value = d?.need_restart || false
  } else {
    previewError.value = getMessage(res) || '获取修复预览失败'
  }
}

const autoRestart = ref(true)
const fixDone = ref(false)
const snapshotId = ref('')
const fixError = ref('')

async function runFix() {
  loading.value = true
  fixError.value = ''
  const res = await call('master_auto_fix_apply', {
    auto_restart: autoRestart.value ? '1' : '0',
    repl_user: replForm.user,
    repl_password: replForm.password,
    replica_host: replForm.hostMode === 'all' ? '%' : replForm.customHost,
  })
  loading.value = false
  if (isOk(res)) {
    const d = extractMsg(res)
    snapshotId.value = d?.snapshot_id || ''
    fixDone.value = true
    msg.success('修复完成')
    env.detectEnv()
  } else {
    fixError.value = getMessage(res) || '修复失败，请查看专家视图获取详情'
  }
}

const replForm = reactive({
  user: 'repl_user',
  password: '',
  hostMode: 'all' as 'all' | 'custom',
  customHost: '',
})
const exportForm = reactive({
  masterHost: '',
  masterPort: 3306,
})
const profileB64 = ref('')
const exportError = ref('')

const hostOptions = [
  { label: '所有 IP 均可连接（推荐）', value: 'all' },
  { label: '仅指定 IP', value: 'custom' },
]

function generatePassword() {
  const chars = 'ABCDEFGHJKMNPQRSTUVWXYZabcdefghjkmnpqrstuvwxyz23456789!@#$'
  let pwd = ''
  for (let i = 0; i < 16; i++) pwd += chars[Math.floor(Math.random() * chars.length)]
  replForm.password = pwd
}

async function exportProfile() {
  if (!replForm.password) {
    msg.warning('请先生成或输入复制密码')
    return
  }
  if (!exportForm.masterHost) {
    msg.warning('请填写主库地址（从库需要通过此地址连接本机）')
    return
  }
  loading.value = true
  exportError.value = ''

  const syncRes = await call('master_create_repl_user', {
    repl_user: replForm.user,
    repl_password: replForm.password,
    replica_host: replForm.hostMode === 'all' ? '%' : replForm.customHost || '%',
  })
  if (!isOk(syncRes)) {
    exportError.value = getMessage(syncRes) || '同步复制账号密码失败'
    loading.value = false
    return
  }

  const res = await call('master_export_signed_profile', {
    source_id: 'm1',
    channel_name: 'ch_m1',
    master_host: exportForm.masterHost,
    master_port: String(exportForm.masterPort),
    repl_user: replForm.user,
    repl_password: replForm.password,
  })
  loading.value = false
  if (isOk(res)) {
    profileB64.value = extractMsg(res)?.profile_b64 || ''
    msg.success('配置单已生成，复制账号密码已同步')
  } else {
    exportError.value = getMessage(res) || '导出配置单失败'
  }
}

function copyProfile() {
  navigator.clipboard.writeText(profileB64.value)
  msg.success('已复制到剪贴板')
}

function initExportForm() {
  if (!exportForm.masterHost) {
    exportForm.masterHost = env.serverIp || ''
  }
  if (exportForm.masterPort === 3306 && env.mysqlPort) {
    exportForm.masterPort = env.mysqlPort
  }
}

function goStep(n: number) {
  step.value = n
  if (n === 1) runHealthCheck()
  if (n === 2) runPreview()
  if (n === 4) initExportForm()
}

watch([step, viewMode], () => {
  nextTick(() => scrollPluginTop())
})

function startReconfigure() {
  viewMode.value = 'wizard'
  step.value = 1
  runHealthCheck()
}

function statusIcon(s: string): string {
  return s === 'ok' ? '✓' : s === 'warn' ? '!' : '✗'
}

// ---------- Physical mode: install replica SSH handshake / pubkey ----------
const replicaPubKey = ref('')
const pubKeyInstalling = ref(false)
const pubKeyResult = ref<{ type: 'success' | 'error' | ''; text: string; meta?: any }>({ type: '', text: '' })

async function installReplicaPubKey() {
  const key = replicaPubKey.value.trim()
  if (!key) { msg.warning('请粘贴从库生成的握手单或公钥'); return }
  pubKeyInstalling.value = true
  pubKeyResult.value = { type: '', text: '' }
  const res = await call('master_import_handshake', { payload: key })
  pubKeyInstalling.value = false
  if (isOk(res)) {
    const data = extractMsg(res) || {}
    if (data?.already_installed) {
      pubKeyResult.value = { type: 'success', text: '该公钥已存在，物理模式可用', meta: data.meta }
    } else {
      const src = data.meta?.replica_ip ? `（来自 ${data.meta.replica_ip}）` : ''
      pubKeyResult.value = { type: 'success', text: `握手单已安装${src}，物理模式已开通`, meta: data.meta }
    }
    msg.success(pubKeyResult.value.text)
  } else {
    pubKeyResult.value = { type: 'error', text: getMessage(res) || '安装失败' }
    msg.error(pubKeyResult.value.text)
  }
}

onMounted(() => {
  if (env.masterConfigured) {
    viewMode.value = 'summary'
    runHealthCheck()
  } else {
    viewMode.value = 'wizard'
    runHealthCheck()
  }
})
</script>

<template>
  <div class="mms-wizard">
    <!-- ==================== Summary View (already configured) ==================== -->
    <template v-if="viewMode === 'summary'">
      <div class="mms-wizard__header">
        <h2 class="mms-wizard__title">主库配置状态</h2>
        <p class="mms-wizard__desc">
          上次配置时间：{{ env.masterSetup?.configured_at || '未知' }}
        </p>
      </div>

      <div class="mms-step-card">
        <div class="mms-step-card__header">
          <h3>当前环境检查</h3>
          <NButton size="small" :loading="loading" @click="runHealthCheck">
            <template #icon><NIcon :component="RefreshOutline" :size="14" /></template>
            刷新检查
          </NButton>
        </div>

        <div v-if="loading && !healthChecked" class="mms-loading-hint">
          正在检测 MySQL 环境…
        </div>

        <NAlert v-if="healthError" type="error" :bordered="false" style="margin-bottom:12px">
          {{ healthError }}
        </NAlert>

        <div v-if="healthItems.length" class="mms-check-list">
          <div v-for="item in healthItems" :key="item.name" class="mms-check-item">
            <div class="mms-check-item__status" :class="`mms-check-item__status--${item.status}`">
              {{ statusIcon(item.status) }}
            </div>
            <div class="mms-check-item__body">
              <div class="mms-check-item__name">{{ item.name }}</div>
              <div class="mms-check-item__detail">
                当前：<code>{{ item.current }}</code>
                <span v-if="item.expected !== item.current" class="mms-check-item__expect">
                  → 期望：<code>{{ item.expected }}</code>
                </span>
              </div>
            </div>
          </div>
        </div>

        <div v-if="healthItems.length" class="mms-summary-bar">
          <span class="mms-summary-bar__ok">✓ {{ healthSummary.ok }} 正常</span>
          <span v-if="healthSummary.warn" class="mms-summary-bar__warn">⚠ {{ healthSummary.warn }} 警告</span>
          <span v-if="healthSummary.fail" class="mms-summary-bar__fail">✗ {{ healthSummary.fail }} 异常</span>
        </div>

        <NAlert v-if="allHealthOk && healthChecked && !loading" type="success" :bordered="false" style="margin-top:12px">
          <template #icon><NIcon :component="CheckmarkCircleOutline" /></template>
          所有配置项检查正常，主库运行状态良好。
        </NAlert>
      </div>

      <div class="mms-summary-info" v-if="env.masterSetup?.repl_user">
        <div class="mms-step-card" style="margin-top:16px">
          <div class="mms-step-card__header">
            <h3>复制账号信息</h3>
          </div>
          <div class="mms-info-row">
            <span class="mms-info-label">复制账号</span>
            <code>{{ env.masterSetup.repl_user }}</code>
          </div>
          <p class="mms-info-hint">如需给从库发送配置单，点击下方"导出配置单"</p>
        </div>
      </div>

      <div class="mms-step-card" style="margin-top:16px">
        <div class="mms-step-card__header">
          <h3>物理模式 · 粘贴握手单（可选）</h3>
        </div>
        <NAlert type="info" :bordered="false" style="margin-bottom:10px">
          选择"物理 (xtrabackup)"模式时，从库需要 SSH 免密访问本主库。
          请在从库"启用物理模式"中点击"生成握手单"，复制得到一串 base64 文本，粘贴到下方一键开通。
          也支持粘贴裸公钥 (ssh-ed25519 / ssh-rsa) 作为兼容方式。
        </NAlert>
        <NInput
          v-model:value="replicaPubKey"
          type="textarea"
          :rows="4"
          placeholder="粘贴从库生成的握手单（base64）或 ssh-ed25519 AAAA... 公钥"
          style="font-family: ui-monospace,Menlo,monospace; font-size:12px"
        />
        <div style="margin-top:10px">
          <NButton type="primary" :loading="pubKeyInstalling" @click="installReplicaPubKey">
            一键安装到 authorized_keys
          </NButton>
        </div>
        <NAlert
          v-if="pubKeyResult.text"
          :type="pubKeyResult.type === 'success' ? 'success' : 'error'"
          :bordered="false"
          style="margin-top:10px"
        >
          {{ pubKeyResult.text }}
          <div v-if="pubKeyResult.meta?.replica_hostname" style="font-size:12px; opacity:0.75; margin-top:4px">
            来源：{{ pubKeyResult.meta.replica_hostname }}
            <span v-if="pubKeyResult.meta.source_id">（源ID {{ pubKeyResult.meta.source_id }}）</span>
          </div>
        </NAlert>
      </div>

      <div class="mms-summary-actions">
        <NButton @click="env.navigate('landing')">返回首页</NButton>
        <NButton type="primary" @click="viewMode = 'wizard'; step = 4; initExportForm()">
          导出配置单
        </NButton>
        <NButton type="warning" ghost @click="startReconfigure">
          重新配置
        </NButton>
      </div>
    </template>

    <!-- ==================== Wizard View ==================== -->
    <template v-else>
      <div class="mms-wizard__header">
        <h2 class="mms-wizard__title">帮我成为主库</h2>
        <p class="mms-wizard__desc">检测环境 → 修复配置 → 创建账号 → 导出配置单</p>
      </div>

      <NSteps :current="step" size="small" class="mms-wizard__steps">
        <NStep title="环境体检" />
        <NStep title="修复预览" />
        <NStep title="执行修复" />
        <NStep title="导出配置单" />
      </NSteps>

    <!-- Step 1 -->
    <div v-if="step === 1" class="mms-step-card">
      <div class="mms-step-card__header">
        <h3>主库环境体检</h3>
        <NButton size="small" :loading="loading" @click="runHealthCheck">
          {{ loading ? '检测中…' : '重新检测' }}
        </NButton>
      </div>

      <div v-if="loading && !healthChecked" class="mms-loading-hint">
        正在检测 MySQL 环境，请稍候…
      </div>

      <NAlert v-if="healthError" type="error" :bordered="false" style="margin-bottom:12px">
        {{ healthError }}
      </NAlert>

      <div v-if="healthItems.length" class="mms-check-list">
        <div v-for="item in healthItems" :key="item.name" class="mms-check-item">
          <div class="mms-check-item__status" :class="`mms-check-item__status--${item.status}`">
            {{ statusIcon(item.status) }}
          </div>
          <div class="mms-check-item__body">
            <div class="mms-check-item__name">{{ item.name }}</div>
            <div class="mms-check-item__detail">
              当前：<code>{{ item.current }}</code>
              <span v-if="item.expected !== item.current" class="mms-check-item__expect">
                → 期望：<code>{{ item.expected }}</code>
              </span>
            </div>
          </div>
        </div>
      </div>

      <div v-if="healthItems.length" class="mms-summary-bar">
        <span class="mms-summary-bar__ok">✓ {{ healthSummary.ok }} 正常</span>
        <span v-if="healthSummary.warn" class="mms-summary-bar__warn">⚠ {{ healthSummary.warn }} 警告</span>
        <span v-if="healthSummary.fail" class="mms-summary-bar__fail">✗ {{ healthSummary.fail }} 异常</span>
      </div>

      <div v-if="!loading && healthChecked && !healthError && healthItems.length === 0" class="mms-loading-hint">
        未获取到检测项，请点击"重新检测"重试
      </div>

      <div class="mms-step-actions">
        <NButton type="primary" :disabled="loading" @click="goStep(2)">
          {{ healthSummary.fail > 0 ? '下一步：去修复' : '下一步' }}
        </NButton>
      </div>
    </div>

    <!-- Step 2 -->
    <div v-if="step === 2" class="mms-step-card">
      <NSpin :show="loading">
        <div class="mms-step-card__header">
          <h3>修复预览</h3>
        </div>

        <NAlert v-if="previewError" type="error" :bordered="false" style="margin-bottom:12px">
          {{ previewError }}
        </NAlert>

        <template v-if="!previewError">
          <NAlert v-if="previewActions.length === 0 && !loading" type="success" :bordered="false" style="margin-bottom:12px">
            所有检查项均正常，无需修复。可以直接跳到导出配置单。
          </NAlert>
          <div v-if="previewActions.length > 0">
            <p style="color:#666; margin: 0 0 12px">以下变更将被应用到 MySQL 配置文件：</p>
            <div class="mms-preview-list">
              <div v-for="(a, i) in previewActions" :key="i" class="mms-preview-item">
                <NIcon :component="CheckmarkCircleOutline" :size="16" color="#18a058" />
                <span>{{ a }}</span>
              </div>
            </div>
            <NAlert v-if="needRestart" type="warning" :bordered="false" style="margin-top:12px">
              部分修改需要重启 MySQL 才能生效。
            </NAlert>
          </div>
        </template>

        <div class="mms-step-actions">
          <NButton @click="goStep(1)">上一步</NButton>
          <NButton type="primary" :disabled="loading" @click="previewActions.length > 0 ? (step = 3) : (step = 4, initExportForm())">
            {{ previewActions.length > 0 ? '下一步：执行修复' : '跳到导出配置单' }}
          </NButton>
        </div>
      </NSpin>
    </div>

    <!-- Step 3 -->
    <div v-if="step === 3" class="mms-step-card">
      <div class="mms-step-card__header">
        <h3>执行修复</h3>
      </div>

      <div class="mms-form-section">
        <NCheckbox v-model:checked="autoRestart">
          修复后自动重启 MySQL（使配置生效）
        </NCheckbox>
      </div>

      <div class="mms-form-section">
        <div class="mms-form-section__title">同时创建复制账号（可选）</div>
        <p class="mms-form-section__hint">从库连接时需要用这个账号，如果已有可跳过</p>
        <div class="mms-form-group">
          <div class="mms-field">
            <label>账号名</label>
            <NInput v-model:value="replForm.user" placeholder="如 repl_user" />
          </div>
          <div class="mms-field">
            <label>密码</label>
            <NSpace>
              <NInput v-model:value="replForm.password" placeholder="点右边按钮自动生成" type="password" show-password-on="click" style="flex:1" />
              <NButton @click="generatePassword" type="primary" ghost>自动生成</NButton>
            </NSpace>
          </div>
          <div class="mms-field">
            <label>允许连接的从库 IP</label>
            <NSelect v-model:value="replForm.hostMode" :options="hostOptions" />
            <NInput
              v-if="replForm.hostMode === 'custom'"
              v-model:value="replForm.customHost"
              placeholder="如 10.0.0.%  或  192.168.1.100"
              style="margin-top: 8px"
            />
          </div>
        </div>
      </div>

      <NAlert v-if="fixDone" type="success" :bordered="false" style="margin-top:16px">
        <template #icon><NIcon :component="ShieldCheckmarkOutline" /></template>
        修复完成！已创建配置快照（ID: {{ snapshotId }}），可随时回滚。
      </NAlert>

      <NAlert v-if="fixError" type="error" :bordered="false" style="margin-top:12px">
        {{ fixError }}
      </NAlert>

      <div class="mms-step-actions">
        <NButton @click="step = 2">上一步</NButton>
        <NButton type="primary" :disabled="fixDone" :loading="loading" @click="runFix">
          {{ fixDone ? '已修复' : '执行修复' }}
        </NButton>
        <NButton v-if="fixDone" type="primary" @click="step = 4; initExportForm()">
          下一步：导出配置单
        </NButton>
      </div>
    </div>

    <!-- Step 4 -->
    <div v-if="step === 4" class="mms-step-card">
      <div class="mms-step-card__header">
        <h3>导出配置单</h3>
      </div>

      <NAlert type="info" :bordered="false" style="margin-bottom:16px">
        生成一段加密文本，在从库插件中粘贴即可自动接入。<br>
        <strong>请确认下方的主库地址是从库能够访问到的 IP。</strong>
      </NAlert>

      <div class="mms-form-group">
        <div class="mms-field">
          <label>本机地址（从库通过此地址连接）</label>
          <NInput v-model:value="exportForm.masterHost" placeholder="本机外网或内网 IP">
            <template #prefix>IP</template>
          </NInput>
          <p v-if="exportForm.masterHost === '127.0.0.1' || exportForm.masterHost === 'localhost'" class="mms-field-warn">
            127.0.0.1 是本机回环地址，从库无法通过此地址连接，请填写真实 IP
          </p>
        </div>
        <div class="mms-field">
          <label>MySQL 端口</label>
          <NInputNumber v-model:value="exportForm.masterPort" :min="1" :max="65535" placeholder="3306" style="width:100%" />
        </div>
        <div class="mms-field">
          <label>复制账号</label>
          <NInput v-model:value="replForm.user" placeholder="复制账号" />
        </div>
        <div class="mms-field">
          <label>密码</label>
          <NSpace>
            <NInput v-model:value="replForm.password" placeholder="点右边按钮自动生成" type="password" show-password-on="click" style="flex:1" />
            <NButton v-if="!replForm.password" @click="generatePassword" type="primary" ghost>自动生成</NButton>
          </NSpace>
        </div>
      </div>

      <NAlert v-if="exportError" type="error" :bordered="false" style="margin-top:12px">
        {{ exportError }}
      </NAlert>

      <NButton type="primary" @click="exportProfile" :disabled="!replForm.password || !exportForm.masterHost" :loading="loading" style="margin-top:12px">
        生成配置单
      </NButton>

      <div v-if="profileB64" class="mms-profile-result">
        <div class="mms-profile-result__label">配置单文本（已签名加密）</div>
        <NInput type="textarea" :value="profileB64" :rows="4" readonly style="font-family: monospace; font-size: 12px" />
        <NButton size="small" type="primary" ghost style="margin-top:8px" @click="copyProfile">
          <template #icon><NIcon :component="CopyOutline" /></template>
          复制到剪贴板
        </NButton>
      </div>

      <NResult v-if="profileB64" status="success" title="配置单已就绪！" style="margin-top:20px">
        <template #default>
          <p style="color:#666">将这段配置单文本发给从库管理员，在从库插件中选择「粘贴配置单」即可自动接入。</p>
        </template>
        <template #footer>
          <NButton type="primary" @click="env.navigate('landing')">返回首页</NButton>
        </template>
      </NResult>

      <!-- Physical-mode handshake card (also shown in wizard step 4 so the
           user never has to hunt for it). -->
      <div class="mms-step-card" style="margin-top:16px">
        <div class="mms-step-card__header">
          <h3>物理模式 · 粘贴握手单（可选）</h3>
        </div>
        <NAlert type="info" :bordered="false" style="margin-bottom:10px">
          仅"物理 (xtrabackup) 模式"需要，普通逻辑同步不需要。<br>
          在从库"初始化策略 → 启用物理模式"中点击"生成握手单"，复制得到一串 base64 文本，粘贴到下方即可一键开通。兼容直接粘贴裸公钥。
        </NAlert>
        <NInput
          v-model:value="replicaPubKey"
          type="textarea"
          :rows="3"
          placeholder="粘贴从库生成的握手单（base64）或 ssh-ed25519 AAAA... 公钥"
          style="font-family: ui-monospace,Menlo,monospace; font-size:12px"
        />
        <div style="margin-top:10px">
          <NButton type="primary" :loading="pubKeyInstalling" @click="installReplicaPubKey">
            一键安装到 authorized_keys
          </NButton>
        </div>
        <NAlert
          v-if="pubKeyResult.text"
          :type="pubKeyResult.type === 'success' ? 'success' : 'error'"
          :bordered="false"
          style="margin-top:10px"
        >
          {{ pubKeyResult.text }}
          <div v-if="pubKeyResult.meta?.replica_hostname" style="font-size:12px; opacity:0.75; margin-top:4px">
            来源：{{ pubKeyResult.meta.replica_hostname }}
            <span v-if="pubKeyResult.meta.source_id">（源ID {{ pubKeyResult.meta.source_id }}）</span>
          </div>
        </NAlert>
      </div>
    </div>
    </template>
  </div>
</template>

<style scoped>
.mms-wizard {
  max-width: 720px;
  margin: 0 auto;
}
.mms-wizard__header {
  margin-bottom: 20px;
}
.mms-wizard__title {
  font-size: 20px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0 0 4px;
}
.mms-wizard__desc {
  font-size: 13px;
  color: #999;
  margin: 0;
}
.mms-wizard__steps {
  margin-bottom: 24px;
}
.mms-step-card {
  background: #fff;
  border: 1px solid #ebeef5;
  border-radius: 12px;
  padding: 24px;
}
.mms-step-card__header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}
.mms-step-card__header h3 {
  font-size: 16px;
  font-weight: 600;
  color: #1a1a2e;
  margin: 0;
}
.mms-step-actions {
  display: flex;
  gap: 8px;
  margin-top: 20px;
  padding-top: 16px;
  border-top: 1px solid #f0f0f5;
}
.mms-check-list {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.mms-check-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  padding: 10px 12px;
  background: #fafafa;
  border-radius: 8px;
}
.mms-check-item__status {
  width: 24px;
  height: 24px;
  border-radius: 50%;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 12px;
  font-weight: 700;
  flex-shrink: 0;
  color: #fff;
}
.mms-check-item__status--ok { background: #18a058; }
.mms-check-item__status--warn { background: #f0a020; }
.mms-check-item__status--fail { background: #d03050; }
.mms-check-item__body { flex: 1; }
.mms-check-item__name {
  font-size: 14px;
  font-weight: 600;
  color: #333;
}
.mms-check-item__detail {
  font-size: 12px;
  color: #888;
  margin-top: 2px;
}
.mms-check-item__detail code {
  background: #e8e8ed;
  padding: 1px 5px;
  border-radius: 3px;
  font-size: 11px;
}
.mms-check-item__expect {
  color: #d03050;
}
.mms-summary-bar {
  display: flex;
  gap: 16px;
  margin-top: 12px;
  padding: 10px 12px;
  background: #f8f8fa;
  border-radius: 8px;
  font-size: 13px;
  font-weight: 500;
}
.mms-summary-bar__ok { color: #18a058; }
.mms-summary-bar__warn { color: #f0a020; }
.mms-summary-bar__fail { color: #d03050; }
.mms-preview-list {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.mms-preview-item {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 8px 12px;
  background: #f0faf5;
  border-radius: 6px;
  font-size: 13px;
  color: #333;
}
.mms-form-section {
  margin-bottom: 16px;
}
.mms-form-section__title {
  font-size: 14px;
  font-weight: 600;
  color: #333;
  margin-bottom: 4px;
}
.mms-form-section__hint {
  font-size: 12px;
  color: #999;
  margin: 0 0 12px;
}
.mms-form-group {
  max-width: 440px;
  display: flex;
  flex-direction: column;
  gap: 12px;
}
.mms-field label {
  display: block;
  font-size: 13px;
  font-weight: 600;
  color: #555;
  margin-bottom: 4px;
}
.mms-profile-result {
  margin-top: 16px;
  padding: 16px;
  background: #f8f8fa;
  border-radius: 8px;
}
.mms-profile-result__label {
  font-size: 13px;
  font-weight: 600;
  color: #555;
  margin-bottom: 8px;
}
.mms-loading-hint {
  text-align: center;
  padding: 32px 16px;
  color: #999;
  font-size: 14px;
}
.mms-summary-actions {
  display: flex;
  gap: 8px;
  margin-top: 20px;
}
.mms-info-row {
  display: flex;
  align-items: center;
  gap: 12px;
  padding: 8px 0;
}
.mms-info-label {
  font-size: 13px;
  color: #888;
  min-width: 70px;
}
.mms-info-row code {
  background: #f0f0f5;
  padding: 2px 8px;
  border-radius: 4px;
  font-size: 13px;
}
.mms-info-hint {
  font-size: 12px;
  color: #999;
  margin: 8px 0 0;
}
.mms-field-warn {
  font-size: 12px;
  color: #d03050;
  margin: 4px 0 0;
  line-height: 1.4;
}
</style>
