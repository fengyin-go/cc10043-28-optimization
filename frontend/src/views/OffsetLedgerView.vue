<template>
  <section class="page" data-module="offset_commit_ledger">
    <header class="page-head">
      <div>
        <h2>偏移量提交台账</h2>
        <p class="page-desc">
          提交记录与分区进度同库入链：重复提交归档到首次回执，签收按推进链顺序生效，
          可见提交不跳过尚未交接的进度。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn" data-testid="refresh-ledger" type="button" @click="reload">刷新台账</button>
      </div>
    </header>

    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">分区推进链</span>
        <strong class="stat-value">{{ progressRows.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">提交回执总数</span>
        <strong class="stat-value">{{ receipts.length }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">待签收提交</span>
        <strong class="stat-value">{{ pendingCount }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">重复归档次数</span>
        <strong class="stat-value">{{ archivedCount }}</strong>
      </article>
    </div>

    <form class="filter-bar ledger-form" @submit.prevent="submit">
      <label class="filter-item">
        <span>消费组</span>
        <input v-model="form.group" data-testid="ledger-group" required placeholder="如 cg-demo" />
      </label>
      <label class="filter-item">
        <span>Topic/Queue</span>
        <input v-model="form.topic" data-testid="ledger-topic" required placeholder="如 orders" />
      </label>
      <label class="filter-item">
        <span>分区</span>
        <input v-model.number="form.partition" data-testid="ledger-partition" type="number" min="0" required />
      </label>
      <label class="filter-item">
        <span>偏移量</span>
        <input v-model.number="form.offset" data-testid="ledger-offset" type="number" min="0" required />
      </label>
      <label class="filter-item">
        <span>来源</span>
        <input v-model="form.source" data-testid="ledger-source" required placeholder="如 producer-a" />
      </label>
      <button class="btn primary" data-testid="ledger-submit" type="submit" :disabled="busy">
        提交入链
      </button>
      <button class="btn" data-testid="ledger-ack" type="button" :disabled="busy" @click="acknowledge">
        进度签收
      </button>
      <button class="btn ghost" data-testid="ledger-lookup" type="button" :disabled="busy" @click="lookup">
        重新进入查询
      </button>
    </form>

    <p v-if="message" class="ledger-message" :class="{ 'error-text': !lastOk }">{{ message }}</p>

    <h3 class="ledger-sub">分区消费位置</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>消费组</th>
          <th>Topic/Queue</th>
          <th>分区</th>
          <th>已收偏移</th>
          <th>可见偏移</th>
          <th>下一提交编号</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in progressRows" :key="row.id" :data-testid="`progress-${row.id}`">
          <td>{{ row.消费组 }}</td>
          <td>{{ row['Topic/Queue'] }}</td>
          <td>{{ row['分区/分片'] }}</td>
          <td>{{ row.received_offset }}</td>
          <td>{{ row.visible_offset }}</td>
          <td>{{ row.next_seq }}</td>
        </tr>
        <tr v-if="!progressRows.length">
          <td colspan="6" class="empty-state">暂无分区进度，先提交一次偏移量</td>
        </tr>
      </tbody>
    </table>

    <h3 class="ledger-sub">提交回执与归档留痕</h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>回执</th>
          <th>分区</th>
          <th>提交编号</th>
          <th>偏移量</th>
          <th>来源</th>
          <th>状态</th>
          <th>可见</th>
          <th>重复归档</th>
          <th>幂等键</th>
        </tr>
      </thead>
      <tbody>
        <tr
          v-for="receipt in receipts"
          :key="receipt.id"
          :data-testid="`receipt-${receipt.id}`"
          :data-status="receipt.status"
        >
          <td>#{{ receipt.id }}</td>
          <td>{{ receipt['分区/分片'] }}</td>
          <td>{{ receipt.seq }}</td>
          <td>{{ receipt.offset }}</td>
          <td>{{ receipt.source }}</td>
          <td>{{ receipt.status }}</td>
          <td>{{ receipt.visible ? '是' : '否' }}</td>
          <td>{{ receipt.duplicate_count }}</td>
          <td class="ledger-key">{{ receipt.idempotency_key }}</td>
        </tr>
        <tr v-if="!receipts.length">
          <td colspan="9" class="empty-state">暂无提交回执</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>同偏移不同来源各有归属；同一确认重复送达只认第一次并归档</span>
      <span v-if="message" :class="lastOk ? '' : 'error-text'">{{ message }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import {
  acknowledgeCommit,
  fetchProgress,
  fetchReceipts,
  lookupCommit,
  submitCommit,
  type CommitPayload,
  type CommitReceipt,
  type PartitionProgress,
} from '@/api/offset-commits'

const form = ref<CommitPayload>({ group: '', topic: '', partition: 0, offset: 0, source: '' })
const progressRows = ref<PartitionProgress[]>([])
const receipts = ref<CommitReceipt[]>([])
const message = ref('')
const lastOk = ref(true)
const busy = ref(false)

const pendingCount = computed(
  () => receipts.value.filter((row) => row.status === '待签收').length,
)
const archivedCount = computed(() =>
  receipts.value.reduce((sum, row) => sum + row.duplicate_count, 0),
)

function payload(): CommitPayload {
  return {
    group: form.value.group.trim(),
    topic: form.value.topic.trim(),
    partition: Number(form.value.partition),
    offset: Number(form.value.offset),
    source: form.value.source.trim(),
  }
}

async function reload() {
  try {
    const [progress, rows] = await Promise.all([fetchProgress(), fetchReceipts()])
    progressRows.value = progress
    receipts.value = rows
  } catch (error) {
    message.value = error instanceof Error ? error.message : '台账读取失败'
    lastOk.value = false
  }
}

async function run(action: () => Promise<{ ok: boolean; message: string }>) {
  busy.value = true
  message.value = ''
  try {
    const result = await action()
    message.value = result.message
    lastOk.value = result.ok
    await reload()
  } catch (error) {
    message.value = error instanceof Error ? error.message : '操作失败'
    lastOk.value = false
  } finally {
    busy.value = false
  }
}

function submit() {
  void run(() => submitCommit(payload()))
}

function acknowledge() {
  void run(() => acknowledgeCommit(payload()))
}

function lookup() {
  void run(() => lookupCommit(payload()))
}

onMounted(reload)
</script>

<style scoped>
.ledger-form {
  flex-wrap: wrap;
  align-items: flex-end;
}
.ledger-sub {
  margin: 20px 0 8px;
  font-size: 15px;
}
.ledger-message {
  margin: 10px 0;
  padding: 8px 12px;
  background: #eef6ff;
  border-left: 3px solid #2f6fed;
  border-radius: 4px;
}
.ledger-key {
  font-family: ui-monospace, monospace;
  font-size: 12px;
  color: #555;
}
</style>
