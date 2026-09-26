/** Toast tone class maps — app-owned, customizable per project. */

export const toastToneStyles = {
  success: {
    container: 'bg-green-50 border-green-200 text-green-800',
    action: 'bg-green-800 text-green-50 hover:bg-green-700 focus:ring-green-900',
  },
  error: {
    container: 'bg-red-50 border-red-200 text-red-800',
    action: 'bg-red-800 text-red-50 hover:bg-red-700 focus:ring-red-900',
  },
  warning: {
    container: 'bg-yellow-50 border-yellow-200 text-yellow-800',
    action: 'bg-yellow-800 text-yellow-50 hover:bg-yellow-700 focus:ring-yellow-900',
  },
  info: {
    container: 'bg-blue-50 border-blue-200 text-blue-800',
    action: 'bg-blue-800 text-blue-50 hover:bg-blue-700 focus:ring-blue-900',
  },
} as const;

export const toastIconMap = {
  success: '\u2713',
  error: '\u2715',
  warning: '\u26A0',
  info: '\u2139',
} as const;
