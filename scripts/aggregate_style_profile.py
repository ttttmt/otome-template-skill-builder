#!/usr/bin/env python3
"""使用可审计的频次门控，聚合逐图风格观察结果。"""

from __future__ import annotations

import argparse
import json
import math
import re
from pathlib import Path
from typing import Any


ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,79}$")
POLARITIES = {"positive", "negative"}


class InputError(ValueError):
    pass


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"无法从 {path} 读取有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise InputError("JSON 顶层必须是对象")
    return value


def required_text(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{key} 必须是非空文本")
    return value.strip()


def validate_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise InputError(f"{label} 必须使用英文小写下划线格式")
    return value


def aggregate(data: dict[str, Any], prevalence: float, confidence_floor: float) -> dict[str, Any]:
    if not 0 < prevalence <= 1:
        raise InputError("prevalence 必须大于 0 且不超过 1")
    if not 0 <= confidence_floor <= 1:
        raise InputError("confidence-floor 必须在 0 到 1 之间")

    template_name = required_text(data, "template_name")
    requirements = required_text(data, "requirements")
    user_usage = required_text(data, "user_usage")
    images = data.get("images")
    if not isinstance(images, list) or not images:
        raise InputError("images 必须是非空数组")

    seen_image_ids: set[str] = set()
    usable: list[dict[str, Any]] = []
    source_summary: list[dict[str, Any]] = []
    for index, image in enumerate(images):
        if not isinstance(image, dict):
            raise InputError(f"images[{index}] 必须是对象")
        image_id = image.get("image_id")
        if not isinstance(image_id, str) or not image_id.strip() or image_id in seen_image_ids:
            raise InputError(f"images[{index}].image_id 必须是唯一的非空文本")
        seen_image_ids.add(image_id)
        is_usable = image.get("usable") is True
        duplicate_of = image.get("duplicate_of")
        if duplicate_of is not None and not isinstance(duplicate_of, str):
            raise InputError(f"{image_id}.duplicate_of 必须是文本或 null")
        counted = is_usable and duplicate_of is None
        source_summary.append({
            "image_id": image_id,
            "path": image.get("path", ""),
            "sha256": image.get("sha256", ""),
            "usable": is_usable,
            "duplicate_of": duplicate_of,
            "counted": counted,
            "limitations": image.get("limitations", []),
        })
        if counted:
            usable.append(image)

    denominator = len(usable)
    if denominator == 0:
        raise InputError("过滤后没有剩余的有效不重复图片")
    minimum_count = math.ceil(prevalence * denominator)

    evidence: dict[tuple[str, str], dict[str, Any]] = {}
    excluded_low_confidence: list[dict[str, Any]] = []
    for image in usable:
        image_id = image["image_id"]
        observations = image.get("observations", [])
        if not isinstance(observations, list):
            raise InputError(f"{image_id}.observations 必须是数组")
        per_image: set[tuple[str, str]] = set()
        for index, obs in enumerate(observations):
            if not isinstance(obs, dict):
                raise InputError(f"{image_id}.observations[{index}] 必须是对象")
            feature_id = validate_id(obs.get("id"), f"{image_id} feature id")
            category = validate_id(obs.get("category"), f"{image_id} category")
            label = required_text(obs, "label")
            polarity = obs.get("polarity")
            if polarity not in POLARITIES:
                raise InputError(f"{image_id}/{feature_id} 的 polarity 只能是 positive 或 negative")
            confidence = obs.get("confidence")
            if not isinstance(confidence, (int, float)) or isinstance(confidence, bool) or not 0 <= confidence <= 1:
                raise InputError(f"{image_id}/{feature_id} 的 confidence 必须在 0 到 1 之间")
            item = {
                "id": feature_id,
                "category": category,
                "label": label,
                "polarity": polarity,
                "image_id": image_id,
                "evidence": str(obs.get("evidence", "")).strip(),
                "confidence": float(confidence),
            }
            if confidence < confidence_floor:
                excluded_low_confidence.append(item)
                continue
            key = (feature_id, polarity)
            if key in per_image:
                continue
            per_image.add(key)
            current = evidence.setdefault(key, {
                "id": feature_id,
                "category": category,
                "label": label,
                "polarity": polarity,
                "supporting_images": [],
                "evidence": [],
            })
            if current["category"] != category or current["label"] != label:
                raise InputError(f"特征 {feature_id}/{polarity} 的 category 或 label 不一致")
            current["supporting_images"].append(image_id)
            if item["evidence"]:
                current["evidence"].append({
                    "image_id": image_id,
                    "observation": item["evidence"],
                    "confidence": item["confidence"],
                })

    accepted_positive: list[dict[str, Any]] = []
    accepted_negative: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    for item in evidence.values():
        count = len(item["supporting_images"])
        result = dict(item)
        result["count"] = count
        result["denominator"] = denominator
        result["prevalence"] = round(count / denominator, 4)
        result["source"] = "reference_images"
        if count >= minimum_count:
            (accepted_positive if item["polarity"] == "positive" else accepted_negative).append(result)
        else:
            result["exclusion_reason"] = "below_prevalence_threshold"
            excluded.append(result)

    explicit_negatives = data.get("explicit_negatives", [])
    if not isinstance(explicit_negatives, list):
        raise InputError("explicit_negatives 必须是数组")
    for index, item in enumerate(explicit_negatives):
        if not isinstance(item, dict):
            raise InputError(f"explicit_negatives[{index}] 必须是对象")
        accepted_negative.append({
            "id": validate_id(item.get("id"), f"explicit_negatives[{index}].id"),
            "category": validate_id(item.get("category"), f"explicit_negatives[{index}].category"),
            "label": required_text(item, "label"),
            "polarity": "negative",
            "source": "explicit_user_requirement",
            "reason": str(item.get("reason", "")).strip(),
            "supporting_images": [],
            "evidence": [],
            "count": None,
            "denominator": denominator,
            "prevalence": None,
        })

    order_key = lambda item: (item["category"], item["id"], item["polarity"])
    accepted_positive.sort(key=order_key)
    accepted_negative.sort(key=order_key)
    excluded.sort(key=order_key)
    excluded_low_confidence.sort(key=lambda item: (item["image_id"], item["category"], item["id"]))

    conflicts = sorted(
        {item["id"] for item in accepted_positive}
        & {item["id"] for item in accepted_negative}
    )
    warnings: list[str] = []
    if denominator < 3:
        warnings.append("有效不重复图片少于 3 张：频次计算在数学上有效，但统计结论较弱。")
    if conflicts:
        warnings.append("存在同时被接纳为正向和负向的特征 ID，需要人工检查：" + ", ".join(conflicts))

    return {
        "format": "otome-style-profile/v1",
        "template_name": template_name,
        "brief": {"requirements": requirements, "user_usage": user_usage},
        "sample": {
            "original_image_count": len(images),
            "usable_unique_image_count": denominator,
            "prevalence_threshold": prevalence,
            "minimum_count": minimum_count,
            "confidence_floor": confidence_floor,
        },
        "positive_genes": accepted_positive,
        "negative_genes": accepted_negative,
        "excluded_features": excluded,
        "excluded_low_confidence_observations": excluded_low_confidence,
        "source_images": source_summary,
        "warnings": warnings,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prevalence", type=float, default=0.60)
    parser.add_argument("--confidence-floor", type=float, default=0.70)
    args = parser.parse_args()
    try:
        profile = aggregate(load_json(args.input), args.prevalence, args.confidence_floor)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except InputError as exc:
        parser.error(str(exc))
    print(json.dumps({
        "output": str(args.output.resolve()),
        "usable_unique_images": profile["sample"]["usable_unique_image_count"],
        "minimum_count": profile["sample"]["minimum_count"],
        "positive_genes": len(profile["positive_genes"]),
        "negative_genes": len(profile["negative_genes"]),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
