"""CSV, JSON and HTML output."""

import csv
import html
import json
from datetime import datetime

from .enrich import linkedin_search
from .score import fmt

CSV_FIELDS = ["rank", "tier", "score", "login", "name", "profile_url", "location", "company", "email", "email_source",
              "website", "twitter", "linkedin", "linkedin_search", "hireable", "followers", "owned_stars", "top_repo", "seed_contributions",
              "contributions_last_year", "contributions_90d", "top_languages", "flags", "reasons", "outreach_hook"]


def rows(candidates):
    for rank, (p, s) in enumerate(candidates, 1):
        seeds = [f"{r}:{n}" for r, n in sorted(s["seed_contribs"].items(), key=lambda kv: -kv[1])]
        seeds += [f"{r}:recent" for r in s["seed_recent"]]
        yield {
            "rank": rank, "tier": s["tier"], "score": s["score"], "login": p["login"], "name": p["name"],
            "profile_url": p["url"], "location": p["location"], "company": p["company"], "email": p["email"],
            "email_source": p["email_source"], "website": p["website"],
            "twitter": f"https://x.com/{p['twitter']}" if p["twitter"] else "",
            "linkedin": p.get("linkedin", ""),
            "linkedin_search": "" if p.get("linkedin") else linkedin_search(p["name"], p["company"]),
            "hireable": "yes" if p["hireable"] else "",
            "followers": p["followers"], "owned_stars": p["owned_stars"],
            "top_repo": p["top_repo"]["name"] if p["top_repo"] else "", "seed_contributions": "; ".join(seeds),
            "contributions_last_year": p["contrib_year"], "contributions_90d": p["contrib_90d"],
            "top_languages": ", ".join(l for l, _ in p["languages"].most_common(4)),
            "flags": "; ".join(s["flags"]), "reasons": " | ".join(s["reasons"]), "outreach_hook": s["hook"],
        }


FORMULA_START = ("=", "+", "-", "@", "\t", "\r")


def _cell(value):
    """Stop Excel/Sheets from running profile text (say, a name of =HYPERLINK(...)) as a formula."""
    if isinstance(value, str) and value.startswith(FORMULA_START):
        return "'" + value
    return value


def write_csv(path, candidates):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows({k: _cell(v) for k, v in row.items()} for row in rows(candidates))


def write_json(path, candidates, meta):
    out = {"meta": meta, "candidates": [
        {**row, "breakdown": s["breakdown"]} for row, (_, s) in zip(rows(candidates), candidates)]}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, default=str)


def _card(rank, p, s):
    e = html.escape
    links = [f'<a href="{e(p["url"])}" target="_blank" rel="noopener">GitHub</a>']
    if p["email"]:
        label = "email" + (" (from commits)" if p["email_source"] == "commit" else "")
        links.append(f'<a href="mailto:{e(p["email"])}">{e(p["email"])}</a> <span class="muted">{label}</span>')
    if p["website"]:
        site = p["website"] if p["website"].lower().startswith(("http://", "https://")) else "https://" + p["website"]
        links.append(f'<a href="{e(site)}" target="_blank" rel="noopener">website</a>')
    if p["twitter"]:
        links.append(f'<a href="https://x.com/{e(p["twitter"])}" target="_blank" rel="noopener">@{e(p["twitter"])}</a>')
    if p.get("linkedin"):
        links.append(f'<a href="{e(p["linkedin"])}" target="_blank" rel="noopener">LinkedIn</a>')
    elif linkedin_search(p["name"], p["company"]):
        links.append(f'<a href="{e(linkedin_search(p["name"], p["company"]))}" target="_blank" rel="noopener" '
                     f'title="LinkedIn search by name and company; check it\'s the right person">find on LinkedIn</a>')
    bars = "".join(f'<div class="bar"><span>{e(k)}</span><i style="--w:{v}%"></i><b>{v}</b></div>'
                   for k, v in s["breakdown"].items())
    flags = "".join(f'<span class="flag">{e(f)}</span>' for f in s["flags"])
    meta = " · ".join(e(x) for x in [p["company"], p["location"] or "location unknown"] if x)
    search = " ".join([p["login"], p["name"], p["company"], p["location"], p["bio"], *s["reasons"]]).lower()
    return f"""
<article class="card" data-tier="{s['tier']}" data-search="{e(search)}">
  <header>
    <div class="rank">#{rank}</div>
    <img src="https://github.com/{e(p['login'])}.png?size=96" alt="" loading="lazy" width="48" height="48">
    <div class="who">
      <h2>{e(p['name'] or p['login'])} <span class="muted">@{e(p['login'])}</span></h2>
      <div class="muted">{meta}</div>
    </div>
    <div class="score tier-{s['tier']}"><b>{s['score']}</b><span>Tier {s['tier']}</span></div>
  </header>
  {f'<p class="bio">{e(p["bio"])}</p>' if p["bio"] else ''}
  <ul class="reasons">{''.join(f'<li>{e(r)}</li>' for r in s['reasons'])}</ul>
  <div class="bars">{bars}</div>
  <div class="links">{' · '.join(links)} {flags}</div>
  <div class="hook"><span>{e(s['hook'])}</span><button type="button" data-copy="{e(s['hook'])}">Copy opener</button></div>
</article>"""


PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root {{ --bg:#f7f7f5; --card:#fff; --ink:#1d1d1b; --muted:#6b6b66; --line:#e4e3de; --accent:#2f6fde;
        --a:#1f8a4c; --b:#b7791f; --c:#8a8a85; --barbg:#efeee9; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg:#141413; --card:#1f1f1d; --ink:#ecebe6; --muted:#9a9993;
        --line:#33322f; --accent:#7aa7ff; --a:#4cc488; --b:#e0a948; --c:#8a8a85; --barbg:#2a2a27; }} }}
* {{ box-sizing:border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif; }}
main {{ max-width:980px; margin:0 auto; padding:24px 16px 64px; }}
h1 {{ font-size:24px; margin:0 0 4px; }}
.muted {{ color:var(--muted); font-weight:400; }}
.summary {{ display:flex; flex-wrap:wrap; gap:8px; margin:16px 0; }}
.pill {{ background:var(--card); border:1px solid var(--line); border-radius:999px; padding:4px 12px; font-size:13px; }}
.controls {{ display:flex; flex-wrap:wrap; gap:8px; margin:16px 0 20px; position:sticky; top:0; background:var(--bg); padding:8px 0; z-index:1; }}
.controls input {{ flex:1; min-width:200px; padding:8px 12px; border:1px solid var(--line); border-radius:8px; background:var(--card); color:var(--ink); font:inherit; }}
.controls button, .hook button {{ border:1px solid var(--line); background:var(--card); color:var(--ink); border-radius:8px; padding:6px 12px; font:inherit; font-size:13px; cursor:pointer; }}
.controls button[aria-pressed=true] {{ background:var(--ink); color:var(--bg); }}
.card {{ background:var(--card); border:1px solid var(--line); border-radius:12px; padding:16px; margin-bottom:14px; }}
.card header {{ display:flex; align-items:center; gap:12px; }}
.card img {{ border-radius:50%; flex:none; }}
.rank {{ color:var(--muted); font-size:13px; width:32px; flex:none; }}
.who {{ flex:1; min-width:0; }}
.who h2 {{ font-size:17px; margin:0; overflow-wrap:anywhere; }}
.score {{ text-align:center; flex:none; min-width:64px; }}
.score b {{ display:block; font-size:26px; line-height:1; }}
.score span {{ font-size:12px; }}
.tier-A b, .tier-A span {{ color:var(--a); }} .tier-B b, .tier-B span {{ color:var(--b); }} .tier-C b, .tier-C span {{ color:var(--c); }}
.bio {{ margin:10px 0 0; color:var(--muted); font-style:italic; }}
.reasons {{ margin:10px 0; padding-left:20px; }}
.reasons li {{ margin:2px 0; overflow-wrap:anywhere; }}
.bars {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(150px,1fr)); gap:4px 16px; margin:8px 0 12px; font-size:12px; }}
.bar {{ display:grid; grid-template-columns:66px 1fr 24px; align-items:center; gap:6px; color:var(--muted); }}
.bar i {{ height:6px; border-radius:3px; background:linear-gradient(90deg,var(--accent) var(--w),var(--barbg) var(--w)); }}
.links {{ font-size:14px; overflow-wrap:anywhere; }}
a {{ color:var(--accent); }}
.flag {{ display:inline-block; font-size:12px; border:1px solid var(--line); color:var(--muted); border-radius:6px; padding:0 6px; margin-left:6px; }}
.hook {{ display:flex; gap:10px; align-items:center; margin-top:12px; padding:10px 12px; background:var(--barbg); border-radius:8px; font-size:14px; }}
.hook span {{ flex:1; }}
.hidden {{ display:none; }}
@media (max-width:560px) {{ .rank {{ display:none; }} .hook {{ flex-direction:column; align-items:stretch; }} }}
</style></head>
<body><main>
<h1>{heading}</h1>
<div class="muted">Generated {generated} · {query}</div>
<div class="summary">{summary}</div>
<div class="controls">
  <input id="q" type="search" placeholder="Filter by name, company, repo, location…">
  <button type="button" data-tier="all" aria-pressed="true">All</button>
  <button type="button" data-tier="A" aria-pressed="false">Tier A</button>
  <button type="button" data-tier="B" aria-pressed="false">Tier B</button>
  <button type="button" data-tier="C" aria-pressed="false">Tier C</button>
</div>
{cards}
</main>
<script>
let tier = "all";
const cards = [...document.querySelectorAll(".card")];
const q = document.getElementById("q");
function apply() {{
  const term = q.value.trim().toLowerCase();
  for (const c of cards) c.classList.toggle("hidden",
    (tier !== "all" && c.dataset.tier !== tier) || (term && !c.dataset.search.includes(term)));
}}
q.addEventListener("input", apply);
for (const b of document.querySelectorAll(".controls button")) b.addEventListener("click", () => {{
  tier = b.dataset.tier;
  for (const o of document.querySelectorAll(".controls button")) o.setAttribute("aria-pressed", o === b);
  apply();
}});
for (const b of document.querySelectorAll("[data-copy]")) b.addEventListener("click", async () => {{
  try {{ await navigator.clipboard.writeText(b.dataset.copy); b.textContent = "Copied"; }}
  catch {{ b.textContent = "Copy failed"; }}
  setTimeout(() => b.textContent = "Copy opener", 1500);
}});
</script></body></html>"""


def write_html(path, candidates, meta):
    tiers = {t: sum(1 for _, s in candidates if s["tier"] == t) for t in "ABC"}
    with_email = sum(1 for p, _ in candidates if p["email"])
    summary = [f"{len(candidates)} candidates", f"Tier A: {tiers['A']}", f"Tier B: {tiers['B']}",
               f"Tier C: {tiers['C']}", f"with email: {with_email}", f"discovered: {fmt(meta['discovered'])}",
               f"enriched: {fmt(meta['enriched'])}"]
    query = ", ".join(f"{k}: {v}" for k, v in meta["query"].items() if v)
    page = PAGE.format(
        title=html.escape(f"{meta['role_label']} sourcing"),
        heading=html.escape(f"{meta['role_label']} — GitHub sourcing"),
        generated=html.escape(datetime.fromisoformat(meta["generated_at"]).strftime("%d %b %Y %H:%M")),
        query=html.escape(query),
        summary="".join(f'<span class="pill">{html.escape(x)}</span>' for x in summary),
        cards="".join(_card(i, p, s) for i, (p, s) in enumerate(candidates, 1)) or "<p>No candidates matched.</p>",
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(page)
