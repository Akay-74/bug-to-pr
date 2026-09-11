'use client'
import Link from 'next/link'
import { usePathname } from 'next/navigation'

export function TopNav() {
  const pathname = usePathname()
  const navItems = [
    { label: 'ISSUES', href: '/' },
    { label: 'RUNS', href: '/runs' },
    { label: 'BENCHMARKS', href: '/benchmarks' },
    { label: 'ABOUT', href: '/about' },
  ]
  
  const isActive = (href: string) => {
    if (href === '/') return pathname === '/' || pathname.startsWith('/issues')
    return pathname.startsWith(href)
  }

  return (
    <nav className="hidden md:flex fixed top-0 w-full z-50 items-center justify-between px-4 h-14 bg-surface-container-low border-b border-outline-variant">
      <div className="flex items-center gap-6">
        <Link href="/" className="text-xl font-bold text-on-surface tracking-tight">Bug-to-PR Agent</Link>
        <div className="flex gap-0 h-14 items-stretch">
          {navItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={`flex items-center px-4 text-[11px] font-bold tracking-[0.05em] transition-colors ${
                isActive(item.href)
                  ? 'text-primary border-b-2 border-primary'
                  : 'text-on-surface-variant hover:bg-surface-container-highest'
              }`}
            >
              {item.label}
            </Link>
          ))}
        </div>
      </div>
      <div className="flex items-center gap-3">
        <button className="text-on-surface-variant hover:bg-surface-container-highest p-1.5 rounded transition-colors">
          <span className="material-symbols-outlined text-[20px]">dark_mode</span>
        </button>
        <button className="text-on-surface-variant hover:bg-surface-container-highest p-1.5 rounded transition-colors">
          <span className="material-symbols-outlined text-[20px]">terminal</span>
        </button>
        <button className="text-[11px] font-bold tracking-[0.05em] bg-inverse-primary text-primary-fixed px-4 py-1.5 rounded transition-colors hover:opacity-90">
          GitHub
        </button>
      </div>
    </nav>
  )
}
