import test from "node:test";
import assert from "node:assert/strict";

import { Backoff, MOODS, audioError, canCancel, displayMood, idleStatus, memoryKindLabel, memoryToast, micLabel, moodFor, moodLabel, resolveToken, statusText } from "../public/js/state.js";

test("todo rosto tem um nome em pt-BR para o indicador", () => {
  for (const mood of MOODS) {
    const label = moodLabel(mood);
    assert.equal(typeof label, "string");
    assert.ok(label.length > 0, `sem nome para ${mood}`);
  }
  assert.equal(moodLabel("idle"), "Aguardando");
  assert.equal(moodLabel("listening"), "Escutando");
  assert.equal(moodLabel("processing"), "Pensando");
  assert.equal(moodLabel("processing", "replying"), "Respondendo");
  assert.equal(moodLabel("processing", "synthesizing"), "Preparando");
  assert.equal(moodLabel("speaking"), "Falando");
});

test("fala: estados canceláveis, texto de estado e aviso de voz indisponível", () => {
  assert.equal(canCancel("synthesizing"), true);
  assert.equal(canCancel("speaking"), true);
  assert.equal(canCancel("idle"), false);
  assert.equal(canCancel(null), false);
  assert.equal(statusText("synthesizing", "x"), "Preparando a voz…");
  assert.equal(statusText("speaking", "x"), "Falando");
  const ok = { available: true, reason: "" };
  const mic = { state: "on", message: "" };
  assert.equal(idleStatus(mic, ok, "hey Luna", ok, ok, ok), "Aguardando ativação · diga “hey Luna”");
  assert.equal(idleStatus(mic, ok, "hey Luna", ok, ok, { available: false, reason: "modelo ausente" }), "Aguardando ativação · diga “hey Luna” · voz indisponível");
  assert.equal(idleStatus(mic, ok, "hey Luna", ok, { available: false, reason: "" }, { available: false, reason: "" }), "Aguardando ativação · diga “hey Luna” · IA indisponível");
});

test("mapeia estados do servidor para os 8 rostos", () => {
  assert.equal(moodFor("idle"), "idle");
  assert.equal(moodFor("wake"), "listening");
  assert.equal(moodFor("listening"), "listening");
  assert.equal(moodFor("transcribing"), "processing");
  assert.equal(moodFor("thinking"), "processing");
  assert.equal(moodFor("synthesizing"), "processing");
  assert.equal(moodFor("speaking"), "speaking");
  assert.equal(moodFor("media"), "media");
  assert.equal(moodFor("offline"), "offline");
  assert.equal(moodFor("error"), "error");
  assert.equal(MOODS.length, 8);
});

test("rosto offline só depois do período de tolerância", () => {
  assert.equal(displayMood("idle", true, 0), "idle");
  assert.equal(displayMood("idle", false, 500), "idle");
  assert.equal(displayMood("idle", false, 1500), "offline");
  assert.equal(displayMood(null, false, 0), "offline");
  assert.equal(displayMood(null, true, 0), "booting");
});

test("texto de estado usa a palavra de ativação e prioriza o detalhe", () => {
  assert.match(statusText("idle", "computador"), /computador/);
  assert.equal(statusText("thinking", "x", "Consultando clima"), "Consultando clima");
  assert.equal(statusText(null, "x"), "Inicializando…");
});

test("estado em espera explica microfone, detector e palavra de ativação", () => {
  const ok = { available: true, reason: "" };
  assert.equal(idleStatus({ state: "on", message: "" }, ok, "hey Jarvis"), "Aguardando ativação · diga “hey Jarvis”");
  assert.equal(idleStatus({ state: "off", message: "" }, ok, "x"), "Microfone desativado");
  assert.match(idleStatus({ state: "unavailable", message: "nenhum microfone encontrado" }, ok, "x"), /^Microfone indisponível · nenhum/);
  assert.match(idleStatus({ state: "insecure", message: "" }, ok, "x"), /HTTPS/);
  assert.match(idleStatus({ state: "denied", message: "" }, ok, "x"), /permissão negada/);
  assert.match(idleStatus({ state: "on", message: "" }, { available: false, reason: "modelo ausente" }, "x"), /indisponível · modelo ausente/);
  assert.equal(statusText("wake", "x"), "Palavra detectada");
  assert.equal(micLabel("on", "USB Mic"), "Microfone ativo · USB Mic");
});

test("backoff cresce, satura em 30 s e reinicia", () => {
  const b = new Backoff(1000, 30000, 0.3, () => 0);
  assert.deepEqual([b.next(), b.next(), b.next(), b.next(), b.next(), b.next()], [1000, 2000, 4000, 8000, 16000, 30000]);
  assert.equal(b.next(), 30000);
  b.reset();
  assert.equal(b.next(), 1000);
  const jittered = new Backoff(1000, 30000, 0.3, () => 1);
  assert.equal(jittered.next(), 1300);
  assert.equal(jittered.max(), 39000);
});

test("token vem do fragmento da URL ou do armazenamento", () => {
  assert.deepEqual(resolveToken("#token=abc%20d", null), { token: "abc d", fromHash: true });
  assert.deepEqual(resolveToken("#x=1&token=zzz", "old"), { token: "zzz", fromHash: true });
  assert.deepEqual(resolveToken("", "stored"), { token: "stored", fromHash: false });
  assert.deepEqual(resolveToken("", ""), { token: null, fromHash: false });
});

test("transcrição: aviso em espera, texto de escuta e estados canceláveis", async () => {
  const { canCancel } = await import("../public/js/state.js");
  const ok = { available: true, reason: "" };
  assert.equal(idleStatus({ state: "on", message: "" }, ok, "x", { available: false, reason: "modelo ausente" }), "Aguardando ativação · diga “x” · transcrição indisponível");
  assert.equal(idleStatus({ state: "on", message: "" }, ok, "x", ok), "Aguardando ativação · diga “x”");
  assert.equal(statusText("listening", "x"), "Ouvindo · pode falar");
  assert.equal(statusText("transcribing", "x"), "Transcrevendo…");
  assert.equal(statusText("idle", "x", "Transcrição concluída"), "Transcrição concluída");
  assert.deepEqual(["wake", "listening", "transcribing"].map(canCancel), [true, true, true]);
  assert.deepEqual(["idle", "booting", null].map(canCancel), [false, false, false]);
});

test("erro de áudio: microfone sem acesso ou falha na transcrição", () => {
  assert.deepEqual(["denied", "unavailable", "insecure"].map((m) => audioError(m)), [true, true, true]);
  assert.deepEqual(["on", "off", "starting", "blocked"].map((m) => audioError(m)), [false, false, false, false]);
  assert.equal(audioError("on", "Falha na transcrição"), true);
  assert.equal(audioError("on", "Transcrição concluída"), false);
});

test("IA local: estados pensando/respondendo, cancelamento e aviso em espera", async () => {
  const { canCancel } = await import("../public/js/state.js");
  const ok = { available: true, reason: "" };
  assert.equal(moodFor("replying"), "processing");
  assert.equal(moodLabel("processing", "thinking"), "Pensando");
  assert.equal(moodLabel("processing", "replying"), "Respondendo");
  assert.equal(moodLabel("idle", "replying"), "Aguardando");
  assert.equal(statusText("thinking", "x"), "Pensando…");
  assert.equal(statusText("replying", "x"), "Respondendo…");
  assert.equal(statusText("idle", "x", "Pronta"), "Pronta");
  assert.deepEqual(["thinking", "replying", "synthesizing", "speaking"].map(canCancel), [true, true, true, true]);
  assert.deepEqual(["idle", "media", "offline", null].map(canCancel), [false, false, false, false]);
  assert.equal(idleStatus({ state: "on", message: "" }, ok, "x", ok, { available: false, reason: "Ollama desligado" }), "Aguardando ativação · diga “x” · IA indisponível");
  assert.equal(idleStatus({ state: "on", message: "" }, ok, "x", ok, ok), "Aguardando ativação · diga “x”");
  assert.equal(idleStatus({ state: "on", message: "" }, ok, "x", { available: false, reason: "" }, { available: false, reason: "" }), "Aguardando ativação · diga “x” · transcrição indisponível");
});

test("memória: rótulos dos tipos, avisos e botão", async () => {
  const { memoryKindLabel, memoryToast, memoryButtonLabel } = await import("../public/js/state.js");
  assert.equal(memoryKindLabel("preference"), "Preferência");
  assert.equal(memoryKindLabel("kitchen"), "Cozinha");
  assert.equal(memoryKindLabel("instruction"), "Instrução");
  assert.equal(memoryKindLabel("note"), "Lembrete");
  assert.equal(memoryKindLabel("outro"), "outro");
  assert.equal(memoryToast("saved"), "Memória salva");
  assert.equal(memoryToast("deleted"), "Memória apagada");
  assert.equal(memoryToast("cleared", 1), "1 memória apagada");
  assert.equal(memoryToast("cleared", 3), "3 memórias apagadas");
  assert.equal(memoryToast("list"), "");
  assert.equal(memoryToast("confirm_clear"), "");
  assert.equal(memoryButtonLabel(0), "Memória");
  assert.equal(memoryButtonLabel(4), "Memória · 4");
});

test("ingredientes (fase 7): tipo na lista e avisos das ações de estoque", () => {
  assert.equal(memoryKindLabel("ingredient"), "Ingrediente");
  assert.equal(memoryToast("stock_saved"), "Ingredientes registrados");
  assert.equal(memoryToast("stock_updated"), "Ingredientes atualizados");
  assert.equal(memoryToast("stock_removed"), "Ingredientes removidos");
  assert.equal(memoryToast("list"), "");
});

test("clima (fase 8): texto do cartão e linha de atualização distinguem consulta, dados antigos e offline", async () => {
  const { weatherCardText, weatherUpdatedLabel } = await import("../public/js/state.js");
  assert.equal(weatherCardText({ available: false }), "Sem dados");
  assert.equal(weatherCardText({ available: false, message: "Não consegui acessar a internet." }), "Sem dados · Não consegui acessar a internet.");
  assert.equal(weatherCardText({ available: true, summary: "23 °C · nublado", detail: "mín 15 °C · máx 27 °C" }), "23 °C · nublado · mín 15 °C · máx 27 °C");
  assert.equal(weatherUpdatedLabel({ available: false }), "Sem dados — nunca atualizado");
  assert.equal(weatherUpdatedLabel({ available: true, updated_at: null }), "Sem dados — nunca atualizado");
  const now = new Date(2026, 9, 12, 16, 0, 0);
  const at = (d, h, m) => new Date(2026, 9, d, h, m, 0).getTime() / 1000;
  assert.equal(weatherUpdatedLabel({ available: true, updated_at: at(12, 14, 32) }, now), "Atualizado às 14:32");
  assert.equal(weatherUpdatedLabel({ available: true, updated_at: at(11, 9, 5) }, now), "Atualizado ontem às 09:05");
  assert.equal(weatherUpdatedLabel({ available: true, updated_at: at(9, 9, 5), stale: true }, now), "Dados antigos · atualizado em 09/10 às 09:05");
  assert.equal(
    weatherUpdatedLabel({ available: true, updated_at: at(12, 14, 32), status: "offline" }, now),
    "Atualizado às 14:32 · sem internet, usando dados salvos",
  );
});
