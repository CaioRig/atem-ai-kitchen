// Reprodução da voz da Luna no quiosque. Os trechos PCM int16 (um por frase) chegam pelo WebSocket
// e são agendados em sequência no Web Audio, sem lacunas e sem bloquear a interface. Nada é gravado:
// cada trecho é descartado depois de tocar. O servidor é avisado quando a reprodução termina.
export interface PlayerHandlers {
  /** Fim da reprodução da fala `id`; `played = false` quando o navegador não conseguiu tocar. */
  done?: (id: number, played: boolean, error: string) => void;
  /** Nível 0..1 do áudio em reprodução (0 ao terminar), para animar o rosto. */
  level?: (rms: number) => void;
}

interface Utterance {
  id: number;
  sampleRate: number;
  ended: boolean; // speak_end recebido
  pending: number; // trechos agendados que ainda não terminaram
  failed: boolean;
}

const LEVEL_INTERVAL_MS = 50;
const LEAD_S = 0.05; // folga antes do primeiro trecho (e após uma lacuna) para o agendamento não atrasar

export class SpeechPlayer {
  private ctx: AudioContext | null = null;
  private out: GainNode | null = null;
  private analyser: AnalyserNode | null = null;
  private current: Utterance | null = null;
  private nextTime = 0;
  private readonly sources = new Set<AudioBufferSourceNode>();
  private levelTimer: number | undefined;
  private readonly samples = new Uint8Array(1024);

  constructor(private readonly handlers: PlayerHandlers) {}

  get playing(): boolean {
    return this.current !== null;
  }

  /** Cria ou retoma o contexto de saída. Chame em um gesto do usuário (política de autoplay). */
  unlock(): void {
    const ctx = this.context();
    if (ctx && ctx.state === "suspended") void ctx.resume().catch(() => undefined);
  }

  /** Nova fala: descarta qualquer reprodução anterior (nunca duas respostas ao mesmo tempo). */
  start(id: number, sampleRate: number): void {
    this.stop();
    const cur: Utterance = { id, sampleRate, ended: false, pending: 0, failed: false };
    this.current = cur;
    this.nextTime = 0;
    const ctx = this.context();
    if (!ctx) {
      this.fail(cur, "este navegador não consegue reproduzir áudio (Web Audio indisponível)");
      return;
    }
    if (ctx.state !== "running") {
      void ctx
        .resume()
        .catch(() => undefined)
        .then(() => {
          if (this.current === cur && ctx.state !== "running") this.fail(cur, "toque na tela para liberar o som");
        });
    }
    this.startLevel();
  }

  /** Trecho PCM int16 LE mono na taxa da fala atual; ignorado se não há fala em andamento. */
  enqueue(pcm: ArrayBuffer): void {
    const cur = this.current;
    const ctx = this.ctx;
    if (!cur || cur.failed || !ctx || !this.out) return;
    const n = Math.floor(pcm.byteLength / 2);
    if (n === 0) return;
    try {
      const i16 = new Int16Array(pcm, 0, n);
      const buffer = ctx.createBuffer(1, n, cur.sampleRate);
      const data = buffer.getChannelData(0);
      for (let i = 0; i < n; i++) data[i] = i16[i] / 32768;
      const src = ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this.out);
      const at = Math.max(ctx.currentTime + LEAD_S, this.nextTime);
      src.start(at);
      this.nextTime = at + buffer.duration;
      cur.pending++;
      this.sources.add(src);
      src.onended = () => {
        this.sources.delete(src);
        if (this.current !== cur) return;
        cur.pending--;
        this.finishIfDone(cur);
      };
    } catch (err) {
      this.fail(cur, err instanceof Error ? err.message : String(err));
    }
  }

  /** O servidor terminou de enviar a fala `id`; o fim é avisado quando o último trecho tocar. */
  end(id: number): void {
    const cur = this.current;
    if (!cur || cur.id !== id) return;
    cur.ended = true;
    this.finishIfDone(cur);
  }

  /** Para imediatamente (interrupção, cancelamento, nova fala ou desconexão). Não avisa `done`. */
  stop(): void {
    for (const src of this.sources) {
      src.onended = null;
      try {
        src.stop();
      } catch {
        /* já parou */
      }
    }
    this.sources.clear();
    this.current = null;
    this.nextTime = 0;
    this.stopLevel();
  }

  private finishIfDone(cur: Utterance): void {
    if (!cur.ended || cur.pending > 0 || this.current !== cur) return;
    this.current = null;
    this.stopLevel();
    this.handlers.done?.(cur.id, true, "");
  }

  private fail(cur: Utterance, message: string): void {
    if (this.current !== cur || cur.failed) return;
    cur.failed = true;
    this.stop();
    this.handlers.done?.(cur.id, false, message);
  }

  private context(): AudioContext | null {
    if (this.ctx) return this.ctx;
    try {
      const ctx = new AudioContext();
      const out = ctx.createGain();
      const analyser = ctx.createAnalyser();
      analyser.fftSize = this.samples.length;
      out.connect(analyser).connect(ctx.destination);
      this.ctx = ctx;
      this.out = out;
      this.analyser = analyser;
      return ctx;
    } catch {
      return null;
    }
  }

  private startLevel(): void {
    this.stopLevel();
    this.levelTimer = window.setInterval(() => {
      const analyser = this.analyser;
      if (!analyser) return;
      analyser.getByteTimeDomainData(this.samples);
      let sum = 0;
      for (let i = 0; i < this.samples.length; i++) {
        const v = (this.samples[i] - 128) / 128;
        sum += v * v;
      }
      this.handlers.level?.(Math.sqrt(sum / this.samples.length));
    }, LEVEL_INTERVAL_MS);
  }

  private stopLevel(): void {
    if (this.levelTimer === undefined) return;
    window.clearInterval(this.levelTimer);
    this.levelTimer = undefined;
    this.handlers.level?.(0);
  }
}
