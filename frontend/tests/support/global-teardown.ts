import { closeRun, RUN_PREFIX_VARIABLE } from './helpers/pg-admin';

/**
 * Drop the run's databases: the seed global setup built, and any worker
 * database a worker's teardown left behind. Best effort, so a failed drop
 * never masks a test failure; the next run's sweep drops what is left.
 */
async function globalTeardown() {
  const prefix = process.env[RUN_PREFIX_VARIABLE];
  if (!prefix) {
    return;
  }

  try {
    await closeRun(prefix);
    console.log(`🧹 Dropped the Playwright databases (${prefix}_*)`);
  } catch (error) {
    console.warn(
      'Failed to drop the Playwright databases:',
      error instanceof Error ? error.message : String(error)
    );
  }
}

export default globalTeardown;
