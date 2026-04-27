# TODOS

Captured during /plan-eng-review on 2026-04-27. These are deferred from v1, not forgotten.

## TODO 1: Cross-run note deduplication

**What:** Hash note content on processing; on re-process, detect if a note overlaps significantly with a previously-processed note and either skip or update existing tickets instead of creating new ones.

**Why:** v1 marks each `.md` file `.processed` and never touches it again. If you edit a note on Wednesday after processing it on Monday, you currently get a fresh batch of mostly-duplicate tickets. After a week of use, Linear gets messy.

**Pros:**
- Cleaner Linear over time.
- Enables the "I edited my notes" use case naturally.

**Cons:**
- Needs a SQLite (or JSON) store of `{file_hash: [linear_ticket_ids]}`.
- Fuzzy matching for "this is the same meeting plus one new bullet" is non-trivial.
- Update-vs-create logic adds branches everywhere.

**Context (for future-you):**
- Current `.processed` marker pattern is the v1 escape hatch — it just skips.
- Two design directions: (a) hash-based exact match (skip if identical), (b) embedding-based similarity (update if >80% overlap). (a) is 1 day, (b) is a week.
- Linear has `issueUpdate` mutation; the API supports the operation.

**Depends on / blocked by:** Nothing. Standalone v2 feature.

---

## TODO 2: Rate-limit / cost ceiling on file processing

**What:** Cap how many files the watcher processes per rolling minute (default 5). Skip excess with a warning log.

**Why:** v1 has no governor. Drag a folder of 100 markdown files in by accident → 100 LLM calls + 500+ Linear writes → real bill, possible Linear rate-limit lockout.

**Decision history:** During /plan-eng-review you weighed this and chose "do nothing — trust the user." Valid call for a single-user learning project. Recording it here so the decision doesn't evaporate.

**Triggers to revisit:**
- Sharing the daemon with another user.
- Leaving the daemon running unattended for >1 day.
- First time you accidentally process a folder you didn't mean to.

**Pros:**
- ~10 lines of code (deque of timestamps).
- Hard ceiling on accidental cost and rate-limit incidents.
- Tunable via env var (`MAX_FILES_PER_MIN=5`).

**Cons:**
- Skipped files need user attention (re-drop them).
- Cost-limit is a slight friction the rest of the time.

**Context:** The pattern is a `collections.deque` of timestamps. On each file event, drop entries older than 60s, then check if `len(deque) >= MAX`. If yes, log and skip. If no, append now and process.

**Depends on / blocked by:** Nothing.

---

## TODO 3: Update existing tickets when note is edited

**What:** When a previously-processed note changes, diff the new tickets against the old set and either update existing Linear issues, create new ones for new items, or close ones that disappeared.

**Why:** Notes evolve. A meeting note from Monday gets edited Tuesday with the actual outcomes. v1 only handles "new note" — edits are silently ignored (file already has `.processed` marker).

**Pros:**
- Workflows stay in sync with reality.
- Closes the "I edited my notes" loop properly (fuller than TODO 1's skip).

**Cons:**
- Needs a stable identity for "this ticket corresponds to this bullet" — non-trivial.
- Easiest design: store `{note_path: [linear_ids_with_extracted_titles]}`, then re-extract and fuzzy-match titles. Brittle.
- Better design: ask the LLM to produce ticket diffs given old + new note. Untested, slow, expensive.

**Context:**
- Closely related to TODO 1 (dedup). Probably solved together as v2 of the system.
- Strongly consider a SQLite store of `{file_hash, note_path, [linear_ticket_ids, ticket_titles]}` to enable both.

**Depends on / blocked by:** TODO 1 (would share the same store).
