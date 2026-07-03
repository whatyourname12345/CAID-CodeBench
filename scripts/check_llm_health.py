from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from cair_v2.batch.batch_config import load_batch_config
from cair_v2.llm.clients import DeepSeekClient, MissingAPIKeyError
from cair_v2.llm.json_utils import parse_model_output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Check DeepSeek/OpenAI-compatible JSON health for CAIR v2.")
    parser.add_argument("--model", default=None)
    parser.add_argument("--base-url", default=None)
    parser.add_argument("--timeout", type=int, default=None)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config = load_batch_config()
    model = args.model or config.model_config.models.get("flash", "deepseek-v4-flash")
    base_url = args.base_url or os.getenv("DEEPSEEK_API_URL") or os.getenv("DEEPSEEK_BASE_URL")
    timeout = args.timeout or config.defaults.client_timeout
    raw_dir = PROJECT_ROOT / ".build" / "raw_llm_outputs"
    try:
        client = DeepSeekClient(model=model, base_url=base_url, timeout=timeout, max_retries=1, max_tokens=64)
        result = client.complete(
            "Return strict JSON only.",
            'Return exactly this JSON object: {"ok": true}',
            model=model,
            temperature=0.0,
            max_tokens=64,
            step_name="health_check",
            raw_output_dir=raw_dir,
        )
    except MissingAPIKeyError as exc:
        print(str(exc))
        raise SystemExit(2) from exc
    parsed = parse_model_output(result.text)
    ok = bool(result.ok and parsed.ok and isinstance(parsed.data, dict) and parsed.data.get("ok") is True)
    report = {
        "ok": ok,
        "model": result.model,
        "base_url_configured": bool(base_url),
        "timeout": timeout,
        "status_code": result.status_code,
        "finish_reason": result.finish_reason,
        "prompt_tokens": result.prompt_tokens,
        "completion_tokens": result.completion_tokens,
        "error_type": result.error_type if not ok else None,
        "error_message": result.error_message if not ok else None,
        "raw_response_path": result.raw_response_path,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
