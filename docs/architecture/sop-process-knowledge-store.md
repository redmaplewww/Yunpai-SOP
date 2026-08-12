# SOP 工艺路线知识库架构

## 目标

把“理解工艺路线”与“排版 SOP”解耦。路线步骤数量、层级、字段、证据和审核状态由独立 SQLite 数据库决定；渲染器只消费已验证路线。

核心原则是**工艺路线优先**：先建立并逐项人工核对工序真源，再输出工作指导书。只有完成正式生产批准并生成不可变人工审批快照的路线，才可进入默认历史复用索引。

## 模块与接口

| 模块 | 实现 | 输入 | 输出 |
|---|---|---|---|
| source ingestion | `ProfileSourceIngestion` | 产品号、只读资料 profile | 文件清单、资料类型、存在性 |
| identity/conflict resolver | `DeterministicIdentityConflictResolver` | ingestion 输出 | `ProductIdentity`、冲突 |
| feature extractor | `DeterministicFeatureExtractor` | 产品身份、资料 | `ProductFeatureSet` |
| evidence ledger | `SqliteEvidenceLedger` | `EvidenceRef` | 持久 evidence id/hash |
| route retriever | `ApprovedOnlyRouteRetriever` | 料号、工艺族、特征 | `RouteMatch[]`、相似度、字段来源 |
| route drafter | `EvidenceBoundRouteDrafter` | 身份、特征、资料 | `RouteDraft` |
| worker review/editor | `cad_ai.sop_knowledge.web` | 自然语言提案、route/step/media/search API | 预览后填草稿、逐项人工确认、相似搜索 |
| validator | `RouteValidator` | `RouteDraft` | 结构、unknown、防混检查 |
| renderer/exporter | `VariableRouteDocxRenderer` | 数据库 route id | route JSON、DOCX、校验 JSON |

Pydantic schema 位于 `cad_ai/sop_knowledge/models.py`；SQLite DDL 位于 `cad_ai/sop_knowledge/schema.sql`。

默认持久化文件为 `outputs/sop_process_knowledge/sop_knowledge.sqlite3`，SOP 主入口仅把它作为独立工艺知识数据库使用；旧两页模板数据库不参与正式路线检索。

## 领域模型

数据库覆盖 `product / product_alias / product_feature`、`process_family`、带版本的 `operation_template`、可变 `product_route / route_step`、版本化 `route_section`、`evidence_source / field_provenance`、`review_session / review_decision / approval_snapshot` 和 `reuse_link`。`route_step.parent_step_id` 表达父子步骤；每步存储 action、why、input、material、tool/equipment、fixture、parameter、method、quality、acceptance、safety、record/output、exception、unknowns。

工作人员扩展表包括：`nl_change_proposal`（自然语言原文、结构化预览、解析方式和应用状态）、`media_asset / step_media`（人工上传图片与工序绑定状态）、`knowledge_fragment / knowledge_fragment_fts`（逐工序人工确认快照与检索索引）。自然语言应用固定写为 `needs_revision`；工序人工确认后才能建立知识片段，图片也只有在同一确认动作后才能被指导书渲染器消费。

知识分两层：`knowledge_fragment` 是人工确认过的可检索参考，不自动获得生产复用资格；`route_fts` 仍只接收整条路线通过 `formal_production` 人工批准后的不可变快照。前端必须分别显示“人工确认参考”和“正式可复用”，禁止把片段确认等同于 SOP 发布。

`route_section` 固定覆盖八个路线级审核域：`product_identity`、`bom_material`、`equipment_fixture`、`process_parameter`、`quality_control`、`packaging_label`、`ie_timing`、`release_signoff`。每次编辑不覆盖旧行，而是创建递增版本，保存 `content_json`、审核状态/意见、字段来源、冲突和结构化unknown，并写入对应的 `review_decision`。

`product_fts` 与 `route_fts` 提供全文索引；别名、工艺族和规范化特征参与检索。

## 状态与不可变性

路线和模板版本支持 `draft / under_review / approved / deprecated`。路线批准时写入 SHA-256 不可变快照；数据库 trigger 禁止直接修改 approved route/step。修改必须调用 `create_revision()` 创建新版本。驳回路线转为 deprecated，不写 approved 索引。

正式相似检索默认只读取 `formal_production`；演示路线只有显式 `allow_demonstration=True` 才可检索，避免把演示审批冒充生产批准。

## 正式批准事务闸门

`formal_production` 批准在同一个 SQLite 事务内强制检查：

1. `approved_by` 必须是非空身份；
2. 所有 route step 都是 `confirmed`；
3. 八类 route section 都存在且最新版本为 `confirmed`；
4. step 和 section 的 blocking unknown 总数为零；
5. section conflict 为空，正文无未展开占位符、泛化“待确认”或同族其他型号；
6. 确认token必须严格等于 `FORMAL_APPROVE:<目标产品号>`。

任何检查失败都会抛出结构化gate错误并回滚，不产生 `approval_snapshot`，也不写正式 `route_fts`。`demonstration_only` 可以保留blocking unknown，但审批范围写入快照且默认检索排除。

## unknown 约束

unknown 不是一个自由文本占位符。每项必须包含：字段、具体缺失原因、确认角色、所需受控资料/现场证据、是否阻断。`待确认`、`unknown` 或 `未知` 这类无责任人与证据要求的条目会被 schema 拒绝。

## 备份与迁移

`SopKnowledgeStore.backup()` 生成 SQLite 备份，`export_json()` 导出全部业务表。数据库不依赖资料项目；资料只读路径和文件哈希作为 evidence ledger 保存。Schema 升级由 `schema_migration` 和 `migrations/001_route_sections_and_formal_gate.sql` 留痕。
