# Evaluation and improvement

## Visual guide

The [implementation hierarchy](diagrams/hierarchy.svg) shows the CLI/controller
and its routing, evaluation, and persistence dependencies. The
[feedback-loop diagram](diagrams/feedback-loop.svg) shows how a candidate passes
through reflection, re-evaluation, acceptance/rejection, and checkpointing.
Both are embedded in the [README](../README.md).

Editable Excalidraw sources are saved alongside the previews:
[hierarchy](diagrams/hierarchy.excalidraw) and
[feedback loop](diagrams/feedback-loop.excalidraw). They depict this deterministic
implementation, not a hierarchy of autonomous LLM agents. The hierarchy arrows
mean "calls/uses"; the feedback arrows show control and policy flow. The audit
is a CLI reporting step after the loop, not an acceptance signal.

## What this demo actually improves

The agent is an ordinary deterministic support-ticket router. Given text, it
chooses one of four labels: `billing`, `account`, `security`, or `other`. The
three strategy settings in `agent.Policy` begin as `false`:

| Setting | Initial behavior | Enabled behavior |
|---|---|---|
| `normalize_case` | Case-sensitive matching | Unicode case folding before matching |
| `whole_words` | Substring matching | Python regex word-boundary matching |
| `security_first` | Billing, then account, then security | Security, then billing, then account |

The vocabulary is fixed: billing uses `charge`, `invoice`, and `refund`; account
uses `login`, `password`, and `reset`; security uses `hacked`, `phishing`, and
`stolen`. Unmatched text is `other`. If several categories match, the policy's
priority decides. Human-authored expectations require security to take priority
over billing or account whenever a security keyword occurs.

Improvement changes these strategy settings, not source code, task labels,
scoring criteria, or model weights. The execution algorithm and reflection
algorithm themselves stay fixed. There is no LLM, external service, training
job, hidden credential, or claim that this is unrestricted self-improvement.

## Fixed datasets and measurable scoring

`evaluation.py` owns immutable tuples of human-authored labeled cases. The
agent and proposer cannot supply replacement labels through the CLI.

| Split | Size | Purpose | Used to select changes? |
|---|---:|---|---|
| Development | 8 | Execution feedback and candidate improvement measurement | Yes |
| Regression | 4 | Protect existing behavior in each category | Veto only |
| Audit | 6 | Report a separate set of outcomes after the loop | No |

Every case gets one exact-match point if the predicted label equals the
expected label, otherwise zero. Accuracy is correct cases divided by all cases;
all cases have equal weight. No subjective grader, random seed, runtime metric,
or iteration-dependent score is used. Reports retain the case ID, ticket,
expected label, and actual prediction, including failures.

The eight development cases cover four simple category examples plus uppercase
`INVOICE`, the false substring match in `charger`, a hacked ticket that also
mentions an invoice/refund, and a combined uppercase/security-priority case.
This intentionally small task makes every improvement inspectable.

The regression cases exercise a normal billing request, account recovery,
security keywords, and an unmatched feature request. They all pass initially.
The audit includes different uppercase billing/account text, a mixed
security/billing request, a different charger sentence, and two routine cases.
It is not supplied to reflection, acceptance, or termination decisions.

The audit is **not a statistically independent, secret benchmark**: its cases
are public and hand-authored for the same three known weaknesses. Although it
provides a separate reporting split, 6/6 does not establish real-world
generalization. The audit fingerprint is recorded for reproducibility, not
used to rank candidates.

## The feedback loop

1. **Execute and evaluate.** Run the current policy on development and regression
   cases using the fixed evaluator.
2. **Reflect.** For each currently disabled strategy, temporarily enable it and
   execute the agent on the current development failures only. Count which
   failures that candidate repairs. Choose the candidate repairing the most
   failures, breaking ties in setting order: case normalization, whole words,
   security priority. These probes are a deterministic, bounded local search;
   they are not fabricated feedback or a scripted sequence of scores.
3. **Propose.** Emit an allowlisted `Policy` and a rationale naming the repaired
   failure IDs. An already evaluated policy is not proposed again.
4. **Re-evaluate.** Independently execute the proposed policy on the complete
   development and regression suites. A promising failure-only probe is not
   enough to accept a change.
5. **Accept or reject.** Require strictly higher development accuracy **and no
   previously correct case becoming wrong on either suite**. Even a higher
   total score is rejected if it hides an individual regression. Rejections
   preserve the previous accepted policy and are written to history.
6. **Carry forward.** Checkpoint the accepted policy and decision; evaluate the
   next proposal relative to that policy, not the original baseline.

This is a greedy hill-climber over three Boolean settings, not an optimizer
that can invent new capabilities. On a fresh run, failure-driven tie-breaking
selects case normalization first (4/8 to 5/8). From that policy, security priority
repairs two failures (5/8 to 7/8), so it is selected next. Whole-word matching
repairs the last failure (7/8 to 8/8). This order emerges from the observed
failure repairs: for example, feedback containing only the charger failure
instead selects whole-word matching first.

The default data deliberately demonstrates a successful path. Rejection,
plateau, and no-candidate behavior are exercised by automated tests; the system
does not manufacture failed proposals just to make the console look realistic.

## Bounds and termination

The first applicable terminal condition wins:

1. `target_reached`: development has no failures (8/8 on this suite).
2. `iteration_limit`: the total persisted candidate-evaluation count reaches
   `--max-iterations` (default 6).
3. `no_candidate`: no untried single-setting change repairs a current
   development failure.

Each proposed candidate counts once whether accepted or rejected. Reflection
can additionally probe at most three single-setting variants on at most eight
failed development cases per iteration. The budget counts full candidate
evaluations, not each individual ticket execution or internal probe. Replay of
existing history during resume is verification, not a new improvement round.

The built-in proposer only enables settings; it never disables them. There
are eight possible Boolean policies including the initial policy. Accepted
scores must strictly increase, repeated proposals are explicitly rejected as
an error, and the total iteration budget provides another bound. The loop
cannot continue indefinitely under its built-in proposer.

A setting that requires an initially neutral or harmful intermediate change
will not be discovered by this greedy search. If none of the allowed settings
helps, the honest outcome is `no_candidate`, not a rising score.

## Persistence, resuming, and reproducibility

Run from the repository root with Python 3.10 or later:

```powershell
python improve.py
python improve.py --state .improvement\partial.json --max-iterations 1
python improve.py --state .improvement\partial.json --max-iterations 6
python -m unittest discover -s tests -v
```

The first command uses `.improvement/state.json`. The next two demonstrate
stopping after one proposal and resuming the same accepted strategy and
history. The larger budget is a new total ceiling, not six additional rounds.
Running an already completed checkpoint reports its existing history without
adding iterations. Use a new path for a fresh run.

A checkpoint contains:

- The state schema version and a SHA-256 fingerprint of the versioned datasets.
- The currently accepted strategy and initial/current evaluation reports.
- Each candidate policy, rationale, pre-change feedback, candidate predictions,
  acceptance decision, rejection reasons, and iteration number.
- The stop reason (`running` during checkpointed execution).

Before resuming, the loader validates the policy schema, dataset fingerprint,
and history. It replays every recorded candidate through the current evaluator
and checks the recorded acceptance decisions, final policy, and scores. A
changed suite, altered scores, malformed JSON, or inconsistent history causes
an explicit error instead of silently starting over. Maintainers must bump
`EVALUATOR_VERSION` when changing evaluator semantics not represented by the
dataset contents.

Writes use a same-directory temporary file, flush/fsync, and atomic replacement
of the checkpoint. A process interrupted between checkpoints can resume from
the last completed write. This is a local **single-writer** demo: do not run two
processes against one checkpoint. Atomic replacement does not provide
multi-process locking, cryptographic authenticity, or a full power-loss
durability guarantee on every filesystem.

State files are local data, not executable programs. There is no `eval`,
generated-code execution, automatic shell execution by the router, or ability
to rewrite evaluator expectations. The fingerprint catches dataset changes,
not malicious edits to the trusted repository; this is not a security sandbox.

## Automated coverage

The standard-library `unittest` suite checks real prediction changes,
the reproducible score trajectory, failure-dependent proposal selection,
strictly improving acceptance, rejection of individual regressions even when
overall accuracy rises, a separate regression-suite veto, plateau rejection,
no-candidate termination, total-budget termination, resume, completed-run
idempotence, invalid policy/state/budget handling, replay integrity, checkpoint
round trips, and CLI output/error behavior.

Run the test command above to verify these claims in your environment. The
expected fresh-run measurements are development 4/8 to 8/8, regression 4/4
throughout, and audit 2/6 to 6/6; these are computed by executing the agent, not
assigned by the improvement loop.

## Limits and a real LLM-backed adaptation

These policies improve a narrow benchmark, not intelligence in general.
Keyword routing misses synonyms, negation, context, multilingual language,
and categories outside its vocabulary. Word boundaries use Python regex
semantics, not a natural-language tokenizer. Repeated development evaluation
can overfit, and regression guards only protect known cases. More iterations
alone do not guarantee improvement. A perfect tiny-suite score can coexist
with poor real-world behavior.

For a real LLM agent, keep the same separation: a trusted evaluator owns
fixed tasks and rubrics; the executing model uses a versioned prompt/tool
strategy; a proposer returns structured, allowlisted prompt or strategy
changes; and an external gate promotes only validated candidates. Re-run
baselines and candidates across multiple stochastic trials, account for
variance, cost, latency, and safety failures, and use human review where
appropriate. Preserve unseen, rotating holdouts outside the proposer and
use a rollout/rollback mechanism before production adoption.

Do not let the model change its grader, read private holdout labels, grant
itself new permissions, or execute arbitrary suggested code. Treat retrieved
content and proposed changes as untrusted input. Improving prompts or
strategies is not training model weights; actual fine-tuning would be a
separate, explicitly controlled training pipeline. None of those LLM,
fine-tuning, or deployment features is implemented or implied here.
