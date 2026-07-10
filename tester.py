import requests
import time

# La URL local donde tu bot de Flask está escuchando
URL_WEBHOOK = "http://localhost:5000/webhook"

# Tu número simulado
MI_NUMERO = "whatsapp:+51999888777" 

def simular_mensaje(texto):
    print(f"\n[TÚ]: {texto}")
    # Twilio siempre envía los datos en formato de formulario (x-www-form-urlencoded)
    payload = {
        "Body": texto,
        "From": MI_NUMERO
    }
    
    # Enviamos la petición POST a Flask
    try:
        response = requests.post(URL_WEBHOOK, data=payload)
        if response.status_code == 204:
            print("✓ Petición enviada correctamente al webhook (esperando respuesta de la IA...)")
        else:
            print(f"⚠️ Error HTTP: {response.status_code}")
    except requests.exceptions.ConnectionError:
        print("❌ Error: No se pudo conectar. ¿Está corriendo 'python app.py' en otra terminal?")

# --- AQUÍ ESCRIBES LAS PRUEBAS QUE QUIERAS HACER ---

# Prueba 1: Registrar un egreso
#simular_mensaje("Compré un menú por 20 soles y pagué con efectivo")

# Esperamos un poquito para no saturar si hacemos varias seguidas
#time.sleep(3) 

# Prueba 2: Simular el "sí" de confirmación (Descomenta para probar)
simular_mensaje("sí")

# Prueba 3: Consultar un saldo (Descomenta para probar)
# simular_mensaje("cuánto tengo en yape")