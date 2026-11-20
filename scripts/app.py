''' Graphical interface of the bibliometric pipeline, for users without Python or terminal experience.

It creates .venv and installs requirements.txt, saves the API keys in .env, and runs scripts/run_pipeline.py with
the Python of .venv, showing its output in the same window.

Runs with the system Python and only the standard library (tkinter), because it has to open before .venv exists.
It never imports bibliometrics: it builds the same command documented in the README.

Usage: double click on Iniciar.bat (Windows) or Iniciar.command (macOS), or python scripts/app.py
'''
import codecs
import hashlib
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

ROOT = Path(__file__).resolve().parents[1]
IS_WINDOWS = sys.platform == "win32"
VENV = ROOT / ".venv"
VENV_PYTHON = VENV / ("Scripts/python.exe" if IS_WINDOWS else "bin/python")
REQUIREMENTS = ROOT / "requirements.txt"
STAMP = VENV / "requirements.sha256" # Hash of the requirements.txt installed in .venv
ENV_FILE, ENV_EXAMPLE = ROOT / ".env", ROOT / ".env_example"
SETTINGS_FILE = ROOT / ".gui_settings.json"
DATA, OUTPUTS = ROOT / "data", ROOT / "outputs"
PIPELINE = ROOT / "scripts" / "run_pipeline.py"
MIN_PYTHON = (3, 12)
NO_WINDOW = subprocess.CREATE_NO_WINDOW if IS_WINDOWS else 0
CONSOLE_FONT = ("Consolas", 10) if IS_WINDOWS else ("Menlo", 11)

# (variable in .env, label, required, page to get it)
KEYS = [('OPENALEX_API_KEY', 'OpenAlex', True, 'https://openalex.org/settings/api-key'),
        ('ANTHROPIC_API_KEY', 'Anthropic', True, 'https://platform.claude.com/settings/keys'),
        ('S2_API_KEY', 'Semantic Scholar (opcional)', False, 'https://www.semanticscholar.org/product/api')]

# Same required fields as src/bibliometrics/queries.py (copied: the package needs pandas, which is in .venv only)
META_FIELDS = ['country', 'start_year', 'end_year', 'country_terms']
THEMATIC_FIELDS = ['thematic_id', 'pillar', 'strategic_objective', 'expected_result', 'thematic_axis', 'keywords']
NAME_PATTERN = re.compile(r'^[A-Za-z0-9_-]*$')

GREEN, RED, ORANGE, GREY = "#1a7f37", "#c62828", "#b26a00", "#666666"


# ---------------------------------------------------------------- Environment

def base_python():
    ''' Python of the system that runs the interface (console version, also when it runs inside a venv) '''
    exe = Path(getattr(sys, '_base_executable', None) or sys.executable)
    if exe.name.lower() == 'pythonw.exe' and exe.with_name('python.exe').exists():
        exe = exe.with_name('python.exe')
    return exe


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def venv_version(venv=VENV):
    ''' (major, minor) of the Python of the venv, from pyvenv.cfg (None if it cannot be read) '''
    try:
        text = (venv / "pyvenv.cfg").read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r'^version(?:_info)?\s*=\s*(\d+)\.(\d+)', text, re.MULTILINE)
    return (int(match[1]), int(match[2])) if match else None


def venv_state(venv=VENV, venv_python=VENV_PYTHON, requirements=REQUIREMENTS, stamp=STAMP):
    ''' missing | other_python | outdated | ready '''
    if not venv_python.exists():
        return 'missing'
    if venv_version(venv) != sys.version_info[:2]:
        return 'other_python'
    if not stamp.exists() or stamp.read_text(encoding="utf-8").strip() != file_hash(requirements):
        return 'outdated'
    return 'ready'


# ---------------------------------------------------------------- API keys

def read_env(path=ENV_FILE):
    ''' Variables of a .env file as {name: value} (empty dict if it does not exist) '''
    values = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                key, value = line.split('=', 1)
                values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def write_env(values, path=ENV_FILE, template=ENV_EXAMPLE):
    ''' Write .env with the lines and comments of .env_example, filled with values. Variables of the current .env
    that are not in the template are kept at the end '''
    current = read_env(path)
    lines, written = [], set()
    template_lines = template.read_text(encoding="utf-8").splitlines() if template.exists() else []
    for line in template_lines:
        key = line.split('=', 1)[0].strip()
        if '=' in line and not line.lstrip().startswith('#'):
            lines.append(f"{key}={values.get(key, current.get(key, ''))}")
            written.add(key)
        elif line.startswith('# Copiar como .env'):
            lines.append("# Claves de API (escritas por la interfaz, scripts/app.py)")
        else:
            lines.append(line)
    for key, value in {**current, **values}.items():
        if key not in written:
            lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def http_get(url, headers, timeout=20):
    ''' (status, body as dict or None); status None if there is no connection '''
    request = urllib.request.Request(url, headers={'User-Agent': 'bibliometrics-app', **headers})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status, raw = response.status, response.read()
    except urllib.error.HTTPError as e:
        status, raw = e.code, e.read()
    except (urllib.error.URLError, OSError):
        return None, None
    try:
        return status, json.loads(raw)
    except ValueError:
        return status, None


def check_openalex(key):
    ''' (state, message) with state ok | invalid | unknown. Uses the free /rate-limit endpoint and shows the balance '''
    status, body = http_get("https://api.openalex.org/rate-limit", {'Authorization': f"Bearer {key}"})
    if status is None:
        return 'unknown', "no se pudo verificar (sin conexión)"
    limits = (body or {}).get('rate_limit') if status == 200 else None
    if not limits:
        return 'invalid', f"clave no válida (código {status})"
    daily, prepaid = limits.get('daily_remaining_usd') or 0, limits.get('prepaid_remaining_usd') or 0
    return 'ok', f"válida · saldo: diario USD {daily:.2f} + prepagado USD {prepaid:.2f}"


def check_anthropic(key):
    ''' (state, message): lists the models (free). The API does not report the balance '''
    status, _ = http_get("https://api.anthropic.com/v1/models?limit=1",
                         {'x-api-key': key, 'anthropic-version': '2023-06-01'})
    if status is None:
        return 'unknown', "no se pudo verificar (sin conexión)"
    if status == 200:
        return 'ok', "válida · revise su saldo en Claude Platform"
    if status in (401, 403):
        return 'invalid', f"clave no válida (código {status})"
    return 'unknown', f"no se pudo verificar (código {status})"


def check_s2(key):
    ''' (state, message) of the optional Semantic Scholar key (one free paper lookup) '''
    if not key:
        return 'ok', "sin clave: se usa el límite compartido (puede quedar algún abstract sin recuperar)"
    status, _ = http_get("https://api.semanticscholar.org/graph/v1/paper/DOI:10.1038/nature12373?fields=title",
                         {'x-api-key': key})
    if status == 200:
        return 'ok', "válida"
    if status in (401, 403):
        return 'invalid', f"clave no válida (código {status})"
    return 'unknown', "no se pudo verificar" + (f" (código {status})" if status else " (sin conexión)")


CHECKS = {'OPENALEX_API_KEY': check_openalex, 'ANTHROPIC_API_KEY': check_anthropic, 'S2_API_KEY': check_s2}


# ---------------------------------------------------------------- Parameters

def resolve(path_text):
    ''' Path of a field: relative paths are relative to the project folder '''
    path = Path(path_text.strip())
    return path if path.is_absolute() else ROOT / path


def display_path(path):
    ''' Path relative to the project folder when it is inside it '''
    path = Path(path)
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def country_slug(country):
    return country.lower().replace(' ', '_')


def preview_queries(path):
    ''' (info, error) of a queries file: info has country, years and number of thematics; error explains in Spanish
    what is wrong (JSON syntax with line and column, or missing fields) '''
    try:
        with open(path, encoding="utf-8") as f:
            queries = json.load(f)
    except FileNotFoundError:
        return None, "No se encontró el archivo"
    except json.JSONDecodeError as e:
        return None, f"El JSON tiene un error de formato en la línea {e.lineno}, columna {e.colno}: {e.msg}"
    except (OSError, UnicodeDecodeError) as e:
        return None, f"No se pudo leer el archivo ({type(e).__name__})"
    if not isinstance(queries, dict):
        return None, "El JSON debe tener las partes 'meta' y 'thematics'"
    meta = queries.get('meta') or {}
    missing = [field for field in META_FIELDS if field not in meta]
    if missing:
        return None, f"Faltan campos en 'meta': {', '.join(missing)}"
    thematics = queries.get('thematics') or []
    if not thematics:
        return None, "No hay ejes temáticos en 'thematics'"
    for thematic in thematics:
        missing = [field for field in THEMATIC_FIELDS if field not in thematic]
        if missing:
            return None, f"Al eje {thematic.get('thematic_id', '?')} le faltan campos: {', '.join(missing)}"
        if not thematic['keywords']:
            return None, f"El eje {thematic['thematic_id']} no tiene palabras clave"
    ids = [str(thematic['thematic_id']) for thematic in thematics]
    duplicated = sorted({i for i in ids if ids.count(i) > 1})
    if duplicated:
        return None, f"Hay thematic_id repetidos: {', '.join(duplicated)}"
    return {'country': meta['country'], 'start_year': meta['start_year'], 'end_year': meta['end_year'],
            'thematics': len(thematics)}, None


def output_notices(name):
    ''' Notices [(color, text)] about the previous outputs of name (only reads files) '''
    notices = []
    if (OUTPUTS / f"{name}_db.xlsx").exists():
        notices.append((GREY, f"Ya existe {name}_db.xlsx: se actualizará y los estudios que ya estaban conservan su "
                              "fecha de entrada. No borre ese archivo."))
    backup = OUTPUTS / f"{name}_backup"
    thematic_files = list(backup.glob("thematic_*.json"))
    summary = backup / "run_summary.json"
    if thematic_files and (not summary.exists()
                           or max(p.stat().st_mtime for p in thematic_files) > summary.stat().st_mtime):
        notices.append((ORANGE, "La corrida anterior con este nombre no terminó. Para retomarla sin pagar de nuevo "
                                "los ejes ya consultados, desactive 'Actualizar'."))
    return notices


def pipeline_command(queries, subfields, name, stemming, update):
    command = [str(VENV_PYTHON), "-u", str(PIPELINE), queries, "--subfields", subfields]
    if name:
        command += ["--name", name]
    return command + ["--stemming" if stemming else "--no-stemming", "--update" if update else "--no-update"]


def child_env():
    ''' Environment of the subprocesses: UTF-8 and unbuffered output, and a fixed width for the tqdm bars '''
    return {**os.environ, 'PYTHONIOENCODING': 'utf-8', 'PYTHONUNBUFFERED': '1', 'COLUMNS': '110'}


def load_settings():
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def open_folder(path):
    if IS_WINDOWS:
        os.startfile(path)
    else:
        subprocess.run(["open", str(path)])


# ---------------------------------------------------------------- Interface

class App:
    def __init__(self, root):
        self.root = root
        self.events = queue.Queue() # (function, args) to run in the Tk thread, sent by the worker threads
        self.proc = None # Running subprocess
        self.busy = False
        self.stopped = False
        self.run_name = None
        self.run_start = None
        self.cr_pending = False # A \r arrived: the next text replaces the current console line
        self.queries_info = None
        self.settings = load_settings()

        root.title("Reporte bibliométrico")
        root.minsize(820, 600)
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        root.report_callback_exception = self.on_error
        root.columnconfigure(0, weight=1)
        root.rowconfigure(3, weight=1)

        self.build_environment()
        self.build_keys()
        self.build_parameters()
        self.build_console()
        if root.winfo_screenheight() < 1000: # Small screens (e.g. 1366x768): the window has to fit
            self.console.configure(height=4)
        self.status = tk.StringVar(value="Listo")
        self.status_label = tk.Label(root, textvariable=self.status, anchor="w", fg=GREY, padx=10)
        self.status_label.grid(row=4, column=0, sticky="ew", pady=(0, 6))

        self.refresh_environment()
        self.load_keys()
        self.load_parameters()
        self.root.after(100, self.poll_events)

    # ------------------------------------------------ Layout

    def section(self, row, title):
        frame = ttk.LabelFrame(self.root, text=title, padding=(10, 6))
        frame.grid(row=row, column=0, sticky="nsew", padx=10, pady=(8, 0))
        return frame

    def build_environment(self):
        frame = self.section(0, "1. Entorno")
        frame.columnconfigure(1, weight=1)
        self.python_label = tk.Label(frame, anchor="w")
        self.python_label.grid(row=0, column=0, sticky="w", padx=(0, 20))
        self.venv_label = tk.Label(frame, anchor="w")
        self.venv_label.grid(row=0, column=1, sticky="w")
        self.install_button = ttk.Button(frame, text="Instalar entorno", command=self.install)
        self.install_button.grid(row=0, column=2, sticky="e")

    def build_keys(self):
        frame = self.section(1, "2. Claves de API")
        frame.columnconfigure(1, weight=1)
        self.keys_intro = tk.Label(frame, anchor="w", fg=ORANGE)
        self.keys_intro.grid(row=0, column=0, columnspan=4, sticky="w")
        self.key_vars, self.key_entries, self.key_labels = {}, {}, {}
        for i, (key, label, required, url) in enumerate(KEYS):
            row = 2 * i + 1 # The row below holds the result of the verification
            ttk.Label(frame, text=label + ("" if not required else " *")).grid(row=row, column=0, sticky="w",
                                                                               padx=(0, 8), pady=2)
            var = tk.StringVar()
            entry = ttk.Entry(frame, textvariable=var, show="•", width=46)
            entry.grid(row=row, column=1, sticky="w", pady=2)
            show = ttk.Button(frame, text="Mostrar", width=8, command=lambda e=entry: self.toggle_show(e))
            show.grid(row=row, column=2, padx=4)
            link = tk.Label(frame, text="¿Cómo obtenerla?", fg="#1565c0", cursor="hand2")
            link.grid(row=row, column=3, sticky="w")
            link.bind("<Button-1>", lambda _, u=url: webbrowser.open(u))
            status = tk.Label(frame, anchor="w", fg=GREY)
            status.grid(row=row + 1, column=1, columnspan=3, sticky="w", pady=(0, 2))
            status.grid_remove()
            self.key_vars[key], self.key_entries[key], self.key_labels[key] = var, (entry, show), status
        self.save_keys_button = ttk.Button(frame, text="Verificar y guardar claves", command=self.save_keys)
        self.save_keys_button.grid(row=2 * len(KEYS) + 1, column=0, columnspan=4, sticky="e", pady=(4, 0))

    def key_status(self, key, text, color):
        ''' Result of the verification of a key, below its field (hidden when empty) '''
        label = self.key_labels[key]
        label.configure(text=text, fg=color)
        if text:
            label.grid()
        else:
            label.grid_remove()

    def build_parameters(self):
        frame = self.section(2, "3. Parámetros")
        frame.columnconfigure(1, weight=1)
        self.queries_var, self.subfields_var, self.name_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self.update_var, self.stemming_var = tk.BooleanVar(value=True), tk.BooleanVar(value=True)

        ttk.Label(frame, text="Queries (JSON) *").grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.queries_entry = ttk.Entry(frame, textvariable=self.queries_var)
        self.queries_entry.grid(row=0, column=1, sticky="ew", pady=2)
        self.queries_button = ttk.Button(frame, text="Buscar…", command=self.pick_queries)
        self.queries_button.grid(row=0, column=2, padx=(4, 0))
        self.queries_info_label = tk.Label(frame, anchor="w", fg=GREY, justify="left", wraplength=700)
        self.queries_info_label.grid(row=1, column=1, columnspan=2, sticky="w")

        ttk.Label(frame, text="Subcampos (Excel) *").grid(row=2, column=0, sticky="w", padx=(0, 8))
        self.subfields_entry = ttk.Entry(frame, textvariable=self.subfields_var)
        self.subfields_entry.grid(row=2, column=1, sticky="ew", pady=2)
        self.subfields_button = ttk.Button(frame, text="Buscar…", command=self.pick_subfields)
        self.subfields_button.grid(row=2, column=2, padx=(4, 0))

        ttk.Label(frame, text="Nombre (opcional)").grid(row=3, column=0, sticky="w", padx=(0, 8))
        name_row = ttk.Frame(frame)
        name_row.grid(row=3, column=1, columnspan=2, sticky="ew", pady=2)
        self.name_entry = ttk.Entry(name_row, textvariable=self.name_var, width=34)
        self.name_entry.pack(side="left")
        self.name_hint = tk.Label(name_row, anchor="w", fg=GREY)
        self.name_hint.pack(side="left", padx=8)

        options = ttk.Frame(frame)
        options.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 0))
        self.update_check = ttk.Checkbutton(options, text="Actualizar", variable=self.update_var,
                                            command=self.refresh_notices)
        self.update_check.grid(row=0, column=0, sticky="w")
        tk.Label(options, fg=GREY, anchor="w",
                 text="Vuelve a consultar todos los ejes en OpenAlex. Desactívelo solo para retomar una corrida "
                      "que no terminó.").grid(row=0, column=1, sticky="w", padx=6)
        self.stemming_check = ttk.Checkbutton(options, text="Stemming", variable=self.stemming_var)
        self.stemming_check.grid(row=1, column=0, sticky="w")
        tk.Label(options, fg=GREY, anchor="w",
                 text="Busca también variantes de las palabras clave (plurales, conjugaciones). Sin stemming se "
                      "buscan como frase exacta.").grid(row=1, column=1, sticky="w", padx=6)

        self.notices_frame = ttk.Frame(frame)
        self.notices_frame.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(4, 0))

        buttons = ttk.Frame(frame)
        buttons.grid(row=6, column=0, columnspan=3, sticky="e", pady=(6, 0))
        self.run_button = ttk.Button(buttons, text="Ejecutar", command=self.run)
        self.run_button.pack(side="left", padx=4)
        self.stop_button = ttk.Button(buttons, text="Detener", command=self.stop, state="disabled")
        self.stop_button.pack(side="left", padx=4)
        self.open_button = ttk.Button(buttons, text="Abrir resultados", command=lambda: open_folder(OUTPUTS))
        self.open_button.pack(side="left", padx=4)

        self.queries_var.trace_add("write", lambda *_: self.on_queries_change())
        self.name_var.trace_add("write", lambda *_: self.refresh_name())

    def build_console(self):
        frame = self.section(3, "4. Consola")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        self.console = tk.Text(frame, height=10, wrap="none", font=CONSOLE_FONT, state="disabled",
                               bg="#1e1e1e", fg="#e0e0e0", insertbackground="#e0e0e0")
        self.console.grid(row=0, column=0, sticky="nsew")
        yscroll = ttk.Scrollbar(frame, orient="vertical", command=self.console.yview)
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll = ttk.Scrollbar(frame, orient="horizontal", command=self.console.xview)
        xscroll.grid(row=1, column=0, sticky="ew")
        self.console.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.console.tag_configure("warning", foreground="#ffb74d")
        self.console.tag_configure("error", foreground="#ef5350")
        self.console.tag_configure("stage", foreground="#81c784")
        self.console.tag_configure("app", foreground="#64b5f6")

    # ------------------------------------------------ Environment

    def refresh_environment(self):
        version = ".".join(map(str, sys.version_info[:3]))
        self.python_ok = sys.version_info[:2] >= MIN_PYTHON
        self.python_label.configure(text=f"Python {version} " + ("✔" if self.python_ok else "✖ se requiere 3.12"),
                                    fg=GREEN if self.python_ok else RED)
        self.venv = venv_state() if self.python_ok else 'missing'
        texts = {'missing': ("Entorno (.venv): no instalado", RED, "Instalar entorno"),
                 'other_python': (f"Entorno (.venv): creado con Python "
                                  f"{'.'.join(map(str, venv_version() or ('?',)))}, hay que recrearlo",
                                  ORANGE, "Recrear entorno"),
                 'outdated': ("Entorno (.venv): desactualizado (cambió requirements.txt o no se instaló desde aquí)",
                              ORANGE, "Actualizar entorno"),
                 'ready': ("Entorno (.venv): listo ✔", GREEN, "Reinstalar entorno")}
        text, color, button = texts[self.venv]
        self.venv_label.configure(text=text, fg=color)
        self.install_button.configure(text=button)
        self.set_busy(self.busy)

    def install(self):
        clear = self.venv in ('missing', 'other_python') and VENV.exists()
        if clear and not messagebox.askyesno(
                "Recrear entorno", "Se borrará la carpeta .venv y se creará de nuevo. ¿Continuar?"):
            return
        commands = []
        if self.venv in ('missing', 'other_python'):
            commands.append([str(base_python()), "-m", "venv"] + (["--clear"] if clear else []) + [str(VENV)])
        commands.append([str(VENV_PYTHON), "-m", "pip", "install", "-r", str(REQUIREMENTS),
                         "--disable-pip-version-check", "--progress-bar", "off"])
        self.clear_console()
        self.write_app("Instalando el entorno. La primera vez puede tardar unos minutos.\n")
        self.set_busy(True)
        self.status_set("Instalando el entorno…", GREY)
        self.start_commands(commands, self.install_done)

    def install_done(self, code):
        self.set_busy(False)
        if code == 0:
            STAMP.write_text(file_hash(REQUIREMENTS), encoding="utf-8")
            self.write_app("\nEntorno instalado.\n")
            self.status_set("Entorno instalado", GREEN)
        else:
            self.write_app("\nNo se pudo instalar el entorno. Si el error menciona la conexión, revise el internet o "
                           "el proxy de su red.\n", "error")
            self.status_set("No se pudo instalar el entorno", RED)
        self.refresh_environment()

    # ------------------------------------------------ Keys

    def load_keys(self):
        values = read_env()
        for key, var in self.key_vars.items():
            var.set(values.get(key, ''))
        first_time = not ENV_FILE.exists() or not all(values.get(k) for k, _, required, _ in KEYS if required)
        self.keys_intro.configure(text="Primera vez: ingrese sus claves de OpenAlex y Anthropic y pulse 'Verificar y "
                                       "guardar claves'." if first_time else "")
        if first_time:
            self.keys_intro.grid()
        else:
            self.keys_intro.grid_remove()

    def toggle_show(self, entry):
        entry.configure(show="" if entry.cget("show") else "•")

    def entered_keys(self):
        return {key: var.get().strip() for key, var in self.key_vars.items()}

    def keys_saved(self):
        saved = read_env()
        return all(saved.get(key, '') == value for key, value in self.entered_keys().items())

    def save_keys(self):
        values = self.entered_keys()
        missing = [label for key, label, required, _ in KEYS if required and not values[key]]
        if missing:
            messagebox.showwarning("Faltan claves", f"Ingrese la clave de {' y '.join(missing)}.")
            return
        for key in self.key_labels:
            self.key_status(key, "verificando…", GREY)
        self.set_busy(True)
        self.status_set("Verificando las claves…", GREY)

        def work():
            results = {key: CHECKS[key](value) for key, value in values.items()}
            self.events.put((self.save_keys_done, (values, results)))
        threading.Thread(target=work, daemon=True).start()

    def save_keys_done(self, values, results):
        self.set_busy(False)
        colors = {'ok': GREEN, 'invalid': RED, 'unknown': ORANGE}
        for key, (state, message) in results.items():
            self.key_status(key, ("✔ " if state == 'ok' else "✖ " if state == 'invalid' else "") + message,
                            colors[state])
        invalid = [label for key, label, _, _ in KEYS if results[key][0] == 'invalid']
        if invalid:
            self.status_set("Claves no guardadas: corrija " + ", ".join(invalid), RED)
            return
        write_env(values)
        self.load_keys()
        unknown = [label for key, label, _, _ in KEYS if results[key][0] == 'unknown']
        self.status_set("Claves guardadas en .env" + (f" (sin verificar: {', '.join(unknown)})" if unknown else ""),
                        ORANGE if unknown else GREEN)

    # ------------------------------------------------ Parameters

    def load_parameters(self):
        s = self.settings
        self.queries_var.set(s.get('queries', ''))
        self.subfields_var.set(s.get('subfields', ''))
        self.name_var.set(s.get('name', ''))
        self.update_var.set(s.get('update', True))
        self.stemming_var.set(s.get('stemming', True))
        self.on_queries_change()

    def save_settings(self):
        settings = {'queries': self.queries_var.get().strip(), 'subfields': self.subfields_var.get().strip(),
                    'name': self.name_var.get().strip(), 'update': self.update_var.get(),
                    'stemming': self.stemming_var.get()}
        try:
            SETTINGS_FILE.write_text(json.dumps(settings, indent=2, ensure_ascii=False), encoding="utf-8")
        except OSError:
            pass

    def pick_queries(self):
        path = filedialog.askopenfilename(title="Archivo de queries", initialdir=DATA,
                                          filetypes=[("JSON", "*.json"), ("Todos los archivos", "*.*")])
        if path:
            self.queries_var.set(display_path(path))

    def pick_subfields(self):
        path = filedialog.askopenfilename(title="Archivo de subcampos", initialdir=DATA,
                                          filetypes=[("Excel", "*.xlsx"), ("Todos los archivos", "*.*")])
        if path:
            self.subfields_var.set(display_path(path))

    def on_queries_change(self):
        text = self.queries_var.get().strip()
        self.queries_info = None
        if not text:
            self.queries_info_label.configure(text="Elija el JSON con los ejes temáticos del país.", fg=GREY)
        else:
            info, error = preview_queries(resolve(text))
            if error:
                self.queries_info_label.configure(text="✖ " + error, fg=RED)
            else:
                self.queries_info = info
                self.queries_info_label.configure(
                    text=f"✔ {info['country']} · {info['start_year']}–{info['end_year']} · {info['thematics']} ejes",
                    fg=GREEN)
                proposed = DATA / f"subfields_tagged_{country_slug(info['country'])}.xlsx"
                if not self.subfields_var.get().strip() and proposed.exists():
                    self.subfields_var.set(display_path(proposed))
        self.refresh_name()

    def output_name(self):
        ''' Name of the outputs: the field, or the default of the pipeline (None if it cannot be known yet) '''
        name = self.name_var.get().strip()
        if name:
            return name
        return f"bibliometrics_{country_slug(self.queries_info['country'])}" if self.queries_info else None

    def refresh_name(self):
        name = self.name_var.get().strip()
        if name and not NAME_PATTERN.match(name):
            self.name_hint.configure(text="✖ use solo letras sin tilde, números, _ y -", fg=RED)
        elif self.queries_info:
            default = f"bibliometrics_{country_slug(self.queries_info['country'])}"
            self.name_hint.configure(text="" if name else f"por defecto: {default}", fg=GREY)
        else:
            self.name_hint.configure(text="por defecto: bibliometrics_<país>", fg=GREY)
        self.refresh_notices()

    def refresh_notices(self):
        for child in self.notices_frame.winfo_children():
            child.destroy()
        name = self.output_name()
        if not name or not NAME_PATTERN.match(name):
            return
        for color, text in output_notices(name):
            tk.Label(self.notices_frame, text="ⓘ " + text, fg=color, anchor="w", justify="left",
                     wraplength=800).pack(anchor="w")

    # ------------------------------------------------ Run

    def validate_run(self):
        ''' Error message in Spanish, or None if everything is ready to run '''
        if self.venv != 'ready':
            return "Primero instale el entorno (sección 1)."
        if not self.keys_saved():
            return "Las claves cambiaron y no están guardadas: pulse 'Verificar y guardar claves'."
        saved = read_env()
        missing = [label for key, label, required, _ in KEYS if required and not saved.get(key)]
        if missing:
            return f"Falta la clave de {' y '.join(missing)} (sección 2)."
        if not self.queries_info:
            return "Revise el archivo de queries: " + self.queries_info_label.cget("text").lstrip("✖ ")
        subfields = self.subfields_var.get().strip()
        if not subfields:
            return "Elija el archivo de subcampos."
        if not resolve(subfields).exists():
            return "No se encontró el archivo de subcampos."
        if resolve(subfields).suffix.lower() != ".xlsx":
            return "El archivo de subcampos debe ser un Excel (.xlsx)."
        if not NAME_PATTERN.match(self.name_var.get().strip()):
            return "El nombre solo puede tener letras sin tilde, números, _ y -."
        return None

    def run(self):
        self.on_queries_change() # The file may have been edited after it was chosen
        error = self.validate_run()
        if error:
            messagebox.showwarning("No se puede ejecutar", error)
            return
        self.save_settings()
        self.run_name = self.output_name()
        command = pipeline_command(self.queries_var.get().strip(), self.subfields_var.get().strip(),
                                   self.name_var.get().strip(), self.stemming_var.get(), self.update_var.get())
        self.clear_console()
        self.write_app("Comando: python " + " ".join(
            f'"{part}"' if " " in part else part for part in [display_path(command[2])] + command[3:]) + "\n")
        self.stopped = False
        self.set_busy(True)
        self.status_set("Revisando el saldo de OpenAlex…", GREY)

        def balance_then_run():
            state, message = check_openalex(read_env().get('OPENALEX_API_KEY', ''))
            self.events.put((self.start_pipeline, (command, state, message)))
        threading.Thread(target=balance_then_run, daemon=True).start()

    def start_pipeline(self, command, state, message):
        colors = {'ok': GREEN, 'invalid': RED, 'unknown': ORANGE}
        self.key_status('OPENALEX_API_KEY', ("✔ " if state == 'ok' else "") + message, colors[state])
        self.write_app(f"OpenAlex: {message}\n\n")
        self.run_start = time.time()
        self.set_busy(True) # Enables Detener
        self.tick()
        self.start_commands([command], self.run_done)

    def run_done(self, code):
        elapsed = time.strftime("%H:%M:%S", time.gmtime(time.time() - self.run_start))
        self.run_start = None
        self.set_busy(False)
        if self.stopped:
            self.write_app("\nCorrida detenida. Los ejes ya consultados quedaron respaldados: para retomar, ejecute "
                           "de nuevo con 'Actualizar' desactivado.\n", "warning")
            self.status_set(f"Detenida por el usuario ({elapsed})", ORANGE)
        elif code == 0:
            self.write_app(f"\nTerminado en {elapsed}. Los resultados están en la carpeta outputs.\n")
            self.status_set(f"Terminado en {elapsed} ✔ — pulse 'Abrir resultados'", GREEN)
        else:
            self.write_app("\nLa corrida se detuvo: lea el último mensaje de la consola, que indica qué hacer.\n",
                           "error")
            self.status_set(f"La corrida se detuvo ({elapsed}): vea el último mensaje de la consola", RED)
        self.save_log()
        self.refresh_notices()

    def save_log(self):
        ''' Text of the console in outputs/{name}_backup/run_log_<date_time>.txt, to send to support '''
        if not self.run_name:
            return
        backup = OUTPUTS / f"{self.run_name}_backup"
        try:
            backup.mkdir(parents=True, exist_ok=True)
            path = backup / f"run_log_{time.strftime('%Y%m%d_%H%M%S')}.txt"
            path.write_text(self.console.get("1.0", "end-1c"), encoding="utf-8")
            self.write_app(f"Registro guardado en {display_path(path)}\n")
        except OSError as e:
            self.write_app(f"No se pudo guardar el registro ({e})\n", "warning")

    def stop(self):
        if self.proc and self.proc.poll() is None and messagebox.askyesno(
                "Detener", "¿Detener la corrida? Los ejes ya consultados quedan respaldados y se puede retomar."):
            self.stopped = True
            self.proc.terminate()

    def tick(self):
        if self.run_start is not None:
            elapsed = time.strftime("%H:%M:%S", time.gmtime(time.time() - self.run_start))
            self.status_set(f"Ejecutando… {elapsed}", GREY)
            self.root.after(1000, self.tick)

    # ------------------------------------------------ Subprocesses

    def start_commands(self, commands, on_done):
        ''' Run commands one after another in a thread (stops at the first failure), streaming their output to the
        console; on_done(exit code) runs in the Tk thread '''
        def work():
            code = 0
            for command in commands:
                try:
                    self.proc = subprocess.Popen(command, cwd=ROOT, env=child_env(), stdin=subprocess.DEVNULL,
                                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, bufsize=0,
                                                 creationflags=NO_WINDOW)
                except OSError as e:
                    self.events.put((self.write_app, (f"No se pudo iniciar {Path(command[0]).name}: {e}\n", "error")))
                    code = 1
                    break
                decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
                while chunk := self.proc.stdout.read(4096):
                    self.events.put((self.write_console, (decoder.decode(chunk),)))
                self.events.put((self.write_console, (decoder.decode(b"", final=True),)))
                code = self.proc.wait()
                if code != 0:
                    break
            self.proc = None
            self.events.put((on_done, (code,)))
        threading.Thread(target=work, daemon=True).start()

    def poll_events(self):
        try:
            while True:
                func, args = self.events.get_nowait()
                func(*args)
        except queue.Empty:
            pass
        self.root.after(100, self.poll_events)

    # ------------------------------------------------ Console

    def clear_console(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", "end")
        self.console.configure(state="disabled")
        self.cr_pending = False

    def write_console(self, text):
        ''' Append output of a subprocess. A \\r (tqdm progress bars) makes the next text replace the current line '''
        if not text:
            return
        at_bottom = self.console.yview()[1] > 0.999
        self.console.configure(state="normal")
        for part in re.split(r'(\r\n|\r|\n)', text):
            if part in ("\n", "\r\n"):
                self.console.insert("end-1c", "\n")
                self.cr_pending = False
            elif part == "\r":
                self.cr_pending = True
            elif part:
                if self.cr_pending:
                    self.console.delete("end-1c linestart", "end-1c")
                    self.cr_pending = False
                self.console.insert("end-1c", part)
                self.tag_line()
        self.console.configure(state="disabled")
        if at_bottom:
            self.console.see("end")

    def tag_line(self):
        ''' Color of the current console line by its content '''
        start, end = "end-1c linestart", "end-1c"
        line = self.console.get(start, end)
        for tag in ("warning", "error", "stage"):
            self.console.tag_remove(tag, start, end)
        if line.lstrip().startswith(("Warning", "Retry")):
            self.console.tag_add("warning", start, end)
        elif re.match(r'\s*(Traceback|[A-Za-z]*Error\b|Exception)', line) or "failed" in line.lower():
            self.console.tag_add("error", start, end)
        elif re.match(r'\[[a-z_]+\]', line):
            self.console.tag_add("stage", start, end)

    def write_app(self, text, tag="app"):
        ''' Message of the interface itself (not of the pipeline) '''
        self.console.configure(state="normal")
        if self.cr_pending or self.console.get("end-2c", "end-1c") not in ("", "\n"):
            self.console.insert("end-1c", "\n")
            self.cr_pending = False
        self.console.insert("end-1c", text, tag)
        self.console.configure(state="disabled")
        self.console.see("end")

    # ------------------------------------------------ State

    def status_set(self, text, color):
        self.status.set(text)
        self.status_label.configure(fg=color)

    def set_busy(self, busy):
        ''' Disable the fields and buttons while something runs '''
        self.busy = busy
        state = "disabled" if busy else "normal"
        widgets = [self.save_keys_button, self.queries_entry, self.queries_button, self.subfields_entry,
                   self.subfields_button, self.name_entry, self.update_check, self.stemming_check]
        widgets += [w for pair in self.key_entries.values() for w in pair]
        for widget in widgets:
            widget.configure(state=state)
        self.install_button.configure(state="disabled" if busy or not self.python_ok else "normal")
        self.run_button.configure(state="disabled" if busy or self.venv != 'ready' else "normal")
        self.stop_button.configure(state="normal" if busy and self.run_start is not None else "disabled")

    def on_close(self):
        if self.proc and self.proc.poll() is None:
            if not messagebox.askyesno("Salir", "Hay un proceso en curso. ¿Detenerlo y salir? Los ejes ya consultados "
                                                "quedan respaldados y se puede retomar."):
                return
            self.proc.terminate()
        self.save_settings()
        self.root.destroy()

    def on_error(self, exc, value, tb):
        ''' Unexpected errors of the interface: shown in the console instead of being lost '''
        self.write_app("Error de la interfaz:\n" + "".join(traceback.format_exception(exc, value, tb)), "error")


def main():
    if IS_WINDOWS:
        try: # Sharp text on high resolution screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    try:
        App(root)
    except Exception:
        messagebox.showerror("Reporte bibliométrico", "No se pudo abrir la interfaz:\n\n" + traceback.format_exc())
        raise
    root.mainloop()


if __name__ == "__main__":
    main()
