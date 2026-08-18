# Yunpai SOP Agent 说明

## Route Reference Files

- The full review workbench supports PDF, DOCX, XLSX, CSV, TXT, PNG, and JPEG route references, limited to 20MB per file.
- Route references are independent from project and step images. New files start as `needs_revision`; confirmation does not write the SOP, knowledge index, or approval state.
- Deduplicate by route and file hash. A duplicate upload reuses the original record without changing confirmed metadata or review state.
- Approved routes reject upload, confirmation, and deletion. Create a revision first; copied references return to `needs_revision`.
- Natural-language project creation must create all eight route sections with blocking unknowns, so the Route References page is never blank and cannot become approved automatically.

## New Project Auto Intake

- The top-bar New Project entry accepts pasted project text plus PNG/JPEG work-instruction images; document parsing and OCR are out of scope.
- Preview drafts are memory-only and expire. Invalid images, unavailable AI, blocking unknowns, and duplicate products must not create a route or document.
- AI may suggest an uploaded image's target step and caption, but every accepted link remains `draft`; unmatched images remain unassigned assets.
- IE timing and other production facts must be grounded in the pasted text. Never estimate them from an image or from model knowledge.
- A successful one-click intake creates only a draft route with `needs_revision` content. It must never approve, publish, or include unconfirmed images in the official DOCX.

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

## 当前自然语言定位规则

- 工序定位必须先经过 `cad_ai.sop_knowledge.targeting`，LLM 不能自行绕过目标解析结果；
- 工序编号、序号或唯一名称命中时可直接锁定；只命中作业内容或存在多个合理结果时，必须先让用户确认候选；
- 本轮明确说法优先于旧对话上下文，“不是……”等否定说法必须排除对应候选；
- 候选可通过页面按钮或“第一个”“最后那个”等自然语言选择，已处理的候选消息不可重复提交；
- LLM 返回的工序不在锁定范围时重试一次，仍不一致则阻止写入且不重新生成 DOCX。

## 当前工艺路线编辑规则

- 根路径 `/` 提供“对话修改 / 路线编辑”双模式，路线编辑不得重新引入旧 `/workbench` 的填表界面；
- 新增、独立拆分、排序、合并、删除和恢复必须从路线数据重新生成 DOCX/PDF/PNG，页数始终为 `1 + 活动工序数量`；
- 拆成作业动作只更新当前指导书的动作区，不增加路线步骤或页数；
- 合并只允许相邻同层级且无子工序的两道工序，并完整保留字段、媒体和来源记录；
- 所有结构变更均标记 `needs_revision`，已批准路线必须先创建新修订版；
- 删除后提供 12 秒即时撤销和 24 小时最近删除恢复，不得自动批准或发布恢复后的内容。

## 当前工图版式规则

- 每道工序的 `route_step.work_image_slots` 可独立设为 1～6，默认 6；路线编辑列表必须直接显示“几格”入口；
- 1～3 格为单排，4 格为 `1,2 / 4,3`，5 格为 `1,2,3 / 5,4`，6 格为 `1,2,3 / 6,5,4`；
- 作业步骤多于格数时按原顺序合并说明，不得截断；IE 动作行数必须等于格数；
- 版式变更后工序回到 `needs_revision`并重建 DOCX/PDF/PNG；已批准路线不可直接修改；
- 目标格数小于已确认图片数时必须拒绝，不得静默隐藏、删除或取消确认图片。
