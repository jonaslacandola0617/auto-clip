import type { Doctor, RecentProject } from "../types";
import { Button, EmptyState, Panel, Status } from "./Ui";

export function HomeView({ doctor, recents, loading, onNew, onOpen, onOpenRecent, onRemoveRecent, onOpenSettings }: { doctor: Doctor | null; recents: RecentProject[]; loading: boolean; onNew: () => void; onOpen: () => void; onOpenRecent: (path: string) => void; onRemoveRecent: (path: string) => void; onOpenSettings: () => void }) {
  const environmentReady = Boolean(doctor?.runtime.ready && doctor.storage.ready && doctor.ffmpeg.ready && doctor.whisper.ready);
  const readiness = doctor ? [
    ["Desktop engine", doctor.runtime],
    ["Application storage", doctor.storage],
    ["Video processing", doctor.ffmpeg],
    ["Transcription", doctor.whisper],
    ["Speech model", doctor.model],
    ["Visual framing", doctor.mediapipe],
    ["AI story analysis (optional)", doctor.gemini],
  ] as const : [];
  return (
    <div className="page page--home">
      <header className="hero"><p className="eyebrow">Projects</p><h1>Get to the moments worth editing.</h1><p>Start from local footage, keep every decision portable, and leave the repetitive work to AutoClip.</p><div className="hero__actions"><Button variant="primary" onClick={onNew}>New project</Button><Button onClick={onOpen}>Open project</Button></div></header>
      {doctor ? <section className="readiness-card" aria-labelledby="readiness-title">
        <div className="readiness-card__heading"><div><p className="eyebrow">First-run check</p><h2 id="readiness-title">{environmentReady ? "Ready to create" : "Setup needs attention"}</h2><p>Core tools are included with AutoClip. The speech model downloads only when you first transcribe; Gemini is optional.</p></div><Button onClick={onOpenSettings}>Open settings</Button></div>
        <div className="readiness-list">{readiness.map(([label, capability]) => <div className="readiness-row" key={label}><span>{label}</span><Status ready={capability.ready}>{capability.summary}</Status></div>)}</div>
      </section> : null}
      <Panel title="Recent projects" action={doctor ? <Status ready={environmentReady}>{environmentReady ? "Processing ready" : "Setup incomplete"}</Status> : undefined}>
        {loading ? <div className="skeleton-list" aria-label="Loading recent projects"><span /><span /><span /></div> : recents.length ? (
          <div className="recent-list">{recents.map((project) => (
            <article className="recent-row" key={project.path}>
              <button className="recent-row__main" onClick={() => onOpenRecent(project.path)} disabled={!project.available}>
                <strong>{project.name}</strong>
                <span>{project.available ? project.path : "Project unavailable — locate the file to continue"}</span>
              </button>
              <time>{new Date(project.last_opened).toLocaleDateString()}</time>
              {project.available ? <Button variant="quiet" onClick={() => onOpenRecent(project.path)}>Continue</Button> : null}
              <Button variant="quiet" onClick={() => onRemoveRecent(project.path)} aria-label={`Remove ${project.name} from recent projects`}>Remove</Button>
            </article>
          ))}</div>
        ) : <EmptyState title="No recent projects" description="Create a project or open an existing AutoClip project to begin." action={<Button variant="primary" onClick={onNew}>New project</Button>} />}
      </Panel>
    </div>
  );
}
