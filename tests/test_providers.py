"""Tests for the static provider catalog (M2).

The catalog must mirror docs/providers.md exactly: 14 providers, fixed order,
verification flags confirmed against omp's provider roster (2026-10-10) and
live LAN llama.cpp probes (2026-10-23 through 2026-11-02). Validation rules
are exercised by tampering with copies of catalog entries (the shipped
CATALOG is frozen).
"""

import dataclasses

import pytest

from mar.providers import CATALOG, Provider, get, ids, unverified, validate

EXPECTED_IDS = (
    "cursor-grok",
    "codex",
    "claude",
    "cline",
    "opencode",
    "zai",
    "openrouter",
    "antigravity",
    "copilot",
    "jev",
    "gemini",
    "commandcode",
    "deepseek",
    "groq",
    "cerebras",
    "mistral",
    "orcarouter",
    "cloudflare",
    "abliteration",
    "aiand",
    "aimlapi",
    "alibaba-coding-plan",
    "alibaba-token-plan",
    "amazon-bedrock",
    "apple",
    "azure",
    "baseten",
    "bedrock-mantle",
    "charm-hyper",
    "cloudflare-ai-gateway",
    "coreweave",
    "deepinfra",
    "devin",
    "exa",
    "factory-droid",
    "firepass",
    "fireworks",
    "gitlab-duo",
    "gitlab-duo-agent",
    "gmi-cloud",
    "google-gemini-cli",
    "google-vertex",
    "helmcode",
    "huggingface",
    "kagi",
    "kilo",
    "kimi-code",
    "litellm",
    "llama-cpp",
    "lm-studio",
    "local",
    "meta",
    "minimax",
    "minimax-code",
    "minimax-code-cn",
    "moonshot",
    "muse-code",
    "nanogpt",
    "novita",
    "nvidia",
    "ollama",
    "ollama-cloud",
    "openai",
    "openai-codex-device",
    "opencode-zen",
    "parallel",
    "perplexity",
    "qianfan",
    "qwen-portal",
    "sakana",
    "siliconflow",
    "siliconflow-cn",
    "singularityapi-dev",
    "singularityapi-tech",
    "snowflake",
    "stencil",
    "stepfun",
    "synthetic",
    "tavily",
    "together",
    "umans",
    "venice",
    "vercel-ai-gateway",
    "vllm",
    "wafer-serverless",
    "web",
    "xai",
    "xai-oauth",
    "xiaomi",
    "xiaomi-token-plan-ams",
    "xiaomi-token-plan-cn",
    "xiaomi-token-plan-sgp",
    "yolo-auto",
    "zai-coding-plan",
    "zenmux",
    "zhipu-coding-plan",
    "strata",
    "qwen",
)

UNVERIFIED_IDS: set[str] = {"cloudflare", "orcarouter"}

VERIFIED_IDS = {"abliteration", "aiand", "aimlapi", "alibaba-coding-plan", "alibaba-token-plan", "amazon-bedrock", "antigravity", "apple", "azure", "baseten", "bedrock-mantle", "cerebras", "charm-hyper", "claude", "cline", "cloudflare-ai-gateway", "codex", "commandcode", "copilot", "coreweave", "cursor-grok", "deepinfra", "deepseek", "devin", "exa", "factory-droid", "firepass", "fireworks", "gemini", "gitlab-duo", "gitlab-duo-agent", "gmi-cloud", "google-gemini-cli", "google-vertex", "groq", "helmcode", "huggingface", "jev", "kagi", "kilo", "kimi-code", "litellm", "llama-cpp", "lm-studio", "local", "meta", "minimax", "minimax-code", "minimax-code-cn", "mistral", "moonshot", "muse-code", "nanogpt", "novita", "nvidia", "ollama", "ollama-cloud", "openai", "openai-codex-device", "opencode", "opencode-zen", "openrouter", "parallel", "perplexity", "qianfan", "qwen", "qwen-portal", "sakana", "siliconflow", "siliconflow-cn", "singularityapi-dev", "singularityapi-tech", "snowflake", "stencil", "stepfun", "strata", "synthetic", "tavily", "together", "umans", "venice", "vercel-ai-gateway", "vllm", "wafer-serverless", "web", "xai", "xai-oauth", "xiaomi", "xiaomi-token-plan-ams", "xiaomi-token-plan-cn", "xiaomi-token-plan-sgp", "yolo-auto", "zai", "zai-coding-plan", "zenmux", "zhipu-coding-plan"}

OMP_IDS = {
    "cursor-grok": "cursor",
    "codex": "openai-codex",
    "claude": "anthropic",
    "cline": "cline-pass",
    "opencode": "opencode-go",
    "zai": "zai",
    "openrouter": "openrouter",
    "antigravity": "google-antigravity",
    "copilot": "github-copilot",
    "jev": "typesafe",
    "gemini": "google",
    "commandcode": "commandcode",
    "deepseek": "deepseek",
    "groq": "groq",
    "cerebras": "cerebras",
    "mistral": "mistral",
    "orcarouter": "orcarouter",
    "cloudflare": "cloudflare",
    "abliteration": "abliteration",
    "aiand": "aiand",
    "aimlapi": "aimlapi",
    "alibaba-coding-plan": "alibaba-coding-plan",
    "alibaba-token-plan": "alibaba-token-plan",
    "amazon-bedrock": "amazon-bedrock",
    "apple": "apple",
    "azure": "azure",
    "baseten": "baseten",
    "bedrock-mantle": "bedrock-mantle",
    "charm-hyper": "charm-hyper",
    "cloudflare-ai-gateway": "cloudflare-ai-gateway",
    "coreweave": "coreweave",
    "deepinfra": "deepinfra",
    "devin": "devin",
    "exa": "exa",
    "factory-droid": "factory-droid",
    "firepass": "firepass",
    "fireworks": "fireworks",
    "gitlab-duo": "gitlab-duo",
    "gitlab-duo-agent": "gitlab-duo-agent",
    "gmi-cloud": "gmi-cloud",
    "google-gemini-cli": "google-gemini-cli",
    "google-vertex": "google-vertex",
    "helmcode": "helmcode",
    "huggingface": "huggingface",
    "kagi": "kagi",
    "kilo": "kilo",
    "kimi-code": "kimi-code",
    "litellm": "litellm",
    "llama-cpp": "llama.cpp",
    "lm-studio": "lm-studio",
    "local": "local",
    "meta": "meta",
    "minimax": "minimax",
    "minimax-code": "minimax-code",
    "minimax-code-cn": "minimax-code-cn",
    "moonshot": "moonshot",
    "muse-code": "muse-code",
    "nanogpt": "nanogpt",
    "novita": "novita",
    "nvidia": "nvidia",
    "ollama": "ollama",
    "ollama-cloud": "ollama-cloud",
    "openai": "openai",
    "openai-codex-device": "openai-codex-device",
    "opencode-zen": "opencode-zen",
    "parallel": "parallel",
    "perplexity": "perplexity",
    "qianfan": "qianfan",
    "qwen-portal": "qwen-portal",
    "sakana": "sakana",
    "siliconflow": "siliconflow",
    "siliconflow-cn": "siliconflow-cn",
    "singularityapi-dev": "singularityapi-dev",
    "singularityapi-tech": "singularityapi-tech",
    "snowflake": "snowflake",
    "stencil": "stencil",
    "stepfun": "stepfun",
    "synthetic": "synthetic",
    "tavily": "tavily",
    "together": "together",
    "umans": "umans",
    "venice": "venice",
    "vercel-ai-gateway": "vercel-ai-gateway",
    "vllm": "vllm",
    "wafer-serverless": "wafer-serverless",
    "web": "web",
    "xai": "xai",
    "xai-oauth": "xai-oauth",
    "xiaomi": "xiaomi",
    "xiaomi-token-plan-ams": "xiaomi-token-plan-ams",
    "xiaomi-token-plan-cn": "xiaomi-token-plan-cn",
    "xiaomi-token-plan-sgp": "xiaomi-token-plan-sgp",
    "yolo-auto": "yolo-auto",
    "zai-coding-plan": "zai-coding-plan",
    "zenmux": "zenmux",
    "zhipu-coding-plan": "zhipu-coding-plan",
    "strata": "llama.cpp",
    "qwen": "lm-studio",
}

ENV_VARS = {
    "cursor-grok": "CURSOR_ACCESS_TOKEN",
    "codex": "OPENAI_CODEX_OAUTH_TOKEN",
    "claude": "ANTHROPIC_API_KEY",
    "cline": "",
    "opencode": "",
    "zai": "",
    "openrouter": "",
    "antigravity": "",
    "copilot": "COPILOT_GITHUB_TOKEN",
    "jev": "TYPESAFE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "commandcode": "COMMAND_CODE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "orcarouter": "ORCAROUTER_API_KEY",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
    "abliteration": "ABLITERATION_API_KEY",
    "aiand": "AIAND_API_KEY",
    "aimlapi": "AIMLAPI_API_KEY",
    "alibaba-coding-plan": "ALIBABA_CODING_PLAN_API_KEY",
    "alibaba-token-plan": "ALIBABA_TOKEN_PLAN_API_KEY",
    "amazon-bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "apple": "",
    "azure": "AZURE_OPENAI_API_KEY",
    "baseten": "BASETEN_API_KEY",
    "bedrock-mantle": "AWS_BEARER_TOKEN_BEDROCK",
    "charm-hyper": "CHARM_HYPER_API_KEY",
    "cloudflare-ai-gateway": "CLOUDFLARE_AI_GATEWAY_API_KEY",
    "coreweave": "COREWEAVE_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "devin": "DEVIN_API_KEY",
    "exa": "EXA_API_KEY",
    "factory-droid": "",
    "firepass": "FIREPASS_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "gitlab-duo": "GITLAB_TOKEN",
    "gitlab-duo-agent": "GITLAB_TOKEN",
    "gmi-cloud": "GMI_API_KEY",
    "google-gemini-cli": "",
    "google-vertex": "GOOGLE_CLOUD_API_KEY",
    "helmcode": "HELMCODE_API_KEY",
    "huggingface": "HUGGINGFACE_HUB_TOKEN",
    "kagi": "KAGI_API_KEY",
    "kilo": "KILO_API_KEY",
    "kimi-code": "KIMI_API_KEY",
    "litellm": "LITELLM_API_KEY",
    "llama-cpp": "LLAMA_CPP_API_KEY",
    "lm-studio": "LM_STUDIO_API_KEY",
    "local": "",
    "meta": "MODEL_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-code": "MINIMAX_CODE_API_KEY",
    "minimax-code-cn": "MINIMAX_CODE_CN_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "muse-code": "",
    "nanogpt": "NANO_GPT_API_KEY",
    "novita": "NOVITA_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "ollama-cloud": "OLLAMA_CLOUD_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex-device": "",
    "opencode-zen": "OPENCODE_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "qianfan": "QIANFAN_API_KEY",
    "qwen-portal": "QWEN_OAUTH_TOKEN",
    "sakana": "SAKANA_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "siliconflow-cn": "SILICONFLOW_CN_API_KEY",
    "singularityapi-dev": "SINGULARITYAPI_DEV_API_KEY",
    "singularityapi-tech": "SINGULARITYAPI_TECH_API_KEY",
    "snowflake": "SNOWFLAKE_PAT",
    "stencil": "STENCIL_API_KEY",
    "stepfun": "STEPFUN_API_KEY",
    "synthetic": "SYNTHETIC_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "together": "TOGETHER_API_KEY",
    "umans": "UMANS_AI_CODING_PLAN_API_KEY",
    "venice": "VENICE_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "vllm": "VLLM_API_KEY",
    "wafer-serverless": "WAFER_SERVERLESS_API_KEY",
    "web": "",
    "xai": "XAI_API_KEY",
    "xai-oauth": "XAI_OAUTH_TOKEN",
    "xiaomi": "XIAOMI_API_KEY",
    "xiaomi-token-plan-ams": "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
    "xiaomi-token-plan-cn": "XIAOMI_TOKEN_PLAN_CN_API_KEY",
    "xiaomi-token-plan-sgp": "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
    "yolo-auto": "YOLO_AUTO_API_KEY",
    "zai-coding-plan": "",
    "zenmux": "ZENMUX_API_KEY",
    "zhipu-coding-plan": "ZHIPU_API_KEY",
    "strata": "",
    "qwen": "",
}

OAUTH_ENV_VARS = {
    "cursor-grok": "",
    "codex": "OPENAI_CODEX_OAUTH_TOKEN",
    "claude": "ANTHROPIC_OAUTH_TOKEN",
    "cline": "",
    "opencode": "",
    "zai": "",
    "openrouter": "",
    "antigravity": "",
    "copilot": "COPILOT_GITHUB_TOKEN",
    "jev": "",
    "gemini": "",
    "commandcode": "",
    "deepseek": "",
    "groq": "",
    "cerebras": "",
    "mistral": "",
    "orcarouter": "",
    "cloudflare": "",
    "abliteration": "",
    "aiand": "",
    "aimlapi": "",
    "alibaba-coding-plan": "",
    "alibaba-token-plan": "",
    "amazon-bedrock": "",
    "apple": "",
    "azure": "",
    "baseten": "",
    "bedrock-mantle": "",
    "charm-hyper": "",
    "cloudflare-ai-gateway": "",
    "coreweave": "",
    "deepinfra": "",
    "devin": "",
    "exa": "",
    "factory-droid": "",
    "firepass": "",
    "fireworks": "",
    "gitlab-duo": "",
    "gitlab-duo-agent": "",
    "gmi-cloud": "",
    "google-gemini-cli": "",
    "google-vertex": "",
    "helmcode": "",
    "huggingface": "",
    "kagi": "",
    "kilo": "",
    "kimi-code": "",
    "litellm": "",
    "llama-cpp": "",
    "lm-studio": "",
    "local": "",
    "meta": "",
    "minimax": "",
    "minimax-code": "",
    "minimax-code-cn": "",
    "moonshot": "",
    "muse-code": "",
    "nanogpt": "",
    "novita": "",
    "nvidia": "",
    "ollama": "",
    "ollama-cloud": "",
    "openai": "",
    "openai-codex-device": "",
    "opencode-zen": "",
    "parallel": "",
    "perplexity": "",
    "qianfan": "",
    "qwen-portal": "",
    "sakana": "",
    "siliconflow": "",
    "siliconflow-cn": "",
    "singularityapi-dev": "",
    "singularityapi-tech": "",
    "snowflake": "",
    "stencil": "",
    "stepfun": "",
    "synthetic": "",
    "tavily": "",
    "together": "",
    "umans": "",
    "venice": "",
    "vercel-ai-gateway": "",
    "vllm": "",
    "wafer-serverless": "",
    "web": "",
    "xai": "",
    "xai-oauth": "XAI_OAUTH_TOKEN",
    "xiaomi": "",
    "xiaomi-token-plan-ams": "",
    "xiaomi-token-plan-cn": "",
    "xiaomi-token-plan-sgp": "",
    "yolo-auto": "",
    "zai-coding-plan": "",
    "zenmux": "",
    "zhipu-coding-plan": "",
    "strata": "",
    "qwen": "",
}

EXPORT_KEY_ENV_VARS = {
    "cursor-grok": "CURSOR_ACCESS_TOKEN",
    "codex": "",
    "claude": "ANTHROPIC_API_KEY",
    "cline": "CLINE_API_KEY",
    "opencode": "OPENCODE_API_KEY",
    "zai": "ZAI_API_KEY",
    "openrouter": "OPENROUTER_API_KEY",
    "antigravity": "",
    "copilot": "COPILOT_GITHUB_TOKEN",
    "jev": "TYPESAFE_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "commandcode": "COMMAND_CODE_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "groq": "GROQ_API_KEY",
    "cerebras": "CEREBRAS_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "orcarouter": "ORCAROUTER_API_KEY",
    "cloudflare": "CLOUDFLARE_API_TOKEN",
    "abliteration": "ABLITERATION_API_KEY",
    "aiand": "AIAND_API_KEY",
    "aimlapi": "AIMLAPI_API_KEY",
    "alibaba-coding-plan": "ALIBABA_CODING_PLAN_API_KEY",
    "alibaba-token-plan": "ALIBABA_TOKEN_PLAN_API_KEY",
    "amazon-bedrock": "AWS_BEARER_TOKEN_BEDROCK",
    "apple": "",
    "azure": "AZURE_OPENAI_API_KEY",
    "baseten": "BASETEN_API_KEY",
    "bedrock-mantle": "AWS_BEARER_TOKEN_BEDROCK",
    "charm-hyper": "CHARM_HYPER_API_KEY",
    "cloudflare-ai-gateway": "CLOUDFLARE_AI_GATEWAY_API_KEY",
    "coreweave": "COREWEAVE_API_KEY",
    "deepinfra": "DEEPINFRA_API_KEY",
    "devin": "DEVIN_API_KEY",
    "exa": "EXA_API_KEY",
    "factory-droid": "",
    "firepass": "FIREPASS_API_KEY",
    "fireworks": "FIREWORKS_API_KEY",
    "gitlab-duo": "GITLAB_TOKEN",
    "gitlab-duo-agent": "GITLAB_TOKEN",
    "gmi-cloud": "GMI_API_KEY",
    "google-gemini-cli": "",
    "google-vertex": "GOOGLE_CLOUD_API_KEY",
    "helmcode": "HELMCODE_API_KEY",
    "huggingface": "HUGGINGFACE_HUB_TOKEN",
    "kagi": "KAGI_API_KEY",
    "kilo": "KILO_API_KEY",
    "kimi-code": "KIMI_API_KEY",
    "litellm": "LITELLM_API_KEY",
    "llama-cpp": "LLAMA_CPP_API_KEY",
    "lm-studio": "LM_STUDIO_API_KEY",
    "local": "",
    "meta": "MODEL_API_KEY",
    "minimax": "MINIMAX_API_KEY",
    "minimax-code": "MINIMAX_CODE_API_KEY",
    "minimax-code-cn": "MINIMAX_CODE_CN_API_KEY",
    "moonshot": "MOONSHOT_API_KEY",
    "muse-code": "",
    "nanogpt": "NANO_GPT_API_KEY",
    "novita": "NOVITA_API_KEY",
    "nvidia": "NVIDIA_API_KEY",
    "ollama": "OLLAMA_API_KEY",
    "ollama-cloud": "OLLAMA_CLOUD_API_KEY",
    "openai": "OPENAI_API_KEY",
    "openai-codex-device": "",
    "opencode-zen": "OPENCODE_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "perplexity": "PERPLEXITY_API_KEY",
    "qianfan": "QIANFAN_API_KEY",
    "qwen-portal": "QWEN_OAUTH_TOKEN",
    "sakana": "SAKANA_API_KEY",
    "siliconflow": "SILICONFLOW_API_KEY",
    "siliconflow-cn": "SILICONFLOW_CN_API_KEY",
    "singularityapi-dev": "SINGULARITYAPI_DEV_API_KEY",
    "singularityapi-tech": "SINGULARITYAPI_TECH_API_KEY",
    "snowflake": "SNOWFLAKE_PAT",
    "stencil": "STENCIL_API_KEY",
    "stepfun": "STEPFUN_API_KEY",
    "synthetic": "SYNTHETIC_API_KEY",
    "tavily": "TAVILY_API_KEY",
    "together": "TOGETHER_API_KEY",
    "umans": "UMANS_AI_CODING_PLAN_API_KEY",
    "venice": "VENICE_API_KEY",
    "vercel-ai-gateway": "AI_GATEWAY_API_KEY",
    "vllm": "VLLM_API_KEY",
    "wafer-serverless": "WAFER_SERVERLESS_API_KEY",
    "web": "",
    "xai": "XAI_API_KEY",
    "xai-oauth": "XAI_OAUTH_TOKEN",
    "xiaomi": "XIAOMI_API_KEY",
    "xiaomi-token-plan-ams": "XIAOMI_TOKEN_PLAN_AMS_API_KEY",
    "xiaomi-token-plan-cn": "XIAOMI_TOKEN_PLAN_CN_API_KEY",
    "xiaomi-token-plan-sgp": "XIAOMI_TOKEN_PLAN_SGP_API_KEY",
    "yolo-auto": "YOLO_AUTO_API_KEY",
    "zai-coding-plan": "",
    "zenmux": "ZENMUX_API_KEY",
    "zhipu-coding-plan": "ZHIPU_API_KEY",
    "strata": "",
    "qwen": "",
}

URL_FIELDS = ("key_url", "limits_url", "signup_url")

# Self-hosted endpoints are plain HTTP on the LAN: (base_url, env route).
BASE_URLS = {
    "strata": ("http://deepseek.nj02.gurov.us/v1", "LLAMA_CPP_BASE_URL"),
    "qwen": ("http://qwen2.nj02.gurov.us/v1", "LM_STUDIO_BASE_URL"),
}

AUTH_METHODS = {
    "cursor-grok": ("api_key",),
    "codex": ("api_key",),
    "claude": ("api_key",),
    "cline": ("api_key",),
    "opencode": ("api_key",),
    "zai": ("api_key",),
    "openrouter": ("api_key",),
    "antigravity": ("api_key",),
    "copilot": ("api_key",),
    "jev": ("api_key",),
    "gemini": ("api_key",),
    "commandcode": ("api_key",),
    "deepseek": ("api_key",),
    "groq": ("api_key",),
    "cerebras": ("api_key",),
    "mistral": ("api_key",),
    "orcarouter": ("api_key",),
    "cloudflare": ("api_key",),
    "abliteration": ("api_key",),
    "aiand": ("api_key",),
    "aimlapi": ("api_key",),
    "alibaba-coding-plan": ("api_key",),
    "alibaba-token-plan": ("api_key",),
    "amazon-bedrock": ("api_key",),
    "apple": ("api_key",),
    "azure": ("api_key",),
    "baseten": ("api_key",),
    "bedrock-mantle": ("api_key",),
    "charm-hyper": ("api_key",),
    "cloudflare-ai-gateway": ("api_key",),
    "coreweave": ("api_key",),
    "deepinfra": ("api_key",),
    "devin": ("api_key",),
    "exa": ("api_key",),
    "factory-droid": ("api_key",),
    "firepass": ("api_key",),
    "fireworks": ("api_key",),
    "gitlab-duo": ("api_key",),
    "gitlab-duo-agent": ("api_key",),
    "gmi-cloud": ("api_key",),
    "google-gemini-cli": ("api_key",),
    "google-vertex": ("api_key",),
    "helmcode": ("api_key",),
    "huggingface": ("api_key",),
    "kagi": ("api_key",),
    "kilo": ("api_key",),
    "kimi-code": ("api_key",),
    "litellm": ("api_key",),
    "llama-cpp": ("api_key",),
    "lm-studio": ("api_key",),
    "local": ("api_key",),
    "meta": ("api_key",),
    "minimax": ("api_key",),
    "minimax-code": ("api_key",),
    "minimax-code-cn": ("api_key",),
    "moonshot": ("api_key",),
    "muse-code": ("api_key",),
    "nanogpt": ("api_key",),
    "novita": ("api_key",),
    "nvidia": ("api_key",),
    "ollama": ("api_key",),
    "ollama-cloud": ("api_key",),
    "openai": ("api_key",),
    "openai-codex-device": ("api_key",),
    "opencode-zen": ("api_key",),
    "parallel": ("api_key",),
    "perplexity": ("api_key",),
    "qianfan": ("api_key",),
    "qwen-portal": ("api_key",),
    "sakana": ("api_key",),
    "siliconflow": ("api_key",),
    "siliconflow-cn": ("api_key",),
    "singularityapi-dev": ("api_key",),
    "singularityapi-tech": ("api_key",),
    "snowflake": ("api_key",),
    "stencil": ("api_key",),
    "stepfun": ("api_key",),
    "synthetic": ("api_key",),
    "tavily": ("api_key",),
    "together": ("api_key",),
    "umans": ("api_key",),
    "venice": ("api_key",),
    "vercel-ai-gateway": ("api_key",),
    "vllm": ("api_key",),
    "wafer-serverless": ("api_key",),
    "web": ("api_key",),
    "xai": ("api_key",),
    "xai-oauth": ("api_key",),
    "xiaomi": ("api_key",),
    "xiaomi-token-plan-ams": ("api_key",),
    "xiaomi-token-plan-cn": ("api_key",),
    "xiaomi-token-plan-sgp": ("api_key",),
    "yolo-auto": ("api_key",),
    "zai-coding-plan": ("api_key",),
    "zenmux": ("api_key",),
    "zhipu-coding-plan": ("api_key",),
    "strata": ("none",),
    "qwen": ("none",),
}


def test_ids_returns_exact_catalog_in_order():
    assert ids() == EXPECTED_IDS
    assert tuple(p.id for p in CATALOG) == EXPECTED_IDS
    assert len(CATALOG) == 98


def test_get_returns_matching_provider():
    zai = get("zai")
    assert isinstance(zai, Provider)
    assert zai.id == "zai"
    assert zai.verified is True
    assert zai.omp_provider_id == "zai"


def test_get_unknown_id_raises_key_error_listing_valid_ids():
    with pytest.raises(KeyError) as excinfo:
        get("does-not-exist")
    message = str(excinfo.value)
    assert "does-not-exist" in message
    for known in EXPECTED_IDS:
        assert known in message


@pytest.mark.parametrize("provider", CATALOG, ids=lambda p: p.id)
def test_every_url_field_is_https(provider):
    for field in URL_FIELDS:
        url = getattr(provider, field)
        assert not url or url.startswith("https://"), f"{provider.id}.{field} = {url!r}"


def test_validate_shipped_catalog_is_healthy():
    assert validate() == []


def test_unverified_is_exactly_the_two_unconfirmed():
    assert tuple(p.id for p in unverified()) == ("orcarouter", "cloudflare")


def test_verified_are_all_roster_confirmed_providers():
    assert {p.id for p in CATALOG if p.verified} == VERIFIED_IDS


@pytest.mark.parametrize(("provider_id", "omp_id"), sorted(OMP_IDS.items()))
def test_omp_ids_match_official_roster(provider_id, omp_id):
    assert get(provider_id).omp_provider_id == omp_id


@pytest.mark.parametrize(("provider_id", "env_var"), sorted(ENV_VARS.items()))
def test_omp_env_vars_match_official_roster(provider_id, env_var):
    assert get(provider_id).omp_env_var == env_var


@pytest.mark.parametrize(("provider_id", "env_var"), sorted(OAUTH_ENV_VARS.items()))
def test_export_oauth_env_vars_match_official_roster(provider_id, env_var):
    assert get(provider_id).export_oauth_env_var == env_var


@pytest.mark.parametrize(("provider_id", "env_var"), sorted(EXPORT_KEY_ENV_VARS.items()))
def test_export_key_env_vars_match_official_roster(provider_id, env_var):
    assert get(provider_id).export_env_var == env_var


def test_limits_kind_api_providers_include_both_local_endpoints():
    api_providers = [p for p in CATALOG if p.limits_kind == "api"]
    assert [p.id for p in api_providers] == [
        "openrouter",
        "gemini",
        "strata",
        "qwen",
    ]
    manual = [p.id for p in CATALOG if p.limits_kind == "manual"]
    assert "gemini" not in manual


@pytest.mark.parametrize("provider", CATALOG, ids=lambda p: p.id)
def test_v1_defaults(provider):
    assert provider.auth_methods == AUTH_METHODS[provider.id]
    assert provider.default_model is None


@pytest.mark.parametrize(("provider_id", "base_url"), sorted(BASE_URLS.items()))
def test_local_endpoint_base_urls_and_env_routes(provider_id, base_url):
    provider = get(provider_id)
    assert (provider.base_url, provider.omp_base_url_env_var) == base_url


def test_only_self_hosted_entries_carry_a_base_url():
    assert [p.id for p in CATALOG if p.base_url] == ["strata", "qwen"]


def test_strata_is_keyless_with_no_web_pages():
    strata = get("strata")
    assert strata.auth_methods == ("none",)
    assert strata.verified is True
    assert strata.omp_provider_id == "llama.cpp"
    assert strata.limits_kind == "api"
    assert (strata.key_url, strata.limits_url, strata.signup_url) == ("", "", "")
    assert strata.export_env_var == "" and strata.omp_env_var == ""


def test_qwen_is_keyless_on_the_lm_studio_route():
    qwen = get("qwen")
    assert qwen.auth_methods == ("none",)
    assert qwen.verified is True
    assert qwen.omp_provider_id == "lm-studio"
    assert qwen.limits_kind == "api"
    assert (qwen.key_url, qwen.limits_url, qwen.signup_url) == ("", "", "")
    assert qwen.export_env_var == "" and qwen.omp_env_var == ""
    assert qwen.omp_base_url_env_var == "LM_STUDIO_BASE_URL"


def test_self_hosted_entries_record_the_server_context_window():
    assert [(p.id, p.context_window) for p in CATALOG if p.context_window] == [
        ("strata", 1048576),
        ("qwen", 1048576),
    ]


def test_only_qwen_needs_an_explicit_omp_model_declaration():
    # omp's llama.cpp route reads n_ctx from /v1/models; its lm-studio route
    # cannot, so qwen's window must be declared in models.yml.
    assert [(p.id, p.omp_models_api) for p in CATALOG if p.omp_models_api] == [
        ("qwen", "openai-completions")
    ]


BASE = CATALOG[0]

TAMPER_CASES = [
    pytest.param(
        dataclasses.replace(BASE, id="codex"),
        "duplicate provider id 'codex'",
        id="duplicate-id",
    ),
    pytest.param(
        dataclasses.replace(BASE, id="Bad_Id"),
        "id 'Bad_Id' does not match",
        id="bad-id-shape",
    ),
    pytest.param(
        dataclasses.replace(BASE, key_url="http://insecure.example"),
        "key_url must start with https://",
        id="http-key-url",
    ),
    pytest.param(
        dataclasses.replace(BASE, limits_kind="psychic"),
        "limits_kind 'psychic' not in",
        id="bad-limits-kind",
    ),
    pytest.param(
        dataclasses.replace(BASE, verified=True, omp_provider_id=""),
        "verified=True but omp_provider_id is empty",
        id="verified-empty-omp-id",
    ),
    pytest.param(
        dataclasses.replace(BASE, verified=True, limits_url=""),
        "verified=True but limits_url is empty",
        id="verified-empty-limits-url",
    ),
    pytest.param(
        dataclasses.replace(BASE, omp_provider_id="Zai Main"),
        "omp_provider_id 'Zai Main' does not match",
        id="bad-omp-id-shape",
    ),
    pytest.param(
        dataclasses.replace(BASE, omp_env_var="not an env var"),
        "omp_env_var 'not an env var' does not match",
        id="bad-env-var-shape",
    ),
    pytest.param(
        dataclasses.replace(BASE, base_url="ftp://localhost:8080/v1"),
        "base_url must start with http:// or https://",
        id="bad-base-url-scheme",
    ),
    pytest.param(
        dataclasses.replace(BASE, base_url="http://localhost:8080/v1"),
        "base_url requires omp_base_url_env_var",
        id="base-url-without-env-route",
    ),
    pytest.param(
        dataclasses.replace(BASE, omp_base_url_env_var="llama_cpp_base_url"),
        "omp_base_url_env_var 'llama_cpp_base_url' does not match",
        id="bad-base-url-env-var-shape",
    ),
    pytest.param(
        dataclasses.replace(BASE, context_window=-1),
        "context_window must be positive",
        id="negative-context-window",
    ),
    pytest.param(
        dataclasses.replace(BASE, context_window=128000),
        "context_window requires a base_url",
        id="context-window-without-base-url",
    ),
    pytest.param(
        dataclasses.replace(BASE, omp_models_api="openai-completions"),
        "omp_models_api requires context_window and a base_url",
        id="models-api-without-context",
    ),
]


@pytest.mark.parametrize(("broken", "expected"), TAMPER_CASES)
def test_validate_catches_each_tampered_rule(broken, expected):
    problems = validate(CATALOG + (broken,))
    assert any(expected in problem for problem in problems), problems


def test_validate_empty_catalog_is_healthy():
    assert validate(()) == []


def test_catalog_entries_are_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        CATALOG[0].id = "mutated"
