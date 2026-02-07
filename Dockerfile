# Use uma imagem base oficial do Python leve
FROM python:3.9-slim

# Define o diretório de trabalho dentro do container
WORKDIR /app

# Variáveis de ambiente para otimizar o Python
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Instala dependências do sistema necessárias para compilar pacotes (se precisar)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

# Copia o arquivo de requisitos primeiro (para aproveitar o cache do Docker)
COPY requirements.txt .

# Instala as dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia todo o resto do código do projeto para o container
COPY . .

# Cria o diretório para sessões do Flask (baseado no seu app.py)
RUN mkdir -p .flask_session

# Expõe a porta que o Gunicorn vai usar (3000 é padrão do Dokploy para apps web)
EXPOSE 3000

# Comando para iniciar a aplicação usando Gunicorn
# -w 4: 4 workers (processos) para aguentar carga
# -b 0.0.0.0:3000: Escuta em todas as interfaces na porta 3000
CMD ["gunicorn", "-w", "4", "-b", "0.0.0.0:3000", "app:app"]