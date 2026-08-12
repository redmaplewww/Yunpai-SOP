# SOP 路由重构执行记录（2026-08-11）

1. 审计旧调用链并用 3/8 步输入复现固定六步填充/截断。
2. 将旧 10 份批次标记为不可交付并保留证据。
3. 建立 SQLite schema、Pydantic 模型、证据账本、approved-only 检索、版本与审批快照。
4. 实现可变路线 workflow、逐字段审核工作台和不自动填图的 render adapter。
5. 从只读资料独立提取 YA.C.06.0017 与 W-H94 身份/BOM/工程证据，生成 10/12 节点草案。
6. 用 `demonstration_only` 路线验证人工修改、批准快照、相似检索、reuse_link 和字段来源。
7. 执行知识库单测、既有回归、DOCX 渲染和逐页视觉检查；失败项返工后再交付。
8. 根据独立验收补充八类route section、版本留痕、零依赖工作台section编辑和formal production事务闸门。
9. 在隔离数据库副本启动真实HTTP工作台，执行产品身份section修改、提交审核、正式批准阻断，并验证snapshot/FTS均无失败副作用。

## 2026-08-12 主入口与 HDMI 工艺族集成

10. 将 `sop_engineering_drawing` 默认入口从固定六步版式切换为 `cad_ai.sop_knowledge`；旧两页生成器仅保留显式 `legacy_two_page` 兼容模式。
11. 修复产品族解析：铜缆 HDMI 成品线、主动光纤 HDMI 和 RJ45 不再互相误路由，未知工艺族失败关闭。
12. 为 HDMI 铜缆成品线建立 17 节点路线草案，覆盖受控版本、备料、裁线、剥线、屏蔽、线序、端接父子步骤、装壳、电测、功能、终检、包装和人工放行闸门。
13. 将独立 SQLite、approved-only 相似复用、八类路线章节、逐字段审核和空图片 DOCX 接入模块返回结果与 Web 摘要。
14. 使用 Word/PDF/PNG 链路逐页验收 HDMI 草案：封面 1 页、工序页 17 页，无图片媒体、无固定六步补齐或截断。
