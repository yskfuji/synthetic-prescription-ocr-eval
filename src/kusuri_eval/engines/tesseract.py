"""Tesseract(日本語)で画像を読み、薬剤の行(drug/dose/days)に解析する。"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from ..drugs import normalize

_DETAIL = re.compile(r"1回|日分")
_DOSE = re.compile(r"1回\d+(?:錠|カプセル)")
_DAYS = re.compile(r"\d+日分")
_NUMBERING = re.compile(r"^\d+[)）\]]")


def ocr_text(image: Path, lang: str = "jpn", psm: int = 6) -> str:
    r = subprocess.run(
        ["tesseract", str(image), "stdout", "-l", lang, "--psm", str(psm)],
        capture_output=True, text=True, check=True,
    )
    return r.stdout


def parse_lines(text: str) -> list[dict]:
    """OCR 結果の文字列から、薬剤の行を取り出す。

    「1回」か「日分」を含む行を用量の行とみなし、その直前の行を薬剤名の行とする。
    """
    lines = [normalize(ln) for ln in text.splitlines() if normalize(ln)]
    out: list[dict] = []
    for i, ln in enumerate(lines):
        if not _DETAIL.search(ln) or i == 0:
            continue
        prev = lines[i - 1]
        if _DETAIL.search(prev):
            continue
        dose = _DOSE.search(ln)
        days = _DAYS.search(ln)
        out.append({
            "drug": _NUMBERING.sub("", prev),
            "dose": dose.group(0) if dose else "",
            "days": days.group(0) if days else "",
        })
    return out


def read_image(image: Path) -> list[dict]:
    return parse_lines(ocr_text(image))
