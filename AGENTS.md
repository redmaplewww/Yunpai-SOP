# Yunpai SOP Agent 说明

本仓库只维护 SOP 工艺知识、人工审核、自然语言修改、DOCX 生成和预览能力。

## HDMI 模板唯一入口

处理 HDMI SOP 或“这份 SOP 模板”前，必须完整读取：

- `docs/handoff/sop_template_ai_handoff/README_AI_HANDOFF.md`
- `docs/handoff/sop_template_ai_handoff/HANDOFF_MANIFEST.json`

唯一生成入口为 `scripts/generate_sop_template_ai_handoff.py`。HDMI 必须使用 `--route-db` 和 `--route-id`，生成第 1 页纵向完整流程图以及按工序续页的横向指导书。不得使用固定两页 HDMI 路由。

## 安全边界

- 不伪造设备、参数、IE 实测、EHS、OEE、良率、试产、培训或人工签核；
- 不自动批准、发布、关闭异常或替代人工评审；
- 人工确认内容才能进入确认知识索引，正式复用仍需整条路线人工批准；
- `.env`、`.env.local`、运行数据库和生成产物不得提交。

## 回归

```powershell
python -m unittest discover -s tests -p "test_sop_*.py" -v
```
