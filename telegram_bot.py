import os
import re
import logging
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Configuración de logs
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Configuración de variables de entorno
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "tu_usuario/bot-dakhla-atlantique")
README_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/README.md"

FLAG_MAP = {
    "arabe": "🇲🇦",
    "árabe": "🇲🇦",
    "frances": "🇫🇷",
    "francés": "🇫🇷",
    "espanol": "🇪🇸",
    "español": "🇪🇸",
    "ingles": "🇬🇧",
    "inglés": "🇬🇧",
}

# Diccionario para convertir números de mes a texto
MESES_NOMBRE = {
    "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
    "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
    "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
}

# Detecta líneas tipo:
#   • [Español] [Título de la noticia](https://enlace)
#   • [Título del vídeo](https://enlace)          <- sin etiqueta de idioma/fuente
LINEA_BULLET_RE = re.compile(
    r'^•\s*(?:\[(?P<tag>[^\]]+)\]\s*)?\[(?P<titulo>[^\]]+)\]\((?P<link>https?://[^\)]+)\)'
)


def limpiar_texto_markdown(texto: str) -> str:
    """Elimina o limpia caracteres que rompen el formato Markdown básico de Telegram"""
    if not texto:
        return ""
    texto = texto.replace("[", "(").replace("]", ")")
    texto = texto.replace("*", "").replace("_", "").replace("`", "")
    return texto.strip()


def obtener_datos_readme() -> dict:
    """
    Descarga el README.md del repositorio y extrae las noticias/vídeos/radios
    registrados cada día. Entiende el formato real que genera main.py:

    ## 2026
    ### 📂 Mes: 07
    ### Registro 2026-07-27
    📰 **NOTICIAS DE PRENSA:**
    • [Español] [Título](enlace)
    🎥 **VÍDEOS DESTACADOS:**
    • [Título](enlace)
    """
    try:
        response = requests.get(README_RAW_URL, timeout=10)
        if response.status_code != 200:
            logger.error(f"Error al obtener README: HTTP status {response.status_code}")
            return {}
        content = response.text
    except Exception as e:
        logger.error(f"Excepción durante la descarga del README: {e}")
        return {}

    datos = {}
    current_year = None
    current_month = None
    current_date = None

    for line in content.splitlines():
        line_str = line.strip()

        # Detectar Año: ## 2026
        year_match = re.search(r'##\s*(\d{4})', line_str)
        if year_match:
            current_year = year_match.group(1)
            datos.setdefault(current_year, {})
            continue

        # Detectar Mes: acepta 1 o 2 dígitos y normaliza a 2 dígitos
        month_match = re.search(r'###.*Mes:?\s*(\d{1,2})', line_str, re.IGNORECASE)
        if month_match and current_year:
            current_month = month_match.group(1).zfill(2)
            datos[current_year].setdefault(current_month, [])
            continue

        # Detectar Fecha: "### Registro 2026-07-27" (formato AAAA-MM-DD real del bot)
        date_match = re.search(r'###\s*Registro\s+(\d{4}-\d{2}-\d{2})', line_str)
        if date_match:
            current_date = date_match.group(1)
            continue

        # Detectar entradas con viñeta: noticias, radios y vídeos
        bullet_match = LINEA_BULLET_RE.match(line_str)
        if bullet_match and current_year and current_month:
            tag = bullet_match.group("tag")
            titulo = bullet_match.group("titulo")
            link = bullet_match.group("link")

            if tag:
                bandera = FLAG_MAP.get(tag.strip().lower(), "🌐")
                etiqueta = tag.strip()
            else:
                # Sin etiqueta -> es un vídeo de YouTube
                bandera = "🔴"
                etiqueta = "Vídeo"

            datos[current_year][current_month].append({
                "fecha": current_date or "Sin fecha",
                "bandera": bandera,
                "etiqueta": etiqueta,
                "titular": limpiar_texto_markdown(titulo),
                "link": link
            })

    return datos


async def comando_registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /registro o /historico: Muestra el primer nivel (Años)"""
    datos = obtener_datos_readme()
    if not datos:
        await update.message.reply_text(
            "⚠️ No se pudo acceder al registro histórico en este momento. Inténtalo más tarde."
        )
        return

    keyboard = []
    for year in sorted(datos.keys(), reverse=True):
        keyboard.append([InlineKeyboardButton(f"📂 Año {year}", callback_data=f"year_{year}")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "📌 *Registro Histórico Dakhla Atlantique*\n\nSelecciona un año para consultar los meses disponibles:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def manejar_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestor principal para todos los botones interactivos"""
    query = update.callback_query
    await query.answer()
    data = query.data
    datos = obtener_datos_readme()

    if not datos:
        await query.edit_message_text("⚠️ No se pudo cargar la información del registro.")
        return

    # 1. Nivel Año -> Mostrar Meses con nombre formateado y contador
    if data.startswith("year_"):
        year = data.split("_")[1]
        meses = datos.get(year, {})

        keyboard = []
        for month in sorted(meses.keys(), reverse=True):
            total_noticias = len(meses[month])
            clave_mes = str(month).zfill(2)
            nombre_mes = MESES_NOMBRE.get(clave_mes, f"Mes {month}")

            keyboard.append([
                InlineKeyboardButton(
                    f"📂 {nombre_mes} ({total_noticias} noticias)",
                    callback_data=f"month_{year}_{month}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 Volver a Años", callback_data="home")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"📅 *AÑO {year}*\n\nSelecciona un mes para ver el desglose:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    # 2. Nivel Mes -> Mostrar Listado Simplificado
    elif data.startswith("month_"):
        _, year, month = data.split("_")
        noticias = datos.get(year, {}).get(month, [])

        clave_mes = str(month).zfill(2)
        nombre_mes_txt = MESES_NOMBRE.get(clave_mes, month).upper()

        if not noticias:
            keyboard = [[InlineKeyboardButton(f"🔙 Volver a Meses ({year})", callback_data=f"year_{year}")]]
            await query.edit_message_text(
                "No hay noticias registradas en este mes.",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return

        bloques = []
        texto_actual = f"📅 *REGISTRO — {nombre_mes_txt} {year}*\n\n"
        fecha_actual = ""

        for item in noticias:
            linea_fecha = ""
            if item["fecha"] != fecha_actual:
                fecha_actual = item["fecha"]
                linea_fecha = f"\n🗓️ *{fecha_actual}*\n"

            linea_noticia = f"{item['bandera']} {item['titular']} — [Noticia]({item['link']})\n"
            bloque_temp = linea_fecha + linea_noticia

            if len(texto_actual) + len(bloque_temp) > 3800:
                bloques.append(texto_actual)
                texto_actual = f"📅 *REGISTRO — {nombre_mes_txt} {year} (Cont.)*\n\n" + bloque_temp
            else:
                texto_actual += bloque_temp

        bloques.append(texto_actual)

        keyboard = [[InlineKeyboardButton(f"🔙 Volver a Meses ({year})", callback_data=f"year_{year}")]]
        reply_markup = InlineKeyboardMarkup(keyboard)

        if len(bloques) > 1:
            for b in bloques[:-1]:
                await query.message.reply_text(b, parse_mode="Markdown", disable_web_page_preview=True)

            await query.message.reply_text(
                bloques[-1],
                reply_markup=reply_markup,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )
        else:
            await query.edit_message_text(
                bloques[0],
                reply_markup=reply_markup,
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

    # 3. Volver al Menú Principal (Inicio)
    elif data == "home":
        keyboard = [
            [InlineKeyboardButton(f"📂 Año {y}", callback_data=f"year_{y}")]
            for y in sorted(datos.keys(), reverse=True)
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            "📌 *Registro Histórico Dakhla Atlantique*\n\nSelecciona un año:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )


def main():
    if not TELEGRAM_TOKEN:
        logger.error("No se ha configurado la variable de entorno TELEGRAM_TOKEN")
        return

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers para comandos y botones
    app.add_handler(CommandHandler("registro", comando_registro))
    app.add_handler(CommandHandler("historico", comando_registro))
    app.add_handler(CallbackQueryHandler(manejar_botones))

    logger.info("Bot iniciado correctamente y escuchando peticiones...")
    app.run_polling()


if __name__ == "__main__":
    main()
