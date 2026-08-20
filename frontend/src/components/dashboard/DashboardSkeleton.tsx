export function DashboardSkeleton() {
  return (
    <div className="animate-pulse space-y-6">
      <div className="h-8 w-56 rounded-lg bg-white/[0.05]" />
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-40 rounded-2xl bg-white/[0.04]" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="h-48 rounded-2xl bg-white/[0.04]" />
        <div className="h-48 rounded-2xl bg-white/[0.04]" />
      </div>
    </div>
  )
}
