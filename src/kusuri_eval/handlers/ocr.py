"""SQS から S3 の通知を受け取り、画像を Claude で読んで、結果を DynamoDB に保存する Lambda。

流れ: S3(画像) → SNS → SQS → この Lambda → DynamoDB。
S3 からの通知は SNS の「生のメッセージ配信」で SQS に届くため、本文は S3 イベントの JSON になる。

画像のキーは runs/<runId>/<split>/<pid>_<condition>.png とする。
結果は (imageKey, engineRun = "<engine>#<runId>") をキーに、まだ無いときだけ書く(上書きしない)。
同じメッセージが2回届いても、既に結果があれば Bedrock を呼ばずに終える(二重課金と、やり直しの痕跡消しを防ぐ)。
"""
from __future__ import annotations

import json
import os
import re
import time
from decimal import Decimal
from urllib.parse import unquote_plus

from ..engines import claude

KEY_PATTERN = re.compile(r"^runs/(?P<run>[A-Za-z0-9_-]+)/(?P<split>dev|eval)/(?P<image>[a-z]+-\d{3}_[a-z]+\.png)$")

_clients: dict = {}


def _get_clients():
    if not _clients:
        import boto3  # Lambda の実行環境に入っている。テストでは差し替える

        _clients["s3"] = boto3.client("s3")
        _clients["bedrock"] = boto3.client("bedrock-runtime")
        _clients["table"] = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _clients


def _image_refs(body: str):
    for rec in json.loads(body).get("Records", []):
        key = unquote_plus(rec["s3"]["object"]["key"])
        m = KEY_PATTERN.match(key)
        if m:
            yield rec["s3"]["bucket"]["name"], key, m, rec.get("eventTime", "")


def _is_conditional_failure(e: Exception) -> bool:
    return getattr(e, "response", {}).get("Error", {}).get("Code") == "ConditionalCheckFailedException"


def process_record(body: str, clients: dict, engine: str, model_id: str,
                   effort: str | None = None, receive_count: int = 1, temperature: float | None = None) -> None:
    for bucket, key, m, event_time in _image_refs(body):
        item_key = {"imageKey": m["image"], "engineRun": f"{engine}#{m['run']}"}
        if "Item" in clients["table"].get_item(Key=item_key):
            print(json.dumps({"level": "INFO", "msg": "既に結果あり。スキップ", **item_key}))
            continue
        t0 = time.perf_counter()
        image = clients["s3"].get_object(Bucket=bucket, Key=key)["Body"].read()
        r = claude.read_image(clients["bedrock"], model_id, image, effort, temperature)
        seconds_total = time.perf_counter() - t0  # S3 の取得から、書き込みの直前まで
        try:
            clients["table"].put_item(
                Item={
                    **item_key,
                    "engine": engine,
                    "runId": m["run"],
                    "split": m["split"],
                    "modelId": model_id,
                    "effort": effort or "default",
                    "temperature": "default" if temperature is None else Decimal(str(temperature)),
                    "promptVersion": claude.PROMPT_VERSION,
                    "promptHash": claude.prompt_hash(model_id, effort, temperature),
                    "lines": r.lines,
                    "parseOk": r.parse_ok,
                    "stopReason": r.stop_reason,
                    "inputTokens": r.input_tokens,
                    "outputTokens": r.output_tokens,
                    "secondsModel": Decimal(str(round(r.seconds, 4))),
                    "secondsTotal": Decimal(str(round(seconds_total, 4))),
                    "costUsd": Decimal(str(round(r.cost_usd, 8))),
                    "receiveCount": receive_count,
                    "s3EventTime": event_time,
                },
                ConditionExpression="attribute_not_exists(imageKey)",
            )
        except Exception as e:
            if not _is_conditional_failure(e):
                raise
            # 並行して同じ画像を処理した別の実行が先に書いた。結果は上書きしない
            print(json.dumps({"level": "WARN", "msg": "同時書き込みのため保存せず", **item_key}))


def handler(event, context, clients: dict | None = None):
    """失敗したメッセージだけを batchItemFailures で返す(成功分は再試行されない)。"""
    clients = clients or _get_clients()
    engine, model_id = os.environ["ENGINE"], os.environ["MODEL_ID"]
    effort = os.environ.get("EFFORT") or None
    temperature = float(os.environ["TEMPERATURE"]) if os.environ.get("TEMPERATURE") else None
    failures = []
    for record in event["Records"]:
        receive_count = int(record.get("attributes", {}).get("ApproximateReceiveCount", "1"))
        try:
            process_record(record["body"], clients, engine, model_id, effort, receive_count, temperature)
        except Exception as e:  # noqa: BLE001 失敗は SQS に任せて再試行・DLQ へ
            print(json.dumps({"level": "ERROR", "messageId": record["messageId"], "error": repr(e)}))
            failures.append({"itemIdentifier": record["messageId"]})
    return {"batchItemFailures": failures}
