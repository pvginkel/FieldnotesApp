import { defineConfig, type Plugin, type ProxyOptions } from 'vite'
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

// The backend builds absolute URLs, such as the OIDC redirect URIs, from the
// origin the browser used: its ProxyFix trusts one hop's X-Forwarded-Proto and
// X-Forwarded-Host. changeOrigin rewrites Host to the backend's, so this hop
// sends both. Values from a proxy in front (the KubeCoder front door, which
// terminates TLS) win, as in nginx's proxy.conf. Vite's `xfwd` won't do: it
// appends this hop's `http` to X-Forwarded-Proto, and ProxyFix reads the last.
const forwardOrigin: ProxyOptions['configure'] = (proxy) => {
  proxy.on('proxyReq', (proxyReq, req) => {
    if (!req.headers['x-forwarded-host'] && req.headers.host) {
      proxyReq.setHeader('X-Forwarded-Host', req.headers.host)
    }
    if (!req.headers['x-forwarded-proto']) {
      proxyReq.setHeader('X-Forwarded-Proto', 'http')
    }
  })
}

// Flush an SSE response's head at once, so EventSource.onopen fires on
// connect, not on the first heartbeat (http-proxy holds the head until data
// flows). Transfer-Encoding goes first: http-proxy drops chunking only after
// `proxyRes`, and a chunked head over the raw piped stream makes the browser
// buffer the stream forever. The `: proxy-connected` comment forces the flush.
const configureSseProxy: ProxyOptions['configure'] = (proxy) => {
  proxy.on('proxyRes', (proxyRes, _req, res) => {
    const contentType = proxyRes.headers['content-type'] ?? ''
    if (!contentType.includes('text/event-stream')) {
      return
    }
    if (!res.headersSent) {
      const headers = { ...proxyRes.headers }
      delete headers['transfer-encoding']
      res.writeHead(proxyRes.statusCode ?? 200, headers)
    }
    res.write(': proxy-connected\n\n')
  })
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
    // Fail on a busy port instead of moving to the next one: the dev stack
    // and Playwright expect the port they asked for.
    strictPort: true,
    allowedHosts: true,
    proxy: {
      ...loadAppProxies(),
      '/api/sse': {
        target: gatewayProxyTarget,
        changeOrigin: true,
        secure: false,
        configure: configureSseProxy,
      },
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
        secure: false,
        configure: forwardOrigin,
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
        configure: configureSseProxy,
      },
      '/api': {
        target: backendProxyTarget,
        changeOrigin: true,
        secure: false,
        configure: forwardOrigin,
      }
    }
  },
  // Test mode follows VITE_TEST_MODE, in a build too: `VITE_TEST_MODE=true
  // pnpm build` gives the production build the Playwright bridges, for the
  // preview-mode run (PLAYWRIGHT_PREVIEW_MODE=true). Any other build leaves
  // them out, so never run the image's `pnpm build` with VITE_TEST_MODE set.
  define: {
    'import.meta.env.VITE_TEST_MODE': JSON.stringify(process.env.VITE_TEST_MODE || 'false'),
  },
  build: {
    rollupOptions: {
      external: process.env.VITE_TEST_MODE !== 'true'
        ? ['./src/lib/test/*']
        : []
    },
    chunkSizeWarningLimit: 2000
  }
})
