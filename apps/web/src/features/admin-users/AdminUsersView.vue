<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { api, problemMessage, type Schemas } from '../../shared/api/client'
import { roleLabel } from '../../shared/labels'

// 계정 관리 (관리자). 권한은 backend가 /api/admin/** 에서 MANAGER만 허용한다.

type Account = Schemas['Account']
type Role = Schemas['Role']
const statusLabel: Record<Schemas['AccountStatus'], string> = { PENDING: '승인 대기', ACTIVE: '사용 중', DISABLED: '비활성' }

const accounts = ref<Account[]>([])
const error = ref('')
// 승인할 때 고를 역할·트럭. 신청한 역할을 기본값으로 둔다.
const decisions = ref<Record<string, { role: Role; truckId: string }>>({})
const EMPTY_FORM = { loginId: '', password: '', name: '', role: 'DRIVER' as Role, truckId: '' }
const createForm = ref({ ...EMPTY_FORM })
const createError = ref('')

const pending = computed(() => accounts.value.filter((a) => a.status === 'PENDING'))
const others = computed(() => accounts.value.filter((a) => a.status !== 'PENDING'))

async function load() {
  const { data, error: e } = await api.GET('/api/admin/users')
  if (e) {
    error.value = problemMessage(e)
    return
  }
  accounts.value = data ?? []
  for (const a of pending.value) decisions.value[a.id] ??= { role: a.role, truckId: '' }
}

async function approve(a: Account) {
  error.value = ''
  const d = decisions.value[a.id]!
  const { error: e } = await api.POST('/api/admin/users/{userId}/approve', {
    params: { path: { userId: a.id } },
    body: { role: d.role, truckId: d.truckId || null },
  })
  if (e) error.value = `${a.loginId}: ${problemMessage(e)}`
  await load()
}

async function disable(a: Account) {
  error.value = ''
  const { error: e } = await api.POST('/api/admin/users/{userId}/disable', { params: { path: { userId: a.id } } })
  if (e) error.value = `${a.loginId}: ${problemMessage(e)}`
  await load()
}

async function create() {
  createError.value = ''
  const f = createForm.value
  const { error: e } = await api.POST('/api/admin/users', { body: { ...f, truckId: f.truckId || null } })
  if (e) {
    createError.value = problemMessage(e)
    return
  }
  createForm.value = { ...EMPTY_FORM }
  await load()
}

onMounted(load)
</script>

<template>
  <div class="console">
    <section class="card">
      <h2>계정 발급</h2>
      <form @submit.prevent="create">
        <label>아이디 <input v-model="createForm.loginId" pattern="[a-z0-9._-]{4,32}" required /></label>
        <label>초기 비밀번호 <input v-model="createForm.password" type="password" minlength="8" maxlength="72" required /></label>
        <label>이름 <input v-model="createForm.name" maxlength="50" required /></label>
        <label>
          역할
          <select v-model="createForm.role">
            <option v-for="(label, role) in roleLabel" :key="role" :value="role">{{ label }}</option>
          </select>
        </label>
        <label v-if="createForm.role === 'DRIVER'">트럭 <input v-model="createForm.truckId" placeholder="truck-3" required /></label>
        <p v-if="createError" class="error">{{ createError }}</p>
        <button>발급</button>
      </form>
    </section>

    <section class="card">
      <h2>가입 신청 <span class="muted">{{ pending.length }}건</span></h2>
      <p v-if="error" class="error">{{ error }}</p>
      <p v-if="pending.length === 0" class="muted">승인할 신청이 없다</p>
      <table v-else class="accounts">
        <tr v-for="a in pending" :key="a.id">
          <td>{{ a.name }}<br /><span class="muted">{{ a.loginId }}</span></td>
          <td>
            <select v-model="decisions[a.id]!.role">
              <option v-for="(label, role) in roleLabel" :key="role" :value="role">{{ label }}</option>
            </select>
          </td>
          <td><input v-if="decisions[a.id]!.role === 'DRIVER'" v-model="decisions[a.id]!.truckId" placeholder="트럭" /></td>
          <td>
            <button @click="approve(a)">승인</button>
            <button class="ghost" @click="disable(a)">거절</button>
          </td>
        </tr>
      </table>

      <h2>전체 계정</h2>
      <table class="accounts">
        <tr v-for="a in others" :key="a.id">
          <td>{{ a.name }}<br /><span class="muted">{{ a.loginId }}</span></td>
          <td>{{ roleLabel[a.role] }}<template v-if="a.truckId"> · {{ a.truckId }}</template></td>
          <td><span :class="['badge', a.status]">{{ statusLabel[a.status] }}</span></td>
          <td><button v-if="a.status === 'ACTIVE'" class="ghost" @click="disable(a)">비활성화</button></td>
        </tr>
      </table>
    </section>
  </div>
</template>
