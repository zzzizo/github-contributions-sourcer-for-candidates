# GitHub Sourcer

Finds senior engineers on GitHub for a role, scores them 0–100 with reasons you can read, and gives you a ranked
shortlist with contact links (email, website, X, LinkedIn) and an outreach opener for each person. Use it from a
web app in your browser or from the command line. Pure Python 3.10+, nothing to install.

## How to run it

**1. Get the code**

```bash
git clone https://github.com/zzzizo/github-contributions-sourcer-for-candidates.git
cd github-contributions-sourcer-for-candidates
```

**2. Check you have Python 3.10 or newer**

```bash
python3 --version
```

If it's missing or older, install it from <https://www.python.org/downloads/>. On Windows, type `python` instead of
`python3` in every command below.

**3. Create a GitHub token** (free, read-only)

Open <https://github.com/settings/personal-access-tokens/new>, give it a name, choose
**Public repositories (read-only)** and no other permissions, then click **Generate token** and copy it.

**4. Start the app**

```bash
python3 source.py ui
```

Your browser opens the app at `http://127.0.0.1:8765`. The first time, it asks for your token: paste it and click
**Save & check**. It's saved in a `.env` file on your computer only, never uploaded.

**5. Find candidates**

Pick a role, optionally add a location (or click a chip like *SF Bay Area* or *Europe*), and press
**Find candidates**. A search takes about 1–5 minutes. You'll see live progress, then a ranked shortlist you can:

- filter by tier (A/B/C), text or "has email", and sort by score, followers, stars or recent activity
- open each person's GitHub, website, X and LinkedIn
- copy a personalised opener, or every email in the current view at once
- download as CSV for your ATS or Google Sheets, or open the full HTML report

Past runs stay in the sidebar, and "Skip people from earlier runs" leaves out anyone you've already found.
Press `Ctrl+C` in the terminal to stop the app. `--port 9000` picks another port; `--no-browser` skips opening a tab.

**Prefer setting the token by hand?** Copy `.env.example` to `.env` (`cp .env.example .env`, or `copy` on Windows),
paste the token after `GITHUB_TOKEN=`, and run `python3 source.py check`: it should print your username and quota.

**Trouble?**

- *"GitHub rejected the token"*: it expired or was copied incompletely. Make a new one (step 3).
- *Certificate errors on macOS*: run `Install Certificates.command` from your Python folder in Applications.
- *A search seems slow*: GitHub sometimes times out on big queries; the tool waits and retries on its own, and
  everything already fetched is cached, so a re-run is quick.

## LinkedIn

If someone added their LinkedIn to their GitHub profile (as a profile link, website or in their bio), their card
gets a **LinkedIn** button to the exact profile. Otherwise, if they have a name on GitHub, you get a
**Find on LinkedIn** button that runs a LinkedIn people search by name and company. That one is a guess, so check
it's the right person. The CSV has both as `linkedin` and `linkedin_search`. The tool never queries LinkedIn itself.

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
