import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { isTestMode } from '@/lib/config/test-mode'
import { setupConsolePolicy } from '@/lib/test/console-policy'
import { setupErrorInstrumentation } from '@/lib/test/error-instrumentation'

// Setup test mode infrastructure
if (isTestMode()) {
  setupConsolePolicy();
  setupErrorInstrumentation();
}

createRoot(document.getElementById('root')!).render(
  (
    import.meta.env.DEV || import.meta.env.VITE_TEST_MODE ? (
      <StrictMode>
        <App />
      </StrictMode>
    ) : (
      <App />
    )
  )
)
