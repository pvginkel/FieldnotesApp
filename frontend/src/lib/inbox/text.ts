// The session's text as the inbox screen reads it (FR-28): the card ids a line names, where a
// wrapped line continues, a code fence, the time of day a message carries, and an idle time.

/** A card id: a project key, one letter or more, and a number. Requirement numbers and the like
 * are not cards. */
const CARD = /\b([A-Z][A-Z0-9]{0,9})-(\d+)\b/g;
const NOT_CARDS = new Set(['FR', 'NFR', 'UTF', 'SHA', 'ISO', 'RFC', 'HTTP', 'TLS', 'CVE']);

type Piece = { text: string; card?: string };

export function pieces(line: string): Piece[] {
  const out: Piece[] = [];
  let at = 0;
  for (const match of line.matchAll(CARD)) {
    if (NOT_CARDS.has(match[1])) continue;
    const index = match.index;
    if (index > at) out.push({ text: line.slice(at, index) });
    out.push({ text: match[0], card: match[0] });
    at = index + match[0].length;
  }
  if (at < line.length) out.push({ text: line.slice(at) });
  return out;
}

/** Where a wrapped line continues: under a card line's text (`  Slice:   FN-44  The ask…`), else
 * under its own indentation. */
export function hang(line: string): number {
  const card = /^\s*(?:[A-Za-z][A-Za-z ]*:\s+)?[A-Z][A-Z0-9]*-\d+\s+/.exec(line);
  if (card) return card[0].length;
  return /^\s*/.exec(line)![0].length;
}

export const isFence = (line: string): boolean => /^\s*```/.test(line);

const pad = (n: number) => String(n).padStart(2, '0');

/** The local time of day: `16:42`. */
export const clock = (iso: string): string => {
  const d = new Date(iso);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
};

/** An idle time given in minutes, to a tenth: `30 minutes`. */
export const minutes = (count: number): string => {
  const shown = Math.round(count * 10) / 10;
  return `${shown} minute${shown === 1 ? '' : 's'}`;
};
