# How to use this atlas

## How do I start?

Install the build-proof-atlas skill in your coding agent, or point it to the downloaded SKILL.md. Give it a paper edition and choose one theorem or the whole paper. For example: “Build a proof atlas for this paper. Formalize all stated results, keep conjectures separate, and ask me where to host it.” The agent checks the edition, creates the explorer and dashboard, and finds or installs the pinned Lean tools.

## Where should I look first?

Open the explorer to read a theorem in plain English, see the original paper passage, follow a structured proof when available, or inspect the Lean source. The dependency graph can follow a selected chain or show the entire paper. Open the dashboard to see the authorized scope, current claim, checked subproofs, remaining obligations, and evidence.

## What has actually been verified?

Lean checked means Lean accepted the recorded formal declaration against its expected type. Machine verified on GitHub means a separate run checked it for the published revision; the badge links to evidence. The automated correspondence comparison is an assessment by the proof agent. Author reviewed the theorem statement proved in Lean means a named person explicitly accepted the displayed comparison. The exact material and review date are available in review history.

## How do I open author review?

Say “Open the local review dashboard for this paper.” You can ask during proof generation or after returning to the project later. The agent starts or reopens the local browser server. Open any claim from the queue. For a direct launch, run `python <skill>/scripts/atlas.py review <project-folder>`. To share a terminal session without opening a browser automatically, add `--no-open`. The terminal alternative is `python <skill>/scripts/atlas.py approve <project-folder> --by "Your name"`. Add a claim label before --by to select just that theorem.

## What am I being asked to decide?

Each checked comparison opens with **How the statements compare**. Its rows explain why the paper and the formal theorem agree or differ on each important case. Read **Scope of the conclusion** and **Agent recommendation** for the decision the evidence supports. When an assumption or endpoint is in doubt, the agent supplies wording you can use or edit when reporting the difference. You can disagree with the recommendation. Only your explicit decision records an author review.

Read the paper passage and the mathematical interpretation under What Lean proved. Compare the objects, assumptions, ranges, and conclusions. Expand a definition or open the paper PDF and exact Lean evidence if helpful. Select Yes, these match to approve. Select I see a difference and describe it in your own words to flag a discrepancy or ask a question. Skip for now leaves the decision open. An author may review as many or as few claims as they wish.

## What if the two sides differ?

If a display says “all dimensions” but the formal declaration says “dimension at least two,” the agent corrects the display and compares again. If the paper actually claims all dimensions, the agent investigates dimension one. It may prove that case, combine the cases, and return with the corrected theorem. If the agent finds a counterexample instead, it shows the counterexample and asks the author how to address it. Only the author decides whether to revise the paper.

A verified upper bound stays available if the paper also needs a lower bound. The missing lower bound becomes an open paper obligation. When Lean proves a stronger result, the agent can derive the paper’s exact theorem. When interpretations are ambiguous, the agent presents the alternatives and their mathematical consequences. A reported discrepancy reopens correspondence while retaining the formal evidence. An active agent checks the review queue at checkpoints. If your agent session has ended, say “Resume this atlas and resolve the reported differences.” The browser saves feedback even when no agent is running.

## Where do suggested paper changes go?

An optional **Lean proves more** note identifies extra results that come with the proof. You can accept coverage of the paper and add your own wording in **Scope note to display with my approval**. That note accompanies your current approval in the explorer. A scope note explains what was checked. Use **Suggested manuscript edit** when you want a change tracked for the paper.

Open **Manuscript edits** from the review dashboard. The optional manuscript-edit field on each review sends wording suggestions to this list, including suggestions saved alongside an approval. The agent also checks ordinary review comments for editorial requests when it resumes. A proposed edit quotes the current passage, provides a replacement and links to its claim.

To make the change yourself, copy the proposed replacement. On the local edits page select the suggestions you handled and choose **Clear selected**, or use **Clear all active suggestions**. **Restore selected** brings cleared items back. Clearing is a housekeeping decision and does not certify that a manuscript was revised.

To delegate the edit, select it and choose **Request draft for selected**. An active agent prepares a reviewable change in the editable manuscript and records the draft location. To resume a stopped session, say “Prepare the requested manuscript edits.” Review the diff before authorizing integration. The agent records the applied revision and refreshes affected comparisons when the new paper edition is pinned. Approving a theorem, requesting a draft, and publishing a revised manuscript are separate decisions.

## What happens to my decision?

The local project records your supplied name, time, decision, and the comparison you saw. Your name is self-reported. Adding the first analysis to previously approved, identical readings preserves that approval and labels it as predating the analysis. The local dashboard saves decisions before publication. Ask the agent to publish the committed review records to the paper’s authorized destination when ready. Hosted GitHub Pages displays that revision’s history; open your local project to submit a new decision. A coauthor working in another checkout should sync and reconcile review records before publishing.

A changed formal statement, relevant project definition, toolchain pin, paper source, or displayed reading calls for a fresh comparison or review. A proof-only edit can keep approval after Lean rechecks the same statement. An old browser tab asks you to reload when the evidence or decisions have changed. Historical decisions remain available.

## Why is a claim waiting?

The queue gives the next actor and action. Proof work can proceed with review skipped. Older atlases keep their machine evidence and counts. To enable optional review for an old claim, ask the agent to refresh its audit with the current skill and prepare the two independent readings. If you only want to read the atlas, no migration is needed.
