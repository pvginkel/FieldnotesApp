// The app's own ESLint config blocks. The template's eslint.config.js
// (template-owned) spreads this array after its own blocks, so a block here
// can add an app's rules or override the template's. This file is app-owned:
// copier writes it once and never updates it.
//
// For example, a local rule in scripts/eslint-rules/:
//
//   import myRule from './scripts/eslint-rules/my-rule.js'
//
//   export default [
//     {
//       files: ['src/**/*.{ts,tsx}'],
//       plugins: { app: { rules: { 'my-rule': myRule } } },
//       rules: { 'app/my-rule': 'error' },
//     },
//   ]

export default []
