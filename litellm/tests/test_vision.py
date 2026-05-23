# /// script
# dependencies = [
#   "requests<3",
#   "rich",
#   "python-dotenv",
# ]
# ///

import os
import base64
import requests
from rich.console import Console
from rich.pretty import pprint
from dotenv import load_dotenv, find_dotenv

assert load_dotenv(find_dotenv()), 'missing env file'
api_key = os.getenv("LITELLM_API_KEY", "sk")
base_url = "http://localhost:4000/v1"

console = Console()

# The prompt from your config.yml for tapo_c260
prompt_text = """This description is for a Frigate camera pipeline monitoring the Master Bedroom. 
Identify the people as follows: man is Dad, baby is Dani, woman is Mom. 
Describe the activity of Dani (or person) naturally, starting the description directly without any "Based on the image" preambles.
For trivial sleep movements or stirring, provide only a single concise sentence.
For significant activity, unusual events (e.g. falling), or ANY instance of someone speaking while the camera is in black and white (nightvision), provide a thorough and detailed description.
Focus on actions and behavior. Be proportional: simple movements get simple summaries; emergencies or nightvision speech get full detail."""

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def test_vision_model(model_name, image_path):
    console.print(f"\n[bold blue]Testing model:[/bold blue] {model_name}")
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
        "Authorization": f"Bearer {api_key}",
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
    image_file = "/mnt/data/docker/litellm/tests/assets/laughter_snapshot.jpg"

    console.print("[bold yellow]Original Frigate Description (Event 1779164646.659747-5wng6m):[/bold yellow]")
    console.print("A woman with gray hair lies on her back in bed, propped up slightly by pillows, wearing a colorful floral pajama top. She is holding a smartphone in both hands and appears to be actively using it — possibly scrolling or typing — while partially covered by a light-colored quilted blanket. Her arms are raised, revealing tattoos on her forearms. In the background, another person is sleeping on the same bed, face turned away, under blue bedding. The scene is calm and domestic; no signs of distress, urgency, or unusual activity. Lighting suggests daytime (camera timestamp shows 09:24 PM, but image is in color, not nightvision). No speech or emergency detected. Simple summary: A woman lies in bed using her phone while someone else sleeps beside her.")
    
    test_vision_model("qwen-27b", image_file)
    test_vision_model("qwen-27b-frigate", image_file)
