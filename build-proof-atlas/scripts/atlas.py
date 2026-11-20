"""Build a local, read-only view of an author's checkpoint records (Python 3.9+)."""
import argparse
import hashlib
import html
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote, urlsplit


def esc(value):
    return html.escape(str(value), quote=True)


def edition_links(project):
    # Older projects included the PDF caption in their edition text.
    edition = project["edition"].removesuffix(" \u00b7 local manuscript and PDF")
    label = esc(edition)
    publication = project.get("publication")
    if publication:
        url = urlsplit(publication)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("project.json: publication must be an absolute HTTP(S) URL")
        label = f'<a href="{esc(publication)}">{label or "Publication"}</a>'
    pdf = '<a href="manuscript.pdf">Manuscript PDF</a>'
    return " &middot; ".join(filter(None, (label, pdf)))


def atomic(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as temp:
        temp.write(data)
    os.replace(temp.name, path)


def local(root, name):
    path = (root / name).resolve()
    if not path.is_relative_to(root):
        raise ValueError(f"Path outside project: {name}")
    return path


def read_source(root, record):
    data = local(root, record["path"]).read_bytes()
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError(f"{record['path']}: source changed; review edition, anchors and checkpoints")
    return data


def inventory(text, filename, envs):
    # Blank comments in place so offsets still point into the original source.
    clean = re.sub(r"(?<!\\)%[^\n]*", lambda m: " " * len(m[0]), text)
    names = "|".join(re.escape(name) for name in envs)
    opening = re.compile(r"\\begin\{(" + names + r")\}(?:\s*\[([^\]]*)\])?")
    for start in opening.finditer(clean):
        kind, title = start.groups()
        end_tag = r"\end{" + kind + "}"
        end = clean.find(end_tag, start.end())
        line = text.count("\n", 0, start.start()) + 1
        if end < 0:
            raise ValueError(f"{filename}:{line}: missing {end_tag}")
        bind = re.match(r"\s*\\label\{([^}]+)\}", clean[start.end():end])
        if not bind:
            raise ValueError(f"{filename}:{line}: assign a stable source mapping for this unlabeled claim")
        stop = end + len(end_tag)
        proof = re.match(r"\s*\\begin\{proof\}.*?\\end\{proof\}", clean[stop:], re.S)
        if proof:
            stop += proof.end()
        title = re.sub(r"\s*\\cite\{[^}]+\}", "", title or bind[1])
        yield dict(id=bind[1], kind=kind, title=title, file=filename, line=line,
                   excerpt=text[start.start():stop], adjacent_proof=bool(proof))


def page(title, body, watch=""):
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)}</title>'
            '<link rel="stylesheet" href="style.css"><body><nav><a href="progress.html">Proof progress</a>'
            '<a href="proofs/index.html">Proof Atlas explorer</a></nav><main>' + body + '</main>' + watch + '</body></html>').encode()


def proof_work_description(project, claims, *, closed_results=None):
    scope = project.get("proof_work")
    if scope is None:
        return "Proof-work authorization has not been recorded."
    mode = scope["mode"]
    if mode == "paper":
        total = sum(claim["kind"] != "conjecture" for claim in claims)
        if total and closed_results == total:
            text = "Paper-wide proof work complete. All stated results are closed."
        else:
            text = f"Paper-wide. Continue through all {total} stated results, including their prerequisites. Track conjectures separately."
    elif mode == "claim":
        selected = next((c for c in claims if c["id"] == scope.get("claim")), None)
        if selected is None:
            raise ValueError("proof_work.claim must identify a manuscript claim")
        text = f"One claim. Work through {selected['title']} ({selected['id']}) and its prerequisites, then stop for the author."
    else:
        raise ValueError("proof_work.mode must be paper or claim")
    if scope.get("paused"):
        return "Paused by the author. Saved scope: " + text
    return text


def github_verification(project, receipt_path, commit):
    if not receipt_path or project.get("hosting", {}).get("lean_ci") != "enabled":
        return None
    data = json.loads(Path(receipt_path).read_bytes())
    repository = project["hosting"]["repository"]
    if (not re.fullmatch(r"[0-9a-f]{40}", commit or "") or data.get("commit") != commit
            or not re.fullmatch(r"[\w.-]+/[\w.-]+", data.get("repository", ""))
            or repository.casefold() != ("https://github.com/" + data["repository"]).casefold()
            or not all(type(data.get(k)) is int and data[k] > 0 for k in ("run_id", "attempt"))):
        return None
    return data


def build(root, *, ci_receipt=None, commit=None, artifact_id=None):
    p = json.loads((root / "project.json").read_text(encoding="utf-8-sig"))
    ci = github_verification(p, ci_receipt, commit)
    if p["schema_version"] != 1 or not p["environments"]:
        raise ValueError("project.json: expected schema version 1 and theorem environments")
    claims = []
    for source in p["tex"]:
        claims.extend(inventory(read_source(root, source).decode("utf-8-sig"), source["path"], p["environments"]))
    by_id = {c["id"]: c for c in claims}
    work = p.get("work", {})
    if len(by_id) != len(claims):
        raise ValueError("duplicate claim label across manuscript sources")
    if "claim_order" in p:
        order = p["claim_order"]
        if len(order) != len(claims) or set(order) != set(by_id):
            raise ValueError("claim_order must list every manuscript claim exactly once")
        claims = [by_id[label] for label in order]
    if work.keys() - by_id.keys() or (p.get("focus") and p["focus"] not in by_id):
        raise ValueError("checkpoint or focus refers to an unknown claim label")
    pdf = read_source(root, p["pdf"])
    output = root / "dist"
    output.mkdir(exist_ok=True)
    materials = {}
    if p.get("reader"):
        materials = json.loads(local(root, p["reader"]).read_text(encoding="utf-8-sig"))
    notes = materials.get("claims", {})
    edges = materials.get("dependencies", [])
    if notes.keys() - by_id.keys() or any(e[k] not in by_id for e in edges for k in ("claim", "prerequisite")):
        raise ValueError("reader: claim or dependency refers to an unknown label")
    assets = Path(__file__).resolve().parents[1] / "assets"
    rows, entries, log_links = [], [], {}
    stale = {}
    subproof_lists = {}
    closed_results = 0
    for c in claims:
        cid = c["id"]
        w = work.get(cid, {})
        m = notes.get(cid, {})
        status = w.get("status", "open")
        if status not in {"open", "drafted", "blocked", "attempted", "closed"}:
            raise ValueError(f"{cid}: unsupported checkpoint status {status}")
        entry = {**c, **m, **w}
        if status == "closed":
            closure = w["closure"]
            receipt = local(root, closure["audit"]).read_bytes()
            audit = json.loads(receipt)
            package = local(root, closure["package"])
            relative = package.relative_to(root)
            inputs = {(relative / name).as_posix(): digest for name, digest in audit["checked_inputs"].items()}
            if (not inputs or inputs != w.get("checked_inputs") or not w.get("run_log") or
                not w.get("scope") or not closure.get("correspondence") or
                audit["expected"] != closure["expected"] or audit["declaration"] != closure["declaration"] or
                set(audit["axioms"]) - {"propext", "Classical.choice", "Quot.sound"} or
                any(part["status"] != "checked" for part in w.get("subproofs", []))):
                raise ValueError(f"{cid}: closure requires matching audit evidence, correspondence and resolved obligations")
            (output / "logs").mkdir(exist_ok=True)
            name = hashlib.sha256(receipt).hexdigest() + ".json"
            atomic(output / "logs" / name, receipt)
            entry["audit_log"] = "logs/" + name
        changed = [path for path, digest in w.get("checked_inputs", {}).items()
                   if not local(root, path).is_file() or hashlib.sha256(local(root, path).read_bytes()).hexdigest() != digest]
        if changed:
            stale[cid] = "Lean inputs changed since the recorded check: " + ", ".join(changed)
            entry["scope"] = stale[cid] + ". The linked log is historical."
            entry["summary"] = m.get("summary", "The paper claim is awaiting a fresh Lean check.")
            status = "awaiting recheck"
        entry["status"] = status
        if c["kind"] != "conjecture" and status == "closed":
            closed_results += 1
        entry.setdefault("reference", c["kind"].title())
        subproofs = []
        for part in w.get("subproofs", []):
            state = part["status"]
            if state not in {"open", "attempted", "checked"}:
                raise ValueError(f"{cid}: unsupported subproof status {state}")
            if state == "checked":
                if not (part.get("declaration") and w.get("run_log") and w.get("checked_inputs")):
                    raise ValueError(f"{cid}: checked subproof requires a declaration and recorded build inputs and log")
                if changed:
                    state = "awaiting recheck"
            subproofs.append(dict(part, status=state))
        entry["subproofs"] = subproofs
        checked = status == "closed"
        has_parts = any(part["status"] == "checked" for part in subproofs)
        label = ("Open conjecture" if c["kind"] == "conjecture" and status == "open" else
                 "Lean checked" if checked else "Awaiting Lean recheck" if changed else
                 "Subproofs checked" if has_parts else "No completed Lean check")
        verification = {"label": label}
        if checked and p.get("hosting", {}).get("lean_ci") == "enabled":
            verification["remote"] = "No GitHub verification for this revision"
            verification["url"] = p["hosting"]["repository"] + "/actions/workflows/verify-lean.yml"
            record = ci["claims"].get(cid) if ci else None
            if (record and record.get("audit_sha256") == hashlib.sha256(receipt).hexdigest()
                    and all(record.get(k) == audit[k] for k in ("expected", "declaration", "toolchain"))
                    and set(record["axioms"]) == set(audit["axioms"])):
                verification.update(remote="Machine verified on GitHub",
                                    url=f'{p["hosting"]["repository"]}/actions/runs/{ci["run_id"]}/attempts/{ci["attempt"]}',
                                    commit=ci["commit"], evidence="logs/lean-verification.json")
                if artifact_id and artifact_id > 0:
                    verification["artifact"] = f'{p["hosting"]["repository"]}/actions/runs/{ci["run_id"]}/artifacts/{artifact_id}'
        entry["verification"] = verification
        if subproofs:
            items = "".join(f'<li>{esc(part["title"])} · {esc(part["status"])}</li>' for part in subproofs)
            subproof_lists[cid] = f"<h3>Subproofs</h3><ol>{items}</ol>"
        entry["sources"] = [
            {"path": filename, "text": local(root, filename).read_text(encoding="utf-8-sig")}
            for filename in dict.fromkeys(([w["lean"]] if w.get("lean") else []) + w.get("lean_sources", []))
        ]
        if m.get("lamport"):
            entry["steps"] = local(root, m["lamport"]).read_text(encoding="utf-8-sig")
        if w.get("run_log"):
            log = local(root, w["run_log"]).read_bytes()
            name = hashlib.sha256(log).hexdigest() + ".txt"
            (output / "logs").mkdir(exist_ok=True)
            atomic(output / "logs" / name, log)
            entry["log"] = "logs/" + name
            log_links[cid] = f'<p><a href="logs/{name}">Read the recorded Lean run</a></p>'
        entries.append(entry)
        url = "proofs/index.html#/claim/" + quote(cid, safe="")
        target = ' <strong class="proof-target">Current proof target</strong>' if cid == p.get("focus") else ''
        badge = f' · <a href="{url}?view=lean">{esc(label)}</a>'
        if "evidence" in verification:
            badge += f' <a class="verified-badge" href="{esc(verification["url"])}">&#10003; {esc(verification["remote"])}</a>'
            badge += f' · <a href="{esc(verification["evidence"])}">Verification record</a>'
            if "artifact" in verification:
                badge += f' · <a href="{esc(verification["artifact"])}">GitHub artifact</a>'
        elif "remote" in verification:
            badge += f' · <a href="{esc(verification["url"])}">{esc(verification["remote"])}</a>'
        rows.append(f'<li data-claim="{esc(cid)}"><a href="{url}">{esc(entry["reference"])} · {esc(c["title"])}</a> · {esc(status)}{badge}{target}<span class="claim-activity"></span></li>')
    if ci:
        (output / "logs").mkdir(exist_ok=True)
        atomic(output / "logs/lean-verification.json", Path(ci_receipt).read_bytes())
    sources = edition_links(p)
    heading = f'<p>Proof Atlas · Maintainer dashboard</p><h1>{esc(p["title"])}</h1><p>{sources}</p>'
    focus = p.get("focus")
    active = ""
    if focus and focus in work:
        w = work[focus]
        active = (f'<section><small>Latest saved proof checkpoint</small><h2><a href="proofs/index.html#/claim/{quote(focus, safe="")}">{esc(by_id[focus]["title"])}</a></h2>'
                  f'<p>{esc(stale.get(focus, w["step"]))}</p><p><strong>Next step</strong><br>{esc(w["next"])}</p>'
                  f'{subproof_lists.get(focus, "")}<time>Last checkpoint · {esc(w["updated"])}</time>{log_links.get(focus, "")}</section>')
    feed = p.get("hosting", {}).get("activity_url", "")
    total_results = sum(c["kind"] != "conjecture" for c in claims)
    scope = proof_work_description(p, claims, closed_results=closed_results)
    dashboard = heading + f'<section id="claim-inventory" data-feed="{esc(feed)}"><h2>Claim inventory</h2><p><strong>Authorized proof work</strong><br>{esc(scope)}</p><p>{closed_results} of {total_results} stated results closed</p><ol>' + "".join(rows) + '</ol></section>' + active
    payload = dict(title=p["title"], focus=focus, claims=entries, dependencies=edges, macros=materials.get("macros", {}))
    template = (assets / "explorer.html").read_text(encoding="utf-8-sig")
    packed = json.dumps(payload, ensure_ascii=False)
    digest = hashlib.sha256((packed + dashboard + template).encode())
    for name in ("explorer.js", "explorer.css", "graph.js", "graph.css", "style.css", "live.js", "activity.js"):
        digest.update((assets / name).read_bytes())
        atomic(output / name, (assets / name).read_bytes())
    version = digest.hexdigest()
    payload["revision"] = version
    packed = json.dumps(payload, ensure_ascii=False).replace("<", r"\u003c")
    explorer = template.replace("__TITLE__", esc(p["title"])).replace("__EDITION__", sources)
    for directory, base in ((output, "./"), (output / "proofs", "../")):
        directory.mkdir(exist_ok=True)
        atomic(directory / "index.html", explorer.replace("__REVISION__", version).replace("__BASE__", base).replace("__DATA__", packed).encode())
    shutil.copytree(assets / "dagre", output / "dagre", dirs_exist_ok=True)
    shutil.copytree(assets / "katex", output / "katex", dirs_exist_ok=True)
    watch = f'<script src="live.js" data-revision="{version}" defer></script>'
    atomic(output / "preview-status.json", b'{}')
    if not (output / "activity.json").exists():
        atomic(output / "activity.json", b'{"runs":[]}')
    atomic(output / "progress.html", page("Proof progress", dashboard, watch + '<script src="activity.js" defer></script>'))
    atomic(output / "manuscript.pdf", pdf)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "assets/style.css", output / "style.css")
    atomic(output / "inventory.json", json.dumps(claims, ensure_ascii=False, indent=2).encode())
    atomic(output / "checkpoint.json", json.dumps(dict(revision=version)).encode())
    print(f"{len(claims)} claims | {output / 'progress.html'}")
    print(f"Authorized proof work: {scope}")
    hosting = p.get("hosting") or {}
    if not hosting.get("repository") and hosting.get("decision") != "local":
        print("Hosting decision pending. Ask where the author wants this paper's explorer and dashboard hosted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["build"])
    parser.add_argument("project", type=Path)
    parser.add_argument("--ci-receipt", type=Path, help="Evidence downloaded from the trusted Lean workflow")
    parser.add_argument("--commit", help="Git revision being rendered")
    parser.add_argument("--artifact-id", type=int, help="ID of the GitHub artifact containing the supplied CI receipt")
    args = parser.parse_args()
    try:
        build(args.project.resolve(), ci_receipt=args.ci_receipt, commit=args.commit, artifact_id=args.artifact_id)
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(f"Proof Atlas: {exc}")
