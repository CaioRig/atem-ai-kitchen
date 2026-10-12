# Palavra de ativação "hey Atem"

Pronúncia alvo: **"ei-tem"** (como o inglês *"hey, A-tem"*).

## Situação
**Em uso hoje: "hey Luna"** (`models/openwakeword/hey_luna.onnx`, modelo da coleção da
comunidade Home Assistant, licença MIT, baixado por `.\scripts\prepare-models.ps1`). Foi escolhido
porque soa igual em português e, no teste com TTS (`docs/microfone.md`), detectou a frase com
score 0,87 nas vozes em inglês e em pt-BR e ignorou "hey Jarvis" e frases em português.
"hey Atem" continua sendo a meta e exige o treino abaixo.

O openWakeWord detecta uma frase com um pequeno classificador treinado só para ela.
Não existe modelo pronto para "hey Atem" (nem nenhum em português), então a frase exige um
**modelo customizado**. Se `[wake] model` apontar para `hey_atem.onnx` antes de ele existir, o servidor reporta
`wake.available = false` com o motivo e a interface mostra
"Detecção de voz indisponível · modelo nao encontrado: hey_atem.onnx; …". Nada é simulado.

Configuração em `config/settings.toml` quando o modelo estiver treinado:
```toml
[assistant]
wake_word = "hey Atem"          # frase exibida
[wake]
model = "models/openwakeword/hey_atem.onnx"
threshold = 0.5
```
Hoje os valores são `wake_word = "hey Luna"` e `model = "models/openwakeword/hey_luna.onnx"`.
O oficial `hey_jarvis_v0.1.onnx` (`.\scripts\prepare-models.ps1 hey_jarvis`) também serve.

## Treinar o modelo (procedimento oficial, ~20 min no Google Colab)
O treino gera milhares de amostras sintéticas da frase com o TTS Piper, mistura com ruído e
reverberação e treina o classificador sobre o modelo de embedding compartilhado. Requer GPU e
internet; **só roda em Linux** (o gerador de amostras do Piper não tem build Windows), por
isso o caminho mais simples é o Colab gratuito.

1. Abra o notebook oficial `notebooks/automatic_model_training.ipynb` do repositório
   `dscripka/openWakeWord` no Google Colab (botão "Open in Colab" no README do projeto)
   e escolha um runtime com GPU (T4).
2. Na célula de configuração, substitua os valores por estes. O TTS é em inglês, então a
   frase entra com **grafias fonéticas** que produzem "ei-tem"; `target_phrase` aceita uma
   lista e todas as grafias viram exemplos positivos do mesmo modelo:
   ```python
   config["target_phrase"] = ["hey atem", "hey aytem", "hey eight em", "hey a tem"]
   config["model_name"] = "hey_atem"
   config["n_samples"] = 2000          # mais exemplos positivos = mais robusto (padrão 1000)
   config["n_samples_val"] = 1000
   config["steps"] = 10000
   config["target_accuracy"] = 0.7
   config["target_recall"] = 0.5
   # frases parecidas que NÃO devem ativar (reduzem falsos positivos)
   config["custom_negative_phrases"] = ["hey item", "hey adam", "hey tim", "a tem", "eight", "hey tem"]
   ```
   Se a célula do notebook não tiver `custom_negative_phrases`, adicione a linha; a chave
   existe em `examples/custom_model.yml` e é lida pelo `train.py`.
3. Execute as células na ordem (gerar amostras, aumentar, treinar). O resultado fica em
   `my_custom_model/hey_atem.onnx` (e `.tflite`, que não usamos).
4. Baixe `hey_atem.onnx` e copie para `E:\Atem\models\openwakeword\hey_atem.onnx`.
   Os modelos base `melspectrogram.onnx` e `embedding_model.onnx` já devem estar na pasta
   (`.\scripts\prepare-models.ps1` baixa só eles por padrão).
5. Reinicie o servidor. O log deve mostrar o modelo carregado e `GET /health` →
   `wake.available = true`, `wake.model = "hey_atem"`.

### Alternativa local (Linux/WSL com GPU)
```bash
git clone https://github.com/dscripka/openWakeWord && cd openWakeWord
pip install -e . && pip install -r requirements_train.txt  # torch, piper-sample-generator…
cp examples/custom_model.yml hey_atem.yml   # edite target_phrase, model_name etc. como acima
python openwakeword/train.py --training_config hey_atem.yml --generate_clips
python openwakeword/train.py --training_config hey_atem.yml --augment_clips
python openwakeword/train.py --training_config hey_atem.yml --train_model
```
O notebook baixa também os dados negativos (ACAV100M ~2000 h em features, AudioSet e FMA
como fundo, MIT RIR como reverberação); localmente é preciso baixá-los antes, como nas
primeiras células do notebook. Conte com dezenas de GB e algumas horas.

## Testar sem microfone
A voz do Windows é em inglês; escreva a frase de forma que ela diga "ei-tem":
```powershell
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$f = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, 'Sixteen', 'Mono')
$s.SetOutputToWaveFile("$env:TEMP\hey_atem.wav", $f); $s.Speak("Hey Aytem"); $s.Dispose()
.\.venv\Scripts\python scripts\check_wakeword.py "$env:TEMP\hey_atem.wav"
```
Saída esperada: `modelo=hey_atem score_max=0.9xx … -> DETECTADO`. Copie o WAV para
`tests/fixtures/hey_atem_16k.wav` para habilitar o teste `test_real_model_detects_hey_atem_sample`.

## Ajuste fino
- **Falsos negativos** (não ativa): baixe `threshold` para 0.3–0.4 ou treine com mais
  `n_samples`; confirme que as pessoas da casa dizem "ei-tem" e não "á-tem" (nesse caso
  acrescente a grafia "hey ah tem" em `target_phrase`).
- **Falsos positivos** (ativa sozinho): suba `threshold` para 0.6–0.7, aumente
  `custom_negative_phrases` com as palavras que disparam, ou suba `steps`.
- O gerador cria vozes variadas, mas todas com sotaque inglês. Se o modelo errar muito com
  falantes de português, grave ~50 amostras reais (16 kHz, mono) e adicione como positivos
  extras: o treino aceita clipes reais junto aos sintéticos (ver README do openWakeWord,
  seção *Training New Models*).
