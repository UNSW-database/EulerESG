from __future__ import annotations

"""Cross Analysis fixed ESG catalog (configuration-driven).

This catalog is the *only* KPI universe used by the Cross Analysis records
extractor. It is intentionally strict to reduce drift.

Field meanings (per detail):
- topic_key: 一级导航 key (environment / social_capital / human_capital / leadership_governance / business_model_innovation)
- issue_key: 二级导航 key within the topic
- detail: 具体指标名（用于前端展示、也作为抽取目标）
- aliases_en / aliases_zh: 常见英文/中文表述（用于 embedding 检索与 rerank query pack）
- value_kind: absolute | intensity | ratio | count | money | text
- units_allow: 允许单位集合（强过滤；可为空表示不强约束）
- must_terms: 必须出现的关键词（强约束；为空表示不强约束）
- negative_terms: 负向排除（命中则强扣分/丢弃）
- year_required: 是否必须提供 year（默认 True）

NOTE:
The extractor may still succeed without LLM, but accuracy improves with LLM.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional


ValueKind = str


@dataclass(frozen=True)
class CatalogDetail:
    detail: str
    aliases_en: List[str] = field(default_factory=list)
    aliases_zh: List[str] = field(default_factory=list)
    value_kind: ValueKind = "absolute"  # absolute | intensity | ratio | count | money | text
    units_allow: List[str] = field(default_factory=list)
    must_terms: List[str] = field(default_factory=list)
    negative_terms: List[str] = field(default_factory=list)
    year_required: bool = True


@dataclass(frozen=True)
class CatalogIssue:
    issue_key: str
    type_zh: str
    type_en: str
    details: List[CatalogDetail]


@dataclass(frozen=True)
class CatalogDimension:
    topic_key: str
    topic_zh: str
    topic_en: str
    issues: List[CatalogIssue]


def _u(*xs: str) -> List[str]:
    return [x for x in xs if x and str(x).strip()]


# -------------------------
# Fixed catalog (user-provided scope)
# -------------------------


CATALOG: List[CatalogDimension] = [
    # Environment (E)
    CatalogDimension(
        topic_key="environment",
        topic_zh="环境（E）",
        topic_en="Environment",
        issues=[
            CatalogIssue(
                issue_key="ghg",
                type_zh="温室气体（GHG）排放",
                type_en="GHG Emissions",
                details=[
                    CatalogDetail(
                        detail="Scope 1绝对量",
                        aliases_en=_u("Scope 1 emissions", "Direct GHG emissions", "Scope 1 (tCO2e)", "Scope 1 (tCO₂e)"),
                        aliases_zh=_u("范围一", "范围1", "直接排放", "Scope 1"),
                        value_kind="absolute",
                        units_allow=_u("tCO2e", "tCO₂e", "kgCO2e", "ktCO2e", "CO2e"),
                        must_terms=_u("scope 1", "范围一"),
                    ),
                    CatalogDetail(
                        detail="Scope 2绝对量",
                        aliases_en=_u("Scope 2 emissions", "Indirect GHG emissions", "Purchased electricity", "Scope 2 (tCO2e)"),
                        aliases_zh=_u("范围二", "范围2", "购入电力", "Scope 2"),
                        value_kind="absolute",
                        units_allow=_u("tCO2e", "tCO₂e", "kgCO2e", "ktCO2e", "CO2e"),
                        must_terms=_u("scope 2", "范围二"),
                    ),
                    CatalogDetail(
                        detail="Scope 3绝对量",
                        aliases_en=_u("Scope 3 emissions", "Value chain emissions", "Other indirect emissions"),
                        aliases_zh=_u("范围三", "范围3", "价值链排放", "Scope 3"),
                        value_kind="absolute",
                        units_allow=_u("tCO2e", "tCO₂e", "kgCO2e", "ktCO2e", "CO2e"),
                        must_terms=_u("scope 3", "范围三"),
                    ),
                    CatalogDetail(
                        detail="Scope 1强度",
                        aliases_en=_u("Scope 1 intensity", "Scope 1 emissions intensity", "tCO2e per revenue", "tCO2e per unit"),
                        aliases_zh=_u("范围一强度", "Scope 1 强度", "排放强度", "碳强度"),
                        value_kind="intensity",
                        units_allow=_u("tCO2e/revenue", "tCO2e/收入", "tCO2e/营收", "tCO2e/产量", "kgCO2e/unit"),
                        must_terms=_u("scope 1", "范围一", "intensity", "强度"),
                    ),
                    CatalogDetail(
                        detail="Scope 2强度",
                        aliases_en=_u("Scope 2 intensity", "Scope 2 emissions intensity"),
                        aliases_zh=_u("范围二强度", "Scope 2 强度", "排放强度", "碳强度"),
                        value_kind="intensity",
                        units_allow=_u("tCO2e/revenue", "tCO2e/收入", "tCO2e/营收", "tCO2e/产量", "kgCO2e/unit"),
                        must_terms=_u("scope 2", "范围二", "intensity", "强度"),
                    ),
                    CatalogDetail(
                        detail="Scope 3强度",
                        aliases_en=_u("Scope 3 intensity", "Scope 3 emissions intensity"),
                        aliases_zh=_u("范围三强度", "Scope 3 强度", "排放强度", "碳强度"),
                        value_kind="intensity",
                        units_allow=_u("tCO2e/revenue", "tCO2e/收入", "tCO2e/营收", "tCO2e/产量", "kgCO2e/unit"),
                        must_terms=_u("scope 3", "范围三", "intensity", "强度"),
                    ),

                    # Explicitly disclosed total across Scope 1/2/3 (do NOT compute).
                    CatalogDetail(
                        detail="Scope 1、2、3绝对量总量",
                        aliases_en=_u(
                            "total scope 1 2 3 emissions",
                            "total emissions (scope 1, scope 2, scope 3)",
                            "total GHG emissions (scope 1+2+3)",
                            "total greenhouse gas emissions",
                        ),
                        aliases_zh=_u("范围一二三总量", "范围1、2、3总量", "温室气体排放总量", "排放总量"),
                        value_kind="absolute",
                        units_allow=_u("tCO2e", "tCO₂e", "kgCO2e", "ktCO2e", "CO2e"),
                        must_terms=_u("total", "总量"),
                        negative_terms=_u("scope 1 intensity", "scope 2 intensity", "scope 3 intensity", "强度"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="energy",
                type_zh="能源",
                type_en="Energy",
                details=[
                    CatalogDetail(
                        detail="总能耗",
                        aliases_en=_u("total energy consumption", "energy consumption", "total energy used"),
                        aliases_zh=_u("总能耗", "能源消耗", "能源使用"),
                        value_kind="absolute",
                        units_allow=_u("GJ", "MWh", "kWh"),
                        must_terms=_u("energy", "能源", "consumption", "能耗"),
                        negative_terms=_u("Scope", "排放"),
                    ),
                    CatalogDetail(
                        detail="能耗强度",
                        aliases_en=_u("energy intensity", "GJ per revenue", "MWh per revenue", "energy per unit"),
                        aliases_zh=_u("能耗强度", "能源强度"),
                        value_kind="intensity",
                        units_allow=_u("GJ/revenue", "MWh/revenue", "kWh/revenue", "GJ/产量", "MWh/产量"),
                        must_terms=_u("intensity", "强度", "energy"),
                    ),
                    CatalogDetail(
                        detail="可再生能源占比",
                        aliases_en=_u("renewable energy share", "renewable electricity percentage", "renewable energy (%)"),
                        aliases_zh=_u("可再生能源占比", "可再生电力占比", "可再生能源比例"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("renewable", "可再生"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="water",
                type_zh="水",
                type_en="Water",
                details=[
                    CatalogDetail(
                        detail="取水",
                        aliases_en=_u("water withdrawal", "total water withdrawal"),
                        aliases_zh=_u("取水", "取用水", "取水量"),
                        value_kind="absolute",
                        units_allow=_u("m3", "m³"),
                        must_terms=_u("withdrawal", "取水"),
                    ),
                    CatalogDetail(
                        detail="水强度",
                        aliases_en=_u("water intensity", "m3 per revenue", "water withdrawal per revenue"),
                        aliases_zh=_u("水强度", "取水强度", "耗水强度"),
                        value_kind="intensity",
                        units_allow=_u("m3/revenue", "m³/revenue", "m3/收入", "m3/营收", "m3/产量"),
                        must_terms=_u("intensity", "强度", "water"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="waste",
                type_zh="废弃物",
                type_en="Waste",
                details=[
                    CatalogDetail(
                        detail="总废弃物",
                        aliases_en=_u("total waste", "waste generated"),
                        aliases_zh=_u("总废弃物", "废弃物产生量"),
                        value_kind="absolute",
                        units_allow=_u("t", "tons", "tonnes", "kg"),
                        must_terms=_u("waste", "废弃物"),
                    ),
                    CatalogDetail(
                        detail="回收/资源化率",
                        aliases_en=_u("recycling rate", "recovery rate", "diversion rate"),
                        aliases_zh=_u("回收率", "资源化率"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("recycl", "回收", "资源化"),
                    ),
                    CatalogDetail(
                        detail="危废处置结构",
                        aliases_en=_u("hazardous waste disposal", "hazardous waste treatment breakdown"),
                        aliases_zh=_u("危废", "危险废物", "处置结构", "处置方式"),
                        value_kind="text",
                        units_allow=_u("%"),
                        must_terms=_u("hazard", "危废", "危险废物"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="env_incidents",
                type_zh="重大环境事件",
                type_en="Major Environmental Incidents",
                details=[
                    CatalogDetail(
                        detail="事件数",
                        aliases_en=_u("major environmental incidents", "environmental incidents", "spills"),
                        aliases_zh=_u("重大环境事件", "泄漏", "事故"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "events", "次", "起"),
                        must_terms=_u("incident", "事件", "泄漏"),
                    ),
                    CatalogDetail(
                        detail="罚款金额",
                        aliases_en=_u("environmental fines", "penalties", "fines"),
                        aliases_zh=_u("罚款", "处罚金额"),
                        value_kind="money",
                        units_allow=_u("CNY", "RMB", "USD", "HKD", "SGD", "$", "元", "万元", "亿元"),
                        must_terms=_u("fine", "penalt", "罚款", "处罚"),
                    ),
                    CatalogDetail(
                        detail="影响",
                        aliases_en=_u("impact", "environmental impact"),
                        aliases_zh=_u("影响", "影响范围", "后果"),
                        value_kind="text",
                        year_required=False,
                    ),
                ],
            ),
        ],
    ),

    # Social Capital (S-External)
    CatalogDimension(
        topic_key="social_capital",
        topic_zh="社会资本（S-External）",
        topic_en="Social Capital",
        issues=[
            CatalogIssue(
                issue_key="data_security",
                type_zh="数据安全",
                type_en="Data Security",
                details=[
                    CatalogDetail(
                        detail="重大事件数",
                        aliases_en=_u("data breach incidents", "security incidents", "major security incident"),
                        aliases_zh=_u("数据泄露", "安全事件", "重大安全事件"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "events", "次", "起"),
                        must_terms=_u("breach", "incident", "泄露", "事件"),
                    ),
                    CatalogDetail(
                        detail="受影响记录数",
                        aliases_en=_u("records affected", "users affected", "impacted records"),
                        aliases_zh=_u("受影响记录数", "受影响用户数"),
                        value_kind="count",
                        units_allow=_u("records", "users", "人", "条"),
                        must_terms=_u("affected", "impacted", "受影响"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="product_quality",
                type_zh="产品质量与安全",
                type_en="Product Quality & Safety",
                details=[
                    CatalogDetail(
                        detail="召回次数",
                        aliases_en=_u("product recalls", "recall cases"),
                        aliases_zh=_u("召回", "召回次数"),
                        value_kind="count",
                        units_allow=_u("recalls", "cases", "次", "起"),
                        must_terms=_u("recall", "召回"),
                    ),
                    CatalogDetail(
                        detail="重大质量事件数",
                        aliases_en=_u("major quality incidents", "major quality events"),
                        aliases_zh=_u("重大质量事件", "重大质量事故"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "events", "次", "起"),
                        must_terms=_u("quality", "incident", "质量", "事件"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="customer_safety",
                type_zh="客户安全",
                type_en="Customer Safety",
                details=[
                    CatalogDetail(
                        detail="重大事故/伤害事件数",
                        aliases_en=_u("customer injury incidents", "serious customer incidents", "safety incidents"),
                        aliases_zh=_u("伤害", "客户事故", "重大事故"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "events", "次", "起"),
                        must_terms=_u("injury", "incident", "伤害", "事故"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="community",
                type_zh="社区",
                type_en="Community",
                details=[
                    CatalogDetail(
                        detail="申诉量",
                        aliases_en=_u("grievances", "complaints", "community grievances"),
                        aliases_zh=_u("申诉", "申诉量", "投诉"),
                        value_kind="count",
                        units_allow=_u("cases", "complaints", "次", "起"),
                        must_terms=_u("grievance", "complaint", "申诉", "投诉"),
                    ),
                    CatalogDetail(
                        detail="解决率",
                        aliases_en=_u("resolution rate", "grievance resolution rate"),
                        aliases_zh=_u("解决率", "结案率"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("resolution", "解决"),
                    ),
                    CatalogDetail(
                        detail="平均处理周期",
                        aliases_en=_u("average resolution time", "average handling time", "average days"),
                        aliases_zh=_u("平均处理周期", "平均处理天数"),
                        value_kind="text",
                        year_required=False,
                    ),
                    CatalogDetail(
                        detail="重大冲突/停工次数",
                        aliases_en=_u("major conflicts", "work stoppages", "shutdowns"),
                        aliases_zh=_u("重大冲突", "停工"),
                        value_kind="count",
                        units_allow=_u("cases", "times", "次", "起"),
                        must_terms=_u("stoppage", "shutdown", "停工", "冲突"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="sales_practices",
                type_zh="销售实践/标签合规",
                type_en="Sales Practices & Label Compliance",
                details=[
                    CatalogDetail(
                        detail="处罚次数",
                        aliases_en=_u("penalty cases", "regulatory actions", "violations"),
                        aliases_zh=_u("处罚次数", "处罚", "违规"),
                        value_kind="count",
                        units_allow=_u("cases", "actions", "次", "起"),
                        must_terms=_u("penalt", "violation", "处罚", "违规"),
                    ),
                    CatalogDetail(
                        detail="处罚金额",
                        aliases_en=_u("penalty amount", "fines", "penalties"),
                        aliases_zh=_u("处罚金额", "罚款金额"),
                        value_kind="money",
                        units_allow=_u("CNY", "RMB", "USD", "HKD", "SGD", "$", "元", "万元", "亿元"),
                        must_terms=_u("fine", "penalt", "罚款", "处罚"),
                    ),
                ],
            ),
        ],
    ),

    # Human Capital (S-Internal)
    CatalogDimension(
        topic_key="human_capital",
        topic_zh="人力资本（S-Internal）",
        topic_en="Human Capital",
        issues=[
            CatalogIssue(
                issue_key="safety",
                type_zh="安全",
                type_en="Safety",
                details=[
                    CatalogDetail(
                        detail="TRIR",
                        aliases_en=_u("TRIR", "Total Recordable Incident Rate", "Total recordable incident rate"),
                        aliases_zh=_u("TRIR", "总可记录事故率"),
                        value_kind="ratio",
                        units_allow=_u("rate", "%"),
                        must_terms=_u("TRIR", "incident rate", "事故率"),
                    ),
                    CatalogDetail(
                        detail="LTIFR",
                        aliases_en=_u("LTIFR", "Lost Time Injury Frequency Rate", "Lost time injury frequency rate"),
                        aliases_zh=_u("LTIFR", "损工事故频率"),
                        value_kind="ratio",
                        units_allow=_u("rate", "%"),
                        must_terms=_u("LTIFR", "injury frequency", "事故率", "频率"),
                    ),
                    CatalogDetail(
                        detail="死亡人数",
                        aliases_en=_u("fatalities", "number of fatalities", "fatality"),
                        aliases_zh=_u("死亡", "死亡人数", "致死"),
                        value_kind="count",
                        units_allow=_u("people", "person", "人", "cases"),
                        must_terms=_u("fatal", "死亡"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="occupational_health",
                type_zh="职业健康",
                type_en="Occupational Health",
                details=[
                    CatalogDetail(
                        detail="重大事件数",
                        aliases_en=_u("occupational disease", "major occupational health incidents", "occupational illness"),
                        aliases_zh=_u("职业健康", "职业病", "重大事件"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "次", "起"),
                        must_terms=_u("occupational", "illness", "职业", "职业病"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="turnover",
                type_zh="流失率",
                type_en="Turnover",
                details=[
                    CatalogDetail(
                        detail="流失率（总体）",
                        aliases_en=_u("employee turnover rate", "turnover rate"),
                        aliases_zh=_u("流失率", "离职率"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("turnover", "流失", "离职"),
                    ),
                    CatalogDetail(
                        detail="流失率（关键岗位/自愿）",
                        aliases_en=_u("voluntary turnover", "key talent turnover", "critical roles turnover"),
                        aliases_zh=_u("自愿流失", "关键岗位流失"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("voluntary", "key", "自愿", "关键"),
                        year_required=False,
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="diversity",
                type_zh="多元",
                type_en="Diversity",
                details=[
                    CatalogDetail(
                        detail="管理层/关键岗位多元占比",
                        aliases_en=_u("leadership diversity", "management diversity", "diversity in critical roles"),
                        aliases_zh=_u("管理层多元", "关键岗位多元", "性别"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("diversity", "gender", "多元", "性别"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="training",
                type_zh="培训",
                type_en="Training",
                details=[
                    CatalogDetail(
                        detail="人均培训小时",
                        aliases_en=_u("training hours per employee", "average training hours"),
                        aliases_zh=_u("人均培训小时", "培训小时"),
                        value_kind="absolute",
                        units_allow=_u("hours", "h", "小时"),
                        must_terms=_u("training", "hours", "培训", "小时"),
                    ),
                    CatalogDetail(
                        detail="培训投入/人",
                        aliases_en=_u("training investment per employee", "training spend per employee"),
                        aliases_zh=_u("培训投入", "投入/人"),
                        value_kind="money",
                        units_allow=_u("CNY", "RMB", "USD", "HKD", "SGD", "$", "元", "万元"),
                        must_terms=_u("training", "investment", "投入", "培训"),
                        year_required=False,
                    ),
                ],
            ),
        ],
    ),

    # Governance (G) - mapped to leadership_governance to match existing dimension naming
    CatalogDimension(
        topic_key="leadership_governance",
        topic_zh="治理（G）",
        topic_en="Governance",
        issues=[
            CatalogIssue(
                issue_key="anti_corruption",
                type_zh="反腐",
                type_en="Anti-corruption",
                details=[
                    CatalogDetail(
                        detail="事件数",
                        aliases_en=_u("corruption incidents", "bribery cases", "anti-corruption cases"),
                        aliases_zh=_u("反腐", "腐败", "贿赂", "事件数"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "events", "次", "起"),
                        must_terms=_u("corruption", "bribery", "腐败", "贿赂"),
                    ),
                    CatalogDetail(
                        detail="罚款金额",
                        aliases_en=_u("fines", "penalties", "settlements"),
                        aliases_zh=_u("罚款", "处罚金额"),
                        value_kind="money",
                        units_allow=_u("CNY", "RMB", "USD", "HKD", "SGD", "$", "元", "万元", "亿元"),
                        must_terms=_u("fine", "penalt", "罚款", "处罚"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="whistleblowing",
                type_zh="举报",
                type_en="Whistleblowing",
                details=[
                    CatalogDetail(
                        detail="举报数量",
                        aliases_en=_u("whistleblowing reports", "hotline reports", "reports received"),
                        aliases_zh=_u("举报", "举报数量", "热线"),
                        value_kind="count",
                        units_allow=_u("cases", "reports", "次", "起"),
                        must_terms=_u("whistle", "hotline", "举报", "热线"),
                    ),
                    CatalogDetail(
                        detail="结案率/周期",
                        aliases_en=_u("case closure rate", "case closure time", "case resolution time"),
                        aliases_zh=_u("结案率", "结案周期", "处理周期"),
                        value_kind="text",
                        year_required=False,
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="compliance_training",
                type_zh="合规培训覆盖率",
                type_en="Compliance Training Coverage",
                details=[
                    CatalogDetail(
                        detail="覆盖率",
                        aliases_en=_u("compliance training coverage", "training completion rate", "coverage rate"),
                        aliases_zh=_u("合规培训覆盖率", "覆盖率", "完成率"),
                        value_kind="ratio",
                        units_allow=_u("%"),
                        must_terms=_u("compliance", "training", "覆盖", "完成"),
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="major_accident_risk",
                type_zh="重大事故风险",
                type_en="Major Accident Risk",
                details=[
                    CatalogDetail(
                        detail="重大事故数",
                        aliases_en=_u("major incidents", "major accidents"),
                        aliases_zh=_u("重大事故", "重大事件"),
                        value_kind="count",
                        units_allow=_u("cases", "incidents", "events", "次", "起"),
                        must_terms=_u("major", "incident", "事故"),
                    ),
                    CatalogDetail(
                        detail="停工时长",
                        aliases_en=_u("shutdown duration", "work stoppage duration", "downtime hours"),
                        aliases_zh=_u("停工时长", "停工时间", "停产时间"),
                        value_kind="text",
                        year_required=False,
                    ),
                ],
            ),
            CatalogIssue(
                issue_key="major_cases",
                type_zh="重大案件",
                type_en="Major Cases",
                details=[
                    CatalogDetail(
                        detail="案件数",
                        aliases_en=_u("major cases", "lawsuits", "litigation"),
                        aliases_zh=_u("重大案件", "诉讼", "案件数"),
                        value_kind="count",
                        units_allow=_u("cases", "lawsuits", "次", "起"),
                        must_terms=_u("case", "lawsuit", "litigation", "案件", "诉讼"),
                    ),
                    CatalogDetail(
                        detail="处罚金额",
                        aliases_en=_u("penalties", "fines", "settlement amount"),
                        aliases_zh=_u("处罚金额", "罚款金额", "和解金额"),
                        value_kind="money",
                        units_allow=_u("CNY", "RMB", "USD", "HKD", "SGD", "$", "元", "万元", "亿元"),
                        must_terms=_u("fine", "penalt", "settlement", "罚款", "处罚", "和解"),
                    ),
                ],
            ),
        ],
    ),

    # Business Model & Innovation (placeholder; extraction disabled by default in records endpoint)
    CatalogDimension(
        topic_key="business_model_innovation",
        topic_zh="商业模式与创新",
        topic_en="Business Model & Innovation",
        issues=[],
    ),
]


def dimension_by_key(topic_key: str) -> Optional[CatalogDimension]:
    for d in CATALOG:
        if d.topic_key == topic_key:
            return d
    return None


def issue_by_key(topic_key: str, issue_key: str) -> Optional[CatalogIssue]:
    d = dimension_by_key(topic_key)
    if not d:
        return None
    for it in d.issues:
        if it.issue_key == issue_key:
            return it
    return None


def all_issue_keys_by_dimension() -> Dict[str, List[str]]:
    return {d.topic_key: [i.issue_key for i in d.issues] for d in CATALOG}
