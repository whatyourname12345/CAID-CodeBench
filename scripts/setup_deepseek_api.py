from __future__ import annotations

import getpass
import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENV_PATH = PROJECT_ROOT / ".env"


def parse_env(text: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def render_env(values: dict[str, str]) -> str:
    lines = [f"{key}={value}" for key, value in values.items()]
    return "\n".join(lines) + "\n"


def main() -> None:
    api_key = getpass.getpass("DeepSeek API key (input hidden): ").strip()
    if not api_key:
        raise SystemExit("No API key provided.")
    values = parse_env(ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else "")
    values["DEEPSEEK_API_KEY"] = api_key
    values.setdefault("DEEPSEEK_API_URL", "https://api.deepseek.com/chat/completions")
    ENV_PATH.write_text(render_env(values), encoding="utf-8")
    os.chmod(ENV_PATH, 0o600)
    print(f"Wrote DeepSeek API configuration to {ENV_PATH}")
    print("The API key was not printed. .env is already ignored by git.")


if __name__ == "__main__":
    main()
