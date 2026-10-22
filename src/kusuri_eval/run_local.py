"""ローカルで Tesseract を実行し、結果を report.py と同じ形式で書き出す。

使い方: uv run python -m kusuri_eval.run_local --split dev
評価セット(eval)は、解析規則とプロンプトを固定した後にだけ実行する。
Tesseract の処理時間はローカルの計測で、Lambda の値ではない(比較表では参考扱い)。
"""
from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from . import degrade as dg
from .engines import tesseract
from .report import build, to_markdown


def run(split_dir: Path) -> dict:
    results = {}
    for truth in sorted(split_dir.glob("*.json")):
        for c in dg.CONDITIONS:
            image = f"{truth.stem}_{c}.png"
            t0 = time.perf_counter()
            lines = tesseract.read_image(split_dir / image)
            results[image] = {"lines": lines, "parseOk": True, "stopReason": "local",
                              "secondsTotal": time.perf_counter() - t0}
    return results


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "eval"], required=True)
    ap.add_argument("--data", type=Path, default=Path("data"))
    a = ap.parse_args()
    results = run(a.data / a.split)
    out = a.data / f"results_tesseract_{a.split}.json"
    out.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(to_markdown(build(a.data / a.split, {"tesseract": results})))


if __name__ == "__main__":
    main()
