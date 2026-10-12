// Ponto de entrada da interface do quiosque.
// Sem token: modo local (interface + microfone, sem detecção), útil para demonstração.
// Com token: conecta ao servidor via WebSocket, envia o áudio do microfone e reflete os estados.
import { MicCapture } from "./audio.js";
import { PixelFace } from "./face.js";
import type { MemoryStatus, ServerMsg, ServerState } from "./protocol.js";
import {
  audioError,
  canCancel,
  displayMood,
  idleStatus,
  memoryToast,
  moodFor,
  moodLabel,
  resolveToken,
  statusText,
  type MicState,
  type Mood,
  type WakeInfo,
} from "./state.js";
import { Panel } from "./ui.js";
import { JarvisSocket } from "./ws.js";
import { SpeechPlayer } from "./speaker.js";

const TOKEN_KEY = "jarvis.token";
const WEATHER_KEY = "jarvis.weather";
const MIC_KEY = "atem.mic"; // "off" desliga o microfone de forma persistente
const DEFAULT_PHRASE = "hey Luna";

const DEBUG_KEYS: Record<string, ServerState> = {
  "1": "booting",
  "2": "idle",
  "3": "listening",
  "4": "thinking",
  "5": "speaking",
  "6": "media",
  "7": "offline",
  "8": "error",
  "9": "wake",
  "0": "transcribing",
};

interface MicView {
  state: MicState;
  message: string;
}

/** Aplica o rosto; em espera, microfone sem acesso ou falha de transcrição mostram "Erro de áudio",
 *  uma falha ao obter a resposta da IA mostra "Erro na resposta" e uma falha da voz "Erro na voz"
 *  (até a próxima pergunta ou fala). */
function showMood(
  panel: Panel,
  face: PixelFace,
  mood: Mood,
  mic: MicState,
  detail = "",
  state: ServerState | null = null,
  replyError = "",
  speechError = "",
): void {
  if (mood === "idle" && audioError(mic, detail)) {
    face.setMood("error");
    panel.setMood("error", "Erro de áudio");
  } else if (mood === "idle" && replyError) {
    face.setMood("error");
    panel.setMood("error", "Erro na resposta");
  } else if (mood === "idle" && speechError) {
    face.setMood("error");
    panel.setMood("error", "Erro na voz");
  } else {
    face.setMood(mood);
    panel.setMood(mood, moodLabel(mood, state));
  }
}

function setupCommon(): { panel: Panel; face: PixelFace } {
  const panel = new Panel();
  const face = new PixelFace(document.getElementById("face") as HTMLCanvasElement);
  face.start();
  panel.startClock();
  restoreWeather(panel);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "f") void document.documentElement.requestFullscreen?.();
  });
  return { panel, face };
}

/** Microfone com botão/tecla M para ligar e desligar; preferência persistida.
 *  `micLevelActive` desliga a animação pelo nível do microfone enquanto a Luna fala. */
function setupMic(
  panel: Panel,
  face: PixelFace,
  onFrame: (pcm: ArrayBuffer) => void,
  onChange: (view: MicView) => void,
  micLevelActive: () => boolean = () => true,
): MicCapture {
  const capture = new MicCapture({
    frame: onFrame,
    level: (rms) => {
      if (micLevelActive()) face.setLevel(Math.min(1, rms / 0.25));
    },
    state: (state, message) => {
      panel.setMic(state, message);
      onChange({ state, message });
    },
  });
  const toggle = () => {
    if (capture.active) {
      localStorage.setItem(MIC_KEY, "off");
      void capture.stop();
    } else {
      localStorage.setItem(MIC_KEY, "on");
      void capture.start();
    }
  };
  panel.onMicToggle(toggle);
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "m") toggle();
  });
  if (localStorage.getItem(MIC_KEY) === "off") panel.setMic("off");
  else void capture.start();
  return capture;
}

/** Modo local: nenhum servidor; teclas 1–9/0 trocam o estado para demonstração. */
function runLocal(panel: Panel, face: PixelFace): void {
  let state: ServerState = "idle";
  let mic: MicView = { state: "off", message: "" };
  const wake: WakeInfo = { available: false, reason: "sem servidor (modo local)" };
  const apply = () => {
    showMood(panel, face, moodFor(state), mic.state);
    panel.setStatus(state === "idle" ? idleStatus(mic, wake, DEFAULT_PHRASE) : statusText(state, DEFAULT_PHRASE));
  };
  panel.setConnection("reconnecting", "modo local, sem servidor");
  setupMic(
    panel,
    face,
    () => undefined, // sem servidor o áudio é descartado imediatamente
    (view) => {
      mic = view;
      apply();
    },
  );
  apply();
  document.addEventListener("keydown", (ev) => {
    const next = DEBUG_KEYS[ev.key];
    if (next) {
      state = next;
      apply();
    }
  });
}

function runConnected(panel: Panel, face: PixelFace, token: string): void {
  let serverState: ServerState | null = null;
  let detail = "";
  let wakeWord = DEFAULT_PHRASE;
  let wake: WakeInfo = { available: false, reason: "" };
  let transcription: WakeInfo = { available: false, reason: "" };
  let llm: WakeInfo = { available: false, reason: "" };
  let tts: WakeInfo = { available: false, reason: "" };
  let memory: MemoryStatus = { available: false, reason: "", count: 0 };
  let memoryAutoClose: number | undefined; // lista aberta por voz fecha sozinha
  let replyError = ""; // última falha da IA; some na próxima pergunta ou resposta
  let speechError = ""; // última falha da voz; some na próxima pergunta ou fala
  let debug = false;
  let connected = false;
  let disconnectedAt = performance.now();
  let mic: MicView = { state: "off", message: "" };

  const refresh = () => {
    showMood(
      panel,
      face,
      displayMood(serverState, connected, performance.now() - disconnectedAt),
      mic.state,
      detail,
      serverState,
      replyError,
      speechError,
    );
    if (!connected) panel.setStatus("Servidor indisponível");
    else if (serverState === "idle" && !detail && speechError) panel.setStatus(speechError);
    else if (serverState === "idle" && !detail) panel.setStatus(idleStatus(mic, wake, wakeWord, transcription, llm, tts));
    else panel.setStatus(statusText(serverState, wakeWord, detail));
    panel.setCancelVisible(connected && canCancel(serverState));
    panel.setRetryVisible(connected && serverState === "idle" && (replyError !== "" || speechError !== ""));
  };
  window.setInterval(refresh, 500);

  // Voz da Luna: o servidor sintetiza, o quiosque reproduz e confirma o fim (speak_done).
  const player = new SpeechPlayer({
    done: (id, played, error) => {
      if (!played) console.warn("reprodução da voz falhou:", error);
      socket.send({ type: "speak_done", id, played, error });
    },
    level: (rms) => face.setLevel(Math.min(1, rms / 0.2)),
  });
  document.addEventListener("pointerdown", () => player.unlock());
  document.addEventListener("keydown", () => player.unlock());

  const proto = location.protocol === "https:" ? "wss" : "ws";
  const socket = new JarvisSocket(`${proto}://${location.host}/ws`, token, {
    connected: () => {
      connected = true;
      panel.setConnection("online");
      refresh();
    },
    disconnected: (reason, retryInMs) => {
      if (connected) disconnectedAt = performance.now();
      connected = false;
      player.stop(); // sem servidor, nada confirma o fim da fala
      panel.setConnection("reconnecting", `${reason}, nova tentativa em ${Math.round(retryInMs / 1000)} s`);
      refresh();
    },
    binary: (data) => player.enqueue(data),
    message: (msg: ServerMsg) => {
      switch (msg.type) {
        case "welcome":
          wakeWord = msg.wake?.phrase || msg.wake_word;
          wake = msg.wake ?? { available: false, reason: "servidor sem detecção" };
          transcription = msg.transcription ?? { available: false, reason: "servidor sem transcrição" };
          llm = msg.llm ?? { available: false, reason: "servidor sem IA" };
          tts = msg.tts ?? { available: false, reason: "servidor sem voz" };
          memory = msg.memory ?? { available: false, reason: "servidor sem memória", count: 0 };
          if (!transcription.available) console.warn("transcrição indisponível:", transcription.reason);
          if (!llm.available) console.warn("IA indisponível:", llm.reason);
          if (!tts.available) console.warn("voz indisponível:", tts.reason);
          if (!memory.available) console.warn("memória indisponível:", memory.reason);
          panel.setMemory(memory.available, memory.count);
          debug = msg.debug;
          serverState = msg.state;
          detail = msg.detail;
          break;
        case "state":
          // Nova interação: limpa o texto anterior. Em "idle" ele permanece visível.
          if (canCancel(msg.state) && !canCancel(serverState)) panel.setHeard("");
          // Nova pergunta a caminho da IA: limpa a resposta e as falhas anteriores.
          if (msg.state === "thinking" && serverState !== "thinking") {
            panel.setReply("");
            replyError = "";
            speechError = "";
          }
          serverState = msg.state;
          detail = msg.detail;
          break;
        case "transcript":
          panel.setHeard(msg.text, msg.final);
          break;
        case "reply":
          panel.setReply(msg.text, msg.final ?? true);
          if (msg.final !== false) {
            replyError = "";
            llm = { available: true, reason: "" };
          }
          break;
        case "reply_error":
          replyError = msg.message;
          llm = { available: false, reason: msg.message };
          panel.setReply(msg.message);
          break;
        case "speak_start":
          speechError = "";
          player.start(msg.id, msg.sample_rate);
          break;
        case "speak_end":
          player.end(msg.id);
          break;
        case "speak_stop":
          player.stop();
          break;
        case "speak_error":
          speechError = msg.message; // o texto da resposta continua na tela
          player.stop();
          break;
        case "memory": {
          // Confirmação visual de memória salva/apagada; a lista na tela acompanha as mudanças.
          if (typeof msg.count === "number") {
            memory = { ...memory, count: msg.count };
            panel.setMemory(memory.available, msg.count);
          }
          if (msg.action === "list") {
            panel.renderMemories(msg.items ?? [], msg.topic ?? "");
            if (msg.spoken && !panel.memoryVisible) {
              panel.setMemoryVisible(true); // pedido por voz: mostra a lista por um tempo
              window.clearTimeout(memoryAutoClose);
              memoryAutoClose = window.setTimeout(() => panel.setMemoryVisible(false), 20000);
            }
          } else if (panel.memoryVisible && msg.action !== "confirm_clear" && msg.action !== "error") {
            socket.send({ type: "memory_list" });
          }
          const toast = memoryToast(msg.action, msg.count ?? 0);
          if (toast) panel.showToast(toast);
          break;
        }
        case "error":
          console.warn("servidor:", msg.code, msg.message);
          break;
      }
      refresh();
    },
  });
  panel.setConnection("connecting");
  socket.start();

  setupMic(
    panel,
    face,
    (pcm) => {
      socket.sendBinary(pcm); // descartado se não houver conexão
    },
    (view) => {
      mic = view;
      refresh();
    },
    () => !player.playing, // enquanto a Luna fala, o rosto segue a voz, não o microfone
  );

  // Cancelar a captura/transcrição/resposta/fala em andamento: botão ou tecla Esc. Volta a aguardar a ativação.
  const cancel = () => {
    if (canCancel(serverState)) socket.send({ type: "cancel" });
  };
  panel.onCancel(cancel);
  // Tentar novamente (botão ou tecla R): reenvia à IA a última pergunta que falhou ou, se foi a voz
  // que falhou, fala de novo a última resposta sem consultar a IA.
  const retry = () => {
    if (serverState !== "idle") return;
    if (replyError) socket.send({ type: "retry" });
    else if (speechError) socket.send({ type: "replay" });
  };
  panel.onRetry(retry);
  // Memória: botão "Memória" (tecla L) abre a lista; "Esquecer" apaga uma; "Apagar todas" pede um segundo toque.
  const toggleMemory = () => {
    window.clearTimeout(memoryAutoClose);
    if (panel.memoryVisible) return panel.setMemoryVisible(false);
    if (!connected || !memory.available) return;
    panel.setMemoryVisible(true);
    socket.send({ type: "memory_list" });
  };
  panel.onMemoryToggle(toggleMemory);
  panel.onMemoryClose(() => panel.setMemoryVisible(false));
  panel.onMemoryDelete((id) => socket.send({ type: "memory_delete", id }));
  panel.onMemoryClear(() => socket.send({ type: "memory_clear", confirm: true }));
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") {
      if (panel.memoryVisible) return panel.setMemoryVisible(false);
      return cancel();
    }
    if (ev.key === "r") return retry();
    if (ev.key === "l") return toggleMemory();
    const state = DEBUG_KEYS[ev.key];
    if (state && debug) socket.send({ type: "set_state", state, detail: "" });
  });
}

function main(): void {
  const { panel, face } = setupCommon();
  const { token, fromHash } = resolveToken(location.hash, localStorage.getItem(TOKEN_KEY));
  if (fromHash && token) {
    localStorage.setItem(TOKEN_KEY, token);
    history.replaceState(null, "", location.pathname + location.search);
  }
  if (token) runConnected(panel, face, token);
  else runLocal(panel, face);
}

function restoreWeather(panel: Panel): void {
  try {
    const raw = localStorage.getItem(WEATHER_KEY);
    if (!raw) return panel.setWeather("Sem dados", null);
    const saved = JSON.parse(raw) as { text: string; updatedAt: string };
    panel.setWeather(saved.text, new Date(saved.updatedAt));
  } catch {
    panel.setWeather("Sem dados", null);
  }
}

main();
