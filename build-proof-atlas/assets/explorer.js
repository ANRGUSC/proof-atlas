"use strict";
const data = JSON.parse(document.querySelector("#atlas-data").textContent);
const claims = new Map(data.claims.map(c => [c.id,c]));
const views = {english:"Plain English", paper:"Paper", lamport:"Lamport proof", lean:"Lean"};
const pane = document.querySelector("#content");
function el(tag, text, cls) {
  const node = document.createElement(tag);
  if (text) node.textContent = text;
  if (cls) node.className = cls;
  return node;
}
function link(text, href, cls) {
  const a = el("a",text,cls); a.href = href; return a;
}
function route(id,view="english",graph=false) {
  return location.pathname+"#/"+(graph?"graph/":"claim/")+encodeURIComponent(id)+(graph?"":"?view="+view);
}
function locationState() {
  const [path,query] = location.hash.slice(1).split("?");
  let id; try {id = decodeURIComponent(path.replace(/^\/(graph|claim)\//,""));} catch { id=""; }
  const view = new URLSearchParams(query).get("view");
  return {id:id || data.focus || data.claims[0]?.id, view:Object.hasOwn(views,view)?view:"english", graph:path.startsWith("/graph/")};
}
function rich(text,tag="p") {
  const p=el(tag);
  for (const part of (text || "").split(/(\$[^$\n]+\$)/g)) {
    if (part.startsWith("$") && part.endsWith("$") && window.katex) {
      const span=el("span"); katex.render(part.slice(1,-1),span,{throwOnError:false,output:"htmlAndMathml",trust:false,macros:data.macros||{}}); p.append(span);
    } else p.append(document.createTextNode(part));
  }
  return p;
}
function fillList(selected) {
  const list=document.querySelector("#claim-list"), term=document.querySelector("#search").value.toLowerCase().trim();
  list.replaceChildren();
  for (const c of claims.values()) {
    if (!(c.reference+" "+c.title+" "+c.id).toLowerCase().includes(term)) continue;
    const a=link("",route(c.id),"claim-choice");
    a.append(el("small",c.reference),el("span",c.title));
    if(c.verification.evidence) a.append(el("small","✓ Machine verified on GitHub","verified-badge"));
    else a.append(el("small", [c.verification.label, c.verification.remote].filter(Boolean).join(" · ")));
    if(c.id===selected) a.setAttribute("aria-current","page");
    list.append(a);
  }
  if (!list.childElementCount) list.append(el("p","No matching claims."));
}
function relations(c, reverse=false) {
  return data.dependencies.filter(e=>reverse?e.prerequisite===c.id:e.claim===c.id);
}
function proofView(c,view) {
  pane.append(el("small",c.reference),el("h2",c.title));
  const v=c.verification;
  if(v.evidence){
    const proof=el("div",null,"lean-checks");
    proof.append(link("✓ "+v.remote,v.url,"verified-badge"));
    if(v.artifact) proof.append(link("GitHub artifact",v.artifact));
    proof.append(link("Verification record · "+v.commit.slice(0,7),v.evidence));
    pane.append(proof);
  }
  pane.append(link("Show in dependency graph",route(c.id,"english",true),"graph-link"));
  const tabs=el("nav",null,"views");tabs.setAttribute("aria-label","Reading view");
  for(const [id,label] of Object.entries(views)){
    const a=link(label,route(c.id,id)); if(id===view) a.setAttribute("aria-current","page"); tabs.append(a);
  }
  pane.append(tabs);
  if(view==="english") {
    pane.append(el("h3","What the claim says"),rich(c.summary || "An English summary is pending. The manuscript view contains the source statement."));
    pane.append(el("h3",c.kind==="conjecture"?"Open question":"High-level proof sketch"),rich(c.sketch || (c.kind==="conjecture"?"The manuscript presents this as a conjecture.":"A proof sketch has yet to be recorded.")));
  } else if(view==="paper"){
    const url="manuscript.pdf"+(c.page?"#page="+c.page:"");
    pane.append(link(c.page?"Open PDF at page "+c.page:"Open manuscript PDF",url));
    if(c.page){const frame=el("iframe");frame.title="Paper passage, page "+c.page;frame.src=url;pane.append(frame);}
    const details=el("details");details.open=!c.page;
    details.append(el("summary","Verbatim TeX · "+c.file+", line "+c.line),el("pre",c.excerpt));pane.append(details);
  } else if(view==="lamport"){
    if(c.steps){
      pane.append(el("p",c.origin || "Structured proof","muted"));
      if(c.source_url && /^https?:\/\//.test(c.source_url)) pane.append(link("Source companion and mapping ledger",c.source_url));
      for(const line of c.steps.split(/\n\s*\n/)){
        const match=line.match(/^\*\*(.*?)\*\*\s*([\s\S]*)$/);
        if(match){const step=el("div",null,"proof-step");step.append(rich(match[1],"strong"),rich(match[2]));pane.append(step);}
        else pane.append(rich(line));
      }
    }
    if(c.structured){const note=el("details");note.open=!c.steps;note.append(el("summary","Pilot supplement"),el("pre",c.structured));pane.append(note);}
    if(!c.steps && !c.structured) pane.append(el("p",c.kind==="conjecture"?"This conjecture has no recorded proof.":"No structured argument has been imported yet."));
  } else {
    const checks=el("div",null,"lean-checks");
    checks.append(el("strong",c.verification.label));
    if(c.audit_log) checks.append(link("Declaration and axiom audit",c.audit_log));
    if(c.log) checks.append(link("Lean run log",c.log));
    if(v.remote && !v.evidence) checks.append(link(v.remote,v.url));
    pane.append(checks);
    pane.append(rich(c.scope || "No Lean formalization has been selected for this claim."));
    if(c.subproofs?.length){
      const list=el("ol");
      for(const part of c.subproofs){
        const item=el("li",part.title+" · "+part.status);
        if(part.declaration) item.append(document.createTextNode(" "),el("code",part.declaration));
        list.append(item);
      }
      pane.append(el("h3","Subproofs"),list);
    }
    for(const source of c.sources){pane.append(el("h3",source.path),el("pre",source.text));}
  }
}
function render(){
  const state=locationState();
  if(graphState.resize){graphState.resize.disconnect();graphState.resize=null;}
  const hadNodeFocus=document.activeElement?.closest(".graph-node");
  document.body.classList.toggle("graph-mode",state.graph);
  document.querySelector("#graph-nav").href=route(state.id || data.focus || "", "english",true);
  fillList(state.id);pane.replaceChildren();
  const c=claims.get(state.id);
  if(!c){pane.append(el("h2",claims.size?"Claim not found":"No claims imported yet"));return;}
  if(state.graph) graphView(c); else proofView(c,state.view);
  if(state.graph && hadNodeFocus) pane.querySelector('.graph-node[aria-current="true"]')?.focus({preventScroll:true});
  document.title=(state.graph?"Dependency graph":c.reference+" · "+c.title)+" | Proof Atlas";
}
document.querySelector("#search").addEventListener("input",()=>fillList(locationState().id));
window.addEventListener("hashchange",render);render();
