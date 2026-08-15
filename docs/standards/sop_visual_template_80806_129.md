# SOP Word 表格模板规范：80806-129 双段式格式

更新日期：2026-07-06

## 1. 适用范围

本规范用于云湃智造一体机生成 80806-129 风格的 SOP Word 表格模板。当前默认产物为 `.docx`，使用 Word 原生表格表达流程图页和标准作业指导书页。

本规范不把 Excel、CSV 或 SVG 作为默认交付物，也不把 Word 页面做成一张仿 Excel 的整页网格。

## 2. 两段式结构

SOP Word 文档必须包含两个 section：

1. `流程图`：对应 PDF 第 1 页“流程图”，竖版 A4。
2. `标准作业指导书`：对应 PDF 第 2 页起“标准作业指导书”，横版 A4。

## 3. 流程图页

必须包含：

- 标题：`流程图`
- 表头字段：`品名`、`料号`、`页数`、`作业部门`、`版本`、`制定日期`、`图号`、`核准`、`审核`、`拟订`、`文件编号`
- 主体：中间保留一个大的空白流程图填充栏，后续再向其中填入流程图
- IE工时记录：流程级工时汇总、机器型号记录和动态调整说明
- 页脚：可记录 `EF-42013-23 (REV. A)` 等表单编号

流程图页采用四块连续的 Word 表格：表头表、单格空白主体表、IE工时记录表、页脚表。主体区不得预先铺 Word 表格网格或工序节点。

## 4. 标准作业指导书页

必须包含：

- 标题：`标准作业指导书`
- 表头字段：`产品品名`、`本厂料号`、`工站`、`文件编号`、`制作日期`、`版本`、`页码`、`作业顺序`
- 左侧栏：`图片流程描述及说明`
- 六个图片区/文字区：上排 `1,2,3`，下排 `6,5,4`
- 每个步骤块用 Word 表格单元表达图片区和文字说明区
- IE工时记录：按动作记录 IE 测时、机器型号和标准工时

标准作业指导书页采用四块连续的 Word 表格：表头表、六步主体表、IE工时记录表、底部签核表。右侧栏不再压成一段文本，而是用 Word 表格行表达各栏目。

右侧栏固定为：

1. `作业标准`
2. `设备/工具`
3. `辅助材料`
4. `注意事项`
5. `变更内容`
6. `物料表`

底部栏固定为：

1. `批准`
2. `审核`
3. `制作`
4. `材料环保要求`
5. `管制文件（印章处）`
6. `图号`

## 5. IE工时记录

每页必须包含 `IE工时记录` 表。字段固定为：

1. `动作`
2. `机器型号`
3. `IE测量方法`
4. `单价`
5. `人数`
6. `评比系数`
7. `宽放率`
8. `标准工时(s)`
9. `工时来源`
10. `动态调整`

填写规则：

- 空白模板默认写 `待IE实测`、`待填` 或留空，不伪造实测工时。
- 标准作业指导书页按动作记录工时，动作顺序跟随 `1,2,3 / 6,5,4` 的作业步骤。
- 机器型号必须与动作绑定，支持同一工序在不同设备/治具上的标准工时差异。
- 单价、人数和标准工时均由 IE/授权人员按现场资料填写，模板不得估算。
- `工时来源` 可为 `待IE实测`、`理论估算`、`历史均值`、`现场实测`、`人工锁定`。
- 发布版 SOP 的标准工时必须来自 `现场实测` 或 `人工锁定`；理论估算和历史均值只能用于草案。
- `动态调整` 用于记录 MES 实绩、IE 复测、机器型号变更、治具变更或工艺变更后的更新策略。

## 6. Agent 填写规则

- 文字可由 Agent 基于 BOM、routing、历史 SOP 文案和用户明确输入生成。
- 图片区默认留空或写入图片占位/图片引用，不生成伪现场图。
- IE 工时字段可由 Agent 生成草案估算，但必须标注来源；不得把估算值标成现场实测。
- `批准`、`审核`、`制作` 等签核区默认留空，除非有真实人工或系统审批记录。
- 正式现场图片、设备状态、人工签核、EHS 记录、试产结果、IE 实测工时必须来自用户输入或外部系统。
- 正式发布状态必须保持草案，除非有人工或系统审批记录。

### 6.1 填表与流程图生成顺序

接到 SOP 需求后，Agent 必须先根据需求/BOM/工艺路线填写 Word 原生表格，再绘制流程图并放到第 1 页流程图主体表的唯一中心单元格。

固定顺序为：`parse_requirement -> fill_word_tables -> build_structured_flowchart -> render_center_flowchart_png -> insert_png_into_process_flow_body_cell -> validate_docx`。

流程图不是独立先画的图，而是由已填好的表格内容派生出来的中心图。填充型 SOP manifest 必须记录 `tables_filled_before_flowchart=true` 与 `center_flowchart_target=process_flow_body_table_cell_0_0`。

## 7. 代码入口

当前实现：

- `cad_ai/sop_visual_template.py`
- `scripts/generate_sop_batch_samples.py`

主要 Word 版式函数：

- `write_sop_word_template_package(out_dir)`：生成空白 80806-129 风格 Word 表格文档。
- `write_demo_sop_word_package(out_dir)`：生成 USB-C 简易数据线样例 Word 表格文档。
- `generate_sop_batch_samples(out_dir, sample_count=3..5)`：生成 3 到 5 份 DEMO SOP 收尾测试包，先填 Word 表格，再绘制中心流程图并插入指定单元格。

默认输出：

- `sop_80806_129_word_template.docx`
- `sop_80806_129_word_manifest.json`
- `sop_80806_129_word_format_check.json`
- `demo_usb_cable_sop_word.docx`
- `demo_usb_cable_sop_word_manifest.json`
- `demo_usb_cable_sop_word_format_check.json`

测试：

- `python -m unittest tests.test_sop_visual_template -v`

## 8. 验收要求

模板格式验收至少覆盖：

- 输出格式为 `.docx` + `.json`，不包含 `.xlsx`、`.csv` 或 `.svg`。
- Word 文档包含竖版流程图 section 和横版标准作业指导书 section。
- Word 文档包含 8 个顶层 Word 表格：流程图表头、流程图主体、流程图IE工时、流程图页脚、作业指导书表头、作业指导书主体、作业指导书IE工时、作业指导书签核栏。
- 流程图页使用 Word 表格表达表头和一个单格大空白流程图栏。
- 标准作业指导书页使用 Word 表格表达表头、六个图片/文字步骤块、右侧栏和底部签核栏。
- 每页包含 `IE工时记录`，且字段包含 `机器型号`、`标准工时(s)`、`工时来源`、`动态调整`。
- 六个步骤块视觉顺序为 `1,2,3 / 6,5,4`。
- 签核栏不预填虚构人名。

- 填充型 SOP 必须先填 Word 表格，再从表格内容派生结构化流程图，渲染 PNG 后插入流程图主体单元格。
- 批量收尾测试必须生成 3 到 5 份 `.docx` 样例，并逐份验证 2 个 section、8 个顶层 Word 表格、单格流程图主体、PNG media、无 SVG/VML、IE 工时表和空白签核栏。

## 9. 禁止事项

- 不得把该模板默认做成 Excel。
- 不得在 Word 中用一张整页大表模拟 Excel 网格。
- 不得把该模板默认做成 CSV 数据表。
- 不得把该模板默认做成 SVG 图。
- 不得伪造现场照片、人工签核、设备状态、EHS 审批、OEE、良率、试产结果或 IE 实测工时。
- 不得把草案模板标记为已发布车间执行版 SOP。

## 10. FastAPI 服务接口

SOP agent 已封装为 `cad_ai.sop_agent` 与 `cad_ai.sop_api`：

- `cad_ai.sop_agent.generate_sop_package(request)`：生成草案 SOP 包，输出 Word `.docx`、中心流程图 `.png`、manifest、格式检查 JSON 和解析后的 SOP JSON。
- `cad_ai.sop_api.create_sop_fastapi_app(default_out_dir=...)`：创建 FastAPI app。
- CLI 启动：`python -m cad_ai sop-api --host 127.0.0.1 --port 8780 --out outputs/sop_agent_api`。

FastAPI 路由：

- `GET /api/sop/health`：查看 SOP agent 状态、草案边界、生成顺序和路由契约。
- `POST /api/sop/generate`：根据 BOM、工艺路线、设备线索和需求文本生成 SOP 草案包。
- `GET /api/sop/runs/{run_id}`：读取生成 run 的 manifest。
- `GET /api/sop/runs/{run_id}/artifacts/{artifact_key}`：下载 `document_docx`、`center_flowchart_png`、`manifest_json`、`format_check_json` 或 `parsed_sop_json`。

接口默认仍遵守草案边界：`status=demo_not_for_release`，不自动发布 SOP，不自动签核，不伪造现场 IE 实测、设备状态、EHS 审批、OEE、良率或试产结果。正式发布仍需人工锁版和现场证据。
