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
        record["analysis"] = dict(summary="Compare the rate at zero.", scope="Only n=0 is asserted.",
                                  cases=[dict(case="Zero", paper="Positive", lean="0 < rate 0", why="rate 0 = 1")],
                                  advice="Check the value at zero.")
        if result != "match":
            record["analysis"]["suggested_feedback"] = "Please clarify the domain at zero."
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

    def test_explanation_is_versioned_and_advice_tracks_feedback(self):
        from review_web import render
        self.compare()
        packet = self.packet()
        review.decide(self.root,"t",packet["token"],"approve","Test author")
        revised = dict(packet["view"]["analysis"], advice="Approve for the zero input only.")
        self.compare(analysis=revised)
        self.assertFalse(self.packet()["approved"])
        self.assertNotEqual(self.packet()["events"][0]["view"]["analysis"],revised)
        self.assertIn(b"Approve for the zero input only.",render(self.packet()))
        review.decide(self.root,"t",self.packet()["token"],"difference","Author","Does this include other inputs?")
        self.assertNotIn(b"Approve for the zero input only.",render(self.packet()).split(b"<h2>Review history</h2>")[0])
        self.compare("unsure",issue="Scope unclear",actor="Proof agent",next="Check the domain")
        with self.assertRaises(ValueError):
            self.compare("unsure",analysis=revised,issue="Scope unclear",actor="Proof agent",next="Check the domain")

    def test_legacy_comparison_and_changed_sources(self):
        self.compare()
        path = self.root/"comparisons.json"
        records = json.loads(path.read_bytes())
        del records["t"]["analysis"]
        path.write_text(json.dumps(records),encoding="utf-8")
        self.assertFalse(self.packet()["ready"])
        self.assertIn("justification",self.packet()["next"])
        self.compare()
        self.receipt({"Statement":"c"*64})
        self.assertNotIn("analysis",self.packet()["view"])

    def test_manuscript_edits_can_be_cleared_and_restored_independently(self):
        import manuscript_edits as edits
        self.compare()
        review.decide(self.root,"t",self.packet()["token"],"approve","Author",paper_edit="Use explicit inequality.")
        first = self.packet()["events"][-1]["id"]
        path = self.root/"manuscript-edits.json"
        path.write_text(json.dumps({"version":1,"edits":{"typo":{"claim":"t","edition":"test","reason":"Typo"}}}))
        def apply(action, selected):
            edits.update(self.root,edits.token(*edits.inventory(self.root)),action,selected)
        old = edits.token(*edits.inventory(self.root))
        apply("clear",[first])
        with self.assertRaisesRegex(ValueError,"Reload"):
            edits.update(self.root,old,"clear-all",[])
        self.assertIn(b"Active (1)",edits.render(self.root))
        apply("clear-all",[])
        self.assertIn(b"Cleared (2)",edits.render(self.root))
        apply("restore",[first,"typo"])
        apply("request",[first])
        self.assertEqual(edits.inventory(self.root)[1][first]["status"],"Draft requested")
        self.assertTrue(self.packet()["approved"])

    def test_optional_stronger_result_and_reviewer_scope(self):
        from review_web import render
        self.compare()
        p = self.packet()
        review.decide(self.root,"t",p["token"],"approve","Author",scope_note="I approve the conclusion at zero.")
        self.compare(stronger_result="The optimizing witness is also unique.")
        p = self.packet()
        self.assertTrue(p["approved"])
        self.assertIn(b"The optimizing witness is also unique.",render(p))
        self.assertEqual(p["author_scope"],"I approve the conclusion at zero.")
        with self.assertRaises(ValueError):
            self.compare("difference",stronger_result="Extra",issue="A case is missing",actor="Proof agent",next="Prove it.")
        review.decide(self.root,"t",p["token"],"difference","Reader","Please check the endpoint.")
        self.assertFalse(self.packet()["stronger_result"])
        self.assertFalse(self.packet()["author_scope"])
