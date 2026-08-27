/**
 * CDP questionnaire topics → JSON stem under `backend/data/cdp_metrics/{slug}.json`.
 */
export const CDP_TOPIC_OPTIONS: { slug: string; label: string }[] = [
  { slug: "Organization", label: "Organization" },
  { slug: "Risk_and_Impact", label: "Risk & Impact" },
  { slug: "Risk_Disclosure", label: "Risk Disclosure" },
  { slug: "Governance", label: "Governance" },
  { slug: "Strategy", label: "Strategy" },
  { slug: "Climate", label: "Climate" },
  { slug: "Water", label: "Water" },
  { slug: "Biodiversity", label: "Biodiversity" },
];
