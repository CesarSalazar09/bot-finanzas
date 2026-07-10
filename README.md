# 🤖 FinBot: Asistente Financiero Personal para WhatsApp

FinBot es un asistente financiero personal desarrollado en Python que permite gestionar, registrar y consultar tus egresos diarios directamente desde WhatsApp. Utiliza Inteligencia Artificial (Groq/Llama) para procesar lenguaje natural y Google Sheets como base de datos centralizada.

## ✨ Características Principales
* **Registro Inteligente:** Envía mensajes casuales (ej. "Gasté 15 soles en menú") y el bot extrae automáticamente la descripción, monto, categoría y método de pago.
* **Procesamiento de Imágenes:** Capacidad de analizar recibos o boletas mediante modelos de visión artificial para extraer datos automáticamente.
* **Consultas en Tiempo Real:** Pregunta cuánto gastaste hoy, esta semana o cuánto dinero disponible tienes en tus diferentes cuentas.
* **Integración con Google Sheets:** Tus finanzas se mantienen organizadas en una hoja de cálculo personal, facilitando el control y análisis posterior.
* **Autenticación Segura:** Implementación de flujo OAuth2 para acceder a tus datos de forma segura bajo tus propios permisos.

## 🛠️ Tecnologías Utilizadas
* **Backend:** Flask (Python)
* **Inteligencia Artificial:** Groq API (Llama 3.1) y Llama Vision (para procesamiento de imágenes).
* **Integraciones:** Twilio API (para la mensajería de WhatsApp), Google Sheets API (vía `gspread`).
* **Seguridad:** Gestión de variables de entorno y autenticación OAuth2 de Google.

## 🚀 Cómo funciona
1. El usuario envía un mensaje a través de WhatsApp.
2. Twilio recibe el mensaje y lo redirige al Webhook de la aplicación Flask.
3. El bot utiliza IA para clasificar la intención (registrar egreso, consultar saldo, etc.).
4. Si es un registro, la IA extrae los datos en formato JSON y el bot los guarda en Google Sheets.
5. Si es una consulta, el bot lee los valores de las celdas configuradas en tu Google Sheet y responde al usuario.

## ⚙️ Configuración del Entorno
Para ejecutar este proyecto, necesitas configurar los siguientes archivos:

1. **`.env`**: Archivo con tus credenciales privadas
   ```text
   GROQ_API_KEY=tu_clave_aqui
   TWILIO_ACCOUNT_SID=tu_sid_aqui
   TWILIO_AUTH_TOKEN=tu_token_aqui
   TWILIO_SANDBOX_NUMBER=tu_numero_sandbox
   SPREADSHEET_ID=el_id_de_tu_hoja_de_calculo
2. **`credentials_user.json`**: Archivo obtenido desde Google Cloud Console (OAuth2 App de escritorio).
3. **`token.json`**: Se genera automáticamente al iniciar sesión por primera vez mediante el navegador.

## 🤝 Contribuciones
Este proyecto está diseñado para uso personal. Si deseas adaptarlo, asegúrate de configurar tu propio entorno de Google Cloud y tus llaves de API.
