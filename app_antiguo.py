from flask import Flask, request
from openai import OpenAI
from twilio.rest import Client as TwilioClient
import gspread
from google.oauth2.service_account import Credentials
import httpx, json, os, base64, threading
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

# --- OpenRouter ---
client = OpenAI(
    api_key=os.getenv("OPENROUTER_API_KEY"),
    base_url="https://openrouter.ai/api/v1"
)
MODEL_TEXTO    = "openrouter/free"
MODELOS_VISION = [
    "nvidia/nemotron-nano-12b-v2-vl:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
]

# --- Estado temporal por usuario ---
estados = {}

# -------------------------------------------------------
# CATEGORÍAS Y MÉTODOS VÁLIDOS
# -------------------------------------------------------

CATEGORIAS_VALIDAS = [
    "Alimentación", "Amigos", "Caridad", "Dios", "Familia",
    "Gastos hormiga", "Gastos innecesario", "Gustos", "Inversión en mí",
    "Pago servicio", "Perrihijos", "Salidas", "Suscripciones"
]

METODOS_VALIDOS = [
    "Efectivo", "Bcp / Yape", "BBVA / Plin", "Tarjeta de Regalo",
    "Interbank / Plin", "Tarjeta de crédito", "Ahorros", "Tarjeta de metro"
]

# -------------------------------------------------------
# GOOGLE SHEETS
# -------------------------------------------------------

def get_creds():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    if creds_json:
        creds_info = json.loads(creds_json)
        return Credentials.from_service_account_info(creds_info, scopes=scopes)
    return Credentials.from_service_account_file("credentials.json", scopes=scopes)

def get_sheet(nombre_hoja):
    gc = gspread.authorize(get_creds())
    sh = gc.open_by_key(os.getenv("SPREADSHEET_ID"))
    return sh.worksheet(nombre_hoja)

# -------------------------------------------------------
# PROMPTS
# -------------------------------------------------------

PROMPT_GASTO = """Eres un asistente que extrae datos de gastos personales en Perú.
Responde SOLO con JSON válido, sin texto extra, sin backticks, sin comentarios.

Estructura exacta:
{
  "descripcion": "texto corto del gasto",
  "cantidad": 50.00,
  "tipo_gasto_2": "Alimentación",
  "metodo": "Efectivo"
}

Valores válidos para metodo (elige el más apropiado):
Efectivo, Bcp / Yape, BBVA / Plin, Tarjeta de Regalo,
Interbank / Plin, Tarjeta de crédito, Ahorros, Tarjeta de metro

Categorías y su significado exacto (elige la más apropiada):
- Alimentación: comidas personales del día a día — desayuno, almuerzo, cena, jugos, etc.
- Amigos: gastos que hago EN un amigo — regalos, detalles, pagar algo por un amigo.
- Caridad: ayuda económica a personas en situación de calle u ONGs, no relacionado a la iglesia.
- Dios: ofrendas, diezmos, aportes a la iglesia o actividades religiosas.
- Familia: gastos que hago en mi familia — compras para la casa, regalos a familiares, compartir algo en casa. NO incluye salidas grupales con familia (eso es Salidas).
- Gastos hormiga: gastos pequeños del día a día que parecen insignificantes — café, golosinas, snacks, chucherías.
- Gastos innecesario: cosas que compré pero no debí gastar, compras impulsivas de las que me arrepiento.
- Gustos: algo que me compré porque se me antojó y lo disfruto, sin arrepentimiento — caprichos personales.
- Inversión en mí: gastos en mi bienestar, desarrollo personal, profesional o salud — cursos, libros, gimnasio, médico, psicólogo, educación, cuidado personal.
- Pago servicio: pagos de servicios básicos y del hogar — luz, agua, internet, celular, teléfono.
- Perrihijos: cualquier gasto relacionado a mis mascotas — comida, veterinario, medicamentos, accesorios, análisis.
- Salidas: gastos al salir — restaurantes, cines, paseos, viajes, entretenimiento fuera de casa, salidas con amigos o pareja.
- Suscripciones: pagos recurrentes de apps y plataformas digitales — Netflix, HBO Max, Spotify, Duolingo, Google One, ChatGPT, Claude, etc.

Reglas importantes:
- Si el gasto es para una mascota → siempre Perrihijos
- Si es una comida personal → siempre Alimentación
- Si es un servicio digital recurrente → siempre Suscripciones
- Si hay duda entre Gustos e Innecesario → usa Gustos por defecto
- Si hay duda entre Familia y Salidas → si salieron juntos usa Salidas, si compró algo para un familiar usa Familia

Si es imagen de boleta, extrae el total y el tipo de negocio."""

PROMPT_PASAJE = """Eres un asistente que extrae datos de pasajes/transporte en Perú.
Responde SOLO con JSON válido, sin texto extra, sin backticks, sin comentarios.

Estructura exacta:
{
  "cantidad": 3.00,
  "metodo": "Efectivo",
  "tipo": "Moto",
  "nota": "texto breve de una línea sobre el pasaje"
}

Valores válidos para tipo: Moto, Taxi, Micro, Metro

Valores válidos para metodo (elige EXACTAMENTE uno):
Efectivo, Bcp / Yape, BBVA / Plin, Tarjeta de Regalo,
Interbank / Plin, Tarjeta de crédito, Ahorros, Tarjeta de metro

Reglas:
- Si menciona "yape" o "bcp" → metodo: Bcp / Yape
- Si menciona "plin" o "bbva" → metodo: BBVA / Plin
- Si menciona "metro" → tipo: Metro, metodo: Tarjeta de metro
- Si no se menciona método → metodo: Efectivo
- El campo metodo NUNCA puede ser null ni None"""

PROMPT_CORRECCION = """El usuario quiere corregir datos de un gasto/pasaje.
Tienes los datos actuales y el mensaje del usuario indicando qué cambiar.
Responde SOLO con el JSON corregido completo, sin texto extra, sin backticks."""

PROMPT_CHAT = """Eres un asistente financiero personal amigable llamado FinBot.
Ayudas a registrar gastos y consultar saldos por WhatsApp.
El usuario te está enviando un mensaje casual o de cortesía.
Responde de forma corta, amigable y natural. No uses más de 2 líneas.
No inventes información financiera.
Si pregunta qué puedes hacer, explica brevemente:
- Registrar gastos y pasajes
- Consultar saldo y dinero disponible
- Ver gastos de hoy, ayer, esta semana o por fecha
- Consultar gastos por categoría"""

def get_prompt_intencion():
    hoy = datetime.now()
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_semana = dias_es[hoy.weekday()]
    return f"""Eres un clasificador de intenciones para un bot financiero personal.
Analiza el mensaje del usuario y responde SOLO con un JSON así:
{{
  "intencion": "REGISTRAR_GASTO",
  "categoria": null,
  "fecha": null
}}

Intenciones posibles:
- REGISTRAR_GASTO: quiere registrar un gasto o compra
- REGISTRAR_PASAJE: quiere registrar un pasaje o transporte
- CONSULTAR_SALDO: quiere saber cuánto dinero tiene disponible
- CONSULTAR_AHORROS: quiere saber cuánto tiene en ahorros
- CONSULTAR_PRESTAMO: quiere saber cuánto le deben o sus préstamos
- CONSULTAR_GASTO_MES: quiere saber cuánto ha gastado en total este mes
- CONSULTAR_GASTO_HOY: quiere saber cuánto ha gastado hoy
- CONSULTAR_GASTO_AYER: quiere saber cuánto gastó ayer
- CONSULTAR_GASTO_SEMANA: quiere saber cuánto gastó esta semana
- CONSULTAR_GASTO_FECHA: quiere saber cuánto gastó en una fecha específica
- CONSULTAR_GASTO_MAYOR: quiere saber cuál fue su gasto más grande
- CONSULTAR_GASTO_CATEGORIA: quiere saber cuánto gastó en una categoría específica
- CHAT_CASUAL: saludo, agradecimiento, confirmación, conversación general

Para CONSULTAR_GASTO_CATEGORIA: en "categoria" pon el nombre exacto de la categoría.
Para CONSULTAR_GASTO_FECHA: en "fecha" pon la fecha en formato DD/MM/YYYY.
Hoy es {hoy.strftime("%d/%m/%Y")} ({dia_semana}). Si el usuario dice "el lunes", "el martes", etc., calcula la fecha exacta de ese día en la semana actual o la anterior.
En otros casos pon null en "fecha" y "categoria".

Categorías válidas: Alimentación, Amigos, Caridad, Dios, Familia, Gastos hormiga, Gastos innecesario, Gustos, Inversión en mí, Pago servicio, Perrihijos, Salidas, Suscripciones"""

# -------------------------------------------------------
# LLAMADA A IA
# -------------------------------------------------------

def llamar_ia(prompt, texto, media_url=None, media_type=None):
    if media_url and media_type and "image" in media_type:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID")
        auth_token  = os.getenv("TWILIO_AUTH_TOKEN")
        from urllib.parse import urlparse
        parsed = urlparse(media_url)
        url_autenticada = f"{parsed.scheme}://{account_sid}:{auth_token}@{parsed.netloc}{parsed.path}"
        messages = [{
            "role": "user",
            "content": [
                {"type": "text", "text": f"{prompt}\n\nMensaje adicional: {texto}"},
                {"type": "image_url", "image_url": {"url": url_autenticada}}
            ]
        }]
        ultimo_error = None
        for modelo in MODELOS_VISION:
            try:
                print(f">>> Probando modelo visión: {modelo}")
                response = client.chat.completions.create(model=modelo, messages=messages)
                if not response.choices or response.choices[0].message.content is None:
                    raise ValueError("Respuesta vacía")
                texto_respuesta = response.choices[0].message.content.strip()
                texto_respuesta = texto_respuesta.replace("```json", "").replace("```", "").strip()
                return json.loads(texto_respuesta)
            except Exception as e:
                print(f">>> {modelo} falló: {str(e)[:80]}")
                ultimo_error = e
        raise ultimo_error
    else:
        messages = [
            {"role": "system", "content": prompt},
            {"role": "user", "content": texto}
        ]
        for intento in range(3):
            try:
                response = client.chat.completions.create(model=MODEL_TEXTO, messages=messages)
                if not response.choices or response.choices[0].message.content is None:
                    raise ValueError("Respuesta vacía")
                texto_respuesta = response.choices[0].message.content.strip()
                texto_respuesta = texto_respuesta.replace("```json", "").replace("```", "").strip()
                return json.loads(texto_respuesta)
            except (ValueError, json.JSONDecodeError) as e:
                if intento < 2:
                    print(f">>> Reintentando ({intento+1}/3)...")
                else:
                    raise

# -------------------------------------------------------
# NORMALIZAR DATOS
# -------------------------------------------------------

def normalizar_datos(datos):
    if "tipo_gasto_2" in datos:
        valor = datos["tipo_gasto_2"].strip().lower()
        for cat in CATEGORIAS_VALIDAS:
            if cat.lower() == valor:
                datos["tipo_gasto_2"] = cat
                break
    if "metodo" in datos:
        valor = datos["metodo"].strip().lower()
        for met in METODOS_VALIDOS:
            if met.lower() == valor:
                datos["metodo"] = met
                break
    return datos

# -------------------------------------------------------
# GUARDAR EN SHEETS
# -------------------------------------------------------

def guardar_gasto(datos):
    ws = get_sheet("Gastos")
    col_b = ws.col_values(2)
    ultima_fila_con_dato = 1
    for i, valor in enumerate(col_b):
        if valor.strip():
            ultima_fila_con_dato = i + 1
    nueva_fila = ultima_fila_con_dato + 1
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    ws.update([[
        datos.get("descripcion", ""),
        datos['cantidad'],
        fecha_hoy,
    ]], f"B{nueva_fila}:D{nueva_fila}")
    ws.update([[
        datos.get("tipo_gasto_2", "Otros"),
        datos.get("metodo", "Efectivo"),
    ]], f"F{nueva_fila}:G{nueva_fila}")

def guardar_pasaje(datos):
    ws = get_sheet("Pasajes")
    col_b = ws.col_values(2)
    ultima_fila_con_dato = 1
    for i, valor in enumerate(col_b):
        if valor.strip():
            ultima_fila_con_dato = i + 1
    nueva_fila = ultima_fila_con_dato + 1
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    ws.update([[
        datos['cantidad'],
        fecha_hoy,
        datos.get("metodo", "Efectivo"),
        datos.get("tipo", "Moto"),
        datos.get("nota", ""),
    ]], f"B{nueva_fila}:F{nueva_fila}")

# -------------------------------------------------------
# CONSULTA DE SALDO
# -------------------------------------------------------

def obtener_saldos():
    ws = get_sheet("Resumen")
    return {
        "efectivo":    ws.acell("E6").value,
        "bcp_yape":    ws.acell("F6").value,
        "bbva_plin":   ws.acell("E8").value,
        "metro":       ws.acell("F8").value,
        "interbank":   ws.acell("F10").value,
        "ahorros":     ws.acell("E14").value,
        "prestamos":   ws.acell("F12").value,
        "tarjeta":     ws.acell("E16").value,
        "total_pagos": ws.acell("F20").value,
        "gasto_mes":   ws.acell("C8").value,
    }

def respuesta_desglose(s):
    return (
        f"💰 *Tu dinero actual:*\n"
        f"💵 Efectivo: {s['efectivo']}\n"
        f"📱 BCP / Yape: {s['bcp_yape']}\n"
        f"🏦 BBVA / Plin: {s['bbva_plin']}\n"
        f"🚇 Tarjeta Metro: {s['metro']}\n"
        f"🏛️ Interbank: {s['interbank']}\n"
        f"💳 Tarjeta de crédito: {s['tarjeta']}\n"
        f"🐷 Ahorros: {s['ahorros']}\n\n"
        f"✅ *Total para pagos: {s['total_pagos']}*"
    )

# -------------------------------------------------------
# CONSULTAS DEL HISTORIAL
# -------------------------------------------------------

def obtener_filas_gastos():
    ws = get_sheet("Gastos")
    todos = ws.get_all_values()
    filas = []
    for fila in todos[1:]:
        if len(fila) < 7:
            continue
        descripcion = fila[1].strip()
        cantidad    = fila[2].strip()
        fecha       = fila[3].strip()
        categoria   = fila[5].strip()
        if not cantidad or not fecha or not descripcion:
            continue
        try:
            monto    = float(str(cantidad).replace("S/.", "").replace(",", ".").strip())
            fecha_dt = datetime.strptime(fecha, "%d/%m/%Y")
        except:
            continue
        filas.append({
            "descripcion": descripcion,
            "cantidad": monto,
            "fecha": fecha,
            "fecha_dt": fecha_dt,
            "categoria": categoria
        })
    return filas

def formatear_gastos(gastos, titulo):
    if not gastos:
        return f"No tienes gastos registrados {titulo}."
    total = sum(g["cantidad"] for g in gastos)
    detalle = "\n".join(
        f"  • {g['descripcion']} — S/.{g['cantidad']:.2f}"
        for g in gastos
    )
    return (
        f"📅 *Gastos {titulo}:*\n"
        f"{detalle}\n\n"
        f"💰 *Total: S/.{total:.2f}*"
    )

def consulta_gasto_hoy():
    hoy = datetime.now()
    filas = obtener_filas_gastos()
    gastos = [g for g in filas
              if g["fecha_dt"].day == hoy.day
              and g["fecha_dt"].month == hoy.month
              and g["fecha_dt"].year == hoy.year]
    return formatear_gastos(gastos, "de hoy")

def consulta_gasto_ayer():
    ayer = datetime.now() - timedelta(days=1)
    filas = obtener_filas_gastos()
    gastos = [g for g in filas
              if g["fecha_dt"].day == ayer.day
              and g["fecha_dt"].month == ayer.month
              and g["fecha_dt"].year == ayer.year]
    return formatear_gastos(gastos, f"de ayer ({ayer.strftime('%d/%m/%Y')})")

def consulta_gasto_semana():
    hoy = datetime.now()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_semana = inicio_semana.replace(hour=0, minute=0, second=0, microsecond=0)
    filas = obtener_filas_gastos()
    gastos = [g for g in filas if inicio_semana <= g["fecha_dt"] <= hoy]
    return formatear_gastos(gastos, "esta semana")

def consulta_gasto_fecha(fecha_str):
    try:
        fecha_dt = datetime.strptime(fecha_str, "%d/%m/%Y")
    except:
        return "No pude entender la fecha. Intenta con formato DD/MM/YYYY."
    filas = obtener_filas_gastos()
    gastos = [g for g in filas
              if g["fecha_dt"].day == fecha_dt.day
              and g["fecha_dt"].month == fecha_dt.month
              and g["fecha_dt"].year == fecha_dt.year]
    return formatear_gastos(gastos, f"del {fecha_str}")

def consulta_gasto_mes():
    hoy = datetime.now()
    filas = obtener_filas_gastos()
    gastos = [g for g in filas
              if g["fecha_dt"].month == hoy.month
              and g["fecha_dt"].year == hoy.year]
    return formatear_gastos(gastos, "este mes")

def consulta_gasto_por_categoria(categoria_buscada):
    hoy = datetime.now()
    filas = obtener_filas_gastos()
    gastos = [g for g in filas
              if categoria_buscada.lower() in g["categoria"].lower()
              and g["fecha_dt"].month == hoy.month
              and g["fecha_dt"].year == hoy.year]
    if not gastos:
        return f"No encontré gastos en *{categoria_buscada}* este mes."
    total = sum(g["cantidad"] for g in gastos)
    return (
        f"📂 *{categoria_buscada} — este mes:*\n"
        f"💰 Total: S/.{total:.2f}\n"
        f"📋 Registros: {len(gastos)}"
    )

def consulta_gasto_mayor():
    hoy = datetime.now()
    filas = obtener_filas_gastos()
    gastos = [g for g in filas
              if g["fecha_dt"].month == hoy.month
              and g["fecha_dt"].year == hoy.year]
    if not gastos:
        return "No tienes gastos registrados este mes."
    mayor = max(gastos, key=lambda g: g["cantidad"])
    return (
        f"🏆 *Gasto más grande este mes:*\n"
        f"📝 {mayor['descripcion']}\n"
        f"💰 S/.{mayor['cantidad']:.2f}\n"
        f"📂 {mayor['categoria']}\n"
        f"📅 {mayor['fecha']}"
    )

# -------------------------------------------------------
# RESUMEN PARA CONFIRMACIÓN
# -------------------------------------------------------

def resumen_gasto(datos):
    return (
        f"📋 *Confirma el gasto:*\n"
        f"📝 Descripción: {datos.get('descripcion')}\n"
        f"💰 Cantidad: S/.{datos['cantidad']:.2f}\n"
        f"📂 Categoría: {datos.get('tipo_gasto_2')}\n"
        f"💳 Método: {datos.get('metodo')}\n\n"
        f"Responde:\n✅ *sí* para registrar\n✏️ O dime qué corregir\n❌ *cancelar* para descartar"
    )

def resumen_pasaje(datos):
    return (
        f"📋 *Confirma el pasaje:*\n"
        f"🚗 Tipo: {datos.get('tipo')}\n"
        f"💰 Cantidad: S/.{datos['cantidad']:.2f}\n"
        f"💳 Método: {datos.get('metodo')}\n"
        f"📝 Nota: {datos.get('nota')}\n\n"
        f"Responde:\n✅ *sí* para registrar\n✏️ O dime qué corregir\n❌ *cancelar* para descartar"
    )

# -------------------------------------------------------
# ENVIAR MENSAJE VÍA TWILIO
# -------------------------------------------------------

def enviar_whatsapp(remitente, mensaje, tiempo_inicio=None):
    twilio_client = TwilioClient(
        os.getenv("TWILIO_ACCOUNT_SID"),
        os.getenv("TWILIO_AUTH_TOKEN")
    )
    twilio_client.messages.create(
        from_=os.getenv("TWILIO_SANDBOX_NUMBER"),
        to=remitente,
        body=mensaje
    )
    if tiempo_inicio:
        segundos = (datetime.now() - tiempo_inicio).total_seconds()
        print(f">>> Enviado en {segundos:.1f}s: {mensaje[:50]}...")
    else:
        print(f">>> Enviado: {mensaje[:60]}...")

# -------------------------------------------------------
# PROCESAMIENTO EN SEGUNDO PLANO
# -------------------------------------------------------

def procesar_mensaje(texto, media_url, media_type, remitente):
    texto_lower  = texto.lower().strip()
    tiempo_inicio = datetime.now()

    try:
        # ── Usuario con estado pendiente ──
        if remitente in estados:
            estado = estados[remitente]
            tipo   = estado.get("tipo")

            if tipo == "consulta_saldo":
                s = obtener_saldos()
                if texto_lower in ["1", "todo", "desglose", "todos", "ver todo"]:
                    enviar_whatsapp(remitente, respuesta_desglose(s), tiempo_inicio)
                    del estados[remitente]
                elif texto_lower in ["2", "total", "total disponible"]:
                    enviar_whatsapp(remitente, f"✅ *Total que puedes usar para pagos:*\n{s['total_pagos']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["efectivo"]):
                    enviar_whatsapp(remitente, f"💵 *Efectivo:* {s['efectivo']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["yape", "bcp"]):
                    enviar_whatsapp(remitente, f"📱 *BCP / Yape:* {s['bcp_yape']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["bbva", "plin"]):
                    enviar_whatsapp(remitente, f"🏦 *BBVA / Plin:* {s['bbva_plin']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["metro"]):
                    enviar_whatsapp(remitente, f"🚇 *Tarjeta Metro:* {s['metro']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["interbank"]):
                    enviar_whatsapp(remitente, f"🏛️ *Interbank:* {s['interbank']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["ahorros"]):
                    enviar_whatsapp(remitente, f"🐷 *Ahorros:* {s['ahorros']}", tiempo_inicio)
                    del estados[remitente]
                elif any(p in texto_lower for p in ["tarjeta", "crédito", "credito"]):
                    enviar_whatsapp(remitente, f"💳 *Tarjeta de crédito:* {s['tarjeta']}", tiempo_inicio)
                    del estados[remitente]
                elif texto_lower in ["cancelar", "cancel", "no"]:
                    del estados[remitente]
                    enviar_whatsapp(remitente, "❌ Consulta cancelada.", tiempo_inicio)
                else:
                    enviar_whatsapp(remitente,
                        "No entendí cuál quieres ver. Responde:\n"
                        "1️⃣ Todo el desglose\n"
                        "2️⃣ Solo el total\n\n"
                        "O escribe: efectivo, yape, bbva, metro, interbank, ahorros, tarjeta",
                        tiempo_inicio
                    )
                return

            datos = estado["datos"]

            if texto_lower in ["sí", "si", "s", "yes", "✅", "ok", "dale", "confirmar"]:
                if tipo == "gasto":
                    guardar_gasto(datos)
                    enviar_whatsapp(remitente,
                        f"✅ *Gasto registrado*\n"
                        f"📝 {datos.get('descripcion')}\n"
                        f"💰 S/.{datos['cantidad']:.2f}\n"
                        f"📂 {datos.get('tipo_gasto_2')}\n"
                        f"💳 {datos.get('metodo')}",
                        tiempo_inicio
                    )
                else:
                    guardar_pasaje(datos)
                    enviar_whatsapp(remitente,
                        f"🚌 *Pasaje registrado*\n"
                        f"🚗 {datos.get('tipo')}\n"
                        f"💰 S/.{datos['cantidad']:.2f}\n"
                        f"💳 {datos.get('metodo')}\n"
                        f"📝 {datos.get('nota')}",
                        tiempo_inicio
                    )
                del estados[remitente]

            elif texto_lower in ["cancelar", "no", "cancel"]:
                del estados[remitente]
                enviar_whatsapp(remitente, "❌ Registro cancelado.", tiempo_inicio)

            else:
                prompt_correccion = f"{PROMPT_CORRECCION}\n\nDatos actuales:\n{json.dumps(datos, ensure_ascii=False)}"
                datos_corregidos = llamar_ia(prompt_correccion, texto)
                datos_corregidos = normalizar_datos(datos_corregidos)
                estados[remitente]["datos"] = datos_corregidos
                if tipo == "gasto":
                    enviar_whatsapp(remitente, resumen_gasto(datos_corregidos), tiempo_inicio)
                else:
                    enviar_whatsapp(remitente, resumen_pasaje(datos_corregidos), tiempo_inicio)

        # ── Nuevo mensaje — detectar intención con IA ──
        else:
            print(">>> Detectando intención...")
            try:
                intencion_raw = llamar_ia(get_prompt_intencion(), texto)
                intencion = intencion_raw.get("intencion", "REGISTRAR_GASTO")
                categoria = intencion_raw.get("categoria")
                fecha     = intencion_raw.get("fecha")
                print(f">>> Intención: {intencion} | Categoría: {categoria} | Fecha: {fecha}")
            except Exception as e:
                print(f">>> Error detectando intención: {e} — asumiendo REGISTRAR_GASTO")
                intencion = "REGISTRAR_GASTO"
                categoria = None
                fecha     = None

            if intencion == "CONSULTAR_AHORROS":
                s = obtener_saldos()
                enviar_whatsapp(remitente, f"🐷 *Tus ahorros:* {s['ahorros']}", tiempo_inicio)

            elif intencion == "CONSULTAR_PRESTAMO":
                s = obtener_saldos()
                enviar_whatsapp(remitente, f"💸 *Préstamos / lo que te deben:* {s['prestamos']}", tiempo_inicio)

            elif intencion == "CONSULTAR_GASTO_MES":
                s = obtener_saldos()
                enviar_whatsapp(remitente, f"📊 *Tu gasto total este mes:* {s['gasto_mes']}", tiempo_inicio)

            elif intencion == "CONSULTAR_SALDO":
                estados[remitente] = {"tipo": "consulta_saldo"}
                enviar_whatsapp(remitente,
                    "💰 ¿Qué quieres consultar?\n\n"
                    "1️⃣ Ver *todo* el desglose\n"
                    "2️⃣ Solo el *total disponible*\n\n"
                    "O escribe directamente:\n"
                    "efectivo · yape · bbva · metro · interbank · ahorros · tarjeta",
                    tiempo_inicio
                )

            elif intencion == "CONSULTAR_GASTO_HOY":
                enviar_whatsapp(remitente, consulta_gasto_hoy(), tiempo_inicio)

            elif intencion == "CONSULTAR_GASTO_AYER":
                enviar_whatsapp(remitente, consulta_gasto_ayer(), tiempo_inicio)

            elif intencion == "CONSULTAR_GASTO_SEMANA":
                enviar_whatsapp(remitente, consulta_gasto_semana(), tiempo_inicio)

            elif intencion == "CONSULTAR_GASTO_FECHA":
                if fecha:
                    enviar_whatsapp(remitente, consulta_gasto_fecha(fecha), tiempo_inicio)
                else:
                    enviar_whatsapp(remitente,
                        "No entendí la fecha. Intenta con:\n"
                        "*'cuánto gasté el 15 de marzo'*\n"
                        "*'cuánto gasté el lunes'*",
                        tiempo_inicio
                    )

            elif intencion == "CONSULTAR_GASTO_MAYOR":
                enviar_whatsapp(remitente, consulta_gasto_mayor(), tiempo_inicio)

            elif intencion == "CONSULTAR_GASTO_CATEGORIA":
                if categoria:
                    enviar_whatsapp(remitente, consulta_gasto_por_categoria(categoria), tiempo_inicio)
                else:
                    lista = "\n".join(f"• {c}" for c in CATEGORIAS_VALIDAS)
                    enviar_whatsapp(remitente, f"¿De qué categoría quieres saber?\n\n{lista}", tiempo_inicio)

            elif intencion == "CHAT_CASUAL":
                messages = [
                    {"role": "system", "content": PROMPT_CHAT},
                    {"role": "user", "content": texto}
                ]
                response = client.chat.completions.create(model=MODEL_TEXTO, messages=messages)
                if response.choices and response.choices[0].message.content:
                    respuesta = response.choices[0].message.content.strip()
                else:
                    respuesta = "¡Hola! ¿En qué puedo ayudarte?"
                enviar_whatsapp(remitente, respuesta, tiempo_inicio)

            elif intencion == "REGISTRAR_PASAJE":
                print(">>> Llamando a IA (pasaje)...")
                datos = llamar_ia(PROMPT_PASAJE, texto, media_url, media_type)
                datos = normalizar_datos(datos)
                estados[remitente] = {"datos": datos, "tipo": "pasaje"}
                enviar_whatsapp(remitente, resumen_pasaje(datos), tiempo_inicio)

            else:
                print(">>> Llamando a IA (gasto)...")
                datos = llamar_ia(PROMPT_GASTO, texto, media_url, media_type)
                datos = normalizar_datos(datos)
                estados[remitente] = {"datos": datos, "tipo": "gasto"}
                enviar_whatsapp(remitente, resumen_gasto(datos), tiempo_inicio)

    except Exception as e:
        import traceback
        segundos = (datetime.now() - tiempo_inicio).total_seconds()
        print(f">>> ERROR tras {segundos:.1f}s: {traceback.format_exc()}")
        if remitente in estados:
            del estados[remitente]
        enviar_whatsapp(remitente, f"❌ Error al procesar. Intenta de nuevo.\n_{str(e)}_")

# -------------------------------------------------------
# WEBHOOK
# -------------------------------------------------------

@app.route("/webhook", methods=["POST"])
def webhook():
    texto      = request.values.get("Body", "").strip()
    media_url  = request.values.get("MediaUrl0")
    media_type = request.values.get("MediaContentType0", "")
    remitente  = request.values.get("From", "")

    print(f">>> [{remitente}] Mensaje: {texto}")

    threading.Thread(
        target=procesar_mensaje,
        args=(texto, media_url, media_type, remitente)
    ).start()

    return "", 204

# -------------------------------------------------------
# ARRANQUE
# -------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)