"""GET /results/{image}: 画像1枚の、エンジンごとの読み取り結果を返す(IAM 認証は API Gateway 側)。"""
from __future__ import annotations

import json
import os
from decimal import Decimal

_cache: dict = {}


def _table():
    if "t" not in _cache:
        import boto3

        _cache["t"] = boto3.resource("dynamodb").Table(os.environ["TABLE_NAME"])
    return _cache["t"]


def _json_default(o):
    if isinstance(o, Decimal):
        return float(o)
    raise TypeError(type(o))


def handler(event, context, table=None):
    image = (event.get("pathParameters") or {}).get("image", "")
    if not image.endswith(".png") or "/" in image:
        return {"statusCode": 400, "body": json.dumps({"error": "image は xxx.png の形式で指定してください"})}
    table = table or _table()
    items = table.query(
        KeyConditionExpression="imageKey = :k", ExpressionAttributeValues={":k": image}
    )["Items"]
    if not items:
        return {"statusCode": 404, "body": json.dumps({"error": "結果がありません"})}
    return {
        "statusCode": 200,
        "headers": {"content-type": "application/json; charset=utf-8"},
        "body": json.dumps({"image": image, "results": items}, ensure_ascii=False, default=_json_default),
    }
