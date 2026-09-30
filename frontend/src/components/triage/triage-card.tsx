import type { ReactNode } from 'react';
import { Tooltip } from '@/components/primitives/tooltip';
import { changesOf, GITHUB, YOUTRACK, type Item, type Snapshot } from '@/lib/triage/queue';
import { when } from '@/lib/triage/time';
import { Emoji, Inline, Prose, Repo, Time } from './prose';

interface TriageCardProps {
  item: Item;
  live: Snapshot | null | undefined; // the observation as the store has it now; null when gone
}

const SectionHead = ({ title }: { title: string }) => (
  <h2 className="section-head"><span>{title}</span></h2>
);

const TextSection = ({ title, body }: { title: string; body: string }) =>
  body ? (
    <section className="section">
      <SectionHead title={title} />
      <Prose source={body} />
    </section>
  ) : null;

export function TriageCard({ item, live }: TriageCardProps) {
  const snapshot = item.snapshot;
  const repos = snapshot.repos;
  const changes = changesOf(item, live);
  // What the store says now shows on hover over the observation's id, and over the changed flag.
  const store = <StoreDetails item={item} live={live} changes={changes} />;

  return (
    <div className="column">
      <article className="card" data-testid="triage.card" data-observation={item.observation}>
        <div className="meta">
          <div className="id-anchor">
            <Tooltip content={store} placement="bottom">
              <span className="id mono" data-testid="triage.card.id">{item.observation}</span>
            </Tooltip>
          </div>
          {changes.length > 0 && (
            <Tooltip content={store} placement="bottom">
              <span className="changed-chip" data-testid="triage.card.changed">
                changed since: {changes.join('; ')}
              </span>
            </Tooltip>
          )}
          <span className={`badge ${snapshot.category}`}>{snapshot.category}</span>
          <span>{snapshot.status}</span><span className="sep">·</span>
          {repos.length > 0 && (
            <span title={repos.join(', ')}>
              <Repo name={repos[0]} />{repos.length > 1 ? ` +${repos.length - 1}` : ''}
            </span>
          )}
          <span className="sep">·</span>
          <Time iso={item.written} format={when} label="Written by the reconciler " />
        </div>
        <h1 className="headline"><Inline text={item.headline} /></h1>

        {item.question && (
          <div className="box returned" data-testid="triage.card.question">
            <div className="box-head">The actioner asks</div>
            <Prose source={item.question.text} />
            {item.ruling && (
              <div className="was">
                Your ruling was <strong>{item.ruling.verb}</strong>
                {item.ruling.note ? <>: <Inline text={item.ruling.note} /></> : null}
              </div>
            )}
          </div>
        )}

        {item.ask && (
          <section className="box ask-box">
            <div className="box-head">Ask</div>
            <Prose source={item.ask} className="ask" />
          </section>
        )}
        {(item.recommendation || item.impact) && (
          <section className="box recommendation-box">
            <div className="box-head">Recommendation</div>
            <Prose source={item.recommendation} />
            {item.impact && (
              <>
                <div className="box-subhead">Impact</div>
                <Prose source={item.impact} />
              </>
            )}
          </section>
        )}
        <TextSection title="Evidence" body={item.evidence} />
        <Reports item={item} />
      </article>
    </div>
  );
}

function Reports({ item }: { item: Item }) {
  return (
    <section className="section">
      <SectionHead title="As reported" />
      {item.reports.map((report, i) => (
        <div key={i} className={report.new ? 'report new' : 'report'} data-testid="triage.card.report">
          <div className="report-head">
            <Time iso={report.at} /><span>·</span>
            <span><Emoji emoji={report.emoji} /> {report.emoji === '👍' ? 'reacted' : 'posted'}</span><span>·</span>
            <Repo name={report.repo} />
            {report.session && (
              <>
                <span>·</span>
                <span title={report.session}>session <span className="mono">{report.session.slice(0, 8)}</span></span>
              </>
            )}
            {report.client && <><span>·</span><span>via {report.client}</span></>}
            {report.new && <><span>·</span><span className="new-tag">new</span></>}
          </div>
          <Prose source={report.text} />
        </div>
      ))}
    </section>
  );
}

function StoreDetails({ item, live, changes }: { item: Item; live: Snapshot | null | undefined; changes: string[] }) {
  if (!live) {
    return (
      <div className="store" data-testid="triage.card.store">
        <div className="store-head">The store says</div>
        <div className="gone">
          The observation is no longer in the store: merged into another or retired since this
          item was written.
        </div>
      </div>
    );
  }
  const row = (label: string, value: ReactNode) =>
    value ? <><dt>{label}</dt><dd>{value}</dd></> : null;
  return (
    <div className="store" data-testid="triage.card.store">
      <div className="store-head">The store says</div>
      {changes.length > 0 && <div className="changes">Changed since: {changes.join('; ')}</div>}
      <Prose source={live.canonical} className="canonical" />
      <dl>
        {row('status', live.status)}
        {row('category', live.category)}
        {row('area', live.area)}
        {row('repos', live.repos.map((name, i) => <span key={name}>{i > 0 && ', '}<Repo name={name} /></span>))}
        {row('card', live.card
          ? <a href={`${YOUTRACK}${live.card}`} target="_blank" rel="noreferrer">{live.card}</a>
          : <span className="count">none</span>)}
        {row('outcome', live.outcome)}
        {row('reason', live.reason ? <Inline text={live.reason} /> : null)}
        {row('first seen', <Time iso={live.created} />)}
        {row('last seen', <Time iso={live.last_seen} />)}
        {row('file', (
          <a href={`${GITHUB}${item.observation}.md`} target="_blank" rel="noreferrer">
            observations/{item.observation}.md
          </a>
        ))}
      </dl>
    </div>
  );
}
