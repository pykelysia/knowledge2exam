import { client } from '@/api/client'
import type { CoursesResponse, SchoolsResponse } from '@/api/types'

export async function listSchools(): Promise<SchoolsResponse> {
  const { data } = await client.get<SchoolsResponse>('/schools')
  return data
}

/** 全局课程列表（课程与学校解绑，可只按课程筛选）。 */
export async function listCourses(): Promise<CoursesResponse> {
  const { data } = await client.get<CoursesResponse>('/courses')
  return data
}
