import os
import re
import sys
import html
import time
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from google import genai
from podcasts import buscar_podcasts_dakhla

# ==========================================
# CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Zona horaria de Canarias (gestiona sola el cambio de horario verano/invierno)
ZONA_CANARIAS = ZoneInfo("Atlantic/Canary")
HORA_ENVIO_OBJETIVO = 8  # 8:00 hora de Canarias

# Si el workflow se ha lanzado manualmente ("Forzar Reporte" en el bot, o "Run
# workflow" en GitHub), nos saltamos tanto la comprobación de la hora como la
# de "ya se envió hoy", para que el botón funcione a cualquier hora.
EJECUCION_MANUAL = os.environ.get("EJECUCION_MANUAL", "false").strip().lower() == "true"

# Orden y banderas de los idiomas de prensa
IDIOMAS_ORDEN = ["Español", "Francés", "Árabe", "Inglés"]
BANDERA_IDIOMA = {"Español": "🇪🇸", "Francés": "🇫🇷", "Árabe": "🇲🇦", "Inglés": "🇬🇧"}

# Antigüedad máxima permitida para considerar algo "noticia de hoy" (en días).
# Se usan 2 días de margen (en vez de 1) para no perder publicaciones por
# pequeños desfases horarios entre servidores, sin llegar a colar noticias
# realmente antiguas que Google/YouTube devuelven por seguir siendo populares.
DIAS_MAXIMOS_NOTICIA = 2

# Configuración de Gemini AI (SDK nuevo "google-genai"; el antiguo
# "google-generativeai" está descontinuado por Google)
GEMINI_MODEL = "gemini-2.5-flash"
gemini_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY else None


def preguntar_gemini(prompt):
    """Llama a Gemini con el SDK nuevo. Devuelve None si falla o no hay API key."""
    if not gemini_client:
        return None
    try:
        response = gemini_client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt
        )
        return response.text
    except Exception as e:
        print(f"Error llamando a Gemini: {e}")
        return None


# ==========================================
# FUNCIONES AUXILIARES
# ==========================================
def cargar_readme(readme_path="README.md"):
    """Lee el contenido completo del README.md (o cadena vacía si no existe)."""
    if not os.path.exists(readme_path):
        return ""
    with open(readme_path, "r", encoding="utf-8") as f:
        return f.read()


def cargar_urls_registradas(contenido_readme):
    """Extrae del README todas las URLs ya guardadas, para evitar duplicados."""
    return set(re.findall(r'https?://[^\s\)\]"]+', contenido_readme))


def esc(texto):
    """Escapa un texto para que sea seguro insertarlo en un mensaje de Telegram en modo HTML."""
    return html.escape(texto or "", quote=False)


def enlace_html(texto, url):
    """Construye un enlace HTML seguro para Telegram (escapando título y URL)."""
    return f'<a href="{html.escape(url or "#", quote=True)}">{esc(texto)}</a>'


def dividir_en_bloques(mensaje, max_len=3900):
    """
    Divide un mensaje largo en bloques respetando los saltos de línea,
    para no cortar nunca una etiqueta HTML (<b>, <a href=...>) por la mitad,
    lo que rompería el formato y haría fallar el envío a Telegram.
    """
    lineas = mensaje.split("\n")
    bloques = []
    actual = ""
    for linea in lineas:
        candidato = (actual + "\n" + linea) if actual else linea
        if len(candidato) > max_len:
            if actual:
                bloques.append(actual)
            actual = linea
        else:
            actual = candidato
    if actual:
        bloques.append(actual)
    return bloques


def enviar_telegram(mensaje):
    """
    Envía el reporte a todos los IDs configurados en TELEGRAM_CHAT_ID (separados por coma).
    Devuelve True solo si TODOS los envíos a TODOS los chats tuvieron éxito, para que
    quien llama pueda hacer fallar la ejecución si algo no ha llegado de verdad.
    """
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: No se han configurado los tokens de Telegram (TELEGRAM_TOKEN / TELEGRAM_CHAT_ID).")
        return False

    chat_ids = [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]
    url_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    todo_ok = True
    for chat_id in chat_ids:
        for sub_mensaje in dividir_en_bloques(mensaje):
            ok = _enviar_un_mensaje(url_base, chat_id, sub_mensaje)
            todo_ok = todo_ok and ok

    return todo_ok


def _enviar_un_mensaje(url_base, chat_id, texto, parse_mode="HTML"):
    """Envía un único mensaje. Si Telegram rechaza el formato (HTML mal formado),
    reintenta una vez en texto plano para no perder el contenido. Devuelve True/False."""
    payload = {
        "chat_id": chat_id,
        "text": texto,
        "disable_web_page_preview": False
    }
    if parse_mode:
        payload["parse_mode"] = parse_mode

    try:
        res = requests.post(url_base, json=payload, timeout=15)
        if res.status_code == 200:
            print(f"Reporte enviado con éxito al Chat ID: {chat_id}")
            return True

        print(f"Error enviando a Telegram (Chat ID {chat_id}): {res.text}")

        # Si el fallo es por formato (entidades HTML mal formadas), reintentamos
        # sin parse_mode para no perder el mensaje, quitando las etiquetas.
        if parse_mode and "can't parse entities" in res.text.lower():
            texto_plano = re.sub(r"<[^>]+>", "", texto)
            return _enviar_un_mensaje(url_base, chat_id, texto_plano, parse_mode=None)

        return False

    except Exception as e:
        print(f"Excepción al conectar con Telegram (Chat ID {chat_id}): {e}")
        return False


def procesar_podcast_con_gemini(episodio):
    """Aplica Gemini para traducir y resumir un episodio de podcast al castellano."""
    prompt = f"""
    Eres un analista de prensa especializado en infraestructuras y geopolítica en África.
    Tengo un episodio de podcast sobre el Puerto de Dakhla Atlantique.

    Información del episodio:
    - Programa: {episodio['podcast']}
    - Título: {episodio['titulo']}
    - Fecha: {episodio['fecha']}
    - Descripción / Notas: {episodio['descripcion']}

    Por favor, analiza la información (tradúcela al castellano si está en otro idioma) y redacta:
    1. Un titular corto e informativo en castellano.
    2. Un resumen breve (2 o 3 frases) con los puntos clave.

    Responde SOLO con estas 2 líneas de texto plano, sin ningún formato Markdown ni HTML
    y sin repetir el nombre del programa ni el enlace (yo los añadiré después):
    Línea 1: el titular
    Línea 2: el resumen
    """
    texto = preguntar_gemini(prompt)
    if not texto:
        return None

    lineas = [l.strip() for l in texto.strip().split("\n") if l.strip()]
    titular = lineas[0] if len(lineas) > 0 else episodio['titulo']
    resumen = lineas[1] if len(lineas) > 1 else episodio['descripcion'][:200]

    return (
        f"🎙️ <b>[PODCAST] {esc(titular)}</b>\n"
        f"• <b>Programa:</b> {esc(episodio['podcast'])}\n"
        f"• <b>Resumen:</b> {esc(resumen)}\n"
        f"🔗 {enlace_html('Escuchar episodio', episodio['url'])}\n"
    )


def generar_resumen_general_ia(texto_noticias):
    """Genera un resumen ejecutivo global diario utilizando Gemini."""
    prompt = f"""
    Sintetiza las siguientes novedades de hoy sobre el Puerto de Dakhla Atlantique en un resumen ejecutivo breve (máximo 2 párrafos) en español.
    Destaca los avances en infraestructura, licitaciones o impacto geopolítico si los hay.
    Responde solo con texto plano, sin Markdown ni HTML.

    Noticias del día:
    {texto_noticias}
    """
    texto = preguntar_gemini(prompt)
    return esc(texto) if texto else ""


# ==========================================
# RASTREO DE PRENSA, YOUTUBE Y RADIOS
# ==========================================
def es_reciente_rss(entry, dias_maximos=DIAS_MAXIMOS_NOTICIA):
    """
    Comprueba si una entrada de un feed RSS (noticia o boletín de radio) se
    publicó dentro de los últimos `dias_maximos` días. Si el feed no trae
    fecha de publicación, se deja pasar (no hay forma de comprobarlo), pero
    se avisa por consola.
    """
    parsed = getattr(entry, "published_parsed", None) or getattr(entry, "updated_parsed", None)
    if not parsed:
        return True
    try:
        fecha_entry = datetime.fromtimestamp(time.mktime(parsed), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return True
    limite = datetime.now(timezone.utc) - timedelta(days=dias_maximos)
    return fecha_entry >= limite


def obtener_noticias_rss(urls_previas):
    """Obtiene noticias en ES, FR, AR e EN vía Google News RSS, descartando
    cualquier resultado con más de DIAS_MAXIMOS_NOTICIA días de antigüedad."""
    busquedas = [
        ("Español", "https://news.google.com/rss/search?q=Puerto+Dakhla+Atlantique&hl=es&gl=ES&ceid=ES:es"),
        ("Francés", "https://news.google.com/rss/search?q=Port+Dakhla+Atlantique&hl=fr&gl=MA&ceid=MA:fr"),
        ("Árabe", "https://news.google.com/rss/search?q=%D9%85%D9%8A%D9%86%D8%A7%D8%A1+%D8%A7%D9%84%D8%AF%D8%A7%D8%AE%D9%84%D8%A9+%D8%A7%D9%84%D8%A3%D8%B7%D9%84%D8%B3%D9%8A&hl=ar&gl=MA&ceid=MA:ar"),
        ("Inglés", "https://news.google.com/rss/search?q=Dakhla+Atlantic+Port&hl=en&gl=US&ceid=US:en")
    ]

    noticias_nuevas = []
    for idioma, url in busquedas:
        try:
            feed = feedparser.parse(url)
            encontradas = 0
            for entry in feed.entries:
                if encontradas >= 3:
                    break
                link = getattr(entry, "link", None)
                titulo = getattr(entry, "title", None)
                if not link or not titulo:
                    continue
                if link in urls_previas:
                    continue
                if not es_reciente_rss(entry):
                    continue
                urls_previas.add(link)
                encontradas += 1
                noticias_nuevas.append({
                    "idioma": idioma,
                    "titulo": titulo,
                    "link": link,
                    "fecha": datetime.now(ZONA_CANARIAS).strftime("%Y-%m-%d")
                })
        except Exception as e:
            print(f"Error en feed RSS ({idioma}): {e}")

    return noticias_nuevas


def obtener_videos_youtube(urls_previas):
    """Obtiene vídeos nuevos desde la API de YouTube."""
    if not YOUTUBE_API_KEY:
        return []

    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": "Port Dakhla Atlantique",
        "type": "video",
        "order": "date",
        "maxResults": 3,
        "key": YOUTUBE_API_KEY
    }

    videos = []
    try:
        res = requests.get(url, params=params, timeout=10)
        if res.status_code == 200:
            items = res.json().get("items", [])
            for item in items:
                video_id = item["id"]["videoId"]
                video_url = f"https://www.youtube.com/watch?v={video_id}"
                if video_url in urls_previas:
                    continue
                publicado_str = item["snippet"].get("publishedAt")
                if publicado_str:
                    try:
                        fecha_video = datetime.fromisoformat(publicado_str.replace("Z", "+00:00"))
                        if fecha_video < datetime.now(timezone.utc) - timedelta(days=DIAS_MAXIMOS_NOTICIA):
                            continue
                    except ValueError:
                        pass  # si no se puede interpretar la fecha, lo dejamos pasar
                urls_previas.add(video_url)
                videos.append({
                    "titulo": item["snippet"]["title"],
                    "link": video_url,
                    "fecha": datetime.now(ZONA_CANARIAS).strftime("%Y-%m-%d")
                })
        else:
            print(f"Error al consultar YouTube API: HTTP {res.status_code} - {res.text}")
    except Exception as e:
        print(f"Error al consultar YouTube API: {e}")

    return videos


def obtener_audios_radio(urls_previas):
    """Obtiene boletines o programas de radio relacionados mediante fuentes RSS y Google News."""
    busquedas_radio = [
        ("Radio Medi1", "https://news.google.com/rss/search?q=Medi1+Port+Dakhla+Atlantique&hl=fr&gl=MA&ceid=MA:fr"),
        ("Radio General", "https://news.google.com/rss/search?q=radio+Dakhla+Atlantic+port&hl=en&gl=US&ceid=US:en")
    ]

    audios_nuevos = []
    for fuente, url in busquedas_radio:
        try:
            feed = feedparser.parse(url)
            encontradas = 0
            for entry in feed.entries:
                if encontradas >= 2:
                    break
                link = getattr(entry, "link", None)
                titulo = getattr(entry, "title", None)
                if not link or not titulo:
                    continue
                if link in urls_previas:
                    continue
                if not es_reciente_rss(entry):
                    continue
                urls_previas.add(link)
                encontradas += 1
                audios_nuevos.append({
                    "fuente": fuente,
                    "titulo": titulo,
                    "link": link,
                    "fecha": datetime.now(ZONA_CANARIAS).strftime("%Y-%m-%d")
                })
        except Exception as e:
            print(f"Error al rastrear radio ({fuente}): {e}")

    return audios_nuevos


# ==========================================
# CONSTRUCCIÓN DEL REPORTE (nuevo diseño)
# ==========================================
def construir_bloque_prensa(noticias):
    """📰 Prensa Escrita Internacional, agrupada por idioma con su bandera."""
    bloque = "📰 <b>Prensa Escrita Internacional</b>\n\n"

    if not noticias:
        return bloque + "No hay noticias.\n"

    for idioma in IDIOMAS_ORDEN:
        items = [n for n in noticias if n["idioma"] == idioma]
        if not items:
            continue
        bandera = BANDERA_IDIOMA.get(idioma, "🌐")
        bloque += f"{bandera} <b>{esc(idioma)}</b>\n"
        for n in items:
            bloque += f"• {enlace_html(n['titulo'], n['link'])}\n"
        bloque += "\n"

    return bloque.rstrip() + "\n"


def construir_bloque_podcasts_radio(radios, podcasts_nuevos):
    """🎙️📻 Podcasts & Radio unificados en una sola sección."""
    bloque = "🎙️📻 <b>Podcasts & Radio</b>\n\n"
    hay_contenido = False

    for r in radios:
        hay_contenido = True
        bloque += f"• [{esc(r['fuente'])}] {enlace_html(r['titulo'], r['link'])}\n"

    for pod in podcasts_nuevos:
        hay_contenido = True
        resumen_pod = procesar_podcast_con_gemini(pod)
        if resumen_pod:
            bloque += resumen_pod + "\n"
        else:
            bloque += f"• [{esc(pod['podcast'])}] {enlace_html(pod['titulo'], pod['url'])}\n"

    if not hay_contenido:
        bloque += "No hay noticias.\n"

    return bloque.rstrip() + "\n"


def construir_bloque_youtube(videos):
    """📺 YouTube & Vídeos."""
    bloque = "📺 <b>YouTube & Vídeos</b>\n\n"

    if not videos:
        return bloque + "No hay noticias.\n"

    for v in videos:
        bloque += f"• {enlace_html(v['titulo'], v['link'])}\n"

    return bloque.rstrip() + "\n"


def construir_bloque_resumen_ia(texto_para_ia):
    """🤖✨ Resumen Diario de la IA, sintetizando todo lo recopilado en el día."""
    bloque = "🤖✨ <b>Resumen Diario de la IA</b>\n\n"

    if not texto_para_ia.strip():
        return bloque + "No hay noticias que resumir hoy."

    resumen_global = generar_resumen_general_ia(texto_para_ia)
    bloque += resumen_global if resumen_global else "No se ha podido generar el resumen automático hoy."
    return bloque


# ==========================================
# FLUJO PRINCIPAL
# ==========================================
def main():
    print("Iniciando rastreo del Puerto de Dakhla Atlantique...")
    print(f"Tipo de ejecución: {'MANUAL (forzada)' if EJECUCION_MANUAL else 'programada (cron)'}")

    # 0. Comprobar que es la hora de envío en Canarias.
    # El workflow de GitHub Actions se dispara dos veces (7:00 y 8:00 UTC)
    # para cubrir el cambio de horario de verano/invierno; aquí descartamos
    # la ejecución que no coincide con las 8:00 hora de Canarias.
    ahora_canarias = datetime.now(ZONA_CANARIAS)
    if not EJECUCION_MANUAL and ahora_canarias.hour != HORA_ENVIO_OBJETIVO:
        print(f"Hora actual en Canarias: {ahora_canarias.strftime('%H:%M')}. "
              f"No son las {HORA_ENVIO_OBJETIVO}:00, se omite esta ejecución.")
        return

    fecha_hoy = ahora_canarias.strftime("%Y-%m-%d")

    # 1. Cargar historial y comprobar que no se haya enviado ya el reporte de hoy
    # (por ejemplo, si las dos ejecuciones programadas caen accidentalmente en
    # la misma hora de Canarias durante el cambio de horario).
    contenido_readme = cargar_readme()
    if not EJECUCION_MANUAL and f"### Registro {fecha_hoy}" in contenido_readme:
        print(f"El reporte de hoy ({fecha_hoy}) ya se envió anteriormente. Se omite esta ejecución.")
        return

    urls_registradas = set() if EJECUCION_MANUAL else cargar_urls_registradas(contenido_readme)
    if EJECUCION_MANUAL:
        print("Ejecución manual: se ignora el filtro de 'ya registrado', para mostrar siempre la foto actual.")
    print(f"Memoria cargada: {len(urls_registradas)} URLs previas en registro.")

    # 2. Recopilar contenido
    noticias = obtener_noticias_rss(urls_registradas)
    videos = obtener_videos_youtube(urls_registradas)
    radios = obtener_audios_radio(urls_registradas)
    podcasts = buscar_podcasts_dakhla(dias_atras=1)
    podcasts_nuevos = [p for p in podcasts if p['url'] not in urls_registradas]

    print(f"Novedades halladas -> Noticias: {len(noticias)} | Vídeos: {len(videos)} | Radios: {len(radios)} | Podcasts: {len(podcasts_nuevos)}")

    # 3. Construir el texto que se le pasará a la IA para el resumen ejecutivo
    texto_para_ia = ""
    for n in noticias:
        texto_para_ia += f"- {n['titulo']}\n"
    for v in videos:
        texto_para_ia += f"- {v['titulo']}\n"
    for r in radios:
        texto_para_ia += f"- Radio ({r['fuente']}): {r['titulo']}\n"
    for pod in podcasts_nuevos:
        texto_para_ia += f"- Podcast: {pod['titulo']} ({pod['descripcion'][:100]})\n"

    # 4. Construir el reporte completo. Se envía SIEMPRE, aunque no haya
    # novedades, mostrando "No hay noticias" en las secciones vacías.
    reporte = f"🚢 <b>REPORTE DIARIO: PUERTO DE DAKHLA ATLANTIQUE</b> ({fecha_hoy})\n\n"
    reporte += construir_bloque_prensa(noticias) + "\n"
    reporte += construir_bloque_podcasts_radio(radios, podcasts_nuevos) + "\n"
    reporte += construir_bloque_youtube(videos) + "\n"
    reporte += construir_bloque_resumen_ia(texto_para_ia) + "\n\n"
    reporte += "🤖 Generado por Mamé el Bot 🤖"

    # 5. Enviar a Telegram. Si falla de verdad (no solo "sin novedades"),
    # hacemos que la ejecución de GitHub Actions se marque como fallida
    # para que llegue la notificación de aviso correspondiente.
    envio_ok = enviar_telegram(reporte)

    # 6. Actualizar historial en README.md (se guarda igualmente, para no
    # perder lo recopilado aunque el envío a Telegram haya fallado)
    with open("README.md", "a", encoding="utf-8") as f:
        f.write(f"\n\n### Registro {fecha_hoy}\n")
        f.write(reporte)

    if not envio_ok:
        print("El envío a Telegram ha fallado. Marcando la ejecución como fallida.")
        sys.exit(1)

    print("¡Reporte enviado y README.md actualizado con éxito!")


if __name__ == "__main__":
    main()
