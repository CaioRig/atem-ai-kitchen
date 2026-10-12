// Rosto pixelado original em Canvas 2D. Grade lógica pequena, redesenho a 20 fps,
// pausa quando a aba está oculta. Sem imagens externas, sem dependências.
import type { Mood } from "./state.js";

const GW = 48;
const GH = 27;
const EYE_Y = 11;
const EYE_DX = 8;

const COLORS: Record<Mood, string> = {
  booting: "#8fa3bf",
  idle: "#7ad7ff",
  listening: "#7cff9a",
  processing: "#ffd166",
  speaking: "#9ad0ff",
  media: "#d19aff",
  offline: "#556070",
  error: "#ff6b6b",
};

export class PixelFace {
  private readonly ctx: CanvasRenderingContext2D;
  private readonly cells = new Uint8Array(GW * GH);
  private mood: Mood = "booting";
  private moodSince = 0;
  private level = 0;
  private running = false;
  private lastFrame = 0;
  private nextBlink = 2;
  private blinkUntil = 0;
  private readonly onResize = () => this.resize();
  private readonly onVisibility = () => (document.hidden ? this.pause() : this.resume());

  constructor(
    private readonly canvas: HTMLCanvasElement,
    private readonly fps = 20,
  ) {
    const ctx = canvas.getContext("2d", { alpha: false });
    if (!ctx) throw new Error("Canvas 2D indisponível");
    this.ctx = ctx;
    this.resize();
    window.addEventListener("resize", this.onResize);
    document.addEventListener("visibilitychange", this.onVisibility);
  }

  setMood(mood: Mood): void {
    if (mood === this.mood) return;
    this.mood = mood;
    this.moodSince = performance.now() / 1000;
  }

  getMood(): Mood {
    return this.mood;
  }

  /** Intensidade 0..1 (nível do microfone ou da fala) usada em "listening"/"speaking". */
  setLevel(level: number): void {
    this.level = Math.min(1, Math.max(0, level));
  }

  start(): void {
    if (this.running) return;
    this.running = true;
    requestAnimationFrame(this.frame);
  }

  stop(): void {
    this.running = false;
    window.removeEventListener("resize", this.onResize);
    document.removeEventListener("visibilitychange", this.onVisibility);
  }

  private pause(): void {
    this.running = false;
  }

  private resume(): void {
    if (!this.running) {
      this.running = true;
      requestAnimationFrame(this.frame);
    }
  }

  private resize(): void {
    this.canvas.width = Math.max(1, this.canvas.clientWidth);
    this.canvas.height = Math.max(1, this.canvas.clientHeight);
    this.lastFrame = 0;
  }

  private readonly frame = (now: number): void => {
    if (!this.running) return;
    requestAnimationFrame(this.frame);
    if (now - this.lastFrame < 1000 / this.fps) return;
    this.lastFrame = now;
    this.draw(now / 1000);
  };

  // ---- composição ---------------------------------------------------------

  private draw(t: number): void {
    this.cells.fill(0);
    const blinking = this.updateBlink(t);
    switch (this.mood) {
      case "booting":
        this.drawBooting(t);
        break;
      case "idle":
        this.drawIdle(t, blinking);
        break;
      case "listening":
        this.drawListening(t, blinking);
        break;
      case "processing":
        this.drawProcessing(t, blinking);
        break;
      case "speaking":
        this.drawSpeaking(t, blinking);
        break;
      case "media":
        this.drawMedia(t);
        break;
      case "offline":
        this.drawOffline(t);
        break;
      case "error":
        this.drawError(t);
        break;
    }
    this.render(COLORS[this.mood]);
  }

  private updateBlink(t: number): boolean {
    if (t >= this.nextBlink) {
      this.blinkUntil = t + 0.15;
      this.nextBlink = t + 3 + Math.random() * 4;
    }
    return t < this.blinkUntil;
  }

  private eyes(w: number, h: number, dx = 0, dy = 0): void {
    const cx = GW / 2;
    const y = EYE_Y - Math.floor(h / 2) + dy;
    this.rect(cx - EYE_DX - Math.floor(w / 2) + dx, y, w, h);
    this.rect(cx + EYE_DX - Math.floor(w / 2) + dx, y, w, h);
  }

  private drawBooting(t: number): void {
    const p = (t * 0.6) % 1;
    const cx = GW / 2;
    const w = 1 + Math.round(p * 5);
    this.rect(cx - EYE_DX - 3, EYE_Y - 1, w, 2);
    this.rect(cx + EYE_DX - 3, EYE_Y - 1, w, 2);
    this.rect(cx - 10, 22, Math.round(p * 20), 1);
  }

  private drawIdle(t: number, blinking: boolean): void {
    const bob = Math.round(Math.sin(t * 1.2));
    this.eyes(6, blinking ? 1 : 6, 0, bob);
    this.rect(GW / 2 - 2, 19 + bob, 5, 1);
  }

  private drawListening(t: number, blinking: boolean): void {
    const pulse = Math.round((Math.sin(t * 4) + 1) / 2);
    const w = 6 + pulse;
    this.eyes(w, blinking ? 1 : 6 + pulse);
    const cx = GW / 2;
    this.rect(cx - 1, 18, 3, 1);
    this.rect(cx - 1, 20, 3, 1);
    this.px(cx - 2, 19);
    this.px(cx + 2, 19);
    const lw = 2 + Math.round(this.level * 22);
    this.rect(cx - Math.floor(lw / 2), 24, lw, 1);
  }

  private drawProcessing(t: number, blinking: boolean): void {
    const dx = Math.round(Math.sin(t * 1.5) * 3);
    this.eyes(6, blinking ? 1 : 4, dx);
    const cx = GW / 2;
    const step = Math.floor(t * 3);
    for (let i = 0; i < 3; i++) {
      if ((step + i) % 3 === 0) this.rect(cx - 5 + i * 4, 19, 2, 1);
    }
  }

  private drawSpeaking(t: number, blinking: boolean): void {
    this.eyes(6, blinking ? 1 : 6);
    const a = this.level > 0 ? this.level : 0.5 + 0.5 * Math.sin(t * 11) * Math.sin(t * 7.3);
    const mh = 1 + Math.round(a * 5);
    const cx = GW / 2;
    const top = 19 - Math.floor(mh / 2);
    this.rect(cx - 3, top, 7, mh);
    if (mh > 2) {
      this.rect(cx - 4, top + 1, 1, mh - 2);
      this.rect(cx + 4, top + 1, 1, mh - 2);
    }
  }

  private drawMedia(t: number): void {
    const bob = Math.round(Math.sin(t * 3) * 0.6);
    const cx = GW / 2;
    for (const ex of [cx - EYE_DX, cx + EYE_DX]) {
      const ey = EYE_Y + bob;
      this.rect(ex - 3, ey + 1, 1, 2);
      this.rect(ex - 2, ey, 1, 2);
      this.rect(ex - 1, ey - 1, 2, 2);
      this.rect(ex + 1, ey, 1, 2);
      this.rect(ex + 2, ey + 1, 1, 2);
    }
    this.px(cx - 4, 18 + bob);
    this.px(cx + 4, 18 + bob);
    this.px(cx - 3, 19 + bob);
    this.px(cx + 3, 19 + bob);
    this.rect(cx - 2, 20 + bob, 5, 1);
    // nota musical flutuando
    const ny = 6 - Math.round(((t * 0.4) % 1) * 4);
    const nx = cx + 16;
    this.rect(nx, ny, 1, 5);
    this.rect(nx - 2, ny + 4, 2, 2);
    this.rect(nx + 1, ny, 3, 1);
  }

  private drawOffline(t: number): void {
    this.eyes(6, 2, 0, 1);
    const cx = GW / 2;
    this.rect(cx - 1, 19, 3, 1);
    for (let k = 0; k < 2; k++) {
      const phase = (t * 0.5 + k * 0.5) % 1;
      const y = 9 - Math.round(phase * 6);
      const x = cx + 13 + k * 4;
      this.rect(x, y, 3, 1);
      this.px(x + 1, y + 1);
      this.rect(x, y + 2, 3, 1);
    }
  }

  private drawError(t: number): void {
    const shake = t - this.moodSince < 0.6 ? Math.round(Math.sin(t * 40)) : 0;
    const cx = GW / 2 + shake;
    for (const ex of [cx - EYE_DX, cx + EYE_DX]) {
      this.line(ex - 3, EYE_Y - 3, ex + 3, EYE_Y + 3);
      this.line(ex + 3, EYE_Y - 3, ex - 3, EYE_Y + 3);
    }
    this.rect(cx - 3, 20, 7, 1);
    this.px(cx - 4, 19);
    this.px(cx + 4, 19);
  }

  // ---- primitivas ---------------------------------------------------------

  private px(x: number, y: number): void {
    if (x < 0 || y < 0 || x >= GW || y >= GH) return;
    this.cells[y * GW + x] = 1;
  }

  private rect(x: number, y: number, w: number, h: number): void {
    for (let j = 0; j < h; j++) for (let i = 0; i < w; i++) this.px(x + i, y + j);
  }

  private line(x0: number, y0: number, x1: number, y1: number): void {
    const dx = Math.abs(x1 - x0);
    const dy = -Math.abs(y1 - y0);
    const sx = x0 < x1 ? 1 : -1;
    const sy = y0 < y1 ? 1 : -1;
    let err = dx + dy;
    for (;;) {
      this.px(x0, y0);
      if (x0 === x1 && y0 === y1) break;
      const e2 = 2 * err;
      if (e2 >= dy) {
        err += dy;
        x0 += sx;
      }
      if (e2 <= dx) {
        err += dx;
        y0 += sy;
      }
    }
  }

  private render(color: string): void {
    const { width: W, height: H } = this.canvas;
    const s = Math.max(1, Math.floor(Math.min(W / GW, H / GH)));
    const ox = Math.floor((W - GW * s) / 2);
    const oy = Math.floor((H - GH * s) / 2);
    const gap = s >= 6 ? 1 : 0;
    const ctx = this.ctx;
    ctx.fillStyle = "#0b0e14";
    ctx.fillRect(0, 0, W, H);
    ctx.fillStyle = color;
    for (let y = 0; y < GH; y++) {
      const row = y * GW;
      for (let x = 0; x < GW; x++) {
        if (this.cells[row + x]) ctx.fillRect(ox + x * s, oy + y * s, s - gap, s - gap);
      }
    }
  }
}
