import os
import re
import html
import requests
import feedparser
from datetime import datetime
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
def cargar_urls_registradas(readme_path="README.md"):
    """Lee el README.md para extraer todas las URLs previamente guardadas y evitar duplicados."""
    if not os.path.exists(readme_path):
        return set()
    with open(readme_path, "r", encoding="utf-8") as f:
        contenido = f.read()
    urls = set(re.findall(r'https?://[^\s\)\]]+', contenido))
    return urls


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
    """Envía el reporte a todos los IDs configurados en TELEGRAM_CHAT_ID (separados por coma)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: No se han configurado los tokens de Telegram.")
        return

    chat_ids = [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]
    url_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    for chat_id in chat_ids:
        for sub_mensaje in dividir_en_bloques(mensaje):
            _enviar_un_mensaje(url_base, chat_id, sub_mensaje)


def _enviar_un_mensaje(url_base, chat_id, texto, parse_mode="HTML"):
    """Envía un único mensaje. Si Telegram rechaza el formato (HTML mal formado),
    reintenta una vez en texto plano para no perder el contenido."""
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
            return

        print(f"Error enviando a Telegram (Chat ID {chat_id}): {res.text}")

        # Si el fallo es por formato (entidades HTML mal formadas), reintentamos
        # sin parse_mode para no perder el mensaje, quitando las etiquetas.
        if parse_mode and "can't parse entities" in res.text.lower():
            texto_plano = re.sub(r"<[^>]+>", "", texto)
            _enviar_un_mensaje(url_base, chat_id, texto_plano, parse_mode=None)

    except Exception as e:
        print(f"Excepción al conectar con Telegram (Chat ID {chat_id}): {e}")


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
def obtener_noticias_rss(urls_previas):
    """Obtiene noticias en ES, FR, AR e EN vía Google News RSS."""
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
            for entry in feed.entries[:3]:
                link = getattr(entry, "link", None)
                titulo = getattr(entry, "title", None)
                if not link or not titulo:
                    continue
                if link not in urls_previas:
                    urls_previas.add(link)
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
                if video_url not in urls_previas:
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
            for entry in feed.entries[:2]:
                link = getattr(entry, "link", None)
                titulo = getattr(entry, "title", None)
                if not link or not titulo:
                    continue
                if link not in urls_previas:
                    urls_previas.add(link)
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
# FLUJO PRINCIPAL
# ==========================================
def main():
    print("Iniciando rastreo del Puerto de Dakhla Atlantique...")

    # 0. Comprobar que es la hora de envío en Canarias.
    # El workflow de GitHub Actions se dispara dos veces (7:00 y 8:00 UTC)
    # para cubrir el cambio de horario de verano/invierno; aquí descartamos
    # la ejecución que no coincide con las 8:00 hora de Canarias.
    ahora_canarias = datetime.now(ZONA_CANARIAS)
    if ahora_canarias.hour != HORA_ENVIO_OBJETIVO:
        print(f"Hora actual en Canarias: {ahora_canarias.strftime('%H:%M')}. "
              f"No son las {HORA_ENVIO_OBJETIVO}:00, se omite esta ejecución.")
        return

    # 1. Cargar historial
    urls_registradas = cargar_urls_registradas()
    print(f"Memoria cargada: {len(urls_registradas)} URLs previas en registro.")

    # 2. Recopilar contenido
    noticias = obtener_noticias_rss(urls_registradas)
    videos = obtener_videos_youtube(urls_registradas)
    radios = obtener_audios_radio(urls_registradas)
    podcasts = buscar_podcasts_dakhla(dias_atras=1)

    # Filtrar podcasts no vistos
    podcasts_nuevos = [p for p in podcasts if p['url'] not in urls_registradas]
    for p in podcasts_nuevos:
        urls_registradas.add(p['url'])

    if not noticias and not videos and not radios and not podcasts_nuevos:
        print("No se encontraron novedades nuevas hoy. Finalizando proceso.")
        return

    print(f"Novedades halladas -> Noticias: {len(noticias)} | Vídeos: {len(videos)} | Radios: {len(radios)} | Podcasts: {len(podcasts_nuevos)}")

    # 3. Construir mensaje con secciones separadas (formato HTML, más robusto que Markdown)
    fecha_hoy = ahora_canarias.strftime("%Y-%m-%d")
    reporte = f"🚢 <b>REPORTE DIARIO: PUERTO DE DAKHLA ATLANTIQUE</b> ({fecha_hoy})\n\n"

    secciones = []
    texto_para_ia = ""

    # Bloque de Prensa
    if noticias:
        block_noticias = "📰 <b>NOTICIAS DE PRENSA:</b>\n"
        for n in noticias:
            block_noticias += f"• [{esc(n['idioma'])}] {enlace_html(n['titulo'], n['link'])}\n"
            texto_para_ia += f"- {n['titulo']}\n"
        secciones.append(block_noticias)

    # Bloque de Youtube
    if videos:
        block_videos = "🎥 <b>VÍDEOS DESTACADOS:</b>\n"
        for v in videos:
            block_videos += f"• {enlace_html(v['titulo'], v['link'])}\n"
            texto_para_ia += f"- {v['titulo']}\n"
        secciones.append(block_videos)

    # Bloque de Radio
    if radios:
        block_radios = "📻 <b>BOLETINES DE RADIO Y EMISIONES:</b>\n"
        for r in radios:
            block_radios += f"• [{esc(r['fuente'])}] {enlace_html(r['titulo'], r['link'])}\n"
            texto_para_ia += f"- Radio ({r['fuente']}): {r['titulo']}\n"
        secciones.append(block_radios)

    # Bloque de Podcasts
    if podcasts_nuevos:
        block_podcasts = "🎙️ <b>PODCASTS Y ANÁLISIS:</b>\n"
        for pod in podcasts_nuevos:
            resumen_pod = procesar_podcast_con_gemini(pod)
            if resumen_pod:
                block_podcasts += resumen_pod + "\n"
                texto_para_ia += f"- Podcast: {pod['titulo']} ({pod['descripcion'][:100]})\n"
            else:
                block_podcasts += f"• <b>{esc(pod['podcast'])}</b>: {enlace_html(pod['titulo'], pod['url'])}\n"
        secciones.append(block_podcasts)

    # Unir todas las secciones principales con un separador visual limpio
    reporte += "\n\n───────────────────\n\n".join(secciones)
    reporte += "\n\n"

    # Resumen General de IA y Firma
    resumen_global = generar_resumen_general_ia(texto_para_ia)
    if resumen_global:
        reporte += "───────────────────\n\n"
        reporte += "🧠 <b>Resumen del Día:</b>\n"
        reporte += f"{resumen_global}\n\n"

    reporte += "🤖 Resumen creado por Mamé_el_bot 🤖"

    # 4. Enviar a Telegram
    enviar_telegram(reporte)

    # 5. Actualizar historial en README.md
    with open("README.md", "a", encoding="utf-8") as f:
        f.write(f"\n\n### Registro {fecha_hoy}\n")
        f.write(reporte)

    print("¡Reporte enviado y README.md actualizado con éxito!")


if __name__ == "__main__":
    main()
