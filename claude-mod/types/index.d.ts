export type OptionView = {
  optionId: string
  label: string
  description?: string
  position: number
}

export type AnswerView = {
  optionIds: string[]
  labels: string[]
  text: string
  isCustom: boolean
  source: 'telegram' | 'vscode' | 'hook'
}

export type QuestionView = {
  questionId: string
  position: number
  question: string
  header?: string
  multiSelect: boolean
  status: 'PENDING' | 'ANSWERED' | 'EXPIRED' | 'CANCELLED'
  options: OptionView[]
  answer?: AnswerView
}

export type RequestView = {
  requestId: string
  toolUseId: string
  status: 'PENDING' | 'ANSWERED' | 'EXPIRED' | 'CANCELLED'
  statusReason?: string
  version: number
  projectName?: string
  expiresAt: number
  questions: QuestionView[]
  /** Where this request is drawn in this session. */
  site: 'pane' | 'placed'
  /** Last error talking to the broker from this panel, if any. */
  notice?: string
}

declare module 'claude-code' {
  interface PluginState {
    'question-sync': {
      requests: Shaped<RequestView[]>
      drafts: Shaped<Record<string, string[]>>
    }
  }
}
