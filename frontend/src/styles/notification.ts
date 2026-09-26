/** Inline notification variant class maps — app-owned, customizable per project. */

import type { ComponentType } from 'react';
import { AlertCircle, AlertTriangle, Info, CheckCircle2 } from 'lucide-react';

export const notificationVariantClasses = {
  error: 'border-destructive/50 bg-destructive/10 text-destructive',
  warning: 'border-amber-400 bg-amber-50 text-amber-900',
  info: 'border-blue-400 bg-blue-50 text-blue-900',
  success: 'border-green-400 bg-green-50 text-green-900',
} as const;

export const notificationDefaultIcons: Record<string, ComponentType<{ className?: string }>> = {
  error: AlertCircle,
  warning: AlertTriangle,
  info: Info,
  success: CheckCircle2,
};
