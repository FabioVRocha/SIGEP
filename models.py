from extensions import db
from werkzeug.security import generate_password_hash, check_password_hash
import datetime
from sqlalchemy.types import JSON, Numeric, String
from CustomTypes import ImageBase64

# Tabela: funcionarios
class Funcionario(db.Model):
    __tablename__ = 'funcionarios'
    cpf = db.Column(db.String(14), primary_key=True)
    nome = db.Column(db.String(255), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    sexo = db.Column(db.String(20), nullable=True)
    pis = db.Column(db.String(14), unique=True)
    id_face = db.Column(db.String(255), unique=True)
    endereco = db.Column(db.String(255), nullable=True)
    bairro = db.Column(db.String(100), nullable=True)
    cidade = db.Column(db.String(100), nullable=True)
    estado = db.Column(db.String(50), nullable=True)
    cep = db.Column(db.String(10), nullable=True)
    telefone = db.Column(db.String(20), nullable=True)
    grau_instrucao = db.Column(db.String(100), nullable=True)
    codigo_banco = db.Column(db.String(10), nullable=True)
    nome_banco = db.Column(db.String(100), nullable=True)
    codigo_agencia = db.Column(db.String(20), nullable=True)
    numero_conta = db.Column(db.String(30), nullable=True)
    variacao_conta = db.Column(db.String(10), nullable=True)
    chave_pix = db.Column(db.String(255), nullable=True)
    observacao = db.Column(db.Text, nullable=True)
    foto_base64 = db.Column(ImageBase64, nullable=True)
    status = db.Column(db.String(20), nullable=True)

    # Relacionamentos
    dependentes = db.relationship('Dependente', backref='funcionario', lazy=True)
    ferias = db.relationship(
        'ControleFerias',
        back_populates='funcionario',
        lazy=True
    )
    banco_horas = db.relationship('BancoHoras', backref='funcionario', lazy=True)
    horas_extras = db.relationship('HorasExtras', backref='funcionario', lazy=True)
    registros_ponto = db.relationship('RegistroPonto', backref='funcionario', lazy=True)
    # Os atributos 'contratos', 'reajustes' e 'demissoes' vêm dos backrefs em suas classes filhas.

    def __repr__(self):
        return f"<Funcionario {self.nome} ({self.cpf})>"

# Tabela: dependentes
class Dependente(db.Model):
    __tablename__ = 'dependentes'
    cpf_dependente = db.Column(db.String(14), primary_key=True)
    nome_dependente = db.Column(db.String(255), nullable=False)
    data_nascimento = db.Column(db.Date, nullable=False)
    status = db.Column(db.Boolean, nullable=False, default=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    salario_familia = db.Column(db.Numeric(10, 2), default=0.00)

    def __repr__(self):
        return f"<Dependente {self.nome_dependente} ({self.cpf_dependente})>"

# Tabela: contratos_trabalho
class ContratoTrabalho(db.Model):
    __tablename__ = 'contratos_trabalho'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    setor = db.Column(db.String(100), nullable=False)
    funcao = db.Column(db.String(100), nullable=False)
    salario_inicial = db.Column(db.Numeric(10, 2), nullable=False)
    bonus = db.Column(db.Numeric(10, 2), default=0.00)
    regime_contratacao = db.Column(db.String(50), nullable=False)
    data_admissao = db.Column(db.Date, nullable=False)
    data_demissao = db.Column(db.Date)
    status = db.Column(db.Boolean, nullable=False, default=True)

    # Relacionamento; backref cria funcionario.contratos
    funcionario = db.relationship('Funcionario', backref='contratos', lazy=True)

    def __repr__(self):
        return f"<Contrato {self.id} - {self.cpf_funcionario}>"

# Tabela: reajustes_salariais
class ReajusteSalarial(db.Model):
    __tablename__ = 'reajustes_salariais'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    data_alteracao = db.Column(db.Date, nullable=False)
    percentual_reajuste_salario = db.Column(db.Numeric(5, 2), nullable=False)
    percentual_reajuste_bonus = db.Column(db.Numeric(5, 2), nullable=False)

    # Relacionamento; backref cria funcionario.reajustes
    funcionario = db.relationship('Funcionario', backref='reajustes', lazy=True)

    def __repr__(self):
        return f"<Reajuste {self.id} - {self.cpf_funcionario}>"

# Tabela: controle_ferias
class ControleFerias(db.Model):
    __tablename__ = 'controle_ferias'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    periodo_aquisitivo_inicio = db.Column(db.Date, nullable=False)
    periodo_aquisitivo_fim = db.Column(db.Date, nullable=False)
    ferias_gozadas_inicio = db.Column(db.Date)
    ferias_gozadas_fim = db.Column(db.Date)

    # Relacionamento corrigido com back_populates
    funcionario = db.relationship(
        'Funcionario',
        back_populates='ferias',
        lazy=True
    )

    def __repr__(self):
        return f"<Ferias {self.id} - {self.cpf_funcionario}>"

# Tabela: demissoes
class Demissao(db.Model):
    __tablename__ = 'demissoes'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    data_demissao = db.Column(db.Date, nullable=False)
    ultimo_dia_trabalhado = db.Column(db.Date, nullable=False)
    tipo_desligamento = db.Column(db.String(100), nullable=False)
    motivo_demissao = db.Column(db.Text, nullable=False)
    aviso_previo = db.Column(db.String(50), nullable=False)
    data_aviso_previo = db.Column(db.Date, nullable=True)
    quantidade_dias_aviso = db.Column(db.Integer, nullable=True)
    data_termino_aviso = db.Column(db.Date, nullable=True)

    # Relacionamento; backref cria funcionario.demissoes
    funcionario = db.relationship('Funcionario', backref='demissoes', lazy=True)

    def __repr__(self):
        return f"<Demissao {self.id} - {self.cpf_funcionario}>"

# Tabela: folha_pagamento (logs e status de fechamento)
class FolhaPagamento(db.Model):
    __tablename__ = 'folha_pagamento'
    id = db.Column(db.Integer, primary_key=True)
    mes_referencia = db.Column(db.Date, unique=True, nullable=False)
    data_fechamento = db.Column(db.DateTime, default=datetime.datetime.now)
    arquivo_remessa_gerado = db.Column(db.Text)
    travado = db.Column(db.Boolean, default=False)

    def __repr__(self):
        return f"<Folha Pagamento {self.mes_referencia.strftime('%Y-%m')}>"

# Tabela: banco_horas
class BancoHoras(db.Model):
    __tablename__ = 'banco_horas'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    horas_extras_trabalhadas = db.Column(db.Numeric(5, 2), default=0.00)
    horas_extras_folgadas = db.Column(db.Numeric(5, 2), default=0.00)

    def __repr__(self):
        return f"<Banco Horas {self.id} - {self.cpf_funcionario}>"

# Tabela: horas_extras
class HorasExtras(db.Model):
    __tablename__ = 'horas_extras'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    data = db.Column(db.Date, nullable=False)
    horas_trabalhadas = db.Column(db.Numeric(5, 2), nullable=False)
    situacao = db.Column(db.String(50), nullable=False)

    def __repr__(self):
        return f"<Horas Extras {self.id} - {self.cpf_funcionario}>"

# Tabela: registro_ponto
class RegistroPonto(db.Model):
    __tablename__ = 'registro_ponto'
    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'))
    pis = db.Column(db.String(14))
    id_face = db.Column(db.String(255))
    data_hora = db.Column(db.DateTime, nullable=False)
    tipo_lancamento = db.Column(db.String(50), nullable=False)
    observacao = db.Column(db.Text)

    def __repr__(self):
        return f"<Registro Ponto {self.id} - {self.data_hora}>"

# Tabela: usuarios
class Usuario(db.Model):
    __tablename__ = 'usuarios'
    codigo = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(255), nullable=False)
    senha_hash = db.Column(db.String(255), nullable=False)
    tipo_usuario = db.Column(db.String(50), nullable=False)

    def set_password(self, password):
        self.senha_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.senha_hash, password)

    def __repr__(self):
        return f"<Usuario {self.nome} ({self.tipo_usuario})>"\


# Tabela: cidades
class Cidade(db.Model):
    __tablename__ = 'cidades'
    cidibge = db.Column(db.String(7), primary_key=True)
    nome_cidade = db.Column(db.String(255), nullable=False)
    estado_uf = db.Column(db.String(2), nullable=False)
    longitude = db.Column(db.Numeric(16, 13), nullable=True)
    latitude = db.Column(db.Numeric(16, 13), nullable=True)
    regiao = db.Column(db.String(100), nullable=True)
    mesorregiao = db.Column(db.String(255), nullable=True)
    microrregiao = db.Column(db.String(255), nullable=True)

    def __repr__(self):
        return f"<Cidade {self.nome_cidade} ({self.estado_uf})>"

# Tabela: setores
class Setor(db.Model):
    __tablename__ = 'setores'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)

    def __repr__(self):
        return f"<Setor {self.nome}>"

# Tabela: funcoes
class Funcao(db.Model):
    __tablename__ = 'funcoes'
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), unique=True, nullable=False)

    def __repr__(self):
        return f"<Funcao {self.nome}>"

# Tabela: logs_auditoria
class LogAuditoria(db.Model):
    __tablename__ = 'logs_auditoria'
    id = db.Column(db.Integer, primary_key=True)
    usuario_id = db.Column(db.Integer, db.ForeignKey('usuarios.codigo'))
    data_hora = db.Column(db.DateTime, default=datetime.datetime.now)
    acao = db.Column(db.Text, nullable=False)
    tabela_afetada = db.Column(db.String(100), nullable=True)
    registro_id = db.Column(db.String(255), nullable=True)
    dados_antigos = db.Column(JSON, nullable=True)
    dados_novos = db.Column(JSON, nullable=True)

    usuario = db.relationship('Usuario', backref='logs_auditoria', lazy=True)

    def __repr__(self):
        return f"<Log {self.id} - {self.acao} por Usuário {self.usuario_id}>"
