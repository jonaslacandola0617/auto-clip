import { useState } from "react";
import { open } from "@tauri-apps/plugin-dialog";
import { Button, Field, Input } from "./Ui";

export type NewProjectValues = { name: string; location: string; source_path?: string };

export function ProjectDialog({ defaultLocation, onClose, onCreate }: { defaultLocation: string; onClose: () => void; onCreate: (values: NewProjectValues) => Promise<void> }) {
  const [name, setName] = useState("");
  const [location, setLocation] = useState(defaultLocation);
  const [source, setSource] = useState("");
  const [busy, setBusy] = useState(false);

  async function chooseLocation() {
    const selected = await open({ directory: true, multiple: false, title: "Choose project location" });
    if (typeof selected === "string") setLocation(selected);
  }

  async function chooseSource() {
    const selected = await open({ multiple: false, title: "Choose source video", filters: [{ name: "Video", extensions: ["mp4", "mov", "mkv", "avi", "webm", "m4v"] }] });
    if (typeof selected === "string") setSource(selected);
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    try {
      await onCreate({ name, location, ...(source ? { source_path: source } : {}) });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form className="dialog" role="dialog" aria-modal="true" aria-labelledby="new-project-title" onSubmit={submit}>
        <header><p className="eyebrow">New project</p><h2 id="new-project-title">Start with the source</h2><p>Create a local project. Your video stays where it is.</p></header>
        <Field label="Project name"><Input autoFocus required value={name} onChange={(event) => setName(event.target.value)} placeholder="Podcast episode 12" /></Field>
        <Field label="Project location"><div className="input-action"><Input required value={location} onChange={(event) => setLocation(event.target.value)} /><Button type="button" variant="quiet" onClick={chooseLocation}>Browse</Button></div></Field>
        <Field label="Source video" hint="Optional now. AutoClip never copies or modifies the original file."><div className="input-action"><Input value={source} readOnly placeholder="Choose a video" /><Button type="button" variant="quiet" onClick={chooseSource}>Choose</Button></div></Field>
        <footer><Button type="button" variant="quiet" onClick={onClose}>Cancel</Button><Button type="submit" variant="primary" disabled={busy || !name.trim() || !location.trim()}>{busy ? "Creating…" : "Create project"}</Button></footer>
      </form>
    </div>
  );
}

