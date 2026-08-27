"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, ExternalLink, Search } from "lucide-react";

import { useT } from "@/i18n/useT";
import {
  apiService,
  type StandardsLibraryCatalogResponse,
  type StandardsLibraryFramework,
  type StandardsLibraryMetricsResponse,
} from "@/lib/api";

const PAGE_SIZE = 20;

const copyFor = (lang: string) =>
  lang === "zh"
    ? {
        title: "标准库",
        subtitle: "按框架、行业与主题查阅披露指标。",
        loading: "正在加载标准…",
        retry: "重试",
        official: "查看官方标准",
        officialOnly: "仅官方标准",
        updated: "更新于",
        scopes: "个范围",
        noLocal: "该框架的指标库尚未收录。",
        noLocalHint: "你仍可前往官方来源查阅现行标准。",
        chooseScope: "选择一个行业或主题，查看对应指标。",
        searchScopes: "搜索行业或主题",
        searchMetrics: "搜索指标",
        metrics: "指标",
        code: "代码",
        metric: "指标",
        topic: "主题",
        classification: "类型",
        unit: "单位",
        definition: "查看定义",
        loadingMetrics: "正在加载指标…",
        noMetrics: "没有匹配的指标。",
        previous: "上一页",
        next: "下一页",
        page: (current: number, total: number) => `第 ${current} / ${total} 页`,
        total: (count: number) => `共 ${count} 条指标`,
      }
    : {
        title: "Standards Library",
        subtitle: "Explore disclosure metrics by framework, industry, and topic.",
        loading: "Loading standards…",
        retry: "Retry",
        official: "View official standard",
        officialOnly: "Official standard only",
        updated: "Updated",
        scopes: "scopes",
        noLocal: "This framework has not been added to the metrics collection yet.",
        noLocalHint: "You can still consult the current standard at its official source.",
        chooseScope: "Select an industry or topic to view its metrics.",
        searchScopes: "Search industries or topics",
        searchMetrics: "Search metrics",
        metrics: "Metrics",
        code: "Code",
        metric: "Metric",
        topic: "Topic",
        classification: "Type",
        unit: "Unit",
        definition: "View definition",
        loadingMetrics: "Loading metrics…",
        noMetrics: "No matching metrics.",
        previous: "Previous",
        next: "Next",
        page: (current: number, total: number) => `Page ${current} of ${total}`,
        total: (count: number) => `${count} metrics`,
      };

const errorMessage = (error: unknown) =>
  error instanceof Error && error.message.trim() ? error.message : "Unable to load standards data";

export default function FrameworkReferencePanel() {
  const { lang } = useT();
  const copy = useMemo(() => copyFor(lang), [lang]);
  const [catalog, setCatalog] = useState<StandardsLibraryCatalogResponse | null>(null);
  const [catalogLoading, setCatalogLoading] = useState(true);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [selectedFrameworkId, setSelectedFrameworkId] = useState("");
  const [selectedGroupId, setSelectedGroupId] = useState("");
  const [selectedScopeId, setSelectedScopeId] = useState("");
  const [scopeSearch, setScopeSearch] = useState("");
  const [metricSearch, setMetricSearch] = useState("");
  const [metricsResult, setMetricsResult] = useState<StandardsLibraryMetricsResponse | null>(null);
  const [metricsLoading, setMetricsLoading] = useState(false);
  const [metricsError, setMetricsError] = useState<string | null>(null);
  const [page, setPage] = useState(1);
  const metricsRequestId = useRef(0);

  const loadCatalog = async (forceRefresh = false) => {
    setCatalogLoading(true);
    setCatalogError(null);
    try {
      const response = await apiService.getStandardsCatalog(forceRefresh);
      setCatalog(response);
      setSelectedFrameworkId((current) => {
        if (response.frameworks.some((framework) => framework.id === current)) return current;
        return response.frameworks.find((framework) => framework.available)?.id ?? response.frameworks[0]?.id ?? "";
      });
    } catch (error) {
      setCatalogError(errorMessage(error));
    } finally {
      setCatalogLoading(false);
    }
  };

  useEffect(() => {
    let active = true;
    setCatalogLoading(true);
    apiService.getStandardsCatalog().then(
      (response) => {
        if (!active) return;
        setCatalog(response);
        setCatalogError(null);
        setSelectedFrameworkId(
          response.frameworks.find((framework) => framework.available)?.id ?? response.frameworks[0]?.id ?? "",
        );
        setCatalogLoading(false);
      },
      (error) => {
        if (!active) return;
        setCatalogError(errorMessage(error));
        setCatalogLoading(false);
      },
    );
    return () => {
      active = false;
    };
  }, []);

  const selectedFramework = useMemo(
    () => catalog?.frameworks.find((framework) => framework.id === selectedFrameworkId) ?? null,
    [catalog, selectedFrameworkId],
  );

  useEffect(() => {
    if (!selectedFramework) return;
    setSelectedGroupId((current) =>
      selectedFramework.groups.some((group) => group.id === current)
        ? current
        : selectedFramework.groups[0]?.id ?? "",
    );
  }, [selectedFramework]);

  const selectedGroup = useMemo(
    () => selectedFramework?.groups.find((group) => group.id === selectedGroupId) ?? null,
    [selectedFramework, selectedGroupId],
  );

  useEffect(() => {
    if (!selectedFrameworkId || !selectedGroupId || !selectedScopeId) {
      setMetricsResult(null);
      setMetricsError(null);
      setMetricsLoading(false);
      return;
    }
    const requestId = ++metricsRequestId.current;
    const controller = new AbortController();
    setMetricsResult(null);
    setMetricsError(null);
    setMetricsLoading(true);
    setMetricSearch("");
    setPage(1);
    apiService
      .getStandardMetrics(selectedFrameworkId, selectedGroupId, selectedScopeId, controller.signal)
      .then(
        (response) => {
          if (requestId !== metricsRequestId.current || controller.signal.aborted) return;
          setMetricsResult(response);
          setMetricsLoading(false);
        },
        (error) => {
          if (requestId !== metricsRequestId.current || controller.signal.aborted) return;
          setMetricsError(errorMessage(error));
          setMetricsLoading(false);
        },
      );
    return () => controller.abort();
  }, [selectedFrameworkId, selectedGroupId, selectedScopeId]);

  const chooseFramework = (framework: StandardsLibraryFramework) => {
    metricsRequestId.current += 1;
    setSelectedFrameworkId(framework.id);
    setSelectedGroupId(framework.groups[0]?.id ?? "");
    setSelectedScopeId("");
    setScopeSearch("");
    setMetricSearch("");
    setMetricsResult(null);
    setMetricsError(null);
    setPage(1);
  };

  const chooseGroup = (groupId: string) => {
    metricsRequestId.current += 1;
    setSelectedGroupId(groupId);
    setSelectedScopeId("");
    setScopeSearch("");
    setMetricsResult(null);
    setMetricsError(null);
    setPage(1);
  };

  const filteredScopes = useMemo(() => {
    const query = scopeSearch.trim().toLocaleLowerCase();
    const scopes = selectedGroup?.scopes ?? [];
    return query ? scopes.filter((scope) => scope.label.toLocaleLowerCase().includes(query)) : scopes;
  }, [scopeSearch, selectedGroup]);

  const filteredMetrics = useMemo(() => {
    const metrics = metricsResult?.metrics ?? [];
    const query = metricSearch.trim().toLocaleLowerCase();
    if (!query) return metrics;
    return metrics.filter((metric) =>
      [metric.code, metric.name, metric.topic, metric.category, metric.type, metric.unit, metric.standard]
        .filter(Boolean)
        .some((value) => String(value).toLocaleLowerCase().includes(query)),
    );
  }, [metricSearch, metricsResult]);

  const totalPages = Math.max(1, Math.ceil(filteredMetrics.length / PAGE_SIZE));
  const visibleMetrics = filteredMetrics.slice((page - 1) * PAGE_SIZE, page * PAGE_SIZE);

  useEffect(() => {
    setPage(1);
  }, [metricSearch]);

  useEffect(() => {
    if (page > totalPages) setPage(totalPages);
  }, [page, totalPages]);

  return (
    <section
      aria-labelledby="standards-library-title"
      className="w-full"
      data-testid="standards-library"
    >
      <header className="mb-6 max-w-3xl">
        <h1 id="standards-library-title" className="m-0 text-3xl font-semibold tracking-[-0.04em] text-slate-950 sm:text-[2.15rem]">
          {copy.title}
        </h1>
        <p className="mb-0 mt-2 max-w-2xl text-sm leading-6 text-slate-500">{copy.subtitle}</p>
      </header>

      {catalogLoading && !catalog ? (
        <div className="bg-[#f6f6f3] px-5 py-12 text-center text-sm text-slate-500" role="status">
          {copy.loading}
        </div>
      ) : catalogError && !catalog ? (
        <div className="bg-red-50 px-5 py-6 text-sm text-red-700" role="alert">
          <p className="m-0">{catalogError}</p>
          <button className="mt-3 rounded-lg bg-red-100 px-3 py-1.5 font-medium text-red-800 transition-colors hover:bg-red-200 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500" onClick={() => void loadCatalog(true)} type="button">
            {copy.retry}
          </button>
        </div>
      ) : (
        <>
          <div aria-label="Frameworks" className="-mx-1 flex gap-4 overflow-x-auto px-1 pb-1 sm:gap-5" role="group">
            {catalog?.frameworks.map((framework) => {
              const active = framework.id === selectedFrameworkId;
              return (
                <article className="relative min-w-[156px] flex-1" key={framework.id}>
                  <button
                    aria-controls="standards-browser"
                    aria-pressed={active}
                    className={`relative block w-full pb-4 pr-7 text-left transition-colors after:absolute after:inset-x-0 after:bottom-0 after:h-0.5 after:origin-left after:transition-transform focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC] focus-visible:ring-offset-4 ${active ? "text-slate-950 after:scale-x-100 after:bg-slate-950" : "text-slate-500 after:scale-x-0 after:bg-slate-300 hover:text-slate-950 hover:after:scale-x-100"}`}
                    onClick={() => chooseFramework(framework)}
                    type="button"
                  >
                    <span className="block text-base font-semibold tracking-[-0.015em]">{framework.name}</span>
                    <span className="mt-1.5 block text-xs text-slate-400">
                      {copy.updated} {framework.as_of} · {framework.available ? `${framework.scope_count} ${copy.scopes}` : copy.officialOnly}
                    </span>
                  </button>
                  <a
                    aria-label={`${copy.official} ${framework.name}`}
                    className="absolute right-0 top-0 rounded p-1 text-slate-400 transition-colors hover:text-[#2274BC] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC]"
                    href={framework.source_url}
                    rel="noopener noreferrer"
                    target="_blank"
                  >
                    <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
                  </a>
                </article>
              );
            })}
          </div>

          <div className="mt-6" id="standards-browser">
            {selectedFramework && !selectedFramework.available ? (
              <div className="bg-[#f6f6f3] px-6 py-12 text-center sm:px-10">
                <h2 className="m-0 text-xl font-semibold tracking-[-0.025em] text-slate-950">{selectedFramework.name}</h2>
                <p className="mb-0 mt-3 text-sm text-slate-600">{copy.noLocal}</p>
                <p className="mb-0 mt-1 text-sm text-slate-500">{copy.noLocalHint}</p>
                <a className="mt-5 inline-flex items-center gap-1.5 text-sm font-semibold text-[#2274BC] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC]" href={selectedFramework.source_url} rel="noopener noreferrer" target="_blank" aria-label={`${copy.official} ${selectedFramework.name}`}>
                  {copy.official} {selectedFramework.name}
                  <ExternalLink aria-hidden="true" className="h-3.5 w-3.5" />
                </a>
              </div>
            ) : selectedFramework ? (
              <div className="grid gap-6 lg:grid-cols-[248px_minmax(0,1fr)] xl:gap-8">
                <aside aria-label={`${selectedFramework.name} taxonomy`} className="min-w-0">
                  {selectedFramework.groups.length > 1 && (
                    <label className="mb-5 block">
                      <span className="px-1 text-xs font-medium text-slate-500">
                        {selectedFramework.group_label}
                      </span>
                      <span className="relative mt-2 block">
                        <select
                          aria-label={selectedFramework.group_label}
                          className="w-full appearance-none rounded-lg bg-[#f4f4f1] py-2.5 pl-3 pr-9 text-sm font-medium text-slate-800 outline-none transition-colors hover:bg-[#ecece8] focus-visible:bg-white focus-visible:ring-2 focus-visible:ring-[#2274BC]/70"
                          onChange={(event) => chooseGroup(event.target.value)}
                          value={selectedGroupId}
                        >
                          {selectedFramework.groups.map((group) => (
                            <option key={group.id} value={group.id}>
                              {group.label}
                            </option>
                          ))}
                        </select>
                        <ChevronDown
                          aria-hidden="true"
                          className="pointer-events-none absolute right-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                        />
                      </span>
                    </label>
                  )}
                  <div className="mb-2 flex items-center justify-between gap-3 px-1">
                    <span className="text-xs font-medium text-slate-500">{selectedFramework.scope_label}</span>
                    <span className="text-xs tabular-nums text-slate-400">{selectedGroup?.scopes.length ?? 0}</span>
                  </div>
                  <label className="relative block">
                    <span className="sr-only">{copy.searchScopes}</span>
                    <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
                    <input
                      aria-label={copy.searchScopes}
                      className="w-full rounded-lg bg-[#f4f4f1] py-2 pl-9 pr-3 text-sm text-slate-800 outline-none transition-colors placeholder:text-slate-400 focus-visible:bg-white focus-visible:ring-2 focus-visible:ring-[#2274BC]/70"
                      onChange={(event) => setScopeSearch(event.target.value)}
                      placeholder={selectedFramework.scope_label}
                      type="search"
                      value={scopeSearch}
                    />
                  </label>
                  <div className="mt-3 max-h-[560px] space-y-1 overflow-y-auto overscroll-y-auto pr-1">
                    {filteredScopes.map((scope) => (
                      <button
                        aria-current={scope.id === selectedScopeId ? "true" : undefined}
                        className={`block w-full rounded-lg px-3 py-2.5 text-left text-sm transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC] ${scope.id === selectedScopeId ? "bg-slate-900 font-medium text-white" : "text-slate-600 hover:bg-[#f4f4f1] hover:text-slate-950"}`}
                        key={scope.id}
                        onClick={() => setSelectedScopeId(scope.id)}
                        type="button"
                      >
                        {scope.label}
                      </button>
                    ))}
                  </div>
                </aside>

                <section aria-label={`${selectedFramework.name} ${copy.metrics}`} className="min-w-0" role="region">
                  {!selectedScopeId ? (
                    <div className="flex min-h-72 items-center justify-center bg-[#fafaf8] px-6 text-center text-sm text-slate-500">
                      <p className="m-0 max-w-sm leading-6">{copy.chooseScope}</p>
                    </div>
                  ) : metricsLoading ? (
                    <div className="bg-[#fafaf8] px-5 py-16 text-center text-sm text-slate-500" role="status">{copy.loadingMetrics}</div>
                  ) : metricsError ? (
                    <div className="bg-red-50 px-5 py-6 text-sm text-red-700" role="alert">{metricsError}</div>
                  ) : metricsResult ? (
                    <>
                      <div className="mb-5 flex flex-col gap-4 sm:flex-row sm:items-end sm:justify-between">
                        <div>
                          <h2 className="m-0 text-2xl font-semibold tracking-[-0.035em] text-slate-950">{metricsResult.scope.label}</h2>
                          <p className="mb-0 mt-1.5 text-sm text-slate-500">{metricsResult.group.label} · {copy.total(metricsResult.total_metrics)}</p>
                        </div>
                        <label className="relative block sm:w-64">
                          <span className="sr-only">{copy.searchMetrics}</span>
                          <Search aria-hidden="true" className="pointer-events-none absolute left-3 top-2.5 h-3.5 w-3.5 text-slate-400" />
                          <input aria-label={copy.searchMetrics} className="w-full rounded-lg bg-[#f4f4f1] py-2 pl-9 pr-3 text-sm text-slate-800 outline-none transition-colors placeholder:text-slate-400 focus-visible:bg-white focus-visible:ring-2 focus-visible:ring-[#2274BC]/70" onChange={(event) => setMetricSearch(event.target.value)} placeholder={copy.searchMetrics} type="search" value={metricSearch} />
                        </label>
                      </div>

                      <div className="overflow-x-auto">
                        <table className="w-full min-w-[760px] border-collapse text-left text-sm">
                          <caption className="sr-only">{metricsResult.scope.label} {copy.metrics}</caption>
                          <thead className="bg-[#f4f4f1] text-xs font-medium text-slate-500">
                            <tr>
                              <th className="px-3 py-2.5" scope="col">{copy.code}</th>
                              <th className="px-3 py-2.5" scope="col">{copy.metric}</th>
                              <th className="px-3 py-2.5" scope="col">{copy.topic}</th>
                              <th className="px-3 py-2.5" scope="col">{copy.classification}</th>
                              <th className="px-3 py-2.5" scope="col">{copy.unit}</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-slate-200/80">
                            {visibleMetrics.map((metric) => (
                              <tr className="align-top transition-colors hover:bg-[#fafaf8]" key={metric.id}>
                                <td className="whitespace-nowrap px-3 py-4 font-mono text-xs text-slate-500">{metric.code || "—"}</td>
                                <td className="max-w-xl px-3 py-4 text-slate-800">
                                  <div className="font-medium">{metric.name}</div>
                                  {metric.standard && <div className="mt-1 text-xs text-slate-500">{metric.standard}</div>}
                                  {(metric.simple_definition || metric.definition) && (
                                    <details className="mt-1.5 text-xs text-slate-600">
                                      <summary className="cursor-pointer font-medium text-[#2274BC] underline-offset-4 hover:underline focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC]">{copy.definition}</summary>
                                      <p className="mb-0 mt-2 whitespace-pre-wrap break-words leading-5">{metric.simple_definition || metric.definition}</p>
                                    </details>
                                  )}
                                </td>
                                <td className="px-3 py-4 text-slate-600">{metric.topic || "—"}</td>
                                <td className="px-3 py-4 text-slate-600">{metric.category || "—"}</td>
                                <td className="px-3 py-4 text-slate-600">{metric.unit || "—"}</td>
                              </tr>
                            ))}
                            {visibleMetrics.length === 0 && (
                              <tr>
                                <td className="px-4 py-12 text-center text-sm text-slate-500" colSpan={5}>{copy.noMetrics}</td>
                              </tr>
                            )}
                          </tbody>
                        </table>
                      </div>

                      <nav aria-label={`${metricsResult.scope.label} ${copy.metrics} pagination`} className="mt-4 flex items-center justify-between gap-3 text-xs text-slate-500">
                        <button className="rounded-lg bg-[#f4f4f1] px-3 py-2 font-medium text-slate-600 transition-colors hover:bg-[#e9e9e5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC] disabled:cursor-not-allowed disabled:opacity-40" disabled={page <= 1} onClick={() => setPage((value) => Math.max(1, value - 1))} type="button">{copy.previous}</button>
                        <span aria-live="polite" className="tabular-nums">{copy.page(page, totalPages)}</span>
                        <button className="rounded-lg bg-[#f4f4f1] px-3 py-2 font-medium text-slate-600 transition-colors hover:bg-[#e9e9e5] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[#2274BC] disabled:cursor-not-allowed disabled:opacity-40" disabled={page >= totalPages} onClick={() => setPage((value) => Math.min(totalPages, value + 1))} type="button">{copy.next}</button>
                      </nav>
                    </>
                  ) : null}
                </section>
              </div>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
