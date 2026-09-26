/** Progress bar variant class maps — app-owned, customizable per project. */

export const progressBarSizeClasses = {
  sm: 'h-1',
  md: 'h-2',
  lg: 'h-3',
} as const;

export const progressBarVariantClasses = {
  default: 'bg-primary',
  success: 'bg-green-500',
  warning: 'bg-yellow-500',
  error: 'bg-red-500',
} as const;
