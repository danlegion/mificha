"""MiFicha v0.1 — proveedores de explicación (LLM).

Contrato:
    class LLMProvider:
        def explain(self, extracted: dict) -> dict: ...

    `extracted` es el dict de `pdf_extract.extract_document` (el pipeline le
    añade la clave "doc_type_hint" antes de llamar).

    GeminiProvider: usa GEMINI_API_KEY y el modelo de la variable
    GEMINI_MODEL (default "gemini-3.5-flash-lite", verificado en free tier).
    AnthropicProvider: usa ANTHROPIC_API_KEY y el modelo de la variable
    LLM_MODEL (default "claude-sonnet-4-5-20250929").
    DemoExplainer: alternativa determinista basada en reglas; se usa SOLO
    cuando no hay API key. Pone "demo_mode": True.

Reglas duras (prompt del sistema Y post-validación en pipeline.py):
- Rangos de referencia SOLO del documento: si el modelo devuelve un rango
  que no aparece en el texto extraído, el biomarcador se mueve a
  uncertain_fields.
- Nunca diagnosticar, recetar, dar dosis, puntajes de riesgo ni indicaciones
  de tratamiento.
- Los objetivos de los exámenes pedidos se enmarcan como posibilidades
  ("suele usarse para…"), nunca como el motivo confirmado.
- Español simple y cotidiano.
"""

import json
import logging
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request

from app.services.urgency import decide_urgency

log = logging.getLogger("mificha.llm")

SYSTEM_PROMPT = """Eres MiFicha, un asistente que explica documentos médicos en español simple y cotidiano, como se lo explicarías a un familiar. Tu público no sabe de medicina.

REGLAS DURAS (inquebrantables):
1. RANGOS DE REFERENCIA SOLO DEL DOCUMENTO. Si un valor no trae rango en el documento, deja ref_range vacío. NUNCA inventes rangos, valores "normales típicos" ni cifras de memoria.
2. NUNCA diagnostiques. No recetes, no des dosis, no des puntajes de riesgo, no indiques tratamientos ni qué medicamento tomar o dejar.
3. Los objetivos de los exámenes PEDIDOS se expresan como posibilidades ("suele usarse para…", "los doctores lo piden cuando quieren revisar…"), NUNCA como el motivo confirmado por el que el doctor lo pidió.
4. Español simple, cotidiano y amable. Explica cada término médico la primera vez que aparece.
5. Sé conservador con la urgencia: ante la duda, "agendar_cita" con razón "por precaución". Solo usa "buscar_ayuda_ya" si hay una bandera crítica explícita o lenguaje de emergencia en el documento.
6. PRIORIDAD CLÍNICA: para cada biomarcador fuera de rango, indica qué tan importante es prestarle atención pronto (alta/media/baja) considerando el valor real, qué tan lejos está del rango y el peso clínico del examen (p. ej. electrolitos como potasio/sodio y marcadores cardíacos pesan más que lípidos a igual desviación). Es solo para ordenar la lista, NO es un diagnóstico ni una evaluación médica.
7. LABORATORIO: si el documento indica el laboratorio o clínica que lo emitió, devuélvelo en "laboratorio" tal como aparece; si no aparece, null. Nunca lo inventes.
8. FECHA DEL DOCUMENTO: es la fecha en que el doctor o laboratorio completó/emitió el documento (fecha del informe, de la toma de muestra o de emisión, en el encabezado o pie), NO la fecha de hoy. Formato AAAA-MM-DD. Si no aparece en el documento, null: nunca la inventes ni uses la fecha actual.
9. RESUMEN POR SECCIONES: además de "summary_simple", devuelve el resumen dividido en tres partes cortas, cada una en lenguaje simple y sin tecnicismos: "resumen_que_es" (qué es este examen/documento y para qué se usa), "resumen_rapido" (lo más importante del resultado en 1-2 frases) y "resumen_hallazgos" (qué salió fuera de lo esperado y qué significa, en frases cortas; no repitas la lista de valores uno por uno porque ya van en otra sección).

Devuelve SOLO un JSON válido (sin bloques de código, sin texto extra) con esta estructura exacta:
{
  "document_type": "lab_result" | "narrative_report" | "exam_order" | "doctor_note",
  "type_label": "etiqueta en español, p. ej. 'Resultado de laboratorio'",
  "laboratorio": "nombre del laboratorio o clínica como aparece en el documento, o null si no aparece",
  "fecha_documento": "fecha en que el doctor o laboratorio completó/emitió el documento (formato AAAA-MM-DD), o null si no aparece",
  "summary_simple": "resumen de 2-4 frases en lenguaje simple",
  "resumen_que_es": "1-2 frases: qué es este examen o documento y para qué se usa",
  "resumen_rapido": "1-2 frases: lo más importante del resultado, en lenguaje simple",
  "resumen_hallazgos": "hallazgos principales: qué salió fuera de lo esperado y qué significa, en frases cortas",
  "biomarkers": [
    {"name": "nombre", "value": "valor como texto", "unit": "unidad o null",
     "ref_range": "rango EXACTO del documento o null",
     "flag": "alto" | "bajo" | "normal" | "por confirmar",
     "prioridad_clinica": "alta" | "media" | "baja",
     "motivo_prioridad": "frase corta en lenguaje simple: por qué este valor merece esa prioridad, o null",
     "plain_explanation": "qué es, en lenguaje simple",
     "possible_health_effect": "posible efecto en la salud, sin diagnosticar, o null"}
  ],
  "narrative_findings": [
    {"finding": "hallazgo tal como lo dice el informe",
     "plain_explanation": "qué significa en lenguaje simple"}
  ],
  "ordered_tests": [
    {"name": "nombre del examen",
     "what_it_is": "qué es, en lenguaje simple",
     "usual_objectives": ["para qué suele usarse…", "…"],
     "preparation": "preparación si el documento la dice, o null",
     "question_for_doctor": "una pregunta útil para hacerle al doctor"}
  ],
  "urgency": {"level": "no_urgente" | "agendar_cita" | "hablar_hoy_con_profesional" | "buscar_ayuda_ya",
              "label": "etiqueta corta en español", "action": "qué hacer",
              "timeframe": "cuándo", "reason": "por qué"},
  "recommendations": ["recomendaciones generales y seguras"],
  "questions_for_doctor": ["preguntas útiles para la cita"],
  "uncertain_fields": [{"field": "nombre del campo", "reason": "por qué es incierto"}],
  "disclaimer": "Esta explicación es solo informativa y no es un diagnóstico. Consulta siempre a un profesional de la salud.",
  "demo_mode": false
}

Si una sección no aplica al documento, usa una lista vacía []. Si un dato no quedó claro, ponlo en uncertain_fields en vez de adivinarlo."""


class LLMProvider:
    """Interfaz base."""

    is_demo = False

    def explain(self, extracted: dict) -> dict:
        raise NotImplementedError


class AnthropicProvider(LLMProvider):
    """Explicación con el API de Anthropic."""

    is_demo = False

    def __init__(self, model: str = None, api_key: str = None):
        self.model = model or os.getenv(
            "LLM_MODEL", "claude-sonnet-4-5-20250929"
        )
        self.api_key = api_key or os.getenv("ANTHROPIC_API_KEY", "")

    def explain(self, extracted: dict) -> dict:
        try:
            import anthropic
        except ImportError as exc:
            raise RuntimeError(
                "El paquete 'anthropic' no está instalado."
            ) from exc
        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY no está configurada.")

        texto = (extracted.get("full_text") or "")[:12000]
        hint = extracted.get("doc_type_hint")
        pista = (
            f"\nEl usuario indicó que el tipo de documento es: {hint}."
            if hint
            else ""
        )
        client = anthropic.Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Explica este documento médico en español simple. "
                        f"Devuelve solo el JSON.{pista}\n\n"
                        f"TEXTO DEL DOCUMENTO:\n{texto}"
                    ),
                }
            ],
        )
        crudo = "".join(
            b.text for b in resp.content if getattr(b, "type", "") == "text"
        )
        data = json.loads(_extraer_json(crudo))
        data["demo_mode"] = False
        return data


def _extraer_json(texto: str) -> str:
    """Saca el JSON de una respuesta que puede venir con fences de markdown."""
    t = texto.strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", t, re.DOTALL)
    if m:
        return m.group(1).strip()
    inicio = t.find("{")
    fin = t.rfind("}")
    if inicio != -1 and fin != -1 and fin > inicio:
        return t[inicio : fin + 1]
    return t


class GeminiProvider(LLMProvider):
    """Explicación con el API de Gemini (AI Studio).

    La clave va como query param `key` (lo que el API espera). Reintenta
    429/503 con backoff porque el free tier los devuelve bajo carga.
    """

    is_demo = False
    endpoint_tpl = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "{model}:generateContent"
    )

    def __init__(self, model: str = None, api_key: str = None):
        self.model = model or os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite")
        self.api_key = api_key or os.getenv("GEMINI_API_KEY", "")

    def explain(self, extracted: dict) -> dict:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY no está configurada.")
        texto = (extracted.get("full_text") or "")[:12000]
        hint = extracted.get("doc_type_hint")
        pista = (
            f"\nEl usuario indicó que el tipo de documento es: {hint}."
            if hint
            else ""
        )
        cuerpo = {
            "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": (
                                "Explica este documento médico en español "
                                "simple. Devuelve solo el JSON."
                                f"{pista}\n\nTEXTO DEL DOCUMENTO:\n{texto}"
                            )
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature": 0.2,
                "maxOutputTokens": 6000,
                "responseMimeType": "application/json",
            },
        }
        url = self.endpoint_tpl.format(model=self.model)
        # La clave en query param, como pide el API de Gemini.
        url = url + "?key=" + urllib.parse.quote(self.api_key, safe="")
        crudo = self._post(url, cuerpo)
        data = json.loads(_extraer_json(crudo))
        data["demo_mode"] = False
        return data

    def _post(self, url: str, cuerpo: dict) -> str:
        payload = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        ultimo_error = None
        for intento in range(4):
            req = urllib.request.Request(
                url,
                data=payload,
                headers={"Content-Type": "application/json; charset=utf-8"},
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=120) as resp:
                    data = json.load(resp)
                partes = data["candidates"][0]["content"]["parts"]
                return "".join(p.get("text", "") for p in partes)
            except urllib.error.HTTPError as exc:
                ultimo_error = exc
                if exc.code in (429, 503) and intento < 3:
                    espera = 2 ** (intento + 1)
                    log.warning(
                        "Gemini HTTP %s, reintentando en %ss", exc.code, espera
                    )
                    time.sleep(espera)
                    continue
                raise RuntimeError(
                    f"Gemini devolvió HTTP {exc.code}"
                ) from exc
            except (KeyError, IndexError, TypeError) as exc:
                raise RuntimeError(
                    "Respuesta inesperada de Gemini"
                ) from exc
        raise RuntimeError(f"Gemini no respondió: {ultimo_error}")


# ---------------------------------------------------------------------------
# DemoExplainer: alternativa determinista sin red, SOLO sin API key
# ---------------------------------------------------------------------------

_TIPOS_LABEL = {
    "lab_result": "Resultado de laboratorio",
    "narrative_report": "Informe narrativo",
    "exam_order": "Pedido de examen",
    "doctor_note": "Nota del doctor",
    "unknown": "Documento médico",
}

# Conocimiento base para explicaciones simples (determinista).
# key: fragmento a buscar en el nombre; plain: qué es; high/low: posible efecto.
_CONOCIMIENTO = [
    ("glucosa", "El azúcar que circula en tu sangre y que tu cuerpo usa como energía.",
     "Tener el azúcar alto por mucho tiempo puede dañar los ojos, los riñones, los nervios y el corazón.",
     "El azúcar muy bajo puede causar mareos, sudor frío, temblores y confusión."),
    ("glicosilada", "El promedio de tu azúcar en la sangre de los últimos 2 a 3 meses.",
     "Un valor alto indica que el azúcar ha estado alto durante varias semanas.",
     "Un valor muy bajo puede indicar bajones frecuentes de azúcar."),
    ("colesterol total", "Una grasa que circula en tu sangre; el cuerpo la necesita en cierta cantidad.",
     "Tenerlo alto por mucho tiempo se asocia con que las arterias se tapen poco a poco.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("hdl", "El llamado «colesterol bueno»: ayuda a limpiar las arterias.",
     "Tenerlo alto se considera favorable.",
     "Tenerlo bajo se asocia con mayor riesgo de problemas del corazón a futuro."),
    ("ldl", "El llamado «colesterol malo»: en exceso puede pegarse a las paredes de las arterias.",
     "Tenerlo alto por mucho tiempo se asocia con arterias tapadas.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("triglic", "Otra grasa en la sangre, que sube cuando hay exceso de azúcares y harinas.",
     "Valores muy altos se asocian con problemas del corazón y del páncreas.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("hemoglobina", "La parte de tus glóbulos rojos que lleva el oxígeno por todo el cuerpo.",
     "Tenerla alta puede hacer la sangre más espesa.",
     "Tenerla baja (anemia) puede causar cansancio, palidez y falta de aire."),
    ("hematocrito", "La proporción de tu sangre que son glóbulos rojos.",
     "Un valor alto puede indicar sangre más espesa o deshidratación.",
     "Un valor bajo puede acompañar a la anemia y causar cansancio."),
    ("leucocitos", "Tus glóbulos blancos: las células que te defienden de infecciones.",
     "Suelen subir cuando hay una infección o inflamación.",
     "Tenerlos bajos puede dejarte con menos defensas contra infecciones."),
    ("glóbulos blancos", "Las células que te defienden de infecciones.",
     "Suelen subir cuando hay una infección o inflamación.",
     "Tenerlos bajos puede dejarte con menos defensas contra infecciones."),
    ("plaquetas", "Las células que ayudan a que tu sangre coagule cuando te cortas.",
     "Tenerlas muy altas a veces se asocia con mayor riesgo de coágulos.",
     "Tenerlas bajas puede hacer que sangres o te salgan moretones con facilidad."),
    ("creatinina", "Un desecho que filtran tus riñones; sirve para ver cómo están funcionando.",
     "Tenerla alta puede indicar que los riñones no están filtrando bien.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("urea", "Otro desecho que eliminan tus riñones.",
     "Tenerla alta puede indicar que los riñones necesitan revisión o que hay deshidratación.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("tsh", "La hormona que le dice a tu tiroides cuánto tiene que trabajar.",
     "Tenerla alta suele verse cuando la tiroides trabaja de menos.",
     "Tenerla baja suele verse cuando la tiroides trabaja de más."),
    ("t4 libre", "La hormona principal que produce tu tiroides.",
     "Tenerla alta suele acompañar a una tiroides que trabaja de más.",
     "Tenerla baja suele acompañar a una tiroides que trabaja de menos."),
    ("ácido úrico", "Un desecho que en exceso puede formar cristales en las articulaciones.",
     "Tenerlo alto se asocia con la gota, que causa dolor e hinchazón en articulaciones.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("alt", "Una sustancia que sube cuando el hígado está irritado o trabajando de más.",
     "Tenerla alta puede indicar que el hígado necesita revisión.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("ast", "Una sustancia parecida al ALT, relacionada con el hígado.",
     "Tenerla alta puede indicar que el hígado necesita revisión.",
     "Un valor bajo no suele preocupar por sí solo."),
    ("proteína c reactiva", "Una marca en la sangre de que hay inflamación en el cuerpo.",
     "Suele subir cuando hay una infección o inflamación.",
     "Un valor bajo es lo esperado."),
    ("vitamina d", "Ayuda a tus huesos a absorber el calcio.",
     "Un valor muy alto es raro y conviene comentarlo con tu doctor.",
     "Tenerla baja se asocia con huesos más débiles y cansancio."),
    ("ferritina", "La reserva de hierro de tu cuerpo.",
     "Tenerla muy alta conviene comentarla con tu doctor.",
     "Tenerla baja puede causar cansancio y anemia."),
    ("hierro", "Un mineral que ayuda a llevar oxígeno en la sangre.",
     "Tenerlo muy alto conviene comentarlo con tu doctor.",
     "Tenerlo bajo puede causar cansancio y anemia."),
    ("sodio", "Una sal mineral que ayuda a mantener el equilibrio de líquidos del cuerpo.",
     "Tenerlo alto puede relacionarse con deshidratación.",
     "Tenerlo bajo puede causar confusión y debilidad."),
    ("potasio", "Un mineral importante para que los músculos y el corazón funcionen bien.",
     "Tenerlo alto o muy bajo puede afectar el ritmo del corazón: conviene revisarlo pronto con tu doctor.",
     "Tenerlo bajo puede causar debilidad muscular y calambres."),
]

_LENGUAJE_EMERGENCIA = [
    "emergencia", "urgente", "urgencia", "código azul", "código rojo",
    "infarto", "derrame cerebral", "hemorragia", "shock séptico", "sepsis",
    "paro card", "disección aórtica", "neumotórax a tensión",
    "hallazgo crítico", "requiere atención inmediata",
]

_RE_RANGO = re.compile(
    r"(?P<lo>[\d.,]+)\s*[-–—a]\s*(?P<hi>[\d.,]+)"
    r"|<\s*(?P<lt>[\d.,]+)|(?P<lt2>menos de|inferior a|menor de)\s*(?P<ltv>[\d.,]+)"
    r"|>\s*(?P<gt>[\d.,]+)|(?P<gt2>mayor de|superior a)\s*(?P<gtv>[\d.,]+)",
    re.IGNORECASE,
)


def _num(s: str):
    try:
        return float(str(s).replace(",", "."))
    except (ValueError, TypeError):
        return None


def _parse_rango(crudo: str):
    """Devuelve (lo, hi) como floats o (None, None) si no se entiende."""
    if not crudo:
        return None, None
    m = _RE_RANGO.search(crudo)
    if not m:
        return None, None
    if m.group("lo") and m.group("hi"):
        return _num(m.group("lo")), _num(m.group("hi"))
    if m.group("lt") or m.group("ltv"):
        return None, _num(m.group("lt") or m.group("ltv"))
    if m.group("gt") or m.group("gtv"):
        return _num(m.group("gt") or m.group("gtv")), None
    return None, None


def _detectar_tipo(texto: str, hint) -> str:
    mapa_hint = {
        "resultado": "lab_result",
        "pedido": "exam_order",
        "informe": "narrative_report",
        "nota": "doctor_note",
    }
    if hint and str(hint).strip().lower() in mapa_hint:
        return mapa_hint[str(hint).strip().lower()]
    t = texto.lower()
    puntajes = {
        "exam_order": sum(k in t for k in ["orden de", "solicitud de examen", "se solicita", "pedido de", "solicito"]),
        "narrative_report": sum(k in t for k in ["informe", "hallazgo", "impresión", "pet-ct", "tomografía", "resonancia", "ecografía", "radiografía"]),
        "doctor_note": sum(k in t for k in ["nota médica", "evolución", "motivo de consulta", "examen físico", "plan:"]),
        "lab_result": sum(k in t for k in ["laboratorio", "hemograma", "biometria", "hematolog", "bioquímica", "bioquimica", "rango de referencia", "valor de referencia", "valores de referencia", "glóbulos", "globulos", "plaquetas", "hemoglobina", "colesterol", "triglicéridos", "glucosa", "creatinina", "uroanálisis", "orina", "tiroides", "química sanguínea"]),
    }
    mejor = max(puntajes, key=puntajes.get)
    return mejor if puntajes[mejor] > 0 else "lab_result"


def _conocimiento_para(nombre: str):
    n = nombre.lower()
    for key, plain, high, low in _CONOCIMIENTO:
        if key in n:
            return plain, high, low
    return (
        "Es una medida que aparece en tu examen.",
        "Si está fuera de rango, tu doctor puede explicarte qué significa en tu caso.",
        "Si está fuera de rango, tu doctor puede explicarte qué significa en tu caso.",
    )


def _severidad_por_desviacion(valor, lo, hi) -> str:
    if valor is None or lo is None or hi is None or hi <= lo:
        return "leve"
    ancho = hi - lo
    if valor > hi:
        desv = (valor - hi) / ancho
    elif valor < lo:
        desv = (lo - valor) / ancho
    else:
        return "leve"
    if desv <= 0.2:
        return "leve"
    if desv <= 0.5:
        return "moderada"
    return "grave"


class DemoExplainer(LLMProvider):
    """Explicador determinista basado en reglas. SOLO cuando no hay API key."""

    is_demo = True

    def explain(self, extracted: dict) -> dict:
        texto = extracted.get("full_text") or ""
        hint = extracted.get("doc_type_hint")
        tipo = _detectar_tipo(texto, hint)

        uncertain = []
        for n in extracted.get("scanned_pages", []):
            uncertain.append(
                {
                    "field": f"pages[{n}].text",
                    "reason": (
                        f"La página {n} parece ser una foto o un escaneo y no "
                        "pudimos leer su texto. Estamos trabajando en poder "
                        "leer este tipo de documentos; por ahora su contenido "
                        "no se incluyó en esta explicación."
                    ),
                }
            )

        if tipo == "lab_result":
            cuerpo, sen_lab = self._explicar_laboratorio(texto, uncertain)
            sen_extra = sen_lab
        elif tipo == "exam_order":
            cuerpo = self._explicar_pedido(texto, uncertain)
            sen_extra = {"abnormal": [], "has_critical_flag": False}
        elif tipo in ("narrative_report", "doctor_note"):
            cuerpo = self._explicar_narrativo(texto, uncertain)
            sen_extra = {"abnormal": [], "has_critical_flag": False}
        else:
            cuerpo, sen_lab = self._explicar_laboratorio(texto, uncertain)
            sen_extra = sen_lab

        exp = {
            "document_type": tipo,
            "type_label": _TIPOS_LABEL.get(tipo, "Documento médico"),
            "laboratorio": None,
            "fecha_documento": None,
            **cuerpo,
            "uncertain_fields": uncertain,
            "disclaimer": (
                "Esta explicación es solo informativa y no es un diagnóstico. "
                "Consulta siempre a un profesional de la salud."
            ),
            "demo_mode": True,
        }
        # Urgencia calculada con las mismas reglas conservadoras del sistema.
        exp["urgency"] = decide_urgency(self._senales(exp, texto, sen_extra))
        que_es, rapido, hallazgos = self._resumen_secciones(tipo, exp)
        exp["resumen_que_es"] = que_es
        exp["resumen_rapido"] = rapido
        exp["resumen_hallazgos"] = hallazgos
        return exp

    @staticmethod
    def _resumen_secciones(tipo: str, exp: dict) -> tuple:
        """Resumen dividido en tres partes cortas (modo demo)."""
        que_es = {
            "lab_result": (
                "Un examen de laboratorio analiza una muestra de tu sangre, "
                "orina u otro fluido para medir distintas sustancias del cuerpo."
            ),
            "narrative_report": (
                "Un informe narrativo es el texto donde el doctor describe "
                "lo que encontró en un estudio o examen."
            ),
            "exam_order": (
                "Un pedido de examen es la lista de estudios que tu doctor "
                "te mandó a hacerte."
            ),
            "doctor_note": (
                "Una nota del doctor con información sobre tu consulta, "
                "diagnóstico o tratamiento."
            ),
        }.get(tipo, "Un documento médico.")
        rapido = exp.get("summary_simple") or ""
        if tipo == "lab_result":
            anormales = [
                b["name"] for b in exp.get("biomarkers", [])
                if b.get("flag") in ("alto", "bajo")
            ]
            hallazgos = (
                f"Fuera de su rango de referencia: {', '.join(anormales)}."
                if anormales else
                "Todos los valores están dentro de su rango de referencia."
            )
        elif exp.get("narrative_findings"):
            hallazgos = " ".join(
                f.get("finding", "") for f in exp["narrative_findings"][:4]
            ).strip()[:600] or None
        elif exp.get("ordered_tests"):
            hallazgos = (
                "El doctor pidió: "
                + ", ".join(t.get("name", "") for t in exp["ordered_tests"][:5])
                + "."
            )
        else:
            hallazgos = rapido
        return que_es, rapido or None, hallazgos

    # -- laboratorio ----------------------------------------------------
    def _explicar_laboratorio(self, texto: str, uncertain: list) -> tuple:
        biomarcadores = []
        abnormal = []
        critico = False
        for linea in texto.splitlines():
            b = self._parsear_linea_lab(linea)
            if not b:
                continue
            biomarcadores.append(b["dict"])
            if b["flag"] in ("alto", "bajo"):
                abnormal.append(
                    {
                        "name": b["dict"]["name"],
                        "flag": b["flag"],
                        "severity": b["severidad"],
                    }
                )
            if b["critico"]:
                critico = True
            if b["incierto"]:
                uncertain.append(b["incierto"])

        # Solo los valores confirmados (con rango) cuentan para el resumen y
        # la urgencia. Los "por confirmar" van a uncertain_fields para que el
        # usuario los revise, pero nunca generan una falsa tranquilidad.
        confirmados = [b for b in biomarcadores if b["flag"] != "por confirmar"]
        hay_inciertos = len(biomarcadores) > len(confirmados)

        if anormales := [a["name"] for a in abnormal]:
            resumen = (
                f"Tu examen de laboratorio muestra {len(confirmados)} valores. "
                f"Fuera de su rango de referencia: {', '.join(anormales)}."
            )
        elif confirmados:
            resumen = (
                f"Tu examen de laboratorio muestra {len(confirmados)} valores "
                "y todos están dentro de su rango de referencia."
            )
        else:
            resumen = (
                "Detectamos que es un examen de laboratorio, pero no pudimos "
                "leer sus valores con confianza. Revisa el documento original "
                "abajo."
            )
            uncertain.append(
                {
                    "field": "biomarkers",
                    "reason": (
                        "No se encontraron líneas con formato de resultado de "
                        "laboratorio (nombre, valor y rango) que pudiéramos "
                        "leer con confianza."
                    ),
                }
            )

        return (
            {
                "summary_simple": resumen,
                "biomarkers": biomarcadores,
                "narrative_findings": [],
                "ordered_tests": [],
                "recommendations": [
                    "Lleva estos resultados a tu próxima cita médica.",
                    "Si tienes síntomas nuevos o que te preocupan, busca atención médica.",
                    "Guarda este documento junto a tus exámenes anteriores para comparar.",
                ],
                "questions_for_doctor": [
                    "¿Estos resultados cambian algo de mi tratamiento o mis controles?",
                    "¿Necesito repetir algún examen? ¿Cuándo?",
                    "¿Hay algo que deba vigilar en casa?",
                ],
            },
            {"abnormal": abnormal, "has_critical_flag": critico,
             # Sin valores confirmados no afirmamos "todo normal": la duda
             # amerita por precaución al menos agendar una cita.
             "has_uncertain_critical": hay_inciertos or not confirmados},
        )

    def _parsear_linea_lab(self, linea: str):
        """Intenta leer 'Nombre  valor  unidad  rango' de una línea."""
        partes = [p.strip() for p in re.split(r"\s{2,}|\t", linea.strip()) if p.strip()]
        if len(partes) < 2:
            return None
        nombre, valor_crudo = partes[0], partes[1]
        # Limpia marcas de laboratorio (!, *) para el cálculo, pero conserva
        # el valor crudo para mostrarlo (esas marcas indican criticidad).
        valor_num = _num(re.sub(r"[!*†#]+$", "", valor_crudo).strip())
        if valor_num is None:
            return None
        if len(nombre) < 2 or len(nombre) > 60:
            return None
        unidad = partes[2] if len(partes) > 2 and re.fullmatch(r"[a-zA-Zµμ°/%³²\-]*", partes[2] or "") else None
        rango_crudo = " ".join(partes[3:]) if len(partes) > 3 else None
        if unidad is None and len(partes) > 2:
            # La tercera columna no parecía unidad: puede ser el rango.
            rango_crudo = " ".join(partes[2:])

        valor = valor_num
        lo, hi = _parse_rango(rango_crudo or "")
        # Solo cuenta como marca crítica si * o ! van pegados a un número
        # (convención de laboratorio); los * de notas al pie no cuentan.
        critico = bool(re.search(r"\d\s*[!*]|cr[ií]tico|p[áa]nico", linea, re.IGNORECASE))

        if lo is None and hi is None:
            flag = "por confirmar"
            incierto = {
                "field": f"biomarkers[{nombre}].ref_range",
                "reason": "El documento no muestra un rango de referencia claro para este valor.",
            }
            severidad = "leve"
        elif (lo is not None and valor < lo) or (hi is not None and valor > hi):
            flag = "bajo" if (lo is not None and valor < lo) else "alto"
            incierto = None
            severidad = "critica" if critico else _severidad_por_desviacion(valor, lo, hi)
        else:
            flag = "normal"
            incierto = None
            severidad = "leve"

        plain, eff_high, eff_low = _conocimiento_para(nombre)
        efecto = None
        if flag == "alto":
            efecto = eff_high
        elif flag == "bajo":
            efecto = eff_low

        return {
            "dict": {
                "name": nombre,
                "value": valor_crudo,
                "unit": unidad,
                "ref_range": rango_crudo,
                "flag": flag,
                "severity": severidad,
                "plain_explanation": plain,
                "possible_health_effect": efecto,
            },
            "flag": flag,
            "incierto": incierto,
            "severidad": severidad,
            "critico": critico,
        }

    # -- pedido de examen -------------------------------------------------
    def _explicar_pedido(self, texto: str, uncertain: list) -> dict:
        pruebas = []
        for linea in texto.splitlines():
            s = linea.strip(" •-\t").strip()
            if not s or any(c.isdigit() for c in s):
                continue
            if len(s) < 4 or len(s) > 80:
                continue
            bajo = s.lower()
            if any(k in bajo for k in ["paciente", "fecha", "doctor", "firma", "laboratorio", "cédula", "teléfono", "dirección"]):
                continue
            if not re.search(r"[a-záéíóúñ]{3,}", bajo):
                continue
            pruebas.append(s)
        pruebas = list(dict.fromkeys(pruebas))[:12]

        ordenados = [
            {
                "name": p,
                "what_it_is": "Es un examen que tu doctor pidió.",
                "usual_objectives": [
                    "Los doctores suelen pedir este examen para revisar tu salud; "
                    "pregúntale a tu doctor para qué lo necesita en tu caso."
                ],
                "preparation": None,
                "question_for_doctor": "¿Para qué me pidió este examen y cómo debo prepararme?",
            }
            for p in pruebas
        ]
        if not ordenados:
            uncertain.append(
                {
                    "field": "ordered_tests",
                    "reason": "No se pudieron identificar los exámenes pedidos en el documento.",
                }
            )
            resumen = "Tu doctor te dejó un pedido de exámenes, pero no pudimos leer con claridad cuáles son."
        else:
            nombres = ", ".join(p["name"] for p in ordenados[:5])
            resto = f" y {len(ordenados) - 5} más" if len(ordenados) > 5 else ""
            resumen = f"Tu doctor pidió {len(ordenados)} exámenes: {nombres}{resto}. Aquí te explicamos qué es cada uno."
        return {
            "summary_simple": resumen,
            "biomarkers": [],
            "narrative_findings": [],
            "ordered_tests": ordenados,
            "recommendations": [
                "Pregúntale a tu doctor para qué pidió cada examen.",
                "Pregunta también cómo prepararte (ayuno, medicamentos, etc.).",
                "Guarda el pedido para llevarlo al laboratorio.",
            ],
            "questions_for_doctor": [
                "¿Para qué me pidió cada uno de estos exámenes?",
                "¿Necesito ayuno o alguna preparación especial?",
                "¿Cuándo debo hacérmelos y cuándo vemos los resultados?",
            ],
        }

    # -- narrativo / nota ---------------------------------------------------
    def _explicar_narrativo(self, texto: str, uncertain: list) -> dict:
        frases = [
            f.strip()
            for f in re.split(r"(?<=[.!?])\s+", texto)
            if len(f.strip()) > 25
        ]
        # Quita encabezados típicos
        frases = [
            f for f in frases
            if not re.match(r"(?i)^(paciente|fecha|doctor|informe|resultado)\s*:", f)
        ][:6]
        hallazgos = [
            {
                "finding": f,
                "plain_explanation": (
                    "Esto es lo que describe el informe con sus propias palabras. "
                    "Tu doctor puede explicarte qué significa en tu caso."
                ),
            }
            for f in frases
        ]
        if not hallazgos:
            uncertain.append(
                {
                    "field": "narrative_findings",
                    "reason": "No se pudieron identificar hallazgos claros en el informe.",
                }
            )
            resumen = "Es un informe narrativo, pero no pudimos extraer sus hallazgos con claridad."
        else:
            resumen = (
                "El informe describe sus hallazgos con palabras. Aquí te los "
                "mostramos uno por uno en lenguaje simple."
            )
        return {
            "summary_simple": resumen,
            "biomarkers": [],
            "narrative_findings": hallazgos,
            "ordered_tests": [],
            "recommendations": [
                "Lleva este informe a tu próxima cita para revisarlo con tu doctor.",
                "Anota las dudas que te surjan al leerlo y pregúntalas en tu cita.",
            ],
            "questions_for_doctor": [
                "¿Qué significan estos hallazgos en mi caso?",
                "¿Necesito algún examen o tratamiento adicional?",
                "¿Cuándo debo volver a controlarme?",
            ],
        }

    # -- señales de urgencia --------------------------------------------------
    def _senales(self, exp: dict, texto: str, sen_extra: dict) -> dict:
        abnormal = list(sen_extra.get("abnormal", []))
        critico = bool(sen_extra.get("has_critical_flag"))
        # Re-deriva severidad con el texto completo si hay marcas críticas.
        # * o ! solo cuentan pegados a un número (p. ej. "350 *"); los *
        # de notas al pie ("VALOR DE REFERENCIA *") no son valores críticos.
        if re.search(r"\d\s*[!*]", texto) or re.search(r"cr[ií]tico|p[áa]nico", texto, re.IGNORECASE):
            critico = True
        emergencia = any(k in texto.lower() for k in _LENGUAJE_EMERGENCIA)
        prioridad = None
        m = re.search(
            r"(?i)\b(prioritari[oa]|urgente|preferente|rutina)\b[^.\n]{0,60}",
            texto,
        )
        if m and exp.get("document_type") == "exam_order":
            prioridad = m.group(0).strip()
        return {
            "abnormal": abnormal,
            "has_critical_flag": critico,
            "has_uncertain_critical": bool(sen_extra.get("has_uncertain_critical")),
            "narrative_emergency_language": emergencia,
            "explicit_priority": prioridad,
            "doc_type": exp.get("document_type"),
        }


def get_llm_provider() -> LLMProvider:
    """Fábrica: Gemini si hay API key, si no Anthropic; si no, DemoExplainer.

    El orden refleja la llave que Daniel conectó para el prototipo.
    DemoExplainer es SOLO para desarrollo sin llave (modo demo).
    """
    if os.getenv("GEMINI_API_KEY", "").strip():
        log.info("Proveedor LLM: Gemini")
        return GeminiProvider()
    if os.getenv("ANTHROPIC_API_KEY", "").strip():
        log.info("Proveedor LLM: Anthropic")
        return AnthropicProvider()
    log.info("Sin API key de LLM: usando DemoExplainer (modo demo)")
    return DemoExplainer()
