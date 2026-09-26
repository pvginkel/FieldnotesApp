import type { TestEvent, SseTestEvent } from '@/lib/test/test-events';
import { Page } from '@playwright/test';

const DEFAULT_BUFFER_CAPACITY = 500;
const PLAYWRIGHT_BINDING_NAME = '__playwright_emitTestEvent';

type EventRecord = {
  event: TestEvent;
  observed: boolean;
};

type Waiter = {
  matcher: (event: TestEvent) => boolean;
  resolve: (record: EventRecord) => void;
  observedMatchSeen: boolean;
  timeoutId?: NodeJS.Timeout;
};

function normalizeEvent(raw: any): TestEvent {
  const fallbackMillis = typeof raw?.timestamp === 'number'
    ? raw.timestamp
    // eslint-disable-next-line no-restricted-properties -- Normalize missing timestamps to epoch ms for instrumentation parity.
    : Date.now();
  const timestamp = typeof raw?.timestamp === 'string'
    ? raw.timestamp
    : new Date(fallbackMillis).toISOString();

  return {
    ...raw,
    timestamp,
  } as TestEvent;
}

export class TestEventBuffer {
  private events: EventRecord[] = [];
  private dropped = 0;
  private capacity: number;
  private waiters: Waiter[] = [];
  private overflowError: Error | null = null;

  constructor(capacity: number = DEFAULT_BUFFER_CAPACITY) {
    this.capacity = Math.max(capacity, 1);
  }

  setCapacity(capacity: number): void {
    this.capacity = Math.max(capacity, 1);
    this.trimIfNeeded();
  }

  addEvent(event: TestEvent): void {
    const normalized = normalizeEvent(event);
    const record: EventRecord = {
      event: normalized,
      observed: false,
    };
    this.events.push(record);
    this.trimIfNeeded();
    this.notifyWaiters(record);
  }

  clear(): void {
    this.events = [];
    this.dropped = 0;
    this.overflowError = null;
  }

  getOverflowError(): Error | null {
    return this.overflowError;
  }

  snapshot(cursor: number): { events: TestEvent[]; total: number } {
    const total = this.dropped + this.events.length;
    const relativeStart = Math.max(cursor - this.dropped, 0);
    const events = relativeStart < this.events.length
      ? this.events.slice(relativeStart).map(record => record.event)
      : [];
    return { events, total };
  }

  getTotalCount(): number {
    return this.dropped + this.events.length;
  }

  async waitForEvent(
    matcher: (event: TestEvent) => boolean,
    timeout: number
  ): Promise<TestEvent> {
    const effectiveTimeout = timeout > 0 ? timeout : 60_000;
    const { record: immediateRecord, observedMatchFound } = this.findMatchingEvent(matcher);
    if (immediateRecord && !observedMatchFound) {
      this.markObserved(immediateRecord);
      return immediateRecord.event;
    }

    return new Promise<TestEvent>((resolve, reject) => {
      const waiter: Waiter = {
        matcher,
        observedMatchSeen: observedMatchFound,
        resolve: (record) => {
          if (waiter.timeoutId) {
            clearTimeout(waiter.timeoutId);
          }
          this.markObserved(record);
          resolve(record.event);
        },
      };

      waiter.timeoutId = setTimeout(() => {
        this.removeWaiter(waiter);
        const baseMessage = `Timeout waiting for event matching criteria after ${effectiveTimeout}ms`;
        const message = waiter.observedMatchSeen
          ? `${baseMessage}. A matching event was already observed earlier in this test run.`
          : baseMessage;
        reject(new Error(message));
      }, effectiveTimeout);

      this.waiters.push(waiter);
    });
  }

  dispose(): void {
    for (const waiter of this.waiters) {
      if (waiter.timeoutId) {
        clearTimeout(waiter.timeoutId);
      }
    }
    this.waiters = [];
    this.clear();
  }

  private trimIfNeeded(): void {
    if (this.events.length <= this.capacity) {
      return;
    }

    const overflow = this.events.length - this.capacity;
    this.events.splice(0, overflow);
    this.dropped += overflow;

    // Set error on first overflow
    if (!this.overflowError) {
      this.overflowError = new Error(
        `Test event buffer overflow! Buffer capacity (${this.capacity}) exceeded. ` +
        `${overflow} event(s) were dropped. Total events dropped: ${this.dropped}. ` +
        `Consider increasing buffer capacity or reducing event volume in this test.`
      );
    }
  }

  private notifyWaiters(record: EventRecord): void {
    if (this.waiters.length === 0) {
      return;
    }

    const remaining: Waiter[] = [];

    for (const waiter of this.waiters) {
      if (waiter.matcher(record.event)) {
        if (record.observed) {
          waiter.observedMatchSeen = true;
          remaining.push(waiter);
          continue;
        }

        waiter.resolve(record);
        continue;
      }

      remaining.push(waiter);
    }

    this.waiters = remaining;
  }

  private removeWaiter(waiter: Waiter): void {
    this.waiters = this.waiters.filter(candidate => candidate !== waiter);
  }

  private findMatchingEvent(
    matcher: (event: TestEvent) => boolean,
  ): { record: EventRecord | null; observedMatchFound: boolean } {
    let observedMatchFound = false;

    for (const record of this.events) {
      if (!matcher(record.event)) {
        continue;
      }

      if (record.observed) {
        observedMatchFound = true;
        continue;
      }

      return { record, observedMatchFound: false };
    }

    return { record: null, observedMatchFound };
  }

  private markObserved(record: EventRecord): void {
    record.observed = true;
  }
}

const bufferRegistry = new WeakMap<Page, TestEventBuffer>();
const bindingRegistry = new WeakSet<Page>();

export async function ensureTestEventBridge(page: Page): Promise<TestEventBuffer> {
  let buffer = bufferRegistry.get(page);

  if (!buffer) {
    buffer = new TestEventBuffer(DEFAULT_BUFFER_CAPACITY);
    bufferRegistry.set(page, buffer);
  }

  if (!bindingRegistry.has(page)) {
    try {
      await page.exposeBinding(PLAYWRIGHT_BINDING_NAME, async (_source, rawEvent) => {
        buffer!.addEvent(rawEvent as TestEvent);
      });
      bindingRegistry.add(page);
    } catch (error) {
      bufferRegistry.delete(page);
      throw new Error(
        `Failed to register Playwright test event binding: ${error instanceof Error ? error.message : String(error)}`
      );
    }
  }

  buffer.setCapacity(DEFAULT_BUFFER_CAPACITY);
  return buffer;
}

export function releaseTestEventBridge(page: Page): void {
  const buffer = bufferRegistry.get(page);
  if (buffer) {
    buffer.dispose();
  }
  bufferRegistry.delete(page);
  bindingRegistry.delete(page);
}

export function getTestEventBuffer(page: Page): TestEventBuffer {
  const buffer = bufferRegistry.get(page);
  if (!buffer) {
    throw new Error('Test event bridge not initialized for this page. Ensure the testEvents fixture is registered.');
  }
  return buffer;
}

export class TestEventCapture {
  private events: TestEvent[] = [];
  private isCapturing = false;
  private bufferSize = DEFAULT_BUFFER_CAPACITY;
  private cursor = 0;

  constructor(private readonly buffer: TestEventBuffer) {}

  async startCapture(options?: { bufferSize?: number }): Promise<void> {
    this.bufferSize = Math.max(options?.bufferSize ?? DEFAULT_BUFFER_CAPACITY, 1);
    this.events = [];
    this.cursor = this.buffer.getTotalCount();
    this.isCapturing = true;
  }

  async stopCapture(): Promise<void> {
    this.isCapturing = false;
  }

  async getEvents(): Promise<TestEvent[]> {
    if (this.isCapturing) {
      const snapshot = this.buffer.snapshot(this.cursor);
      if (snapshot.total > this.cursor) {
        const newEvents = snapshot.events.map(normalizeEvent);
        this.cursor = snapshot.total;
        this.events = [...this.events, ...newEvents].slice(-this.bufferSize);
      }
    }

    return [...this.events];
  }

  async clearEvents(): Promise<void> {
    this.events = [];
    this.cursor = this.buffer.getTotalCount();
  }

  async waitForEvent(
    matcher: (event: TestEvent) => boolean,
    options?: { timeout?: number; intervalMs?: number }
  ): Promise<TestEvent> {
    const timeout = options?.timeout ?? 5000;
    const event = await this.buffer.waitForEvent(matcher, timeout);
    // Refresh local snapshot so downstream assertions see the consumed event.
    await this.getEvents();
    return event;
  }

  async assertEventEmitted(expectedEvent: Partial<TestEvent>): Promise<TestEvent> {
    const events = await this.getEvents();

    const matchedEvent = events.find(event => {
      return Object.entries(expectedEvent).every(([key, value]) => {
        if (value === undefined) {
          return true;
        }
        const actual = event[key as keyof TestEvent];
        return JSON.stringify(actual) === JSON.stringify(value);
      });
    });

    if (!matchedEvent) {
      throw new Error(
        `No event found matching: ${JSON.stringify(expectedEvent)}\n` +
        `Captured events: ${JSON.stringify(events, null, 2)}`
      );
    }

    return matchedEvent;
  }

  async assertEventSequence(expectedSequence: Array<Partial<TestEvent>>): Promise<void> {
    const events = await this.getEvents();
    let eventIndex = 0;

    for (const expectedEvent of expectedSequence) {
      let found = false;

      for (let i = eventIndex; i < events.length; i++) {
        const event = events[i];
        const matches = Object.entries(expectedEvent).every(([key, value]) => {
          if (value === undefined) {
            return true;
          }
          const actual = event[key as keyof TestEvent];
          return JSON.stringify(actual) === JSON.stringify(value);
        });

        if (matches) {
          found = true;
          eventIndex = i + 1;
          break;
        }
      }

      if (!found) {
        throw new Error(
          `Event sequence not matched. Expected: ${JSON.stringify(expectedEvent)}\n` +
          `Remaining events: ${JSON.stringify(events.slice(eventIndex), null, 2)}`
        );
      }
    }
  }

  async getEventsByKind(kind: string): Promise<TestEvent[]> {
    const events = await this.getEvents();
    return events.filter(event => event.kind === kind);
  }

  async getEventsByFeature(feature: string): Promise<TestEvent[]> {
    const events = await this.getEvents();
    return events.filter(event => {
      const candidate = event as unknown as { feature?: string };
      return candidate.feature === feature;
    });
  }

  async getLastEvent(): Promise<TestEvent | undefined> {
    const events = await this.getEvents();
    return events[events.length - 1];
  }

  async countEvents(matcher?: (event: TestEvent) => boolean): Promise<number> {
    const events = await this.getEvents();
    if (!matcher) {
      return events.length;
    }
    return events.filter(matcher).length;
  }

  async dumpEvents(): Promise<string> {
    const events = await this.getEvents();
    return JSON.stringify(events, null, 2);
  }
}

export function createTestEventCapture(page: Page): TestEventCapture {
  const buffer = getTestEventBuffer(page);
  return new TestEventCapture(buffer);
}

type SseEventCriteria = {
  streamId: string;
  phase?: SseTestEvent['phase'];
  event?: string;
  timeoutMs?: number;
  matcher?: (event: SseTestEvent) => boolean;
};

/**
 * Waits for a matching SSE test event. Deployment streams now echo `correlation_id` values.
 */
export async function waitForSseEvent(
  page: Page,
  criteria: SseEventCriteria
): Promise<SseTestEvent> {
  const buffer = getTestEventBuffer(page);
  const timeout = criteria.timeoutMs ?? 5000;

  const event = await buffer.waitForEvent(eventCandidate => {
    if (eventCandidate.kind !== 'sse') {
      return false;
    }

    const sseEvent = eventCandidate as SseTestEvent;

    if (sseEvent.streamId !== criteria.streamId) {
      return false;
    }

    if (criteria.phase && sseEvent.phase !== criteria.phase) {
      return false;
    }

    if (criteria.event && sseEvent.event !== criteria.event) {
      return false;
    }

    if (criteria.matcher) {
      return criteria.matcher(sseEvent);
    }

    return true;
  }, timeout);

  return event as SseTestEvent;
}

export function extractSseData<T = unknown>(event: SseTestEvent): T | undefined {
  return event.data as T | undefined;
}

export async function emitTestEvent(page: Page, event: Omit<TestEvent, 'timestamp'>): Promise<void> {
  await page.evaluate(async ({ evt, binding }) => {
    const globalAny = globalThis as Record<string, any>;
    const payload = {
      ...evt,
      timestamp: new Date().toISOString(),
    };

    const bridge = globalAny[binding];

    if (typeof bridge !== 'function') {
      throw new Error('Playwright test event binding is not registered on the page.');
    }

    await bridge(payload);
  }, { evt: event, binding: PLAYWRIGHT_BINDING_NAME });
}

export function unregisterTestEventBuffer(page: Page): void {
  releaseTestEventBridge(page);
}

export { DEFAULT_BUFFER_CAPACITY as TEST_EVENT_BUFFER_CAPACITY };
