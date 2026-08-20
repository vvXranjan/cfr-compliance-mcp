import os

import requests

LLM_MODEL_NAME = "nvidia/nemotron-3-nano-omni"
LLM_MODEL_URL = "https://atm.accure.ai"

LLM_API_KEY = os.getenv("ATM_API_KEY")
if LLM_API_KEY:
    LLM_API_KEY = f"Bearer {LLM_API_KEY}"
else:
    LLM_API_KEY = None

url = LLM_MODEL_URL + "/v1/chat/completions"

headers = {}
if LLM_API_KEY:
    headers["Authorization"] = LLM_API_KEY
else:
    headers["Content-Type"] = "application/json"

payload = {
    "model": LLM_MODEL_NAME,
    "messages": [
        {
            "role": "system",
            "content": "You are a helpful assistant."
        },
        {
            "role": "user",
            "content": "Say hello."
        }
    ],
    "max_tokens": 100,
    "temperature": 0
}

if LLM_API_KEY:
    response = requests.post(url, headers=headers, json=payload)
    print(response.status_code)
    print(response.text)
else:
    print("Skipping live LLM call: ATM_API_KEY not set")
    print("Set ATM_API_KEY environment variable to enable live calls.")

# uv run python tests/test_company_llm.py