/**
 * The worker backend's KubeCoder controller: a fake, laid out through `/api/testing/controller`
 * (backend/app/api/testing_controller.py), that plays the inbox sitting's session and the
 * actioner's prompt runs. A test that reads the inbox or the app bar's pills lays it out first: a
 * layout ends the sitting the last one held and starts the actioner afresh. FieldnotesApp is
 * public: every sitting is invented.
 */

import type { APIRequestContext } from '@playwright/test';
import { expect } from '@playwright/test';

/** An environment of the store's project; `start_as` is what a start brings a stopped one to. */
interface EnvironmentLayout {
  env_id: string;
  status?: 'running' | 'stopped';
  start_as?: 'running' | 'starting' | 'error' | 'admission-blocked';
}

/** The next send's turn: its log lines, then its result, `pace` seconds apart; a `held` one
 *  stays in flight after its lines until `finish`. */
interface TurnLayout {
  lines?: string[];
  pace?: number;
  result?: string;
  is_error?: boolean;
  held?: boolean;
}

/** `inbox` false: the inbox not configured. `runs`: the answer to each prompt run, the last one
 *  to every run after; 202 accepts it, another status refuses it, null fails to connect. */
interface ControllerLayout {
  inbox?: boolean;
  environments?: EnvironmentLayout[];
  turns?: TurnLayout[];
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

  /** A line of the sitting's turn in flight, as the session's condensed log writes it. */
  emit(line: string): Promise<void> {
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

  /** The last prompt run the controller accepted ends as its webhook reports it: no outcome is a
   *  success. */
  runEnd(outcome: 'failed' | 'skipped' | null, reason: string | null = null): Promise<void> {
    return this.call('post', '/run-end', { outcome, reason });
  }
}
