# 80806-129 SOP Word 表格模板记录

更新日期：2026-07-06

## 参考 PDF 结构

参考文件：`80806-129.pdf`

抽取到的格式事实：

- PDF 共 42 页。
- 第 1 页为竖版 A4，标题为“流程图”，用于展示完整工艺流程。
- 第 2 到 42 页为横版 A4，标题为“标准作业指导书”，每页对应一个工站/工序。
- 当前默认沉淀为 Word 表格文档，而不是 Excel、CSV 或 SVG，也不使用 Word 整页网格去硬凑 Excel 版式。
- 流程图页只复刻标题、表头和大空白流程图填充栏，流程图节点后续再填入；当前用“表头表 + 单格主体表 + IE工时记录表 + 页脚表”表达。
- 标准作业指导书页采用固定六格图片流程：上排 1、2、3，下排 6、5、4；当前用“表头表 + 六步主体表 + IE工时记录表 + 底部签核表”表达。
- 每页新增 `IE工时记录`，用于记录动作、机器型号、IE测量方法、单价、人数、评比系数、宽放率、标准工时、工时来源和动态调整。
- 右侧固定栏位为：作业标准、设备/工具、辅助材料、注意事项、变更内容、物料表。
- 底部固定栏位为：批准、审核、制作、材料环保要求、管制文件（印章处）、图号。

## 已搭建 Word 表格模板

代码模板：

- `cad_ai/sop_visual_template.py`

规范：

- `docs/standards/sop_visual_template_80806_129.md`

测试：

- `tests/test_sop_visual_template.py`

默认空白版式输出包：

- `outputs/sop_word_template_80806_129_layout/sop_80806_129_word_template.docx`
- `outputs/sop_word_template_80806_129_layout/sop_80806_129_word_manifest.json`
- `outputs/sop_word_template_80806_129_layout/sop_80806_129_word_format_check.json`

Demo 版式输出包：

- `outputs/sop_demo_usb_cable_word_layout/demo_usb_cable_sop_word.docx`
- `outputs/sop_demo_usb_cable_word_layout/demo_usb_cable_sop_word_manifest.json`
- `outputs/sop_demo_usb_cable_word_layout/demo_usb_cable_sop_word_format_check.json`

## Agent 填写边界

- 流程图主体栏先保留为单格空白栏，后续填入流程图。
- 图片区先保留为空白占位、图片引用或“步骤示意”文字，不默认生成 SVG。
- 文字区域可由 Agent 根据 BOM、routing、历史 SOP 和工序模板填写。
- IE 工时可先作为草案字段预留或标记为估算，但正式标准工时必须来自现场实测或人工锁定。
- 现场事实、人工审批、设备状态、EHS 记录、试产结果和 IE 实测工时不得由 Agent 伪造。
- 签核区默认空白，不填虚构人名。
- 该模板只定义格式，不代表 SOP 已发布。

## Skill 沉淀

已沉淀为本机 Codex skill：

- `C:/Users/1/.codex/skills/sop-visual-template/SKILL.md`
- `C:/Users/1/.codex/skills/sop-visual-template/references/80806-129-format-contract.md`
- `C:/Users/1/.codex/skills/sop-visual-template/assets/80806-129-format-profile.json`

该 skill 用于未来创建、审查或填写 80806-129 风格的 SOP Word 表格模板。
