import { client } from '@/api/client'
import type { CoursesResponse, SchoolsResponse } from '@/api/types'

export async function listSchools(): Promise<SchoolsResponse> {
  const { data } = await client.get<SchoolsResponse>('/schools')
  return data
}

export async function listCourses(schoolId: string): Promise<CoursesResponse> {
  const { data } = await client.get<CoursesResponse>(`/schools/${schoolId}/courses`)
  return data
}
