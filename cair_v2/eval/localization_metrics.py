from __future__ import annotations

from pathlib import PurePosixPath
from typing import Any


NOT_APPLICABLE = "not_applicable"


def canonicalize_file(path: Any) -> str:
    text = str(path or "").strip().replace("\\", "/")
    while text.startswith("./"):
        text = text[2:]
    if text.startswith("a/") or text.startswith("b/"):
        text = text[2:]
    return str(PurePosixPath(text)) if text else ""


def canonicalize_symbol(symbol: Any) -> str:
    text = str(symbol or "").strip().replace("\\", "/")
    if "::" not in text:
        return text
    path, name = text.split("::", 1)
    return f"{canonicalize_file(path)}::{name.strip()}"


def _ranked_file_values(predicted_files: list[Any]) -> list[str]:
    values: list[str] = []
    for item in predicted_files:
        if isinstance(item, dict):
            value = item.get("path")
        else:
            value = item
        clean = canonicalize_file(value)
        if clean:
            values.append(clean)
    return values


def _ranked_symbol_values(predicted_functions: list[Any]) -> list[str]:
    values: list[str] = []
    for item in predicted_functions:
        if isinstance(item, dict):
            value = item.get("symbol")
        else:
            value = item
        clean = canonicalize_symbol(value)
        if clean:
            values.append(clean)
    return values


def file_hit_at_k(predicted_files: list[Any], gold_files: list[Any], k: int) -> int:
    gold = {canonicalize_file(path) for path in gold_files if canonicalize_file(path)}
    if not gold:
        return 0
    return int(any(path in gold for path in _ranked_file_values(predicted_files)[:k]))


def function_hit_at_k(predicted_functions: list[Any], gold_functions: list[Any], k: int) -> int | str:
    gold = {canonicalize_symbol(symbol) for symbol in gold_functions if canonicalize_symbol(symbol)}
    if not gold:
        return NOT_APPLICABLE
    return int(any(symbol in gold for symbol in _ranked_symbol_values(predicted_functions)[:k]))


def file_recall_at_k(predicted_files: list[Any], gold_files: list[Any], k: int) -> float:
    gold = {canonicalize_file(path) for path in gold_files if canonicalize_file(path)}
    if not gold:
        return 0.0
    hits = set(_ranked_file_values(predicted_files)[:k]) & gold
    return len(hits) / len(gold)


def function_recall_at_k(predicted_functions: list[Any], gold_functions: list[Any], k: int) -> float | str:
    gold = {canonicalize_symbol(symbol) for symbol in gold_functions if canonicalize_symbol(symbol)}
    if not gold:
        return NOT_APPLICABLE
    hits = set(_ranked_symbol_values(predicted_functions)[:k]) & gold
    return len(hits) / len(gold)


def file_mrr(predicted_files: list[Any], gold_files: list[Any]) -> float:
    gold = {canonicalize_file(path) for path in gold_files if canonicalize_file(path)}
    for index, path in enumerate(_ranked_file_values(predicted_files), start=1):
        if path in gold:
            return 1.0 / index
    return 0.0


def function_mrr(predicted_functions: list[Any], gold_functions: list[Any]) -> float | str:
    gold = {canonicalize_symbol(symbol) for symbol in gold_functions if canonicalize_symbol(symbol)}
    if not gold:
        return NOT_APPLICABLE
    for index, symbol in enumerate(_ranked_symbol_values(predicted_functions), start=1):
        if symbol in gold:
            return 1.0 / index
    return 0.0


def localization_report(prediction: dict[str, Any], gold: dict[str, Any], ks: list[int] | None = None) -> dict[str, Any]:
    ks = ks or [1, 3, 5]
    predicted_files = prediction.get("ranked_files") if isinstance(prediction, dict) else []
    predicted_functions = prediction.get("ranked_functions") if isinstance(prediction, dict) else []
    gold_files = gold.get("files") if isinstance(gold, dict) else []
    gold_functions = gold.get("functions") if isinstance(gold, dict) else []
    result: dict[str, Any] = {}
    for k in ks:
        result[f"file_hit_at_{k}"] = file_hit_at_k(predicted_files or [], gold_files or [], k)
        result[f"function_hit_at_{k}"] = function_hit_at_k(predicted_functions or [], gold_functions or [], k)
    result["file_mrr"] = file_mrr(predicted_files or [], gold_files or [])
    result["function_mrr"] = function_mrr(predicted_functions or [], gold_functions or [])
    return result


def _self_test() -> None:
    assert canonicalize_file("./a/sklearn/base.py") == "sklearn/base.py"
    assert canonicalize_symbol("./sklearn/base.py::BaseEstimator.get_params") == "sklearn/base.py::BaseEstimator.get_params"
    assert file_hit_at_k([{"path": "./sklearn/base.py"}], ["sklearn/base.py"], 1) == 1
    assert file_hit_at_k([{"path": "sklearn/utils.py"}], ["sklearn/base.py"], 1) == 0
    assert function_hit_at_k([], [], 1) == NOT_APPLICABLE
    assert function_hit_at_k([{"symbol": "sklearn/base.py::clone"}], ["./sklearn/base.py::clone"], 1) == 1


if __name__ == "__main__":
    _self_test()
    print("localization_metrics self-test passed")
