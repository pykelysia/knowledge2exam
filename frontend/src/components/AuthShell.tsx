import { GraduationCap } from 'lucide-react'
import { Card, CardContent } from '@/components/ui/card'

/** 登录 / 注册共用的品牌区 + 居中卡片外壳。 */
export function AuthShell({
  title,
  subtitle,
  children,
}: {
  title: string
  subtitle: string
  children: React.ReactNode
}) {
  return (
    <div className="grid min-h-screen place-items-center bg-[radial-gradient(640px_300px_at_50%_8%,rgb(15_23_42/0.055),transparent_72%)] px-5 py-10">
      <div className="w-[384px] max-w-full">
        <div className="mb-6 flex flex-col items-center gap-2.5 text-center">
          <span className="grid h-11 w-11 place-items-center rounded-xl bg-gradient-to-br from-slate-800 to-slate-900 text-white shadow-btn">
            <GraduationCap className="h-5 w-5" />
          </span>
          <span className="text-[17px] font-semibold tracking-[-0.01em] text-slate-900">
            knowledge2exam
          </span>
          <span className="-mt-1.5 text-[12.5px] text-slate-500">
            上传课程资料，自动生成模拟试卷
          </span>
        </div>
        <Card>
          <CardContent className="px-6 pb-6 pt-6">
            <div className="mb-[18px] text-center">
              <h1 className="text-lg font-semibold tracking-[-0.01em] text-slate-900">{title}</h1>
              <p className="mt-1 text-[12.5px] text-slate-500">{subtitle}</p>
            </div>
            {children}
          </CardContent>
        </Card>
      </div>
    </div>
  )
}
