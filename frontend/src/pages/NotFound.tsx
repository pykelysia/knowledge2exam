import { Link } from 'react-router-dom'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card'

export function NotFound() {
  return (
    <div className="flex items-center justify-center py-24">
      <Card className="max-w-md">
        <CardHeader>
          <CardTitle className="text-center text-xl">404</CardTitle>
        </CardHeader>
        <CardContent className="space-y-4 text-center">
          <p className="text-slate-600">页面不存在或已被移除</p>
          <Link to="/">
            <Button>返回首页</Button>
          </Link>
        </CardContent>
      </Card>
    </div>
  )
}
