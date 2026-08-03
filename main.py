import os
import re
import sys
import html
import json
import time
import requests
import feedparser
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo
from podcasts import buscar_podcasts_dakhla

try:
    from googlenewsdecoder import new_decoderv1
except ImportError:
    new_decoderv1 = None

# ==========================================
# CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")

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

# Configuración de la IA para el resumen diario: Groq (gratis, muy rápido,
# modelos de código abierto tipo Llama). Se usa vía API REST compatible con
# el formato de OpenAI, con requests, sin necesidad de instalar ninguna
# librería nueva.
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"


def preguntar_ia(prompt):
    """Llama a Groq. Devuelve None si falla o no hay API key configurada."""
    if not GROQ_API_KEY:
        return None
    try:
        res = requests.post(
            GROQ_URL,
            headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
            json={
                "model": GROQ_MODEL,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            },
            timeout=30
        )
        if res.status_code != 200:
            print(f"Error llamando a la IA (Groq): HTTP {res.status_code} - {res.text}")
            return None
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        print(f"Error llamando a la IA (Groq): {e}")
        return None


def resolver_url_real(url):
    """
    Si el enlace es un redirector de Google News (news.google.com/rss/articles/...),
    intenta resolverlo a la URL real del artículo de origen. Si la librería no está
    disponible, falla, o tarda demasiado, se devuelve la URL original sin más:
    nunca debe impedir que la noticia se envíe.
    """
    if not new_decoderv1 or "news.google.com" not in url:
        return url
    try:
        resultado = new_decoderv1(url, interval=1)
        if resultado.get("status") and resultado.get("decoded_url"):
            return resultado["decoded_url"]
    except Exception as e:
        print(f"No se pudo resolver el enlace real (se deja el original): {e}")
    return url


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


def cargar_registro_json(path="registro.json"):
    """
    Lee el registro estructurado (registro.json), la fuente de datos "de verdad"
    que usa telegram_bot.py para /registro y /exportar. Si no existe o está
    corrupto, se devuelve un diccionario vacío en vez de romper la ejecución.
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError) as e:
        print(f"Aviso: no se pudo leer {path} ({e}). Se empieza un registro nuevo.")
        return {}


def guardar_registro_json(registro, path="registro.json"):
    """Guarda el registro estructurado en disco, con claves de fecha ordenadas
    (que al ser AAAA-MM-DD también quedan ordenadas cronológicamente)."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(registro, f, ensure_ascii=False, indent=2, sort_keys=True)


def extraer_urls_registro_json(registro):
    """Extrae todas las URLs ya guardadas en el registro.json, para el control de duplicados."""
    urls = set()
    for dia in registro.values():
        for item in dia.get("items", []):
            if item.get("link"):
                urls.add(item["link"])
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


def procesar_podcast_con_ia(episodio):
    """Aplica la IA para traducir y resumir un episodio de podcast al castellano."""
    prompt = f"""
    Tengo un episodio de podcast sobre el Puerto de Dakhla Atlantique. Aquí está su
    información tal cual la proporciona la plataforma del podcast:

    - Programa: {episodio['podcast']}
    - Título: {episodio['titulo']}
    - Fecha: {episodio['fecha']}
    - Descripción / Notas: {episodio['descripcion']}

    Tradúcela al castellano si está en otro idioma y redacta, ÚNICAMENTE a partir de
    esta información (sin añadir datos, opiniones ni análisis que no estén aquí):
    1. Un titular corto e informativo en castellano.
    2. Un resumen breve (2 o 3 frases) con los puntos clave de la descripción.

    Responde SOLO con estas 2 líneas de texto plano, sin ningún formato Markdown ni HTML
    y sin repetir el nombre del programa ni el enlace (yo los añadiré después):
    Línea 1: el titular
    Línea 2: el resumen
    """
    texto = preguntar_ia(prompt)
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
    """Genera un resumen ejecutivo global diario utilizando la IA."""
    prompt = f"""
    A continuación tienes una lista de titulares recopilados HOY sobre el Puerto de Dakhla Atlantique.

    Redacta un resumen breve (máximo 2 párrafos) en español que sintetice ÚNICAMENTE
    la información contenida en esos titulares.

    Reglas estrictas:
    - No añadas datos, cifras, opiniones, análisis ni interpretaciones geopolíticas que
      no estén explícitamente en los titulares de abajo.
    - No completes información con lo que sepas por tu cuenta sobre el tema.
    - Limítate a resumir de forma neutra y factual qué se ha publicado hoy.
    - Responde solo con texto plano, sin Markdown ni HTML.

    Titulares de hoy:
    {texto_noticias}
    """
    texto = preguntar_ia(prompt)
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
                    "link": resolver_url_real(link),
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
                    "link": resolver_url_real(link),
                    "fecha": datetime.now(ZONA_CANARIAS).strftime("%Y-%m-%d")
                })
        except Exception as e:
            print(f"Error al rastrear radio ({fuente}): {e}")

    return audios_nuevos


# ==========================================
# CONSTRUCCIÓN DEL REPORTE (nuevo diseño)
# ==========================================
NUMEROS_EMOJI = ["0️⃣", "1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]

# Marca de izquierda-a-derecha (invisible): al ponerla delante de una línea que
# empieza en árabe, obliga a Telegram a alinear esa línea a la izquierda
# (como el resto del reporte) sin cambiar el sentido de lectura del árabe en sí.
LRM = "\u200e"


def numero_emoji(n):
    """Devuelve el emoji de número correspondiente (1️⃣, 2️⃣...); para más de 10, usa '11.', '12.', etc."""
    if 0 <= n <= 10:
        return NUMEROS_EMOJI[n]
    return f"{n}."


def linea_item(numero, titulo, link, idioma=None):
    """Construye una línea con el formato: [número emoji] Titular 🔗LINK.
    Si el idioma es Árabe, se antepone la marca invisible que fuerza la
    alineación a la izquierda de toda la línea."""
    prefijo = LRM if idioma == "Árabe" else ""
    return f"{prefijo}{numero_emoji(numero)} {esc(titulo)} 🔗{enlace_html('LINK', link)}\n"


def construir_bloque_prensa(noticias):
    """📰 Prensa Escrita Internacional, agrupada por idioma con su bandera.
    Todos los idiomas se muestran siempre, aunque no haya noticias en alguno."""
    bloque = "📰 <b>Prensa Escrita Internacional</b>\n\n"

    for idioma in IDIOMAS_ORDEN:
        items = [n for n in noticias if n["idioma"] == idioma]
        bandera = BANDERA_IDIOMA.get(idioma, "🌐")
        bloque += f"{bandera} <b>{esc(idioma)}</b>\n"
        if not items:
            bloque += "• No hay noticias\n\n"
            continue
        for i, n in enumerate(items, start=1):
            bloque += linea_item(i, n["titulo"], n["link"], idioma=idioma)
        bloque += "\n"

    return bloque.rstrip() + "\n"


def construir_bloque_podcasts_radio(radios, podcasts_nuevos):
    """🎙️📻 Podcasts & Radio unificados en una sola sección, numerados."""
    bloque = "🎙️📻 <b>Podcasts & Radio</b>\n\n"
    contador = 0

    for r in radios:
        contador += 1
        bloque += linea_item(contador, r["titulo"], r["link"])

    for pod in podcasts_nuevos:
        resumen_pod = procesar_podcast_con_ia(pod)
        if resumen_pod:
            contador += 1
            bloque += f"{numero_emoji(contador)} {resumen_pod}\n"
        else:
            contador += 1
            bloque += linea_item(contador, pod["titulo"], pod["url"])

    if contador == 0:
        bloque += "• No hay noticias\n"

    return bloque.rstrip() + "\n"


def construir_bloque_youtube(videos):
    """📺 YouTube & Vídeos, numerados."""
    bloque = "📺 <b>YouTube & Vídeos</b>\n\n"

    if not videos:
        return bloque + "• No hay noticias\n"

    for i, v in enumerate(videos, start=1):
        bloque += linea_item(i, v["titulo"], v["link"])

    return bloque.rstrip() + "\n"


def construir_bloque_resumen_ia(texto_para_ia):
    """🤖✨ Resumen Diario de la IA, sintetizando todo lo recopilado en el día."""
    bloque = "🤖✨ <b>Resumen Diario de la IA</b> ✨🤖\n\n"

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

    # 1. Cargar historial (README para el archivo humano + registro.json para
    # los datos estructurados) y comprobar que no se haya enviado ya el
    # reporte de hoy (por ejemplo, si las dos ejecuciones programadas caen
    # accidentalmente en la misma hora de Canarias durante el cambio de horario).
    contenido_readme = cargar_readme()
    registro_json = cargar_registro_json()

    ya_enviado_hoy = fecha_hoy in registro_json or f"### Registro {fecha_hoy}" in contenido_readme
    if not EJECUCION_MANUAL and ya_enviado_hoy:
        print(f"El reporte de hoy ({fecha_hoy}) ya se envió anteriormente. Se omite esta ejecución.")
        return

    if EJECUCION_MANUAL:
        urls_registradas = set()
        print("Ejecución manual: se ignora el filtro de 'ya registrado', para mostrar siempre la foto actual.")
    else:
        # Unimos las URLs ya vistas tanto del README histórico (por continuidad
        # con lo registrado antes de tener registro.json) como del propio JSON.
        urls_registradas = cargar_urls_registradas(contenido_readme) | extraer_urls_registro_json(registro_json)
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
    reporte += "🤖 Informe generado por Mamé el Bot 🤖"

    # 5. Enviar a Telegram. Si falla de verdad (no solo "sin novedades"),
    # hacemos que la ejecución de GitHub Actions se marque como fallida
    # para que llegue la notificación de aviso correspondiente.
    envio_ok = enviar_telegram(reporte)

    # 6a. Construir la versión estructurada del día para registro.json: una
    # entrada por cada noticia/vídeo/radio/podcast, con sus datos "en limpio"
    # (sin HTML), para que telegram_bot.py pueda leerlos de forma robusta sin
    # tener que analizar texto con expresiones regulares.
    items_estructurados = []
    for n in noticias:
        items_estructurados.append({
            "categoria": "Prensa", "idioma": n["idioma"], "etiqueta": n["idioma"],
            "titular": n["titulo"], "link": n["link"]
        })
    for r in radios:
        items_estructurados.append({
            "categoria": "Podcasts y Radio", "etiqueta": r["fuente"],
            "titular": r["titulo"], "link": r["link"]
        })
    for pod in podcasts_nuevos:
        items_estructurados.append({
            "categoria": "Podcasts y Radio", "etiqueta": pod["podcast"],
            "titular": pod["titulo"], "link": pod["url"]
        })
    for v in videos:
        items_estructurados.append({
            "categoria": "YouTube", "etiqueta": "Vídeo",
            "titular": v["titulo"], "link": v["link"]
        })

    registro_json[fecha_hoy] = {"items": items_estructurados}
    guardar_registro_json(registro_json)

    # 6b. Actualizar historial en README.md (se guarda igualmente, para no
    # perder lo recopilado aunque el envío a Telegram haya fallado).
    # Se envuelve cada día en una sección plegable de GitHub (<details>) para
    # que el README no se convierta en un scroll interminable; la línea
    # "### Registro {fecha}" se mantiene intacta dentro para que quede como
    # copia legible de referencia (los datos "de verdad" ya viven en el JSON).
    with open("README.md", "a", encoding="utf-8") as f:
        f.write(f"\n\n<details>\n<summary>📅 <b>Registro {fecha_hoy}</b> — pulsa para ver el reporte completo</summary>\n\n")
        f.write(f"### Registro {fecha_hoy}\n")
        f.write(reporte)
        f.write("\n\n</details>\n")

    if not envio_ok:
        print("El envío a Telegram ha fallado. Marcando la ejecución como fallida.")
        sys.exit(1)

    print("¡Reporte enviado y README.md actualizado con éxito!")


if __name__ == "__main__":
    main()
