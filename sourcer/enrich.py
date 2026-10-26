"""Profile enrichment via batched GraphQL, plus optional commit-email lookup."""

import json
import re
import urllib.parse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .github import GitHubError, log

# Enrichment runs in two stages because GitHub caps GraphQL at about 60s of server time per minute. Reading a
# profile is cheap (~0.1s), but a person's repos and contribution history cost ~2s each. So the profile is read for
# every lead, people outside the location are dropped, and only the strongest leads get the expensive check.
PROFILE = """
fragment U on User {
  login name bio company location email websiteUrl twitterUsername isHireable createdAt url
  followers { totalCount }
  organizations(first: 10) { nodes { login } }
  socialAccounts(first: 10) { nodes { provider url } }
}"""
REPOS = """
  repositories(ownerAffiliations: OWNER, isFork: false, first: 20,
               orderBy: {field: STARGAZERS, direction: DESC}) {
    totalCount
    nodes {
      nameWithOwner stargazerCount pushedAt description
      primaryLanguage { name }
      repositoryTopics(first: 5) { nodes { topic { name } } }
    }
  }"""
WORK = """
fragment U on User {
  login %s
  contributionsCollection {
    commitContributionsByRepository(maxRepositories: 25) {
      repository { nameWithOwner stargazerCount }
      contributions { totalCount }
    }
    contributionCalendar { totalContributions weeks { contributionDays { contributionCount date } } }
  }
}""" % REPOS
# For the odd account whose history is too big to load within GitHub's 10s limit: repos only.
WORK_LIGHT = "fragment U on User { login %s }" % REPOS


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _query(logins, fragment):
    parts = [f"u{i}: user(login: {json.dumps(login)}) {{ ...U }}" for i, login in enumerate(logins)]
    return "query {\n" + "\n".join(parts) + "\n}\n" + fragment


def _fetch(gh, logins, fragment, fallback=None):
    try:
        data = gh.graphql(_query(logins, fragment))
        return [data.get(f"u{i}") for i in range(len(logins))]
    except GitHubError as err:
        if len(logins) == 1:
            if fallback:
                return _fetch(gh, logins, fallback)
            log(f"  ! could not check {logins[0]}: {err}")
            return [None]
        # A heavy batch can time out on GitHub's side; retry it in halves, down to one user at a time.
        half = len(logins) // 2
        return _fetch(gh, logins[:half], fragment, fallback) + _fetch(gh, logins[half:], fragment, fallback)


def _batched(gh, logins, fragment, batch_size, workers, label, fallback=None):
    batches = [logins[i:i + batch_size] for i in range(0, len(logins), batch_size)]
    out = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, users in enumerate(pool.map(lambda b: _fetch(gh, b, fragment, fallback), batches), 1):
            out.extend(users)
            if done % max(1, len(batches) // 20) == 0 or done == len(batches):
                log(f"  {label} {min(done * batch_size, len(logins))}/{len(logins)}")
    return out


def fetch_profiles(gh, logins, batch_size=20, workers=4):
    """Stage 1: basic profile (name, bio, location, followers, links) for every lead. Missing users are dropped."""
    return [u for u in _batched(gh, logins, PROFILE, batch_size, workers, "read profiles") if u and u.get("login")]


def add_work(gh, users, batch_size=2, workers=4):
    """Stage 2: add repos and the last year of contributions to stage-1 profiles, in place."""
    work = _batched(gh, [u["login"] for u in users], WORK, batch_size, workers, "enriched", fallback=WORK_LIGHT)
    for user, extra in zip(users, work):
        user.update({k: v for k, v in (extra or {}).items() if k != "login"})
    return users


def to_profiles(users, now=None):
    profiles = []
    for u in users:
        try:
            profiles.append(normalize(u, now))
        except (KeyError, TypeError, ValueError, AttributeError) as err:
            log(f"  ! skipped {u.get('login', '?')}: unexpected profile data ({err!r})")
    return profiles


def enrich(gh, logins, batch_size=2, workers=4, now=None):
    """Both stages for every login (no filtering in between)."""
    users = fetch_profiles(gh, logins, workers=workers)
    return to_profiles(add_work(gh, users, batch_size=batch_size, workers=workers), now)


def _nodes(connection):
    # GraphQL can return null for a whole connection or for single nodes it couldn't resolve.
    return [n for n in ((connection or {}).get("nodes") or []) if n]


def _text(value):
    return (value or "").strip()


LINKEDIN = re.compile(r"linkedin\.com/(?:in|pub)/([A-Za-z0-9%_-]+)", re.I)


def find_linkedin(u):
    """LinkedIn URL the person published on GitHub: social links first, then website, then bio."""
    candidates = [a.get("url") for a in _nodes(u.get("socialAccounts")) if a.get("provider") == "LINKEDIN"]
    candidates += [a.get("url") for a in _nodes(u.get("socialAccounts"))]
    candidates += [u.get("websiteUrl"), u.get("bio")]
    for text in candidates:
        match = LINKEDIN.search(text or "")
        if match:
            return f"https://www.linkedin.com/in/{match.group(1).rstrip('-_')}"
    return ""


def linkedin_search(name, company):
    """A LinkedIn people-search link for someone who hasn't linked their profile; a person checks the match."""
    if not name:
        return ""
    words = " ".join([name, company.lstrip("@").split(",")[0].strip()]).strip()
    return "https://www.linkedin.com/search/results/people/?" + urllib.parse.urlencode({"keywords": words})


def _calendar(u):
    collection = u.get("contributionsCollection") or u.get("year") or {}
    return collection.get("contributionCalendar") or {}


def _contributions(u, now):
    """(last year, last 90 days). Older cached profiles carried the 90-day total as its own field."""
    calendar = _calendar(u)
    year = calendar.get("totalContributions") or 0
    if "recent" in u:
        return year, ((u.get("recent") or {}).get("contributionCalendar") or {}).get("totalContributions") or 0
    cutoff = ((now or datetime.now(timezone.utc)) - timedelta(days=90)).strftime("%Y-%m-%d")
    recent = sum(day.get("contributionCount") or 0 for week in calendar.get("weeks") or [] for day in
                 (week or {}).get("contributionDays") or [] if day and (day.get("date") or "") >= cutoff)
    return year, recent


def _contributed(u):
    """Other people's repos they committed to in the last year, with commit counts when GitHub gives them."""
    by_repo = ((u.get("contributionsCollection") or {}).get("commitContributionsByRepository")) or []
    if by_repo:
        return [{"name": (r.get("repository") or {}).get("nameWithOwner"),
                 "stars": (r.get("repository") or {}).get("stargazerCount") or 0,
                 "commits": (r.get("contributions") or {}).get("totalCount") or 0} for r in by_repo if r]
    return [{"name": r.get("nameWithOwner"), "stars": r.get("stargazerCount") or 0, "commits": 0}
            for r in _nodes(u.get("repositoriesContributedTo"))]


def normalize(u, now=None):
    repos = [r for r in _nodes(u.get("repositories")) if r.get("nameWithOwner")]
    login_prefix = u["login"].lower() + "/"
    contributed = sorted((r for r in _contributed(u) if r["name"] and not r["name"].lower().startswith(login_prefix)),
                         key=lambda r: r["stars"], reverse=True)
    contrib_year, contrib_90d = _contributions(u, now)
    languages = Counter(r["primaryLanguage"]["name"] for r in repos if (r.get("primaryLanguage") or {}).get("name"))
    topics = Counter(t["topic"]["name"] for r in repos for t in _nodes(r.get("repositoryTopics"))
                     if (t.get("topic") or {}).get("name"))
    pushes = [parse_time(r["pushedAt"]) for r in repos if r.get("pushedAt")]
    top = repos[0] if repos else None
    return {
        "login": u["login"],
        "name": _text(u.get("name")),
        "bio": _text(u.get("bio")),
        "company": _text(u.get("company")),
        "location": _text(u.get("location")),
        "email": _text(u.get("email")),
        "email_source": "profile" if _text(u.get("email")) else "",
        "website": _text(u.get("websiteUrl")),
        "twitter": _text(u.get("twitterUsername")),
        "linkedin": find_linkedin(u),
        "hireable": bool(u.get("isHireable")),
        "url": u.get("url") or f"https://github.com/{u['login']}",
        "created_at": parse_time(u.get("createdAt")),
        "followers": (u.get("followers") or {}).get("totalCount") or 0,
        "orgs": [o["login"] for o in _nodes(u.get("organizations")) if o.get("login")],
        "repo_count": (u.get("repositories") or {}).get("totalCount") or len(repos),
        "repos": [{"name": r["nameWithOwner"], "stars": r.get("stargazerCount") or 0,
                   "pushed_at": parse_time(r.get("pushedAt")), "description": _text(r.get("description"))}
                  for r in repos],
        "owned_stars": sum(r.get("stargazerCount") or 0 for r in repos),
        "top_repo": {"name": top["nameWithOwner"], "stars": top.get("stargazerCount") or 0,
                     "description": _text(top.get("description"))} if top else None,
        "languages": languages,
        "topics": topics,
        "contributed": contributed,
        "contrib_year": contrib_year,
        "contrib_90d": contrib_90d,
        "last_push": max(pushes) if pushes else None,
    }


JUNK_EMAIL = re.compile(r"noreply|no-reply|localhost|example\.(com|org)|\.local$|^root@|^admin@|@users\.github", re.I)


def find_commit_email(gh, profile, repos_to_check=3):
    """Most common real author email on the person's own recent commits."""
    recent = sorted((r for r in profile["repos"] if r["pushed_at"]), key=lambda r: r["pushed_at"], reverse=True)
    emails = Counter()
    for repo in recent[:repos_to_check]:
        try:
            commits = gh.rest(f"/repos/{repo['name']}/commits", {"author": profile["login"], "per_page": 20})
        except GitHubError:
            continue
        for c in commits or []:
            email = ((c.get("commit") or {}).get("author") or {}).get("email") or ""
            if "@" in email and not JUNK_EMAIL.search(email):
                emails[email.lower()] += 1
        if emails:
            break
    return emails.most_common(1)[0][0] if emails else ""


def add_commit_emails(gh, profiles, workers=4):
    missing = [p for p in profiles if not p["email"]]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for profile, email in zip(missing, pool.map(lambda p: find_commit_email(gh, p), missing)):
            if email:
                profile["email"], profile["email_source"] = email, "commit"
    log(f"  found commit emails for {sum(1 for p in missing if p['email'])}/{len(missing)} without a public email")
