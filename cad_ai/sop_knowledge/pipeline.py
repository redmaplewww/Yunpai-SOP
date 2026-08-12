from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from .models import EvidenceRef, ProductFeatureSet, ProductIdentity, RouteDraft, RouteMatch, RouteSectionDraft, RouteStepDraft, UnknownItem
from .store import SopKnowledgeStore


class SourceIngestion(Protocol):
    def ingest(self, product_code: str, source_profile: dict[str, Any]) -> dict[str, Any]: ...


class IdentityConflictResolver(Protocol):
    def resolve(self, ingested: dict[str, Any]) -> ProductIdentity: ...


class FeatureExtractor(Protocol):
    def extract(self, identity: ProductIdentity, ingested: dict[str, Any]) -> ProductFeatureSet: ...


class EvidenceLedger(Protocol):
    def record(self, product_code: str, evidence: EvidenceRef) -> int: ...


class RouteRetriever(Protocol):
    def retrieve(self, product_code: str, features: ProductFeatureSet) -> list[RouteMatch]: ...


class RouteDrafter(Protocol):
    def draft(self, identity: ProductIdentity, features: ProductFeatureSet, ingested: dict[str, Any], match: RouteMatch | None) -> RouteDraft: ...


class ProfileSourceIngestion:
    def ingest(self, product_code: str, source_profile: dict[str, Any]) -> dict[str, Any]:
        if product_code not in source_profile:
            raise KeyError(product_code)
        raw = dict(source_profile[product_code])
        raw["product_code"] = product_code
        raw["sources"] = [str(Path(path)) for path in raw.get("sources", [])]
        raw["source_inventory"] = [
            {
                "path": path,
                "source_type": "acceptance_or_order" if "01_承认书-订单" in path else "engineering_drawing" if "02_工程图" in path else "bom",
                "exists": Path(path).is_file(),
            }
            for path in raw["sources"]
        ]
        return raw


class SqliteEvidenceLedger:
    """Persist source/provenance separately from route drafting and rendering."""

    def __init__(self, store: SopKnowledgeStore) -> None:
        self.store = store

    def record(self, product_code: str, evidence: EvidenceRef) -> int:
        return self.store.add_evidence(
            product_code,
            source_type=evidence.source_type,
            source_path=evidence.source_path,
            page_or_sheet=evidence.page_or_sheet,
            excerpt=evidence.excerpt,
        )


class DeterministicIdentityConflictResolver:
    def resolve(self, ingested: dict[str, Any]) -> ProductIdentity:
        code = ingested["product_code"]
        conflicts = list(ingested.get("identity_conflicts") or [])
        missing = [item["path"] for item in ingested.get("source_inventory", []) if not item["exists"]]
        if missing:
            conflicts.append("source file missing: " + "; ".join(missing))
        profile_type = str(ingested.get("profile_type") or "")
        family = str(ingested.get("process_family_code") or "")
        if not family:
            family = {
                "rj45_acceptance": "rj45_connector_incoming_inspection",
                "active_optical_hdmi": "active_optical_cable_final_assembly_packaging",
                "hdmi_finished_cable": "hdmi_finished_cable_manufacturing",
            }.get(profile_type, "")
        if not family:
            raise ValueError("source profile must provide a supported process_family_code or profile_type")
        aliases = [ingested.get("supplier_part", ""), ingested.get("drawing_no", "")]
        return ProductIdentity(
            product_code=code,
            product_name=ingested["product_name"],
            aliases=[item for item in aliases if item],
            process_family_code=family,
            description=ingested.get("evidence_scope", ""),
            conflicts=conflicts,
        )


class DeterministicFeatureExtractor:
    def extract(self, identity: ProductIdentity, ingested: dict[str, Any]) -> ProductFeatureSet:
        if identity.process_family_code == "rj45_connector_incoming_inspection":
            features = {
                "product_class": "RJ45 modular plug",
                "network_category": "CAT6A",
                "shielding": "FTP",
                "contact_layout": "dual-row 6-up 2-down" if "双排6上2下" in identity.product_name else "single-row",
                "construction": "two-piece",
                "hole_diameter_mm": str(ingested.get("hole_mm", "")),
                "shell_material": ingested.get("shell_material", ""),
                "drawing_no": ingested.get("drawing_no", ""),
            }
        elif identity.process_family_code == "active_optical_cable_final_assembly_packaging":
            features = {
                "product_class": "active optical HDMI cable",
                "interface": "HDTV2.1",
                "resolution_class": "8K",
                "length_m": str(ingested.get("length_m", "")),
                "shell_material": "zinc alloy" if "锌合金" in identity.product_name else "aluminum alloy",
                "packaging": ingested.get("package", {}).get("summary", ""),
            }
        elif identity.process_family_code == "hdmi_finished_cable_manufacturing":
            features = {
                "product_class": "HDMI finished cable",
                "interface": "HDMI",
                "signal_medium": str(ingested.get("signal_medium") or "source_not_supplied"),
                "hdmi_version": str(ingested.get("hdmi_version") or "source_not_supplied"),
                "length_m": str(ingested.get("length_m") or "source_not_supplied"),
                "termination_method": str(ingested.get("termination_method") or "source_not_supplied"),
                "packaging_scope": str((ingested.get("package") or {}).get("summary") or "source_not_supplied"),
            }
        else:
            features = {
                "product_class": str(ingested.get("product_class") or identity.product_name),
                **{str(key): str(value) for key, value in (ingested.get("features") or {}).items()},
            }
        evidence = [
            EvidenceRef(source_type=item["source_type"], source_path=item["path"], confidence=1.0)
            for item in ingested.get("source_inventory", []) if item["exists"]
        ]
        return ProductFeatureSet(
            product_code=identity.product_code,
            process_family_code=identity.process_family_code,
            features=features,
            conflicts=identity.conflicts,
            evidence=evidence,
        )


class ApprovedOnlyRouteRetriever:
    def __init__(self, store: SopKnowledgeStore, *, allow_demonstration: bool = False) -> None:
        self.store = store
        self.allow_demonstration = allow_demonstration

    def retrieve(self, product_code: str, features: ProductFeatureSet) -> list[RouteMatch]:
        return self.store.retrieve_approved(
            product_code,
            features.features,
            allow_demonstration=self.allow_demonstration,
        )


def unknown(field_name: str, reason: str, owner: str, evidence: str, *, blocking: bool = True) -> UnknownItem:
    return UnknownItem(field_name=field_name, reason=reason, owner_role=owner, required_evidence=evidence, blocking=blocking)


def refs(ingested: dict[str, Any], *source_types: str, excerpt: str = "") -> list[EvidenceRef]:
    return [
        EvidenceRef(source_type=item["source_type"], source_path=item["path"], excerpt=excerpt)
        for item in ingested.get("source_inventory", [])
        if item["exists"] and item["source_type"] in source_types
    ]


class EvidenceBoundRouteDrafter:
    def draft(self, identity: ProductIdentity, features: ProductFeatureSet, ingested: dict[str, Any], match: RouteMatch | None) -> RouteDraft:
        if match:
            raise ValueError("approved-route cloning is handled by SopRouteWorkflow.clone_approved_match")
        if identity.process_family_code == "rj45_connector_incoming_inspection":
            return self._draft_rj45(identity, ingested)
        if identity.process_family_code == "active_optical_cable_final_assembly_packaging":
            return self._draft_optical_cable(identity, ingested)
        if identity.process_family_code == "hdmi_finished_cable_manufacturing":
            return self._draft_hdmi_finished_cable(identity, ingested)
        raise ValueError(f"no evidence-bound route drafter for process family: {identity.process_family_code}")

    def _draft_hdmi_finished_cable(self, identity: ProductIdentity, data: dict[str, Any]) -> RouteDraft:
        evidence = refs(data, "acceptance_or_order", "engineering_drawing", "bom", excerpt="产品身份、材料、工艺或检验要求")
        route_scope = str(data.get("route_scope") or "full_manufacturing")
        length_text = str(data.get("length_m") or "资料未提供标称长度")
        termination_text = str(data.get("termination_method") or "资料未提供端接方式")
        shared_equipment = unknown(
            "equipment_fixture_model",
            "输入资料没有给出裁线、剥线、端接、成型和测试设备的受控型号及治具编号，不能从历史模板直接写入。",
            "工艺工程师/设备工程师",
            "现场设备卡、治具台账、校准状态和已批准工艺卡",
        )
        cut_strip_window = unknown(
            "cut_strip_process_window",
            "输入资料没有给出成品长度公差、裁线补偿、外被剥除长度、芯线剥皮长度和屏蔽保留尺寸。",
            "产品工程师/工艺工程师",
            "受控工程图、端接规格书、首件记录和工艺参数卡",
        )
        termination_window = unknown(
            "termination_process_window",
            "输入资料没有确认焊接、压接或刺破端接方式，也没有温度、压力、时间和辅料要求。",
            "产品工程师/工艺工程师",
            "连接器规格书、受控端接工艺卡、设备参数卡和试产确认记录",
        )
        electrical_test = unknown(
            "electrical_test_program",
            "输入资料没有给出19针映射、屏蔽/地线判定、导通电阻门限、耐压或绝缘测试程序。",
            "测试工程师/品质工程师",
            "批准电测程序、针位表、测试设备清单和合格样本",
        )
        functional_test = unknown(
            "functional_test_program",
            "输入资料没有给出适用HDMI版本、分辨率、刷新率、带宽、音频、方向性和稳定性判定方法。",
            "产品工程师/测试工程师/品质工程师",
            "客户受控规格、批准功能测试规范、信号源/显示端配置和判定记录",
        )
        packaging_release = unknown(
            "packaging_label_release",
            "输入资料没有给出盘线直径、最小弯曲半径、扎带位置、单件包材、标签稿和装箱数量的受控版本。",
            "包装工程师/PMC/品质工程师",
            "受控包装规范、标签批准稿、BOM包材段和装箱标准",
        )

        def step(
            code: str,
            seq: float,
            title: str,
            action: str,
            why: str,
            method: list[str],
            checks: list[str],
            criteria: list[str],
            records: list[str],
            exceptions: list[str],
            *,
            parent: str | None = None,
            inputs: list[str] | None = None,
            materials: list[str] | None = None,
            equipment: list[str] | None = None,
            fixtures: list[str] | None = None,
            parameters: list[dict[str, Any]] | None = None,
            safety: list[str] | None = None,
            unknowns: list[UnknownItem] | None = None,
        ) -> RouteStepDraft:
            return RouteStepDraft(
                step_code=code,
                sequence_no=seq,
                parent_step_code=parent,
                title=title,
                action=action,
                why=why,
                inputs=inputs or ["当前工单/订单", "受控BOM、工程图和工艺卡"],
                materials=materials or [],
                tool_equipment=equipment or [],
                fixtures=fixtures or [],
                parameters=parameters or [],
                method=method,
                quality_check=checks,
                acceptance_criteria=criteria,
                safety=safety or ["按现场风险评估和设备作业规范执行；未经授权不得操作设备。"],
                record_output=records,
                exception=exceptions,
                unknowns=unknowns or [],
                evidence={"source": evidence} if evidence else {},
            )

        steps = [
            step(
                "HD-01", 1, "工单、料号与受控版本核对",
                f"开工前核对产品料号 {identity.product_code}、品名、订单规格、标称长度（{length_text}）、BOM和工艺文件版本。",
                "防止不同长度、HDMI版本、线材结构、端接方式或包装版本混用。",
                ["逐字符核对料号和品名。", "核对工单、BOM、工程图和工艺卡版本。", "把资料冲突记录为阻断项，不自行选用版本。"],
                ["产品身份、长度、版本、路线范围和批次标签一致性。"],
                [f"全部身份字段仅指向 {identity.product_code}；任一冲突未关闭时不得开工。"],
                ["开工资料核对记录", "版本冲突清单"],
                ["身份或版本不一致时隔离物料并提交工程/PMC人工判定。"],
                parameters=[{"name": "路线范围", "value": route_scope, "source": "本次草案请求", "status": "draft"}],
            ),
            step(
                "HD-02", 2, "物料齐套与批次防混",
                "按受控BOM核对线材、HDMI插头/端接件、壳体或成型料、屏蔽辅料、标签和包装材料，并保留原批次标识。",
                "建立每批物料与成品路线的追溯关系，避免把相似线材或连接器混入当前产品。",
                ["按BOM逐项点料并分格摆放。", "核对线材印字、连接器方向和物料批次。", "缺少制造辅料用量时记录缺口，不从近似产品补数。"],
                ["料号、规格、数量、批次、方向和外观。"],
                ["受控BOM明确项目齐套，批次可追溯；资料未覆盖项目保持阻断。"],
                ["领料/齐套记录", "批次关联表", "缺料或资料缺口清单"],
                ["缺料、混料、批次不清或BOM冲突时停止并隔离。"],
                materials=["HDMI线材", "连接器/端接件", "壳体或成型材料", "屏蔽辅料", "包装材料"],
                equipment=["条码或人工核对工具（受控型号资料未提供）"],
                fixtures=["防混料周转盘（编号资料未提供）"],
                unknowns=[unknown("manufacturing_bom", "当前输入没有证明完整制造BOM和单件用量，不能用包装模板替代。", "产品工程师/PMC", "受控MBOM、领料单和单件用量")],
            ),
            step(
                "HD-03", 3, "裁线与长度补偿",
                "按批准的成品长度、公差和端部加工补偿设置裁线长度，裁切后核对两端切口和线材批次。",
                "裁线长度是后续剥线、端接和成品长度的共同基准。",
                ["确认线材批次和方向要求。", "调用批准裁线参数。", "首件测量裁切长度并记录。", "批量裁切时按批准频次复核。"],
                ["裁切长度原始值、切口垂直度、护套压伤、批次。"],
                ["长度在批准公差内，切口无影响后续加工的压扁、散股或破损。"],
                ["首件裁线记录", "巡检长度记录", "设备/刀具编号"],
                ["长度超差或切口异常时暂停本批，隔离自上次合格检查后的产品。"],
                equipment=["裁线机或定长切断设备（型号资料未提供）"],
                fixtures=["长度计量装置（编号资料未提供）"],
                parameters=[{"name": "标称长度", "value": length_text, "source": "输入资料", "status": "unreviewed"}],
                unknowns=[shared_equipment, cut_strip_window],
            ),
            step(
                "HD-04", 4, "外被剥除与端部定长",
                "按端接结构要求剥除两端外被，保留规定长度的屏蔽层、排流线和芯线，不伤及导体及绝缘。",
                "端部尺寸决定屏蔽处理、芯线排布和连接器壳体装配位置。",
                ["确认两端剥线基准。", "调用批准剥线尺寸。", "剥除外被后检查编织/铝箔、排流线和芯线。", "测量首件剥除长度。"],
                ["剥除长度、外被切口、屏蔽层完整性、芯线绝缘损伤。"],
                ["尺寸满足批准工艺卡；导体、绝缘和屏蔽层无割伤或非预期缺失。"],
                ["剥外被首件记录", "外观检查记录"],
                ["发现伤芯、断股或屏蔽破损时隔离，不得用胶带遮盖后流转。"],
                equipment=["剥线机或剥线工具（型号资料未提供）"],
                fixtures=["剥线定位治具（编号资料未提供）"],
                unknowns=[shared_equipment, cut_strip_window],
            ),
            step(
                "HD-05", 5, "屏蔽层、排流线与接地准备",
                "按受控结构整理编织、铝箔、排流线和接地导体，保持屏蔽连续并避免与信号芯线短接。",
                "HDMI高速信号完整性和EMI性能依赖屏蔽层及接地连接的连续与位置关系。",
                ["识别编织、铝箔、排流线和地线。", "按工艺卡修整并定位。", "检查无散丝进入信号端接区。", "保留屏蔽连接所需长度。"],
                ["屏蔽结构完整、排流线方向、散丝、短路风险。"],
                ["屏蔽和接地结构符合受控图纸，端接区无散丝或非预期接触。"],
                ["屏蔽准备自检记录"],
                ["屏蔽层破损、散丝不可控或接地结构不明时停止并提交工艺判定。"],
                equipment=["屏蔽修整工具（规格资料未提供）"],
                fixtures=["屏蔽定位夹具（编号资料未提供）"],
                unknowns=[cut_strip_window, termination_window],
            ),
            step(
                "HD-06", 6, "芯线排序、校直与剥皮",
                "依据受控针位表对差分对、时钟、控制和地线进行排序与校直，并按端接要求剥除芯线绝缘。",
                "芯线顺序、对绞保持长度和剥皮尺寸直接决定针位正确性与高速性能。",
                ["读取当前连接器针位表。", "保持差分对配对并控制解绞长度。", "按批准尺寸剥皮。", "检查导体无缺股、氧化和污染。"],
                ["线序、对绞保持、剥皮长度、导体缺股和污染。"],
                ["线序与受控针位表一致，导体完整，解绞和剥皮尺寸满足批准工艺。"],
                ["线序/剥皮首件记录"],
                ["针位表缺失、线色冲突或导体损伤时隔离并停止端接。"],
                equipment=["芯线剥皮工具（型号资料未提供）"],
                fixtures=["线序定位板（版本资料未提供）"],
                unknowns=[cut_strip_window, electrical_test],
            ),
            step(
                "HD-07", 7, "连接器端接总成",
                f"按受控端接方式（{termination_text}）完成两端HDMI连接器的芯线、地线和屏蔽连接。",
                "端接是决定导通、短路、接触可靠性和高速信号质量的关键工序。",
                ["先确认针位表和连接器方向。", "按批准工艺完成逐针端接。", "端接后检查相邻针、焊点或压接区。", "在装壳前完成端接自检。"],
                ["针位、连接器方向、焊点/压接形貌、相邻短路、拉脱风险。"],
                ["端接位置和形貌满足受控规格；无漏接、错接、连锡、虚接或导体外露超限。"],
                ["端接首件记录", "端接设备参数记录", "操作员自检记录"],
                ["错接、虚接、短路或端接方法不明时隔离，返修必须走批准返修流程。"],
                equipment=["焊接/压接/刺破端接设备（方式及型号资料未提供）"],
                fixtures=["连接器端接定位治具（编号资料未提供）"],
                unknowns=[shared_equipment, termination_window, electrical_test],
            ),
            step(
                "HD-07.1", 7.1, "针位映射与方向复核",
                "在不可逆端接前逐针核对P1至P19、外壳/屏蔽和地线映射，并确认两端连接器观察方向。",
                "防止镜像观察、连接器翻转或近似线序造成整线错接。",
                ["锁定针位表版本。", "标识两端连接器观察面。", "逐针点检线色/线号。", "由第二人或受控治具复核。"],
                ["P1-P19映射、连接器方向、屏蔽和地线。"],
                ["全部针位与批准针位表一致并完成复核记录。"],
                ["针位映射核对表", "复核人记录"],
                ["针位表版本不清或复核不一致时不得进入端接。"],
                parent="HD-07",
                unknowns=[electrical_test],
            ),
            step(
                "HD-07.2", 7.2, "端接执行与形貌检查",
                "按批准设备参数完成端接，清除工艺残留并检查端接形貌、导体位置和绝缘间距。",
                "把针位关系转化为稳定机械和电气连接。",
                ["确认设备/工具状态。", "按工艺卡逐针端接。", "在规定照明或放大条件下检查。", "记录参数和异常。"],
                ["端接形貌、润湿/压接高度、绝缘间距、残留物和机械牢固性。"],
                ["端接形貌满足批准标准且连接牢固；具体参数须由工艺卡锁定。"],
                ["端接过程参数记录", "形貌检验记录"],
                ["不合格端接不得凭经验补焊/重压后直接流转。"],
                parent="HD-07",
                unknowns=[termination_window],
            ),
            step(
                "HD-08", 8, "屏蔽壳连接与端部绝缘防护",
                "完成屏蔽层、排流线与连接器金属壳的批准连接，并对端接区实施规定的绝缘、固定和应力释放。",
                "确保屏蔽连续、避免内部短路，并降低拉扯传递到端接点的风险。",
                ["核对屏蔽连接位置。", "按工艺卡连接屏蔽/排流线。", "安装绝缘和固定材料。", "检查无尖锐边缘压伤芯线。"],
                ["屏蔽连续、绝缘覆盖、固定位置、应力释放和短路风险。"],
                ["屏蔽/地连接符合图纸，绝缘完整，端接区无可见短路或夹线。"],
                ["屏蔽与端部防护检查记录"],
                ["屏蔽连接或绝缘结构不清时保持阻断，不得自行增加材料。"],
                materials=["屏蔽/绝缘/固定辅料（具体规格资料未提供）"],
                unknowns=[termination_window, electrical_test],
            ),
            step(
                "HD-09", 9, "连接器壳体装配或成型",
                "按产品结构装配连接器外壳，或执行批准的内模/外模成型，并形成规定的应力释放结构。",
                "为端接区提供机械保护、定位、握持和弯折缓冲。",
                ["确认壳体方向和部件齐套。", "按批准工艺装壳或成型。", "冷却/固化后检查外形。", "确认线身未被夹伤和拉偏。"],
                ["壳体方向、结合、成型外观、应力释放、偏芯、露线和夹伤。"],
                ["壳体定位牢固、外观完整、应力释放有效；成型参数必须来自批准工艺卡。"],
                ["装壳/成型首件记录", "设备参数记录"],
                ["开裂、缺胶、溢胶、松动、偏芯或参数未锁定时隔离。"],
                equipment=["装壳、锁附或成型设备（型号资料未提供）"],
                fixtures=["壳体/模具定位治具（编号资料未提供）"],
                unknowns=[shared_equipment, unknown("housing_molding_window", "产品结构和输入资料没有给出装壳扭矩或成型温度、压力、时间、材料和模具编号。", "产品工程师/工艺工程师", "受控结构图、模具卡和成型参数卡")],
            ),
            step(
                "HD-10", 10, "19针导通、开短路与屏蔽电测",
                "将成品线接入批准的HDMI线缆测试系统，执行P1至P19映射、开路、短路、错线及屏蔽/地连续性检查。",
                "在功能测试前拦截端接和线序缺陷，避免把设备显示正常误当作全部针位正确。",
                ["核对测试程序与产品版本。", "连接两端并执行全针位扫描。", "保存原始测试结果。", "失败品保留错误针位和端别。"],
                ["P1-P19导通、错线、开路、短路、屏蔽和地连续性。"],
                ["按批准测试程序判定；程序、阈值或治具未批准前只能记录草案结果，不得正式放行。"],
                ["电测原始记录", "测试程序版本", "设备/治具编号"],
                ["任一失败立即隔离并禁止直接重复测试覆盖首次失败记录。"],
                equipment=["HDMI线缆测试仪（型号资料未提供）"],
                fixtures=["HDMI测试治具（编号资料未提供）"],
                unknowns=[shared_equipment, electrical_test],
            ),
            step(
                "HD-11", 11, "音视频与连接稳定性功能测试",
                "依据客户/产品批准规格连接信号源和显示端，验证规定的音视频模式、握手、方向性和连接稳定性。",
                "导通正确不能代替带宽、协议兼容和实际音视频功能验证。",
                ["核对功能测试规范和设备组合。", "按规定模式连接并上电。", "运行规定时长和切换项目。", "记录模式、现象和原始结果。"],
                ["画面、声音、握手、间歇、方向性、分辨率/刷新率/带宽模式。"],
                ["全部项目按批准规范通过；未给出HDMI版本和模式时不得自行选取‘最高规格’作为结论。"],
                ["功能测试原始记录", "信号源/显示端配置", "测试程序版本"],
                ["黑屏、闪屏、无声、掉线、握手失败或间歇时隔离并保留复现条件。"],
                equipment=["批准的信号源、显示端和功能测试治具（型号资料未提供）"],
                fixtures=["接口固定/防松治具（编号资料未提供）"],
                unknowns=[shared_equipment, functional_test],
            ),
            step(
                "HD-12", 12, "成品尺寸、拉力与外观终检",
                "检查成品总长、两端外露尺寸、连接器外观、线身印字、应力释放和表面状态；仅在规范要求时执行批准的拉力项目。",
                "确认产品机械尺寸、标识和外观满足交付要求，并拦截加工损伤。",
                ["在规定张力状态测量总长。", "测量端部关键尺寸。", "旋转检查连接器六面和整段线身。", "按批准规范执行或核验拉力记录。"],
                ["总长、端部尺寸、插头变形、线身伤痕、印字、应力释放和污染。"],
                ["尺寸与外观按受控图纸/限度样板判定；拉力值和方法未给出时不得自拟。"],
                ["成品尺寸记录", "外观终检记录", "拉力记录（如适用）"],
                ["尺寸或外观不合格时隔离并追溯到对应加工批次。"],
                equipment=["长度量具、外观检验台、拉力设备（适用性/型号资料未提供）"],
                unknowns=[cut_strip_window, unknown("finished_cable_mechanical_test", "输入资料没有给出成品长度测量张力、关键尺寸公差和拉力项目/限值。", "产品工程师/品质工程师", "受控工程图、检验规范和限度样板")],
            ),
            step(
                "HD-13", 13, "盘线、扎带与端头防护",
                "按批准的最小弯曲半径和盘线直径自然盘线，避免扭结，并按规定位置扎带和安装端头防尘件。",
                "保护线身和连接器，形成一致且不损伤高速线材的包装形态。",
                ["确认线材自然无扭结。", "按批准盘径逐圈盘线。", "扎带固定但不得压伤外被。", "安装防尘件并检查端头不受压。"],
                ["盘径、弯折、扭结、扎带位置/松紧、端头防护。"],
                ["盘线形态与受控包装规范一致，线身无急弯和压痕。"],
                ["盘线/扎带自检记录"],
                ["发生过弯、扭结或压伤时隔离检查，不得简单重新盘线后放行。"],
                materials=["扎带、端头防尘件（规格资料未提供）"],
                equipment=["盘线工装（直径资料未提供）"],
                unknowns=[packaging_release],
            ),
            step(
                "HD-14", 14, "装袋、贴标与装箱复核",
                "按受控包装BOM完成单件装袋/装盒、标签核对与粘贴、装箱计数、箱唛和封箱。",
                "建立正确的销售/运输包装和产品追溯，防止错标、混长、少装或运输挤压。",
                ["核对包材和标签稿版本。", "扫描或人工核对料号、长度和批次。", "按批准数量装箱并交叉复核。", "记录箱号、批次和数量。"],
                ["包材规格、标签内容/位置/可读性、装箱数量、防护和箱唛。"],
                ["全部包装项目与受控BOM/包装规范一致；资料未批准前保持草案。"],
                ["单件包装记录", "标签首件记录", "装箱/箱号追溯记录"],
                ["错标、混箱、数量或包材版本不一致时隔离受影响包装单元并追溯。"],
                materials=["内袋/彩盒/标签/纸箱/封箱材料（具体规格资料未提供）"],
                equipment=["扫码、打印、计数或称重设备（适用性/型号资料未提供）"],
                unknowns=[packaging_release],
            ),
            step(
                "HD-15", 15, "记录复核、异常隔离与人工放行闸门",
                "汇总各工序原始记录、设备/程序版本和异常处置；阻断未决项、试产和签核未关闭前保持DRAFT。",
                "确保经过人工核对的路线才能沉淀为可复用知识，且草案不会被误当成生产批准。",
                ["检查每个工序记录完整性。", "核对blocking unknown和异常状态。", "区分合格、待判和不合格产品。", "提交工程、生产、质量、EHS/IE适用角色审核。"],
                ["记录完整、异常隔离、未决项关闭、审核状态和版本一致性。"],
                ["所有正式闸门通过并形成不可变人工批准快照后，路线才可进入正式复用索引。"],
                ["工序记录汇总", "异常/MRB记录", "人工审核决定", "批准快照（批准后）"],
                ["记录缺失、未决项未关闭或签核不完整时保持under_review，不自动放行。"],
                unknowns=[shared_equipment, cut_strip_window, termination_window, electrical_test, functional_test, packaging_release, unknown("formal_approval", "本路线尚未完成人工逐字段确认、现场试产和正式签核。", "工程/生产/质量/批准人", "工作台确认记录、试产记录、培训记录和正式批准快照")],
            ),
        ]
        return RouteDraft(
            product=identity,
            route_name=f"{identity.product_code} HDMI成品线完整制造路线",
            route_summary="按产品身份、备料、裁线、剥线、屏蔽、端接、装壳、电测、功能、终检和包装拆解；具体设备、参数和判据保持结构化未决项并等待人工逐项确认。",
            source_kind="family_template",
            steps=steps,
            route_unknowns=[shared_equipment, cut_strip_window, termination_window, electrical_test, functional_test, packaging_release],
        )

    def _draft_rj45(self, identity: ProductIdentity, data: dict[str, Any]) -> RouteDraft:
        acceptance = refs(data, "acceptance_or_order", excerpt="产品身份、材料和性能要求")
        drawing = refs(data, "engineering_drawing", excerpt="结构、关键尺寸、材料和镀层要求")
        bom = refs(data, "bom", excerpt="仅采用明确命中产品料号的身份/用量事实")
        hole = str(data.get("hole_mm"))
        dimensions = ", ".join(data.get("key_dimensions") or [])
        force = str(data.get("force_n"))
        shared_unknowns = [
            unknown("equipment", "资料未给出压接机、影像测量仪或电测设备的受控型号，不能据版面推定。", "工艺工程师/品质工程师", "现场设备卡、量具台账和校准状态"),
            unknown("sampling", "承认书与工程图未提供本工序抽样频次。", "品质工程师", "受控检验规范或AQL抽样方案"),
        ]
        steps = [
            RouteStepDraft(
                step_code="RJ-01", sequence_no=1, title="产品身份与受控资料核对",
                action="在拆包和检验前核对产品料号、供应商规格号、图号、版本以及本批来料标签，确认资料只属于当前产品。",
                why="阻止相似水晶头的孔径、排列、壳体材料或镀层要求串用。",
                inputs=[identity.product_code, identity.product_name, data.get("supplier_part", ""), data.get("drawing_no", "")],
                materials=["本批水晶头来料及其标签/送检单"], tool_equipment=["文控系统或受控纸质资料"], fixtures=[],
                parameters=[{"name":"目标料号","value":identity.product_code,"source":"承认书/BOM","status":"confirmed"}],
                method=["逐字符核对料号和供应商规格号。", "核对图号/版本与送检单。", "发现其他型号内容时停止并隔离资料及物料。"],
                quality_check=["料号、品名、图号、版本、批次标签一致性。"],
                acceptance_criteria=[f"所有身份字段一致且仅出现 {identity.product_code}；任何不一致不得转入后续检验。"],
                safety=["保持不同型号物料物理分区，避免混料。"], record_output=["来料身份核对记录", "资料冲突清单（如有）"],
                exception=["身份不一致时整批暂停，贴隔离标识并提交品质/工程判定。"], unknowns=[], evidence={"identity": acceptance + bom},
            ),
            RouteStepDraft(
                step_code="RJ-02", sequence_no=2, title="来料批次与组成件齐套检查",
                action="按承认书结构和材料明细核对胶芯、接触片、分线架/两件式结构及金属壳的组成关系与批次标识。",
                why="在尺寸和性能检验前确认检验对象组成完整，避免把下游成品BOM的引用用量误当制造分解BOM。",
                inputs=["承认书结构页", "工程图材料栏", "材料明细表明确命中行"], materials=["胶芯", "接触片", "分线架/两件式结构", data.get("shell_material", "金属壳")],
                tool_equipment=["照明放大镜（倍率资料未给出）"], fixtures=["分格防混料盘（编号资料未给出）"], parameters=[],
                method=["按组成件类别分格摆放。", "核对数量关系仅到资料明确范围。", "记录没有制造级单件用量的组成件。"],
                quality_check=["组成件类别、结构型式、颜色/壳体材料与资料一致。"], acceptance_criteria=["组成关系一致；制造级BOM缺失项必须留在unknown清单，不得自行补数。"],
                safety=["金属接触片和壳体边缘可能锐利，取放时佩戴适当手部防护。"], record_output=["来料齐套记录", "制造BOM缺口清单"],
                exception=["缺件、混件或材料描述冲突时分批隔离并保留原标签。"],
                unknowns=[unknown("manufacturing_bom", "现有BOM只确认产品身份或作为下游成品组件的用量，不能证明本体制造分解用量。", "产品工程师", "受控MBOM及单件用量")], evidence={"materials": acceptance + drawing + bom},
            ),
            RouteStepDraft(
                step_code="RJ-02.1", sequence_no=2.1, parent_step_code="RJ-02", title="胶芯与分线架结构核验",
                action="检查胶芯为PC/UL94V-2资料要求，确认长体双排6上2下两件式结构及分线架方向。",
                why="胶芯材料和线孔排列决定导线导入、接触位置与绝缘性能。",
                inputs=["工程图材料说明", "承认书结构图"], materials=["PC胶芯", "分线架/两件式结构"], tool_equipment=["放大镜或影像设备（受控型号缺失）"], fixtures=[],
                parameters=[{"name":"胶芯材料","value":"PC / UL94V-2","source":"工程图","status":"confirmed"},{"name":"线孔排列","value":"双排6上2下","source":"承认书/工程图","status":"confirmed"}],
                method=["从正面和侧面确认结构。", "确认分线架插入方向标识。", "检查缺胶、裂纹、变形和明显毛边。"], quality_check=["材料标识/结构、孔位排列和外观。"],
                acceptance_criteria=["结构与工程图一致；不得有影响装配或绝缘的裂纹、缺胶、堵孔。"], safety=["不得使用会侵蚀PC的清洗剂。"], record_output=["胶芯/分线架检查记录"],
                exception=["结构或材料不符时隔离该批并通知材料/品质工程师。"], unknowns=shared_unknowns, evidence={"material": drawing + acceptance},
            ),
            RouteStepDraft(
                step_code="RJ-02.2", sequence_no=2.2, parent_step_code="RJ-02", title="接触片与壳体材料核验",
                action="核对接触片为H65/CuZn37黄铜、壳体为资料指定铜壳，并确认壳体型式与自扣结构。",
                why="接触片基材和壳体结构影响导电、压接和屏蔽可靠性。",
                inputs=["工程图材料栏", "承认书材料说明", "BOM明确命中行"], materials=["H65/CuZn37接触片", data.get("shell_material", "铜壳")], tool_equipment=["放大镜", "材质证明文件"], fixtures=[],
                parameters=[{"name":"接触片基材","value":"H65 / CuZn37","source":"工程图","status":"confirmed"}],
                method=["核对来料材质证明。", "检查接触片方向、缺片、弯折。", "检查壳体自扣部位和表面状态。"], quality_check=["材质证明、8片接触片完整性、壳体型式。"],
                acceptance_criteria=["材质与资料一致，接触片完整无严重变形，壳体无开裂或妨碍扣合的变形。"], safety=["接触片/壳体锐边防割伤。"], record_output=["接触片/壳体材料核验记录"],
                exception=["材质证明缺失或壳体型式不符时不得放行。"], unknowns=shared_unknowns, evidence={"material": drawing + acceptance + bom},
            ),
            RouteStepDraft(
                step_code="RJ-03", sequence_no=3, title="线孔与接触片几何检查",
                action=f"使用经校准量具确认线孔目标尺寸 {hole} mm，并检查8片接触片的排列、窗口和导向结构。",
                why="线孔和接触片位置直接决定穿线、压接与触点接触。",
                inputs=["工程图线孔/接触片视图", "受控尺寸检验方案"], materials=["已完成组成核验的水晶头"], tool_equipment=["影像测量仪或针规（型号及方法未提供）"], fixtures=["定位治具（编号未提供）"],
                parameters=[{"name":"线孔名义尺寸","value":f"{hole} mm","source":"承认书/工程图","status":"confirmed"}],
                method=["清洁量具并确认校准状态。", "按工程图基准定位。", "逐孔或按批准抽样方案测量并记录。", "观察接触片缺片、翘曲和间距异常。"], quality_check=["孔径、孔位完整性、接触片数量与排列。"],
                acceptance_criteria=[f"孔径符合受控工程图对 {hole} mm 的公差要求；8片接触片完整且不干涉。具体量具方法需批准后锁定。"], safety=["量测时避免针规或治具划伤胶芯。"], record_output=["孔径/接触片检验记录", "量具编号与校准有效期"],
                exception=["超差或结构异常时按批次隔离，保留测量原始值并发起不合格评审。"], unknowns=shared_unknowns, evidence={"parameter": drawing + acceptance},
            ),
            RouteStepDraft(
                step_code="RJ-04", sequence_no=4, title="关键外形尺寸测量",
                action="按工程图基准逐项测量关键外形尺寸，并将原始值与图纸公差比较。",
                why="确认水晶头能与配合件正确插拔、扣合和定位。",
                inputs=["工程图尺寸页", "受控量测方案"], materials=["待检水晶头"], tool_equipment=["影像测量仪/卡尺/高度规（具体分配未知）"], fixtures=["产品定位治具（编号未知）"],
                parameters=[{"name":"关键外形尺寸","value":dimensions,"source":"工程图/OQC","status":"confirmed"}],
                method=["按工程图基准建立坐标。", "依尺寸清单逐项测量。", "记录原始读数，不只填写合格/不合格。"], quality_check=["尺寸值、公差、基准方向和量具校准状态。"], acceptance_criteria=[f"所列关键尺寸分别落入工程图公差：{dimensions}。"],
                safety=["夹持力不得导致PC胶芯变形。"], record_output=["关键尺寸原始记录", "不合格尺寸编号"], exception=["任一关键尺寸超差时隔离并提交品质工程师判定，禁止挑选混批放行。"], unknowns=shared_unknowns, evidence={"parameter": drawing},
            ),
            RouteStepDraft(
                step_code="RJ-05", sequence_no=5, title="镀层与材料报告核验",
                action="核对接触片镀层报告和来料材质报告，确认Ni底层与Au FU/Flash边界没有被误写为厚金。",
                why="镀层体系影响接触电阻、耐磨和腐蚀性能，且FU/Flash不能从价格或历史产品反推。",
                inputs=["工程图镀层说明", "膜厚/盐雾报告", "材质报告"], materials=["接触片", "壳体"], tool_equipment=["XRF膜厚仪（若现场复测；型号未知）"], fixtures=["XRF定位夹具（未知）"],
                parameters=[{"name":"Ni底层","value":"50 μin","source":"工程图/膜厚要求","status":"confirmed"},{"name":"Au镀层","value":"FU / Flash，仅按受控SPEC","source":"工程图/BOM边界","status":"confirmed"}],
                method=["核对报告产品/批次和检测日期。", "核对Ni与Au项目及单位。", "如需现场复测，按批准XRF程序执行。"], quality_check=["报告身份、Ni 50 μin、Au FU/Flash边界、批次关联。"], acceptance_criteria=["报告与当前批次关联且结果满足受控SPEC；不得把FU/Flash解释为未提供的金厚数值。"],
                safety=["XRF设备仅由授权人员按辐射安全规程操作。"], record_output=["镀层/材质报告核验记录", "报告编号"], exception=["报告缺失、单位不明或超限时隔离并提交品质/供应商处理。"], unknowns=shared_unknowns, evidence={"parameter": drawing + bom},
            ),
            RouteStepDraft(
                step_code="RJ-06", sequence_no=6, title="电气与机械性能资料核验/试验",
                action="按承认书要求核对或执行额定、电气、插拔寿命与保持力试验；没有批准设备和方法时仅核验受控报告，不自行试验。",
                why="确认产品满足连接器基本电气绝缘、接触和机械寿命要求。",
                inputs=["承认书性能要求", "受控试验报告"], materials=["性能试验样品"], tool_equipment=["耐压/绝缘/接触电阻/插拔寿命设备（型号未知）"], fixtures=["RJ45性能试验夹具（编号未知）"],
                parameters=[{"name":"额定","value":"125VAC 1.5A","source":"承认书","status":"confirmed"},{"name":"耐压","value":"1000VAC / 1 min","source":"承认书","status":"confirmed"},{"name":"绝缘电阻","value":"≥500 MΩ","source":"承认书","status":"confirmed"},{"name":"接触电阻","value":"≤20 mΩ","source":"承认书","status":"confirmed"},{"name":"插拔寿命","value":"≥750次","source":"承认书","status":"confirmed"},{"name":"保持力","value":f"{force} N","source":"承认书","status":"confirmed"}],
                method=["优先核验当前批次受控报告。", "需现场复验时由品质工程师批准试验方法和样本。", "逐项记录原始数据及设备编号。"], quality_check=["电气、寿命、保持力结果及报告批次关联。"], acceptance_criteria=[f"满足125VAC 1.5A、1000VAC/1min、IR≥500MΩ、接触≤20mΩ、寿命≥750次、保持力{force}N等受控要求。"],
                safety=["耐压测试必须由授权人员执行，测试区防触电并完成放电。"], record_output=["性能报告核验表或现场试验原始记录"], exception=["任一性能项目不合格时整批暂停放行并发起MRB/供应商纠正。"],
                unknowns=[unknown("test_equipment_method", "资料给出性能判据但未给出现场设备型号、接线方法和抽样数。", "品质工程师", "批准试验规范、设备卡和抽样方案")], evidence={"parameter": acceptance},
            ),
            RouteStepDraft(
                step_code="RJ-07", sequence_no=7, title="外观、清洁与溶剂隔离检查",
                action="检查胶芯、接触片和壳体外观，确认无污染、裂纹、明显变形；清洁时执行PC材料溶剂禁忌。",
                why="防止外观缺陷和不相容溶剂造成PC应力开裂或后续接触失效。",
                inputs=["外观判定要求", "溶剂隔离通知"], materials=["已检水晶头", "批准的无尘清洁材料"], tool_equipment=["照明放大镜"], fixtures=["防混料盘"],
                parameters=[{"name":"环境温度边界","value":"-40~85 °C（产品要求）","source":"承认书","status":"confirmed"}],
                method=["在规定照明下旋转检查各面。", "发现残屑时只使用批准清洁方法。", "禁止让PC接触未批准溶剂并与高温水源隔离。"], quality_check=["裂纹、缺胶、毛边、接触片污染、壳体变形和溶剂接触风险。"], acceptance_criteria=["无影响功能/装配的外观缺陷；PC未接触禁用溶剂。"],
                safety=["化学品仅按SDS和现场批准清单使用。"], record_output=["外观检查记录", "清洁/化学品异常记录"], exception=["怀疑溶剂接触或PC应力开裂时整批隔离，不得清洗后直接放行。"], unknowns=[unknown("approved_cleaner", "通知说明PC需隔离溶剂，但未给出现场批准清洁剂牌号。", "EHS/工艺工程师", "批准化学品清单与SDS")], evidence={"safety": acceptance + drawing},
            ),
            RouteStepDraft(
                step_code="RJ-08", sequence_no=8, title="不合格隔离、记录复核与草案放行",
                action="汇总身份、材料、尺寸、镀层、性能和外观记录；将不合格批次物理隔离，仅在人工审核完成后形成放行结论。",
                why="确保记录闭环并防止AI草案或缺失现场参数被当成正式生产批准。",
                inputs=["本路线全部检验记录", "不合格报告", "人工审核决定"], materials=["合格批与隔离批"], tool_equipment=["QMS/纸质隔离标签"], fixtures=["隔离区/锁定容器"], parameters=[],
                method=["逐项确认记录完整。", "检查unknown是否已由责任角色关闭。", "区分合格、待判和不合格批次。", "签核为空时保持DRAFT。"], quality_check=["记录完整性、unknown关闭状态、批次物理隔离。"], acceptance_criteria=["所有阻断unknown关闭且人工审核通过后才可进入正式发行流程；本轮输出仍为DRAFT。"],
                safety=["不合格品不得回流合格区。"], record_output=["检验汇总", "隔离/MRB记录", "人工审核意见"], exception=["记录缺失或审批未完成时保持under_review，不生成生产放行。"], unknowns=[unknown("formal_approval", "当前为AI生成与演示审核，未取得正式生产批准。", "批准人/变更委员会", "签署的批准快照和受控发行号")], evidence={"record": acceptance + drawing + bom},
            ),
        ]
        return RouteDraft(
            product=identity,
            route_name=f"{identity.product_code} 来料检验与装配前放行路线",
            route_summary="基于承认书、已核对工程图与明确命中BOM事实建立；不把缺失制造BOM和现场参数补写为事实。",
            source_kind="family_template", steps=steps,
            route_unknowns=[unknown("manufacturing_route", "资料不足以证明完整制造装配路线，本草案限定为来料检验/装配前放行。", "工艺工程师", "受控制造流程卡、MBOM和现场设备参数")],
        )

    def _draft_optical_cable(self, identity: ProductIdentity, data: dict[str, Any]) -> RouteDraft:
        order = refs(data, "acceptance_or_order", excerpt="订单料号、长度、规格和包装要求")
        bom = refs(data, "bom", excerpt="成品BOM材料、包材和成品型号行")
        length = str(data.get("length_m"))
        package = data.get("package", {})
        components = data.get("components", "")
        equipment_unknown = unknown("equipment", "订单和BOM没有提供焊接、注塑、装壳和功能测试设备型号或参数。", "工艺工程师", "现场设备卡、工艺参数卡和试产确认记录")
        test_unknown = unknown("functional_test", "资料只给出产品身份/规格，未提供功能测试接线、分辨率/带宽判定方法。", "测试/品质工程师", "批准测试规范、设备清单和合格样本")
        steps = [
            RouteStepDraft(step_code="OC-01",sequence_no=1,title="订单、料号与版本核对",action=f"核对订单与成品BOM中的料号 {identity.product_code}、长度 {length}M、HDTV2.1/8K身份和包装备注。",why="防止长度、外壳颜色、包装版本和相似型号混用。",inputs=["当前订单行","成品BOM成品型号行"],materials=["本批成品/半成品和包装物料"],tool_equipment=["受控订单/BOM查看终端"],fixtures=[],parameters=[{"name":"长度","value":f"{length}M","source":"订单/BOM","status":"confirmed"},{"name":"规格身份","value":"HDTV2.1 / 8K","source":"订单","status":"confirmed"}],method=["逐字符核对料号。","核对长度、颜色/壳体、包装备注。","对订单中‘122HZ’原文只记录冲突，不改写为技术参数。"],quality_check=["订单/BOM身份一致性和包装版本。"],acceptance_criteria=[f"料号 {identity.product_code}、{length}M、产品描述一致；有冲突时停止。"],safety=["不同长度线材分区，防止混料。"],record_output=["订单/BOM身份核对记录"],exception=["任一身份字段不一致时隔离并提交PMC/工程澄清。"],unknowns=[unknown("order_122hz", "订单存在‘122HZ’原文但缺少批准技术定义，不能解释为带宽/刷新率。", "产品经理/工程师", "客户确认规格或受控技术规格")],evidence={"identity":order+bom}),
            RouteStepDraft(step_code="OC-02",sequence_no=2,title="材料与模组齐套",action="按成品BOM材料段备齐线材、短距TX/RX模组、枪黑锌合金壳和防尘盖，并保持方向/批次可追溯。",why="确保装配输入物料正确且不把库存、价格或公式结果当作工艺参数。",inputs=["成品BOM材料段","领料单"],materials=[components],tool_equipment=["分格料盘","条码/标签核对工具（若现场配置）"],fixtures=["防混料周转盘（编号未知）"],parameters=[{"name":"线材长度","value":f"XC005 30# {length}M","source":"BOM","status":"confirmed"},{"name":"TX/RX数量","value":"TX 1PCS / RX 1PCS","source":"BOM","status":"confirmed"},{"name":"防尘盖","value":"2PCS","source":"BOM","status":"confirmed"}],method=["按BOM逐项点料。","TX与RX分格放置并核对料号。","核对壳体颜色和数量。","保留原批次标签。"],quality_check=["料号、规格、数量、方向标识和批次。"],acceptance_criteria=["BOM明确项目齐套且无混料；缺少的制造过程辅料不得自行补入。"],safety=["线缆盘放稳定，避免绊倒和过度弯折。"],record_output=["领料/齐套记录","缺料清单"],exception=["缺料、混料或批次不清时停止齐套并隔离。"],unknowns=[unknown("manufacturing_consumables", "BOM未证明焊料、胶水、注塑料等本工序辅料和用量。", "工艺/产品工程师", "受控MBOM和工艺配方")],evidence={"materials":bom}),
            RouteStepDraft(step_code="OC-02.1",sequence_no=2.1,parent_step_code="OC-02",title="线材与TX/RX方向核对",action="核对XC005线材长度以及短距TX、RX模组料号，保持TX/RX方向标识不互换。",why="主动光纤线存在方向性，TX/RX错装将导致功能失效。",inputs=["BOM TX/RX料号行"],materials=[f"XC005光纤铜包钢30# {length}M","短距TX YA.C.01.MZ21094 1PCS","短距RX YA.C.01.MZ21095 1PCS"],tool_equipment=["标签核对工具"],fixtures=["TX/RX分格盘"],parameters=[],method=["将TX、RX分别放入标识格。","逐一核对料号后再转入装配。","不得仅按外观判断方向。"],quality_check=["TX/RX料号与方向标签。"],acceptance_criteria=["TX和RX各1件，料号与BOM一致，方向可追溯。"],safety=["端头防尘，禁止触碰光学/电气接口。"],record_output=["TX/RX方向核对记录"],exception=["方向标签缺失时隔离，禁止猜测装配。"],unknowns=[],evidence={"materials":bom}),
            RouteStepDraft(step_code="OC-02.2",sequence_no=2.2,parent_step_code="OC-02",title="壳体与防尘件核对",action="核对枪黑锌合金壳套件和2个HDMI防尘盖的料号、颜色、表面及数量。",why="确保成品外观、防护和包材齐套。",inputs=["BOM壳体/防尘盖行"],materials=["YA.F.01.041枪黑锌合金壳1套","HDMI防尘盖2PCS"],tool_equipment=["照明检查台"],fixtures=["防刮垫"],parameters=[],method=["确认壳体成套。","检查表面无明显划伤、变形。","防尘盖按两端各1件分配。"],quality_check=["壳体颜色/表面/数量，防尘盖数量。"],acceptance_criteria=["壳体1套、防尘盖2个，外观和料号与BOM一致。"],safety=["金属壳边缘防割伤，表面防刮。"],record_output=["壳体/防尘件核对记录"],exception=["数量或颜色不符时隔离，不得拼套。"],unknowns=[],evidence={"materials":bom}),
            RouteStepDraft(step_code="OC-03",sequence_no=3,title="TX/RX端装配准备",action="按方向标识将线材两端与对应TX/RX模组配对，确认端部状态和待装壳方向；本轮不下发未经证实的焊接/注塑参数。",why="在不可逆装配前建立正确的方向和部件关系。",inputs=["齐套后的线材、TX/RX模组","受控工艺卡（当前缺失）"],materials=["线材","TX模组","RX模组"],tool_equipment=["装配/焊接/注塑设备（unknown）"],fixtures=["端头定位治具（unknown）"],parameters=[],method=["复核TX/RX方向。","检查端部无污染和损伤。","按现场受控工艺卡定位；无卡则停止。"],quality_check=["方向、料号、端部外观和工艺卡可用性。"],acceptance_criteria=["方向关系正确且受控工艺卡/设备参数已由工艺工程师确认后方可装配。"],safety=["端部装配设备仅由授权人员操作。"],record_output=["装配前方向确认记录","设备/工艺卡编号"],exception=["工艺卡或设备参数缺失时保持阻断，不得凭经验生成数值。"],unknowns=[equipment_unknown],evidence={"materials":bom}),
            RouteStepDraft(step_code="OC-04",sequence_no=4,title="端头壳体装配与防护",action="依据现场批准工艺将TX/RX端装入对应枪黑锌合金壳，装后检查壳体定位、接口和防尘盖配合。",why="完成端头机械保护并保持方向、接口和外观可靠。",inputs=["方向确认后的端头","壳体套件","受控装配工艺卡"],materials=["枪黑锌合金壳1套","防尘盖2PCS"],tool_equipment=["装壳/锁附设备（unknown）"],fixtures=["壳体定位治具（unknown）"],parameters=[],method=["按TX/RX对应壳体分配。","按批准工艺装壳并确认不夹伤线缆。","检查接口可见部分和防尘盖配合。"],quality_check=["壳体定位、松动、夹线、接口损伤和防尘盖配合。"],acceptance_criteria=["壳体完整定位、无松动/夹线/明显划伤；防尘盖可正常装配。"],safety=["装壳设备防夹手，金属壳防刮伤。"],record_output=["端头装配检查记录"],exception=["壳体变形、松动或接口受损时隔离，禁止返修后直接混入合格批。"],unknowns=[equipment_unknown],evidence={"materials":bom}),
            RouteStepDraft(step_code="OC-05",sequence_no=5,title="长度、功能与外观检验",action=f"测量成品长度并执行批准的方向/信号功能检验，同时检查线身、接口、壳体和防尘件外观。",why=f"确认 {length}M 产品身份、方向功能和装配外观满足受控要求。",inputs=["装配成品","订单规格","批准测试规范（当前缺失）"],materials=["待检成品"],tool_equipment=["长度量具","信号源/显示端/测试仪（unknown）"],fixtures=["测试连接夹具（unknown）"],parameters=[{"name":"标称长度","value":f"{length}M","source":"订单/BOM","status":"confirmed"}],method=["在不施加拉伸的状态测量长度。","按批准规范连接TX/RX方向。","执行功能测试并记录原始结果。","检查线身、端头、接口和防尘件。"],quality_check=["长度、方向、功能、接口、线身和壳体外观。"],acceptance_criteria=[f"产品身份为 {length}M；长度公差和功能判据需由批准规范提供，未提供前不得判正式合格。"],safety=["测试设备可靠接地，避免激光/光源接口直视和线缆绊倒。"],record_output=["长度原始记录","功能测试记录","外观检查记录"],exception=["无信号、间歇、方向异常或外观缺陷时隔离并保留端别信息。"],unknowns=[test_unknown,unknown("length_tolerance", "订单/BOM给出标称长度但未给出成品长度公差和测量张力。", "产品/品质工程师", "客户规格或批准检验规范")],evidence={"parameter":order+bom}),
            RouteStepDraft(step_code="OC-06",sequence_no=6,title="盘线、扎带与网套防护",action=f"在不小于批准最小弯曲半径的前提下盘线，使用 {package.get('tie','BOM扎带')} 固定并配置 {package.get('sleeve','网套')}。",why="形成稳定包装形态并防止光纤线过弯、松散和表面擦伤。",inputs=["检验后的成品","BOM包材段"],materials=[package.get("tie",""),package.get("sleeve","")],tool_equipment=["盘线工装（直径unknown）"],fixtures=["盘线治具（编号unknown）"],parameters=[{"name":"扎带","value":package.get("tie",""),"source":"BOM","status":"confirmed"},{"name":"网套","value":package.get("sleeve",""),"source":"BOM","status":"confirmed"}],method=["确认线缆自然无扭结。","按批准盘径盘线。","扎带固定但不得压伤线身。","网套覆盖易摩擦部位。"],quality_check=["盘线无急弯/扭结、扎带规格、网套数量和表面防护。"],acceptance_criteria=["扎带/网套与BOM一致；盘径必须在工艺工程师确认后锁定。"],safety=["长线盘绕防绊倒，保持工位通道畅通。"],record_output=["盘线/内防护自检记录"],exception=["发现过弯、扭结或护套损伤时隔离检查，不得直接重新盘绕放行。"],unknowns=[unknown("minimum_bend_radius", "资料未提供主动光纤线最小弯曲半径和批准盘线直径。", "产品/工艺工程师", "线缆技术规格和盘线治具卡")],evidence={"packaging":bom}),
            RouteStepDraft(step_code="OC-07",sequence_no=7,title="内袋与彩盒包装",action=f"按订单和BOM确认后，将盘线成品装入 {package.get('inner_bag','内袋')} 和 {package.get('box','彩盒')}，避免端头受压。",why="完成单件防尘、防刮和零售包装。",inputs=["订单包装备注","BOM包材段"],materials=[package.get("inner_bag",""),package.get("box","")],tool_equipment=["包装工作台"],fixtures=["防刮垫"],parameters=[],method=["核对内袋和彩盒规格。","端头加防尘盖并避开受压位置。","装袋后排除可能导致挤压的折叠。","装入彩盒并检查闭合。"],quality_check=["内袋/彩盒规格、端头防护、包装闭合和外观。"],acceptance_criteria=["包装物料与当前订单/BOM一致，成品无挤压或裸露。"],safety=["使用刀具时刀刃远离产品和人员。"],record_output=["单件包装自检记录"],exception=["包材规格冲突时暂停并由PMC/工程确认当前受控版本。"],unknowns=[],evidence={"packaging":order+bom}),
            RouteStepDraft(step_code="OC-08",sequence_no=8,title="标签核对与粘贴",action=f"核对并粘贴 {package.get('labels','标签')}，确认料号、长度和包装版本与成品一致。",why="保证客户识别、追溯和防止不同长度产品混箱。",inputs=["订单标签要求","BOM标签行","批准标签稿"],materials=[package.get("labels","")],tool_equipment=["标签打印/扫码设备（如现场配置，型号unknown）"],fixtures=["标签定位样板（unknown）"],parameters=[],method=["核对标签稿版本。","扫描或人工核对料号/长度。","在批准位置平整粘贴。","复核无重贴、翘边和错标。"],quality_check=["标签数量、内容、版本、位置和可读性。"],acceptance_criteria=[f"标签指向 {identity.product_code}/{length}M，数量与BOM一致且清晰牢固。"],safety=["标签离型纸及时收集，避免地面滑倒。"],record_output=["标签首件/抽检记录"],exception=["错标或标签版本不明时隔离整批已贴产品并追溯。"],unknowns=[unknown("label_artwork", "BOM给出标签数量但未提供受控标签图稿和定位尺寸。", "PMC/品质工程师", "批准标签稿、条码规则和定位样板")],evidence={"packaging":order+bom}),
            RouteStepDraft(step_code="OC-09",sequence_no=9,title="装箱与数量复核",action=f"按 {package.get('carton','BOM纸箱规则')} 装箱，逐箱复核数量、方向防护和箱唛。",why="形成可追溯运输单元并防止少装、多装和运输挤压。",inputs=["BOM纸箱行","订单装箱要求"],materials=[package.get("carton","")],tool_equipment=["计数器/电子秤（若批准）","封箱工具"],fixtures=["装箱定位隔板（如有，资料未给出）"],parameters=[{"name":"装箱数量","value":"12PCS/箱","source":"BOM","status":"confirmed"}],method=["核对纸箱规格。","按防压方向装入单件包装。","计数12PCS并交叉复核。","封箱并粘贴箱唛。"],quality_check=["纸箱规格、装箱数量、箱内防护、箱唛和封箱。"],acceptance_criteria=["纸箱42×42×22且12PCS/箱（按当前BOM）；订单另有要求时以受控工单确认。"],safety=["搬运遵守重量限制，封箱工具防割伤。"],record_output=["装箱计数记录","箱号/批次追溯记录"],exception=["数量或纸箱版本不一致时拆箱复核并隔离。"],unknowns=[unknown("carton_weight_limit", "资料未提供单箱毛重和搬运限值。", "包装工程师/EHS", "包装规范和搬运风险评估", blocking=False)],evidence={"packaging":bom}),
            RouteStepDraft(step_code="OC-10",sequence_no=10,title="异常隔离、记录复核与草案放行",action="复核齐套、装配、功能、外观和包装记录；异常品单独隔离，未完成人工批准前不得形成生产放行。",why="确保工序记录、异常和unknown闭环，防止草案自动沉淀为正式知识。",inputs=["全部工序记录","异常单","人工审核决定"],materials=["合格、待判和不合格成品"],tool_equipment=["QMS/隔离标签"],fixtures=["隔离区域"],parameters=[],method=["逐项复核记录。","检查阻断unknown是否关闭。","区分合格/待判/不合格。","签核为空时保持DRAFT。"],quality_check=["记录完整性、异常隔离、unknown关闭和审批状态。"],acceptance_criteria=["阻断unknown关闭且人工审核通过后方可进入正式发行；本轮仍为DRAFT。"],safety=["隔离品不得回流。"],record_output=["工序记录汇总","异常/MRB记录","审核意见"],exception=["记录缺失或审批未完成时保持under_review。"],unknowns=[equipment_unknown,test_unknown,unknown("formal_approval", "本轮没有正式生产批准或现场试产结论。", "批准人/变更委员会", "签署批准快照、试产与培训记录")],evidence={"record":order+bom}),
        ]
        return RouteDraft(product=identity,route_name=f"{identity.product_code} 成品装配检验包装路线",route_summary="订单+BOM证据限定的可执行草案；装配/测试设备参数和长度公差保持结构化unknown。",source_kind="family_template",steps=steps,route_unknowns=[equipment_unknown,test_unknown])


class RouteValidator:
    def validate(self, route: RouteDraft) -> dict[str, Any]:
        text = json.dumps(route.model_dump(mode="json"), ensure_ascii=False)
        wrong_models = [token for token in ("YA.C.06.0008", "USB-C数据线包装") if token in text]
        family_patterns = []
        if route.product.product_code.startswith("YA.C.06."):
            family_patterns = re.findall(r"YA\.C\.06\.\d{4}", text)
        elif route.product.product_code.startswith("W-H"):
            family_patterns = re.findall(r"W-H\d+", text)
        wrong_models.extend(token for token in family_patterns if token != route.product.product_code)
        wrong_models = sorted(set(wrong_models))
        generic_unknown = "待确认" in text
        unresolved_placeholders = sorted(set(re.findall(r"\{[A-Za-z_][A-Za-z0-9_]*\}", text)))
        return {
            "valid": not route.product.conflicts and not wrong_models and not generic_unknown and not unresolved_placeholders,
            "step_count": len(route.steps),
            "top_level_step_count": sum(step.parent_step_code is None for step in route.steps),
            "child_step_count": sum(step.parent_step_code is not None for step in route.steps),
            "wrong_models": wrong_models,
            "unresolved_placeholders": unresolved_placeholders,
            "generic_unknown_absent": not generic_unknown,
            "unknown_items_have_owner_and_evidence": all(item.owner_role and item.required_evidence for step in route.steps for item in step.unknowns),
            "image_policy": "human_uploaded_and_confirmed_only",
        }


def build_route_sections(
    draft: RouteDraft,
    features: ProductFeatureSet,
    ingested: dict[str, Any],
) -> list[RouteSectionDraft]:
    all_unknowns = [item for step in draft.steps for item in step.unknowns]
    evidence = features.evidence

    def select_unknowns(*tokens: str) -> list[UnknownItem]:
        selected = [
            item for item in all_unknowns
            if any(token in item.field_name.lower() for token in tokens)
        ]
        unique: dict[tuple[str, str], UnknownItem] = {}
        for item in selected:
            unique[(item.field_name, item.reason)] = item
        return list(unique.values())

    equipment = sorted({item for step in draft.steps for item in step.tool_equipment if item})
    fixtures = sorted({item for step in draft.steps for item in step.fixtures if item})
    materials = sorted({item for step in draft.steps for item in step.materials if item})
    parameters = [
        {"step_code": step.step_code, **parameter}
        for step in draft.steps for parameter in step.parameters
    ]
    quality = [
        {
            "step_code": step.step_code,
            "checks": step.quality_check,
            "acceptance_criteria": step.acceptance_criteria,
        }
        for step in draft.steps
    ]
    packaging_steps = [
        {"step_code": step.step_code, "title": step.title, "materials": step.materials, "method": step.method}
        for step in draft.steps
        if any(token in step.title for token in ("包装", "标签", "装箱", "盘线"))
    ]
    return [
        RouteSectionDraft(
            section_type="product_identity",
            content={
                "product_code": draft.product.product_code,
                "product_name": draft.product.product_name,
                "aliases": draft.product.aliases,
                "process_family_code": draft.product.process_family_code,
                "description": draft.product.description,
                "features": features.features,
            },
            sources=evidence,
            conflicts=draft.product.conflicts + features.conflicts,
            unknowns=[],
        ),
        RouteSectionDraft(
            section_type="bom_material",
            content={
                "materials_from_route": materials,
                "bom_scope": ingested.get("bom_scope", "只采用明确命中产品身份/材料的行，不反推制造分解BOM。"),
                "manufacturing_bom_complete": False,
            },
            sources=evidence,
            unknowns=select_unknowns("bom", "material", "consumable"),
        ),
        RouteSectionDraft(
            section_type="equipment_fixture",
            content={"equipment_tools": equipment, "fixtures_gauges": fixtures},
            sources=evidence,
            unknowns=select_unknowns("equipment", "tool", "fixture", "gauge"),
        ),
        RouteSectionDraft(
            section_type="process_parameter",
            content={"confirmed_parameters": parameters, "rule": "只保留资料有来源的参数；现场窗口另行审核。"},
            sources=evidence,
            unknowns=select_unknowns("parameter", "tolerance", "bend", "cleaner"),
        ),
        RouteSectionDraft(
            section_type="quality_control",
            content={"step_quality_controls": quality},
            sources=evidence,
            unknowns=select_unknowns("sampling", "test", "quality", "functional"),
        ),
        RouteSectionDraft(
            section_type="packaging_label",
            content={
                "packaging_steps": packaging_steps,
                "packaging_scope": "按订单/BOM明确包装事实审核；无资料时不得自行补充。",
            },
            sources=evidence,
            unknowns=(select_unknowns("packaging", "label", "carton") or [
                unknown(
                    "packaging_release",
                    "当前产品资料未形成独立受控包装/标签批准快照，不能由通用模板代替。",
                    "PMC/包装工程师/品质工程师",
                    "受控包装规范、标签批准稿和装箱要求",
                )
            ]),
        ),
        RouteSectionDraft(
            section_type="ie_timing",
            content={"measured_time": None, "standard_time": None, "unit": "s", "source": "现场IE实测"},
            sources=[],
            unknowns=[unknown(
                "ie_timing",
                "资料未提供现场观测工时、宽放率和标准工时，禁止使用模板估算。",
                "IE工程师",
                "现场时间研究原始记录、样本数和批准宽放规则",
            )],
        ),
        RouteSectionDraft(
            section_type="release_signoff",
            content={
                "prepared_by": "",
                "reviewed_by": "",
                "approved_by": "",
                "release_number": "",
                "status": "DRAFT",
            },
            sources=[],
            unknowns=[unknown(
                "formal_approval",
                "本路线尚未完成生产批准、现场试产和签核快照，不得发布。",
                "批准人/变更委员会",
                "签署的批准快照、受控发行号及适用时的试产/培训记录",
            )],
        ),
    ]


@dataclass
class SopRouteWorkflow:
    store: SopKnowledgeStore
    ingestion: SourceIngestion = ProfileSourceIngestion()
    identity_resolver: IdentityConflictResolver = DeterministicIdentityConflictResolver()
    feature_extractor: FeatureExtractor = DeterministicFeatureExtractor()
    drafter: RouteDrafter = EvidenceBoundRouteDrafter()
    validator: RouteValidator = RouteValidator()

    def build_draft(self, product_code: str, profiles: dict[str, Any]) -> tuple[int, RouteDraft, dict[str, Any]]:
        ingested = self.ingestion.ingest(product_code, profiles)
        identity = self.identity_resolver.resolve(ingested)
        features = self.feature_extractor.extract(identity, ingested)
        self.store.upsert_product(identity, features.features)
        for evidence in features.evidence:
            self.store.add_evidence(identity.product_code, source_type=evidence.source_type, source_path=evidence.source_path, page_or_sheet=evidence.page_or_sheet, excerpt=evidence.excerpt)
        approved = ApprovedOnlyRouteRetriever(self.store).retrieve(product_code, features)
        exact = next((match for match in approved if match.source_product_code == product_code), None)
        if exact:
            raise RuntimeError(f"exact approved route exists: {exact.source_route_id}; create a revision instead")
        near = approved[0] if approved and approved[0].similarity >= 0.85 else None
        if near:
            route_id = self.store.clone_approved_route_as_draft(
                near.source_route_id,
                identity,
                similarity=near.similarity,
                match_basis=near.match_basis,
            )
            cloned = self.store.get_route(route_id)
            validation = {
                "valid": True,
                "step_count": len(cloned["steps"]),
                "source_kind": "similar_approved",
                "reuse_source_route_id": near.source_route_id,
                "field_level_provenance": bool(cloned["provenance"]),
            }
            return route_id, RouteDraft(
                product=identity,
                route_name=cloned["route"]["route_name"],
                route_summary=cloned["route"]["route_summary"],
                source_kind="similar_approved",
                steps=[
                    RouteStepDraft(
                        step_code=step["step_code"], sequence_no=step["sequence_no"],
                        parent_step_code=next((parent["step_code"] for parent in cloned["steps"] if parent["id"] == step["parent_step_id"]), None),
                        title=step["title"], action=step["action"], why=step["why"], inputs=step["input_json"],
                        materials=step["material_json"], tool_equipment=step["tool_equipment_json"], fixtures=step["fixture_json"],
                        parameters=step["parameter_json"], method=step["method_json"], quality_check=step["quality_check_json"],
                        acceptance_criteria=step["acceptance_criteria_json"], safety=step["safety_json"], record_output=step["record_output_json"],
                        exception=step["exception_json"], unknowns=step["unknowns_json"], reviewer_comment=step["reviewer_comment"],
                    ) for step in cloned["steps"]
                ], similarity=near.similarity, reuse_source_route_id=near.source_route_id, match_basis=near.match_basis,
            ), validation
        draft = self.drafter.draft(identity, features, ingested, None)
        validation = self.validator.validate(draft)
        if not validation["valid"]:
            raise ValueError("draft validation failed: " + json.dumps(validation, ensure_ascii=False))
        route_id = self.store.create_route(draft)
        for section in build_route_sections(draft, features, ingested):
            self.store.create_route_section(route_id, section)
        return route_id, draft, validation
