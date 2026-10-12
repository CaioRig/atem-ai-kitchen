// Tipos das mensagens do WebSocket. Espelha docs/protocolo.md.

export type ServerState =
  | "booting"
  | "idle"
  | "wake"
  | "listening"
  | "transcribing"
  | "thinking"
  | "replying"
  | "synthesizing"
  | "speaking"
  | "media"
  | "offline"
  | "error";

export interface WakeStatus {
  available: boolean;
  reason: string;
  phrase: string;
  model: string;
}

export interface TranscriptionStatus {
  available: boolean;
  reason: string;
  model: string;
  language: string;
}

export interface LlmStatus {
  available: boolean;
  reason: string;
  model: string;
}

export interface TtsStatus {
  available: boolean;
  reason: string;
  engine: string;
  voice: string;
  sample_rate: number;
}

export interface MemoryStatus {
  available: boolean;
  reason: string;
  count: number;
}

export type MemoryKind = "preference" | "kitchen" | "ingredient" | "instruction" | "note";

export interface MemoryItem {
  id: number;
  kind: MemoryKind;
  text: string;
  created_at: number;
  updated_at: number;
}

export type MemoryAction =
  | "saved"
  | "updated"
  | "exists"
  | "deleted"
  | "cleared"
  | "kept"
  | "not_found"
  | "confirm_clear"
  | "list"
  | "error"
  /** Ingredientes disponíveis (Fase 7): registrados, quantidades atualizadas ou itens tirados da lista. */
  | "stock_saved"
  | "stock_updated"
  | "stock_removed";

/** Mudança ou consulta da memória persistente (por voz ou pela tela). `count` é o total após a ação. */
export interface MemoryMsg {
  type: "memory";
  action: MemoryAction;
  item?: MemoryItem;
  previous?: MemoryItem;
  items?: MemoryItem[];
  count?: number;
  topic?: string;
  message?: string;
  /** true quando a lista foi pedida por voz (a tela a mostra por alguns segundos). */
  spoken?: boolean;
}

export interface WelcomeMsg {
  type: "welcome";
  server: string;
  version: string;
  wake_word: string;
  wake?: WakeStatus;
  transcription?: TranscriptionStatus;
  llm?: LlmStatus;
  tts?: TtsStatus;
  memory?: MemoryStatus;
  debug: boolean;
  state: ServerState;
  detail: string;
  ts: number;
}

export interface StateMsg {
  type: "state";
  state: ServerState;
  detail: string;
  ts: number;
}

export interface PongMsg {
  type: "pong";
  t: number | null;
}

export interface ErrorMsg {
  type: "error";
  code: string;
  message: string;
}

export interface TranscriptMsg {
  type: "transcript";
  text: string;
  final: boolean;
}

export interface ReplyMsg {
  type: "reply";
  text: string;
  /** false enquanto a resposta ainda está sendo gerada (texto parcial). */
  final?: boolean;
}

export interface ReplyErrorMsg {
  type: "reply_error";
  code: string;
  message: string;
  retry: boolean;
}

/** Início de uma fala da Luna; em seguida chegam quadros binários PCM int16 LE mono (um por frase). */
export interface SpeakStartMsg {
  type: "speak_start";
  id: number;
  sample_rate: number;
  channels: number;
  format: string;
}

/** Último trecho da fala `id` já foi enviado. */
export interface SpeakEndMsg {
  type: "speak_end";
  id: number;
  samples: number;
}

/** Parar a reprodução da fala `id` imediatamente (interrupção, cancelamento ou falha). */
export interface SpeakStopMsg {
  type: "speak_stop";
  id: number;
}

export interface SpeakErrorMsg {
  type: "speak_error";
  code: string;
  message: string;
  retry: boolean;
}

export type ServerMsg =
  | WelcomeMsg
  | StateMsg
  | PongMsg
  | ErrorMsg
  | TranscriptMsg
  | ReplyMsg
  | ReplyErrorMsg
  | SpeakStartMsg
  | SpeakEndMsg
  | SpeakStopMsg
  | SpeakErrorMsg
  | MemoryMsg;

export const PROTOCOL_VERSION = 1;
