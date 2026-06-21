# Gateway Service Documentation & Agent Instructions

This service intercepts and pre-processes OpenAI-compatible API requests before routing them to the active LLM backend (`llama-swap`/`llama-cpp`). It handles dynamic routing to active models and injects custom overrides (like disabling reasoning for vision or smart home integrations).

## Architecture

```
[Client] -> [Gateway Service (Port 4001)] -> [Llama-Swap / Llama-CPP (Port 8082)]
```

- **Uvicorn / FastAPI Application:** Configured via `app.py`.
- **Dynamic Model Matching:** Overrides request fields (like temperature, max_tokens, chat_template_kwargs) per model alias configured in `config.yaml`.
- **Auto Model Resolution:** If a model has `model: "auto"` configured in `config.yaml`, the gateway queries `/running` or `/v1/models` on the backend to dynamically route requests to the active model without triggering a swap.

## Key Behavior

### Payload Hoisting for `llama-server` Compatibility
`llama-server` expects OpenAI extra-body keys (such as `chat_template_kwargs` to control thinking/reasoning blocks) to be located at the root of the JSON request payload. 

To ensure seamless integration, the gateway automatically hoists any keys provided inside the `extra_body` dictionary to the root of the request payload and removes the `extra_body` wrapper before forwarding the call upstream.

## Configuration (`config.yaml`)

Define model overrides and behavior. For example, to map the generic `frigate` alias to the active model and disable reasoning (so that the model outputs immediate description text instead of deep-thinking steps):

```yaml
models:
  frigate:
    model: "auto"
    temperature: 1.1
    top_p: 0.95
    max_tokens: 512
    max_model_len: 8192
    context_length: 8192
    extra_body:
      chat_template_kwargs: {"enable_thinking": false}
```

## Running Verification Tests

You can execute testing scripts using `uv` (which resolves inline script metadata dependencies automatically):

```bash
# Verify chat completions & reasoning overrides
uv run test_endpoints.py

# Verify vision model completions & reasoning overrides
uv run test_vision.py
```
