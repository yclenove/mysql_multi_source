/// <reference types="vite/client" />

declare module '*.vue' {
  import type { DefineComponent } from 'vue'
  const component: DefineComponent<{}, {}, any>
  export default component
}

interface Window {
  bt_tools?: {
    send: (
      config: { url: string; data?: Record<string, any> } | string,
      callback: (res: any) => void,
      loadingTitle?: string,
    ) => void
  }
  bt?: {
    confirm: (opts: { title: string; msg: string; icon?: string }, cb: () => void) => void
  }
}
