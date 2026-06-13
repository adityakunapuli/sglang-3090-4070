# /// script
# dependencies = [
#   "requests<3",
#   "rich",
# ]
# ///

import os
import requests
from rich.console import Console
from rich.pretty import pprint

# Direct llama-cpp endpoint
base_url = "http://localhost:8082/v1"
model_name = "Gemma-4-12B-MTP"

console = Console()

def test_model(prompt="Say 'Gemma 4 Native Test Successful' and nothing else."):
    console.print(f"\n[bold blue]Testing llama-cpp directly:[/bold blue] {model_name}")
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a warm, observant parent describing precious moments."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 100
    }
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        console.print(f"[green]Success![/green]")
        pprint(result)
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        if hasattr(e, 'response') and e.response is not None:
            console.print(e.response.text)

if __name__ == "__main__":
    test_prompt = "Describe a baby girl laughing in her sleep."
    test_model(test_prompt)
