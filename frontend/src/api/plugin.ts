const PLUGIN_NAME = 'mysql_multi_source'

export interface ApiResult<T = any> {
  status: boolean
  msg: T
}

function isBtPanel(): boolean {
  return typeof window.bt_tools?.send === 'function'
}

export function call<T = any>(
  method: string,
  data: Record<string, any> = {},
): Promise<ApiResult<T>> {
  return new Promise((resolve) => {
    if (isBtPanel()) {
      try {
        window.bt_tools!.send(
          {
            url: `/plugin?action=a&name=${PLUGIN_NAME}&s=${method}`,
            data,
          },
          (res: any) => {
            if (res === undefined || res === null) {
              resolve({ status: false, msg: '服务端返回为空，请检查插件是否正常安装' as any })
              return
            }
            resolve(res as ApiResult<T>)
          },
        )
      } catch (e: any) {
        resolve({ status: false, msg: ('面板通信异常: ' + (e?.message || '未知错误')) as any })
      }
    } else {
      fetch(`/plugin?action=a&name=${PLUGIN_NAME}&s=${method}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
        body: new URLSearchParams(
          Object.fromEntries(
            Object.entries(data).map(([k, v]) => [
              k,
              typeof v === 'object' ? JSON.stringify(v) : String(v),
            ]),
          ),
        ),
      })
        .then((r) => r.json())
        .then((r) => resolve(r as ApiResult<T>))
        .catch(() => resolve({ status: false, msg: '网络请求失败，请检查面板是否运行中' as any }))
    }
  })
}

export function extractMsg<T = any>(res: ApiResult<T>): T {
  return res?.msg as T
}

export function isOk(res: ApiResult): boolean {
  return res?.status === true
}

export function getMessage(res: ApiResult): string {
  const m = res?.msg
  if (typeof m === 'string') return m
  if (m && typeof m === 'object') {
    return (m as any).message || ''
  }
  return ''
}

export function getCode(res: ApiResult): string {
  const m = res?.msg
  if (m && typeof m === 'object') {
    return (m as any).code || ''
  }
  return ''
}

export function btConfirm(title: string, content: string): Promise<boolean> {
  return new Promise((resolve) => {
    if (window.bt?.confirm) {
      window.bt.confirm({ title, msg: content }, () => resolve(true))
    } else {
      resolve(window.confirm(`${title}\n\n${content}`))
    }
  })
}
