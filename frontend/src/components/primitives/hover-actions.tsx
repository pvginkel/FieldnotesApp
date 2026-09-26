import { type ReactNode } from 'react';

interface HoverActionsProps {
  children: ReactNode;
  actions: ReactNode;
  className?: string;
}

export function HoverActions({ children, actions, className = '' }: HoverActionsProps) {
  return (
    <div className={`relative group ${className}`}>
      {children}
      <div className="absolute inset-0 bg-black/60 opacity-0 group-hover:opacity-100 transition-opacity duration-200 rounded-lg flex items-center justify-center">
        <div className="flex space-x-2">
          {actions}
        </div>
      </div>
    </div>
  );
}

interface IconButtonProps {
  onClick: () => void;
  icon: ReactNode;
  tooltip?: string;
  variant?: 'default' | 'destructive';
  size?: 'sm' | 'md';
  testId?: string;
}

export function IconButton({
  onClick,
  icon,
  tooltip,
  variant = 'default',
  size = 'sm',
  testId,
}: IconButtonProps) {
  const sizeClasses = {
    sm: 'w-8 h-8',
    md: 'w-10 h-10'
  };

  const variantClasses = {
    default: 'bg-background/90 hover:bg-background text-foreground',
    destructive: 'bg-red-500/90 hover:bg-red-500 text-white'
  };

  return (
    <button
      onClick={(e) => {
        e.stopPropagation();
        e.preventDefault();
        onClick();
      }}
      className={`
        ${sizeClasses[size]}
        ${variantClasses[variant]}
        rounded-full flex items-center justify-center
        transition-colors backdrop-blur-sm cursor-pointer
        ${tooltip ? 'relative' : ''}
      `}
      title={tooltip}
      data-testid={testId}
    >
      {icon}
    </button>
  );
}