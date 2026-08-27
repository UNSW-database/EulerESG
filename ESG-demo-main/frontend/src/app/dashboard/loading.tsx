export default function DashboardLoading() {
  return (
    <main
      className="min-h-screen w-full bg-slate-50 px-6 py-5"
      role="status"
      aria-label="Loading dashboard"
    >
      <div className="mx-auto w-full max-w-[1500px] animate-pulse space-y-4">
        <div className="h-24 rounded-2xl border border-slate-200 bg-white" />
        <div className="h-10 w-64 rounded-lg bg-slate-200/80" />
        <div className="overflow-hidden rounded-2xl border border-slate-200 bg-white">
          <div className="h-12 border-b border-slate-200 bg-slate-100" />
          {Array.from({ length: 6 }, (_, index) => (
            <div
              key={index}
              className="h-12 border-b border-slate-100 last:border-0"
            />
          ))}
        </div>
      </div>
      <span className="sr-only">Loading…</span>
    </main>
  );
}
