# SOP 模板 AI 交接包

## 0. AI 强制起始指令

如果你是接手任务的 AI，必须完整阅读本文件和同目录 `HANDOFF_MANIFEST.json` 后再执行。当前 HDMI SOP 的已确认结构不是两页模板：

- 第 1 页：A4 纵向完整工艺流程图；
- 第 2 页起：A4 横向标准作业指导书；
- 指导书按工艺路线逐工序续页，页数由路线步骤数决定，不得压缩成一页或固定两页；
- 每张指导书保留六个动作/图片区，视觉顺序为 `1,2,3 / 6,5,4`；
- 图片默认空白，等待人工上传和确认；
- 文档始终为 `DRAFT`，批准、审核、制作值栏必须为空。

当前模板 ID：`yunpai.sop.hdmi-cable.multi-page.v1`。

## 1. 工作根目录和唯一入口

工作根目录：本仓库根目录（即包含 `pyproject.toml` 和 `cad_ai/` 的目录）。

唯一生成入口：`scripts/generate_sop_template_ai_handoff.py`。HDMI 必须使用知识库路线模式：

```powershell
python scripts\generate_sop_template_ai_handoff.py `
  --out-dir outputs\deliverables\hdmi_sop_template `
  --document-date 2026-08-12 `
  --route-db outputs\deliverables\hdmi_process_knowledge_sop_20260812\knowledge\sop_knowledge.sqlite3 `
  --route-id 1
```

禁止使用 `--content-profile hdmi-cable` 生成固定两页文件；生成器会主动拒绝该旧路由。

## 2. 数据和分页契约

生成器必须先从 `SopKnowledgeStore` 读取指定路线，再执行：

```text
读取产品与全部路线步骤
-> 第 1 页填写 Word 原生流程表格
-> 从路线步骤派生纵向流程图 PNG
-> 每个路线步骤建立一张横向指导书
-> 填写动作、作业标准、设备工具、材料、注意事项和记录要求
-> 图片区保持待人工上传
-> 应用 DRAFT、空白签核与受控日期
-> 校验 DOCX
-> Word 转 PDF/PNG
-> 检查每一页
```

对于当前 HDMI 路线 1，共有 17 个步骤，因此应渲染 18 页：1 页流程图 + 17 页指导书。若路线增删步骤，指导书页数随之变化。

## 3. 输出契约

目标包只包含五个正式产物：

| 文件 | 用途 |
|---|---|
| `SOP完整模板_HDMI线制作_草案.docx` | 最终 Word 草案 |
| `center_flowchart.png` | 第 1 页流程图事实产物 |
| `sop_template_manifest.json` | 模板、路线、页数和安全边界 |
| `sop_template_format_check.json` | 版式检查 |
| `sop_template_validation.json` | DOCX 结构校验 |

浏览器预览必须由同一 DOCX 转换为 PDF/PNG，不能用 HTML 仿制页面冒充 DOCX 预览。

## 4. 结构与视觉验收

必须满足：

- 第 1 section 纵向，后续指导书 section 横向；
- 顶层表格数为 `4 + 工序数 × 4`；
- 每个工序都有指导书页，页码为 `工序序号 OF 工序总数`；
- 每页指导书六步视觉顺序为 `1,2,3 / 6,5,4`；
- 每页包含 IE 工时记录表，但没有伪造的现场实测值；
- 每页批准、审核、制作值栏为空；
- 无空白夹页、截断、重叠、乱码或表格越界；
- 实际渲染页数等于 `1 + 工序数`。

当前正式回归命令：

```powershell
python -m unittest tests.test_sop_template_ai_handoff tests.test_sop_conversational_docx -v
```

交付前还必须打开所有 `page-*.png` 逐页检查。结构校验通过不等于视觉验收通过。

## 5. Web 对话修改

`cad_ai.sop_knowledge.documents.SopDocumentService` 必须调用上述唯一生成器。工作人员在简版页面用自然语言修改工序后，系统更新路线知识库、重新生成同一模板 DOCX，再用该 DOCX 刷新 PDF 预览。不得回退到 `VariableRouteDocxRenderer` 的通用版式。

## 6. 知识沉淀和安全边界

人工确认的路线、步骤和字段决定写入独立 SQLite 知识库，后续可按产品身份、特征和工艺族检索复用。不得自动发布 SOP、自动填写签核、自动批准变更，也不得伪造设备状态、工艺参数、测试限值、IE 实测、EHS、OEE、良率、试产、培训或人工评审记录。

## 7. USB-C 旧样包边界

`yunpai.sop.usb_c_cable_packaging.two_page.v1` 仅保留为 USB-C 包装的历史两页参考样包，不是 HDMI 当前模板，不能拿来处理 HDMI 或任意可变工艺路线。
