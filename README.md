# Yunpai SOP

云湃 SOP 是从 M2 中独立拆出的制造工艺知识、人工审核和 DOCX 生成模块。

核心能力：

- 独立 SQLite 工艺知识库，保存产品、可变工艺路线、工序、人工审核和历史复用索引；
- 工作人员自然语言修改 SOP，AI 负责定位工序并写入待核对草稿；
- 第 1 页 A4 纵向完整流程图，第 2 页起按路线逐工序生成 A4 横向标准作业指导书；
- 浏览器左侧预览真实 DOCX 转换的 PDF，右侧自然语言对话修改；
- 图片、工艺参数、IE 实测和签核信息必须由人工提供或确认；
- 不自动批准、发布或替代现场工艺/质量/EHS 责任人。

## 安装

```powershell
python -m pip install -e .
```

## 启动作业台

```powershell
python -m cad_ai.sop_knowledge.web `
  --db outputs/sop_process_knowledge/sop_knowledge.sqlite3 `
  --host 127.0.0.1 `
  --port 8787
```

打开 `http://127.0.0.1:8787/`。仓库只提供这一套“自然语言对话 + DOCX/PDF 预览”前端，不再包含旧的结构化填表工作台。

## 生成路线型 HDMI SOP

```powershell
python scripts/generate_sop_template_ai_handoff.py `
  --out-dir outputs/hdmi_sop `
  --document-date 2026-08-12 `
  --route-db outputs/sop_process_knowledge/sop_knowledge.sqlite3 `
  --route-id 1
```

HDMI 最终模板 ID 为 `yunpai.sop.hdmi-cable.multi-page.v1`。旧的固定两页 HDMI 路由会失败关闭；USB-C 两页文件只保留为历史参考。

详细交接规范见 [`docs/handoff/sop_template_ai_handoff/README_AI_HANDOFF.md`](docs/handoff/sop_template_ai_handoff/README_AI_HANDOFF.md)。

## 测试

```powershell
python -m unittest discover -s tests -p "test_sop_*.py" -v
```

## 凭据

复制 `.env.example` 为 `.env.local` 并在本机填写。不要提交真实 API Key。
