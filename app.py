from flask import Flask, request
from openai import OpenAI
from twilio.rest import Client as TwilioClient
import gspread
from google.oauth2.service_account import Credentials
import httpx, json, os, base64, threading
from datetime import datetime, timedelta
from dotenv import load_dotenv
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
import os.path


load_dotenv()

app = Flask(__name__)

# -------------------------------------------------------
# CARGAR CONFIGURACIÓN EXTERNA (Privada)
# -------------------------------------------------------
try:
    with open('config.json', 'r', encoding='utf-8') as f:
        config_data = json.load(f)
except FileNotFoundError:
    print("ADVERTENCIA: No se encontró config.json. El bot usará configuraciones por defecto vacías.")
    config_data = {"categorias": [], "metodos": [], "reglas_categorias": "", "celdas_saldo": {}}

CATEGORIAS_VALIDAS = config_data.get("categorias", [])
METODOS_VALIDOS = config_data.get("metodos", [])
REGLAS_CATEGORIAS = config_data.get("reglas_categorias", "")
CELDAS_SALDO = config_data.get("celdas_saldo", {})

# -------------------------------------------------------
# CONFIGURACIÓN DE IA
# -------------------------------------------------------
client = OpenAI(
    api_key=os.getenv("GROQ_API_KEY"),
    base_url="https://api.groq.com/openai/v1"
)

# Modelo principal para texto e intenciones
MODEL_TEXTO = "llama-3.1-8b-instant"

# Modelo para leer fotos de recibos/boletas
MODELOS_VISION = ["llama-3.2-11b-vision-preview"]

# --- Estado temporal por usuario ---
estados = {}

# -------------------------------------------------------
# GOOGLE SHEETS
# -------------------------------------------------------

def get_creds():
    scopes = ["https://www.googleapis.com/auth/spreadsheets"]
    creds = None
    
    # Busca si ya tienes un token guardado de una sesión anterior
    if os.path.exists('token.json'):
        creds = Credentials.from_authorized_user_file('token.json', scopes)
    
    # Si no hay credenciales válidas, inicia el flujo de inicio de sesión
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # Aquí es donde se abrirá tu navegador para loguearte
            flow = InstalledAppFlow.from_client_secrets_file('credentials.json', scopes)
            creds = flow.run_local_server(port=0)
        
        # Guardamos el token para no tener que loguearnos cada vez
        with open('token.json', 'w') as token:
            token.write(creds.to_json())
            
    return creds

def get_sheet(nombre_hoja):
    creds = get_creds()
    gc = gspread.authorize(creds)
    sh = gc.open_by_key(os.getenv("SPREADSHEET_ID"))
    return sh.worksheet(nombre_hoja)

# -------------------------------------------------------
# PROMPTS
# -------------------------------------------------------

def get_prompt_egreso():
    hoy = datetime.now()
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_semana = dias_es[hoy.weekday()]
    
    return f"""Eres un asistente que extrae datos de egresos personales.
Responde SOLO con JSON válido, sin texto extra, sin backticks, sin comentarios.

Hoy es {hoy.strftime("%d/%m/%Y")} ({dia_semana}).

Estructura exacta:
{{
  "descripcion": "texto corto del gasto",
  "cantidad": 50.00,
  "categoria": "Categoría Ejemplo",
  "subcategoria": "No aplica",
  "metodo": "Metodo Ejemplo",
  "fecha": "DD/MM/YYYY"
}}

Reglas de Fecha (¡MUY IMPORTANTE!):
- Calcula la fecha exacta basada en el mensaje del usuario.
- Si el usuario dice "ayer", pon la fecha de ayer.
- Si dice "hace 2 días", resta 2 días a la fecha de hoy.
- Si dice "el lunes", calcula la fecha de ese día.
- Si NO menciona ninguna fecha temporal, usa la fecha de hoy por defecto: {hoy.strftime("%d/%m/%Y")}.

Valores válidos para metodo (elige el más apropiado):
{', '.join(METODOS_VALIDOS)}

Categorías y su significado exacto (elige la más apropiada):
{REGLAS_CATEGORIAS}

Reglas IMPORTANTES para Pasajes:
- Si el gasto es transporte, "categoria" es SIEMPRE "Pasajes".
- Si "categoria" es "Pasajes", en "subcategoria" debes poner: Moto, Taxi, Micro o Metro.
- Para CUALQUIER OTRA categoría que no sea Pasajes, "subcategoria" SIEMPRE es "No aplica".

Otras reglas:
- Si menciona "yape" o "bcp" → metodo: Bcp / Yape
- Si menciona "plin" o "bbva" → metodo: BBVA / Plin
- Si es imagen de boleta, extrae el total y el tipo de negocio. """

PROMPT_CORRECCION = """El usuario quiere corregir datos de un egreso.
Tienes los datos actuales y el mensaje del usuario indicando qué cambiar.
Responde SOLO con el JSON corregido completo, sin texto extra, sin backticks."""

PROMPT_CHAT = """Eres un asistente financiero personal amigable.
Ayudas a registrar egresos y consultar saldos por WhatsApp.
El usuario te está enviando un mensaje casual o de cortesía.
Responde de forma corta, amigable y natural. No uses más de 2 líneas.
No inventes información financiera.
Si pregunta qué puedes hacer, explica brevemente tus funciones de registro y consulta de saldo."""

def get_prompt_intencion():
    hoy = datetime.now()
    dias_es = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
    dia_semana = dias_es[hoy.weekday()]
    return f"""Eres un clasificador de intenciones para un bot financiero personal.
Analiza el mensaje del usuario y responde SOLO con un JSON así:
{{
  "intencion": "REGISTRAR_EGRESO",
  "categoria": null,
  "fecha": null
}}

Intenciones posibles:
- REGISTRAR_EGRESO: quiere registrar un egreso (gasto, pasaje u otro gasto general)
- CONSULTAR_SALDO: quiere saber cuánto dinero tiene disponible
- CONSULTAR_AHORROS: quiere saber cuánto tiene en ahorros
- CONSULTAR_PRESTAMO: quiere saber cuánto le deben o sus préstamos
- CONSULTAR_EGRESO_MES: quiere saber cuánto ha gastado en total este mes
- CONSULTAR_EGRESO_HOY: quiere saber cuánto ha gastado hoy
- CONSULTAR_EGRESO_AYER: quiere saber cuánto gastó ayer
- CONSULTAR_EGRESO_SEMANA: quiere saber cuánto gastó esta semana
- CONSULTAR_EGRESO_FECHA: quiere saber cuánto gastó en una fecha específica
- CONSULTAR_EGRESO_MAYOR: quiere saber cuál fue su egreso más grande
- CONSULTAR_EGRESO_CATEGORIA: quiere saber cuánto gastó en una categoría específica
- CHAT_CASUAL: saludo, agradecimiento, confirmación, conversación general

Para CONSULTAR_EGRESO_CATEGORIA: en "categoria" pon el nombre exacto de la categoría.
Para CONSULTAR_EGRESO_FECHA: en "fecha" pon la fecha en formato DD/MM/YYYY.
Hoy es {hoy.strftime("%d/%m/%Y")} ({dia_semana}). 

Categorías válidas: {', '.join(CATEGORIAS_VALIDAS)}"""

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
    if "categoria" in datos:
        valor = datos["categoria"].strip().lower()
        for cat in CATEGORIAS_VALIDAS:
            if cat.lower() == valor:
                datos["categoria"] = cat
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

def guardar_egreso(datos):
    ws = get_sheet("Egresos")
    col_b = ws.col_values(2)
    ultima_fila_con_dato = 1
    for i, valor in enumerate(col_b):
        if valor.strip():
            ultima_fila_con_dato = i + 1
    nueva_fila = ultima_fila_con_dato + 1
    fecha_hoy = datetime.now().strftime("%d/%m/%Y")
    fecha_gasto = datos.get("fecha", fecha_hoy)

    ws.update([[
        datos.get("descripcion", ""),
        datos['cantidad'],
        fecha_gasto,
    ]], f"B{nueva_fila}:D{nueva_fila}", value_input_option="USER_ENTERED")
    
    ws.update([[
        datos.get("categoria", "Otros"),
        datos.get("subcategoria", "No aplica"),
        datos.get("metodo", "Efectivo"),
    ]], f"G{nueva_fila}:I{nueva_fila}", value_input_option="USER_ENTERED")
    
    print(f">>> Registro guardado exitosamente en la fila {nueva_fila}")

# -------------------------------------------------------
# CONSULTA DE SALDO
# -------------------------------------------------------

def obtener_saldos():
    ws = get_sheet("Resumen")
    # Usa las celdas mapeadas en el config.json, con fallbacks de seguridad
    return {
        "efectivo":    ws.acell(CELDAS_SALDO.get("efectivo", "C4")).value,
        "bcp_yape":    ws.acell(CELDAS_SALDO.get("bcp_yape", "D4")).value,
        "bbva_plin":   ws.acell(CELDAS_SALDO.get("bbva_plin", "C6")).value,
        "metro":       ws.acell(CELDAS_SALDO.get("metro", "D6")).value,
        "interbank":   ws.acell(CELDAS_SALDO.get("interbank", "D8")).value,
        "ahorros":     ws.acell(CELDAS_SALDO.get("ahorros", "C12")).value,
        "prestamos":   ws.acell(CELDAS_SALDO.get("prestamos", "D10")).value,
        "tarjeta":     ws.acell(CELDAS_SALDO.get("tarjeta", "C14")).value,
        "total_pagos": ws.acell(CELDAS_SALDO.get("total_pagos", "D18")).value,
        "gasto_mes":   ws.acell(CELDAS_SALDO.get("gasto_mes", "A6")).value,
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

def obtener_filas_egresos():
    ws = get_sheet("Egresos")
    todos = ws.get_all_values()
    filas = []
    for fila in todos[1:]:
        if len(fila) < 7:
            continue
        descripcion = fila[1].strip()
        cantidad    = fila[2].strip()
        fecha       = fila[3].strip()
        categoria   = fila[6].strip()
        
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
    filas = obtener_filas_egresos()
    gastos = [g for g in filas
              if g["fecha_dt"].day == hoy.day
              and g["fecha_dt"].month == hoy.month
              and g["fecha_dt"].year == hoy.year]
    return formatear_gastos(gastos, "de hoy")

def consulta_gasto_ayer():
    ayer = datetime.now() - timedelta(days=1)
    filas = obtener_filas_egresos()
    gastos = [g for g in filas
              if g["fecha_dt"].day == ayer.day
              and g["fecha_dt"].month == ayer.month
              and g["fecha_dt"].year == ayer.year]
    return formatear_gastos(gastos, f"de ayer ({ayer.strftime('%d/%m/%Y')})")

def consulta_gasto_semana():
    hoy = datetime.now()
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_semana = inicio_semana.replace(hour=0, minute=0, second=0, microsecond=0)
    filas = obtener_filas_egresos()
    gastos = [g for g in filas if inicio_semana <= g["fecha_dt"] <= hoy]
    return formatear_gastos(gastos, "esta semana")

def consulta_gasto_fecha(fecha_str):
    try:
        fecha_dt = datetime.strptime(fecha_str, "%d/%m/%Y")
    except:
        return "No pude entender la fecha. Intenta con formato DD/MM/YYYY."
    filas = obtener_filas_egresos()
    gastos = [g for g in filas
              if g["fecha_dt"].day == fecha_dt.day
              and g["fecha_dt"].month == fecha_dt.month
              and g["fecha_dt"].year == fecha_dt.year]
    return formatear_gastos(gastos, f"del {fecha_str}")

def consulta_gasto_mes():
    hoy = datetime.now()
    filas = obtener_filas_egresos()
    gastos = [g for g in filas
              if g["fecha_dt"].month == hoy.month
              and g["fecha_dt"].year == hoy.year]
    return formatear_gastos(gastos, "este mes")

def consulta_gasto_por_categoria(categoria_buscada):
    hoy = datetime.now()
    filas = obtener_filas_egresos()
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
    filas = obtener_filas_egresos()
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

def resumen_egresos(datos):
    return (
        f"📋 *Confirma el egreso:*\n"
        f"📅 Fecha: {datos.get('fecha')}\n"  
        f"📝 Descripción: {datos.get('descripcion')}\n"
        f"💰 Cantidad: S/.{datos['cantidad']:.2f}\n"
        f"📂 Categoría: {datos.get('categoria')}\n"
        f"📂 Subcategoría: {datos.get('subcategoria')}\n"
        f"💳 Método: {datos.get('metodo')}\n\n"
        f"Responde:\n✅ *sí* para registrar\n✏️ O dime qué corregir\n❌ *cancelar* para descartar"
    )

# -------------------------------------------------------
# ENVIAR MENSAJE VÍA TWILIO
# -------------------------------------------------------

def enviar_whatsapp(remitente, mensaje, tiempo_inicio=None):
    # --- COMENTAMOS ESTO PARA PRUEBAS LOCALES ---
    #twilio_client = TwilioClient(
    #    os.getenv("TWILIO_ACCOUNT_SID"),
    #    os.getenv("TWILIO_AUTH_TOKEN")
    #)
    #twilio_client.messages.create(
    #    from_=os.getenv("TWILIO_SANDBOX_NUMBER"),
    #    to=remitente,
    #    body=mensaje
    #)
    if tiempo_inicio:
        segundos = (datetime.now() - tiempo_inicio).total_seconds()
        print(f">>> Enviado en {segundos:.1f}s: {mensaje[:50]}...")
    else:
        print(f">>> Enviado: {mensaje[:60]}...")

    # --- DEJAMOS SOLO EL PRINT PARA VERLO EN LA TERMINAL ---
    print(f"\n=========================================")
    print(f"💬 MOCK WHATSAPP PARA {remitente}:")
    print(mensaje)
    print(f"=========================================\n")

# -------------------------------------------------------
# PROCESAMIENTO EN SEGUNDO PLANO
# -------------------------------------------------------

def procesar_mensaje(texto, media_url, media_type, remitente):
    texto_lower  = texto.lower().strip()
    tiempo_inicio = datetime.now()
    try:
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
                        "O escribe el método de pago.",
                        tiempo_inicio
                    )
                return

            datos = estado["datos"]

            if texto_lower in ["sí", "si", "s", "yes", "✅", "ok", "dale", "confirmar"]:
                guardar_egreso(datos)
                enviar_whatsapp(remitente,
                    f"✅ *Egreso registrado*\n"
                    f"📝 {datos.get('descripcion')}\n"
                    f"💰 S/.{datos['cantidad']:.2f}\n"
                    f"📂 {datos.get('categoria')} ({datos.get('subcategoria')})\n"
                    f"💳 {datos.get('metodo')}",
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
                enviar_whatsapp(remitente, resumen_egresos(datos_corregidos), tiempo_inicio)

        else:
            print(">>> Detectando intención...")
            try:
                intencion_raw = llamar_ia(get_prompt_intencion(), texto)
                intencion = intencion_raw.get("intencion", "REGISTRAR_EGRESO")
                categoria = intencion_raw.get("categoria")
                fecha     = intencion_raw.get("fecha")
            except Exception as e:
                print(f">>> Error detectando intención: {e} — asumiendo REGISTRAR_EGRESO")
                intencion = "REGISTRAR_EGRESO"
                categoria = None
                fecha     = None

            if intencion == "CONSULTAR_AHORROS":
                s = obtener_saldos()
                enviar_whatsapp(remitente, f"🐷 *Tus ahorros:* {s['ahorros']}", tiempo_inicio)

            elif intencion == "CONSULTAR_PRESTAMO":
                s = obtener_saldos()
                enviar_whatsapp(remitente, f"💸 *Préstamos / lo que te deben:* {s['prestamos']}", tiempo_inicio)

            elif intencion == "CONSULTAR_EGRESO_MES":
                s = obtener_saldos()
                enviar_whatsapp(remitente, f"📊 *Tu gasto total este mes:* {s['gasto_mes']}", tiempo_inicio)

            elif intencion == "CONSULTAR_SALDO":
                estados[remitente] = {"tipo": "consulta_saldo"}
                enviar_whatsapp(remitente,
                    "💰 ¿Qué quieres consultar?\n\n"
                    "1️⃣ Ver *todo* el desglose\n"
                    "2️⃣ Solo el *total disponible*\n\n"
                    "O escribe el método directamente",
                    tiempo_inicio
                )

            elif intencion == "CONSULTAR_EGRESO_HOY":
                enviar_whatsapp(remitente, consulta_gasto_hoy(), tiempo_inicio)

            elif intencion == "CONSULTAR_EGRESO_AYER":
                enviar_whatsapp(remitente, consulta_gasto_ayer(), tiempo_inicio)

            elif intencion == "CONSULTAR_EGRESO_SEMANA":
                enviar_whatsapp(remitente, consulta_gasto_semana(), tiempo_inicio)

            elif intencion == "CONSULTAR_EGRESO_FECHA":
                if fecha:
                    enviar_whatsapp(remitente, consulta_gasto_fecha(fecha), tiempo_inicio)
                else:
                    enviar_whatsapp(remitente,
                        "No entendí la fecha. Intenta con:\n"
                        "*'cuánto gasté el 15 de marzo'*\n"
                        "*'cuánto gasté el lunes'*",
                        tiempo_inicio
                    )

            elif intencion == "CONSULTAR_EGRESO_MAYOR":
                enviar_whatsapp(remitente, consulta_gasto_mayor(), tiempo_inicio)

            elif intencion == "CONSULTAR_EGRESO_CATEGORIA":
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

            else:
                print(">>> Llamando a IA (egreso)...")
                datos = llamar_ia(get_prompt_egreso(), texto, media_url, media_type)
                datos = normalizar_datos(datos)
                estados[remitente] = {"datos": datos, "tipo": "egreso"}
                enviar_whatsapp(remitente, resumen_egresos(datos), tiempo_inicio)

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