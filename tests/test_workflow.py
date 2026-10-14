import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(module)
    return module


aggregate_module = load_module("aggregate_style_profile", ROOT / "scripts" / "aggregate_style_profile.py")
builder_module = load_module("build_image_prompt", ROOT / "scripts" / "build_image_prompt.py")


class WorkflowTest(unittest.TestCase):
    def sample_analysis(self):
        images = []
        for index in range(1, 5):
            observations = []
            if index <= 3:
                observations.append({
                    "id": "soft_edge_transitions", "category": "line_edge",
                    "label": "柔和边缘过渡", "polarity": "positive",
                    "evidence": "可见柔和过渡", "confidence": 0.9,
                })
                observations.append({
                    "id": "uniform_hard_outline", "category": "line_edge",
                    "label": "均匀硬黑轮廓", "polarity": "negative",
                    "evidence": "未见硬轮廓", "confidence": 0.9,
                })
            if index <= 2:
                observations.append({
                    "id": "fine_paper_grain", "category": "material_texture",
                    "label": "细纸张颗粒", "polarity": "positive",
                    "evidence": "可见细颗粒", "confidence": 0.9,
                })
            if index == 4:
                observations.append({
                    "id": "cool_shadow_bias", "category": "palette",
                    "label": "冷色阴影倾向", "polarity": "positive",
                    "evidence": "图片压缩导致判断不确定", "confidence": 0.4,
                })
            images.append({
                "image_id": f"img-{index:02d}", "path": f"/{index}.png",
                "sha256": "", "usable": True, "duplicate_of": None,
                "limitations": [], "observations": observations,
            })
        images.append({
            "image_id": "img-05", "path": "/5.png", "sha256": "",
            "usable": True, "duplicate_of": "img-01", "limitations": [],
            "observations": [],
        })
        return {
            "template_name": "月下私语",
            "requirements": "可迁移风格",
            "user_usage": "上传角色图和用户图生成双人图",
            "explicit_negatives": [{
                "id": "watermark_text", "category": "surface_effects",
                "label": "水印和无关文字", "reason": "用户明确禁止",
            }],
            "images": images,
        }

    def sample_spec(self):
        def variable(name):
            return {
                "name": name,
                "description": f"填写{name}",
                "cases": [f"{name}案例{index}" for index in range(1, 11)],
            }

        return {
            "template_name": "月下私语",
            "requirements": "身份不混淆，风格可迁移，输出 2:3 竖图。",
            "user_usage": "用户替换变量后直接用于生图。",
            "prompt_body": "【主体】位于【场景世界观】，采用【画面角度】，穿着【服装】，发型为【发型发色】。",
            "fixed_positive": ["两人互动自然，手部结构正确"],
            "fixed_negative": ["避免文字、Logo 和额外肢体"],
            "variables": [
                variable("主体"),
                variable("场景世界观"),
                variable("画面角度"),
                variable("服装"),
                variable("发型发色"),
            ],
        }

    def test_gate_dedup_and_build(self):
        profile = aggregate_module.aggregate(self.sample_analysis(), 0.60, 0.70)
        self.assertEqual(profile["sample"]["usable_unique_image_count"], 4)
        self.assertEqual(profile["sample"]["minimum_count"], 3)
        self.assertEqual([item["id"] for item in profile["positive_genes"]], ["soft_edge_transitions"])
        self.assertIn("fine_paper_grain", [item["id"] for item in profile["excluded_features"]])
        self.assertIn("cool_shadow_bias", [item["id"] for item in profile["excluded_low_confidence_observations"]])
        self.assertEqual(
            {item["id"] for item in profile["negative_genes"]},
            {"uniform_hard_outline", "watermark_text"},
        )

        constraints = builder_module.load_object(ROOT / "references" / "core-constraints.json")
        prompt = builder_module.build(self.sample_spec(), profile, constraints)
        self.assertEqual(len(prompt["variables"]), 5)
        self.assertEqual(len(prompt["variables"][0]["cases"]), 10)
        self.assertIn("【主体】", prompt["positive_prompt"])
        self.assertNotIn("{{", prompt["positive_prompt"])
        self.assertIn("柔和边缘过渡", prompt["positive_prompt"])
        self.assertIn("自然写实、可明确识别的成年东亚男性五官", prompt["positive_prompt"])
        self.assertIn("避免均匀硬黑轮廓", prompt["negative_prompt"])
        self.assertIn("避免尖下巴、三角形下巴、锥子脸", prompt["negative_prompt"])
        for constraint in constraints["positive"]:
            self.assertIn(constraint, prompt["positive_prompt"])
        for constraint in constraints["negative"]:
            self.assertIn(constraint, prompt["negative_prompt"])
        markdown = builder_module.to_markdown(prompt)
        self.assertIn("## 正向 Prompt", markdown)
        self.assertIn("## 变量案例", markdown)

    def test_rejects_more_than_five_variables(self):
        profile = aggregate_module.aggregate(self.sample_analysis(), 0.60, 0.70)
        constraints = builder_module.load_object(ROOT / "references" / "core-constraints.json")
        spec = self.sample_spec()
        spec["variables"].append({
            "name": "肤色瞳色",
            "description": "填写肤色瞳色",
            "cases": [f"肤色瞳色案例{index}" for index in range(1, 11)],
        })
        spec["prompt_body"] += "肤色与瞳色为【肤色瞳色】。"
        with self.assertRaises(builder_module.InputError):
            builder_module.build(spec, profile, constraints)

    def test_rejects_variable_in_fixed_constraints(self):
        profile = aggregate_module.aggregate(self.sample_analysis(), 0.60, 0.70)
        constraints = builder_module.load_object(ROOT / "references" / "core-constraints.json")
        spec = self.sample_spec()
        spec["fixed_positive"].append("固定画面【服装】")
        with self.assertRaises(builder_module.InputError):
            builder_module.build(spec, profile, constraints)


if __name__ == "__main__":
    unittest.main()
