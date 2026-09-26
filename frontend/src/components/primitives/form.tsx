import * as React from 'react'
import { cn } from '@/lib/utils'

type NativeFormProps = React.ComponentPropsWithoutRef<"form">

type FormProps = NativeFormProps

export const Form = React.forwardRef<HTMLFormElement, FormProps>(
  ({ className, children, onSubmit, ...props }, ref) => {
    // Wrap onSubmit to prevent event propagation to parent forms.
    // This is critical for nested dialogs (e.g., SellerCreateDialog opened from PartForm)
    // to prevent the dialog's form submission from triggering the parent form's submission.
    const handleSubmit: typeof onSubmit = onSubmit
      ? (e) => {
          e.stopPropagation()
          onSubmit(e)
        }
      : undefined

    return (
      <form
        ref={ref}
        {...props}
        onSubmit={handleSubmit}
        className={cn('', className)}
      >
        {children}
      </form>
    )
  }
)

Form.displayName = 'Form'

type NativeDivProps = React.ComponentPropsWithoutRef<"div">

type FormFieldProps = NativeDivProps

export const FormField = React.forwardRef<HTMLDivElement, FormFieldProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn('space-y-2', className)}
      >
        {children}
      </div>
    )
  }
)

FormField.displayName = 'FormField'

type NativeLabelProps = React.ComponentPropsWithoutRef<"label">

interface FormLabelProps extends NativeLabelProps {
  required?: boolean
}

export const FormLabel = React.forwardRef<HTMLLabelElement, FormLabelProps>(
  ({ required, className, children, ...props }, ref) => {
    return (
      <label
        ref={ref}
        {...props}
        className={cn(
          'text-sm font-medium leading-none peer-disabled:cursor-not-allowed peer-disabled:opacity-70',
          className
        )}
      >
        {children}
        {required && <span className="text-destructive ml-1">*</span>}
      </label>
    )
  }
)

FormLabel.displayName = 'FormLabel'

type NativePProps = React.ComponentPropsWithoutRef<"p">

interface FormErrorProps extends NativePProps {
  message?: string
}

export const FormError = React.forwardRef<HTMLParagraphElement, FormErrorProps>(
  ({ message, className, children, ...props }, ref) => {
    if (!message && !children) return null

    return (
      <p
        ref={ref}
        {...props}
        className={cn('text-sm text-destructive', className)}
      >
        {message || children}
      </p>
    )
  }
)

FormError.displayName = 'FormError'

type FormDescriptionProps = NativePProps

export const FormDescription = React.forwardRef<HTMLParagraphElement, FormDescriptionProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <p
        ref={ref}
        {...props}
        className={cn('text-sm text-muted-foreground', className)}
      >
        {children}
      </p>
    )
  }
)

FormDescription.displayName = 'FormDescription'

type FormControlProps = NativeDivProps

export const FormControl = React.forwardRef<HTMLDivElement, FormControlProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={className}
      >
        {children}
      </div>
    )
  }
)

FormControl.displayName = 'FormControl'

type FormMessageProps = NativePProps

export const FormMessage = React.forwardRef<HTMLParagraphElement, FormMessageProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <p
        ref={ref}
        {...props}
        aria-live="polite"
        className={cn('text-sm text-destructive', className)}
      >
        {children}
      </p>
    )
  }
)

FormMessage.displayName = 'FormMessage'