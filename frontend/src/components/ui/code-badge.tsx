export interface CodeBadgeProps {
  code: string;
  testId?: string;
}

export function CodeBadge({ code, testId }: CodeBadgeProps) {
  return (
    <span
      className="inline-block bg-muted px-2 py-1 rounded font-mono text-sm"
      data-testid={testId}
    >
      {code}
    </span>
  );
}
