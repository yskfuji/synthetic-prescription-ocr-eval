"""厚生労働省の薬価基準収載品目リスト(内用薬)から、合成処方箋に使う薬剤を読み込む。

出典: 厚生労働省ホームページ「薬価基準収載品目リスト及び後発医薬品に関する情報について」
https://www.mhlw.go.jp/topics/2026/04/tp20260401-01.html を加工して作成。
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import openpyxl

UNIT_PATTERNS = (("カプセル", "カプセル"), ("錠", "錠"))
MAX_NAME_LEN = 24


@dataclass(frozen=True)
class Drug:
    code: str
    name: str  # NFKC 正規化済みの品名(例: ユーロジン1mg錠)
    unit: str  # 錠 / カプセル


def normalize(text: str) -> str:
    """表記ゆれの正規化。全角半角を NFKC で揃え、空白を除く。採点でも同じ関数を使う。"""
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text))


def _unit_of(name: str) -> str | None:
    for key, unit in UNIT_PATTERNS:
        if key in name:
            return unit
    return None


def load_drugs(xlsx_path: str | Path) -> list[Drug]:
    wb = openpyxl.load_workbook(xlsx_path, read_only=True)
    ws = wb.worksheets[0]
    rows = ws.iter_rows(values_only=True)
    header = next(rows)
    idx = {h: i for i, h in enumerate(header) if h}
    code_i, name_i = idx["薬価基準収載医薬品コード"], idx["品名"]
    seen: set[str] = set()
    drugs: list[Drug] = []
    for row in rows:
        raw = row[name_i]
        if not raw:
            continue
        name = normalize(str(raw))
        unit = _unit_of(name)
        # 「」や括弧を含む後発品名、長すぎる名前、重複は除く(画像上で一行に収め、曖昧さを減らす)
        if unit is None or len(name) > MAX_NAME_LEN or name in seen:
            continue
        if any(ch in name for ch in "「」()（）"):
            continue
        seen.add(name)
        drugs.append(Drug(code=str(row[code_i]), name=name, unit=unit))
    return sorted(drugs, key=lambda d: d.code)
