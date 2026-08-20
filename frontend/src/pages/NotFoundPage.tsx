import { Link } from 'react-router-dom'

export function NotFoundPage() {
  return (
    <div className="flex flex-col items-start gap-2 py-16">
      <h1 className="text-2xl font-semibold text-[color:var(--color-ink)]">Page not found</h1>
      <Link to="/farms" className="text-sm font-medium text-lime-400 hover:text-lime-300">
        Back to dashboard
      </Link>
    </div>
  )
}
