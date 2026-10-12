// Captura do microfone: getUserMedia + AudioWorklet -> blocos PCM int16 mono 16 kHz.
// Nada é gravado: cada bloco é entregue ao callback e descartado. O áudio só sai
// desta máquina pelo WebSocket da rede local, e somente quando `frame` o enviar.
import type { MicState } from "./state.js";

export interface MicHandlers {
  /** Bloco de 1280 amostras (80 ms) PCM int16 LE, pronto para o servidor. */
  frame?: (pcm: ArrayBuffer) => void;
  /** Nível RMS 0..1 do bloco, para animar o rosto. */
  level?: (rms: number) => void;
  state?: (state: MicState, message: string) => void;
}

const WORKLET_URL = "audio/capture-processor.js";
const PROCESSOR = "atem-capture";
const TARGET_RATE = 16000;

// Processamento do navegador aplicado ao microfone.
// - echoCancellation desligado (decisão mantida na fase 5, ver docs/voz.md): o cancelador de eco do Chrome
//   pode atenuar o início das frases; o detector só reage a "hey Luna". Religue se a voz da Luna gerar
//   falsos positivos no LG, ou desligue tts.barge_in.
// - noiseSuppression e autoGainControl ligados: nível estável para o detector e para o fim da fala no servidor.
const MIC_PROCESSING = { echoCancellation: false, noiseSuppression: true, autoGainControl: true };

interface WorkletMessage {
  pcm?: ArrayBuffer;
  rms: number;
}

export class MicCapture {
  state: MicState = "off";
  private wanted = false;
  private ctx: AudioContext | null = null;
  private stream: MediaStream | null = null;
  private node: AudioWorkletNode | null = null;

  constructor(private readonly handlers: MicHandlers) {}

  /** Verdadeiro quando o usuário quer o microfone ligado (mesmo se ainda iniciando/bloqueado). */
  get active(): boolean {
    return this.wanted;
  }

  async start(): Promise<void> {
    if (this.wanted) return;
    this.wanted = true;
    this.set("starting", "");
    if (!window.isSecureContext) return this.fail("insecure", "");
    if (!navigator.mediaDevices?.getUserMedia) return this.fail("unavailable", "navegador sem suporte a captura de áudio");

    let stream: MediaStream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, sampleRate: TARGET_RATE, ...MIC_PROCESSING },
      });
    } catch (err) {
      const [state, message] = describeError(err);
      return this.fail(state, message);
    }
    if (!this.wanted) {
      stopTracks(stream);
      return;
    }
    try {
      const ctx = createContext();
      await ctx.audioWorklet.addModule(WORKLET_URL);
      const node = new AudioWorkletNode(ctx, PROCESSOR, { numberOfInputs: 1, numberOfOutputs: 1, outputChannelCount: [1] });
      node.port.onmessage = (ev: MessageEvent<WorkletMessage>) => {
        this.handlers.level?.(ev.data.rms);
        if (ev.data.pcm) this.handlers.frame?.(ev.data.pcm);
      };
      const mute = ctx.createGain();
      mute.gain.value = 0; // o worklet precisa estar ligado ao destino para processar; nada é reproduzido
      ctx.createMediaStreamSource(stream).connect(node).connect(mute).connect(ctx.destination);
      this.ctx = ctx;
      this.stream = stream;
      this.node = node;
      const track = stream.getAudioTracks()[0];
      track?.addEventListener("ended", () => {
        if (!this.wanted) return;
        void this.release();
        this.wanted = false;
        this.set("unavailable", "microfone desconectado");
      });
      logCapture(track, ctx);
      await this.ensureRunning(track?.label ?? "");
    } catch (err) {
      stopTracks(stream);
      return this.fail("unavailable", err instanceof Error ? err.message : String(err));
    }
  }

  async stop(): Promise<void> {
    this.wanted = false;
    await this.release();
    this.set("off", "");
  }

  private async ensureRunning(label: string): Promise<void> {
    const ctx = this.ctx;
    if (!ctx) return;
    try {
      await ctx.resume();
    } catch {
      /* tratado abaixo pelo estado do contexto */
    }
    if (ctx.state === "running") {
      this.set("on", label);
      return;
    }
    // Política de autoplay: o contexto só roda após um gesto do usuário.
    this.set("blocked", "");
    const unlock = async () => {
      document.removeEventListener("pointerdown", unlock);
      document.removeEventListener("keydown", unlock);
      if (!this.wanted || this.ctx !== ctx) return;
      await ctx.resume().catch(() => undefined);
      if (ctx.state === "running") this.set("on", label);
    };
    document.addEventListener("pointerdown", unlock);
    document.addEventListener("keydown", unlock);
  }

  private async release(): Promise<void> {
    this.node?.disconnect();
    this.node = null;
    if (this.stream) stopTracks(this.stream);
    this.stream = null;
    const ctx = this.ctx;
    this.ctx = null;
    if (ctx && ctx.state !== "closed") await ctx.close().catch(() => undefined);
  }

  private async fail(state: MicState, message: string): Promise<void> {
    this.wanted = false;
    await this.release();
    this.set(state, message);
  }

  private set(state: MicState, message: string): void {
    this.state = state;
    this.handlers.state?.(state, message);
  }
}

function createContext(): AudioContext {
  try {
    return new AudioContext({ sampleRate: TARGET_RATE });
  } catch {
    return new AudioContext();
  }
}

/** Registra no console qual microfone e qual formato estão em uso (para conferir na cozinha, tecla F12). */
function logCapture(track: MediaStreamTrack | undefined, ctx: AudioContext): void {
  const s = track?.getSettings() ?? {};
  console.info(
    `microfone: "${track?.label ?? "?"}" · dispositivo ${s.sampleRate ?? "?"} Hz, ${s.channelCount ?? "?"} canal(is) · ` +
      `eco=${String(s.echoCancellation)} ruído=${String(s.noiseSuppression)} ganho=${String(s.autoGainControl)} · ` +
      `contexto ${ctx.sampleRate} Hz`,
  );
  if (ctx.sampleRate !== TARGET_RATE) {
    console.warn(`contexto de áudio em ${ctx.sampleRate} Hz: o worklet reamostra para ${TARGET_RATE} Hz (filtro anti-aliasing + interpolação)`);
  }
}

function stopTracks(stream: MediaStream): void {
  for (const t of stream.getTracks()) t.stop();
}

function describeError(err: unknown): [MicState, string] {
  const name = err instanceof DOMException ? err.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return ["denied", ""];
    case "NotFoundError":
    case "OverconstrainedError":
      return ["unavailable", "nenhum microfone encontrado"];
    case "NotReadableError":
    case "AbortError":
      return ["unavailable", "microfone em uso por outro programa ou com falha"];
    default:
      return ["unavailable", err instanceof Error ? err.message : "erro desconhecido"];
  }
}
