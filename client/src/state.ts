// Lógica pura (sem DOM) para facilitar testes com `node --test`.
import type { ServerState } from "./protocol.js";

export type Mood =
  | "booting"
  | "idle"
  | "listening"
  | "processing"
  | "speaking"
  | "media"
  | "offline"
  | "error";

export const MOODS: readonly Mood[] = [
  "booting",
  "idle",
  "listening",
  "processing",
  "speaking",
  "media",
  "offline",
  "error",
];

const MOOD_BY_STATE: Record<ServerState, Mood> = {
  booting: "booting",
  idle: "idle",
  wake: "listening",
  listening: "listening",
  transcribing: "processing",
  thinking: "processing",
  replying: "processing",
  synthesizing: "processing",
  speaking: "speaking",
  media: "media",
  offline: "offline",
  error: "error",
};

export function moodFor(state: ServerState): Mood {
  return MOOD_BY_STATE[state] ?? "error";
}

const MOOD_LABELS: Record<Mood, string> = {
  booting: "Inicializando",
  idle: "Aguardando",
  listening: "Escutando",
  processing: "Pensando",
  speaking: "Falando",
  media: "Reproduzindo mídia",
  offline: "Offline",
  error: "Erro",
};

/** Nome do estado em pt-BR para o indicador visual. Enquanto a IA responde, "Respondendo";
 *  enquanto a voz é gerada, "Preparando". */
export function moodLabel(mood: Mood, state: ServerState | null = null): string {
  if (state === "replying" && mood === "processing") return "Respondendo";
  if (state === "synthesizing" && mood === "processing") return "Preparando";
  return MOOD_LABELS[mood];
}

/**
 * Rosto a exibir considerando a conexão. Uma queda breve (< graceMs) não
 * muda o rosto, para evitar piscar durante reconexões rápidas.
 */
export function displayMood(
  state: ServerState | null,
  connected: boolean,
  disconnectedForMs: number,
  graceMs = 1500,
): Mood {
  if (!connected && (state === null || disconnectedForMs >= graceMs)) return "offline";
  if (state === null) return "booting";
  return moodFor(state);
}

/** Estado do microfone no cliente. */
export type MicState = "off" | "starting" | "on" | "blocked" | "denied" | "unavailable" | "insecure";

const MIC_LABELS: Record<MicState, string> = {
  off: "Microfone desativado",
  starting: "Microfone iniciando…",
  on: "Microfone ativo",
  blocked: "Microfone aguardando um toque na tela",
  denied: "Microfone indisponível: permissão negada",
  unavailable: "Microfone indisponível",
  insecure: "Microfone indisponível: a página precisa de HTTPS ou localhost",
};

export function micLabel(state: MicState, message = ""): string {
  const base = MIC_LABELS[state];
  return message ? `${base} · ${message}` : base;
}

export interface WakeInfo {
  available: boolean;
  reason: string;
}

/** Texto principal em espera: explica por que a ativação por voz não funcionaria. */
export function idleStatus(
  mic: { state: MicState; message: string },
  wake: WakeInfo,
  phrase: string,
  transcription: WakeInfo = { available: true, reason: "" },
  llm: WakeInfo = { available: true, reason: "" },
  tts: WakeInfo = { available: true, reason: "" },
): string {
  if (mic.state === "off") return "Microfone desativado";
  if (mic.state !== "on" && mic.state !== "starting") return micLabel(mic.state, mic.message);
  if (!wake.available) return `Detecção de voz indisponível${wake.reason ? ` · ${wake.reason}` : ""}`;
  const base = `Aguardando ativação · diga “${phrase}”`;
  if (!transcription.available) return `${base} · transcrição indisponível`;
  if (!llm.available) return `${base} · IA indisponível`;
  return tts.available ? base : `${base} · voz indisponível`;
}

/** Estados em que há uma interação em andamento que pode ser cancelada (inclui a fala). */
export function canCancel(state: ServerState | null): boolean {
  return (
    state === "wake" ||
    state === "listening" ||
    state === "transcribing" ||
    state === "thinking" ||
    state === "replying" ||
    state === "synthesizing" ||
    state === "speaking"
  );
}

/** "Erro de áudio": microfone sem acesso ou falha na transcrição (rosto vermelho em espera). */
export function audioError(mic: MicState, detail = ""): boolean {
  return mic === "denied" || mic === "unavailable" || mic === "insecure" || detail === "Falha na transcrição";
}

export function statusText(state: ServerState | null, wakeWord: string, detail = ""): string {
  if (detail) return detail;
  switch (state) {
    case null:
    case "booting":
      return "Inicializando…";
    case "idle":
      return `Aguardando ativação · diga “${wakeWord}”`;
    case "wake":
      return "Palavra detectada";
    case "listening":
      return "Ouvindo · pode falar";
    case "transcribing":
      return "Transcrevendo…";
    case "thinking":
      return "Pensando…";
    case "replying":
      return "Respondendo…";
    case "synthesizing":
      return "Preparando a voz…";
    case "speaking":
      return "Falando";
    case "media":
      return "Reproduzindo mídia";
    case "offline":
      return "Servidor indisponível";
    case "error":
      return "Ocorreu um erro";
  }
}

/** Backoff exponencial com jitter. `rng` é injetável para testes. */
export class Backoff {
  private attempt = 0;

  constructor(
    private readonly baseMs = 1000,
    private readonly maxMs = 30000,
    private readonly jitter = 0.3,
    private readonly rng: () => number = Math.random,
  ) {}

  next(): number {
    const exp = Math.min(this.maxMs, this.baseMs * 2 ** this.attempt);
    this.attempt = Math.min(this.attempt + 1, 30);
    return Math.round(exp * (1 + this.jitter * this.rng()));
  }

  max(): number {
    return Math.round(this.maxMs * (1 + this.jitter * this.rng()));
  }

  reset(): void {
    this.attempt = 0;
  }

  get attempts(): number {
    return this.attempt;
  }
}

/** Lê o token do fragmento da URL (#token=…) ou do localStorage. */
export function resolveToken(hash: string, stored: string | null): { token: string | null; fromHash: boolean } {
  const m = /(?:^#|&)token=([^&]+)/.exec(hash);
  if (m) return { token: decodeURIComponent(m[1]), fromHash: true };
  return { token: stored && stored.length > 0 ? stored : null, fromHash: false };
}

/** Memória persistente: nomes dos tipos em pt-BR para a lista na tela. */
export const MEMORY_KIND_LABELS: Record<string, string> = {
  preference: "Preferência",
  kitchen: "Cozinha",
  ingredient: "Ingrediente",
  instruction: "Instrução",
  note: "Lembrete",
};

export function memoryKindLabel(kind: string): string {
  return MEMORY_KIND_LABELS[kind] ?? kind;
}

/** Aviso curto exibido quando a memória muda; "" quando não há o que avisar (lista, confirmação pendente). */
export function memoryToast(action: string, count = 0): string {
  switch (action) {
    case "saved":
      return "Memória salva";
    case "updated":
      return "Memória atualizada";
    case "exists":
      return "Isso já estava na memória";
    case "deleted":
      return "Memória apagada";
    case "cleared":
      return count === 1 ? "1 memória apagada" : `${count} memórias apagadas`;
    case "kept":
      return "Nada foi apagado";
    case "not_found":
      return "Nada parecido na memória";
    case "error":
      return "Memória indisponível";
    case "stock_saved":
      return "Ingredientes registrados";
    case "stock_updated":
      return "Ingredientes atualizados";
    case "stock_removed":
      return "Ingredientes removidos";
    default:
      return "";
  }
}

/** Rótulo do botão "Memória" com a contagem. */
export function memoryButtonLabel(count: number): string {
  return count > 0 ? `Memória · ${count}` : "Memória";
}
