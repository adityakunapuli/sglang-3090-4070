# /// script
# dependencies = [
#   "requests<3",
#   "rich",
# ]
# ///

import os
import base64
import requests
from rich.console import Console
from rich.pretty import pprint

# Direct llama-cpp endpoint
base_url = "http://localhost:8082/v1"
model_name = "Gemma-4-12B-MTP"
# Reference asset from litellm stack
image_path = "/mnt/data/docker/litellm/tests/assets/laughter_snapshot.jpg"

console = Console()

prompt_text = """This description is for a Frigate camera pipeline monitoring the Master Bedroom. 
Identify the people as follows: man is Dad, baby is Dani, woman is Mom. 
Describe the activity of Dani (or person) naturally, starting the description directly without any "Based on the image" preambles.
For trivial sleep movements or stirring, provide only a single concise sentence.
For significant activity, unusual events (e.g. falling), or ANY instance of someone speaking while the camera is in black and white (nightvision), provide a thorough and detailed description.
Focus on actions and behavior. Be proportional: simple movements get simple summaries; emergencies or nightvision speech get full detail."""

def encode_image(path):
    with open(path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def test_vision_model():
    console.print(f"\n[bold blue]Testing llama-cpp vision directly:[/bold blue] {model_name}")
    
    if not os.path.exists(image_path):
        console.print(f"[red]Error:[/red] Image not found at {image_path}")
        return

    base64_image = encode_image(image_path)
    
    payload = {
        "model": model_name,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt_text},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{base64_image}"
                        }
                    }
                ]
            }
        ],
        "max_tokens": 150
    }
    headers = {
        "Content-Type": "application/json"
    }
    
    try:
        response = requests.post(f"{base_url}/chat/completions", json=payload, headers=headers)
        response.raise_for_status()
        result = response.json()
        console.print(f"[green]Success![/green]")
        console.print(f"[italic]{result['choices'][0]['message']['content']}[/italic]")
    except Exception as e:
        console.print(f"[red]Failed:[/red] {e}")
        if hasattr(e, 'response') and e.response is not None:
            console.print(e.response.text)

if __name__ == "__main__":
    test_vision_model()
