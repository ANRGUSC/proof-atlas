"""Local and published views of the same correspondence evidence."""
import html
import json
import secrets
import tempfile
import threading
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, urlsplit
from urllib.request import urlopen

from author_review import BADGE, decide, digest, get_packet, review_url


def document(title,body,base="../"):
    from atlas import esc
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>{esc(title)} · Proof Atlas</title><link rel="stylesheet" href="{base}review.css"></head><body>'
            f'<header><a href="{base}index.html">Proof Atlas<span>.</span></a><nav><a href="{base}review.html">Review queue</a> '
            f'<a href="{base}progress.html">Dashboard</a> <a href="{base}guide.html">How to use this atlas</a></nav></header>'
            f'<main>{body}</main></body></html>').encode()


def render(data,secret=None,notice=""):
    from atlas import esc
    v=data["view"]
    cid=esc(data["id"])
    def para(value): return f'<p>{esc(value)}</p>' if value else ""
    def sources(items):
        return ''.join(f'<details><summary>{esc(s["name"])} · {esc(s["file"])} L{s["lines"][0]}</summary>'
                       f'<pre>{esc(s["text"])}</pre></details>' for s in items)
    def columns(view):
        return ('<div class="columns"><section><h2>Paper theorem</h2>'+para(view["paper_reading"])+
                para(view["paper_refs"])+'<details><summary>Original manuscript passage</summary><pre>'+esc(view["excerpt"])+'</pre></details></section>'
                '<section><h2>What Lean proved</h2><small>Mathematical reading of the audited Lean statement</small>'+para(view["lean_reading"] or "The agent has not supplied a current independent reading.")+
                para(view["lean_refs"])+'</section></div>')
    body=f'<p>{esc(v["edition"])} · {cid}</p><h1>{esc(data["title"])}</h1>'
    body+=para(notice)+para(data["assessment"])+columns(v)
    if data["approved"]:
        body+=para(BADGE+" · "+data["events"][-1]["by"])
    elif any(e["decision"]=="approve" for e in data["events"]):
        body+=para("The earlier approval is historical. A fresh review is available after the current comparison is ready.")
    page = data.get("page")
    pdf = f"../manuscript.pdf#page={page}" if type(page) is int and page > 0 else "../manuscript.pdf"
    body+=(f'<p><a href="{pdf}">Paper PDF</a> · <a href="../proofs/index.html#/claim/'+quote(data["id"],safe="")+
           '?view=lean">Lean source and verification evidence</a></p>')
    if data["sources"]:
        body+='<details><summary>Inspect the exact Lean statement and definitions</summary>'+sources(data["sources"])+'</details>'
    body+=para(data["issue"])+para(data["resolution"])
    body+='<p><strong>Next action · '+esc(data["actor"])+'</strong><br>'+esc(data["next"])+'</p>'
    if secret:
        body+=('<form method="post"><h2>Does the statement shown here match your paper’s theorem, including its assumptions and conclusion?</h2>'
               f'<input type="hidden" name="csrf" value="{esc(secret)}"><input type="hidden" name="token" value="{esc(data["token"])}">'
               '<label>Your name<input name="by" autocomplete="name"></label><label>Difference or question (for the proof agent)'
               '<textarea name="comment" rows="2"></textarea></label><div class="buttons">')
        if data["ready"]:
            body+='<button name="decision" value="approve">Yes, these match</button>'
        body+=('<button name="decision" value="difference">I see a difference</button>'
               '<button name="decision" value="skip">Skip for now</button></div>'
               '<p>Decisions save to this local project. Publishing the project shares the reviewer’s name and feedback.</p></form>')
    else:
        body+=para("To respond, ask your agent to open the local review dashboard for this paper.")
    if data["events"]:
        body+='<h2>Review history</h2>'
    for e in reversed(data["events"]):
        label=BADGE if e["decision"]=="approve" else "Difference reported"
        body+=(f'<details><summary>{esc(label)} · {esc(e["by"])} · {esc(e["at"])}</summary>'+para(e["comment"])+
               para(e["view"]["edition"])+columns(e["view"])+sources(e.get("sources",[]))+'</details>')
    return document(data["title"],body)


def queue(entries):
    from atlas import esc
    rows="".join(f'<li><a href="{review_url(d["id"])}">{esc(d["title"])}</a><br>'
                 f'{esc(BADGE if d["approved"] else d["assessment"])} · {esc(d["actor"])}: {esc(d["next"])}</li>' for d in entries)
    return document("Author review",'<h1>Author review</h1><p>Compare the paper and the formal statement. Human review is optional.</p><ol>'+rows+'</ol>',"./")


def serve(root,open_browser=True,port=0):
    from atlas import atomic, build_local, manuscript_claims
    root=root.resolve()
    key=digest(str(root))
    session=Path(tempfile.gettempdir())/("proof-atlas-review-"+key+".json")
    try:
        cached=json.loads(session.read_bytes())
        base=f'http://127.0.0.1:{int(cached["port"])}'
        with urlopen(base+"/session",timeout=1) as response:
            if response.read(128).decode()==key:
                print(base+"/review.html",flush=True)
                if open_browser: webbrowser.open(base+"/review.html")
                return
    except (ValueError,OSError,KeyError):
        pass
    build_local(root)
    secret=secrets.token_urlsafe(32)
    def claims():
        p=json.loads((root/"project.json").read_bytes())
        return {"/"+review_url(c["id"]):c["id"] for c in manuscript_claims(root,p)}
    class Handler(SimpleHTTPRequestHandler):
        def reply(self,body,status=200):
            self.send_response(status)
            self.send_header("Content-Type","text/html; charset=utf-8")
            self.send_header("Cache-Control","no-store")
            self.send_header("X-Frame-Options","DENY")
            self.send_header("Content-Length",str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        def do_GET(self):
            if self.headers.get("Host")!=base.removeprefix("http://"):
                return self.send_error(403)
            path=urlsplit(self.path).path
            if path=="/session":
                return self.reply(key.encode())
            if path.startswith("/review/") or path=="/review.html":
                try:
                    ids=claims()
                    if path=="/review.html":
                        return self.reply(queue([get_packet(root,c) for c in ids.values()]))
                    if path in ids:
                        notice="Saved locally." if urlsplit(self.path).query=="saved" else ""
                        return self.reply(render(get_packet(root,ids[path]),secret,notice))
                except (ValueError,KeyError,OSError) as error:
                    return self.send_error(409,str(error))
            super().do_GET()
        def do_POST(self):
            if self.headers.get("Origin")!=base or self.headers.get("Host")!=base.removeprefix("http://"):
                return self.send_error(403)
            try:
                if self.headers.get_content_type() != "application/x-www-form-urlencoded":
                    return self.send_error(415)
                size=int(self.headers.get("Content-Length","0"))
                if not 0<size<=32768: return self.send_error(413)
                form={k:v[0] for k,v in parse_qs(self.rfile.read(size).decode(),keep_blank_values=True).items()}
                if not secrets.compare_digest(form["csrf"],secret): return self.send_error(403)
                path=urlsplit(self.path).path
                cid=claims()[path]
                decide(root,cid,form["token"],form["decision"],form.get("by",""),form.get("comment",""))
                self.send_response(303)
                self.send_header("Location","/review.html" if form["decision"]=="skip" else path+"?saved")
                self.end_headers()
            except (ValueError,KeyError,OSError) as error:
                self.reply(document("Review needs attention",'<h1>Review needs attention</h1><p>'+html.escape(str(error))+
                                    '</p><a href="">Reload the claim</a>'),409)
    from preview import inputs, watch
    with ThreadingHTTPServer(("127.0.0.1",port),partial(Handler,directory=str(root/"dist"))) as server:
        base=f"http://127.0.0.1:{server.server_port}"
        atomic(session,json.dumps({"port":server.server_port}).encode())
        stop=threading.Event()
        thread=threading.Thread(target=watch,args=(root,stop,inputs(root)),daemon=True)
        thread.start()
        try:
            print(base+"/review.html",flush=True)
            if open_browser: webbrowser.open(base+"/review.html")
            server.serve_forever()
        finally:
            stop.set()
            thread.join(timeout=5)
