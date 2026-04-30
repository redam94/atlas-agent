# ATLAS Phase 2 — Plan 6: Note Editor (design)

**Date:** 2026-04-29
**Phase:** 2
**Plan:** 6 of 6
**Depends on:** Plans 1–5 (web ingestion, Neo4j schema, NER+PageRank, hybrid retrieval, knowledge explorer) — all merged.
**Blocks:** None.

## 1. Purpose

`/projects/:id/notes` lets the user write notes. Notes are first-class graph nodes — their bodies go through the same parser → chunker → embedder → graph pipeline as ingested documents. Explicit `@`-mentions of existing entities create `TAGGED_WITH` edges, distinct from the NER-derived `REFERENCES` edges. This closes Phase 2's loop: every surface (chat, explorer, notes) reads and writes the same knowledge graph.

## 2. Scope

In scope:
- New `notes` Postgres table for editor metadata and a stable, durable home for the markdown body.
- `KnowledgeNodeType` gains a `NOTE = "note"` member; ingested notes appear in `knowledge_nodes` with `type='note'` and in Neo4j as `(:Document)` nodes with `type='note'` (Plan 5 Explorer renders them under the Document filter pill — no Plan 5 changes needed).
- REST CRUD `/api/v1/notes` (list, get, create, patch, delete) plus a separate `POST /api/v1/notes/{id}/index` that runs the heavy ingestion pipeline.
- Mention autocomplete endpoint `GET /api/v1/knowledge/entities?project_id=&prefix=`.
- New `GraphStore.tag_note(note_id, entity_ids)` writing `(:Document)-[:TAGGED_WITH]->(:Entity)` edges on indexed notes.
- Frontend `/projects/:id/notes` route with a third Sidebar tab (Chat / Explorer / Notes), a split rail+editor layout, TipTap rich-text editor with markdown round-trip, `@`-mention dropdown sourced from existing entities, and a two-state save UX (cheap auto-save to Postgres + explicit "Save & Index" for the full pipeline).

Out of scope (explicit non-goals for v1):
- Collaborative editing (Y.js / CRDT). Single-user only.
- Version history across sessions. TipTap's session-scoped undo is enough.
- Note export to other formats (PDF, HTML).
- Note-to-note `[[wikilinks]]`. Folders / tags. Image attachments.
- Auto-creation of new entities from `@foo` of unknown names — existing-entities-only autocomplete; typed-but-unmatched `@foo` becomes plain text.
- Search-within-notes UI. Chat retrieval already finds indexed notes; a dedicated note-search is YAGNI.
- Mobile / touch optimization. Desktop only.
- Real-time presence indicators.

## 3. Architecture

```
                   ┌────────────────────────────────────────┐
                   │  apps/web                              │
                   │                                        │
                   │  /projects/:id/notes                   │
                   │   ├─ note-list-rail (left)             │
                   │   └─ note-editor (right)               │
                   │      ├─ title input                    │
                   │      ├─ TipTap (StarterKit + Mention)  │
                   │      └─ Save & Index button            │
                   │                                        │
                   │  Zustand: editor session draft state   │
                   │  React Query: notes list, by-id,       │
                   │                entities autocomplete    │
                   └────────────────┬───────────────────────┘
                                    │ HTTP
                                    ▼
                   ┌────────────────────────────────────────┐
                   │  apps/api                              │
                   │                                        │
                   │  /api/v1/notes (CRUD)                  │
                   │  /api/v1/notes/{id}/index (heavy)      │
                   │  /api/v1/knowledge/entities (prefix)   │
                   └─────┬──────────────────────────┬───────┘
                         │                          │
                         ▼                          ▼
            ┌──────────────────────┐   ┌────────────────────────┐
            │ NotesService         │   │ GraphStore             │
            │   notes table CRUD   │   │   tag_note (NEW)       │
            │   index() orchestrate│   │   list_entities (NEW)  │
            └────┬───────────┬─────┘   └──────────┬─────────────┘
                 │           │                    │
                 ▼           ▼                    ▼
       ┌──────────────┐  ┌──────────────────┐  ┌─────────┐
       │ Postgres     │  │ IngestionService │  │  Neo4j  │
       │  notes table │  │  (Plan 1+3)      │  │         │
       │  knowledge_  │  │  cleanup_document│  │         │
       │  nodes       │  │  ingest          │  │         │
       └──────────────┘  └──────────────────┘  └─────────┘
```

### 3.1 The two-state save model

The fundamental design decision: separating cheap durability (Postgres write) from expensive indexing (chunker + embedder + NER + PageRank + graph writes).

- **PATCH `/notes/{id}`** — fires after a 2s debounce on every edit. Writes `body_markdown`, `title`, `mention_entity_ids` to the `notes` row. Bumps `updated_at`. **Does not run any ingestion.** Returns in milliseconds.
- **POST `/notes/{id}/index`** — runs the full pipeline. Returns in seconds. UX shows a spinner.
- A note is **Saved** when `notes.body_markdown` matches the editor (last PATCH succeeded). It is **Indexed** when `notes.indexed_at >= notes.updated_at`. The header badge displays one of: `Saving…`, `Saved`, `Indexed`, `Indexed (stale)`.
- Editor blur fires Save & Index automatically. Route navigation only flushes pending PATCHes — does not auto-index, since route changes shouldn't trigger NER.

### 3.2 Schema reuse decision

Notes use the existing `knowledge_nodes` table (with new `type='note'`) and the existing `(:Document)` Neo4j label (with property `type='note'`). The `notes` Postgres table is a thin metadata layer: it owns editor-facing fields (`body_markdown`, `title`, `indexed_at`) and holds a nullable FK to the `knowledge_nodes` row that backs retrieval. This lets the chunker, embedder, hybrid retriever (Plan 4), and Knowledge Explorer (Plan 5) treat notes as documents — zero changes to those code paths. The cost is one extra fk hop on the editor side, paid once on note load.

## 4. Backend

### 4.1 New `notes` table (Alembic migration)

```sql
CREATE TABLE notes (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id             TEXT NOT NULL,
  project_id          UUID NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  knowledge_node_id   UUID NULL REFERENCES knowledge_nodes(id) ON DELETE SET NULL,
  title               TEXT NOT NULL DEFAULT 'Untitled',
  body_markdown       TEXT NOT NULL DEFAULT '',
  mention_entity_ids  UUID[] NOT NULL DEFAULT '{}',
  indexed_at          TIMESTAMPTZ NULL,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX notes_project_id_updated_at ON notes(project_id, updated_at DESC);
```

Column rationale:
- `knowledge_node_id` is null until the note is first indexed; reset to a new value on each re-index. ON DELETE SET NULL so deleting the underlying KnowledgeNode (e.g. via `/knowledge/nodes/{id}`) doesn't orphan the row.
- `mention_entity_ids` is the canonical store of explicit @-mentioned entity IDs. PATCH overwrites it; the index endpoint reads from it to write TAGGED_WITH edges. Database-side rather than FE-side because we want explicit mentions to survive across editor sessions.
- `indexed_at < updated_at` (or null) ⇒ stale-index banner.
- ON DELETE CASCADE on `project_id` so deleting a project drops its notes.

### 4.2 `KnowledgeNodeType` extension

Add `NOTE = "note"` to the StrEnum in `packages/atlas-knowledge/atlas_knowledge/models/nodes.py`. The ORM column is plain `TEXT`, so no DB migration needed for this addition.

### 4.3 REST endpoints

All under `/api/v1/notes`. Live in `apps/api/atlas_api/routers/notes.py` (new file). Project ownership check on every endpoint (404 if `project_id` doesn't belong to the requesting user).

| Method | Path | Body | Returns | Triggers ingestion |
|--------|------|------|---------|--------------------|
| GET    | `/notes?project_id=` | — | `list[NoteListItem]` (id, title, updated_at, indexed_at) | No |
| POST   | `/notes` | `{project_id, title?, body_markdown?}` | `Note` (full row) | No |
| GET    | `/notes/{id}` | — | `Note` | No |
| PATCH  | `/notes/{id}` | `{title?, body_markdown?, mention_entity_ids?}` | `Note` | No |
| POST   | `/notes/{id}/index` | — | `IngestionJob` | **Yes** |
| DELETE | `/notes/{id}` | — | 204 | (cleanup only) |

`mention_entity_ids` is persisted on the row (see §4.1). PATCH overwrites it; the index endpoint reads from it.

### 4.4 Index endpoint logic

`IngestionService.ingest` currently returns only `job.id`; the document id created during ingest is local to the function. Plan 6 needs the document id (to update `notes.knowledge_node_id` and to tag mentions). **Change the return type** to a small dataclass:

```python
@dataclass(frozen=True)
class IngestionResult:
    job_id: UUID
    document_id: UUID | None  # None if the source produced zero chunks AND zero document
```

All existing callers (`routers/knowledge.py` ingest handlers) take `result.job_id` — a one-line update at each call site.

Index handler:

```python
async def index_note(note_id):
    note = await db.get(NoteORM, note_id)            # 404 if missing
    if note.knowledge_node_id is not None:
        await graph_store.cleanup_document(          # Plan 3 helper on GraphStore
            project_id=note.project_id,
            document_id=note.knowledge_node_id,
        )
        # Postgres-side: KnowledgeNodeORM cascade deletes chunks via parent_id FK;
        # Chroma cleanup is handled inside IngestionService.ingest before re-write.
    parsed = parse_markdown(note.body_markdown, title=note.title)
    result = await ingestion_service.ingest(
        db=db, user_id=note.user_id, project_id=note.project_id,
        parsed=parsed, source_type="note", source_filename=None,
    )
    if result.document_id is not None and note.mention_entity_ids:
        await graph_store.tag_note(
            note_id=result.document_id,
            entity_ids=note.mention_entity_ids,
        )
    note.knowledge_node_id = result.document_id
    note.indexed_at = datetime.now(UTC)
    await db.flush()
    return await db.get(IngestionJobORM, result.job_id)
```

The new document id is the same UUID as the `knowledge_nodes.id` row AND the `(:Document {type:'note'})` Neo4j node id (Plan 2 establishes this invariant). `tag_note` matches on that id and creates `(:Document)-[:TAGGED_WITH]->(:Entity)` edges.

### 4.5 Mention autocomplete endpoint

`GET /api/v1/knowledge/entities?project_id=&prefix=&limit=10` in `routers/knowledge.py`:

Cypher:
```cypher
MATCH (e:Entity {project_id: $pid})
WHERE toLower(e.name) STARTS WITH toLower($prefix)
RETURN e.id AS id, e.name AS name, e.type AS entity_type,
       coalesce(e.pagerank_global, 0.0) AS pagerank
ORDER BY pagerank DESC
LIMIT $limit
```

When `prefix` is empty, returns the top-N entities by PageRank (good for the initial dropdown view before typing). 503 on `GraphUnavailableError`.

New method `GraphStore.list_entities(project_id, prefix, limit)` returns a list of dicts. Pydantic response model `EntitySuggestion` in `atlas_knowledge.models.graph` (extends Plan 5's models file).

### 4.6 `GraphStore.tag_note`

New Cypher constant in `packages/atlas-graph/atlas_graph/store.py`:

```python
TAG_NOTE_CYPHER = """
UNWIND $entity_ids AS eid
MATCH (n:Document {id: $note_id}), (e:Entity {id: eid})
MERGE (n)-[:TAGGED_WITH]->(e)
"""
```

The MERGE makes re-indexing idempotent. New method:

```python
async def tag_note(
    self, *, note_id: UUID, entity_ids: list[UUID],
) -> None:
    if not entity_ids:
        return
    async def _do(tx):
        await tx.run(
            TAG_NOTE_CYPHER,
            note_id=str(note_id),
            entity_ids=[str(e) for e in entity_ids],
        )
    await self._with_retry(_do)
```

Note: stale TAGGED_WITH edges from a previous index are pre-emptively removed by `cleanup_document` (which already DETACH DELETEs the document and its REFERENCES; TAGGED_WITH edges hang off the document node so they're swept too).

### 4.7 Pydantic models

`packages/atlas-core/atlas_core/models/notes.py` (new):

- `Note` — id, user_id, project_id, knowledge_node_id, title, body_markdown, mention_entity_ids, indexed_at, created_at, updated_at.
- `NoteListItem` — id, title, updated_at, indexed_at.
- `CreateNoteRequest` — project_id, title (default 'Untitled'), body_markdown (default '').
- `PatchNoteRequest` — title?, body_markdown?, mention_entity_ids?

ORM model `NoteORM` lives in `packages/atlas-core/atlas_core/db/orm.py`.

### 4.8 Code organization

Logic lives in the router file (`apps/api/atlas_api/routers/notes.py`), matching the existing pattern in `routers/knowledge.py`, `routers/projects.py`, `routers/sessions.py`. No separate service object — keep the surface flat until there's a reason not to.

## 5. Frontend

### 5.1 Routes

```ts
{ path: "projects/:id", element: <ProjectShell />, children: [
    { index: true, element: <ChatRoute /> },
    { path: "explorer", element: <ExplorerRoute /> },
    { path: "notes", element: <NotesRoute />, children: [
        { index: true, element: <NotesEmpty /> },
        { path: ":noteId", element: <NoteEditor /> },
    ]},
]}
```

`ProjectTabs` (Plan 5) gets a third tab `Notes` with the Lucide `StickyNote` icon. Tab order: Chat / Explorer / Notes.

### 5.2 Component tree

```
routes/notes.tsx                       # NotesRoute — split layout shell
└── components/notes/
    ├── note-list-rail.tsx             # left rail: list + "+ New" button
    ├── note-list-item.tsx             # one row in the rail
    ├── note-editor.tsx                # right pane: title input + TipTap + save bar
    ├── note-mention-extension.ts      # TipTap Mention extension config
    ├── note-mention-list.tsx          # autocomplete dropdown component
    ├── note-empty.tsx                 # right pane when no note selected
    ├── notes-store.ts                 # Zustand: editor draft state, dirty flag
    └── use-notes.ts                   # react-query hooks (list, get, create, patch, index, delete)
```

### 5.3 Library choice

- `@tiptap/react`, `@tiptap/starter-kit` — base editor.
- `@tiptap/extension-mention`, `@tiptap/suggestion`, `tippy.js` — mention dropdown.
- `turndown` — HTML → markdown round-trip on save.
- `marked` — markdown → HTML on load (already a transitive dep via remark, but we'll add it explicitly because TipTap needs synchronous HTML).

Total weight ~120 kb gzipped. Matches the dep weight of substantive features in this app.

### 5.4 State (`notes-store.ts`)

```ts
interface NotesState {
  draftBody: string;            // current editor markdown
  draftTitle: string;
  draftMentionIds: Set<string>; // entity ids extracted from TipTap doc on every doc change
  dirty: boolean;               // body or title changed since last successful PATCH

  setDraft(body: string, title: string, mentions: Set<string>): void;
  markSaved(): void;            // on PATCH success, clears dirty
  reset(): void;                // on note switch / unmount
}
```

The store only holds editor-session state. Persistent state lives in react-query (notes list, by-id queries).

### 5.5 React-query hooks (`use-notes.ts`)

- `useNotesQuery(projectId)` → `['notes', projectId]`, `staleTime: 30_000`.
- `useNoteQuery(noteId)` → `['notes', 'detail', noteId]`.
- `useEntitiesQuery(projectId, prefix)` → `['entities', projectId, prefix]`, debounce 150ms, `staleTime: 60_000`.
- `useCreateNote()`, `usePatchNote()`, `useIndexNote()`, `useDeleteNote()` — mutations with optimistic updates on the list cache.

### 5.6 Save flow

- TipTap `onUpdate` → `setDraft` → debounced 2s → `patchMutation.mutate({title, body_markdown, mention_entity_ids})` → on success `markSaved()`.
- Header badge logic:
  - `dirty` → "Unsaved"
  - `patchMutation.isPending` → "Saving…"
  - `note.indexed_at && note.updated_at <= note.indexed_at && !dirty` → "Indexed"
  - `note.indexed_at && note.updated_at > note.indexed_at && !dirty` → "Indexed (stale)"
  - `!note.indexed_at && !dirty` → "Saved"
- "Save & Index" button:
  1. Awaits `patchMutation` if in flight.
  2. Calls `useIndexNote().mutate(noteId)`.
  3. On success, react-query invalidates the notes list and the note detail query.
- Editor blur (`useEffect` on focus state from TipTap): if `dirty`, fire the same Save & Index sequence automatically.
- Route change (note switch / unmount): flush pending PATCH (await it); do NOT auto-index; reset store.

### 5.7 Mention extension

`note-mention-extension.ts`:

```ts
import Mention from "@tiptap/extension-mention";
import { renderMentionSuggestion } from "./note-mention-list";

export const buildMention = (projectId: string) =>
  Mention.configure({
    HTMLAttributes: { class: "mention-chip" },
    suggestion: {
      char: "@",
      items: ({ query }) => fetchEntities(projectId, query),  // returns Promise<Entity[]>
      render: renderMentionSuggestion,
    },
  });
```

`note-mention-list.tsx` is a Tippy.js-positioned popover with keyboard nav (Up/Down/Enter). Each suggestion shows the entity name + a small type badge.

Mention nodes in TipTap have `attrs: { id: entity.id, label: entity.name }`. Inline rendering: a green pill matching the Plan 5 Entity color (`bg-emerald-500/20 text-emerald-700`).

`extractMentionIds(doc: TipTap.Doc) → Set<string>`: traverses the doc tree, collects `attrs.id` from every `mention` node.

### 5.8 Markdown round-trip

- On note load (`useNoteQuery` data arrives): `editor.commands.setContent(markedHTML)` where `markedHTML = marked.parse(note.body_markdown)`.
- On editor `onUpdate`: `editor.getHTML()` → `turndown.turndown(html)` → `body_markdown` written to store.
- Mention nodes need a Turndown rule that emits the original mention syntax (`@[Llama 3](entity:uuid)` or similar) so that re-loading round-trips correctly. **Decision:** TipTap's mention HTML is `<span data-type="mention" data-id="..." data-label="...">@Llama 3</span>`. We add a Turndown rule that emits `@Llama 3` (the human-readable form) and store the entity id list separately on the row (the `mention_entity_ids` column). This means the markdown body is human-readable; the entity-id list is the canonical source for graph edges.
- On reload, the FE walks the doc to reconstruct mention nodes by matching `@Word` against the stored `mention_entity_ids` + entity names from a one-shot `useEntitiesQuery(projectId, '')` call. **Caveat:** if the user manually edits `@Word` in a way that doesn't match a known entity, the mention chip becomes plain text — that's an acceptable degradation (the user can re-tag).

### 5.9 Visuals

- Title input: 24px font, no border, placeholder "Untitled".
- Editor: 16px, comfortable line-height, max-width 720px centered.
- Mention chip: green pill, 0.875em font, no padding gap.
- Save status badge: small pill in the header, color-coded (Saved=neutral, Indexed=blue, Indexed (stale)=amber, Saving=pulsing).
- Empty state: centered "Select a note or create a new one" with the "+ New note" button.

### 5.10 Types

`apps/web/src/lib/api/notes.ts`:

```ts
export interface Note {
  id: string;
  user_id: string;
  project_id: string;
  knowledge_node_id: string | null;
  title: string;
  body_markdown: string;
  mention_entity_ids: string[];
  indexed_at: string | null;
  created_at: string;
  updated_at: string;
}
export interface NoteListItem {
  id: string;
  title: string;
  updated_at: string;
  indexed_at: string | null;
}
export interface CreateNoteArgs { project_id: string; title?: string; body_markdown?: string }
export interface PatchNoteArgs {
  title?: string;
  body_markdown?: string;
  mention_entity_ids?: string[];
}
// fetchNotes, fetchNote, createNote, patchNote, indexNote, deleteNote
```

`apps/web/src/lib/api/entities.ts`:

```ts
export interface Entity {
  id: string;
  name: string;
  entity_type: string | null;
  pagerank: number;
}
export async function fetchEntities(
  projectId: string,
  prefix: string,
  limit?: number,
): Promise<Entity[]> { ... }
```

## 6. Testing

### 6.1 Backend

`apps/api/atlas_api/tests/test_notes_router.py`:
- POST creates row with default title "Untitled", `knowledge_node_id=null`, `indexed_at=null`.
- PATCH updates fields; mention_entity_ids round-trips through the column.
- GET `?project_id=` lists in `updated_at DESC` order.
- POST `/index`:
  - First-time index: `cleanup_document` NOT called (no prior knowledge_node_id); `IngestionService.ingest(source_type="note")` called; `tag_note` called with the row's mention_entity_ids; `notes.indexed_at`, `notes.knowledge_node_id` updated.
  - Re-index: `cleanup_document` called with the previous knowledge_node_id BEFORE the new ingest.
  - Empty mention list: `tag_note` not called (short-circuit in the method).
- DELETE: calls `cleanup_document` if knowledge_node_id was set, deletes the row.
- 404 on unknown id; 422 on bad payload; 403 if project_id doesn't belong to user.

`apps/api/atlas_api/tests/test_knowledge_entities_endpoint.py`:
- Happy path with fake `GraphStore.list_entities` returns ordered list.
- Empty prefix returns top-N by PageRank (verified via mock kwargs).
- Unknown project: 404.
- `GraphUnavailableError`: 503 with `detail="graph_unavailable"`.

`packages/atlas-graph/atlas_graph/tests/test_tag_note.py`:
- `tag_note` against fake driver: asserts the right Cypher with the right kwargs.
- Empty entity_ids: short-circuits, no Cypher run.

`packages/atlas-graph/atlas_graph/tests/test_list_entities.py`:
- `list_entities` runs the right Cypher with project_id, prefix (lowercased on the server), limit.
- Empty prefix: passes empty string through.

`packages/atlas-knowledge/atlas_knowledge/tests/test_ingestion_note.py`:
- `IngestionService.ingest(source_type="note", parsed=parse_markdown(body, title))` produces a Document row with `metadata.source_type="note"` and writes chunks to Chroma + Neo4j (via existing fakes). Verifies the new source_type doesn't trip any branch.

`packages/atlas-graph/atlas_graph/tests/test_subgraph_integration.py` (real Neo4j, opt-in):
- Add a test: create a note, index it with one mention entity, assert (:Document {type:'note'}) node exists, has chunks via HAS_CHUNK, has a `(:Document)-[:TAGGED_WITH]->(:Entity)` edge.

### 6.2 Frontend

Vitest + React Testing Library:
- `notes-store.test.ts` — `setDraft` updates fields and dirty flag; `markSaved` clears dirty; `reset` returns to initial.
- `note-list-rail.test.tsx` — renders list; "+ New" fires create mutation; empty state shows when list is empty; stale dot shows when `updated_at > indexed_at`.
- `note-editor.test.tsx` — title input round-trip; header badge transitions Saved → Saving → Saved → Indexed; "Save & Index" awaits pending PATCH before firing index; delete confirms before firing.
- `note-mention-extension.test.ts` — given a TipTap doc fragment with mention nodes, `extractMentionIds(doc)` returns the right Set.
- `use-notes.test.tsx` — react-query hooks fire the right URLs with the right bodies (mocked fetch).

**TipTap content-editable rendering not unit-tested in v1.** Same gap as Plan 5's Cytoscape canvas; document the gap, smoke-test manually before merge.

## 7. Acceptance criteria

1. Opening `/projects/:id/notes` shows the rail (empty if no notes, with "+ New note" prominently). Clicking "+ New note" creates a row, navigates to `/projects/:id/notes/:newId`, opens the editor.
2. Typing in the editor: after 2s of idle, the body is persisted to Postgres (header shows "Saved").
3. Typing `@` opens an autocomplete dropdown sourced from existing entities (top-N by PageRank when prefix empty); selecting one inserts a green mention chip.
4. Clicking "Save & Index" runs the full pipeline (chunker + embedder + NER + PageRank + graph) and the header transitions to "Indexed". The note's chunks become findable via the chat search (`/api/v1/knowledge/search` returns them when the body matches).
5. Re-opening `/projects/:id/explorer` after indexing shows the note as a Document-type node (blue), with TAGGED_WITH edges to mentioned entities and REFERENCES edges from its chunks to NER-detected entities.
6. Editing an indexed note → header shows "Indexed (stale)"; clicking Save & Index again re-runs cleanup + ingest cleanly (no duplicate chunks or edges).
7. Deleting a note removes it from Postgres, Chroma, and Neo4j; chat search no longer returns its chunks.

## 8. Risks and open items

- **TipTap markdown round-trip fidelity.** TipTap is HTML-native, not markdown-native. Round-tripping markdown ↔ HTML via `marked` + `turndown` works for prose + lists + code blocks but tables and complex formatting may degrade. Acceptable for v1 (notes are personal context, not authoring); revisit if it bites.
- **Mention reconstruction on reload.** Storing only `mention_entity_ids` (not positions) means on reload we match `@Word` text against entity names to re-render chips. Edits that break the match leave plain text behind. The user can re-tag; not a v1 blocker.
- **Re-index on busy notes.** Each Save & Index runs NER (LM Studio call) + PageRank. A user who hits Save & Index every minute compounds cost. Mitigation: the explicit-button design forces user intent, so we don't expect heavy re-index traffic. If it bites, add a "Save without indexing" option.
- **Mention column array migration.** Adding `mention_entity_ids UUID[]` is a single ALTER TABLE ADD COLUMN with a default — fast on small tables. At scale this would need to be a separate column-add migration.
- **Empty body indexing.** Indexing an empty markdown body produces no chunks (existing code path). The (:Document) node is still created with `type='note'`; empty-content notes are findable in the explorer but not via search. Acceptable.
- **Concurrent edits.** Single-user, but if Matt opens the same note in two tabs, last-write-wins on the PATCH. No optimistic locking. Acceptable for v1.

## 9. Definition of Done

- [ ] `notes` table migration applied; `NoteORM` and Pydantic models in atlas-core.
- [ ] `KnowledgeNodeType.NOTE` added; `IngestionService.ingest(source_type="note")` works against fakes.
- [ ] `/api/v1/notes/*` endpoints implemented and tested.
- [ ] `/api/v1/knowledge/entities` endpoint implemented and tested.
- [ ] `GraphStore.tag_note` and `GraphStore.list_entities` implemented and tested.
- [ ] `/projects/:id/notes` route renders for a real project.
- [ ] All seven acceptance criteria pass on a manual smoke run.
- [ ] Backend unit + degraded tests pass; opt-in `slow` real-Neo4j acceptance test passes.
- [ ] Frontend unit tests pass.
- [ ] Code review approved (per workflow: Haiku implementer + Sonnet reviewers).
