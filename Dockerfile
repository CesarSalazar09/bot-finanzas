# 1. Usamos una imagen oficial de Python súper ligera
FROM python:3.11-slim

# 2. Configuraciones para que los logs de Python se vean en tiempo real en la consola de Google
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# 3. Creamos una carpeta llamada /app dentro del contenedor y nos movemos ahí
WORKDIR /app

# 4. Copiamos SOLO el archivo de requerimientos primero (optimiza el tiempo de construcción)
COPY requirements.txt .

# 5. Instalamos tus librerías (gspread, openai, flask, gunicorn, etc.)
RUN pip install --no-cache-dir -r requirements.txt

# 6. Ahora copiamos el resto de tu código fuente (app.py, credenciales si las hubiera, etc.)
COPY . .

# 7. Cloud Run inyecta automáticamente la variable $PORT (usualmente 8080)
ENV PORT=8080

# 8. Arrancamos Gunicorn como el mánager de tu bot
CMD exec gunicorn --bind :$PORT --workers 1 --threads 8 --timeout 0 app:app