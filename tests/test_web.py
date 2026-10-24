import http.client
import json
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

from sourcer import web
from sourcer.cli import load_roles


def make_run(results, name, role="ai", people=1):
    path = Path(results, name)
    path.mkdir(parents=True)
    candidates = [{"rank": i + 1, "tier": "A", "score": 80, "login": f"dev{i}", "email": "x@y.dev" if i == 0 else ""}
                  for i in range(people)]
    meta = {"role": role, "role_label": "AI / ML Engineer", "generated_at": "2026-10-24T12:00:00+00:00",
            "query": {"location": "london"}}
    (path / "candidates.json").write_text(json.dumps({"meta": meta, "candidates": candidates}))
    (path / "candidates.csv").write_text("rank,login\n1,dev0\n")
    (path / "report.html").write_text("<p>report</p>")
    return path


class ArgvTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(web, "RESULTS", Path(self.tmp.name))
        self.patch.start()
        self.roles = load_roles()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_form_to_argv(self):
        make_run(self.tmp.name, "ai-london-20261024-1200")
        argv = web.search_argv({"role": "ai", "location": " sf,  london ", "limit": "25", "min_score": "",
                                "emails": True, "refresh": False, "exclude_runs": ["ai-london-20261024-1200", "../etc"]},
                               self.roles)
        self.assertEqual(argv[:2], ["search", "ai"])
        self.assertIn("--location=sf, london", argv)
        self.assertIn("--limit=25", argv)
        self.assertIn("--emails", argv)
        self.assertNotIn("--refresh", argv)
        self.assertFalse(any(a.startswith("--min-score") for a in argv))
        excludes = [a for a in argv if a.startswith("--exclude=")]
        self.assertEqual(len(excludes), 1)
        self.assertTrue(excludes[0].endswith("candidates.csv"))

    def test_values_cannot_become_options(self):
        argv = web.search_argv({"role": "ai", "location": "--refresh"}, self.roles)
        self.assertIn("--location=--refresh", argv)
        self.assertNotIn("--refresh", argv)

    def test_rejects_bad_input(self):
        with self.assertRaises(web.BadRequest):
            web.search_argv({"role": "nope"}, self.roles)
        with self.assertRaises(web.BadRequest):
            web.search_argv({"role": "ai", "limit": "lots"}, self.roles)

    def test_run_path_blocks_traversal(self):
        make_run(self.tmp.name, "ai-20261024-1200")
        self.assertIsNotNone(web.run_path("ai-20261024-1200"))
        for bad in ["..", "../ai-20261024-1200", ".cache", "", "missing", "a/b"]:
            self.assertIsNone(web.run_path(bad), bad)


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.patch = mock.patch.object(web, "RESULTS", Path(cls.tmp.name))
        cls.patch.start()
        make_run(cls.tmp.name, "ai-london-20261024-1200", people=3)
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.patch.stop()
        cls.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers or {})
        res = conn.getresponse()
        data = res.read()
        conn.close()
        return res.status, data, res

    def test_app_and_roles(self):
        status, page, _ = self.request("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"GitHub Sourcer", page)
        status, data, _ = self.request("GET", "/api/roles")
        self.assertEqual(status, 200)
        self.assertIn("ai", [r["key"] for r in json.loads(data)])

    def test_runs_listing_and_files(self):
        runs = json.loads(self.request("GET", "/api/runs")[1])
        self.assertEqual(runs[0]["name"], "ai-london-20261024-1200")
        self.assertEqual((runs[0]["count"], runs[0]["tier_a"], runs[0]["with_email"]), (3, 3, 1))
        status, data, res = self.request("GET", "/runs/ai-london-20261024-1200/candidates.csv")
        self.assertEqual(status, 200)
        self.assertIn("attachment", res.getheader("Content-Disposition"))
        self.assertEqual(self.request("GET", "/runs/..%2F..%2Fetc/candidates.csv")[0], 404)
        self.assertEqual(self.request("GET", "/runs/ai-london-20261024-1200/secret.txt")[0], 404)

    def test_foreign_host_rejected(self):
        # DNS rebinding: a page on evil.example resolving to 127.0.0.1 still sends its own Host header.
        self.assertEqual(self.request("GET", "/api/runs", headers={"Host": "evil.example:8765"})[0], 403)

    def test_writes_need_custom_header(self):
        status, _, _ = self.request("POST", "/api/search", {"role": "ai"}, {"Content-Type": "application/json"})
        self.assertEqual(status, 403)

    def test_bad_search_is_a_clean_400(self):
        status, data, _ = self.request("POST", "/api/search", {"role": "nope"}, {"X-Sourcer": "1"})
        self.assertEqual(status, 400)
        self.assertIn("Pick a role", json.loads(data)["error"])

    def test_bad_token_shape_rejected_without_calling_github(self):
        status, data, _ = self.request("POST", "/api/token", {"token": "hello"}, {"X-Sourcer": "1"})
        self.assertEqual(status, 400)
        self.assertIn("doesn't look like", json.loads(data)["error"])


class JobTests(unittest.TestCase):
    def test_job_runs_cli_and_captures_output(self):
        job = web.Job(["roles"])
        job.proc.wait(timeout=30)
        for _ in range(100):
            if job.state != "running":
                break
            threading.Event().wait(0.05)
        self.assertEqual(job.state, "done")
        self.assertTrue(any("AI / ML Engineer" in line for line in job.lines))


if __name__ == "__main__":
    unittest.main()
