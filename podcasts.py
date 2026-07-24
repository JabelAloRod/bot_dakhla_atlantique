import requests
from datetime import datetime, timedelta

def buscar_podcasts_dakhla(dias_atras=7):
    """
    Busca episodios de podcasts recientes sobre el Puerto de Dakhla 
    utilizando la API gratuita de iTunes/Apple Podcasts.
    """
    terminos_busqueda = [
        "Dakhla Atlantique",
        "Port Dakhla",
        "Puerto Dakhla",
        "Dakhla Atlantic Port"
    ]
    
    url_base = "https://itunes.apple.com/search"
    episodios_encontrados = []
    ids_procesados = set()
    
    # Límite de fecha para filtrar solo episodios recientes
    fecha_limite = datetime.now() - timedelta(days=dias_atras)

    for termino in terminos_busqueda:
        params = {
            "term": termino,
            "entity": "podcastEpisode",
            "limit": 5
        }
        
        try:
            respuesta = requests.get(url_base, params=params, timeout=10)
            if respuesta.status_code == 200:
                resultados = respuesta.json().get("results", [])
                
                for item in resultados:
                    track_id = item.get("trackId")
                    if track_id in ids_procesados:
                        continue
                    
                    # Comprobar la fecha de publicación
                    fecha_str = item.get("releaseDate")
                    if fecha_str:
                        # Formato ISO de iTunes (ej: 2026-06-23T10:00:00Z)
                        fecha_ep = datetime.strptime(fecha_str.split("T")[0], "%Y-%m-%d")
                        if fecha_ep < fecha_limite:
                            continue  # Saltar episodios antiguos
                    
                    ids_procesados.add(track_id)
                    
                    episodios_encontrados.append({
                        "id": track_id,
                        "titulo": item.get("trackName", "Sin título"),
                        "podcast": item.get("collectionName", "Podcast desconocido"),
                        "descripcion": item.get("description", item.get("shortDescription", "Sin descripción")),
                        "url": item.get("trackViewUrl", "#"),
                        "fecha": fecha_str.split("T")[0] if fecha_str else "Fecha desconocida"
                    })
        except Exception as e:
            print(f"Error al buscar podcasts para '{termino}': {e}")
            
    return episodios_encontrados
