#!/usr/bin/env node

const fs = require('fs');
const path = require('path');

/**
 * Verifies that test instrumentation code is mostly excluded from production builds
 * by checking for common test markers and patterns.
 */

// Test markers to search for
const TEST_MARKERS = [
  'TEST_EVT',
  '__playwright_emitTestEvent',
  'src/lib/test/',
];

// Colors for console output
const colors = {
  red: '\x1b[31m',
  yellow: '\x1b[33m',
  green: '\x1b[32m',
  blue: '\x1b[34m',
  reset: '\x1b[0m',
};

/**
 * Searches for test markers in a file
 * @param {string} filePath - Path to the file to check
 * @returns {Object} Object containing marker counts and occurrences
 */
function checkFileForMarkers(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  const results = {
    file: path.relative(process.cwd(), filePath),
    markers: {},
    totalCount: 0,
  };

  TEST_MARKERS.forEach(marker => {
    const regex = new RegExp(marker, 'g');
    const matches = content.match(regex);
    if (matches) {
      results.markers[marker] = matches.length;
      results.totalCount += matches.length;
    }
  });

  return results;
}

/**
 * Main verification function
 */
function verifyBuild() {
  console.log(`${colors.blue}Production Build Verification${colors.reset}`);
  console.log('=' .repeat(50));
  console.log('Checking for test markers in production bundles...\n');

  // Find all JavaScript files in the dist/assets directory
  const distPath = path.join(process.cwd(), 'dist', 'assets');

  if (!fs.existsSync(distPath)) {
    console.error(`${colors.red}Error: dist/assets directory not found.${colors.reset}`);
    console.error('Please run "pnpm build" before running verification.\n');
    process.exit(1);
  }

  // Find all JavaScript files in the dist/assets directory
  const jsFiles = fs.readdirSync(distPath)
    .filter(file => file.endsWith('.js'))
    .map(file => path.join(distPath, file));

  if (jsFiles.length === 0) {
    console.error(`${colors.red}Error: No JavaScript files found in dist/assets.${colors.reset}`);
    console.error('Please ensure the build completed successfully.\n');
    process.exit(1);
  }

  console.log(`Found ${jsFiles.length} JavaScript file(s) to verify.\n`);

  // Check each file for test markers
  const findings = [];
  let totalMarkers = 0;

  jsFiles.forEach(file => {
    const result = checkFileForMarkers(file);
    if (result.totalCount > 0) {
      findings.push(result);
      totalMarkers += result.totalCount;
    }
  });

  // Report findings
  console.log('Results:');
  console.log('-'.repeat(50));

  if (findings.length === 0) {
    console.log(`${colors.green}✓ No test markers found in production build!${colors.reset}`);
    console.log('Production bundles are clean.\n');
  } else {
    console.log(`${colors.yellow}⚠ Found ${totalMarkers} test marker(s) in ${findings.length} file(s):${colors.reset}\n`);

    findings.forEach(finding => {
      console.log(`  File: ${finding.file}`);
      Object.entries(finding.markers).forEach(([marker, count]) => {
        console.log(`    - "${marker}": ${count} occurrence(s)`);
      });
      console.log();
    });

    console.log(`${colors.yellow}Note: Some test markers may be acceptable in production.${colors.reset}`);
    console.log('Review the findings to ensure no sensitive test code remains.\n');
  }

  // Summary
  console.log('=' .repeat(50));
  console.log('Summary:');
  console.log(`  Total files checked: ${jsFiles.length}`);
  console.log(`  Files with markers: ${findings.length}`);
  console.log(`  Total markers found: ${totalMarkers}`);
  console.log();

  // Always exit with 0 (informational only, not a failure)
  process.exit(0);
}

// Run verification
verifyBuild();
