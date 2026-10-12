// Ligações com o DOM: relógio, textos, indicadores, botão do microfone e painel de memória.
import type { MemoryItem } from "./protocol.js";
import { memoryButtonLabel, memoryKindLabel, micLabel, type MicState } from "./state.js";

export type ConnectionView = "connecting" | "online" | "reconnecting";

function byId<T extends HTMLElement>(id: string): T {
  const el = document.getElementById(id);
  if (!el) throw new Error(`elemento #${id} não encontrado`);
  return el as T;
}

export class Panel {
  private readonly time = byId("time");
  private readonly date = byId("date");
  private readonly status = byId("status");
  private readonly mood = byId("mood");
  private readonly heard = byId("heard");
  private readonly reply = byId("reply");
  private readonly weatherText = byId("weather-text");
  private readonly weatherUpdated = byId("weather-updated");
  private readonly conn = byId("conn");
  private readonly mic = byId<HTMLButtonElement>("mic");
  private readonly cancel = byId<HTMLButtonElement>("cancel");
  private readonly retry = byId<HTMLButtonElement>("retry");
  private readonly offlineBadge = byId("offline-badge");
  private readonly memoryBtn = byId<HTMLButtonElement>("memory");
  private readonly memoryPanel = byId("memory-panel");
  private readonly memoryTitle = byId("memory-title");
  private readonly memoryList = byId("memory-list");
  private readonly memoryEmpty = byId("memory-empty");
  private readonly memoryClear = byId<HTMLButtonElement>("memory-clear");
  private readonly memoryClose = byId<HTMLButtonElement>("memory-close");
  private readonly toast = byId("toast");
  private toastTimer: number | undefined;
  private clearArmTimer: number | undefined;
  private memoryDeleteHandler: ((id: number) => void) | null = null;
  private readonly timeFmt = new Intl.DateTimeFormat("pt-BR", { hour: "2-digit", minute: "2-digit" });
  private readonly dateFmt = new Intl.DateTimeFormat("pt-BR", { weekday: "long", day: "numeric", month: "long" });
  private lastMinute = -1;

  startClock(): void {
    const tick = () => {
      const now = new Date();
      if (now.getMinutes() !== this.lastMinute) {
        this.lastMinute = now.getMinutes();
        this.time.textContent = this.timeFmt.format(now);
        this.date.textContent = this.dateFmt.format(now);
      }
    };
    tick();
    window.setInterval(tick, 1000);
  }

  setStatus(text: string): void {
    this.status.textContent = text;
  }

  /** Indicador textual do estado atual; `mood` também vira classe CSS para a cor. */
  setMood(mood: string, label: string): void {
    this.mood.textContent = label;
    this.mood.className = `mood mood-${mood}`;
  }

  setHeard(text: string, final = true): void {
    this.heard.textContent = text || "—";
    this.heard.classList.toggle("partial", !final);
  }

  /** Resposta da IA; `final = false` enquanto o texto ainda está chegando. */
  setReply(text: string, final = true): void {
    this.reply.textContent = text || "—";
    this.reply.classList.toggle("partial", !final);
  }

  setWeather(text: string, updatedAt: Date | null): void {
    this.weatherText.textContent = text;
    this.weatherUpdated.textContent = updatedAt
      ? `Atualizado às ${this.timeFmt.format(updatedAt)}`
      : "Sem dados — nunca atualizado";
  }

  setConnection(view: ConnectionView, extra = ""): void {
    const label = { connecting: "conectando…", online: "conectado", reconnecting: "reconectando" }[view];
    this.conn.textContent = `Servidor: ${label}${extra ? ` · ${extra}` : ""}`;
    this.conn.classList.toggle("ok", view === "online");
    this.conn.classList.toggle("warn", view !== "online");
    this.offlineBadge.classList.toggle("hidden", view === "online");
  }

  setMic(state: MicState, message = ""): void {
    this.mic.textContent = micLabel(state, message);
    this.mic.classList.toggle("ok", state === "on");
    this.mic.classList.toggle("warn", state === "off" || state === "starting" || state === "blocked");
    this.mic.classList.toggle("err", state === "denied" || state === "unavailable" || state === "insecure");
    this.mic.setAttribute("aria-pressed", String(state === "on" || state === "starting" || state === "blocked"));
  }

  onMicToggle(fn: () => void): void {
    this.mic.addEventListener("click", fn);
  }

  /** Botão "Cancelar": visível enquanto há captura, transcrição, resposta ou fala em andamento. */
  setCancelVisible(visible: boolean): void {
    this.cancel.classList.toggle("hidden", !visible);
  }

  onCancel(fn: () => void): void {
    this.cancel.addEventListener("click", fn);
  }

  /** Botão "Tentar novamente": visível após uma falha da IA ou da voz. */
  setRetryVisible(visible: boolean, label = "Tentar novamente"): void {
    this.retry.classList.toggle("hidden", !visible);
    if (this.retry.textContent !== label) this.retry.textContent = label;
  }

  onRetry(fn: () => void): void {
    this.retry.addEventListener("click", fn);
  }

  // ---- Memória persistente -------------------------------------------------------------------

  /** Botão "Memória" com a contagem no rótulo; escondido quando o servidor não tem memória. */
  setMemory(available: boolean, count: number): void {
    this.memoryBtn.classList.toggle("hidden", !available);
    this.memoryBtn.textContent = memoryButtonLabel(count);
    if (!available) this.setMemoryVisible(false);
  }

  get memoryVisible(): boolean {
    return !this.memoryPanel.classList.contains("hidden");
  }

  setMemoryVisible(visible: boolean): void {
    this.memoryPanel.classList.toggle("hidden", !visible);
    this.memoryBtn.setAttribute("aria-expanded", String(visible));
    if (!visible) this.disarmClear();
  }

  /** Lista de memórias (todas, ou as de um assunto pedido por voz); cada linha tem "Esquecer". */
  renderMemories(items: MemoryItem[], topic = ""): void {
    this.memoryTitle.textContent = topic ? `Memória · ${topic}` : "Memória da Luna";
    this.memoryList.replaceChildren(...items.map((item) => this.memoryRow(item)));
    this.memoryEmpty.classList.toggle("hidden", items.length > 0);
    this.memoryClear.classList.toggle("hidden", items.length === 0 || topic !== "");
    this.disarmClear();
  }

  private memoryRow(item: MemoryItem): HTMLLIElement {
    const li = document.createElement("li");
    li.className = "memory-item";
    const kind = document.createElement("span");
    kind.className = `memory-kind kind-${item.kind}`;
    kind.textContent = memoryKindLabel(item.kind);
    const text = document.createElement("span");
    text.className = "memory-text";
    text.textContent = item.text;
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "pill warn memory-forget";
    forget.textContent = "Esquecer";
    forget.title = "Apagar esta memória";
    forget.addEventListener("click", () => this.memoryDeleteHandler?.(item.id));
    li.append(kind, text, forget);
    return li;
  }

  onMemoryToggle(fn: () => void): void {
    this.memoryBtn.addEventListener("click", fn);
  }

  onMemoryClose(fn: () => void): void {
    this.memoryClose.addEventListener("click", fn);
  }

  onMemoryDelete(fn: (id: number) => void): void {
    this.memoryDeleteHandler = fn;
  }

  /** "Apagar todas": o primeiro toque arma a confirmação por 5 s; o segundo executa. */
  onMemoryClear(fn: () => void): void {
    this.memoryClear.addEventListener("click", () => {
      if (this.clearArmTimer !== undefined) {
        this.disarmClear();
        fn();
        return;
      }
      this.memoryClear.textContent = "Confirmar: apagar todas?";
      this.memoryClear.classList.add("err");
      this.clearArmTimer = window.setTimeout(() => this.disarmClear(), 5000);
    });
  }

  private disarmClear(): void {
    window.clearTimeout(this.clearArmTimer);
    this.clearArmTimer = undefined;
    this.memoryClear.textContent = "Apagar todas";
    this.memoryClear.classList.remove("err");
  }

  /** Aviso breve sobre o rosto ("Memória salva", "Memória apagada"). */
  showToast(text: string, ms = 3500): void {
    this.toast.textContent = text;
    this.toast.classList.remove("hidden");
    window.clearTimeout(this.toastTimer);
    this.toastTimer = window.setTimeout(() => this.toast.classList.add("hidden"), ms);
  }
}
