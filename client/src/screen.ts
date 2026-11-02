import type { StageView } from "./stage.js";
/** Comandos locais de tela e preferencias do quiosque. Sem acesso a codigo recebido. */
export function textScale(value: unknown): number {
  const n = Number(value);
  return Number.isFinite(n) && n >= 1 && n <= 1.5 ? n : 1;
}
const KEY = "luna.text.scale";
const panels = ["settings-panel", "memory-panel"];

export class ScreenControls {
  private scale = 1;
  constructor(private notice: (text: string) => void, private stage: StageView) {
    new ResizeObserver(entries => {
      document.documentElement.style.setProperty("--controls-height", `${entries[0].target.getBoundingClientRect().height}px`);
    }).observe(document.getElementById("indicators")!);
    try { this.scale = textScale(localStorage.getItem(KEY)); } catch { /* sessao */ }
    this.applyScale();
    document.getElementById("settings-open")!.addEventListener("click", () => this.execute("settings"));
    document.getElementById("settings-close")!.addEventListener("click", () => this.close("settings-panel"));
    document.getElementById("home")!.addEventListener("click", () => this.execute("home"));
    document.getElementById("text-larger")!.addEventListener("click", () => this.execute("larger"));
    document.getElementById("text-smaller")!.addEventListener("click", () => this.execute("smaller"));
    document.getElementById("visual-reset")!.addEventListener("click", () => {
      this.scale = 1; this.applyScale();
      const duration = document.getElementById("forecast-duration") as HTMLInputElement;
      duration.value = "15"; duration.dispatchEvent(new Event("change"));
      this.notice("Visual padrão restaurado");
    });
    document.getElementById("fullscreen")!.addEventListener("click", async () => {
      try {
        if (document.fullscreenElement) await document.exitFullscreen();
        else if (document.documentElement.requestFullscreen) await document.documentElement.requestFullscreen();
        else this.notice("Tela cheia indisponível neste navegador");
      } catch { this.notice("O navegador não permitiu tela cheia"); }
    });
    document.addEventListener("luna:panel", (event) => {
      const id = (event as CustomEvent<string>).detail;
      for (const other of panels) if (other !== id) this.close(other);
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && (this.active() || this.stage.mode !== "face")) {
        event.stopImmediatePropagation(); this.execute("close");
      }
    }, true);
  }
  private active(): string | undefined {
    return panels.find(id => !document.getElementById(id)!.classList.contains("hidden"));
  }
  private close(id: string): void {
    if (id === "settings-panel") document.getElementById(id)!.classList.add("hidden");
    else document.getElementById(id.replace("-panel", "-close"))!.click();
  }
  private applyScale(): void {
    document.documentElement.style.setProperty("--text-scale", String(this.scale));
    document.getElementById("text-size")!.textContent = `${Math.round(this.scale * 100)}%`;
    try { localStorage.setItem(KEY, String(this.scale)); } catch { /* sessao */ }
    window.dispatchEvent(new Event("resize"));
  }
  execute(action: string, message = ""): void {
    switch (action) {
      case "notice": this.notice(message); break;
      case "settings":
        document.dispatchEvent(new CustomEvent("luna:panel", { detail: "settings-panel" }));
        document.getElementById("settings-panel")!.classList.remove("hidden");
        break;
      case "memory": {
        const button = document.getElementById("memory") as HTMLButtonElement;
        if (button.disabled || button.classList.contains("hidden")) this.notice("Memória indisponível neste momento");
        else if (this.active() !== "memory-panel") button.click();
        break;
      }
      case "close": {
        const active = this.active();
        if (active) this.close(active); else if (this.stage.mode !== "face") this.stage.close(); else this.notice("Nenhuma janela aberta");
        break;
      }
      case "home":
        panels.forEach(id => this.close(id));
        this.stage.close();
        document.getElementById("content-scroll")!.scrollTo({ top: 0 });
        this.notice("Tela principal"); break;
      case "up": case "down": {
        const active = this.active();
        const target = document.getElementById(active === "memory-panel" ? "memory-list" : active || (this.stage.mode !== "face" ? "stage-scroll" : "content-scroll"))!;
        const before = target.scrollTop;
        target.scrollBy({ top: (action === "down" ? 1 : -1) * target.clientHeight * 0.7, behavior: "instant" });
        if (before === target.scrollTop) this.notice("Limite da rolagem");
        break;
      }
      case "larger": case "smaller":
        this.scale = Math.max(1, Math.min(1.5, Math.round((this.scale + (action === "larger" ? 0.1 : -0.1)) * 10) / 10));
        this.applyScale(); this.notice(`Texto em ${Math.round(this.scale * 100)}%`); break;
      case "stop": this.notice("Fala interrompida"); break;
      case "video1": case "video2": case "video3": void this.stage.select(Number(action.slice(-1)) - 1); break;
      case "pause_video": this.stage.pause(); break;
      case "resume_video": this.stage.resume(); break;
      default: this.notice("Ação de tela indisponível");
    }
  }
}
