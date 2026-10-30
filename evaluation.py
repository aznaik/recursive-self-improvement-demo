"""Fixed, human-authored expectations; improvement never edits this module."""

from dataclasses import asdict, dataclass
import hashlib
import json

from agent import Policy, route


EVALUATOR_VERSION = 1


@dataclass(frozen=True)
class Case:
    id: str
    ticket: str
    expected: str


DEVELOPMENT = (
    Case("dev-billing", "Please refund this charge", "billing"),
    Case("dev-account", "I forgot my password", "account"),
    Case("dev-security", "My profile was hacked", "security"),
    Case("dev-other", "Hello team", "other"),
    Case("dev-case", "I need an INVOICE", "billing"),
    Case("dev-boundary", "This charger is broken", "other"),
    Case(
        "dev-priority",
        "My account was hacked; please refund the invoice",
        "security",
    ),
    Case("dev-combined", "HACKED account; INVOICE appeared", "security"),
)

REGRESSION = (
    Case("guard-billing", "charge refund", "billing"),
    Case("guard-account", "reset login password", "account"),
    Case("guard-security", "hacked phishing stolen", "security"),
    Case("guard-other", "feature request", "other"),
)

# Not supplied to reflection or acceptance: an audit, not a selection signal.
AUDIT = (
    Case("audit-case-billing", "Can I get a REFUND?", "billing"),
    Case("audit-case-account", "PASSWORD recovery help", "account"),
    Case("audit-priority", "I was HACKED; a CHARGE appeared", "security"),
    Case("audit-boundary", "My charger cable broke", "other"),
    Case("audit-billing", "I need an invoice for the renewal", "billing"),
    Case("audit-other", "Thanks for your help", "other"),
)


@dataclass(frozen=True)
class Observation:
    id: str
    ticket: str
    expected: str
    actual: str

    @property
    def correct(self) -> bool:
        return self.expected == self.actual


@dataclass(frozen=True)
class Result:
    observations: tuple[Observation, ...]

    @property
    def correct(self) -> int:
        return sum(item.correct for item in self.observations)

    @property
    def accuracy(self) -> float:
        return self.correct / len(self.observations)

    @property
    def failures(self) -> tuple[Observation, ...]:
        return tuple(item for item in self.observations if not item.correct)

    def to_dict(self) -> dict:
        return {
            "correct": self.correct,
            "total": len(self.observations),
            "accuracy": self.accuracy,
            "observations": [asdict(item) for item in self.observations],
        }


@dataclass(frozen=True)
class Evaluation:
    development: Result
    regression: Result

    def to_dict(self) -> dict:
        return {
            "development": self.development.to_dict(),
            "regression": self.regression.to_dict(),
        }


def score(policy: Policy, cases: tuple[Case, ...]) -> Result:
    if not cases:
        raise ValueError("Evaluation requires at least one case")
    if len({case.id for case in cases}) != len(cases):
        raise ValueError("Evaluation case IDs must be unique")
    return Result(
        tuple(
            Observation(case.id, case.ticket, case.expected, route(case.ticket, policy))
            for case in cases
        )
    )


def evaluate(policy: Policy) -> Evaluation:
    return Evaluation(score(policy, DEVELOPMENT), score(policy, REGRESSION))


def suite_fingerprint() -> str:
    payload = {
        "version": EVALUATOR_VERSION,
        "development": [asdict(case) for case in DEVELOPMENT],
        "regression": [asdict(case) for case in REGRESSION],
        "audit": [asdict(case) for case in AUDIT],
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")
    ).hexdigest()
