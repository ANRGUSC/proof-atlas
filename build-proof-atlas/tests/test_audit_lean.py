"""Offline integration regression. Set ELAN_TOOLCHAIN to an installed Lean pin."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import audit_lean


@unittest.skipUnless(os.environ.get("ELAN_TOOLCHAIN"), "requires an installed pinned Lean toolchain")
class AuditClosure(unittest.TestCase):
    def test_local_dependency_stability(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pin = os.environ["ELAN_TOOLCHAIN"]
            (root/"lean-toolchain").write_text(pin)
            (root/"lakefile.toml").write_text('name = "Example"\n[[lean_lib]]\nname = "Pkg"\n')
            (root/"Pkg").mkdir()
            sources = {
                "Pkg/Base.lean": "def quantity := 2\n",
                "Pkg/Proof.lean": "import Pkg.Base\ntheorem quantity_eq : quantity = 2 := rfl\n",
                "Pkg/Expected.lean": "def Statement : Prop := 2 = (2 : Nat)\n",
                "Pkg/Unrelated.lean": "-- independent claim\n",
                "Contract.lean": "import Pkg.Proof\n/- import Pkg.Unrelated -/\nimport Pkg.Expected\n",
            }
            for name, body in sources.items():
                (root/name).write_text(body, encoding="utf-8")
            env = os.environ.copy()
            lake = audit_lean.select_lake(root, pin, env, sys.stderr, offline=True)
            subprocess.run([*lake, "update"], cwd=root, env=env, check=True)
            args = SimpleNamespace(package=root, contract="Contract.lean", expected="Statement",
                                   declaration="quantity_eq", target=["Pkg.Proof"])

            def run_with_edit(path):
                build = audit_lean.run
                def edit_after_build(*args, **kwargs):
                    build(*args, **kwargs)
                    with (root/path).open("a", encoding="utf-8") as source:
                        source.write("-- parallel edit\n")
                return patch.object(audit_lean, "run", edit_after_build)

            with run_with_edit("Pkg/Unrelated.lean"):
                receipt = audit_lean.audit(args)
            self.assertEqual(receipt["input_scope"], "module-closure")
            self.assertEqual(set(receipt["checked_inputs"]),
                             {"lean-toolchain", "lakefile.toml", "lake-manifest.json",
                              "Pkg/Base.lean", "Pkg/Proof.lean", "Pkg/Expected.lean", "Contract.lean"})
            with run_with_edit("Pkg/Base.lean"), self.assertRaisesRegex(ValueError, "changed during the audit"):
                audit_lean.audit(args)

            # A custom source directory requests the conservative package-wide receipt.
            with (root/"lakefile.toml").open("a", encoding="utf-8") as config:
                config.write('srcDir = "."\n')
            fallback = audit_lean.audit(args)
            self.assertEqual(fallback["input_scope"], "full-package")
            self.assertIn("Pkg/Unrelated.lean", fallback["checked_inputs"])


if __name__ == "__main__":
    unittest.main()
