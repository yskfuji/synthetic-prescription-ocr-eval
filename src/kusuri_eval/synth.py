"""合成処方箋の仕様(正解)の生成と、画像への描画。

すべて架空のデータ。実在の処方箋・医療機関・医師・患者は使わない。
正解は乱数のシードから作り、画像を読み取った結果から作らない。
"""
from __future__ import annotations

import os
import random
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from .drugs import Drug

FAMILY = ["架空", "見本", "試験", "仮名", "例示"]
GIVEN = ["太郎", "花子", "一郎", "桜", "健", "美咲", "翼", "葵"]
CLINIC = ["サンプル総合病院", "テスト記念病院", "見本クリニック"]
DAYS_CHOICES = [7, 14, 21, 28, 30, 35, 56, 60, 90]
DOSE_CHOICES = [1, 2, 3]
TIMES = [("1日1回 朝食後", 1), ("1日2回 朝夕食後", 2), ("1日3回 毎食後", 3), ("1日1回 就寝前", 1)]
LINES_PER_PRESCRIPTION = 3
IMAGE_SIZE = (1240, 800)  # A5 横を150dpi 相当で描く想定


@dataclass(frozen=True)
class MedLine:
    drug: str  # 正解: 薬剤名(正規化済み)
    dose: str  # 正解: 用量(例: 1回2錠)
    days: str  # 正解: 日数(例: 14日分)
    schedule: str  # 画像には描くが、採点しない(服用回数・タイミング)


@dataclass(frozen=True)
class Prescription:
    pid: str
    patient: str
    clinic: str
    lines: list[MedLine]

    def to_json(self) -> dict:
        return asdict(self)


def make_prescription(pid: str, drugs: list[Drug], rng: random.Random) -> Prescription:
    picked = rng.sample(drugs, LINES_PER_PRESCRIPTION)
    lines = []
    for d in picked:
        n = rng.choice(DOSE_CHOICES)
        schedule, _ = rng.choice(TIMES)
        lines.append(MedLine(
            drug=d.name,
            dose=f"1回{n}{d.unit}",
            days=f"{rng.choice(DAYS_CHOICES)}日分",
            schedule=schedule,
        ))
    return Prescription(
        pid=pid,
        patient=f"{rng.choice(FAMILY)} {rng.choice(GIVEN)}",
        clinic=rng.choice(CLINIC),
        lines=lines,
    )


def find_font() -> str:
    env = os.environ.get("KUSURI_FONT")
    if env and Path(env).exists():
        return env
    out = subprocess.run(
        ["fc-match", "-f", "%{file}", "YuGothic:lang=ja"], capture_output=True, text=True
    ).stdout.strip()
    if out and Path(out).exists():
        return out
    raise RuntimeError("日本語フォントが見つかりません。環境変数 KUSURI_FONT にフォントのパスを指定してください")


def render(p: Prescription, font_path: str | None = None) -> Image.Image:
    font_path = font_path or find_font()
    f_title = ImageFont.truetype(font_path, 44)
    f_body = ImageFont.truetype(font_path, 34)
    f_small = ImageFont.truetype(font_path, 26)
    img = Image.new("L", IMAGE_SIZE, 255)
    d = ImageDraw.Draw(img)
    d.text((60, 40), "処 方 箋", font=f_title, fill=0)
    d.text((IMAGE_SIZE[0] - 520, 50), "見本・架空データ", font=f_small, fill=90)
    d.text((60, 120), f"医療機関: {p.clinic}", font=f_small, fill=0)
    d.text((60, 165), f"患者: {p.patient} 様  (ID {p.pid})", font=f_small, fill=0)
    d.line((60, 215, IMAGE_SIZE[0] - 60, 215), fill=0, width=2)
    y = 260
    for i, ln in enumerate(p.lines, 1):
        d.text((60, y), f"{i})  {ln.drug}", font=f_body, fill=0)
        d.text((120, y + 50), f"{ln.dose}   {ln.schedule}   {ln.days}", font=f_body, fill=0)
        y += 150
    return img
