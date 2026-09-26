import * as React from 'react'
import { cn } from '@/lib/utils'
import { ClearButtonIcon } from '@/components/icons/clear-button-icon'
import { inputBaseClasses, inputErrorClasses } from '@/styles/input'

type NativeInputProps = React.ComponentPropsWithoutRef<"input">

interface InputProps extends NativeInputProps {
  icon?: React.ReactNode
  error?: string
  action?: React.ReactNode
  clearable?: boolean
  onClear?: () => void
  invalid?: boolean
}

export const Input = React.forwardRef<HTMLInputElement, InputProps>(
  ({
    icon,
    error,
    action,
    clearable,
    onClear,
    className,
    value,
    invalid,
    ...props
  }, ref) => {
    const hasError = error || invalid
    const hasRightContent = action || (clearable && value)
    const hasBothButtons = action && clearable && value
    const paddingRightClass = hasBothButtons ? 'pr-16' : hasRightContent ? 'pr-10' : ''
    const paddingLeftClass = icon ? 'pl-10' : ''

    const handleClear = () => {
      onClear?.()
    }

    return (
      <div className="relative">
        {icon && (
          <div className="absolute left-3 top-1/2 -translate-y-1/2 text-muted-foreground">
            {icon}
          </div>
        )}

        <input
          ref={ref}
          {...props}
          value={value}
          aria-invalid={hasError ? 'true' : undefined}
          className={cn(
            inputBaseClasses,
            hasError && inputErrorClasses,
            paddingLeftClass,
            paddingRightClass,
            className
          )}
        />

        {hasRightContent && (
          <div className="absolute right-2 top-1/2 -translate-y-1/2 flex items-center gap-1">
            {clearable && value && (
              <button
                type="button"
                onClick={handleClear}
                className="cursor-pointer p-1 text-muted-foreground hover:text-foreground focus:outline-hidden"
                aria-label="Clear"
              >
                <ClearButtonIcon />
              </button>
            )}
            {action}
          </div>
        )}

        {error && (
          <p className="mt-1 text-sm text-destructive">{error}</p>
        )}
      </div>
    )
  }
)

Input.displayName = 'Input'