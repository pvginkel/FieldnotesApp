import { Fragment, type ReactNode } from 'react';
import type { ReportsPlace } from '@/lib/triage/browser-state';
import { changesOf, GITHUB, YOUTRACK, type Item, type Snapshot } from '@/lib/triage/queue';
import { when } from '@/lib/triage/time';
import { Emoji, Inline, Prose, Repo, Time } from './prose';

interface TriageCardProps {
  item: Item;
  live: Snapshot | null | undefined; // the observation as the store has it now; null when gone
  reportsPlace: ReportsPlace;
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

export function TriageCard({ item, live, reportsPlace }: TriageCardProps) {
  const snapshot = item.snapshot;
  const repos = snapshot.repos;
  const changes = changesOf(item, live);

  const sections: Record<string, ReactNode> = {
    ask: item.ask ? (
      <section className="box ask-box">
        <div className="box-head">Ask</div>
        <Prose source={item.ask} className="ask" />
      </section>
    ) : null,
    evidence: <TextSection title="Evidence" body={item.evidence} />,
    reports: <Reports item={item} />,
    recommendation: <TextSection title="Recommendation" body={item.recommendation} />,
    impact: <TextSection title="Impact" body={item.impact} />,
    store: <StoreSection item={item} live={live} changes={changes} />,
  };
  const order = reportsPlace === 'last'
    ? ['ask', 'evidence', 'recommendation', 'impact', 'reports', 'store']
    : ['ask', 'evidence', 'reports', 'recommendation', 'impact', 'store'];

  return (
    <div className="column">
      <article className="card" data-testid="triage.card" data-observation={item.observation}>
        <div className="meta">
          <span className="id mono">{item.observation}</span>
          {changes.length > 0 && (
            <button
              type="button"
              className="link changed-chip"
              title="What the store says now"
              onClick={() => document.getElementById('store')?.scrollIntoView({ behavior: 'smooth' })}
              data-testid="triage.card.changed"
            >
              changed since: {changes.join('; ')}
            </button>
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

        {order.map((key) => <Fragment key={key}>{sections[key]}</Fragment>)}
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

function StoreSection({ item, live, changes }: { item: Item; live: Snapshot | null | undefined; changes: string[] }) {
  if (!live) {
    return (
      <section className="section" id="store">
        <SectionHead title="The store says" />
        <div className="store">
          <div className="gone">
            The observation is no longer in the store: merged into another or retired since this
            item was written.
          </div>
        </div>
      </section>
    );
  }
  const row = (label: string, value: ReactNode) =>
    value ? <><dt>{label}</dt><dd>{value}</dd></> : null;
  return (
    <section className="section" id="store" data-testid="triage.card.store">
      <SectionHead title="The store says" />
      <div className="store">
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
    </section>
  );
}
