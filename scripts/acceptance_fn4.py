"""FN4: 失敗したメッセージが、再試行(maxReceiveCount=5)の後に DLQ へ送られることの受け入れ試験。

本番の設定のまま行う(VisibilityTimeout 720 秒 × 5 回 ≈ 60 分)。Bedrock は使わない。
  - 毒メッセージ: runs/ の形式だが存在しない画像を指す。S3 の取得で失敗し、5回目の後に DLQ に移る。
  - 正常なメッセージ: 処理対象外のキーを指す。ハンドラーが読み飛ばして成功し、DLQ に入らない。

使い方:
  AWS_PROFILE=kusuri-ai uv run --group aws python scripts/acceptance_fn4.py send
  AWS_PROFILE=kusuri-ai uv run --group aws python scripts/acceptance_fn4.py wait --timeout-min 80
結果は docs/evidence/fn4.json に書く(アカウント ID は書かない)。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

REGION = "ap-southeast-2"
STACK = "kusuri-ai"
STATE = Path("docs/evidence/fn4_state.json")
EVIDENCE = Path("docs/evidence/fn4.json")
ENGINES = {"haiku": ("HaikuQueue", "HaikuDlq"), "sonnet": ("SonnetQueue", "SonnetDlq")}


def queues() -> dict:
    res = boto3.client("cloudformation", region_name=REGION).describe_stack_resources(StackName=STACK)["StackResources"]
    return {r["LogicalResourceId"]: r["PhysicalResourceId"] for r in res if r["ResourceType"] == "AWS::SQS::Queue"}


def s3_event(bucket: str, key: str) -> str:
    return json.dumps({"Records": [{"eventTime": datetime.now(timezone.utc).isoformat(),
                                    "s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]})


def send() -> None:
    q = queues()
    bucket = {o["OutputKey"]: o["OutputValue"] for o in boto3.client("cloudformation", region_name=REGION)
              .describe_stacks(StackName=STACK)["Stacks"][0]["Outputs"]}["InputBucketName"]
    sqs = boto3.client("sqs", region_name=REGION)
    run = "fn4-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    poison_key = f"runs/{run}/dev/dev-999_original.png"  # 存在しない画像
    normal_key = f"not-a-run/{run}.txt"  # 処理対象外。読み飛ばされる
    state = {"run": run, "sentAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "poisonKey": poison_key, "normalKey": normal_key, "messages": {}}
    for engine, (main_q, _) in ENGINES.items():
        p = sqs.send_message(QueueUrl=q[main_q], MessageBody=s3_event(bucket, poison_key))["MessageId"]
        n = sqs.send_message(QueueUrl=q[main_q], MessageBody=s3_event(bucket, normal_key))["MessageId"]
        state["messages"][engine] = {"poison": p, "normal": n}
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(state, ensure_ascii=False, indent=1))


def _peek_dlq(sqs, url: str) -> list[dict]:
    """DLQ のメッセージを見る(見た後すぐ見える状態に戻す)。"""
    msgs = sqs.receive_message(QueueUrl=url, MaxNumberOfMessages=10, WaitTimeSeconds=1,
                               AttributeNames=["ApproximateReceiveCount"]).get("Messages", [])
    for m in msgs:
        sqs.change_message_visibility(QueueUrl=url, ReceiptHandle=m["ReceiptHandle"], VisibilityTimeout=0)
    return msgs


def wait(timeout_min: int) -> None:
    state = json.loads(STATE.read_text(encoding="utf-8"))
    q = queues()
    sqs = boto3.client("sqs", region_name=REGION)
    deadline = time.time() + timeout_min * 60
    result: dict = {}
    while True:
        result = {}
        for engine, (main_q, dlq) in ENGINES.items():
            keys = []
            for m in _peek_dlq(sqs, q[dlq]):
                body = json.loads(m["Body"])
                keys += [r["s3"]["object"]["key"] for r in body.get("Records", [])]
            attrs = sqs.get_queue_attributes(QueueUrl=q[main_q], AttributeNames=[
                "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
            result[engine] = {
                "poison_in_dlq": state["poisonKey"] in keys,
                "normal_in_dlq": state["normalKey"] in keys,
                "main_queue_remaining": int(attrs["ApproximateNumberOfMessages"])
                + int(attrs["ApproximateNumberOfMessagesNotVisible"]),
            }
        done = all(r["poison_in_dlq"] for r in result.values())
        print(datetime.now(timezone.utc).strftime("%H:%M:%S"), json.dumps(result), flush=True)
        if done or time.time() > deadline:
            break
        time.sleep(120)
    evidence = {"test": "FN4", "run": state["run"], "sentAt": state["sentAt"],
                "checkedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "maxReceiveCount": 5, "visibilityTimeoutSec": 720, "result": result,
                "pass": all(r["poison_in_dlq"] and not r["normal_in_dlq"] for r in result.values())}
    EVIDENCE.write_text(json.dumps(evidence, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=False, indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("send")
    w = sub.add_parser("wait")
    w.add_argument("--timeout-min", type=int, default=80)
    a = ap.parse_args()
    send() if a.cmd == "send" else wait(a.timeout_min)


if __name__ == "__main__":
    main()
