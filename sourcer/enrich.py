"""Profile enrichment via batched GraphQL, plus optional commit-email lookup."""

import json
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

from .github import GitHubError, log

FRAGMENT = """
fragment U on User {
  login name bio company location email websiteUrl twitterUsername isHireable createdAt url
  followers { totalCount }
  organizations(first: 10) { nodes { login } }
  repositories(ownerAffiliations: OWNER, isFork: false, first: 30,
               orderBy: {field: STARGAZERS, direction: DESC}) {
    totalCount
    nodes {
      nameWithOwner stargazerCount pushedAt description
      primaryLanguage { name }
      repositoryTopics(first: 8) { nodes { topic { name } } }
    }
  }
  repositoriesContributedTo(first: 30, contributionTypes: [COMMIT, PULL_REQUEST],
                            orderBy: {field: STARGAZERS, direction: DESC}) {
    nodes { nameWithOwner stargazerCount primaryLanguage { name } }
  }
  year: contributionsCollection { contributionCalendar { totalContributions } }
  recent: contributionsCollection(from: "%s") { contributionCalendar { totalContributions } }
}"""


def parse_time(value):
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _query(logins, since):
    parts = [f"u{i}: user(login: {json.dumps(login)}) {{ ...U }}" for i, login in enumerate(logins)]
    return "query {\n" + "\n".join(parts) + "\n}\n" + FRAGMENT % since


def _fetch(gh, logins, since):
    try:
        data = gh.graphql(_query(logins, since))
        return [data.get(f"u{i}") for i in range(len(logins))]
    except GitHubError as err:
        if len(logins) == 1:
            log(f"  ! could not enrich {logins[0]}: {err}")
            return [None]
        # A heavy batch can time out on GitHub's side; retry it in halves, down to one user at a time.
        half = len(logins) // 2
        return _fetch(gh, logins[:half], since) + _fetch(gh, logins[half:], since)


def enrich(gh, logins, batch_size=5, workers=4, now=None):
    now = now or datetime.now(timezone.utc)
    # Day precision keeps the query text (and so the cache key) stable within a day.
    since = (now - timedelta(days=90)).strftime("%Y-%m-%dT00:00:00Z")
    batches = [logins[i:i + batch_size] for i in range(0, len(logins), batch_size)]
    profiles = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for done, users in enumerate(pool.map(lambda b: _fetch(gh, b, since), batches), 1):
            for u in users:
                if not u:
                    continue
                try:
                    profiles.append(normalize(u))
                except (KeyError, TypeError, ValueError, AttributeError) as err:
                    log(f"  ! skipped {u.get('login', '?')}: unexpected profile data ({err!r})")
            if done % 5 == 0 or done == len(batches):
                log(f"  enriched {min(done * batch_size, len(logins))}/{len(logins)}")
    return profiles


def _nodes(connection):
    # GraphQL can return null for a whole connection or for single nodes it couldn't resolve.
    return [n for n in ((connection or {}).get("nodes") or []) if n]


def _text(value):
    return (value or "").strip()


def _total_contributions(collection):
    return ((collection or {}).get("contributionCalendar") or {}).get("totalContributions") or 0


def normalize(u):
    repos = [r for r in _nodes(u.get("repositories")) if r.get("nameWithOwner")]
    login_prefix = u["login"].lower() + "/"
    contributed = [r for r in _nodes(u.get("repositoriesContributedTo"))
                   if r.get("nameWithOwner") and not r["nameWithOwner"].lower().startswith(login_prefix)]
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
        "contributed": [{"name": r["nameWithOwner"], "stars": r.get("stargazerCount") or 0} for r in contributed],
        "contrib_year": _total_contributions(u.get("year")),
        "contrib_90d": _total_contributions(u.get("recent")),
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
