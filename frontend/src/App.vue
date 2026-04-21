<script setup lang="ts">
import { onMounted, watch, nextTick } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, zhCN, dateZhCN, type GlobalThemeOverrides } from 'naive-ui'
import { useEnvStore } from '@/store/env'
import LandingCards from '@/components/LandingCards.vue'
import WizardMaster from '@/views/WizardMaster.vue'
import WizardReplica from '@/views/WizardReplica.vue'
import DashboardView from '@/views/DashboardView.vue'
import DiagnoseView from '@/views/DiagnoseView.vue'
import ExpertLayout from '@/views/ExpertLayout.vue'
import AppHeader from '@/components/AppHeader.vue'
import { scrollPluginTop } from '@/utils/scroll'

const env = useEnvStore()
onMounted(() => {
  env.detectEnv()
  scrollPluginTop()
})

watch(
  () => env.currentView,
  () => {
    nextTick(() => scrollPluginTop())
  },
)

const themeOverrides: GlobalThemeOverrides = {
  common: {
    primaryColor: '#2080f0',
    primaryColorHover: '#4098fc',
    primaryColorPressed: '#1060c9',
    borderRadius: '8px',
    borderRadiusSmall: '6px',
    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", "Microsoft YaHei", sans-serif',
  },
  Card: {
    borderRadius: '12px',
    boxShadow: '0 1px 4px rgba(0,0,0,0.06)',
  },
  Button: {
    borderRadiusMedium: '8px',
    borderRadiusSmall: '6px',
    borderRadiusTiny: '4px',
  },
  Tag: {
    borderRadius: '6px',
  },
  Steps: {
    indicatorSizeMedium: '28px',
    indicatorSizeSmall: '24px',
  },
}
</script>

<template>
  <NConfigProvider :locale="zhCN" :date-locale="dateZhCN" :theme-overrides="themeOverrides">
    <NMessageProvider>
      <NDialogProvider>
        <div class="mms-app">
          <AppHeader />
          <div class="mms-body">
            <LandingCards v-if="env.currentView === 'landing'" />
            <WizardMaster v-else-if="env.currentView === 'wizard_master'" />
            <WizardReplica v-else-if="env.currentView === 'wizard_replica'" />
            <DashboardView v-else-if="env.currentView === 'dashboard'" />
            <DiagnoseView v-else-if="env.currentView === 'diagnose'" />
            <ExpertLayout v-else-if="env.currentView === 'expert'" />
          </div>
        </div>
      </NDialogProvider>
    </NMessageProvider>
  </NConfigProvider>
</template>

<style>
.mms-app {
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB',
    'Microsoft YaHei', sans-serif;
  color: #1a1a2e;
  line-height: 1.6;
  max-width: 1100px;
  margin: 0 auto;
  padding: 0 20px;
}
.mms-body {
  padding-top: 16px;
  padding-bottom: 24px;
}
</style>
