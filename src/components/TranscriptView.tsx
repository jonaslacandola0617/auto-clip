import { useDeferredValue, useMemo, useState } from "react";
import type { Transcript } from "../types";
import { Button, EmptyState, Input, Panel, Select } from "./Ui";

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

export function TranscriptView({ transcript, busy, onTranscribe, onCorrect, onCreateClip }: {
  transcript: Transcript | null;
  busy: boolean;
  onTranscribe: () => void;
  onCorrect: (segmentId: string, text: string) => Promise<void>;
  onCreateClip: (startSegmentId: string, endSegmentId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [startId, setStartId] = useState("");
  const [endId, setEndId] = useState("");
  const filtered = useMemo(() => transcript?.segments.filter((segment) => !deferredQuery || segment.text.toLowerCase().includes(deferredQuery)) ?? [], [deferredQuery, transcript]);

  if (!transcript) return <EmptyState title="No transcript yet" description="Transcribe the source video locally to create timestamped text." action={<Button variant="primary" disabled={busy} onClick={onTranscribe}>{busy ? "Processing…" : "Transcribe"}</Button>} />;
  return <div className="transcript-layout">
    <Panel title="Transcript" action={<Input aria-label="Search transcript" placeholder="Search transcript" value={query} onChange={(event) => setQuery(event.target.value)} />}>
      <div className="transcript-list" aria-live="polite">
        {filtered.length ? filtered.map((segment) => <article className="transcript-segment" key={segment.id}>
          <button className="timecode" onClick={() => { setStartId(segment.id); if (!endId) setEndId(segment.id); }}>{clock(segment.start_seconds)}</button>
          <div>
            {editing === segment.id ? <div className="correction-row"><textarea className="input" value={draft} onChange={(event) => setDraft(event.target.value)} /><Button variant="primary" onClick={async () => { await onCorrect(segment.id, draft); setEditing(null); }}>Save correction</Button><Button variant="quiet" onClick={() => setEditing(null)}>Cancel</Button></div> : <>
              <p className="transcript-text">{segment.text}</p>
              {segment.corrected_text ? <small>Original: {segment.original_text}</small> : null}
              <Button variant="quiet" onClick={() => { setEditing(segment.id); setDraft(segment.text); }}>Correct</Button>
            </>}
          </div>
        </article>) : <p className="muted">No transcript segments match this search.</p>}
      </div>
    </Panel>
    <Panel title="Create manual clip">
      <div className="field-stack">
        <label className="field"><span className="field__label">Start segment</span><Select value={startId} onChange={(event) => { setStartId(event.target.value); if (!endId) setEndId(event.target.value); }}><option value="">Choose start</option>{transcript.segments.map((segment) => <option key={segment.id} value={segment.id}>{clock(segment.start_seconds)} — {segment.text.slice(0, 48)}</option>)}</Select></label>
        <label className="field"><span className="field__label">End segment</span><Select value={endId} onChange={(event) => setEndId(event.target.value)}><option value="">Choose end</option>{transcript.segments.map((segment) => <option key={segment.id} value={segment.id}>{clock(segment.end_seconds)} — {segment.text.slice(0, 48)}</option>)}</Select></label>
        <Button variant="primary" disabled={!startId || !endId} onClick={() => onCreateClip(startId, endId)}>Create clip</Button>
      </div>
    </Panel>
  </div>;
}
