# What exact JSON field does llama.cpp's OpenAI-compatible server accept

_2026-08-25 15:39_

**Question:** What exact JSON field does llama.cpp's OpenAI-compatible server accept to disable Qwen3 thinking mode, and what is the equivalent in vLLM?

**Rubric:** Must state the exact JSON field for each server: llama.cpp uses "chat_template_kwargs": {"enable_thinking": false} in the request body; vLLM equivalent via chat_template_kwargs (e.g., in extra_body). Cite official docs or source code, show placement in payload, and note any version constraints.

## Answer

## Answer

**llama.cpp:** The OpenAI-compatible server (`llama-server`) accepts a **top-level field `"chat_template_kwargs"` in the request body**, set to `{"enable_thinking": false}`, as a sibling of standard fields like `messages` and `temperature` [4]:

```json
{
  "model": "Qwen/Qwen3-8B",
  "messages": [
    {"role":"user","content":"Give me a short introduction to large language models."}
  ],
  "temperature": 0.7,
  "top_p": 0.8,
  "top_k": 20,
  "max_tokens": 8192,
  "presence_penalty": 1.5,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

This was identified as the "proper way" (vs. prompt hacks) in the GGUF discussion thread, pointing to llama.cpp issue #13178 [3]. A server-side default can also be set at launch with `--chat-template-kwargs '{"enable_thinking":false}'` [2][5].

**vLLM:** The equivalent field has the **same name, `chat_template_kwargs`**, passed inside `extra_body` when using the OpenAI Python client, or merged directly into the JSON payload for raw HTTP calls [6]:

```python
extra_body = {
    "chat_template_kwargs": {
        "enable_thinking": False
    }
}
```

vLLM's official OpenAI-compatible server docs define it as a typed extra parameter: `chat_template_kwargs : dict[str, Any] | None = Field(default=None, description="Additional keyword args to pass to the template renderer...")` [6]. The same `extra_body={"chat_template_kwargs": {...}}` pattern appears in vLLM's reasoning examples (shown there for Granite with a `thinking` key) [7][8].

## Mechanism

Both servers forward the value into Qwen3's Jinja chat template, which contains `{%- if enable_thinking is defined and enable_thinking is false %} {{- '\n\n' }} {%- endif %}` — injecting an empty `` block to suppress reasoning [1][5]. The Qwen3 model card confirms `enable_thinking` is exposed through SGLang and vLLM APIs [1].

## Caveats

- **Soft-switch alternative:** Appending `/no_think` to the prompt also disables thinking without any API field [3].
- **Residual `

## Sources

1. https://huggingface.co/Qwen/Qwen3-32B
2. https://github.com/ggml-org/llama.cpp/issues/20182
3. https://huggingface.co/bartowski/Qwen_Qwen3-32B-GGUF/discussions/1
4. https://github.com/ggml-org/llama.cpp/issues/13160
5. https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/discussions/4
6. https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/
7. https://docs.vllm.ai/en/latest/examples/reasoning/openai_chat_completion_with_reasoning/
8. https://docs.vllm.ai/en/latest/examples/reasoning/openai_chat_completion_with_reasoning_streaming/

## Evidence

[1] https://huggingface.co/Qwen/Qwen3-32B
The page confirms the `enable_thinking` parameter controls Qwen3's thinking mode. For vLLM, it states: "The `enable_thinking` switch is also available in APIs created by SGLang and vLLM." It provides the vLLM server command: `vllm serve Qwen/Qwen3-32B --enable-reasoning --reasoning-parser deepseek_r1`.

For llama.cpp, the page notes: "For local use, applications such as Ollama, LMStudio, MLX-LM, llama.cpp, and KTransformers have also supported Qwen3." It also mentions: "For llama-server from llama.cpp, you can use `llama-server ... --rope-scaling yarn --rope-scale 4 --yarn-orig-ctx 32768`" (regarding context, not thinking).

The chat template logic shows: `{%- if enable_thinking is defined and enable_thinking is false %} {{- '\n\n' }} {%- endif %}`.

However, the page **does not** explicitly state the exact JSON field name (e.g., `chat_template_kwargs`) or its placement in the request body for either llama.cpp or vLLM. It only confirms the parameter name `enable_thinking` and that it is available in their APIs.

[2] https://github.com/ggml-org/llama.cpp/issues/20182
**llama.cpp (OpenAI‑compatible server)**  
- The request body must include the field **`chat_template_kwargs`** with the JSON value `{"enable_thinking": false}`.  
  Example from the issue:  

  ```bash
  ./llama-cli -m ~/models/Qwen3.5-9B-Q4_K_M.gguf \
    -cnv -t 10 -c 2048 \
    --chat-template-kwargs '{"enable_thinking": false}'
  ```

  This flag is passed to the server as part of the request payload.

**vLLM (OpenAI‑compatible server)**  
- The equivalent setting is also named **`chat_template_kwargs`** but is placed inside the `extra_body` of the request.  
  ```json
  {
    "model": "qwen3.5",
    "messages": [...],
    "extra_body": {
      "chat_template_kwargs": {
        "enable_thinking": false
      }
    }
  }
  ```

**Version constraints**  
- The `chat_template_kwargs` field is supported in vLLM **≥ 0.5.0** (see vLLM docs).  
- The `--chat-template-kwargs` flag is available in llama.cpp **≥ 0.8.2** (see the issue discussion).

[3] https://huggingface.co/bartowski/Qwen_Qwen3-32B-GGUF/discussions/1
The page confirms the `enable_thinking` parameter is encoded in the Qwen3 chat template. It notes that "llama-cpp fails to parse it with `--jinja` arg, so it would need some simplification."

The discussion identifies two methods to disable thinking:
1.  **Prompt-based:** Adding `/no_think` at the end of the prompt. User `bartowski` states: "Yes, I've found adding `/no_think` at the end of my prompt works perfectly."
2.  **Proper Method:** User `kurnevsky` points to the "proper way" via a link to `https://github.com/ggml-org/llama.cpp/issues/13178#issuecomment-2839416968`.

The page does **not** explicitly state the exact JSON field name (e.g., `chat_template_kwargs`) or the vLLM equivalent in the visible text, nor does it show the specific JSON payload structure. It only references the external GitHub issue for the "proper way."

[4] https://github.com/ggml-org/llama.cpp/issues/13160
**llama.cpp**  
- The OpenAI‑compatible server accepts the flag **`enable_thinking`** in the request body.  
- Example payload (from the issue):  

```json
{
  "model": "Qwen/Qwen3-8B",
  "messages": [
    {"role":"user","content":"Give me a short introduction to large language models."}
  ],
  "temperature":0.7,
  "top_p":0.8,
  "top_k":20,
  "max_tokens":8192,
  "presence_penalty":1.5,
  "chat_template_kwargs": {"enable_thinking": false}
}
```

**vLLM**  
- vLLM uses the same field name **`chat_template_kwargs`** (typically passed via `extra_body` in the client).  
- The equivalent payload in vLLM is identical to the llama.cpp example above, with `chat_template_kwargs: {"enable_thinking": false}`.  

**Version constraints**  
- The issue refers to the current llama.cpp build (April 2025) and vLLM documentation links for Qwen deployment. No specific version numbers are given, but the functionality is present in the latest releases of both projects.

[5] https://huggingface.co/unsloth/Qwen3.5-27B-GGUF/discussions/4
The page confirms that for llama.cpp (specifically version b8148), the JSON field to disable Qwen3 thinking mode is `"chat_template_kwargs": {"enable_thinking": false}`.

**llama.cpp Placement:**
The page demonstrates this is passed via the command-line flag `--chat-template-kwargs '{"enable_thinking":false}'` when starting `llama-server`. A user notes: "it however works as intended with llama-server using `--chat-template-kwargs '{\"enable_thinking\":false}'`". The chat template embedded in the GGUF file contains the logic: `{%- if enable_thinking is defined and enable_thinking is false %} {{- '\n\n' }}`.

**vLLM Equivalent:**
The page provides standard vLLM usage instructions (`vllm serve "unsloth/Qwen3.5-27B-GGUF"`) and a `curl` example for the OpenAI-compatible API, but it **does not** explicitly state the vLLM-specific JSON field or `extra_body` placement for disabling thinking mode. It only shows a standard request body with `model` and `messages`.

**Version Constraint:**
The discussion is specific to **llama.cpp b8148**. One user reports that even with `enable_thinking: false`, the model still showed thinking blocks in the CLI, requiring an additional flag `--reasoning-budget 0`. However, another user confirms it works as intended in `llama-server` with just the `chat_template_kwargs` flag.

[6] https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/
In vLLM, the exact JSON field is `chat_template_kwargs`. It is defined in the Chat API extra parameters as:

```python
chat_template_kwargs : dict [ str , Any ] | None = Field ( default = None , description = ( "Additional keyword args to pass to the template renderer. " "Will be accessible by the chat template." ), )
```

To disable Qwen3 thinking mode, you pass `{"enable_thinking": false}` within this field. When using the OpenAI Python client, this is placed in the `extra_body` parameter:

```python
extra_body = {
    "chat_template_kwargs": {
        "enable_thinking": false
    }
}
```

If using direct HTTP calls, merge `chat_template_kwargs` directly into the JSON payload. The documentation notes that vLLM supports parameters not in the OpenAI API by passing them as extra parameters or merging them into the JSON payload. No specific version constraints are mentioned for this field in the provided text, though it is part of the latest developer preview docs.

[7] https://docs.vllm.ai/en/latest/examples/reasoning/openai_chat_completion_with_reasoning/
The page provides the exact JSON field and placement for vLLM. It states: "For granite, add: `extra_body={"chat_template_kwargs": {"thinking": True}}`". This indicates the field is `chat_template_kwargs` passed within the `extra_body` parameter of the OpenAI client request. The example code shows this is used to control reasoning/thinking modes for models like Granite (and by extension, similar architectures like Qwen3). The page does not mention llama.cpp or Qwen3 specifically, but provides the vLLM equivalent mechanism.

**vLLM:**
- **Field:** `chat_template_kwargs`
- **Placement:** Inside `extra_body` in the request payload.
- **Example:** `extra_body={"chat_template_kwargs": {"thinking": True}}` (set to `false` to disable).
- **Source:** `https://github.com/vllm-project/vllm/blob/main/examples/reasoning/openai_chat_completion_with_reasoning.py`

**llama.cpp:**
- The provided page contains **no information** regarding llama.cpp's specific JSON fields or documentation.

[8] https://docs.vllm.ai/en/latest/examples/reasoning/openai_chat_completion_with_reasoning_streaming/
The page provides the exact JSON field for vLLM to control thinking mode via `chat_template_kwargs`.

**vLLM Equivalent:**
The documentation explicitly states:
> "For granite: add: `extra_body={"chat_template_kwargs": {"thinking": True}}`"

This indicates that in vLLM's OpenAI-compatible server, the field is passed via the `extra_body` parameter in the client request, containing the key `chat_template_kwargs` with the sub-key `thinking` (boolean). While the example shows `True` for enabling, the structure implies `{"thinking": false}` would disable it.

**llama.cpp:**
The provided text **does not contain** information regarding llama.cpp's specific JSON field (`"chat_template_kwargs": {"enable_thinking": false}`) or its placement in the request body. It only covers vLLM's implementation and references DeepSeek-R1 reasoning parsers.

**Relevant Quote:**
```python
# For granite: add: `extra_body={"chat_template_kwargs": {"thinking": True}}`
```

**Version Constraints:**
The page notes: "You are viewing the latest developer preview docs." No specific version constraints for the `chat_template_kwargs` feature are listed in the extracted text.

**Conclusion:**
Only the vLLM portion of the rubric is addressed by this page. The llama.cpp specific field and placement are not present in this source.

## Provenance

```json
{
  "question": "What exact JSON field does llama.cpp's OpenAI-compatible server accept to disable Qwen3 thinking mode, and what is the equivalent in vLLM?",
  "wide_lane": true,
  "breadth": 6,
  "max_pages": 14,
  "queries": [
    "llama.cpp server disable Qwen3 thinking enable_thinking false",
    "llama.cpp OpenAI compatible server chat_template_kwargs enable_thinking",
    "vLLM disable Qwen3 thinking mode enable_thinking false",
    "vLLM chat_template_kwargs enable_thinking Qwen3 example",
    "Qwen3 enable_thinking false OpenAI API request body",
    "ggml llama.cpp github issue Qwen3 thinking toggle json field"
  ],
  "rubric": "Must state the exact JSON field for each server: llama.cpp uses \"chat_template_kwargs\": {\"enable_thinking\": false} in the request body; vLLM equivalent via chat_template_kwargs (e.g., in extra_body). ",
  "plan_seeds": [
    "https://github.com/ggml-org/llama.cpp",
    "https://docs.vllm.ai/en/latest/",
    "https://huggingface.co/Qwen/Qwen3-32B",
    "https://qwen.readthedocs.io/",
    "https://github.com/vllm-project/vllm"
  ],
  "seed_urls": 14,
  "links_harvested": 2123,
  "hop2_top": [
    {
      "url": "https://docs.vllm.ai/en/latest/serving/online_serving/openai_compatible_server/",
      "score": 26.4,
      "anchor": "OpenAI-Compatible Server"
    },
    {
      "url": "https://docs.vllm.ai/en/latest/examples/applications/api_server/",
      "score": 15.8,
      "anchor": "API Server"
    },
    {
      "url": "https://github.com/ggml-org/llama.cpp/issues/9291",
      "score": 14.3,
      "anchor": "llama-server REST API"
    },
    {
      "url": "https://docs.vllm.ai/en/latest/examples/features/tensorize_vllm_model/",
      "score": 14.1,
      "anchor": "Tensorize vLLM Model"
    },
    {
      "url": "https://docs.vllm.ai/en/latest/features/speculative_decoding/acceptance_metrics/",
      "score": 13.6,
      "anchor": "Per-Request Acceptance Metrics"
    }
  ],
  "pages_fetched": 28,
  "pages_with_evidence": 8,
  "dropped": {
    "irrelevant": 20,
    "error": 0
  },
  "seconds": 312.0,
  "evidence_chars": 8961
}
```
