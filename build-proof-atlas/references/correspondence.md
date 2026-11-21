# Recording correspondence

Before proof work, compare each proposed formal statement against the pinned paper. Record its objects, inherited hypotheses, ranges, and every conclusion. Track missing conclusions as subproofs. Repeat with the checked declaration before marking the automated comparison complete.

## Independent readings

Use the agent running this skill to orchestrate two isolated LLM readings. Give the paper reader only the manuscript passage, edition and surrounding assumptions. Give the other reader the exact expected Lean statement, its definitions and pin, with no paper text, title-based hints or prewritten paper summary. Ask each for mathematical language that a non-Lean author can verify and precise source references. When workers are available, launch the two readers in separate contexts. If independent readings are unavailable, keep comparison pending and continue authorized proof work.

Run `atlas.py review-inputs PROJECT CLAIM --side paper` and `--side lean` separately to collect the evidence. The paper reader can inspect the manuscript at the cited lines; the Lean reader can inspect the audited modules. The coordinator compares objects, quantifiers, assumptions, ranges and all conclusions. Before recording a match, have an LLM check the exact displayed Lean reading against the formal type and cited definitions. Correct omitted quantifiers or mistranslated terms and repeat this check. Preserve distinctions such as one-sided versus two-sided bounds, and state meaningful extra conclusions. Automated agreement is evidence, not a guarantee.

## Record or revise the assessment

Create a JSON object containing the unmodified `bindings` returned by the command, two nonempty strings `paper_reading` and `lean_reading`, corresponding `paper_refs` and `lean_refs`, and `result` equal to `match`, `difference`, or `unsure`. For the latter two also supply `issue`, `actor` (Proof agent or Author), and `next` (what will close the gap). Submit it with `atlas.py compare PROJECT CLAIM --readings FILE`. The command validates the bindings and updates comparisons.json and the atlas. Reuse the recorded bindings only for the evidence actually read.

For feedback from a reviewer, inspect review history and the `reported_differences` IDs from `review-inputs` without --side. After investigating, include the addressed IDs as a `resolves` array and a specific `resolution`. Retain already resolved IDs when updating the assessment. Reconcile an incorrect display with the audited formal statement, complete a missing bound, derive the paper claim from a stronger one, or quarantine a proof of a different theorem under its correct scope. For genuine ambiguities show competing readings. Ask the author for a mathematical decision when evidence calls for one. Pin a revised manuscript as a new edition and compare again.

## Author decisions and release

Start `atlas.py review PROJECT` for the author. The CLI alternative is `atlas.py approve PROJECT --by NAME`. These paths share the same evidence and decisions. Launching review is authorized when requested; approving on someone’s behalf is not. Never manufacture reviews.json. On resume, inspect outstanding feedback and continue within the authorized scope. Report saved-local versus published state accurately. Before publishing include the intended comparisons.json and reviews.json in the checked commit and verify the public record after deployment. Self-reported reviewer names do not authenticate an identity. Static Pages exposes evidence; local review collects decisions.
