# Provider catalog data

Scope: MAR catalogs the user's full model-provider surface — the 12 original
subscriptions, the 6 mesh-lane providers, and every provider in omp's roster
(94 auth policies as of 2026-10-13) so `mar add`/`mar omp-sync` cover all of
omp. Editorial source for `src/mar/providers.py`; keep both in sync.

Verification: the 12 originals' omp id/env were confirmed 2026-10-10 (live
`~/.omp/agent/agent.db` rows for cline/opencode/zai; OpenRouter's documented
credits API). The 2026-10-13 roster sweep confirmed every remaining omp id +
env var against omp's provider roster (`omp://providers.md` env tables and the
compiled KDL auth policies in the oh-my-pi source on this box); `key_url`s
marked in the catalog notes as omp's documented login auth-url come from that
roster, other URLs are best-known. Three entries are `verified=False`:
`llama-cpp` (omp's roster id is `llama.cpp`, which MAR's id grammar cannot
express), and the non-roster mesh providers `orcarouter`/`cloudflare`.
`unverified()` returns those three.

| # | id | display | omp_provider_id | credential route | key_url | limits_url | limits_kind |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | cursor-grok | Cursor / Grok | cursor | env `CURSOR_ACCESS_TOKEN` | https://cursor.com/settings | https://cursor.com/usage | manual |
| 2 | codex | OpenAI Codex | openai-codex | env `OPENAI_CODEX_OAUTH_TOKEN` | https://platform.openai.com/api-keys | https://platform.openai.com/usage | manual |
| 3 | claude | Anthropic Claude | anthropic | env `ANTHROPIC_API_KEY` | https://console.anthropic.com/settings/keys | https://console.anthropic.com/settings/usage | manual |
| 4 | cline | Cline | cline-pass | agent.db (OAuth-first) | https://cline.bot | https://cline.bot | manual |
| 5 | opencode | OpenCode | opencode-go | agent.db (OAuth-first) | https://opencode.ai | https://opencode.ai | manual |
| 6 | zai | Z.ai | zai | agent.db (OAuth-first) | https://z.ai/manage-apikey/apikey-list | https://z.ai | manual |
| 7 | openrouter | OpenRouter | openrouter | agent.db (OAuth-first) | https://openrouter.ai/settings/keys | https://openrouter.ai/credits | api |
| 8 | antigravity | Google Antigravity | google-antigravity | agent.db (OAuth-first) | https://antigravity.google | https://antigravity.google | manual |
| 9 | copilot | GitHub Copilot | github-copilot | env `COPILOT_GITHUB_TOKEN` | https://github.com/settings/tokens | https://github.com/settings/copilot | manual |
| 10 | jev | Jev (Typesafe) | typesafe | env `TYPESAFE_API_KEY` | https://typesafe.ai | https://typesafe.ai | manual |
| 11 | gemini | Google Gemini | google | env `GEMINI_API_KEY` | https://aistudio.google.com/api/keys | https://aistudio.google.com/usage | api |
| 12 | commandcode | Command Code | commandcode | env `COMMAND_CODE_API_KEY` | https://commandcode.ai | https://commandcode.ai | manual |
| 13 | deepseek | DeepSeek | deepseek | env `DEEPSEEK_API_KEY` | https://platform.deepseek.com/api_keys | https://platform.deepseek.com/usage | manual |
| 14 | groq | Groq | groq | env `GROQ_API_KEY` | https://console.groq.com/keys | https://console.groq.com/usage | manual |
| 15 | cerebras | Cerebras | cerebras | env `CEREBRAS_API_KEY` | https://cloud.cerebras.ai | https://cloud.cerebras.ai | manual |
| 16 | mistral | Mistral | mistral | env `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys | https://console.mistral.ai | manual |
| 17 | orcarouter | Orcarouter | orcarouter | env `ORCAROUTER_API_KEY` | https://orcarouter.ai/dashboard | https://orcarouter.ai/dashboard | manual |
| 18 | cloudflare | Cloudflare Workers AI | cloudflare | env `CLOUDFLARE_API_TOKEN` | https://dash.cloudflare.com/profile/api-tokens | https://dash.cloudflare.com | manual |
| 19 | abliteration | Abliteration | abliteration | env `ABLITERATION_API_KEY` | https://abliteration.ai/console | https://abliteration.ai | manual |
| 20 | aiand | ai& | aiand | env `AIAND_API_KEY` | https://console.aiand.com/api-keys | https://console.aiand.com | manual |
| 21 | aimlapi | AIML API | aimlapi | env `AIMLAPI_API_KEY` | https://aimlapi.com | https://aimlapi.com | manual |
| 22 | alibaba-coding-plan | Alibaba Coding Plan | alibaba-coding-plan | env `ALIBABA_CODING_PLAN_API_KEY` | https://www.alibabacloud.com | https://www.alibabacloud.com | manual |
| 23 | alibaba-token-plan | QwenCloud Token Plan | alibaba-token-plan | env `ALIBABA_TOKEN_PLAN_API_KEY` | https://www.alibabacloud.com | https://www.alibabacloud.com | manual |
| 24 | amazon-bedrock | Amazon Bedrock | amazon-bedrock | env `AWS_BEARER_TOKEN_BEDROCK` | https://console.aws.amazon.com/iam | https://console.aws.amazon.com/bedrock | manual |
| 25 | apple | Apple Foundation Models | apple | keyless (no env) | https://developer.apple.com | https://developer.apple.com | manual |
| 26 | azure | Azure OpenAI | azure | env `AZURE_OPENAI_API_KEY` | https://portal.azure.com | https://portal.azure.com | manual |
| 27 | baseten | Baseten | baseten | env `BASETEN_API_KEY` | https://app.baseten.co/settings/api_keys | https://app.baseten.co | manual |
| 28 | bedrock-mantle | Amazon Bedrock Mantle | bedrock-mantle | env `AWS_BEARER_TOKEN_BEDROCK` | https://aws.amazon.com/bedrock | https://aws.amazon.com/bedrock | manual |
| 29 | charm-hyper | Charm Hyper | charm-hyper | env `CHARM_HYPER_API_KEY` | https://hyper.charm.land/ | https://hyper.charm.land | manual |
| 30 | cloudflare-ai-gateway | Cloudflare AI Gateway | cloudflare-ai-gateway | env `CLOUDFLARE_AI_GATEWAY_API_KEY` | https://dash.cloudflare.com | https://dash.cloudflare.com | manual |
| 31 | coreweave | CoreWeave Serverless Inference | coreweave | env `COREWEAVE_API_KEY` | https://wandb.ai/settings | https://wandb.ai | manual |
| 32 | deepinfra | DeepInfra | deepinfra | env `DEEPINFRA_API_KEY` | https://deepinfra.com/dash/api_keys | https://deepinfra.com/dash | manual |
| 33 | devin | Devin | devin | env `DEVIN_API_KEY` | https://app.devin.ai | https://app.devin.ai | manual |
| 34 | exa | Exa | exa | env `EXA_API_KEY` | https://dashboard.exa.ai/api-keys | https://dashboard.exa.ai | manual |
| 35 | factory-droid | Factory Droid | factory-droid | agent.db (OAuth-first) | https://factory.ai | https://factory.ai | manual |
| 36 | firepass | Fire Pass (Fireworks subscription) | firepass | env `FIREPASS_API_KEY` | https://app.fireworks.ai/settings/users/api-keys | https://app.fireworks.ai | manual |
| 37 | fireworks | Fireworks | fireworks | env `FIREWORKS_API_KEY` | https://app.fireworks.ai/settings/users/api-keys | https://app.fireworks.ai | manual |
| 38 | gitlab-duo | GitLab Duo Non-Agentic | gitlab-duo | env `GITLAB_TOKEN` | https://gitlab.com/-/user_settings/personal_access_tokens | https://gitlab.com | manual |
| 39 | gitlab-duo-agent | GitLab Duo Agent | gitlab-duo-agent | env `GITLAB_TOKEN` | https://gitlab.com/-/user_settings/personal_access_tokens | https://gitlab.com | manual |
| 40 | gmi-cloud | GMI Cloud | gmi-cloud | env `GMI_API_KEY` | https://console.gmicloud.ai | https://console.gmicloud.ai | manual |
| 41 | google-gemini-cli | Google Cloud Code Assist (Gemini CLI) | google-gemini-cli | agent.db (OAuth-first) | https://developers.google.com/gemini-code-assist | https://developers.google.com/gemini-code-assist | manual |
| 42 | google-vertex | Google Vertex AI | google-vertex | env `GOOGLE_CLOUD_API_KEY` | https://console.cloud.google.com/apis/credentials | https://console.cloud.google.com/vertex-ai | manual |
| 43 | helmcode | Helmcode | helmcode | env `HELMCODE_API_KEY` | https://cloud.helmcode.com | https://cloud.helmcode.com | manual |
| 44 | huggingface | Hugging Face Inference | huggingface | env `HUGGINGFACE_HUB_TOKEN` | https://huggingface.co/settings/tokens | https://huggingface.co/settings | manual |
| 45 | kagi | Kagi | kagi | env `KAGI_API_KEY` | https://kagi.com/settings/api | https://kagi.com/settings | manual |
| 46 | kilo | Kilo Gateway | kilo | env `KILO_API_KEY` | https://kilo.dev | https://kilo.dev | manual |
| 47 | kimi-code | Kimi Code | kimi-code | env `KIMI_API_KEY` | https://www.kimi.com | https://www.kimi.com | manual |
| 48 | litellm | LiteLLM | litellm | env `LITELLM_API_KEY` | https://docs.litellm.ai/docs/proxy/deploy | https://docs.litellm.ai | manual |
| 49 | llama-cpp | llama.cpp (local) | — | env `LLAMA_CPP_API_KEY` | https://github.com/ggmlorg/llama.cpp | https://github.com/ggmlorg/llama.cpp | manual |
| 50 | lm-studio | LM Studio (local) | lm-studio | env `LM_STUDIO_API_KEY` | https://lmstudio.ai | https://lmstudio.ai | manual |
| 51 | local | Local models | local | keyless (no env) | https://ohmypi.xyz | https://ohmypi.xyz | manual |
| 52 | meta | Meta Model API | meta | env `MODEL_API_KEY` | https://developer.meta.com/ai/ | https://developer.meta.com/ai/ | manual |
| 53 | minimax | MiniMax | minimax | env `MINIMAX_API_KEY` | https://platform.minimax.io | https://platform.minimax.io | manual |
| 54 | minimax-code | MiniMax Token Plan (International) | minimax-code | env `MINIMAX_CODE_API_KEY` | https://platform.minimax.io/subscribe/token-plan | https://platform.minimax.io | manual |
| 55 | minimax-code-cn | MiniMax Token Plan (China) | minimax-code-cn | env `MINIMAX_CODE_CN_API_KEY` | https://platform.minimaxi.com/subscribe/token-plan | https://platform.minimaxi.com | manual |
| 56 | moonshot | Moonshot (Kimi API) | moonshot | env `MOONSHOT_API_KEY` | https://platform.moonshot.ai/console/api-keys | https://platform.moonshot.ai/console | manual |
| 57 | muse-code | Muse Code (Subscription) | muse-code | agent.db (OAuth-first) | https://www.meta.com | https://www.meta.com | manual |
| 58 | nanogpt | NanoGPT | nanogpt | env `NANO_GPT_API_KEY` | https://nano-gpt.com/api | https://nano-gpt.com | manual |
| 59 | novita | Novita | novita | env `NOVITA_API_KEY` | https://novita.ai/settings/key-management | https://novita.ai | manual |
| 60 | nvidia | NVIDIA | nvidia | env `NVIDIA_API_KEY` | https://org.ngc.nvidia.com/setup/personal-keys | https://build.nvidia.com | manual |
| 61 | ollama | Ollama (local) | ollama | env `OLLAMA_API_KEY` | https://ollama.com | https://ollama.com | manual |
| 62 | ollama-cloud | Ollama Cloud | ollama-cloud | env `OLLAMA_CLOUD_API_KEY` | https://ollama.com/settings/keys | https://ollama.com/settings | manual |
| 63 | openai | OpenAI | openai | env `OPENAI_API_KEY` | https://platform.openai.com/api-keys | https://platform.openai.com/usage | manual |
| 64 | openai-codex-device | ChatGPT Plus/Pro (Codex, headless) | openai-codex-device | agent.db (OAuth-first) | https://chatgpt.com | https://chatgpt.com | manual |
| 65 | opencode-zen | OpenCode Zen | opencode-zen | env `OPENCODE_API_KEY` | https://opencode.ai/auth | https://opencode.ai | manual |
| 66 | parallel | Parallel | parallel | env `PARALLEL_API_KEY` | https://platform.parallel.ai/settings?tab=api-keys | https://platform.parallel.ai | manual |
| 67 | perplexity | Perplexity (Pro/Max) | perplexity | env `PERPLEXITY_API_KEY` | https://www.perplexity.ai/settings/api | https://www.perplexity.ai | manual |
| 68 | qianfan | Qianfan | qianfan | env `QIANFAN_API_KEY` | https://console.bce.baidu.com/qianfan/ais/console/apiKey | https://console.bce.baidu.com | manual |
| 69 | qwen-portal | Qwen Portal | qwen-portal | env `QWEN_OAUTH_TOKEN` | https://chat.qwen.ai | https://chat.qwen.ai | manual |
| 70 | sakana | Sakana AI | sakana | env `SAKANA_API_KEY` | https://console.sakana.ai/api-keys | https://console.sakana.ai | manual |
| 71 | siliconflow | SiliconFlow | siliconflow | env `SILICONFLOW_API_KEY` | https://cloud.siliconflow.com/account/ak | https://cloud.siliconflow.com | manual |
| 72 | siliconflow-cn | SiliconFlow (China) | siliconflow-cn | env `SILICONFLOW_CN_API_KEY` | https://cloud.siliconflow.cn/account/ak | https://cloud.siliconflow.cn | manual |
| 73 | singularityapi-dev | SingularityAPI | singularityapi-dev | env `SINGULARITYAPI_DEV_API_KEY` | https://app.singularityapi.dev | https://app.singularityapi.dev | manual |
| 74 | singularityapi-tech | SingularityAPI Reserved Lanes | singularityapi-tech | env `SINGULARITYAPI_TECH_API_KEY` | https://app.singularityapi.tech | https://app.singularityapi.tech/compute/billing | manual |
| 75 | snowflake | Snowflake Cortex | snowflake | env `SNOWFLAKE_PAT` | https://app.snowflake.com | https://app.snowflake.com | manual |
| 76 | stencil | Stencil (invite only) | stencil | env `STENCIL_API_KEY` | https://auth.stencil.so | https://auth.stencil.so | manual |
| 77 | stepfun | StepFun | stepfun | env `STEPFUN_API_KEY` | https://platform.stepfun.ai/interface-key | https://platform.stepfun.ai | manual |
| 78 | synthetic | Synthetic | synthetic | env `SYNTHETIC_API_KEY` | https://dev.synthetic.new/docs/api/overview | https://dev.synthetic.new | manual |
| 79 | tavily | Tavily | tavily | env `TAVILY_API_KEY` | https://app.tavily.com/home | https://app.tavily.com | manual |
| 80 | together | Together | together | env `TOGETHER_API_KEY` | https://api.together.xyz/settings/api-keys | https://api.together.xyz/settings | manual |
| 81 | umans | Umans AI Coding Plan | umans | env `UMANS_AI_CODING_PLAN_API_KEY` | https://app.umans.ai/billing | https://app.umans.ai | manual |
| 82 | venice | Venice | venice | env `VENICE_API_KEY` | https://venice.ai/settings/api | https://venice.ai/settings | manual |
| 83 | vercel-ai-gateway | Vercel AI Gateway | vercel-ai-gateway | env `AI_GATEWAY_API_KEY` | https://vercel.com/ai-gateway | https://vercel.com/ai-gateway | manual |
| 84 | vllm | vLLM (local) | vllm | env `VLLM_API_KEY` | https://docs.vllm.ai | https://docs.vllm.ai | manual |
| 85 | wafer-serverless | Wafer Serverless | wafer-serverless | env `WAFER_SERVERLESS_API_KEY` | https://app.wafer.ai | https://app.wafer.ai/usage | manual |
| 86 | web | Web search engines | web | keyless (no env) | https://ohmypi.xyz | https://ohmypi.xyz | manual |
| 87 | xai | xAI API | xai | env `XAI_API_KEY` | https://console.x.ai | https://console.x.ai | manual |
| 88 | xai-oauth | xAI Grok OAuth (SuperGrok/X Premium+) | xai-oauth | env `XAI_OAUTH_TOKEN` | https://x.ai | https://x.ai | manual |
| 89 | xiaomi | Xiaomi MiMo | xiaomi | env `XIAOMI_API_KEY` | https://platform.xiaomimimo.com | https://platform.xiaomimimo.com | manual |
| 90 | xiaomi-token-plan-ams | Xiaomi Token Plan (Europe) | xiaomi-token-plan-ams | env `XIAOMI_TOKEN_PLAN_AMS_API_KEY` | https://platform.xiaomimimo.com/console/plan-manage | https://platform.xiaomimimo.com | manual |
| 91 | xiaomi-token-plan-cn | Xiaomi Token Plan (China) | xiaomi-token-plan-cn | env `XIAOMI_TOKEN_PLAN_CN_API_KEY` | https://platform.xiaomimimo.com/console/plan-manage | https://platform.xiaomimimo.com | manual |
| 92 | xiaomi-token-plan-sgp | Xiaomi Token Plan (Singapore) | xiaomi-token-plan-sgp | env `XIAOMI_TOKEN_PLAN_SGP_API_KEY` | https://platform.xiaomimimo.com/console/plan-manage | https://platform.xiaomimimo.com | manual |
| 93 | yolo-auto | Yolo-Auto | yolo-auto | env `YOLO_AUTO_API_KEY` | https://yolo-auto.com/app | https://yolo-auto.com | manual |
| 94 | zai-coding-plan | Z.AI (GLM Coding Plan - Sign in) | zai-coding-plan | agent.db (OAuth-first) | https://z.ai | https://z.ai | manual |
| 95 | zenmux | ZenMux | zenmux | env `ZENMUX_API_KEY` | https://zenmux.ai/settings/keys | https://zenmux.ai | manual |
| 96 | zhipu-coding-plan | Zhipu Coding Plan | zhipu-coding-plan | env `ZHIPU_API_KEY` | https://bigmodel.cn/coding-plan/personal/overview | https://bigmodel.cn | manual |

Notes:

- Mesh lanes (2026-10-13): `deepseek`/`groq`/`cerebras`/`mistral` are roster
  providers whose PAYG keys live in `~/tools/key-drop/collected/`;
  `orcarouter` is the mesh router (API base `https://api.orcarouter.ai/v1`
  per the key-drop docs, omp id unverified); `cloudflare` is Workers AI
  (token dashboard template, key file carries `account_id:token`; the roster
  has only `cloudflare-ai-gateway`, a different product).
- `jev` is judge-kind infrastructure, not a chat model: omp wires
  `typesafe/jev-latest` (and `jev-preview`) to `modelRoles.judge` only — on
  fresh machines add the role with
  `mar omp-sync --set-role judge=typesafe/jev-latest`.
- `cursor-grok` is one subscription line in the problem statement; kept as one
  catalog entry. omp's `cursor` provider is OAuth-first; the `crsr_` token is
  written to the agent dir's `.env` (documented env fallback), which omp picks
  up when no OAuth session is stored.
- `codex` = ChatGPT/Codex OAuth subscription in omp (`openai-codex`); the
  `openai` entry is the platform PAYG key product. `openai-codex-device` is
  omp's headless device-flow variant of the same subscription.
- `zai` (api-key route) and `zai-coding-plan` (browser OAuth sign-in) are the
  same GLM coding plan; `zhipu-coding-plan` is the domestic Zhipu endpoint.
- omp's env tables mention `coralbricks` (`CORAL_API_KEY`), but this omp
  build's roster has no such auth policy — not cataloged.
- Credential routes: `env VAR` → `mar omp-sync` writes `VAR=<key>` to
  `<agent_dir>/.env` (omp loads it as environment fallback); agent.db rows are
  login-style and rank above env in omp's credential precedence; OAuth-first
  providers with no env route are reached via omp `/login <id>`.
- `limits_kind` is `api` only where a documented endpoint exists
  (openrouter `/api/v1/credits`; gemini key-liveness via the models list);
  every other provider is `manual` — `mar limits` points at `limits_url`.
- Default models are the user's choice at `mar add` time; the catalog never
  invents one.
