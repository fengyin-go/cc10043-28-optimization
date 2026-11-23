import { request } from './client'

/** 偏移量提交台账的接口封装：提交、签收、重新进入查询与推进链核对。 */

export type PartitionProgress = {
  id: number
  消费组: string
  'Topic/Queue': string
  '分区/分片': number
  received_offset: number
  visible_offset: number
  next_seq: number
  updated_at?: string
}

export type CommitReceipt = {
  id: number
  消费组: string
  'Topic/Queue': string
  '分区/分片': number
  seq: number
  offset: number
  source: string
  status: string
  acked: boolean
  duplicate_count: number
  duplicate_of: number | null
  visible: boolean
  idempotency_key: string
  created_at: string
  acked_at: string | null
  archived_attempts: { at: string; archived_to_receipt: number }[]
}

export type ChainSnapshot = PartitionProgress & {
  chain: CommitReceipt[]
  visible_advance_count: number
  acked_count: number
  pending_count: number
}

export type CommitPayload = {
  group: string
  topic: string
  partition: number
  offset: number
  source: string
}

export type CommitResult = {
  ok: boolean
  duplicate?: boolean
  effective?: boolean
  duplicate_ack?: boolean
  known?: boolean
  message: string
  receipt?: CommitReceipt
  progress?: PartitionProgress
}

async function post(path: string, payload: CommitPayload): Promise<CommitResult> {
  const response = await request(path, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
  return await response.json()
}

export function submitCommit(payload: CommitPayload): Promise<CommitResult> {
  return post('/api/offset-commits/submit', payload)
}

export function acknowledgeCommit(payload: CommitPayload): Promise<CommitResult> {
  return post('/api/offset-commits/acknowledge', payload)
}

export function lookupCommit(payload: CommitPayload): Promise<CommitResult> {
  return post('/api/offset-commits/lookup', payload)
}

export async function fetchProgress(group?: string): Promise<PartitionProgress[]> {
  const query = group ? `?group=${encodeURIComponent(group)}` : ''
  const response = await request(`/api/offset-commits/progress${query}`)
  const body = await response.json()
  return body.items as PartitionProgress[]
}

export async function fetchReceipts(group?: string): Promise<CommitReceipt[]> {
  const query = group ? `?group=${encodeURIComponent(group)}` : ''
  const response = await request(`/api/offset-commits/receipts${query}`)
  const body = await response.json()
  return body.items as CommitReceipt[]
}

export async function fetchChain(
  group: string,
  topic: string,
  partition: number,
): Promise<ChainSnapshot> {
  const query = new URLSearchParams({ group, topic, partition: String(partition) })
  const response = await request(`/api/offset-commits/chain?${query.toString()}`)
  return await response.json()
}
