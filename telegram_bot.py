import os
import re
import io
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
import requests
from openpyxl import Workbook
from openpyxl.styles import Font, Alignment
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, InputFile, Update
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
    registrados cada día.
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

        # Detectar Fecha: "### Registro 2026-07-27"
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


TEXTO_AYUDA = (
    "🤖 *Bot Dakhla Atlantique — Ayuda e Información*\n\n"
    "📌 *Registro Histórico*\n"
    "Consulta el archivo histórico de noticias, navegando año a año y mes a mes.\n\n"
    "📤 *Exportar a Excel*\n"
    "Descarga en un archivo Excel (.xlsx) las noticias registradas de cualquier mes, "
    "con formato limpio y enlaces directos funcionales.\n\n"
    "El reporte diario se envía de forma totalmente automática todos los días a las "
    "08:00 hora de Canarias."
)


async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el panel de control interactivo estilo BotFather con los botones principales."""
    keyboard = [
        [InlineKeyboardButton("❓ /ayuda", callback_data="menu_ayuda")],
        [InlineKeyboardButton("📜 /registro-historico", callback_data="menu_historico")],
        [InlineKeyboardButton("📊 /exportar", callback_data="menu_exportar")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = "🎛️ *Panel de Control — Dakhla Atlantique*\n\nSelecciona una opción del menú:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(texto, reply_markup=reply_markup, parse_mode="Markdown")


async def comando_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /ayuda."""
    keyboard = [[InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(TEXTO_AYUDA, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(TEXTO_AYUDA, reply_markup=reply_markup, parse_mode="Markdown")


async def comando_registro(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /registro o /historico: Muestra el primer nivel (Años)."""
    datos = obtener_datos_readme()
    if not datos:
        msg = "⚠️ No se pudo acceder al registro histórico en este momento. Inténtalo más tarde."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    keyboard = []
    for year in sorted(datos.keys(), reverse=True):
        keyboard.append([InlineKeyboardButton(f"📂 Año {year}", callback_data=f"year_{year}")])
    
    keyboard.append([InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = "📌 *Registro Histórico Dakhla Atlantique*\n\nSelecciona un año para consultar los meses disponibles:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(texto, reply_markup=reply_markup, parse_mode="Markdown")


async def comando_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Comando /exportar: primer paso, elegir el año a exportar."""
    datos = obtener_datos_readme()
    if not datos:
        msg = "⚠️ No se pudo acceder al registro histórico en este momento. Inténtalo más tarde."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    keyboard = []
    for year in sorted(datos.keys(), reverse=True):
        keyboard.append([InlineKeyboardButton(f"📂 Año {year}", callback_data=f"exp_year_{year}")])
    
    keyboard.append([InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = "📤 *Exportar registro a Excel*\n\nSelecciona el año:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(texto, reply_markup=reply_markup, parse_mode="Markdown")


def generar_excel_mes(noticias, year, month) -> io.BytesIO:
    """Construye un archivo Excel (.xlsx) en memoria con las noticias de un mes."""
    libro = Workbook()
    hoja = libro.active
    hoja.title = f"{year}-{month}"

    encabezados = ["Fecha", "Categoría", "Titular", "Enlace"]
    hoja.append(encabezados)
    for celda in hoja[1]:
        celda.font = Font(bold=True)
        celda.alignment = Alignment(horizontal="center")

    for item in noticias:
        hoja.append([item["fecha"], item["etiqueta"], item["titular"], item["link"]])
        fila = hoja.max_row
        celda_enlace = hoja.cell(row=fila, column=4)
        celda_enlace.hyperlink = item["link"]
        celda_enlace.font = Font(color="0563C1", underline="single")

    hoja.column_dimensions["A"].width = 14
    hoja.column_dimensions["B"].width = 16
    hoja.column_dimensions["C"].width = 70
    hoja.column_dimensions["D"].width = 55
    hoja.freeze_panes = "A2"

    buffer_bytes = io.BytesIO()
    libro.save(buffer_bytes)
    buffer_bytes.seek(0)
    buffer_bytes.name = f"dakhla_registro_{year}-{month}.xlsx"
    return buffer_bytes


async def manejar_botones(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Gestor principal para todos los botones interactivos"""
    query = update.callback_query
    await query.answer()
    data = query.data

    # Accesos rápidos desde el menú principal BotFather
    if data == "menu_main":
        await comando_menu(update, context)
        return
    elif data == "menu_ayuda":
        await comando_ayuda(update, context)
        return
    elif data == "menu_historico":
        await comando_registro(update, context)
        return
    elif data == "menu_exportar":
        await comando_exportar(update, context)
        return

    datos = obtener_datos_readme()
    if not datos:
        await query.edit_message_text("⚠️ No se pudo cargar la información del registro.")
        return

    # 1. Nivel Año -> Mostrar Meses con contador
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

        keyboard.append([InlineKeyboardButton("🔙 Volver a Años", callback_data="menu_historico")])
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

    # 4. Exportar - Nivel Año -> Mostrar Meses exportables
    elif data.startswith("exp_year_"):
        year = data.split("_")[2]
        meses = datos.get(year, {})

        keyboard = []
        for month in sorted(meses.keys(), reverse=True):
            total_noticias = len(meses[month])
            clave_mes = str(month).zfill(2)
            nombre_mes = MESES_NOMBRE.get(clave_mes, f"Mes {month}")

            keyboard.append([
                InlineKeyboardButton(
                    f"📤 {nombre_mes} ({total_noticias} noticias)",
                    callback_data=f"exp_month_{year}_{month}"
                )
            ])

        keyboard.append([InlineKeyboardButton("🔙 Volver a Años", callback_data="menu_exportar")])
        reply_markup = InlineKeyboardMarkup(keyboard)

        await query.edit_message_text(
            f"📤 *Exportar — AÑO {year}*\n\nSelecciona el mes a exportar:",
            reply_markup=reply_markup,
            parse_mode="Markdown"
        )

    # 5. Exportar - Nivel Mes -> Generar y enviar el Excel
    elif data.startswith("exp_month_"):
        _, _, year, month = data.split("_")
        noticias = datos.get(year, {}).get(month, [])

        if not noticias:
            await query.answer("No hay noticias registradas en ese mes.", show_alert=True)
            return

        archivo_excel = generar_excel_mes(noticias, year, month)
        clave_mes = str(month).zfill(2)
        nombre_mes = MESES_NOMBRE.get(clave_mes, month)

        await context.bot.send_document(
            chat_id=query.message.chat_id,
            document=InputFile(archivo_excel, filename=archivo_excel.name),
            caption=f"📤 Registro de {nombre_mes} {year} ({len(noticias)} noticias)"
        )
        await query.answer("Archivo enviado ✅")


class _HealthCheckHandler(BaseHTTPRequestHandler):
    """Responde 200 OK para evitar que Render duerma el servicio gratuito."""

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write("Bot de Telegram activo.".encode("utf-8"))

    def log_message(self, format, *args):
        pass


def iniciar_servidor_salud():
    puerto = int(os.environ.get("PORT", "10000"))
    servidor = HTTPServer(("0.0.0.0", puerto), _HealthCheckHandler)
    logger.info(f"Servidor de salud escuchando en el puerto {puerto}")
    servidor.serve_forever()


def main():
    if not TELEGRAM_TOKEN:
        logger.error("No se ha configurado la variable de entorno TELEGRAM_TOKEN")
        return

    # Arranca el servidor de salud en segundo plano para Render
    hilo_salud = threading.Thread(target=iniciar_servidor_salud, daemon=True)
    hilo_salud.start()

    app = Application.builder().token(TELEGRAM_TOKEN).build()

    # Handlers para comandos y menú interactivo
    app.add_handler(CommandHandler("start", comando_menu))
    app.add_handler(CommandHandler("menu", comando_menu))
    app.add_handler(CommandHandler("ayuda", comando_ayuda))
    app.add_handler(CommandHandler("registro", comando_registro))
    app.add_handler(CommandHandler("historico", comando_registro))
    app.add_handler(CommandHandler("exportar", comando_exportar))
    app.add_handler(CallbackQueryHandler(manejar_botones))

    logger.info("Bot iniciado correctamente con menú interactivo y escuchando peticiones...")
    app.run_polling()


if __name__ == "__main__":
    main()
