<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const rows = ref([])
const shifts = ref([])
const targetShift = ref({})
const err = ref('')
const msg = ref('')
const ok = ref(false)

async function refresh() {
  rows.value = await getJson('/api/batches')
  if (!shifts.value.length) {
    shifts.value = await getJson('/api/shifts')
  }
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    err.value = String(e.message || e)
  }
})

async function settle(b) {
  msg.value = ''
  ok.value = false
  try {
    await postJson(`/api/batches/${b.id}/settle`, {})
    ok.value = true
    msg.value = `批次 ${b.biz_key} 已结清`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function transfer(b) {
  msg.value = ''
  ok.value = false
  const to = Number(targetShift.value[b.id])
  if (!to) {
    msg.value = '请先选择目标班次'
    return
  }
  try {
    await postJson(`/api/batches/${b.id}/transfer`, { to_shift_id: to })
    ok.value = true
    msg.value = `批次 ${b.biz_key} 已改归属`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>在途批次</h1>
    <p class="lead">
      批次归属当前值班班次；发起交班后批次进入托管态，不计入任何一方在途。
      托管期内结清、改归属都会被拒绝并指明托管交班单号。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <p class="msg" :class="{ ok }">{{ msg }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>业务键</th><th>标题</th><th>归属班次</th><th>状态</th>
            <th>托管交班单</th><th>备注</th><th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="b in rows" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>{{ b.owner_shift_name || '—' }}</td>
            <td>
              <span
                class="tag"
                :class="{ on: b.status === '在途', escrow: b.status === '托管中', settled: b.status === '已结清' }"
              >{{ b.status }}</span>
            </td>
            <td>{{ b.escrow_handover_id ? `#${b.escrow_handover_id}` : '—' }}</td>
            <td>{{ b.note || '—' }}</td>
            <td>
              <div class="row" style="margin:0">
                <button
                  class="btn ghost mini"
                  :disabled="b.status !== '在途'"
                  @click="settle(b)"
                >结清</button>
                <select v-model="targetShift[b.id]" :disabled="b.status !== '在途'">
                  <option :value="undefined" disabled>改归属到…</option>
                  <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }}</option>
                </select>
                <button
                  class="btn mini"
                  :disabled="b.status !== '在途'"
                  @click="transfer(b)"
                >改归属</button>
              </div>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
