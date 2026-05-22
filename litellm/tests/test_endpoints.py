# /// script
# dependencies = [
#   "requests<3",
#   "rich",
#   "python-dotenv",
# ]
# ///

import os
import requests
from rich.console import Console
from rich.pretty import pprint
from dotenv import load_dotenv, find_dotenv

# Load environment variables
assert load_dotenv(find_dotenv()), 'missing env file'
api_key = os.getenv("LITELLM_API_KEY", "sk")
base_url = "http://localhost:4000/v1"

console = Console()

def test_model(model_name, prompt="Say 'LiteLLM Test Successful' and nothing else."):
    console.print(f"\n[bold blue]Testing model:[/bold blue] {model_name}")
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a warm, observant parent describing precious moments."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 100
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        console.print(f"[green]Success![/green]")
        pprint(result["choices"][0]["message"]["content"])
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        if hasattr(e, 'response') and e.response is not None:
            console.print(e.response.text)

if __name__ == "__main__":
    test_prompt = "Describe a baby girl laughing in her sleep."
    test_model("qwen-27b", test_prompt)
    test_model("qwen-27b-storyteller", test_prompt)
