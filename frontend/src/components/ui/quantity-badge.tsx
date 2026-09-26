export interface QuantityBadgeProps {
  quantity: number;
  testId: string;
}

export function QuantityBadge({ quantity, testId }: QuantityBadgeProps) {
  return (
    <span
      className="px-3 py-1 text-sm font-bold rounded-full bg-primary text-primary-foreground"
      data-testid={testId}
    >
      {quantity}
    </span>
  );
}
