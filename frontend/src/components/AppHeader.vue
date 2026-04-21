<script setup lang="ts">
import { NSpace, NTag, NButton, NDivider } from 'naive-ui'
import { useEnvStore } from '@/store/env'

const env = useEnvStore()

const modeLabel: Record<string, string> = {
  master_mode: '主库模式',
  replica_mode: '从库模式',
  unknown: '未确定',
}
const modeColor: Record<string, string> = {
  master_mode: '#f0a020',
  replica_mode: '#18a058',
  unknown: '#909399',
}
</script>

<template>
  <div class="mms-header">
    <div class="mms-header__left">
      <div class="mms-header__logo" @click="env.navigate('landing')">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none">
          <circle cx="12" cy="12" r="10" stroke="#2080f0" stroke-width="2"/>
          <circle cx="12" cy="8" r="2" fill="#2080f0"/>
          <circle cx="7" cy="15" r="2" fill="#36ad6a"/>
          <circle cx="17" cy="15" r="2" fill="#36ad6a"/>
          <line x1="12" y1="10" x2="7" y2="13" stroke="#999" stroke-width="1.5"/>
          <line x1="12" y1="10" x2="17" y2="13" stroke="#999" stroke-width="1.5"/>
        </svg>
        <span class="mms-header__title">多源复制</span>
      </div>
      <span class="mms-header__subtitle">MySQL Multi-Source Replication</span>
    </div>
    <div class="mms-header__right">
      <NTag :bordered="false" size="small" round :style="{ background: modeColor[env.activeMode] + '18', color: modeColor[env.activeMode] }">
        {{ modeLabel[env.activeMode] || '未知' }}
      </NTag>
      <NTag v-if="env.mysqlVersion" size="small" :bordered="false" round style="background:#f0f0f5">
        MySQL {{ env.mysqlVersion }}
      </NTag>
      <NDivider vertical style="margin: 0 4px" />
      <NButton
        v-if="env.currentView !== 'landing'"
        text size="small"
        @click="env.navigate('landing')"
      >首页</NButton>
      <NButton
        v-if="env.currentView !== 'dashboard'"
        text size="small"
        @click="env.navigate('dashboard')"
      >仪表盘</NButton>
      <NButton
        v-if="env.currentView !== 'diagnose'"
        text size="small"
        @click="env.navigate('diagnose')"
      >诊断</NButton>
      <NButton
        size="small"
        :type="env.expertMode ? 'primary' : 'default'"
        :secondary="!env.expertMode"
        :ghost="env.expertMode"
        round
        @click="env.toggleExpert()"
      >{{ env.expertMode ? '简洁视图' : '专家视图' }}</NButton>
    </div>
  </div>
</template>

<style scoped>
.mms-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 12px 0;
  border-bottom: 1px solid #ebeef5;
  margin-bottom: 4px;
  flex-wrap: wrap;
  gap: 8px;
}
.mms-header__left {
  display: flex;
  align-items: center;
  gap: 12px;
}
.mms-header__logo {
  display: flex;
  align-items: center;
  gap: 8px;
  cursor: pointer;
  user-select: none;
}
.mms-header__title {
  font-size: 17px;
  font-weight: 700;
  color: #1a1a2e;
  letter-spacing: 0.5px;
}
.mms-header__subtitle {
  font-size: 12px;
  color: #999;
  letter-spacing: 0.3px;
}
.mms-header__right {
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
</style>
