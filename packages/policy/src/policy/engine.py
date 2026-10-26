from dataclasses import dataclass

from contracts import Stage, StageProof

from policy.rules import REQUIRED_CHECKS


@dataclass(frozen=True, slots=True)
class PolicyViolationError(ValueError):
    stage: Stage
    missing: frozenset[str]

    def __str__(self) -> str:
        checks = ", ".join(sorted(self.missing))
        return f"stage {self.stage.value} is missing required checks: {checks}"


class GatePolicy:
    def __init__(self, required: dict[Stage, frozenset[str]] | None = None) -> None:
        self._required = required or REQUIRED_CHECKS
        undefined = set(Stage).difference(self._required)
        if undefined:
            names = ", ".join(sorted(stage.value for stage in undefined))
            raise ValueError(f"controls do not define every stage: {names}")

    def validate(self, proof: StageProof) -> None:
        missing = self._required[proof.stage].difference(proof.checks)
        if missing:
            raise PolicyViolationError(stage=proof.stage, missing=frozenset(missing))

    def checks(self, stage: Stage) -> frozenset[str]:
        return self._required[stage]
