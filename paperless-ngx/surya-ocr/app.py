import asyncio
import base64
import io
import os
import re
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("surya-ocr")

INFERENCE_URL = os.environ.get("SURYA_INFERENCE_URL", "http://surya-llama:5804/v1")
predictor = None
layout_predictor = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global predictor, layout_predictor
    from surya.inference import SuryaInferenceManager
    from surya.recognition import RecognitionPredictor
    from surya.layout import LayoutPredictor

    os.environ["SURYA_INFERENCE_URL"] = INFERENCE_URL
    os.environ["TORCH_DEVICE"] = "cpu"

    await asyncio.sleep(5)
    for attempt in range(30):
        try:
            import httpx
            async with httpx.AsyncClient() as client:
                r = await client.get(f"{INFERENCE_URL.replace('/v1', '')}/health", timeout=5)
                if r.status_code == 200:
                    logger.info(f"Inference backend ready at {INFERENCE_URL}")
                    break
        except Exception as e:
            logger.info(f"Waiting for inference backend ({attempt+1}/30): {e}")
        await asyncio.sleep(2)
    else:
        logger.error("Inference backend not ready after 60s")
        raise RuntimeError("Inference backend failed to start")

    loop = asyncio.get_event_loop()
    manager = SuryaInferenceManager()
    predictor = await loop.run_in_executor(None, lambda: RecognitionPredictor(manager))
    layout_predictor = await loop.run_in_executor(None, lambda: LayoutPredictor(manager))
    logger.info("Surya-OCR predictors initialized")

    yield


app = FastAPI(lifespan=lifespan)


def extract_text_from_predictions(predictions) -> str:
    texts = []
    for page_pred in predictions:
        if not isinstance(page_pred, dict):
            page_pred = page_pred.model_dump() if hasattr(page_pred, 'model_dump') else {}
        for block in page_pred.get("blocks", []):
            if not isinstance(block, dict):
                block = block.model_dump() if hasattr(block, 'model_dump') else {}
            html = block.get("html", "")
            if html:
                plain = re.sub(r'<[^>]+>', '', html).strip()
                if plain:
                    texts.append(plain)
    return "\n".join(texts)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    global predictor, layout_predictor
    if predictor is None:
        return JSONResponse({"error": "OCR predictor not initialized"}, status_code=503)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON"}, status_code=400)

    image_data = None
    for msg in body.get("messages", []):
        content = msg.get("content", [])
        if isinstance(content, list):
            for item in content:
                if isinstance(item, dict) and item.get("type") == "image_url":
                    url = item["image_url"]["url"]
                    if url.startswith("data:image"):
                        _, b64 = url.split(",", 1)
                        image_data = base64.b64decode(b64)
                    break

    if not image_data:
        return JSONResponse({"error": "No image found"}, status_code=400)

    img = Image.open(io.BytesIO(image_data)).convert("RGB")
    logger.info(f"Processing image: {img.size}")

    loop = asyncio.get_event_loop()

    layouts = await loop.run_in_executor(None, layout_predictor, [img])
    logger.info(f"Layout done: {len(layouts[0].bboxes) if layouts else 0} blocks")

    predictions = await loop.run_in_executor(None, predictor, [img], layouts)
    text = extract_text_from_predictions(predictions)
    logger.info(f"OCR result: {text[:300]}")

    return {
        "id": "surya-ocr",
        "object": "chat.completion",
        "created": 0,
        "model": body.get("model", "surya-ocr-2"),
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": text or ""
                },
                "finish_reason": "stop"
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    }


@app.get("/health")
async def health():
    return {"status": "ok", "predictor_ready": predictor is not None}