# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "requests",
#     "python-dotenv",
# ]
# ///

import os
import time
import requests
import json
import base64
from dotenv import load_dotenv

# Load from the Frigate .env file
# Based on my research, the relative path from /mnt/data/docker/llama-cpp/tests/ is ../../frigate/.env
dotenv_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../frigate/.env'))
load_dotenv(dotenv_path)

FRIGATE_URL = "http://localhost:5000"
GENAI_BASE_URL = os.getenv("FRIGATE_GENAI_BASE_URL", "http://localhost:9876")
GENAI_API_KEY = os.getenv("FRIGATE_GENAI_API_KEY", "asddsa")
MODEL_NAME = "Gemma-4"

# Example system prompt based on the config.yml I saw
SYSTEM_PROMPT = """Analyze the person in these images from the front_doorbell security camera. Focus on the actions, behavior, and potential intent of the person, rather than just describing its appearance.

### Assessment Guidance
Evaluate in this order:
1. **If person is verified/known** → Level 0 regardless of time or activity
2. **If person is unidentified:**
  - Check time: If late night/early morning (11 PM - 5 AM) AND in private areas (driveways, near vehicles/buildings) → Level 1
  - Check actions: If testing doors/handles, taking items, climbing → Level 1
  - Otherwise, if daytime/evening (6 AM - 10 PM) with clear legitimate purpose (delivery, service worker) → Level 0
3. **Escalate to Level 2 if:** Weapons, break-in tools, forced entry in progress, violence, or active property damage visible (escalates from Level 0 or 1)
"""

USER_PROMPT = "Examine the main person in these images. What are they doing and what might their actions suggest about their intent (e.g., approaching a door, leaving an area, standing still)? Do not describe the surroundings or static details."

def get_latest_event_id():
    """Fetch the most recent event ID from Frigate."""
    try:
        response = requests.get(f"{FRIGATE_URL}/api/events?limit=1")
        response.raise_for_status()
        events = response.json()
        if events:
            return events[0]['id']
    except Exception as e:
        print(f"Error fetching event: {e}")
    return None

def get_snapshot_base64(event_id):
    """Fetch snapshot for a given event and encode as base64."""
    try:
        # Use the snapshot endpoint from Frigate
        response = requests.get(f"{FRIGATE_URL}/api/events/{event_id}/snapshot.jpg")
        response.raise_for_status()
        return base64.b64encode(response.content).decode('utf-8')
    except Exception as e:
        print(f"Error fetching snapshot: {e}")
    return None

def run_test(event_id, snapshot_b64):
    """Simulate a GenAI request from Frigate to llama-server."""
    url = f"{GENAI_BASE_URL}/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {GENAI_API_KEY}"
    }
    
    payload = {
        "model": MODEL_NAME,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": USER_PROMPT
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{snapshot_b64}"
                        }
                    }
                ]
            }
        ],
        "temperature": 0.1,
        "max_tokens": 512
    }
    
    print(f"\n--- Starting Test for Event: {event_id} ---")
    start_time = time.time()
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=120)
        response.raise_for_status()
        duration = time.time() - start_time
        print(f"Request completed in {duration:.2f} seconds.")
        # print(json.dumps(response.json(), indent=2))
        return response.json()
    except Exception as e:
        print(f"Error during request: {e}")
    return None

if __name__ == "__main__":
    event_id = get_latest_event_id()
    if not event_id:
        print("No recent events found in Frigate. Using a dummy snapshot for testing.")
        # If no events, we could either fail or try the latest doorbell snapshot
        event_id = "manual_test"
        response = requests.get(f"{FRIGATE_URL}/api/front_doorbell/latest.webp")
        snapshot_b64 = base64.b64encode(response.content).decode('utf-8')
    else:
        snapshot_b64 = get_snapshot_base64(event_id)
        
    if snapshot_b64:
        # Run twice to test prompt caching
        print("First pass (Expected to process full prompt)...")
        run_test(event_id, snapshot_b64)
        
        print("\nSecond pass (Expected to use cache for system/user prompt prefix)...")
        run_test(event_id, snapshot_b64)
    else:
        print("Failed to obtain a snapshot for testing.")
