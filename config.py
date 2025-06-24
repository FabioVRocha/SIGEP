import os

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'uma-chave-secreta-muito-segura'
    # URI de conexão com o PostgreSQL
    # Formato: postgresql://username:password@host:port/database_name
    # Certifique-se de que o usuário, senha e nome do banco de dados estão corretos.
    # A senha agora é 'master123!' (sem caracteres especiais problemáticos)
    # Substitua 'master123!' pela senha real do seu usuário 'postgres' se for diferente.
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL') or \
                              'postgresql://postgres:postgres@45.161.184.156:5433/sigep_db'
    SQLALCHEMY_TRACK_MODIFICATIONS = False