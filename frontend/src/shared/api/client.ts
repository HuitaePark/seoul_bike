import createClient, { type Middleware } from 'openapi-fetch'
import type { components, paths } from './schema'

// schema.d.ts는 docs/specs/openapi.yaml에서 생성한다 (npm run gen:api). 손으로 고치지 않는다.
export type Schemas = components['schemas']
export type Problem = Schemas['Problem']

let tokenProvider: () => string | null = () => null
let onUnauthorized: () => void = () => {}

/** auth 스토어가 기동할 때 연결한다. 클라이언트가 스토어를 직접 import하면 순환 참조가 생긴다. */
export function bindAuth(getToken: () => string | null, unauthorized: () => void) {
  tokenProvider = getToken
  onUnauthorized = unauthorized
}

export function authHeader(): Record<string, string> {
  const token = tokenProvider()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

const auth: Middleware = {
  onRequest({ request }) {
    const token = tokenProvider()
    if (token) request.headers.set('Authorization', `Bearer ${token}`)
    return request
  },
  onResponse({ response, request }) {
    // 로그인 요청의 401은 "비밀번호 틀림"이라 로그아웃 처리하지 않는다.
    if (response.status === 401 && !request.url.endsWith('/api/auth/login')) onUnauthorized()
    return response
  },
}

export const api = createClient<paths>({ baseUrl: '' })
api.use(auth)

/** 오류 응답(problem+json)을 화면에 보여줄 한 줄로 바꾼다. */
export function problemMessage(error: unknown): string {
  const p = error as Partial<Problem> | undefined
  return p?.detail ?? p?.title ?? '요청을 처리하지 못했다'
}
