// The triage screen's logic: the mockup's app.js (FieldnotesAppSpecs/mockups/triage-ui/), with
// the API as the store. The queue is `GET /api/triage/queue` (FR-23); a ruling, its take-back and
// a submit are the store's writes (FR-25, FR-26), each pushed before its reply, and a failed
// write is the reply: the card shows what the store holds again, the note comes back as a draft,
// and a toast says why. Drafts, skips, the visit order and the preferences are the browser's.

import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { api } from '@/lib/api/client';
import { useGetTriageQueue } from '@/lib/api/generated/hooks';
import {
  applyTheme,
  loadBrowserState,
  saveBrowserState,
  watchSystemTheme,
  type BrowserState,
  type Draft,
  type Prefs,
} from '@/lib/triage/browser-state';
import { isRuled, stackOrder, type Item, type Queue, type Ruling, type Verb } from '@/lib/triage/queue';
import { storeNow } from '@/lib/triage/time';

const QUEUE_KEY = ['getTriageQueue', undefined];

// Where the operator is: the stack for this visit (what was seen, then the rest in stack order),
// the card shown, and whether the page is past the last card, where an item arriving meanwhile
// does not move it.
interface Nav {
  order: string[];
  idx: number;
  atFinish: boolean;
}

type View = 'loading' | 'error' | 'empty' | 'finish' | 'card';

export interface Toast {
  id: number;
  text: string;
  error: boolean;
}

// A write in flight: the ruling the card shows meanwhile, and which write it is, so an earlier
// write on the same card settling does not take a later one's off.
interface Pending {
  ruling: Ruling | null;
  seq: number;
}

interface Reply<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

const build = (items: Item[], browser: BrowserState): Nav => {
  const ids = new Set(items.map((item) => item.observation));
  const seen = browser.seen.filter((id) => ids.has(id));
  const rest = stackOrder(items.filter((item) => !seen.includes(item.observation)), browser.skipped);
  const order = [...seen, ...rest];
  return { order, idx: seen.length, atFinish: seen.length >= order.length };
};

// The queue fetched again: an item that left it leaves the stack, a new one joins its end, and
// the card shown stays shown.
const reconcile = (nav: Nav, items: Item[], skipped: Record<string, string>): Nav => {
  const ids = new Set(items.map((item) => item.observation));
  const kept = nav.order.filter((id) => ids.has(id));
  const added = stackOrder(items.filter((item) => !kept.includes(item.observation)), skipped);
  const order = [...kept, ...added];
  if (!nav.order.length) return { order, idx: 0, atFinish: false };
  const idx = nav.order.slice(0, nav.idx).filter((id) => ids.has(id)).length;
  return { order, idx, atFinish: nav.atFinish || idx >= order.length };
};

// The pane starts from the ruling in force. A returned item starts empty: its old ruling is in
// the actioner's box, and the question may well change it.
const draftIn = (drafts: Record<string, Draft>, item: Item): Draft => {
  const draft = drafts[item.observation];
  if (draft) return draft;
  return isRuled(item) ? { verb: item.ruling!.verb, note: item.ruling!.note } : { verb: null, note: '' };
};

const without = <T,>(record: Record<string, T>, id: string) => {
  const rest = { ...record };
  delete rest[id];
  return rest;
};

const plural = (n: number, word: string) => (n === 1 ? word : `${word}s`);

// What a refused or failed write says: the problem's title and detail, or the network's error.
const failure = (error: unknown): string => {
  if (error && typeof error === 'object' && 'title' in error) {
    const problem = error as { title: string; detail?: string | null };
    return problem.detail ? `${problem.title}: ${problem.detail}.` : `${problem.title}.`;
  }
  return `The store did not answer: ${error instanceof Error ? error.message : String(error)}.`;
};

export function useTriage() {
  const queryClient = useQueryClient();
  // Fetched again when the operator comes back to the tab: that is how new items arrive.
  const query = useGetTriageQueue(undefined, { staleTime: 0, refetchOnWindowFocus: true });
  const [browser, setBrowser] = useState(loadBrowserState);
  const [pending, setPending] = useState<Record<string, Pending>>({});
  const [nav, setNav] = useState<Nav | null>(null);
  const [navFor, setNavFor] = useState<Item[] | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [toasts, setToasts] = useState<Toast[]>([]);
  const inFlight = useRef(new Set<Promise<void>>());
  const counter = useRef(0);
  const noteRef = useRef<HTMLTextAreaElement>(null);
  const verbsRef = useRef<HTMLDivElement>(null);
  const mainRef = useRef<HTMLElement>(null);

  useEffect(() => saveBrowserState(browser), [browser]);

  const theme = browser.prefs.theme;
  useLayoutEffect(() => {
    applyTheme(theme);
    return watchSystemTheme(theme);
  }, [theme]);

  const queue = query.data as Queue | undefined;
  // The items as the store holds them, with a write in flight showing its ruling already.
  const items = useMemo(
    () =>
      queue?.items.map((item) =>
        item.observation in pending ? { ...item, ruling: pending[item.observation].ruling } : item,
      ),
    [queue, pending],
  );
  const byId = useMemo(() => new Map(items?.map((item) => [item.observation, item])), [items]);

  if (items && (nav === null || items !== navFor)) {
    setNavFor(items);
    setNav(nav ? reconcile(nav, items, browser.skipped) : build(items, browser));
    const newest = items.map((item) => item.written).sort().pop();
    if (newest && (!browser.lastWritten || newest > browser.lastWritten)) {
      setBrowser((b) => ({ ...b, lastWritten: newest }));
    }
  }

  const view: View = !items || !nav
    ? query.isError ? 'error' : 'loading'
    : !nav.order.length ? 'empty'
    : nav.atFinish || nav.idx >= nav.order.length ? 'finish'
    : 'card';
  const current = view === 'card' ? byId.get(nav!.order[nav!.idx]) ?? null : null;
  const ruledCount = items?.filter(isRuled).length ?? 0;

  const draftOf = (item: Item) => draftIn(browser.drafts, item);

  const setDraft = (item: Item, change: Partial<Draft>) =>
    setBrowser((b) => ({
      ...b,
      drafts: { ...b.drafts, [item.observation]: { ...draftIn(b.drafts, item), ...change } },
    }));

  const toast = (text: string, error = false) => {
    const id = ++counter.current;
    setToasts((all) => [...all, { id, text, error }]);
    setTimeout(() => setToasts((all) => all.filter((t) => t.id !== id)), 4000);
  };

  // ---- Where the operator is ---------------------------------------------------------------

  const go = (idx: number) => {
    if (!nav) return;
    if (current) {
      const id = current.observation;
      setBrowser((b) => (b.seen.includes(id) ? b : { ...b, seen: [...b.seen, id] }));
    }
    const next = Math.max(0, Math.min(idx, nav.order.length));
    setNav({ ...nav, idx: next, atFinish: next >= nav.order.length });
    setMessage(null);
    if (mainRef.current) mainRef.current.scrollTop = 0;
  };

  const previous = () => {
    if (nav && nav.idx > 0) go(nav.idx - 1);
  };

  // ---- The store's writes ------------------------------------------------------------------

  const write = (
    item: Item,
    ruling: Ruling | null,
    request: () => Promise<Reply<Item>>,
    restore: Draft | null,
  ) => {
    const id = item.observation;
    const seq = ++counter.current;
    setPending((all) => ({ ...all, [id]: { ruling, seq } }));
    const promise = request()
      .then(async ({ data, error }) => {
        if (error || !data) throw error ?? new Error('an empty reply');
        await queryClient.cancelQueries({ queryKey: QUEUE_KEY });
        queryClient.setQueryData<Queue>(QUEUE_KEY, (q) =>
          q && { ...q, items: q.items.map((other) => (other.observation === id ? data : other)) },
        );
      })
      .catch((error: unknown) => {
        if (restore) setBrowser((b) => ({ ...b, drafts: { ...b.drafts, [id]: restore } }));
        toast(failure(error), true);
        // Gone (stamped or withdrawn) or rewritten since the page loaded: the queue as it stands.
        void queryClient.invalidateQueries({ queryKey: QUEUE_KEY });
      })
      .finally(() => {
        setPending((all) => (all[id]?.seq === seq ? without(all, id) : all));
        inFlight.current.delete(promise);
      });
    inFlight.current.add(promise);
  };

  const takeBack = (item: Item) =>
    write(
      item,
      null,
      () =>
        api.DELETE('/api/triage/items/{id}/ruling', {
          params: { path: { id: item.observation }, query: { written: item.written } },
        }) as Promise<Reply<Item>>,
      null,
    );

  // ---- Actions -----------------------------------------------------------------------------

  // A refusal shows where the fix is: the missing note turns red and says why in its
  // placeholder; a missing verb shakes the verbs.
  const refuse = (text: string | null) => {
    if (text) {
      setMessage(text);
      noteRef.current?.focus();
      return;
    }
    const verbs = verbsRef.current;
    if (!verbs) return;
    verbs.classList.remove('shake');
    void verbs.offsetWidth;
    verbs.classList.add('shake');
  };

  // Pressing the verb the pane shows takes it off again, a saved ruling's verb included.
  const setVerb = (verb: Verb) => {
    if (!current) return;
    const off = draftOf(current).verb === verb;
    setDraft(current, { verb: off ? null : verb });
    setMessage(null);
    if (!off && verb !== 'yes') noteRef.current?.focus();
  };

  const setNote = (note: string) => {
    if (!current) return;
    setDraft(current, { note });
    setMessage(null);
  };

  // The × in the note clears the text only; the verb, and a saved ruling, stay.
  const clearNote = () => {
    if (!current) return;
    setDraft(current, { note: '' });
    noteRef.current?.focus();
  };

  const rule = () => {
    if (!current || !nav) return;
    const { verb, note } = draftOf(current);
    const text = note.trim();
    if (!verb) return refuse(null);
    if (verb === 'no' && !text) return refuse('A no needs a note.');
    if (verb === 'later' && !text) return refuse('A later needs a note.');
    const item = current;
    const id = item.observation;
    write(
      item,
      { verb, note: text, at: storeNow(), submitted: null },
      () =>
        api.PUT('/api/triage/items/{id}/ruling', {
          params: { path: { id } },
          body: { verb, note: text, written: item.written },
        }) as Promise<Reply<Item>>,
      { verb, note },
    );
    setBrowser((b) => ({ ...b, drafts: without(b.drafts, id), skipped: without(b.skipped, id) }));
    noteRef.current?.blur();
    go(nav.idx + 1);
  };

  // The pane holds something other than the ruling in force.
  const edited = (item: Item) => {
    const draft = browser.drafts[item.observation];
    if (!draft) return false;
    if (!isRuled(item)) return true;
    return draft.verb !== item.ruling!.verb || draft.note.trim() !== item.ruling!.note;
  };

  // → is `next` while the pane shows a verb: it saves an edit, as Ctrl+Enter does, and moves on.
  // Without a verb it is `skip`: the card goes to the back of the stack next time. A ruled card
  // whose verb was taken off loses its ruling (DELETE …/ruling) and is skipped.
  const next = () => {
    if (!current || !nav) return;
    const draft = draftOf(current);
    if (draft.verb && edited(current)) return rule();
    if (!draft.verb) {
      if (isRuled(current)) takeBack(current);
      const id = current.observation;
      setBrowser((b) => ({ ...b, skipped: { ...b.skipped, [id]: new Date().toISOString() } }));
    }
    go(nav.idx + 1);
  };

  // Start over, from the finish card with nothing ruled: the first card, with the skips and the
  // visit forgotten, so a reload starts there too.
  const restart = () => {
    setBrowser((b) => ({ ...b, skipped: {}, seen: [] }));
    go(0);
  };

  // A submit waits for the writes in flight, then starts a new round, as Start over does: the
  // first card, the skips forgotten.
  const submit = async () => {
    if (submitting || !ruledCount) return;
    setSubmitting(true);
    try {
      await Promise.allSettled([...inFlight.current]);
      let reply: Reply<string[]>;
      try {
        reply = (await api.POST('/api/triage/submit')) as Reply<string[]>;
      } catch (error) {
        reply = { error, response: new Response() };
      }
      if (reply.error || !reply.data) {
        toast(failure(reply.error), true);
        void queryClient.invalidateQueries({ queryKey: QUEUE_KEY });
        return;
      }
      const ids = new Set(reply.data);
      await queryClient.cancelQueries({ queryKey: QUEUE_KEY });
      const left = queryClient.setQueryData<Queue>(QUEUE_KEY, (q) =>
        q && { ...q, items: q.items.filter((item) => !ids.has(item.observation)) },
      );
      const open = new Set(left?.items.map((item) => item.observation));
      setBrowser((b) => ({
        ...b,
        skipped: {},
        seen: [],
        drafts: Object.fromEntries(Object.entries(b.drafts).filter(([id]) => open.has(id))),
      }));
      setNav(null);
      toast(
        ids.size
          ? `${ids.size} ${plural(ids.size, 'ruling')} submitted.`
          : 'Nothing was submitted: no ruling had reached the store.',
      );
      void queryClient.invalidateQueries({ queryKey: QUEUE_KEY });
    } finally {
      setSubmitting(false);
    }
  };

  const setPref = <K extends keyof Prefs>(key: K, value: Prefs[K]) =>
    setBrowser((b) => ({ ...b, prefs: { ...b.prefs, [key]: value } }));

  return {
    query,
    queue,
    items: items ?? [],
    view,
    current,
    order: nav?.order ?? [],
    idx: nav?.idx ?? 0,
    byId,
    ruledCount,
    submitting,
    draft: current ? draftOf(current) : { verb: null, note: '' },
    message,
    skipped: browser.skipped,
    lastWritten: browser.lastWritten,
    prefs: browser.prefs,
    toasts,
    noteRef,
    verbsRef,
    mainRef,
    go,
    previous,
    next,
    rule,
    setVerb,
    setNote,
    clearNote,
    restart,
    submit,
    setPref,
  };
}
