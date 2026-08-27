"""Cross Analysis taxonomy.

This taxonomy is the **built-in navigation** for Cross Analysis.

Design goals:
- Stable keys (dimension/topic) so URLs and stored state remain valid
- Minimal keyword lists used for cheap metric/topic matching (MVP)
"""

from __future__ import annotations

from typing import Dict, List


def _kw(*items: str) -> List[str]:
    return [s.strip() for s in items if s and s.strip()]


TAXONOMY: Dict[str, Dict] = {
    "Environment": {
        "intro": "Cost, compliance, supply chain exposure, and operational resilience.",
        "topics": {
            "GHG Emissions": {
                "label_zh": "温室气体（GHG）排放",
                "metrics": [
                    "Total GHG",
                    "GHG intensity",
                    "Scope 1",
                    "Scope 2",
                    "Scope 3",
                ],
                "keywords": _kw(
                    "ghg",
                    "greenhouse gas",
                    "carbon",
                    "co2",
                    "co2e",
                    "scope 1",
                    "scope 2",
                    "scope 3",
                    "emission",
                    "emissions",
                    "intensity",
                    "排放",
                    "温室气体",
                    "碳",
                    "二氧化碳",
                    "范围1",
                    "范围2",
                    "范围3",
                ),
            },
            "Energy Management": {
                "label_zh": "能源管理",
                "metrics": ["Energy consumption", "Renewable energy share", "Energy efficiency"],
                "keywords": _kw(
                    "energy",
                    "electricity",
                    "fuel",
                    "kwh",
                    "mwh",
                    "renewable",
                    "efficiency",
                    "intensity",
                    "能耗",
                    "能源",
                    "可再生",
                    "能效",
                ),
            },
            "Water & Wastewater": {
                "label_zh": "水与废水管理",
                "metrics": ["Water withdrawal", "Water consumption", "Wastewater discharge", "Water stress"],
                "keywords": _kw(
                    "water",
                    "withdrawal",
                    "consumption",
                    "wastewater",
                    "effluent",
                    "water stress",
                    "scarcity",
                    "取水",
                    "耗水",
                    "废水",
                    "排放",
                    "缺水",
                ),
            },
            "Waste & Hazardous": {
                "label_zh": "废弃物与危废",
                "metrics": ["Waste generated", "Recycling rate", "Hazardous waste", "Disposal method"],
                "keywords": _kw(
                    "waste",
                    "hazardous",
                    "recycle",
                    "recycling",
                    "landfill",
                    "incineration",
                    "disposal",
                    "废弃物",
                    "危废",
                    "回收",
                    "处置",
                ),
            },
            "Biodiversity": {
                "label_zh": "生物多样性影响",
                "metrics": ["Sensitive areas", "Restoration", "Violations/fines"],
                "keywords": _kw(
                    "biodiversity",
                    "habitat",
                    "protected area",
                    "restoration",
                    "remediation",
                    "fine",
                    "violation",
                    "生物多样性",
                    "敏感区",
                    "修复",
                    "罚款",
                    "违规",
                ),
            },
        },
    },
    "Social Capital": {
        "intro": "Brand, customers, and license to operate.",
        "topics": {
            "Data Security & Privacy": {
                "label_zh": "数据安全与客户隐私",
                "metrics": ["Incidents", "Records impacted", "Remediation spend"],
                "keywords": _kw(
                    "privacy",
                    "data security",
                    "breach",
                    "incident",
                    "cyber",
                    "gdpr",
                    "整改",
                    "泄露",
                    "隐私",
                    "数据安全",
                ),
            },
            "Customer Welfare & Product Responsibility": {
                "label_zh": "客户福祉/产品责任",
                "metrics": ["Complaints", "Recalls", "Major quality events"],
                "keywords": _kw(
                    "customer",
                    "welfare",
                    "product responsibility",
                    "complaint",
                    "recall",
                    "quality",
                    "safety",
                    "投诉",
                    "召回",
                    "质量",
                    "安全",
                ),
            },
            "Human Rights & Community": {
                "label_zh": "人权与社区关系",
                "metrics": ["Community conflicts", "Work stoppage", "Grievances resolution rate"],
                "keywords": _kw(
                    "human rights",
                    "community",
                    "grievance",
                    "complaints",
                    "stoppage",
                    "protest",
                    "人权",
                    "社区",
                    "冲突",
                    "停工",
                    "申诉",
                ),
            },
            "Fair Disclosure & Marketing": {
                "label_zh": "公平披露与标签/营销",
                "metrics": ["Misleading claims", "Regulatory penalties"],
                "keywords": _kw(
                    "marketing",
                    "label",
                    "misleading",
                    "greenwashing",
                    "penalty",
                    "regulator",
                    "误导",
                    "处罚",
                    "监管",
                    "宣传",
                ),
            },
        },
    },
    "Human Capital": {
        "intro": "Workforce efficiency, labor risks, and operational safety.",
        "topics": {
            "Health & Safety": {
                "label_zh": "员工健康与安全",
                "metrics": ["Incident rate", "Fatalities", "Occupational disease"],
                "keywords": _kw(
                    "safety",
                    "incident rate",
                    "ltifr",
                    "trir",
                    "fatal",
                    "fatalities",
                    "injury",
                    "occupational",
                    "健康",
                    "安全",
                    "事故率",
                    "死亡",
                    "职业病",
                ),
            },
            "Labor Relations": {
                "label_zh": "劳工关系与公平用工",
                "metrics": ["Disputes", "Compliance", "Supplier labor risk"],
                "keywords": _kw(
                    "labor",
                    "labour",
                    "union",
                    "dispute",
                    "strike",
                    "wage",
                    "supplier labor",
                    "劳工",
                    "争议",
                    "合规",
                    "供应商",
                ),
            },
            "Diversity & Inclusion": {
                "label_zh": "多元与包容",
                "metrics": ["Representation", "Leadership diversity"],
                "keywords": _kw(
                    "diversity",
                    "inclusion",
                    "dei",
                    "gender",
                    "representation",
                    "minority",
                    "多元",
                    "包容",
                    "性别",
                    "占比",
                ),
            },
            "Talent & Retention": {
                "label_zh": "招聘/发展/留任",
                "metrics": ["Turnover", "Key talent attrition", "Training investment"],
                "keywords": _kw(
                    "turnover",
                    "retention",
                    "attrition",
                    "training",
                    "development",
                    "hiring",
                    "流失",
                    "留任",
                    "培训",
                    "招聘",
                ),
            },
        },
    },
    "Business Model & Innovation": {
        "intro": "Product competitiveness and sustainability-driven moats.",
        "topics": {
            "Product Lifecycle": {
                "label_zh": "产品全生命周期影响",
                "metrics": ["Materials", "Use-phase energy", "Recyclability"],
                "keywords": _kw(
                    "lifecycle",
                    "life cycle",
                    "materials",
                    "use phase",
                    "recyclable",
                    "circular",
                    "全生命周期",
                    "材料",
                    "可回收",
                    "使用阶段",
                ),
            },
            "Asset & Operational Impact": {
                "label_zh": "资产与运营的影响",
                "metrics": ["Impairment risk", "Operational disruption"],
                "keywords": _kw(
                    "impairment",
                    "asset risk",
                    "disruption",
                    "interruption",
                    "resilience",
                    "资产减值",
                    "运营中断",
                    "韧性",
                ),
            },
            "Packaging & Product Quality": {
                "label_zh": "包装与产品质量安全",
                "metrics": ["Incidents", "Compliance", "Improvements"],
                "keywords": _kw(
                    "packaging",
                    "product quality",
                    "product safety",
                    "compliance",
                    "incident",
                    "improvement",
                    "包装",
                    "质量",
                    "安全",
                    "改进",
                    "合规",
                ),
            },
        },
    },
    "Leadership & Governance": {
        "intro": "Systemic risk control and integrity signals.",
        "topics": {
            "Business Ethics": {
                "label_zh": "商业道德与透明度",
                "metrics": ["Corruption incidents", "Penalties", "Remediation"],
                "keywords": _kw(
                    "ethics",
                    "corruption",
                    "bribery",
                    "fraud",
                    "transparency",
                    "penalty",
                    "商业道德",
                    "腐败",
                    "贿赂",
                    "透明",
                    "处罚",
                ),
            },
            "Incident & Safety Management": {
                "label_zh": "事故与安全管理",
                "metrics": ["Major incidents", "Contractor safety"],
                "keywords": _kw(
                    "major incident",
                    "accident",
                    "contractor",
                    "safety management",
                    "重大事故",
                    "承包商",
                    "安全管理",
                ),
            },
            "Supply Chain Management": {
                "label_zh": "供应链管理",
                "metrics": ["Audit coverage", "Critical findings", "Corrective actions"],
                "keywords": _kw(
                    "supply chain",
                    "supplier",
                    "audit",
                    "finding",
                    "corrective action",
                    "procurement",
                    "供应链",
                    "供应商",
                    "审计",
                    "缺陷",
                    "整改",
                    "采购",
                ),
            },
            "Regulatory & Political Influence": {
                "label_zh": "监管与政治影响",
                "metrics": ["Antitrust", "Lobbying compliance"],
                "keywords": _kw(
                    "antitrust",
                    "competition",
                    "lobby",
                    "lobbying",
                    "regulatory",
                    "political",
                    "反垄断",
                    "游说",
                    "监管",
                    "政治",
                    "竞争",
                ),
            },
        },
    },
}


def topic_keywords(dimension: str, topic: str) -> List[str]:
    """Return keyword list for a given taxonomy node (best-effort)."""
    dim = TAXONOMY.get(dimension) or {}
    topics = dim.get("topics") or {}
    node = topics.get(topic) or {}
    kws = node.get("keywords") or []
    return [str(x).lower() for x in kws]
