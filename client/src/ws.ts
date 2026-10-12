// Conexão WebSocket com autenticação, heartbeat e reconexão automática.
import { PROTOCOL_VERSION, type ServerMsg } from "./protocol.js";
import { Backoff } from "./state.js";

export const CLOSE_BAD_TOKEN = 4401;
const HEARTBEAT_MS = 10000;
const PONG_TIMEOUT_MS = 5000;

export interface SocketHandlers {
  message?: (msg: ServerMsg) => void;
  /** Quadro binário do servidor: áudio PCM da voz da Luna (ver `speak_start`). */
  binary?: (data: ArrayBuffer) => void;
  connected?: () => void;
  disconnected?: (reason: string, retryInMs: number) => void;
}

export class JarvisSocket {
  private ws: WebSocket | null = null;
  private backoff = new Backoff();
  private heartbeatTimer: number | undefined;
  private pongTimer: number | undefined;
  private reconnectTimer: number | undefined;
  private stopped = false;
  private authenticated = false;

  constructor(
    private readonly url: string,
    private token: string,
    private readonly handlers: SocketHandlers,
  ) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    this.clearTimers();
    this.ws?.close();
  }

  setToken(token: string): void {
    this.token = token;
    this.backoff.reset();
  }

  get isConnected(): boolean {
    return this.authenticated && this.ws?.readyState === WebSocket.OPEN;
  }

  send(obj: object): boolean {
    if (!this.isConnected || !this.ws) return false;
    this.ws.send(JSON.stringify(obj));
    return true;
  }

  /** Quadro binário (PCM do microfone). Descartado sem conexão ou com a fila cheia. */
  sendBinary(data: ArrayBuffer): boolean {
    if (!this.isConnected || !this.ws || this.ws.bufferedAmount > 256 * 1024) return false;
    this.ws.send(data);
    return true;
  }

  private connect(): void {
    let ws: WebSocket;
    try {
      ws = new WebSocket(this.url);
    } catch {
      this.scheduleReconnect("endereço inválido");
      return;
    }
    this.ws = ws;
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      ws.send(JSON.stringify({ type: "hello", token: this.token, client: "ui", version: PROTOCOL_VERSION }));
    };
    ws.onmessage = (ev: MessageEvent) => {
      if (typeof ev.data !== "string") {
        if (this.authenticated && ev.data instanceof ArrayBuffer) this.handlers.binary?.(ev.data);
        return;
      }
      let msg: ServerMsg;
      try {
        msg = JSON.parse(ev.data) as ServerMsg;
      } catch {
        return;
      }
      if (msg.type === "welcome") {
        this.authenticated = true;
        this.backoff.reset();
        this.startHeartbeat();
        this.handlers.connected?.();
      } else if (msg.type === "pong") {
        window.clearTimeout(this.pongTimer);
        this.pongTimer = undefined;
      }
      this.handlers.message?.(msg);
    };
    ws.onclose = (ev: CloseEvent) => {
      this.clearTimers();
      this.authenticated = false;
      if (this.stopped) return;
      const reason = ev.code === CLOSE_BAD_TOKEN ? "token recusado" : "conexão perdida";
      this.scheduleReconnect(reason, ev.code === CLOSE_BAD_TOKEN);
    };
    ws.onerror = () => {
      /* onclose é chamado em seguida */
    };
  }

  private scheduleReconnect(reason: string, slow = false): void {
    const delay = slow ? this.backoff.max() : this.backoff.next();
    this.handlers.disconnected?.(reason, delay);
    this.reconnectTimer = window.setTimeout(() => this.connect(), delay);
  }

  private startHeartbeat(): void {
    window.clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = window.setInterval(() => {
      if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
      this.ws.send(JSON.stringify({ type: "ping", t: Date.now() }));
      if (this.pongTimer === undefined) {
        this.pongTimer = window.setTimeout(() => this.ws?.close(4000, "sem pong"), PONG_TIMEOUT_MS);
      }
    }, HEARTBEAT_MS);
  }

  private clearTimers(): void {
    window.clearInterval(this.heartbeatTimer);
    window.clearTimeout(this.pongTimer);
    window.clearTimeout(this.reconnectTimer);
    this.heartbeatTimer = this.pongTimer = this.reconnectTimer = undefined;
  }
}
