import { Link, NavLink, Outlet, useLocation, useNavigate } from 'react-router-dom'
import { GraduationCap, LogOut } from 'lucide-react'
import { useAuth } from '@/hooks/useAuth'
import { cn } from '@/lib/utils'

const NAV_ITEMS = [
  { to: '/', end: true, label: '生成试卷' },
  { to: '/jobs', end: false, label: '我的任务' },
] as const

export function Layout() {
  const { user, logout } = useAuth()
  const navigate = useNavigate()
  const location = useLocation()

  async function handleLogout() {
    await logout()
    navigate('/login')
  }

  return (
    <div className="min-h-screen">
      <header className="sticky top-0 z-10 border-b border-slate-200/90 bg-white/80 backdrop-blur-md backdrop-saturate-150">
        <div className="mx-auto flex h-[60px] max-w-5xl items-center justify-between px-4">
          <div className="flex items-center">
            <Link to="/" className="flex items-center gap-3">
              <span className="grid h-[30px] w-[30px] place-items-center rounded-[9px] bg-gradient-to-br from-slate-800 to-slate-900 text-white shadow-btn">
                <GraduationCap className="h-4 w-4" />
              </span>
              <span className="text-base font-semibold tracking-[-0.01em] text-slate-900">
                knowledge2exam
              </span>
            </Link>
            <nav className="ml-5 flex items-center gap-1 text-[13.5px]">
              {NAV_ITEMS.map((item) => (
                <NavLink
                  key={item.to}
                  to={item.to}
                  end={item.end}
                  className={({ isActive }) =>
                    cn(
                      'rounded-full px-3.5 py-1.5 transition-colors',
                      isActive
                        ? 'bg-slate-100 font-medium text-slate-900'
                        : 'text-slate-600 hover:bg-slate-100 hover:text-slate-900',
                    )
                  }
                >
                  {item.label}
                </NavLink>
              ))}
            </nav>
          </div>
          <div className="flex items-center gap-3">
            {user && (
              <span className="flex items-center gap-2 text-[13px] text-slate-700">
                <span className="grid h-[26px] w-[26px] place-items-center rounded-full bg-gradient-to-br from-slate-700 to-slate-900 text-[11.5px] font-semibold text-white">
                  {user.username.slice(0, 1).toUpperCase()}
                </span>
                {user.username}
              </span>
            )}
            <button
              onClick={handleLogout}
              className="inline-flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-[13px] text-slate-600 transition-colors hover:bg-slate-100 hover:text-slate-900"
            >
              <LogOut className="h-3.5 w-3.5" />
              登出
            </button>
          </div>
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-8">
        <div key={location.pathname} className="animate-page-in">
          <Outlet />
        </div>
      </main>
    </div>
  )
}
