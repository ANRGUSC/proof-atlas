"""Read or record a paper's hosting choice, or publish its reviewed commit."""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys

from atlas import atomic


def command(args, cwd, check=True):
    return subprocess.run(args, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.PIPE, check=check)


def exists(endpoint, root):
    result = command(["gh", "api", endpoint, "--silent"], root, check=False)
    if result.returncode == 0:
        return True
    if "HTTP 404" in result.stderr:
        return False
    raise ValueError(result.stderr.strip())


def publish(root, choice):
    url = choice.get("repository", "")
    match = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+)", url)
    if not match or choice.get("visibility") not in ("public", "private"):
        raise ValueError("Record the approved GitHub destination and visibility first")
    repo = match[1]
    def git(*args):
        return command(["git", *args], root).stdout.strip()
    if Path(git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ValueError("Publish from the paper repository root")
    if git("status", "--porcelain") or git("branch", "--show-current") != "main":
        raise ValueError("Commit the reviewed project on main before publishing")
    git("ls-files", "--error-unmatch", "project.json")
    if choice.get("site"):
        git("ls-files", "--error-unmatch", ".github/workflows/pages.yml")
    remotes = git("remote").splitlines()
    if "origin" in remotes:
        for name in git("remote", "get-url", "--push", "--all", "origin").splitlines():
            expected = {url.casefold(), f"git@github.com:{repo}".casefold()}
            if name.removesuffix(".git").casefold() not in expected:
                raise ValueError("The origin push URL differs from the saved hosting choice")
    endpoint = "repos/" + repo
    if exists(endpoint, root):
        visibility = command(["gh", "api", endpoint, "--jq", ".visibility"], root).stdout.strip()
        if visibility != choice["visibility"]:
            raise ValueError("Existing repository visibility differs from the saved choice")
    else:
        command(["gh", "repo", "create", repo, "--"+choice["visibility"]], root)
    if "origin" not in remotes:
        git("remote", "add", "origin", url+".git")
    git("push", "-u", "origin", "main")
    if choice.get("site"):
        if not exists(endpoint+"/pages", root):
            command(["gh", "api", "-X", "POST", endpoint+"/pages", "-f", "build_type=workflow"], root)
        mode = command(["gh", "api", endpoint+"/pages", "--jq", ".build_type"], root).stdout.strip()
        if mode != "workflow":
            raise ValueError("Existing Pages site uses a different publishing source; review it before switching")
        command(["gh", "workflow", "run", "pages.yml", "--repo", repo, "--ref", "main"], root)
    print(url)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--github", metavar="OWNER/REPO")
    actions.add_argument("--local", action="store_true")
    actions.add_argument("--publish", action="store_true", help="Publish the committed paper project to its approved GitHub destination")
    parser.add_argument("--visibility", choices=["public", "private"])
    pages = parser.add_mutually_exclusive_group()
    pages.add_argument("--pages", action="store_true")
    pages.add_argument("--no-pages", action="store_true")
    args = parser.parse_args()
    root = args.project.resolve()
    path = root/"project.json"
    project = json.loads(path.read_text(encoding="utf-8-sig"))
    choice = project.get("hosting") or {"decision":"pending"}
    if (args.visibility or args.pages or args.no_pages) and not args.github:
        parser.error("Use the GitHub options with --github")
    if args.publish:
        publish(root, choice)
        return
    if args.github:
        if not re.fullmatch(r"[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*", args.github):
            parser.error("Expected OWNER/REPO")
        if not args.visibility or not (args.pages or args.no_pages):
            parser.error("Supply the agreed --visibility and --pages or --no-pages")
        url = "https://github.com/"+args.github
        choice = dict(choice) if choice.get("repository", "").casefold() == url.casefold() else {}
        choice.update(decision="github", repository=url, visibility=args.visibility)
        if args.pages:
            owner, repo = args.github.split("/")
            choice.setdefault("site", f"https://{owner.lower()}.github.io/" +
                              ("" if repo.lower() == owner.lower()+".github.io" else repo+"/"))
        else:
            choice.pop("site", None)
            choice.pop("activity_url", None)
    if args.local:
        choice = {"decision":"local"}
    if args.github or args.local:
        project["hosting"] = choice
        atomic(path, (json.dumps(project, indent=2, ensure_ascii=False)+"\n").encode())
    print(json.dumps(choice, indent=2))
    if choice.get("decision") == "pending":
        print("Ask the author where to host this paper's explorer and dashboard. Continue local work while waiting.")


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as exc:
        sys.exit(exc.stderr.strip() or str(exc))
    except (OSError, ValueError) as exc:
        sys.exit(str(exc))
