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

from app.i18n import t as _t
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
10. NOMBRE DEL EXAMEN ("nombre_examen"): 2 a 5 palabras en lenguaje cotidiano, como se lo dirías a un familiar que no sabe de medicina: usa el nombre común, no el técnico ("Examen de sangre completo", no "Hemograma"; "Examen de la tiroides", no "Perfil tiroideo"; "Radiografía del pecho", no "Radiografía de tórax"). Sé específico dentro de lo común ("Examen de sangre completo" mejor que solo "Examen de sangre" si el documento lo permite). Si el documento no permite identificarlo, usa la etiqueta de tipo.
11. VACUNAS: si el documento es un registro de vacunación ("vaccine_record"), lista en "vaccines" cada vacuna recibida tal como aparece: nombre, fecha de administración, dosis, lote, fabricante y proveedor SOLO si el documento los dice. Nunca inventes fechas, dosis ni vacunas, y nunca calcules próximas dosis ni recomiendes qué vacunas ponerse: el registro es solo lo ya recibido.

Devuelve SOLO un JSON válido (sin bloques de código, sin texto extra) con esta estructura exacta:
{
  "document_type": "lab_result" | "narrative_report" | "exam_order" | "doctor_note" | "vaccine_record",
  "type_label": "etiqueta en español, p. ej. 'Resultado de laboratorio'",
  "nombre_examen": "nombre corto del examen en palabras cotidianas, p. ej. 'Examen de sangre completo', 'Examen de la tiroides'",
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
  "vaccines": [
    {"name": "nombre de la vacuna tal como aparece en el documento",
     "date_administered": "fecha de administración (AAAA-MM-DD) o null si no aparece",
     "dose": "dosis tal como aparece (p. ej. '1', '2', 'refuerzo') o null",
     "lot": "lote o null", "manufacturer": "fabricante o null",
     "provider": "quién la aplicó / clínica o null"}
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


# English mirror of SYSTEM_PROMPT (used when the user navigates in English).
# JSON field names stay the same (contract); all content is generated in
# simple, everyday English.
SYSTEM_PROMPT_EN = """You are MiFicha, an assistant that explains medical documents in simple, everyday English, the way you'd explain them to a family member. Your audience doesn't know medicine.

HARD RULES (unbreakable):
1. REFERENCE RANGES ONLY FROM THE DOCUMENT. If a value has no range in the document, leave ref_range empty. NEVER invent ranges, "typical normal" values, or numbers from memory.
2. NEVER diagnose. Don't prescribe, don't give doses, don't give risk scores, don't indicate treatments or which medication to take or stop.
3. The objectives of ORDERED exams are expressed as possibilities ("it's usually used for…", "doctors order it when they want to check…"), NEVER as the confirmed reason the doctor ordered it.
4. Simple, everyday, kind English. Explain each medical term the first time it appears.
5. Be conservative with urgency: when in doubt, "agendar_cita" with reason "out of caution". Only use "buscar_ayuda_ya" if there is an explicit critical flag or emergency language in the document.
6. CLINICAL PRIORITY: for each out-of-range biomarker, indicate how important it is to look into it soon (alta/media/baja) considering the real value, how far it is from the range, and the clinical weight of the exam (e.g. electrolytes like potassium/sodium and heart markers weigh more than lipids at equal deviation). It's only for ordering the list, NOT a diagnosis or medical evaluation.
7. LAB: if the document names the lab or clinic that issued it, return it in "laboratorio" exactly as it appears; if it doesn't appear, null. Never invent it.
8. DOCUMENT DATE: it's the date the doctor or lab completed/issued the document (report date, sample date, or issue date, in the header or footer), NOT today's date. Format YYYY-MM-DD. If it doesn't appear in the document, null: never invent it or use the current date.
9. SECTIONED SUMMARY: besides "summary_simple", return the summary split into three short parts, each in simple language without jargon: "resumen_que_es" (what this exam/document is and what it's used for), "resumen_rapido" (the most important thing about the result in 1-2 sentences), and "resumen_hallazgos" (what came out as unexpected and what it means, in short sentences; don't repeat the list of values one by one since they go in another section).
10. EXAM NAME ("nombre_examen"): 2 to 5 words in everyday language, as you'd tell a family member who doesn't know medicine: use the common name, not the technical one ("Complete blood test", not "Hemogram"; "Thyroid test", not "Thyroid profile"; "Chest X-ray", not "Thorax X-ray"). Be specific within what's common ("Complete blood test" better than just "Blood test" if the document allows it). If the document doesn't let you identify it, use the type label.
11. VACCINES: if the document is a vaccination record ("vaccine_record"), list in "vaccines" each vaccine received exactly as it appears: name, date administered, dose, lot, manufacturer, and provider ONLY if the document states them. Never invent dates, doses, or vaccines, and never compute next doses or recommend which vaccines to get: the registry is only what was already received.

Return ONLY valid JSON (no code blocks, no extra text) with this exact structure:
{
  "document_type": "lab_result" | "narrative_report" | "exam_order" | "doctor_note" | "vaccine_record",
  "type_label": "label in English, e.g. 'Lab result'",
  "nombre_examen": "short exam name in everyday words, e.g. 'Complete blood test', 'Thyroid test'",
  "laboratorio": "lab or clinic name as it appears in the document, or null if absent",
  "fecha_documento": "date the doctor or lab completed/issued the document (YYYY-MM-DD format), or null if absent",
  "summary_simple": "2-4 sentence summary in simple language",
  "resumen_que_es": "1-2 sentences: what this exam or document is and what it's used for",
  "resumen_rapido": "1-2 sentences: the most important thing about the result, in simple language",
  "resumen_hallazgos": "main findings: what came out as unexpected and what it means, in short sentences",
  "biomarkers": [
    {"name": "name", "value": "value as text", "unit": "unit or null",
     "ref_range": "EXACT range from the document or null",
     "flag": "alto" | "bajo" | "normal" | "por confirmar",
     "prioridad_clinica": "alta" | "media" | "baja",
     "motivo_prioridad": "short sentence in simple language: why this value deserves that priority, or null",
     "plain_explanation": "what it is, in simple language",
     "possible_health_effect": "possible health effect, without diagnosing, or null"}
  ],
  "narrative_findings": [
    {"finding": "finding as the report states it",
     "plain_explanation": "what it means in simple language"}
  ],
  "ordered_tests": [
    {"name": "exam name",
     "what_it_is": "what it is, in simple language",
     "usual_objectives": ["what it's usually used for…", "…"],
     "preparation": "preparation if the document states it, or null",
     "question_for_doctor": "one useful question to ask the doctor"}
  ],
  "vaccines": [
    {"name": "vaccine name as it appears in the document",
     "date_administered": "date administered (YYYY-MM-DD) or null if absent",
     "dose": "dose as stated (e.g. '1', '2', 'booster') or null",
     "lot": "lot or null", "manufacturer": "manufacturer or null",
     "provider": "who gave it / clinic or null"}
  ],
  "urgency": {"level": "no_urgente" | "agendar_cita" | "hablar_hoy_con_profesional" | "buscar_ayuda_ya",
              "label": "short label in English", "action": "what to do",
              "timeframe": "when", "reason": "why"},
  "recommendations": ["general, safe recommendations"],
  "questions_for_doctor": ["useful questions for the appointment"],
  "uncertain_fields": [{"field": "field name", "reason": "why it's uncertain"}],
  "disclaimer": "This explanation is only informational and is not a diagnosis. Always consult a health professional.",
  "demo_mode": false
}

If a section doesn't apply to the document, use an empty list []. If a datum wasn't clear, put it in uncertain_fields instead of guessing."""


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
        lang = extracted.get("lang", "en")
        if lang not in ("es", "en"):
            lang = "en"
        if lang == "en":
            pista = (
                f"\nThe user indicated the document type is: {hint}."
                if hint
                else ""
            )
            prompt = SYSTEM_PROMPT_EN
            user_msg = (
                "Explain this medical document in simple English. "
                f"Return only the JSON.{pista}\n\n"
                f"DOCUMENT TEXT:\n{texto}"
            )
        else:
            pista = (
                f"\nEl usuario indicó que el tipo de documento es: {hint}."
                if hint
                else ""
            )
            prompt = SYSTEM_PROMPT
            user_msg = (
                "Explica este documento médico en español simple. "
                f"Devuelve solo el JSON.{pista}\n\n"
                f"TEXTO DEL DOCUMENTO:\n{texto}"
            )
        client = anthropic.Anthropic(api_key=self.api_key)
        resp = client.messages.create(
            model=self.model,
            max_tokens=4000,
            system=prompt,
            messages=[
                {
                    "role": "user",
                    "content": user_msg,
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
        lang = extracted.get("lang", "en")
        if lang not in ("es", "en"):
            lang = "en"
        if lang == "en":
            pista = (
                f"\nThe user indicated the document type is: {hint}."
                if hint
                else ""
            )
            prompt = SYSTEM_PROMPT_EN
            user_msg = (
                "Explain this medical document in simple English. "
                "Return only the JSON."
                f"{pista}\n\nDOCUMENT TEXT:\n{texto}"
            )
        else:
            pista = (
                f"\nEl usuario indicó que el tipo de documento es: {hint}."
                if hint
                else ""
            )
            prompt = SYSTEM_PROMPT
            user_msg = (
                "Explica este documento médico en español "
                "simple. Devuelve solo el JSON."
                f"{pista}\n\nTEXTO DEL DOCUMENTO:\n{texto}"
            )
        cuerpo = {
            "systemInstruction": {"parts": [{"text": prompt}]},
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": user_msg
                        }
                    ],
                }
            ],
            "generationConfig": {
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
    "vaccine_record": "Registro de vacunas",
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

# English mirror of _CONOCIMIENTO (demo mode only). Keys stay the same
# (they match Spanish biomarker names as printed on lab PDFs).
_CONOCIMIENTO_EN = [
    ("glucosa", "The sugar in your blood that your body uses for energy.",
     "Having high sugar for a long time can damage your eyes, kidneys, nerves, and heart.",
     "Very low sugar can cause dizziness, cold sweat, tremors, and confusion."),
    ("glicosilada", "Your average blood sugar over the last 2 to 3 months.",
     "A high value means sugar has been high for several weeks.",
     "A very low value can mean frequent sugar dips."),
    ("colesterol total", "A fat that circulates in your blood; your body needs a certain amount of it.",
     "Having it high for a long time is linked to arteries slowly getting clogged.",
     "A low value usually isn't a concern on its own."),
    ("hdl", "The so-called \u00abgood cholesterol\u00bb: it helps clean your arteries.",
     "Having it high is considered favorable.",
     "Having it low is linked to a higher risk of heart problems down the road."),
    ("ldl", "The so-called \u00abbad cholesterol\u00bb: in excess it can stick to artery walls.",
     "Having it high for a long time is linked to clogged arteries.",
     "A low value usually isn't a concern on its own."),
    ("triglic", "Another fat in the blood, which rises with excess sugar and starch.",
     "Very high values are linked to heart and pancreas problems.",
     "A low value usually isn't a concern on its own."),
    ("hemoglobina", "The part of your red blood cells that carries oxygen around your body.",
     "Having it high can make your blood thicker.",
     "Having it low (anemia) can cause tiredness, paleness, and shortness of breath."),
    ("hematocrito", "The share of your blood that is red blood cells.",
     "A high value can mean thicker blood or dehydration.",
     "A low value can come with anemia and cause tiredness."),
    ("leucocitos", "Your white blood cells: the cells that defend you from infections.",
     "They usually rise when there's an infection or inflammation.",
     "Having them low can leave you with fewer defenses against infections."),
    ("gl\u00f3bulos blancos", "The cells that defend you from infections.",
     "They usually rise when there's an infection or inflammation.",
     "Having them low can leave you with fewer defenses against infections."),
    ("plaquetas", "The cells that help your blood clot when you cut yourself.",
     "Having them very high is sometimes linked to a higher risk of clots.",
     "Having them low can make you bleed or bruise easily."),
    ("creatinina", "A waste product your kidneys filter; it shows how well they're working.",
     "Having it high can mean your kidneys aren't filtering well.",
     "A low value usually isn't a concern on its own."),
    ("urea", "Another waste product your kidneys remove.",
     "Having it high can mean your kidneys need a check or that you're dehydrated.",
     "A low value usually isn't a concern on its own."),
    ("tsh", "The hormone that tells your thyroid how hard to work.",
     "Having it high is usually seen when the thyroid is underactive.",
     "Having it low is usually seen when the thyroid is overactive."),
    ("t4 libre", "The main hormone your thyroid makes.",
     "Having it high usually goes with an overactive thyroid.",
     "Having it low usually goes with an underactive thyroid."),
    ("\u00e1cido \u00farico", "A waste product that in excess can form crystals in your joints.",
     "Having it high is linked to gout, which causes pain and swelling in joints.",
     "A low value usually isn't a concern on its own."),
    ("alt", "A substance that rises when the liver is irritated or overworked.",
     "Having it high can mean your liver needs a check.",
     "A low value usually isn't a concern on its own."),
    ("ast", "A substance similar to ALT, related to the liver.",
     "Having it high can mean your liver needs a check.",
     "A low value usually isn't a concern on its own."),
    ("prote\u00edna c reactiva", "A marker in the blood that there's inflammation in the body.",
     "It usually rises when there's an infection or inflammation.",
     "A low value is what's expected."),
    ("vitamina d", "Helps your bones absorb calcium.",
     "A very high value is rare and worth mentioning to your doctor.",
     "Having it low is linked to weaker bones and tiredness."),
    ("ferritina", "Your body's iron reserve.",
     "Having it very high is worth mentioning to your doctor.",
     "Having it low can cause tiredness and anemia."),
    ("hierro", "A mineral that helps carry oxygen in the blood.",
     "Having it very high is worth mentioning to your doctor.",
     "Having it low can cause tiredness and anemia."),
    ("sodio", "A mineral salt that helps keep your body's fluid balance.",
     "Having it high can be related to dehydration.",
     "Having it low can cause confusion and weakness."),
    ("potasio", "A mineral important for your muscles and heart to work well.",
     "Having it high or very low can affect your heart rhythm: worth checking with your doctor soon.",
     "Having it low can cause muscle weakness and cramps."),
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
        "vacuna": "vaccine_record",
        "vaccine": "vaccine_record",
    }
    if hint and str(hint).strip().lower() in mapa_hint:
        return mapa_hint[str(hint).strip().lower()]
    t = texto.lower()
    puntajes = {
        "vaccine_record": sum(k in t for k in ["registro de vacun", "vaccination record", "immunization record", "cartilla de vacun", "carnet de vacun", "vacuna aplicada", "fecha de vacun", "vaccines received", "inmunización", "inmunizacion"]),
        "exam_order": sum(k in t for k in ["orden de", "solicitud de examen", "se solicita", "pedido de", "solicito"]),
        "narrative_report": sum(k in t for k in ["informe", "hallazgo", "impresión", "pet-ct", "tomografía", "resonancia", "ecografía", "radiografía"]),
        "doctor_note": sum(k in t for k in ["nota médica", "evolución", "motivo de consulta", "examen físico", "plan:"]),
        "lab_result": sum(k in t for k in ["laboratorio", "hemograma", "biometria", "hematolog", "bioquímica", "bioquimica", "rango de referencia", "valor de referencia", "valores de referencia", "glóbulos", "globulos", "plaquetas", "hemoglobina", "colesterol", "triglicéridos", "glucosa", "creatinina", "uroanálisis", "orina", "tiroides", "química sanguínea"]),
    }
    mejor = max(puntajes, key=puntajes.get)
    return mejor if puntajes[mejor] > 0 else "lab_result"


def _conocimiento_para(nombre: str, lang: str = "es"):
    n = nombre.lower()
    base = _CONOCIMIENTO_EN if lang == "en" else _CONOCIMIENTO
    for key, plain, high, low in base:
        if key in n:
            return plain, high, low
    if lang == "en":
        generico = (
            "If it's out of range, your doctor can explain what it means in your case."
        )
        return (
            "It's a measure that appears in your exam.",
            generico,
            generico,
        )
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


def _tipo_label(tipo: str, lang: str = "es") -> str:
    return {
        "lab_result": _t("demo.type_lab", lang=lang),
        "narrative_report": _t("demo.type_narrative", lang=lang),
        "exam_order": _t("demo.type_order", lang=lang),
        "doctor_note": _t("demo.type_note", lang=lang),
        "vaccine_record": _t("demo.type_vaccine", lang=lang),
    }.get(tipo, _t("demo.type_unknown", lang=lang))


class DemoExplainer(LLMProvider):
    """Explicador determinista basado en reglas. SOLO cuando no hay API key."""

    is_demo = True

    def explain(self, extracted: dict) -> dict:
        texto = extracted.get("full_text") or ""
        hint = extracted.get("doc_type_hint")
        lang = extracted.get("lang", "en")
        if lang not in ("es", "en"):
            lang = "en"
        tipo = _detectar_tipo(texto, hint)

        uncertain = []
        for n in extracted.get("scanned_pages", []):
            uncertain.append(
                {
                    "field": f"pages[{n}].text",
                    "reason": _t("demo.scanned", lang=lang, n=n),
                }
            )
        for n in extracted.get("ocr_pages", []):
            uncertain.append(
                {
                    "field": f"pages[{n}].text",
                    "reason": _t("demo.ocr_page", lang=lang, n=n),
                }
            )

        if tipo == "lab_result":
            cuerpo, sen_lab = self._explicar_laboratorio(texto, uncertain, lang)
            sen_extra = sen_lab
        elif tipo == "vaccine_record":
            cuerpo = self._explicar_vacunas(texto, uncertain, lang)
            sen_extra = {"abnormal": [], "has_critical_flag": False}
        elif tipo == "exam_order":
            cuerpo = self._explicar_pedido(texto, uncertain, lang)
            sen_extra = {"abnormal": [], "has_critical_flag": False}
        elif tipo in ("narrative_report", "doctor_note"):
            cuerpo = self._explicar_narrativo(texto, uncertain, lang)
            sen_extra = {"abnormal": [], "has_critical_flag": False}
        else:
            cuerpo, sen_lab = self._explicar_laboratorio(texto, uncertain, lang)
            sen_extra = sen_lab

        exp = {
            "document_type": tipo,
            "type_label": _tipo_label(tipo, lang),
            "nombre_examen": None,
            "laboratorio": None,
            "fecha_documento": None,
            **cuerpo,
            "uncertain_fields": uncertain,
            "disclaimer": _t("demo.disclaimer", lang=lang),
            "demo_mode": True,
        }
        # Urgencia calculada con las mismas reglas conservadoras del sistema.
        exp["urgency"] = decide_urgency(self._senales(exp, texto, sen_extra))
        que_es, rapido, hallazgos = self._resumen_secciones(tipo, exp, lang)
        exp["resumen_que_es"] = que_es
        exp["resumen_rapido"] = rapido
        exp["resumen_hallazgos"] = hallazgos
        return exp

    @staticmethod
    def _resumen_secciones(tipo: str, exp: dict, lang: str = "es") -> tuple:
        """Resumen dividido en tres partes cortas (modo demo)."""
        que_es = {
            "lab_result": _t("demo.what_lab", lang=lang),
            "narrative_report": _t("demo.what_narrative", lang=lang),
            "exam_order": _t("demo.what_order", lang=lang),
            "doctor_note": _t("demo.what_note", lang=lang),
            "vaccine_record": _t("demo.what_vaccine", lang=lang),
        }.get(tipo, _t("demo.what_unknown", lang=lang))
        rapido = exp.get("summary_simple") or ""
        if tipo == "lab_result":
            anormales = [
                b["name"] for b in exp.get("biomarkers", [])
                if b.get("flag") in ("alto", "bajo")
            ]
            hallazgos = (
                _t("demo.some_out", lang=lang, nombres=", ".join(anormales))
                if anormales else
                _t("demo.all_clear", lang=lang)
            )
        elif exp.get("vaccines"):
            hallazgos = _t("demo.vaccine_summary", lang=lang,
                           n=len(exp["vaccines"]),
                           nombres=", ".join(
                               v.get("name", "") for v in exp["vaccines"][:5]))
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
    def _explicar_laboratorio(self, texto: str, uncertain: list,
                              lang: str = "es") -> tuple:
        biomarcadores = []
        abnormal = []
        critico = False
        for linea in texto.splitlines():
            b = self._parsear_linea_lab(linea, lang)
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
            resumen = _t("demo.lab_some", lang=lang, n=len(confirmados),
                         nombres=", ".join(anormales))
        elif confirmados:
            resumen = _t("demo.lab_all", lang=lang, n=len(confirmados))
        else:
            resumen = _t("demo.lab_none", lang=lang)
            uncertain.append(
                {
                    "field": "biomarkers",
                    "reason": _t("demo.lab_no_confidence", lang=lang),
                }
            )

        return (
            {
                "summary_simple": resumen,
                "biomarkers": biomarcadores,
                "narrative_findings": [],
                "ordered_tests": [],
                "recommendations": [
                    _t("demo.lab_rec1", lang=lang),
                    _t("demo.lab_rec2", lang=lang),
                    _t("demo.lab_rec3", lang=lang),
                ],
                "questions_for_doctor": [
                    _t("demo.lab_q1", lang=lang),
                    _t("demo.lab_q2", lang=lang),
                    _t("demo.lab_q3", lang=lang),
                ],
            },
            {"abnormal": abnormal, "has_critical_flag": critico,
             # Sin valores confirmados no afirmamos "todo normal": la duda
             # amerita por precaución al menos agendar una cita.
             "has_uncertain_critical": hay_inciertos or not confirmados},
        )

    def _parsear_linea_lab(self, linea: str, lang: str = "es"):
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
                "reason": _t("demo.norange", lang=lang),
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

        plain, eff_high, eff_low = _conocimiento_para(nombre, lang)
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

    # -- registro de vacunas --------------------------------------------
    # Conservative demo parser: only lines that name a known vaccine AND
    # carry a date become registry entries. Anything else is left out
    # rather than guessed; the LLM path extracts the full record.
    _VACUNAS_CONOCIDAS = (
        "influenza", "flu", "covid", "sars", "tetanus", "tétanos",
        "tetano", "tdap", "dtap", "mmr", "sarampion", "sarampión",
        "rubeola", "rubéola", "hepatitis", "hpv", "varicela",
        "chickenpox", "neumococo", "pneumococcal", "mening", "rotavirus",
        "polio", "bcg", "fiebre amarilla", "yellow fever", "difteria",
        "diphtheria", "pertussis", "tos ferina", "herpes zoster",
        "shingles", "rsv", "vrs",
    )
    _FABRICANTES = ("pfizer", "moderna", "astrazeneca", "janssen",
                    "sinovac", "novavax", "gsk", "sanofi", "merck")

    def _explicar_vacunas(self, texto: str, uncertain: list,
                          lang: str = "es") -> dict:
        from app.services.vaccines import parse_vaccine_date
        vacunas = []
        vistos = set()
        for linea in texto.splitlines():
            s = " ".join(linea.strip().split())
            if not s or len(s) > 200:
                continue
            bajo = s.lower()
            if any(k in bajo for k in ["paciente", "patient", "nacimiento",
                                       "birth", "nombre:", "name:",
                                       "cédula", "teléfono", "dirección",
                                       "firma", "signature"]):
                continue
            if not any(k in bajo for k in self._VACUNAS_CONOCIDAS):
                continue
            m_fecha = re.search(
                r"(\d{4}-\d{2}-\d{2}|\d{1,2}[/-]\d{1,2}[/-]\d{4}|"
                r"\d{1,2}\s+(?:de\s+)?[A-Za-záéíóúñ]+\s+(?:de\s+)?\d{4})", s)
            if not m_fecha:
                continue
            fecha = parse_vaccine_date(m_fecha.group(0)) or m_fecha.group(0)
            # Name: text before the date, cleaned of separators and
            # trailing dose/lot markers; fall back to the known name.
            nombre = re.split(r"[,;|]", s[:m_fecha.start()])[-1].strip(" •-\t")
            nombre = re.sub(r"\s+", " ", nombre).strip()
            if len(nombre) < 3 or len(nombre) > 80:
                nombre = next(k for k in self._VACUNAS_CONOCIDAS if k in bajo)
            m_dosis = re.search(
                r"(?i)\b(?:dosis|dose)\s*(\d+|refuerzo|booster)\b", s)
            if not m_dosis:
                m_dosis = re.search(r"\b(\d+)(?:a|ª|st|nd|rd|th)\s+"
                                    r"(?:dosis|dose)\b", s, re.IGNORECASE)
            m_lote = re.search(r"(?i)\b(?:lote|lot)\s*[:#]?\s*([A-Za-z0-9-]+)",
                               s)
            fabricante = next((f for f in self._FABRICANTES if f in bajo),
                              None)
            clave = (nombre.lower(), fecha)
            if clave in vistos:
                continue
            vistos.add(clave)
            vacunas.append({
                "name": nombre,
                "date_administered": fecha,
                "dose": m_dosis.group(1) if m_dosis else None,
                "lot": m_lote.group(1) if m_lote else None,
                "manufacturer": fabricante,
                "provider": None,
            })
        if not vacunas:
            uncertain.append({
                "field": "vaccines",
                "reason": _t("demo.vaccine_none", lang=lang),
            })
            resumen = _t("demo.vaccine_summary_none", lang=lang)
        else:
            nombres = ", ".join(v["name"] for v in vacunas[:5])
            resto = _t("demo.order_more", lang=lang,
                       n=len(vacunas) - 5) if len(vacunas) > 5 else ""
            resumen = _t("demo.vaccine_summary", lang=lang, n=len(vacunas),
                         nombres=nombres) + resto
        return {
            "summary_simple": resumen,
            "biomarkers": [],
            "narrative_findings": [],
            "ordered_tests": [],
            "vaccines": vacunas,
            "recommendations": [
                _t("demo.vaccine_rec1", lang=lang),
                _t("demo.vaccine_rec2", lang=lang),
            ],
            "questions_for_doctor": [
                _t("demo.vaccine_q1", lang=lang),
            ],
        }

    # -- pedido de examen -------------------------------------------------
    def _explicar_pedido(self, texto: str, uncertain: list,
                         lang: str = "es") -> dict:
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
                "what_it_is": _t("demo.order_what", lang=lang),
                "usual_objectives": [
                    _t("demo.order_obj", lang=lang)
                ],
                "preparation": None,
                "question_for_doctor": _t("demo.order_q", lang=lang),
            }
            for p in pruebas
        ]
        if not ordenados:
            uncertain.append(
                {
                    "field": "ordered_tests",
                    "reason": _t("demo.order_none", lang=lang),
                }
            )
            resumen = _t("demo.order_summary_none", lang=lang)
        else:
            nombres = ", ".join(p["name"] for p in ordenados[:5])
            resto = _t("demo.order_more", lang=lang, n=len(ordenados) - 5) \
                if len(ordenados) > 5 else ""
            resumen = _t("demo.order_summary", lang=lang, n=len(ordenados),
                         nombres=nombres, resto=resto)
        return {
            "summary_simple": resumen,
            "biomarkers": [],
            "narrative_findings": [],
            "ordered_tests": ordenados,
            "recommendations": [
                _t("demo.order_rec1", lang=lang),
                _t("demo.order_rec2", lang=lang),
                _t("demo.order_rec3", lang=lang),
            ],
            "questions_for_doctor": [
                _t("demo.order_q1", lang=lang),
                _t("demo.order_q2", lang=lang),
                _t("demo.order_q3", lang=lang),
            ],
        }

    # -- narrativo / nota ---------------------------------------------------
    def _explicar_narrativo(self, texto: str, uncertain: list,
                            lang: str = "es") -> dict:
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
                "plain_explanation": _t("demo.narr_plain", lang=lang),
            }
            for f in frases
        ]
        if not hallazgos:
            uncertain.append(
                {
                    "field": "narrative_findings",
                    "reason": _t("demo.narr_none", lang=lang),
                }
            )
            resumen = _t("demo.narr_summary_none", lang=lang)
        else:
            resumen = _t("demo.narr_summary", lang=lang)
        return {
            "summary_simple": resumen,
            "biomarkers": [],
            "narrative_findings": hallazgos,
            "ordered_tests": [],
            "recommendations": [
                _t("demo.narr_rec1", lang=lang),
                _t("demo.narr_rec2", lang=lang),
            ],
            "questions_for_doctor": [
                _t("demo.narr_q1", lang=lang),
                _t("demo.narr_q2", lang=lang),
                _t("demo.narr_q3", lang=lang),
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
