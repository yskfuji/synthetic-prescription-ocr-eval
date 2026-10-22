import random
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from kusuri_eval import degrade as dg
from kusuri_eval.drugs import Drug, normalize
from kusuri_eval.synth import LINES_PER_PRESCRIPTION, find_font, make_prescription, render

DRUGS = [Drug(code=f"{i:05d}", name=f"テスト薬{i}錠", unit="錠") for i in range(10)]


def test_normalize_fullwidth_and_space():
    assert normalize("ユーロジン１ｍｇ錠 ") == "ユーロジン1mg錠"


def test_same_seed_same_prescription():
    a = make_prescription("x", DRUGS, random.Random(1))
    b = make_prescription("x", DRUGS, random.Random(1))
    assert a == b
    assert len(a.lines) == LINES_PER_PRESCRIPTION
    assert len({ln.drug for ln in a.lines}) == LINES_PER_PRESCRIPTION  # 同じ薬が重ならない


def test_truth_fields_format():
    p = make_prescription("x", DRUGS, random.Random(2))
    for ln in p.lines:
        assert ln.dose.startswith("1回") and ln.dose.endswith("錠")
        assert ln.days.endswith("日分")


def test_degrade_deterministic_and_different():
    try:
        font = find_font()
    except RuntimeError:
        pytest.skip("日本語フォントがない環境")
    img = render(make_prescription("x", DRUGS, random.Random(3)), font)
    for c in ("tilt", "noise"):
        a, b = dg.degrade(img, c, 7), dg.degrade(img, c, 7)
        assert np.array_equal(np.asarray(a), np.asarray(b))
        assert not np.array_equal(np.asarray(a), np.asarray(img))
    assert np.array_equal(np.asarray(dg.degrade(img, "original", 7)), np.asarray(img))


def test_unknown_condition():
    with pytest.raises(ValueError):
        dg.degrade(Image.new("L", (10, 10)), "blur", 1)
