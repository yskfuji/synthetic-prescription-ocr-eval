"""劣化条件。パラメータは定数で固定する(docs/acceptance-criteria.md の Q4)。

- original: 加工しない
- tilt: 3〜6度のどちらかの向きに回転(符号と角度はシードで決める)、背景は白
- noise: ガウスノイズ(標準偏差 20)を加え、JPEG 品質 40 で圧縮し直す
"""
from __future__ import annotations

import io
import random

import numpy as np
from PIL import Image

CONDITIONS = ("original", "tilt", "noise")
TILT_MIN_DEG, TILT_MAX_DEG = 3.0, 6.0
NOISE_SIGMA = 20.0
JPEG_QUALITY = 40


def degrade(img: Image.Image, condition: str, seed: int) -> Image.Image:
    if condition == "original":
        return img.copy()
    rng = random.Random(seed)
    if condition == "tilt":
        angle = rng.uniform(TILT_MIN_DEG, TILT_MAX_DEG) * rng.choice((-1, 1))
        return img.rotate(angle, resample=Image.BICUBIC, expand=False, fillcolor=255)
    if condition == "noise":
        arr = np.asarray(img, dtype=np.float32)
        gen = np.random.default_rng(seed)
        arr = np.clip(arr + gen.normal(0.0, NOISE_SIGMA, arr.shape), 0, 255).astype(np.uint8)
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="JPEG", quality=JPEG_QUALITY)
        buf.seek(0)
        return Image.open(buf).convert("L")
    raise ValueError(f"未知の劣化条件: {condition}")
