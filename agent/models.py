# from pydantic import BaseModel


# class Clause(BaseModel):
#     title: str
#     text: str
from dataclasses import dataclass


@dataclass
class Clause:
    """A single contract clause identified by its section heading."""

    title: str
    text: str