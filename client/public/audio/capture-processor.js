// AudioWorklet: converte o áudio do microfone em PCM int16 mono a 16 kHz, em blocos
// de 1280 amostras (80 ms). Se o contexto não estiver em 16 kHz (o pedido de 16 kHz foi
// recusado pelo navegador), reamostra: filtro passa-baixa (sinc janelado) e interpolação
// linear. Sem o filtro, o conteúdo acima de 8 kHz dobraria para dentro da banda (aliasing)
// e prejudicaria o reconhecimento. Não guarda nada além do bloco em construção.
const TARGET_RATE = 16000;
const BLOCK = 1280;
const TAPS = 31; // coeficientes do filtro anti-aliasing (corte em 7 kHz, janela de Hamming)

function lowpass(rate) {
  const fc = 7000 / rate;
  const h = new Float32Array(TAPS);
  let sum = 0;
  for (let i = 0; i < TAPS; i++) {
    const n = i - (TAPS - 1) / 2;
    const sinc = n === 0 ? 2 * fc : Math.sin(2 * Math.PI * fc * n) / (Math.PI * n);
    const w = 0.54 - 0.46 * Math.cos((2 * Math.PI * i) / (TAPS - 1));
    h[i] = sinc * w;
    sum += h[i];
  }
  for (let i = 0; i < TAPS; i++) h[i] /= sum;
  return h;
}

class CaptureProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.ratio = sampleRate / TARGET_RATE;
    this.pos = 0; // posição fracionária de leitura, relativa ao início do bloco atual
    this.prev = 0; // última amostra (filtrada) do bloco anterior, para interpolar na borda
    this.out = new Int16Array(BLOCK);
    this.n = 0;
    this.sumsq = 0;
    if (this.ratio !== 1) {
      this.h = lowpass(sampleRate);
      this.hist = new Float32Array(TAPS - 1); // últimas amostras de entrada, para o filtro na borda do bloco
    }
  }

  push(s) {
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    this.out[this.n++] = s < 0 ? s * 32768 : s * 32767;
    this.sumsq += s * s;
    if (this.n === BLOCK) {
      const pcm = this.out.buffer;
      this.port.postMessage({ pcm, rms: Math.sqrt(this.sumsq / BLOCK) }, [pcm]);
      this.out = new Int16Array(BLOCK);
      this.n = 0;
      this.sumsq = 0;
    }
  }

  /** Aplica o passa-baixa ao bloco de entrada, continuando do bloco anterior. */
  filter(ch) {
    const n = ch.length;
    const h = this.h;
    const buf = new Float32Array(TAPS - 1 + n);
    buf.set(this.hist);
    buf.set(ch, TAPS - 1);
    const y = new Float32Array(n);
    for (let i = 0; i < n; i++) {
      let acc = 0;
      for (let k = 0; k < TAPS; k++) acc += h[k] * buf[i + TAPS - 1 - k];
      y[i] = acc;
    }
    this.hist.set(buf.subarray(n));
    return y;
  }

  process(inputs) {
    const raw = inputs[0] && inputs[0][0];
    if (!raw || raw.length === 0) return true;
    if (this.ratio === 1) {
      for (let i = 0; i < raw.length; i++) this.push(raw[i]);
      return true;
    }
    const ch = this.filter(raw);
    let p = this.pos;
    const n = ch.length;
    while (p < n - 1) {
      const i = Math.floor(p);
      const frac = p - i;
      const s0 = i < 0 ? this.prev : ch[i];
      const s1 = ch[i + 1];
      this.push(s0 + (s1 - s0) * frac);
      p += this.ratio;
    }
    this.pos = p - n;
    this.prev = ch[n - 1];
    return true;
  }
}

registerProcessor("atem-capture", CaptureProcessor);
