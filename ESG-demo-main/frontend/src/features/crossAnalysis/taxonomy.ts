// src/features/crossAnalysis/taxonomy.ts
// Cross Analysis (beta) taxonomy: drives navigation + semantic query packs.
// 一级/二级/三级导航必须完整可见（UI 不做省略），并以此结构进行语义检索与指标抽取。

export type DimensionKey =
  | "environment"
  | "social_capital"
  | "human_capital"
  | "business_model_innovation"
  | "leadership_governance";

export type IssueKey =
  | "ghg"
  | "energy"
  | "water"
  | "waste"
  | "biodiversity"
  | "data_privacy"
  | "product_responsibility"
  | "human_rights_community"
  | "fair_marketing"
  | "health_safety"
  | "labor_practices"
  | "diversity_inclusion"
  | "recruitment_retention"
  | "lifecycle_impact"
  | "asset_operational_impact"
  | "packaging_quality_safety"
  | "business_ethics"
  | "incident_safety_mgmt"
  | "supply_chain_mgmt"
  | "regulatory_political";

// 三级指标 key：尽量稳定、可读、可扩展
export type MetricKey =
  // Environment / GHG
  | "ghg_absolute_total"
  | "ghg_intensity_revenue"
  | "ghg_intensity_production"
  // Environment / Energy
  | "energy_consumption"
  | "renewable_energy_share"
  | "energy_efficiency"
  // Environment / Water
  | "water_withdrawal"
  | "water_consumption"
  | "wastewater_discharge"
  | "water_stress_exposure"
  // Environment / Waste
  | "waste_generated"
  | "waste_treatment_breakdown"
  | "recycling_rate"
  | "hazardous_waste_compliance"
  // Environment / Biodiversity
  | "sensitive_site_operations"
  | "biodiversity_restoration"
  | "biodiversity_violations_fines"

  // Social Capital / Data privacy
  | "data_breach_count"
  | "records_impacted"
  | "privacy_remediation_investment"
  // Social Capital / Product responsibility
  | "customer_complaints"
  | "product_recalls"
  | "major_quality_incidents"
  // Social Capital / Human rights & community
  | "community_conflicts"
  | "community_shutdowns"
  | "grievances_resolution_rate"
  // Social Capital / Fair marketing
  | "misleading_marketing_events"
  | "regulatory_penalties_marketing"

  // Human capital / Health & safety
  | "incident_rate"
  | "fatalities_major_incidents"
  | "occupational_disease"
  // Human capital / Labor practices
  | "labor_disputes"
  | "labor_compliance"
  | "supplier_labor_risk"
  // Human capital / Diversity
  | "workforce_representation"
  | "leadership_diversity"
  // Human capital / Recruitment
  | "turnover_rate"
  | "key_talent_attrition"
  | "training_investment"

  // Business model & innovation / Lifecycle
  | "materials_footprint"
  | "use_phase_energy"
  | "recyclability"
  // Business model & innovation / Asset & operation
  | "asset_impairment_risk"
  | "operational_disruption_risk"
  // Business model & innovation / Packaging & quality
  | "packaging_quality_incidents"
  | "packaging_compliance"
  | "improvement_outcomes"

  // Leadership & governance / Ethics
  | "corruption_bribery_events"
  | "ethics_penalties"
  | "ethics_remediation"
  // Leadership & governance / Incident safety mgmt
  | "major_incidents"
  | "contractor_safety"
  // Leadership & governance / Supply chain
  | "supplier_audit_coverage"
  | "critical_findings"
  | "supplier_remediation"
  // Leadership & governance / Regulatory & political
  | "antitrust"
  | "lobbying_compliance";

export type TaxonomyMetric = {
  key: MetricKey;
  labelZh: string;
  labelEn: string;
  // Semantic hints for retrieval & extraction
  queryPack: string[];
};

export type TaxonomyIssue = {
  key: IssueKey;
  labelZh: string;
  labelEn: string;
  metrics: TaxonomyMetric[];
  // Issue-level semantic pack
  queryPack: string[];
};

export type TaxonomyDimension = {
  key: DimensionKey;
  labelZh: string;
  labelEn: string;
  subtitleZh: string;
  issues: TaxonomyIssue[];
};

const common = {
  years: ["FY", "2020", "2021", "2022", "2023", "2024", "2025"],
  intensity: [
    "强度",
    "intensity",
    "per revenue",
    "per sales",
    "per unit",
    "per production",
    "归一化",
    "normalized",
  ],
  units: ["tCO2e", "kgCO2e", "CO2e", "MWh", "kWh", "GJ", "m3", "m³", "%", "tons", "tonnes"],
  scope: [
    "Scope 1",
    "Scope 2",
    "Scope 3",
    "范围一",
    "范围二",
    "范围三",
    "market-based",
    "location-based",
  ],
};

export const CROSS_TAXONOMY: TaxonomyDimension[] = [
  {
    key: "environment",
    labelZh: "环境（Environment）",
    labelEn: "Environment",
    subtitleZh: "成本、合规、供应链与运营韧性",
    issues: [
      {
        key: "ghg",
        labelZh: "温室气体（GHG）排放",
        labelEn: "Greenhouse Gas (GHG) Emissions",
        queryPack: [
          "温室气体",
          "GHG",
          "greenhouse gas",
          "碳排放",
          "排放",
          "emissions",
          ...common.scope,
          ...common.units,
          ...common.intensity,
        ],
        metrics: [
          {
            key: "ghg_absolute_total",
            labelZh: "总量",
            labelEn: "Absolute (Total Emissions)",
            queryPack: [
              "总量",
              "总排放",
              "absolute",
              "total emissions",
              ...common.scope,
              "Scope 1+2",
              "Scope 1 and 2",
              "Scope 1&2",
              "tCO2e",
              "CO2e",
            ],
          },
          {
            key: "ghg_intensity_revenue",
            labelZh: "强度（按收入归一化）",
            labelEn: "Intensity (Normalized by Revenue)",
            queryPack: [
              "排放强度",
              "碳强度",
              ...common.intensity,
              "按收入",
              "per revenue",
              "per sales",
              "per $",
              ...common.scope,
            ],
          },
          {
            key: "ghg_intensity_production",
            labelZh: "强度（按产量/产出归一化）",
            labelEn: "Intensity (Normalized by Production/Output)",
            queryPack: [
              "排放强度",
              "碳强度",
              ...common.intensity,
              "按产量",
              "按产出",
              "per unit",
              "per production",
              "per product",
              ...common.scope,
            ],
          },
        ],
      },
      {
        key: "energy",
        labelZh: "能源管理",
        labelEn: "Energy Management",
        queryPack: ["能源", "energy", "electricity", "power", "fuel", "可再生", "renewable", "能效", "efficiency", ...common.units],
        metrics: [
          {
            key: "energy_consumption",
            labelZh: "能耗",
            labelEn: "Energy Consumption",
            queryPack: [
              "能耗",
              "energy consumption",
              "electricity consumption",
              "total energy",
              "kWh",
              "MWh",
              "GJ",
              ...common.years,
            ],
          },
          {
            key: "renewable_energy_share",
            labelZh: "可再生能源占比",
            labelEn: "Renewable Energy Share",
            queryPack: [
              "可再生能源",
              "renewable energy",
              "renewables",
              "RECs",
              "占比",
              "share",
              "percentage",
              "%",
            ],
          },
          {
            key: "energy_efficiency",
            labelZh: "能效",
            labelEn: "Energy Efficiency",
            queryPack: [
              "能效",
              "energy efficiency",
              "节能",
              "saving",
              "reduction",
              ...common.intensity,
            ],
          },
        ],
      },
      {
        key: "water",
        labelZh: "水与废水管理",
        labelEn: "Water & Wastewater",
        queryPack: ["取水", "耗水", "water", "withdrawal", "consumption", "wastewater", "discharge", "缺水", "water stress", ...common.units],
        metrics: [
          {
            key: "water_withdrawal",
            labelZh: "取水",
            labelEn: "Water Withdrawal",
            queryPack: ["取水", "withdrawal", "abstraction", "m3", "m³", "ML"],
          },
          {
            key: "water_consumption",
            labelZh: "耗水",
            labelEn: "Water Consumption",
            queryPack: ["耗水", "consumption", "water consumed", "m3", "m³", "ML"],
          },
          {
            key: "wastewater_discharge",
            labelZh: "排放（废水/排污）",
            labelEn: "Wastewater Discharge",
            queryPack: ["排放", "discharge", "effluent", "wastewater", "污水", "m3", "m³"],
          },
          {
            key: "water_stress_exposure",
            labelZh: "缺水地区暴露",
            labelEn: "Water-stress Exposure",
            queryPack: [
              "缺水",
              "water-stressed",
              "water stress",
              "high baseline water stress",
              "risk",
              "exposure",
              "%",
            ],
          },
        ],
      },
      {
        key: "waste",
        labelZh: "废弃物与危废",
        labelEn: "Waste & Hazardous Waste",
        queryPack: ["废弃物", "waste", "hazardous", "危废", "recycling", "recovery", "landfill", "incineration", "%", "tons"],
        metrics: [
          {
            key: "waste_generated",
            labelZh: "产生量",
            labelEn: "Waste Generated",
            queryPack: ["产生量", "generated", "waste generation", "tons", "tonnes"],
          },
          {
            key: "waste_treatment_breakdown",
            labelZh: "处置方式",
            labelEn: "Treatment Breakdown",
            queryPack: ["处置", "treatment", "landfill", "incineration", "recovery", "disposal"],
          },
          {
            key: "recycling_rate",
            labelZh: "回收率",
            labelEn: "Recycling Rate",
            queryPack: ["回收率", "recycling rate", "reuse", "circular", "%"],
          },
          {
            key: "hazardous_waste_compliance",
            labelZh: "危废合规",
            labelEn: "Hazardous Waste Compliance",
            queryPack: ["危废", "hazardous", "compliance", "violation", "罚款", "fine"],
          },
        ],
      },
      {
        key: "biodiversity",
        labelZh: "生物多样性影响",
        labelEn: "Biodiversity Impact",
        queryPack: ["生物多样性", "biodiversity", "protected area", "sensitive", "restoration", "remediation", "fines", "violations"],
        metrics: [
          {
            key: "sensitive_site_operations",
            labelZh: "高敏感区作业",
            labelEn: "Operations in Sensitive Sites",
            queryPack: ["高敏感区", "sensitive area", "protected area", "critical habitat", "operations"],
          },
          {
            key: "biodiversity_restoration",
            labelZh: "修复",
            labelEn: "Restoration / Remediation",
            queryPack: ["修复", "restoration", "remediation", "rehabilitation"],
          },
          {
            key: "biodiversity_violations_fines",
            labelZh: "违规/罚款",
            labelEn: "Violations / Fines",
            queryPack: ["违规", "violation", "罚款", "fine", "penalty"],
          },
        ],
      },
    ],
  },
  {
    key: "social_capital",
    labelZh: "社会资本（Social Capital）",
    labelEn: "Social Capital",
    subtitleZh: "品牌、客户、许可经营",
    issues: [
      {
        key: "data_privacy",
        labelZh: "数据安全与客户隐私",
        labelEn: "Data Security & Customer Privacy",
        queryPack: ["数据安全", "privacy", "data breach", "泄露", "incident", "GDPR", "cybersecurity", "remediation"],
        metrics: [
          {
            key: "data_breach_count",
            labelZh: "泄露事件数",
            labelEn: "Breach Count",
            queryPack: ["泄露", "breach", "incident", "事件数", "count", "number of incidents"],
          },
          {
            key: "records_impacted",
            labelZh: "影响记录数",
            labelEn: "Impacted Records",
            queryPack: ["影响记录", "records impacted", "customers affected", "users affected"],
          },
          {
            key: "privacy_remediation_investment",
            labelZh: "整改投入",
            labelEn: "Remediation Investment",
            queryPack: ["整改", "remediation", "mitigation", "investment", "spend", "capex", "opex"],
          },
        ],
      },
      {
        key: "product_responsibility",
        labelZh: "客户福祉/产品责任",
        labelEn: "Customer Welfare / Product Responsibility",
        queryPack: ["product safety", "投诉", "complaints", "召回", "recall", "质量", "quality", "safety incident"],
        metrics: [
          {
            key: "customer_complaints",
            labelZh: "投诉",
            labelEn: "Customer Complaints",
            queryPack: ["投诉", "complaints", "customer complaint"],
          },
          {
            key: "product_recalls",
            labelZh: "召回",
            labelEn: "Product Recalls",
            queryPack: ["召回", "recall", "product recall"],
          },
          {
            key: "major_quality_incidents",
            labelZh: "重大质量事件",
            labelEn: "Major Quality Incidents",
            queryPack: ["重大", "major", "quality incident", "quality event", "product safety"],
          },
        ],
      },
      {
        key: "human_rights_community",
        labelZh: "人权与社区关系",
        labelEn: "Human Rights & Community Relations",
        queryPack: ["community", "community conflict", "protest", "shutdown", "grievance", "human rights", "due diligence"],
        metrics: [
          {
            key: "community_conflicts",
            labelZh: "社区冲突",
            labelEn: "Community Conflicts",
            queryPack: ["社区冲突", "community conflict", "protest", "blockade"],
          },
          {
            key: "community_shutdowns",
            labelZh: "停工",
            labelEn: "Shutdowns / Interruptions",
            queryPack: ["停工", "shutdown", "work stoppage", "operations halted"],
          },
          {
            key: "grievances_resolution_rate",
            labelZh: "申诉与解决率",
            labelEn: "Grievances & Resolution Rate",
            queryPack: ["申诉", "grievance", "解决率", "resolution rate", "%"],
          },
        ],
      },
      {
        key: "fair_marketing",
        labelZh: "公平披露与标签/营销",
        labelEn: "Fair Disclosure & Marketing/Labeling",
        queryPack: ["misleading", "greenwashing", "labeling", "marketing", "误导", "处罚", "regulatory"],
        metrics: [
          {
            key: "misleading_marketing_events",
            labelZh: "误导宣传",
            labelEn: "Misleading Claims",
            queryPack: ["误导", "misleading", "false claims", "greenwashing"],
          },
          {
            key: "regulatory_penalties_marketing",
            labelZh: "监管处罚",
            labelEn: "Regulatory Penalties",
            queryPack: ["处罚", "penalty", "fine", "regulator", "sanction"],
          },
        ],
      },
    ],
  },
  {
    key: "human_capital",
    labelZh: "人力资本（Human Capital）",
    labelEn: "Human Capital",
    subtitleZh: "效率、用工风险、生产安全",
    issues: [
      {
        key: "health_safety",
        labelZh: "员工健康与安全",
        labelEn: "Employee Health & Safety",
        queryPack: ["TRIR", "LTIFR", "事故率", "injury rate", "fatality", "死亡", "职业病", "occupational disease", "OSHA"],
        metrics: [
          {
            key: "incident_rate",
            labelZh: "事故率",
            labelEn: "Incident Rate",
            queryPack: ["事故率", "TRIR", "LTIFR", "injury rate", "recordable"],
          },
          {
            key: "fatalities_major_incidents",
            labelZh: "重大事故/死亡",
            labelEn: "Major Incidents / Fatalities",
            queryPack: ["重大事故", "major incident", "fatality", "死亡"],
          },
          {
            key: "occupational_disease",
            labelZh: "职业病",
            labelEn: "Occupational Disease",
            queryPack: ["职业病", "occupational disease", "illness"],
          },
        ],
      },
      {
        key: "labor_practices",
        labelZh: "劳工关系与公平用工",
        labelEn: "Labor Relations & Fair Employment",
        queryPack: ["labor dispute", "strike", "合规", "compliance", "supplier labor", "forced labor", "争议"],
        metrics: [
          {
            key: "labor_disputes",
            labelZh: "争议事件",
            labelEn: "Dispute Events",
            queryPack: ["争议", "dispute", "strike", "work stoppage"],
          },
          {
            key: "labor_compliance",
            labelZh: "合规",
            labelEn: "Compliance",
            queryPack: ["合规", "compliance", "violation", "audit finding"],
          },
          {
            key: "supplier_labor_risk",
            labelZh: "供应商用工风险",
            labelEn: "Supplier Labor Risk",
            queryPack: ["供应商", "supplier", "forced labor", "child labor", "labor risk"],
          },
        ],
      },
      {
        key: "diversity_inclusion",
        labelZh: "多元与包容",
        labelEn: "Diversity & Inclusion",
        queryPack: ["diversity", "inclusion", "gender", "women", "representation", "多元", "包容", "%"],
        metrics: [
          {
            key: "workforce_representation",
            labelZh: "结构占比",
            labelEn: "Workforce Representation",
            queryPack: ["结构占比", "representation", "gender", "women", "%"],
          },
          {
            key: "leadership_diversity",
            labelZh: "关键岗位多元性",
            labelEn: "Key-role Diversity",
            queryPack: ["关键岗位", "leadership", "management", "diversity", "%"],
          },
        ],
      },
      {
        key: "recruitment_retention",
        labelZh: "招聘/发展/留任",
        labelEn: "Hiring / Development / Retention",
        queryPack: ["turnover", "attrition", "流失率", "training", "培训", "development", "retention"],
        metrics: [
          {
            key: "turnover_rate",
            labelZh: "流失率",
            labelEn: "Turnover Rate",
            queryPack: ["流失率", "turnover", "attrition", "%"],
          },
          {
            key: "key_talent_attrition",
            labelZh: "关键人才流失",
            labelEn: "Key Talent Attrition",
            queryPack: ["关键人才", "key talent", "attrition", "critical roles"],
          },
          {
            key: "training_investment",
            labelZh: "培训投入",
            labelEn: "Training Investment",
            queryPack: ["培训", "training", "hours", "investment", "spend"],
          },
        ],
      },
    ],
  },
  {
    key: "business_model_innovation",
    labelZh: "商业模式与创新（Business Model & Innovation）",
    labelEn: "Business Model & Innovation",
    subtitleZh: "增长质量与产品竞争力",
    issues: [
      {
        key: "lifecycle_impact",
        labelZh: "产品全生命周期影响",
        labelEn: "Product Lifecycle Impact",
        queryPack: ["lifecycle", "LCA", "材料", "materials", "use phase", "使用阶段", "recyclable", "可回收"],
        metrics: [
          {
            key: "materials_footprint",
            labelZh: "材料",
            labelEn: "Materials",
            queryPack: ["材料", "materials", "raw material", "recycled content"],
          },
          {
            key: "use_phase_energy",
            labelZh: "使用阶段能耗",
            labelEn: "Use-phase Energy",
            queryPack: ["使用阶段", "use phase", "energy", "能耗", "kWh", "MWh"],
          },
          {
            key: "recyclability",
            labelZh: "可回收性",
            labelEn: "Recyclability",
            queryPack: ["可回收", "recyclable", "recyclability", "%"],
          },
        ],
      },
      {
        key: "asset_operational_impact",
        labelZh: "资产与运营的影响",
        labelEn: "Asset & Operational Impact",
        queryPack: ["impairment", "资产减值", "disruption", "运营中断", "resilience", "climate risk"],
        metrics: [
          {
            key: "asset_impairment_risk",
            labelZh: "资产减值风险",
            labelEn: "Asset Impairment Risk",
            queryPack: ["减值", "impairment", "write-down", "asset risk"],
          },
          {
            key: "operational_disruption_risk",
            labelZh: "运营中断风险",
            labelEn: "Operational Disruption Risk",
            queryPack: ["中断", "disruption", "shutdown", "business continuity", "resilience"],
          },
        ],
      },
      {
        key: "packaging_quality_safety",
        labelZh: "包装与产品质量安全",
        labelEn: "Packaging & Product Quality/Safety",
        queryPack: ["packaging", "包装", "quality", "质量", "safety", "合规", "compliance", "improvement"],
        metrics: [
          {
            key: "packaging_quality_incidents",
            labelZh: "质量事故",
            labelEn: "Quality Incidents",
            queryPack: ["质量事故", "incident", "product quality", "safety incident"],
          },
          {
            key: "packaging_compliance",
            labelZh: "合规",
            labelEn: "Compliance",
            queryPack: ["合规", "compliance", "violation", "regulatory"],
          },
          {
            key: "improvement_outcomes",
            labelZh: "改进结果",
            labelEn: "Improvement Outcomes",
            queryPack: ["改进", "improvement", "CAPA", "outcome", "result"],
          },
        ],
      },
    ],
  },
  {
    key: "leadership_governance",
    labelZh: "领导力与治理（Leadership & Governance）",
    labelEn: "Leadership & Governance",
    subtitleZh: "系统性风险管控",
    issues: [
      {
        key: "business_ethics",
        labelZh: "商业道德与透明度",
        labelEn: "Business Ethics & Transparency",
        queryPack: ["corruption", "bribery", "腐败", "贿赂", "ethics", "code of conduct", "penalty", "remediation"],
        metrics: [
          {
            key: "corruption_bribery_events",
            labelZh: "腐败/贿赂事件",
            labelEn: "Corruption/Bribery Events",
            queryPack: ["腐败", "corruption", "贿赂", "bribery", "case", "incident"],
          },
          {
            key: "ethics_penalties",
            labelZh: "处罚",
            labelEn: "Penalties",
            queryPack: ["处罚", "penalty", "fine", "sanction"],
          },
          {
            key: "ethics_remediation",
            labelZh: "整改",
            labelEn: "Remediation",
            queryPack: ["整改", "remediation", "corrective action", "training"],
          },
        ],
      },
      {
        key: "incident_safety_mgmt",
        labelZh: "事故与安全管理",
        labelEn: "Incident & Safety Management",
        queryPack: ["major incident", "重大事故", "contractor safety", "承包商安全", "process safety"],
        metrics: [
          {
            key: "major_incidents",
            labelZh: "重大事故",
            labelEn: "Major Incidents",
            queryPack: ["重大事故", "major incident", "process safety", "PSM"],
          },
          {
            key: "contractor_safety",
            labelZh: "承包商安全",
            labelEn: "Contractor Safety",
            queryPack: ["承包商", "contractor", "safety", "incident"],
          },
        ],
      },
      {
        key: "supply_chain_mgmt",
        labelZh: "供应链管理",
        labelEn: "Supply Chain Management",
        queryPack: ["supplier audit", "审计", "audit coverage", "findings", "整改", "remediation", "critical finding"],
        metrics: [
          {
            key: "supplier_audit_coverage",
            labelZh: "审计覆盖",
            labelEn: "Audit Coverage",
            queryPack: ["审计覆盖", "audit coverage", "%", "suppliers audited"],
          },
          {
            key: "critical_findings",
            labelZh: "关键缺陷",
            labelEn: "Critical Findings",
            queryPack: ["关键缺陷", "critical finding", "major finding", "non-conformance"],
          },
          {
            key: "supplier_remediation",
            labelZh: "整改",
            labelEn: "Remediation",
            queryPack: ["整改", "remediation", "corrective action", "closure rate"],
          },
        ],
      },
      {
        key: "regulatory_political",
        labelZh: "监管与政治影响",
        labelEn: "Regulatory & Political Influence",
        queryPack: ["antitrust", "反垄断", "lobbying", "游说", "political contributions", "compliance", "regulatory"],
        metrics: [
          {
            key: "antitrust",
            labelZh: "反垄断",
            labelEn: "Antitrust",
            queryPack: ["反垄断", "antitrust", "competition law", "investigation"],
          },
          {
            key: "lobbying_compliance",
            labelZh: "游说合规等",
            labelEn: "Lobbying Compliance, etc.",
            queryPack: ["游说", "lobbying", "compliance", "political contribution", "policy engagement"],
          },
        ],
      },
    ],
  },
];

export function buildTopicKey(dimension: DimensionKey, issue: IssueKey, metric: MetricKey): string {
  return `${dimension}.${issue}.${metric}`;
}

export function getDefaultSelection(): { dimension: DimensionKey; issue: IssueKey; metric: MetricKey } {
  const d = CROSS_TAXONOMY[0];
  const i = d.issues[0];
  const m = i.metrics[0];
  return { dimension: d.key, issue: i.key, metric: m.key };
}

export function getQueryPack(dimension: DimensionKey, issue: IssueKey, metric: MetricKey): string[] {
  const d = CROSS_TAXONOMY.find((x) => x.key === dimension);
  if (!d) return [];
  const i = d.issues.find((x) => x.key === issue);
  if (!i) return [];
  const m = i.metrics.find((x) => x.key === metric);
  if (!m) return i.queryPack;

  const merged = [...i.queryPack, ...m.queryPack];

  // de-dup but keep order
  const seen = new Set<string>();
  return merged.filter((q) => {
    const k = q.trim().toLowerCase();
    if (!k) return false;
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
