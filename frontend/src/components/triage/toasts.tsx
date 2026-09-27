import type { Toast } from '@/hooks/use-triage';

export function Toasts({ toasts }: { toasts: Toast[] }) {
  return (
    <div className="toasts" data-testid="triage.toasts">
      {toasts.map((toast) => (
        <div
          key={toast.id}
          className={toast.error ? 'toast error' : 'toast'}
          role={toast.error ? 'alert' : 'status'}
          data-testid="triage.toast"
        >
          {toast.text}
        </div>
      ))}
    </div>
  );
}
