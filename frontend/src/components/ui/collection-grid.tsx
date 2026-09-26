export interface CollectionGridProps {
  children: React.ReactNode;
  testId?: string;
}

export function CollectionGrid({ children, testId }: CollectionGridProps) {
  return (
    <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3 3xl:grid-cols-4" data-testid={testId}>
      {children}
    </div>
  );
}
