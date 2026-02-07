"""
Phygital SaaS - Flask Application
Infra: VPS Dokploy + PostgreSQL (Dados) + Directus (Arquivos)
Autor: Phygital Team
Data: 2026 (Atualizado)
"""

import os
import re
import logging
import requests
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, abort
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# ============================================================================
# CONFIGURAÇÃO INICIAL
# ============================================================================

load_dotenv()

# Configurar logging (Mantendo seu padrão)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Configurar sessão
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)

# ============================================================================
# CONFIGURAÇÃO DO BANCO DE DADOS (POSTGRESQL)
# ============================================================================

# Credenciais fornecidas (Idealmente viriam do .env em produção)
DB_USER = os.getenv('DB_USER', 'postgres')
DB_PASS = os.getenv('DB_PASS', 'mwwzsq5rwujqmlcu') 
DB_HOST = os.getenv('DB_HOST', '213.199.56.207')
DB_PORT = os.getenv('DB_PORT', '5437') # Porta específica do Dokploy
DB_NAME = os.getenv('DB_NAME', 'postgres')

# String de conexão SQLAlchemy
app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True, 
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

# ============================================================================
# CONSTANTES EXTERNAS
# ============================================================================

# Directus (Apenas para Storage de Arquivos)
DIRECTUS_URL = os.getenv('DIRECTUS_URL', '').rstrip('/')
DIRECTUS_TOKEN = os.getenv('DIRECTUS_TOKEN')
DIRECTUS_HEADERS = {
    'Authorization': f'Bearer {DIRECTUS_TOKEN}'
    # Content-Type removido aqui para permitir multipart/form-data no upload
}

# Spotify Config
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')
SPOTIFY_TOKEN_URL = 'https://accounts.spotify.com/api/token'
SPOTIFY_API_BASE = 'https://api.spotify.com/v1'

# ============================================================================
# MODELOS DO BANCO DE DADOS (SCHEMA)
# ============================================================================

class LovePage(db.Model):
    __tablename__ = 'love_pages'
    
    id = db.Column(db.Integer, primary_key=True)
    slug = db.Column(db.String(100), unique=True, nullable=False, index=True)
    title = db.Column(db.String(200))
    message = db.Column(db.Text)
    background_color = db.Column(db.String(20), default='#FF6B8B')
    spotify_url = db.Column(db.String(500))
    admin_password = db.Column(db.String(100)) # Plaintext conforme solicitado
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamento One-to-Many com fotos
    photos = db.relationship('PagePhoto', backref='page', lazy=True, cascade="all, delete-orphan", order_by="PagePhoto.display_order")

class PagePhoto(db.Model):
    __tablename__ = 'page_photos'
    
    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey('love_pages.id'), nullable=False)
    image_url = db.Column(db.String(500), nullable=False) # URL completa do asset no Directus
    display_order = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================================
# HELPER FUNCTIONS - DIRECTUS & UPLOAD
# ============================================================================

def upload_file_to_directus(file_storage):
    """
    Envia arquivo para o Directus e retorna a URL pública do asset.
    """
    try:
        url = f"{DIRECTUS_URL}/files"
        filename = secure_filename(file_storage.filename)
        
        # Prepara o arquivo
        files = {'file': (filename, file_storage, file_storage.mimetype)}
        
        # Headers específicos para upload (sem content-type JSON)
        headers = {'Authorization': f'Bearer {DIRECTUS_TOKEN}'}
        
        response = requests.post(url, headers=headers, files=files, timeout=30)
        
        if response.status_code == 200:
            file_data = response.json().get('data', {})
            file_id = file_data.get('id')
            if file_id:
                full_url = f"{DIRECTUS_URL}/assets/{file_id}"
                logger.info(f"Upload Directus sucesso: {full_url}")
                return full_url
        
        logger.error(f"Erro Directus Upload: {response.status_code} - {response.text}")
        return None
    except Exception as e:
        logger.error(f"Exceção no Upload: {str(e)}")
        return None

# ============================================================================
# HELPER FUNCTIONS - SPOTIFY
# ============================================================================

def get_spotify_token():
    """Client Credentials Flow para Spotify"""
    try:
        auth_response = requests.post(
            SPOTIFY_TOKEN_URL,
            data={
                'grant_type': 'client_credentials',
                'client_id': SPOTIFY_CLIENT_ID,
                'client_secret': SPOTIFY_CLIENT_SECRET,
            },
            timeout=10
        )
        if auth_response.status_code == 200:
            return auth_response.json().get('access_token')
        return None
    except Exception as e:
        logger.error(f"Erro Token Spotify: {e}")
        return None

def ensure_embed_url(url):
    """Garante que a URL do Spotify esteja no formato Embed"""
    if not url: return None
    if 'open.spotify.com/embed' in url: return url
    
    # Converte link de track/playlist normal para embed
    if '/track/' in url:
        track_id = url.split('/track/')[-1].split('?')[0]
        return f"https://open.spotify.com/embed/track/{track_id}"
    elif '/playlist/' in url:
        playlist_id = url.split('/playlist/')[-1].split('?')[0]
        return f"https://open.spotify.com/embed/playlist/{playlist_id}"
        
    return url

def search_tracks(query, limit=10):
    """Busca músicas na API do Spotify"""
    token = get_spotify_token()
    if not token: return []
    
    headers = {'Authorization': f'Bearer {token}'}
    params = {'q': query, 'type': 'track', 'limit': limit, 'market': 'BR'}
    
    try:
        res = requests.get(f"{SPOTIFY_API_BASE}/search", headers=headers, params=params, timeout=10)
        if res.status_code == 200:
            items = res.json().get('tracks', {}).get('items', [])
            results = []
            for item in items:
                track_id = item.get('id')
                embed_url = f"https://open.spotify.com/embed/track/{track_id}"
                
                results.append({
                    'id': track_id,
                    'name': item.get('name'),
                    'artist': item['artists'][0]['name'] if item['artists'] else 'Desconhecido',
                    'image_url': item['album']['images'][0]['url'] if item['album']['images'] else None,
                    'embed_url': embed_url
                })
            return results
    except Exception as e:
        logger.error(f"Erro busca Spotify: {e}")
    return []

# ============================================================================
# DECORATORS
# ============================================================================

def login_required(f):
    @wraps(f)
    def decorated_function(slug, *args, **kwargs):
        if session.get('admin_slug') != slug:
            return redirect(url_for('login', slug=slug))
        return f(slug, *args, **kwargs)
    return decorated_function

# ============================================================================
# ROTAS
# ============================================================================

@app.route('/<slug>')
def love_page(slug):
    """Rota Pública - Renderiza página do banco PostgreSQL"""
    try:
        page = LovePage.query.filter_by(slug=slug).first()
        
        if not page:
            return render_template('404.html', slug=slug), 404
        
        return render_template(
            'love_page.html',
            page=page,
            current_year=datetime.now().year
        )
    except Exception as e:
        logger.error(f"Erro Crítico DB: {str(e)}")
        return render_template('500.html'), 500

@app.route('/<slug>/login', methods=['GET', 'POST'])
def login(slug):
    """
    Rota Unificada: Autenticação + Edição
    Se não logado: Mostra form de senha.
    Se logado: Mostra form de edição (antigo dashboard).
    """
    page = LovePage.query.filter_by(slug=slug).first_or_404()
    
    # Verifica estado de login
    is_logged_in = (session.get('admin_slug') == slug)
    
    error = None
    success = None
    
    if request.method == 'POST':
        # --- CASO 1: Tentativa de Login (Formulário de Senha) ---
        if 'password' in request.form:
            password = request.form.get('password')
            if page.admin_password == password:
                session['admin_slug'] = slug
                session.permanent = True
                is_logged_in = True
                logger.info(f"Login efetuado para: {slug}")
                # Redireciona para GET para limpar o POST da senha e mostrar o editor
                return redirect(url_for('login', slug=slug))
            else:
                error = "Senha incorreta."
                logger.warning(f"Falha login para: {slug}")

        # --- CASO 2: Salvar Edições (Formulário de Edição) ---
        # Só executa se já estiver logado
        elif is_logged_in:
            try:
                # 1. Atualizar Textos (PostgreSQL)
                page.title = request.form.get('titulo', page.title).strip()
                page.message = request.form.get('mensagem', page.message).strip()
                page.background_color = request.form.get('cor_fundo', page.background_color)
                
                # 2. Atualizar Spotify
                new_spotify = request.form.get('spotify_url', '').strip()
                if new_spotify:
                    page.spotify_url = ensure_embed_url(new_spotify)
                
                # 3. Uploads (Flask -> Directus -> Postgres)
                uploaded_files = request.files.getlist('fotos')
                files_processed = 0
                
                for file in uploaded_files:
                    if file and file.filename:
                        # Envia para Directus
                        directus_url = upload_file_to_directus(file)
                        
                        if directus_url:
                            # Salva referencia no Postgres
                            new_photo = PagePhoto(
                                page_id=page.id,
                                image_url=directus_url,
                                display_order=0 
                            )
                            db.session.add(new_photo)
                            files_processed += 1
                
                db.session.commit()
                logger.info(f"Edição salva em Login: {slug}. Fotos novas: {files_processed}")
                success = "Página atualizada com sucesso!"
                
            except Exception as e:
                db.session.rollback()
                logger.error(f"Erro ao salvar edição: {e}")
                error = "Erro ao salvar. Tente novamente."

    # Renderiza o template login.html passando a variável 'is_logged_in' para controlar o que aparece
    return render_template(
        'login.html', 
        slug=slug, 
        page=page, # Passa o objeto 'page' completo (com title, photos, etc)
        is_logged_in=is_logged_in,
        error=error,
        success=success,
        current_year=datetime.now().year
    )

@app.route('/<slug>/logout')
def logout(slug):
    session.pop('admin_slug', None)
    return redirect(url_for('love_page', slug=slug))

@app.route('/api/spotify-search')
def spotify_search_api():
    """API Interna"""
    query = request.args.get('q', '')
    if not query: return jsonify([])
    results = search_tracks(query)
    return jsonify({'results': results})

@app.route('/health')
def health_check():
    """Healthcheck simples"""
    status = {'status': 'ok', 'db': 'unknown'}
    try:
        db.session.execute(db.text('SELECT 1'))
        status['db'] = 'connected'
    except Exception as e:
        status['db'] = str(e)
    return jsonify(status)

# ============================================================================
# INICIALIZAÇÃO
# ============================================================================

if __name__ == '__main__':
    # Em produção, o Gunicorn que vai chamar o app, mas isso ajuda no dev local
    if not os.path.exists(app.config['SESSION_FILE_DIR']):
        os.makedirs(app.config['SESSION_FILE_DIR'])

    with app.app_context():
        # Cria tabelas se não existirem (Apenas para garantir, ideal é usar Migrations depois)
        try:
            db.create_all()
        except Exception as e:
            logger.error(f"Erro ao inicializar DB: {e}")

    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=(os.getenv('FLASK_ENV') == 'development')
    )