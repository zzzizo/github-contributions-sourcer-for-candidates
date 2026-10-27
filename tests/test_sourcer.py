import csv
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from sourcer import cli, discover, enrich, jd, places, report
from sourcer.cli import load_roles, read_excludes, role_problems
from sourcer.enrich import JUNK_EMAIL, normalize
from sourcer.github import AuthError, GitHub, GitHubError, _seconds_until
from sourcer.score import score

NOW = datetime(2026, 10, 24, tzinfo=timezone.utc)


def raw_user(**overrides):
    user = {
        "login": "ada", "name": "Ada Lovelace", "bio": "Staff ML engineer, LLM inference", "company": "@acme",
        "location": "London, UK", "email": "", "websiteUrl": "ada.dev", "twitterUsername": None, "isHireable": True,
        "createdAt": "2012-03-01T00:00:00Z", "url": "https://github.com/ada",
        "followers": {"totalCount": 1500}, "organizations": {"nodes": [{"login": "huggingface"}]},
        "repositories": {"totalCount": 2, "nodes": [
            {"nameWithOwner": "ada/fastinfer", "stargazerCount": 4200, "pushedAt": "2026-10-01T00:00:00Z",
             "description": "Fast LLM inference", "primaryLanguage": {"name": "Python"},
             "repositoryTopics": {"nodes": [{"topic": {"name": "llm-inference"}}, {"topic": {"name": "pytorch"}}]}},
            {"nameWithOwner": "ada/kernels", "stargazerCount": 300, "pushedAt": "2026-06-01T00:00:00Z",
             "description": None, "primaryLanguage": {"name": "CUDA"}, "repositoryTopics": {"nodes": []}},
        ]},
        "repositoriesContributedTo": {"nodes": [
            {"nameWithOwner": "vllm-project/vllm", "stargazerCount": 60000, "primaryLanguage": {"name": "Python"}},
            {"nameWithOwner": "pytorch/pytorch", "stargazerCount": 90000, "primaryLanguage": {"name": "Python"}},
            {"nameWithOwner": "ada/fastinfer", "stargazerCount": 4200, "primaryLanguage": {"name": "Python"}},
        ]},
        "year": {"contributionCalendar": {"totalContributions": 1800}},
        "recent": {"contributionCalendar": {"totalContributions": 400}},
    }
    user.update(overrides)
    return user


class LocationTests(unittest.TestCase):
    def test_aliases_and_boundaries(self):
        terms, search = discover.expand_locations("sf, uk")
        self.assertEqual(search, ["san francisco", "united kingdom", "bay area", "london"])  # each place first
        self.assertTrue(discover.location_matches("Palo Alto, CA", terms))
        self.assertTrue(discover.location_matches("London, UK", terms))
        self.assertFalse(discover.location_matches("Kyiv, Ukraine", terms))
        self.assertFalse(discover.location_matches("Sfax, Tunisia", terms))
        self.assertTrue(discover.location_matches("", []))

    def test_custom_location_passes_through(self):
        terms, search = discover.expand_locations("Toronto")
        self.assertEqual((terms, search), (["toronto"], ["toronto"]))

    def test_countries_and_regions_cover_their_cities(self):
        brazil = places.resolve("Brazil")["match"]
        for location in ["São Paulo, Brasil", "Sao Paulo", "Florianópolis - SC", "brazil"]:
            self.assertTrue(places.matches(location, brazil), location)
        self.assertFalse(places.matches("Lisbon, Portugal", brazil))
        latam = places.resolve("latam")["match"]
        self.assertTrue(places.matches("Bogotá, Colombia", latam))
        self.assertTrue(places.matches("Ciudad de México", latam))
        self.assertTrue(places.matches("Gdańsk", places.resolve("poland")["match"]))
        self.assertTrue(places.matches("Łódź", places.resolve("Lodz")["match"]))
        self.assertTrue(places.matches("Bangalore, India", places.resolve("Bengaluru")["match"]))

    def test_states_match_postal_codes_but_not_ambiguous_ones(self):
        texas = places.resolve("texas")["match"]
        self.assertTrue(places.matches("Austin, TX", texas))
        self.assertTrue(places.matches("Houston, Texas, USA", texas))
        usa = places.resolve("USA")["match"]
        for location in ["Boston, MA", "Brooklyn", "Seattle", "Washington, D.C."]:
            self.assertTrue(places.matches(location, usa), location)
        for location in ["Berlin, DE", "Bogotá, CO", "La Paz, Bolivia", "Toronto, ON, CA", "Kyiv, Ukraine"]:
            self.assertFalse(places.matches(location, usa), location)

    def test_state_or_country_after_a_place_narrows_it(self):
        for raw in ["Austin, TX", "Austin, Texas, USA"]:
            where = places.resolve(raw)
            self.assertEqual((len(where["places"]), where["match"], where["search"]), (1, ["austin"], ["austin"]))
        self.assertEqual(places.resolve("sf, usa")["places"][0]["name"], "SF Bay Area")
        where = places.resolve("Smalltown, Germany")
        self.assertEqual((where["match"], where["places"][0]["input"]), (["smalltown"], "Smalltown, Germany"))
        self.assertEqual(len(places.resolve("london, sf")["places"]), 2)  # two cities: either one

    def test_unknown_short_and_anywhere(self):
        where = places.resolve("anywhere, x, Atlantis")
        self.assertEqual(where["match"], ["atlantis"])
        self.assertEqual(len(where["notes"]), 3)
        self.assertEqual(places.resolve("worldwide")["match"], [])
        self.assertLessEqual(len(places.resolve("europe, latam, apac, emea, nyc")["search"]), places.MAX_SEARCH_TERMS)


class PoolTests(unittest.TestCase):
    def test_bots_skipped_and_ranked_by_signal(self):
        pool = discover.Pool()
        pool.add("dependabot[bot]", "x")
        pool.add("light", "search:Python")
        pool.add("Deep", "contributor:a/b", "a/b", 300)
        pool.add("deep", "owner:llm")
        self.assertEqual(len(pool), 2)
        self.assertEqual(pool.ranked()[0]["login"], "Deep")
        self.assertEqual(pool.ranked()[0]["sources"], {"contributor:a/b", "owner:llm"})


class ScoreTests(unittest.TestCase):
    def setUp(self):
        self.role = load_roles()["ai"]

    def test_strong_profile_scores_tier_a_with_reasons(self):
        profile = normalize(raw_user())
        self.assertEqual([r["name"] for r in profile["contributed"]], ["pytorch/pytorch", "vllm-project/vllm"])
        result = score(profile, self.role, {"vllm-project/vllm": 240}, NOW)
        self.assertEqual(result["tier"], "A")
        self.assertEqual(result["seed_contribs"], {"vllm-project/vllm": 240})
        self.assertEqual(result["seed_recent"], ["pytorch/pytorch"])
        text = " ".join(result["reasons"])
        self.assertIn("vllm-project/vllm (240 commits)", text)
        self.assertIn("pytorch/pytorch (recent)", text)
        self.assertIn("your 240 commits to vllm-project/vllm", result["hook"])
        self.assertNotIn("no public contact", result["flags"])

    def test_weak_inactive_profile(self):
        old = (NOW - timedelta(days=800)).strftime("%Y-%m-%dT%H:%M:%SZ")
        profile = normalize(raw_user(
            bio="", company="", websiteUrl=None, isHireable=False, createdAt="2024-01-01T00:00:00Z",
            followers={"totalCount": 3}, organizations={"nodes": []}, repositoriesContributedTo={"nodes": []},
            repositories={"totalCount": 1, "nodes": [
                {"nameWithOwner": "ada/todo", "stargazerCount": 0, "pushedAt": old, "description": None,
                 "primaryLanguage": {"name": "HTML"}, "repositoryTopics": {"nodes": []}}]},
            year={"contributionCalendar": {"totalContributions": 0}},
            recent={"contributionCalendar": {"totalContributions": 0}}))
        result = score(profile, self.role, {}, NOW)
        self.assertEqual(result["tier"], "C")
        self.assertIn("inactive 12+ months", result["flags"])
        self.assertIn("no public contact", result["flags"])

    def test_seed_repo_case_insensitive(self):
        profile = normalize(raw_user())
        result = score(profile, self.role, {"VLLM-Project/VLLM": 50}, NOW)
        self.assertEqual(result["seed_contribs"], {"vllm-project/vllm": 50})

    def test_all_roles_load_with_required_keys(self):
        for key, role in load_roles().items():
            for field in ("label", "languages", "search_languages", "topics", "seed_repos"):
                self.assertTrue(role.get(field), f"{key} missing {field}")
            for repo in role["seed_repos"]:
                self.assertRegex(repo, r"^[\w.-]+/[\w.-]+$")


class EmailAndExcludeTests(unittest.TestCase):
    def test_junk_emails(self):
        for junk in ["123+ada@users.noreply.github.com", "root@localhost", "dev@example.com", "ada@macbook.local"]:
            self.assertTrue(JUNK_EMAIL.search(junk), junk)
        self.assertFalse(JUNK_EMAIL.search("ada@lovelace.dev"))

    def test_read_excludes(self):
        with tempfile.TemporaryDirectory() as d:
            prev = Path(d, "candidates.csv")
            with open(prev, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=["rank", "login"])
                w.writeheader()
                w.writerow({"rank": 1, "login": "Ada"})
            plain = Path(d, "list.txt")
            plain.write_text("https://github.com/Grace-H\nlinus\n\nnot a login!\n")
            self.assertEqual(read_excludes([str(prev), str(plain)]), {"ada", "grace-h", "linus"})

    def test_read_excludes_spreadsheet_export(self):
        # Excel/Sheets exports add a BOM and may quote or capitalise headers.
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, "export.csv")
            path.write_text('\ufeff"Rank","Login"\n1," Ada "\n2,\n', encoding="utf-8")
            self.assertEqual(read_excludes([str(path)]), {"ada"})

    def test_missing_exclude_file_exits_cleanly(self):
        with self.assertRaises(SystemExit) as ctx:
            read_excludes(["/nonexistent/candidates.csv"])
        self.assertIn("Could not read --exclude file", str(ctx.exception.code))


class RoleValidationTests(unittest.TestCase):
    def test_valid_role_has_no_problems(self):
        self.assertEqual(role_problems(load_roles()["founding"]), [])

    def test_typos_are_reported(self):
        role = {**load_roles()["ai"], "weights": {"imapct": 10}, "seed_repos": ["vllm"], "min_followers": "50"}
        problems = " ".join(role_problems(role))
        self.assertIn("unknown weights imapct", problems)
        self.assertIn("bad seed repo 'vllm'", problems)
        self.assertIn("min_followers", problems)
        self.assertIn("missing label", " ".join(role_problems({"languages": ["Go"]})))


class ReportTests(unittest.TestCase):
    def test_outputs_escape_and_round_trip(self):
        profile = normalize(raw_user(name='<script>alert(1)</script>'))
        result = score(profile, load_roles()["ai"], {"vllm-project/vllm": 240}, NOW)
        meta = {"role": "ai", "role_label": "AI / ML Engineer", "generated_at": NOW.isoformat(),
                "discovered": 900, "enriched": 300, "query": {"location": "london"}}
        with tempfile.TemporaryDirectory() as d:
            report.write_csv(Path(d, "c.csv"), [(profile, result)])
            report.write_json(Path(d, "c.json"), [(profile, result)], meta)
            report.write_html(Path(d, "r.html"), [(profile, result)], meta)
            with open(Path(d, "c.csv"), newline="") as f:
                row = next(csv.DictReader(f))
            self.assertEqual((row["login"], row["tier"]), ("ada", "A"))
            self.assertEqual(json.loads(Path(d, "c.json").read_text())["candidates"][0]["login"], "ada")
            page = Path(d, "r.html").read_text()
            self.assertNotIn("<script>alert(1)", page)
            self.assertIn("&lt;script&gt;", page)

    def test_csv_neutralises_formulas(self):
        profile = normalize(raw_user(name='=HYPERLINK("http://evil","x")', company="@acme"))
        result = score(profile, load_roles()["ai"], {}, NOW)
        with tempfile.TemporaryDirectory() as d:
            report.write_csv(Path(d, "c.csv"), [(profile, result)])
            with open(Path(d, "c.csv"), newline="") as f:
                row = next(csv.DictReader(f))
        self.assertEqual(row["name"], '\'=HYPERLINK("http://evil","x")')
        self.assertEqual(row["company"], "'@acme")
        self.assertEqual(row["login"], "ada")


class LinkedInTests(unittest.TestCase):
    def test_found_in_social_links_website_or_bio(self):
        social = {"nodes": [{"provider": "TWITTER", "url": "https://twitter.com/ada"},
                            {"provider": "LINKEDIN", "url": "https://www.linkedin.com/in/ada-lovelace/"}]}
        self.assertEqual(normalize(raw_user(socialAccounts=social))["linkedin"], "https://www.linkedin.com/in/ada-lovelace")
        generic = {"nodes": [{"provider": "GENERIC", "url": "https://uk.linkedin.com/in/ada123"}]}
        self.assertEqual(normalize(raw_user(socialAccounts=generic))["linkedin"], "https://www.linkedin.com/in/ada123")
        self.assertEqual(normalize(raw_user(websiteUrl="linkedin.com/in/ada"))["linkedin"], "https://www.linkedin.com/in/ada")
        self.assertEqual(normalize(raw_user(bio="ML eng · linkedin.com/in/ada-l"))["linkedin"],
                         "https://www.linkedin.com/in/ada-l")
        self.assertEqual(normalize(raw_user())["linkedin"], "")

    def test_outputs_exact_link_or_search(self):
        linked = normalize(raw_user(socialAccounts={"nodes": [{"provider": "LINKEDIN", "url": "https://linkedin.com/in/ada"}]}))
        unlinked = normalize(raw_user(login="grace", name="Grace Hopper", company="@navy, Harvard"))
        rows = list(report.rows([(p, score(p, load_roles()["ai"], {}, NOW)) for p in (linked, unlinked)]))
        self.assertEqual((rows[0]["linkedin"], rows[0]["linkedin_search"]), ("https://www.linkedin.com/in/ada", ""))
        self.assertEqual(rows[1]["linkedin"], "")
        self.assertEqual(rows[1]["linkedin_search"],
                         "https://www.linkedin.com/search/results/people/?keywords=Grace+Hopper+navy")
        nameless = normalize(raw_user(name=None))
        self.assertEqual(next(report.rows([(nameless, score(nameless, load_roles()["ai"], {}, NOW))]))["linkedin_search"], "")


class RobustnessTests(unittest.TestCase):
    def test_sparse_profile_normalizes(self):
        sparse = {"login": "ghost-ish", "name": "   ", "repositories": None, "repositoriesContributedTo": {"nodes": [None]},
                  "organizations": None, "followers": None, "year": None, "recent": {}}
        profile = normalize(sparse)
        self.assertEqual((profile["repos"], profile["followers"], profile["contrib_year"]), ([], 0, 0))
        result = score(profile, load_roles()["ai"], {}, NOW)
        self.assertTrue(result["hook"].startswith("Hi ghost-ish"))

    def test_failed_batch_is_split_in_halves(self):
        class Flaky:
            calls = 0

            def graphql(self, query, variables=None):
                Flaky.calls += 1
                logins = re.findall(r'u(\d+): user\(login: "([^"]+)"\)', query)
                if len(logins) > 2:
                    raise GitHubError("502")
                return {f"u{i}": raw_user(login=login, url=f"https://github.com/{login}") for i, login in logins}

        names = [f"dev{i}" for i in range(8)]
        users = enrich.add_work(Flaky(), [{"login": n} for n in names], batch_size=8, workers=1)
        self.assertEqual([u["login"] for u in users], names)
        self.assertTrue(all(u["repositories"]["nodes"] for u in users))
        self.assertEqual(Flaky.calls, 7)  # 8 -> 4+4 -> 2+2+2+2, instead of 1 + 8 single calls

    def test_heavy_single_user_falls_back_to_repos_only(self):
        class Heavy:
            def graphql(self, query, variables=None):
                if "contributionsCollection" in query:
                    raise GitHubError("502")
                return {"u0": {"login": "big", "repositories": {"totalCount": 0, "nodes": []}}}

        users = enrich.add_work(Heavy(), [{"login": "big", "name": "Big"}], batch_size=2, workers=1)
        self.assertEqual(users[0]["name"], "Big")
        self.assertIn("repositories", users[0])

    def test_commit_counts_and_calendar(self):
        days = [{"date": "2026-10-01", "contributionCount": 7}, {"date": "2026-01-01", "contributionCount": 50}]
        user = raw_user(contributionsCollection={
            "commitContributionsByRepository": [
                {"repository": {"nameWithOwner": "vllm-project/vllm", "stargazerCount": 60000}, "contributions": {"totalCount": 42}},
                {"repository": {"nameWithOwner": "ada/fastinfer", "stargazerCount": 4200}, "contributions": {"totalCount": 9}}],
            "contributionCalendar": {"totalContributions": 57, "weeks": [{"contributionDays": days}]}})
        del user["repositoriesContributedTo"], user["year"], user["recent"]
        profile = normalize(user, NOW)
        self.assertEqual((profile["contrib_year"], profile["contrib_90d"]), (57, 7))
        self.assertEqual(profile["contributed"], [{"name": "vllm-project/vllm", "stars": 60000, "commits": 42}])
        result = score(profile, load_roles()["ai"], {}, NOW)
        self.assertEqual(result["seed_contribs"], {"vllm-project/vllm": 42})

    def test_retry_after_formats(self):
        self.assertEqual(_seconds_until("30"), 30)
        self.assertEqual(_seconds_until("not a date"), 60)
        self.assertGreater(_seconds_until("Wed, 21 Oct 2099 07:28:00 GMT"), 0)


class ClientTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.gh = GitHub("token", cache_dir=self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_partial_graphql_result_returned_but_not_cached(self):
        partial = {"data": {"u0": {"login": "ada"}, "u1": None}, "errors": [{"type": "SERVICE_UNAVAILABLE"}]}
        with mock.patch.object(self.gh, "_request", return_value=partial) as req:
            self.assertEqual(self.gh.graphql("q")["u0"], {"login": "ada"})
            self.gh.graphql("q")
        self.assertEqual(req.call_count, 2)

    def test_complete_graphql_result_cached(self):
        ok = {"data": {"u0": {"login": "ada"}, "u1": None}, "errors": [{"type": "NOT_FOUND"}]}
        with mock.patch.object(self.gh, "_request", return_value=ok) as req:
            self.gh.graphql("q")
            self.gh.graphql("q")
        self.assertEqual(req.call_count, 1)

    def test_graphql_rate_limit_waits_and_retries(self):
        limited = {"data": None, "errors": [{"type": "RATE_LIMITED"}]}
        ok = {"data": {"viewer": {"login": "ada"}}}
        with mock.patch.object(self.gh, "_request", side_effect=[limited, ok]), \
                mock.patch.object(self.gh, "_graphql_reset_wait", return_value=1), \
                mock.patch("sourcer.github.time.sleep") as sleep, mock.patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(self.gh.graphql("q"), {"viewer": {"login": "ada"}})
        sleep.assert_called_once_with(1)

    def test_graphql_total_failure_raises(self):
        with mock.patch.object(self.gh, "_request", return_value={"data": None, "errors": [{"type": "X"}]}):
            with self.assertRaises(GitHubError):
                self.gh.graphql("q")

    def test_bad_token_raises_auth_error(self):
        err = urllib.error.HTTPError("https://api.github.com/user", 401, "Unauthorized", {}, io.BytesIO(b"Bad credentials"))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(AuthError):
                self.gh.viewer()

    def test_expired_cache_pruned(self):
        old = Path(self.tmp.name, "old.json")
        old.write_text("{}")
        os.utime(old, (0, 0))
        GitHub("token", cache_dir=self.tmp.name)
        self.assertFalse(old.exists())


JD = """Senior Backend Engineer (Go) - Acme Pay | London (Hybrid)

About Acme
We're a Series A fintech. Our customers include Stripe and Shopify, and our offices are in Berlin.

What you'll do
- Build highly available payment APIs and ledger services in Go
- Own our Kafka event pipelines end to end

Requirements
- 5+ years of backend experience with Go
- Strong PostgreSQL skills
- Ex-Google or Stripe engineers are a plus

Nice to have
- Kubernetes and Terraform

We are not looking for frontend engineers. We don't need React experience.

Benefits
- Offices in San Francisco and New York
- Visa sponsorship is not available.
"""


class JobDescriptionTests(unittest.TestCase):
    def test_reads_the_jd(self):
        result = jd.analyze(JD)
        role, a = result["role"], result["analysis"]
        self.assertEqual(role["label"], "Senior Backend Engineer (Go)")  # company and location dropped
        self.assertEqual(result["key"], "jd-senior-backend-engineer-go")
        must = {s["name"] for s in a["skills"] if s["must"]}
        nice = {s["name"] for s in a["skills"] if not s["must"]}
        self.assertTrue({"Backend", "Payments", "Streaming", "PostgreSQL"} <= must, must)
        self.assertIn("Kubernetes", nice)
        self.assertFalse({"React", "Frontend"} & (must | nice))  # "not looking for" lines don't count
        self.assertEqual(role["search_languages"][0], "Go")
        self.assertEqual(a["location"], "london")  # Berlin (about) and SF/NY (benefits) are ignored
        self.assertTrue(a["onsite"])
        self.assertFalse(a["visa"])
        self.assertIn("senior", a["levels"])
        self.assertEqual(a["years"], 5)
        self.assertIn("google", a["companies"])
        self.assertNotIn("shopify", a["companies"])  # a customer, not a candidate signal
        self.assertEqual(role_problems(role), [])
        reqs = {r["name"]: r for r in role["requirements"]}
        self.assertEqual(reqs["Payments"]["languages"], [])  # writing Go isn't evidence of payments work
        self.assertTrue(reqs["Go"]["must"])
        self.assertFalse(any("&" in k or " / " in k for k in role["keywords"]))  # no "SRE & observability"
        self.assertEqual(jd.analyze("iOS Engineer\n\nRequirements\n- Shipped iOS apps")["role"]["requirements"][0]
                         ["languages"], ["Swift"])
        self.assertLessEqual(len(role["seed_repos"]), 15)
        self.assertGreater(role["weights"]["impact"], 0)

    def test_location_anywhere_in_the_world(self):
        result = jd.analyze("Backend Engineer\nLocation: Lisbon, Portugal (hybrid)\n\nRequirements\n- Go and Kafka")
        self.assertEqual(result["analysis"]["location"], "lisbon, portugal")
        self.assertEqual(places.resolve("lisbon, portugal")["match"], ["lisbon", "lisboa"])
        result = jd.analyze("Backend Engineer\nFully remote within Brazil or Argentina.\n\nRequirements\n- Go, Kafka")
        self.assertEqual(result["analysis"]["location"], "brazil, argentina")
        result = jd.analyze("Elixir Engineer\n\nRequirements\n- Phoenix LiveView, remote-friendly team")
        self.assertEqual(result["analysis"]["location"], "")

    def test_paragraph_jd_and_rejection_criteria(self):
        result = jd.analyze("Smart Contract Engineer\nRemote\n\nYou'll build our lending protocol. You should have "
                            "shipped production Solidity and know Foundry inside out.\n\nRejection criteria\n"
                            "- Only Python data science experience")
        a = result["analysis"]
        self.assertIn("Smart contracts", [s["name"] for s in a["skills"] if s["must"]])
        self.assertNotIn("Data science", [s["name"] for s in a["skills"]])
        self.assertEqual(a["location"], "")  # remote: search everywhere
        self.assertEqual(result["role"]["search_languages"][0], "Solidity")

    def test_rejects_empty_or_non_technical_text(self):
        with self.assertRaises(ValueError):
            jd.analyze("hi")
        with self.assertRaises(ValueError):
            jd.analyze("We are a friendly bakery looking for a cheerful person to bake bread every morning.")

    def test_github_refinement(self):
        class Repos:
            def graphql(self, query, variables=None):
                if variables:  # repository search
                    return {"search": {"nodes": [
                        {"nameWithOwner": "someone/awesome-go", "stargazerCount": 90000, "description": "A curated list"},
                        {"nameWithOwner": "go-kratos/kratos", "stargazerCount": 24000, "description": "Go framework",
                         "primaryLanguage": {"name": "Go"}}]}}
                out = {}
                for alias, owner, name in re.findall(r'(r\d+): repository\(owner: "([^"]+)", name: "([^"]+)"\)', query):
                    archived = f"{owner}/{name}" == "apache/kafka"
                    out[alias] = None if name == "pulsar" else {
                        "nameWithOwner": f"{owner}/{name}", "stargazerCount": 1000, "isArchived": archived,
                        "pushedAt": "2026-10-01T00:00:00Z", "primaryLanguage": {"name": "Go"}}
                return out

        result = jd.refine_with_github(Repos(), jd.analyze(JD), now=NOW)
        seeds = result["role"]["seed_repos"]
        self.assertNotIn("apache/kafka", seeds)    # archived
        self.assertNotIn("apache/pulsar", seeds)   # gone
        self.assertNotIn("someone/awesome-go", seeds)
        self.assertIn("go-kratos/kratos", seeds)
        info = {i["repo"]: i for i in result["analysis"]["seed_info"]}
        self.assertEqual(info["go-kratos/kratos"]["source"], "github")

    def test_requirements_feed_the_score(self):
        role = jd.analyze(JD)["role"]
        go_dev = normalize(raw_user(bio="Backend engineer, payments and Kafka", repositories={"totalCount": 1, "nodes": [
            {"nameWithOwner": "ada/ledger", "stargazerCount": 900, "pushedAt": "2026-10-01T00:00:00Z",
             "description": "Double-entry ledger in Go", "primaryLanguage": {"name": "Go"},
             "repositoryTopics": {"nodes": [{"topic": {"name": "payments"}}]}}]}))
        result = score(go_dev, role, {}, NOW)
        first = result["reasons"][0]
        self.assertTrue(first.startswith("Matches the JD: "), first)
        self.assertIn("Payments", first)
        self.assertIn("Backend", first)

    def test_save_role_appends_without_rewriting(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "ROOT", Path(d)):
            original = Path(__file__).resolve().parent.parent.joinpath("roles.json").read_text()
            Path(d, "roles.json").write_text(original)
            role = jd.analyze(JD)["role"]
            self.assertEqual(cli.save_role("jd-go-backend", role), "jd-go-backend")
            text = Path(d, "roles.json").read_text()
            self.assertTrue(text.startswith(original.rstrip()[:-1].rstrip()))  # existing entries untouched
            self.assertEqual(cli.load_roles()["jd-go-backend"]["label"], role["label"])
            with self.assertRaises(ValueError):
                cli.save_role("jd-go-backend", role)  # duplicate
            with self.assertRaises(ValueError):
                cli.save_role("Bad Key!", role)


class FakeGitHub:
    """Serves the three discovery calls and batched enrichment from fixtures."""

    def __init__(self):
        self.people = {
            "ada": raw_user(),
            "grace": raw_user(login="grace", name="Grace Hopper", location="San Francisco, CA",
                              url="https://github.com/grace"),
            "linus": raw_user(login="linus", name="Linus", location="Kyiv, Ukraine", url="https://github.com/linus"),
        }

    def rate_limit(self):
        return {"resources": {"core": {"limit": 5000, "remaining": 4999, "reset": 0},
                              "graphql": {"limit": 5000, "remaining": 4999, "reset": 0}}}

    def rest(self, path, params=None):
        if path == "/repos/vllm-project/vllm/contributors" and params["page"] == 1:
            return [{"login": "ada", "type": "User", "contributions": 240},
                    {"login": "linus", "type": "User", "contributions": 90},
                    {"login": "github-actions[bot]", "type": "Bot", "contributions": 999}]
        return []

    def graphql(self, query, variables=None):
        if "type: USER" in query:
            return {"search": {"userCount": 1, "pageInfo": {"hasNextPage": False, "endCursor": None},
                               "nodes": [{"login": "grace"}]}}
        if "type: REPOSITORY" in query:
            return {"search": {"nodes": [{"nameWithOwner": "ada/fastinfer", "stargazerCount": 4200,
                                          "owner": {"__typename": "User", "login": "ada"}}]}}
        if "repository(owner:" in query:
            return {}
        logins = re.findall(r'u(\d+): user\(login: "([^"]+)"\)', query)
        return {f"u{i}": self.people.get(login) for i, login in logins}


class PipelineTests(unittest.TestCase):
    def test_search_end_to_end(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "client", return_value=FakeGitHub()), \
                mock.patch("sys.stdout", new_callable=io.StringIO) as out, mock.patch("sys.stderr", new_callable=io.StringIO):
            cli.main(["search", "ai", "--location", "london, sf", "--out", d])
            run_dir = next(Path(d).iterdir())
            with open(run_dir / "candidates.csv", newline="") as f:
                logins = [row["login"] for row in csv.DictReader(f)]
            # linus is filtered by location ("Ukraine" must not match "uk"); ada outranks grace via seed commits.
            self.assertEqual(logins, ["ada", "grace"])
            self.assertTrue((run_dir / "report.html").exists())
            self.assertIn("2 candidates", out.getvalue())

    def test_same_minute_runs_get_separate_folders(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "client", return_value=FakeGitHub()), \
                mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch("sys.stderr", new_callable=io.StringIO):
            cli.main(["search", "ai", "--out", d])
            cli.main(["search", "ai", "--out", d])
            self.assertEqual(len(list(Path(d).iterdir())), 2)

    def test_search_from_jd_and_role_file(self):
        with tempfile.TemporaryDirectory() as d, mock.patch.object(cli, "client", return_value=FakeGitHub()), \
                mock.patch("sys.stdout", new_callable=io.StringIO), mock.patch("sys.stderr", new_callable=io.StringIO):
            Path(d, "jd.txt").write_text("ML Inference Engineer\n\nRequirements\n- Deep experience with LLM inference, "
                                         "vLLM and CUDA\n- Strong Python")
            cli.main(["search", "--jd", str(Path(d, "jd.txt")), "--out", str(Path(d, "out"))])
            run = next(Path(d, "out").iterdir())
            self.assertTrue(run.name.startswith("jd-ml-inference-engineer"))
            meta = json.loads((run / "candidates.json").read_text())["meta"]
            self.assertEqual(meta["role_label"], "ML Inference Engineer")
            self.assertIn("LLM inference", [r["name"] for r in meta["requirements"]])

            role = dict(load_roles()["ai"], key="custom-ai")
            Path(d, "role.json").write_text(json.dumps(role))
            cli.main(["search", "--role-file", str(Path(d, "role.json")), "--out", str(Path(d, "out2"))])
            self.assertTrue(next(Path(d, "out2").iterdir()).name.startswith("custom-ai"))

    def test_role_sources_are_exclusive(self):
        with self.assertRaises(SystemExit):
            cli.main(["search"])
        with self.assertRaises(SystemExit):
            cli.main(["search", "ai", "--role-file", "x.json"])

    def test_bad_arguments_rejected(self):
        with mock.patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            cli.main(["search", "ai", "--limit", "0"])
        with self.assertRaises(SystemExit) as ctx:
            cli.main(["search", "ai", "--repos", "not-a-repo"])
        self.assertIn("owner/name", str(ctx.exception.code))


if __name__ == "__main__":
    unittest.main()
