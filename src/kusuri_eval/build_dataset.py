"""開発用(20枚)と評価用(100枚)の合成処方箋を data/ に書き出す。

使い方: uv run python -m kusuri_eval.build_dataset --master data/raw/naiyo_20261014.xlsx
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
from pathlib import Path

from . import degrade as dg
from .drugs import load_drugs
from .synth import find_font, make_prescription, render

SPLITS = {"dev": (20, 20261022), "eval": (100, 20261023)}  # (枚数, シード)


def build(master: Path, out: Path) -> dict:
    drugs = load_drugs(master)
    font = find_font()
    manifest = {"drug_count": len(drugs), "font": Path(font).name, "splits": {}}
    for split, (n, seed) in SPLITS.items():
        rng = random.Random(seed)
        d = out / split
        d.mkdir(parents=True, exist_ok=True)
        items = []
        for i in range(n):
            pid = f"{split}-{i:03d}"
            p = make_prescription(pid, drugs, rng)
            (d / f"{pid}.json").write_text(
                json.dumps(p.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
            base = render(p, font)
            for c in dg.CONDITIONS:
                img = dg.degrade(base, c, seed=seed * 1000 + i)
                path = d / f"{pid}_{c}.png"
                img.save(path)
                items.append({"file": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        manifest["splits"][split] = {"n": n, "seed": seed, "images": len(items), "items": items}
    manifest["degrade"] = {
        "tilt_deg": [dg.TILT_MIN_DEG, dg.TILT_MAX_DEG],
        "noise_sigma": dg.NOISE_SIGMA,
        "jpeg_quality": dg.JPEG_QUALITY,
    }
    (out / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
    return manifest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--master", type=Path, required=True)
    ap.add_argument("--out", type=Path, default=Path("data"))
    a = ap.parse_args()
    m = build(a.master, a.out)
    print({k: (v if k != "splits" else {s: x["images"] for s, x in v.items()}) for k, v in m.items() if k != "items"})


if __name__ == "__main__":
    main()
