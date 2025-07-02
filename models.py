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
    jornada_id = db.Column(db.Integer, db.ForeignKey('jornadas.id'))
    salario_inicial = db.Column(db.Numeric(10, 2), nullable=False)
    bonus = db.Column(db.Numeric(10, 2), default=0.00)
    regime_contratacao = db.Column(db.String(50), nullable=False)
    data_admissao = db.Column(db.Date, nullable=False)
    data_demissao = db.Column(db.Date)
    status = db.Column(db.Boolean, nullable=False, default=True)

    # Relacionamento; backref cria funcionario.contratos
    funcionario = db.relationship('Funcionario', backref='contratos', lazy=True)
    jornada = db.relationship('Jornada', backref='contratos', lazy=True)

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
    classificacao = db.Column(db.String(20), nullable=False, default='Neutro')
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
    nome = db.Column(db.String(255), nullable=False)  # nome de usuário
    nome_completo = db.Column(db.String(255), nullable=False)
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

# Tabela: jornadas
class Jornada(db.Model):
    __tablename__ = 'jornadas'
    id = db.Column(db.Integer, primary_key=True)
    primeiro_turno_inicio = db.Column(db.Time, nullable=False)
    primeiro_turno_fim = db.Column(db.Time, nullable=False)
    segundo_turno_inicio = db.Column(db.Time, nullable=False)
    segundo_turno_fim = db.Column(db.Time, nullable=False)
    turno_extra_inicio = db.Column(db.Time)
    turno_extra_fim = db.Column(db.Time)

    def __repr__(self):
        return f"<Jornada {self.id}>"

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

# ------------------------ Módulo de Exames Ocupacionais -----------------------

class EntidadeSaudeOcupacional(db.Model):
    """Médicos ou empresas responsáveis por exames."""
    __tablename__ = 'entidades_saude_ocupacional'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(255), nullable=False)
    crm_cnpj = db.Column(db.String(30))
    telefone = db.Column(db.String(20))
    email = db.Column(db.String(255), unique=True)

    exames = db.relationship('ExameFuncionario', backref='entidade', lazy=True)

    def __repr__(self):
        return f"<EntidadeSaudeOcupacional {self.nome}>"


class TipoExame(db.Model):
    """Tipos de exames e suas periodicidades."""
    __tablename__ = 'tipos_exames'

    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(255), nullable=False, unique=True)
    periodicidade_dias = db.Column(db.Integer, nullable=False)
    observacoes = db.Column(db.Text)

    funcoes = db.relationship('ExameFuncao', backref='tipo_exame', lazy=True)
    exames = db.relationship('ExameFuncionario', backref='tipo_exame', lazy=True)

    def __repr__(self):
        return f"<TipoExame {self.nome}>"


class ExameFuncao(db.Model):
    """Associação entre funções e tipos de exames obrigatórios."""
    __tablename__ = 'exames_funcoes'

    id = db.Column(db.Integer, primary_key=True)
    funcao_id = db.Column(db.Integer, db.ForeignKey('funcoes.id'), nullable=False)
    tipo_exame_id = db.Column(db.Integer, db.ForeignKey('tipos_exames.id'), nullable=False)

    def __repr__(self):
        return f"<ExameFuncao funcao={self.funcao_id} exame={self.tipo_exame_id}>"


class ExameFuncionario(db.Model):
    """Registro de exames realizados pelos colaboradores."""
    __tablename__ = 'exames_funcionarios'

    id = db.Column(db.Integer, primary_key=True)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    tipo_exame_id = db.Column(db.Integer, db.ForeignKey('tipos_exames.id'), nullable=False)
    data_realizacao = db.Column(db.Date, nullable=False)
    data_vencimento = db.Column(db.Date, nullable=False)
    entidade_id = db.Column(db.Integer, db.ForeignKey('entidades_saude_ocupacional.id'))
    observacoes = db.Column(db.Text)

    funcionario = db.relationship('Funcionario', backref='exames', lazy=True)

    def __repr__(self):
        return f"<ExameFuncionario {self.cpf_funcionario} {self.tipo_exame_id}>"
# ------------------------ Modulo de Fardas e EPIs ---------------------------
class ItemEPI(db.Model):
    """Catalogo de fardas e equipamentos de protecao individual."""
    __tablename__ = 'itens_epi'

    id = db.Column(db.Integer, primary_key=True)
    tipo_item = db.Column(db.String(10), nullable=False)
    descricao = db.Column(db.String(255), nullable=False)
    codigo = db.Column(db.String(50), unique=True, nullable=False)
    funcoes_permitidas = db.Column(db.Text)
    periodicidade_dias = db.Column(db.Integer)
    fornecedor = db.Column(db.String(255))
    observacoes = db.Column(db.Text)

    def __repr__(self):
        return f"<ItemEPI {self.codigo} ({self.tipo_item})>"

class DistribuicaoItem(db.Model):
    """Registro de distribuicao de fardas e EPIs."""
    __tablename__ = 'distribuicoes_itens'

    id = db.Column(db.Integer, primary_key=True)
    item_id = db.Column(db.Integer, db.ForeignKey('itens_epi.id'), nullable=False)
    cpf_funcionario = db.Column(db.String(14), db.ForeignKey('funcionarios.cpf'), nullable=False)
    quantidade = db.Column(db.Integer, nullable=False, default=1)
    certificado_aprovacao = db.Column(db.String(100))
    data_entrega = db.Column(db.DateTime, default=datetime.datetime.now)
    responsavel = db.Column(db.String(255))

    item = db.relationship('ItemEPI', backref='distribuicoes', lazy=True)
    funcionario = db.relationship('Funcionario', backref='itens_recebidos', lazy=True)

    @property
    def data_vencimento(self):
        if self.item and self.item.periodicidade_dias:
            return (self.data_entrega.date() + datetime.timedelta(days=self.item.periodicidade_dias))
        return None

    def __repr__(self):
        return f"<DistribuicaoItem {self.id} func={self.cpf_funcionario}>"

class DevolucaoItem(db.Model):
    """Registro de devolucao ou troca de itens."""
    __tablename__ = 'devolucoes_itens'

    id = db.Column(db.Integer, primary_key=True)
    distribuicao_id = db.Column(db.Integer, db.ForeignKey('distribuicoes_itens.id'), nullable=False)
    data_devolucao = db.Column(db.DateTime, default=datetime.datetime.now)
    motivo = db.Column(db.String(255))
    estado_item = db.Column(db.String(50))
    observacoes = db.Column(db.Text)

    distribuicao = db.relationship('DistribuicaoItem', backref=db.backref('devolucoes', lazy=True))

    def __repr__(self):
        return f"<DevolucaoItem {self.id} dist={self.distribuicao_id}>"