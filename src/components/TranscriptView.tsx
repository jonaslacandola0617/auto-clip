import { useDeferredValue, useEffect, useMemo, useRef, useState } from "react";
import type { Transcript } from "../types";
import { Button, EmptyState, Panel } from "./Ui";
import { VideoPreview } from "./VideoPreview";

function clock(seconds: number) {
  const minutes = Math.floor(seconds / 60);
  return `${minutes}:${Math.floor(seconds % 60).toString().padStart(2, "0")}`;
}

type Range = { startId: string; endId: string };

export function TranscriptView({ transcript, sourcePath = null, busy, onTranscribe, onCorrect, onCreateClip }: {
  transcript: Transcript | null;
  /** The source (or preview proxy) path, so playback and reading can stay in sync. */
  sourcePath?: string | null;
  busy: boolean;
  onTranscribe: () => void;
  onCorrect: (segmentId: string, text: string) => Promise<void>;
  onCreateClip: (startSegmentId: string, endSegmentId: string) => Promise<void>;
}) {
  const [query, setQuery] = useState("");
  const deferredQuery = useDeferredValue(query.trim().toLowerCase());
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState("");
  const [selection, setSelection] = useState<Range | null>(null);
  const [creating, setCreating] = useState(false);
  const [currentTime, setCurrentTime] = useState(0);
  const [seekTo, setSeekTo] = useState<number | undefined>(undefined);
  const [seekKey, setSeekKey] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);

  const filtered = useMemo(() => transcript?.segments.filter((segment) => !deferredQuery || segment.text.toLowerCase().includes(deferredQuery)) ?? [], [deferredQuery, transcript]);
  const filteredIds = useMemo(() => filtered.map((segment) => segment.id), [filtered]);
  const activeSegment = useMemo(() => filtered.find((segment) => currentTime >= segment.start_seconds && currentTime < segment.end_seconds), [filtered, currentTime]);

  useEffect(() => {
    if (!activeSegment) return;
    listRef.current?.querySelector(`[data-segment-id="${activeSegment.id}"]`)?.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }, [activeSegment?.id]);

  if (!transcript) return <EmptyState title="No transcript yet" description="Transcribe the source video locally to create timestamped text." action={<Button variant="primary" disabled={busy} onClick={onTranscribe}>{busy ? "Processing…" : "Transcribe"}</Button>} />;

  function indexOf(id: string) { return filteredIds.indexOf(id); }

  function selectSingle(id: string) {
    setSelection((current) => (current && current.startId === id && current.endId === id ? null : { startId: id, endId: id }));
  }

  function handleMouseUp() {
    const sel = window.getSelection();
    const container = listRef.current;
    if (!sel || !container || sel.isCollapsed || !sel.toString().trim()) return;
    const anchorHost = sel.anchorNode instanceof Element ? sel.anchorNode : sel.anchorNode?.parentElement;
    const focusHost = sel.focusNode instanceof Element ? sel.focusNode : sel.focusNode?.parentElement;
    const anchorRow = anchorHost?.closest<HTMLElement>("[data-segment-id]");
    const focusRow = focusHost?.closest<HTMLElement>("[data-segment-id]");
    if (!anchorRow || !focusRow || !container.contains(anchorRow) || !container.contains(focusRow)) return;
    const a = indexOf(anchorRow.dataset.segmentId!);
    const b = indexOf(focusRow.dataset.segmentId!);
    if (a === -1 || b === -1) return;
    const [startIdx, endIdx] = a <= b ? [a, b] : [b, a];
    setSelection({ startId: filteredIds[startIdx], endId: filteredIds[endIdx] });
  }

  async function confirmClip() {
    if (!selection) return;
    setCreating(true);
    try { await onCreateClip(selection.startId, selection.endId); setSelection(null); } finally { setCreating(false); }
  }

  const selectionStart = selection ? transcript.segments.find((segment) => segment.id === selection.startId) : null;
  const selectionEnd = selection ? transcript.segments.find((segment) => segment.id === selection.endId) : null;

  return (
    <div className="page transcript-page">
      <header className="page-header"><div><p className="eyebrow">Transcript</p><h1>Watch and read together</h1></div></header>

      <div className="transcript-workspace">
        <div className="transcript-video-pane">
          {sourcePath ? <VideoPreview path={sourcePath} onTimeUpdate={setCurrentTime} seekTo={seekTo} seekKey={seekKey} /> : <div className="source-summary__missing" aria-hidden="true">▶</div>}
        </div>

        <Panel className="transcript-panel" title="Transcript">
          <input className="input transcript-search" aria-label="Search transcript" placeholder="Search transcript" value={query} onChange={(event) => setQuery(event.target.value)} />

          <div className="transcript-list" ref={listRef} onMouseUp={handleMouseUp} aria-live="polite">
            {filtered.length ? filtered.map((segment) => {
              const inSelection = Boolean(selection) && indexOf(segment.id) >= indexOf(selection!.startId) && indexOf(segment.id) <= indexOf(selection!.endId);
              const isActive = activeSegment?.id === segment.id;
              const rowClass = ["transcript-row", isActive ? "transcript-row--active" : "", inSelection ? "transcript-row--selected" : ""].filter(Boolean).join(" ");
              return (
                <article
                  className={rowClass}
                  key={segment.id}
                  data-segment-id={segment.id}
                  onClick={(event) => {
                    if ((event.target as HTMLElement).closest("button, textarea")) return;
                    const live = window.getSelection();
                    if (live && !live.isCollapsed) return;
                    selectSingle(segment.id);
                  }}
                >
                  <button type="button" className="transcript-row__time" onClick={() => { setSeekTo(segment.start_seconds); setSeekKey((value) => value + 1); }}>{clock(segment.start_seconds)}</button>
                  <div>
                    {editing === segment.id ? (
                      <div className="correction-editor">
                        <textarea className="input" value={draft} onChange={(event) => setDraft(event.target.value)} />
                        <div className="correction-editor__actions">
                          <Button variant="primary" onClick={async () => { await onCorrect(segment.id, draft); setEditing(null); }}>Save correction</Button>
                          <Button variant="quiet" onClick={() => setEditing(null)}>Cancel</Button>
                        </div>
                      </div>
                    ) : (
                      <>
                        <p className="transcript-row__text">{segment.text}</p>
                        {segment.corrected_text ? <small className="transcript-row__original">Original: {segment.original_text}</small> : null}
                        <Button variant="quiet" className="transcript-row__correct" onClick={() => { setEditing(segment.id); setDraft(segment.text); }}>Correct</Button>
                      </>
                    )}
                  </div>
                </article>
              );
            }) : <p className="muted">No transcript segments match this search.</p>}
          </div>
        </Panel>
      </div>

      {selection && selectionStart && selectionEnd ? (
        <div className="selection-bar" role="toolbar" aria-label="Selection actions">
          <span>{clock(selectionStart.start_seconds)}–{clock(selectionEnd.end_seconds)} selected</span>
          <div className="selection-bar__actions">
            <Button variant="quiet" onClick={() => setSelection(null)}>Clear</Button>
            <Button variant="primary" disabled={creating} onClick={confirmClip}>{creating ? "Creating…" : "Create clip"}</Button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
