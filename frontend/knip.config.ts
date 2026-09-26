import { readFileSync } from 'node:fs';
import type { KnipConfig } from 'knip';

/**
 * Template-owned files are listed in knip-template-ignore.json and excluded
 * from unused-export analysis. The template developer maintains that file;
 * app-specific ignores go in the config below.
 */
const templateIgnore: string[] = JSON.parse(
  readFileSync('./knip-template-ignore.json', 'utf-8'),
);

const config: KnipConfig = {
  entry: [
    'src/routes/**/*.tsx',
    'tests/**/*.spec.ts',
    'tests/support/fixtures.ts',
  ],
  project: [
    'src/**/*.{ts,tsx}',
    'tests/**/*.ts',
    'scripts/**/*.{js,cjs,ts}',
  ],
  ignore: [
    ...templateIgnore,
    'src/lib/api/generated/**',
  ],
  ignoreDependencies: [
    // Template-provided dependencies used in template-owned (ignored) code
    '@radix-ui/react-dialog',
    '@radix-ui/react-dropdown-menu',
    '@radix-ui/react-popover',
    'lucide-react',
    'openapi-typescript',
    'postcss',
    'tailwindcss',
    'ulid',
  ],
  rules: {
    files: 'error',
    dependencies: 'error',
    unlisted: 'error',
    exports: 'error',
    types: 'error',
    duplicates: 'warn',
  },
};

export default config;
