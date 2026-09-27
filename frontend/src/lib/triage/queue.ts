// The triage queue as `GET /api/triage/queue` answers it (FR-23), and the rules the screen reads
// it by: the mockup's (FieldnotesAppSpecs/mockups/triage-ui/app.js).

import type {
  TriageQueue_b288cbf,
  TriageQueue_b288cbf_Ruling,
  TriageQueue_b288cbf_Snapshot,
  TriageQueue_b288cbf_TriageItem,
  TriageQueue_b288cbf_Verb,
} from '@/lib/api/generated/hooks';
import { ago } from './time';

export type Queue = TriageQueue_b288cbf;
export type Item = TriageQueue_b288cbf_TriageItem;
export type Ruling = TriageQueue_b288cbf_Ruling;
export type Snapshot = TriageQueue_b288cbf_Snapshot;
export type Verb = TriageQueue_b288cbf_Verb;

export const YOUTRACK = 'https://issues.webathome.org/issue/';
export const GITHUB = 'https://github.com/pvginkel/Fieldnotes/blob/main/observations/';

/** A returned item counts as ruled only once it is ruled again after the question. */
export const isRuled = (item: Item): boolean =>
  Boolean(item.ruling) && (!item.question || item.ruling!.at > item.question.at);

/** Returned items first, then oldest written first; skipped items last. */
export const stackOrder = (items: Item[], skipped: Record<string, string>): string[] => {
  const rank = (item: Item) => (item.question ? 0 : skipped[item.observation] ? 2 : 1);
  const key = (item: Item) => skipped[item.observation] || item.written;
  return [...items]
    .sort((a, b) => rank(a) - rank(b) || key(a).localeCompare(key(b)))
    .map((item) => item.observation);
};

/** What changed between the item's snapshot and the observation as the store has it now. */
export const changesOf = (item: Item, live: Snapshot | null | undefined): string[] => {
  if (!live) return ['merged away or retired since the item was written'];
  const was = item.snapshot;
  const changes: string[] = [];
  if (live.card && live.card !== was.card) changes.push(`carded ${live.card}`);
  if (live.status !== was.status) changes.push(`${was.status} → ${live.status}`);
  if (live.last_seen !== was.last_seen) changes.push(`reported again ${ago(live.last_seen)}`);
  if (live.canonical !== was.canonical) changes.push('statement rewritten');
  if (live.reason !== was.reason && live.reason) changes.push('a reason set');
  return changes;
};

/** The owners of the queue's repositories: an `owner/name` in the prose for one of them is a
 *  repository name. */
export const ownersOf = (queue: Queue): string[] => [
  ...new Set(
    queue.items
      .flatMap((item) => [...item.snapshot.repos, ...item.reports.map((report) => report.repo)])
      .map((name) => name.split('/')[0]),
  ),
];
