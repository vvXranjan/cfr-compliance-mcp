import os

from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel

LLM_MODEL_NAME = "nvidia/nemotron-3-nano-omni"
LLM_MODEL_URL = "https://atm.accure.ai"

LLM_API_KEY = os.getenv("ATM_API_KEY")
model_api_key = LLM_API_KEY.replace("Bearer ", "") if LLM_API_KEY else None


class ComplianceResult(BaseModel):
    status: str
    confidence: float
    reason: str


model = OpenAIChat(
    id=LLM_MODEL_NAME,
    api_key=model_api_key,
    base_url=LLM_MODEL_URL + "/v1",
)


agent = Agent(
    model=model,
    output_schema=ComplianceResult,
    instructions=[
        """
        You are a compliance evaluator.

        Evaluate this clause:

        "The company must comply with all applicable laws and regulations."

        Return:
        - status
        - confidence
        - reason
        """
    ],
)


result = agent.run(
    "Evaluate this clause."
)

print(result.content)
print(type(result.content))