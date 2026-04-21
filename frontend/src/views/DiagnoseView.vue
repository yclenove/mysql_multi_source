<script setup lang="ts">
import { ref, onMounted, computed } from 'vue'
import {
  NButton, NSpace, NTag, NEmpty, NCollapse, NCollapseItem,
  NAlert, NIcon, useMessage, NSpin,
} from 'naive-ui'
import { CheckmarkCircleOutline, AlertCircleOutline } from '@vicons/ionicons5'
import { call, isOk, extractMsg, getMessage } from '@/api/plugin'

const msg = useMessage()
const loading = ref(false)
const installLoading = ref<Record<string, boolean>>({})
const groups = ref<Record<string, any[]>>({})
const totalIssues = ref(0)

const catLabels: Record<string, string> = {
  network: '网络问题', auth: '权限 / 账号', gtid: 'GTID 配置',
  conflict: '数据冲突', resource: '资源不足', config: 'MySQL 配置', other: '其它',
}
const catColors: Record<string, string> = {
  network: '#d03050', auth: '#d03050', gtid: '#f0a020',
  conflict: '#f0a020', resource: '#d03050', config: '#f0a020', other: '#909399',
}

async function scan() {
  loading.value = true
  try {
    const res = await call('wizard_diagnose_all')
    if (isOk(res)) {
      const d = extractMsg(res)
      groups.value = d.groups || {}
      totalIssues.value = d.total_issues || 0
    } else { msg.error(getMessage(res) || '诊断失败') }
  } finally { loading.value = false }
}

async function quickFix(cat: string) {
  loading.value = true
  try {
    const payload: Record<string, string> = { category: cat }
    if (cat === 'config' || cat === 'gtid') payload.auto_restart = '1'
    const res = await call('wizard_quick_fix', payload)
    if (isOk(res)) {
      const d = extractMsg(res)
      if (d?.restart_result && d.restart_result.ok === false) {
        msg.warning(`修复已执行，但重启失败：${d.restart_result.err || '请手动重启 MySQL'}`)
      } else {
        msg.success(getMessage(res) || '修复完成')
      }
      await scan()
    }
    else msg.error(getMessage(res) || '修复失败')
  } finally { loading.value = false }
}

async function installTool(tool: 'xtrabackup' | 'mariabackup') {
  installLoading.value[tool] = true
  try {
    const res = await call('install_bootstrap_tool', { tool_name: tool })
    if (isOk(res)) msg.success(`${tool} 安装成功`)
    else msg.error(getMessage(res) || `安装 ${tool} 失败`)
  } finally {
    installLoading.value[tool] = false
  }
}

const nonEmpty = computed(() => Object.entries(groups.value).filter(([, v]) => v?.length > 0))
onMounted(() => scan())
</script>

<template>
  <div class="mms-diag">
    <div class="mms-diag__header">
      <div>
        <h2 class="mms-diag__title">
          诊断中心
          <NTag v-if="totalIssues > 0" :bordered="false" size="small" round style="background:#d0305018; color:#d03050; margin-left:8px">
            {{ totalIssues }} 个问题
          </NTag>
          <NTag v-else-if="!loading" :bordered="false" size="small" round style="background:#18a05818; color:#18a058; margin-left:8px">
            全部正常
          </NTag>
        </h2>
        <p class="mms-diag__desc">扫描所有来源和任务，按类别展示问题并提供修复</p>
      </div>
      <NButton size="small" @click="scan" :loading="loading">重新扫描</NButton>
    </div>
    <NSpace style="margin-bottom: 12px">
      <NButton size="small" @click="installTool('xtrabackup')" :loading="installLoading.xtrabackup">安装 xtrabackup</NButton>
      <NButton size="small" @click="installTool('mariabackup')" :loading="installLoading.mariabackup">安装 mariabackup</NButton>
    </NSpace>

    <NSpin :show="loading">
      <NEmpty v-if="nonEmpty.length === 0 && !loading" style="margin: 40px 0">
        <template #icon>
          <NIcon :size="48" :component="CheckmarkCircleOutline" color="#18a058" />
        </template>
        <template #default>
          <span style="color:#666">未发现问题，所有来源和任务状态正常</span>
        </template>
      </NEmpty>

      <div v-if="nonEmpty.length > 0" class="mms-diag-list">
        <div v-for="[cat, items] in nonEmpty" :key="cat" class="mms-diag-group">
          <div class="mms-diag-group__header">
            <div class="mms-diag-group__title">
              <div class="mms-diag-group__dot" :style="{ background: catColors[cat] || '#909399' }"></div>
              <span>{{ catLabels[cat] || cat }}</span>
              <NTag :bordered="false" size="small" round :style="{ background: (catColors[cat] || '#909399') + '18', color: catColors[cat] || '#909399' }">{{ items.length }}</NTag>
            </div>
            <NButton v-if="items.some((i: any) => i.fixable)" size="tiny" type="primary" ghost @click="quickFix(cat)">
              一键修复
            </NButton>
          </div>
          <div class="mms-diag-group__items">
            <div v-for="(item, idx) in items" :key="idx" class="mms-diag-issue">
              <div class="mms-diag-issue__header">
                <span v-if="item.source_id" class="mms-diag-issue__scope">来源 {{ item.source_id }}</span>
                <span v-if="item.task_id" class="mms-diag-issue__scope">任务 {{ item.task_id }}</span>
                <span v-if="item.name" class="mms-diag-issue__name">{{ item.name }}</span>
                <NTag v-if="item.fixable" :bordered="false" size="small" round style="background:#18a05818; color:#18a058">可自动修复</NTag>
              </div>
              <div class="mms-diag-issue__msg">{{ item.message }}</div>
              <div v-if="item.current" class="mms-diag-issue__detail">
                当前: <code>{{ item.current }}</code> → 期望: <code>{{ item.expected }}</code>
              </div>
            </div>
          </div>
        </div>
      </div>
    </NSpin>
  </div>
</template>

<style scoped>
.mms-diag { max-width: 800px; margin: 0 auto; }
.mms-diag__header { display: flex; align-items: flex-start; justify-content: space-between; margin-bottom: 20px; }
.mms-diag__title { font-size: 20px; font-weight: 700; color: #1a1a2e; margin: 0; display: flex; align-items: center; }
.mms-diag__desc { font-size: 13px; color: #999; margin: 4px 0 0; }
.mms-diag-list { display: flex; flex-direction: column; gap: 16px; }
.mms-diag-group { background: #fff; border: 1px solid #ebeef5; border-radius: 10px; overflow: hidden; }
.mms-diag-group__header { display: flex; align-items: center; justify-content: space-between; padding: 12px 16px; background: #f8f8fa; }
.mms-diag-group__title { display: flex; align-items: center; gap: 8px; font-size: 14px; font-weight: 600; }
.mms-diag-group__dot { width: 8px; height: 8px; border-radius: 50%; }
.mms-diag-group__items { padding: 4px 16px; }
.mms-diag-issue { padding: 12px 0; border-bottom: 1px solid #f5f5f5; }
.mms-diag-issue:last-child { border-bottom: none; }
.mms-diag-issue__header { display: flex; align-items: center; gap: 8px; margin-bottom: 4px; }
.mms-diag-issue__scope { font-size: 12px; color: #999; background: #f0f0f5; padding: 2px 8px; border-radius: 4px; }
.mms-diag-issue__name { font-size: 13px; font-weight: 600; color: #333; }
.mms-diag-issue__msg { font-size: 13px; color: #555; }
.mms-diag-issue__detail { font-size: 12px; color: #999; margin-top: 4px; }
.mms-diag-issue__detail code { background: #e8e8ed; padding: 1px 5px; border-radius: 3px; }
</style>
