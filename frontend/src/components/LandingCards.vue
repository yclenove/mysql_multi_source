<script setup lang="ts">
import { NButton, NTag, NSpin, NIcon } from 'naive-ui'
import {
  ServerOutline, GitNetworkOutline, SpeedometerOutline,
  ConstructOutline, ArrowForwardOutline, CheckmarkCircleOutline,
  SettingsOutline,
} from '@vicons/ionicons5'
import { useEnvStore } from '@/store/env'
import { computed, type Component } from 'vue'

const env = useEnvStore()

interface LandingCard {
  key: string
  icon: Component
  color: string
  bg: string
  title: string
  desc: string
  action: () => void
  recommended: boolean
  badge: string
  done: boolean
  doneLabel: string
  actionLabel: string
}

const cards = computed<LandingCard[]>(() => [
  {
    key: 'master',
    icon: ServerOutline,
    color: '#f0a020',
    bg: env.masterConfigured
      ? 'linear-gradient(135deg, #f6ffed 0%, #eefae6 100%)'
      : 'linear-gradient(135deg, #fff9f0 0%, #fff4e6 100%)',
    title: env.masterConfigured ? '主库已配置' : '我是主库，帮我准备好',
    desc: env.masterConfigured
      ? `主库配置正常运行中${env.masterSetup?.repl_user ? '，复制账号: ' + env.masterSetup.repl_user : ''}`
      : '自动检测并修复 MySQL 配置，创建复制账号，一键导出配置单给从库使用',
    action: () => { env.setMode('master_mode'); env.navigate('wizard_master') },
    recommended: !env.masterConfigured && env.suggestedMode === 'master_mode',
    badge: '',
    done: env.masterConfigured,
    doneLabel: env.masterSetup?.health_ok ? '运行正常' : '需要检查',
    actionLabel: env.masterConfigured ? '查看详情' : '开始配置',
  },
  {
    key: 'replica',
    icon: GitNetworkOutline,
    color: '#18a058',
    bg: env.hasSources
      ? 'linear-gradient(135deg, #f6ffed 0%, #eefae6 100%)'
      : 'linear-gradient(135deg, #f0faf5 0%, #e8f5e9 100%)',
    title: env.hasSources ? '从库已接入' : '我是从库，帮我接入主库',
    desc: env.hasSources
      ? `已配置 ${env.counts.sources} 个数据源，${env.counts.running_sources} 个运行中`
      : '粘贴配置单或手动填写主库信息，选择要同步的库，一键开始同步',
    action: () => { env.setMode('replica_mode'); env.navigate('wizard_replica') },
    recommended: !env.hasSources && env.suggestedMode === 'replica_mode',
    badge: '',
    done: env.hasSources,
    doneLabel: `${env.counts.running_sources}/${env.counts.sources} 运行中`,
    actionLabel: env.hasSources ? '添加新数据源' : '开始配置',
  },
  {
    key: 'dashboard',
    icon: SpeedometerOutline,
    color: '#2080f0',
    bg: 'linear-gradient(135deg, #f0f7ff 0%, #e6f0ff 100%)',
    title: '查看复制状态仪表盘',
    desc: '实时监控所有复制来源的运行状态、延迟和任务进度',
    action: () => env.navigate('dashboard'),
    recommended: false,
    badge: env.counts.sources > 0 ? `${env.counts.sources} 个来源` : '',
    done: false,
    doneLabel: '',
    actionLabel: '查看',
  },
  {
    key: 'diagnose',
    icon: ConstructOutline,
    color: '#d03050',
    bg: 'linear-gradient(135deg, #fff5f5 0%, #ffebee 100%)',
    title: '我的复制有问题',
    desc: '一键扫描所有来源和任务，按类别展示错误并提供自动修复',
    action: () => env.navigate('diagnose'),
    recommended: false,
    badge: '',
    done: false,
    doneLabel: '',
    actionLabel: '诊断',
  },
])
</script>

<template>
  <div class="mms-landing">
    <div class="mms-landing__hero">
      <h2 class="mms-landing__title">选择你要做的事情</h2>
      <p class="mms-landing__subtitle">
        <template v-if="env.masterConfigured || env.hasSources">
          当前服务器已配置为
          <strong v-if="env.masterConfigured" style="color: #f0a020">主库</strong>
          <template v-if="env.masterConfigured && env.hasSources">，同时作为</template>
          <strong v-if="env.hasSources" style="color: #18a058">从库（{{ env.counts.sources }} 个来源）</strong>
        </template>
        <template v-else-if="env.suggestedMode !== 'unknown' && !env.loading">
          根据环境检测，当前机器更适合作为
          <strong :style="{ color: env.suggestedMode === 'master_mode' ? '#f0a020' : '#18a058' }">
            {{ env.suggestedMode === 'master_mode' ? '主库' : '从库' }}
          </strong>
          使用
        </template>
        <template v-else-if="env.loading">正在检测环境...</template>
        <template v-else>请选择你的角色开始配置</template>
      </p>
    </div>

    <NSpin :show="env.loading" :style="{ minHeight: '200px' }">
      <div class="mms-cards">
        <div
          v-for="c in cards"
          :key="c.key"
          class="mms-card"
          :class="{ 'mms-card--recommended': c.recommended, 'mms-card--done': c.done }"
          :style="{ background: c.bg }"
          @click="c.action"
        >
          <div class="mms-card__header">
            <div class="mms-card__icon" :style="{ background: c.color + '15', color: c.color }">
              <NIcon :size="26" :component="c.icon" />
            </div>
            <div class="mms-card__badges">
              <NTag v-if="c.done" type="success" size="small" round :bordered="false">
                <template #icon><NIcon :component="CheckmarkCircleOutline" :size="14" /></template>
                {{ c.doneLabel }}
              </NTag>
              <NTag v-if="c.recommended" type="warning" size="small" round :bordered="false">
                推荐
              </NTag>
              <NTag v-if="c.badge && !c.done" size="small" round :bordered="false" style="background:#e8e8ed">
                {{ c.badge }}
              </NTag>
            </div>
          </div>
          <h3 class="mms-card__title">{{ c.title }}</h3>
          <p class="mms-card__desc">{{ c.desc }}</p>
          <div class="mms-card__action">
            <span>{{ c.actionLabel }}</span>
            <NIcon :size="14" :component="c.done ? SettingsOutline : ArrowForwardOutline" />
          </div>
        </div>
      </div>
    </NSpin>

    <div class="mms-landing__footer">
      <NButton text size="small" type="primary" @click="env.toggleExpert()">
        进入专家视图（查看所有原始操作）
      </NButton>
    </div>
  </div>
</template>

<style scoped>
.mms-landing {
  max-width: 840px;
  margin: 0 auto;
}
.mms-landing__hero {
  text-align: center;
  padding: 20px 0 24px;
}
.mms-landing__title {
  font-size: 22px;
  font-weight: 700;
  color: #1a1a2e;
  margin: 0 0 6px;
}
.mms-landing__subtitle {
  font-size: 14px;
  color: #666;
  margin: 0;
}
.mms-cards {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 16px;
}
.mms-card {
  position: relative;
  border-radius: 14px;
  padding: 20px;
  cursor: pointer;
  border: 1.5px solid transparent;
  transition: all 0.25s ease;
}
.mms-card:hover {
  transform: translateY(-2px);
  box-shadow: 0 8px 24px rgba(0,0,0,0.08);
}
.mms-card--recommended {
  border-color: #f0a020;
  box-shadow: 0 2px 12px rgba(240,160,32,0.12);
}
.mms-card--done {
  border-color: #b7eb8f;
}
.mms-card__header {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  margin-bottom: 12px;
}
.mms-card__icon {
  width: 48px;
  height: 48px;
  border-radius: 12px;
  display: flex;
  align-items: center;
  justify-content: center;
}
.mms-card__badges {
  display: flex;
  gap: 4px;
}
.mms-card__title {
  font-size: 15px;
  font-weight: 600;
  color: #1a1a2e;
  margin: 0 0 6px;
  line-height: 1.4;
}
.mms-card__desc {
  font-size: 13px;
  color: #666;
  margin: 0 0 12px;
  line-height: 1.5;
}
.mms-card__action {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 13px;
  font-weight: 600;
  color: #2080f0;
}
.mms-landing__footer {
  text-align: center;
  margin-top: 28px;
  padding-bottom: 8px;
}
@media (max-width: 640px) {
  .mms-cards { grid-template-columns: 1fr; }
}
</style>
