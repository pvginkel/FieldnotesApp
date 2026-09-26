import * as React from 'react'
import { cn } from '@/lib/utils'
import { cardBaseClasses, cardVariantClasses, cardGridTileInteractiveClasses } from '@/styles/card'

type NativeDivProps = React.ComponentPropsWithoutRef<"div">

interface CardProps extends NativeDivProps {
  variant?: 'default' | 'stats' | 'action' | 'content' | 'grid-tile' | 'grid-tile-disabled' | 'slim';
}

export const Card = React.forwardRef<HTMLDivElement, CardProps>(
  ({ variant = 'default', className, onClick, onKeyDown, children, ...props }, ref) => {
    // Build grid-tile classes conditionally based on onClick presence
    const resolvedVariantClasses = variant === 'grid-tile' && onClick
      ? cn(cardVariantClasses['grid-tile'], cardGridTileInteractiveClasses)
      : cardVariantClasses[variant]

    const handleClick: React.MouseEventHandler<HTMLDivElement> = (e) => {
      onClick?.(e)
    }

    const handleKeyDown: React.KeyboardEventHandler<HTMLDivElement> = (e) => {
      // If there's a custom onKeyDown handler, call it first
      onKeyDown?.(e)

      // If onClick is provided and the key is Enter or Space, trigger the click
      if (onClick && (e.key === 'Enter' || e.key === ' ')) {
        e.preventDefault()
        onClick(e as unknown as React.MouseEvent<HTMLDivElement>)
      }
    }

    return (
      <div
        ref={ref}
        {...props}
        className={cn(cardBaseClasses, resolvedVariantClasses, className)}
        onClick={handleClick}
        onKeyDown={onClick ? handleKeyDown : onKeyDown}
      >
        {children}
      </div>
    )
  }
)

Card.displayName = 'Card'

type CardHeaderProps = NativeDivProps

export const CardHeader = React.forwardRef<HTMLDivElement, CardHeaderProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn('flex flex-col space-y-1.5 pb-4', className)}
      >
        {children}
      </div>
    )
  }
)

CardHeader.displayName = 'CardHeader'

type NativeH3Props = React.ComponentPropsWithoutRef<"h3">

type CardTitleProps = NativeH3Props

export const CardTitle = React.forwardRef<HTMLHeadingElement, CardTitleProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <h3
        ref={ref}
        {...props}
        className={cn('text-lg font-semibold leading-none tracking-tight', className)}
      >
        {children}
      </h3>
    )
  }
)

CardTitle.displayName = 'CardTitle'

type CardContentProps = NativeDivProps

export const CardContent = React.forwardRef<HTMLDivElement, CardContentProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn('', className)}
      >
        {children}
      </div>
    )
  }
)

CardContent.displayName = 'CardContent'

type CardFooterProps = NativeDivProps

export const CardFooter = React.forwardRef<HTMLDivElement, CardFooterProps>(
  ({ className, children, ...props }, ref) => {
    return (
      <div
        ref={ref}
        {...props}
        className={cn('flex items-center pt-4', className)}
      >
        {children}
      </div>
    )
  }
)

CardFooter.displayName = 'CardFooter'

type NativePProps = React.ComponentPropsWithoutRef<"p">

type CardDescriptionProps = NativePProps

export const CardDescription = React.forwardRef<HTMLParagraphElement, CardDescriptionProps>(
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

CardDescription.displayName = 'CardDescription'