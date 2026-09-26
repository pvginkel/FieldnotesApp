export function ClearButtonIcon({ className = "w-4 h-4" }: { className?: string }) {
  return (
    <svg 
      className={className}
      viewBox="0 0 16 16" 
      fill="currentColor"
    >
      <defs>
        <mask id="clear-button-mask">
          <rect width="16" height="16" fill="white" />
          <path 
            d="M5.5 5.5L10.5 10.5M10.5 5.5L5.5 10.5" 
            stroke="black" 
            strokeWidth="1.5" 
            strokeLinecap="round"
          />
        </mask>
      </defs>
      <circle 
        cx="8" 
        cy="8" 
        r="8" 
        className="fill-muted-foreground hover:fill-muted-foreground/80" 
        mask="url(#clear-button-mask)"
      />
    </svg>
  );
}