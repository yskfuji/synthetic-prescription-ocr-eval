"""Bedrock の Claude で画像を読み、薬剤の行(drug/dose/days)を取り出す。

Lambda でも動くよう、標準ライブラリだけを import する(boto3 のクライアントは呼び出し側から渡す)。

使うモデルは Haiku 4.5 と Sonnet 4.6(2026-10-22 に本人が決定)。Haiku 5.5・Sonnet 5.5 は、このアカウントの
TPM 上限が0で呼べなかった。4.5/4.6 世代は temperature を受け付ける(2026-10-22 の試験呼び出しで確認)ので、
再現性のために 0 に固定する。Haiku 5.5・Sonnet 5.5 では既定以外の temperature が 400 になるため、
モデルを戻すときは temperature を送らない設定(TEMPERATURE を空)にする。
思考は、4.5/4.6 では thinking を指定しなければ使わない。maxTokens は余裕を持たせる。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass

# プロンプトは開発用の合成処方箋(dev)だけで調整し、評価(eval)の前に固定する。
# 固定した版の ID と promptHash を結果に記録する。v1-draft のままでは評価を実行できない(eval_run.py)。
PROMPT_VERSION = "v1"  # 2026-10-23 凍結(dev の2回の実行で決定。以後、eval の前に変えない)
# v1-draft → 2026-10-23 dev で修正: 用量に服用回数・タイミングまで含めて返す誤りが両モデルで全件に近く出たため、
# 用量の定義(1回量だけ)を明記した。dev の個別の画像に合わせた変更ではなく、項目の仕様の明確化。
PROMPT = (
    "これは架空の見本処方箋の画像です。薬剤の行を上から順にすべて読み取り、"
    "JSON の配列だけを返してください。説明やコードブロックは付けないでください。"
    '各要素は {"drug": 薬剤名, "dose": 1回量, "days": 日数} です。\n'
    "- drug: 画像の表記どおり(規格や単位も含める)\n"
    "- dose: 「1回」で始まる1回量だけ(例: 1回2錠)。服用回数(例: 1日3回)と服用のタイミング(例: 毎食後)は含めない\n"
    "- days: 日数(例: 14日分)"
)
MAX_TOKENS = 4096
DRAFT_VERSION = "v1-draft"

HAIKU_ID = "au.anthropic.claude-haiku-4-5-20251001-v1:0"
SONNET_ID = "au.anthropic.claude-sonnet-4-6"

# 100万トークンあたりの米ドル(ap-southeast-2、au. プロファイル = Price List の標準の行)。
# 出典: AWS Price List API(AmazonBedrockFoundationModels)、2026-10-22 に取得。
# 5.5 世代は、このアカウントでは呼べないため単価だけ残す(参考)。
PRICES_USD_PER_MTOK = {
    HAIKU_ID: (1.10, 5.50),
    SONNET_ID: (3.30, 16.50),
    "au.anthropic.claude-haiku-5-5": (0.11, 0.55),
    "au.anthropic.claude-sonnet-5-5": (2.20, 11.00),
}


@dataclass(frozen=True)
class EngineResult:
    lines: list[dict]
    parse_ok: bool
    stop_reason: str
    input_tokens: int
    output_tokens: int  # 思考のトークンを含む(課金対象)
    seconds: float  # Converse 呼び出しだけの時間
    cost_usd: float


def inference_config(effort: str | None = None, temperature: float | None = None) -> dict:
    """推論の設定。effort は dev で決めて凍結する(None ならモデルの既定)。temperature は None なら送らない。"""
    cfg: dict = {"inferenceConfig": {"maxTokens": MAX_TOKENS}}
    if temperature is not None:
        cfg["inferenceConfig"]["temperature"] = temperature
    if effort:
        # Converse での effort の渡し方は、最初の呼び出しで確認する(未確認)
        cfg["additionalModelRequestFields"] = {"output_config": {"effort": effort}}
    return cfg


def prompt_hash(model_id: str, effort: str | None = None, temperature: float | None = None) -> str:
    """プロンプト・推論設定・モデル ID をまとめたハッシュ。文字列を変えずにプロンプトを直しても検出できる。"""
    payload = json.dumps(
        {"prompt": PROMPT, "model": model_id, **inference_config(effort, temperature)},
        ensure_ascii=False, sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


def build_request(model_id: str, image_bytes: bytes, effort: str | None = None,
                  temperature: float | None = None) -> dict:
    return {
        "modelId": model_id,
        "messages": [{
            "role": "user",
            "content": [
                {"image": {"format": "png", "source": {"bytes": image_bytes}}},
                {"text": PROMPT},
            ],
        }],
        **inference_config(effort, temperature),
    }


def parse_lines(text: str) -> tuple[list[dict], bool]:
    """モデルの返答から JSON 配列を取り出す。取り出せなければ ([], False)。"""
    start, end = text.find("["), text.rfind("]")
    if start < 0 or end <= start:
        return [], False
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return [], False
    if not isinstance(data, list):
        return [], False
    lines = [
        {k: str(item.get(k, "")) for k in ("drug", "dose", "days")}
        for item in data if isinstance(item, dict)
    ]
    return lines, True


def cost_usd(model_id: str, input_tokens: int, output_tokens: int) -> float:
    p_in, p_out = PRICES_USD_PER_MTOK[model_id]
    return (input_tokens * p_in + output_tokens * p_out) / 1_000_000


def read_image(client, model_id: str, image_bytes: bytes, effort: str | None = None,
               temperature: float | None = None) -> EngineResult:
    t0 = time.perf_counter()
    resp = client.converse(**build_request(model_id, image_bytes, effort, temperature))
    seconds = time.perf_counter() - t0
    # 思考は reasoningContent ブロックで返る。本文は text ブロックだけを読む
    text = "".join(b.get("text", "") for b in resp["output"]["message"]["content"])
    lines, ok = parse_lines(text)
    usage = resp["usage"]
    return EngineResult(
        lines=lines,
        parse_ok=ok,
        stop_reason=resp.get("stopReason", ""),
        input_tokens=usage["inputTokens"],
        output_tokens=usage["outputTokens"],
        seconds=seconds,
        cost_usd=cost_usd(model_id, usage["inputTokens"], usage["outputTokens"]),
    )
