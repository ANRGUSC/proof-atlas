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
import author_review as review


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


    def packet(self):
        return review.get_packet(self.root,"t")

    def compare(self, result="match", **updates):
        record = dict(bindings=self.packet()["view"]["bindings"], result=result, paper_reading="For n=0 the rate is positive.",
                      lean_reading="The rate at zero exceeds zero.", paper_refs="paper.tex, theorem t", lean_refs="Statement and rate")
        record.update(updates)
        review.save_comparison(self.root,"t",record)

    def test_optional_and_versioned_review(self):
        self.receipt({"Statement":"a"*64, "rate":"b"*64})
        self.compare()
        initial=self.packet()
        with patch.object(sys.stdin,"isatty",return_value=True),redirect_stdout(io.StringIO()):
            review.terminal(self.root,["t"],"Demo reviewer",ask=lambda prompt:"")
        self.assertFalse(self.packet()["events"])
        review.decide(self.root,"t",initial["token"],"approve","Demo reviewer")
        self.assertTrue(self.packet()["approved"])
        with self.assertRaisesRegex(ValueError,"Reload"):
            review.decide(self.root,"t",initial["token"],"difference","Second reader","Check n=0")
        self.receipt({"Statement":"a"*64, "rate":"b"*64})
        self.assertTrue(self.packet()["approved"])
        self.compare(lean_reading="The audited conclusion concerns the value at zero.")
        self.assertFalse(self.packet()["approved"])
        self.assertIn("exceeds zero",self.packet()["events"][0]["view"]["lean_reading"])
        self.receipt({"Statement":"a"*64, "rate":"c"*64})
        self.assertFalse(self.packet()["ready"])
        self.assertEqual(self.packet()["assessment"],"Comparison pending")

    def test_discrepancy_resolution_and_counts(self):
        self.receipt({"Statement":"a"*64})
        for result,issue in [("difference","Only the upper bound is checked."),("difference","Different walk."),
                             ("unsure","Unclear boundary case.")]:
            self.compare(result,issue=issue,actor="Author" if result=="unsure" else "Proof agent",next="Resolve the stated gap.")
            self.assertTrue(self.packet()["disputed"])
            self.assertEqual(self.packet()["issue"],issue)
        self.compare(resolution="Corrected the theorem and checked both cases.")
        review.decide(self.root,"t",self.packet()["token"],"difference","Test author","The display says all n.")
        with redirect_stdout(io.StringIO()):
            atlas.build(self.root)
        page=(self.root/"dist/progress.html").read_text(encoding="utf8")
        self.assertIn("0 of 1 stated results closed",page)
        self.assertIn("Lean checked",page)
        event=self.packet()["events"][-1]["id"]
        self.compare(resolves=[event],resolution="Corrected the display to n=0.")
        self.assertTrue(self.packet()["ready"])
        with patch.object(sys.stdin,"isatty",return_value=True),redirect_stdout(io.StringIO()):
            review.terminal(self.root,["t"],"Test author",ask=lambda prompt:"t")
            atlas.build(self.root)
        self.assertTrue(self.packet()["approved"])
        self.assertIn("1/1 author reviewed",(self.root/"dist/progress.html").read_text(encoding="utf8"))
        self.assertEqual(self.packet()["events"][-1]["decision"],"approve")
