<script setup>
import { onMounted, ref } from 'vue'
import { getJson } from '../api'

const rows = ref([])
const err = ref('')

onMounted(async () => {
  try {
    rows.value = await getJson('/api/reconcile')
  } catch (e) {
    err.value = String(e.message || e)
  }
})
</script>

<template>
  <div>
    <h1>交班对账</h1>
    <p class="lead">
      每个交班单两笔同源报告：<strong>发起</strong>时在途减少、批次入托管；<strong>出口</strong>时完成则接班方增加、
      取消/失败则交班方恢复。数字与托管集合、在途集合、交班单同一事务写入。
    </p>
    <p v-if="err" class="msg">{{ err }}</p>
    <div class="panel">
      <table>
        <thead>
          <tr>
            <th>报告</th><th>交班单</th><th>时点</th>
            <th>清单/发起在途</th><th>托管数</th>
            <th>交班方在途</th><th>接班方在途</th><th>一致</th><th>解释</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id">
            <td>#{{ r.id }}</td>
            <td>#{{ r.handover_id }}</td>
            <td>{{ r.exit_type || '—' }}</td>
            <td>{{ r.before_count }}</td>
            <td>{{ r.escrow_count }}</td>
            <td>{{ r.after_from_count }}</td>
            <td>{{ r.after_to_count }}</td>
            <td>
              <span class="tag" :class="{ on: r.一致 }">{{ r.一致 ? '一致' : '不一致' }}</span>
            </td>
            <td class="muted-cell">{{ r.detail['解释'] || '—' }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
