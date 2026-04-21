/**
 * Post-process Vite singlefile output for BaoTa plugin compatibility.
 *
 * BaoTa loads plugin index.html content into a panel container — it must NOT
 * be a full HTML document. This script strips <!DOCTYPE>, <html>, <head>,
 * <body> wrappers and keeps only <style>, <div id="app">, and <script> tags
 * that are actual top-level HTML elements (not JS string literals).
 */
import { readFileSync, writeFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const distHtml = resolve(__dirname, '..', 'frontend', 'dist', 'index.html')
const rootHtml = resolve(__dirname, '..', 'index.html')

const html = readFileSync(distHtml, 'utf-8')

// Extract content from <head> (only <style> and <script> tags, skip meta/title)
const headMatch = html.match(/<head[^>]*>([\s\S]*?)<\/head>/i)
const bodyMatch = html.match(/<body[^>]*>([\s\S]*?)<\/body>/i)

if (!headMatch || !bodyMatch) {
  console.error('[postbuild] Could not parse HTML structure')
  process.exit(1)
}

const headContent = headMatch[1]
const bodyContent = bodyMatch[1].trim()

// From <head>, extract only top-level <style ...>...</style> and <script ...>...</script> tags.
// These are the real inlined assets from vite-plugin-singlefile.
// We use a non-greedy approach matching from the HTML level.
const parts = []

// Extract real <style> blocks (top-level only, with rel="stylesheet")
const styleRe = /<style\s+rel="stylesheet"[^>]*>[\s\S]*?<\/style>/gi
let m
while ((m = styleRe.exec(headContent)) !== null) {
  parts.push(m[0])
}

// Body content (the <div id="app">)
parts.push(bodyContent)

// Extract <script> blocks from head, clean attributes for BaoTa
const scriptRe = /<script[^>]*>[\s\S]*?<\/script>/gi
while ((m = scriptRe.exec(headContent)) !== null) {
  let tag = m[0]
  tag = tag.replace(/ crossorigin/g, '')
  tag = tag.replace(/ type="module"/g, '')
  // Replace ES module export with a direct invocation — BaoTa evaluates scripts in classic mode
  tag = tag.replace(/\}\);export default\s+(\w+)\(\);?<\/script>$/, '});$1();<\/script>')
  parts.push(tag)
}

const output = parts.join('\n')

writeFileSync(rootHtml, output, 'utf-8')
console.log(`[postbuild] Wrote BaoTa-compatible index.html (${(output.length / 1024).toFixed(1)} KB)`)
