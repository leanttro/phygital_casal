"""
Phygital SaaS - Flask Application
Infra: VPS Dokploy + PostgreSQL (Dados) + Directus (Arquivos)
Autor: Phygital Team
Data: 2026 (Atualizado - Versão Completa e Segura - Hash de Senha + Token Oculto + Admin Reset)
"""

import os
import re
import json
import logging
import requests
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, abort
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash # IMPORT DE SEGURANÇA
from dotenv import load_dotenv

# ============================================================================
# CONFIGURAÇÃO INICIAL
# ============================================================================

# Carrega variáveis do arquivo .env (se existir localmente)
load_dotenv()

# Configurar logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# SEGURANÇA: Chave secreta deve vir do ambiente. Use um valor padrão apenas em dev.
app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

# Configurar sessão (Filesystem para persistir entre restarts do container)
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)

# ============================================================================
# CONFIGURAÇÃO DO BANCO DE DADOS (POSTGRESQL)
# ============================================================================

# Credenciais lidas das Variáveis de Ambiente (Segurança)
DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASS')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

# Verifica se as variáveis essenciais existem
if not all([DB_USER, DB_PASS, DB_HOST, DB_NAME]):
    logger.warning("⚠️ Variáveis de banco de dados incompletas no ENV. Verifique o Dokploy.")

# Monta a string de conexão
app.config['SQLALCHEMY_DATABASE_URI'] = f"postgresql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Opções para manter a conexão viva (evitar erros de timeout)
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = {
    "pool_pre_ping": True, 
    "pool_recycle": 300,
}

db = SQLAlchemy(app)

# ============================================================================
# CONSTANTES EXTERNAS
# ============================================================================

# Directus (Storage de Arquivos)
DIRECTUS_URL = os.getenv('DIRECTUS_URL', '').rstrip('/')
DIRECTUS_TOKEN = os.getenv('DIRECTUS_TOKEN')
DIRECTUS_HEADERS = {
    'Authorization': f'Bearer {DIRECTUS_TOKEN}'
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
    # Aumentei para 255 para caber o Hash gerado pelo Werkzeug com segurança
    admin_password = db.Column(db.String(255)) 
    
    # NOVOS CAMPOS PARA CADASTRO
    nome = db.Column(db.String(100))
    sobrenome = db.Column(db.String(100))
    whatsapp = db.Column(db.String(50))
    
    # --- NOVOS CAMPOS PARA PERSONALIZAÇÃO ---
    theme = db.Column(db.String(50), default='classic') # ex: classic, elegant, modern
    font_style = db.Column(db.String(50), default='sans') # ex: sans, serif, handwriting
    layout_order = db.Column(db.Text, default='header,text,spotify,timeline,photos,footer')
    
    # NOVAS ADIÇÕES SOLICITADAS:
    gallery_title = db.Column(db.String(200), default='Nossa Galeria')
    font_color = db.Column(db.String(20), default='#374151') 
    title_color = db.Column(db.String(20), default='#111827')
    font_size = db.Column(db.String(20), default='medium') # small, medium, large
    aspect_ratio = db.Column(db.String(20), default='square') # square ou story

    # CAMPO DE TIMELINE COMO JSON PARA EVITAR TABELA EXTRA
    timeline_data = db.Column(db.Text, default='[]')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    # Relacionamento One-to-Many com fotos
    photos = db.relationship('PagePhoto', backref='page', lazy=True, cascade="all, delete-orphan", order_by="PagePhoto.display_order")

class PagePhoto(db.Model):
    __tablename__ = 'page_photos'
    
    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey('love_pages.id'), nullable=False)
    image_url = db.Column(db.String(500), nullable=False) # URL do Directus
    display_order = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================================
# HELPER FUNCTIONS - DIRECTUS & UPLOAD
# ============================================================================

def upload_file_to_directus(file_storage):
    """
    Envia arquivo para o Directus e retorna a URL pública do asset.
    IMPORTANTE: O token é usado APENAS no backend. A URL retornada é pública.
    Certifique-se que a role 'Public' no Directus tem permissão de LEITURA (Read) em 'directus_files'.
    """
    try:
        url = f"{DIRECTUS_URL}/files"
        filename = secure_filename(file_storage.filename)
        
        # Prepara o arquivo para envio via multipart/form-data
        files = {'file': (filename, file_storage, file_storage.mimetype)}
        
        # Headers (sem Content-Type json) - Token Seguro aqui
        headers = {'Authorization': f'Bearer {DIRECTUS_TOKEN}'}
        
        response = requests.post(url, headers=headers, files=files, timeout=30)
        
        if response.status_code == 200:
            file_data = response.json().get('data', {})
            file_id = file_data.get('id')
            if file_id:
                # --- MODIFICAÇÃO DE SEGURANÇA ---
                # NÃO embutimos mais o token na URL.
                # A URL agora depende da permissão pública do Directus.
                full_url = f"{DIRECTUS_URL}/assets/{file_id}"
                
                logger.info(f"Upload Directus sucesso (URL Limpa): {full_url}")
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
    
    # Limpa query params extras que podem quebrar o embed
    clean_url = url.split('?')[0]
    
    if 'open.spotify.com/embed' in clean_url: return clean_url
    
    # Converte link de track/playlist normal para embed
    if '/track/' in clean_url:
        track_id = clean_url.split('/track/')[-1]
        return f"https://open.spotify.com/embed/track/{track_id}"
    elif '/playlist/' in clean_url:
        playlist_id = clean_url.split('/playlist/')[-1]
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
                    'image_url': item['album']['images'][0]['url'] if item['album']['images'] else '',
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

@app.route('/')
def home():
    """Rota da Home (Para evitar 404 na raiz)"""
    return redirect("https://leanttro.com")

@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    """Rota de Cadastro de novos usuários/páginas"""
    error = None
    if request.method == 'POST':
        # SEGURANÇA: Limpa e padroniza o slug no backend
        raw_slug = request.form.get('slug', '').strip().lower()
        # Remove caracteres indesejados se passaram pelo front
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug)

        nome = request.form.get('nome', '').strip()
        sobrenome = request.form.get('sobrenome', '').strip()
        whatsapp = request.form.get('whatsapp', '').strip()
        
        # SEGURANÇA: Captura senha e gera Hash
        password_plain = request.form.get('admin_password', '').strip()

        # Validação básica
        if not slug:
            error = "O link é obrigatório."
        elif not password_plain:
             error = "A senha é obrigatória."
        else:
            # Verifica se o slug já existe
            existing = LovePage.query.filter_by(slug=slug).first()
            if existing:
                error = "Este link já está em uso. Escolha outro."
            else:
                try:
                    # Gera o Hash seguro da senha
                    hashed_password = generate_password_hash(password_plain)

                    new_page = LovePage(
                        slug=slug,
                        nome=nome,
                        sobrenome=sobrenome,
                        whatsapp=whatsapp,
                        admin_password=hashed_password, # Salva o Hash, nunca a senha pura
                        title=f"Página de {nome}",
                        message="Bem-vindos à nossa história de amor!"
                    )
                    db.session.add(new_page)
                    db.session.commit()
                    
                    # Loga o usuário automaticamente após cadastro
                    session['admin_slug'] = slug
                    return redirect(url_for('login', slug=slug))
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Erro no cadastro: {e}")
                    error = "Erro ao criar conta. Tente novamente."

    return render_template('cadastro.html', error=error, current_year=datetime.now().year)

@app.route('/<slug>')
def love_page(slug):
    """Rota Pública - Renderiza página do banco com o TEMA escolhido"""
    
    # ============================================================
    # ENXERTO SOS MOTOBOY - VERIFICAÇÃO DE EMERGÊNCIA
    # ============================================================
    try:
        # Tenta buscar na coleção 'motoboys' do Directus
        # Reutilizamos a mesma URL e Token que já existem nas variáveis de ambiente
        sos_headers = {'Authorization': f'Bearer {DIRECTUS_TOKEN}'}
        sos_url = f"{DIRECTUS_URL}/items/motoboys?filter[slug][_eq]={slug}&limit=1"

        # Timeout curto (2s) para não travar o carregamento da Love Page se não for motoboy
        sos_response = requests.get(sos_url, headers=sos_headers, timeout=2)

        if sos_response.status_code == 200:
            sos_data = sos_response.json().get('data')
            if sos_data:
                # É UM MOTOBOY! Renderiza o template de emergência imediatamente
                motoboy = sos_data[0]
                
                # Helper interno para imagem do SOS
                def get_sos_img(img_id):
                    base = DIRECTUS_URL.rstrip('/')
                    return f"{base}/assets/{img_id}" if img_id else "https://placehold.co/400x400?text=Sem+Foto"
                
                motoboy['foto_url'] = get_sos_img(motoboy.get('foto'))
                
                # Cálculo de idade para o SOS
                idade = ""
                if motoboy.get('data_nascimento'):
                    try:
                        born = datetime.strptime(motoboy['data_nascimento'], "%Y-%m-%d")
                        today = datetime.today()
                        idade = today.year - born.year - ((today.month, today.day) < (born.month, born.day))
                    except: pass

                # Retorna o template SOS e INTERROMPE a função aqui (não carrega love page)
                return render_template('sos_enxerto.html', m=motoboy, idade=idade)

    except Exception as e:
        # Se der erro na verificação do motoboy, apenas loga e SEGUE O FLUXO NORMAL
        logger.error(f"Check SOS Motoboy falhou ou não encontrado: {e}")
    # ============================================================
    # FIM DO ENXERTO
    # ============================================================

    try:
        # Garante slug limpo na busca
        clean_slug = slug.strip().lower()
        page = LovePage.query.filter_by(slug=clean_slug).first()
        
        if not page:
            # Se não achou no banco, renderiza o 404 personalizado
            return render_template('404.html', slug=slug), 404
        
        # --- LÓGICA DE TEMAS ---
        template_name = 'theme_elegant.html' if page.theme == 'elegant' else 'index.html'
        
        # --- LÓGICA DE FONTES E TAMANHOS ---
        fonts_map = {
            'sans': 'Inter, sans-serif',
            'serif': 'Playfair Display, serif',
            'handwriting': 'Great Vibes, cursive',
            'mono': 'Fira Code, monospace'
        }
        
        # Mapeamento de tamanhos para Tailwind/CSS
        size_map = {
            'small': '1.0rem',
            'medium': '1.25rem',
            'large': '1.75rem'
        }

        # --- CORREÇÃO DO JSON ---
        if isinstance(page.timeline_data, list):
            timeline_list = page.timeline_data
        else:
            try:
                timeline_list = json.loads(page.timeline_data) if page.timeline_data else []
            except:
                timeline_list = []

        return render_template(
            template_name,
            page=page,
            timeline_events=timeline_list,
            font_css=fonts_map.get(page.font_style, 'sans-serif'),
            font_size_val=size_map.get(page.font_size, '1.25rem'),
            current_year=datetime.now().year
        )
    except Exception as e:
        logger.error(f"Erro Crítico DB: {str(e)}")
        return render_template('500.html'), 500

@app.route('/<slug>/login', methods=['GET', 'POST'])
def login(slug):
    """
    Rota Unificada: Autenticação + Edição + Exclusão + Ordenação + Temas
    """
    page = LovePage.query.filter_by(slug=slug).first_or_404()
    
    # Verifica estado de login na sessão
    is_logged_in = (session.get('admin_slug') == slug)
    
    error = None
    success = None
    
    if request.method == 'POST':
        # --- CASO 1: Tentativa de Login (Formulário de Senha) ---
        if 'password' in request.form:
            password_attempt = request.form.get('password')
            
            # SEGURANÇA: Verifica Hash ao invés de texto plano
            # check_password_hash(hash_do_banco, senha_digitada)
            if page.admin_password and check_password_hash(page.admin_password, password_attempt):
                session['admin_slug'] = slug
                session.permanent = True
                is_logged_in = True
                logger.info(f"Login efetuado para: {slug}")
                return redirect(url_for('login', slug=slug))
            else:
                error = "Senha incorreta."
                logger.warning(f"Falha login para: {slug}")

        # --- CASO 2: Ações do Painel (Salvar, Excluir, Ordenar) ---
        elif is_logged_in:
            try:
                # --- CORREÇÃO DO JSON ---
                if isinstance(page.timeline_data, list):
                    current_timeline = page.timeline_data
                else:
                    try:
                        current_timeline = json.loads(page.timeline_data) if page.timeline_data else []
                    except:
                        current_timeline = []

                # --- Ação A: EXCLUIR FOTO ---
                delete_id = request.form.get('delete_photo_id')
                
                # --- Ação A2: EXCLUIR EVENTO TIMELINE (VIA INDEX) ---
                delete_event_idx = request.form.get('delete_event_idx')

                # --- Ação A3: TROCA DE SENHA PELO USUÁRIO (NOVO) ---
                new_pass_change = request.form.get('new_password_change')

                if delete_id:
                    try:
                        photo_id = int(delete_id)
                        photo_to_delete = PagePhoto.query.get(photo_id)
                        if photo_to_delete and photo_to_delete.page_id == page.id:
                            db.session.delete(photo_to_delete)
                            db.session.commit()
                            db.session.refresh(page)
                            success = "Foto removida com sucesso!"
                        else:
                            error = "Erro ao remover: Foto não encontrada ou sem permissão."
                    except ValueError:
                        error = "ID de foto inválido."

                elif delete_event_idx is not None:
                    try:
                        idx = int(delete_event_idx)
                        if 0 <= idx < len(current_timeline):
                            current_timeline.pop(idx)
                            page.timeline_data = json.dumps(current_timeline)
                            db.session.commit()
                            success = "Evento da linha do tempo removido!"
                        else:
                            error = "Evento não encontrado."
                    except ValueError:
                        error = "Índice de evento inválido."
                
                # --- Ação B: SALVAR DADOS E UPLOAD (Se não for exclusão) ---
                else:
                    # 1. Atualizar Textos e Cor
                    page.title = request.form.get('titulo', page.title).strip()
                    page.message = request.form.get('mensagem', page.message).strip()
                    page.background_color = request.form.get('cor_fundo', page.background_color)
                    
                    # NOVAS CONFIGURAÇÕES DE PERSONALIZAÇÃO
                    page.gallery_title = request.form.get('gallery_title', page.gallery_title).strip()
                    page.font_color = request.form.get('font_color', page.font_color)
                    page.title_color = request.form.get('title_color', page.title_color)
                    page.font_size = request.form.get('font_size', page.font_size)
                    page.aspect_ratio = request.form.get('aspect_ratio', page.aspect_ratio)
                    
                    # 2. Atualizar Configurações de TEMA
                    page.theme = request.form.get('theme', 'classic')
                    page.font_style = request.form.get('font_style', 'sans')
                    
                    # === NOVO: Salva a Ordem das Seções ===
                    new_layout = request.form.get('layout_order')
                    if new_layout:
                        page.layout_order = new_layout
                    
                    # 3. Atualizar Spotify
                    new_spotify = request.form.get('spotify_url', '').strip()
                    if new_spotify:
                        page.spotify_url = ensure_embed_url(new_spotify)

                    # --- NOVO: Troca de Senha Segura ---
                    if new_pass_change and new_pass_change.strip():
                        page.admin_password = generate_password_hash(new_pass_change.strip())
                        success = "Senha alterada e dados salvos!"

                    # --- NOVO: Adicionar Evento na Timeline (JSON) ---
                    new_event_date = request.form.get('new_event_date')
                    new_event_title = request.form.get('new_event_title')
                    if new_event_date and new_event_title:
                        current_timeline.append({
                            'date': new_event_date,
                            'title': new_event_title.strip()
                        })
                        # Ordenar por data
                        current_timeline.sort(key=lambda x: x['date'])
                        page.timeline_data = json.dumps(current_timeline)
                    
                    # 4. Atualizar ORDEM das fotos existentes
                    for key, value in request.form.items():
                        if key.startswith('order_'):
                            try:
                                photo_id_str = key.split('_')[1]
                                photo_id = int(photo_id_str)
                                new_order = int(value)
                                photo = PagePhoto.query.get(photo_id)
                                if photo and photo.page_id == page.id:
                                    photo.display_order = new_order
                            except (ValueError, IndexError):
                                pass

                    # 5. Uploads (Flask -> Directus -> Postgres)
                    uploaded_files = request.files.getlist('fotos')
                    files_processed = 0
                    
                    for file in uploaded_files:
                        if file and file.filename:
                            directus_url = upload_file_to_directus(file)
                            if directus_url:
                                new_photo = PagePhoto(
                                    page_id=page.id,
                                    image_url=directus_url,
                                    display_order=99
                                )
                                db.session.add(new_photo)
                                files_processed += 1
                    
                    db.session.commit()
                    db.session.refresh(page)
                    
                    if not success:
                        success = "Página atualizada com sucesso!"
                    logger.info(f"Edição salva em Login: {slug}. Fotos novas: {files_processed}")
                
            except Exception as e:
                db.session.rollback()
                logger.error(f"Erro geral no POST: {e}")
                error = "Erro ao processar sua solicitação. Tente novamente."

    # Ordena as fotos para exibição no grid
    if page.photos:
        page.photos.sort(key=lambda x: x.display_order)

    # --- CORREÇÃO DO JSON PARA O LOGIN ---
    if isinstance(page.timeline_data, list):
        timeline_display = page.timeline_data
    else:
        try:
            timeline_display = json.loads(page.timeline_data) if page.timeline_data else []
        except:
            timeline_display = []

    return render_template(
        'login.html', 
        slug=slug, 
        page=page, 
        is_logged_in=is_logged_in,
        error=error,
        success=success,
        timeline_events=timeline_display,
        current_year=datetime.now().year
    )

@app.route('/<slug>/logout')
def logout(slug):
    session.pop('admin_slug', None)
    return redirect(url_for('love_page', slug=slug))

@app.route('/api/spotify-search')
def spotify_search_api():
    """API Interna para buscar músicas (AJAX)"""
    query = request.args.get('q', '')
    if not query: return jsonify([])
    results = search_tracks(query)
    return jsonify({'results': results})

# ============================================================================
# ROTA DE EMERGÊNCIA (GOD MODE - ADMIN USE ONLY)
# ============================================================================
@app.route('/admin/reset/<slug>/<new_password>')
def admin_force_reset(slug, new_password):
    """
    Rota para o Admin resetar senhas manualmente.
    Uso: seudominio.com/admin/reset/NOME-DO-CLIENTE/NOVA-SENHA?key=SUA_SECRET_KEY
    """
    # Proteção: Verifica se a chave secreta foi passada na URL
    secret_key_check = request.args.get('key')
    if secret_key_check != app.secret_key:
        return "ACESSO NEGADO: Chave de segurança incorreta.", 403

    page = LovePage.query.filter_by(slug=slug).first()
    if page:
        page.admin_password = generate_password_hash(new_password)
        db.session.commit()
        return f"SUCESSO: Senha de '{slug}' alterada para '{new_password}'. Hash gerado."
    
    return "ERRO: Página/Cliente não encontrado.", 404

@app.route('/health')
def health_check():
    """Healthcheck simples para monitoramento"""
    status = {'status': 'ok', 'db': 'unknown'}
    try:
        db.session.execute(db.text('SELECT 1'))
        status['db'] = 'connected'
    except Exception as e:
        status['db'] = str(e)
    return jsonify(status)

if __name__ == '__main__':
    if not os.path.exists(app.config['SESSION_FILE_DIR']):
        os.makedirs(app.config['SESSION_FILE_DIR'])

    with app.app_context():
        try:
            # Em produção, use Alembic para migrações em vez de create_all
            db.create_all()
        except Exception as e:
            logger.error(f"Erro ao inicializar DB: {e}")

    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=(os.getenv('FLASK_ENV') == 'development')
    )