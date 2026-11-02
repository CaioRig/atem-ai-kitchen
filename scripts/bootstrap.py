"""One startup path for Windows/Linux. Standard library until the isolated venv is ready."""
from __future__ import annotations

import argparse
import gc
import hashlib
import http.client
import importlib.metadata
import json
import logging
import os
import platform
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.request
import zipfile
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.downloads import download, sha256
from server.services.performance import GIB, Hardware, detect_hardware, hardware_key, initial_profile, is_local_inference

log = logging.getLogger("luna.bootstrap")
SCHEMA = 1


class BootstrapError(RuntimeError):
    pass


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def detect_server() -> Hardware:
    try:
        return detect_hardware()
    except Exception:
        log.warning("Detecção de hardware indisponível; recursos desconhecidos e limites conservadores")
        return Hardware(os=platform.system(), architecture=platform.machine())


def load_catalog(root: Path) -> dict:
    catalog = read_json(root / "config/model-catalog.json")
    try:
        if (catalog.get("schema_version") != 1 or not isinstance(catalog["revision"], str)
                or not re.fullmatch(r"\d+\.\d+\.\d+", catalog["node_version"])
                or not isinstance(catalog["models"], list) or not catalog["models"]):
            raise ValueError()
        for entry in catalog["models"]:
            if not re.fullmatch(r"[A-Za-z0-9_./:-]{1,128}", entry["name"]):
                raise ValueError()
            for key in ("download_bytes", "min_memory_gib", "min_threads", "context", "quality_rank"):
                if not isinstance(entry[key], (int, float)) or not 0 < entry[key] < 10 ** 15:
                    raise ValueError()
    except (ValueError, KeyError, TypeError):
        raise BootstrapError("Catálogo de modelos inválido ou incompatível; restaure config/model-catalog.json") from None
    return catalog


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.chmod(0o600)
    temp.replace(path)


def command(args: list[str], cwd: Path = ROOT, **kwargs) -> None:
    try:
        result = subprocess.run(args, cwd=cwd, **kwargs)
    except OSError as exc:
        raise BootstrapError(f"Não foi possível executar {Path(args[0]).name}. Verifique permissões e execute novamente.") from exc
    if result.returncode:
        raise BootstrapError(f"Etapa {Path(args[0]).name} falhou ({result.returncode}). Corrija o erro acima e execute o inicializador novamente.")


def executable(name: str) -> str | None:
    found = shutil.which(name)
    if found:
        return found
    if os.name == "nt" and name == "ollama":
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Ollama/ollama.exe"
        if candidate.is_file():
            return str(candidate)
    return None


def approve_system(message: str) -> None:
    print(message, flush=True)
    if not sys.stdin.isatty() or input("Autoriza a alteração no sistema (pode pedir administrador/sudo)? [s/N] ").strip().lower() not in ("s", "sim"):
        raise BootstrapError("Instalação do sistema não autorizada. Instale o componente indicado e execute novamente.")


def linux_packages(packages: list[str]) -> None:
    approve_system("Instalar pacotes necessários: " + ", ".join(packages))
    prefix = [] if hasattr(os, "geteuid") and os.geteuid() == 0 else ["sudo"]
    if executable("apt-get"):
        command(prefix + ["apt-get", "update"])
        command(prefix + ["apt-get", "install", "-y", *packages])
    elif executable("dnf"):
        command(prefix + ["dnf", "install", "-y", *packages])
    else:
        raise BootstrapError("Instale " + ", ".join(packages) + " pelo gerenciador da distribuição e execute novamente.")


def require_online(offline: bool, component: str) -> None:
    if offline:
        raise BootstrapError(f"{component} ausente ou incompatível. Execute uma vez sem --offline com acesso à internet.")


def prepare_venv(root: Path, offline: bool) -> Path:
    python = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    if python.is_file():
        if not (root / ".venv/pyvenv.cfg").is_file():
            raise BootstrapError("Ambiente .venv incompleto: renomeie essa pasta e execute novamente. Nenhum ambiente existente será apagado.")
        return python
    require_online(offline, "Ambiente Python isolado")
    log.info("Criando ambiente Python isolado .venv")
    try:
        command([sys.executable, "-m", "venv", str(root / ".venv")])
    except BootstrapError:
        if os.name == "nt":
            raise BootstrapError("Python não forneceu venv/pip. Repare a instalação oficial do Python 3.12 e execute novamente.")
        linux_packages([f"python{sys.version_info.major}.{sys.version_info.minor}-venv"])
        command([sys.executable, "-m", "venv", str(root / ".venv")])
    return python


def dependency_report(python: Path, root: Path) -> dict:
    # pip vendors the requirement parser; bootstrap does not need an extra global package.
    script = '''import importlib.metadata as m, json, sys
from pip._vendor.packaging.requirements import Requirement
from pip._vendor.packaging.markers import default_environment
bad=[]
for line in open(sys.argv[1],encoding="utf-8"):
 line=line.split("#",1)[0].strip()
 if not line: continue
 r=Requirement(line)
 if r.marker and not r.marker.evaluate(default_environment()): continue
 try:
  if m.version(r.name) not in r.specifier: bad.append(r.name)
  for item in m.requires(r.name) or []:
   dependency=Requirement(item)
   if dependency.marker and not any(dependency.marker.evaluate(dict(default_environment(),extra=extra)) for extra in (r.extras or {""})): continue
   try:
    if m.version(dependency.name) not in dependency.specifier: bad.append(dependency.name)
   except m.PackageNotFoundError: bad.append(dependency.name)
 except m.PackageNotFoundError: bad.append(r.name)
print(json.dumps({"missing":sorted(set(bad)),"versions":{d.metadata["Name"]:d.version for d in m.distributions() if d.metadata["Name"]}}))
'''
    result = subprocess.run([str(python), "-c", script, str(root / "requirements.txt")], capture_output=True, text=True)
    if result.returncode:
        raise BootstrapError("Ambiente .venv sem pip funcional. Repare o venv com python -m ensurepip e execute novamente.")
    return json.loads(result.stdout)


def ensure_dependencies(python: Path, root: Path, previous: dict, offline: bool) -> dict:
    report = dependency_report(python, root)
    if report["missing"]:
        require_online(offline, "Dependências Python: " + ", ".join(report["missing"]))
        log.info("Instalando somente dependências Python ausentes/incompatíveis: %s", ", ".join(report["missing"]))
        command([str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(root / "requirements.txt")])
        report = dependency_report(python, root)
        if report["missing"]:
            raise BootstrapError("Dependências Python ainda incompatíveis após instalação")
    if previous.get("python_versions") != report["versions"]:
        command([str(python), "-m", "pip", "check"])
        command([str(python), "-c", "import fastapi, uvicorn, numpy, openwakeword, pywhispercpp, kokoro_onnx, cryptography"])
    return report["versions"]


def safe_unpack(archive: Path, destination: Path) -> None:
    """Reject escaping paths/links and special files before extracting trusted vendor archives."""
    destination.mkdir(parents=True, exist_ok=True)
    base = destination.resolve()
    def check(name: str) -> Path:
        path = (base / name).resolve()
        if not path.is_relative_to(base) or "\\" in name or ":" in name:
            raise BootstrapError("Arquivo de instalação contém caminho inseguro")
        return path
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                check(member.filename)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise BootstrapError("Link simbólico inesperado no ZIP")
            bundle.extractall(base)
    else:
        with tarfile.open(archive) as bundle:
            for member in bundle.getmembers():
                path = check(member.name)
                if member.issym():
                    check(str(path.relative_to(base).parent / member.linkname))
                elif member.islnk():
                    check(member.linkname)
                elif not (member.isfile() or member.isdir()):
                    raise BootstrapError("Arquivo especial inesperado no pacote")
            # The explicit preflight also supports Python 3.11 releases without filter=.
            bundle.extractall(base)


def machine_arch() -> str:
    arch = platform.machine().lower()
    if arch in ("amd64", "x86_64"):
        return "x64"
    if arch in ("aarch64", "arm64"):
        return "arm64"
    raise BootstrapError("Arquitetura não suportada pelo bootstrap: " + arch)


def ensure_node(root: Path, catalog: dict, offline: bool) -> tuple[str, str]:
    version = catalog["node_version"]
    system = "win" if os.name == "nt" else "linux"
    directory = root / ".tools" / f"node-v{version}-{system}-{machine_arch()}"
    local = directory / ("node.exe" if os.name == "nt" else "bin/node")
    node = str(local) if local.is_file() else executable("node")
    if node:
        installed = subprocess.check_output([node, "--version"], text=True).strip()
        if int(installed.lstrip("v").split(".")[0]) >= 20:
            os.environ["PATH"] = str(Path(node).parent) + os.pathsep + os.environ.get("PATH", "")
            npm = str(Path(node).parent / ("node_modules/npm/bin/npm-cli.js" if os.name == "nt" else "npm"))
            if Path(npm).is_file():
                return node, npm
    require_online(offline, "Node.js >=20 com npm")
    suffix = "zip" if os.name == "nt" else "tar.xz"
    filename = directory.name + "." + suffix
    base = f"https://nodejs.org/dist/v{version}/"
    with urllib.request.urlopen(base + "SHASUMS256.txt", timeout=30) as response:
        entries = response.read(100_000).decode().splitlines()
    checksums = {line.split()[1].lstrip("*"): line.split()[0] for line in entries if len(line.split()) == 2}
    if filename not in checksums:
        raise BootstrapError("Pacote Node sem checksum no manifesto oficial")
    archive = download(base + filename, root / ".tools" / filename, checksums[filename])
    safe_unpack(archive, root / ".tools")
    if not local.is_file():
        raise BootstrapError("Pacote Node não contém o executável esperado")
    os.environ["PATH"] = str(local.parent) + os.pathsep + os.environ.get("PATH", "")
    return str(local), str(local.parent / ("node_modules/npm/bin/npm-cli.js" if os.name == "nt" else "npm"))


def fingerprint(paths: list[Path], root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(str(path.relative_to(root)).replace("\\", "/").encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def ensure_client(root: Path, catalog: dict, state: dict, offline: bool) -> dict:
    client = root / "client"
    sources = list((client / "src").rglob("*.ts")) + [client / "tsconfig.json", client / "package.json", client / "package-lock.json"]
    stamp = fingerprint(sources, root)
    outputs_ok = all((client / "public/js" / p.relative_to(client / "src").with_suffix(".js")).is_file()
                     for p in (client / "src").rglob("*.ts"))
    if state.get("client", {}).get("fingerprint") == stamp and outputs_ok:
        return state["client"]
    node, npm = ensure_node(root, catalog, offline)
    lock = read_json(client / "package-lock.json")
    expected = lock.get("packages", {}).get("node_modules/typescript", {}).get("version")
    actual = read_json(client / "node_modules/typescript/package.json").get("version")
    compiler_ok = (client / "node_modules/typescript/bin/tsc").is_file() and (client / "node_modules/typescript/lib/tsc.js").is_file()
    if not expected or actual != expected or not compiler_ok or state.get("client", {}).get("lock") not in (None, sha256(client / "package-lock.json")):
        require_online(offline, "Dependências npm")
        command([node, npm, "ci", "--no-audit", "--no-fund"], cwd=client)
    command([node, npm, "run", "build"], cwd=client)
    return {"fingerprint": stamp, "lock": sha256(client / "package-lock.json"), "node": subprocess.check_output(["node", "--version"], text=True).strip(), "typescript": expected}


def api(url: str, path: str, body: dict | None = None, timeout: float = 5) -> dict:
    from server.services.llm import local_url_problem
    if local_url_problem(url):
        raise BootstrapError("Ollama deve estar em localhost ou na rede local")
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = cls(parts.hostname, parts.port or (443 if parts.scheme == "https" else 11434), timeout=timeout)
    try:
        connection.request("POST" if body is not None else "GET", path,
                           body=json.dumps(body).encode() if body is not None else None,
                           headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise BootstrapError(f"Ollama respondeu {response.status} em {path}")
        raw = response.read(2_000_000)
        result = json.loads(raw)
        if not isinstance(result, dict) or result.get("error"):
            raise BootstrapError("Ollama devolveu erro ou resposta inválida")
        return result
    finally:
        connection.close()


def installed_models(url: str) -> list[dict]:
    return [m for m in api(url, "/api/tags").get("models", []) if isinstance(m, dict)]


def ensure_ollama(root: Path, url: str, offline: bool, owned: list[subprocess.Popen]) -> list[dict]:
    from server.services.llm import local_url_problem
    try:
        if local_url_problem(url) or urlsplit(url).port == 0:
            raise ValueError()
    except ValueError:
        raise BootstrapError("Corrija llm.url: use host e porta válidos da LAN, sem credenciais na URL") from None
    try:
        return installed_models(url)
    except (OSError, ValueError, BootstrapError, http.client.HTTPException):
        if not is_local_inference(url):
            raise BootstrapError("Ollama remoto indisponível. Inicie o serviço na máquina de inferência e execute novamente; nenhuma instalação será feita nessa máquina pela rede.")
    binary = executable("ollama")
    portable = root / ".tools/ollama/bin/ollama"
    if not binary and portable.is_file():
        binary = str(portable)
    if not binary:
        require_online(offline, "Ollama")
        if os.name == "nt":
            if not executable("winget"):
                raise BootstrapError("Instale Ollama de https://ollama.com/download/windows e execute novamente (Winget ausente).")
            approve_system("Instalar Ollama pelo Winget, usando o pacote oficial Ollama.Ollama.")
            command(["winget", "install", "--id", "Ollama.Ollama", "--exact", "--source", "winget"])
            binary = executable("ollama")
        else:
            with urllib.request.urlopen(urllib.request.Request("https://api.github.com/repos/ollama/ollama/releases/latest", headers={"User-Agent": "Luna-bootstrap"}), timeout=30) as response:
                release = json.load(response)
            arch = "amd64" if machine_arch() == "x64" else "arm64"
            asset = next((a for a in release.get("assets", []) if a.get("name") in (f"ollama-linux-{arch}.tar.zst", f"ollama-linux-{arch}.tgz")), None)
            if not asset or not str(asset.get("digest", "")).startswith("sha256:"):
                raise BootstrapError("Release Ollama sem SHA256 verificável. Instale o pacote oficial manualmente: https://ollama.com/download/linux; execute novamente.")
            if shutil.disk_usage(root).free < asset["size"] * 3 + GIB:
                raise BootstrapError("Espaço insuficiente para instalar o Ollama local")
            archive = download(asset["browser_download_url"], root / ".tools" / asset["name"], asset["digest"].split(":", 1)[1])
            if archive.name.endswith(".zst"):
                if not executable("zstd"):
                    linux_packages(["zstd"])
                unpacked = root / ".tools/ollama-linux.tar"
                with unpacked.open("wb") as output:
                    command(["zstd", "-d", "-c", str(archive)], stdout=output)
                archive = unpacked
            safe_unpack(archive, root / ".tools/ollama")
            binary = str(portable) if portable.is_file() else None
    if not binary:
        raise BootstrapError("Ollama instalado fora do caminho conhecido. Reabra o terminal e execute novamente.")
    log.info("Iniciando Ollama local; o serviço será encerrado junto com a Luna somente se iniciado aqui")
    # Respect a custom loopback port, and never bind the bootstrap-owned process to all interfaces.
    env = dict(os.environ, OLLAMA_HOST=urlsplit(url).netloc, OLLAMA_NUM_PARALLEL="1", OLLAMA_MAX_LOADED_MODELS="1")
    service_log = (root / "logs/ollama.log").open("ab")
    try:
        process = subprocess.Popen([binary, "serve"], cwd=root, env=env, stdout=service_log, stderr=service_log,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    finally:
        service_log.close()
    owned.append(process)
    for _ in range(40):
        try:
            return installed_models(url)
        except (OSError, ValueError, BootstrapError, http.client.HTTPException):
            if process.poll() is not None:
                break
            time.sleep(0.25)
    raise BootstrapError("Ollama não iniciou. Consulte logs/ollama.log e execute novamente.")


def update_acceleration(hardware: Hardware, url: str, model: str | None = None) -> None:
    if not is_local_inference(url):
        return
    try:
        running = api(url, "/api/ps").get("models", [])
        hardware.ollama_vram = sum(m.get("size_vram", 0) for m in running)
        hardware.inference_gpu_used = any(m.get("size_vram", 0) > 0 and (model is None or m.get("name") in (model, model + ":latest")) for m in running)
    except (OSError, ValueError, BootstrapError, http.client.HTTPException):
        hardware.inference_gpu_used = False


def suitable(entry: dict, h: Hardware, provisional: bool = False) -> bool:
    ram_ok = (h.ram_available or 0) >= entry["min_memory_gib"] * GIB
    gpu_memory = min(h.vram or 0, (h.vram_available if h.vram_available is not None else h.vram or 0) + h.ollama_vram)
    gpu_ok = (h.inference_gpu_used or provisional) and gpu_memory >= entry["download_bytes"] + 1.5 * GIB
    return h.available_threads >= entry["min_threads"] and (ram_ok or gpu_ok)


def performance_profile(settings, hardware: Hardware) -> str:
    preferences = read_json(settings.performance_path).get("preferences", {})
    requested = preferences.get("profile", settings.performance_profile) if isinstance(preferences, dict) else settings.performance_profile
    return requested if requested in ("economic", "balanced", "performance") else initial_profile(hardware)


def select_recommendation(catalog: dict, models: list[dict], h: Hardware) -> tuple[dict, bool]:
    options = [m for m in catalog["models"] if suitable(m, h, provisional=h.acceleration_verified)]
    known = {e["name"] for e in catalog["models"]}
    for installed in models:
        name, size = installed.get("name", ""), installed.get("size", 0)
        quant = str(installed.get("details", {}).get("quantization_level", ""))
        if (name not in known and isinstance(size, (int, float)) and 0 < size <= catalog.get("max_download_bytes", 10 * GIB)
                and any(f in name.lower() for f in ("qwen", "llama3", "gemma"))
                and not any(f in name.lower() for f in ("embed", "vision", "coder"))
                and quant.startswith(("Q4", "Q5", "Q6", "Q8"))):
            entry = {"name": name, "download_bytes": size, "min_memory_gib": 2 + size / GIB * 1.35,
                     "min_threads": 4 if size < 3 * GIB else 6, "context": 4096 if size < 3 * GIB else 8192,
                     "quality_rank": min(4, max(1, size / GIB))}
            if suitable(entry, h, provisional=h.acceleration_verified):
                options.append(entry)
    if not options:
        raise BootstrapError("Memória/CPU abaixo da estimativa mínima dos modelos do catálogo. Libere recursos ou configure um servidor Ollama na LAN; nenhum modelo será baixado às cegas.")
    # Reuse a compatible installed model before downloading any alternative.
    installed = [m for m in options if any(i.get("name") in (m["name"], m["name"] + ":latest") for i in models)]
    return max(installed or options, key=lambda m: m["quality_rank"]), bool(installed)


def pull_model(url: str, model: str, required_bytes: int, model_dir: Path, offline: bool) -> None:
    require_online(offline, "Modelo " + model)
    if not is_local_inference(url):
        raise BootstrapError(f"Instale o modelo escolhido no servidor remoto: ollama pull {model}")
    directory = model_dir
    while not directory.exists():
        directory = directory.parent
    if shutil.disk_usage(directory).free < required_bytes * 2 + GIB:
        raise BootstrapError("Espaço insuficiente no volume de modelos do Ollama")
    log.info("Baixando um modelo recomendado: %s; interrupções podem ser retomadas pelo Ollama", model)
    parts = urlsplit(url)
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    connection = cls(parts.hostname, parts.port or (443 if parts.scheme == "https" else 11434), timeout=120)
    try:
        connection.request("POST", "/api/pull", json.dumps({"model": model, "stream": True}), {"Content-Type": "application/json"})
        response = connection.getresponse()
        if response.status != 200:
            raise BootstrapError(f"Download Ollama recusado ({response.status})")
        deadline = time.monotonic() + 7200
        success = False
        while time.monotonic() < deadline:
            line = response.readline(65536)
            if not line:
                break
            event = json.loads(line)
            if event.get("error"):
                raise BootstrapError("Ollama falhou ao baixar o modelo; verifique conectividade/espaço e execute novamente")
            success = event.get("status") == "success"
            if success:
                break
        if not success:
            raise BootstrapError("Download interrompido ou excedeu duas horas. Execute novamente para retomar.")
    finally:
        connection.close()
    if not any(m.get("name") in (model, model + ":latest") for m in installed_models(url)):
        raise BootstrapError("Ollama não confirmou o modelo baixado")
    api(url, "/api/show", {"model": model})


def local_ip() -> str:
    try:
        addresses = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
        for info in addresses:
            value = info[4][0]
            if not value.startswith(("127.", "169.254.")):
                return value
    except OSError:
        pass
    return "127.0.0.1"


def ensure_config(root: Path) -> tuple[Path, bool]:
    path = Path(os.environ.get("JARVIS_CONFIG", root / "config/settings.toml"))
    if path.is_file():
        return path, False
    text = (root / "config/settings.example.toml").read_text(encoding="utf-8")
    text = text.replace("TROQUE-ESTE-TOKEN", secrets.token_hex(24)).replace('bind_host = "192.168.1.6"', f'bind_host = "{local_ip()}"')
    text = text.replace("\n[llm]\n", "\n[llm]\nautomatic_model = true\n", 1)
    text += '\n[bootstrap]\nmanaged = true\n'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    path.chmod(0o600)
    return path, True


def ensure_tls(settings) -> None:
    if not settings.tls_enabled:
        return
    cert, key = settings.tls_certfile, settings.tls_keyfile
    if cert.is_file() and key.is_file():
        return
    if cert.exists() != key.exists():
        raise BootstrapError("Par TLS incompleto. Preserve seu certificado e corrija os caminhos; nenhum arquivo existente será sobrescrito.")
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    import datetime
    import ipaddress
    private = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Luna local")])
    names = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]
    try:
        names.append(x509.IPAddress(ipaddress.ip_address(settings.bind_host)))
    except ValueError:
        names.append(x509.DNSName(settings.bind_host))
    now = datetime.datetime.now(datetime.timezone.utc)
    certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(private.public_key())
        .serial_number(x509.random_serial_number()).not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=3650)).add_extension(x509.SubjectAlternativeName(names), critical=False)
        .sign(private, hashes.SHA256()))
    cert.parent.mkdir(parents=True, exist_ok=True)
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    key.chmod(0o600)
    cert.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    log.info("Certificado local criado. Aceite o aviso do navegador ou importe o certificado no cliente; confiança do sistema não foi alterada.")


def model_assets(settings, offline: bool, previous: dict | None = None) -> dict:
    from scripts import prepare_models as models
    def fetch(url, path):
        cached = (previous or {}).get(str(path), {})
        if path.is_file() and cached == {"bytes": path.stat().st_size, "mtime_ns": path.stat().st_mtime_ns}:
            return path
        if not path.is_file() or not path.stat().st_size:
            require_online(offline, "Arquivo " + path.name)
        try:
            return download(url, path)
        except ValueError as exc:
            raise BootstrapError(f"Integridade inválida: {path.name}. Mova o arquivo danificado e execute novamente; arquivos parciais serão refeitos.") from exc
        except OSError as exc:
            raise BootstrapError(f"Não foi possível preparar {path.name}. Confira internet, espaço e permissões; execute novamente para retomar o arquivo parcial.") from exc
    features = settings.wake_features_dir
    for name in models.FEATURES:
        fetch(models.OWW_BASE + name, features / name)
    wake = settings.wake_model
    if not wake.is_file():
        name = wake.stem
        if name in models.COMMUNITY:
            fetch(models.COMMUNITY_BASE + models.COMMUNITY[name], wake)
        elif name in models.PRETRAINED.values():
            fetch(models.OWW_BASE + wake.name, wake)
        elif name + ".onnx" in models.PRETRAINED.values():
            fetch(models.OWW_BASE + wake.name, wake)
        else:
            raise BootstrapError("Modelo de ativação personalizado ausente. Restaure o arquivo configurado; não será substituído por outra palavra.")
    # Separate configured VAD paths are honored as well.
    fetch(models.OWW_BASE + "silero_vad.onnx", settings.wake_vad_model)
    if not settings.stt_model.is_file():
        if not settings.stt_model.name.startswith("ggml-") or settings.stt_model.name.endswith(".en.bin"):
            raise BootstrapError("Modelo ASR personalizado ausente; restaure seu arquivo ou escolha um Whisper multilíngue suportado")
        fetch(models.WHISPER_BASE + settings.stt_model.name, settings.stt_model)
    fetch(models.VAD_URL, settings.stt_vad_model)
    paths = [wake, settings.stt_model, settings.stt_vad_model, settings.wake_vad_model, *(features / f for f in models.FEATURES)]
    if settings.tts_enabled:
        if settings.tts_engine == "kokoro":
            if settings.tts_model.name not in {f for files in models.KOKORO.values() for f in files}:
                if not settings.tts_model.is_file():
                    raise BootstrapError("Modelo Kokoro personalizado ausente; restaure o arquivo configurado")
            else:
                fetch(models.KOKORO_BASE + settings.tts_model.name, settings.tts_model)
            fetch(models.KOKORO_BASE + settings.tts_voices.name, settings.tts_voices)
            paths += [settings.tts_model, settings.tts_voices]
        else:
            name = settings.tts_model.stem
            if name not in models.PIPER_VOICES and not settings.tts_model.is_file():
                raise BootstrapError("Voz Piper personalizada ausente")
            for extension in (".onnx", ".onnx.json"):
                path = settings.tts_model.with_name(name + extension)
                if name in models.PIPER_VOICES:
                    fetch(models.PIPER_BASE + models.PIPER_VOICES[name] + path.name, path)
                elif not path.is_file():
                    raise BootstrapError("Configuração da voz Piper personalizada ausente")
                paths.append(path)
    return {str(p): {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in paths}


def probe_llm(url: str, model: str, context: int) -> dict:
    started = time.perf_counter()
    response = api(url, "/api/chat", {"model": model, "stream": False, "keep_alive": "2m",
        "messages": [{"role": "user", "content": "Responda em português somente: pronto"}],
        "options": {"temperature": 0, "num_predict": 8, "num_ctx": context}}, timeout=60)
    if not response.get("done") or not (response.get("message") or {}).get("content", "").strip():
        raise BootstrapError("Modelo instalado não passou na geração mínima; verifique o Ollama ou restaure o modelo")
    duration = response.get("eval_duration", 0) / 1e9
    return {"total_s": time.perf_counter() - started, "load_s": response.get("load_duration", 0) / 1e9,
            "tokens_per_s": response.get("eval_count", 0) / duration if duration else None, "model": model}


def configure_models(settings, catalog: dict, hardware: Hardware, previous: dict, offline: bool) -> tuple[str, dict]:
    evaluated = {}
    profile = performance_profile(settings, hardware)
    models = installed_models(settings.llm_url)
    prefs = read_json(settings.performance_path).get("preferences", {})
    manual = prefs.get("model") or (settings.llm_model if not settings.llm_auto_model else "")
    update_acceleration(hardware, settings.llm_url)
    if not is_local_inference(settings.llm_url):
        name = manual or settings.llm_model
        if not any(m.get("name") in (name, name + ":latest") for m in models):
            raise BootstrapError(f"Modelo ausente no servidor remoto. Execute nele: ollama pull {name}")
        return name, {"llm_probe": previous.get("llm_probe", {})}
    if manual:
        name = manual
        if not any(m.get("name") in (name, name + ":latest") for m in models):
            entry = next((e for e in catalog["models"] if e["name"] == name), None)
            if entry is None:
                raise BootstrapError(f"Modelo manual ausente e tamanho desconhecido. Instale conscientemente com ollama pull {name}; sua escolha foi preservada.")
            pull_model(settings.llm_url, name, entry["download_bytes"], Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models"))), offline)
        context = settings.llm_num_ctx or 8192
    else:
        # A single lightweight probe can confirm an installed GPU candidate before deciding to pull.
        profile = performance_profile(settings, hardware)
        maximum = {"economic": 2.5, "balanced": 5, "performance": 10}[profile] * GIB
        selection = {**catalog, "models": [e for e in catalog["models"] if e["download_bytes"] <= maximum],
                     "max_download_bytes": maximum}
        candidates = [e for e in selection["models"] if suitable(e, hardware, provisional=bool(hardware.gpu))
                      and any(m.get("name") in (e["name"], e["name"] + ":latest") for m in models)]
        if hardware.gpu and hardware.acceleration_verified and not hardware.inference_gpu_used and candidates and previous.get("hardware") != hardware_key(hardware):
            candidate = max(candidates, key=lambda e: e["quality_rank"])
            try:
                evaluated[(candidate["name"], candidate["context"])] = probe_llm(settings.llm_url, candidate["name"], candidate["context"])
                update_acceleration(hardware, settings.llm_url, candidate["name"])
            except (OSError, ValueError, BootstrapError, http.client.HTTPException):
                log.warning("Candidato com GPU não passou na avaliação; seleção seguirá os recursos confirmados")
        entry, installed = select_recommendation(selection, models, hardware)
        cached = previous.get("probe_identity", {})
        cached_models = [m for m in models if m.get("name") in (cached.get("model"), str(cached.get("model")) + ":latest")]
        if (cached.get("hardware") == hardware_key(hardware) and cached.get("catalog") == catalog["revision"]
                and cached.get("profile") == profile and cached.get("ollama_version") == previous.get("ollama_version")
                and cached_models and cached_models[0].get("digest") == cached.get("digest")):
            cached_entry, cached_ready = select_recommendation(selection, cached_models, hardware)
            if cached_ready:
                entry, installed = cached_entry, True  # retain a measured lighter choice across restarts
        name, context = entry["name"], entry["context"]
        if not installed:
            pull_model(settings.llm_url, name, entry["download_bytes"], Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models"))), offline)
    metadata = next((m for m in installed_models(settings.llm_url) if m.get("name") in (name, name + ":latest")), {})
    identity = {"hardware": hardware_key(hardware), "model": name, "url": settings.llm_url, "context": context,
                "catalog": catalog["revision"], "digest": metadata.get("digest"), "modified_at": metadata.get("modified_at"),
                "ollama_version": previous.get("ollama_version"), "profile": profile}
    if previous.get("probe_identity") == identity and previous.get("llm_probe"):
        probe = previous["llm_probe"]
    else:
        probe = evaluated.get((name, context)) or probe_llm(settings.llm_url, name, context)
        update_acceleration(hardware, settings.llm_url, name)
        identity["hardware"] = hardware_key(hardware)
        # Evaluate warm generation speed separately from cold loading.
        if not manual and probe.get("tokens_per_s") and probe["tokens_per_s"] < 5:
            confirmation = probe_llm(settings.llm_url, name, context)
            probe["confirmed_tokens_per_s"] = confirmation.get("tokens_per_s")
            selected_size = next((e["download_bytes"] for e in catalog["models"] if e["name"] == name), metadata.get("size", 0))
            lighter = [e for e in catalog["models"] if suitable(e, hardware) and e["download_bytes"] < selected_size]
            if not confirmation.get("tokens_per_s") or confirmation["tokens_per_s"] >= 5:
                lighter = []
            if lighter:
                lighter_models = [m for m in models if 0 < m.get("size", 0) < selected_size]
                smaller, ready = select_recommendation({"models": lighter}, lighter_models, hardware)
                if not ready:
                    pull_model(settings.llm_url, smaller["name"], smaller["download_bytes"], Path(os.environ.get("OLLAMA_MODELS", str(Path.home() / ".ollama/models"))), offline)
                # Unload only the candidate loaded for this assessment before loading the alternative.
                api(settings.llm_url, "/api/generate", {"model": name, "keep_alive": 0})
                name, context = smaller["name"], smaller["context"]
                probe = probe_llm(settings.llm_url, name, context)
                identity.update(model=name, context=context)
                metadata = next((m for m in installed_models(settings.llm_url) if m.get("name") in (name, name + ":latest")), {})
                identity.update(digest=metadata.get("digest"), modified_at=metadata.get("modified_at"))
    settings.llm_model = name
    if not manual:
        settings.llm_num_ctx = context
    return name, {"llm_probe": probe, "probe_identity": identity}


def check_only(root: Path, hardware: Hardware) -> int:
    python = root / (".venv/Scripts/python.exe" if os.name == "nt" else ".venv/bin/python")
    print(json.dumps({"hardware": asdict(hardware), "python": platform.python_version(), "venv": python.is_file(),
                      "disk_free_bytes": shutil.disk_usage(root).free, "node": bool(executable("node")),
                      "ffmpeg_required": False}, ensure_ascii=True, indent=2))
    if python.is_file():
        report = dependency_report(python, root)
        print("Dependências ausentes/incompatíveis:", ", ".join(report["missing"]) or "nenhuma")
        return int(bool(report["missing"]))
    return 1


def prepare(root: Path, options, owned: list[subprocess.Popen]) -> dict:
    from server.storage.config import Settings, apply_automatic_settings
    state_path = root / "data/bootstrap-state.json"
    state = read_json(state_path)
    if state.get("schema_version") != SCHEMA:
        state = {}
    catalog = load_catalog(root)
    python = Path(sys.executable)
    state["python_versions"] = ensure_dependencies(python, root, state, options.offline)
    state["client"] = ensure_client(root, catalog, state, options.offline)
    config, created = ensure_config(root)
    base = Settings.from_toml(config)
    settings = apply_automatic_settings(Settings.from_toml(config))
    if settings.tts_enabled and settings.tts_engine == "piper":
        try:
            from pip._vendor.packaging.version import Version
            compatible = Version("1.8") <= Version(importlib.metadata.version("piper-tts")) < Version("2")
        except importlib.metadata.PackageNotFoundError:
            compatible = False
        if not compatible:
            require_online(options.offline, "Motor Piper configurado")
            command([str(python), "-m", "pip", "install", "--disable-pip-version-check", "piper-tts>=1.8,<2"])
            state["python_versions"] = dependency_report(python, root)["versions"]
    hardware = detect_server()
    profile = performance_profile(settings, hardware)
    log.info("Hardware: %s; %s threads disponíveis; perfil inicial %s", hardware.cpu or "CPU desconhecida", hardware.available_threads, profile)
    if settings.llm_enabled:
        ensure_ollama(root, settings.llm_url, options.offline, owned)
        try:
            state["ollama_version"] = api(settings.llm_url, "/api/version").get("version", "unknown")
        except (OSError, ValueError, BootstrapError, http.client.HTTPException):
            state["ollama_version"] = "unknown"
        if options.reprobe:
            state.pop("probe_identity", None)
        model, evaluation = configure_models(settings, catalog, hardware, state, options.offline)
        state.update(evaluation)
    else:
        model = settings.llm_model
    # Only settings created/owned by bootstrap get automatic ASR/TTS paths. Individual TOML edits win.
    automatic_path = root / "data/bootstrap-auto.json"
    prior = read_json(automatic_path)
    baseline = prior.get("baseline", {}) or {key: str(getattr(base, key)) for key in
                ("llm_model", "llm_num_ctx", "stt_model", "tts_model")}
    if settings.bootstrap_managed:
        if str(base.stt_model) == baseline.get("stt_model"):
            settings.stt_model = root / ("models/whisper/ggml-base.bin" if profile == "economic" else "models/whisper/ggml-small-q8_0.bin")
        if settings.tts_engine == "kokoro" and str(base.tts_model) == baseline.get("tts_model"):
            settings.tts_model = root / ("models/tts/kokoro-v1.0.int8.onnx" if profile == "economic" else "models/tts/kokoro-v1.0.onnx")
    assets = model_assets(settings, options.offline, state.get("assets"))
    ensure_tls(settings)
    settings.validate()
    preferences = read_json(settings.performance_path).get("preferences", {})
    if not isinstance(preferences, dict):
        preferences = {}
    voice_identity = {"assets": assets, "versions": state["python_versions"], "profile": profile,
                      "voice": preferences.get("voice") or settings.tts_voice, "engine": settings.tts_engine, "hardware": hardware_key(hardware)}
    if state.get("voice_identity") != voice_identity or options.reprobe:
        from server.api.app import load_detector, load_vad, load_transcriber, load_synthesizer
        from server.services.performance import PerformancePolicy, process_ram
        policy = PerformancePolicy(settings, hardware)
        detector, reason = load_detector(settings)
        if detector is None or load_vad(settings) is None:
            raise BootstrapError("Ativação/VAD não carregaram. Consulte logs/bootstrap.log; restaure os arquivos dos modelos e execute novamente. " + reason)
        started = time.perf_counter()
        transcriber, reason = load_transcriber(settings)
        state["asr_probe"] = {"load_s": time.perf_counter() - started, "threads": settings.stt_threads,
                              "backend": "whisper.cpp CPU"}
        if transcriber is None:
            raise BootstrapError("ASR não carregou: " + reason)
        speech_probe = {}
        if settings.tts_enabled:
            engine, reason = load_synthesizer(settings)
            if engine is None:
                raise BootstrapError("TTS não carregou: " + reason)
            engine.warmup()
            started = time.perf_counter()
            for pcm in engine.synthesize("Olá, eu sou a Luna."):
                if pcm and not speech_probe:
                    speech_probe["first_audio_s"] = time.perf_counter() - started
            speech_probe["total_s"] = time.perf_counter() - started
            speech_probe["process_ram_bytes"] = process_ram()
            del engine
        del transcriber, detector, policy
        gc.collect()
        state["tts_probe"] = speech_probe
        state["voice_identity"] = voice_identity
    if settings.bootstrap_managed:
        selected = {key: str(getattr(settings, key)) for key in baseline}
        write_json(automatic_path, {"schema_version": SCHEMA, "baseline": baseline, "selected": selected})
    state.update(schema_version=SCHEMA, hardware=hardware_key(hardware), hardware_observed=asdict(hardware),
                 catalog_revision=catalog["revision"], model=model, asr=str(settings.stt_model), tts=str(settings.tts_model),
                 profile=profile, assets=assets, python=platform.python_version(),
                 requirements_sha256=sha256(root / "requirements.txt"), disk_free_bytes=shutil.disk_usage(root).free)
    write_json(state_path, state)
    log.info("Preparação concluída; IA=%s; voz=%s. Configuração e segredos existentes preservados.", model, settings.tts_engine)
    return {"settings": settings, "state": state}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Preparar e iniciar a Luna (Windows/Linux)")
    parser.add_argument("--check", action="store_true", help="somente diagnóstico; sem instalações ou gravações")
    parser.add_argument("--offline", action="store_true", help="proibir instalação/download; usar ambiente já preparado")
    parser.add_argument("--prepare-only", action="store_true", help="preparar sem iniciar servidor Luna")
    parser.add_argument("--reprobe", action="store_true", help="refazer avaliação leve, sem apagar preferências")
    options = parser.parse_args(argv)
    if not (3, 11) <= sys.version_info[:2] < (3, 13):
        print("Use Python 3.11 ou 3.12 para os motores ASR/TTS deste projeto.", file=sys.stderr)
        return 1
    if options.check:
        return check_only(ROOT, detect_server())
    try:
        python = prepare_venv(ROOT, options.offline)
    except (BootstrapError, OSError) as exc:
        print(str(exc) if isinstance(exc, BootstrapError) else "Não foi possível criar o ambiente isolado. Verifique permissões.", file=sys.stderr)
        return 1
    if Path(sys.prefix).resolve() != (ROOT / ".venv").resolve():
        target = str((ROOT / ".venv").resolve())
        if os.environ.get("LUNA_BOOTSTRAP_VENV") == target:
            print("O .venv não ativou o isolamento corretamente. Renomeie essa pasta e execute novamente.", file=sys.stderr)
            return 1
        return subprocess.call([str(python), str(Path(__file__).resolve()), *(argv if argv is not None else sys.argv[1:])],
                               cwd=ROOT, env=dict(os.environ, LUNA_BOOTSTRAP_VENV=target))
    (ROOT / "logs").mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(ROOT / "logs/bootstrap.log", encoding="utf-8")])
    owned: list[subprocess.Popen] = []
    lock = ROOT / "data/bootstrap.lock"
    lock.parent.mkdir(exist_ok=True)
    # OS-held advisory lock: released even after termination; no stale PID-file lockout.
    handle = lock.open("a+b")
    try:
        handle.seek(0)
        if os.name == "nt":
            import msvcrt
            handle.write(b"0")
            handle.flush()
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        handle.close()
        log.error("Outro inicializador Luna está em execução neste projeto")
        return 1
    try:
        prepare(ROOT, options, owned)
        if not options.prepare_only:
            command([str(python), "-m", "server.main"])
        return 0
    except KeyboardInterrupt:
        log.info("Interrompido. Execute novamente para retomar as etapas pendentes.")
        return 130
    except (RuntimeError, OSError, ValueError, http.client.HTTPException) as exc:
        # Never include URLs, credentials, tokens or arbitrary response bodies in bootstrap logs.
        message = str(exc) if isinstance(exc, BootstrapError) else f"Falha de {type(exc).__name__}. Verifique internet, permissões e arquivos; execute novamente para retomar."
        log.error("%s", message)
        return 1
    finally:
        for process in owned:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        handle.close()


if __name__ == "__main__":
    raise SystemExit(main())
