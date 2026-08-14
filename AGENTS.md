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
