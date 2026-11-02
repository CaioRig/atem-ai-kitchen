const KEY = "luna.forecast.duration";
export function duration(value: unknown): number {
  const n = Number(value);
  return Number.isInteger(n) && n >= 1 && n <= 300 ? n : 15;
}

export class ForecastSettings {
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
    });
  }
  get seconds(): number { return duration(this.input.value); }
}
