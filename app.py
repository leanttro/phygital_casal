"""
Phygital SaaS - Flask Application
Infra: VPS Dokploy + PostgreSQL (Dados) + Directus (Arquivos)
Autor: Phygital Team
Data: 2026 (Atualizado - Segurança Avançada + Recuperação de Senha por E-mail)
"""

import os
import re
import json
import logging
import requests
import time
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from functools import wraps
from datetime import datetime
from flask import Flask, render_template, request, session, redirect, url_for, jsonify, abort
from flask_session import Session
from flask_sqlalchemy import SQLAlchemy
from werkzeug.utils import secure_filename
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer
from dotenv import load_dotenv

# ============================================================================
# CONFIGURAÇÃO INICIAL E SEGURANÇA
# ============================================================================

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.secret_key = os.getenv('SECRET_KEY', 'dev-secret-key-change-in-production')

app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_USE_SIGNER'] = True
app.config['SESSION_FILE_DIR'] = './.flask_session/'
Session(app)

# Serializador para criar tokens de e-mail com tempo de expiração
serializer = URLSafeTimedSerializer(app.secret_key)

# Controle simples de tentativas de login na memória (Anti Força Bruta)
failed_logins = {}

# ============================================================================
# BARREIRA ANTI ROBÔS
# ============================================================================
@app.before_request
def block_bots():
    user_agent = request.headers.get('User-Agent', '').lower()
    malicious_bots = ['curl', 'wget', 'nikto', 'nmap', 'sqlmap', 'python-requests']
    if any(bot in user_agent for bot in malicious_bots):
        abort(403)

# ============================================================================
# CONFIGURAÇÃO DO BANCO DE DADOS (POSTGRESQL) E E-MAIL
# ============================================================================

DB_USER = os.getenv('DB_USER')
DB_PASS = os.getenv('DB_PASS')
DB_HOST = os.getenv('DB_HOST')
DB_PORT = os.getenv('DB_PORT')
DB_NAME = os.getenv('DB_NAME')

MAIL_USERNAME = os.getenv('MAIL_USERNAME')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD')
MAIL_SERVER = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
MAIL_PORT = int(os.getenv('MAIL_PORT', 587))

if not all([DB_USER, DB_PASS, DB_HOST, DB_NAME]):
    logger.warning("Variáveis de banco de dados incompletas no ENV. Verifique o Dokploy.")

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

DIRECTUS_URL = os.getenv('DIRECTUS_URL', '').rstrip('/')
DIRECTUS_TOKEN = os.getenv('DIRECTUS_TOKEN')
DIRECTUS_HEADERS = {
    'Authorization': f'Bearer {DIRECTUS_TOKEN}'
}

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
    admin_password = db.Column(db.String(255)) 
    
    nome = db.Column(db.String(100))
    sobrenome = db.Column(db.String(100))
    email = db.Column(db.String(120)) # Novo campo essencial para recuperação
    whatsapp = db.Column(db.String(50))
    
    theme = db.Column(db.String(50), default='classic') 
    font_style = db.Column(db.String(50), default='sans') 
    layout_order = db.Column(db.Text, default='header,text,spotify,timeline,photos,footer')
    
    gallery_title = db.Column(db.String(200), default='Nossa Galeria')
    font_color = db.Column(db.String(20), default='#374151') 
    title_color = db.Column(db.String(20), default='#111827')
    font_size = db.Column(db.String(20), default='medium') 
    aspect_ratio = db.Column(db.String(20), default='square') 

    timeline_data = db.Column(db.Text, default='[]')

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    
    photos = db.relationship('PagePhoto', backref='page', lazy=True, cascade="all, delete-orphan", order_by="PagePhoto.display_order")

class PagePhoto(db.Model):
    __tablename__ = 'page_photos'
    
    id = db.Column(db.Integer, primary_key=True)
    page_id = db.Column(db.Integer, db.ForeignKey('love_pages.id'), nullable=False)
    image_url = db.Column(db.String(500), nullable=False)
    display_order = db.Column(db.Integer, default=0)
    uploaded_at = db.Column(db.DateTime, default=datetime.utcnow)

# ============================================================================
# FUNÇÕES DE SERVIÇO (E-MAIL, DIRECTUS, SPOTIFY)
# ============================================================================

def send_reset_email(user_email, reset_url):
    if not MAIL_USERNAME or not MAIL_PASSWORD:
        logger.error("Credenciais de e-mail não configuradas no ENV.")
        return False
    try:
        msg = MIMEMultipart()
        msg['From'] = MAIL_USERNAME
        msg['To'] = user_email
        msg['Subject'] = "Recuperação de Senha - QR Code Love"

        body = f"Olá!\n\nVocê solicitou a redefinição da sua senha de acesso à página.\n\nClique no link abaixo para criar uma nova senha. Este link expira em 30 minutos.\n\n{reset_url}\n\nSe você não fez este pedido, basta ignorar este e-mail."
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        server = smtplib.SMTP(MAIL_SERVER, MAIL_PORT)
        server.starttls()
        server.login(MAIL_USERNAME, MAIL_PASSWORD)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        logger.error(f"Erro ao enviar e-mail de recuperação: {e}")
        return False

def upload_file_to_directus(file_storage):
    try:
        url = f"{DIRECTUS_URL}/files"
        filename = secure_filename(file_storage.filename)
        
        files = {'file': (filename, file_storage, file_storage.mimetype)}
        headers = {'Authorization': f'Bearer {DIRECTUS_TOKEN}'}
        
        response = requests.post(url, headers=headers, files=files, timeout=30)
        
        if response.status_code == 200:
            file_data = response.json().get('data', {})
            file_id = file_data.get('id')
            if file_id:
                full_url = f"{DIRECTUS_URL}/assets/{file_id}"
                logger.info(f"Upload Directus sucesso (URL Limpa): {full_url}")
                return full_url
        
        logger.error(f"Erro Directus Upload: {response.status_code} - {response.text}")
        return None
    except Exception as e:
        logger.error(f"Exceção no Upload: {str(e)}")
        return None

def get_spotify_token():
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
    if not url: return None
    clean_url = url.split('?')[0]
    if 'open.spotify.com/embed' in clean_url: return clean_url
    if '/track/' in clean_url:
        track_id = clean_url.split('/track/')[-1]
        return f"https://open.spotify.com/embed/track/{track_id}"
    elif '/playlist/' in clean_url:
        playlist_id = clean_url.split('/playlist/')[-1]
        return f"https://open.spotify.com/embed/playlist/{playlist_id}"
    return url

def search_tracks(query, limit=10):
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
    return redirect("https://leanttro.com")

@app.route('/cadastro', methods=['GET', 'POST'])
def cadastro():
    error = None
    if request.method == 'POST':
        raw_slug = request.form.get('slug', '').strip().lower()
        slug = re.sub(r'[^a-z0-9-]', '', raw_slug)

        nome = request.form.get('nome', '').strip()
        sobrenome = request.form.get('sobrenome', '').strip()
        email = request.form.get('email', '').strip()
        whatsapp = request.form.get('whatsapp', '').strip()
        
        password_plain = request.form.get('admin_password', '').strip()

        if not slug:
            error = "O link é obrigatório."
        elif not email:
            error = "O e-mail é obrigatório para recuperação de senha."
        elif not password_plain:
             error = "A senha é obrigatória."
        else:
            existing = LovePage.query.filter_by(slug=slug).first()
            existing_email = LovePage.query.filter_by(email=email).first()
            if existing:
                error = "Este link já está em uso. Escolha outro."
            elif existing_email:
                error = "Este e-mail já está cadastrado em outra página."
            else:
                try:
                    hashed_password = generate_password_hash(password_plain)

                    new_page = LovePage(
                        slug=slug,
                        nome=nome,
                        sobrenome=sobrenome,
                        email=email,
                        whatsapp=whatsapp,
                        admin_password=hashed_password, 
                        title=f"Página de {nome}",
                        message="Bem-vindos à nossa história de amor!"
                    )
                    db.session.add(new_page)
                    db.session.commit()
                    
                    session['admin_slug'] = slug
                    return redirect(url_for('login', slug=slug))
                except Exception as e:
                    db.session.rollback()
                    logger.error(f"Erro no cadastro: {e}")
                    error = "Erro ao criar conta. Tente novamente."

    return render_template('cadastro.html', error=error, current_year=datetime.now().year)

@app.route('/esqueci-senha', methods=['GET', 'POST'])
def esqueci_senha():
    error = None
    success = None
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        page = LovePage.query.filter_by(email=email).first()
        if page:
            token = serializer.dumps(page.email, salt='email-reset-salt')
            reset_url = url_for('reset_senha', token=token, _external=True)
            if send_reset_email(page.email, reset_url):
                success = "Um link de redefinição de senha foi enviado para o seu e-mail."
            else:
                error = "Tivemos um problema ao enviar o e-mail. Verifique o servidor."
        else:
            success = "Se o e-mail existir na nossa base, um link foi enviado para você."
    return render_template('esqueci_senha.html', error=error, success=success)

@app.route('/reset-senha/<token>', methods=['GET', 'POST'])
def reset_senha(token):
    try:
        email = serializer.loads(token, salt='email-reset-salt', max_age=1800)
    except Exception:
        return render_template('reset_senha.html', error="O link de recuperação é inválido ou expirou.")

    error = None
    success = None
    if request.method == 'POST':
        new_password = request.form.get('password')
        if new_password:
            page = LovePage.query.filter_by(email=email).first()
            if page:
                page.admin_password = generate_password_hash(new_password)
                db.session.commit()
                success = "Sua senha foi atualizada com sucesso! Você já pode fazer login."
        else:
            error = "A senha não pode ficar em branco."

    return render_template('reset_senha.html', error=error, success=success, token=token)

@app.route('/<slug>')
def love_page(slug):
    if slug == '001':
        return redirect(f"https://motoboys.leanttro.com/{slug}", code=302)

    try:
        clean_slug = slug.strip().lower()
        page = LovePage.query.filter_by(slug=clean_slug).first()
        
        if not page:
            return render_template('404.html', slug=slug), 404
        
        if page.theme == 'elegant':
            template_name = 'theme_elegant.html'
        elif page.theme == 'stitch':
            template_name = 'stitch.html'
        else:
            template_name = 'index.html'
        
        fonts_map = {
            'sans': 'Inter, sans-serif',
            'serif': 'Playfair Display, serif',
            'handwriting': 'Great Vibes, cursive',
            'mono': 'Fira Code, monospace'
        }
        
        size_map = {
            'small': '1.0rem',
            'medium': '1.25rem',
            'large': '1.75rem'
        }

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
    page = LovePage.query.filter_by(slug=slug).first_or_404()
    is_logged_in = (session.get('admin_slug') == slug)
    
    error = None
    success = None
    
    if request.method == 'POST':
        if 'password' in request.form:
            # SISTEMA DE PROTEÇÃO CONTRA FORÇA BRUTA
            ip = request.remote_addr
            now = time.time()
            failed_logins[ip] = [t for t in failed_logins.get(ip, []) if now - t < 300]
            
            if len(failed_logins[ip]) >= 5:
                error = "Muitas tentativas falhas. Aguarde 5 minutos por segurança."
            else:
                password_attempt = request.form.get('password')
                
                if page.admin_password and check_password_hash(page.admin_password, password_attempt):
                    session['admin_slug'] = slug
                    session.permanent = True
                    is_logged_in = True
                    # Reseta as falhas deste IP se logar com sucesso
                    if ip in failed_logins:
                        del failed_logins[ip]
                    logger.info(f"Login efetuado para: {slug}")
                    return redirect(url_for('login', slug=slug))
                else:
                    failed_logins[ip].append(now)
                    error = "Senha incorreta."
                    logger.warning(f"Falha login para: {slug}")

        elif is_logged_in:
            try:
                if isinstance(page.timeline_data, list):
                    current_timeline = page.timeline_data
                else:
                    try:
                        current_timeline = json.loads(page.timeline_data) if page.timeline_data else []
                    except:
                        current_timeline = []

                delete_id = request.form.get('delete_photo_id')
                delete_event_idx = request.form.get('delete_event_idx')
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
                
                else:
                    page.title = request.form.get('titulo', page.title).strip()
                    page.message = request.form.get('mensagem', page.message).strip()
                    page.background_color = request.form.get('cor_fundo', page.background_color)
                    
                    page.gallery_title = request.form.get('gallery_title', page.gallery_title).strip()
                    page.font_color = request.form.get('font_color', page.font_color)
                    page.title_color = request.form.get('title_color', page.title_color)
                    page.font_size = request.form.get('font_size', page.font_size)
                    page.aspect_ratio = request.form.get('aspect_ratio', page.aspect_ratio)
                    
                    page.theme = request.form.get('theme', 'classic')
                    page.font_style = request.form.get('font_style', 'sans')
                    
                    new_layout = request.form.get('layout_order')
                    if new_layout:
                        page.layout_order = new_layout
                    
                    new_spotify = request.form.get('spotify_url', '').strip()
                    if new_spotify:
                        page.spotify_url = ensure_embed_url(new_spotify)

                    if new_pass_change and new_pass_change.strip():
                        page.admin_password = generate_password_hash(new_pass_change.strip())
                        success = "Senha alterada e dados salvos!"

                    new_event_date = request.form.get('new_event_date')
                    new_event_title = request.form.get('new_event_title')
                    if new_event_date and new_event_title:
                        current_timeline.append({
                            'date': new_event_date,
                            'title': new_event_title.strip()
                        })
                        current_timeline.sort(key=lambda x: x['date'])
                        page.timeline_data = json.dumps(current_timeline)
                    
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

    if page.photos:
        page.photos.sort(key=lambda x: x.display_order)

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
    query = request.args.get('q', '')
    if not query: return jsonify([])
    results = search_tracks(query)
    return jsonify({'results': results})

@app.route('/admin/reset/<slug>/<new_password>')
def admin_force_reset(slug, new_password):
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
            db.create_all()
        except Exception as e:
            logger.error(f"Erro ao inicializar DB: {e}")

    app.run(
        host='0.0.0.0',
        port=int(os.getenv('PORT', 5000)),
        debug=(os.getenv('FLASK_ENV') == 'development')
    )