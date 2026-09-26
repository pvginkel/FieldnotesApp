/** Button variant and size class maps — app-owned, customizable per project. */

export const buttonBaseClasses = {
  default: 'inline-flex items-center justify-center rounded-md font-medium transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 cursor-pointer',
  filter: 'inline-flex items-center justify-center rounded-full font-semibold transition-colors focus-visible:outline-hidden focus-visible:ring-2 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50 cursor-pointer',
} as const;

export const buttonVariants = {
  default: 'bg-primary text-primary-foreground hover:bg-primary/90',
  primary: 'bg-primary text-primary-foreground hover:bg-primary/90',
  secondary: 'bg-secondary text-secondary-foreground hover:bg-secondary/80',
  outline: 'border border-input bg-background hover:bg-accent hover:text-accent-foreground',
  ghost: 'hover:bg-accent hover:text-accent-foreground',
  ai_assisted: 'bg-gradient-to-r from-[#0afecf] to-[#16bbd4] ai-glare',
  filter: 'border border-input bg-background text-foreground hover:opacity-80 aria-pressed:bg-primary aria-pressed:text-primary-foreground aria-pressed:border-primary',
  destructive: 'bg-destructive text-destructive-foreground hover:bg-destructive/90',
} as const;

export const buttonSizes = {
  default: {
    sm: 'h-8 px-2 text-sm',
    md: 'h-10 px-4',
    lg: 'h-11 px-8',
  },
  filter: {
    sm: 'px-2.5 py-0.5 text-xs',
    md: 'px-3 py-1 text-sm',
    lg: 'px-4 py-1.5 text-base',
  },
} as const;

/** Text gradient classes for the ai_assisted variant's children. */
export const buttonAiTextClasses = 'bg-gradient-to-r from-[#1982a4] to-[#bd3cb9] bg-clip-text text-transparent relative z-10';
