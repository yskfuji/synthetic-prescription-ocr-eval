"""採点。docs/acceptance-criteria.md の「Q1 の計算方法(固定)」に従う。

- 単位は薬剤の1行。分母は正解の行数(結果が無い・読み落とした行は不正解)。
- 正解の行と予測の行は、正規化した薬剤名の類似度(difflib)の合計が最大になる組み合わせで対応づける。
  同点なら並び順に近い組み合わせを選ぶ。対応しなかった正解の行は「不足」、予測の行は「余分」として別に数える。
- 項目(drug/dose/days)ごとに、normalize 後の完全一致で正誤を決める。
- 95% 信頼区間は、元の処方箋を単位にしたブートストラップ(同じ処方箋の行は相関するため)。
  ブートストラップは境界で狭くなりすぎる(幅0になる)ため、境界の近くでは処方箋単位の Clopper–Pearson 区間を使う:
  - 1に近い側: 誤りのある処方箋が BOUNDARY_ERRORS 件未満なら、全行正解の処方箋の数で区間を作る
    (行単位の正解率はこの割合以上なので、下限が保守的になる)
  - 0に近い側: 正解が1行でもある処方箋が BOUNDARY_ERRORS 件未満なら、その数で区間を作る
    (行単位の正解率はこの割合以下なので、上限が保守的になる。下限は点推定と比べて小さい方)
"""
from __future__ import annotations

import math
import random
from difflib import SequenceMatcher
from itertools import permutations

from .drugs import normalize

FIELDS = ("drug", "dose", "days")
BOOTSTRAP_RESAMPLES = 2000
BOOTSTRAP_SEED = 20261024
BOUNDARY_ERRORS = 5
ALPHA = 0.05


def align(truth_lines: list[dict], pred_lines: list[dict]) -> list[int | None]:
    """正解の各行に対応する予測の行の番号(無ければ None)を返す。"""
    t = [normalize(x["drug"]) for x in truth_lines]
    p = [normalize(str(x.get("drug", ""))) for x in pred_lines]
    sim = [[SequenceMatcher(None, a, b).ratio() for b in p] for a in t]
    slots: list[int | None] = list(range(len(p))) + [None] * len(t)
    best, best_key = None, None
    for cand in set(permutations(slots, len(t))):
        score = sum(sim[i][j] for i, j in enumerate(cand) if j is not None)
        displacement = sum(abs(i - j) for i, j in enumerate(cand) if j is not None)
        key = (round(score, 9), -displacement, tuple(-1 if j is None else j for j in cand))
        if best_key is None or key > best_key:
            best, best_key = list(cand), key
    return best if best is not None else [None] * len(t)


def score_prescription(truth_lines: list[dict], pred_lines: list[dict]) -> dict:
    """項目ごとの (正解数, 行数) と、不足・余分の行数。"""
    mapping = align(truth_lines, pred_lines)
    out: dict = {f: [0, len(truth_lines)] for f in FIELDS}
    for t, j in zip(truth_lines, mapping):
        p = pred_lines[j] if j is not None else {}
        for f in FIELDS:
            if normalize(str(p.get(f, ""))) == normalize(t[f]):
                out[f][0] += 1
    matched = sum(j is not None for j in mapping)
    result = {f: (c, n) for f, (c, n) in out.items()}
    result["missing"] = len(truth_lines) - matched
    result["extra"] = len(pred_lines) - matched
    return result


def _binom_cdf(k: int, n: int, p: float) -> float:
    return sum(math.comb(n, i) * p**i * (1 - p) ** (n - i) for i in range(k + 1))


def clopper_pearson(x: int, n: int, alpha: float = ALPHA) -> tuple[float, float]:
    """二項比率の正確な信頼区間(二分法で求める。scipy に頼らない)。"""
    def solve(f, lo=0.0, hi=1.0):
        for _ in range(100):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if f(mid) else (lo, mid)
        return (lo + hi) / 2

    lower = 0.0 if x == 0 else solve(lambda p: 1 - _binom_cdf(x - 1, n, p) < alpha / 2)
    upper = 1.0 if x == n else solve(lambda p: _binom_cdf(x, n, p) > alpha / 2)
    return lower, upper


def _acc(rows: list[dict], f: str) -> float:
    return sum(r[f][0] for r in rows) / sum(r[f][1] for r in rows)


def _bootstrap(stat, k: int) -> tuple[float, float]:
    rng = random.Random(BOOTSTRAP_SEED)
    boots = sorted(stat([rng.randrange(k) for _ in range(k)]) for _ in range(BOOTSTRAP_RESAMPLES))
    return (boots[int(ALPHA / 2 * BOOTSTRAP_RESAMPLES)],
            boots[int((1 - ALPHA / 2) * BOOTSTRAP_RESAMPLES) - 1])


def accuracy_with_ci(per_prescription: list[dict]) -> dict[str, dict]:
    k = len(per_prescription)
    result = {}
    for f in FIELDS:
        point = _acc(per_prescription, f)
        perfect = sum(r[f][0] == r[f][1] for r in per_prescription)
        any_correct = sum(r[f][0] > 0 for r in per_prescription)
        if k - perfect < BOUNDARY_ERRORS:
            ci, method = clopper_pearson(perfect, k), "clopper_pearson_prescription"
        elif any_correct < BOUNDARY_ERRORS:
            lo, hi = clopper_pearson(any_correct, k)
            ci, method = (min(lo, point), hi), "clopper_pearson_prescription_low"
        else:
            ci = _bootstrap(lambda idx: _acc([per_prescription[i] for i in idx], f), k)
            method = "cluster_bootstrap"
        result[f] = {"accuracy": point, "ci95": list(ci), "ci_method": method, "n_prescriptions": k,
                     "n_lines": sum(r[f][1] for r in per_prescription)}
    return result


def paired_difference_ci(per_a: list[dict], per_b: list[dict]) -> dict[str, dict]:
    """同じ処方箋の並びで、正解率の差(a − b)と 95% 区間。

    結果が食い違う処方箋が BOUNDARY_ERRORS 件未満のときは、ブートストラップが幅0に近くなるため、
    処方箋単位の Clopper–Pearson で保守的な区間にする: a が劣る処方箋1件は差を最大で 1/k 下げるので、
    下限は −(a が劣る処方箋の割合の上限)、上限は +(a が勝る処方箋の割合の上限)とする。
    """
    assert len(per_a) == len(per_b)
    k = len(per_a)
    out = {}
    for f in FIELDS:
        diff = _acc(per_a, f) - _acc(per_b, f)
        a_worse = sum(a[f][0] < b[f][0] for a, b in zip(per_a, per_b))
        a_better = sum(a[f][0] > b[f][0] for a, b in zip(per_a, per_b))
        if a_worse + a_better < BOUNDARY_ERRORS:
            lo = min(diff, -clopper_pearson(a_worse, k)[1])
            hi = max(diff, clopper_pearson(a_better, k)[1])
            out[f] = {"diff": diff, "ci95": [lo, hi], "ci_method": "clopper_pearson_discordant"}
            continue

        def stat(idx):
            return _acc([per_a[i] for i in idx], f) - _acc([per_b[i] for i in idx], f)
        out[f] = {"diff": diff, "ci95": list(_bootstrap(stat, k)), "ci_method": "cluster_bootstrap"}
    return out


def percentile_nearest_rank(xs: list[float], q: float) -> float:
    """nearest-rank 法(Hyndman & Fan の type 1)。p95 なら小さい方から ceil(0.95n) 番目。"""
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]
