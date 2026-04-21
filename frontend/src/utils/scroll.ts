// Scroll helpers for BaoTa plugin context.
// Plugin UI runs inside BaoTa's modal/dialog whose scroll container is unknown.
// We walk up ancestors, find every scrollable element, and reset scrollTop to 0.
// Also resets window/document scroll for safety.

function scrollAllAncestors(el: Element | null) {
  try {
    let p: Element | null = el
    while (p) {
      const node = p as HTMLElement
      if (node.scrollHeight > node.clientHeight + 1) {
        try {
          node.scrollTop = 0
        } catch (_) {
          // ignore
        }
      }
      p = p.parentElement
    }
  } catch (_) {
    // ignore
  }
}

export function scrollPluginTop() {
  try {
    const root =
      (document.querySelector('.mms-app') as HTMLElement | null) ||
      (document.querySelector('.mms-body') as HTMLElement | null) ||
      document.body

    scrollAllAncestors(root)

    try {
      window.scrollTo({ top: 0, behavior: 'auto' })
    } catch (_) {
      window.scrollTo(0, 0)
    }
    if (document.documentElement) document.documentElement.scrollTop = 0
    if (document.body) document.body.scrollTop = 0

    // BaoTa sometimes wraps plugin content in .soft-Body / .plugin_body / .bt-w-body
    ;['.soft-Body', '.plugin_body', '.bt-w-body', '.layui-layer-content'].forEach((sel) => {
      document.querySelectorAll(sel).forEach((node) => {
        try {
          ;(node as HTMLElement).scrollTop = 0
        } catch (_) {
          // ignore
        }
      })
    })

    // If running inside same-origin parent (BaoTa), try parent too.
    try {
      if (window.parent && window.parent !== window) {
        window.parent.scrollTo(0, 0)
        const pdoc = window.parent.document
        if (pdoc) {
          pdoc.documentElement.scrollTop = 0
          pdoc.body.scrollTop = 0
          ;['.soft-Body', '.plugin_body', '.bt-w-body', '.layui-layer-content'].forEach((sel) => {
            pdoc.querySelectorAll(sel).forEach((node) => {
              try {
                ;(node as HTMLElement).scrollTop = 0
              } catch (_) {
                // ignore
              }
            })
          })
        }
      }
    } catch (_) {
      // cross-origin or not available
    }
  } catch (_) {
    // swallow
  }
}
