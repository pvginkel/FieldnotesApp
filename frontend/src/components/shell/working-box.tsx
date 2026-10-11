// The working box (FR-28, FR-33): what a session is doing, as the last lines of its condensed log,
// up to four, the spinner on the last; the top line goes as a new one comes, and nothing marks the
// lines gone. Each line stays on one line, cut with an ellipsis of its own. Before the first line,
// the spinner with the waiting text.

import { LoaderCircle } from 'lucide-react';

export const WORKING_LINES = 4;

interface WorkingBoxProps {
  lines: string[];
  waiting: string;
  testId: string;
}

export function WorkingBox({ lines, waiting, testId }: WorkingBoxProps) {
  const shown = lines.slice(-WORKING_LINES);
  return (
    <div className="working" data-testid={testId}>
      <span className="working-spin" data-testid={`${testId}.spinner`}>
        <LoaderCircle className="icon" aria-hidden="true" />
      </span>
      {shown.length ? (
        <ol className="working-lines">
          {shown.map((line, i) => (
            <li key={i} className="working-line" title={line} data-testid={`${testId}.line`}>
              {line}
            </li>
          ))}
        </ol>
      ) : (
        <span className="working-line" data-testid={`${testId}.waiting`}>
          {waiting}
        </span>
      )}
    </div>
  );
}
