import type { Doctor, RecentProject } from "../types";
import { Button, EmptyState, Panel, Status } from "./Ui";

export function HomeView({ doctor, recents, loading, onNew, onOpen, onOpenRecent, onRemoveRecent }: { doctor: Doctor | null; recents: RecentProject[]; loading: boolean; onNew: () => void; onOpen: () => void; onOpenRecent: (path: string) => void; onRemoveRecent: (path: string) => void }) {
  const environmentReady = Boolean(doctor?.ffmpeg.ready && doctor.whisper.ready);
  return (
    <div className="page page--home">
      <header className="hero"><p className="eyebrow">Projects</p><h1>Get to the moments worth editing.</h1><p>Start from local footage, keep every decision portable, and leave the repetitive work to AutoClip.</p><div className="hero__actions"><Button variant="primary" onClick={onNew}>New project</Button><Button onClick={onOpen}>Open project</Button></div></header>
      {doctor && !environmentReady ? <div className="notice notice--warning"><strong>Processing setup needs attention.</strong><span>Open Settings to see what AutoClip needs before transcription.</span></div> : null}
      <Panel title="Recent projects" action={doctor ? <Status ready={environmentReady}>{environmentReady ? "Processing ready" : "Setup incomplete"}</Status> : undefined}>
        {loading ? <div className="skeleton-list" aria-label="Loading recent projects"><span /><span /><span /></div> : recents.length ? (
          <div className="recent-list">{recents.map((project) => <article className="recent-row" key={project.path}><button className="recent-row__main" onClick={() => onOpenRecent(project.path)}><strong>{project.name}</strong><span>{project.available ? project.path : "Project unavailable"}</span></button><time>{new Date(project.last_opened).toLocaleDateString()}</time><Button variant="quiet" onClick={() => onRemoveRecent(project.path)} aria-label={`Remove ${project.name} from recent projects`}>Remove</Button></article>)}</div>
        ) : <EmptyState title="No recent projects" description="Create a project or open an existing AutoClip project to begin." action={<Button variant="primary" onClick={onNew}>New project</Button>} />}
      </Panel>
    </div>
  );
}

