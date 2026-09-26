import { createFileRoute } from '@tanstack/react-router';
import { UserDropdown } from '@/components/layout/user-dropdown';

export const Route = createFileRoute('/')({
  component: IndexPage,
});

// The empty UI: the app's own header (no app shell), and a page the triage stack replaces.
function IndexPage() {
  return (
    <>
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-4">
        <h1 className="text-lg font-semibold">Fieldnotes</h1>
        <UserDropdown />
      </header>
      <main className="flex flex-1 items-center justify-center" data-testid="index.page" />
    </>
  );
}
