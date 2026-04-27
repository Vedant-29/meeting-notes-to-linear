# notes-to-linear

A small file-watcher daemon that turns messy markdown meeting notes into
clean, well-structured Linear issues. Drop a `.md` file in a watched
folder; within a few seconds, every actionable item appears in Linear
with a sensible title, priority, description, and labels. Nothing to
click, nothing to clean up.

> **Project status**
> Built end-to-end and verified live against a real Linear workspace.
> The standup fixture in `evals/fixtures/01_standup.md` produced 6 well
> formed issues (`TES-5` through `TES-10`) and the chitchat fixture
> correctly produced **zero** — the regression check for
> hallucination. Designed and reviewed via the gstack workflow:
> `/office-hours` produced the design doc, `/plan-eng-review` locked
> the architecture and tests.

---

## Table of contents

1. [What this builds](#what-this-builds)
2. [Architecture](#architecture)
3. [Key design decisions](#key-design-decisions)
4. [Failure modes](#failure-modes)
5. [Setup](#setup)
6. [Running and testing](#running-and-testing)
7. [Eval harness](#eval-harness)
8. [Repository tour](#repository-tour)
9. [Deferred work](#deferred-work)
10. [Credits](#credits)

---

## What this builds

A daemon that watches a folder for new or modified `.md` files. On
each change, it extracts a list of actionable tickets from the note
and creates one Linear issue per ticket. The "magic" is comprehension
quality: the prompt and schema are tuned so that the LLM produces
issues a teammate could pick up tomorrow with no extra context.

Three concrete properties make this more than a one-shot script:

- **Schema-first output.** A tight Pydantic ticket model mirrors
  Linear's `IssueCreateInput` exactly. The LLM is forced to return a
  shape that Linear will accept — no surprise 4xx errors at write
  time.
- **Eval harness with a no-op fixture.** Five fixtures with `count_min`,
  `count_max`, and `must_mention` assertions. One fixture is pure
  chitchat with `count_min=count_max=0` — the regression catch for
  prompt drift turning the extractor into a hallucination machine.
- **Idempotent batches with sidecar logs.** On full success, write a
  sibling `.processed` marker. On partial Linear failure, write a
  `.failed` marker plus a `.failed.json` listing which tickets
  succeeded (with Linear IDs and URLs) and which failed. The watcher
  never re-processes a marked file.

## Architecture

Layered, single-process daemon. Three modules, one orchestrator.

```
                   ┌──────────────┐
   .md file save → │  watchdog    │ on_created / on_modified / on_moved
                   │  Observer    │ (often all 3 fire on a single editor save)
                   └──────┬───────┘
                          │ each event resets a 500ms timer for the path
                          ▼
                   ┌──────────────┐    success
                   │  main.py     │──────────────► <note>.md.processed
                   │  (orchestr.) │
                   └──────┬───────┘    partial fail
                          │            ──────────► <note>.md.failed
                          ▼                       + <note>.md.failed.json
                   ┌──────────────┐
                   │ extractor.py │◄─── prompts/extract_tickets.md
                   │  (Anthropic) │     schemas/ticket.py (forced tool)
                   └──────┬───────┘
                          │ List[Ticket]   (empty list = no-op, valid)
                          ▼
                   ┌──────────────┐
                   │linear_client │── per-ticket: Linear GraphQL issueCreate
                   │              │    on 5xx: retry 3x exp backoff
                   └──────────────┘    on 4xx / GraphQL error: raise (no retry)
```

**Module responsibilities:**

| Module | Responsibility |
|---|---|
| `src/watcher.py` | `watchdog` observer + per-path `threading.Timer` debounce. Coalesces atomic-rename save events into one process call per file. Skips files that already have a `.processed` sibling. |
| `src/extractor.py` | Anthropic SDK call with forced tool use. Tool schema mirrors `Ticket`. Validates the tool input through Pydantic before returning. |
| `src/linear_client.py` | Linear GraphQL client. Resolves team key (`TES`) → UUID at startup. Maps label names → IDs (creating missing labels). Retries 5xx, raises on 4xx. |
| `src/main.py` | Wires the three together. Owns the `.processed` / `.failed` / `.failed.json` lifecycle. Catches every exception so the watcher stays alive. |
| `schemas/ticket.py` | Pydantic `Ticket` model. Enforces priority enum (0-4), title length (≤256), label-name list, optional estimate and project. |
| `prompts/extract_tickets.md` | System prompt. Version-controlled separately from code so prompt iterations are reviewable diffs. |

## Key design decisions

The full reasoning lives in the design doc and engineering review;
the short version is below. Each decision was made deliberately, not
by default.

| Decision | Choice | Why |
|---|---|---|
| Trigger | File watcher (not button, not live-as-you-type) | Visible demo trigger ("drag the file in"); decouples capture from processing. |
| Wow moment | Magic / comprehension | Plays to LLM strength; demo wins on output quality, not plumbing. |
| LLM output | Forced tool use with Pydantic-mirrored schema | LLM cannot produce values Linear will reject. Fail at parse time, not write time. |
| Debounce | Per-path `{path: threading.Timer}` with 500ms quiet window | Editors atomic-rename on save (3 events per save). Per-path means dropping 5 files at once still fires 5 separate process calls. |
| Partial failure | Per-ticket retry + sidecar JSON | Linear has no transactions. Sidecar surfaces partial state without silent loss. |
| Eval coverage | `count_min` + `count_max` + `must_mention` + a no-op fixture | Shape-only assertions miss two common prompt regressions: silent zero output and hallucination from chitchat. |
| Schema rigor | Tight Pydantic schema mirroring Linear's API | Priority is `Literal[0,1,2,3,4]`, title `max_length=256`, labels are name strings the client maps to IDs. |
| Rate ceiling | **Deliberately not implemented in v1** | Single-user learning project, conscious risk. Captured in `TODOS.md` with revisit triggers. |

## Failure modes

Every codepath in the system has a planned test, error handling, and
a visible signal to the operator (log line, marker file, or sidecar
JSON). Critical gaps: zero.

| Codepath | Realistic failure | Test | Error handling | Visible? |
|---|---|---|---|---|
| Watcher | Editor atomic-rename fires 3 events on one save | `test_watcher.py` | Per-path debounce | Silent correctness |
| Extractor | Anthropic timeout / 5xx | `test_main.py` (mocked) | Raises `ExtractorError` → `.failed` + sidecar | Yes |
| Extractor | LLM returns malformed structure despite forced tool | Pydantic validation | Logs raw payload, file marked `.failed` | Yes |
| Linear client | Linear 5xx (transient) | `test_linear_client.py` | Retry 3x exp backoff | No (silently recovered) |
| Linear client | Linear 4xx (auth, validation) | `test_linear_client.py` | Logs + raises, file marked `.failed` | Yes |
| Linear client | Permanent fail on ticket 3 of 5 | `test_main.py` | Sidecar JSON with succeeded IDs and failed payloads | Yes |

## Setup

### Prerequisites

- **Python 3.11+**
- An **Anthropic API key** from https://console.anthropic.com/
- A **Linear personal API key** from https://linear.app/settings/api
  (the personal-key flow, not OAuth)

### 1. Clone and install

```bash
git clone <repo-url>
cd kalpi-gstack-automation
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
```

Open `.env` and fill in four values:

| Variable | What |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key (`sk-ant-...`) |
| `LINEAR_API_KEY` | Your Linear personal API key (`lin_api_...`) |
| `LINEAR_TEAM_ID` | Either the team UUID **or** the short key like `TES`. The client resolves short keys to UUIDs at startup. |
| `INBOX_DIR` | Absolute path of the folder to watch. Create it if it doesn't exist. |

To find your team key, run:

```bash
python -m src.linear_client list-teams
```

That prints a `KEY  UUID  NAME` table for every team your API key
can see — useful as a sanity check before the first run.

### 3. Run

```bash
python -m src.main
```

The daemon starts, resolves the Linear team, and begins watching
`INBOX_DIR` for `.md` files. Drop one in. Within a few seconds, well
formed Linear issues appear and the file gets a `.processed` sibling.

Stop with `Ctrl+C`. The watcher shuts down cleanly.

## Running and testing

| Command | What it does |
|---|---|
| `python -m src.main` | Run the daemon. Watches `INBOX_DIR`. |
| `python -m src.linear_client list-teams` | Sanity-check API key and find team UUIDs. |
| `pytest` | Run unit tests. 33 tests, no network, ~1 second. |
| `pytest --run-evals` | Also run the LLM eval suite against the 5 fixtures. Costs ~$0.05 on Sonnet 4.6 and requires `ANTHROPIC_API_KEY`. |
| `pytest -k watcher` | Run only the watcher tests. |

The unit tests use `respx` to mock `httpx`, `unittest.mock` to mock
the extractor and Linear client at boundaries, and `tempfile` for
filesystem isolation. None of them touch the network.

## Eval harness

The eval suite is the load-bearing piece. Each fixture is a pair:

```
evals/fixtures/01_standup.md            ← messy notes
evals/fixtures/01_standup.expected.json ← {count_min, count_max, must_mention}
```

For each fixture the test:

1. Loads the `.md` and runs `Extractor.extract()` against the live API.
2. Asserts `count_min ≤ len(tickets) ≤ count_max`.
3. For each `must_mention` keyword, asserts at least one ticket's
   title or description contains it (case-insensitive).

The five shipping fixtures cover the full quality surface:

| Fixture | Expected count | What it checks |
|---|---|---|
| `01_standup.md` | 4-7 | Multi-person status with mixed urgencies |
| `02_design_review.md` | 5-9 | Long-form notes with dense action items |
| `03_one_on_one.md` | 3-6 | Career conversation with subtle action items |
| `04_chitchat_noop.md` | **0** | Pure social text — must produce zero tickets (regression catch for hallucination) |
| `05_mixed_signal.md` | 2-4 | Action items buried in stream-of-consciousness chatter |

Adding a new fixture is two files (`name.md` + `name.expected.json`)
— no test code change required.

## Deferred work

Captured in [`TODOS.md`](./TODOS.md) with the rationale for each:

1. **Cross-run note deduplication** — re-processing an edited note
   currently creates duplicate tickets. v2 needs a hash → ticket-IDs
   store and update-vs-create logic.
2. **Rate-limit / cost ceiling** — no governor on file processing in
   v1. A 100-file accidental drop would cost real money. Conscious
   risk; revisit triggers documented.
3. **Update existing tickets when a note is edited** — closely
   related to the dedup work; probably solved together.

The reasoning for *not* doing these in v1 (the boil-the-lake call
ended at "evals + tight schema + sidecar JSON") is in the design doc
and the eng review.

## Credits

- Built using the [**gstack**](https://garryslist.org/) workflow:
  `/office-hours` produced the structured design doc,
  `/plan-eng-review` ran the architecture and test review with
  decision-by-decision tradeoffs.
- [**Anthropic Python SDK**](https://github.com/anthropics/anthropic-sdk-python)
  for the structured-output (`tools` + `tool_choice`) pattern.
- [**watchdog**](https://github.com/gorakhargosh/watchdog) for the
  cross-platform file event observer.
- [**Linear GraphQL API**](https://developers.linear.app/) — clean,
  well-documented, and the personal-API-key flow is the single best
  thing that made this project a one-day build instead of a one-week
  build.
