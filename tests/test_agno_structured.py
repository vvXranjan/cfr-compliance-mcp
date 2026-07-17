from agno.agent import Agent
from agno.models.openai import OpenAIChat
from pydantic import BaseModel


LLM_API_KEY = "Bearer atm_JIxbkUNzYqsRpRXlnSAnHUODVaIflcoQFa"
LLM_MODEL_URL = "https://atm.accure.ai"
LLM_MODEL_NAME = "nvidia/nemotron-3-super"


class ComplianceResult(BaseModel):
    status: str
    confidence: float
    reason: str


model = OpenAIChat(
    id=LLM_MODEL_NAME,
    api_key=LLM_API_KEY.replace("Bearer ", ""),
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