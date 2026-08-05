import os
import io
import json
import time
import html
import logging
import threading
from datetime import datetime
from zoneinfo import ZoneInfo
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
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_REPO = os.getenv("GITHUB_REPO", "tu_usuario/bot-dakhla-atlantique")
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
JSON_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/registro.json"
GROQ_MODEL = "llama-3.3-70b-versatile"
GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"

ZONA_CANARIAS = ZoneInfo("Atlantic/Canary")
HORA_ENVIO_OBJETIVO = 8  # 8:00 hora de Canarias

FLAG_MAP = {
    "arabe": "🇲🇦", "árabe": "🇲🇦",
    "frances": "🇫🇷", "francés": "🇫🇷",
    "espanol": "🇪🇸", "español": "🇪🇸",
    "ingles": "🇬🇧", "inglés": "🇬🇧",
    "vídeo": "📺", "video": "📺",
}

MESES_NOMBRE = {
    "01": "Enero", "02": "Febrero", "03": "Marzo", "04": "Abril",
    "05": "Mayo", "06": "Junio", "07": "Julio", "08": "Agosto",
    "09": "Septiembre", "10": "Octubre", "11": "Noviembre", "12": "Diciembre"
}


def bandera_para(etiqueta: str) -> str:
    """Devuelve el emoji más adecuado para una etiqueta (idioma, fuente de radio o podcast)."""
    clave = (etiqueta or "").strip().lower()
    if clave in FLAG_MAP:
        return FLAG_MAP[clave]
    if clave.startswith("radio"):
        return "📻"
    if clave:
        return "🎙️"
    return "🌐"


def obtener_datos_registro() -> dict:
    """Descarga registro.json y la convierte a la forma {año: {mes: [items]}}."""
    try:
        response = requests.get(JSON_RAW_URL, timeout=10)
        if response.status_code != 200:
            logger.error(f"Error al obtener registro.json: HTTP status {response.status_code}")
            return {}
        registro = response.json()
    except Exception as e:
        logger.error(f"Excepción al obtener/leer registro.json: {e}")
        return {}

    datos = {}
    for fecha, contenido_dia in registro.items():
        partes = fecha.split("-")
        if len(partes) != 3:
            continue
        anio, mes, _ = partes
        datos.setdefault(anio, {}).setdefault(mes, [])

        for item in contenido_dia.get("items", []):
            etiqueta = item.get("idioma") or item.get("etiqueta") or item.get("categoria") or "Otros"
            datos[anio][mes].append({
                "fecha": fecha,
                "bandera": bandera_para(etiqueta),
                "etiqueta": etiqueta,
                "titular": item.get("titular", ""),
                "link": item.get("link", "#")
            })

    for anio in datos:
        for mes in datos[anio]:
            datos[anio][mes].sort(key=lambda it: it["fecha"], reverse=True)

    return datos


TEXTO_AYUDA = (
    "🤖 *Bot Dakhla Atlantique — Ayuda e Información*\n\n"
    "📌 *Registro Histórico*\n"
    "Consulta el archivo histórico de noticias, navegando año a año y mes a mes.\n\n"
    "📤 *Exportar a Excel*\n"
    "Descarga en un archivo Excel (.xlsx) las noticias registradas de cualquier mes.\n\n"
    "⚓ *Resumen Mensual*\n"
    "Genera, cuando tú lo pidas, un resumen del mes completo en dos versiones: "
    "una puramente factual y otra con perspectiva de analista (sin valoraciones). "
    "No se envía nunca automáticamente.\n\n"
    "🔄 *Forzar Reporte*\n"
    "Dispara manualmente la ejecución del bot en GitHub Actions al momento.\n\n"
    "📊 *Estado del Sistema*\n"
    "Comprueba el estado de la última ejecución y salud general del bot.\n\n"
    "El reporte diario se envía automáticamente todos los días a las 08:00 hora de Canarias."
)


async def comando_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Muestra el panel de control interactivo con todas las opciones."""
    keyboard = [
        [InlineKeyboardButton("🔄 Forzar Reporte", callback_data="menu_forzar")],
        [InlineKeyboardButton("📊 Estado del Sistema", callback_data="menu_estado")],
        [InlineKeyboardButton("📜 /registro-historico", callback_data="menu_historico")],
        [InlineKeyboardButton("📊 /exportar", callback_data="menu_exportar")],
        [InlineKeyboardButton("⚓ /resume_mes", callback_data="menu_resumenmes")],
        [InlineKeyboardButton("❓ /ayuda", callback_data="menu_ayuda")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = "🎛️ *Panel de Control — Dakhla Atlantique*\n\nSelecciona una opción del menú:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(texto, reply_markup=reply_markup, parse_mode="Markdown")


async def comando_ayuda(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(TEXTO_AYUDA, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(TEXTO_AYUDA, reply_markup=reply_markup, parse_mode="Markdown")


def preguntar_ia(prompt: str):
    """Llama a Groq (mismo proveedor que usa main.py para el resumen diario)."""
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
            timeout=60
        )
        if res.status_code != 200:
            logger.error(f"Error llamando a la IA (Groq): HTTP {res.status_code} - {res.text}")
            return None
        return res.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"Error llamando a la IA (Groq): {e}")
        return None


def construir_texto_titulares_mes(items_mes):
    """Construye el listado de titulares del mes, en orden cronológico, para pasárselo a la IA."""
    items_ordenados = sorted(items_mes, key=lambda it: it["fecha"])
    return "\n".join(f"- [{it['fecha']}] {it['titular']}" for it in items_ordenados)


def generar_resumen_mensual_general(texto_titulares, mes_nombre, anio):
    """Resumen puramente extractivo del mes: sin interpretación ni añadidos."""
    prompt = f"""
    A continuación tienes TODOS los titulares recopilados durante {mes_nombre} de {anio}
    sobre el Puerto de Dakhla Atlantique, en orden cronológico.

    Redacta un resumen factual (varios párrafos si hace falta) que sintetice
    ÚNICAMENTE la información contenida en estos titulares.

    Reglas estrictas:
    - No añadas opiniones, valoraciones, interpretaciones ni conclusiones propias.
    - No completes con datos que no estén en la lista.
    - Limítate a describir de forma neutra qué se ha publicado durante el mes.
    - Responde solo con texto plano, sin Markdown ni HTML.

    Titulares del mes:
    {texto_titulares}
    """
    return preguntar_ia(prompt)


def generar_resumen_mensual_analista(texto_titulares, mes_nombre, anio):
    """Resumen del mes con perspectiva de analista de inteligencia."""
    prompt = f"""
    A continuación tienes TODOS los titulares recopilados durante {mes_nombre} de {anio}
    sobre el Puerto de Dakhla Atlantique, en orden cronológico.

    Redacta un resumen con la perspectiva de un analista de inteligencia: organiza
    la información por temas o actores relevantes, conecta hechos relacionados
    entre sí y señala patrones o líneas de desarrollo que se repitan a lo largo
    del mes.

    Reglas estrictas:
    - NO emitas valoraciones, juicios personales, opiniones ni predicciones.
    - No califiques los hechos como positivos, negativos, preocupantes, etc.
    - Limítate a mostrar relaciones y patrones objetivos entre los hechos
      publicados, sin añadir información que no esté en los titulares.
    - Responde solo con texto plano, sin Markdown ni HTML.

    Titulares del mes:
    {texto_titulares}
    """
    return preguntar_ia(prompt)


async def comando_resumen_mensual(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Punto de entrada: muestra los años disponibles para el resumen mensual."""
    datos = obtener_datos_registro()
    if not datos:
        msg = "⚠️ No se pudo acceder al registro histórico en este momento. Inténtalo más tarde."
        if update.callback_query:
            await update.callback_query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return

    keyboard = []
    for year in sorted(datos.keys(), reverse=True):
        keyboard.append([InlineKeyboardButton(f"📂 Año {year}", callback_data=f"resmes_year_{year}")])

    keyboard.append([InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    texto = "⚓ *Resúmenes Mensuales — Puerto de Dakhla Atlantique*\n\nSelecciona el año:"

    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.edit_text(texto, reply_markup=reply_markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(texto, reply_markup=reply_markup, parse_mode="Markdown")


async def resmes_year_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_parts = query.data.split("_")
    year = data_parts[2]

    datos = obtener_datos_registro()
    if year not in datos:
        await query.message.edit_text("⚠️ No hay datos para este año.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Volver", callback_data="menu_resumenmes")]]))
        return

    keyboard = []
    for mes_num in sorted(datos[year].keys(), reverse=True):
        nombre_mes = MESES_NOMBRE.get(mes_num, mes_num)
        keyboard.append([InlineKeyboardButton(f"📅 {nombre_mes} {year}", callback_data=f"resmes_month_{year}_{mes_num}")])

    keyboard.append([InlineKeyboardButton("« Volver a Años", callback_data="menu_resumenmes")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(f"⚓ *Resumen Mensual — Año {year}*\n\nSelecciona el mes:", reply_markup=reply_markup, parse_mode="Markdown")


async def resmes_month_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data_parts = query.data.split("_")
    year = data_parts[2]
    mes_num = data_parts[3]

    nombre_mes = MESES_NOMBRE.get(mes_num, mes_num)
    keyboard = [
        [InlineKeyboardButton("📄 Resumen Factual (Extractivo)", callback_data=f"resmes_gen_{year}_{mes_num}")],
        [InlineKeyboardButton("🧠 Resumen Analista (Inteligencia)", callback_data=f"resmes_ana_{year}_{mes_num}")],
        [InlineKeyboardButton("« Volver a Meses", callback_data=f"resmes_year_{year}")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.message.edit_text(
        f"⚓ *Resumen Mensual — {nombre_mes} {year}*\n\nSelecciona el enfoque del resumen:",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )


async def resmes_generar_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Generando resumen con IA...", show_alert=False)

    data_parts = query.data.split("_")
    tipo = data_parts[1]
    year = data_parts[2]
    mes_num = data_parts[3]

    nombre_mes = MESES_NOMBRE.get(mes_num, mes_num)
    datos = obtener_datos_registro()
    items_mes = datos.get(year, {}).get(mes_num, [])
    
    if not items_mes:
        await query.message.edit_text(
            f"⚠️ No hay registros de noticias para {nombre_mes} {year}.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("« Volver", callback_data=f"resmes_year_{year}")]])
        )
        return

    await query.message.edit_text(f"⏳ Generando resumen de {nombre_mes} {year} con IA... Esto puede tardar unos segundos.")

    texto_titulares = construir_texto_titulares_mes(items_mes)

    if tipo == "gen":
        resumen = generar_resumen_mensual_general(texto_titulares, nombre_mes, year)
        titulo_resumen = f"⚓ *Resumen Mensual Factual — {nombre_mes} {year}*"
    else:
        resumen = generar_resumen_mensual_analista(texto_titulares, nombre_mes, year)
        titulo_resumen = f"🧠 *Resumen Mensual de Analista — {nombre_mes} {year}*"

    if not resumen:
        resumen = "⚠️ Hubo un error al generar el resumen con la IA. Inténtalo de nuevo más tarde."

    mensaje_final = f"{titulo_resumen}\n\n{resumen}"

    keyboard = [
        [InlineKeyboardButton(f"« Volver a {nombre_mes} {year}", callback_data=f"resmes_month_{year}_{mes_num}")],
        [InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if len(mensaje_final) > 4000:
        await query.message.reply_text(mensaje_final[:4000], parse_mode="Markdown")
        await query.message.reply_text(mensaje_final[4000:], reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await query.message.reply_text(mensaje_final, reply_markup=reply_markup, parse_mode="Markdown")


async def comando_exportar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    datos = obtener_datos_registro()
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


def disparar_workflow_github(modo="manual"):
    if not GITHUB_TOKEN:
        return False, "⚠️ Error: No se ha configurado GITHUB_TOKEN en las variables de entorno."

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/workflows/daily_bot.yml/dispatches"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        response = requests.post(
            url,
            json={"ref": "main", "inputs": {"modo": modo}},
            headers=headers,
            timeout=10
        )
        if response.status_code == 204:
            return True, "🤖 Estoy trabajando en ello, en unos minutos te facilito la información. Siéntate y tómate un café. ☕"
        return False, f"⚠️ No se pudo disparar el workflow (Código HTTP {response.status_code})."
    except Exception as e:
        return False, f"❌ Excepción al conectar con GitHub: {e}"


def iniciar_reloj_disparo_diario():
    ultimo_dia_disparado = None
    while True:
        try:
            ahora = datetime.now(ZONA_CANARIAS)
            hoy = ahora.strftime("%Y-%m-%d")
            if ahora.hour == HORA_ENVIO_OBJETIVO and hoy != ultimo_dia_disparado:
                logger.info(f"Reloj interno: son las {ahora.strftime('%H:%M')} en Canarias, disparando el reporte diario.")
                ok, _ = disparar_workflow_github(modo="automatico")
                if ok:
                    ultimo_dia_disparado = hoy
        except Exception as e:
            logger.error(f"Error en el reloj de disparo diario: {e}")
        time.sleep(60)


async def comando_forzar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    if query:
        await query.answer("Lanzando reporte...")

    _, msg = disparar_workflow_github(modo="manual")
    keyboard = [[InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.message.edit_text(msg, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown")


async def comando_estado(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query if update.callback_query else None
    if query:
        await query.answer("Comprobando estado...")

    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"

    url = f"https://api.github.com/repos/{GITHUB_REPO}/actions/runs?per_page=1"
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            runs = response.json().get("workflow_runs", [])
            if runs:
                run = runs[0]
                estado = run.get("conclusion") or run.get("status")
                fecha = run.get("created_at", "")[:19].replace("T", " ")
                html_url = run.get("html_url")
                
                icono_estado = "✅" if estado == "success" else ("⏳" if estado in ["queued", "in_progress"] else "❌")
                msg = (
                    f"📊 *Estado del Sistema — GitHub Actions*\n\n"
                    f"{icono_estado} **Último estado:** `{estado}`\n"
                    f"🗓️ **Fecha:** `{fecha} UTC`\n\n"
                    f"🔗 [Ver detalles en GitHub]({html_url})"
                )
            else:
                msg = "ℹ️ No se encontraron ejecuciones recientes en el repositorio."
        else:
            msg = f"⚠️ Error al consultar la API de GitHub (Código HTTP {response.status_code})."
    except Exception as e:
        msg = f"❌ Excepción al consultar el estado: {e}"

    keyboard = [[InlineKeyboardButton("« Volver al Menú Principal", callback_data="menu_main")]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    if query:
        await query.message.edit_text(msg, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)
    else:
        await update.message.reply_text(msg, reply_markup=reply_markup, parse_mode="Markdown", disable_web_page_preview=True)


def generar_excel_mes(noticias, year, month) -> io.BytesIO:
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

    hoja.column_dimensions["A"].width = 12
    hoja.column_dimensions["B"].width = 20
    hoja.column_dimensions["C"].width = 60
    hoja.column_dimensions["D"].width = 40

    buffer = io.BytesIO()
    libro.save(buffer)
    buffer.seek(0)
    return buffer
