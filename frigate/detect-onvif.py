import os

from onvif import ONVIFCamera
from dotenv import load_dotenv

load_dotenv('.env')

cameras = [
    (
        os.getenv('FRIGATE_DOORBELL_IP'),
        os.getenv('FRIGATE_DOORBELL_USERNAME'),
        os.getenv('FRIGATE_DOORBELL_PASSWORD'),
     ),
    (
        os.getenv('FRIGATE_TAPO_IP'),
        os.getenv('FRIGATE_TAPO_USERNAME'),
        os.getenv('FRIGATE_TAPO_PASSWORD'),
    )
]

for x in cameras:
    cam = ONVIFCamera(x[0], x[1], )
    media = cam.create_media_service()
    profiles = media.GetProfiles()

    for p in profiles:
        # This identifies the specific RTSP path for each profile
        obj = media.GetStreamUri(
            {'StreamSetup': {'Stream': 'RTP-Unicast', 'Transport': {'Protocol': 'RTSP'}}, 'ProfileToken': p.token})
        print(f"Profile: {p.Name} | URL: {obj.Uri}")

# # Use the info from your log
# IP = '192.168.254.202'
# PORT = 2020
# USER = 'your_username'
# PASS = 'your_password'