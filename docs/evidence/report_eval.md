split=eval git=fe7001869b8b promptHash=9b863683476a7a34,9edeae0ca67a5229 処方箋=100

| エンジン | 条件 | 項目 | 正解率 | 95%CI | CI の方法 | 結果なし | parse 失敗 | 行の不足/余分 | p50/p95 秒 |
|---|---|---|---|---|---|---|---|---|---|
| haiku | original | drug | 0.947 | [0.923, 0.970] | cluster_bootstrap | 0 | 0 | 0/0 | 1.60/2.23 |
| haiku | original | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.60/2.23 |
| haiku | original | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.60/2.23 |
| haiku | tilt | drug | 0.933 | [0.907, 0.957] | cluster_bootstrap | 0 | 0 | 0/0 | 1.64/2.01 |
| haiku | tilt | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.64/2.01 |
| haiku | tilt | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.64/2.01 |
| haiku | noise | drug | 0.943 | [0.917, 0.970] | cluster_bootstrap | 0 | 0 | 0/0 | 1.81/3.03 |
| haiku | noise | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.81/3.03 |
| haiku | noise | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.81/3.03 |
| sonnet | original | drug | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.86/2.15 |
| sonnet | original | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.86/2.15 |
| sonnet | original | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.86/2.15 |
| sonnet | tilt | drug | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.87/2.23 |
| sonnet | tilt | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.87/2.23 |
| sonnet | tilt | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.87/2.23 |
| sonnet | noise | drug | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.98/2.18 |
| sonnet | noise | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.98/2.18 |
| sonnet | noise | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 1.98/2.18 |
| tesseract | original | drug | 0.893 | [0.857, 0.927] | cluster_bootstrap | 0 | 0 | 0/0 | 0.58/0.70 |
| tesseract | original | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 0.58/0.70 |
| tesseract | original | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 0.58/0.70 |
| tesseract | tilt | drug | 0.383 | [0.323, 0.447] | cluster_bootstrap | 0 | 0 | 1/0 | 0.61/0.71 |
| tesseract | tilt | dose | 0.820 | [0.767, 0.867] | cluster_bootstrap | 0 | 0 | 1/0 | 0.61/0.71 |
| tesseract | tilt | days | 0.910 | [0.873, 0.943] | cluster_bootstrap | 0 | 0 | 1/0 | 0.61/0.71 |
| tesseract | noise | drug | 0.877 | [0.833, 0.913] | cluster_bootstrap | 0 | 0 | 0/0 | 0.62/0.75 |
| tesseract | noise | dose | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 0.62/0.75 |
| tesseract | noise | days | 1.000 | [0.964, 1.000] | clopper_pearson_prescription | 0 | 0 | 0/0 | 0.62/0.75 |

判定: {"q1": {"haiku": {"pass": true, "unconfirmed_cells": []}, "sonnet": {"pass": true, "unconfirmed_cells": []}, "pass": true}, "q2": {"haiku": {"p95_max": 3.0308, "pass": true}, "sonnet": {"p95_max": 2.2317, "pass": true}}, "q3": {"haiku": {"total_usd": 0.7027019999999999, "per_image_usd": 0.0023423399999999996, "pass_per_image": true}, "sonnet": {"total_usd": 2.045109, "per_image_usd": 0.00681703, "pass_per_image": true}, "eval_total_usd": 2.747811, "pass_total": true}}
