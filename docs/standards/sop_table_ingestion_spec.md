# SOP 表入库规范

适用对象：`yunpai_manufacturing_agent.sop_engineering_drawing`

## 1. 入库结论

SOP 工程图生成后的结构化事实源采用本地 SQLite 表库，建议库路径为：

```text
outputs/sop_case_store/sop_table_cases.sqlite
```

模型只参与 SOP 内容规划、候选复用判断和缺口问题生成；`.docx`、PNG、manifest、格式检查和结构化行数据的入库应由确定性代码执行。模型不得直接写 SQLite、不得批准、发布或锁版 SOP。

## 2. 入库来源

- `structured_sop_request`：产品信息、BOM 物料、工艺路线、工位/设备提示、显式现场记录。
- `sop.docx`：80806-129 风格 Word 原生表格 SOP。
- `center_flowchart.png`：从结构化节点生成并插入第 1 页流程图主体单元格的中心流程图。
- `manifest.json`：生成顺序、状态、产物清单和格式约束。
- `format_check.json`：两节、八个顶层 Word 表、PNG media、无 SVG/VML 等检查结果。
- `parsed_sop.json`：从已填写表格抽取的 SOP 结构化内容。

## 3. 核心表

| 表名 | 作用 |
|---|---|
| `sop_case` | SOP 头信息、产品、文档号、版本、状态、来源、派生来源和检索文本。 |
| `sop_step` | 标准作业指导书六步主体，保留工序号、步骤标题、作业说明、工位、图片引用和顺序槽位。 |
| `sop_flow_node` | 流程图节点，保留节点编号、工序名、节点类型、形状策略、工位和备注。 |
| `sop_flow_edge` | 流程图边，保留起点、终点、条件标签和返工/异常流向。 |
| `sop_ie_time_row` | IE 工时记录，保留动作、机器型号、测量方法、观测次数、平均观测工时、评比系数、宽放率、标准工时、工时来源和动态调整。 |
| `sop_material_item` | SOP 物料表，保留物料编码、名称、规格、数量、单位和备注。 |
| `sop_section` | 右侧作业标准、设备/工具、辅助材料、注意事项、变更内容、物料表以及底部签核/控制文件区域。 |
| `sop_artifact` | `sop.docx`、`center_flowchart.png`、manifest、格式检查、解析 JSON 的路径、哈希和生成状态。 |
| `sop_retrieval_index` | 产品、工位、工序、物料、文档号等确定性检索索引。 |
| `sop_retrieval_index_fts` | SQLite FTS5 可用时建立的全文索引辅助表。 |

## 4. 状态与发布边界

- 默认状态：`demo_not_for_release` 或 `draft_not_for_release`。
- 只有绑定真实现场记录、IE 实测或人工锁定工时、责任角色签核和变更评审后，才可升级到发布候选。
- `DRAFT`、`demo_not_for_release` 和历史候选命中都不代表可下发车间。
- 签核栏缺少真实记录时必须留空，不得填入模拟姓名、模拟日期或模型生成结论。

## 5. 模型边界

模型输入：

- 产品元数据。
- BOM 物料摘要。
- 工艺路线和工位提示。
- Top-K 历史 SOP 候选。
- 已确认的现场记录。

模型输出建议为严格 JSON：

- `sop_content_plan`
- `operation_changes`
- `missing_site_questions`
- `format_risk_notes`

模型禁止事项：

- 直接写 SQLite。
- 批准、发布或锁版 SOP。
- 伪造现场照片、IE 实测工时、设备状态、EHS 审批、OEE、良率、试产结果、培训记录或人工签核。

## 6. 入库校验

入库前至少校验：

- manifest 记录生成顺序：`parse_requirement -> fill_word_tables -> build_structured_flowchart -> render_center_flowchart_png -> insert_png_into_process_flow_body_cell -> validate_docx`。
- `tables_filled_before_flowchart=true`。
- `center_flowchart_target=process_flow_body_table_cell_0_0`。
- Word 文件为两节、八个顶层 Word 表。
- 流程图主体为单格表格，包含生成 PNG，不包含 SVG/VML。
- IE 工时行保留工时来源，非现场实测不得标记为发布版标准工时。
- 底部批准、审核、制作单元格在没有真实记录时为空。
