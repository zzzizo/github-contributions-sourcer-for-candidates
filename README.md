# GitHub Sourcer

Finds senior engineers on GitHub for a role, scores them 0–100 with reasons you can read, and writes a CSV plus a
filterable HTML report with an outreach opener for each person. Pure Python 3.10+, nothing to install.

## Setup (once)

1. Create a token at <https://github.com/settings/personal-access-tokens/new>: fine-grained,
   "Public repositories (read-only)", no other permissions.
2. `cp .env.example .env` and paste the token after `GITHUB_TOKEN=`.
3. `python3 source.py check` should print your username and quota.

## Web app

```bash
python3 source.py ui
```

Opens the app in your browser (at `http://127.0.0.1:8765`, only reachable from this computer). Pick a role, add a
location, and press **Find candidates**: you'll see live progress, then a ranked shortlist you can filter by tier,
search, sort, copy openers and emails from, and download as CSV. Past runs stay in the sidebar, and you can paste
your GitHub token straight into the app the first time. `--port` picks another port; `--no-browser` skips opening a tab.

## Command line

```bash
python3 source.py roles                                   # list presets
python3 source.py search ai --location "sf, london"       # AI engineers in SF or London
python3 source.py search smart-contracts --emails -n 100  # also pull emails from public commits
python3 source.py search quantum --keep-unknown-location  # keep people with no location set
python3 source.py search backend -l europe --exclude results/backend-europe-20261024-1500/candidates.csv
python3 source.py search ai --repos "modal-labs/modal-client,mistralai/mistral-inference"  # add your own seeds
```

Each run writes `results/<role>-<location>-<time>/` with `report.html`, `candidates.csv` (import into your ATS or
Sheets) and `candidates.json`. In the CSV, text that starts with `=`, `+`, `-` or `@` gets a leading `'` so
spreadsheets show it as text instead of running it as a formula; the JSON keeps the raw values.

## How it finds people

1. **Seed-repo contributors.** Top contributors to the flagship projects for the role (vLLM, PyTorch, Foundry,
   OpenZeppelin, Qiskit, Kubernetes…). This is the strongest seniority signal.
2. **User search** by language, location and follower count.
3. **Popular-project owners** in role topics, which is useful for founding engineers.

## How the score works

| Part | Measures |
|---|---|
| impact | stars on projects they own |
| seed | commits to the role's seed repos |
| notable | other 1k+ star projects they contributed to in the last year |
| activity | contributions in the last year and last 90 days |
| experience | account age and followers |
| fit | languages, repo topics, bio keywords, seniority words (staff, founder, PhD…) |

Tier A ≥ 65, B ≥ 45. Weights can be changed per role in `roles.json` (see `founding`).

## Tuning

Everything role-specific lives in `roles.json`: seed repos, languages, topics, bio keywords, weights and minimum
followers. Add a role by copying a block. Results are cached for 72h in `.cache/`, so re-runs are fast and cheap;
`--refresh` ignores the cache.

## Using it responsibly

The tool only reads public GitHub data. Commit emails (`--emails`) are public but weren't shared for recruiting:
contact people once, personally, and honour opt-outs. Store only what you need (GDPR/CCPA), and treat the score
as a pointer for your own judgement, never as an automatic reject.

## Tests

```bash
python3 -m unittest discover -s tests
```
