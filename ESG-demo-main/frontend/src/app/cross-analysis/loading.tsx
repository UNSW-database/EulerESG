export default function CrossAnalysisLoading() {
  return (
    <main
      className="min-h-screen w-full bg-slate-50 px-6 py-5"
      role="status"
      aria-label="Loading cross analysis"
    >
      <div className="mx-auto w-full max-w-[1500px] animate-pulse space-y-5">
        <div className="flex items-center justify-between gap-4">
          <div className="h-9 w-72 rounded-lg bg-slate-200/80" />
          <div className="h-9 w-44 rounded-lg bg-slate-200/80" />
        </div>
        <div className="grid gap-4 xl:grid-cols-2">
          {Array.from({ length: 4 }, (_, index) => (
            <div
              key={index}
              className="h-[360px] rounded-2xl border border-slate-200 bg-white"
            />
          ))}
        </div>
      </div>
      <span className="sr-only">Loading…</span>
    </main>
  );
}
