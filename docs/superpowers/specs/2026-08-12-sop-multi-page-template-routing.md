# HDMI SOP 多页模板路由规范（2026-08-12）

## 决策

HDMI SOP 最终成品不是两页紧凑模板。固定结构为：

1. 第 1 页 A4 纵向完整工艺流程图；
2. 第 2 页起为 A4 横向标准作业指导书；
3. 每个知识库路线步骤对应一张指导书，文档页数为 `1 + 路线步骤数`；
4. 每张指导书使用 `1,2,3 / 6,5,4` 六槽版式，图片默认待人工上传；
5. 路线内容、工序动作和右侧说明来自 `SopKnowledgeStore`，模板不得自行补工序、合步或截断。

## 唯一生成链

`scripts/generate_sop_template_ai_handoff.py --route-db <db> --route-id <id>` 是最终 HDMI DOCX 的唯一入口。`SopDocumentService`、网页预览和下载接口必须调用该入口。通用 `VariableRouteDocxRenderer` 可保留供其他输出适配器测试，但不能作为最终 HDMI DOCX 的生成器。

## 验收

- 模板 ID 为 `yunpai.sop.hdmi-cable.multi-page.v1`；
- 第 1 页纵向，所有后续页横向；
- 顶层表格数为 `4 + 路线步骤数 × 4`；
- Word/PDF 实际页数为 `1 + 路线步骤数`；
- 无空白夹页、跨页残留、裁切、重叠和乱码；
- 每页 `DRAFT`，批准、审核、制作值栏为空；
- 图片区不生成虚构现场图片；
- IE、设备、工艺参数和测试限值没有证据时保持待人工确认。

## 兼容边界

`yunpai.sop.usb_c_cable_packaging.two_page.v1` 只属于 USB-C 包装历史样包。`--content-profile hdmi-cable` 必须失败关闭，防止未来 session 再把 HDMI 路由回固定两页模板。
