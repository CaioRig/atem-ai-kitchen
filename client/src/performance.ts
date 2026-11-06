import type { PixelFace } from "./face.js";

interface Recommendation {
  name: string;
  label: string;
  size_bytes: number;
  ram_gib: number;
  vram_gib: number;
  installed: boolean;
  fits_estimate: boolean;
}

interface PerformanceStatus {
  preferences: { profile: string; model: string; voice: string; llm_enabled?: boolean | null };
  hardware: { cpu: string; cores: number | null; available_threads: number; ram_available: number | null; gpu: string | null };
  profile: string;
  inference: string;
  model: string;
  tts_engine: string;
  tts_voice: string;
  models: string[];
  manual_model: string;
  recommendations: Recommendation[];
  llm: { enabled: boolean; available: boolean; notice: string; reason: string };
  notices: string[];
  restart_required: boolean;
}

export function setupPerformance(token: string, face: PixelFace): void {
  const profile = document.getElementById("performance-profile") as HTMLSelectElement;
  const model = document.getElementById("performance-model") as HTMLSelectElement;
  const voice = document.getElementById("performance-voice") as HTMLSelectElement;
  const llm = document.getElementById("performance-llm") as HTMLSelectElement;
  const actions = document.getElementById("performance-model-actions")!;
  const recommendations = document.getElementById("performance-recommendations")!;
  const showModels = document.getElementById("performance-show-models") as HTMLButtonElement;
  const status = document.getElementById("performance-status")!;
  const nav = navigator as Navigator & { deviceMemory?: number };
  const weakClient = (navigator.hardwareConcurrency || 2) <= 4 || (nav.deviceMemory !== undefined && nav.deviceMemory <= 4);
  face.setFps(weakClient ? 8 : 20);
  document.documentElement.classList.toggle("economic-client", weakClient);
  const toggleRecommendations = (show: boolean) => {
    recommendations.classList.toggle("hidden", !show);
    showModels.setAttribute("aria-expanded", String(show));
  };
  showModels.addEventListener("click", () => toggleRecommendations(recommendations.classList.contains("hidden")));
  document.getElementById("performance-keep-model")!.addEventListener("click", () => {
    model.value = model.dataset.saved ?? "";
    toggleRecommendations(false);
  });
  const render = (data: PerformanceStatus) => {
    profile.value = data.preferences.profile;
    const names = new Set([...data.models, ...data.recommendations.map(entry => entry.name)]);
    if (data.preferences.model) names.add(data.preferences.model);
    model.replaceChildren(new Option(data.manual_model ? "Configuração do servidor" : "Seleção automática", ""),
      ...[...names].map(name => new Option(name, name)));
    model.value = data.preferences.model;
    model.dataset.saved = data.preferences.model;
    voice.value = data.preferences.voice;
    voice.disabled = data.tts_engine !== "kokoro";
    llm.value = typeof data.preferences.llm_enabled === "boolean" ? String(data.preferences.llm_enabled) : "";
    const issue = [data.llm.notice, data.llm.enabled && !data.llm.available ? data.llm.reason : ""].filter(Boolean);
    actions.classList.toggle("hidden", !issue.length && !data.recommendations.length);
    showModels.disabled = !data.recommendations.length;
    toggleRecommendations(false);
    recommendations.replaceChildren(...data.recommendations.map(entry => {
      const row = document.createElement("li");
      const button = document.createElement("button");
      button.type = "button";
      button.className = "pill";
      button.textContent = `Selecionar ${entry.label}`;
      button.addEventListener("click", () => {
        model.value = entry.name;
        model.focus();
        status.textContent = "Confirme em Salvar desempenho. A escolha será aplicada após reiniciar o servidor.";
      });
      row.append(button, ` ${(entry.size_bytes / 1e9).toFixed(2)} GB; estimativa: ${entry.ram_gib} GiB RAM em CPU ou ${entry.vram_gib} GiB VRAM com GPU compatível e RAM para os serviços. `
        + (entry.installed ? "Já instalado. " : "Requer download pelo start.bat no modo online. ")
        + (entry.fits_estimate ? "" : "A estimativa excede os recursos livres detectados. ")
        + "Pode ter menor precisão em tarefas complexas.");
      return row;
    }));
    const h = data.hardware;
    const ram = h.ram_available === null ? "RAM desconhecida" : `${(h.ram_available / 1024 ** 3).toFixed(1)} GB RAM disponível`;
    document.getElementById("performance-hardware")!.textContent =
      `Servidor: ${h.cpu || "CPU desconhecida"}; ${h.cores ?? "?"} núcleos; ${h.available_threads} threads disponíveis; ${ram}; ${h.gpu || "GPU não identificada"}. ` +
      `IA: ${data.model}; voz: ${data.tts_engine}/${data.tts_voice}. Inferência ${data.inference === "local" ? "neste servidor" : "remota (hardware desconhecido)"}. ` +
      `Interface neste dispositivo: ${weakClient ? "econômica, 8 fps" : "completa, 20 fps"}.`;
    status.textContent = (data.restart_required ? "Salvo. Reinicie o servidor para aplicar. " : `Perfil ativo: ${data.profile}. `)
      + [...issue, ...data.notices].join(" ");
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
  document.getElementById("performance-save")!.addEventListener("click", () => void request({
    profile: profile.value, model: model.value, voice: voice.value,
    llm_enabled: llm.value ? llm.value === "true" : null,
  }));
  document.getElementById("performance-reset")!.addEventListener("click", () => void request({ profile: "automatic", model: "", voice: "" }));
}
