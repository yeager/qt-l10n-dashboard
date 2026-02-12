# Qt Translation Infrastructure: Gerrit + Weblate Integration

**Design Document — v0.1 (2026-02-12)**
**Status:** Draft for review
**Context:** Qt Translation Dashboard project
**Stakeholders:** Oswald Buddenhagen, Qt l10n maintainers

---

## 1. Problem Statement

Qt's current translation workflow suffers from several issues:

- **Duplicate Gerrit changes** — new changes are created instead of reusing open ones
- **One-way flow** — translations pushed directly to Gerrit don't flow back to translators
- **Lost contributions** — translations submitted via Gerrit can get stuck and never reach the translation database
- **Poor review UX** — Gerrit is bad at reviewing large translation diffs

Weblate can serve as the translation database and review interface, with bidirectional sync to Gerrit.

---

## 2. Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Qt Gerrit                                │
│  codereview.qt-project.org                                      │
│                                                                 │
│  ┌──────────┐    ┌──────────────┐    ┌───────────────────────┐  │
│  │ Merged   │    │ Open Changes │    │ Stream Events API     │  │
│  │ Branches │    │ (reused)     │    │ (SSH stream-events)   │  │
│  └────┬─────┘    └──────┬───────┘    └───────────┬───────────┘  │
│       │                 │                        │              │
└───────┼─────────────────┼────────────────────────┼──────────────┘
        │                 │                        │
        │                 ▼                        ▼
        │        ┌────────────────┐    ┌───────────────────────┐
        │        │  Sync Service  │◄───│   Gerrit Watcher      │
        │        │  (orchestrator)│    │   (stream-events      │
        │        │                │    │    listener)           │
        │        └───────┬────────┘    └───────────────────────┘
        │                │
        ▼                ▼
┌─────────────────────────────────────────────────────────────────┐
│                        Weblate                                  │
│  (self-hosted, Gerrit VCS backend)                              │
│                                                                 │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────────┐ │
│  │ Project:    │  │ Components  │  │ REST API                │ │
│  │ Qt 6.x     │  │ per module  │  │ /api/components/        │ │
│  │ Qt 5.15    │  │ qtbase,     │  │ /api/translations/      │ │
│  │            │  │ qtdecl, ... │  │ /api/units/             │ │
│  └─────────────┘  └─────────────┘  └─────────────────────────┘ │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
        ▲                │
        │                ▼
┌───────────────┐  ┌──────────────┐
│  publish.sh   │  │  Dashboard   │
│  (enhanced)   │  │  (web UI)    │
└───────────────┘  └──────────────┘
```

---

## 3. Weblate Capabilities (Research Findings)

### 3.1 Native Gerrit Support

Weblate has **built-in Gerrit VCS backend** (`gerrit`). Key behaviors:

- Uses `git review` to push changes (creates Gerrit reviews instead of direct pushes)
- Pulls from the Git repository normally
- Requires `git-review` package and `.gitreview` config in the repo
- Supports SSH key-based auth to Gerrit

**Implication:** Weblate can natively push translation changes as Gerrit reviews. However, it creates *new* changes by default — it does not inherently reuse open changes. This is the core gap we need to bridge.

### 3.2 Branch/Component Model

- **Components** map to file masks within a repo (e.g., `translations/qtbase/*.ts`)
- **"Additional branch"** — create a component that tracks a different branch of the same repo, sharing the VCS clone via `weblate://` internal URLs
- Components can be organized into **categories** within a project
- Each component independently tracks its own branch

**For competing translations:** Weblate doesn't natively support "sub-versions" within a branch. Options:
1. Use separate Weblate components per Gerrit change (heavy, doesn't scale)
2. Use Weblate's **suggestion system** — competing translations become suggestions that reviewers pick between
3. Use Weblate **string-level comments/reviews** for discussion

### 3.3 API Capabilities

Full REST API (`/api/`) covering:
- `POST /api/components/{id}/file/` — upload translation files
- `POST /api/translations/{id}/file/` — upload per-language
- `POST /api/components/{id}/repository/` — trigger pull/push/reset
- `GET /api/units/` — query individual translation strings
- Lock/unlock components (prevent edits during sync)
- `wlc` CLI client wraps this API

### 3.4 Conflict Avoidance

Weblate docs recommend a lock-based workflow:
1. `wlc lock` — prevent Weblate edits
2. `wlc push` — flush pending Weblate changes upstream
3. Do external changes (template updates, etc.)
4. `wlc pull` — tell Weblate to pull
5. `wlc unlock`

---

## 4. Component Breakdown

### 4.1 Gerrit Watcher

**Purpose:** Monitor Gerrit for translation-related changes and trigger syncs.

**Based on:** `qtrepotools//git-hooks/gerrit-bot` pattern (Oswald's recommendation).

**Implementation:**

```
gerrit-watcher/
├── watcher.py          # Main event loop
├── gerrit_client.py    # Gerrit REST + SSH stream API
├── change_tracker.py   # Track open changes per target
└── config.yaml         # Watched repos, branch patterns
```

**Event sources** (Gerrit stream-events via SSH):
- `patchset-created` — new translation change uploaded
- `change-merged` — translation change merged
- `comment-added` — review activity

**Key logic:**

```python
# Pseudocode for the watcher
ssh gerrit stream-events |
  filter(project in WATCHED_REPOS) |
  filter(path matches "translations/**/*.ts") |
  for each event:
    if patchset-created:
      sync_change_to_weblate(change)
    if change-merged:
      trigger_weblate_pull(component)
```

### 4.2 Change Reuse Registry

**Core requirement from Oswald: don't create duplicate Gerrit changes.**

Maintain a registry mapping `(repo, branch, language/module)` → `change_id`:

```yaml
# change_registry.yaml (or SQLite DB)
qt/qttranslations:
  dev:
    change_id: "I4a8b2c..."
    change_number: 567890
    last_updated: "2026-02-10T14:30:00Z"
  6.8:
    change_id: "I7d3e1f..."
    change_number: 567456
    last_updated: "2026-02-09T10:15:00Z"
```

**Push workflow:**
1. Check registry for existing open change for `(repo, branch)`
2. If exists: `git review` with `--replace <change_id>` (amend existing change)
3. If not (or change was merged/abandoned): create new, update registry
4. On change-merged event: clear registry entry

**Gerrit REST API calls needed:**
- `GET /changes/?q=project:qt/qttranslations+status:open+topic:weblate-sync` — find open changes
- `POST /changes/{id}/revisions/current/review` — add comments
- Change topic/hashtag to make weblate-managed changes identifiable

### 4.3 Enhanced publish.sh

**Current:** Pushes updated templates (`.pot`/`.ts` source files) to repos.

**Enhanced workflow:**

```bash
#!/bin/bash
# publish.sh - enhanced

# 1. Generate/update templates as before
update_templates "$MODULE"

# 2. Lock Weblate to prevent concurrent edits
wlc lock "qt/$MODULE"

# 3. Push templates to Weblate
wlc push "qt/$MODULE"  # flush any pending Weblate changes first

# 4. Upload updated templates to Weblate
for ts_file in translations/$MODULE/*.ts; do
    lang=$(extract_lang "$ts_file")
    curl -X POST \
        -H "Authorization: Token $WEBLATE_TOKEN" \
        -F "file=@$ts_file" \
        -F "method=replace" \
        "https://weblate.qt.io/api/translations/qt/$MODULE/$lang/file/"
done

# 5. Also push already-integrated translations to Weblate
# This ensures Weblate has the latest merged state
wlc pull "qt/$MODULE"

# 6. Unlock
wlc unlock "qt/$MODULE"
```

### 4.4 Gerrit → Weblate Sync

When translations are pushed directly to Gerrit (not through Weblate):

1. **Watcher** detects `patchset-created` for translation files
2. Fetches the change's diff/files via Gerrit REST API
3. Options for ingestion:
   - **Option A (recommended):** Upload changed `.ts` files to Weblate via file upload API
   - **Option B:** If Weblate component tracks the Gerrit ref, trigger a pull from that ref

**Benefits (per Oswald):**
- Translators can review changes in Weblate's UI (much better than Gerrit for large diffs)
- Contributions don't get stuck in Gerrit limbo — they're visible in Weblate

**For unmerged changes (open reviews):** Import as Weblate *suggestions* rather than accepted translations. This preserves the review workflow.

### 4.5 Weblate → Gerrit Push

Leveraging Weblate's native Gerrit backend:

1. Weblate commits translations locally (lazy commit, configurable interval)
2. Weblate pushes via `git review` → creates/updates Gerrit change
3. **Custom addon or post-push hook** checks the change registry and amends existing change if one is open

**Alternative approach:** Don't use Weblate's built-in Gerrit push. Instead:
1. Weblate pushes to a staging branch (e.g., `weblate-staging/dev`)
2. Sync service picks up the push, cherry-picks into existing Gerrit change
3. More control over change reuse, commit message format, etc.

**Recommendation:** Use the staging branch approach for maximum control over Gerrit change lifecycle.

---

## 5. Data Flow Diagrams

### 5.1 Template Update Flow

```
Developer updates source strings
         │
         ▼
    publish.sh runs
         │
         ├──► Update .ts templates in repo
         │
         ├──► Lock Weblate component
         │
         ├──► Upload templates + integrated translations to Weblate API
         │
         ├──► Trigger Weblate pull
         │
         └──► Unlock Weblate component
```

### 5.2 Translation Submission via Weblate

```
Translator edits in Weblate
         │
         ▼
  Weblate auto-commits (lazy commit)
         │
         ▼
  Weblate pushes to staging branch
         │
         ▼
  Sync service detects push
         │
         ├──► Check change registry for open change
         │         │
         │    ┌────┴────┐
         │    │ Exists  │ Does not exist
         │    ▼         ▼
         │  Amend     Create new
         │  change    Gerrit change
         │    │         │
         │    └────┬────┘
         │         │
         └──► Update registry
```

### 5.3 Direct Gerrit Submission (External Contributor)

```
Contributor pushes translation change to Gerrit
         │
         ▼
  Watcher detects patchset-created
         │
         ├──► Fetch changed .ts files from Gerrit REST API
         │
         ├──► Upload to Weblate as suggestions (if change is unmerged)
         │    OR as accepted translations (if change is merged)
         │
         └──► Link Gerrit change URL in Weblate comment
```

---

## 6. Competing Translations Strategy

**Problem:** Multiple translators submit competing translations for the same strings via Gerrit.

### 6.1 Weblate's Native Mechanisms

- **Suggestions:** Any user can suggest alternative translations. Reviewers accept/reject.
- **Review workflow:** Enable review mode — translations need approval before becoming "approved"
- **String history:** Full history of changes per string, with attribution
- **Voting:** Not built-in, but suggestions can be discussed via comments

### 6.2 Proposed Approach

1. **First Gerrit submission** for a target → imported as Weblate translations (primary)
2. **Competing submissions** → imported as Weblate **suggestions** with a note linking the Gerrit change
3. Reviewers compare in Weblate UI (far superior to Gerrit for this)
4. Accepted suggestion → becomes the translation, sync service updates the Gerrit change
5. Rejected alternatives → Gerrit changes get a comment explaining the decision

### 6.3 Branch-Based Alternative (Oswald's Sub-Versions Idea)

Weblate supports multiple components tracking different branches. We *could*:

- Create per-contributor branches: `translations/contributor-a/dev`, `translations/contributor-b/dev`
- Each gets its own Weblate component
- Review/merge happens at the Weblate level

**Assessment:** This is heavy. The suggestions-based approach is simpler and uses Weblate's existing review workflow. Reserve branch-based approach for large-scale competing efforts (e.g., entirely new language with multiple teams).

---

## 7. Weblate Component Model for Qt

```
Project: Qt
├── Category: Qt 6.8 (dev)
│   ├── Component: qtbase         (branch: dev, mask: translations/qtbase/*.ts)
│   ├── Component: qtdeclarative  (branch: dev, weblate://qt/qtbase)
│   ├── Component: qttools        (branch: dev, weblate://qt/qtbase)
│   └── ...
├── Category: Qt 6.7
│   ├── Component: qtbase         (branch: 6.7, mask: translations/qtbase/*.ts)
│   └── ...
└── Category: Qt 5.15 (LTS)
    └── ...
```

- **Shared VCS via `weblate://`** — components in the same category share the repo clone
- **Per-branch categories** map to Qt release branches
- **VCS backend:** `gerrit` (for components that push) or plain `git` (for read-only + external sync service push)

---

## 8. Open Questions & Decisions Needed

| # | Question | Options | Recommendation |
|---|----------|---------|----------------|
| 1 | **Push mechanism** | Weblate native Gerrit push vs. staging branch + sync service | Staging branch (more control over change reuse) |
| 2 | **Weblate hosting** | Self-hosted vs. hosted.weblate.org | Self-hosted (need Gerrit SSH access, custom hooks) |
| 3 | **Competing translations** | Suggestions vs. branch-per-contributor | Suggestions (simpler, covers 90% of cases) |
| 4 | **Change registry storage** | File (YAML) vs. SQLite vs. Gerrit topics/hashtags | Gerrit hashtags (no external state needed) |
| 5 | **Watcher deployment** | Standalone service vs. Gerrit plugin | Standalone (like gerrit-bot pattern) |
| 6 | **Granularity** | One Gerrit change per module per branch, or one per language? | Per module per branch (matches current workflow) |
| 7 | **Review workflow** | Weblate review mode (approved/needs-review) enabled? | Yes — maps well to Gerrit +2/+1 model |
| 8 | **Who merges?** | Weblate auto-merge on approval, or human merges in Gerrit? | Human merges in Gerrit (existing workflow) |

---

## 9. Phased Implementation Plan

### Phase 1: Foundation (Weeks 1-3)

- [ ] Deploy self-hosted Weblate instance with Gerrit VCS backend
- [ ] Configure SSH key access from Weblate → Qt Gerrit
- [ ] Create initial project/component structure for one module (e.g., `qtbase/dev`)
- [ ] Import existing translations into Weblate
- [ ] Validate Weblate ↔ Gerrit round-trip (manual push/pull)

### Phase 2: Gerrit Watcher (Weeks 4-6)

- [ ] Implement watcher based on `gerrit-bot` pattern (stream-events listener)
- [ ] Filter for translation file changes in watched repos
- [ ] On `change-merged`: trigger Weblate pull for affected component
- [ ] On `patchset-created`: log and notify (don't sync yet)
- [ ] Deploy as systemd service alongside Weblate

### Phase 3: Bidirectional Sync (Weeks 7-10)

- [ ] Implement change reuse registry (Gerrit hashtag-based)
- [ ] Build sync service: Weblate staging branch → Gerrit (with change reuse)
- [ ] Implement Gerrit → Weblate sync for direct submissions (as suggestions)
- [ ] Enhance `publish.sh` to push templates + translations to Weblate API
- [ ] Lock/unlock workflow in publish.sh

### Phase 4: Competing Translations & Polish (Weeks 11-13)

- [ ] Enable Weblate review workflow
- [ ] Implement competing translation → suggestion import logic
- [ ] Add Gerrit comment linking to Weblate review for large diffs
- [ ] Dashboard integration (show Weblate stats, links)
- [ ] Documentation and handoff

### Phase 5: Scale Out (Week 14+)

- [ ] Expand to all Qt modules
- [ ] Expand to all active branches (dev, 6.8, 6.7, 5.15)
- [ ] Monitor and tune lazy commit intervals, sync frequency
- [ ] Evaluate if branch-based competing translations are needed for any language

---

## 10. Technical Dependencies

| Dependency | Purpose | Version |
|-----------|---------|---------|
| Weblate | Translation management | 5.x (self-hosted, Docker) |
| git-review | Gerrit push from Weblate | Latest |
| Python 3.10+ | Watcher + sync service | — |
| Gerrit REST API | Change management, file fetch | Qt Gerrit instance |
| Gerrit SSH | stream-events, git access | — |
| `wlc` | Weblate CLI client | Latest |

---

## 11. References

- [Weblate VCS docs — Gerrit backend](https://docs.weblate.org/en/latest/vcs.html#gerrit)
- [Weblate continuous localization](https://docs.weblate.org/en/latest/admin/continuous.html)
- [Weblate REST API](https://docs.weblate.org/en/latest/api.html)
- [Gerrit stream-events](https://gerrit-review.googlesource.com/Documentation/cmd-stream-events.html)
- [Gerrit REST API](https://gerrit-review.googlesource.com/Documentation/rest-api.html)
- [`qtrepotools//git-hooks/gerrit-bot`](https://codereview.qt-project.org/admin/repos/qtrepotools) — template for watcher
- [Weblate suggestions workflow](https://docs.weblate.org/en/latest/user/translating.html#suggestions)
