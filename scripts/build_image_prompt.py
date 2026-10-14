#!/usr/bin/env python3
"""根据 Prompt 规范、风格档案和固定核心约束生成可直接生图的提示词。"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONSTRAINTS = ROOT / "references" / "core-constraints.json"
PLACEHOLDER_RE = re.compile(r"【([^【】\n]+)】")


class InputError(ValueError):
    pass


def load_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise InputError(f"无法从 {path} 读取有效 JSON：{exc}") from exc
    if not isinstance(value, dict):
        raise InputError(f"{path} 必须包含一个 JSON 对象")
    return value


def required_text(obj: dict[str, Any], key: str) -> str:
    value = obj.get(key)
    if not isinstance(value, str) or not value.strip():
        raise InputError(f"{key} 必须是非空文本")
    return value.strip()


def text_list(obj: dict[str, Any], key: str, allow_empty: bool = True) -> list[str]:
    value = obj.get(key, [])
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise InputError(f"{key} 必须是由非空文本组成的数组")
    if not allow_empty and not value:
        raise InputError(f"{key} 不能为空")
    return [item.strip() for item in value]


def validate_spec(spec: dict[str, Any]) -> None:
    for key in ["template_name", "requirements", "user_usage", "prompt_body"]:
        required_text(spec, key)
    fixed_positive = text_list(spec, "fixed_positive")
    fixed_negative = text_list(spec, "fixed_negative")
    for item in fixed_positive + fixed_negative:
        if PLACEHOLDER_RE.search(item) or "{{" in item or "}}" in item:
            raise InputError("固定约束必须使用普通文本，不能包含变量占位符")
    variables = spec.get("variables")
    if not isinstance(variables, list) or not 1 <= len(variables) <= 5:
        raise InputError("variables 必须包含 1–5 个变量")
    names: list[str] = []
    for index, variable in enumerate(variables):
        if not isinstance(variable, dict):
            raise InputError(f"variables[{index}] 必须是对象")
        name = required_text(variable, "name")
        if any(mark in name for mark in "【】{}[]"):
            raise InputError(f"变量名称不能包含括号：{name}")
        if name in names:
            raise InputError(f"变量名称重复：{name}")
        names.append(name)
        required_text(variable, "description")
        cases = text_list(variable, "cases", allow_empty=False)
        if len(cases) != 10 or len(set(cases)) != 10:
            raise InputError(f"变量【{name}】必须提供正好 10 个互不重复的案例")
    placeholders = PLACEHOLDER_RE.findall(spec["prompt_body"])
    if set(placeholders) != set(names):
        missing = sorted(set(names) - set(placeholders))
        unknown = sorted(set(placeholders) - set(names))
        details = []
        if missing:
            details.append("正文缺少变量：" + "、".join(missing))
        if unknown:
            details.append("正文包含未声明变量：" + "、".join(unknown))
        raise InputError("；".join(details))
    if "{{" in spec["prompt_body"] or "}}" in spec["prompt_body"]:
        raise InputError("变量必须使用【】表示，不能使用双大括号")
    for name in names:
        wrong_forms = ("{{" + name + "}}", "{" + name + "}", "[" + name + "]")
        if any(form in spec["prompt_body"] for form in wrong_forms):
            raise InputError(f"变量【{name}】只能使用全角方括号表示")


def validate_profile(profile: dict[str, Any], template_name: str) -> None:
    if profile.get("format") != "otome-style-profile/v1":
        raise InputError("不支持当前风格档案格式")
    if profile.get("template_name") != template_name:
        raise InputError("Prompt 规范和风格档案中的 template_name 不一致")
    for key in ["positive_genes", "negative_genes", "excluded_features"]:
        if not isinstance(profile.get(key), list):
            raise InputError(f"风格档案中的 {key} 必须是数组")
    if not profile["positive_genes"]:
        raise InputError("没有正向风格基因通过频次门槛；请补充风格一致的参考图")


def validate_constraints(constraints: dict[str, Any]) -> None:
    if constraints.get("format") != "otome-core-constraints/v1":
        raise InputError("不支持当前核心约束格式")
    required_text(constraints, "scope")
    text_list(constraints, "positive", allow_empty=False)
    text_list(constraints, "negative", allow_empty=False)


def unique(items: list[str]) -> list[str]:
    return list(dict.fromkeys(item.strip() for item in items if item and item.strip()))


def gene_labels(profile: dict[str, Any], key: str) -> list[str]:
    labels: list[str] = []
    for item in profile[key]:
        label = item.get("label")
        if isinstance(label, str) and label.strip():
            labels.append(label.strip())
    return unique(labels)


def negative_gene_phrases(profile: dict[str, Any]) -> list[str]:
    phrases = []
    for label in gene_labels(profile, "negative_genes"):
        phrases.append(label if label.startswith(("避免", "禁止", "不要", "无")) else "避免" + label)
    return phrases


def build(spec: dict[str, Any], profile: dict[str, Any], constraints: dict[str, Any]) -> dict[str, Any]:
    validate_spec(spec)
    validate_profile(profile, spec["template_name"])
    validate_constraints(constraints)

    positive_parts = unique([
        spec["prompt_body"].strip(),
        "制作要求：" + spec["requirements"].strip(),
        *text_list(spec, "fixed_positive"),
        "风格固定约束：" + "；".join(gene_labels(profile, "positive_genes")),
        constraints["scope"].strip(),
        *[item.strip() for item in constraints["positive"]],
    ])
    negative_parts = unique([
        *text_list(spec, "fixed_negative"),
        *negative_gene_phrases(profile),
        *[item.strip() for item in constraints["negative"]],
    ])
    variables = [
        {
            "name": variable["name"].strip(),
            "placeholder": f"【{variable['name'].strip()}】",
            "description": variable["description"].strip(),
            "cases": [item.strip() for item in variable["cases"]],
        }
        for variable in spec["variables"]
    ]
    sample = profile.get("sample", {})
    return {
        "format": "otome-image-prompt/v1",
        "template_name": spec["template_name"].strip(),
        "user_usage": spec["user_usage"].strip(),
        "positive_prompt": "\n".join(positive_parts),
        "negative_prompt": "\n".join(negative_parts),
        "variables": variables,
        "audit": {
            "usable_unique_images": sample.get("usable_unique_image_count"),
            "minimum_count": sample.get("minimum_count"),
            "accepted_positive_genes": gene_labels(profile, "positive_genes"),
            "accepted_negative_genes": gene_labels(profile, "negative_genes"),
            "excluded_features": gene_labels(profile, "excluded_features"),
            "warnings": profile.get("warnings", []),
        },
    }


def to_markdown(result: dict[str, Any]) -> str:
    sections = [
        f"# {result['template_name']}",
        "## 正向 Prompt\n\n```text\n" + result["positive_prompt"] + "\n```",
        "## 负向 Prompt\n\n```text\n" + result["negative_prompt"] + "\n```",
        "## 变量案例",
    ]
    for variable in result["variables"]:
        cases = "\n".join(f"{index}. {case}" for index, case in enumerate(variable["cases"], 1))
        sections.append(f"### {variable['placeholder']}\n\n{variable['description']}\n\n{cases}")
    audit = result["audit"]
    warnings = audit["warnings"] or ["无额外警告"]
    sections.append(
        "## 风格统计摘要\n\n"
        f"- 有效不重复图片：{audit['usable_unique_images']}\n"
        f"- 60% 门槛数量：{audit['minimum_count']}\n"
        f"- 正向风格基因：{'、'.join(audit['accepted_positive_genes'])}\n"
        f"- 负向风格基因：{'、'.join(audit['accepted_negative_genes']) or '无'}\n"
        f"- 被过滤特征：{'、'.join(audit['excluded_features']) or '无'}\n"
        f"- 限制：{'；'.join(warnings)}"
    )
    return "\n\n".join(sections) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--profile", type=Path, required=True)
    parser.add_argument("--constraints", type=Path, default=DEFAULT_CONSTRAINTS)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--json-output", type=Path)
    args = parser.parse_args()
    try:
        result = build(load_object(args.spec), load_object(args.profile), load_object(args.constraints))
    except InputError as exc:
        parser.error(str(exc))
    markdown = to_markdown(result)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(markdown, encoding="utf-8")
    else:
        print(markdown, end="")
    if args.json_output:
        args.json_output.parent.mkdir(parents=True, exist_ok=True)
        args.json_output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
