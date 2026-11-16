<script setup>
import { onMounted, ref } from 'vue'
import { getJson, postJson } from '../api'

const shifts = ref([])
const fromId = ref(1)
const toId = ref(2)
const current = ref(null)
const escrowRows = ref([])
const msg = ref('')
const ok = ref(false)

async function refresh() {
  shifts.value = await getJson('/api/shifts')
  const logs = await getJson('/api/handovers')
  current.value = logs.find((h) => h.status === '进行中') || null
  escrowRows.value = await getJson('/api/handovers/escrow')
  if (current.value) {
    current.value = await getJson(`/api/handovers/${current.value.id}`)
  }
}

onMounted(async () => {
  try {
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
})

async function start() {
  msg.value = ''
  ok.value = false
  try {
    current.value = await postJson('/api/handovers/start', {
      from_shift_id: Number(fromId.value),
      to_shift_id: Number(toId.value),
    })
    ok.value = true
    msg.value = `已开交班单 #${current.value.id}：清单 ${current.value.batch_count} 批已进入托管，两侧在途均不计入`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}

async function exitVia(path, successText, failText) {
  if (!current.value) return
  msg.value = ''
  ok.value = false
  try {
    const body = path.includes('cancel')
      ? { reason: '人工取消交班' }
      : path.includes('fail')
        ? { reason: '交班失败，批次退回交班方' }
        : {}
    const result = await postJson(`/api/handovers/${current.value.id}/${path}`, body)
    ok.value = result.status === '已完成'
    msg.value = result.status === '已完成'
      ? successText
      : `${failText}（交班单 #${result.id} 状态：${result.status}），清单批次已整单退回交班方在途`
    await refresh()
  } catch (e) {
    msg.value = String(e.message || e)
  }
}
</script>

<template>
  <div>
    <h1>交班办理</h1>
    <p class="lead">
      发起后清单批次进入<strong>托管态</strong>：既不计入交班方在途，也不计入接班方在途，仅挂本交班单可查；
      托管期内禁止结清、改归属、再次交班。完成则出托管过户接班方，取消/失败则整单退回交班方。
    </p>

    <div class="panel">
      <div class="row">
        <label>交班方
          <select v-model.number="fromId">
            <option v-for="s in shifts" :key="s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <label>接班方
          <select v-model.number="toId">
            <option v-for="s in shifts" :key="'t'+s.id" :value="s.id">{{ s.name }} · {{ s.keeper }}</option>
          </select>
        </label>
        <button class="btn" :disabled="!!current" @click="start">发起交班（进入托管）</button>
        <button class="btn ghost" :disabled="!current" @click="exitVia('complete', '已完成：托管批次出托管并过户接班方', '')">完成交班</button>
        <button class="btn ghost" :disabled="!current" @click="exitVia('cancel', '', '已取消')">取消交班</button>
        <button class="btn danger" :disabled="!current" @click="exitVia('fail', '', '已失败')">标记失败</button>
      </div>
      <p class="msg" :class="{ ok }">{{ msg }}</p>
    </div>

    <div class="panel" v-if="current">
      <h2>当前交班单 #{{ current.id }} · {{ current.status }}</h2>
      <p class="lead" style="margin-bottom:12px">
        {{ current.from_shift_name }} → {{ current.to_shift_name }} · 清单 {{ current.batch_count }} 批 ·
        托管中 {{ current.escrow_count }} 批
      </p>
      <table>
        <thead><tr><th>业务键</th><th>标题</th><th>批次状态</th><th>托管于</th><th>进入托管时间</th></tr></thead>
        <tbody>
          <tr v-for="b in current.items || []" :key="b.id">
            <td>{{ b.biz_key }}</td>
            <td>{{ b.title }}</td>
            <td>
              <span class="tag" :class="{ escrow: b.status === '托管中' }">{{ b.status }}</span>
            </td>
            <td>{{ b.escrow_handover_id ? `交班单 #${b.escrow_handover_id}` : '—' }}</td>
            <td>{{ b.escrowed_at || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <p class="lead" style="margin:12px 0 0">
        托管集合物理隔离于在途集合，独立入口 <code>GET /api/handovers/escrow?handover_id={{ current.id }}</code>
      </p>
    </div>

    <div class="panel" v-else-if="escrowRows.length">
      <h2>托管中的批次（其他交班单）</h2>
      <table>
        <thead><tr><th>交班单</th><th>批次</th><th>标题</th><th>交班方</th><th>接班方</th></tr></thead>
        <tbody>
          <tr v-for="e in escrowRows" :key="`${e.handover_id}-${e.batch_id}`">
            <td>#{{ e.handover_id }}</td>
            <td>{{ e.biz_key }}</td>
            <td>{{ e.title }}</td>
            <td>{{ e.from_shift_name }}</td>
            <td>{{ e.to_shift_name }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
