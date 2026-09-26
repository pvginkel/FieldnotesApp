import { defineConfig, type Plugin } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'
import { execSync } from 'child_process'
import fs from 'fs'
import { loadAppProxies } from './vite-proxy-extensions'

function versionPlugin(): Plugin {
  const getGitCommitId = () => {
    try {
      // First try to read from git-rev file (for Docker builds)
      const gitRevFile = path.resolve(__dirname, 'git-rev')
      if (fs.existsSync(gitRevFile)) {
        const fileContent = fs.readFileSync(gitRevFile, 'utf8').trim()
        if (fileContent) {
          return fileContent
        }
      }
      // Fallback to git command
      return execSync('git rev-parse HEAD', { encoding: 'utf8' }).trim()
    } catch (error) {
      console.warn('Failed to get git commit:', error)
      return 'unknown'
    }
  }

  return {
    name: 'version-plugin',
    generateBundle() {
      const gitCommitId = getGitCommitId()
      const versionData = { version: gitCommitId }
      this.emitFile({
        type: 'asset',
        fileName: 'version.json',
        source: JSON.stringify(versionData, null, 2)
      })
    }
  }
}

function backendProxyStatusPlugin(target: string): Plugin {
  const probeUrl = new URL('/health/readyz', target).toString()

  const checkBackend = async () => {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 2000)
    try {
      const response = await fetch(probeUrl, { signal: controller.signal })
      if (!response.ok) {
        console.warn(
          `[vite] Backend at ${target} responded with ${response.status}. ` +
          'Ensure the backend is running or update BACKEND_URL.'
        )
      }
    } catch (error) {
      const reason = error instanceof Error ? error.message : 'Unknown error'
      console.warn(
        `[vite] Unable to reach backend at ${target}: ${reason}. ` +
        'Start the backend or set BACKEND_URL to a reachable URL.'
      )
    } finally {
      clearTimeout(timeoutId)
    }
  }

  const safeCheck = () => {
    checkBackend().catch(() => {})
  }

  return {
    name: 'backend-proxy-status',
    configureServer() { safeCheck() },
    configurePreviewServer() { safeCheck() }
  }
}

const backendProxyTarget = process.env.BACKEND_URL || 'http://localhost:3401'
const gatewayProxyTarget = process.env.SSE_GATEWAY_URL || 'http://localhost:3402'

export default defineConfig({
  plugins: [tailwindcss(), react(), versionPlugin(), backendProxyStatusPlugin(backendProxyTarget)],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
      '@/components': path.resolve(__dirname, './src/components'),
      '@/routes': path.resolve(__dirname, './src/routes'),
      '@/lib': path.resolve(__dirname, './src/lib'),
      '@/hooks': path.resolve(__dirname, './src/hooks'),
      '@/types': path.resolve(__dirname, './src/types'),
    },
  },
  server: {
    host: true,
    port: 3400,
    allowedHosts: true,
    proxy: {
      ...loadAppProxies(),
      '/api/sse': {
        target: gatewayProxyTarget,
        changeOrigin: true,
        secure: false,
      },
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
        secure: false,
      }
    },
    watch: process.env.VITE_TEST_MODE === 'true' ? { ignored: ['**'] } : { ignored: ['**/.pnpm-store/**'] }
  },
  preview: {
    proxy: {
      ...loadAppProxies(),
      '/api/sse': {
        target: gatewayProxyTarget,
        changeOrigin: true,
        secure: false,
      },
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
        secure: false,
      }
    }
  },
  define: {
    'import.meta.env.VITE_TEST_MODE': process.env.NODE_ENV === 'production'
      ? JSON.stringify('false')
      : JSON.stringify(process.env.VITE_TEST_MODE || 'false'),
  },
  build: {
    rollupOptions: {
      external: process.env.NODE_ENV === 'production' && process.env.VITE_TEST_MODE === 'true'
        ? ['./src/lib/test/*']
        : []
    },
    chunkSizeWarningLimit: 2000
  }
})
