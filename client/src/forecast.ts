const KEY = "luna.forecast.duration";
export function duration(value: unknown): number {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 300 ? n : 15;
}

export class ForecastPopup {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private readonly panel = document.getElementById("forecast-panel")!;
  private readonly text = document.getElementById("forecast-text")!;
  private readonly input = document.getElementById("forecast-duration") as HTMLInputElement;
  constructor() {
    let saved = "15";
    try { saved = String(duration(localStorage.getItem(KEY))); } catch { /* sessao */ }
    // Preserva valores numericos escolhidos antes da troca do campo por um seletor.
    if (typeof HTMLSelectElement !== "undefined" && this.input instanceof HTMLSelectElement && !Array.from(this.input.options).some(o => o.value === saved)) {
      this.input.add(new Option(`${saved} segundos`, saved));
    }
    this.input.value = saved;
    this.input.addEventListener("change", () => {
      this.input.value = String(duration(this.input.value));
      try { localStorage.setItem(KEY, this.input.value); } catch { /* sessão sem armazenamento */ }
      if (!this.panel.classList.contains("hidden")) this.schedule();
    });
    document.getElementById("forecast-close")!.addEventListener("click", () => this.close());
    document.addEventListener("keydown", (ev) => { if (ev.key === "Escape") this.close(); });
  }
  show(text: string): void {
    document.dispatchEvent(new CustomEvent("luna:panel", { detail: "forecast-panel" }));
    this.text.textContent = text;
    this.panel.classList.remove("hidden");
    this.schedule();
  }
  close(): void {
    clearTimeout(this.timer);
    this.panel.classList.add("hidden");
  }
  private schedule(): void {
    clearTimeout(this.timer);
    this.timer = setTimeout(() => this.close(), duration(this.input.value) * 1000);
  }
}
