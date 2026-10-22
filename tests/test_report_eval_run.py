import hashlib
import json
import subprocess

import pytest

from kusuri_eval import eval_run, report
from kusuri_eval.engines import claude

LINES = [{"drug": "A錠", "dose": "1回1錠", "days": "7日分", "schedule": "1日1回"}]


def _truths(tmp_path, n=3):
    d = tmp_path / "eval"
    d.mkdir()
    for i in range(n):
        (d / f"eval-{i:03d}.json").write_text(json.dumps({"pid": f"eval-{i:03d}", "lines": LINES}), encoding="utf-8")
    return d


def _item(lines=LINES, seconds=1.0, cost=0.001):
    return {"lines": lines, "parseOk": True, "stopReason": "end_turn", "secondsTotal": seconds, "costUsd": cost}


def _all(n=3, **kw):
    return {f"eval-{i:03d}_{c}.png": _item(**kw) for i in range(n) for c in ("original", "tilt", "noise")}


def test_missing_results_count_as_wrong_with_fixed_denominator(tmp_path):
    d = _truths(tmp_path)
    results = _all()
    del results["eval-000_original.png"]
    rep = report.build(d, {"haiku": results})
    cell = rep["scores"]["haiku"]["original"]
    assert cell["missing_results"] == 1
    assert cell["accuracy"]["drug"]["n_lines"] == 3  # 分母は正解ファイルから
    assert cell["accuracy"]["drug"]["accuracy"] == pytest.approx(2 / 3)


def test_judge_point_estimate_any_engine_and_q2_q3(tmp_path):
    d = _truths(tmp_path)
    bad = {k: _item(lines=[]) for k in _all()}
    rep = report.build(d, {"haiku": bad, "sonnet": _all(seconds=2.0, cost=0.01)})
    v = rep["verdict"]
    assert v["q1"]["haiku"]["pass"] is False and v["q1"]["sonnet"]["pass"] is True and v["q1"]["pass"] is True
    assert v["q2"]["sonnet"]["pass"] is True and v["q2"]["sonnet"]["p95_max"] == 2.0
    assert v["q3"]["sonnet"]["per_image_usd"] == pytest.approx(0.01) and v["q3"]["sonnet"]["pass_per_image"]
    ni = rep["noninferiority_haiku_vs_sonnet"]["original"]["drug"]
    assert ni["diff"] == pytest.approx(-1.0) and ni["noninferior"] is False


def test_q3_fails_when_per_image_cost_exceeds_target(tmp_path):
    d = _truths(tmp_path)
    rep = report.build(d, {"sonnet": _all(cost=0.03)})
    assert rep["verdict"]["q3"]["sonnet"]["pass_per_image"] is False


def test_markdown_has_27_rows_for_three_engines(tmp_path):
    d = _truths(tmp_path)
    rep = report.build(d, {"haiku": _all(), "sonnet": _all(), "tesseract": _all()})
    body = [ln for ln in report.to_markdown(rep).splitlines() if ln.startswith("| ") and "エンジン" not in ln]
    assert len(body) == 27


def _fake_git(status="", tag_rc=0):
    def run(*args):
        if args[0] == "status":
            return subprocess.CompletedProcess(args, 0, status, "")
        if args[0] == "describe":
            return subprocess.CompletedProcess(args, tag_rc, "prereg-v1\n" if tag_rc == 0 else "", "")
        return subprocess.CompletedProcess(args, 0, "abc123\n", "")
    return run


def test_eval_refuses_when_not_frozen(monkeypatch):
    monkeypatch.setattr(eval_run, "_git", _fake_git(status=" M x.py", tag_rc=128))
    monkeypatch.setattr(claude, "PROMPT_VERSION", claude.DRAFT_VERSION)
    with pytest.raises(eval_run.NotFrozenError) as e:
        eval_run.check_frozen()
    assert "v1-draft" in str(e.value) and "未コミット" in str(e.value) and "タグ" in str(e.value)


def test_eval_allowed_when_frozen(monkeypatch):
    monkeypatch.setattr(eval_run, "_git", _fake_git())
    monkeypatch.setattr(claude, "PROMPT_VERSION", "v1")
    assert eval_run.check_frozen() == {"gitSha": "abc123", "tag": "prereg-v1"}


def _dataset(tmp_path, tamper=False):
    d = tmp_path / "dev"
    d.mkdir()
    (d / "dev-000_original.png").write_bytes(b"img")
    digest = hashlib.sha256(b"img").hexdigest()
    manifest = {"splits": {"dev": {"items": [{"file": "dev-000_original.png", "sha256": digest}]}}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if tamper:
        (d / "dev-000_original.png").write_bytes(b"changed")
    return manifest


def test_upload_dry_run_uses_runs_layout(tmp_path, monkeypatch):
    _dataset(tmp_path)
    monkeypatch.setattr(eval_run, "_git", _fake_git())
    rec = eval_run.upload("dev", tmp_path, "bucket", dry_run=True)
    assert rec["runId"].startswith("dev-") and rec["images"] == 1
    assert (tmp_path / "runs" / f"{rec['runId']}.json").exists()


def test_upload_is_paced_below_rpm_limit(tmp_path, monkeypatch):
    d = tmp_path / "dev"
    d.mkdir()
    items = []
    for i in range(3):
        name = f"dev-00{i}_original.png"
        (d / name).write_bytes(bytes([i]))
        items.append({"file": name, "sha256": hashlib.sha256(bytes([i])).hexdigest()})
    (tmp_path / "manifest.json").write_text(json.dumps({"splits": {"dev": {"items": items}}}), encoding="utf-8")
    monkeypatch.setattr(eval_run, "_git", _fake_git())

    class S3:
        def __init__(self):
            self.keys = []

        def upload_file(self, path, bucket, key):
            self.keys.append(key)

    s3, waits = S3(), []
    rec = eval_run.upload("dev", tmp_path, "bucket", dry_run=False, s3=s3, per_minute=8, sleep=waits.append)
    assert len(s3.keys) == 3 and all(k.startswith(f"runs/{rec['runId']}/dev/") for k in s3.keys)
    assert waits == [7.5, 7.5]  # 1分あたり8枚 = 7.5秒おき。最初の1枚は待たない
    assert rec["perMinute"] == 8


def test_upload_rejects_sha_mismatch(tmp_path, monkeypatch):
    _dataset(tmp_path, tamper=True)
    monkeypatch.setattr(eval_run, "_git", _fake_git())
    with pytest.raises(RuntimeError, match="sha256"):
        eval_run.upload("dev", tmp_path, "bucket", dry_run=True)
