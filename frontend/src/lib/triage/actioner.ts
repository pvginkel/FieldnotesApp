// The actioner as the app shows it (FR-27, FR-33): its state, as `GET /api/triage/actioner`
// answers it, and what a failure says, in the Actioner pill's tooltip and in its dialog alike.

import type {
  ActionerState_0c09c4d,
  ActionerState_0c09c4d_ActionerFailure,
} from '@/lib/api/generated/hooks';
import { stamp } from './time';

export type ActionerState = ActionerState_0c09c4d;
export type ActionerFailure = ActionerState_0c09c4d_ActionerFailure;

/** A start pending or a run held: the actioner page shows, and the pill opens it. */
export const isBusy = (state: ActionerState | undefined): boolean =>
  state?.state === 'starting' || state?.state === 'running';

/** What ended without succeeding: a run that failed or was skipped, or a start the controller
 *  refused. */
export const whatFailed = (failure: ActionerFailure): string =>
  failure.outcome === 'refused'
    ? "The controller refused the actioner's start"
    : `The actioner's last run ${failure.outcome === 'skipped' ? 'was skipped' : 'failed'}`;

/** The failure in a line: what, when, and why. */
export const failed = (failure: ActionerFailure): string =>
  `${whatFailed(failure)} at ${stamp(failure.at)}${failure.reason ? `: ${failure.reason}` : ''}`;
