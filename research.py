"""Compatible model/session adapter; configuration is read without mutating environment."""

import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parent


def setting(key, default=""):
    return os.getenv(key) or dotenv_values(ROOT / ".env").get(key) or default


def model_name():
    return setting("LLM_MODEL", "deepseek-chat")


def model_enabled():
    return bool(setting("LLM_API_KEY"))


async def model_json(instructions, payload):
    if not model_enabled():
        raise ValueError("Set LLM_API_KEY in this project's .env.")
    body = {
        "model": model_name(),
        "temperature": 0,
        "max_tokens": int(setting("MODEL_MAX_TOKENS", "4500")),
        "response_format": {"type": "json_object"},
        "messages": [
            {
                "role": "system",
                "content": instructions + "\nReturn a JSON object only.",
            },
            {
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False, allow_nan=False),
            },
        ],
    }
    async with httpx.AsyncClient(
        timeout=float(setting("MODEL_TIMEOUT", "40"))
    ) as client:
        response = await client.post(
            setting("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/")
            + "/chat/completions",
            json=body,
            headers={"Authorization": "Bearer " + setting("LLM_API_KEY")},
        )
        response.raise_for_status()
    raw = response.json()["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1].rsplit("```", 1)[0]
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Model must return an object.")
    return result


@asynccontextmanager
async def connect(directory=None):
    from core.mcp_host import connect_servers

    directory = directory or ROOT / "runs" / "manual"
    async with connect_servers(directory) as host:
        yield host
