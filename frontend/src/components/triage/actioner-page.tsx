// The actioner page (FR-33): while a start is pending or a run is held, what the run is doing, in
// the inbox's working box: the run's last log lines, with the waiting text until one has come.
// Only `GET /api/triage/actioner` carries them, so the page fetches it on a timer while it shows.

import { WorkingBox } from '@/components/shell/working-box';
import { useGetTriageActioner } from '@/lib/api/generated/hooks';
import { CenterCard } from './finish-card';

const LINES_EVERY = 2000;

export function ActionerPage() {
  const { data } = useGetTriageActioner(undefined, { refetchInterval: LINES_EVERY });
  return (
    <CenterCard
      title={data?.state === 'running' ? 'The actioner is running' : 'The actioner is starting'}
      testId="triage.actioner"
    >
      <div className="actioner-run">
        <WorkingBox lines={data?.lines ?? []} waiting="Waiting for the run…" testId="triage.actioner.working" />
      </div>
    </CenterCard>
  );
}
