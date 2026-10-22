"""FN2: API Gateway(HTTP API、IAM 認証)の受け入れ試験。docs/acceptance-criteria.md の FN2 を実環境で確かめる。

確かめること:
  1. 署名なしの GET は 403
  2. execute-api:Invoke 権限のない主体(試験用の一時ロール。試験後に削除)が署名しても 403
  3. 署名付きの GET は 200 で、本文が DynamoDB の項目と一致(数値の許容誤差 1e-9)
  4. abc.jpg は 400
  5. 存在しない画像は 404 で、本文に「結果がありません」がある(API Gateway 自体の 404 と区別する)

使い方: AWS_PROFILE=kusuri-ai uv run --group aws python scripts/acceptance_fn2.py --image dev-000_original.png
結果は docs/evidence/fn2.json に書く(アカウント ID は書かない)。
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

REGION = "ap-southeast-2"
STACK = "kusuri-ai"


def outputs() -> dict:
    stack = boto3.client("cloudformation", region_name=REGION).describe_stacks(StackName=STACK)["Stacks"][0]
    return {o["OutputKey"]: o["OutputValue"] for o in stack["Outputs"]}


def call(url: str, credentials=None) -> tuple[int, str]:
    req = AWSRequest(method="GET", url=url)
    if credentials is not None:
        SigV4Auth(credentials, "execute-api", REGION).add_auth(req)
    r = urllib.request.Request(url, headers=dict(req.headers), method="GET")
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            return resp.status, resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8")


def _num_equal(a, b) -> bool:
    if isinstance(b, Decimal):
        return abs(float(a) - float(b)) <= 1e-9
    if isinstance(b, list):
        return isinstance(a, list) and len(a) == len(b) and all(_num_equal(x, y) for x, y in zip(a, b))
    if isinstance(b, dict):
        return isinstance(a, dict) and a.keys() == b.keys() and all(_num_equal(a[k], b[k]) for k in b)
    return a == b


def no_permission_credentials():
    """権限を何も持たない一時ロールを作って引き受ける。呼び出し側で必ず削除する。"""
    iam, sts = boto3.client("iam"), boto3.client("sts")
    me = sts.get_caller_identity()
    account = me["Account"]
    # セッション ARN にはロールのパスが含まれないため、ロールの ARN は IAM から取る
    my_role_arn = iam.get_role(RoleName=me["Arn"].split("/")[1])["Role"]["Arn"]
    name = f"kusuri-fn2-noperm-{int(time.time())}"
    trust = {"Version": "2012-10-17", "Statement": [{
        "Effect": "Allow", "Action": "sts:AssumeRole", "Principal": {"AWS": my_role_arn}}]}
    iam.create_role(RoleName=name, AssumeRolePolicyDocument=json.dumps(trust),
                    Description="FN2 test: no permissions. Delete after test.", MaxSessionDuration=3600,
                    Tags=[{"Key": "project", "Value": "kusuri-ai"}, {"Key": "purpose", "Value": "fn2-test"}])
    for attempt in range(12):  # IAM の反映を待つ
        try:
            c = sts.assume_role(RoleArn=f"arn:aws:iam::{account}:role/{name}", RoleSessionName="fn2")["Credentials"]
            creds = boto3.Session(aws_access_key_id=c["AccessKeyId"], aws_secret_access_key=c["SecretAccessKey"],
                                  aws_session_token=c["SessionToken"]).get_credentials()
            return name, creds
        except sts.exceptions.ClientError:
            time.sleep(5)
    return name, None


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="dev-000_original.png")
    a = ap.parse_args()
    out = outputs()
    base = out["ApiUrl"].rstrip("/")
    own = boto3.Session().get_credentials()
    table = boto3.resource("dynamodb", region_name=REGION).Table(out["ResultsTableName"])
    items = table.query(KeyConditionExpression="imageKey = :k", ExpressionAttributeValues={":k": a.image})["Items"]

    checks = {}
    status, _ = call(f"{base}/results/{a.image}")
    checks["unsigned_403"] = {"status": status, "pass": status == 403}

    role_name, noperm = no_permission_credentials()
    try:
        status = call(f"{base}/results/{a.image}", noperm)[0] if noperm else None
        checks["no_permission_403"] = {"status": status, "pass": status == 403}
    finally:
        boto3.client("iam").delete_role(RoleName=role_name)
        checks["temp_role_deleted"] = {"role": role_name, "pass": True}

    status, body = call(f"{base}/results/{a.image}", own)
    got = json.loads(body).get("results", []) if status == 200 else []
    key = lambda x: x["engineRun"]  # noqa: E731
    match = len(items) > 0 and len(got) == len(items) and all(
        _num_equal(g, i) for g, i in zip(sorted(got, key=key), sorted(items, key=key)))
    checks["signed_200_matches_dynamodb"] = {"status": status, "items": len(items), "pass": status == 200 and match}

    status, body = call(f"{base}/results/abc.jpg", own)
    checks["bad_name_400"] = {"status": status, "pass": status == 400}

    status, body = call(f"{base}/results/none-999_original.png", own)
    try:  # 本文は JSON(日本語は \\u エスケープ)。読み取ってから比べる
        error = json.loads(body).get("error")
    except ValueError:
        error = None
    checks["missing_404_from_lambda"] = {"status": status, "error": error,
                                         "pass": status == 404 and error == "結果がありません"}

    result = {"test": "FN2", "at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "image": a.image,
              "checks": checks, "pass": all(c["pass"] for c in checks.values())}
    path = Path("docs/evidence/fn2.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
