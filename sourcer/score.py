"""Explainable 0-100 seniority/fit score, plus reasons and an outreach opener."""

import math
import re
from datetime import datetime, timezone

DEFAULT_WEIGHTS = {"impact": 25, "seed": 20, "notable": 10, "activity": 15, "experience": 10, "fit": 20}
SENIORITY = ["senior", "staff", "principal", "lead", "founder", "co-founder", "cofounder", "cto", "architect",
             "phd", "head of", "vp", "director", "researcher", "maintainer", "core team", "core dev", "professor"]
TIERS = [(65, "A"), (45, "B"), (0, "C")]


def fmt(n):
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k".replace(".0k", "k")
    return str(n)


def log_scale(x, full):
    return min(1.0, math.log10(1 + max(0, x)) / math.log10(1 + full))


def word_hits(text, words):
    return [w for w in words if re.search(rf"(?<![a-z0-9]){re.escape(w.lower())}(?![a-z0-9])", text)]


RECENT_ONLY = 5  # weight for a seed repo they contributed to recently but whose commit count we don't know


def seed_contributions(profile, role, discovered):
    """Commit counts to the role's seed repos (from discovery) and seed repos seen only in recent activity."""
    seeds = {r.lower(): r for r in role["seed_repos"]}
    counts = {seeds[repo.lower()]: n for repo, n in discovered.items() if repo.lower() in seeds}
    recent = {seeds[r["name"].lower()] for r in profile["contributed"] if r["name"].lower() in seeds} - counts.keys()
    return counts, recent


def score(profile, role, discovered=None, now=None):
    now = now or datetime.now(timezone.utc)
    weights = {**DEFAULT_WEIGHTS, **role.get("weights", {})}
    parts, reasons, flags = {}, [], []

    # Impact: stars on projects they own.
    parts["impact"] = log_scale(profile["owned_stars"], 5000)
    top = profile["top_repo"]
    if top and profile["owned_stars"] >= 50:
        reasons.append(f"★{fmt(profile['owned_stars'])} on own repos — top: {top['name']} (★{fmt(top['stars'])})")

    # Depth in the role's flagship projects.
    counts, recent = seed_contributions(profile, role, discovered or {})
    seeds = {**counts, **{repo: RECENT_ONLY for repo in recent}}
    parts["seed"] = log_scale(sum(seeds.values()), 200)
    if seeds:
        best = sorted(counts.items(), key=lambda kv: kv[1], reverse=True)[:3]
        labels = [f"{repo} ({n} commits)" for repo, n in best] + [f"{repo} (recent)" for repo in sorted(recent)]
        reasons.append("Contributes to " + ", ".join(labels[:3]))

    # Other well-known projects they've contributed to in the last year.
    seen = {repo.lower() for repo in seeds}
    notable = sorted((r for r in profile["contributed"] if r["stars"] >= 1000 and r["name"].lower() not in seen),
                     key=lambda r: r["stars"], reverse=True)
    best_stars = notable[0]["stars"] if notable else 0
    parts["notable"] = 0.5 * min(1.0, len(notable) / 5) + 0.5 * log_scale(best_stars, 100_000)
    if notable:
        reasons.append("Also contributes to " + ", ".join(f"{r['name']} (★{fmt(r['stars'])})" for r in notable[:3]))

    # Activity.
    year, last_90d = profile["contrib_year"], profile["contrib_90d"]
    parts["activity"] = 0.7 * log_scale(year, 1500) + 0.3 * min(1.0, last_90d / 50)
    if year:
        reasons.append(f"{fmt(year)} contributions in the last year ({fmt(last_90d)} in the last 90 days)")
    last_push = profile["last_push"]
    if year == 0 and (not last_push or (now - last_push).days > 365):
        flags.append("inactive 12+ months")

    # Experience proxies.
    years = (now - profile["created_at"]).days / 365.25 if profile["created_at"] else 0
    parts["experience"] = 0.5 * min(1.0, years / 10) + 0.5 * log_scale(profile["followers"], 2000)
    reasons.append(f"On GitHub since {profile['created_at'].year if profile['created_at'] else '?'} · "
                   f"{fmt(profile['followers'])} followers")

    # Role fit: languages, topics, bio keywords, seniority words.
    role_langs = {l.lower() for l in role["languages"]}
    total_langs = sum(profile["languages"].values())
    lang_share = (sum(n for l, n in profile["languages"].items() if l.lower() in role_langs) / total_langs
                  if total_langs else 0)
    topic_hits = sorted({t for t in profile["topics"] if t.lower() in {x.lower() for x in role["topics"]}})
    text = " ".join([profile["bio"], profile["company"], " ".join(profile["orgs"])]).lower()
    keyword_hits = word_hits(text, role.get("keywords", []))
    senior_hits = word_hits(profile["bio"].lower() + " " + profile["company"].lower(), SENIORITY)
    parts["fit"] = (0.35 * lang_share + 0.25 * min(1.0, len(topic_hits) / 3)
                    + 0.2 * min(1.0, len(keyword_hits) / 2) + 0.2 * (1.0 if senior_hits else 0.0))
    top_langs = [l for l, _ in profile["languages"].most_common(4)]
    if top_langs:
        reasons.append("Writes " + ", ".join(top_langs) + (f" · topics: {', '.join(topic_hits[:4])}" if topic_hits else ""))
    if profile["bio"] and (senior_hits or keyword_hits):
        bio = profile["bio"] if len(profile["bio"]) <= 110 else profile["bio"][:107] + "…"
        reasons.append(f"Bio: “{bio}”")

    bonus = 0
    if profile["hireable"]:
        bonus += 2
        reasons.append("Marked “available for hire” on GitHub")
    if not (profile["email"] or profile["website"] or profile["twitter"]):
        flags.append("no public contact")

    total = sum(parts[k] * weights[k] for k in weights) / sum(weights.values()) * 100 + bonus
    value = max(0, min(100, round(total)))
    return {
        "score": value,
        "tier": next(t for cut, t in TIERS if value >= cut),
        "breakdown": {k: round(parts[k] * 100) for k in weights},
        "reasons": reasons,
        "flags": flags,
        "seed_contribs": counts,
        "seed_recent": sorted(recent),
        "hook": outreach_hook(profile, counts, recent, notable),
    }


def outreach_hook(profile, counts, recent, notable):
    words = profile["name"].split()
    first = words[0] if words else profile["login"]
    if counts or recent:
        repo, n = max(counts.items(), key=lambda kv: kv[1]) if counts else (sorted(recent)[0], 0)
        depth = f"your {n} commits" if n >= 20 else "your contributions"
        return f"Hi {first} — came across {depth} to {repo}; that's exactly the depth our client is looking for."
    top = profile["top_repo"]
    if top and top["stars"] >= 100:
        return f"Hi {first} — {top['name']} (★{fmt(top['stars'])}) caught my eye; really impressive work."
    if notable:
        return f"Hi {first} — saw your work on {notable[0]['name']}; I'd love to tell you about a role that fits it."
    return f"Hi {first} — your GitHub work stood out; I'd love to share a senior role I think you'd find interesting."
