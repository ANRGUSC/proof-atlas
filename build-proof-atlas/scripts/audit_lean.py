"""Build and check a declaration against a separately authored Lean proposition."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from check_lean import select_lake, run


def inputs(root):
    result = {}
    for directory, children, files in os.walk(root):
        children[:] = [child for child in children if not child.startswith(".")]
        for name in files:
            if name.endswith(".lean") or name in ("lakefile.toml", "lake-manifest.json", "lean-toolchain"):
                path = Path(directory)/name
                result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def audit(args):
    root = args.package.resolve(strict=True)
    contract = (root/args.contract).resolve(strict=True)
    if not contract.is_relative_to(root) or contract.suffix != ".lean":
        raise ValueError("Keep the independent contract in the Lean package")
    for name in (args.declaration, args.expected):
        if not re.fullmatch(r"[A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)*", name):
            raise ValueError("Expected a fully qualified Lean identifier")
    pin = (root/"lean-toolchain").read_text(encoding="utf-8-sig").strip()
    env = os.environ.copy()
    env.update(ELAN_TOOLCHAIN=pin, GIT_ALLOW_PROTOCOL="file", MATHLIB_NO_CACHE_ON_UPDATE="1")
    initial = inputs(root)
    if "lake-manifest.json" not in initial or contract.relative_to(root).as_posix() not in initial:
        raise ValueError("Run initial package setup and keep the contract with the source files")
    lake = select_lake(root, pin, env, sys.stderr, offline=True)
    # The Lake build runner sends output to an actual file descriptor.
    run([*lake, "--no-cache", "build"], root, env, sys.stderr)
    declaration = args.declaration
    program = (contract.read_text(encoding="utf-8-sig") + "\n" +
               f"example : {args.expected} := {declaration}\n#print axioms {declaration}\n")
    result = subprocess.run([*lake, "env", "lean", "--stdin"], input=program.encode(),
                            cwd=root, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    output = result.stdout.decode("utf-8-sig", errors="replace")
    if result.returncode or re.search(r"\bwarning:", output, re.I):
        raise ValueError("Lean contract check did not pass cleanly:\n"+output)
    quoted = re.escape("'"+declaration+"'")
    reports = re.findall(quoted + r" (?:depends on axioms:\s*\[([^\]]*)\]|does not depend on any axioms)", output)
    if len(reports) != 1:
        raise ValueError("Could not identify one axiom report for the requested declaration:\n"+output)
    axioms = [a.strip() for a in reports[0].split(",") if a.strip()]
    additional = set(axioms) - {"propext", "Classical.choice", "Quot.sound"}
    if additional:
        raise ValueError("Declaration uses additional axioms: "+", ".join(sorted(additional)))
    if initial != inputs(root):
        raise ValueError("Package inputs changed during the audit; rerun on stable sources")
    return dict(declaration=declaration, contract=contract.relative_to(root).as_posix(),
                expected=args.expected, toolchain=pin, axioms=axioms,
                checked_inputs=initial, lean_output=output.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("contract", help="File importing the target and defining the expected proposition")
    parser.add_argument("expected", help="Name of the independent proposition")
    parser.add_argument("declaration", help="Name of the proof to check")
    parser.add_argument("--output", type=Path, help="New evidence file to create on success")
    args = parser.parse_args()
    try:
        data = json.dumps(audit(args), indent=2)+"\n"
        if args.output:
            with args.output.open("x", encoding="utf-8") as out:
                out.write(data)
        else:
            print(data, end="")
    except (OSError, RuntimeError, ValueError) as exc:
        sys.exit(str(exc))
