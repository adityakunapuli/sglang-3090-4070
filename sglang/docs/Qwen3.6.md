> ## Documentation Index
> Fetch the complete documentation index at: https://docs.sglang.io/llms.txt
> Use this file to discover all available pages before exploring further.

# Qwen3.6

export const Qwen36Deployment = () => {
  const options = {
    hardware: {
      name: 'hardware',
      title: 'Hardware Platform',
      items: [{
        id: 'h100',
        label: 'H100',
        default: true
      }, {
        id: 'h200',
        label: 'H200',
        default: false
      }, {
        id: 'b200',
        label: 'B200',
        default: false
      }]
    },
    modelSize: {
      name: 'modelSize',
      title: 'Model Size',
      items: [{
        id: '35b-a3b',
        label: '35B-A3B (MoE)',
        default: true
      }, {
        id: '27b',
        label: '27B (Dense)',
        default: false
      }]
    },
    quantization: {
      name: 'quantization',
      title: 'Quantization',
      items: [{
        id: 'fp8',
        label: 'FP8',
        default: true
      }, {
        id: 'bf16',
        label: 'BF16',
        default: false
      }]
    },
    reasoning: {
      name: 'reasoning',
      title: 'Reasoning Parser',
      items: [{
        id: 'disabled',
        label: 'Disabled',
        default: false
      }, {
        id: 'enabled',
        label: 'Enabled',
        default: true
      }],
      commandRule: value => value === 'enabled' ? '--reasoning-parser qwen3' : null
    },
    toolcall: {
      name: 'toolcall',
      title: 'Tool Call Parser',
      items: [{
        id: 'disabled',
        label: 'Disabled',
        default: false
      }, {
        id: 'enabled',
        label: 'Enabled',
        default: true
      }],
      commandRule: value => value === 'enabled' ? '--tool-call-parser qwen3_coder' : null
    },
    speculative: {
      name: 'speculative',
      title: 'Speculative Decoding (MTP)',
      items: [{
        id: 'disabled',
        label: 'Disabled',
        default: false
      }, {
        id: 'enabled',
        label: 'Enabled',
        default: true
      }],
      commandRule: value => value === 'enabled' ? '--speculative-algorithm EAGLE \\\n  --speculative-num-steps 3 \\\n  --speculative-eagle-topk 1 \\\n  --speculative-num-draft-tokens 4' : null
    },
    mambaCache: {
      name: 'mambaCache',
      title: 'Mamba Radix Cache',
      getDynamicItems: values => {
        const mtpEnabled = values.speculative === 'enabled';
        if (mtpEnabled) {
          return [{
            id: 'v1',
            label: 'V1',
            default: false,
            disabled: true
          }, {
            id: 'v2',
            label: 'V2',
            default: true
          }];
        }
        return [{
          id: 'v1',
          label: 'V1',
          default: true
        }, {
          id: 'v2',
          label: 'V2',
          default: false
        }];
      },
      commandRule: value => value === 'v2' ? '--mamba-scheduler-strategy extra_buffer' : null
    }
  };
  const modelConfigs = {
    '35b-a3b': {
      baseName: '35B-A3B',
      h100: {
        bf16: {
          tp: 1,
          mem: 0.8
        },
        fp8: {
          tp: 1,
          mem: 0.8
        }
      },
      h200: {
        bf16: {
          tp: 1,
          mem: 0.8
        },
        fp8: {
          tp: 1,
          mem: 0.8
        }
      },
      b200: {
        bf16: {
          tp: 1,
          mem: 0.8
        },
        fp8: {
          tp: 1,
          mem: 0.8
        }
      }
    },
    '27b': {
      baseName: '27B',
      h100: {
        bf16: {
          tp: 1,
          mem: 0.8
        },
        fp8: {
          tp: 1,
          mem: 0.8
        }
      },
      h200: {
        bf16: {
          tp: 1,
          mem: 0.8
        },
        fp8: {
          tp: 1,
          mem: 0.8
        }
      },
      b200: {
        bf16: {
          tp: 1,
          mem: 0.8
        },
        fp8: {
          tp: 1,
          mem: 0.8
        }
      }
    }
  };
  const resolveItems = (option, vals) => typeof option.getDynamicItems === 'function' ? option.getDynamicItems(vals) : option.items;
  const getInitialState = () => {
    const initialState = {};
    for (const [key, option] of Object.entries(options)) {
      const items = resolveItems(option, initialState);
      const def = items.find(item => item.default && !item.disabled) || items.find(item => !item.disabled) || items[0];
      initialState[key] = def.id;
    }
    return initialState;
  };
  const [values, setValues] = useState(getInitialState);
  const [isDark, setIsDark] = useState(false);
  useEffect(() => {
    const checkDarkMode = () => {
      const html = document.documentElement;
      const isDarkMode = html.classList.contains('dark') || html.getAttribute('data-theme') === 'dark' || html.style.colorScheme === 'dark';
      setIsDark(isDarkMode);
    };
    checkDarkMode();
    const observer = new MutationObserver(checkDarkMode);
    observer.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ['class', 'data-theme', 'style']
    });
    return () => observer.disconnect();
  }, []);
  useEffect(() => {
    setValues(prev => {
      const next = {
        ...prev
      };
      for (const [key, option] of Object.entries(options)) {
        if (typeof option.getDynamicItems !== 'function') continue;
        const items = option.getDynamicItems(next);
        const current = items.find(item => item.id === next[key]);
        if (!current || current.disabled) {
          const fallback = items.find(item => item.default && !item.disabled) || items.find(item => !item.disabled);
          if (fallback) next[key] = fallback.id;
        }
      }
      return next;
    });
  }, [values.speculative]);
  const handleRadioChange = (optionName, value) => {
    setValues(prev => ({
      ...prev,
      [optionName]: value
    }));
  };
  const generateCommand = () => {
    const {hardware, modelSize, quantization, speculative} = values;
    const sizeConfig = modelConfigs[modelSize];
    const hwConfig = sizeConfig?.[hardware]?.[quantization];
    if (!hwConfig) {
      return '# Please select a valid hardware and quantization combination';
    }
    const quantSuffix = quantization === 'fp8' ? '-FP8' : '';
    const modelName = `Qwen/Qwen3.6-${sizeConfig.baseName}${quantSuffix}`;
    let cmd = '';
    if (speculative === 'enabled') {
      cmd += 'SGLANG_ENABLE_SPEC_V2=1 ';
    }
    cmd += `sglang serve --model-path ${modelName}`;
    if (hwConfig.tp > 1) {
      cmd += ` \\\n  --tp ${hwConfig.tp}`;
    }
    const adjustedValues = {
      ...values,
      mambaCache: speculative === 'enabled' ? 'v2' : values.mambaCache
    };
    for (const [key, option] of Object.entries(options)) {
      if (key === 'quantization' || key === 'hardware' || key === 'modelSize') continue;
      if (!option.commandRule) continue;
      const rule = option.commandRule(adjustedValues[key]);
      if (rule) {
        cmd += ` \\\n  ${rule}`;
      }
    }
    if (hardware === 'b200') {
      cmd += ` \\\n  --attention-backend trtllm_mha`;
    }
    cmd += ` \\\n  --mem-fraction-static ${hwConfig.mem}`;
    return cmd;
  };
  const containerStyle = {
    maxWidth: '900px',
    margin: '0 auto',
    display: 'flex',
    flexDirection: 'column',
    gap: '4px'
  };
  const cardStyle = {
    padding: '8px 12px',
    border: `1px solid ${isDark ? '#374151' : '#e5e7eb'}`,
    borderLeft: `3px solid ${isDark ? '#E85D4D' : '#D45D44'}`,
    borderRadius: '4px',
    display: 'flex',
    alignItems: 'center',
    gap: '12px',
    background: isDark ? '#1f2937' : '#fff'
  };
  const titleStyle = {
    fontSize: '13px',
    fontWeight: '600',
    minWidth: '140px',
    flexShrink: 0,
    color: isDark ? '#e5e7eb' : 'inherit'
  };
  const itemsStyle = {
    display: 'flex',
    rowGap: '2px',
    columnGap: '6px',
    flexWrap: 'wrap',
    alignItems: 'center',
    flex: 1
  };
  const labelBaseStyle = {
    padding: '4px 10px',
    border: `1px solid ${isDark ? '#9ca3af' : '#d1d5db'}`,
    borderRadius: '3px',
    cursor: 'pointer',
    display: 'inline-flex',
    flexDirection: 'column',
    alignItems: 'center',
    justifyContent: 'center',
    fontWeight: '500',
    fontSize: '13px',
    transition: 'all 0.2s',
    userSelect: 'none',
    minWidth: '45px',
    textAlign: 'center',
    flex: 1,
    background: isDark ? '#374151' : '#fff',
    color: isDark ? '#e5e7eb' : 'inherit'
  };
  const checkedStyle = {
    background: '#D45D44',
    color: 'white',
    borderColor: '#D45D44'
  };
  const disabledStyle = {
    cursor: 'not-allowed',
    opacity: 0.4
  };
  const commandDisplayStyle = {
    flex: 1,
    padding: '12px 16px',
    background: isDark ? '#111827' : '#f5f5f5',
    borderRadius: '6px',
    fontFamily: "'Menlo', 'Monaco', 'Courier New', monospace",
    fontSize: '12px',
    lineHeight: '1.5',
    color: isDark ? '#e5e7eb' : '#374151',
    whiteSpace: 'pre-wrap',
    overflowX: 'auto',
    margin: 0,
    border: `1px solid ${isDark ? '#374151' : '#e5e7eb'}`
  };
  return <div style={containerStyle} className="not-prose">
      {Object.entries(options).map(([key, option]) => {
    const items = resolveItems(option, values);
    return <div key={key} style={cardStyle}>
            <div style={titleStyle}>{option.title}</div>
            <div style={itemsStyle}>
              {items.map(item => {
      const isChecked = values[option.name] === item.id;
      const isDisabled = !!item.disabled;
      return <label key={item.id} style={{
        ...labelBaseStyle,
        ...isChecked ? checkedStyle : {},
        ...isDisabled ? disabledStyle : {}
      }} title={item.disabledReason || ''}>
                    <input type="radio" name={option.name} value={item.id} checked={isChecked} disabled={isDisabled} onChange={() => !isDisabled && handleRadioChange(option.name, item.id)} style={{
        display: 'none'
      }} />
                    {item.label}
                  </label>;
    })}
            </div>
          </div>;
  })}
      <div style={cardStyle}>
        <div style={titleStyle}>Run this Command:</div>
        <pre style={commandDisplayStyle}>{generateCommand()}</pre>
      </div>
    </div>;
};

## 1. Model Introduction

The Qwen3.6 series is developed by Alibaba. Built on direct feedback from the community, Qwen3.6 prioritizes stability and real-world utility, delivering substantial upgrades in agentic coding and thinking preservation. Two size/sparsity variants are released:

* [Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B) — **Sparse MoE** (35B total, 3B active) on a Gated Delta Networks backbone.
* [Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B) — **Dense** hybrid GDN; smaller weights footprint, single-GPU friendly.

Both variants share the same hybrid reasoning, tool-calling, and multimodal interface and natively handle context lengths of up to 262,144 tokens, extensible to over 1M tokens.

**Key Features:**

* **Agentic Coding**: Handles frontend workflows and repository-level reasoning with greater fluency and precision
* **Thinking Preservation**: New option to retain reasoning context from historical messages, streamlining iterative development
* **Efficient Hybrid Architecture**: Gated Delta Networks backbone; sparse MoE (35B / 3B active) or dense 27B variant
* **Hybrid Reasoning**: Thinking mode enabled by default with step-by-step reasoning, can be disabled for direct responses
* **Tool Calling**: Built-in tool calling support with `qwen3_coder` parser
* **Multi-Token Prediction (MTP)**: Speculative decoding support for lower latency; both MoE and Dense variants ship `mtp.safetensors`
* **Multimodal**: Unified vision-language model supporting text, image, and video inputs

**Available Models:**

<table style={{width: "100%", borderCollapse: "collapse", tableLayout: "fixed"}}>
  <thead>
    <tr>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>Model</th>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>Architecture</th>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>Weights</th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.02)"}}>Qwen3.6-35B-A3B (BF16)</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>MoE 35B / 3B active</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>[Qwen/Qwen3.6-35B-A3B](https://huggingface.co/Qwen/Qwen3.6-35B-A3B)</td>
    </tr>

    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.05)"}}>Qwen3.6-35B-A3B (FP8)</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>MoE 35B / 3B active</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>[Qwen/Qwen3.6-35B-A3B-FP8](https://huggingface.co/Qwen/Qwen3.6-35B-A3B-FP8)</td>
    </tr>

    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.02)"}}>Qwen3.6-27B (BF16)</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>Dense 27B</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>[Qwen/Qwen3.6-27B](https://huggingface.co/Qwen/Qwen3.6-27B)</td>
    </tr>

    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.05)"}}>Qwen3.6-27B (FP8)</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>Dense 27B</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>[Qwen/Qwen3.6-27B-FP8](https://huggingface.co/Qwen/Qwen3.6-27B-FP8)</td>
    </tr>
  </tbody>
</table>

**License:** Apache 2.0

## 2. SGLang Installation

SGLang `>=0.5.10` is required for Qwen3.6. You can install from PyPI, from source, or use a Docker image:

```bash Command theme={null}
# Install from PyPI
uv pip install sglang

# Or install from source
uv pip install 'git+https://github.com/sgl-project/sglang.git#subdirectory=python'

# Or use Docker (NVIDIA GPUs)
docker pull lmsysorg/sglang:latest
```

For the full Docker setup and other installation methods, please refer to the [official SGLang installation guide](../../../docs/get-started/install).

## 3. Model Deployment

This section provides deployment configurations optimized for different hardware platforms and use cases.

### 3.1 Basic Configuration

**Interactive Command Generator**: Use the configuration selector below to automatically generate the appropriate deployment command for your hardware platform and capabilities.

<Qwen36Deployment />

### 3.2 Configuration Tips

* Speculative decoding (MTP) can significantly reduce latency for interactive use cases.
* **Mamba Radix Cache**: Qwen3.6's hybrid Gated Delta Networks architecture supports two mamba scheduling strategies via `--mamba-scheduler-strategy`:
  * **V1 (`no_buffer`)**: Default. No overlap scheduler, lower memory usage.
  * **V2 (`extra_buffer`)**: Enables overlap scheduling and branching point caching with `--mamba-scheduler-strategy extra_buffer --page-size 64`. Requires FLA kernel backend (NVIDIA GPUs only). Trades higher mamba state memory for better throughput.
* The `--mem-fraction-static` flag is recommended for optimal memory utilization, adjust it based on your hardware and workload.
* Context length defaults to 262,144 tokens. If you encounter OOM errors, consider reducing it, but maintain at least 128K to preserve thinking capabilities.
* **CUDA IPC Transport**: Add `SGLANG_USE_CUDA_IPC_TRANSPORT=1` as an environment variable to use CUDA IPC for transferring multimodal features, significantly improving TTFT (Time To First Token). Note: this consumes additional memory proportional to image size, so you may need to lower `--mem-fraction-static` or `--max-running-requests`.
* **Multimodal Attention Backend**: Use `--mm-attention-backend fa3` on H100/H200 for better vision performance, or `--mm-attention-backend fa4` on B200.
* For processing large images or videos, you may need to lower `--mem-fraction-static` to leave room for image feature tensors.
* Hardware requirements:
  * **35B-A3B BF16**: \~70GB for weights. TP=1 fits on all supported hardware.
  * **35B-A3B FP8**: \~35GB for weights. TP=1 fits on all supported hardware.
  * **27B BF16**: \~54GB for weights. TP=1 fits on all supported hardware.
  * **27B FP8**: \~27GB for weights. TP=1 fits on all supported hardware.

All Qwen3.6 variants (MoE 35B-A3B and Dense 27B) fit on a single supported GPU at both precisions:

<table style={{width: "100%", borderCollapse: "collapse", tableLayout: "fixed"}}>
  <thead>
    <tr>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>Hardware</th>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>Memory</th>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>BF16 TP</th>
      <th style={{padding: "9px 12px", textAlign: "left", borderBottom: "1px solid rgba(148,163,184,0.3)"}}>FP8 TP</th>
    </tr>
  </thead>

  <tbody>
    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.02)"}}>H100</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>80GB</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>1</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>1</td>
    </tr>

    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.05)"}}>H200</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>141GB</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>1</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.05)"}}>1</td>
    </tr>

    <tr>
      <td style={{padding: "9px 12px", fontWeight: 500, backgroundColor: "rgba(255,255,255,0.02)"}}>B200</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>183GB</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>1</td>
      <td style={{padding: "9px 12px", backgroundColor: "rgba(255,255,255,0.02)"}}>1</td>
    </tr>
  </tbody>
</table>

## 4. Model Invocation

Deploy Qwen3.6 with the following command (H200, all features enabled). Swap `--model-path` to `Qwen/Qwen3.6-27B-FP8` for the dense 27B variant — all other flags carry over:

```shell Command theme={null}
SGLANG_ENABLE_SPEC_V2=1 sglang serve \
  --model-path Qwen/Qwen3.6-35B-A3B-FP8 \
  --reasoning-parser qwen3 \
  --tool-call-parser qwen3_coder \
  --speculative-algorithm EAGLE \
  --speculative-num-steps 3 \
  --speculative-eagle-topk 1 \
  --speculative-num-draft-tokens 4 \
  --mem-fraction-static 0.8 \
  --host 0.0.0.0 \
  --port 30000
```

### 4.1 Basic Usage

For basic API usage and request examples, please refer to:

* [SGLang Basic Usage Guide](../../../docs/basic_usage/send_request)

### 4.2 Vision Input

Qwen3.6 supports image and video inputs as a unified vision-language model.

**Image Input Example:**

```python Example theme={null}
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="EMPTY"
)

response = client.chat.completions.create(
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen3.5/demo/CI_Demo/mathv-1327.jpg"
                    }
                },
                {
                    "type": "text",
                    "text": "Describe this image in detail."
                }
            ]
        }
    ],
    max_tokens=2048,
    stream=True
)

thinking_started = False
has_thinking = False
has_answer = False

for chunk in response:
    if chunk.choices and len(chunk.choices) > 0:
        delta = chunk.choices[0].delta

        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            if not thinking_started:
                print("=============== Thinking =================", flush=True)
                thinking_started = True
            has_thinking = True
            print(delta.reasoning_content, end="", flush=True)

        if delta.content:
            if has_thinking and not has_answer:
                print("\n=============== Content =================", flush=True)
                has_answer = True
            print(delta.content, end="", flush=True)

print()
```

**Video Input Example:**

```python Example theme={null}
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="EMPTY"
)

response = client.chat.completions.create(
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    messages=[
        {
            "role": "user",
            "content": [
                {
                    "type": "video_url",
                    "video_url": {
                        "url": "https://qianwen-res.oss-accelerate.aliyuncs.com/Qwen3.5/demo/video/N1cdUjctpG8.mp4"
                    }
                },
                {
                    "type": "text",
                    "text": "Describe what happens in this video."
                }
            ]
        }
    ],
    max_tokens=2048,
    stream=True
)

thinking_started = False
has_thinking = False
has_answer = False

for chunk in response:
    if chunk.choices and len(chunk.choices) > 0:
        delta = chunk.choices[0].delta

        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            if not thinking_started:
                print("=============== Thinking =================", flush=True)
                thinking_started = True
            has_thinking = True
            print(delta.reasoning_content, end="", flush=True)

        if delta.content:
            if has_thinking and not has_answer:
                print("\n=============== Content =================", flush=True)
                has_answer = True
            print(delta.content, end="", flush=True)

print()
```

### 4.3 Advanced Usage

#### 4.3.1 Reasoning Parser

Qwen3.6 supports Thinking mode **by default**. Enable the reasoning parser during deployment to separate the thinking and content sections. The thinking process is returned via `reasoning_content` in the streaming response.

To disable thinking and use Instruct mode, pass `chat_template_kwargs` at request time:

* **Thinking mode** (default): The model performs step-by-step reasoning before answering. No extra parameters needed.
* **Instruct mode** (`{"enable_thinking": false}`): The model responds directly without a thinking process.

**Example 1: Thinking Mode (Default)**

```python Example theme={null}
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="EMPTY"
)

response = client.chat.completions.create(
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    messages=[
        {"role": "user", "content": "Solve this problem step by step: What is 15% of 240?"}
    ],
    max_tokens=2048,
    stream=True
)

has_thinking = False
has_answer = False
thinking_started = False

for chunk in response:
    if chunk.choices and len(chunk.choices) > 0:
        delta = chunk.choices[0].delta

        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            if not thinking_started:
                print("=============== Thinking =================", flush=True)
                thinking_started = True
            has_thinking = True
            print(delta.reasoning_content, end="", flush=True)

        if delta.content:
            if has_thinking and not has_answer:
                print("\n=============== Content =================", flush=True)
                has_answer = True
            print(delta.content, end="", flush=True)

print()
```

**Example 2: Instruct Mode (Thinking Off)**

To disable thinking and get a direct response, pass `{"enable_thinking": false}` via `chat_template_kwargs`:

```python Example theme={null}
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="EMPTY"
)

response = client.chat.completions.create(
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    messages=[
        {"role": "user", "content": "What is 15% of 240?"}
    ],
    extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    max_tokens=2048,
    stream=True
)

for chunk in response:
    if chunk.choices and len(chunk.choices) > 0:
        delta = chunk.choices[0].delta
        if delta.content:
            print(delta.content, end="", flush=True)

print()
```

#### 4.3.2 Thinking Preservation

Qwen3.6 has been trained to preserve and leverage thinking traces from historical messages. Enable this for agent scenarios where maintaining full reasoning context improves decision consistency:

```python Example theme={null}
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="EMPTY"
)

response = client.chat.completions.create(
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    messages=[
        {"role": "user", "content": "Help me plan a web app architecture."}
    ],
    extra_body={"chat_template_kwargs": {"preserve_thinking": True}},
    max_tokens=2048,
    stream=True
)

thinking_started = False
has_thinking = False
has_answer = False

for chunk in response:
    if chunk.choices and len(chunk.choices) > 0:
        delta = chunk.choices[0].delta

        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            if not thinking_started:
                print("=============== Thinking =================", flush=True)
                thinking_started = True
            has_thinking = True
            print(delta.reasoning_content, end="", flush=True)

        if delta.content:
            if has_thinking and not has_answer:
                print("\n=============== Content =================", flush=True)
                has_answer = True
            print(delta.content, end="", flush=True)

print()
```

#### 4.3.3 Tool Calling

Qwen3.6 supports tool calling capabilities. Enable the tool call parser during deployment.

```python Example theme={null}
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:30000/v1",
    api_key="EMPTY"
)

tools = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a location",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {
                        "type": "string",
                        "description": "The city name"
                    },
                    "unit": {
                        "type": "string",
                        "enum": ["celsius", "fahrenheit"],
                        "description": "Temperature unit"
                    }
                },
                "required": ["location"]
            }
        }
    }
]

response = client.chat.completions.create(
    model="Qwen/Qwen3.6-35B-A3B-FP8",
    messages=[
        {"role": "user", "content": "What's the weather in Beijing?"}
    ],
    tools=tools,
    stream=True
)

thinking_started = False
has_thinking = False

for chunk in response:
    if chunk.choices and len(chunk.choices) > 0:
        delta = chunk.choices[0].delta

        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
            if not thinking_started:
                print("=============== Thinking =================", flush=True)
                thinking_started = True
            has_thinking = True
            print(delta.reasoning_content, end="", flush=True)

        if hasattr(delta, 'tool_calls') and delta.tool_calls:
            if has_thinking and thinking_started:
                print("\n=============== Content =================", flush=True)
                thinking_started = False

            for tool_call in delta.tool_calls:
                if tool_call.function:
                    print(f"Tool Call: {tool_call.function.name}")
                    print(f"   Arguments: {tool_call.function.arguments}")

        if delta.content:
            print(delta.content, end="", flush=True)

print()
```
