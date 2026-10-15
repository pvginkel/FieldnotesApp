/**
 * The worker backend's KubeCoder controller: a fake, laid out through `/api/testing/controller`
 * (backend/app/api/testing_controller.py), that plays the inbox sitting's session and the
 * actioner's prompt runs. A test that reads the inbox or the app bar's pills lays it out first: a
 * layout ends the sitting the last one held and starts the actioner afresh. FieldnotesApp is
 * public: every sitting is invented.
 */

import type { APIRequestContext } from '@playwright/test';
import { expect } from '@playwright/test';

/** An environment of the store's project; `start_as` is what a start brings a stopped one to.
 *  `working` names headless sessions of a running one busy at the layout, which an actioner run's
 *  follow sees past. */
interface EnvironmentLayout {
  env_id: string;
  status?: 'running' | 'stopped';
  start_as?: 'running' | 'starting' | 'error' | 'admission-blocked';
  working?: string[];
}

/** A message of a turn, whole; `final` when its text is part of the turn's result. */
interface MessageLayout {
  text: string;
  final?: boolean;
}

/** A session's turn, the sitting's next send or the next actioner run's: its log lines and its
 *  messages, in the order the log holds them, then its result, `pace` seconds apart; a `held` one
 *  stays in flight after its lines until `finish` (`runFinish` for a run's). The result is the
 *  layout's to match: the worker's joins the final messages' texts with blank lines, and a reply
 *  shows it only where it differs from them. */
interface TurnLayout {
  lines?: (string | MessageLayout)[];
  pace?: number;
  result?: string;
  is_error?: boolean;
  held?: boolean;
}

/** `inbox` false: the inbox not configured. `runs`: the answer to each prompt run, the last one
 *  to every run after; 202 accepts it, another status refuses it, null fails to connect. An
 *  accepted run plays the next of `run_turns` in a new headless session of the first running
 *  environment, else of the first stopped one, which it starts; with no environment it plays
 *  nothing, and a run past `run_turns` plays no line and is over at once. */
interface ControllerLayout {
  inbox?: boolean;
  environments?: EnvironmentLayout[];
  turns?: TurnLayout[];
  run_turns?: TurnLayout[];
  runs?: (number | null)[];
}

interface ControllerReading {
  environments: {
    env_id: string;
    status: string;
    sessions: { name: string; mode: string; cwd: string; status: string; messages: string[] }[];
  }[];
  runs: string[];
}

export class Controller {
  constructor(
    private readonly request: APIRequestContext,
    private readonly backendUrl: string,
  ) {}

  private async call(method: 'put' | 'post', path: string, data: object): Promise<void> {
    const response = await this.request[method](`${this.backendUrl}/api/testing/controller${path}`, { data });
    expect(response.status(), await response.text()).toBe(204);
  }

  /** The controller the API is served from now on. */
  layOut(layout: ControllerLayout = {}): Promise<void> {
    return this.call('put', '', layout);
  }

  /** A line of the sitting's turn in flight, as the session's condensed log writes it, or a
   *  message of it. */
  emit(line: string | MessageLayout): Promise<void> {
    return this.call('post', '/emit', { line });
  }

  /** The sitting's turn in flight ends with this result. */
  finish(result = 'done'): Promise<void> {
    return this.call('post', '/finish', { result });
  }

  /** What the controller holds: each environment's status and its sessions, with the messages
   *  each was sent. */
  async read(): Promise<ControllerReading> {
    const response = await this.request.get(`${this.backendUrl}/api/testing/controller`);
    expect(response.status(), await response.text()).toBe(200);
    return (await response.json()) as ControllerReading;
  }

  /** The sitting lost under the API: its worker forgets its session, or its environment stops. */
  lose(what: 'session' | 'environment'): Promise<void> {
    return this.call('post', '/lose', { what });
  }

  /** The sitting closed now as the idle expiry closes it: ended, or a lost one kept lost. */
  expire(): Promise<void> {
    return this.call('post', '/expire', {});
  }

  /** A line of the turn in flight of the last prompt run the controller accepted. */
  runEmit(line: string): Promise<void> {
    return this.call('post', '/run-emit', { line });
  }

  /** That run's turn ends with this result, and the controller ends its session; the run's end is
   *  `runEnd`'s. */
  runFinish(result = 'done'): Promise<void> {
    return this.call('post', '/run-finish', { result });
  }

  /** The last prompt run the controller accepted ends as its webhook reports it: no outcome is a
   *  success. */
  runEnd(outcome: 'failed' | 'skipped' | null, reason: string | null = null): Promise<void> {
    return this.call('post', '/run-end', { outcome, reason });
  }
}
