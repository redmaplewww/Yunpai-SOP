# SOP 作业图标准格式调研与样张方案

更新日期：2026-07-04

## 1. 调研结论

行业里没有一个跨所有制造业通用的“SOP 图纸国标模板”。更稳妥的做法是把 SOP 图定义为一类受控作业指导书：既要让一线员工看得懂、照着做，也要满足质量体系、现场安全、标准作业、追溯和变更控制要求。

对“云湃-智造一体机”的 SOP 工程图 Agent，建议输出的不是可直接发布的正式 SOP，而是 `draft_requires_review` 的 SOP 作业图草案包。草案包可以自动生成版式、步骤、物料映射、质量点和缺口提示，但发布前必须经过工程、生产、质量、EHS、PMC 人工确认。

## 2. 规范依据转译

### 2.1 ISO 9001：受控文件与客观证据

ISO 9001:2015 允许组织按自身过程决定文件化程度，但要求保留足够的 documented information 来支持过程运行，并能提供过程有效性的客观证据。转译到 SOP 图中，必须有：

- 文件编号、版本、状态、适用产品/工序。
- 编制、审核、批准角色。
- 生效状态、发布日期、变更记录。
- 使用的输入证据：BOM 版本、图纸版本、工艺路线、试产记录。
- 发布和变更不能由 AI 自动完成。

### 2.2 精益标准作业：节拍、作业顺序、标准在制品

标准作业图通常围绕三个核心元素展开：takt/cycle time、work sequence、standard WIP。转译到 SOP 图中，必须有：

- 工序顺序和前后关系。
- 理论工时、实测工时、节拍目标和工时来源。
- 最小线边物料/标准 WIP。
- 工位布局、物料位置和移动路径。

### 2.3 TWI Job Instruction：步骤、要点、理由

TWI 的作业分解强调 Important Steps、Key Points、Reasons。转译到 SOP 图中，每个步骤卡必须有：

- 做什么：动作/步骤。
- 怎么做：关键操作要点、参数、工装。
- 为什么：防错、防伤、保证质量或提升效率的理由。

### 2.4 OSHA JHA：步骤、危险、控制

JHA 要把作业拆成步骤，再识别每步危险并给出控制。转译到 SOP 图中，必须有：

- 每步潜在危险。
- 工程控制、管理控制、PPE。
- 异常停止和升级路径。
- 现场变化后重新评审。

### 2.5 ISO 7010：安全图形类别

ISO 7010 规定安全标志用于事故预防、消防、健康危害和应急疏散。SOP 图中建议使用统一的安全符号类别：

- 黄色三角：警告/危险。
- 蓝色圆形：强制 PPE 或必做动作。
- 红色禁令/停止：禁止动作、异常停机。
- 绿色方形：安全条件、合格流转、应急信息。

### 2.6 GMP 参考：偏差记录

21 CFR 211.100 对药品 GMP 场景要求书面生产/过程控制程序及偏差记录。即使云湃当前不是药品 GMP 系统，也可借鉴其审计思想：

- 作业必须按受控 SOP 执行。
- 偏离 SOP 的动作必须记录原因。
- 变更必须经过适当组织和质量角色审核。

## 3. 推荐 SOP 图标准格式

### 3.1 一页 SOP 作业图的固定区域

| 区域 | 必要内容 | Agent 数据来源 |
|---|---|---|
| 标题栏 | SOP 编号、版本、状态、产品、工序、适用工位、页码 | `sop_baseline`、`engineering_baseline` |
| 版本控制 | 编制、审核、批准、发布日期、变更摘要 | 人工录入或外部系统同步 |
| 输入区 | BOM 物料、工装、设备、治具、PPE、前置条件 | `bom_baseline`、`operation_breakdown` |
| 流程区 | 上游工序、本工序、下游工序、异常流向 | routing / `operation_breakdown` |
| 作业步骤区 | 步骤图、动作、关键点、理由、标准时间 | routing、TWI 分解、工时数据 |
| 质量区 | 检验项目、频次、判定标准、记录字段 | quality plan、工程图 |
| 安全区 | 危险源、控制措施、PPE、禁令 | EHS 输入、JHA 模板 |
| 记录区 | 扫码、批次、测试记录、异常单、首件记录 | MES/QMS/WMS 或人工录入 |
| 发布闸口 | 工程/生产/质量/EHS/PMC 状态 | `release_gate_field_requirements` |

### 3.2 工序卡字段

每个 `operation_sheet` 建议字段：

```json
{
  "operation_id": "OP40",
  "operation_name": "上下盖与按键装配",
  "station": "总装工位",
  "previous_operation": "OP30",
  "next_operation": "OP50",
  "material_inputs": [],
  "tools_and_fixtures": [],
  "ppe": [],
  "standard_time": {
    "value_sec": 110,
    "source": "theoretical_estimate",
    "release_requires": "site_measured_or_human_locked"
  },
  "steps": [
    {
      "step_no": 1,
      "action": "确认上盖、下盖、左右按键和滚轮齐套",
      "key_point": "物料编码与批次必须与工单一致",
      "reason": "防止混料和追溯断点",
      "quality_check": "扫码确认",
      "safety_note": "保持工位无散落螺丝"
    }
  ],
  "records_required": [],
  "open_questions": [],
  "release_gate": {
    "can_release_to_shopfloor": false,
    "blocking_reasons": []
  }
}
```

### 3.3 图形版式建议

第一版采用 A3 横版单工序作业图：

- 顶部：受控文件标题栏。
- 左侧：流程位置和 BOM/工具/PPE 输入。
- 中部：4 到 6 个作业步骤卡，按从左到右、从上到下阅读。
- 右侧：质量点、安全点、记录要求、异常升级。
- 底部：发布闸口、AI 边界、版本变更栏。

## 4. Agent 生成规则

1. 优先使用 MBOM，不用 EBOM 直接生成车间 SOP。
2. BOM 行只能生成物料输入，不能把治具、任务、测试程序混进物料消耗。
3. 每个 BOM 关键物料必须映射到至少一道工序；未映射则进入 `open_questions`。
4. 理论工时只能用于草案；正式发布必须来自现场实测或人工锁定。
5. 现场地点、OEE、良率、设备状态、班次、EHS 审批、试产结果不得由 AI 生成事实。
6. 图纸输出默认状态为 `draft_requires_review`。
7. 发布状态只能由人工闸口或外部受控系统写入。

## 5. 绘制结果

样张文件：

- `outputs/sop_visual_sample/sop_operation_sheet.svg`

样张内容为鼠标总装工序 `OP40 上下盖与按键装配` 的 A3 横版 SOP 作业图草案，包含标题栏、BOM 输入、工具/PPE、4 步作业卡、质量控制、安全/EHS、记录追溯和发布闸口。

## 6. 参考来源

- ISO, Guidance on the requirements for Documented Information of ISO 9001:2015: https://www.iso.org/iso/documented_information.pdf
- ISO 9001:2015 overview: https://www.iso.org/standard/62085.html
- Lean Enterprise Institute, Standardized Work: https://www.lean.org/lexicon-terms/standardized-work/
- TWI Institute, Job Instruction: https://www.twi-institute.com/job-instruction/
- OSHA, Job Hazard Analysis: https://www.osha.gov/sites/default/files/publications/OSHA3071.pdf
- ISO 7010:2019 overview: https://www.iso.org/standard/72424.html
- eCFR 21 CFR 211.100 Written procedures; deviations: https://www.ecfr.gov/current/title-21/chapter-I/subchapter-C/part-211/subpart-F/section-211.100
