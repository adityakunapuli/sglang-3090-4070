#!/usr/bin/env bash

# ==========================================
# LOAD SECRETS
# ==========================================
ENV_FILE=.env

if [ -f "$ENV_FILE" ]; then
    set -a
    source "$ENV_FILE"
    set +a
    echo "✅ Credentials loaded from $ENV_FILE"
else
    echo "❌ Error: .env file not found at $ENV_FILE"
    exit 1
fi

# Tapo standard RTSP paths (using the local camera account credentials)
STREAMS=(
  "rtsp://${FRIGATE_TAPO_USER}:${FRIGATE_TAPO_PASSWORD}@${FRIGATE_TAPO_IP}:554/stream1"
  "rtsp://${FRIGATE_TAPO_USER}:${FRIGATE_TAPO_PASSWORD}@${FRIGATE_TAPO_IP}:554/stream2"
)

echo "--------------------------------------------------------"
echo "Detecting available Tapo RTSP streams on $FRIGATE_TAPO_IP"
echo "--------------------------------------------------------"

for URL in "${STREAMS[@]}"; do
    DISPLAY_URL=$(echo "$URL" | sed -E "s/:[^@\/]+@/:****@/g")
    echo "Checking: $DISPLAY_URL"

    STREAM_NAME="test_rtsp_tapo"

    # Add stream to go2rtc
    docker exec frigate curl -s -X PUT "http://127.0.0.1:1984/api/streams?name=${STREAM_NAME}&src=$URL" > /dev/null
    
    # Fetch a frame to trigger the connection
    docker exec frigate curl -s -o /dev/null "http://127.0.0.1:1984/api/frame.jpeg?src=${STREAM_NAME}"
    
    # Wait a moment for logs to process
    sleep 1

    # Check the logs from the last 3 seconds
    LOGS=$(docker compose logs --since 3s 2>/dev/null | grep -i "401 Unauthorized\|404 Not Found\|timeout\|failed")

    if [ ! -z "$LOGS" ]; then
        echo "❌  FAILED"
    else
        echo "✅  AVAILABLE / NO ERROR LOGGED"
    fi

    # Remove stream
    docker exec frigate curl -s -X DELETE "http://127.0.0.1:1984/api/streams?name=${STREAM_NAME}" > /dev/null
    
    # Sleep to ensure logs separate for next attempt
    sleep 3

    echo "--------------------------------------------"
done
