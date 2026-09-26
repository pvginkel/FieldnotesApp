import { createFileRoute } from '@tanstack/react-router';

export const Route = createFileRoute('/')({
  component: IndexPage,
});

// The empty UI: the triage stack replaces this page.
function IndexPage() {
  return (
    <main className="flex flex-1 items-center justify-center" data-testid="index.page">
      <h1 className="text-2xl font-semibold">Fieldnotes</h1>
    </main>
  );
}
