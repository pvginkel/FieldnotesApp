#!/usr/bin/env node

import { writeFileSync, readFileSync } from 'fs';
import { createHash } from 'crypto';
import https from 'https';
import http from 'http';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const CACHE_DIR = path.join(__dirname, '../openapi-cache');
const CACHE_FILE = path.join(CACHE_DIR, 'openapi.json');

// Primary and fallback URLs for fetching OpenAPI spec
const BACKEND_PORT = process.env.PORT || '3401';
const OPENAPI_URLS = [
  `http://localhost:${BACKEND_PORT}/api/docs/openapi.json`
];

/**
 * Fetches content from a URL using https/http
 */
function fetchUrl(url) {
  return new Promise((resolve, reject) => {
    const client = url.startsWith('https:') ? https : http;
    
    const req = client.get(url, (res) => {
      if (res.statusCode !== 200) {
        reject(new Error(`HTTP ${res.statusCode}: ${res.statusMessage}`));
        return;
      }
      
      let data = '';
      res.on('data', chunk => data += chunk);
      res.on('end', () => {
        try {
          const parsed = JSON.parse(data);
          resolve(parsed);
        } catch (err) {
          reject(new Error(`Invalid JSON response: ${err.message}`));
        }
      });
    });
    
    req.on('error', reject);
    req.setTimeout(10000, () => {
      req.destroy();
      reject(new Error('Request timeout'));
    });
  });
}

/**
 * Tries to fetch OpenAPI spec from multiple URLs
 */
async function fetchOpenAPISpec() {
  let lastError;
  
  for (const url of OPENAPI_URLS) {
    try {
      console.log(`Attempting to fetch OpenAPI spec from ${url}...`);
      const spec = await fetchUrl(url);
      console.log(`✅ Successfully fetched OpenAPI spec from ${url}`);
      return spec;
    } catch (error) {
      console.log(`❌ Failed to fetch from ${url}: ${error.message}`);
      lastError = error;
    }
  }
  
  throw new Error(`Failed to fetch OpenAPI spec from all URLs. Last error: ${lastError.message}`);
}

/**
 * Calculates hash of OpenAPI spec for change detection
 */
function calculateSpecHash(spec) {
  const specString = JSON.stringify(spec, null, 2);
  return createHash('sha256').update(specString).digest('hex');
}

/**
 * Loads cached OpenAPI spec and metadata
 */
function loadCache() {
  try {
    const spec = JSON.parse(readFileSync(CACHE_FILE, 'utf8'));
    return spec;
  } catch (error) {
    return null;
  }
}

/**
 * Saves OpenAPI spec and metadata to cache
 */
function saveCache(spec) {
  // Save the spec
  writeFileSync(CACHE_FILE, JSON.stringify(spec, null, 2));
  
  console.log(`✅ Cached OpenAPI spec`);
}

/**
 * Main function to fetch and cache OpenAPI spec
 */
export async function fetchAndCache(options = {}) {
  const { forceRefresh = false, buildMode = false } = options;
  
  if (buildMode) {
    // Build mode: only use cache, never fetch
    const spec = loadCache();
    if (!spec) {
      throw new Error(
        'No cached OpenAPI spec found. Run "pnpm generate:api" first to fetch and cache the spec.'
      );
    }
    return spec;
  }
  
  // Check if we should use cache
  if (!forceRefresh) {
    const cache = loadCache();
    if (cache) {
      console.log(`📦 Found cached OpenAPI spec from ${cache.timestamp} (hash: ${cache.hash})`);
      
      // Try to fetch to check for changes
      try {
        const freshSpec = await fetchOpenAPISpec();
        const freshHash = calculateSpecHash(freshSpec);
        
        if (freshHash === cache.hash) {
          console.log('✅ OpenAPI spec is up to date, using cache');
          return cache.spec;
        } else {
          console.log('🔄 OpenAPI spec has changed, updating cache');
          saveCache(freshSpec);
          return freshSpec;
        }
      } catch (error) {
        console.log(`⚠️  Failed to fetch fresh spec, using cache: ${error.message}`);
        return cache.spec;
      }
    }
  }
  
  // Fetch fresh spec
  const spec = await fetchOpenAPISpec();
  saveCache(spec);
  return spec;
}

// CLI usage
if (process.argv[1] === __filename) {
  const args = process.argv.slice(2);
  const forceRefresh = args.includes('--force');
  const buildMode = args.includes('--cache-only');
  
  fetchAndCache({ forceRefresh, buildMode })
    .then(() => {
      console.log('✅ OpenAPI fetch completed successfully');
      process.exit(0);
    })
    .catch(error => {
      console.error('❌ OpenAPI fetch failed:', error.message);
      process.exit(1);
    });
}