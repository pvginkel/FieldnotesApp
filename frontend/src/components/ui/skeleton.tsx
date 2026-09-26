/**
 * Skeleton loading primitives for consistent loading states across the application.
 *
 * Design Principles:
 * - Optional className prop for additional styling (margin, etc.)
 * - Variants provide shape presets (text, circular, rectangular, avatar)
 * - Width/height props accept Tailwind classes, CSS units, or numbers
 * - testId applied to outermost element without wrapper divs
 *
 * Usage:
 * ```tsx
 * // Simple rectangular skeleton
 * <Skeleton />
 *
 * // Circular avatar skeleton
 * <Skeleton variant="circular" width="w-12" height="h-12" />
 *
 * // Multiple skeleton items
 * {Array.from({ length: 3 }).map((_, i) => (
 *   <Skeleton key={i} />
 * ))}
 * ```
 */

interface SkeletonProps {
  variant?: 'text' | 'circular' | 'rectangular' | 'avatar';
  width?: string | number;
  height?: string | number;
  testId?: string;
  className?: string;
}

/**
 * Maps variant to default dimensions and border radius
 */
const VARIANT_DEFAULTS = {
  text: { width: 'w-full', height: 'h-4', rounded: 'rounded' },
  circular: { width: 'w-8', height: 'h-8', rounded: 'rounded-full' },
  rectangular: { width: 'w-full', height: 'h-4', rounded: 'rounded' },
  avatar: { width: 'w-10', height: 'h-10', rounded: 'rounded-full' },
};

/**
 * Parses width/height prop into Tailwind class or inline style.
 *
 * Rules:
 * - String starting with 'w-' or 'h-': Applied as Tailwind class
 * - String ending with '%' or 'px': Applied as inline style
 * - Number: Applied as inline style in pixels
 * - Other strings: Applied as Tailwind class (fallback for custom utilities)
 * - Negative numbers or invalid values: Return undefined (graceful fallback)
 */
function parseSizeProps(
  prop: string | number | undefined,
  axis: 'width' | 'height'
): { className?: string; style?: Record<string, string> } {
  if (prop === undefined) return {};

  // Handle numbers (convert to pixels, guard against negative)
  if (typeof prop === 'number') {
    if (prop < 0) return {}; // Edge case: negative numbers ignored
    return { style: { [axis]: `${prop}px` } };
  }

  // Handle strings
  const trimmed = prop.trim();

  // Guard against invalid empty strings
  if (trimmed === '') return {};

  // Tailwind utility class patterns (w-*, h-*)
  const prefix = axis === 'width' ? 'w-' : 'h-';
  if (trimmed.startsWith(prefix)) {
    return { className: trimmed };
  }

  // CSS unit patterns (%, px, rem, etc.)
  if (/^\d+(\.\d+)?(px|%|rem|em|vh|vw)$/.test(trimmed)) {
    return { style: { [axis]: trimmed } };
  }

  // Fallback: assume it's a valid Tailwind utility class
  return { className: trimmed };
}

/**
 * Skeleton primitive component.
 *
 * Renders a single skeleton placeholder with encapsulated Tailwind styles.
 * Applies variant-specific defaults for common shapes (text lines, circular avatars, etc.).
 */
export function Skeleton({
  variant = 'rectangular',
  width,
  height,
  testId,
  className,
}: SkeletonProps) {
  const defaults = VARIANT_DEFAULTS[variant];

  // Parse width and height props
  const widthParsed = parseSizeProps(width, 'width');
  const heightParsed = parseSizeProps(height, 'height');

  // Build className: base + rounded + dimensions + custom
  const classNames = [
    'bg-muted',
    'animate-pulse',
    defaults.rounded,
    widthParsed.className || (width === undefined ? defaults.width : undefined),
    heightParsed.className || (height === undefined ? defaults.height : undefined),
    className,
  ]
    .filter(Boolean)
    .join(' ');

  // Merge inline styles
  const inlineStyles = {
    ...widthParsed.style,
    ...heightParsed.style,
  };

  return (
    <div
      className={classNames}
      style={Object.keys(inlineStyles).length > 0 ? inlineStyles : undefined}
      data-testid={testId}
    />
  );
}
