import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hosting


class IncrementalRelease(unittest.TestCase):
    def test_publishes_the_commit_while_a_different_proof_is_in_progress(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            def git(*args):
                return subprocess.check_output(["git", *args], cwd=root).decode().strip()

            git("init", "-q", "-b", "main")
            source = root / "Proof.lean"
            source.write_bytes(b"theorem demo : True := True.intro\n")
            choice = {"repository": "https://github.com/example/atlas", "visibility": "public"}
            project = {"hosting": choice, "work": {"demo": {"status": "closed", "checked_inputs": {
                source.name: hashlib.sha256(source.read_bytes()).hexdigest()}}}}
            (root / "project.json").write_text(json.dumps(project), encoding="utf-8")
            git("add", "--", source.name, "project.json")
            git("-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "proof")
            head = git("rev-parse", "HEAD")
            source.write_text("-- next draft\n", encoding="utf-8")
            (root / "Unrelated.lean").write_text("-- in progress\n", encoding="utf-8")
            real_command = hosting.command
            pushes = []

            def local_only(args, cwd, check=True):
                if args[0] == "gh":
                    return subprocess.CompletedProcess(args, 0, "public\n", "")
                if args[:2] == ["git", "push"]:
                    pushes.append(args)
                    return subprocess.CompletedProcess(args, 0, "", "")
                return real_command(args, cwd, check)

            with patch.object(hosting, "command", side_effect=local_only):
                hosting.publish(root, choice)
                self.assertEqual(pushes, [["git", "push", "origin", head + ":refs/heads/main"]])
                with self.assertRaisesRegex(ValueError, "hosting destination"):
                    hosting.publish(root, {**choice, "repository": "https://github.com/example/other"})
                git("add", "--", source.name)
                git("-c", "user.name=Test", "-c", "user.email=test@example.org", "commit", "-qm", "changed")
                with self.assertRaisesRegex(ValueError, "changed committed proof inputs"):
                    hosting.publish(root, choice)
                self.assertEqual(len(pushes), 1)
