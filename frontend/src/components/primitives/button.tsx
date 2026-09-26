import * as React from 'react'
import { Slot } from '@radix-ui/react-slot'
import { cn } from '@/lib/utils'
import { buttonBaseClasses, buttonVariants, buttonSizes, buttonAiTextClasses } from '@/styles/button'

type NativeButtonProps = React.ComponentPropsWithoutRef<"button">

export interface ButtonProps extends NativeButtonProps {
  variant?: 'default' | 'primary' | 'secondary' | 'outline' | 'ghost' | 'ai_assisted' | 'filter' | 'destructive'
  size?: 'sm' | 'md' | 'lg'
  loading?: boolean
  icon?: React.ReactNode
  preventValidation?: boolean
  asChild?: boolean
}

export const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({
    variant = 'default',
    size = 'md',
    loading = false,
    icon,
    className,
    onClick,
    onMouseUp,
    disabled,
    type = 'button',
    preventValidation = false,
    asChild = false,
    children,
    ...props
  }, ref) => {
    const Comp = asChild ? Slot : 'button'

    const baseClasses = variant === 'filter'
      ? buttonBaseClasses.filter
      : buttonBaseClasses.default

    const sizeClasses = variant === 'filter' ? buttonSizes.filter : buttonSizes.default

    if (preventValidation && !onClick) {
      throw new Error('onClick must be provided when preventValidation is set')
    }

    const handleMouseUp: React.MouseEventHandler<HTMLButtonElement> = (e) => {
      if (preventValidation && onClick && e.button === 0) {
        e.preventDefault()
        e.stopPropagation()
        onClick(e)
      }
      onMouseUp?.(e)
    }

    const handleClick: React.MouseEventHandler<HTMLButtonElement> = (e) => {
      if (preventValidation) {
        return
      }
      onClick?.(e)
    }

    const prefix = loading ? (
      <div className="mr-2 h-4 w-4 animate-spin rounded-full border-2 border-current border-t-transparent" />
    ) : icon ? (
      <span className="mr-2">{icon}</span>
    ) : null

    return (
      <Comp
        ref={ref}
        {...props}
        type={type}
        onClick={handleClick}
        onMouseUp={handleMouseUp}
        disabled={disabled || loading}
        className={cn(
          baseClasses,
          buttonVariants[variant],
          sizeClasses[size],
          className
        )}
      >
        {!asChild ? prefix : null}
        {variant === 'ai_assisted' ? (
          <span className={buttonAiTextClasses}>
            {children}
          </span>
        ) : (
          children
        )}
      </Comp>
    )
  }
)

Button.displayName = 'Button'
