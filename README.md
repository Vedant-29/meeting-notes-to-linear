# meeting-notes-to-linear

A small Python daemon that watches a folder for markdown meeting notes and turns the action items in them into Linear issues. Drop a `.md` file in the folder and, a few seconds later, each actionable item shows up in Linear with a title, priority, description, and labels.

It has been run end to end against a real Linear workspace. The standup fixture (`evals/fixtures/01_standup.md`) produced 6 issues, and the chitchat fixture correctly produced none.

## How it works

- `src/watcher.py` watches the folder with `watchdog` and debounces each file for 500 ms, since editors often fire several events on one save.
- `src/extractor.py` sends the note to Claude with forced tool use. The tool schema mirrors the Pydantic `Ticket` model in `schemas/ticket.py`, which mirrors Linear's `IssueCreateInput`, so bad output fails at parse time instead of at Linear.
- `src/linear_client.py` creates one issue per ticket over Linear's GraphQL API (`https://api.linear.app/graphql`). It creates missing labels, attaches the issue to a project only if a project with that name already exists in the team, and tries each request up to 3 times on 5xx and network errors. 4xx and GraphQL errors fail at once.
- `src/main.py` writes a `<note>.md.processed` marker on success. If extraction or any ticket fails it writes `<note>.md.failed` plus `<note>.md.failed.json` listing which tickets were created and which were not. Notes with a `.processed` marker are skipped from then on.

The system prompt lives in `prompts/extract_tickets.md`.

## Requirements

- Python 3.11+
- An Anthropic API key and a Linear personal API key (see Services)

## Setup

```sh
git clone https://github.com/Vedant-29/meeting-notes-to-linear.git
cd meeting-notes-to-linear
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env
python -m src.main
```

Fill in `.env` first. The daemon exits if any required variable is missing, if `INBOX_DIR` does not exist, or if a short team key like `TES` is not found in Linear. Otherwise it resolves the team and starts watching `INBOX_DIR`. Drop a `.md` file in it. Stop with `Ctrl+C`.

To find your team key or UUID, run `python -m src.linear_client list-teams`. It prints every team your API key can see.

## Services

You need your own account and key for each. None are included in the repo.

| Service | Used for | Required | Env vars |
|---|---|---|---|
| Anthropic API | Extracting tickets from the note (Claude, forced tool use) | Yes | `ANTHROPIC_API_KEY`, `EXTRACTOR_MODEL` |
| Linear API | Creating issues, labels, and looking up teams and projects | Yes | `LINEAR_API_KEY`, `LINEAR_TEAM_ID` |

### Anthropic

Create an API key in the Anthropic Console (console.anthropic.com > API keys). The account needs billing set up. Each note is one Messages API call with up to 4096 output tokens. `EXTRACTOR_MODEL` picks the model and defaults to `claude-sonnet-4-6`.

### Linear

Create a personal API key in Linear > Settings > API > Personal API keys. The key needs access to the team you set in `LINEAR_TEAM_ID`, and permission to create issues and labels there. Run `python -m src.linear_client list-teams` to check the key and see each team's key and UUID. Use a test team at first, since every processed note creates real issues.

## Environment variables

| Variable | Required | What it is for | Where to get it |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Calls Claude to extract tickets | console.anthropic.com > API keys |
| `LINEAR_API_KEY` | Yes | Creates issues in Linear | Linear > Settings > API > Personal API keys |
| `LINEAR_TEAM_ID` | Yes | Team to create issues in. A UUID or a short key like `TES` | `python -m src.linear_client list-teams` |
| `INBOX_DIR` | Yes | Folder to watch (absolute path) | Any folder you create |
| `EXTRACTOR_MODEL` | No | Model to use. Defaults to `claude-sonnet-4-6` | |
| `LOG_LEVEL` | No | Log level. Defaults to `INFO` | |

## Usage

| Command | What it does |
|---|---|
| `python -m src.main` | Run the daemon |
| `notes-to-linear` | Same as above, installed by `pip install -e .` |
| `python -m src.linear_client list-teams` | Check your API key and list teams |
| `pytest` | Run the unit tests (no network) |
| `pytest --run-evals` | Also run the live LLM evals. Needs `ANTHROPIC_API_KEY` and costs a few cents |

## Evals

Each fixture in `evals/fixtures/` is a pair: a messy note (`01_standup.md`) and its expectations (`01_standup.expected.json` with `count_min`, `count_max`, and `must_mention`). The test runs the extractor against the live API and checks the ticket count and keywords.

| Fixture | Expected tickets | What it checks |
|---|---|---|
| `01_standup.md` | 4 to 7 | Status updates with mixed urgency |
| `02_design_review.md` | 5 to 9 | Long notes with many action items |
| `03_one_on_one.md` | 3 to 6 | Subtle action items in a career chat |
| `04_chitchat_noop.md` | 0 | Pure social talk. Catches hallucinated tickets |
| `05_mixed_signal.md` | 2 to 4 | Action items buried in rambling |

To add a fixture, add a `name.md` and a `name.expected.json`. No test code changes are needed.

## Notes

See [TODOS.md](TODOS.md) for details.

- Only `.md` files directly inside `INBOX_DIR` are watched (not subfolders), and only when they are created, saved, or moved in while the daemon runs. Notes already in the folder at startup are not picked up.
- Edits to a note that has a `.processed` marker are ignored. Edited notes do not update existing tickets.
- There is no cross-run dedup. Deleting a `.processed` marker, or dropping a copy of a note under a new name, creates a fresh batch of tickets.
- Notes with a `.failed` marker are not skipped. Saving one again reprocesses the whole note, so tickets that were already created get created again.
- There is no rate or cost limit. Dropping 100 files at once will make 100 LLM calls.

## Credits

- Planned with the [gstack](https://github.com/garrytan/gstack) workflow (`/office-hours` and `/plan-eng-review`).
- [Anthropic Python SDK](https://github.com/anthropics/anthropic-sdk-python), [watchdog](https://github.com/gorakhargosh/watchdog), and the [Linear GraphQL API](https://developers.linear.app/).
