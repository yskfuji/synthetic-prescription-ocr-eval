import io
import json
from decimal import Decimal

from kusuri_eval.engines import claude
from kusuri_eval.handlers import api, ocr

HAIKU = claude.HAIKU_ID
KEY = "runs/dev-20261022T000000Z/dev/dev-000_original.png"
S3_EVENT = {"Records": [{"eventTime": "2026-10-22T00:00:00.000Z",
                         "s3": {"bucket": {"name": "b"}, "object": {"key": KEY}}}]}


class FakeBedrock:
    def __init__(self, text, fail=False, stop="end_turn"):
        self.text, self.fail, self.stop, self.calls = text, fail, stop, []

    def converse(self, **kw):
        self.calls.append(kw)
        if self.fail:
            raise RuntimeError("throttled")
        return {"output": {"message": {"content": [{"reasoningContent": {"reasoningText": {"text": "考え中"}}},
                                                   {"text": self.text}]}},
                "stopReason": self.stop,
                "usage": {"inputTokens": 1500, "outputTokens": 300}}


class FakeS3:
    def __init__(self):
        self.calls = 0

    def get_object(self, Bucket, Key):
        self.calls += 1
        return {"Body": io.BytesIO(b"PNGDATA")}


class ConditionalCheckFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class FakeTable:
    def __init__(self, race=False):
        self.items, self.race = [], race

    def get_item(self, Key):
        for i in self.items:
            if i["imageKey"] == Key["imageKey"] and i["engineRun"] == Key["engineRun"]:
                return {"Item": i}
        return {}

    def put_item(self, Item, ConditionExpression=None):
        if self.race or (ConditionExpression and self.get_item(
                {"imageKey": Item["imageKey"], "engineRun": Item["engineRun"]})):
            raise ConditionalCheckFailed()
        self.items.append(Item)

    def query(self, **kw):
        return {"Items": [i for i in self.items if i["imageKey"] == kw["ExpressionAttributeValues"][":k"]]}


def _event(*bodies, receive_count="1"):
    return {"Records": [{"messageId": f"m{i}", "body": b, "attributes": {"ApproximateReceiveCount": receive_count}}
                        for i, b in enumerate(bodies)]}


def _clients(text='[{"drug":"A錠","dose":"1回1錠","days":"7日分"}]', fail=False, stop="end_turn", race=False):
    return {"s3": FakeS3(), "bedrock": FakeBedrock(text, fail, stop), "table": FakeTable(race)}


def _env(monkeypatch):
    monkeypatch.setenv("ENGINE", "haiku")
    monkeypatch.setenv("MODEL_ID", HAIKU)
    monkeypatch.delenv("EFFORT", raising=False)
    monkeypatch.delenv("TEMPERATURE", raising=False)


def test_parse_lines_tolerates_code_fence_and_rejects_garbage():
    lines, ok = claude.parse_lines('```json\n[{"drug":"A","dose":"1回1錠","days":"7日分"}]\n```')
    assert ok and lines == [{"drug": "A", "dose": "1回1錠", "days": "7日分"}]
    assert claude.parse_lines("読み取れません") == ([], False)
    assert claude.parse_lines("[壊れた") == ([], False)


def test_cost_uses_au_prices():
    assert abs(claude.cost_usd(HAIKU, 1_000_000, 1_000_000) - 6.60) < 1e-9
    assert abs(claude.cost_usd(claude.SONNET_ID, 1_000_000, 1_000_000) - 19.80) < 1e-9


def test_temperature_is_sent_only_when_set_and_changes_hash():
    # 4.5/4.6 世代は temperature を受け付ける。5.5 世代では送らない(400 になる)
    assert claude.build_request(HAIKU, b"x", temperature=0.0)["inferenceConfig"]["temperature"] == 0.0
    assert claude.prompt_hash(HAIKU, temperature=0.0) != claude.prompt_hash(HAIKU)


def test_handler_passes_temperature_from_env(monkeypatch):
    _env(monkeypatch)
    monkeypatch.setenv("TEMPERATURE", "0")
    c = _clients()
    ocr.handler(_event(json.dumps(S3_EVENT)), None, clients=c)
    assert c["bedrock"].calls[0]["inferenceConfig"]["temperature"] == 0.0
    item = c["table"].items[0]
    assert item["temperature"] == Decimal("0.0") and item["promptHash"] == claude.prompt_hash(HAIKU, None, 0.0)


def test_request_without_temperature_sends_no_sampling_params():
    # 既定(temperature=None)では送らない。5.5 世代は既定以外の temperature 等で 400 になるため
    req = claude.build_request(HAIKU, b"x")
    cfg = req["inferenceConfig"]
    assert not {"temperature", "topP", "topK"} & set(cfg)
    assert cfg["maxTokens"] >= 4096
    blocks = req["messages"][0]["content"]
    assert blocks[0]["image"]["source"]["bytes"] == b"x" and blocks[1]["text"] == claude.PROMPT
    assert "additionalModelRequestFields" not in req


def test_effort_is_passed_and_changes_prompt_hash():
    req = claude.build_request(HAIKU, b"x", effort="low")
    assert req["additionalModelRequestFields"] == {"output_config": {"effort": "low"}}
    assert claude.prompt_hash(HAIKU) != claude.prompt_hash(HAIKU, "low")
    assert claude.prompt_hash(HAIKU) != claude.prompt_hash(claude.SONNET_ID)


def test_handler_saves_result_with_run_and_required_attributes(monkeypatch):
    _env(monkeypatch)
    c = _clients()
    out = ocr.handler(_event(json.dumps(S3_EVENT), receive_count="2"), None, clients=c)
    assert out == {"batchItemFailures": []}
    item = c["table"].items[0]
    assert item["imageKey"] == "dev-000_original.png"
    assert item["engineRun"] == "haiku#dev-20261022T000000Z" and item["split"] == "dev"
    assert item["lines"] == [{"drug": "A錠", "dose": "1回1錠", "days": "7日分"}]  # 思考ブロックは本文に混ぜない
    assert item["stopReason"] == "end_turn" and item["receiveCount"] == 2
    assert item["promptHash"] == claude.prompt_hash(HAIKU)
    assert item["secondsTotal"] >= item["secondsModel"] >= 0
    assert isinstance(item["costUsd"], Decimal) and item["s3EventTime"] == "2026-10-22T00:00:00.000Z"


def test_max_tokens_stop_is_recorded(monkeypatch):
    _env(monkeypatch)
    c = _clients(text="", stop="max_tokens")
    ocr.handler(_event(json.dumps(S3_EVENT)), None, clients=c)
    item = c["table"].items[0]
    assert item["stopReason"] == "max_tokens" and item["parseOk"] is False


def test_duplicate_delivery_does_not_call_bedrock_twice_or_overwrite(monkeypatch):
    _env(monkeypatch)
    c = _clients()
    ocr.handler(_event(json.dumps(S3_EVENT)), None, clients=c)
    first = dict(c["table"].items[0])
    out = ocr.handler(_event(json.dumps(S3_EVENT)), None, clients=c)
    assert out == {"batchItemFailures": []}
    assert len(c["bedrock"].calls) == 1 and c["table"].items == [first]


def test_concurrent_write_is_not_overwritten_and_not_failed(monkeypatch):
    _env(monkeypatch)
    c = _clients(race=True)
    assert ocr.handler(_event(json.dumps(S3_EVENT)), None, clients=c) == {"batchItemFailures": []}
    assert c["table"].items == []


def test_handler_reports_only_failed_messages(monkeypatch):
    _env(monkeypatch)
    c = _clients(fail=True)
    out = ocr.handler(_event(json.dumps(S3_EVENT), json.dumps({"Records": []})), None, clients=c)
    assert out == {"batchItemFailures": [{"itemIdentifier": "m0"}]}  # 画像のない m1 は成功扱い


def test_keys_outside_runs_layout_are_skipped(monkeypatch):
    _env(monkeypatch)
    for key in ("x.txt", "eval-000_original.png", "runs/r1/other/eval-000_original.png"):
        ev = {"Records": [{"s3": {"bucket": {"name": "b"}, "object": {"key": key}}}]}
        c = _clients()
        assert ocr.handler(_event(json.dumps(ev)), None, clients=c) == {"batchItemFailures": []}
        assert c["table"].items == [] and c["bedrock"].calls == [] and c["s3"].calls == 0


def test_api_returns_results_and_errors():
    t = FakeTable()
    t.put_item({"imageKey": "eval-000_original.png", "engineRun": "haiku#r1", "secondsTotal": Decimal("1.5")})
    ok = api.handler({"pathParameters": {"image": "eval-000_original.png"}}, None, table=t)
    assert ok["statusCode"] == 200 and json.loads(ok["body"])["results"][0]["secondsTotal"] == 1.5
    missing = api.handler({"pathParameters": {"image": "none.png"}}, None, table=t)
    assert missing["statusCode"] == 404 and "結果がありません" in json.loads(missing["body"])["error"]
    assert api.handler({"pathParameters": {"image": "abc.jpg"}}, None, table=t)["statusCode"] == 400
