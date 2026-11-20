from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
            statement = {"expected": "True", "sha256": "1" * 64, "constants": []}
            audit = {"expected": "True", "declaration": "result", "axioms": [], "toolchain": "test-pin",
                     "checked_inputs": {"T.lean": checked["sha256"]}, "statement": statement}
            approval = {"by": "A. Author", "approved": "2026-01-01T00:00:00+00:00", "expected": "True",
                        "statement_sha256": statement["sha256"], "constants": {}}
            (root / "approvals.json").write_text(json.dumps({"schema_version": 1, "claims": {"t": approval}}))
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

            def dashboard(**options):
                (root / "project.json").write_text(json.dumps(project), encoding="utf-8")
                atlas.build(root, **options)
                return (root / "dist/progress.html").read_text(encoding="utf-8")

            complete = "Paper-wide proof work complete"
            current = dashboard()
            self.assertIn("1 of 1 stated results closed", current)
            self.assertIn(complete, current)
            self.assertIn("Lean checked", current)
            self.assertIn("Statement approved by A. Author", current)
            self.assertNotIn("Machine verified on GitHub", current)

            project["hosting"] = {"repository": "https://github.com/example/paper", "lean_ci": "enabled"}
            revision = "a" * 40
            record = {"repository": "example/paper", "commit": revision, "run_id": 123, "attempt": 2,
                      "claims": {"t": dict(audit, audit_sha256=hashlib.sha256((root / "audit.json").read_bytes()).hexdigest(),
                                           statement_sha256=statement["sha256"])}}
            path = root / "remote.json"
            path.write_text(json.dumps(record), encoding="utf8")
            options = dict(ci_receipt=path, commit=revision, artifact_id=456)
            verified = dashboard(**options)
            self.assertEqual(verified.count("Machine verified on GitHub"), 1)
            self.assertIn("example/paper/actions/runs/123/attempts/2", verified)
            self.assertIn("example/paper/actions/runs/123/artifacts/456", verified)
            self.assertEqual((root / "dist/logs/lean-verification.json").read_bytes(), path.read_bytes())
            self.assertNotIn("Machine verified on GitHub", dashboard(ci_receipt=path, commit="b"*40))
            record["claims"]["t"]["audit_sha256"] = "0"*64
            path.write_text(json.dumps(record), encoding="utf8")
            self.assertNotIn("Machine verified on GitHub", dashboard(**options))

            project["proof_work"] = {"mode": "claim", "claim": "t"}
            self.assertNotIn(complete, dashboard())

            project["proof_work"] = {"mode": "paper"}
            (root / checked["path"]).write_bytes(b"-- edited\n")
            stale = dashboard()
            self.assertIn("awaiting recheck", stale)
            self.assertIn("0 of 1 stated results closed", stale)
            self.assertNotIn(complete, stale)


class StatementApproval(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = root = Path(folder.name).resolve()

        def source(name, data):
            (root / name).write_bytes(data)
            return {"path": name, "sha256": hashlib.sha256(data).hexdigest()}

        tex = source("paper.tex", rb"\begin{theorem}\label{t}The rate at zero is positive.\end{theorem}")
        (root / "lean").mkdir()
        self.lean = source("lean/T.lean", b"def rate (n : Nat) := n + 1\ndef Statement : Prop := rate 0 > 0\n"
                                          b"theorem result : Statement := Nat.zero_lt_one\n")
        (root / "run.log").write_bytes(b"Built T\n")
        self.receipt({"rate": "a" * 64, "Statement": "b" * 64})
        self.project = {
            "schema_version": 1, "environments": ["theorem"], "edition": "test", "title": "Test paper",
            "tex": [tex], "pdf": source("paper.pdf", b"test"), "proof_work": {"mode": "paper"},
            "hosting": {"decision": "local"},
            "work": {"t": {"status": "closed", "scope": "Result.", "run_log": "run.log",
                           "checked_inputs": {self.lean["path"]: self.lean["sha256"]},
                           "closure": {"expected": "Statement", "declaration": "result", "package": "lean",
                                       "audit": "audit.json", "correspondence": "Result."}}},
        }
        (root / "project.json").write_text(json.dumps(self.project), encoding="utf-8")

    def receipt(self, digests, statement=True):
        constants = [{"name": n, "file": "T.lean", "lines": [i + 1, i + 1], "sha256": d}
                     for i, (n, d) in enumerate(sorted(digests.items()))]
        audit = {"expected": "Statement", "declaration": "result", "axioms": [], "toolchain": "test-pin",
                 "checked_inputs": {"T.lean": self.lean["sha256"]}}
        if statement:
            audit["statement"] = {"expected": "Statement", "constants": constants, "sha256": hashlib.sha256(
                "".join(f'{c["name"]} {c["sha256"]}\n' for c in constants).encode()).hexdigest()}
        (self.root / "audit.json").write_text(json.dumps(audit), encoding="utf-8")

    def build(self):
        atlas.build(self.root)
        return (self.root / "dist/progress.html").read_text(encoding="utf-8")

    def approve(self, answer, labels=("t",), tty=True):
        prompts = []
        def ask(prompt):
            prompts.append(prompt)
            return answer
        with patch.object(atlas.sys.stdin, "isatty", return_value=tty), redirect_stdout(io.StringIO()) as shown:
            atlas.approve(self.root, list(labels), "A. Author", ask=ask)
        return shown.getvalue(), prompts

    def test_closed_claim_waits_for_the_author(self):
        # A receipt from before statement records still builds, showing both facts separately.
        self.receipt({}, statement=False)
        legacy = self.build()
        self.assertIn("Lean checked", legacy)
        self.assertIn("Statement not yet approved", legacy)
        self.assertIn("0 of 1 stated results closed", legacy)
        self.assertNotIn("Paper-wide proof work complete", legacy)
        shown, prompts = self.approve("t")
        self.assertIn("predates statement records", shown)
        self.assertEqual(prompts, [])

        self.receipt({"rate": "a" * 64, "Statement": "b" * 64})
        with self.assertRaisesRegex(ValueError, "interactive terminal"):
            self.approve("t", tty=False)
        shown, _ = self.approve("")
        self.assertIn("t: not approved", shown)
        self.assertFalse((self.root / "approvals.json").exists())

        shown, prompts = self.approve("t", labels=())
        self.assertIn("The rate at zero is positive", shown)
        self.assertIn("def rate (n : Nat) := n + 1", shown)
        self.assertIn("type the claim label t", prompts[0])
        approved = self.build()
        self.assertIn("Statement approved by A. Author", approved)
        self.assertIn("Lean checked", approved)
        self.assertIn("1 of 1 stated results closed", approved)
        self.assertIn("Paper-wide proof work complete", approved)
        explorer = (self.root / "dist/index.html").read_text(encoding="utf-8")
        self.assertIn("Statement approved by A. Author", explorer)

        # A recheck after a proof-only edit reproduces the statement digest: approval stays.
        self.receipt({"rate": "a" * 64, "Statement": "b" * 64})
        self.assertIn("Statement approved by A. Author", self.build())

        # A recheck after a definition changed withdraws the approval and reopens the claim.
        self.receipt({"rate": "c" * 64, "Statement": "b" * 64})
        with redirect_stdout(io.StringIO()) as log:
            withdrawn = self.build()
        self.assertIn("Statement not yet approved", withdrawn)
        self.assertIn("0 of 1 stated results closed", withdrawn)
        self.assertIn("Statement approval withdrawn: t (approved by A. Author; changed: rate)", log.getvalue())

        # Edited Lean sources must be rechecked before anyone approves them.
        (self.root / self.lean["path"]).write_bytes(b"-- edited\n")
        shown, prompts = self.approve("t")
        self.assertIn("recheck the claim first", shown)
        self.assertEqual(prompts, [])
