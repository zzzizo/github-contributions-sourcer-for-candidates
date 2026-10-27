"""Command-line entry point."""

import argparse
import csv
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import discover, enrich, jd, places, report
from .github import ROOT, AuthError, GitHub, GitHubError, load_token, log
from .score import DEFAULT_WEIGHTS, SENIORITY, fmt, log_scale, score, word_hits

TOKEN_STEPS = """  1. Create one at https://github.com/settings/personal-access-tokens/new
     (fine-grained, "Public repositories (read-only)", no extra permissions needed).
  2. Copy .env.example to .env in this folder and paste the token after GITHUB_TOKEN=
     (or export GITHUB_TOKEN=... in your shell)."""
TOKEN_HELP = "No GitHub token found.\n" + TOKEN_STEPS

REQUIRED_ROLE_FIELDS = ("label", "languages", "search_languages", "topics", "seed_repos")
LIST_ROLE_FIELDS = ("languages", "search_languages", "topics", "topic_search", "keywords", "seed_repos")
SEED_REPO = re.compile(r"[\w.-]+/[\w.-]+")
ROLE_KEY = re.compile(r"[a-z0-9][a-z0-9-]{1,39}")
REQUIREMENT_LISTS = ("languages", "topics", "keywords", "repos")


def role_problems(role):
    """Everything wrong with one roles.json entry, so a typo fails up front instead of mid-run."""
    if not isinstance(role, dict):
        return ["must be an object"]
    problems = [f"missing {field}" for field in REQUIRED_ROLE_FIELDS if not role.get(field)]
    for field in LIST_ROLE_FIELDS:
        if field in role and not (isinstance(role[field], list) and all(isinstance(x, str) for x in role[field])):
            problems.append(f"{field} must be a list of strings")
    if isinstance(role.get("seed_repos"), list):
        problems += [f"bad seed repo {r!r} (expected owner/name)" for r in role["seed_repos"]
                     if isinstance(r, str) and not SEED_REPO.fullmatch(r)]
    weights = role.get("weights", {})
    if not isinstance(weights, dict):
        problems.append("weights must be an object")
    else:
        unknown = sorted(set(weights) - set(DEFAULT_WEIGHTS))
        if unknown:
            problems.append(f"unknown weights {', '.join(unknown)} (use {', '.join(DEFAULT_WEIGHTS)})")
        if any(not isinstance(v, (int, float)) or v < 0 for v in weights.values()):
            problems.append("weights must be numbers of 0 or more")
        elif sum({**DEFAULT_WEIGHTS, **weights}.values()) <= 0:
            problems.append("weights must not all be 0")
    if "min_followers" in role and not (isinstance(role["min_followers"], int) and role["min_followers"] >= 0):
        problems.append("min_followers must be a whole number of 0 or more")
    requirements = role.get("requirements", [])
    if not isinstance(requirements, list) or not all(
            isinstance(r, dict) and isinstance(r.get("name"), str) and r["name"].strip()
            and all(isinstance(r.get(f, []), list) and all(isinstance(x, str) for x in r.get(f, []))
                    for f in REQUIREMENT_LISTS) for r in requirements):
        problems.append("requirements must be a list of {name, must, languages, topics, keywords, repos}")
    return problems


def load_roles():
    path = ROOT / "roles.json"
    try:
        with open(path, encoding="utf-8") as f:
            roles = json.load(f)
    except OSError as err:
        sys.exit(f"Could not read {path}: {err}")
    except json.JSONDecodeError as err:
        sys.exit(f"{path} is not valid JSON (line {err.lineno}, column {err.colno}): {err.msg}")
    if not isinstance(roles, dict) or not roles:
        sys.exit(f"{path} must be an object of roles, like {{\"ai\": {{...}}}}")
    problems = [f"  {key}: {problem}" for key, role in roles.items() for problem in role_problems(role)]
    if problems:
        sys.exit(f"{path} has problems:\n" + "\n".join(problems))
    return roles


def load_role_file(path):
    """A single role saved as JSON (the web app writes one for each job-description search)."""
    try:
        role = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as err:
        sys.exit(f"Could not read role file {path}: {err}")
    problems = role_problems(role)
    if problems:
        sys.exit(f"Role file {path} has problems: " + "; ".join(problems))
    return role


def _role_json(role):
    """One role in the same layout as the hand-written entries in roles.json: one field per line."""
    lines = [f"    {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}" for k, v in role.items()]
    return "{\n" + ",\n".join(lines) + "\n  }"


def save_role(key, role):
    """Add a role to roles.json, keeping the existing entries exactly as they are written."""
    key = (key or "").strip().lower()
    if not ROLE_KEY.fullmatch(key):
        raise ValueError("Use 2–40 lowercase letters, digits or dashes for the role key, like jd-founding-ios.")
    role = {k: v for k, v in role.items() if k in (*REQUIRED_ROLE_FIELDS, *LIST_ROLE_FIELDS, "weights",
                                                   "min_followers", "requirements")}
    problems = role_problems(role)
    if problems:
        raise ValueError("; ".join(problems))
    roles = load_roles()
    if key in roles:
        raise ValueError(f"There's already a role called {key}. Pick another key.")
    path = ROOT / "roles.json"
    text = path.read_text(encoding="utf-8").rstrip()
    if not text.endswith("}"):
        raise ValueError("roles.json doesn't end with }, so it can't be extended safely.")
    text = text[:-1].rstrip() + f',\n  {json.dumps(key)}: {_role_json(role)}\n}}\n'
    json.loads(text)  # never write a file we couldn't read back
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)
    return key


def split(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def read_excludes(paths):
    """Logins from previous result CSVs (login column) or plain lists of logins / GitHub URLs."""
    logins = set()
    for path in paths or []:
        try:
            # utf-8-sig strips the BOM that Excel and Sheets add to exported CSVs.
            text = Path(path).read_text(encoding="utf-8-sig")
        except (OSError, UnicodeDecodeError) as err:
            sys.exit(f"Could not read --exclude file {path}: {err}")
        lines = text.splitlines()
        header = [h.strip().lower() for h in next(csv.reader(lines[:1]), [])]
        if "login" in header:
            reader = csv.DictReader(lines[1:], fieldnames=header)
            logins.update(row["login"].strip().lower() for row in reader if (row.get("login") or "").strip())
            continue
        for line in lines:
            line = line.strip()
            match = re.search(r"github\.com/([A-Za-z0-9-]+)", line)
            if match:
                logins.add(match.group(1).lower())
            elif re.fullmatch(r"[A-Za-z0-9-]+", line):
                logins.add(line.lower())
    return logins


def client(args):
    token = load_token()
    if not token:
        sys.exit(TOKEN_HELP)
    return GitHub(token, use_cache=not getattr(args, "refresh", False))


def preflight(gh):
    """Fail fast on a bad token instead of one error per call; warn when quota is low. Costs no quota."""
    try:
        limits = gh.rate_limit()["resources"]
    except (GitHubError, KeyError, TypeError):
        return  # connection problems will surface (with retries) on the first real call
    for name in ("core", "graphql"):
        r = limits.get(name) or {}
        if r.get("limit") and r.get("remaining", 0) < r["limit"] * 0.1:
            reset = datetime.fromtimestamp(r.get("reset", 0)).strftime("%H:%M")
            log(f"  ! GitHub {name} quota low ({r['remaining']}/{r['limit']}, resets {reset}); "
                f"the run will pause when it runs out.")


def run_dir(base):
    """A fresh folder, so two runs in the same minute don't overwrite each other."""
    path, n = base, 2
    while path.exists():
        path, n = base.with_name(f"{base.name}-{n}"), n + 1
    path.mkdir(parents=True)
    return path


def int_at_least(low):
    def parse(value):
        n = int(value)
        if n < low:
            raise argparse.ArgumentTypeError(f"must be {low} or more")
        return n
    parse.__name__ = "int"  # argparse names the type in its "invalid int value" message
    return parse


def cmd_roles(args):
    for key, role in load_roles().items():
        print(f"{key:<16} {role['label']}")
        print(f"{'':<16} seeds: {', '.join(role['seed_repos'][:5])}, …")


def cmd_check(args):
    gh = client(args)
    try:
        me = gh.viewer()
        limits = gh.rate_limit()["resources"]
    except GitHubError as err:
        sys.exit(f"Token check failed: {err}")
    print(f"Authenticated as {me['login']}")
    for name in ("core", "search", "graphql"):
        r = limits[name]
        print(f"  {name:<8} {r['remaining']}/{r['limit']} remaining")


def read_jd(path):
    try:
        return sys.stdin.read() if path == "-" else Path(path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError) as err:
        sys.exit(f"Could not read the job description {path}: {err}")


def analyze_jd(gh, path):
    try:
        result = jd.analyze(read_jd(path))
    except ValueError as err:
        sys.exit(str(err))
    log("Checking the JD's seed repos and searching GitHub for matching projects…")
    return jd.refine_with_github(gh, result)


def resolve_role(args, gh):
    """(key, role) from a preset name, a role file, or a job description."""
    given = [x for x in (args.role, args.role_file, args.jd) if x]
    if len(given) != 1:
        sys.exit("Give one of: a role preset (e.g. search ai), --role-file FILE, or --jd FILE.")
    if args.jd:
        result = analyze_jd(gh, args.jd)
        log(f"Read the JD as: {result['role']['label']} · "
            f"{', '.join(s['name'] for s in result['analysis']['skills'][:6])}")
        return result["key"], result["role"]
    if args.role_file:
        role = load_role_file(args.role_file)
        return role.get("key") or "jd", role
    roles = load_roles()
    if args.role not in roles:
        sys.exit(f"Unknown role {args.role!r}. Choose from: {', '.join(roles)}")
    return args.role, roles[args.role]


def prerank(user, lead, role):
    """How promising a lead looks from the cheap profile alone, to pick who gets the expensive check."""
    seed = sum(min(c, 500) for c in lead.get("seed_contribs", {}).values())
    followers = (user.get("followers") or {}).get("totalCount") or 0
    text = " ".join([user.get("bio") or "", user.get("company") or ""]).lower()
    return (2 * log_scale(seed, 200) + log_scale(followers, 2000) + 0.1 * len(lead.get("sources", ()))
            + 0.5 * min(1.0, len(word_hits(text, role.get("keywords", []))) / 2)
            + 0.3 * bool(word_hits(text, SENIORITY)))


def cmd_search(args):
    gh = client(args) if args.jd else None  # only a JD needs GitHub before the arguments are checked
    key, role = resolve_role(args, gh)
    role = dict(role)
    if args.repos:
        bad = [r for r in split(args.repos) if not SEED_REPO.fullmatch(r)]
        if bad:
            sys.exit(f"--repos expects owner/name, got: {', '.join(bad)}")
        role["seed_repos"] = role["seed_repos"] + split(args.repos)
    if args.languages:
        role["search_languages"] = split(args.languages)
    where = places.resolve(args.location)
    match_terms, search_terms = where["match"], where["search"]
    min_followers = args.min_followers if args.min_followers is not None else role.get("min_followers", 30)
    excluded = read_excludes(args.exclude)
    gh = gh or client(args)
    preflight(gh)
    now = datetime.now(timezone.utc)

    log(f"\n▶ {role['label']}" + (f" in {args.location}" if args.location else ""))
    if where["places"]:
        log(f"  location: {places.describe(where)}\n  searching GitHub in: {', '.join(search_terms)}")
    for note in where["notes"]:
        log(f"  note: {note}")
    if args.location and not match_terms:
        log("  no usable location left, so searching everywhere")
    log("\nDiscovering…")
    pool = discover.Pool()
    if not args.skip_contributors:
        discover.from_seed_repos(gh, pool, role["seed_repos"], args.seed_pages)
    if not args.skip_search:
        discover.from_user_search(gh, pool, role["search_languages"], search_terms, min_followers, args.per_search)
    if not args.skip_topics:
        discover.from_topic_repos(gh, pool, role.get("topic_search", []), args.min_stars, args.per_topic)

    leads = [e for e in pool.ranked() if e["login"].lower() not in excluded]
    # Reading a profile is cheap, and a location filter throws most of them away, so read more when there is one.
    max_read = args.max_enrich or (1200 if match_terms else 400)
    log(f"\n{len(pool)} unique people found, {len(pool) - len(leads)} excluded; "
        f"reading the top {min(len(leads), max_read)} profiles…")
    leads = leads[:max_read]
    by_login = {e["login"].lower(): e for e in leads}
    users = enrich.fetch_profiles(gh, [e["login"] for e in leads])

    dropped = {"location": 0, "inactive": 0}
    if match_terms:
        nearby = []
        for u in users:
            location = (u.get("location") or "").strip()
            if discover.location_matches(location, match_terms) or (args.keep_unknown_location and not location):
                nearby.append(u)
        dropped["location"] = len(users) - len(nearby)
        users = nearby
    max_deep = args.max_deep or max(2 * args.limit, 100)
    users.sort(key=lambda u: prerank(u, by_login.get(u["login"].lower(), {}), role), reverse=True)
    log(f"{len(users)} profiles left after the location filter; checking the work of the top {min(len(users), max_deep)}…")
    profiles = enrich.to_profiles(enrich.add_work(gh, users[:max_deep]), now)

    kept = []
    for p in profiles:
        result = score(p, role, by_login.get(p["login"].lower(), {}).get("seed_contribs"), now)
        if "inactive 12+ months" in result["flags"] and not args.include_inactive:
            dropped["inactive"] += 1
            continue
        if result["score"] >= args.min_score:
            kept.append((p, result))
    kept.sort(key=lambda c: c[1]["score"], reverse=True)
    kept = kept[:args.limit]
    log(f"Filtered out {dropped['location']} outside the location and {dropped['inactive']} inactive.")

    if args.emails and kept:
        log("Looking up commit emails…")
        enrich.add_commit_emails(gh, [p for p, _ in kept])
        for p, s in kept:
            if p["email"] and "no public contact" in s["flags"]:
                s["flags"].remove("no public contact")

    slug = "-".join(x for x in [key, re.sub(r"[^a-z0-9]+", "-", (args.location or "").lower()).strip("-")] if x)
    try:
        out_dir = run_dir(Path(args.out) / f"{slug}-{now.astimezone().strftime('%Y%m%d-%H%M')}")
    except OSError as err:
        sys.exit(f"Could not create the results folder in {args.out}: {err}")
    meta = {
        "role": key, "role_label": role["label"], "generated_at": now.astimezone().isoformat(),
        "discovered": len(pool), "enriched": len(profiles),
        "requirements": [{"name": r["name"], "must": bool(r.get("must"))} for r in role.get("requirements") or []],
        "query": {"location": args.location, "extra repos": args.repos, "min score": args.min_score or None},
    }
    report.write_csv(out_dir / "candidates.csv", kept)
    report.write_json(out_dir / "candidates.json", kept, meta)
    report.write_html(out_dir / "report.html", kept, meta)

    print(f"\n{'#':>3}  {'tier':<4} {'score':>5}  {'login':<22} {'location':<24} top signal")
    for i, (p, s) in enumerate(kept[:15], 1):
        print(f"{i:>3}  {s['tier']:<4} {s['score']:>5}  {p['login'][:22]:<22} {(p['location'] or '-')[:24]:<24} "
              f"{s['reasons'][0][:60] if s['reasons'] else ''}")
    tiers = {t: sum(1 for _, s in kept if s["tier"] == t) for t in "ABC"}
    print(f"\n{len(kept)} candidates (A: {tiers['A']}, B: {tiers['B']}, C: {tiers['C']}) · "
          f"{sum(1 for p, _ in kept if p['email'])} with email")
    print(f"Report: {out_dir / 'report.html'}\nCSV:    {out_dir / 'candidates.csv'}")


def cmd_jd(args):
    gh = client(args)
    result = analyze_jd(gh, args.file)
    role, a = result["role"], result["analysis"]
    print(f"\n{role['label']}")
    if a["levels"] or a["years"]:
        print(f"  level:        {', '.join(a['levels']) or '-'}" + (f" · {a['years']}+ years" if a["years"] else ""))
    print(f"  location:     {a['location'] or 'anywhere'}")
    print(f"  must-haves:   {', '.join(s['name'] for s in a['skills'] if s['must']) or '-'}")
    print(f"  nice-to-have: {', '.join(s['name'] for s in a['skills'] if not s['must']) or '-'}")
    print(f"  languages:    {', '.join(role['languages'])} (search: {', '.join(role['search_languages'])})")
    print(f"  topics:       {', '.join(role['topic_search'])} …")
    print(f"  weights:      {', '.join(f'{k} {v}' for k, v in role['weights'].items())}")
    print("  seed repos:")
    for info in a["seed_info"]:
        stars = f"★{fmt(info['stars'])}" if info.get("stars") else ""
        print(f"    {info['repo']:<45} {stars:>7}  {info['why']}" + (" (GitHub search)" if info["source"] == "github" else ""))
    for note in a["notes"]:
        print(f"  note: {note}")
    if args.save:
        try:
            print(f"\nSaved as role {save_role(args.save, role)} in roles.json: python3 source.py search {args.save}")
        except ValueError as err:
            sys.exit(f"Could not save the role: {err}")
    else:
        print(f"\nSearch with it: python3 source.py search --jd {args.file}"
              f"\nOr keep it as a preset:  python3 source.py jd {args.file} --save {result['key']}")


def cmd_ui(args):
    from . import web  # imported lazily: web imports this module
    web.serve(args.port, open_browser=not args.no_browser)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="source.py", description="Find senior engineers on GitHub.")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("roles", help="list role presets").set_defaults(func=cmd_roles)
    sub.add_parser("check", help="verify your GitHub token and remaining quota").set_defaults(func=cmd_check)

    u = sub.add_parser("ui", help="open the web app in your browser")
    u.add_argument("--port", type=int_at_least(1), default=8765, help="port to listen on (default 8765)")
    u.add_argument("--no-browser", action="store_true", help="don't open a browser tab")
    u.set_defaults(func=cmd_ui)

    j = sub.add_parser("jd", help="read a job description and show the search profile it turns into")
    j.add_argument("file", help="text file with the job description (- reads from stdin)")
    j.add_argument("--save", metavar="KEY", help="also save it as a role preset in roles.json under this key")
    j.set_defaults(func=cmd_jd)

    s = sub.add_parser("search", help="source candidates for a role")
    s.add_argument("role", nargs="?", help="role preset from roles.json (e.g. ai, founding, backend, web3, quantum)")
    s.add_argument("--jd", metavar="FILE", help="build the search from a job description in a text file instead")
    s.add_argument("--role-file", metavar="FILE", help="use a role saved as JSON instead of a preset")
    s.add_argument("-l", "--location", help='any cities, states, countries or regions, comma-separated, '
                                            'e.g. "sf, london", "Brazil", "LATAM" or "Austin, TX"')
    s.add_argument("-n", "--limit", type=int_at_least(1), default=50, help="candidates in the final report (default 50)")
    s.add_argument("--repos", help="extra seed repos, comma-separated owner/name")
    s.add_argument("--languages", help="override languages used for user search, comma-separated")
    s.add_argument("--emails", action="store_true", help="look up emails from public commits for people without one")
    s.add_argument("--exclude", action="append", help="file of logins/URLs or a previous candidates.csv; repeatable")
    s.add_argument("--min-score", type=int_at_least(0), default=0)
    s.add_argument("--min-followers", type=int_at_least(0), help="for user search (default from role)")
    s.add_argument("--min-stars", type=int_at_least(0), default=200, help="for popular-project owner search (default 200)")
    s.add_argument("--seed-pages", type=int_at_least(1), default=2, help="pages of 100 contributors per seed repo (default 2)")
    s.add_argument("--per-search", type=int_at_least(1), default=100, help="users per language/location search (default 100)")
    s.add_argument("--per-topic", type=int_at_least(1), default=50, help="repos per topic search (default 50)")
    s.add_argument("--max-enrich", type=int_at_least(1),
                   help="max profiles to read (default 400, or 1200 with a location)")
    s.add_argument("--max-deep", type=int_at_least(1),
                   help="max profiles whose repos and activity get checked (default 2x --limit, at least 100)")
    s.add_argument("--keep-unknown-location", action="store_true", help="keep people with no location set")
    s.add_argument("--include-inactive", action="store_true", help="keep people inactive for 12+ months")
    s.add_argument("--skip-contributors", action="store_true")
    s.add_argument("--skip-search", action="store_true")
    s.add_argument("--skip-topics", action="store_true")
    s.add_argument("--refresh", action="store_true", help="ignore the 72h cache")
    s.add_argument("--out", default=str(ROOT / "results"))
    s.set_defaults(func=cmd_search)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.exit("\nInterrupted. Anything fetched so far is cached, so a re-run picks up quickly.")
    except AuthError as err:
        sys.exit(f"\n{err}\nThe token is wrong or expired. To make a new one:\n{TOKEN_STEPS}")
    except GitHubError as err:
        sys.exit(f"\nGitHub error: {err}")
