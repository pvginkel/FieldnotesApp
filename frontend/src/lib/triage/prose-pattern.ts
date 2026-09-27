// What the triage prose turns into links, code and icons, as one pattern: bold, a markdown link,
// a bare URL, an issue key, a repository name and an emoji the store writes. The earliest match
// wins, so a key inside a URL stays part of the URL.

import { createContext } from 'react';

const ISSUE = String.raw`\b(?:KC|AIWF|ANS|FN|GBMCP|DI|MAT|EI)-\d+\b`;

const EMOJI = ['👍', '📝'];

const escape = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

export function prosePattern(owners: string[]): RegExp {
  const parts = [
    String.raw`\*\*(?<bold>[^*]+)\*\*`,
    String.raw`\[(?<linkText>[^\]]+)\]\((?<linkHref>https?:[^)\s]+)\)`,
    String.raw`(?<![^\s(])(?<url>https?:\/\/[^\s<)]*[^\s<).,;:'])`,
    `(?<issue>${ISSUE})`,
    // `owner/name` for an owner the queue's repositories have, standing alone: not inside a URL
    // or a longer path, and without a sentence's full stop.
    owners.length
      ? String.raw`(?<![\w/.-])(?<repo>(?:${owners.map(escape).join('|')})\/[\w.-]*[\w-])(?![\w/-])`
      : null,
    `(?<emoji>${EMOJI.join('|')})`,
  ];
  return new RegExp(parts.filter(Boolean).join('|'), 'gu');
}

export const ProsePatternContext = createContext<RegExp>(prosePattern([]));
