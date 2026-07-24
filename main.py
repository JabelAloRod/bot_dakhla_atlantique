import os
import re
import html
import datetime
import requests
import feedparser
import difflib
import google.generativeai as genai
from deep_translator import GoogleTranslator

# ---------------------------------------------------------
# 1. CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ---------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

EMOJIS_NUMEROS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]
traductor = GoogleTranslator(source='auto', target='es')

if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)

RSS_FEEDS = {
    "arabe": "https://news.google.com/rss/search?q=%D9%85%D9%8A%D9%86%D8%A7%D8%A1%20%D8%A7%D9%84%D8%AF%D8%AE%D9%84%D8%A9%20%D8%A7%D9%84%D8%A3%D8%B7%D9%84%D8%B3%D9%8A%20when:1d&hl=ar&gl=MA&ceid=MA:ar",
    "frances": "https://news.google.com/rss/search?q=(%22Nouveau%20port%20Dakhla%20Atlantique%22%20OR%20%22Port%20Dakhla%20Atlantique%22)%20when:1d&hl=fr&gl=FR&ceid=FR:fr",
    "espanol": "https://news.google.com/rss/search?q=(%22Puerto%20de%20Dakhla%20Atlantique%22%20OR%20%22Muelle%20de%20Dakhla%22)%20when:1d&hl=es&gl=ES&ceid=ES:es",
    "ingles": "https://news.google.com/rss/search?q=%22Dakhla%20Atlantique%20Port%22%20when:1d&hl=en-US&gl=US&ceid=US:en"
}

# ---------------------------------------------------------
# 2. SISTEMA ANTI-DUPLICADOS
# ---------------------------------------------------------
def cargar_historial_previo():
    urls = set()
    titulares = set()
    archivo_readme = "README.md"
    
    if os.path.exists(archivo_readme):
        with open(archivo_readme, "r", encoding="utf-8") as f:
            content = f.read()
            urls.update(re.findall(r'\[.*?\]\((https?://.*?)\)', content))
            titulares_match = re.findall(r'\|\s*[^\|]+\s*\|\s*[^\|]+\s*\|\s*([^\|]+)\s*\|', content)
            titulares.update([t.strip() for t in titulares_match])
            
    return urls, titulares

def es_muy_similar(texto1, texto2, umbral=0.85):
    return difflib.SequenceMatcher(None, texto1.lower(), texto2.lower()).ratio() > umbral

# ---------------------------------------------------------
# 3. FUNCIONES DE EXTRACCIÓN
# ---------------------------------------------------------
def obtener_noticias_rss(url_rss, urls_vistas, titulares_vistos):
    try:
        req = requests.get(url_rss, timeout=10)
        req.raise_for_status()
        feed = feedparser.parse(req.content)
    except Exception as e:
        print(f"⚠️ Error cargando RSS: {e}")
        return []

    resultados = []
    for entry in feed.entries:
        if len(resultados) >= 5:
            break
            
        url = entry.link
        titular_orig = entry.title
        medio = entry.source.title if hasattr(entry, 'source') else "Medio Digital"
        
        if url in urls_vistas:
            continue
            
        if any(es_muy_similar(titular_orig, t_visto) for t_visto in titulares_vistos):
            continue

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
        
        urls_vistas.add(url)
        titulares_vistos.add(titular_orig)
        
    return resultados

def obtener_videos_youtube(urls_vistas, titulares_vistos):
    if not YOUTUBE_API_KEY:
        return []
        
    hace_24h = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
    url = "https://www.googleapis.com/youtube/v3/search"
    params = {
        "part": "snippet",
        "q": "Dakhla Atlantique Port",
        "type": "video",
        "order": "date",
        "publishedAfter": hace_24h,
        "maxResults": 3,
        "key": YOUTUBE_API_KEY
    }
    
    videos = []
    try:
        res = requests.get(url, params=params, timeout=10)
        res.raise_for_status()
        data = res.json()
        
        for item in data.get("items", []):
            snippet = item["snippet"]
            titular_orig = snippet["title"]
            url_video = f"https://www.youtube.com/watch?v={item['id']['videoId']}"
            canal = snippet["channelTitle"]
            
            if url_video in urls_vistas:
                continue

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
            
            urls_vistas.add(url_video)
            
    except Exception as e:
        print(f"⚠️ Error consultando YouTube API: {e}")
        
    return videos

# ---------------------------------------------------------
# 4. RESUMEN CON INTELIGENCIA ARTIFICIAL (GEMINI)
# ---------------------------------------------------------
def generar_resumen_ia(noticias):
    if not GEMINI_API_KEY:
        return "\n\n<i>⚠️ Falta configurar GEMINI_API_KEY en Render para generar el resumen.</i>\n\nresumen creado por Mamé_el_bot"
        
    # Extraer todos los titulares traducidos al español para dárselos a la IA
    titulares = [item['titular_es'] for lista in noticias.values() for item in lista]
    
    if not titulares:
        return ""
        
    texto_titulares = "\n".join(f"- {t}" for t in titulares)
    
    prompt = f"""
    Eres un analista experto en infraestructuras y geopolítica.
    A continuación tienes los titulares de las noticias publicadas hoy sobre el 'Puerto Dakhla Atlantique':
    
    {texto_titulares}
    
    Genera un resumen global, breve y directo al grano (máximo 2 párrafos cortos) integrando la información de estos titulares.
    No inventes información, básate estrictamente en lo proporcionado.
    """
    
    try:
        # Usamos el modelo más rápido y eficiente
        model = genai.GenerativeModel('gemini-1.5-flash')
        respuesta = model.generate_content(prompt)
        resumen_texto = respuesta.text.strip()
        
        return f"\n\n<b>🧠 Resumen del Día:</b>\n{resumen_texto}\n\n<i>resumen creado por Mamé_el_bot</i>"
    except Exception as e:
        print(f"⚠️ Error al conectar con Gemini: {e}")
        return "\n\n<i>⚠️ Hubo un error al generar el resumen hoy.</i>\n\nresumen creado por Mamé_el_bot"

# ---------------------------------------------------------
# 5. CONSTRUCCIÓN Y ENVÍO A TELEGRAM
# ---------------------------------------------------------
def enviar_reporte_telegram(noticias):
    fecha_str = datetime.datetime.now().strftime("%d/%m/%Y")
    msg = f"<b>📅 Búsqueda del {fecha_str} — Puerto de Dakhla Atlantique (Últimas 24h)</b>\n\n"

    def agregar_bloque(titulo, clave_noticias):
        bloque = f"<b>{titulo}</b>\n"
        if noticias[clave_noticias]:
            for idx, item in enumerate(noticias[clave_noticias]):
                num = EMOJIS_NUMEROS[idx] if idx < len(EMOJIS_NUMEROS) else f"{idx+1}️⃣"
                tit = html.escape(item['titular_orig'])
                med = html.escape(item['medio'])
                bloque += f"\u200E{num} {tit} - <i>{med}</i> - <a href=\"{item['url']}\">Link</a>\n"
        else:
            bloque += "No hay noticias nuevas en las últimas 24h\n"
        return bloque + "\n"

    msg += agregar_bloque("1. Medios en árabe 🇲🇦", "arabe")
    msg += agregar_bloque("2. Medios de habla francesa 🇫🇷", "frances")
    msg += agregar_bloque("3. Medios en español 🇪🇸", "espanol")
    
    msg += "<b>4. Medios en lengua inglesa y vídeos 🇬🇧/🇺🇸/🎥</b>\n"
    combinados_ingles = noticias["ingles"] + noticias["youtube"]
    if combinados_ingles:
        for idx, item in enumerate(combinados_ingles):
            num = EMOJIS_NUMEROS[idx] if idx < len(EMOJIS_NUMEROS) else f"{idx+1}️⃣"
            tit = html.escape(item['titular_orig'])
            med = html.escape(item['medio'])
            msg += f"{num} {tit} - <i>{med}</i> - <a href=\"{item['url']}\">Link</a>\n"
    else:
        msg += "No hay novedades en las últimas 24h\n"

    # Generar y adjuntar el resumen IA al final del mensaje
    resumen = generar_resumen_ia(noticias)
    msg += resumen

    # División segura en trozos si supera los límites de Telegram
    trozos_mensaje = []
    texto_actual = ""
    for linea in msg.split("\n"):
        if len(texto_actual) + len(linea) > 3800:
            trozos_mensaje.append(texto_actual)
            texto_actual = linea + "\n"
        else:
            texto_actual += linea + "\n"
    if texto_actual:
        trozos_mensaje.append(texto_actual)

    lista_chat_ids = [cid.strip() for cid in TELEGRAM_CHAT_ID.split(",") if cid.strip()] if TELEGRAM_CHAT_ID else []
    url_api = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"

    for chat_id in lista_chat_ids:
        for trozo in trozos_mensaje:
            payload = {
                "chat_id": chat_id,
                "text": trozo,
                "parse_mode": "HTML",
                "disable_web_page_preview": True
            }
            try:
                res = requests.post(url_api, json=payload, timeout=10)
                if res.status_code != 200:
                    print(f"⚠️ Error enviando a {chat_id}: {res.text}")
            except Exception as e:
                print(f"⚠️ Excepción enviando a {chat_id}: {e}")

# ---------------------------------------------------------
# 6. ACTUALIZACIÓN DEL REGISTRO HISTÓRICO EN GITHUB
# ---------------------------------------------------------
def actualizar_registro_markdown(noticias):
    fecha_hoy = datetime.datetime.now()
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
    
    with open(archivo_readme, "a", encoding="utf-8") as f:
        f.write(bloque_nuevo)

# ---------------------------------------------------------
# 7. EJECUCIÓN PRINCIPAL
# ---------------------------------------------------------
if __name__ == "__main__":
    print("Iniciando rastreo de noticias (Últimas 24 horas)...")
    
    urls_vistas, titulares_vistos = cargar_historial_previo()
    
    noticias_todas = {
        "arabe": obtener_noticias_rss(RSS_FEEDS["arabe"], urls_vistas, titulares_vistos),
        "frances": obtener_noticias_rss(RSS_FEEDS["frances"], urls_vistas, titulares_vistos),
        "espanol": obtener_noticias_rss(RSS_FEEDS["espanol"], urls_vistas, titulares_vistos),
        "ingles": obtener_noticias_rss(RSS_FEEDS["ingles"], urls_vistas, titulares_vistos),
        "youtube": obtener_videos_youtube(urls_vistas, titulares_vistos)
    }
    
    total_nuevas = sum(len(lista) for lista in noticias_todas.values())
    
    if total_nuevas > 0:
        print(f"Se encontraron {total_nuevas} noticias NUEVAS. Enviando a Telegram...")
        enviar_reporte_telegram(noticias_todas)
        print("Actualizando registro Markdown en GitHub...")
        actualizar_registro_markdown(noticias_todas)
    else:
        print("No se encontraron noticias nuevas. Evitando hacer spam en Telegram y GitHub.")
        
    print("¡Proceso completado con éxito!")
