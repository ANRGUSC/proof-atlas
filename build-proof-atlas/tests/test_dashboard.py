import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import atlas


class DashboardCompletion(unittest.TestCase):
    def test_counts_current_closures_and_excludes_conjectures(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()

            def source(name, data):
                (root / name).write_bytes(data)
                return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

            tex = source("paper.tex", (
                r"\begin{theorem}\label{t}Result.\end{theorem}" "\n"
                r"\begin{conjecture}\label{c}Conjecture.\end{conjecture}"
            ).encode())
            pdf = source("paper.pdf", b"test")
            (root / "lean").mkdir()
            checked = source("lean/T.lean", b"theorem result : True := True.intro\n")
            (root / "run.log").write_bytes(b"Built T\n")
            audit = {"expected": "True", "declaration": "result", "axioms": [],
                     "checked_inputs": {"T.lean": checked["sha256"]}}
            (root / "audit.json").write_text(json.dumps(audit), encoding="utf-8")
            closure = {"expected": "True", "declaration": "result", "package": "lean",
                       "audit": "audit.json", "correspondence": "Result."}
            project = {
                "schema_version": 1, "environments": ["theorem", "conjecture"],
                "edition": "test", "title": "Test paper", "tex": [tex], "pdf": pdf,
                "proof_work": {"mode": "paper"}, "hosting": {"decision": "local"},
                "work": {"t": {"status": "closed", "closure": closure, "scope": "Result.",
                               "run_log": "run.log", "checked_inputs": {checked["path"]: checked["sha256"]}}},
            }

            def dashboard():
                (root / "project.json").write_text(json.dumps(project), encoding="utf-8")
                atlas.build(root)
                return (root / "dist/progress.html").read_text(encoding="utf-8")

            complete = "Paper-wide proof work complete"
            current = dashboard()
            self.assertIn("1 of 1 stated results closed", current)
            self.assertIn(complete, current)

            project["proof_work"] = {"mode": "claim", "claim": "t"}
            self.assertNotIn(complete, dashboard())

            project["proof_work"] = {"mode": "paper"}
            (root / checked["path"]).write_bytes(b"-- edited\n")
            stale = dashboard()
            self.assertIn("awaiting recheck", stale)
            self.assertIn("0 of 1 stated results closed", stale)
            self.assertNotIn(complete, stale)
