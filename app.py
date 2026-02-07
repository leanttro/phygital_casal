"""
Phygital SaaS - Flask Application
Gerencia páginas personalizadas acessadas via QR Code
Autor: Phygital Team
Data: 2024
"""

import os
import re
import json
import logging
from functools import wraps
from datetime import datetime
from urllib.parse import urljoin

import requests
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, abort
from flask_session import Session
from dotenv import load_dotenv
from werkzeug.utils import secure_filename

# ============================================================================
# CONFIGURAÇÃO INICIAL
# ============================================================================

# Carregar variáveis de ambiente
load_dotenv()

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Inicializar Flask app
app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Configurar sessão (usando sistema de arquivos em produção, memória em dev)
app.config['SESSION_TYPE'] = 'filesystem' if os.getenv('FLASK_ENV') == 'production' else 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)

# ============================================================================
# VARIÁVEIS DE AMBIENTE E CONSTANTES
# ============================================================================

# Directus Config
DIRECTUS_URL = os.getenv('DIRECTUS_URL', '').rstrip('/')
DIRECTUS_TOKEN = os.getenv('DIRECTUS_TOKEN')
DIRECTUS_HEADERS = {
    'Authorization': f'Bearer {DIRECTUS_TOKEN}',
    'Content-Type': 'application/json'
}

# Spotify Config
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API_BASE = 'https://api.spotify.com/v1'

# Validação de configuração
if not all([DIRECTUS_URL, DIRECTUS_TOKEN]):
    logger.warning("Variáveis do Directus não configuradas. A aplicação pode não funcionar corretamente.")

if not all([SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET]):
    logger.warning("Variáveis do Spotify não configuradas. A funcionalidade de busca de músicas estará indisponível.")

# ============================================================================
# FUNÇÕES HELPER - DIRECTUS
# ============================================================================

def get_page_by_slug(slug):
    """
    Busca uma página na coleção 'paginas' do Directus pelo slug.
    
    Args:
        slug (str): Slug da página (ex: 'lucasegabi')
    
    Returns:
        dict: Dados da página ou None se não encontrado
    """
    try:
        # URL da API do Directus para a coleção 'paginas'
        url = f"{DIRECTUS_URL}/items/paginas"
        
        # Parâmetros de busca
        params = {
            'filter[slug][_eq]': slug,
            'fields': '*,fotos.*',  # Traz todos os campos e as fotos relacionadas
            'limit': 1
        }
        
        response = requests.get(
            url,
            headers=DIRECTUS_HEADERS,
            params=params,
            timeout=10
        )
        
        if response.status_code == 200:
            data = response.json()
            if data.get('data') and len(data['data']) > 0:
                page_data = data['data'][0]
                
                # Processar fotos para ter URLs completas
                if 'fotos' in page_data and page_data['fotos']:
                    for i, foto in enumerate(page_data['fotos']):
                        if 'filename_disk' in foto:
                            page_data['fotos'][i]['url'] = f"{DIRECTUS_URL}/assets/{foto['filename_disk']}"
                        elif 'id' in foto:
                            page_data['fotos'][i]['url'] = f"{DIRECTUS_URL}/files/{foto['id']}"
                
                logger.info(f"Página encontrada: {slug}")
                return page_data
            else:
                logger.info(f"Página não encontrada: {slug}")
                return None
                
        else:
            logger.error(f"Erro ao buscar página no Directus: {response.status_code}")
            logger.error(f"Resposta: {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão com Directus: {str(e)}")
        return None


def verify_password(slug, input_password):
    """
    Verifica se a senha fornecida corresponde à senha da página.
    
    Nota: Por enquanto, a senha está em texto simples no Directus.
          Em produção, considerar usar hash.
    
    Args:
        slug (str): Slug da página
        input_password (str): Senha fornecida pelo usuário
    
    Returns:
        bool: True se a senha estiver correta
    """
    try:
        # Buscar a página para obter a senha
        page = get_page_by_slug(slug)
        if not page:
            return False
        
        # Comparar senhas (texto simples por enquanto)
        stored_password = page.get('senha')
        
        # Se não houver senha cadastrada, não requer login
        if not stored_password:
            return True
            
        return stored_password == input_password
        
    except Exception as e:
        logger.error(f"Erro ao verificar senha: {str(e)}")
        return False


def update_page(slug, data):
    """
    Atualiza os dados de uma página no Directus.
    
    Args:
        slug (str): Slug da página
        data (dict): Dados para atualizar
    
    Returns:
        dict: Dados atualizados ou None em caso de erro
    """
    try:
        # Primeiro, buscar o ID da página
        page = get_page_by_slug(slug)
        if not page or 'id' not in page:
            logger.error(f"Página {slug} não encontrada para atualização")
            return None
        
        page_id = page['id']
        url = f"{DIRECTUS_URL}/items/paginas/{page_id}"
        
        # Preparar dados para envio
        # O Directus espera um objeto JSON com os campos para atualizar
        patch_data = {}
        
        # Campos permitidos para atualização
        allowed_fields = ['titulo', 'mensagem', 'cor_fundo', 'spotify_url']
        
        for field in allowed_fields:
            if field in data and data[field] is not None:
                patch_data[field] = data[field]
        
        # Processar fotos se existirem
        if 'fotos' in data and data['fotos'] is not None:
            # O Directus espera um array de IDs de arquivos
            foto_ids = []
            for foto in data['fotos']:
                if isinstance(foto, dict) and 'id' in foto:
                    foto_ids.append(foto['id'])
                elif isinstance(foto, str):
                    # Se for string, assumir que já é um ID
                    foto_ids.append(foto)
            
            patch_data['fotos'] = foto_ids
        
        response = requests.patch(
            url,
            headers=DIRECTUS_HEADERS,
            json=patch_data,
            timeout=10
        )
        
        if response.status_code in [200, 204]:
            logger.info(f"Página {slug} atualizada com sucesso")
            # Retornar dados atualizados
            updated_page = get_page_by_slug(slug)
            return updated_page
        else:
            logger.error(f"Erro ao atualizar página: {response.status_code}")
            logger.error(f"Resposta: {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão ao atualizar página: {str(e)}")
        return None


def upload_file(file_storage):
    """
    Faz upload de um arquivo para o Directus.
    
    Args:
        file_storage: Objeto FileStorage do Flask/Werkzeug
    
    Returns:
        dict: Dados do arquivo carregado ou None em caso de erro
    """
    try:
        url = f"{DIRECTUS_URL}/files"
        
        # Preparar arquivo para upload
        filename = secure_filename(file_storage.filename)
        
        files = {
            'file': (filename, file_storage, file_storage.mimetype)
        }
        
        # Headers para upload (sem Content-Type, pois será multipart/form-data)
        upload_headers = DIRECTUS_HEADERS.copy()
        upload_headers.pop('Content-Type', None)
        
        response = requests.post(
            url,
            headers=upload_headers,
            files=files,
            timeout=30  # Timeout maior para uploads
        )
        
        if response.status_code == 200:
            file_data = response.json()
            logger.info(f"Arquivo {filename} carregado com sucesso")
            return file_data.get('data')
        else:
            logger.error(f"Erro ao fazer upload: {response.status_code}")
            logger.error(f"Resposta: {response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão ao fazer upload: {str(e)}")
        return None
    except Exception as e:
        logger.error(f"Erro inesperado ao fazer upload: {str(e)}")
        return None


# ============================================================================
# FUNÇÕES HELPER - SPOTIFY
# ============================================================================

def get_spotify_token():
    """
    Obtém token de acesso da API do Spotify usando Client Credentials Flow.
    
    Returns:
        str: Token de acesso ou None em caso de erro
    """
    try:
        auth_response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={
                'grant_type': 'client_credentials',
                'client_id': SPOTIFY_CLIENT_ID,
                'client_secret': SPOTIFY_CLIENT_SECRET,
            },
            headers={'Content-Type': 'application/x-www-form-urlencoded'},
            timeout=10
        )
        
        if auth_response.status_code == 200:
            token_data = auth_response.json()
            token = token_data.get('access_token')
            logger.info("Token do Spotify obtido com sucesso")
            return token
        else:
            logger.error(f"Erro ao obter token Spotify: {auth_response.status_code}")
            logger.error(f"Resposta: {auth_response.text}")
            return None
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão com Spotify Auth: {str(e)}")
        return None


def ensure_embed_url(url):
    """
    Converte uma URL normal do Spotify para formato embed.
    
    Args:
        url (str): URL do Spotify (track, playlist ou album)
    
    Returns:
        str: URL no formato embed ou a URL original se não for do Spotify
    """
    if not url:
        return None
    
    # Se já for uma URL embed, retorna como está
    if 'embed.spotify.com' in url or '/embed/' in url:
        return url
    
    # Padrões de URL do Spotify
    patterns = [
        # open.spotify.com/track/abc123?si=xyz
        r'(?:https?://)?(?:open\.)?spotify\.com/(track|playlist|album)/([a-zA-Z0-9]+)',
        # spotify:track:abc123
        r'spotify:(track|playlist|album):([a-zA-Z0-9]+)'
    ]
    
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            type_ = match.group(1)
            id_ = match.group(2)
            embed_url = f"https://open.spotify.com/embed/{type_}/{id_}"
            logger.info(f"URL do Spotify convertida para embed: {embed_url}")
            return embed_url
    
    # Se não for uma URL do Spotify reconhecida, retorna a original
    logger.warning(f"URL não reconhecida do Spotify: {url}")
    return url


def search_tracks(query, limit=10):
    """
    Busca músicas na API do Spotify.
    
    Args:
        query (str): Termo de busca
        limit (int): Número máximo de resultados
    
    Returns:
        list: Lista de dicionários com informações das músicas
    """
    token = get_spotify_token()
    if not token:
        logger.error("Não foi possível obter token do Spotify")
        return []
    
    try:
        headers = {
            'Authorization': f'Bearer {token}',
            'Content-Type': 'application/json'
        }
        
        params = {
            'q': query,
            'type': 'track',
            'limit': min(limit, 20),  # Máximo 20 resultados
            'market': 'BR'  # Brasil por padrão
        }
        
        response = requests.get(
            f"{SPOTIFY_API_BASE}/search",
            headers=headers,
            params=params,
            timeout=15
        )
        
        if response.status_code == 200:
            data = response.json()
            tracks = data.get('tracks', {}).get('items', [])
            
            results = []
            for track in tracks:
                # Informações básicas
                track_id = track.get('id')
                track_name = track.get('name', 'Música desconhecida')
                
                # Artista(s)
                artists = track.get('artists', [])
                artist_name = artists[0].get('name', 'Artista desconhecida') if artists else 'Artista desconhecida'
                
                # Imagem do álbum (pegar a maior resolução disponível)
                album_images = track.get('album', {}).get('images', [])
                image_url = album_images[0].get('url') if album_images else None
                
                # URL embed
                spotify_url = f"https://open.spotify.com/track/{track_id}"
                embed_url = ensure_embed_url(spotify_url)
                
                results.append({
                    'id': track_id,
                    'name': track_name,
                    'artist': artist_name,
                    'image_url': image_url,
                    'embed_url': embed_url,
                    'preview_url': track.get('preview_url'),
                    'duration_ms': track.get('duration_ms')
                })
            
            logger.info(f"Busca no Spotify: '{query}' retornou {len(results)} resultados")
            return results
            
        elif response.status_code == 401:
            logger.error("Token do Spotify expirado ou inválido")
            return []
        else:
            logger.error(f"Erro na API do Spotify: {response.status_code}")
            return []
            
    except requests.exceptions.RequestException as e:
        logger.error(f"Erro de conexão com Spotify API: {str(e)}")
        return []


# ============================================================================
# DECORATORS E FUNÇÕES DE UTILIDADE
# ============================================================================

def login_required(f):
    """
    Decorator para proteger rotas que requerem login.
    Verifica se o usuário está autenticado para a página específica.
    """
    @wraps(f)
    def decorated_function(slug, *args, **kwargs):
        if 'admin_slug' not in session or session['admin_slug'] != slug:
            return redirect(url_for('login', slug=slug))
        return f(slug, *args, **kwargs)
    return decorated_function


def format_page_data(page_data):
    """
    Formata os dados da página para o template.
    
    Args:
        page_data (dict): Dados brutos do Directus
    
    Returns:
        dict: Dados formatados para o template
    """
    if not page_data:
        return None
    
    formatted = {
        'titulo': page_data.get('titulo', 'Página de Amor'),
        'mensagem': page_data.get('mensagem', ''),
        'cor_fundo': page_data.get('cor_fundo', '#FF6B8B'),
        'slug': page_data.get('slug', ''),
        'fotos': [],
        'spotify_url': None
    }
    
    # Processar fotos
    if 'fotos' in page_data and page_data['fotos']:
        for foto in page_data['fotos']:
            if isinstance(foto, dict) and 'url' in foto:
                formatted['fotos'].append(foto['url'])
    
    # Processar URL do Spotify
    spotify_url = page_data.get('spotify_url')
    if spotify_url:
        formatted['spotify_url'] = ensure_embed_url(spotify_url)
    
    return formatted


# ============================================================================
# ROTAS DA APLICAÇÃO
# ============================================================================

@app.route('/<slug>')
def love_page(slug):
    """
    Rota pública - Renderiza a página personalizada.
    
    Args:
        slug (str): Slug da página
    
    Returns:
        Response: Template renderizado ou 404
    """
    # Buscar dados da página
    page_data = get_page_by_slug(slug)
    
    if not page_data:
        # Página não encontrada
        return render_template('404.html', slug=slug), 404
    
    # Formatar dados para o template
    formatted_data = format_page_data(page_data)
    
    # Adicionar ano atual para o rodapé
    current_year = datetime.now().year
    
    # Renderizar template
    return render_template(
        'love_page.html',
        page=formatted_data,
        current_year=current_year
    )


@app.route('/<slug>/login', methods=['GET', 'POST'])
def login(slug):
    """
    Rota de login - Autenticação para acessar o dashboard.
    
    Args:
        slug (str): Slug da página
    """
    # Se já estiver logado, redirecionar para o dashboard
    if 'admin_slug' in session and session['admin_slug'] == slug:
        return redirect(url_for('dashboard', slug=slug))
    
    error = None
    
    if request.method == 'POST':
        password = request.form.get('password', '')
        
        if verify_password(slug, password):
            # Login bem-sucedido
            session['admin_slug'] = slug
            session.permanent = True
            logger.info(f"Login bem-sucedido para: {slug}")
            return redirect(url_for('dashboard', slug=slug))
        else:
            error = "Senha incorreta. Tente novamente."
            logger.warning(f"Tentativa de login falhou para: {slug}")
    
    # Buscar título da página para mostrar no formulário de login
    page_data = get_page_by_slug(slug)
    page_title = page_data.get('titulo', 'Minha Página') if page_data else slug
    
    return render_template(
        'login.html',
        slug=slug,
        page_title=page_title,
        error=error
    )


@app.route('/<slug>/dashboard', methods=['GET', 'POST'])
@login_required
def dashboard(slug):
    """
    Dashboard - Interface de administração da página.
    
    Args:
        slug (str): Slug da página
    """
    page_data = get_page_by_slug(slug)
    if not page_data:
        abort(404, description="Página não encontrada")
    
    formatted_data = format_page_data(page_data)
    current_year = datetime.now().year
    
    if request.method == 'POST':
        # Processar atualização da página
        update_data = {}
        
        # Campos de texto
        update_data['titulo'] = request.form.get('titulo', '').strip()
        update_data['mensagem'] = request.form.get('mensagem', '').strip()
        update_data['cor_fundo'] = request.form.get('cor_fundo', '#FF6B8B').strip()
        
        # URL do Spotify
        spotify_url = request.form.get('spotify_url', '').strip()
        if spotify_url:
            update_data['spotify_url'] = ensure_embed_url(spotify_url)
        else:
            update_data['spotify_url'] = None
        
        # Processar upload de novas fotos
        uploaded_files = request.files.getlist('fotos')
        new_foto_ids = []
        
        # Manter fotos existentes
        if 'fotos' in page_data and page_data['fotos']:
            for foto in page_data['fotos']:
                if isinstance(foto, dict) and 'id' in foto:
                    new_foto_ids.append(foto['id'])
        
        # Fazer upload de novas fotos
        for file_storage in uploaded_files:
            if file_storage and file_storage.filename:
                file_data = upload_file(file_storage)
                if file_data and 'id' in file_data:
                    new_foto_ids.append(file_data['id'])
        
        update_data['fotos'] = new_foto_ids
        
        # Atualizar página no Directus
        updated_page = update_page(slug, update_data)
        
        if updated_page:
            formatted_data = format_page_data(updated_page)
            success_message = "Página atualizada com sucesso!"
            return render_template(
                'dashboard.html',
                page=formatted_data,
                current_year=current_year,
                success_message=success_message
            )
        else:
            error_message = "Erro ao atualizar a página. Tente novamente."
            return render_template(
                'dashboard.html',
                page=formatted_data,
                current_year=current_year,
                error_message=error_message
            )
    
    # GET request - mostrar dashboard
    return render_template(
        'dashboard.html',
        page=formatted_data,
        current_year=current_year
    )


@app.route('/<slug>/logout')
@login_required
def logout(slug):
    """
    Rota de logout - Encerra a sessão.
    
    Args:
        slug (str): Slug da página
    """
    session.pop('admin_slug', None)
    logger.info(f"Logout para: {slug}")
    return redirect(url_for('love_page', slug=slug))


@app.route('/api/spotify-search', methods=['GET'])
def spotify_search_api():
    """
    API endpoint para busca de músicas no Spotify.
    
    Query parameters:
        q (str): Termo de busca
        limit (int, opcional): Número de resultados (padrão: 10)
    
    Returns:
        JSON: Lista de resultados ou mensagem de erro
    """
    query = request.args.get('q', '').strip()
    limit = request.args.get('limit', 10, type=int)
    
    if not query or len(query) < 2:
        return jsonify({
            'error': 'Query muito curta. Use pelo menos 2 caracteres.',
            'results': []
        }), 400
    
    try:
        tracks = search_tracks(query, limit)
        
        return jsonify({
            'query': query,
            'count': len(tracks),
            'results': tracks
        })
        
    except Exception as e:
        logger.error(f"Erro na busca do Spotify: {str(e)}")
        return jsonify({
            'error': 'Erro interno no servidor',
            'results': []
        }), 500


@app.route('/health')
def health_check():
    """
    Endpoint de health check para monitoramento.
    
    Returns:
        JSON: Status da aplicação
    """
    health_status = {
        'status': 'healthy',
        'timestamp': datetime.now().isoformat(),
        'directus': 'unknown',
        'spotify': 'unknown'
    }
    
    # Verificar conexão com Directus
    try:
        response = requests.get(
            f"{DIRECTUS_URL}/server/ping",
            timeout=5
        )
        health_status['directus'] = 'connected' if response.status_code == 200 else 'error'
    except:
        health_status['directus'] = 'disconnected'
    
    # Verificar conexão com Spotify
    try:
        token = get_spotify_token()
        health_status['spotify'] = 'connected' if token else 'error'
    except:
        health_status['spotify'] = 'disconnected'
    
    return jsonify(health_status), 200


@app.errorhandler(404)
def page_not_found(e):
    """
    Handler para páginas não encontradas.
    """
    slug = request.path.strip('/')
    return render_template('404.html', slug=slug), 404


@app.errorhandler(500)
def internal_server_error(e):
    """
    Handler para erros internos do servidor.
    """
    logger.error(f"Erro interno do servidor: {str(e)}")
    return render_template('500.html'), 500


# ============================================================================
# INICIALIZAÇÃO DA APLICAÇÃO
# ============================================================================

if __name__ == '__main__':
    # Criar diretório para sessões se não existir
    if not os.path.exists(app.config['SESSION_FILE_DIR']):
        os.makedirs(app.config['SESSION_FILE_DIR'])
    
    # Verificar templates necessários
    required_templates = ['love_page.html', 'dashboard.html', 'login.html', '404.html', '500.html']
    
    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=(os.getenv('FLASK_ENV') == 'development')
    )