import { useEffect, useState, type FormEvent } from 'react'
import { AlertCircle, Check, School as SchoolIcon, UserRound } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select } from '@/components/ui/select'
import { Card, CardContent, CardHeader, CardIcon, CardTitle } from '@/components/ui/card'
import { Spinner } from '@/components/ui/spinner'
import { listSchools } from '@/api/schools'
import { extractApiError } from '@/api/client'
import { useAuth } from '@/hooks/useAuth'
import type { School } from '@/api/types'
import { errorMessage } from '@/lib/constants'

/** 个人资料：查看账号信息、修改学校归属（决定「本校共享」的可见范围）。 */
export function Profile() {
  const { user, updateCurrentUser } = useAuth()
  const [schools, setSchools] = useState<School[]>([])
  const [schoolId, setSchoolId] = useState(user?.school_id ?? '')
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    listSchools()
      .then((res) => setSchools(res.schools))
      .catch(() => setSchools([]))
  }, [])

  useEffect(() => {
    setSchoolId(user?.school_id ?? '')
  }, [user?.school_id])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)
    setSaved(false)
    setSaving(true)
    try {
      await updateCurrentUser({ school_id: schoolId || null })
      setSaved(true)
    } catch (err) {
      const apiErr = extractApiError(err)
      setError(apiErr ? errorMessage(apiErr.error_code) : '保存失败，请稍后再试')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-[-0.02em] text-slate-900">个人资料</h1>
        <p className="mt-1.5 text-[13.5px] text-slate-500">
          学校归属决定「本校共享」资料的可见范围：设置为某校后可见该校文件，也可上传时携带该校标签。
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardIcon>
            <UserRound />
          </CardIcon>
          <CardTitle>账号信息</CardTitle>
        </CardHeader>
        <CardContent className="grid grid-cols-1 gap-4 sm:grid-cols-2">
          <div>
            <Label htmlFor="profile-email">邮箱</Label>
            <Input id="profile-email" value={user?.email ?? ''} readOnly disabled />
          </div>
          <div>
            <Label htmlFor="profile-username">用户名</Label>
            <Input id="profile-username" value={user?.username ?? ''} readOnly disabled />
          </div>
        </CardContent>
      </Card>

      <form onSubmit={handleSubmit}>
        <Card>
          <CardHeader>
            <CardIcon>
              <SchoolIcon />
            </CardIcon>
            <CardTitle>学校归属</CardTitle>
          </CardHeader>
          <CardContent className="space-y-4">
            <div className="max-w-sm">
              <Label htmlFor="profile-school">学校（可选）</Label>
              <Select
                id="profile-school"
                value={schoolId}
                onChange={(e) => {
                  setSchoolId(e.target.value)
                  setSaved(false)
                }}
              >
                <option value="">暂不设置（不可使用「本校共享」）</option>
                {schools.map((s) => (
                  <option key={s.school_id} value={s.school_id}>
                    {s.name}
                  </option>
                ))}
              </Select>
              <p className="mt-1.5 text-[12px] text-slate-400">
                修改立即生效：更换后只能看到新学校的「本校共享」文件；已上传文件的学校标签不变。
              </p>
            </div>

            {error && (
              <div className="flex items-start gap-2.5 rounded-[10px] border border-red-200 bg-red-50 px-3.5 py-2.5 text-[13px] leading-relaxed text-red-700">
                <AlertCircle className="mt-0.5 h-4 w-4 flex-none text-red-500" />
                <span>{error}</span>
              </div>
            )}

            <div className="flex items-center gap-3">
              <Button type="submit" disabled={saving}>
                {saving ? (
                  <>
                    <Spinner className="h-4 w-4" /> 保存中…
                  </>
                ) : (
                  '保存修改'
                )}
              </Button>
              {saved && (
                <span className="inline-flex items-center gap-1.5 text-[12.5px] text-emerald-600">
                  <Check className="h-3.5 w-3.5" />
                  已保存
                </span>
              )}
            </div>
          </CardContent>
        </Card>
      </form>
    </div>
  )
}
