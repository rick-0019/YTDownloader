from flask import Flask, render_template, request, send_from_directory, jsonify
from yt_dlp import YoutubeDL
import os
import threading
import requests
import re
from jinja2 import Environment, FileSystemLoader

app = Flask(__name__)

DOWNLOAD_FOLDER = 'downloads'
THUMBNAIL_FOLDER = 'static/thumbnails'

os.makedirs(DOWNLOAD_FOLDER, exist_ok=True)
os.makedirs(THUMBNAIL_FOLDER, exist_ok=True)

# Diagnóstico de FFmpeg
import subprocess
try:
    ffmpeg_check = subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True)
    print(f"DEBUG: FFmpeg detectado: {ffmpeg_check.stdout.splitlines()[0]}")
except Exception as e:
    print(f"DEBUG: FFmpeg NO DETECTADO o error: {e}")

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

    # Buscar cookies en rutas locales y de Render
    possible_cookie_paths = ["cookies.txt", "/etc/secrets/cookies.txt"]
    cookie_path = None
    for path in possible_cookie_paths:
        if os.path.exists(path):
            cookie_path = path
            print(f"DEBUG: Archivo de cookies detectado en: {path}")
            break
    
    if not cookie_path:
        print(f"DEBUG: No se encontró cookies.txt en ninguna de las rutas: {possible_cookie_paths}")
        print(f"DEBUG: Archivos en el directorio actual ({os.getcwd()}): {os.listdir('.')}")

    ydl_opts_info = {
        'extract_flat': True,
        'skip_download': True,
        'quiet': False,
        'no_warnings': False,
        'noplaylist': True,
        'progress_hooks': [progreso_hook],
        'writethumbnail': False
    }
    
    if cookie_path:
        ydl_opts_info['cookiefile'] = cookie_path

    with YoutubeDL(ydl_opts_info) as ydl:
        info = ydl.extract_info(url, download=False)
        title = info.get('title', 'video')

    safe_filename = "".join(c for c in title if c.isalnum() or c in " -_").strip()
    
    if solo_audio:
        final_filename_base = f"{safe_filename}_mp3"
    else:
        final_filename_base = safe_filename
    
    thumb_url = info.get('thumbnail')
    thumb_file_path = None
    if thumb_url:
        try:
            ext = os.path.splitext(thumb_url.split("?")[0])[1] or ".jpg"
            thumb_dest = os.path.join(THUMBNAIL_FOLDER, f"{final_filename_base}{ext}")

            r = requests.get(thumb_url, timeout=10)
            with open(thumb_dest, "wb") as f:
                f.write(r.content)
            
            thumb_file_path = f"/static/thumbnails/{final_filename_base}{ext}"
        except Exception as e:
            print("Error al guardar miniatura:", e)

    ydl_opts_download = {
        'outtmpl': f'{carpeta_destino}/{final_filename_base}.%(ext)s',
        'ignoreerrors': True,
        'progress_hooks': [lambda d: progreso_hook(d, url)],
        'writethumbnail': False
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
            'format': 'best[ext=mp4]/best',
        })

    with YoutubeDL(ydl_opts_download) as ydl:
        ydl.download([url])

    file_ext = 'mp3' if solo_audio else 'mp4'
    filename_raw = os.path.join(carpeta_destino, f"{final_filename_base}.{file_ext}")
    
    return title, thumb_file_path, filename_raw

def progreso_hook(d, url):
    if d.get('total_bytes') and d.get('downloaded_bytes'):
        progreso_videos[url] = int(d['downloaded_bytes'] / d['total_bytes'] * 100)
    elif d.get('status') == 'finished':
        progreso_videos[url] = 100

@app.route('/')
def index():
    miniaturas = []
    for root, dirs, files in os.walk(THUMBNAIL_FOLDER):
        for f in files:
            thumb_url = f"/static/thumbnails/{f}"
            filename_base = os.path.splitext(f)[0]
            
            video_download_url = None
            
            for video_root, video_dirs, video_files in os.walk(DOWNLOAD_FOLDER):
                for v_file in video_files:
                    if os.path.splitext(v_file)[0] == filename_base:
                        relative_path = os.path.relpath(os.path.join(video_root, v_file), DOWNLOAD_FOLDER)
                        video_download_url = f"/downloads/{relative_path.replace(os.path.sep, '/')}"
                        break
                if video_download_url:
                    break
            
            if video_download_url:
                miniaturas.append({
                    'thumb_url': thumb_url,
                    'download_url': video_download_url
                })
    
    return render_template('index.html', miniaturas=miniaturas)

@app.route('/download', methods=['POST'])
def download():
    form = request.form or {}
    urls_raw = form.get('urls', '') or ''
    urls = [u.strip() for u in urls_raw.splitlines() if u.strip()]

    if not urls:
        return jsonify({'error': 'No se ingresaron URLs'}), 400

    solo_audio = form.get('solo_audio', 'off') == 'on'
    subcarpeta = form.get('subcarpeta', '').strip()
    carpeta_destino = os.path.join(DOWNLOAD_FOLDER, subcarpeta) if subcarpeta else DOWNLOAD_FOLDER

    results = []

    def thread_func():
        for url in urls:
            current_download_url = None
            current_title = "video"
            info_videos[url] = {'status': 'processing'}
            try:
                title, thumbnail, full_file_path = descargar_video(url, solo_audio, carpeta_destino)
                current_title = title
                
                if not os.path.exists(full_file_path):
                    print(f"ERROR: El archivo no se creó en {full_file_path}")
                    # Tal vez yt-dlp dejó otra extensión si falló ffmpeg
                    base = os.path.splitext(full_file_path)[0]
                    found = False
                    for f in os.listdir(os.path.dirname(full_file_path)):
                        if f.startswith(os.path.basename(base)):
                            print(f"INFO: Se encontró archivo alternativo: {f}")
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
            'status': info.get('status', 'downloading')
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

@app.route('/delete', methods=['POST'])
def delete_file():
    data = request.get_json()
    download_url = data.get('download_url')

    if not download_url:
        return jsonify({'error': 'URL de descarga no proporcionada'}), 400

    try:
        # Convertir la URL relativa en una ruta de archivo absoluta
        filepath_relative = download_url.replace('/downloads/', '', 1)
        filepath = os.path.join(DOWNLOAD_FOLDER, filepath_relative)

        # 1. Eliminar el archivo principal (MP4 o MP3)
        if os.path.exists(filepath):
            os.remove(filepath)
        else:
            return jsonify({'error': 'Archivo no encontrado'}), 404
        
        # 2. Eliminar la miniatura asociada
        filename_base = os.path.splitext(os.path.basename(filepath))[0]
        
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





















