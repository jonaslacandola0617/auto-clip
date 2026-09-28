import type { ButtonHTMLAttributes, InputHTMLAttributes, ReactNode, SelectHTMLAttributes } from "react";

export function Button({ variant = "secondary", className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "quiet" | "danger" }) {
  return <button className={`button button--${variant} ${className}`} {...props} />;
}

export function Panel({ title, action, children, className = "" }: { title?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`panel ${className}`}>
      {title ? <header className="panel__header"><h2>{title}</h2>{action}</header> : null}
      {children}
    </section>
  );
}

export function Status({ ready, children }: { ready: boolean; children: ReactNode }) {
  return <span className={`status ${ready ? "status--ready" : "status--warning"}`}><span aria-hidden="true" />{children}</span>;
}

export function Badge({ tone = "neutral", children }: { tone?: "neutral" | "success" | "warning" | "danger"; children: ReactNode }) {
  return <span className={`badge badge--${tone}`}>{children}</span>;
}

export function EmptyState({ title, description, action }: { title: string; description: string; action?: ReactNode }) {
  return <div className="empty-state"><div className="empty-state__mark" aria-hidden="true">◇</div><h3>{title}</h3><p>{description}</p>{action}</div>;
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return <label className="field"><span className="field__label">{label}</span>{children}{hint ? <span className="field__hint">{hint}</span> : null}</label>;
}

export function Input(props: InputHTMLAttributes<HTMLInputElement>) {
  return <input className="input" {...props} />;
}

export function Select(props: SelectHTMLAttributes<HTMLSelectElement>) {
  return <select className="input" {...props} />;
}

export function Progress({ value }: { value: number }) {
  const percent = Math.round(value * 100);
  return <div className="progress" role="progressbar" aria-valuenow={percent} aria-valuemin={0} aria-valuemax={100}><span style={{ width: `${percent}%` }} /></div>;
}

/** A single obvious way to switch between a small number of mutually exclusive views (e.g. Highlight Clips vs Smart Edits). */
export function SegmentedControl<T extends string>({ options, value, onChange, ariaLabel }: { options: Array<{ value: T; label: string }>; value: T; onChange: (value: T) => void; ariaLabel: string }) {
  return (
    <div className="segmented" role="group" aria-label={ariaLabel}>
      {options.map((option) => (
        <button key={option.value} type="button" className={option.value === value ? "segmented__option segmented__option--active" : "segmented__option"} aria-pressed={option.value === value} onClick={() => onChange(option.value)}>
          {option.label}
        </button>
      ))}
    </div>
  );
}

/** A togglable chip for a detected automatic action (e.g. Punch-in, Reframe) that AutoClip applied and the user can turn off. */
export function ActionChip({ label, active, onToggle, disabled }: { label: string; active: boolean; onToggle: () => void; disabled?: boolean }) {
  return (
    <button type="button" className={active ? "action-chip action-chip--on" : "action-chip action-chip--off"} onClick={onToggle} disabled={disabled} aria-pressed={active}>
      <span className="action-chip__dot" aria-hidden="true" />{label}
    </button>
  );
}

/** The project's progress through the workflow: Source, Transcript, Analysis, Clips, Visual edit, Export. */
export function WorkflowSteps({ steps }: { steps: Array<{ label: string; state: "done" | "current" | "upcoming" }> }) {
  return (
    <ol className="workflow-steps" aria-label="Project progress">
      {steps.map((step, index) => (
        <li key={step.label} className={`workflow-steps__item workflow-steps__item--${step.state}`}>
          <span className="workflow-steps__marker" aria-hidden="true">{step.state === "done" ? "✓" : index + 1}</span>
          <span>{step.label}</span>
        </li>
      ))}
    </ol>
  );
}
