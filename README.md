# GitHub Sourcer

Finds senior engineers on GitHub for a role, scores them 0–100 with reasons you can read, and gives you a ranked
shortlist with contact links (email, website, X, LinkedIn) and an outreach opener for each person. Pick a preset
role or paste a job description. Use it from a web app in your browser or from the command line. Pure Python 3.10+,
nothing to install, no AI service or API costs.

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

Pick a role, optionally type a location (any city, state, country or region, or click a chip like *SF Bay Area*),
and press **Find candidates**. A search takes about 1–5 minutes. You'll see live progress, then a ranked shortlist you can:

- filter by tier (A/B/C), text or "has email", and sort by score, followers, stars or recent activity
- open each person's GitHub, website, X and LinkedIn
- copy a personalised opener, or every email in the current view at once
- download as CSV for your ATS or Google Sheets, or open the full HTML report

Past runs stay in the sidebar, and "Skip people from earlier runs" leaves out anyone you've already found.

**Hiring for something the presets don't cover?** Switch the sidebar to **From a JD** (see below).
Press `Ctrl+C` in the terminal to stop the app. `--port 9000` picks another port; `--no-browser` skips opening a tab.

**Prefer setting the token by hand?** Copy `.env.example` to `.env` (`cp .env.example .env`, or `copy` on Windows),
paste the token after `GITHUB_TOKEN=`, and run `python3 source.py check`: it should print your username and quota.

**Trouble?**

- *"GitHub rejected the token"*: it expired or was copied incompletely. Make a new one (step 3).
- *Certificate errors on macOS*: run `Install Certificates.command` from your Python folder in Applications.
- *A search seems slow*: GitHub sometimes times out on big queries; the tool waits and retries on its own, and
  everything already fetched is cached, so a re-run is quick.

## Searching from a job description

In the app, switch **Role** to **From a JD**, paste the whole job description and press **Analyze JD**. In about
10 seconds you get a search profile you can check and edit:

- **Requirements**: must-haves and nice-to-haves. Click one to switch it, × to drop it, or type your own.
- **Where to look**: the flagship projects whose contributors get checked, with stars. Ones marked *search* were
  found by GitHub search rather than the built-in list, so give them a look. Languages marked ★ are also used to
  search users (up to 3).
- **How to rank**: the score weights, preset from the JD's wording (ownership and shipping raise *impact*,
  open source raises *seed*, research raises *notable*), and the bio keywords.

Then press **Find candidates**. Every candidate's first reason says which JD requirements their GitHub shows
evidence of, and which must-haves it doesn't. **Save as preset…** adds the profile to `roles.json` so you can reuse
it. That file is in git, so keep client names out of the title if you push it.

How it reads the JD, with no AI service involved:

- It splits the JD into sections and weights each line by where it sits: requirements and responsibilities count
  most, nice-to-haves less, the company blurb little. Benefits, "not required" lists and rejection criteria don't
  count, and neither do lines like "we're not looking for frontend engineers".
- A built-in dictionary of about 90 skills (iOS, payments, LLM inference, Kafka, smart contracts…) maps the JD's
  wording to GitHub languages, repo topics, bio keywords and curated flagship repos.
- It picks up the title (dropping the company name), seniority, years, location (in person, hybrid or remote),
  visa sponsorship, and companies or schools named as good signals.
- GitHub then checks the flagship repos (dropping archived or renamed ones), prefers repos written in the JD's
  languages, and adds popular, active projects for the top skills.

From the command line:

```bash
python3 source.py jd job.txt                        # show what it understood
python3 source.py jd job.txt --save jd-ios-founder  # also save it as a preset
python3 source.py search --jd job.txt -l sf         # search straight from the JD
```

## Locations

Type any place, several separated by commas. Under the box, the app shows how it read each one and where it will
search:

- **Countries, states and regions** cover the cities inside them. *Brazil* keeps people in "São Paulo" or
  "Florianópolis - SC", *Texas* keeps "Austin, TX", and *LATAM*, *Europe*, *Nordics*, *DACH*, *APAC*, *EMEA*, *GCC* and
  others cover their countries. Russia and Belarus are available on their own but aren't part of any region.
- **Other spellings** work: *Bangalore* or *Bengaluru*, *Munich* or *München*, *Kyiv* or *Kiev*. Accents don't matter.
- **A state or country after a place only says where it is**: *Austin, TX* means Austin, not all of Texas, and
  *sf, usa* means the Bay Area.
- **Anywhere else** still works: a town that isn't built in keeps people whose location mentions it.
- *anywhere* or *worldwide* means no location filter.

The places live in `sourcer/places.py`; add a line there to teach it a new one.

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

Then it checks people in two steps, because GitHub allows only about a minute of query time per minute and a
person's full history is the expensive part. First it reads every lead's basic profile, which is cheap, and drops
people outside the location. Then it checks the repos and last year of commits only for the most promising
(`--max-deep`, by default twice `--limit` and at least 100).

## How the score works

| Part | Measures |
|---|---|
| impact | stars on projects they own |
| seed | commits to the role's seed repos |
| notable | other 1k+ star projects they contributed to in the last year |
| activity | contributions in the last year and last 90 days |
| experience | account age and followers |
| fit | languages, repo topics, bio keywords, seniority words (staff, founder, PhD…), and the JD's requirements |

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
