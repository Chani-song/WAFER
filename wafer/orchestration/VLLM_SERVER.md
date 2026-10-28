# Model server

The runs used three Qwen3.5 models from Hugging Face:

| Model key | Repository | Revision |
|---|---|---|
| `qwen35_4b` | `Qwen/Qwen3.5-4B` | `851bf6e806efd8d0a36b00ddf55e13ccb7b8cd0a` |
| `qwen35_9b` | `Qwen/Qwen3.5-9B` | `c202236235762e1c871ad0ccb60c8ee5ba337b9a` |
| `qwen35_27b` | `Qwen/Qwen3.5-27B` | `fc05daec18b0a78c049392ed2e771dde82bdf654` |

Each model was served by a local vLLM endpoint with an OpenAI-compatible chat interface, started by `server_ctl.sh`. Every request used temperature 0.7, top-p 0.8, at most 4,096 generated tokens and a context length of 32,768 tokens. The generation seed equals the run index, and the endpoint enforces a strict JSON schema for the output.
