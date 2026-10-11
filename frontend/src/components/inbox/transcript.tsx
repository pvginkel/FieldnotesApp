// The sitting's transcript (FR-28): the inbox prompt as the sitting's opening line, the session's
// messages as the skill writes them (markdown.tsx), the operator's as they typed them, each with
// its time under it; under the last one, while a turn is in flight, the working box over the
// session's condensed log. A sitting the expiry ended (FR-31) or a lost one (FR-32) ends on a line
// saying so.

import { TimerOff, TriangleAlert } from 'lucide-react';
import { WorkingBox } from '@/components/shell/working-box';
import type { Message, Sitting } from '@/hooks/use-inbox';
import { clock, minutes } from '@/lib/inbox/text';
import { stamp } from '@/lib/triage/time';
import { Markdown } from './markdown';

function Opening({ sitting, prompt }: { sitting: Sitting; prompt: Message }) {
  return (
    <div className="opening" data-testid="inbox.opening">
      <span className="rule" />
      <span>
        <code>{prompt.text}</code> · began {clock(prompt.at)}
        {sitting.environment && ` in ${sitting.environment}`}
        {sitting.started_environment && ', started for this sitting'}
      </span>
      <span className="rule" />
    </div>
  );
}

/** When the message was sent or answered, under it; the date in the tooltip. */
function MessageTime({ at }: { at: string }) {
  return (
    <time className="msg-time" dateTime={at} title={stamp(at)} data-testid="inbox.message.time">
      {clock(at)}
    </time>
  );
}

const sentence = (text: string) => `${text.charAt(0).toUpperCase()}${text.slice(1)}.`;

interface TranscriptProps {
  sitting: Sitting;
  tracker: string | null;
  lines: string[];
}

export function Transcript({ sitting, tracker, lines }: TranscriptProps) {
  const [prompt, ...rest] = sitting.transcript;
  return (
    <div className="transcript" data-testid="inbox.transcript">
      {prompt && <Opening sitting={sitting} prompt={prompt} />}
      {rest.map((message, i) =>
        message.author === 'session' ? (
          <section key={i} className="msg session" data-testid="inbox.message.session">
            <div className="session-md">
              <Markdown text={message.text} tracker={tracker} />
            </div>
            <MessageTime at={message.at} />
          </section>
        ) : (
          <section key={i} className="msg operator" data-testid="inbox.message.operator">
            <div className="operator-text">{message.text}</div>
            <MessageTime at={message.at} />
          </section>
        ),
      )}
      {sitting.turn && <WorkingBox lines={lines} waiting="Waiting for the session…" testId="inbox.working" />}
      {sitting.lost && (
        <div className="closed-line" role="status" data-testid="inbox.lost">
          <TriangleAlert className="icon" aria-hidden="true" />
          <div>
            <strong>Lost at {clock(sitting.lost.at)}.</strong> {sentence(sitting.lost.reason)} What the session
            wrote on the tracker stands, and a new sitting reads it there.
          </div>
        </div>
      )}
      {sitting.ended && (
        <div className="closed-line" role="status" data-testid="inbox.ended">
          <TimerOff className="icon" aria-hidden="true" />
          <strong>
            Ended at {clock(sitting.ended.at)} after {minutes(sitting.ended.idle_minutes)} idle.
          </strong>
        </div>
      )}
    </div>
  );
}
