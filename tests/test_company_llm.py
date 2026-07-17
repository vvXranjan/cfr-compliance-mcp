import requests

LLM_API_KEY = "Bearer atm_JIxbkUNzYqsRpRXlnSAnHUODVaIflcoQFa"
LLM_MODEL_URL = "https://atm.accure.ai"
LLM_MODEL_NAME = "nvidia/nemotron-3-super"

url = LLM_MODEL_URL + "/v1/chat/completions"

headers = {
    "Authorization": LLM_API_KEY,
    "Content-Type": "application/json",
}

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

response = requests.post(url, headers=headers, json=payload)

print(response.status_code)
print(response.text)
# uv run python tests/test_company_llm.py