import os
import re
import requests
import feedparser
from datetime import datetime, timedelta
import google.generativeai as genai
from podcasts import buscar_podcasts_dakhla

# ==========================================
# CONFIGURACIÓN Y VARIABLES DE ENTORNO
# ==========================================
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")
YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")

# Configuración de Gemini AI
if GEMINI_API_KEY:
    genai.configure(api_key=GEMINI_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-1.5-flash')
else:
    gemini_model = None

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

def enviar_telegram(mensaje):
    """Envía el reporte a todos los IDs configurados en TELEGRAM_CHAT_ID (separados por coma)."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Error: No se han configurado los tokens de Telegram.")
        return

    chat_ids = [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]
    url_base = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    MAX_LENGTH = 4000

    for chat_id in chat_ids:
        for i in range(0, len(mensaje), MAX_LENGTH):
            sub_mensaje = mensaje[i:i+MAX_LENGTH]
            payload = {
                "chat_id": chat_id,
                "text": sub_mensaje,
                "parse_mode": "Markdown",
                "disable_web_page_preview": False
            }
            try:
                res = requests.post(url_base, json=payload, timeout=10)
                if res.status_code != 200:
                    print(f"Error enviando a Telegram (Chat ID {chat_id}): {res.text}")
                else:
                    print(f"Reporte enviado con éxito al Chat ID: {chat_id}")
            except Exception as e:
                print(f"Excepción al conectar con Telegram (Chat ID {chat_id}): {e}")

def procesar_podcast_con_gemini(episodio):
    """Aplica Gemini para traducir y resumir un episodio de podcast al castellano."""
    if not gemini_model:
        return None
        
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
    
    Formato de salida requerido (Markdown de Telegram):
    🎙️ **[PODCAST] Titular en español**
    • **Programa:** {episodio['podcast']}
    • **Resumen:** Texto resumido
    🔗 [Escuchar episodio]({episodio['url']})
    """
    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error procesando podcast con Gemini: {e}")
        return None

def generar_resumen_general_ia(texto_noticias):
    """Genera un resumen ejecutivo global diario utilizando Gemini."""
    if not gemini_model:
        return ""
        
    prompt = f"""
    Sintetiza las siguientes novedades de hoy sobre el Puerto de Dakhla Atlantique en un resumen ejecutivo breve (máximo 2 párrafos) en español.
    Destaca los avances en infraestructura, licitaciones o impacto geopolítico si los hay.
    
    Noticias del día:
    {texto_noticias}
    """
    try:
        response = gemini_model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Error generando resumen general con Gemini: {e}")
        return ""

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
                link = entry.link
                if link not in urls_previas:
                    urls_previas.add(link)
                    noticias_nuevas.append({
                        "idioma": idioma,
                        "titulo": entry.title,
                        "link": link,
                        "fecha": datetime.now().strftime("%Y-%m-%d")
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
                        "fecha": datetime.now().strftime("%Y-%m-%d")
                    })
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
                link = entry.link
                if link not in urls_previas:
                    urls_previas.add(link)
                    audios_nuevos.append({
                        "fuente": fuente,
                        "titulo": entry.title,
                        "link": link,
                        "fecha": datetime.now().strftime("%Y-%m-%d")
                    })
        except Exception as e:
            print(f"Error al rastrear radio ({fuente}): {e}")
            
    return audios_nuevos

# ==========================================
# FLUJO PRINCIPAL
# ==========================================
def main():
    print("Iniciando rastreo del Puerto de Dakhla Atlantique...")
    
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

    # 3. Construir mensaje con secciones separadas
    fecha_hoy = datetime.now().strftime("%Y-%m-%d")
    reporte = f"🚢 **REPORTE DIARIO: PUERTO DE DAKHLA ATLANTIQUE** ({fecha_hoy})\n\n"
    
    secciones = []
    texto_para_ia = ""

    # Bloque de Prensa
    if noticias:
        block_noticias = "📰 **NOTICIAS DE PRENSA:**\n"
        for n in noticias:
            block_noticias += f"• [{n['idioma']}] [{n['titulo']}]({n['link']})\n"
            texto_para_ia += f"- {n['titulo']}\n"
        secciones.append(block_noticias)

    # Bloque de Youtube
    if videos:
        block_videos = "🎥 **VÍDEOS DESTACADOS:**\n"
        for v in videos:
            block_videos += f"• [{v['titulo']}]({v['link']})\n"
            texto_para_ia += f"- {v['titulo']}\n"
        secciones.append(block_videos)

    # Bloque de Radio
    if radios:
        block_radios = "📻 **BOLETINES DE RADIO Y EMISIONES:**\n"
        for r in radios:
            block_radios += f"• [{r['fuente']}] [{r['titulo']}]({r['link']})\n"
            texto_para_ia += f"- Radio ({r['fuente']}): {r['titulo']}\n"
        secciones.append(block_radios)

    # Bloque de Podcasts
    if podcasts_nuevos:
        block_podcasts = "🎙️ **PODCASTS Y ANÁLISIS:**\n"
        for pod in podcasts_nuevos:
            resumen_pod = procesar_podcast_con_gemini(pod)
            if resumen_pod:
                block_podcasts += resumen_pod + "\n"
                texto_para_ia += f"- Podcast: {pod['titulo']} ({pod['descripcion'][:100]})\n"
            else:
                block_podcasts += f"• **{pod['podcast']}**: [{pod['titulo']}]({pod['url']})\n"
        secciones.append(block_podcasts)

    # Unir todas las secciones principales con un separador visual limpio
    reporte += "\n\n───────────────────\n\n".join(secciones)
    reporte += "\n\n"

    # Resumen General de IA y Firma
    resumen_global = generar_resumen_general_ia(texto_para_ia)
    if resumen_global:
        reporte += "───────────────────\n\n"
        reporte += "🧠 **Resumen del Día:**\n"
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
