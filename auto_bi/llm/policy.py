"""Reasoning policy (task 1.12): thinking only where the LLM actually designs.

GROUNDING and PROPOSE/PATCH decide what the dashboard is — they get reasoning.
Narrating precomputed advisor findings is formulation over decided facts, and
schema-repair rounds inherit the flag of the step they repair — mechanical steps
default to False (latency/cost, ARCHITECTURE risk table).
"""

REASONING_BY_STEP: dict[str, bool] = {
    "grounding": True,
    "propose_spec": True,
    "patch_spec": True,
    "narrate_advisor": False,
}


def reasoning_for(step: str) -> bool:
    """Mechanical by default: unknown steps run without thinking."""
    return REASONING_BY_STEP.get(step, False)
