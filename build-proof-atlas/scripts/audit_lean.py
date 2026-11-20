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


def lake_json(lake, arguments, root, env, stdin=None):
    result = subprocess.run([*lake, *arguments], cwd=root, env=env, input=stdin,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise ValueError(result.stderr.decode("utf-8", errors="replace").strip() or "Lake query failed")
    return [json.loads(line) for line in result.stdout.decode("utf-8-sig").splitlines()]


def hash_paths(root, paths):
    return {p: hashlib.sha256((root/p).read_bytes()).hexdigest() for p in sorted(paths)}


def import_headers(lake, paths, root, env):
    # The compiler parses module, public/meta imports, quoted names and comments.
    rows = lake_json(lake, ["env", "lean", "--deps-json", "--stdin"], root, env,
                     ("\n".join(paths) + "\n").encode())
    if len(rows) != 1 or len(rows[0]["imports"]) != len(paths):
        raise ValueError("Incomplete Lean import headers")
    imports = set()
    for item in rows[0]["imports"]:
        if item["errors"]:
            raise ValueError("Lean could not read an import header")
        imports.update(imp["module"] for imp in item["result"]["imports"])
    return imports


def module_inputs(lake, root, env, targets, contract):
    # Only use module-to-path correspondence for ordinary root-source Lake packages.
    # Executable/custom Lake configurations can read arbitrary files.
    import tomllib
    if (root/"lakefile.lean").exists():
        raise ValueError("Executable Lake configuration")
    config = tomllib.loads((root/"lakefile.toml").read_text(encoding="utf-8-sig"))
    if set(config) - {"name", "version", "defaultTargets", "lean_lib", "require"}:
        raise ValueError("Custom Lake package configuration")
    libraries = config["lean_lib"]
    if any(set(lib) != {"name"} for lib in libraries):
        raise ValueError("Custom Lean library configuration")
    if any(lib["name"] in targets and (root/Path(*lib["name"].split("."))).is_dir() for lib in libraries):
        raise ValueError("Requested target includes a library of modules")
    manifest = json.loads((root/"lake-manifest.json").read_bytes())
    if (manifest.get("packagesDir") != ".lake/packages" or
        any(p["type"] != "git" or not re.fullmatch(r"[0-9a-f]{40}", p["rev"]) for p in manifest["packages"])):
        raise ValueError("Nonstandard dependency directory or unpinned external package")
    if any(env.get(name) for name in ("LEAN_PATH", "LEAN_SRC_PATH")):
        raise ValueError("Custom Lean search path")

    def source(name):
        if not re.fullmatch(r"[\w']+(?:\.[\w']+)*", name):
            raise ValueError("Unrecognized Lean module name")
        return "/".join(name.split(".")) + ".lean"

    contract_imports = import_headers(lake, [contract], root, env) - {"Init"}
    roots = sorted(set(targets) | contract_imports)
    # transImports recursively parses current sources through Lake's module resolver,
    # including modules reached through pinned dependencies. It does not read old .ileans.
    deps = lake_json(lake, ["query", "--json", *["+" + name + ":transImports" for name in roots]], root, env)
    if len(deps) != len(roots) or not all(isinstance(row, list) for row in deps):
        raise ValueError("Incomplete Lake transitive import metadata")
    modules = set(roots).union(*deps)
    local = {name: source(name) for name in modules if (root/source(name)).is_file()}
    if not set(targets) <= local.keys() or (root/"Init.lean").exists():
        raise ValueError("Unresolved local module target or shadowed prelude")
    # Cross-check the paths and detect local imports unknown to Lake (e.g. a stale
    # .olean outside all configured libraries). Such a graph cannot be scoped safely.
    resolved = lake_json(lake, ["query", "--json", *["+" + name + ":lean" for name in sorted(local)]], root, env)
    expected = [(root/local[name]).resolve() for name in sorted(local)]
    if ([Path(p).resolve() for p in resolved] != expected or
        any(not p.is_relative_to(root) for p in expected)):
        raise ValueError("Lake resolved a different source directory")
    headers = import_headers(lake, sorted(set(local.values())), root, env)
    if any(name not in local and (root/source(name)).is_file() for name in headers):
        raise ValueError("Local compiler import missing from Lake closure")
    return ({contract, "lean-toolchain", "lakefile.toml", "lake-manifest.json", *local.values()},
            ["+" + name for name in sorted(contract_imports)])


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
    for name in (args.declaration, args.expected, *args.target):
        if not re.fullmatch(r"[A-Za-z_][\w']*(?:\.[A-Za-z_][\w']*)*", name):
            raise ValueError("Expected a fully qualified Lean identifier")
    pin = (root/"lean-toolchain").read_text(encoding="utf-8-sig").strip()
    env = os.environ.copy()
    env.update(ELAN_TOOLCHAIN=pin, GIT_ALLOW_PROTOCOL="file", MATHLIB_NO_CACHE_ON_UPDATE="1")
    if not (root/"lake-manifest.json").is_file():
        raise ValueError("Run initial package setup and keep the contract with the source files")
    lake = select_lake(root, pin, env, sys.stderr, offline=True)
    contract_path = contract.relative_to(root).as_posix()
    scoped = None
    build_targets = list(args.target)
    if args.target:
        try:
            scoped, contract_targets = module_inputs(lake, root, env, args.target, contract_path)
        except (ImportError, KeyError, OSError, TypeError, ValueError) as exc:
            print(f"Using full-package source hashes: {exc}", file=sys.stderr)
        else:
            build_targets += [t for t in contract_targets if t[1:] not in args.target]
    initial = inputs(root) if scoped is None else hash_paths(root, scoped)
    if contract_path not in initial or pin != (root/"lean-toolchain").read_text(encoding="utf-8-sig").strip():
        raise ValueError("Missing contract or toolchain pin changed during audit setup")
    # The Lake build runner sends output to an actual file descriptor.
    run([*lake, "--no-cache", "build", *build_targets], root, env, sys.stderr)
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
    if scoped is None:
        final = inputs(root)
    else:
        try:
            final_scope, _ = module_inputs(lake, root, env, args.target, contract_path)
        except (KeyError, OSError, TypeError, ValueError) as exc:
            raise ValueError(f"Could not confirm the dependency closure after the audit: {exc}") from exc
        final = hash_paths(root, final_scope)
    if initial != final:
        raise ValueError("Package inputs changed during the audit; rerun on stable sources")
    return dict(declaration=declaration, contract=contract.relative_to(root).as_posix(),
                expected=args.expected, toolchain=pin, axioms=axioms,
                targets=args.target, input_scope="module-closure" if scoped is not None else "full-package",
                checked_inputs=initial, lean_output=output.strip())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package", type=Path)
    parser.add_argument("contract", help="File importing the target and defining the expected proposition")
    parser.add_argument("expected", help="Name of the independent proposition")
    parser.add_argument("declaration", help="Name of the proof to check")
    parser.add_argument("--target", action="append", default=[], help="Build this proof module before checking its independent contract")
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
