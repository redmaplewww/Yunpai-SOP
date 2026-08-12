# 下一 AI 任务可复制提示词

```text
工作根目录：克隆后的 Yunpai-SOP 仓库根目录

先完整读取：
1. AGENTS.md
2. docs\handoff\sop_template_ai_handoff\README_AI_HANDOFF.md
3. docs\handoff\sop_template_ai_handoff\HANDOFF_MANIFEST.json

要生成的是 yunpai.sop.hdmi-cable.multi-page.v1：第 1 页 A4 纵向完整流程图，第 2 页起按知识库路线逐工序生成 A4 横向标准作业指导书。它不是两页紧凑模板。

唯一入口：
python scripts\generate_sop_template_ai_handoff.py --out-dir <输出目录> --document-date <YYYY-MM-DD> --route-db <sop_knowledge.sqlite3> --route-id <路线ID>

禁止使用 --content-profile hdmi-cable、VariableRouteDocxRenderer、generate_sop_package 或 sop_engineering_drawing 代替。网页预览也必须来自这份 DOCX。

验收：实际页数必须等于 1 + 路线步骤数；第 1 页纵向；其余页横向；没有空白夹页；每个工序有一张指导书；图片待人工上传；批准/审核/制作为空；逐页检查所有 PNG 后才可交付。
```
