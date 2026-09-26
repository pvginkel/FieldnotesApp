/** Alert variant class maps — app-owned, customizable per project. */

import type { ComponentType } from 'react';
import { AlertCircle, AlertTriangle, Info, CheckCircle2 } from 'lucide-react';

export const alertVariantClasses = {
  error: 'border-destructive/50 bg-destructive/10 text-destructive',
  warning: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/50 dark:bg-amber-500/10 dark:text-amber-400',
  info: 'border-blue-300 bg-blue-50 text-blue-900',
  success: 'border-green-300 bg-green-50 text-green-900',
} as const;

export const alertDefaultIcons: Record<string, ComponentType<{ className?: string }>> = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
  success: CheckCircle2,
};
