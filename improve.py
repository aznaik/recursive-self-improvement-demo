"""Bounded execute/evaluate/reflect/propose/re-evaluate loop."""

import argparse
from dataclasses import dataclass, field, replace
import json
import os
from pathlib import Path
import tempfile
from typing import Callable

from agent import Policy, route
from evaluation import AUDIT, Evaluation, evaluate, score, suite_fingerprint


STATE_VERSION = 1
FEATURES = ("normalize_case", "whole_words", "security_first")
TERMINATIONS = {"running", "target_reached", "no_candidate", "iteration_limit"}


def policy_key(policy: Policy) -> tuple[bool, ...]:
    return tuple(policy.to_dict()[name] for name in FEATURES)


@dataclass(frozen=True)
class Proposal:
    policy: Policy
    rationale: str


def reflect(
    policy: Policy,
    feedback: Evaluation,
    attempted: set[tuple[bool, ...]],
) -> Proposal | None:
    """Probe only failures, then propose the strategy repairing the most."""
    best: Proposal | None = None
    best_repairs = 0
    for feature in FEATURES:
        if getattr(policy, feature):
            continue
        candidate = replace(policy, **{feature: True})
        if policy_key(candidate) in attempted:
            continue
        repairs = [
            item.id
            for item in feedback.development.failures
            if route(item.ticket, candidate) == item.expected
        ]
        if len(repairs) > best_repairs:
            best_repairs = len(repairs)
            best = Proposal(
                candidate,
                f"Enable {feature}: repairs failed cases {', '.join(repairs)}",
            )
    return best


def acceptance(before: Evaluation, candidate: Evaluation) -> tuple[bool, list[str]]:
    reasons = []
    if candidate.development.correct <= before.development.correct:
        reasons.append("Development accuracy did not strictly improve")
    for name in ("development", "regression"):
        previous = getattr(before, name).observations
        proposed = getattr(candidate, name).observations
        if [
            (item.id, item.ticket, item.expected) for item in previous
        ] != [
            (item.id, item.ticket, item.expected) for item in proposed
        ]:
            raise ValueError("Cannot compare evaluations of different cases")
        lost = [
            old.id
            for old, new in zip(previous, proposed)
            if old.correct and not new.correct
        ]
        if lost:
            reasons.append(f"Regressed {name} cases: {', '.join(lost)}")
    return not reasons, reasons


def make_record(
    iteration: int, policy: Policy, proposal: Proposal
) -> dict:
    before = evaluate(policy)
    candidate = evaluate(proposal.policy)
    accepted, reasons = acceptance(before, candidate)
    return {
        "iteration": iteration,
        "before_policy": policy.to_dict(),
        "candidate_policy": proposal.policy.to_dict(),
        "rationale": proposal.rationale,
        "feedback": before.to_dict(),
        "candidate_evaluation": candidate.to_dict(),
        "accepted": accepted,
        "rejection_reasons": reasons,
    }


@dataclass
class State:
    policy: Policy = field(default_factory=Policy)
    history: list[dict] = field(default_factory=list)
    termination: str = "running"

    def to_dict(self) -> dict:
        return {
            "version": STATE_VERSION,
            "suite_fingerprint": suite_fingerprint(),
            "policy": self.policy.to_dict(),
            "initial_evaluation": evaluate(Policy()).to_dict(),
            "current_evaluation": evaluate(self.policy).to_dict(),
            "history": self.history,
            "termination": self.termination,
        }


def save_state(path: Path, state: State) -> None:
    """Replace one complete checkpoint; never leave a partially written JSON file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, delete=False
        ) as stream:
            temporary = Path(stream.name)
            json.dump(state.to_dict(), stream, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def load_state(path: Path) -> State:
    with path.open(encoding="utf-8") as stream:
        data = json.load(stream)
    expected_keys = set(State().to_dict())
    if not isinstance(data, dict) or set(data) != expected_keys:
        raise ValueError("Invalid state shape")
    if type(data["version"]) is not int or data["version"] != STATE_VERSION:
        raise ValueError("Unsupported state version")
    if data["suite_fingerprint"] != suite_fingerprint():
        raise ValueError("Evaluation suite changed; use a new state file")
    if not isinstance(data["history"], list):
        raise ValueError("State history must be a list")
    if not isinstance(data["termination"], str) or data["termination"] not in TERMINATIONS:
        raise ValueError("Invalid termination reason")

    # Replay every acceptance decision rather than trusting persisted scores.
    state = State()
    attempted = set()
    for index, record in enumerate(data["history"], start=1):
        if not isinstance(record, dict):
            raise ValueError("Invalid history record")
        candidate = Policy.from_dict(record.get("candidate_policy"))
        rationale = record.get("rationale")
        if not isinstance(rationale, str) or not rationale:
            raise ValueError("Invalid proposal rationale")
        key = policy_key(candidate)
        if key in attempted:
            raise ValueError("History contains a repeated candidate")
        attempted.add(key)
        expected = make_record(index, state.policy, Proposal(candidate, rationale))
        if record != expected:
            raise ValueError("History does not match replayed evaluation decisions")
        state.history.append(expected)
        if expected["accepted"]:
            state.policy = candidate
    if Policy.from_dict(data["policy"]) != state.policy:
        raise ValueError("Stored policy does not match accepted history")
    state.termination = data["termination"]
    if data != state.to_dict():
        raise ValueError("Stored scores do not match current evaluation")
    return state


Proposer = Callable[
    [Policy, Evaluation, set[tuple[bool, ...]]], Proposal | None
]


def run_loop(
    path: Path,
    max_iterations: int = 6,
    proposer: Proposer = reflect,
) -> State:
    if type(max_iterations) is not int or max_iterations < 1:
        raise ValueError("max_iterations must be a positive integer")
    state = load_state(path) if path.exists() else State()
    attempted = {
        policy_key(Policy.from_dict(record["candidate_policy"]))
        for record in state.history
    }
    state.termination = "running"
    save_state(path, state)
    while True:
        feedback = evaluate(state.policy)
        if not feedback.development.failures:
            state.termination = "target_reached"
            break
        if len(state.history) >= max_iterations:
            state.termination = "iteration_limit"
            break
        proposal = proposer(state.policy, feedback, attempted.copy())
        if proposal is None:
            state.termination = "no_candidate"
            break
        key = policy_key(proposal.policy)
        if key in attempted:
            raise ValueError("Proposer returned an already attempted candidate")
        if not isinstance(proposal.rationale, str) or not proposal.rationale:
            raise ValueError("Proposal rationale must be a nonempty string")
        attempted.add(key)
        record = make_record(len(state.history) + 1, state.policy, proposal)
        state.history.append(record)
        if record["accepted"]:
            state.policy = proposal.policy
        save_state(path, state)
    save_state(path, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state", type=Path, default=Path(".improvement") / "state.json"
    )
    parser.add_argument(
        "--max-iterations",
        type=int,
        default=6,
        help="Total candidate-evaluation budget, including earlier resumed iterations",
    )
    args = parser.parse_args()
    try:
        state = run_loop(args.state, args.max_iterations)
    except (OSError, ValueError) as error:
        parser.exit(2, f"error: {error}\n")

    baseline = evaluate(Policy())
    current = evaluate(state.policy)
    print(
        f"Development: {baseline.development.correct}/"
        f"{len(baseline.development.observations)}"
        f" -> {current.development.correct}/"
        f"{len(current.development.observations)}"
    )
    for record in state.history:
        candidate = record["candidate_evaluation"]["development"]
        decision = "ACCEPT" if record["accepted"] else "REJECT"
        print(
            f"Iteration {record['iteration']}: {decision}"
            f" {candidate['correct']}/{candidate['total']}"
            f" | {record['rationale']}"
        )
        for reason in record["rejection_reasons"]:
            print(f"  {reason}")
    print(
        f"Regression: {current.regression.correct}/"
        f"{len(current.regression.observations)}"
    )
    audit_before = score(Policy(), AUDIT)
    audit_after = score(state.policy, AUDIT)
    print(
        f"Audit (not used for selection): {audit_before.correct}/"
        f"{len(audit_before.observations)} -> {audit_after.correct}/"
        f"{len(audit_after.observations)}"
    )
    print(f"Stop: {state.termination}")
    print(f"Policy: {json.dumps(state.policy.to_dict(), sort_keys=True)}")
    print(f"Checkpoint: {args.state}")


if __name__ == "__main__":
    main()
