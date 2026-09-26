import { createContext } from 'react';
import type { ToastOptions, ToastType } from '@/components/primitives/toast';

export interface ToastContextValue {
  showToast: (message: string, type: ToastType, options?: ToastOptions) => string;
  showError: (message: string, options?: ToastOptions) => string;
  showSuccess: (message: string, options?: ToastOptions) => string;
  showWarning: (message: string, options?: ToastOptions) => string;
  showInfo: (message: string, options?: ToastOptions) => string;
  showException: (message: string, error: unknown, options?: ToastOptions) => string;
  removeToast: (id: string) => void;
}

export const ToastContext = createContext<ToastContextValue | undefined>(undefined);
