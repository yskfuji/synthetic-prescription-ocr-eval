"""エンジン × 劣化条件 × 項目の比較表(FN3)と、Q1〜Q3 の判定を作る。

入力の結果は、エンジンごとに {imageKey: 項目} の辞書(DynamoDB の項目と同じ形)。
分母は正解ファイル(<split>/<pid>.json)から作る。結果が無い画像は、全行を不正解として数え、件数を別に報告する。

使い方: uv run python -m kusuri_eval.report --split eval --results data/results_haiku_eval.json data/results_sonnet_eval.json ...
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from . import degrade as dg
from .score import FIELDS, accuracy_with_ci, paired_difference_ci, percentile_nearest_rank, score_prescription

# docs/acceptance-criteria.md で固定した目標(緩めない)
Q1_TARGET = {"original": 0.90, "tilt": 0.80, "noise": 0.80}
Q2_P95_SECONDS = 20.0
Q3_PER_IMAGE_USD = 0.02
Q3_TOTAL_USD = 5.0
NONINFERIORITY_MARGIN = 0.05
CLAUDE_ENGINES = ("haiku", "sonnet")


def _git_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def score_engine(truths: dict, results: dict) -> dict:
    """1エンジンの、条件ごとの採点と、処理時間・費用の集計。"""
    out = {}
    for c in dg.CONDITIONS:
        per, seconds, costs = [], [], []
        missing_results = parse_fail = missing_lines = extra_lines = 0
        stop_reasons: dict[str, int] = {}
        for pid, t in truths.items():
            item = results.get(f"{pid}_{c}.png")
            if item is None:
                missing_results += 1
                pred = []
            else:
                pred = item.get("lines", [])
                parse_fail += 0 if item.get("parseOk", True) else 1
                sr = item.get("stopReason", "")
                stop_reasons[sr] = stop_reasons.get(sr, 0) + 1
                if "secondsTotal" in item:
                    seconds.append(float(item["secondsTotal"]))
                if "costUsd" in item:
                    costs.append(float(item["costUsd"]))
            s = score_prescription(t["lines"], pred)
            missing_lines += s["missing"]
            extra_lines += s["extra"]
            per.append(s)
        out[c] = {
            "accuracy": accuracy_with_ci(per),
            "per_prescription": per,
            "missing_results": missing_results,
            "parse_fail": parse_fail,
            "missing_lines": missing_lines,
            "extra_lines": extra_lines,
            "stop_reasons": stop_reasons,
            "seconds_p50": percentile_nearest_rank(seconds, 0.50) if seconds else None,
            "seconds_p95": percentile_nearest_rank(seconds, 0.95) if seconds else None,
            "cost_total_usd": sum(costs) if costs else None,
            "cost_max_usd": max(costs) if costs else None,
        }
    return out


def judge(scores: dict, n_images: int) -> dict:
    """Q1(点推定)・Q2・Q3 の判定。Q1 は「少なくとも1つの Claude エンジン」(記述的な判定)。"""
    verdict: dict = {"q1": {}, "q2": {}, "q3": {}}
    for e in CLAUDE_ENGINES:
        if e not in scores:
            continue
        cells = {(c, f): scores[e][c]["accuracy"][f] for c in dg.CONDITIONS for f in FIELDS}
        verdict["q1"][e] = {
            "pass": all(v["accuracy"] >= Q1_TARGET[c] for (c, f), v in cells.items()),
            "unconfirmed_cells": [f"{c}/{f}" for (c, f), v in cells.items() if v["ci95"][0] < Q1_TARGET[c]],
        }
        p95s = [scores[e][c]["seconds_p95"] for c in dg.CONDITIONS]
        verdict["q2"][e] = {"p95_max": max(p95s) if None not in p95s else None,
                            "pass": None not in p95s and max(p95s) <= Q2_P95_SECONDS}
        totals = [scores[e][c]["cost_total_usd"] for c in dg.CONDITIONS]
        total = sum(totals) if None not in totals else None
        verdict["q3"][e] = {"total_usd": total,
                            "per_image_usd": total / n_images if total is not None else None,
                            "pass_per_image": total is not None and total / n_images <= Q3_PER_IMAGE_USD}
    verdict["q1"]["pass"] = any(v["pass"] for k, v in verdict["q1"].items() if k != "pass")
    claude_totals = [v["total_usd"] for v in verdict["q3"].values()]
    verdict["q3"]["eval_total_usd"] = sum(claude_totals) if claude_totals and None not in claude_totals else None
    verdict["q3"]["pass_total"] = verdict["q3"]["eval_total_usd"] is not None and verdict["q3"]["eval_total_usd"] <= Q3_TOTAL_USD
    return verdict


def noninferiority(scores: dict) -> dict | None:
    """副次: Haiku − Sonnet の対応のある差。下限 > −margin なら「非劣性を示せた」(報告のみ。合否に使わない)。"""
    if not all(e in scores for e in CLAUDE_ENGINES):
        return None
    out = {}
    for c in dg.CONDITIONS:
        d = paired_difference_ci(scores["haiku"][c]["per_prescription"], scores["sonnet"][c]["per_prescription"])
        for f, v in d.items():
            v["noninferior"] = v["ci95"][0] > -NONINFERIORITY_MARGIN
        out[c] = d
    return out


def build(split_dir: Path, results_by_engine: dict[str, dict]) -> dict:
    truths = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in sorted(split_dir.glob("*.json"))}
    scores = {e: score_engine(truths, r) for e, r in results_by_engine.items()}
    prompt_hashes = sorted({str(i.get("promptHash")) for r in results_by_engine.values() for i in r.values()
                            if i.get("promptHash")})
    return {
        "split": split_dir.name,
        "git_sha": _git_sha(),
        "prompt_hashes": prompt_hashes,
        "n_prescriptions": len(truths),
        "scores": {e: {c: {k: v for k, v in s.items() if k != "per_prescription"} for c, s in sc.items()}
                   for e, sc in scores.items()},
        "verdict": judge(scores, len(truths) * len(dg.CONDITIONS)),
        "noninferiority_haiku_vs_sonnet": noninferiority(scores),
    }


def to_markdown(rep: dict) -> str:
    rows = ["| エンジン | 条件 | 項目 | 正解率 | 95%CI | CI の方法 | 結果なし | parse 失敗 | 行の不足/余分 | p50/p95 秒 |",
            "|---|---|---|---|---|---|---|---|---|---|"]
    for e, sc in rep["scores"].items():
        for c, s in sc.items():
            for f in FIELDS:
                a = s["accuracy"][f]
                lat = "-" if s["seconds_p50"] is None else f"{s['seconds_p50']:.2f}/{s['seconds_p95']:.2f}"
                rows.append(f"| {e} | {c} | {f} | {a['accuracy']:.3f} | [{a['ci95'][0]:.3f}, {a['ci95'][1]:.3f}] | "
                            f"{a['ci_method']} | {s['missing_results']} | {s['parse_fail']} | "
                            f"{s['missing_lines']}/{s['extra_lines']} | {lat} |")
    head = (f"split={rep['split']} git={rep['git_sha'][:12]} promptHash={','.join(rep['prompt_hashes']) or '-'} "
            f"処方箋={rep['n_prescriptions']}\n\n")
    return head + "\n".join(rows) + "\n\n判定: " + json.dumps(rep["verdict"], ensure_ascii=False) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["dev", "eval"], required=True)
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--results", type=Path, nargs="+", required=True,
                    help="results_<engine>_<split>.json(エンジン名はファイル名から取る)")
    a = ap.parse_args()
    results = {p.stem.split("_")[1]: json.loads(p.read_text(encoding="utf-8")) for p in a.results}
    rep = build(a.data / a.split, results)
    out = a.data / f"report_{a.split}.json"
    out.write_text(json.dumps(rep, ensure_ascii=False, indent=1), encoding="utf-8")
    md = to_markdown(rep)
    (a.data / f"report_{a.split}.md").write_text(md, encoding="utf-8")
    print(md)


if __name__ == "__main__":
    main()
