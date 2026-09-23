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

