import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

const root = document.getElementById('root')!

function showCrash(err: unknown) {
  const message = err instanceof Error ? err.stack || err.message : String(err)
  root.innerHTML = `<pre style="padding:16px;font:13px/1.45 ui-monospace,monospace;white-space:pre-wrap;color:#171717">${message.replace(/</g, '&lt;')}</pre>`
}

window.addEventListener('error', (event) => {
  if (!root.childElementCount) showCrash(event.error || event.message)
})
window.addEventListener('unhandledrejection', (event) => {
  if (!root.childElementCount) showCrash(event.reason)
})

try {
  createRoot(root).render(
    <StrictMode>
      <App />
    </StrictMode>,
  )
} catch (err) {
  showCrash(err)
}
