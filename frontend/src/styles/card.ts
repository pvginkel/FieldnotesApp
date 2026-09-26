/** Card variant class maps — app-owned, customizable per project. */

export const cardBaseClasses = 'rounded-lg border bg-card text-card-foreground shadow-sm';

export const cardVariantClasses = {
  default: 'p-6',
  stats: 'p-6 text-center',
  action: 'p-4 hover:bg-accent/50 cursor-pointer transition-colors',
  content: 'p-4',
  'grid-tile': 'p-4 overflow-hidden',
  'grid-tile-disabled': 'p-4 overflow-hidden pointer-events-none',
  slim: 'border-0 p-2',
} as const;

/** Interactive classes appended to grid-tile variant when onClick is present. */
export const cardGridTileInteractiveClasses = 'transition-all duration-200 hover:shadow-md hover:scale-[1.02] hover:border-primary/50 active:scale-[0.98] cursor-pointer';
