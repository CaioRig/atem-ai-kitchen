import type { PixelFace } from "./face.js";

interface PerformanceStatus {
  preferences: { profile: string; model: string; voice: string };
  hardware: { cpu: string; cores: number | null; available_threads: number; ram_available: number | null; gpu: string | null };
  profile: string;
  inference: string;
  model: string;
  tts_engine: string;
  tts_voice: string;
  models: string[];
  notices: string[];
  restart_required: boolean;
}

export function setupPerformance(token: string, face: PixelFace): void {
  const profile = document.getElementById("performance-profile") as HTMLSelectElement;
  const model = document.getElementById("performance-model") as HTMLSelectElement;
  const voice = document.getElementById("performance-voice") as HTMLSelectElement;
  const status = document.getElementById("performance-status")!;
  const nav = navigator as Navigator & { deviceMemory?: number };
  const weakClient = (navigator.hardwareConcurrency || 2) <= 4 || (nav.deviceMemory !== undefined && nav.deviceMemory <= 4);
  face.setFps(weakClient ? 8 : 20);
  document.documentElement.classList.toggle("economic-client", weakClient);
  const render = (data: PerformanceStatus) => {
    profile.value = data.preferences.profile;
    model.replaceChildren(new Option("Seleção automática", ""), ...data.models.map(name => new Option(name, name)));
    model.value = data.preferences.model;
    voice.value = data.preferences.voice;
    voice.disabled = data.tts_engine !== "kokoro";
    const h = data.hardware;
    const ram = h.ram_available === null ? "RAM desconhecida" : `${(h.ram_available / 1024 ** 3).toFixed(1)} GB RAM disponível`;
    document.getElementById("performance-hardware")!.textContent =
      `Servidor: ${h.cpu || "CPU desconhecida"}; ${h.cores ?? "?"} núcleos; ${h.available_threads} threads disponíveis; ${ram}; ${h.gpu || "GPU não identificada"}. ` +
      `IA: ${data.model}; voz: ${data.tts_engine}/${data.tts_voice}. Inferência ${data.inference === "local" ? "neste servidor" : "remota (hardware desconhecido)"}. ` +
      `Interface neste dispositivo: ${weakClient ? "econômica, 8 fps" : "completa, 20 fps"}.`;
    status.textContent = (data.restart_required ? "Salvo. Reinicie o servidor para aplicar. " : `Perfil ativo: ${data.profile}. `) + data.notices.join(" ");
  };
  const request = async (preferences?: PerformanceStatus["preferences"]) => {
    if (!token) { status.textContent = "Conecte ao servidor para ajustar desempenho."; return; }
    try {
      const response = await fetch("/api/performance", {
        method: preferences ? "PUT" : "GET",
        headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json" },
        ...(preferences ? { body: JSON.stringify(preferences) } : {}),
      });
      if (!response.ok) throw new Error(`Servidor respondeu ${response.status}`);
      render(await response.json() as PerformanceStatus);
    } catch (error) { status.textContent = `Não foi possível carregar ou salvar desempenho: ${String(error)}`; }
  };
  document.addEventListener("luna:settings-opened", () => void request());
  document.getElementById("performance-save")!.addEventListener("click", () => void request({ profile: profile.value, model: model.value, voice: voice.value }));
  document.getElementById("performance-reset")!.addEventListener("click", () => void request({ profile: "automatic", model: "", voice: "" }));
}
