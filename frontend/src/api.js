export async function getJson(url) {
  const res = await fetch(url)
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

export async function postJson(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  })
  if (!res.ok) throw new Error(await readError(res))
  return res.json()
}

async function readError(res) {
  const text = await res.text()
  try {
    const data = JSON.parse(text)
    if (data.detail) return String(data.detail)
  } catch {
    /* 非 JSON 响应，回退原文 */
  }
  return text
}
