// Just enough markdown for the reconciler's prose, the mockup's own renderer: paragraphs, lists,
// quotes, fences, code spans, bold and links, with issue keys linked to YouTrack, repository
// names in mono and the store's emoji as Lucide icons.

import { Fragment, useContext, type ReactNode } from 'react';
import { PencilLine, ThumbsUp } from 'lucide-react';
import { ProsePatternContext } from '@/lib/triage/prose-pattern';
import { YOUTRACK } from '@/lib/triage/queue';
import { stamp, ago } from '@/lib/triage/time';

const EMOJI_ICONS: Record<string, typeof ThumbsUp> = { '👍': ThumbsUp, '📝': PencilLine };

/** The emoji the store writes that have an icon; any other emoji is shown as it is. */
export function Emoji({ emoji }: { emoji: string }) {
  const Icon = EMOJI_ICONS[emoji];
  return Icon ? <Icon className="icon" aria-hidden="true" /> : <>{emoji}</>;
}

/** A repository name, in full and in mono, wherever the UI shows one. */
export function Repo({ name }: { name: string }) {
  return <span className="mono repo">{name}</span>;
}

/** The page shows the relative time; the exact one is in the tooltip. */
export function Time({ iso, format = ago, label = '' }: { iso: string; format?: (iso: string) => string; label?: string }) {
  return <time dateTime={iso} title={`${label}${stamp(iso)}`}>{format(iso)}</time>;
}

const external = (href: string, text: ReactNode, key: number) => (
  <a key={key} href={href} target="_blank" rel="noreferrer">{text}</a>
);

function decorate(text: string, pattern: RegExp): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(new RegExp(pattern))) {
    const at = match.index;
    if (at > last) nodes.push(text.slice(last, at));
    const g = match.groups ?? {};
    const key = nodes.length;
    if (g.bold !== undefined) nodes.push(<strong key={key}>{decorate(g.bold, pattern)}</strong>);
    else if (g.linkHref !== undefined) nodes.push(external(g.linkHref, g.linkText, key));
    else if (g.url !== undefined) nodes.push(external(g.url, g.url, key));
    else if (g.issue !== undefined) nodes.push(external(`${YOUTRACK}${g.issue}`, g.issue, key));
    else if (g.repo !== undefined) nodes.push(<Repo key={key} name={g.repo} />);
    else if (g.emoji !== undefined) nodes.push(<Emoji key={key} emoji={g.emoji} />);
    last = at + match[0].length;
  }
  if (last < text.length) nodes.push(text.slice(last));
  return nodes;
}

function inline(text: string, pattern: RegExp): ReactNode[] {
  const nodes: ReactNode[] = [];
  const plain = (from: number, to?: number) => (
    <Fragment key={`t${from}`}>{decorate(text.slice(from, to), pattern)}</Fragment>
  );
  let last = 0;
  for (const match of text.matchAll(/`([^`]+)`/g)) {
    nodes.push(plain(last, match.index), <code key={`c${match.index}`}>{match[1]}</code>);
    last = match.index + match[0].length;
  }
  nodes.push(plain(last));
  return nodes;
}

const BULLET = /^\s*[-*] /;
const NUMBER = /^\s*\d+\. /;
const FENCE = /^```/;

// A list's items: its marker lines, with the lines under each joined on.
const listItems = (block: string[], marker: RegExp) => {
  const items: string[] = [];
  for (const line of block) {
    if (marker.test(line)) items.push(line.replace(marker, ''));
    else items[items.length - 1] += ` ${line.trim()}`;
  }
  return items;
};

function blocks(source: string, pattern: RegExp): ReactNode[] {
  const lines = source.replace(/\r/g, '').split('\n');
  const html: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const key = html.length;
    if (FENCE.test(lines[i])) {
      const code = [];
      for (i++; i < lines.length && !FENCE.test(lines[i]); i++) code.push(lines[i]);
      i++;
      html.push(<pre key={key}><code>{code.join('\n')}</code></pre>);
      continue;
    }
    if (!lines[i].trim()) {
      i++;
      continue;
    }
    const block: string[] = [];
    for (; i < lines.length && lines[i].trim() && !FENCE.test(lines[i]); i++) block.push(lines[i]);
    const list = (items: string[]) => items.map((item, n) => <li key={n}>{inline(item, pattern)}</li>);
    if (BULLET.test(block[0])) {
      html.push(<ul key={key}>{list(listItems(block, BULLET))}</ul>);
    } else if (NUMBER.test(block[0])) {
      html.push(<ol key={key}>{list(listItems(block, NUMBER))}</ol>);
    } else if (block.every((line) => line.startsWith('>'))) {
      const quoted = block.map((line) => line.replace(/^> ?/, '')).join('\n');
      html.push(<blockquote key={key}>{blocks(quoted, pattern)}</blockquote>);
    } else {
      html.push(<p key={key}>{inline(block.join(' '), pattern)}</p>);
    }
  }
  return html;
}

export function Prose({ source, className }: { source: string | null | undefined; className?: string }) {
  const pattern = useContext(ProsePatternContext);
  return <div className={className ? `prose ${className}` : 'prose'}>{blocks(source ?? '', pattern)}</div>;
}

export function Inline({ text }: { text: string }) {
  const pattern = useContext(ProsePatternContext);
  return <>{inline(text, pattern)}</>;
}
