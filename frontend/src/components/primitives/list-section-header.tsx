export interface ListSectionHeaderProps {
  /**
   * Section title. Can be a string or custom ReactNode.
   * - String: Renders as <h3> with standardized styling (text-base font-semibold)
   */
  title: string;
  /**
   * Optional description text below the title.
   * - String: Renders as muted text (text-xs text-muted-foreground)
   * - ReactNode: Caller provides custom structure (e.g., links, formatted content)
   */
  description?: string | React.ReactNode;
  /**
   * Optional slot for information in the middle.
   * Should contain only informational displays.
   */
  information?: React.ReactNode;
  /**
   * Optional slot for interactive buttons/controls on the right side.
   * Should contain only actionable elements (buttons, dropdowns), not informational displays.
   */
  actions?: React.ReactNode;
  /**
   * Optional full-width content displayed below the title/actions row.
   * Useful for filter notes, metadata, or other contextual information.
   */
  footer?: React.ReactNode;
  /** Optional test ID for Playwright selectors */
  testId?: string;
  /** When true, removes the bottom border. Useful when the parent controls border visibility. */
  noBorder?: boolean;
}

/**
 * Reusable section header component for lists and tables.
 *
 * Provides consistent styling for section headers with title, optional description,
 * optional action buttons, and optional footer content. Supports both simple string
 * titles and complex ReactNode titles for advanced use cases.
 *
 * @example
 * // Simple string usage
 * <ListSectionHeader
 *   title="Concept lines"
 *   description="Lines that need seller assignment"
 *   actions={<Button>Add Line</Button>}
 * />
 *
 * @example
 * // ReactNode usage with custom heading
 * <ListSectionHeader
 *   title={<h3 className="text-lg font-semibold">Custom Heading</h3>}
 *   description={<ExternalLink href="...">Website</ExternalLink>}
 *   footer={<div>Filter note text</div>}
 * />
 */
export function ListSectionHeader({ title, description, information, actions, footer, testId, noBorder }: ListSectionHeaderProps) {
  return (
    <div
      className={`flex flex-wrap items-center justify-between gap-3 px-4 py-3${noBorder ? '' : ' border-b'}`}
      data-testid={testId}
    >
      <div>
        <h3 className="text-base font-semibold text-foreground">{title}</h3>
        {description && (
          typeof description === 'string' ? (
            <p className="text-xs text-muted-foreground">{description}</p>
          ) : (
            description
          )
        )}
      </div>
      <div className="flex gap-7">
        {information && <div className="flex gap-3">{information}</div>}
        {actions && <div className="flex gap-2">{actions}</div>}
      </div>
      {footer && <div className="w-full">{footer}</div>}
    </div>
  );
}
