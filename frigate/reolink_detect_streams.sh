#!/usr/bin/env bash

# ==========================================
# LOAD SECRETS
# ==========================================
ENV_FILE=.env

if [ -f "$ENV_FILE" ]; then
    # set -a exports all variables defined in the file automatically
    set -a
    source "$ENV_FILE"
    set +a
    echo "✅ Credentials loaded from $ENV_FILE"
else
    echo "❌ Error: .env file not found at $ENV_FILE"
    exit 1
fi

# Reolink doorbell paths (RTSP + HTTP-FLV)
STREAMS=(
  "rtsp://${FRIGATE_DOORBELL_USER}:${FRIGATE_DOORBELL_PASSWORD}@${FRIGATE_DOORBELL_IP}:554/h264Preview_01_main"
  "rtsp://${FRIGATE_DOORBELL_USER}:${FRIGATE_DOORBELL_PASSWORD}@${FRIGATE_DOORBELL_IP}:554/h264Preview_01_sub"
  "http://${FRIGATE_DOORBELL_IP}/flv?port=1935&app=bcs&stream=channel0_main.bcs&user=${FRIGATE_DOORBELL_USER}&password=${FRIGATE_DOORBELL_PASSWORD}"
  "http://${FRIGATE_DOORBELL_IP}/flv?port=1935&app=bcs&stream=channel0_ext.bcs&user=${FRIGATE_DOORBELL_USER}&password=${FRIGATE_DOORBELL_PASSWORD}"
)

echo "--------------------------------------------------------"
echo "Detecting available Reolink streams on $FRIGATE_DOORBELL_IP"
echo "--------------------------------------------------------"

for URL in "${STREAMS[@]}"; do
    # Masking credentials for clean output
    DISPLAY_URL=$(echo "$URL" | sed -E "s/:[^@\/]+@/:****@/g")
    echo "Checking: $DISPLAY_URL"

    # ffprobe check (timeout after 5 seconds to prevent hanging)
    ffprobe -v error -timeout 5000000 -select_streams v:0 \
        -show_entries stream=codec_name,width,height,avg_frame_rate \
        -of default=noprint_wrappers=1:nokey=0 "$URL"

    if [ $? -ne 0 ]; then
        echo "❌  NOT AVAILABLE (Check Camera IP/Credentials/HTTP settings)"
    else
        echo "✅  AVAILABLE"
    fi

    echo "--------------------------------------------"
done