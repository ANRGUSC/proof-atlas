"""Project-local correspondence evidence and optional human decisions."""
import hashlib
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

BADGE = "Author reviewed the theorem statement proved in Lean"


def digest(value):
    data = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(data).hexdigest()


def read(path, default):
    return json.loads(path.read_bytes()) if path.exists() else default


def review_url(cid):
    return "review/" + digest(cid) + ".html"


def bindings(project, claim, audit):
    paper = {k: project.get(k) for k in ("edition", "publication", "tex", "pdf")}
    paper.update({k: claim[k] for k in ("id", "file", "line", "excerpt")})
    statement = (audit or {}).get("statement", {})
    inputs = (audit or {}).get("checked_inputs", {})
    return dict(paper=digest(paper), statement=statement.get("sha256"),
                expected=(audit or {}).get("expected"), toolchain=(audit or {}).get("toolchain"),
                manifest=inputs.get("lake-manifest.json"))


def history(root, cid):
    book = read(root / "reviews.json", {"version": 1, "claims": {}})
    if book.get("version") != 1:
        raise ValueError("Unknown reviews.json version")
    return book["claims"].get(cid, [])


def packet(root, project, claim, work, audit, comparison, events, checked):
    keys = bindings(project, claim, audit)
    current = comparison and comparison.get("bindings") == keys and checked and keys["statement"]
    fields = ("paper_reading", "lean_reading", "paper_refs", "lean_refs")
    if current:
        if comparison.get("result") not in ("match", "difference", "unsure") or any(
                not isinstance(comparison.get(k), str) or not comparison[k].strip() for k in fields):
            raise ValueError(f"{claim['id']}: incomplete independent comparison")
        if comparison["result"] != "match" and not all(comparison.get(k) for k in ("issue", "actor", "next")):
            raise ValueError(f"{claim['id']}: give the difference, next actor and resolution")
    comp = comparison if current else {}
    view = dict(bindings=keys, excerpt=claim["excerpt"], edition=project["edition"],
                **{k:comp.get(k, "") for k in fields})
    version = digest(view)
    last = events[-1] if events else {}
    resolved = comp.get("resolves", []) if comp.get("resolution") else []
    outstanding = [e for e in events if e["decision"] == "difference" and e["id"] not in resolved]
    machine_issue = (comparison or {}).get("result") in ("difference", "unsure")
    disputed = bool(outstanding or machine_issue)
    approved = checked and comp.get("result") == "match" and not disputed and last.get("decision") == "approve" and last.get("version") == version
    if outstanding:
        issue, actor, step = " · ".join(e["comment"] for e in outstanding), "Proof agent", "Investigate the reported difference and record a resolution."
    elif machine_issue:
        issue, actor, step = (comparison.get(k,"") for k in ("issue","actor","next"))
    elif not checked:
        issue, actor, step = "", "Proof agent", ("Refresh the Lean audit for the current sources." if work.get("status") == "closed" else work.get("next") or "Finish the Lean check.")
    elif not keys["statement"]:
        issue, actor, step = "", "Proof agent", "Refresh the legacy audit to record statement fingerprints for optional review."
    elif not comp:
        issue, actor, step = "", "Proof agent", "Prepare independent paper and Lean readings."
    else:
        issue, actor, step = "", "Author (optional)", "Review the comparison whenever convenient."
    return dict(id=claim["id"], title=claim["title"], view=view, version=version,
                token=digest([version, comparison, last.get("id"), checked]), events=events,
                checked=checked, approved=bool(approved), disputed=disputed,
                ready=bool(checked and comp.get("result")=="match" and not disputed),
                assessment={"match":"Automated comparison found no discrepancy","difference":"Difference found",
                            "unsure":"Interpretation unresolved"}.get(comp.get("result"), "Comparison pending"),
                issue=issue, actor=actor, next=step, resolution=comp.get("resolution",""))


def exact_sources(root, work, audit):
    from atlas import local
    result = []
    if not audit:
        return result
    package = work["closure"]["package"]
    seen = set()
    for c in sorted(audit.get("statement", {}).get("constants", []),
                    key=lambda x:(x["name"] != audit["expected"], x["name"])):
        lines = c.get("lines")
        if not lines or (c["file"],tuple(lines)) in seen:
            continue
        seen.add((c["file"],tuple(lines)))
        source = local(root, package + "/" + c["file"]).read_text(encoding="utf-8-sig").splitlines()
        result.append(dict(name=c["name"], file=c["file"], lines=lines,
                           text="\n".join(source[lines[0]-1:lines[1]])))
    return result


def get_packet(root, cid):
    from atlas import current_audit, manuscript_claims, read_source, local
    project = read(root/"project.json", {})
    read_source(root,project["pdf"])
    claim = next((c for c in manuscript_claims(root,project) if c["id"]==cid), None)
    if claim is None:
        raise ValueError("Unknown claim")
    work = project.get("work", {}).get(cid, {})
    audit = None
    checked = False
    if work.get("status") == "closed":
        try:
            audit = current_audit(root,cid,work)
            checked = True
        except ValueError:
            pass
    comp = read(root/"comparisons.json", {}).get(cid)
    data = packet(root,project,claim,work,audit,comp,history(root,cid),checked)
    reader = read(local(root,project["reader"]), {}) if project.get("reader") else {}
    data["page"] = work.get("page") or reader.get("claims",{}).get(cid,{}).get("page")
    data["sources"] = exact_sources(root,work,audit)
    return data


@contextmanager
def project_lock(root):
    path = Path(tempfile.gettempdir()) / ("proof-atlas-" + digest(str(root.resolve())) + ".lock")
    with path.open("a+b") as lock:
        if lock.tell() == 0:
            lock.write(b"0")
            lock.flush()
        lock.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        yield


def decide(root,cid,token,decision,by,comment=""):
    from atlas import atomic
    if decision not in ("approve","difference","skip"):
        raise ValueError("Unknown decision")
    with project_lock(root):
        data = get_packet(root,cid)
        if token != data["token"]:
            raise ValueError("The evidence or review changed. Reload and review the current version.")
        if decision == "skip":
            return
        if not by.strip() or (decision=="difference" and not comment.strip()):
            raise ValueError("Enter your name and describe any difference.")
        if decision=="approve" and not data["ready"]:
            raise ValueError("Approval requires a current checked comparison with no outstanding difference.")
        event = dict(by=by.strip(), decision=decision, comment=comment.strip(), version=data["version"],
                     at=datetime.now(timezone.utc).isoformat(), view=data["view"], sources=data["sources"])
        event["id"] = digest(event)
        path = root/"reviews.json"
        book = read(path, {"version":1,"claims":{}})
        book["claims"].setdefault(cid,[]).append(event)
        atomic(path, (json.dumps(book,indent=2,ensure_ascii=False)+"\n").encode())


def save_comparison(root,cid,record):
    """Install the agent's two readings only against the evidence it actually read."""
    from atlas import atomic
    with project_lock(root):
        data = get_packet(root,cid)
        if not data["checked"] or not data["view"]["bindings"]["statement"]:
            raise ValueError("Refresh the checked statement fingerprint before recording a comparison")
        if record.get("bindings") != data["view"]["bindings"]:
            raise ValueError("Comparison inputs changed; repeat the readings")
        required = ("paper_reading","lean_reading","paper_refs","lean_refs")
        if any(not isinstance(record.get(k),str) or not record[k].strip() for k in required):
            raise ValueError("Supply both independent mathematical readings and their source references")
        result = record.get("result")
        if result not in ("match","difference","unsure"):
            raise ValueError("Choose match, difference or unsure")
        if result != "match" and (record.get("actor") not in ("Proof agent","Author") or
                                  not all(isinstance(record.get(k),str) and record[k].strip() for k in ("issue","next"))):
            raise ValueError("Record the precise issue, next actor and action")
        path=root/"comparisons.json"
        comparisons=read(path,{})
        prior=comparisons.get(cid,{})
        if prior.get("result") in ("difference","unsure") and result=="match" and not record.get("resolution"):
            raise ValueError("Record how the prior discrepancy was resolved")
        ids={e["id"] for e in data["events"] if e["decision"]=="difference"}
        resolves=record.get("resolves",[])
        if not isinstance(resolves,list) or any(i not in ids for i in resolves) or (resolves and not record.get("resolution")):
            raise ValueError("Reference reported feedback IDs and explain the resolution")
        comparisons[cid]=record
        atomic(path,(json.dumps(comparisons,ensure_ascii=False,indent=2)+"\n").encode())


def terminal(root,labels,by,ask=input):
    import sys
    from atlas import manuscript_claims
    if not sys.stdin.isatty() or not by.strip():
        raise ValueError("Review in an interactive terminal with --by NAME")
    project = read(root/"project.json", {})
    for cid in labels or [c["id"] for c in manuscript_claims(root,project)]:
        data=get_packet(root,cid)
        if not labels and (data["approved"] or not data["checked"]):
            continue
        print(cid, data["view"]["edition"], data["view"]["excerpt"], sep="\n")
        for key in ("paper_reading","paper_refs","lean_reading","lean_refs"):
            print(data["view"][key])
        for source in data["sources"]:
            print(source["file"],source["lines"],source["text"],sep="\n")
        print(data["assessment"],data["issue"],data["actor"],data["next"],sep="\n")
        for event in data["events"]:
            print(event["at"],event["by"],event["decision"],event["comment"])
        options = f"Type {cid} to approve, " if data["ready"] else ""
        answer=ask(options+"! to report a difference, or Enter to skip: ").strip()
        if answer=="!":
            decide(root,cid,data["token"],"difference",by,ask("What differs? "))
        elif answer==cid and data["ready"]:
            decide(root,cid,data["token"],"approve",by)
