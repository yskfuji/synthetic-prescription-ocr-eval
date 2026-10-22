import math

import pytest

from kusuri_eval.engines.tesseract import parse_lines
from kusuri_eval.score import (
    accuracy_with_ci,
    align,
    clopper_pearson,
    paired_difference_ci,
    percentile_nearest_rank,
    score_prescription,
)

TRUTH = [
    {"drug": "アデムパス錠2.5mg", "dose": "1回3錠", "days": "7日分"},
    {"drug": "ディオバンOD錠40mg", "dose": "1回2錠", "days": "56日分"},
    {"drug": "ロトリガ粒状カプセル2g", "dose": "1回3カプセル", "days": "28日分"},
]


def test_parse_lines_from_spaced_ocr_text():
    text = "処 方 箋\n患者: 見本 健 様\n1) アデムパス錠 2.5mg\n1回 3 錠 1日3回 毎食後 7日分\n\n2) ディオバン OD 錠 40mg\n1回2錠 1日1回 朝食後 56日分\n"
    assert parse_lines(text) == [
        {"drug": "アデムパス錠2.5mg", "dose": "1回3錠", "days": "7日分"},
        {"drug": "ディオバンOD錠40mg", "dose": "1回2錠", "days": "56日分"},
    ]


def test_skipped_first_line_does_not_shift_the_rest():
    # 並び順で対応づけると、1行目の読み落としで3行とも不正解になっていた(C9)
    s = score_prescription(TRUTH, TRUTH[1:])
    assert s["drug"] == (2, 3) and s["dose"] == (2, 3) and s["days"] == (2, 3)
    assert s["missing"] == 1 and s["extra"] == 0


def test_extra_lines_are_counted_separately():
    pred = TRUTH + [{"drug": "幻の薬", "dose": "1回1錠", "days": "1日分"}]
    s = score_prescription(TRUTH, pred)
    assert s["drug"] == (3, 3) and s["extra"] == 1 and s["missing"] == 0


def test_alignment_tolerates_ocr_noise_and_prefers_order_on_ties():
    noisy = [{"drug": "ディオバン0D錠40mg"}, {"drug": "アデムパス錠2.5ｍｇ"}, {"drug": "ロトリガ粒状カプセル2g"}]
    assert align(TRUTH, noisy) == [1, 0, 2]
    same = [{"drug": "X錠"}, {"drug": "X錠"}]
    assert align([{"drug": "X錠"}, {"drug": "X錠"}], same) == [0, 1]


def test_fullwidth_matches_after_normalize_and_wrong_field_counts():
    pred = [{"drug": "アデムパス錠2.5ｍｇ", "dose": "1回3錠", "days": "1日分"}]
    s = score_prescription(TRUTH[:1], pred)
    assert s["drug"] == (1, 1) and s["days"] == (0, 1)


def test_clopper_pearson_known_values():
    lo, hi = clopper_pearson(100, 100)
    assert hi == 1.0 and math.isclose(lo, 0.025 ** (1 / 100), rel_tol=1e-6)  # 0.9638
    lo, hi = clopper_pearson(0, 10)
    assert lo == 0.0 and math.isclose(hi, 1 - 0.025 ** (1 / 10), rel_tol=1e-6)
    lo, hi = clopper_pearson(50, 100)
    assert lo == pytest.approx(0.3983, abs=1e-3) and hi == pytest.approx(0.6017, abs=1e-3)


def test_boundary_uses_clopper_pearson_not_zero_width_bootstrap():
    perfect = [score_prescription(TRUTH, TRUTH)] * 100
    r = accuracy_with_ci(perfect)["dose"]
    assert r["accuracy"] == 1.0 and r["ci_method"] == "clopper_pearson_prescription"
    assert r["ci95"][0] < 0.97 and r["ci95"][1] == 1.0  # 幅が0にならない


def test_low_boundary_is_not_zero_width():
    # 0に近い側でも幅0にしない(dev で Sonnet の用量が 0.000 [0.000, 0.000] になった不具合)
    wrong = [score_prescription(TRUTH, [{**t, "dose": "誤り"} for t in TRUTH])] * 20
    r = accuracy_with_ci(wrong)["dose"]
    assert r["accuracy"] == 0.0 and r["ci_method"] == "clopper_pearson_prescription_low"
    assert r["ci95"][0] == 0.0 and r["ci95"][1] == pytest.approx(1 - 0.025 ** (1 / 20), rel=1e-6)  # 0.168


def test_interior_uses_cluster_bootstrap():
    per = [score_prescription(TRUTH, TRUTH)] * 50 + [score_prescription(TRUTH, [])] * 50
    r = accuracy_with_ci(per)["drug"]
    assert r["accuracy"] == 0.5 and r["ci_method"] == "cluster_bootstrap"
    assert 0 <= r["ci95"][0] < 0.5 < r["ci95"][1] <= 1 and r["n_lines"] == 300


def test_paired_difference():
    good = [score_prescription(TRUTH, TRUTH)] * 20
    half = [score_prescription(TRUTH, TRUTH)] * 10 + [score_prescription(TRUTH, [])] * 10
    d = paired_difference_ci(half, good)["drug"]
    assert d["diff"] == pytest.approx(-0.5) and d["ci95"][0] <= -0.5 <= d["ci95"][1] < 0


def test_paired_difference_without_discordance_is_not_zero_width():
    # 食い違いが無いと、ブートストラップの差の区間が [0, 0] になっていた(dev で発生)
    same = [score_prescription(TRUTH, TRUTH)] * 100
    d = paired_difference_ci(same, same)["dose"]
    assert d["diff"] == 0.0 and d["ci_method"] == "clopper_pearson_discordant"
    lo, hi = d["ci95"]
    assert lo == pytest.approx(-(1 - 0.025 ** (1 / 100)), rel=1e-6) and hi == pytest.approx(1 - 0.025 ** (1 / 100), rel=1e-6)
    assert lo > -0.05  # 100枚・食い違い0なら、許容差5pt の非劣性は示せる


def test_percentile_nearest_rank():
    xs = list(range(1, 301))  # 1..300
    assert percentile_nearest_rank(xs, 0.95) == 285  # ceil(0.95*300)=285 番目
    assert percentile_nearest_rank(xs, 0.50) == 150
    assert percentile_nearest_rank([7.0], 0.95) == 7.0
