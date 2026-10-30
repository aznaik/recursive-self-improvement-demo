from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from agent import Policy, route
from evaluation import AUDIT, Case, Evaluation, Result, evaluate, score
from improve import (
    Proposal,
    State,
    acceptance,
    load_state,
    reflect,
    run_loop,
    save_state,
)


class AgentTests(unittest.TestCase):
    def test_strategy_changes_real_predictions(self):
        ticket = "HACKED account; INVOICE appeared"
        self.assertEqual(route(ticket, Policy()), "other")
        self.assertEqual(route(ticket, Policy(normalize_case=True)), "billing")
        self.assertEqual(route(ticket, Policy(True, True, True)), "security")
        self.assertEqual(route("charger", Policy()), "billing")
        self.assertEqual(route("charger", Policy(whole_words=True)), "other")

    def test_fixed_baseline_and_final_scores(self):
        baseline = evaluate(Policy())
        final = evaluate(Policy(True, True, True))
        self.assertEqual(baseline.development.correct, 4)
        self.assertEqual(final.development.correct, 8)
        self.assertEqual(final.regression.correct, 4)
        self.assertEqual(score(Policy(), AUDIT).correct, 2)
        self.assertEqual(score(Policy(True, True, True), AUDIT).correct, 6)

    def test_strict_policy_validation(self):
        for invalid in ({}, {"normalize_case": True}, {"extra": True}):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                Policy.from_dict(invalid)
        with self.assertRaises(ValueError):
            Policy(normalize_case=1)

    def test_evaluator_rejects_empty_or_duplicate_cases(self):
        with self.assertRaises(ValueError):
            score(Policy(), ())
        duplicate = Case("same", "invoice", "billing")
        with self.assertRaises(ValueError):
            score(Policy(), (duplicate, duplicate))


class AcceptanceTests(unittest.TestCase):
    def test_equal_or_lower_score_is_rejected(self):
        result = evaluate(Policy(normalize_case=True))
        self.assertFalse(acceptance(result, result)[0])
        self.assertFalse(acceptance(result, evaluate(Policy()))[0])

    def test_higher_total_cannot_hide_individual_regression(self):
        before = evaluate(Policy(normalize_case=True))
        candidate = evaluate(Policy(whole_words=True, security_first=True))
        self.assertGreater(candidate.development.correct, before.development.correct)
        accepted, reasons = acceptance(before, candidate)
        self.assertFalse(accepted)
        self.assertTrue(any("dev-case" in reason for reason in reasons))

    def test_regression_suite_is_a_separate_guard(self):
        before = evaluate(Policy())
        improved = evaluate(Policy(normalize_case=True))
        intact_guard = score(Policy(), (Case("guard-other", "hello", "other"),))
        broken_guard = Result(
            (replace(intact_guard.observations[0], actual="billing"),)
        )
        accepted, reasons = acceptance(
            Evaluation(before.development, intact_guard),
            Evaluation(improved.development, broken_guard),
        )
        self.assertFalse(accepted)
        self.assertIn("Regressed regression cases: guard-other", reasons)

    def test_mismatched_cases_cannot_be_compared(self):
        before = evaluate(Policy())
        different = score(Policy(), (Case("different", "hello", "other"),))
        with self.assertRaises(ValueError):
            acceptance(before, Evaluation(before.development, different))


class LoopTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "nested" / "state.json"

    def test_feedback_drives_measured_improvements_and_persistence(self):
        state = run_loop(self.path)
        self.assertEqual(state.termination, "target_reached")
        self.assertEqual(state.policy, Policy(True, True, True))
        self.assertEqual(
            [
                item["candidate_evaluation"]["development"]["correct"]
                for item in state.history
            ],
            [5, 7, 8],
        )
        self.assertTrue(all(item["accepted"] for item in state.history))
        self.assertEqual(load_state(self.path), state)
        self.assertEqual(run_loop(self.path), state)
        self.assertEqual(
            sorted(item.name for item in self.path.parent.iterdir()), ["state.json"]
        )

    def test_reflection_depends_on_actual_failure_feedback(self):
        policy = Policy()
        boundary_only = score(policy, (Case("boundary", "charger", "other"),))
        proposal = reflect(
            policy, Evaluation(boundary_only, evaluate(policy).regression), set()
        )
        self.assertIsNotNone(proposal)
        self.assertEqual(proposal.policy, Policy(whole_words=True))
        self.assertIn("boundary", proposal.rationale)

    def test_iteration_limit_and_resume_carry_policy_forward(self):
        first = run_loop(self.path, max_iterations=1)
        self.assertEqual(first.termination, "iteration_limit")
        self.assertEqual(first.policy, Policy(normalize_case=True))
        self.assertEqual(len(first.history), 1)
        resumed = run_loop(self.path, max_iterations=3)
        self.assertEqual(resumed.termination, "target_reached")
        self.assertEqual(len(resumed.history), 3)
        self.assertEqual(resumed.history[0], first.history[0])
        self.assertEqual(resumed.history[1]["before_policy"], first.policy.to_dict())

    def test_no_candidate_terminates_without_inventing_progress(self):
        state = run_loop(self.path, proposer=lambda *_: None)
        self.assertEqual(state.termination, "no_candidate")
        self.assertEqual(state.policy, Policy())
        self.assertEqual(state.history, [])
        self.assertEqual(load_state(self.path), state)

    def test_rejected_candidate_does_not_change_live_policy(self):
        def proposer(policy, feedback, attempted):
            if attempted:
                return None
            return Proposal(policy, "Deliberately propose a plateau")

        state = run_loop(self.path, proposer=proposer)
        self.assertEqual(state.policy, Policy())
        self.assertFalse(state.history[0]["accepted"])
        self.assertEqual(state.termination, "no_candidate")
        self.assertEqual(load_state(self.path), state)

    def test_regression_rejection_preserves_previous_accepted_policy_on_disk(self):
        def proposer(policy, feedback, attempted):
            if not attempted:
                return Proposal(Policy(normalize_case=True), "Repair case handling")
            if len(attempted) == 1:
                return Proposal(
                    Policy(whole_words=True, security_first=True),
                    "Higher total, but loses a previously correct case",
                )
            return None

        state = run_loop(self.path, proposer=proposer)
        self.assertEqual(state.policy, Policy(normalize_case=True))
        self.assertEqual([item["accepted"] for item in state.history], [True, False])
        self.assertIn(
            "Regressed development cases: dev-case",
            state.history[1]["rejection_reasons"],
        )
        self.assertEqual(state.termination, "no_candidate")
        self.assertEqual(load_state(self.path), state)

    def test_repeated_candidate_errors_instead_of_looping(self):
        def proposer(*_):
            return Proposal(Policy(), "Repeated plateau")

        with self.assertRaisesRegex(ValueError, "already attempted"):
            run_loop(self.path, proposer=proposer)
        checkpoint = load_state(self.path)
        self.assertEqual(len(checkpoint.history), 1)
        self.assertEqual(checkpoint.policy, Policy())

    def test_invalid_budget_does_not_create_state(self):
        for budget in (0, -1, True):
            with self.subTest(budget=budget), self.assertRaises(ValueError):
                run_loop(self.path, max_iterations=budget)
        self.assertFalse(self.path.exists())

    def test_changed_suite_and_corrupted_history_are_explicit_errors(self):
        run_loop(self.path)
        original = json.loads(self.path.read_text(encoding="utf-8"))
        changed = dict(original, suite_fingerprint="different")
        self.path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "suite changed"):
            load_state(self.path)
        original["history"][0]["accepted"] = False
        self.path.write_text(json.dumps(original), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "replayed"):
            load_state(self.path)

    def test_malformed_state_is_not_silently_reset(self):
        self.path.parent.mkdir(parents=True)
        self.path.write_text("{", encoding="utf-8")
        with self.assertRaises(ValueError):
            run_loop(self.path)
        self.assertEqual(self.path.read_text(encoding="utf-8"), "{")

    def test_atomic_save_and_initial_state_round_trip(self):
        state = State()
        save_state(self.path, state)
        self.assertEqual(load_state(self.path), state)

    def test_failed_replace_preserves_previous_checkpoint_and_cleans_temporary(self):
        previous = run_loop(self.path, max_iterations=1)
        saved = self.path.read_text(encoding="utf-8")
        with patch("improve.os.replace", side_effect=OSError("write denied")):
            with self.assertRaisesRegex(OSError, "write denied"):
                save_state(self.path, State())
        self.assertEqual(self.path.read_text(encoding="utf-8"), saved)
        self.assertEqual(load_state(self.path), previous)
        self.assertEqual(
            sorted(item.name for item in self.path.parent.iterdir()), ["state.json"]
        )

    def test_cli_runs_and_reports_real_scores(self):
        completed = subprocess.run(
            [sys.executable, "improve.py", "--state", str(self.path)],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertIn("Development: 4/8 -> 8/8", completed.stdout)
        self.assertIn("Audit (not used for selection): 2/6 -> 6/6", completed.stdout)
        self.assertIn("Stop: target_reached", completed.stdout)
        self.assertEqual(load_state(self.path).policy, Policy(True, True, True))

    def test_cli_invalid_budget_is_an_explicit_failure(self):
        completed = subprocess.run(
            [
                sys.executable, "improve.py", "--state", str(self.path),
                "--max-iterations", "0",
            ],
            cwd=Path(__file__).resolve().parents[1],
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 2)
        self.assertIn("positive integer", completed.stderr)
        self.assertFalse(self.path.exists())


if __name__ == "__main__":
    unittest.main()
