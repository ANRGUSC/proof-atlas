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
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlsplit


def esc(value):
    return html.escape(str(value), quote=True)


def edition_reference(project):
    # Older projects included the PDF caption in their edition text.
    edition = project["edition"].removesuffix(" \u00b7 local manuscript and PDF")
    label = esc(edition)
    publication = project.get("publication")
    if publication:
        url = urlsplit(publication)
        if url.scheme not in ("http", "https") or not url.hostname:
            raise ValueError("project.json: publication must be an absolute HTTP(S) URL")
        label = f'<a href="{esc(publication)}">{label or "Publication"}</a>'
    return label


def edition_links(project):
    return " &middot; ".join(filter(None, (edition_reference(project), '<a href="manuscript.pdf">Manuscript PDF</a>')))


def paper_citation(project):
    authors = ", ".join(esc(author) for author in project.get("authors", []))
    title = "<cite>" + esc(project["title"]) + "</cite>"
    edition = " ".join(filter(None, (edition_reference(project), esc(project.get("citation_details", "")))))
    return ". ".join(filter(None, (authors, title, edition))) + "."


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


def claim_url(label):
    return "proofs/index.html#/claim/" + quote(label, safe="")


def masthead(project, first):
    claim = claim_url(first)
    graph = "proofs/index.html#/graph/" + quote(first, safe="")
    return (f'<header class="mast"><a class="wordmark" href="index.html">Proof Atlas<span>.</span></a>'
            f'<nav aria-label="Project"><a data-nav="home" href="index.html">Overview</a>'
            f'<a data-nav="reader" href="{claim}">Claim reader</a><a data-nav="graph" id="graph-nav" href="{graph}">Dependency graph</a>'
            '<a data-nav="progress" href="progress.html">Dashboard</a></nav>'
            '<details id="about-atlas" class="citation"><summary>Paper &amp; citation</summary><div>'
            f'<h2>About this atlas</h2><p>{paper_citation(project)}</p>'
            f'<p>{esc(project.get("paper_overview", ""))}</p><p><a href="manuscript.pdf">Manuscript PDF</a></p>'
            f'<h3>Read a proof</h3><p><a href="{claim}?view=english">Plain English</a> gives the claim and proof idea. '
            f'<a href="{claim}?view=paper">Paper</a> opens the manuscript. <a href="{claim}?view=lamport">Lamport proof</a> '
            f'breaks the argument into steps. <a href="{claim}?view=lean">Lean</a> shows the formal sources and check records.</p>'
            f'<p>The <a href="{graph}">dependency graph</a> points from prerequisite to result. Whole paper includes isolated claims. '
            'The <a href="progress.html">progress dashboard</a> tracks active work, checked claims, and verification evidence.</p>'
            '</div></details></header>')


def featured_result(root, output, folio, entries):
    selected = folio.get("featured_claim")
    if selected and not any(c["id"] == selected for c in entries):
        raise ValueError("folio.featured_claim is absent from the inventory")
    if not entries:
        return "<p>No claims imported yet.</p>"
    c = next((c for c in entries if c["id"] == selected), entries[0])
    url = claim_url(c["id"])
    summary = folio.get("blurb") or c.get("summary") or c.get("sketch") or ""
    image = ""
    fig = folio.get("figure")
    if fig:
        path = local(root, fig["path"])
        if (path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"} or not fig.get("alt")
                or not fig.get("caption") or type(fig.get("page")) is not int or fig["page"] < 1):
            raise ValueError("folio.figure needs a raster image, alt text, caption and one-based manuscript page")
        content = path.read_bytes()
        name = "folio-" + hashlib.sha256(content).hexdigest()[:16] + path.suffix.lower()
        atomic(output / name, content)
        image = (f'<figure><a href="manuscript.pdf#page={fig["page"]}"><img src="{name}" alt="{esc(fig["alt"])}"></a>'
                 f'<figcaption>{esc(fig["caption"])} <a href="manuscript.pdf#page={fig["page"]}">Manuscript, p. {fig["page"]}</a></figcaption></figure>')
    return (f'<section class="feature"><div><small>Featured result · {esc(c["reference"])}</small>'
            f'<h2><a href="{url}">{esc(c["title"])}</a></h2><p class="feature-summary">{esc(summary)}</p>'
            f'<a class="read-proof" href="{url}">Read the claim →</a></div>{image}</section>')


def page(title, mast, body, watch=""):
    return (f'<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" '
            f'content="width=device-width,initial-scale=1"><title>{esc(title)} | Proof Atlas</title>'
            '<link rel="stylesheet" href="style.css"><body class="dashboard">'
            + mast + '<main>' + body + '</main>' + watch + '</body></html>').encode()


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


def manuscript_claims(root, p):
    claims = []
    for source in p["tex"]:
        claims.extend(inventory(read_source(root, source).decode("utf-8-sig"), source["path"], p["environments"]))
    return claims


def load_approvals(root):
    """Author approvals of Lean statements, kept apart from the agent's checkpoints."""
    path = root / "approvals.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    if data.get("schema_version") != 1:
        raise ValueError("approvals.json: expected schema version 1")
    return data["claims"]


def approval_state(approval, audit):
    """An approval holds while the audited statement and its definitions hash as approved."""
    statement = audit.get("statement")
    if not approval:
        return None, []
    if statement and approval["expected"] == audit["expected"] and approval["statement_sha256"] == statement["sha256"]:
        return approval, []
    now = {c["name"]: c["sha256"] for c in (statement or {}).get("constants", [])}
    then = approval.get("constants", {})
    changed = sorted(n for n in now.keys() | then.keys() if now.get(n) != then.get(n))
    return None, changed or [audit["expected"]]


def current_audit(root, cid, w):
    """The closure's receipt, provided the audited sources are unchanged."""
    if w.get("status") != "closed":
        raise ValueError(f"{cid}: only a closed claim with a Lean audit can be approved")
    audit = json.loads(local(root, w["closure"]["audit"]).read_bytes())
    for path, digest in w.get("checked_inputs", {}).items():
        if not local(root, path).is_file() or hashlib.sha256(local(root, path).read_bytes()).hexdigest() != digest:
            raise ValueError(f"{cid}: {path} changed since the Lean check; recheck the claim first")
    if not audit.get("statement"):
        raise ValueError(f"{cid}: the audit predates statement records; rerun audit_lean.py with this skill version")
    return audit


def approve(root, labels, by, ask=input):
    """Show each audited statement with its definitions and record the author's approval."""
    if not by.strip():
        raise ValueError("Give the approving author's name with --by")
    if not sys.stdin.isatty():
        raise ValueError("Statement approval is typed by the author at an interactive terminal")
    p = json.loads((root / "project.json").read_text(encoding="utf-8-sig"))
    claims = {c["id"]: c for c in manuscript_claims(root, p)}
    work = p.get("work", {})
    approvals = load_approvals(root)
    if not labels:
        labels = [cid for cid, w in work.items() if w.get("status") == "closed" and
                  not approval_state(approvals.get(cid), json.loads(local(root, w["closure"]["audit"]).read_bytes()))[0]]
        print(f"{len(labels)} closed claims await statement approval.")
    for cid in labels:
        if cid not in claims or cid not in work:
            raise ValueError(f"{cid}: no checkpoint for this manuscript claim")
        try:
            audit = current_audit(root, cid, work[cid])
        except ValueError as exc:
            print(exc)
            continue
        package = work[cid]["closure"]["package"]
        print(f"\n=== {cid} · {claims[cid]['file']}, line {claims[cid]['line']} ===\n{claims[cid]['excerpt']}\n")
        print(f"Lean statement {audit['expected']} and the definitions it uses from this project:")
        shown = set()
        constants = sorted(audit["statement"]["constants"], key=lambda c: (c["name"] != audit["expected"], c["file"], c["lines"] or []))
        for c in constants:
            key = (c["file"], tuple(c["lines"] or ()))
            if c["lines"] and key not in shown:
                shown.add(key)
                lines = local(root, package + "/" + c["file"]).read_text(encoding="utf-8-sig").splitlines()
                print(f"\n-- {package}/{c['file']}, lines {c['lines'][0]}-{c['lines'][1]}")
                print("\n".join(lines[c["lines"][0] - 1:c["lines"][1]]))
        hidden = sum(1 for c in audit["statement"]["constants"] if not c["lines"])
        if hidden:
            print(f"\n(plus {hidden} constants Lean generated from these definitions)")
        try:
            answer = ask(f"\nTo approve this statement as {by}, type the claim label {cid} (Enter skips): ")
        except EOFError:
            answer = ""
        if answer.strip() != cid:
            print(f"{cid}: not approved")
            continue
        approvals[cid] = dict(by=by, approved=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                              expected=audit["expected"], statement_sha256=audit["statement"]["sha256"],
                              constants={c["name"]: c["sha256"] for c in audit["statement"]["constants"]})
        data = json.dumps(dict(schema_version=1, claims=approvals), indent=2, ensure_ascii=False) + "\n"
        atomic(root / "approvals.json", data.encode())
        print(f"{cid}: statement approved by {by}")


def build(root, *, ci_receipt=None, commit=None, artifact_id=None):
    p = json.loads((root / "project.json").read_text(encoding="utf-8-sig"))
    ci = github_verification(p, ci_receipt, commit)
    if p["schema_version"] != 1 or not p["environments"]:
        raise ValueError("project.json: expected schema version 1 and theorem environments")
    claims = manuscript_claims(root, p)
    approvals = load_approvals(root)
    awaiting_approval, withdrawn = [], []
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
        approval = None
        if status == "closed":
            approval, changed_definitions = approval_state(approvals.get(cid), audit)
            if changed_definitions:
                withdrawn.append(f'{cid} (approved by {approvals[cid]["by"]}; changed: {", ".join(changed_definitions)})')
            if not approval:
                awaiting_approval.append(cid)
        entry["status"] = status if approval or status != "closed" else "awaiting approval"
        if c["kind"] != "conjecture" and approval:
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
        if checked:
            verification["approval"] = ("Statement approved by " + approval["by"] if approval
                                        else "Statement not yet approved")
            verification["approved"] = bool(approval)
        if checked and p.get("hosting", {}).get("lean_ci") == "enabled":
            verification["remote"] = "No GitHub verification for this revision"
            verification["url"] = p["hosting"]["repository"] + "/actions/workflows/verify-lean.yml"
            record = ci["claims"].get(cid) if ci else None
            if (record and record.get("audit_sha256") == hashlib.sha256(receipt).hexdigest()
                    and all(record.get(k) == audit[k] for k in ("expected", "declaration", "toolchain"))
                    and record.get("statement_sha256") == audit.get("statement", {}).get("sha256")
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
        if "approval" in verification:
            badge += f' · <span class="approval{" approved" if verification["approved"] else ""}">{esc(verification["approval"])}</span>'
        if "evidence" in verification:
            badge += f' <a class="verified-badge" href="{esc(verification["url"])}">&#10003; {esc(verification["remote"])}</a>'
            badge += f' · <a href="{esc(verification["evidence"])}">Verification record</a>'
            if "artifact" in verification:
                badge += (f' · <a href="{esc(verification["artifact"])}" data-artifact-record="{esc(verification["evidence"])}">'
                          'GitHub artifact (sign-in required)</a>')
        elif "remote" in verification:
            badge += f' · <a href="{esc(verification["url"])}">{esc(verification["remote"])}</a>'
        rows.append(f'<li data-claim="{esc(cid)}"><a href="{url}">{esc(entry["reference"])} · {esc(c["title"])}</a> · {esc(entry["status"])}{badge}{target}<span class="claim-activity"></span></li>')
    if ci:
        (output / "logs").mkdir(exist_ok=True)
        atomic(output / "logs/lean-verification.json", Path(ci_receipt).read_bytes())
    sources = edition_links(p)
    folio = p.get("folio", {})
    short_title = folio.get("short_title") or p["title"]
    byline = " &middot; ".join(filter(None, (", ".join(map(esc, p.get("authors", []))), sources)))
    heading = f'<header class="paper"><small>{esc(short_title)}</small><h1>Progress dashboard</h1><p>{byline}</p></header>'
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
    pending = f" · {len(awaiting_approval)} Lean checked, awaiting the author's statement approval" if awaiting_approval else ""
    dashboard = heading + f'<section id="claim-inventory" data-feed="{esc(feed)}"><h2>Claim inventory</h2><p><strong>Authorized proof work</strong><br>{esc(scope)}</p><p>{closed_results} of {total_results} stated results closed{pending}</p><ol>' + "".join(rows) + '</ol></section>' + active
    payload = dict(title=p["title"], focus=focus, claims=entries, dependencies=edges, macros=materials.get("macros", {}))
    template = (assets / "explorer.html").read_text(encoding="utf-8-sig")
    packed = json.dumps(payload, ensure_ascii=False)
    first = focus or folio.get("featured_claim") or (entries[0]["id"] if entries else "")
    mast = masthead(p, first)
    feature = featured_result(root, output, folio, entries)
    contents = []
    for entry in entries:
        link = claim_url(entry["id"])
        v = entry["verification"]
        mark = (f'<a class="verified-badge" href="{esc(v["url"])}">✓ {esc(v["remote"])}</a>'
                if v.get("evidence") else f'<a class="status" href="{link}?view=lean">{esc(v["label"])}</a>')
        if "approval" in v:
            mark = f'<span class="marks">{mark}<span class="status approval{" approved" if v["approved"] else ""}">{esc(v["approval"])}</span></span>'
        contents.append(f'<li><a class="toc-claim" href="{link}"><small>{esc(entry["reference"])}</small>'
                        f'<strong>{esc(entry["title"])}</strong></a>{mark}</li>')
    explorer = (template.replace("__MAST__", mast).replace("__TITLE__", esc(p["title"]))
                .replace("__SHORT_TITLE__", esc(short_title)).replace("__BYLINE__", byline)
                .replace("__FEATURE__", feature).replace("__COUNT__", str(len(entries)))
                .replace("__CONTENTS__", "".join(contents)))
    digest = hashlib.sha256((packed + dashboard + explorer).encode())
    for name in ("explorer.js", "explorer.css", "graph.js", "graph.css", "style.css", "live.js", "activity.js", "artifact.js"):
        digest.update((assets / name).read_bytes())
        atomic(output / name, (assets / name).read_bytes())
    version = digest.hexdigest()
    payload["revision"] = version
    packed = json.dumps(payload, ensure_ascii=False).replace("<", r"\u003c")
    for directory, base, home in ((output, "./", True), (output / "proofs", "../", False)):
        directory.mkdir(exist_ok=True)
        shell = (explorer.replace("__ENTRY__", "home" if home else "reader")
                 .replace("__HOME_HIDDEN__", "" if home else "hidden")
                 .replace("__READER_HIDDEN__", "hidden" if home else "")
                 .replace("__REVISION__", version).replace("__BASE__", base))
        atomic(directory / "index.html", shell.replace("__DATA__", packed).encode())
    shutil.copytree(assets / "dagre", output / "dagre", dirs_exist_ok=True)
    shutil.copytree(assets / "katex", output / "katex", dirs_exist_ok=True)
    watch = f'<script src="live.js" data-revision="{version}" defer></script>'
    atomic(output / "preview-status.json", b'{}')
    if not (output / "activity.json").exists():
        atomic(output / "activity.json", b'{"runs":[]}')
    atomic(output / "progress.html", page("Proof progress", mast, dashboard, watch + '<script src="activity.js" defer></script><script src="artifact.js" defer></script>'))
    atomic(output / "manuscript.pdf", pdf)
    shutil.copyfile(Path(__file__).resolve().parents[1] / "assets/style.css", output / "style.css")
    atomic(output / "inventory.json", json.dumps(claims, ensure_ascii=False, indent=2).encode())
    atomic(output / "checkpoint.json", json.dumps(dict(revision=version)).encode())
    print(f"{len(claims)} claims | {output / 'progress.html'}")
    print(f"Authorized proof work: {scope}")
    for item in withdrawn:
        print(f"Statement approval withdrawn: {item}")
    if awaiting_approval:
        print(f"Lean checked, awaiting the author's statement approval: {', '.join(awaiting_approval)}. "
              f"Ask the author to run: python <skill>/scripts/atlas.py approve {root} --by \"<name>\"")
    hosting = p.get("hosting") or {}
    if not hosting.get("repository") and hosting.get("decision") != "local":
        print("Hosting decision pending. Ask where the author wants this paper's explorer and dashboard hosted.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["build", "approve"])
    parser.add_argument("project", type=Path)
    parser.add_argument("claims", nargs="*", help="approve: claim labels (default: every closed claim awaiting approval)")
    parser.add_argument("--by", help="approve: the author approving the statements. Only the author runs approve")
    parser.add_argument("--ci-receipt", type=Path, help="Evidence downloaded from the trusted Lean workflow")
    parser.add_argument("--commit", help="Git revision being rendered")
    parser.add_argument("--artifact-id", type=int, help="ID of the GitHub artifact containing the supplied CI receipt")
    args = parser.parse_args()
    try:
        if args.operation == "approve":
            approve(args.project.resolve(), args.claims, args.by or "")
        else:
            build(args.project.resolve(), ci_receipt=args.ci_receipt, commit=args.commit, artifact_id=args.artifact_id)
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(f"Proof Atlas: {exc}")
