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

load_dotenv(find_dotenv())
api_key = os.getenv("LITELLM_API_KEY", "sk")

console = Console()

def test_model(url, model_name, prompt="Say 'LiteLLM Test Successful' and nothing else."):
    console.print(f"\n[bold blue]Testing model {model_name} on {url}:[/bold blue]")
    payload = {
        "model": model_name,
        "messages": [
            {"role": "system", "content": "You are a warm, observant parent describing precious moments."},
            {"role": "user", "content": prompt}
        ],
        "max_tokens": 150
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(f"{url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        console.print(f"[green]Success![/green]")
        
        # Print content and reasoning if present
        choice = result["choices"][0]["message"]
        content = choice.get("content", "")
        reasoning = choice.get("reasoning_content", "")
        
        console.print(f"[bold yellow]Reasoning Content:[/bold yellow]\n{reasoning or '<None>'}")
        console.print(f"[bold green]Response Content:[/bold green]\n{content or '<None>'}")
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        if hasattr(e, 'response') and e.response is not None:
            console.print(e.response.text)

if __name__ == "__main__":
    test_prompt = "Describe a baby girl laughing in her sleep."
    
    # 1. Directly against llama-swap (port 8082) using the active model name (reasoning ON)
    test_model("http://localhost:8082/v1", "Gemma-4-12B-MTP", test_prompt)
    
    # 2. Against Gateway (port 4001) using a non-mapped model (passes through, reasoning ON)
    test_model("http://localhost:4001/v1", "Gemma-4-12B-MTP", test_prompt)
    
    # 3. Against Gateway (port 4001) using the mapped frigate proxy model (reasoning OFF)
    test_model("http://localhost:4001/v1", "frigate", test_prompt)
