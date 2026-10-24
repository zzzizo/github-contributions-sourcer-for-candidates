"""Candidate discovery: seed-repo contributors, user search, and popular-project owners."""

import re

from .github import GitHubError, log

# Shorthands a recruiter is likely to type, expanded to the strings people put in their profiles.
LOCATION_ALIASES = {
    "sf": ["san francisco", "sf", "bay area", "palo alto", "mountain view", "berkeley", "oakland", "san jose", "menlo park"],
    "bay area": ["san francisco", "sf", "bay area", "palo alto", "mountain view", "berkeley", "oakland", "san jose", "menlo park"],
    "nyc": ["new york", "nyc", "brooklyn", "manhattan"],
    "new york": ["new york", "nyc", "brooklyn", "manhattan"],
    "london": ["london"],
    "uk": ["united kingdom", "uk", "england", "london", "manchester", "cambridge", "oxford", "edinburgh", "scotland", "bristol"],
    "usa": ["united states", "usa", "us", "san francisco", "new york", "seattle", "boston", "austin", "los angeles", "chicago"],
    "us": ["united states", "usa", "us", "san francisco", "new york", "seattle", "boston", "austin", "los angeles", "chicago"],
    "germany": ["germany", "deutschland", "berlin", "munich", "münchen", "hamburg"],
    "europe": ["europe", "london", "berlin", "paris", "amsterdam", "zurich", "zürich", "munich", "lisbon", "barcelona",
               "madrid", "stockholm", "dublin", "warsaw", "prague", "vienna", "copenhagen"],
    "india": ["india", "bangalore", "bengaluru", "hyderabad", "pune", "mumbai", "delhi", "chennai", "gurgaon", "noida"],
    "dubai": ["dubai", "uae", "united arab emirates", "abu dhabi"],
    "singapore": ["singapore"],
    "remote": ["remote"],
}


def expand_locations(raw):
    """'sf, london' -> (match terms, search terms). Search terms are one canonical name per entry."""
    match_terms, search_terms = [], []
    for part in (raw or "").split(","):
        key = part.strip().lower()
        if not key:
            continue
        aliases = LOCATION_ALIASES.get(key, [key])
        match_terms.extend(aliases)
        search_terms.append(aliases[0])
    return list(dict.fromkeys(match_terms)), list(dict.fromkeys(search_terms))


def location_matches(location, terms):
    if not terms:
        return True
    text = (location or "").lower()
    # Letter boundaries so "uk" doesn't match "ukraine" and "sf" doesn't match "sfax".
    return any(re.search(rf"(?<![a-z]){re.escape(t)}(?![a-z])", text) for t in terms)


def is_bot(login):
    low = login.lower()
    return (low.endswith(("[bot]", "-bot", "_bot"))
            or low in {"dependabot", "renovate", "github-actions", "copilot", "web-flow", "ghost", "pre-commit-ci"})


class Pool:
    """Discovered logins with where we found them and their contribution counts to seed repos."""

    def __init__(self):
        self.entries = {}

    def add(self, login, source, repo=None, contributions=0):
        if not login or is_bot(login):
            return
        entry = self.entries.setdefault(login.lower(), {"login": login, "sources": set(), "seed_contribs": {}})
        entry["sources"].add(source)
        if repo:
            entry["seed_contribs"][repo] = max(entry["seed_contribs"].get(repo, 0), contributions)

    def __len__(self):
        return len(self.entries)

    def ranked(self):
        """Strongest discovery signal first, so a capped enrichment keeps the best leads."""
        def strength(e):
            return (sum(min(c, 500) for c in e["seed_contribs"].values()) + 50 * len(e["sources"]))
        return sorted(self.entries.values(), key=strength, reverse=True)


def from_seed_repos(gh, pool, repos, pages):
    for repo in repos:
        found = 0
        for page in range(1, pages + 1):
            try:
                data = gh.rest(f"/repos/{repo}/contributors", {"per_page": 100, "page": page})
            except GitHubError as err:
                log(f"  ! {repo}: {err}")
                break
            if not data:
                break
            for c in data:
                if c.get("type") == "User":
                    pool.add(c["login"], f"contributor:{repo}", repo, c.get("contributions", 0))
                    found += 1
            if len(data) < 100:
                break
        log(f"  {repo}: {found} contributors")


SEARCH_USERS = """
query($q: String!, $n: Int!, $after: String) {
  search(query: $q, type: USER, first: $n, after: $after) {
    userCount
    pageInfo { hasNextPage endCursor }
    nodes { ... on User { login } }
  }
}"""


def from_user_search(gh, pool, languages, locations, min_followers, per_query):
    for lang in languages:
        for loc in locations or [None]:
            query = f'language:"{lang}" followers:>={min_followers} type:user sort:followers-desc'
            if loc:
                query += ' location:"{}"'.format(loc.replace('"', ""))  # a stray quote would break the query
            found, after = 0, None
            while found < per_query:
                try:
                    data = gh.graphql(SEARCH_USERS, {"q": query, "n": min(100, per_query - found), "after": after})
                except GitHubError as err:
                    log(f"  ! search {query!r}: {err}")
                    break
                result = data["search"]
                for node in result["nodes"]:
                    if node.get("login"):
                        pool.add(node["login"], f"search:{lang}" + (f"@{loc}" if loc else ""))
                        found += 1
                if not result["pageInfo"]["hasNextPage"] or not result["nodes"]:
                    break
                after = result["pageInfo"]["endCursor"]
            log(f"  search {lang}" + (f" in {loc}" if loc else "") + f": {found} users")


SEARCH_REPOS = """
query($q: String!, $n: Int!) {
  search(query: $q, type: REPOSITORY, first: $n) {
    nodes { ... on Repository { nameWithOwner stargazerCount owner { __typename login } } }
  }
}"""


def from_topic_repos(gh, pool, topics, min_stars, per_topic):
    for topic in topics:
        query = f"topic:{topic} stars:>={min_stars} fork:false sort:stars-desc"
        try:
            data = gh.graphql(SEARCH_REPOS, {"q": query, "n": min(100, per_topic)})
        except GitHubError as err:
            log(f"  ! topic {topic}: {err}")
            continue
        found = 0
        for repo in data["search"]["nodes"]:
            owner = repo.get("owner") or {}
            if owner.get("__typename") == "User":
                pool.add(owner["login"], f"owner:{topic}")
                found += 1
        log(f"  topic {topic}: {found} project owners")
