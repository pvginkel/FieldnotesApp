import { RuleTester } from 'eslint';
import rule from '../no-route-mocks.js';

const parserModule = await import('@typescript-eslint/parser');
const parser = (parserModule as unknown as { default?: any }).default ?? parserModule;

const ruleTester = new RuleTester({
  languageOptions: {
    parser,
    parserOptions: {
      ecmaVersion: 2020,
      sourceType: 'module',
    },
  },
});

ruleTester.run('testing/no-route-mocks', rule as any, {
  valid: [
    {
      code: `
        await apiClient.GET('/api/parts');
      `,
    },
    {
      code: `
        const work = async () => {
          // eslint-disable-next-line testing/no-route-mocks -- AI analysis helper seeds backend stream
          await aiAnalysisMock();
        };
      `,
    },
    {
      code: `
        const onSseMessage = (event: MessageEvent) => {
          console.log(event.data);
        };
      `,
    },
  ],
  invalid: [
    {
      code: `
        await page.route('**/api/types', handler);
      `,
      errors: [{ messageId: 'routeCall' }],
    },
    {
      code: `
        const { route: intercept } = page;
        await intercept('**/api/parts', handler);
      `,
      errors: [{ messageId: 'routeCall' }],
    },
    {
      code: `
        await context.route('**/api/parts', handler);
      `,
      errors: [{ messageId: 'routeCall' }],
    },
    {
      code: `
        const { mockSSE } = sseMocker;
        await mockSSE({ url: /test/, events: [] });
      `,
      errors: [{ messageId: 'sseMock' }],
    },
    {
      code: `
        const setup = async () => {
          return route.fulfill({ status: 200, body: 'ok' });
        };
      `,
      errors: [{ messageId: 'routeHandler' }],
    },
    {
      code: `
        // eslint-disable-next-line testing/no-route-mocks
        await page.route('**/api/types', handler);
      `,
      errors: [
        { messageId: 'missingDisableReason', line: 2 },
        { messageId: 'routeCall', line: 3 },
      ],
    },
  ],
});
