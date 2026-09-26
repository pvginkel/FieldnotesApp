interface ListScreenCountsProps {
  visible: number;
  total: number;
  /**
   * Optional category descriptor appended before the noun (e.g., "active").
   */
  category?: string;
  /**
   * Base noun for the entities being summarized. Defaults to "item(s)".
   */
  noun?: {
    singular: string;
    plural?: string;
  };
  /**
   * Number of records remaining after filters. Omit to hide the badge.
   */
  filtered?: number;
}

function formatCount(count: number): string {
  return count.toLocaleString();
}

function resolveNoun(total: number, noun?: { singular: string; plural?: string }): string {
  const base = noun ?? { singular: 'item', plural: 'items' };
  if (total === 1) {
    return base.singular;
  }

  return base.plural ?? `${base.singular}s`;
}

export function ListScreenCounts({
  visible,
  total,
  category,
  noun,
  filtered,
}: ListScreenCountsProps) {
  const nounText = resolveNoun(total, noun);
  const categoryPrefix = category ? `${category.toLowerCase()} ` : '';
  const showFilteredSummary = typeof filtered === 'number' && filtered >= 0 && filtered < total;
  const filteredCount = showFilteredSummary ? filtered : undefined;
  const summary = showFilteredSummary
    ? `${formatCount(visible)} of ${formatCount(total)} ${categoryPrefix}${nounText} showing`
    : `${formatCount(total)} ${categoryPrefix}${nounText}`;
  const hasFilteredBadge = typeof filteredCount === 'number';

  return (
    <div className="flex flex-col gap-2 text-sm text-muted-foreground sm:flex-row sm:items-center sm:justify-between">
      <span aria-live="polite">{summary}</span>
      {hasFilteredBadge && (
        <span className="inline-flex items-center gap-2 text-xs font-medium uppercase tracking-wide text-muted-foreground/80" data-testid="list-screen.counts.filtered">
          {formatCount(filteredCount)} filtered
        </span>
      )}
    </div>
  );
}
