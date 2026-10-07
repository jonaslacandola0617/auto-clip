# AutoClip V2.2 editor-domain contract

The canonical editable truth is `AutoClipProject.media_library` plus `AutoClipProject.sequences`. A `Sequence` owns ordered `Track` objects and structured timeline items. Timeline placement uses `timeline_start`/`timeline_duration`; source-backed items separately retain `source_in`/`source_out` and a stable `media_asset_id`.

Legacy `ProjectClip`, `EditSequence`, `VisualEditPlan`, `ReframeTrack`, and `EnhancementPlan` data remains serialized for backward compatibility and provenance. It is not a second editable timeline. V2.2 adapters materialize legacy clips and Smart Edits into sequences, and render/export consumers adapt from the canonical sequence. Visual and enhancement plans remain derived, revision-bound plans referenced by the sequence. Canonical caption and graphic items override their derived legacy representation at render time.

AI output is advisory. A validated, ready `EditSequence` is accepted through one `EditorSession` command transaction. Provider data never directly issues filesystem, FFmpeg, or arbitrary nested-project mutations.

## Edit semantics

- Insert, move, trim, split, remove, reorder, enable/disable, caption edits, and graphic edits are validated commands.
- Remove, move, trim, and split are non-ripple unless `ripple: true` is explicit.
- Linked source video/audio remains synchronized unless `preserve_link: false` is explicit.
- Same-track video/audio overlap is rejected unless that track explicitly has `allow_overlap` metadata. Caption and graphic overlap is valid.
- Sequence duration is derived from the latest enabled item end; no independent stored duration can drift.
- Undo/redo history is bounded and in-memory. Autosave does not clear it, and history contains sequence state rather than media data or project caches.

## Revisions and invalidation

Every committed command transaction increments the sequence and project editor revisions once. Caption-only changes invalidate caption render, preview, and final render without invalidating visual analysis. Structural trims/splits invalidate dependent captions, visual layout, preview, and render. Sequence naming and legacy AI reasoning are independent of timeline mechanics.

Preview and output associations record the sequence revision used. A background render finishing after a newer edit is retained as stale output and cannot overwrite the newer project sequence.

## Persistence and migration

Schema 2.2 loads supported V1/V2 schemas into the editor domain in memory. Loading does not rewrite the original. The first successful save is atomic and subsequent writes retain `.previous` recovery data. Unsupported newer schemas are rejected. Source relinking updates location only after fingerprint validation; logical asset IDs remain stable.
