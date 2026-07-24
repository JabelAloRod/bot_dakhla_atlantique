import os
import html
import datetime
import requests
import feedparser
from deep_translator import GoogleTranslator

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")

EMOJIS_NUMEROS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
traductor = GoogleTranslator(source='auto', target='es')

# Feeds RSS de Google News por idioma
RSS_FEEDS = {
    "arabe": "https://news.google.com/rss/search?q=%D9%85%D9%8A%D9%8A%D8%A7%20%D8%A7%D9%84%D8%AF%D8%AE%D9%84%D8%A9%20%D8%A7%D9%84%D8%A3%D8%B7%D9%84%D8%B3%D9%8A&hl=ar&gl=MA&ceid=MA:ar",
    "frances": "https://news.google.com/rss/search?q=%22Nouveau%20port%20Dakhla%20Atlantique%22%20OR%20%22Port%20Dakhla%20Atlantique%22&hl=fr&gl=FR&ceid=FR:fr",
    "espanol": "https://news.google.com/rss/search?q=%22Puerto%20de%20Dakhla%20Atlantique%22%20OR%20%22Muelle%20de%20Dakhla%22&hl=es&gl=ES&ceid=ES:es",
    "ingles": "https://news.google.com/rss/search?q=%22Dakhla%20Atlantique%20Port%22&hl=en-US&gl=US&ceid=US:en"
}

# ---------------------------------------------------------
# 2. FUNCIONES DE EXTRACCIÓN
# ---------------------------------------------------------
def obtener_noticias_rss(url_rss):
    """Extrae noticias de un Feed RSS y traduce titulares."""
    feed = feedparser.parse(url_rss)
    resultados = []
    
    for entry in feed.entries[:5]:  # Tomamos las 5 más recientes por idioma
        titular_orig = entry.title
        medio = entry.source.title if hasattr(entry, 'source') else "Medio Digital"
        url = entry.link
        
        try:
            titular_es = traductor.translate(titular_orig)
        except Exception:
            titular_es = titular_orig

        resultados.append({
            "medio": medio,
            "titular_orig": titular_orig,
            "titular_es": titular_es,
            "url": url
        })
    return resultados

def obtener_videos_youtube():
    """Consulta la API de YouTube para vídeos recientes sobre Dakhla Atlantique."""
    if not YOUTUBE_API_KEY:
        return []
        
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": "Dakhla Atlantique Port",
        "type": "video",
        "order": "date",
        "maxResults": 3,
        "key": YOUTUBE_API_KEY
    }
    
    videos = []
    try:
        res = requests.get(url, params=params).json()
        for item in res.get("items", []):
            snippet = item["snippet"]
            titular_orig = snippet["title"]
            url_video = f"https://www.youtube.com/watch?v={item['id']['videoId']}"
            canal = snippet["channelTitle"]
            
            try:
                titular_es = traductor.translate(titular_orig)
            except Exception:
                titular_es = titular_orig

            videos.append({
                "medio": f"YouTube ({canal})",
                "titular_orig": titular_orig,
                "titular_es": titular_es,
                "url": url_video
            })
    except Exception as e:
        print(f"Error consultando YouTube API: {e}")
        
    return videos

# ---------------------------------------------------------
# 3. CONSTRUCCIÓN Y ENVÍO A TELEGRAM (SOPORTA MULTIPLES CHAT IDs)
# ---------------------------------------------------------
def enviar_reporte_telegram(noticias):
    fecha_str = datetime.datetime.now().strftime("%d/%m/%Y")
    msg = f"<b>📅 Búsqueda del {fecha_str} — Puerto de Dakhla Atlantique</b>\n\n"

    # 1. Árabe
    msg += "<b>1. Medios en árabe 🇲🇦</b>\n"
    if noticias["arabe"]:
        for idx, item in enumerate(noticias["arabe"]):
            num = EMOJIS_NUMEROS[idx] if idx < len(EMOJIS_NUMEROS) else f"{idx+1}️⃣"
            tit = html.escape(item['titular_orig'])
            med = html.escape(item['medio'])
            msg += f"\u200E{num} {tit} - <i>{med}</i> - <a href=\"{item['url']}\">Link</a>\n"
    else:
        msg += "No hay noticias nuevas hoy\n"
    msg += "\n"

    # 2. Francés
    msg += "<b>2. Medios de habla francesa 🇫🇷</b>\n"
    if noticias["frances"]:
        for idx, item in enumerate(noticias["frances"]):
            num = EMOJIS_NUMEROS[idx] if idx < len(EMOJIS_NUMEROS) else f"{idx+1}️⃣"
            tit = html.escape(item['titular_orig'])
            med = html.escape(item['medio'])
            msg += f"{num} {tit} - <i>{med}</i> - <a href=\"{item['url']}\">Link</a>\n"
    else:
        msg += "No hay noticias nuevas hoy\n"
    msg += "\n"

    # 3. Español
    msg += "<b>3. Medios en español 🇪🇸</b>\n"
    if noticias["espanol"]:
        for idx, item in enumerate(noticias["espanol"]):
            num = EMOJIS_NUMEROS[idx] if idx < len(EMOJIS_NUMEROS) else f"{idx+1}️⃣"
            tit = html.escape(item['titular_orig'])
            med = html.escape(item['medio'])
            msg += f"{num} {tit} - <i>{med}</i> - <a href=\"{item['url']}\">Link</a>\n"
    else:
        msg += "No hay noticias nuevas hoy\n"
    msg += "\n"

    # 4. Inglés / YouTube
    msg += "<b>4. Medios en lengua inglesa y vídeos 🇬🇧/🇺🇸/🎥</b>\n"
    combinados_ingles = noticias["ingles"] + noticias["youtube"]
    if combinados_ingles:
        for idx, item in enumerate(combinados_ingles):
            num = EMOJIS_NUMEROS[idx] if idx < len(EMOJIS_NUMEROS) else f"{idx+1}️⃣"
            tit = html.escape(item['titular_orig'])
            med = html.escape(item['medio'])
            msg += f"{num} {tit} - <i>{med}</i> - <a href=\"{item['url']}\">Link</a>\n"
    else:
        msg += "No hay novedades hoy\n"

    # Separar la cadena de TELEGRAM_CHAT_ID por comas si hay más de uno
    lista_chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()] if TELEGRAM_CHAT_ID else []
    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for chat_id in lista_chat_ids:
        payload = {
            "chat_id": chat_id,
            "text": msg,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        }
        try:
            res = requests.post(url_api, json=payload)
            if res.status_code != 200:
                print(f"Error enviando mensaje al CHAT_ID {chat_id}: {res.text}")
        except Exception as e:
            print(f"Excepción enviando mensaje al CHAT_ID {chat_id}: {e}")

# ---------------------------------------------------------
# 4. ACTUALIZACIÓN DEL REGISTRO HISTÓRICO EN GITHUB (README.md)
# ---------------------------------------------------------
def actualizar_registro_markdown(noticias):
    """Agrega las noticias del día traducidas al castellano en el README.md."""
    fecha_hoy = datetime.datetime.now()
    anio_str = fecha_hoy.strftime("%Y")
    mes_str = fecha_hoy.strftime("%m - %B")
    dia_str = fecha_hoy.strftime("%d/%m/%Y")

    lineas_tabla = []
    for idioma, lista in noticias.items():
        for item in lista:
            lineas_tabla.append(
                f"| {idioma.capitalize()} | {item['medio']} | {item['titular_es']} | [Enlace]({item['url']}) |"
            )

    if not lineas_tabla:
        return

    bloque_nuevo = f"\n### 📅 {dia_str}\n\n| Idioma | Medio | Titular (Traducido) | Link |\n|---|---|---|---|\n"
    bloque_nuevo += "\n".join(lineas_tabla) + "\n"

    archivo_readme = "README.md"
    contenido_previo = ""
    if os.path.exists(archivo_readme):
        with open(archivo_readme, "r", encoding="utf-8") as f:
            contenido_previo = f.read()

    nuevo_contenido = f"# 📌 Registro Histórico Dakhla Atlantique\n\n## {anio_str}\n### 📂 Mes: {mes_str}\n" + bloque_nuevo + "\n" + contenido_previo
    
    with open(archivo_readme, "w", encoding="utf-8") as f:
        f.write(nuevo_contenido)

# ---------------------------------------------------------
# 5. EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Iniciando rastreo de noticias...")
    
    noticias_todas = {
        "arabe": obtener_noticias_rss(RSS_FEEDS["arabe"]),
        "frances": obtener_noticias_rss(RSS_FEEDS["frances"]),
        "espanol": obtener_noticias_rss(RSS_FEEDS["espanol"]),
        "ingles": obtener_noticias_rss(RSS_FEEDS["ingles"]),
        "youtube": obtener_videos_youtube()
    }
    
    print("Enviando reporte a Telegram...")
    enviar_reporte_telegram(noticias_todas)
    
    print("Actualizando registro Markdown en GitHub...")
    actualizar_registro_markdown(noticias_todas)
    
    print("¡Proceso completado con éxito!")
