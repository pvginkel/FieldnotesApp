// The session's text as the operator knows it from Claude Code's terminal, as far as /dev:inbox
// writes it: the sorted inbox in a fenced block, kept in monospace with the skill's alignment; around
// it paragraphs, bullet and numbered lists (nested by indentation), bold, code spans and links.
// Every card id is linked to its issue (FR-28). Not a markdown engine: what the skill does not write
// is shown as written.

import { Fragment, type ReactNode } from 'react';
import { hang, isFence, pieces } from '@/lib/inbox/text';

/** A fenced block's lines as written, in monospace: a wrapped line continues under its own text. */
function Fenced({ lines, tracker }: { lines: string[]; tracker: string | null }) {
  return (
    <div className="fenced" data-testid="inbox.fenced">
      {lines.map((line, i) => {
        const indent = hang(line);
        return (
          <div key={i} className="line" style={indent ? { paddingLeft: `${indent}ch`, textIndent: `-${indent}ch` } : undefined}>
            {linked(line, tracker)}
          </div>
        );
      })}
    </div>
  );
}

function linked(text: string, tracker: string | null): ReactNode[] {
  return pieces(text).map((piece, j) =>
    piece.card && tracker ? (
      <a key={j} href={tracker + piece.card} target="_blank" rel="noreferrer">
        {piece.text}
      </a>
    ) : (
      <Fragment key={j}>{piece.text}</Fragment>
    ),
  );
}

// -- inline: code spans are taken out first, so bold and links can hold them; then bold, emphasis,
// links and URLs; card ids in the plain text left.

const CODE = /\uE000(\d+)\uE000/;
const DECOR =
  /\*\*(?<bold>.+?)\*\*|\[(?<linkText>[^\]]+)\]\((?<linkHref>https?:[^)\s]+)\)|(?<![^\s(])(?<url>https?:\/\/[^\s<)\uE000]*[^\s<).,;:'\uE000])|(?<!\w)_(?<em>[^_\n]+)_(?!\w)/g;

function plain(text: string, tracker: string | null, codes: string[]): ReactNode[] {
  return text.split(CODE).flatMap((part, i): ReactNode[] => (i % 2 ? [<code key={`c${i}`}>{codes[Number(part)]}</code>] : linked(part, tracker)));
}

function decorate(text: string, tracker: string | null, codes: string[]): ReactNode[] {
  const nodes: ReactNode[] = [];
  let last = 0;
  for (const match of text.matchAll(DECOR)) {
    if (match.index > last) nodes.push(...plain(text.slice(last, match.index), tracker, codes));
    const g = match.groups ?? {};
    const key = `d${match.index}`;
    if (g.bold !== undefined) nodes.push(<strong key={key}>{decorate(g.bold, tracker, codes)}</strong>);
    else if (g.linkHref !== undefined) nodes.push(<a key={key} href={g.linkHref} target="_blank" rel="noreferrer">{g.linkText}</a>);
    else if (g.url !== undefined) nodes.push(<a key={key} href={g.url} target="_blank" rel="noreferrer">{g.url}</a>);
    else if (g.em !== undefined) nodes.push(<em key={key}>{decorate(g.em, tracker, codes)}</em>);
    last = match.index + match[0].length;
  }
  if (last < text.length) nodes.push(...plain(text.slice(last), tracker, codes));
  return nodes.map((node, i) => <Fragment key={i}>{node}</Fragment>);
}

function inline(text: string, tracker: string | null): ReactNode[] {
  const codes: string[] = [];
  const masked = text.replace(/`([^`]+)`/g, (_, code: string) => `\uE000${codes.push(code) - 1}\uE000`);
  return decorate(masked, tracker, codes);
}

// -- blocks

const MARKER = /^(\s*)([-*]|\d+\.)\s+(.*)$/;
const HEADING = /^#{1,6}\s+(.*)$/;

interface Item {
  text: string;
  ordered: boolean;
  children: Item[];
}

function parseList(block: string[]): Item[] {
  const root: Item[] = [];
  const stack = [{ indent: MARKER.exec(block[0])![1].length, items: root }];
  let last: Item | null = null;
  for (const line of block) {
    const m = MARKER.exec(line);
    if (!m) {
      if (last) last.text += ` ${line.trim()}`;
      continue;
    }
    const indent = m[1].length;
    while (stack.length > 1 && indent < stack[stack.length - 1].indent) stack.pop();
    if (indent > stack[stack.length - 1].indent && last) stack.push({ indent, items: last.children });
    const item: Item = { text: m[3], ordered: /\d/.test(m[2]), children: [] };
    stack[stack.length - 1].items.push(item);
    last = item;
  }
  return root;
}

function list(items: Item[], tracker: string | null, key: string): ReactNode {
  const children = items.map((item, i) => (
    <li key={i}>
      {inline(item.text, tracker)}
      {item.children.length > 0 && list(item.children, tracker, `${key}.${i}`)}
    </li>
  ));
  return items[0]?.ordered ? <ol key={key}>{children}</ol> : <ul key={key}>{children}</ul>;
}

export function Markdown({ text, tracker }: { text: string; tracker: string | null }) {
  const lines = text.replace(/\r/g, '').split('\n');
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const key = `b${i}`;
    const line = lines[i];
    if (isFence(line)) {
      const code: string[] = [];
      for (i++; i < lines.length && !isFence(lines[i]); i++) code.push(lines[i]);
      i++;
      out.push(<Fenced key={key} lines={code} tracker={tracker} />);
    } else if (!line.trim()) {
      i++;
    } else if (MARKER.test(line)) {
      const block: string[] = [];
      for (; i < lines.length && lines[i].trim() && !isFence(lines[i]); i++) block.push(lines[i]);
      out.push(list(parseList(block), tracker, key));
    } else if (HEADING.test(line)) {
      out.push(<p key={key} className="heading">{inline(HEADING.exec(line)![1], tracker)}</p>);
      i++;
    } else {
      const para: string[] = [];
      for (; i < lines.length && lines[i].trim() && !isFence(lines[i]) && !MARKER.test(lines[i]); i++) {
        para.push(lines[i].trim());
      }
      out.push(<p key={key}>{inline(para.join(' '), tracker)}</p>);
    }
  }
  return <div className="md">{out}</div>;
}
