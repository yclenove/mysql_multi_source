<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref, watch, nextTick } from 'vue'
import { NConfigProvider, NMessageProvider, NDialogProvider, NButton, NIcon, zhCN, dateZhCN, type GlobalThemeOverrides } from 'naive-ui'
import { ChevronUpOutline, ChevronDownOutline } from '@vicons/ionicons5'
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

// --- Scroll helpers ---------------------------------------------------------
// Use an internal scroll container as a hard fallback. This avoids relying on
// BaoTa modal/iframe outer wrappers, which may hide/clip host scrollbars.
const showFabs = ref(false)
const scrollPct = ref(0)
const bodyRef = ref<HTMLElement | null>(null)

function updateScrollPct() {
  const el = bodyRef.value
  if (!el) {
    showFabs.value = false
    scrollPct.value = 0
    return
  }
  const max = el.scrollHeight - el.clientHeight
  if (max <= 10) {
    showFabs.value = false
    scrollPct.value = 0
    return
  }
  scrollPct.value = Math.min(100, Math.max(0, Math.round((el.scrollTop / max) * 100)))
  showFabs.value = true
}

function attachScroll() {
  detachScroll()
  if (bodyRef.value) {
    bodyRef.value.addEventListener('scroll', updateScrollPct, { passive: true })
  }
  window.addEventListener('resize', updateScrollPct, { passive: true })
  updateScrollPct()
}

function detachScroll() {
  window.removeEventListener('resize', updateScrollPct)
  if (bodyRef.value) {
    bodyRef.value.removeEventListener('scroll', updateScrollPct)
  }
}

function doScrollTo(top: number, smooth = true) {
  const el = bodyRef.value
  if (!el) return
  const opts: ScrollToOptions = { top, behavior: smooth ? 'smooth' : 'auto' }
  el.scrollTo(opts)
}

function scrollToTop() {
  doScrollTo(0)
}

function scrollToBottom() {
  const el = bodyRef.value
  if (!el) return
  doScrollTo(el.scrollHeight)
}

function jumpToTrack(evt: MouseEvent) {
  const track = evt.currentTarget as HTMLElement
  const rect = track.getBoundingClientRect()
  const ratio = Math.min(1, Math.max(0, (evt.clientY - rect.top) / rect.height))
  const el = bodyRef.value
  if (!el) return
  doScrollTo((el.scrollHeight - el.clientHeight) * ratio)
}

function dragTrack(evt: MouseEvent) {
  const track = evt.currentTarget as HTMLElement
  const rect = track.getBoundingClientRect()
  const el = bodyRef.value
  if (!el) return
  const max = el.scrollHeight - el.clientHeight
  const onMove = (e: MouseEvent) => {
    const ratio = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height))
    doScrollTo(max * ratio, false)
  }
  const onUp = () => {
    window.removeEventListener('mousemove', onMove)
    window.removeEventListener('mouseup', onUp)
  }
  window.addEventListener('mousemove', onMove)
  window.addEventListener('mouseup', onUp)
  evt.preventDefault()
}

onMounted(() => {
  env.detectEnv()
  scrollPluginTop()
  nextTick(() => setTimeout(attachScroll, 200))
})

onBeforeUnmount(() => {
  detachScroll()
})

watch(
  () => env.currentView,
  () => {
    nextTick(() => {
      scrollPluginTop()
      if (bodyRef.value) bodyRef.value.scrollTop = 0
      setTimeout(() => {
        attachScroll()
        updateScrollPct()
      }, 200)
    })
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
          <div ref="bodyRef" class="mms-body">
            <LandingCards v-if="env.currentView === 'landing'" />
            <WizardMaster v-else-if="env.currentView === 'wizard_master'" />
            <WizardReplica v-else-if="env.currentView === 'wizard_replica'" />
            <DashboardView v-else-if="env.currentView === 'dashboard'" />
            <DiagnoseView v-else-if="env.currentView === 'diagnose'" />
            <ExpertLayout v-else-if="env.currentView === 'expert'" />
          </div>

          <!-- Floating scroll helper (right edge). Visible only when the
               page content actually overflows. -->
          <div v-if="showFabs" class="mms-scroll-fab" @mouseenter="updateScrollPct">
            <NButton circle size="small" type="primary" ghost @click="scrollToTop" title="回到顶部">
              <template #icon><NIcon :component="ChevronUpOutline" /></template>
            </NButton>
            <div
              class="mms-scroll-fab__track"
              :title="`滚动进度 ${scrollPct}%（点击跳转 / 按住拖动）`"
              @click="jumpToTrack"
              @mousedown="dragTrack"
            >
              <div class="mms-scroll-fab__thumb" :style="{ height: Math.max(12, scrollPct) + '%' }"></div>
            </div>
            <NButton circle size="small" type="primary" ghost @click="scrollToBottom" title="跳到底部">
              <template #icon><NIcon :component="ChevronDownOutline" /></template>
            </NButton>
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
  position: relative;
  height: 100vh;
  max-height: 100vh;
  display: flex;
  flex-direction: column;
  box-sizing: border-box;
}
.mms-body {
  padding-top: 16px;
  padding-bottom: 24px;
  padding-right: 8px;
  flex: 1;
  min-height: 0;
  overflow-y: auto;
  overflow-x: hidden;
}

/* Force a visible, draggable scrollbar for WebKit browsers. BaoTa's modal
   container often renders a near-invisible native scrollbar that users
   can't see or drag; this makes it discoverable. */
body,
.mms-app,
.mms-body,
html,
.layui-layer-content,
.plugin_body,
.soft-Body {
  scrollbar-width: thin;
  scrollbar-color: #c5c8d0 transparent;
}
::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}
::-webkit-scrollbar-track {
  background: transparent;
}
::-webkit-scrollbar-thumb {
  background: #c5c8d0;
  border-radius: 6px;
  border: 2px solid transparent;
  background-clip: padding-box;
  min-height: 40px;
}
::-webkit-scrollbar-thumb:hover {
  background: #8a8f9d;
  background-clip: padding-box;
  border: 2px solid transparent;
}

/* Floating up / down / progress indicator on the right edge */
.mms-scroll-fab {
  position: fixed;
  right: 12px;
  top: 50%;
  transform: translateY(-50%);
  z-index: 2147483000; /* max-ish, above BaoTa overlays */
  display: flex;
  flex-direction: column;
  align-items: center;
  gap: 6px;
  padding: 8px 6px;
  background: rgba(255, 255, 255, 0.95);
  border: 1px solid rgba(0, 0, 0, 0.08);
  border-radius: 22px;
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.14);
  backdrop-filter: blur(6px);
  pointer-events: auto;
}
.mms-scroll-fab__track {
  width: 6px;
  height: 140px;
  background: #eef0f4;
  border-radius: 3px;
  overflow: hidden;
  margin: 4px 0;
  cursor: pointer;
  position: relative;
}
.mms-scroll-fab__thumb {
  width: 100%;
  background: linear-gradient(180deg, #4098fc 0%, #2080f0 100%);
  border-radius: 3px;
  transition: height 0.12s ease;
  min-height: 18px;
}

@media (max-width: 768px) {
  .mms-app {
    padding: 0 12px;
  }
  .mms-scroll-fab {
    right: 6px;
  }
}
</style>
