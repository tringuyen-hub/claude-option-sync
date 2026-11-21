import { atom, read, update } from 'claude-code'
import type { EngineInterface, Register } from 'claude-code'

import type { AnswerView, QuestionView, RequestView } from '../types'
import { RUN_DIR } from './config'

const PANE = 'claude-option'
const LETTERS = 'ABCDEFGHIJ'
const ENDPOINT_FILE = `${RUN_DIR}/endpoint.json`
const TOKEN_FILE = `${RUN_DIR}/api.token`
const LONG_POLL_SECONDS = 15
const KEEP_RESOLVED = 3

// Change a tag when the stored value's fields change: older values are then read as the initial value.
const requests = atom({ plugin: 'question-sync', key: 'requests' } as const, [] as RequestView[], {
  shape: 'requests-1',
})
const drafts = atom({ plugin: 'question-sync', key: 'drafts' } as const, {} as Record<string, string[]>, {
  shape: 'drafts-1',
})

type $T = EngineInterface
type BrokerReply = { status: number; data: any }

// ------------------------------------------------------------------ broker bridge
// The broker writes endpoint.json: a Unix socket on Linux/macOS, 127.0.0.1:<port> on Windows.

type Endpoint = { base: string; socketPath?: string }
let cachedToken: string | undefined
let cachedEndpoint: Endpoint | undefined

async function endpoint($: $T): Promise<Endpoint> {
  if (cachedEndpoint === undefined) {
    const e = JSON.parse(String(await $.fs.read(ENDPOINT_FILE)))
    cachedEndpoint =
      e.transport === 'unix'
        ? { base: 'http://broker', socketPath: String(e.socket) }
        : { base: `http://127.0.0.1:${Number(e.port)}` }
  }
  return cachedEndpoint
}

async function broker(
  $: $T,
  method: 'GET' | 'POST',
  path: string,
  body?: unknown,
  retried = false,
): Promise<BrokerReply> {
  if (cachedToken === undefined) cachedToken = String(await $.fs.read(TOKEN_FILE)).trim()
  const ep = await endpoint($)
  let res
  try {
    res = await $.http.fetch(`${ep.base}${path}`, {
      method,
      socketPath: ep.socketPath,
      headers: { Authorization: `Bearer ${cachedToken}`, 'Content-Type': 'application/json' },
      body: body === undefined ? undefined : JSON.stringify(body),
    })
  } catch (err) {
    cachedEndpoint = undefined // broker restarted: re-read its port or socket and try once more
    cachedToken = undefined
    if (!retried) return broker($, method, path, body, true)
    throw err
  }
  if (res.status === 401) cachedToken = undefined // token rotated by a broker restart
  let data: any = {}
  try {
    data = JSON.parse(res.text)
  } catch {
    data = { error: res.text }
  }
  return { status: res.status, data }
}

function toView(s: any, site: RequestView['site']): RequestView {
  return {
    requestId: s.request_id,
    toolUseId: s.tool_use_id,
    status: s.status,
    statusReason: s.status_reason ?? undefined,
    version: s.version,
    projectName: s.project_name ?? undefined,
    expiresAt: s.expires_at,
    site,
    questions: (s.questions as any[]).map(
      (q): QuestionView => ({
        questionId: q.question_id,
        position: q.position,
        question: q.question,
        header: q.header ?? undefined,
        multiSelect: q.multi_select,
        status: q.status,
        options: (q.options as any[]).map(o => ({
          optionId: o.option_id,
          label: o.label,
          description: o.description ?? undefined,
          position: o.position,
        })),
        answer: q.answer
          ? ({
              optionIds: q.answer.option_ids,
              labels: q.answer.labels,
              text: q.answer.text,
              isCustom: q.answer.is_custom,
              source: q.answer.source,
            } as AnswerView)
          : undefined,
      }),
    ),
  }
}

async function upsert($: $T, s: any, site?: RequestView['site'], notice?: string) {
  await update($, requests, list => {
    const old = list.find(r => r.requestId === s.request_id)
    if (old && old.version > s.version) return list // never go backwards
    const view = { ...toView(s, site ?? old?.site ?? 'placed'), notice }
    const rest = list.filter(r => r.requestId !== s.request_id)
    const pending = [...rest, view].filter(r => r.status === 'PENDING')
    const resolved = [...rest, view].filter(r => r.status !== 'PENDING').slice(-KEEP_RESOLVED)
    return [...resolved, ...pending]
  })
}

async function setNotice($: $T, requestId: string, notice: string | undefined) {
  await update($, requests, list => list.map(r => (r.requestId === requestId ? { ...r, notice } : r)))
}

// ------------------------------------------------------------------ selection from VS Code

async function submit($: $T, req: RequestView, q: QuestionView, optionIds: string[], customText?: string) {
  try {
    const r = await broker($, 'POST', '/v1/select', {
      question_id: q.questionId,
      option_ids: optionIds,
      custom_text: customText,
      source: 'vscode',
    })
    if (r.status !== 200) {
      await setNotice($, req.requestId, `Not accepted: ${r.data?.error ?? r.status}`)
      return
    }
    await upsert($, r.data.state)
    if (!r.data.won) {
      const won = (r.data.state.questions as any[]).find(x => x.question_id === q.questionId)?.answer
      await setNotice(
        $,
        req.requestId,
        won ? `Already answered ${sourceText(won.source)}: ${won.text}` : `Not accepted: ${r.data.reason}`,
      )
    }
  } catch (err) {
    await setNotice($, req.requestId, `Broker unreachable: ${String(err).slice(0, 120)}`)
  }
}

function sourceText(source: string) {
  return source === 'telegram' ? 'through Telegram' : source === 'vscode' ? 'in VS Code' : 'in Claude Code'
}

function clock(epochSeconds: number) {
  const d = new Date(epochSeconds * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

// ------------------------------------------------------------------ drawing

function card($: $T, e: any, req: RequestView, draftMap: Record<string, string[]>) {
  const { Box, Text, Button, Input } = $.ui.resolve(e) as any
  const open = req.status === 'PENDING'
  return (
    <Box key={`req-${req.requestId}`} flexDirection="column" borderStyle="round" paddingX={1}>
      <Text>
        <Text bold>{open ? '❓ Claude needs your input' : 'Claude asked'}</Text>
        <Text dimColor>
          {'  '}
          {req.projectName ? `${req.projectName} · ` : ''}request {req.requestId}
        </Text>
      </Text>
      {req.questions.map(q => question($, e, req, q, draftMap[q.questionId] ?? []))}
      {req.notice ? <Text color="warning">{req.notice}</Text> : null}
      {open ? (
        <Text dimColor>
          Also on Telegram. The first answer wins. Expires {clock(req.expiresAt)}.
        </Text>
      ) : req.status === 'EXPIRED' ? (
        <Text color="warning">⌛ Expired: no option was selected.</Text>
      ) : req.status === 'CANCELLED' ? (
        <Text color="warning">✖ Cancelled{req.statusReason ? `: ${req.statusReason}` : ''}.</Text>
      ) : null}
    </Box>
  )
}

function question($: $T, e: any, r: RequestView, q: QuestionView, draft: string[]) {
  const { Box, Text, Button, Input } = $.ui.resolve(e) as any
  const pending = q.status === 'PENDING' && r.status === 'PENDING'
  const chosen = new Set(q.answer?.optionIds ?? [])
  const head = (
    <Text>
      {q.header ? <Text color="suggestion">[{q.header}] </Text> : null}
      <Text bold>{q.question}</Text>
      {q.multiSelect ? <Text dimColor> (choose any)</Text> : null}
    </Text>
  )
  if (!pending) {
    return (
      <Box key={`q-${q.questionId}`} flexDirection="column" marginTop={1}>
        {head}
        {q.options.map(o =>
          chosen.has(o.optionId) ? (
            <Text key={`o-${o.optionId}`} color="success" bold>
              ✓ {LETTERS[o.position]}. {o.label}
            </Text>
          ) : (
            <Text key={`o-${o.optionId}`} dimColor strikethrough={q.status === 'ANSWERED'}>
              {'  '}
              {LETTERS[o.position]}. {o.label}
            </Text>
          ),
        )}
        {q.answer ? (
          <Text color="success">
            Selected {sourceText(q.answer.source)}:{' '}
            {q.answer.isCustom
              ? q.answer.text
              : q.answer.optionIds
                  .map((id, i) => `${LETTERS[q.options.find(o => o.optionId === id)?.position ?? 0]}. ${q.answer!.labels[i]}`)
                  .join(', ')}
          </Text>
        ) : null}
      </Box>
    )
  }
  return (
    <Box key={`q-${q.questionId}`} flexDirection="column" marginTop={1}>
      {head}
      {q.options.map(o => {
        const on = draft.includes(o.optionId)
        return (
          <Button
            key={`${q.questionId}|${o.optionId}`}
            plain
            onPress={() =>
              q.multiSelect
                ? update($, drafts, m => ({
                    ...m,
                    [q.questionId]: on
                      ? (m[q.questionId] ?? []).filter(x => x !== o.optionId)
                      : [...(m[q.questionId] ?? []), o.optionId],
                  }))
                : submit($, r, q, [o.optionId])
            }
          >
            {q.multiSelect ? (on ? '☑ ' : '☐ ') : ''}
            {LETTERS[o.position]}. {o.label}
            {o.description ? <Text dimColor> — {o.description}</Text> : null}
          </Button>
        )
      })}
      {q.multiSelect ? (
        <Button
          key={`${q.questionId}|submit`}
          variant="primary"
          onPress={() => (draft.length ? submit($, r, q, draft) : setNotice($, r.requestId, 'Select at least one option first.'))}
        >
          Submit selection ({draft.length})
        </Button>
      ) : null}
      <Input
        key={`${q.questionId}|other`}
        placeholder="Or type your own answer…"
        submitLabel="Send"
        onSubmit={(value: string) => (value.trim() ? submit($, r, q, [], value.trim()) : undefined)}
      />
    </Box>
  )
}

// ------------------------------------------------------------------ hooks

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    await $.command.register({
      name: 'claude-option',
      description: 'Show synchronized questions (VS Code + Telegram) in a pane',
    })
    return next(e)
  })

  on('command.run', { command: 'claude-option' }, async $ => {
    await $.ui.open({ id: PANE, title: 'Claude Option' })
    return { text: 'Claude Option pane opened.' }
  })

  on('tool.call', { tool: 'AskUserQuestion' }, async ($, e, next) => {
    if (e.answers && Object.keys(e.answers).length > 0) return next(e)

    let state: any
    try {
      const sessionId = await $.session.id()
      const root = await $.session.root()
      const r = await broker($, 'POST', '/v1/questions/ensure', {
        session_id: sessionId,
        tool_use_id: e.tool_use_id,
        questions: e.questions,
        project_root: root,
        agent_id: (e as any).agentId,
      })
      if (r.status !== 200) {
        $.ui.log(`claude-option: not mirrored (${r.data?.error ?? r.status}); using the native dialog`)
        return next(e)
      }
      state = r.data
    } catch (err) {
      $.ui.log(`claude-option: broker unreachable; using the native dialog (${String(err).slice(0, 120)})`)
      return next(e)
    }

    // Show it in VS Code: a pane if the surface seats one now, else an inline row in the transcript.
    const opened = await $.ui.open({ id: PANE, title: 'Claude Option' })
    const site: RequestView['site'] = opened.isPlaced ? 'pane' : 'placed'
    await upsert($, state, site)
    if (!opened.isPlaced) {
      await $.ui.close({ id: PANE })
      await $.ui.place({ id: `req-${state.request_id}` })
    }
    $.ui.status(`Question ${state.request_id} waiting (VS Code + Telegram)`)

    const requestId: string = state.request_id
    const onAbort = () => {
      void broker($, 'POST', `/v1/requests/${requestId}/cancel`, { reason: 'interrupted in Claude Code', source: 'vscode' })
        .then(r => (r.status === 200 ? upsert($, r.data) : undefined))
        .catch(() => undefined)
    }
    next.signal.addEventListener('abort', onAbort, { once: true })

    try {
      while (state.status === 'PENDING' && !next.signal.aborted) {
        const r = await broker(
          $,
          'GET',
          `/v1/requests/${requestId}/wait?since=${state.version}&timeout=${LONG_POLL_SECONDS}`,
        )
        if (r.status !== 200) throw new Error(`wait failed: ${r.data?.error ?? r.status}`)
        state = r.data
        await upsert($, state)
      }
    } catch (err) {
      $.ui.status(undefined)
      await setNotice($, requestId, 'Broker connection lost: answer in the native dialog instead.')
      $.ui.log(`claude-option: lost the broker while waiting; falling back to the native dialog (${String(err).slice(0, 120)})`)
      return next(e)
    } finally {
      next.signal.removeEventListener('abort', onAbort)
    }
    $.ui.status(undefined)

    if (state.status === 'ANSWERED') {
      const viaTelegram = (state.questions as any[]).filter(q => q.answer?.source === 'telegram')
      if (viaTelegram.length) {
        $.ui.toast(`Selected through Telegram: ${viaTelegram.map(q => q.answer.text).join(' · ')}`)
      }
      // The PreToolUse hook beneath finds the same request and confirms these answers.
      return next({ ...e, answers: state.answers })
    }
    if (state.status === 'EXPIRED') {
      return {
        deny:
          `The user did not select an option before the question expired (request ${requestId}). ` +
          'No answer was chosen; do not assume one. Ask again if the decision is still needed.',
      }
    }
    return {
      deny: `The question was cancelled (${state.status_reason ?? 'cancelled'}) before the user answered. No answer was chosen.`,
    }
  }).catch(($, e, next) => next(e)) // any failure: the native dialog still works

  on('session.end', async ($, e, next) => {
    try {
      const sessionId = await $.session.id()
      await broker($, 'POST', `/v1/sessions/${sessionId}/cancel`, { reason: 'Claude Code session ended' })
    } catch {
      // broker down: its own expiry closes the request
    }
    return next(e)
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text } = $.ui.resolve(e) as any
    const list = await read($, requests)
    const draftMap = await read($, drafts)
    const shown = [...list].reverse()
    return (
      <Box flexDirection="column">
        {shown.length === 0 ? <Text dimColor>No questions yet.</Text> : null}
        {shown.map(r => card($, e, r, draftMap))}
      </Box>
    )
  })

  on('ui.render', { component: 'Placed' }, async ($, e, next) => {
    if (e.props.plugin !== $.plugin.name || !e.props.id.startsWith('req-')) return next(e)
    const list = await read($, requests)
    const req = list.find(r => `req-${r.requestId}` === e.props.id)
    const { Text } = $.ui.resolve(e) as any
    if (!req) return <Text dimColor>Claude Option: question closed.</Text>
    return card($, e, req, await read($, drafts))
  })
}
