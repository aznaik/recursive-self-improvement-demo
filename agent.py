"""A deliberately limited, deterministic agent with an editable strategy."""

from dataclasses import asdict, dataclass, fields
import re


RULES = (
    ("billing", ("charge", "invoice", "refund")),
    ("account", ("login", "password", "reset")),
    ("security", ("hacked", "phishing", "stolen")),
)


@dataclass(frozen=True)
class Policy:
    normalize_case: bool = False
    whole_words: bool = False
    security_first: bool = False

    def __post_init__(self) -> None:
        if any(type(value) is not bool for value in asdict(self).values()):
            raise ValueError("Policy settings must be booleans")

    def to_dict(self) -> dict[str, bool]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: object) -> "Policy":
        names = {field.name for field in fields(cls)}
        if not isinstance(data, dict) or set(data) != names:
            raise ValueError("Policy must contain exactly the supported settings")
        return cls(**data)


def route(ticket: str, policy: Policy) -> str:
    text = ticket.casefold() if policy.normalize_case else ticket
    rules = RULES
    if policy.security_first:
        rules = (RULES[2], RULES[0], RULES[1])
    for label, keywords in rules:
        for keyword in keywords:
            matched = (
                re.search(r"\b" + re.escape(keyword) + r"\b", text) is not None
                if policy.whole_words
                else keyword in text
            )
            if matched:
                return label
    return "other"
