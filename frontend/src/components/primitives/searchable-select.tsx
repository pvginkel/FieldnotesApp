import * as React from 'react';
import { useState, useRef, useEffect, useLayoutEffect, type ReactNode } from 'react';
import * as Popover from '@radix-ui/react-popover';
import { Input } from './input';
import { cn } from '@/lib/utils';

interface SearchableSelectOption<IdType extends string | number = number> {
  id: IdType;
  name: string;
  [key: string]: unknown; // Allow additional properties
}

type NativeInputProps = React.ComponentPropsWithoutRef<"input">;

/**
 * SearchableSelect - A fully accessible searchable select component
 *
 * Features:
 * - Full keyboard navigation and screen reader support
 * - Native HTML input props support (data-testid, etc.)
 * - Ref forwarding to the input element
 * - Generic TypeScript support for custom option types
 * - Inline creation of new options
 * - Custom option rendering
 *
 * @example
 * ```tsx
 * <SearchableSelect
 *   value={selectedId}
 *   onChange={setSelectedId}
 *   options={items}
 *   isLoading={isLoading}
 *   searchTerm={search}
 *   onSearchChange={setSearch}
 *   data-testid="my-select"
 * />
 * ```
 */
interface SearchableSelectProps<
  IdType extends string | number = number,
  Option extends SearchableSelectOption<IdType> = SearchableSelectOption<IdType>
> extends Omit<NativeInputProps, "value" | "onChange" | "role" | "aria-expanded" | "aria-haspopup" | "aria-autocomplete"> {
  /** The currently selected option's ID */
  value?: IdType;
  /** Callback when selection changes */
  onChange: (value: IdType | undefined) => void;

  // Data fetching
  /** Array of options to display */
  options: Option[];
  /** Whether options are being loaded */
  isLoading: boolean;
  /** Current search term value */
  searchTerm: string;
  /** Callback when search term changes */
  onSearchChange: (term: string) => void;

  // Custom rendering
  /** Custom function to render each option */
  renderOption?: (option: Option) => ReactNode;

  // Inline creation
  /** Enable creating new options inline */
  enableInlineCreate?: boolean;
  /** Callback when creating a new option */
  onCreateNew?: (searchTerm: string) => void;
  /** Custom label for create option */
  createNewLabel?: (searchTerm: string) => string;

  // Loading states
  /** Text to show while loading */
  loadingText?: string;
  /** Text to show when no results found */
  noResultsText?: string;

  // Error state
  /** Error message to display (backward compatibility) */
  error?: string;
  /** Capture wheel events on the popover content */
  onPopoverWheelCapture?: (event: React.WheelEvent<HTMLDivElement>) => void;
}

function SearchableSelectComponent<
  IdType extends string | number = number,
  Option extends SearchableSelectOption<IdType> = SearchableSelectOption<IdType>
>({
  value,
  onChange,
  placeholder = "Search or select...",
  className,
  options = [] as Option[],
  isLoading,
  searchTerm,
  onSearchChange,
  renderOption,
  enableInlineCreate = false,
  onCreateNew,
  createNewLabel = (term) => `Create "${term}"`,
  loadingText = "Searching...",
  noResultsText = "No results found",
  error,
  onFocus,
  onPopoverWheelCapture,
  ...props
}: SearchableSelectProps<IdType, Option>, ref: React.Ref<HTMLInputElement>) {
  const [open, setOpen] = useState(false);
  const [isUserEditing, setIsUserEditing] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const contentRef = useRef<HTMLDivElement | null>(null);

  // Merge refs
  React.useImperativeHandle(ref, () => inputRef.current!);

  // Find the selected option
  const hasSelectedValue = value !== undefined && value !== null;
  // Guard allows falsy identifiers like 0 to be treated as valid selections.
  const selectedOption = hasSelectedValue ? options.find(option => option.id === value) : null;

  // Handle input focus
  const handleInputFocus: React.FocusEventHandler<HTMLInputElement> = (e) => {
    setIsUserEditing(true);
    // Use a small delay to avoid conflicts with Radix UI's internal handlers
    setTimeout(() => setOpen(true), 0);
    onFocus?.(e);
  };

  // Handle input change
  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newSearchTerm = e.target.value;
    onSearchChange(newSearchTerm);
    setIsUserEditing(true);

    // Open the popover when typing
    if (!open) {
      setOpen(true);
    }

    // If the input is cleared, clear the selection
    if (!newSearchTerm) {
      onChange(undefined);
    }
  };

  // Handle option selection
  const handleSelectOption = (option: Option) => {
    onChange(option.id);
    onSearchChange(option.name);
    setIsUserEditing(false);
    setOpen(false);

    // Keep focus on input after selection
    setTimeout(() => inputRef.current?.focus(), 0);
  };

  // Handle create new
  const handleCreateNew = () => {
    if (onCreateNew && searchTerm.trim()) {
      onCreateNew(searchTerm.trim());
      setOpen(false);
    }
  };

  // Handle dropdown toggle
  const handleDropdownToggle = (e: React.MouseEvent) => {
    e.preventDefault();
    e.stopPropagation();

    if (open) {
      setOpen(false);
    } else {
      inputRef.current?.focus();
      setOpen(true);
    }
  };

  // Update search term when selection changes from outside
  useLayoutEffect(() => {
    if (selectedOption && !isUserEditing && searchTerm !== selectedOption.name) {
      onSearchChange(selectedOption.name);
    } else if (!selectedOption && !isUserEditing && searchTerm) {
      onSearchChange('');
    }
  }, [value, selectedOption, isUserEditing, searchTerm, onSearchChange]);

  // Handle popover open state changes
  const handleOpenChange = (newOpen: boolean) => {
    setOpen(newOpen);

    if (!newOpen) {
      setIsUserEditing(false);
      // Restore original value if user didn't select anything
      setTimeout(() => {
        if (selectedOption && searchTerm !== selectedOption.name) {
          onSearchChange(selectedOption.name);
        } else if (!selectedOption && searchTerm) {
          onSearchChange('');
        }
      }, 0);
    }
  };
  const handleWheelCapture = (event: React.WheelEvent<HTMLDivElement>) => {
    onPopoverWheelCapture?.(event);
  };

  useEffect(() => {
    const content = contentRef.current;
    if (!content) {
      return undefined;
    }

    const handleNativeWheel = (nativeEvent: WheelEvent) => {
      try {
        nativeEvent.preventDefault();
      } catch {
        /* noop - some browsers treat wheel listeners as passive */
      }
      content.scrollTop += nativeEvent.deltaY;
      if (onPopoverWheelCapture) {
        onPopoverWheelCapture(nativeEvent as unknown as React.WheelEvent<HTMLDivElement>);
      }
    };

    content.addEventListener('wheel', handleNativeWheel, { passive: false });
    return () => {
      content.removeEventListener('wheel', handleNativeWheel);
    };
  }, [onPopoverWheelCapture, open]);

  // Check for exact match and create option
  const exactMatch = options.find(option =>
    option.name.toLowerCase() === searchTerm.toLowerCase()
  );
  const showCreateOption = enableInlineCreate &&
    searchTerm.trim() &&
    !exactMatch &&
    !isLoading &&
    onCreateNew;

  return (
    <Popover.Root open={open} onOpenChange={handleOpenChange} modal={false}>
      <Popover.Anchor asChild>
        <div className="relative">
          <Input
            ref={inputRef}
            {...props}
            value={searchTerm}
            onChange={handleInputChange}
            onFocus={handleInputFocus}
            placeholder={placeholder}
            error={error}
            className={cn("w-full pr-8", className)}
            aria-expanded={open}
            aria-haspopup="listbox"
            aria-autocomplete="list"
            role="combobox"
          />
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 cursor-pointer p-0 h-4 w-4"
            tabIndex={-1}
            onClick={handleDropdownToggle}
            aria-label="Toggle dropdown"
          >
            <svg
              className={cn(
                "h-4 w-4 text-muted-foreground transition-transform",
                open && "rotate-180"
              )}
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
            </svg>
          </button>
        </div>
      </Popover.Anchor>

      <Popover.Portal>
        <Popover.Content
          ref={contentRef}
          className="z-50 w-[var(--radix-popover-trigger-width)] bg-card border border-input rounded-md shadow-md max-h-60 overflow-y-auto"
          sideOffset={4}
          align="start"
          onOpenAutoFocus={(e) => {
            // Prevent focus from moving to the popover content
            e.preventDefault();
          }}
          onInteractOutside={(e) => {
            // Prevent closing when interacting with the input
            const target = e.target as HTMLElement;
            if (inputRef.current?.contains(target)) {
              e.preventDefault();
            }
          }}
          onWheelCapture={handleWheelCapture}
        >
          <div role="listbox">
            {isLoading ? (
              <div className="p-3">
                <div className="flex items-center gap-2">
                  <div className="w-4 h-4 border-2 border-muted-foreground border-t-transparent rounded-full animate-spin"></div>
                  <span className="text-sm text-muted-foreground">{loadingText}</span>
                </div>
              </div>
            ) : (
              <>
                {options.map((option) => (
                  <SearchableSelectOptionButton
                    key={String(option.id)}
                    option={option}
                    onClick={() => handleSelectOption(option)}
                    isSelected={value === option.id}
                  >
                    {renderOption ? renderOption(option) : option.name}
                  </SearchableSelectOptionButton>
                ))}

                {showCreateOption && (
                  <SearchableSelectCreateOption
                    onClick={handleCreateNew}
                    label={createNewLabel(searchTerm)}
                  />
                )}

                {options.length === 0 && !showCreateOption && searchTerm && (
                  <div className="p-3 text-sm text-muted-foreground">{noResultsText}</div>
                )}
              </>
            )}
          </div>
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

export const SearchableSelect = React.forwardRef(SearchableSelectComponent) as <
  IdType extends string | number = number,
  Option extends SearchableSelectOption<IdType> = SearchableSelectOption<IdType>
>(
  props: SearchableSelectProps<IdType, Option> & { ref?: React.Ref<HTMLInputElement> }
) => React.ReactElement;

(SearchableSelect as { displayName?: string }).displayName = "SearchableSelect";

interface SearchableSelectOptionButtonProps<
  Option extends SearchableSelectOption<string | number>
> {
  option: Option;
  onClick: () => void;
  isSelected: boolean;
  children: ReactNode;
}

function SearchableSelectOptionButton<
  Option extends SearchableSelectOption<string | number>
>({
  option,
  onClick,
  isSelected,
  children
}: SearchableSelectOptionButtonProps<Option>) {
  return (
    <button
      type="button"
      role="option"
      aria-selected={isSelected}
      className={cn(
        "w-full cursor-pointer px-3 py-2 text-left text-sm hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground focus:outline-hidden flex justify-between items-center",
        isSelected && "bg-accent text-accent-foreground"
      )}
      onClick={onClick}
      onMouseDown={(e) => {
        // Prevent the input from losing focus when clicking an option
        e.preventDefault();
      }}
      data-value={String(option.id)}
    >
      {children}
    </button>
  );
}

interface SearchableSelectCreateOptionProps {
  onClick: () => void;
  label: string;
}

function SearchableSelectCreateOption({
  onClick,
  label
}: SearchableSelectCreateOptionProps) {
  return (
    <button
      type="button"
      className="w-full cursor-pointer px-3 py-2 text-left text-sm text-muted-foreground hover:bg-accent hover:text-accent-foreground focus:bg-accent focus:text-accent-foreground focus:outline-hidden border-t"
      onClick={onClick}
      onMouseDown={(e) => {
        // Prevent the input from losing focus when clicking the create option
        e.preventDefault();
      }}
    >
      {label}
    </button>
  );
}
