from flask import Flask, render_template, request, send_from_directory, jsonify
from yt_dlp import YoutubeDL
import os
import threading
import requests
import re
import time
from jinja2 import Environment, FileSystemLoader
from urllib.parse import unquote

app = Flask(__name__)

DOWNLOAD_FOLDER = 'downloads'
THUMBNAIL_FOLDER = 'static/thumbnails'

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)


progreso_videos = {}
# Almacenar información extra como el link de descarga final
info_videos = {}

# Configuración del entorno Jinja2 para usar funciones personalizadas
app.jinja_env.globals['render_item'] = lambda item: f"""
    <div class="bg-gray-800 p-2 rounded shadow relative group cursor-pointer" onclick="playMedia('{item.get('download_url')}')">
        <img src="{item.get('thumb_url')}" class="w-full rounded">
        <button class="absolute top-0 right-0 m-1 p-1 bg-red-600 hover:bg-red-700 text-white rounded-full opacity-0 group-hover:opacity-100 transition-opacity"
                onclick="event.stopPropagation(); deleteFile(this, '{item.get('download_url')}');">
            ✖️
        </button>
    </div>
"""

def descargar_video(url, solo_audio, carpeta_destino):
    os.makedirs(carpeta_destino, exist_ok=True)
    progreso_videos[url] = 0

    # Buscar cookies locales
    cookie_path = "cookies.txt" if os.path.exists("cookies.txt") else None
    
    ydl_opts_info = {
        'extract_flat': True,
        'skip_download': True,
        'no_warnings': True,
        'quiet': True,
        'js_runtimes': {'node': {}},
    }
    
    if cookie_path:
        ydl_opts_info['cookiefile'] = cookie_path

    with YoutubeDL(ydl_opts_info) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'video')
        is_playlist = 'entries' in info or 'list=' in url.lower() or info.get('_type') == 'playlist'

    ydl_opts_download = {
        'ignoreerrors': True,
        'progress_hooks': [lambda d: progreso_hook(d, url)],
        'no_warnings': True,
        'writethumbnail': True,
        'js_runtimes': {'node': {}},
    }

    if cookie_path:
        ydl_opts_download['cookiefile'] = cookie_path

    if solo_audio:
        ydl_opts_download.update({
            'format': 'bestaudio/best',
            'postprocessors': [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': 'mp3',
                'preferredquality': '192',
            }],
        })
    else:
        ydl_opts_download.update({
            'format': 'bestvideo+bestaudio/best',
            'merge_output_format': 'mp4',
        })

    if is_playlist:
        # Nombre dinámico para cada item
        ydl_opts_download['outtmpl'] = f'{carpeta_destino}/%(playlist_index)02d - %(title)s.%(ext)s'
        
        try:
            with YoutubeDL(ydl_opts_download) as ydl:
                ydl.download([url])
        except Exception as e:
            print(f"Error descargando playlist {url}: {e}")
            raise e
        
        return title, None, None
    else:
        safe_filename = "".join(c for c in title if c.isalnum() or c in " -_").strip()
        final_filename_base = f"{safe_filename}_mp3" if solo_audio else safe_filename
        ydl_opts_download['outtmpl'] = f'{carpeta_destino}/{final_filename_base}.%(ext)s'
        
        try:
            with YoutubeDL(ydl_opts_download) as ydl:
                ydl.download([url])
        except Exception as e:
            print(f"Error descargando {url}: {e}")
            raise e

        file_ext = 'mp3' if solo_audio else 'mp4'
        filename_raw = os.path.join(carpeta_destino, f"{final_filename_base}.{file_ext}")
        
        return title, None, filename_raw

def progreso_hook(d, url):
    if d.get('total_bytes') and d.get('downloaded_bytes'):
        progreso_videos[url] = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
    elif d.get('status') == 'finished':
        progreso_videos[url] = 100


@app.route('/')
def index():
    media_by_folder = {}
    
    # Extensiones soportadas
    extensions = ('.mp4', '.mp3', '.mkv', '.webm')
    
    for root, dirs, files in os.walk(DOWNLOAD_FOLDER):
        relative_dir = os.path.relpath(root, DOWNLOAD_FOLDER).replace(os.path.sep, '/')
        folder_name = "Principal" if relative_dir == "." else relative_dir
        
        folder_items = []
        for f in files:
            if f.lower().endswith(extensions):
                filename_base = os.path.splitext(f)[0]
                
                # Buscar miniatura nativa o antigua
                thumb_url = "/static/placeholder.jpg"
                
                # Buscar en carpeta descargas nativa de yt-dlp
                for img_ext in ['.jpg', '.webp', '.png']:
                    if os.path.exists(os.path.join(root, filename_base + img_ext)):
                        rel = os.path.relpath(os.path.join(root, filename_base + img_ext), DOWNLOAD_FOLDER)
                        thumb_url = f"/media/{rel.replace(os.path.sep, '/')}"
                        break
                
                if thumb_url == "/static/placeholder.jpg":
                    for t_file in os.listdir(THUMBNAIL_FOLDER):
                        if os.path.splitext(t_file)[0] == filename_base:
                            thumb_url = f"/static/thumbnails/{t_file}"
                            break
                
                relative_path = os.path.relpath(os.path.join(root, f), DOWNLOAD_FOLDER)
                download_url = f"/downloads/{relative_path.replace(os.path.sep, '/')}"
                
                folder_items.append({
                    'name': f,
                    'thumb_url': thumb_url,
                    'download_url': download_url,
                    'is_audio': f.lower().endswith('.mp3')
                })
        
        if folder_items:
            if folder_name not in media_by_folder:
                media_by_folder[folder_name] = []
            media_by_folder[folder_name].extend(folder_items)
    
    return render_template('index.html', media_by_folder=media_by_folder)

@app.route('/download', methods=['POST'])
def download():
    form = request.form or {}
    urls_raw = form.get('urls', '') or ''
    urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]

    if not urls:
        return jsonify({'error': 'No se ingresaron URLs'}), 400

    solo_audio = form.get('solo_audio', 'off') == 'on'
    subcarpeta = form.get('subcarpeta', '').strip().replace('\\', '/')
    carpeta_destino = os.path.join(DOWNLOAD_FOLDER, subcarpeta) if subcarpeta else DOWNLOAD_FOLDER

    results = []

    def thread_func():
        for url in urls:
            current_download_url = None
            current_title = "video"
            info_videos[url] = {'status': 'processing', 'solo_audio': solo_audio}
            try:
                title, thumbnail, full_file_path = descargar_video(url, solo_audio, carpeta_destino)
                current_title = title
                
                if full_file_path is None:
                    # Es una playlist
                    current_download_url = "" 
                else:
                    if not os.path.exists(full_file_path):
                        base = os.path.splitext(full_file_path)[0]
                        found = False
                        for f in os.listdir(os.path.dirname(full_file_path)):
                            if f.startswith(os.path.basename(base)):
                                full_file_path = os.path.join(os.path.dirname(full_file_path), f)
                                found = True
                                break
                        if not found:
                            raise Exception("Archivo no encontrado después de la descarga.")
                            
                    relative_path = os.path.relpath(full_file_path, DOWNLOAD_FOLDER)
                    current_download_url = f"/downloads/{relative_path.replace(os.path.sep, '/')}"

                print(f"Descarga exitosa: {current_download_url}")
                results.append({
                    'url': url,
                    'title': title,
                    'thumbnail': thumbnail,
                    'download_url': current_download_url
                })
            except Exception as e:
                error_msg = str(e)
                results.append({'url': url, 'error': error_msg})
                print(f"Error descargando {url}: {error_msg}")
                info_videos[url] = {'error': error_msg, 'status': 'error'}
            finally:
                if current_download_url:
                    progreso_videos[url] = 100
                    info_videos[url] = {'download_url': current_download_url, 'title': current_title, 'status': 'finished'}

    thread = threading.Thread(target=thread_func)
    thread.start()
    # No esperaremos al thread (quitamos thread.join()) para que la web responda al instante
    return jsonify({'status': 'started', 'message': 'Descarga iniciada'})

@app.route('/progress')
def progress():
    # Combinar progreso con info extra si existe
    data = {}
    for url, prog in progreso_videos.items():
        info = info_videos.get(url, {})
        data[url] = {
            'progress': prog,
            'download_url': info.get('download_url'),
            'title': info.get('title'),
            'error': info.get('error'),
            'status': info.get('status', 'downloading'),
            'solo_audio': info.get('solo_audio', False)
        }
    return jsonify(data)

@app.route('/downloads/<path:filepath>')
def serve_file(filepath):
    full_path = os.path.join(DOWNLOAD_FOLDER, filepath)
    if not os.path.exists(full_path):
        print(f"ERROR: Se intentó descargar un archivo que no existe: {full_path}")
        return jsonify({'error': 'El archivo no existe en el servidor. Puede que haya sido borrado.'}), 404
        
    filename = os.path.basename(filepath)
    return send_from_directory(DOWNLOAD_FOLDER, filepath, as_attachment=True, download_name=filename)

@app.route('/media/<path:filepath>')
def serve_media(filepath):
    # Sirve archivos sin as_attachment para miniaturas
    return send_from_directory(DOWNLOAD_FOLDER, filepath)

@app.route('/delete', methods=['POST'])
def delete_file():
    data = request.get_json()
    download_url = data.get('download_url')

    if not download_url:
        return jsonify({'error': 'URL de descarga no proporcionada'}), 400

    try:
        # Convertir la URL relativa en una ruta de archivo absoluta (decodificando URL)
        filepath_relative = unquote(download_url.replace('/downloads/', '', 1))
        # Asegurar compatibilidad de separadores en la ruta relativa
        filepath_relative = filepath_relative.replace('/', os.path.sep)
        filepath = os.path.join(DOWNLOAD_FOLDER, filepath_relative)

        # 1. Eliminar el archivo principal (MP4 o MP3)
        if os.path.exists(filepath):
            # Intentar borrar con reintentos (para Windows WinError 32)
            deleted = False
            for i in range(5):
                try:
                    os.remove(filepath)
                    deleted = True
                    break
                except PermissionError:
                    print(f"Archivo bloqueado, reintentando {i+1}/5...")
                    time.sleep(0.5)
            
            if not deleted:
                return jsonify({'error': 'El archivo está siendo usado por otro proceso. Cierra el reproductor e intenta de nuevo.'}), 423
        else:
            return jsonify({'error': 'Archivo no encontrado'}), 404
        
        # 2. Eliminar la miniatura asociada
        filename_base = os.path.splitext(os.path.basename(filepath))[0]
        
        for img_ext in ['.jpg', '.webp', '.png']:
            native_thumb = os.path.splitext(filepath)[0] + img_ext
            if os.path.exists(native_thumb):
                os.remove(native_thumb)
        
        thumb_file = None
        for f in os.listdir(THUMBNAIL_FOLDER):
            if os.path.splitext(f)[0] == filename_base:
                thumb_file = os.path.join(THUMBNAIL_FOLDER, f)
                break
        
        if thumb_file and os.path.exists(thumb_file):
            os.remove(thumb_file)

        return jsonify({'message': 'Archivo y miniatura eliminados correctamente'}), 200

    except Exception as e:
        print(f"Error al intentar eliminar el archivo: {e}")
        return jsonify({'error': 'Error interno del servidor'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)





















