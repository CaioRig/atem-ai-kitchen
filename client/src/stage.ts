/** Unico dono do conteudo principal, do prazo da previsao e do player. */
export type StageMode = "face" | "reply" | "recipe" | "forecast" | "results" | "video";
export interface VideoItem { id: string; title: string; source: string }
interface Player { destroy(): void; playVideo(): void; pauseVideo(): void }
interface PlayerEvent { target: Player; data?: number }
interface YouTubeAPI { Player: new (node: HTMLElement, options: object) => Player }
declare global { interface Window { YT?: YouTubeAPI; onYouTubeIframeAPIReady?: () => void } }

let apiLoading: Promise<YouTubeAPI> | undefined;
function youtube(): Promise<YouTubeAPI> {
  if (window.YT?.Player) return Promise.resolve(window.YT);
  if (!apiLoading) apiLoading = new Promise<YouTubeAPI>((resolve, reject) => {
    const script = document.createElement("script");
    const timeout = setTimeout(() => { script.remove(); reject(new Error("player indisponível")); }, 12000);
    window.onYouTubeIframeAPIReady = () => { clearTimeout(timeout); resolve(window.YT!); };
    script.onerror = () => { clearTimeout(timeout); script.remove(); reject(new Error("sem conexão com o player")); };
    script.src = "https://www.youtube.com/iframe_api";
    document.head.append(script);
  }).catch(error => { apiLoading = undefined; throw error; });
  return apiLoading;
}

export function validVideo(item: VideoItem): boolean {
  return typeof item?.id === "string" && /^[a-zA-Z0-9_-]{11}$/.test(item.id)
    && typeof item.title === "string" && typeof item.source === "string";
}

export class StageView {
  mode: StageMode = "face";
  private version = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private player: Player | undefined;
  private ready = false;
  private ignoreReplies = false;
  private items: VideoItem[] = [];
  private readonly area = document.getElementById("stage-content")!;
  private readonly scroll = document.getElementById("stage-scroll")!;
  private readonly title = document.getElementById("stage-title")!;
  constructor(private notice: (text: string) => void) {
    document.getElementById("stage-back")!.addEventListener("click", () => this.close());
  }
  begin(): void { this.ignoreReplies = false; this.transition("face", ""); }
  private transition(mode: StageMode, title: string): void {
    ++this.version;
    clearTimeout(this.timer);
    this.timer = undefined;
    if (this.player) { this.player.destroy(); this.player = undefined; }
    this.ready = false;
    this.mode = mode;
    this.scroll.replaceChildren();
    this.scroll.scrollTop = 0;
    this.title.textContent = title;
    this.area.classList.toggle("hidden", mode === "face");
    document.getElementById("face")!.classList.toggle("hidden", mode !== "face");
    document.getElementById("app")!.classList.toggle("show-content", mode !== "face");
    window.dispatchEvent(new Event("resize"));
  }
  close(): void { this.ignoreReplies = true; this.items = []; this.transition("face", ""); }
  reply(text: string, kind: "reply" | "recipe" = "reply"): void {
    if (!text || this.ignoreReplies) return;
    if (this.mode !== kind) this.transition(kind, kind === "recipe" ? "Na cozinha" : "Luna responde");
    this.renderText(text, kind === "recipe");
  }
  private renderText(text: string, recipe = false): void {
    const nodes = text.split(/\n+/).filter(Boolean).map(line => {
      const clean = line.replace(/^#{1,6}\s+/, "").replace(/\*\*/g, "");
      const heading = recipe && /^(ingredientes|modo de preparo|preparo|passo a passo|porções|rendimento)\b/i.test(clean);
      const node = document.createElement(heading ? "h3" : "p");
      node.textContent = clean;
      return node;
    });
    this.scroll.replaceChildren(...nodes);
  }
  forecast(text: string, seconds: number): void {
    this.ignoreReplies = false;
    this.transition("forecast", "Previsão do tempo");
    this.renderText(text);
    const version = this.version;
    this.timer = setTimeout(() => { if (version === this.version) this.close(); }, seconds * 1000);
  }
  results(query: string, items: VideoItem[], message: string): void {
    this.ignoreReplies = false;
    this.transition("results", "Vídeos de receitas");
    this.items = items.filter(validVideo).slice(0, 3);
    this.renderText(message || "Escolha um vídeo por toque ou diga: abra o primeiro vídeo.");
    this.items.forEach((item, index) => {
      const button = document.createElement("button");
      button.className = "pill video-result";
      button.textContent = `${index + 1}. ${item.title} · YouTube / ${item.source}`;
      button.addEventListener("click", () => { void this.select(index); });
      this.scroll.append(button);
    });
    this.link("Pesquisar no YouTube (abre outra aba)", "https://www.youtube.com/results?search_query=" + encodeURIComponent(query.slice(0, 160)));
  }
  private link(label: string, url: string): void {
    const link = document.createElement("a");
    link.className = "pill external-video"; link.textContent = label;
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    this.scroll.append(link);
  }
  async select(index: number): Promise<void> {
    const item = this.items[index];
    if (this.mode !== "results" || !item || !validVideo(item)) return this.notice("Nenhum resultado disponível nessa posição");
    this.transition("video", item.title);
    const version = this.version;
    document.dispatchEvent(new Event("luna:video-play"));
    const status = document.createElement("p");
    status.textContent = "Carregando player. Toque em reproduzir para iniciar; o navegador pode bloquear o início por voz.";
    const mount = document.createElement("div");
    mount.className = "video-mount";
    this.scroll.append(status, mount);
    this.link("Abrir este vídeo no YouTube", `https://www.youtube.com/watch?v=${item.id}`);
    const fail = () => { status.textContent = "Não foi possível reproduzir aqui. A conexão ou a incorporação pode estar bloqueada. Use o link para abrir no YouTube."; };
    const timeout = setTimeout(() => { if (version === this.version && !this.ready) fail(); }, 15000);
    try {
      const api = await youtube();
      if (version !== this.version) return;
      this.player = new api.Player(mount, {
        videoId: item.id, width: "100%", height: "300",
        playerVars: { autoplay: 0, playsinline: 1, origin: location.origin },
        events: {
          onReady: () => { if (version === this.version) { this.ready = true; clearTimeout(timeout); status.textContent = "Toque no player para reproduzir. Você também pode pedir: continue o vídeo."; } },
          onError: () => { if (version === this.version) fail(); },
          onAutoplayBlocked: () => { if (version === this.version) status.textContent = "O navegador bloqueou o início automático. Toque em reproduzir no vídeo."; },
          onStateChange: (event: PlayerEvent) => { if (version === this.version && event.data === 1) document.dispatchEvent(new Event("luna:video-play")); },
        },
      });
    } catch { if (version === this.version) fail(); }
    finally { if (version !== this.version) clearTimeout(timeout); }
  }
  pause(silent = false): void {
    if (this.player && this.ready) this.player.pauseVideo();
    else if (!silent) this.notice("Nenhum vídeo pronto para pausar");
  }
  resume(): void {
    if (this.player && this.ready) { document.dispatchEvent(new Event("luna:video-play")); this.player.playVideo(); }
    else this.notice("Nenhum vídeo pronto para continuar");
  }
}
