// The inbox screen's logic (FR-28 to FR-32): the sitting as `GET /api/inbox` answers it, the three
// writes, and the turn in flight followed over the template's task channel. The page follows the
// task the latest sitting names, so a page that opens mid-turn joins it, and a turn another pod
// took over is followed under its new task (FR-30). While a turn is in flight the sitting is also
// fetched on a timer, so a task the page does not hear only slows it down.

import { useCallback, useEffect, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { WORKING_LINES } from '@/components/shell/working-box';
import { useSSETask } from '@/hooks/use-sse-task';
import type { Toast } from '@/hooks/use-triage';
import { api } from '@/lib/api/client';
import { useGetInbox } from '@/lib/api/generated/hooks';
import type {
  InboxState_5430f38,
  InboxState_5430f38_InboxEntry,
  InboxState_5430f38_InboxSitting,
} from '@/lib/api/generated/hooks';

type InboxState = InboxState_5430f38;
export type Sitting = InboxState_5430f38_InboxSitting;
export type Entry = InboxState_5430f38_InboxEntry;

/** The lines the task said since the sitting was fetched at `fetched`. */
interface Heard {
  task: string;
  fetched: number;
  lines: string[];
}

const heardSince = (heard: Heard | null, task: string, fetched: number): heard is Heard =>
  heard !== null && heard.task === task && heard.fetched === fetched;

const KEY = ['getInbox', undefined];
const DRAFT = 'fieldnotes-inbox-draft';

interface Reply {
  data?: InboxState;
  error?: unknown;
  response: Response;
}

// What a write the API took makes of the sitting. Start and Send answer it; End answers `204` with
// no body, which openapi-fetch reads as `{}`, so its sitting is cleared here.
type Next = (reply: Reply, state: InboxState | undefined) => InboxState | undefined;
const answered: Next = (reply) => reply.data;
const cleared: Next = (_, state) => state && { ...state, sitting: null };

const problemTitle = (error: unknown): string => {
  const title = error && typeof error === 'object' && 'title' in error ? String(error.title) : '';
  return title || 'the API refused it';
};

export function useInbox() {
  const queryClient = useQueryClient();
  const query = useGetInbox(undefined, {
    refetchInterval: (q) => ((q.state.data as InboxState | undefined)?.sitting?.turn ? 5000 : false),
  });
  const state = query.data;
  const sitting = state?.sitting ?? null;
  const turn = sitting?.turn ?? null;

  // The turn's last log lines: those the sitting held when it was last fetched, then those the
  // task said since. Each fetch replaces what was heard before it, its lines changed or not.
  const [heard, setHeard] = useState<Heard | null>(null);
  const followed = useRef<string | null>(null);
  // The API writes each line to the sitting's file before it sends it, so a fetch holds the lines
  // said before it: the task channel's replay of them on a subscribe would show them twice.
  const replaying = useRef(false);
  // Tasks whose end the page has seen. The template's task channel replays a task's events, its
  // end included, on every subscribe: following one again while a fetched sitting still names it
  // ends it again and fetches again, in a loop.
  const finished = useRef(new Set<string>());

  const settle = useCallback(() => {
    if (followed.current) finished.current.add(followed.current);
    followed.current = null;
    void queryClient.invalidateQueries({ queryKey: KEY });
  }, [queryClient]);

  const onProgress = useCallback(
    (line: string) => {
      const fetched = queryClient.getQueryState<InboxState>(KEY);
      const task = fetched?.data?.sitting?.turn?.task;
      if (replaying.current || !fetched || !task || task !== followed.current) return;
      setHeard((before) => ({
        task,
        fetched: fetched.dataUpdatedAt,
        lines: [...(heardSince(before, task, fetched.dataUpdatedAt) ? before.lines : []), line].slice(
          -WORKING_LINES,
        ),
      }));
    },
    [queryClient],
  );

  const { subscribeToTask, unsubscribe } = useSSETask({ onProgress, onResult: settle, onError: settle });

  useEffect(() => {
    if (turn && followed.current !== turn.task && !finished.current.has(turn.task)) {
      followed.current = turn.task;
      replaying.current = true;
      subscribeToTask(turn.task);
      replaying.current = false;
    } else if (!turn && followed.current) {
      followed.current = null;
      unsubscribe();
    }
  }, [turn, subscribeToTask, unsubscribe]);

  const lines = turn
    ? [...turn.lines, ...(heardSince(heard, turn.task, query.dataUpdatedAt) ? heard.lines : [])].slice(
        -WORKING_LINES,
      )
    : [];

  // Toasts, for a write the API refused.
  const [toasts, setToasts] = useState<Toast[]>([]);
  const nextToast = useRef(0);
  const toast = useCallback((text: string, error = false) => {
    const id = ++nextToast.current;
    setToasts((all) => [...all, { id, text, error }]);
    setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), 5000);
  }, []);

  const [busy, setBusy] = useState(false);
  const write = useCallback(
    async (call: () => Promise<Reply>, failed: string, next: Next): Promise<boolean> => {
      setBusy(true);
      try {
        const reply = await call();
        if (reply.response.ok) {
          // A fetch in flight would answer the sitting as it stood before the write.
          await queryClient.cancelQueries({ queryKey: KEY });
          queryClient.setQueryData<InboxState>(KEY, (s) => next(reply, s));
          return true;
        }
        toast(`${failed}: ${problemTitle(reply.error)}`, true);
        void queryClient.invalidateQueries({ queryKey: KEY });
        return false;
      } catch {
        toast(`${failed}: the API did not answer`, true);
        return false;
      } finally {
        setBusy(false);
      }
    },
    [queryClient, toast],
  );

  // The operator's answer, kept in the browser until it is sent, so a reload does not lose it.
  const [draft, setDraftState] = useState(() => localStorage.getItem(DRAFT) ?? '');
  const setDraft = useCallback((text: string) => {
    setDraftState(text);
    if (text) localStorage.setItem(DRAFT, text);
    else localStorage.removeItem(DRAFT);
  }, []);

  const start = useCallback(
    (scope: string) =>
      write(() => api.POST('/api/inbox/start', { body: { scope } }) as Promise<Reply>, 'Start failed', answered),
    [write],
  );

  const send = useCallback(async () => {
    const message = draft.trim();
    if (!message) return false;
    const sent = await write(
      () => api.POST('/api/inbox/turns', { body: { message } }) as Promise<Reply>,
      'Send failed',
      answered,
    );
    if (sent) setDraft('');
    return sent;
  }, [draft, write, setDraft]);

  const end = useCallback(
    () => write(() => api.POST('/api/inbox/end') as Promise<Reply>, 'End failed', cleared),
    [write],
  );

  return { query, state, sitting, turn, lines, busy, toasts, draft, setDraft, start, send, end };
}
