/**
 * The triage suite's store: invented observations and triage items, laid out in the worker
 * backend's store through `/api/testing/store` (backend/app/api/testing_store.py), and read back
 * to see what the UI wrote. FieldnotesApp is public: nothing here comes from the real store.
 *
 * The files are spelled as the store's writers spell them: an observation as the file the API
 * writes (docs/observation-file.md), an item as `reconcile.py triage` writes it (the item keys of
 * backend/app/fieldnotes/triage.py). Every string in the frontmatter is JSON, which YAML reads as a
 * double-quoted scalar.
 */

import type { APIRequestContext } from '@playwright/test';
import { expect } from '@playwright/test';
import { ulid } from 'ulid';

export type Verb = 'yes' | 'no' | 'later';

interface Reaction {
  at: string;
  emoji: string;
  repo: string;
  session: string | null;
  client: string | null;
  text: string | null;
}

interface Observation {
  id: string;
  status: string;
  category: string;
  area: string;
  canonical: string;
  repos: string[];
  card: string | null;
  outcome: string | null;
  reason: string | null;
  created: string;
  last_seen: string;
  reactions: Reaction[];
}

interface Ruling {
  verb: Verb;
  note: string;
  at: string;
  submitted: string | null;
}

interface Item {
  observation: string;
  written: string;
  headline: string;
  ask: string;
  evidence: string;
  recommendation: string;
  impact: string;
  snapshot: Omit<Observation, 'id' | 'reactions'>;
  reports: (Reaction & { new: boolean })[];
  ruling: Ruling | null;
  question: { at: string; text: string } | null;
  actioned: null;
}

const REPO = 'acme/widgets';

/** A time `minutes` before now, in the store's spelling: UTC, whole seconds, `Z`. */
export function minutesAgo(minutes: number): string {
  // A real timestamp: the page shows times relative to now.
  // eslint-disable-next-line no-restricted-properties
  return new Date(Date.now() - minutes * 60_000).toISOString().replace(/\.\d+Z$/, 'Z');
}

export function observation(fields: Partial<Observation> = {}): Observation {
  const canonical = fields.canonical ?? 'The widget build caches nothing between runs.';
  const created = fields.created ?? minutesAgo(3 * 24 * 60);
  return {
    id: ulid(),
    status: 'open',
    category: 'friction',
    area: 'widget build',
    canonical,
    repos: [REPO],
    card: null,
    outcome: null,
    reason: null,
    created,
    last_seen: created,
    reactions: [{ at: created, emoji: '📝', repo: REPO, session: 's-1', client: 'mcp', text: canonical }],
    ...fields,
  };
}

/** The observation's fields as an item's snapshot copies them. */
function snapshotOf(obs: Observation): Item['snapshot'] {
  const { status, category, area, canonical, repos, card, outcome, reason, created, last_seen } = obs;
  return { status, category, area, canonical, repos, card, outcome, reason, created, last_seen };
}

/** An item on the observation, its snapshot and reports taken from it as they stand. */
export function item(obs: Observation, fields: Partial<Item> = {}): Item {
  return {
    observation: obs.id,
    written: minutesAgo(60),
    headline: `Widget builds start cold (${obs.id.slice(-4)})`,
    ask: 'Keep the build cache between runs?',
    evidence: `One report from ${REPO}.`,
    recommendation: 'Yes: a cache directory on the runner is cheap.',
    impact: 'Every build pays the full compile.',
    snapshot: snapshotOf(obs),
    reports: obs.reactions.map((reaction) => ({ ...reaction, new: false })),
    ruling: null,
    question: null,
    actioned: null,
    ...fields,
  };
}

const yaml = (value: unknown) => JSON.stringify(value);

function observationFile(obs: Observation): string {
  const frontmatter = {
    id: obs.id,
    status: obs.status,
    area: obs.area,
    category: obs.category,
    repos: obs.repos,
    created: obs.created,
    last_updated: obs.last_seen,
    last_reviewed: null,
    last_seen: obs.last_seen,
    canonical: obs.canonical,
    outcome: obs.outcome,
    reason: obs.reason,
    card: obs.card,
    card_updated: null,
    pointer: null,
  };
  const reactions = obs.reactions.map((reaction) =>
    Object.entries(reaction)
      .filter(([, value]) => value !== null)
      .map(([key, value], i) => `${i ? '  ' : '- '}${key}: ${yaml(value)}`)
      .join('\n'),
  );
  return [
    '---',
    ...Object.entries(frontmatter).map(([key, value]) => `${key}: ${yaml(value)}`),
    '---',
    '',
    '### reactions',
    '',
    ...reactions,
    '',
    '### comments',
    '',
  ].join('\n');
}

const itemFile = (it: Item) => `${JSON.stringify(it, null, 2)}\n`;

export class TriageStore {
  constructor(
    private readonly request: APIRequestContext,
    private readonly backendUrl: string,
  ) {}

  private get url() {
    return `${this.backendUrl}/api/testing/store`;
  }

  /** The store becomes these observations and items, and nothing else, indexed before it returns. */
  async layOut({ observations = [], items = [] }: { observations?: Observation[]; items?: Item[] }): Promise<void> {
    const files: Record<string, string> = {};
    for (const obs of observations) files[`observations/${obs.id}.md`] = observationFile(obs);
    for (const it of items) files[`triage/${it.observation}.json`] = itemFile(it);
    const response = await this.request.put(this.url, { data: { files } });
    expect(response.status(), await response.text()).toBe(204);
  }

  /** The item as the store holds it now. */
  async item(id: string): Promise<Item> {
    const response = await this.request.get(this.url);
    expect(response.ok(), await response.text()).toBe(true);
    const { files } = (await response.json()) as { files: Record<string, string> };
    const text = files[`triage/${id}.json`];
    expect(text, `triage/${id}.json is in the store`).toBeDefined();
    return JSON.parse(text) as Item;
  }

  /** The item's ruling as the store holds it, for `expect.poll`: a write is in flight after the
   *  card has moved on. */
  async ruling(id: string): Promise<Ruling | null> {
    return (await this.item(id)).ruling;
  }
}

/** `n` items, the oldest written first, each on an observation of its own. */
export function stack(n: number, fields: (i: number) => Partial<Item> = () => ({})) {
  const observations = Array.from({ length: n }, () => observation());
  const items = observations.map((obs, i) => item(obs, { written: minutesAgo(100 - i), ...fields(i) }));
  return { observations, items, ids: items.map((it) => it.observation) };
}
