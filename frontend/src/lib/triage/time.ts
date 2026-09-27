// The triage screen's times: relative on the page, exact in the tooltip (the mockup's).

const pad = (n: number) => String(n).padStart(2, '0');

/** The exact local time, for a tooltip: `2026-09-27 14:05`. */
export const stamp = (iso: string): string => {
  const d = new Date(iso);
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
};
const clock = (iso: string) => stamp(iso).slice(11);

/** Now, in the store's one spelling of a timestamp: UTC, whole seconds, `Z`. */
export const storeNow = (): string => new Date().toISOString().replace(/\.\d+Z$/, 'Z');

// Calendar days between then and today: 0 is today, 1 yesterday.
const daysAgo = (iso: string) => {
  const midnight = (d: Date) => new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
  return Math.round((midnight(new Date()) - midnight(new Date(iso))) / 86400000);
};
const some = (n: number, unit: string) => (n === 1 ? `${unit === 'hour' ? 'an' : 'a'} ${unit}` : `${n} ${unit}s`);

/** How long ago, coarse: hours today, then yesterday, days, weeks, months. */
export const ago = (iso: string): string => {
  const days = daysAgo(iso);
  if (days <= 0) {
    const minutes = Math.floor((new Date().getTime() - new Date(iso).getTime()) / 60000);
    if (minutes < 1) return 'just now';
    if (minutes < 60) return `${some(minutes, 'minute')} ago`;
    return `${some(Math.floor(minutes / 60), 'hour')} ago`;
  }
  if (days === 1) return 'yesterday';
  if (days < 7) return `${days} days ago`;
  if (days < 30) return `${some(Math.floor(days / 7), 'week')} ago`;
  if (days < 365) return `${some(Math.floor(days / 30), 'month')} ago`;
  return `${some(Math.floor(days / 365), 'year')} ago`;
};

/** Finer, for the item itself: the time today, yesterday with its time, then as `ago`. */
export const when = (iso: string): string => {
  const days = daysAgo(iso);
  if (days <= 0) return clock(iso);
  if (days === 1) return `yesterday ${clock(iso)}`;
  return ago(iso);
};
