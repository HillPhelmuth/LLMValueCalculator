from __future__ import annotations

import hashlib
import json
import random
from dataclasses import asdict, dataclass, replace
from typing import Any, Protocol

from calibration.models import CanonicalCase


class PerturbationError(ValueError):
    """Raised when a treatment violates its declared invariants."""


@dataclass(frozen=True, slots=True)
class TreatmentSpec:
    treatment_id: str
    version: str
    seed: int
    parameters: dict[str, Any]
    invariants: tuple[str, ...] = ("expected_answer", "case_parent")


@dataclass(frozen=True, slots=True)
class PerturbationVariant:
    parent_case_id: str
    variant_case_id: str
    treatment_id: str
    treatment_version: str
    treatment_seed: int
    treatment_metadata: dict[str, Any]
    case: CanonicalCase
    output_hash: str
    invariant_results: dict[str, bool]

    def to_json(self) -> dict[str, Any]:
        return {
            "parent_case_id": self.parent_case_id,
            "variant_case_id": self.variant_case_id,
            "treatment_id": self.treatment_id,
            "treatment_version": self.treatment_version,
            "treatment_seed": self.treatment_seed,
            "treatment_metadata": self.treatment_metadata,
            "case": asdict(self.case),
            "output_hash": self.output_hash,
            "invariant_results": self.invariant_results,
        }


class PerturbationTransform(Protocol):
    name: str

    def apply(
        self, case: CanonicalCase, treatment: TreatmentSpec, rng: random.Random
    ) -> CanonicalCase: ...


class WhitespaceTransform:
    name = "whitespace"

    def apply(
        self, case: CanonicalCase, treatment: TreatmentSpec, rng: random.Random
    ) -> CanonicalCase:
        del rng
        prompt = str(case.input.get("prompt", ""))
        mode = str(treatment.parameters.get("mode", "collapse"))
        if mode == "collapse":
            transformed = " ".join(prompt.split())
        elif mode == "linebreak":
            transformed = prompt.replace(" ", "\n")
        else:
            raise PerturbationError(f"Unknown whitespace mode: {mode}")
        return replace(case, input={**case.input, "prompt": transformed})


class ChoiceOrderTransform:
    name = "choice_order"

    def apply(
        self, case: CanonicalCase, treatment: TreatmentSpec, rng: random.Random
    ) -> CanonicalCase:
        choices = case.input.get("choices")
        if not isinstance(choices, list) or len(choices) < 2:
            raise PerturbationError("choice_order requires at least two choices")
        shuffled = list(choices)
        rng.shuffle(shuffled)
        return replace(case, input={**case.input, "choices": shuffled})


class PerturbationRegistry:
    def __init__(self, transforms: dict[str, PerturbationTransform] | None = None) -> None:
        self._transforms = transforms or {
            WhitespaceTransform.name: WhitespaceTransform(),
            ChoiceOrderTransform.name: ChoiceOrderTransform(),
        }

    def register(self, name: str, transform: PerturbationTransform) -> None:
        if name in self._transforms:
            raise PerturbationError(f"Duplicate treatment transform: {name}")
        self._transforms[name] = transform

    def generate(
        self,
        case: CanonicalCase,
        treatments: tuple[TreatmentSpec, ...],
    ) -> tuple[PerturbationVariant, ...]:
        variants: list[PerturbationVariant] = []
        for treatment in treatments:
            try:
                transform = self._transforms[treatment.treatment_id]
            except KeyError as error:
                raise PerturbationError(
                    f"Unknown treatment transform: {treatment.treatment_id}"
                ) from error
            seed_material = f"{case.case_id}:{treatment.treatment_id}:{treatment.seed}"
            derived_seed = int(hashlib.sha256(seed_material.encode()).hexdigest()[:16], 16)
            transformed = transform.apply(case, treatment, random.Random(derived_seed))
            variant_id = f"{case.case_id}--{treatment.treatment_id}"
            metadata = {
                **transformed.metadata,
                "parent_case_id": case.case_id,
                "treatment_id": treatment.treatment_id,
                "treatment_version": treatment.version,
                "treatment_seed": treatment.seed,
                "label_available": case.label_available,
            }
            transformed = replace(transformed, case_id=variant_id, metadata=metadata)
            invariant_results = _check_invariants(case, transformed, treatment)
            if not all(invariant_results.values()):
                raise PerturbationError(
                    f"Treatment {treatment.treatment_id} changed a declared invariant"
                )
            output_hash = hashlib.sha256(
                json.dumps(asdict(transformed), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
                    "utf-8"
                )
            ).hexdigest()
            variants.append(
                PerturbationVariant(
                    parent_case_id=case.case_id,
                    variant_case_id=variant_id,
                    treatment_id=treatment.treatment_id,
                    treatment_version=treatment.version,
                    treatment_seed=treatment.seed,
                    treatment_metadata=dict(treatment.parameters),
                    case=transformed,
                    output_hash=output_hash,
                    invariant_results=invariant_results,
                )
            )
        return tuple(variants)


def paired_work_groups(
    cases: tuple[CanonicalCase, ...],
    variants: tuple[PerturbationVariant, ...],
) -> tuple[tuple[str, tuple[CanonicalCase, ...]], ...]:
    """Group baseline and variants so schedulers retain paired observations."""
    groups: dict[str, list[CanonicalCase]] = {case.case_id: [case] for case in cases}
    for variant in variants:
        groups.setdefault(variant.parent_case_id, []).append(variant.case)
    return tuple(
        (parent_id, tuple(groups[parent_id])) for parent_id in sorted(groups)
    )


def _check_invariants(
    parent: CanonicalCase, variant: CanonicalCase, treatment: TreatmentSpec
) -> dict[str, bool]:
    checks: dict[str, bool] = {}
    if "expected_answer" in treatment.invariants:
        checks["expected_answer"] = parent.expected == variant.expected
    if "case_parent" in treatment.invariants:
        checks["case_parent"] = variant.metadata.get("parent_case_id") == parent.case_id
    if "label_access" in treatment.invariants:
        checks["label_access"] = parent.label_available == variant.label_available
    return checks
