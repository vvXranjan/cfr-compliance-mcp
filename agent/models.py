from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field


@dataclass
class Clause:
    """
    A single contract clause identified by its section heading.
    """

    title: str
    text: str


class ComplianceResult(BaseModel):
    """
    Structured output returned by the Agno compliance agent.
    """

    clause_title: str

    status: Literal[
        "Compliant",
        "Non-Compliant",
        "Needs Review",
    ]

    confidence: float = Field(
        ge=0.0,
        le=1.0,
        description="Confidence score between 0 and 1",
    )

    reason: str