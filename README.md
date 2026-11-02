# MiFicha v0.1 — prototipo

MiFicha es un prototipo de app de salud que explica documentos médicos en español simple: resultados de laboratorio, informes narrativos (por ejemplo un PET) y órdenes de exámenes. Por cada documento dice qué significa cada valor, qué tan fuera de rango está, qué efecto podría tener en la salud y qué tan urgente es buscar atención médica — sin dar diagnósticos.

## Self-hosting con Docker: código y datos separados

Esta es la forma recomendada para uso personal/familiar, y la que usa
el despliegue privado: el código vive en este repo y en la imagen Docker;
los datos viven fuera de la imagen, en una carpeta normal del host.

```bash
git clone https://github.com/danlegion/mificha.git
cd mificha
cp .env.example .env
# Edita .env: GEMINI_API_KEY, SECRET_KEY (genera una aleatoria) y
# BASE_URL (p. ej. http://mini-pc:18473 si usas Tailscale).
# Deja SUPABASE_URL vacío para usar SQLite local.
docker compose up -d --build
```

Abre la app en `http://<tu-máquina>:18473`, o solo por Tailscale en un
despliegue privado. No hagas port-forward de tu router a este puerto.

La carpeta de datos (por defecto `./mificha-data`, configurable con
`MIFICHA_DATA_DIR`) es la fuente de verdad y nunca se sube a git
(está en `.gitignore` y `.dockerignore`):

```
mificha-data/
  mificha.db         # índice SQLite: usuarios, documentos, intakes, shares
  originales/*.pdf   # PDFs originales subidos, sin modificar
  quarantine/*.pdf   # subidas retenidas durante el procesamiento
```

El contenedor es desechable: puedes borrarlo y recrearlo sin perder
datos, siempre que no borres la carpeta de datos. Haz copia de esa
carpeta, no del contenedor:

```bash
cp -r ./mificha-data /ruta/de/respaldo/
# o sincronización plana a un bucket / carpeta de Drive, p. ej.:
rclone sync ./mificha-data remote:mificha-backup
```

Restaurar es copiar la carpeta de vuelta. La app se puede reconstruir
a partir de ella; ningún proveedor en la nube es necesario para leer
los archivos (son PDFs, SQLite y, en el futuro, JSON/CSV por persona).
Sin llave de Gemini, la app corre en modo demo local, sin llamadas de red.

Licencia: GNU Affero General Public License v3.0, ver `LICENSE`.

## Requisitos

- Python 3.11 o superior
- (Opcional) claves de API para modo real: Anthropic, Supabase, proveedor OCR

## Instalación

```bash
cd ~/workspace/mificha
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Cómo correr

```bash
cd ~/workspace/mificha
source .venv/bin/activate
uvicorn app.main:app --reload --port 18473
```

La app queda en http://localhost:18473.

## Variables de entorno

| Variable | Obligatoria | Default (si vacía) | Descripción |
|---|---|---|---|
| `GEMINI_API_KEY` | No | modo demo | Clave de Gemini (AI Studio). Primera opción si está presente. |
| `GEMINI_MODEL` | No | `gemini-3.5-flash-lite` | Modelo Gemini (verificado en free tier). |
| `ANTHROPIC_API_KEY` | No | modo demo | Clave de Anthropic (alternativa). Vacías ambas = explicación local determinista, sin llamadas de red. |
| `LLM_MODEL` | No | default del backend | Modelo LLM para Anthropic. |
| `SUPABASE_URL` | No | SQLite local | URL del proyecto Supabase (producción). |
| `SUPABASE_ANON_KEY` | No | — | Clave anónima: solo para Supabase Auth (magic links). Con RLS activado ya no accede a las tablas. |
| `SUPABASE_SERVICE_KEY` | Sí, con RLS activado | — | Clave service_role (solo servidor, nunca al navegador): acceso a datos y Storage. Obligatoria desde que RLS está activado. |
| `OCR_PROVIDER` | No | `tesseract` | Tesseract local (español + inglés, dentro de la imagen Docker). |
| `DOCAI_PROCESSOR` | Solo para fallback | — | Procesador de Google Document AI (`projects/.../processors/...`); solo se usa si Tesseract no extrae texto útil. |
| `DOCAI_CREDENTIALS_JSON` | Solo para fallback | — | JSON de la cuenta de servicio de Document AI. |
| `SECRET_KEY` | Sí | — | Firma de sesiones/tokens. **Cámbiala en producción.** |
| `DATABASE_URL` / `STORAGE_DIR` | — | — | Ya no se leen en el código actual: SQLite vive en la carpeta de datos (`./data/mificha.db` local, `./mificha-data/mificha.db` con Docker) y los PDFs en `originales/` dentro de esa misma carpeta. |
| `PORT` | No | `18473` | Puerto del servidor. |
| `BASE_URL` | No | `http://localhost:18473` | URL pública; construye los enlaces de WhatsApp. En producción usa tu dominio. |

## Qué funciona sin keys (modo demo)

- Explicación local determinista (sin LLM): plantillas en español simple, sin llamadas de red.
- Auth de desarrollo (sin Supabase).
- SQLite local en `./data/mificha.db` y PDFs en `./data/originales`.
- Detección de páginas escaneadas: se marcan como tales (sin OCR no se extrae su texto).

## Para producción hay que conectar

1. **Supabase**: `SUPABASE_URL`, `SUPABASE_ANON_KEY`, `SUPABASE_SERVICE_KEY` (y `DATABASE_URL` si usas Postgres directo).
2. **Gemini (o Anthropic)**: `GEMINI_API_KEY` (y `GEMINI_MODEL` si quieres fijar el modelo) para la explicación en lenguaje natural. Alternativa: `ANTHROPIC_API_KEY` + `LLM_MODEL`.
3. **OCR**: `OCR_PROVIDER=azure|google` con sus credenciales, para extraer texto de PDFs escaneados.

## Cómo correr los tests

```bash
cd ~/workspace/mificha
python -m pytest -q
```

- Los tests no hacen llamadas de red y generan sus PDFs en memoria (PyMuPDF).
- `tests/test_explain_llm.py` prueba los proveedores con la red mockeada.
- Contratos que cubren: `tests/test_pdf_extract.py` (extracción de PDFs), `tests/test_urgency.py` (niveles de urgencia), `tests/test_schemas.py` (modelo Explanation).

## Estructura del proyecto

```
mificha/
├── app/
│   ├── main.py                 # app FastAPI (backend)
│   ├── schemas.py              # modelos pydantic de la Explanation
│   └── services/
│       ├── pdf_extract.py      # extract_document(pdf_bytes) -> dict
│       ├── urgency.py          # decide_urgency(signals) -> dict
│       └── explain.py          # explicación (demo local o LLM)
├── static/                     # assets frontend
├── templates/                  # vistas
├── tests/                      # suite pytest (contratos)
├── data/                       # SQLite + PDFs (local, no va a git)
├── .env.example
├── requirements.txt
└── README.md
```

## Qué está implementado vs. qué es stub

- **Real**: extracción de texto de PDFs digitales, detección de páginas escaneadas (menos de 20 caracteres → escaneada), reglas de urgencia, esquemas de la Explanation.
- **Stub / demo**: explicación en lenguaje simple sin LLM (plantillas deterministas); OCR de escaneados (solo se marcan hasta conectar un proveedor); auth dev y SQLite local.

## Reglas duras

1. **Los rangos de referencia salen solo del documento.** Nunca inventar rangos ni valores.
2. **Sin diagnósticos.** Se explican valores y hallazgos; no se dice qué enfermedad tiene el usuario.
3. **La prioridad de una orden se cita literal** en la explicación; nunca se inventa.
4. **Ante dato crítico incierto, ser conservador**: nunca `no_urgente`.
5. **Lenguaje simple** en español: valores fuera de rango marcados con qué tan lejos están, y guía visible de si/cuándo buscar atención médica.
