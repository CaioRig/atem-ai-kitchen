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

export function youtubeVideoId(value: string): string | undefined {
  try {
    const url = new URL(value);
    if (url.protocol !== "https:" || url.username || url.password || url.port) return;
    let id: string | null = null;
    if (url.hostname === "youtu.be" && url.pathname.split("/").length === 2) id = url.pathname.slice(1);
    else if (["youtube.com", "www.youtube.com", "m.youtube.com"].includes(url.hostname)) {
      if (url.pathname === "/watch") id = url.searchParams.get("v");
      else if (/^\/(shorts|embed)\/[^/]+$/.test(url.pathname)) id = url.pathname.split("/")[2];
    }
    return id && /^[a-zA-Z0-9_-]{11}$/.test(id) ? id : undefined;
  } catch { return; }
}

export class StageView {
  mode: StageMode = "face";
  private version = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private loadTimer: ReturnType<typeof setTimeout> | undefined;
  private player: Player | undefined;
  private ready = false;
  private ignoreReplies = false;
  private lastReply: { text: string; kind: "reply" | "recipe" } | undefined;
  private lastRecipe = "";
  private opener: HTMLElement | undefined;
  private items: VideoItem[] = [];
  private readonly area = document.getElementById("stage-content")!;
  private readonly scroll = document.getElementById("stage-scroll")!;
  private readonly title = document.getElementById("stage-title")!;
  constructor(private notice: (text: string) => void) {
    document.getElementById("stage-back")!.addEventListener("click", () => this.close());
    this.onOpen("video-search", () => this.results("", [], "Pesquise no YouTube ou cole o link de um vídeo para assistir aqui."));
    this.onOpen("recipes-open", () => {
      this.transition("recipe", "Receitas");
      if (this.lastRecipe) this.renderText(this.lastRecipe, true);
      else this.renderText("Peça uma receita para a Luna.\nExperimente dizer: “hey Luna, como faço panquecas?” ou “hey Luna, o que posso cozinhar com o que tenho na despensa?”");
    });
    this.onOpen("answer-open", () => {
      if (this.lastReply) {
        this.ignoreReplies = false;
        this.showReply(this.lastReply.text, this.lastReply.kind);
      }
    });
  }
  onOpen(id: string, open: () => void): void {
    const button = document.getElementById(id)!;
    button.addEventListener("click", () => {
      this.opener = button;
      open();
      if (this.mode !== "face") this.scroll.focus();
    });
  }
  begin(): void { this.ignoreReplies = false; this.transition("face", ""); }
  private transition(mode: StageMode, title: string): void {
    const restore = mode === "face" && this.area.contains(document.activeElement);
    ++this.version;
    clearTimeout(this.timer);
    clearTimeout(this.loadTimer);
    this.timer = undefined;
    if (this.player) { this.player.destroy(); this.player = undefined; }
    this.ready = false;
    this.mode = mode;
    this.area.dataset.mode = mode;
    this.scroll.replaceChildren();
    this.scroll.scrollTop = 0;
    this.title.textContent = title;
    this.area.classList.toggle("hidden", mode === "face");
    document.getElementById("portrait")!.classList.toggle("hidden", mode !== "face");
    document.getElementById("app")!.classList.toggle("show-content", mode !== "face");
    for (const id of ["home", "recipes-open", "video-search"]) {
      const selected = id === "recipes-open" ? mode === "recipe"
        : id === "video-search" ? mode === "results" || mode === "video"
        : mode === "face" || mode === "reply";
      const button = document.getElementById(id)!;
      if (selected) button.setAttribute("aria-current", "page");
      else button.removeAttribute("aria-current");
    }
    if (restore) (this.opener?.getClientRects().length ? this.opener : document.getElementById("home"))?.focus();
    if (mode === "face") this.opener = undefined;
  }
  close(): void {
    this.ignoreReplies = true;
    this.items = [];
    this.transition("face", "");
  }
  reply(text: string, kind: "reply" | "recipe" = "reply"): void {
    if (!text) return;
    this.lastReply = { text, kind };
    if (kind === "recipe") this.lastRecipe = text;
    const preview = text.replace(/(?:^|\n)\s*#{1,6}\s+/g, " ").replace(/\*\*/g, "").replace(/\s+/g, " ").trim();
    document.getElementById("answer-preview")!.textContent = preview.length > 155 ? `${preview.slice(0, 152).trimEnd()}…` : preview;
    document.getElementById("answer-open")!.classList.remove("hidden");
    if (!this.ignoreReplies) this.showReply(text, kind);
  }
  private showReply(text: string, kind: "reply" | "recipe"): void {
    if (this.mode !== kind) this.transition(kind, kind === "recipe" ? "Receita da Luna" : "Luna responde");
    this.renderText(text, kind === "recipe");
  }
  private renderText(text: string, recipe = false): void {
    if (recipe) {
      this.renderRecipe(text);
      return;
    }
    this.scroll.replaceChildren(...text.split(/\n+/).filter(Boolean).map(line => {
      const node = document.createElement("p");
      node.textContent = line.replace(/^#{1,6}\s+/, "").replace(/\*\*/g, "");
      return node;
    }));
  }
  private renderRecipe(text: string): void {
    const nodes: HTMLElement[] = [];
    let section = "";
    let list: HTMLOListElement | HTMLUListElement | undefined;
    for (const line of text.split(/\n+/).map(line => line.trim()).filter(Boolean)) {
      const clean = line.replace(/^#{1,6}\s+/, "").replace(/\*\*/g, "");
      const heading = /^(ingredientes|modo de preparo|modo de fazer|preparo|passo a passo|passos)\s*[:.]?$/i.exec(clean);
      const step = /^(\d+)[.)]\s+(.+)$/.exec(clean);
      if (heading) {
        const node = document.createElement("h3");
        node.textContent = clean;
        nodes.push(node);
        section = heading[1].toLowerCase() === "ingredientes" ? "ingredients" : "steps";
        list = undefined;
        continue;
      }
      const meta = /^(tempo(?: estimado| de preparo)?|porções|rendimento|dica|observação)\s*:/i.test(clean);
      const ingredient = section === "ingredients" && !meta && !step;
      if (ingredient || (step && section === "steps")) {
        if (!list) {
          list = document.createElement(ingredient ? "ul" : "ol");
          list.className = ingredient ? "recipe-list" : "recipe-list recipe-steps";
          nodes.push(list);
        }
        const row = document.createElement("li");
        const item = (step?.[2] ?? clean).replace(/^[-*•]\s+/, "");
        const quantity = ingredient ? /^([\d¼½¾]+(?:[.,/]\d+)?(?:\s*(?:g|kg|ml|l|xícaras?|colheres?|copos?|pitadas?|unidades?)\b)?)(?=\s)/i.exec(item) : null;
        if (step) row.value = Number(step[1]);
        if (quantity) {
          const amount = document.createElement("strong");
          amount.textContent = quantity[0];
          row.append(amount, item.slice(quantity[0].length));
        } else row.textContent = item;
        list.append(row);
        continue;
      }
      list = undefined;
      if (meta) section = "";
      const node = document.createElement(nodes.length === 0 && clean.length < 100 ? "h2" : "p");
      if (meta) node.className = "recipe-meta";
      node.textContent = clean;
      nodes.push(node);
    }
    this.scroll.replaceChildren(...nodes);
  }
  forecast(text: string, seconds?: number, source?: string): void {
    this.ignoreReplies = false;
    this.transition("forecast", "Previsão do tempo");
    const nodes = text.split(/\n+/).filter(Boolean).map(line => {
      const node = document.createElement("p");
      const day = /^([^:]+\b\d{1,2} de [^:]+):\s*(.+)$/i.exec(line);
      if (day) {
        const heading = document.createElement("strong");
        heading.textContent = day[1];
        node.className = "forecast-day";
        node.append(heading, day[2]);
      } else node.textContent = line;
      return node;
    });
    if (source) {
      const label = document.createElement("p");
      label.className = "muted";
      label.textContent = source;
      nodes.unshift(label);
    }
    this.scroll.replaceChildren(...nodes);
    const version = this.version;
    if (seconds !== undefined) this.timer = setTimeout(() => { if (version === this.version) this.close(); }, seconds * 1000);
  }
  results(query: string, items: VideoItem[], message: string): void {
    this.ignoreReplies = false;
    this.transition("results", "Vídeos de receitas");
    this.items = items.filter(validVideo).slice(0, 3);
    this.renderText(message || (this.items.length ? "Escolha um vídeo por toque ou diga: abra o primeiro vídeo." : "Pesquise no site oficial. Os resultados e a seleção ficam na outra aba."));
    this.items.forEach((item, index) => {
      const button = document.createElement("button");
      button.className = "pill video-result";
      button.textContent = `${index + 1}. ${item.title} · YouTube / ${item.source}`;
      button.addEventListener("click", () => { void this.select(index); });
      this.scroll.append(button);
    });
    const form = document.createElement("form");
    form.action = "https://www.youtube.com/results"; form.method = "get"; form.target = "_blank";
    form.setAttribute("rel", "noopener noreferrer");
    const label = document.createElement("label"); label.textContent = "O que você quer assistir?";
    const input = document.createElement("input"); input.name = "search_query"; input.type = "search";
    input.value = query.slice(0, 160); input.maxLength = 160; input.required = true;
    label.append(input);
    const button = document.createElement("button"); button.type = "submit"; button.className = "pill";
    button.textContent = "Pesquisar no YouTube · outra aba";
    form.append(label, button);
    for (const [name, value] of [["hl", "pt-BR"], ["gl", "BR"]]) {
      const hidden = document.createElement("input"); hidden.type = "hidden"; hidden.name = name; hidden.value = value; form.append(hidden);
    }
    form.addEventListener("submit", event => {
      input.value = input.value.trim();
      if (!input.value || !navigator.onLine) {
        event.preventDefault(); this.notice(input.value ? "Sem conexão. Tente novamente quando a internet voltar." : "Informe um tema para pesquisar."); return;
      }
      document.dispatchEvent(new Event("luna:video-play")); this.pause(true);
    });
    this.scroll.append(form);
    const linkForm = document.createElement("form");
    const linkLabel = document.createElement("label"); linkLabel.textContent = "Opcional: link do vídeo para assistir na Luna";
    const linkInput = document.createElement("input"); linkInput.type = "url"; linkInput.placeholder = "https://www.youtube.com/watch?v=…"; linkInput.required = true;
    linkLabel.append(linkInput);
    const open = document.createElement("button"); open.type = "submit"; open.className = "pill"; open.textContent = "Assistir aqui";
    linkForm.append(linkLabel, open);
    linkForm.addEventListener("submit", event => {
      event.preventDefault();
      const id = youtubeVideoId(linkInput.value.trim());
      if (!id) return this.notice("Use um link HTTPS de um vídeo do YouTube ou youtu.be.");
      this.items = [{ id, title: "Vídeo informado por você", source: "YouTube" }];
      void this.select(0);
    });
    this.scroll.append(linkForm);
  }
  private link(label: string, url: string): void {
    const link = document.createElement("a");
    link.className = "pill external-video"; link.textContent = label;
    link.href = url; link.target = "_blank"; link.rel = "noopener noreferrer";
    this.scroll.append(link);
  }
  async select(index: number): Promise<void> {
    const item = this.items[index];
    if (this.mode !== "results" || !item || !validVideo(item)) return this.notice("Escolha o vídeo no YouTube. A Luna não lê os resultados da outra aba.");
    this.transition("video", item.title);
    const version = this.version;
    document.dispatchEvent(new Event("luna:video-play"));
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    status.textContent = "Carregando player. Toque em reproduzir para iniciar; o navegador pode bloquear o início por voz.";
    const mount = document.createElement("div");
    mount.className = "video-mount";
    const actions = document.createElement("div");
    actions.className = "video-actions";
    const play = document.createElement("button");
    const pause = document.createElement("button");
    for (const button of [play, pause]) {
      button.type = "button";
      button.className = "pill";
      button.disabled = true;
      actions.append(button);
    }
    play.textContent = "Reproduzir";
    pause.textContent = "Pausar";
    play.addEventListener("click", () => this.resume());
    pause.addEventListener("click", () => this.pause());
    this.scroll.append(status, actions, mount);
    this.link("Abrir este vídeo no YouTube", `https://www.youtube.com/watch?v=${item.id}`);
    const fail = () => {
      this.ready = false;
      play.disabled = pause.disabled = true;
      status.textContent = "Não foi possível reproduzir aqui. A conexão ou a incorporação pode estar bloqueada. Use o link para abrir no YouTube.";
    };
    const timeout = setTimeout(() => { if (version === this.version && !this.ready) fail(); }, 15000);
    this.loadTimer = timeout;
    try {
      const api = await youtube();
      if (version !== this.version) return;
      this.player = new api.Player(mount, {
        videoId: item.id, width: "100%", height: "300",
        playerVars: { autoplay: 0, playsinline: 1, origin: location.origin },
        events: {
          onReady: () => {
            if (version !== this.version) return;
            this.ready = true;
            clearTimeout(timeout);
            play.disabled = false;
            status.textContent = "Pronto para reproduzir.";
          },
          onError: () => { if (version === this.version) fail(); },
          onAutoplayBlocked: () => {
            if (version !== this.version) return;
            play.disabled = false;
            pause.disabled = true;
            status.textContent = "O navegador bloqueou o início automático. Toque em reproduzir no vídeo.";
          },
          onStateChange: (event: PlayerEvent) => {
            if (version !== this.version) return;
            const labels: Record<number, string> = { 0: "Vídeo concluído", 1: "Em reprodução", 2: "Pausado", 3: "Carregando vídeo…" };
            if (event.data === undefined || !labels[event.data]) return;
            status.textContent = labels[event.data];
            play.disabled = event.data === 1 || event.data === 3;
            pause.disabled = event.data !== 1;
            if (event.data === 1) document.dispatchEvent(new Event("luna:video-play"));
          },
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
