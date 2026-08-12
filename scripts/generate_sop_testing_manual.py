from __future__ import annotations

from pathlib import Path

from docx import Document
from docx.enum.section import WD_ORIENT
from docx.enum.style import WD_STYLE_TYPE
from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs" / "manuals" / "Yunpai_SOP_人工测试辅助说明手册.docx"


def set_cell_shading(cell, fill: str) -> None:
    properties = cell._tc.get_or_add_tcPr()
    shading = OxmlElement("w:shd")
    shading.set(qn("w:fill"), fill)
    properties.append(shading)


def set_repeat_table_header(row) -> None:
    properties = row._tr.get_or_add_trPr()
    marker = OxmlElement("w:tblHeader")
    marker.set(qn("w:val"), "true")
    properties.append(marker)


def add_table(document: Document, headers: list[str], rows: list[list[str]], widths: list[float] | None = None):
    table = document.add_table(rows=1, cols=len(headers))
    table.style = "Table Grid"
    table.autofit = False
    header = table.rows[0]
    set_repeat_table_header(header)
    for index, value in enumerate(headers):
        cell = header.cells[index]
        cell.text = value
        set_cell_shading(cell, "1F4E78")
        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
        for run in cell.paragraphs[0].runs:
            run.font.color.rgb = RGBColor(255, 255, 255)
            run.font.bold = True
            run.font.size = Pt(9)
        if widths:
            cell.width = Cm(widths[index])
    for values in rows:
        cells = table.add_row().cells
        for index, value in enumerate(values):
            cells[index].text = value
            cells[index].vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.TOP
            if widths:
                cells[index].width = Cm(widths[index])
            for paragraph in cells[index].paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(8.5)
    document.add_paragraph()
    return table


def add_bullets(document: Document, items: list[str]) -> None:
    for item in items:
        document.add_paragraph(item, style="List Bullet")


def add_numbered(document: Document, items: list[str]) -> None:
    for item in items:
        document.add_paragraph(item, style="List Number")


def add_heading(document: Document, text: str, level: int = 1) -> None:
    document.add_heading(text, level=level)


def add_note(document: Document, text: str) -> None:
    paragraph = document.add_paragraph()
    paragraph.style = "Intense Quote"
    paragraph.add_run(text).bold = True


def configure_document(document: Document) -> None:
    section = document.sections[0]
    section.top_margin = Cm(1.7)
    section.bottom_margin = Cm(1.7)
    section.left_margin = Cm(1.8)
    section.right_margin = Cm(1.8)
    styles = document.styles
    styles["Normal"].font.name = "Microsoft YaHei"
    styles["Normal"]._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
    styles["Normal"].font.size = Pt(10)
    for name, size, color in (("Title", 24, "17365D"), ("Heading 1", 16, "17365D"), ("Heading 2", 12, "1F4E78")):
        style = styles[name]
        style.font.name = "Microsoft YaHei"
        style._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        style.font.size = Pt(size)
        style.font.color.rgb = RGBColor.from_string(color)
    if "Small Text" not in [style.name for style in styles]:
        small = styles.add_style("Small Text", WD_STYLE_TYPE.PARAGRAPH)
        small.font.name = "Microsoft YaHei"
        small._element.rPr.rFonts.set(qn("w:eastAsia"), "Microsoft YaHei")
        small.font.size = Pt(8.5)


def main() -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    doc = Document()
    configure_document(doc)

    title = doc.add_paragraph(style="Title")
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title.add_run("Yunpai SOP 工作台\n人工测试辅助说明手册")
    subtitle = doc.add_paragraph(style="Subtitle")
    subtitle.alignment = WD_ALIGN_PARAGRAPH.CENTER
    subtitle.add_run("参照模板：HDMI成品线检验与包装_SOP图_草案.docx\n版本：测试准备稿 | 日期：2026-08-12")
    doc.add_paragraph()
    add_note(doc, "适用边界：本手册用于人工测试本地 SOP 工作台，不构成生产放行依据。模板和当前演示路线均标记为 DRAFT / demo_not_for_release；所有规格、阈值、工时和签核仍须来自受控资料与人工确认。")

    add_heading(doc, "1. 使用目标")
    doc.add_paragraph("本手册把 HDMI 成品线“成品检验与包装”标准模板中的字段、工序和质量边界映射到 Yunpai SOP 工作台，帮助测试人员：")
    add_bullets(doc, [
        "确认网页是否能正确展示、编辑、保存、确认和生成 SOP 内容；",
        "验证自然语言修改、图片素材、历史知识搜索、路线资料与 DOCX 预览之间的一致性；",
        "用有意的错误输入检查系统是否保留草稿、阻止错误确认，并清楚提示原因；",
        "记录可复现的页面、接口、数据一致性和权限/门禁问题。",
    ])

    add_heading(doc, "2. 模板基线")
    doc.add_paragraph("本次参照的 HDMI 模板定义了“成品检验与包装”工站，作业顺序为：备料 -> 外观 -> 导通/短路测试 -> 功能测试 -> 盘线扎线 -> 装袋贴标装箱。其关键受控信息如下。")
    add_table(doc, ["模板区域", "应有内容", "工作台测试关注点"], [
        ["文件头", "品名、料号、版本、日期、文件编号、页码、核准/审核/拟订", "产品身份与路线资料是否可显示、编辑后是否留痕；草稿不能被误展示为正式发布。"],
        ["六道工序", "备料、外观、电测、功能、盘线、包装", "流程顺序、节点数量、标题、状态、字段编辑与确认是否一致。"],
        ["作业标准", "BOM/工单一致性、限度样板、批准测试程序、不合格隔离", "质量与异常标签页能否完整记录；不能把“待确认/TBD”当作合格标准。"],
        ["设备与物料", "测试仪、信号源/显示端或治具、扫码/盘线工具、保护帽/扎带/PE袋/标签/纸箱", "工具、治具、材料字段能否逐项输入、保存和再载入。"],
        ["IE工时", "测量方法、观测次数、平均/标准工时、来源、动态调整", "路线资料的 IE 章节能否识别为空或待确认，且不从模板臆填工时。"],
        ["签核与发布", "批准、审核、制作、环保/管制文件", "单工序确认、路线审核、正式批准的边界是否清晰，未完成项目是否被拦截。"],
    ], [3.1, 7.0, 7.2])

    add_heading(doc, "3. 页面与模块说明")
    doc.add_paragraph("本地服务地址： http://127.0.0.1:8787/ 。首页是“DOCX 校样台”；/workbench 是“作业修正台”。两者共用同一条 SOP 路线与知识库，但面向不同的人工工作方式。")
    add_table(doc, ["模块", "位置", "细节功能", "人工测试要点"], [
        ["产品/路线选择", "首页与作业修正台顶部", "读取产品清单并加载最新路线；显示产品编码、版本、路线状态和节点数。", "切换后工序、文档、图片和搜索结果必须全部切换到同一条路线；空路线应有明确空状态。"],
        ["DOCX 校样与下载", "首页左侧", "获取最新 DOCX，展示由同一 DOCX 生成的 PDF/页面预览，并提供 DOCX 下载。", "预览版本、页数和下载内容需对应同一次更新；生成失败时不能展示陈旧文件为最新。"],
        ["对话式修改", "首页右侧", "输入自然语言要求，系统定位工序/章节、写入草稿、重新生成 DOCX，并显示对话历史。", "含糊指令、多个工序、错误工序编号、空输入、AI不可用时的离线解析均需有可理解结果。"],
        ["自然语言预览", "/workbench 顶部", "先把文字要求解析成变更提案（字段改动、新增工序、图片引用），供人工查看后应用。", "预览不应直接确认或发布；同一句话涉及多项变更时，目标工序和字段必须正确。"],
        ["工艺流程", "/workbench 中部", "按路线顺序展示所有节点，标明工序代码、名称、子步骤和审核状态。", "测试 6 步模板路线和 17 步演示路线；顺序、数量和子步骤关系均不可丢失或截断。"],
        ["作业指导", "/workbench 下部的“作业指导”", "编辑标题、动作、目的、方法、材料、设备、治具和安全要求；保存草稿。", "多行数组字段要按“一行一项”保留；保存后刷新和切换工序不应丢失。"],
        ["质量与异常", "/workbench 下部的“质量与异常”", "编辑质量检查、合格标准、记录输出、异常处理和审核意见。", "输入不合格隔离、待工程确认、TBD 等内容后，系统不可把工序标成已确认或已批准。"],
        ["路线资料", "/workbench 下部的“路线资料”", "查看/修订产品身份、BOM、设备治具、参数、质量、包装标签、IE工时、签核等章节。", "章节保存应生成新版本并保留历史；空的受控资料要呈现 unknown/blocking 信息。"],
        ["图片素材", "/workbench 右侧", "上传 PNG/JPEG 图片，展示素材并绑定到当前工序；可通过名称匹配供提案引用。", "文件格式、空文件、超大文件、重复名、跨工序绑定、刷新后图片可访问等均应测试。"],
        ["相似历史内容", "/workbench 右侧", "按工序标题或关键字搜索已确认的历史知识。", "草稿、被拒绝和非适用产品不应被混入；搜索空词、特殊符号、超长词需稳定。"],
        ["单工序确认", "/workbench 底部操作区", "操作人确认已逐项核对后，当前工序进入可搜索知识；不等同于整份 SOP 发布。", "未填写操作人、取消确认、含 blocking unknown 或未核对字段时的行为应与门禁规则一致。"],
        ["审核/发布门禁", "后端审核接口与完整工作台流程", "路线可经历草稿、提交审核、演示批准或正式批准；正式批准需要全部工序/章节确认与产品确认令牌。", "验证缺任何确认、使用错误令牌、空批准人、仍有 unknown 时必须拒绝，且不得产生部分副作用。"],
    ], [2.6, 3.0, 6.3, 5.4])

    add_heading(doc, "4. 测试前准备")
    add_numbered(doc, [
        "打开 http://127.0.0.1:8787/ 和 http://127.0.0.1:8787/workbench；确认当前产品为 HDMI-DRAFT-001。",
        "准备一组可丢弃的测试文本、两张 PNG/JPEG 图片、受控 BOM/测试规范的占位引用和一个测试人员姓名或工号。不要录入真实客户、员工或密钥信息。",
        "在每个场景开始前记录当前路线版本、工序代码、浏览器版本和时间；对影响数据的动作使用测试专用名称，例如 TEST-QA-20260812。",
        "每次点击“保存草稿”“确认本工序”“应用提案”或下载 DOCX 后，重新加载页面并记录结果；对异常保留截图、页面地址与提示文本。",
    ])
    add_note(doc, "当前基础回归状态：2026-08-12 已运行仓库内 51 项 SOP 自动化测试，全部通过。该结果只证明已有自动化覆盖的逻辑链路，不替代本手册中的人工视觉、真实浏览器、异常输入和业务验收测试。")

    add_heading(doc, "5. 推荐人工测试问题")
    doc.add_paragraph("以下问题均可直接复制到首页的对话输入框，或改写后填入作业修正台的“直接说哪里要改”。建议每次只做一个问题，先观察提案，再决定是否应用。")
    add_table(doc, ["编号", "测试问题/输入", "预期检查点", "风险等级"], [
        ["NL-01", "把第 1 道工序改为“备料核对”，补充：按受控工单和 BOM 核对线材规格、版本、数量及包装辅料。", "正确命中第1步和作业方法；变更停留在草稿/提案，DOCX 更新后文字一致。", "高"],
        ["NL-02", "第 2 道外观检查增加：插头外壳、端子、线身和护套不得有变形、破损、露铜和污染。", "质量要求进入外观检查，而非误写到其他工序；多项缺陷文本无截断。", "高"],
        ["NL-03", "第 3 步使用批准测试仪检查 HDMI 19 针导通、开短路及屏蔽连续性；测试仪型号暂不填。", "“型号暂不填”应保留 unknown/TBD 语义，不能伪造型号或自动确认电测门限。", "高"],
        ["NL-04", "第 4 步功能测试：按订单要求检查音视频输出和连接稳定性，但 HDMI 版本、分辨率和阈值待工程确认。", "正确命中功能测试；待确认内容应成为阻塞信息，不可被当作正式标准。", "高"],
        ["NL-05", "第 5 步盘线：按批准圈径盘线，禁止扭结、急折和挤压插头，扎带居中。", "作业指导、质量/异常字段归属合理；刷新、切换工序后内容存在。", "中"],
        ["NL-06", "第 6 步装袋贴标装箱：装保护帽、入 PE 袋、贴标签、核对批次和数量后转待检区。", "包装顺序、物料和记录输出均可见；不合格状态不能被自动放行。", "高"],
        ["NL-07", "在外观检查与导通测试之间插入“扫码绑定批次”工序，记录工单号和产品序列号。", "新增节点的位置、编号和页面/DOCX 顺序正确；不应覆写已有步骤。", "高"],
        ["NL-08", "把第 3 道和第 4 道工序合并为“测试”，所有内容都放进去。", "检查系统对可能破坏模板职责边界的请求是否清晰预览、可撤销且不误确认。", "中"],
        ["NL-09", "将第 99 道工序的电测标准改为通过。", "不存在的工序必须给出明确错误或不产生变更，不能静默改错工序。", "高"],
        ["NL-10", "删除全部 unknown 并直接正式批准。", "系统必须拒绝绕过受控资料、人工确认、试产与签核门禁的请求。", "严重"],
        ["NL-11", "第 2 步图片是 hdmi_appearance.jpg，第 3 步图片是 continuity_test.png。", "仅在已上传并可匹配时生成图片引用；不存在图片时应提示缺失而非生成虚假绑定。", "中"],
        ["NL-12", "把“待 IE 实测”改成平均 15 秒、标准 18 秒。", "测试无来源工时是否被标记为待人工/IE确认；不得把估算值当受控实测数据。", "高"],
    ], [1.3, 7.4, 7.3, 1.3])

    add_heading(doc, "6. 场景化检查清单")
    add_table(doc, ["场景", "操作", "通过标准", "异常证据"], [
        ["首次加载", "分别打开首页和 /workbench，观察产品、路线、工序、AI状态。", "页面无空白/乱码/控制台错误；产品和17个演示节点可见。", "截图、浏览器控制台、网络请求状态。"],
        ["路线切换", "若有多个产品，反复切换，再返回 HDMI。", "标题、节点、章节、DOCX 链接和图片不串线；选择状态正确恢复。", "切换前后产品编码和 API 返回。"],
        ["保存草稿", "修改外观检查的 action、method、materials、safety，点击保存草稿并刷新。", "字段原样保留，审核状态仍为草稿/待核对，产生可追溯审核记录。", "修改前后截图、操作人、时间、字段值。"],
        ["确认工序", "填写测试操作人，选择一项已核对工序，确认后搜索相同关键词。", "明确显示“人工已确认”；搜索可发现已确认内容；不能自动批准整条路线。", "确认弹窗、状态标签、搜索结果。"],
        ["取消确认", "在确认弹窗点击取消。", "数据和状态完全不变，无成功提示或后台写入。", "弹窗、刷新后状态、网络请求。"],
        ["图片上传绑定", "上传 PNG/JPEG，尝试绑定到外观/电测工序，刷新页面。", "缩略图、文件名、绑定关系和图片访问稳定；不支持格式有明确提示。", "文件名、大小、截图、接口响应。"],
        ["路线资料版本", "编辑 BOM 或包装标签章节并保存，查看历史。", "生成新章节版本，旧版本仍可追溯；未知项、来源和审核意见不丢失。", "章节 ID、版本号、历史列表。"],
        ["DOCX 一致性", "在首页提交一条明确修改，等待预览，再下载 DOCX。", "预览页、下载文件、最新路线字段三者一致；页码/标题无错位。", "预览截图、下载文件名、修改内容位置。"],
        ["异常输入", "测试空操作人、空指令、超长文本、特殊字符、无效 JSON/网络中断。", "不崩溃；提示具体原因；未成功时无半写入、无错误状态迁移。", "输入文本、提示、请求与数据库/刷新后状态。"],
        ["发布门禁", "保留至少一个 blocking unknown，尝试提交或正式批准。", "必须拒绝，并说明缺失的确认/证据；路线仍为非正式状态。", "请求参数、响应错误、路线状态。"],
    ], [2.7, 5.7, 6.2, 4.2])

    add_heading(doc, "7. 模板对应的高风险点")
    add_bullets(doc, [
        "六工序完整性：模板检验与包装是 6 个步骤；当前演示路线可含更多制造步骤。测试时确认系统既不强制截成 6 步，也不会在生成 DOCX 时漏掉任何步骤。",
        "测试参数不能臆填：HDMI 版本、19针针位、导通阈值、分辨率、带宽、音视频判定、最小弯曲半径、包装数量等若无受控来源，必须呈现为待确认，而非“通过”。",
        "异常隔离优先：输入“外观不良”“电测失败”“标签不一致”等词时，需检查是否能记录隔离、标识和人工评审，不应出现自动放行或自动关闭异常。",
        "数据边界：保存草稿、确认工序、提交审核、演示批准、正式批准是不同动作。界面文案、状态标签和实际结果必须一致。",
        "DOCX 可信度：下载文件应与可见预览来源相同。重点检查产品名称、料号、路线顺序、作业标准、IE工时空值和签核栏是否被错误填充。",
        "编码与中文：页面及 DOCX 的中文标题、标点、换行、编号和表格边界需人工检查，尤其在窄屏、长文本、特殊符号和复制粘贴后。",
    ])

    add_heading(doc, "8. 缺陷记录模板")
    doc.add_paragraph("每发现一个问题单独记录一行；对于会误确认、误发布、错写工序、丢失数据或泄露跨产品内容的问题，建议标为 P0/P1 并停止继续在该路线写入。")
    add_table(doc, ["字段", "填写内容"], [
        ["缺陷编号", "例如 SOP-WEB-20260812-001"],
        ["严重级别", "P0 阻断/误发布；P1 数据或业务关键错误；P2 功能异常；P3 体验或文案问题"],
        ["环境", "浏览器版本、网址、产品/路线ID、工序代码、测试账号/操作人"],
        ["前置条件", "例如：当前路线为 HDMI-DRAFT-001，第3步尚未确认，存在 electrical_test_program unknown"],
        ["复现步骤", "按 1、2、3 编号记录，每一步包含输入文本和点击对象"],
        ["预期结果", "基于模板、页面提示或受控规则的正确行为"],
        ["实际结果", "页面显示、状态变化、下载文件差异、接口提示或异常"],
        ["证据", "截图路径、录屏、DOCX 文件名、浏览器控制台、网络请求/响应"],
        ["影响与建议", "是否造成误确认/错误SOP/数据丢失；建议的修复方向"],
    ], [3.5, 15.3])

    add_heading(doc, "9. 本轮测试结论填写页")
    doc.add_paragraph("测试人员可在执行后补充以下结论。")
    add_table(doc, ["项目", "结果/备注"], [
        ["测试日期与人员", ""],
        ["浏览器与页面地址", ""],
        ["已执行场景数量", ""],
        ["通过/失败/阻塞数量", ""],
        ["发现的 P0/P1 问题", ""],
        ["DOCX 预览与下载一致性", ""],
        ["模板六工序覆盖情况", ""],
        ["是否允许进入下一轮测试", ""],
        ["审核意见", ""],
    ], [5.0, 13.8])

    doc.add_paragraph("附注：本手册根据 HDMI成品线检验与包装_SOP图_草案.docx 的表格结构与工序文本，以及当前 Yunpai SOP 工作台的页面与接口能力整理。")
    doc.save(OUT)
    print(OUT)


if __name__ == "__main__":
    main()
