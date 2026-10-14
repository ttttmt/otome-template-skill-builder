# 分析输入数据格式

`aggregate_style_profile.py` 接收以下结构的 UTF-8 JSON：

```json
{
  "template_name": "月下私语",
  "requirements": "保留柔和、朦胧、低刺激的视觉气质",
  "user_usage": "用户上传角色图和自拍，生成双人纪念图",
  "explicit_negatives": [
    {
      "id": "watermark_text",
      "category": "surface_effects",
      "label": "水印和无关文字",
      "reason": "用户明确禁止"
    }
  ],
  "images": [
    {
      "image_id": "img-01",
      "path": "/absolute/path/01.png",
      "sha256": "可选哈希值",
      "usable": true,
      "duplicate_of": null,
      "limitations": [],
      "observations": [
        {
          "id": "soft_edge_transitions",
          "category": "line_edge",
          "label": "柔和边缘过渡",
          "polarity": "positive",
          "evidence": "轮廓主要由明暗过渡形成",
          "confidence": 0.91
        },
        {
          "id": "uniform_hard_outline",
          "category": "line_edge",
          "label": "均匀硬黑轮廓",
          "polarity": "negative",
          "evidence": "可见边界均由柔和明暗形成，无均匀描边",
          "confidence": 0.86
        }
      ]
    }
  ]
}
```

约束：

- `template_name`、`requirements`、`user_usage` 和 `images` 为必填项。
- 每个 `image_id` 必须唯一。
- `usable=false` 的图片，以及 `duplicate_of` 不为空的图片，不计入统计分母。
- `id` 和 `category` 使用英文小写下划线格式。
- `polarity` 只能是 `positive` 或 `negative`。
- `confidence` 的取值范围为 0 到 1。低于脚本置信度下限的观察结果会被记录为排除项，不参与频次计算。
- 同一“特征与极性”组合在每张图片中最多计数一次。
- `explicit_negatives` 不受频次门槛限制，但必须清楚标记为用户明确要求。
