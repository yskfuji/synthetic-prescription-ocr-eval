"""評価の1回の実行(runId)を管理する。アップロードと、DynamoDB からの結果の取得。

- eval は、凍結の条件(プロンプトが v1-draft でない、作業ツリーが clean、HEAD にタグ)を満たさないと実行しない。
- 画像の sha256 が manifest.json と一致しないものは送らない(Q4)。
- 画像は runs/<runId>/<split>/<画像名> に置く。Lambda はこのキーから runId を読む。

使い方:
  uv run python -m kusuri_eval.eval_run upload --split dev --bucket <バケット名> [--dry-run]
  uv run python -m kusuri_eval.eval_run fetch --split dev --run-id <runId> --table <テーブル名>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import time
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from .engines import claude

ENGINES = ("haiku", "sonnet")


class NotFrozenError(RuntimeError):
    pass


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True)


def check_frozen() -> dict:
    """eval の前提条件を確かめる。満たさなければ NotFrozenError。"""
    problems = []
    if claude.PROMPT_VERSION == claude.DRAFT_VERSION:
        problems.append(f"PROMPT_VERSION が {claude.DRAFT_VERSION} のまま")
    if _git("status", "--porcelain").stdout.strip():
        problems.append("作業ツリーに未コミットの変更がある")
    tag = _git("describe", "--exact-match", "--tags", "HEAD")
    if tag.returncode != 0:
        problems.append("HEAD にタグが無い")
    if problems:
        raise NotFrozenError("; ".join(problems))
    return {"gitSha": _git("rev-parse", "HEAD").stdout.strip(), "tag": tag.stdout.strip()}


def verified_images(split_dir: Path, manifest: dict) -> list[Path]:
    """manifest の sha256 と一致する画像だけを返す。1枚でも不一致なら止める。"""
    expected = {i["file"]: i["sha256"] for i in manifest["splits"][split_dir.name]["items"]}
    paths = []
    for name, digest in sorted(expected.items()):
        p = split_dir / name
        if hashlib.sha256(p.read_bytes()).hexdigest() != digest:
            raise RuntimeError(f"sha256 が manifest と一致しない: {name}")
        paths.append(p)
    return paths


def new_run_id(split: str) -> str:
    return f"{split}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"


PER_MINUTE = 8  # Bedrock の RPM 上限(このアカウントは各モデル10)より少なくする


def upload(split: str, data: Path, bucket: str, dry_run: bool, s3=None,
           per_minute: int = PER_MINUTE, sleep=time.sleep) -> dict:
    """画像を1分あたり per_minute 枚までの間隔でアップロードする(各画像は両エンジンに1回ずつ届く)。"""
    meta = check_frozen() if split == "eval" else {"gitSha": _git("rev-parse", "HEAD").stdout.strip(), "tag": None}
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    images = verified_images(data / split, manifest)
    run_id = new_run_id(split)
    keys = [f"runs/{run_id}/{split}/{p.name}" for p in images]
    # promptHash は Lambda が項目ごとに保存する(モデル ID と effort はデプロイ時のパラメータで決まるため)
    record = {"runId": run_id, "split": split, "images": len(keys), "promptVersion": claude.PROMPT_VERSION,
              "perMinute": per_minute, **meta, "dryRun": dry_run}
    if not dry_run:
        if s3 is None:
            import boto3

            s3 = boto3.client("s3")
        interval = 60.0 / per_minute
        for i, (p, key) in enumerate(zip(images, keys)):
            if i:
                sleep(interval)
            s3.upload_file(str(p), bucket, key)
    runs = data / "runs"
    runs.mkdir(exist_ok=True)
    (runs / f"{run_id}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return record


def _plain(o):
    if isinstance(o, Decimal):
        return float(o)
    if isinstance(o, list):
        return [_plain(x) for x in o]
    if isinstance(o, dict):
        return {k: _plain(v) for k, v in o.items()}
    return o


def fetch(split: str, run_id: str, data: Path, table=None) -> dict[str, Path]:
    """runId の結果を、エンジンごとに results_<engine>_<split>.json に書き出す(無い画像は書かない)。"""
    if table is None:
        raise ValueError("table が必要")
    manifest = json.loads((data / "manifest.json").read_text(encoding="utf-8"))
    names = [i["file"] for i in manifest["splits"][split]["items"]]
    out = {}
    for e in ENGINES:
        results = {}
        for name in names:
            item = table.get_item(Key={"imageKey": name, "engineRun": f"{e}#{run_id}"}).get("Item")
            if item:
                results[name] = _plain(item)
        path = data / f"results_{e}_{split}.json"
        path.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
        out[e] = path
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    up = sub.add_parser("upload")
    up.add_argument("--split", choices=["dev", "eval"], required=True)
    up.add_argument("--bucket", required=True)
    up.add_argument("--dry-run", action="store_true")
    fe = sub.add_parser("fetch")
    fe.add_argument("--split", choices=["dev", "eval"], required=True)
    fe.add_argument("--run-id", required=True)
    fe.add_argument("--table", required=True)
    for p in (up, fe):
        p.add_argument("--data", type=Path, default=Path("data"))
    a = ap.parse_args()
    if a.cmd == "upload":
        print(json.dumps(upload(a.split, a.data, a.bucket, a.dry_run), ensure_ascii=False, indent=1))
    else:
        import boto3

        print(fetch(a.split, a.run_id, a.data, boto3.resource("dynamodb").Table(a.table)))


if __name__ == "__main__":
    main()
