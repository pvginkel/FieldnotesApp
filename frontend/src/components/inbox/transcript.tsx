// The sitting's transcript (FR-28): the inbox prompt as the sitting's opening line, the session's
// messages as the skill writes them (markdown.tsx), the operator's as they typed them, each with
// its time under it; under the last one, while a turn is in flight, the last line of the session's
// condensed log. A lost sitting ends on a line saying so (FR-32).

import { LoaderCircle, TriangleAlert } from 'lucide-react';
import type { Message, Sitting } from '@/hooks/use-inbox';
import { clock } from '@/lib/inbox/text';
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

function Working({ log }: { log: string | null }) {
  return (
    <div className="working" data-testid="inbox.working">
      <LoaderCircle className="icon spin" aria-hidden="true" />
      <span className="working-log" title={log ?? undefined} data-testid="inbox.working.log">
        {log ?? 'Waiting for the session…'}
      </span>
    </div>
  );
}

interface TranscriptProps {
  sitting: Sitting;
  tracker: string | null;
  log: string | null;
}

export function Transcript({ sitting, tracker, log }: TranscriptProps) {
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
      {sitting.turn && <Working log={log} />}
      {sitting.lost && (
        <div className="lost" role="status" data-testid="inbox.lost">
          <TriangleAlert className="icon" aria-hidden="true" />
          <div>
            <strong>Lost at {clock(sitting.lost.at)}.</strong> {sentence(sitting.lost.reason)} What the session
            wrote on the tracker stands, and a new sitting reads it there.
          </div>
        </div>
      )}
    </div>
  );
}
