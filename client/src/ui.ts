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
  private readonly weatherText = byId("weather-text");
  private readonly weatherUpdated = byId("weather-updated");
  private readonly weatherIcon = document.querySelector("#weather-icon")!;
  private readonly conn = byId("conn");
  private readonly mic = byId<HTMLButtonElement>("mic");
  private readonly cancel = byId<HTMLButtonElement>("cancel");
  private readonly retry = byId<HTMLButtonElement>("retry");
  private readonly offlineBadge = byId("offline-badge");
  private readonly memoryBtn = byId<HTMLButtonElement>("memory");
  private readonly ingredientsBtn = byId<HTMLButtonElement>("ingredients-open");
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
  private ingredientsOnly = false;
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
    if (this.status.textContent !== text) this.status.textContent = text;
  }

  /** Indicador textual do estado atual; `mood` também vira classe CSS para a cor. */
  setMood(mood: string, label: string): void {
    const className = `mood mood-${mood}`;
    if (this.mood.textContent !== label) this.mood.textContent = label;
    if (this.mood.className !== className) this.mood.className = className;
  }

  setHeard(text: string, final = true): void {
    this.heard.textContent = text || "Aguardando sua pergunta.";
    this.heard.classList.toggle("partial", !final);
  }

  /** Cartão "Clima": resumo, linha de atualização (hora da consulta à fonte) e destaque quando os dados estão antigos. */
  setWeather(text: string, updatedLabel: string, stale = false, location?: string): void {
    const temperature = /^(-?\d+(?:[.,]\d+)?\s*°C)\s*·\s*/.exec(text);
    if (temperature) {
      const value = document.createElement("strong");
      value.className = "weather-temperature";
      value.textContent = temperature[1];
      this.weatherText.replaceChildren(value, text.slice(temperature[0].length));
    } else {
      this.weatherText.textContent = text;
    }
    const condition = (temperature ? text.slice(temperature[0].length) : text).split(" · ")[0].toLowerCase();
    const icon = /^(sol|céu limpo)/.test(condition) ? "sun"
      : /chuva|garoa|tempestade/.test(condition) ? "rain"
      : /nuvens|nublado|nevoeiro/.test(condition) ? "cloud" : "weather";
    this.weatherIcon.setAttribute("href", `#icon-${icon}`);
    if (location) byId("weather-location").textContent = `Clima · ${location}`;
    this.weatherUpdated.textContent = updatedLabel;
    this.weatherUpdated.classList.toggle("stale", stale);
  }

  setConnection(view: ConnectionView, extra = ""): void {
    const label = { connecting: "conectando…", online: "conectado", reconnecting: "reconectando" }[view];
    this.conn.textContent = `Servidor: ${label}${extra ? ` · ${extra}` : ""}`;
    this.conn.classList.toggle("ok", view === "online");
    this.conn.classList.toggle("warn", view !== "online");
    this.offlineBadge.classList.toggle("hidden", view === "online");
  }

  setMic(state: MicState, message = ""): void {
    this.mic.textContent = micLabel(state);
    this.mic.title = message ? micLabel(state, message) : "Ligar ou desligar o microfone (tecla M)";
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
    this.cancel.classList.remove("hidden");
    this.cancel.disabled = !visible;
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
    byId("memory-label").textContent = memoryButtonLabel(count);
    if (!available) this.setMemoryVisible(false);
  }

  get memoryVisible(): boolean {
    return !this.memoryPanel.classList.contains("hidden");
  }

  setMemoryVisible(visible: boolean, ingredients = this.ingredientsOnly): void {
    if (!visible && !this.memoryVisible) return;
    if (visible) document.dispatchEvent(new CustomEvent("luna:panel", { detail: "memory-panel" }));
    this.ingredientsOnly = visible && ingredients;
    this.memoryPanel.classList.toggle("hidden", !visible);
    this.memoryBtn.setAttribute("aria-expanded", String(visible && !ingredients));
    this.ingredientsBtn.setAttribute("aria-expanded", String(visible && ingredients));
    if (!visible) this.disarmClear();
    document.dispatchEvent(new Event("luna:panel-changed"));
  }

  /** Lista de memórias (todas, ou as de um assunto pedido por voz); cada linha tem "Esquecer". */
  renderMemories(items: MemoryItem[], topic = ""): void {
    const stock = this.ingredientsOnly || /^ingredientes\b/i.test(topic);
    const rows = stock ? items.filter(item => item.kind === "ingredient") : items;
    const restoreFocus = this.memoryList.contains(document.activeElement);
    this.memoryTitle.textContent = stock ? (topic || "Despensa") : topic ? `Memória · ${topic}` : "Memória da Luna";
    this.memoryEmpty.textContent = stock
      ? "Nenhum ingrediente nesta lista. Diga “hey Luna, tenho três ovos” para começar."
      : "Nada guardado ainda. Diga “hey Luna, lembre que…” para começar.";
    byId("memory-help").textContent = stock
      ? "Para atualizar uma quantidade, diga “hey Luna, agora tenho dois ovos”."
      : "As informações ficam guardadas neste servidor.";
    this.memoryList.replaceChildren(...rows.map((item) => this.memoryRow(item)));
    if (restoreFocus) this.memoryList.focus();
    this.memoryEmpty.classList.toggle("hidden", rows.length > 0);
    this.memoryClear.classList.toggle("hidden", stock || rows.length === 0 || topic !== "");
    this.disarmClear();
  }

  showMemoryLoading(ingredients: boolean): void {
    this.renderMemories([], ingredients ? "Despensa" : "");
    this.memoryEmpty.textContent = "Carregando lista…";
  }

  private memoryRow(item: MemoryItem): HTMLLIElement {
    const li = document.createElement("li");
    li.className = "memory-item";
    const kind = document.createElement("span");
    kind.className = `memory-kind kind-${item.kind}`;
    kind.textContent = memoryKindLabel(item.kind);
    const text = document.createElement("span");
    text.className = "memory-text";
    const ingredient = item.kind === "ingredient" ? /^([^:]+):\s*(.+)$/.exec(item.text) : null;
    if (ingredient) {
      const name = document.createElement("strong");
      name.textContent = ingredient[1];
      const amount = document.createElement("span");
      amount.className = "ingredient-amount";
      amount.textContent = ingredient[2];
      text.append(name, amount);
    } else {
      text.textContent = item.text;
    }
    const forget = document.createElement("button");
    forget.type = "button";
    forget.className = "pill warn memory-forget";
    forget.textContent = item.kind === "ingredient" ? "Remover" : "Esquecer";
    forget.setAttribute("aria-label", `${forget.textContent}: ${ingredient?.[1] ?? item.text}`);
    forget.addEventListener("click", () => {
      if (window.confirm(`Remover “${ingredient?.[1] ?? item.text}”?`)) this.memoryDeleteHandler?.(item.id);
    });
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
