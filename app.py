from flask import Flask, render_template, session, redirect, url_for, request, flash, jsonify, Response
from sqlalchemy import or_, text, inspect
from sqlalchemy.exc import IntegrityError
from extensions import db
from config import Config
import os
from werkzeug.security import generate_password_hash, check_password_hash
import datetime

# Importações para geração de PDF
from io import BytesIO
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak, Table, TableStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.colors import HexColor, black, white, lightgrey  # Para usar as cores da paleta no PDF
import base64 # Importar base64 para decodificar a foto

GRAUS_INSTRUCAO = [
    "Sem instrução",
    "Analfabeto funcional",
    "Ensino Fundamental incompleto",
    "Ensino Fundamental completo",
    "Ensino Médio incompleto",
    "Ensino Médio completo",
    "Educação Profissional Técnica (Médio Técnico)",
    "Ensino Superior incompleto",
    "Ensino Superior completo",
    "Pós-graduação Lato Sensu",
    "Pós-graduação Stricto Sensu",
    "Pós-doutorado",
]

SEXOS = [
    "Masculino",
    "Feminino",
]

def create_app():
    """
    Cria e configura a instância do aplicativo Flask.
    Este é o padrão de fábrica de aplicativos.
    """
    app = Flask(__name__)
    app.config.from_object(Config)

    # Aumenta o limite de tamanho do corpo da requisição para 16 MB (pode ser ajustado)
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 Megabytes

    # Associa a instância do SQLAlchemy ao aplicativo Flask.
    # Isso deve acontecer DENTRO da função de criação do aplicativo.
    db.init_app(app)

    # Importa os modelos de dados APÓS 'db' ser inicializado e associado ao 'app'.
    # Isso é crucial para evitar dependências circulares e garantir que os modelos
    # tenham acesso à instância 'db' corretamente configurada.
    from models import (Funcionario, Dependente, ContratoTrabalho, ReajusteSalarial,
                       ControleFerias, FolhaPagamento, BancoHoras, HorasExtras,
                       RegistroPonto, LogAuditoria, Usuario, Cidade, Setor, Funcao,
                       Demissao, Jornada, EntidadeSaudeOcupacional, TipoExame,
                       ExameFuncao, ExameFuncionario, ItemEPI, DistribuicaoItem,
                       DevolucaoItem, Adiantamento, ParcelaAdiantamento)

    # Define o diretório de uploads (para planilhas e outros arquivos)
    UPLOAD_FOLDER = 'uploads'
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)
    app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

    # --- Funções Auxiliares de Validação ---
    def valida_cpf(cpf):
        """
        Valida o CPF de acordo com o algoritmo da Receita Federal.
        Aceita CPF com ou sem pontos e traço.
        """
        cpf = ''.join(filter(str.isdigit, cpf)) # Remove pontos e traço

        if len(cpf) != 11 or len(set(cpf)) == 1: # Verifica se tem 11 dígitos e não são todos iguais
            return False

        # Validação do primeiro dígito
        soma = 0
        for i in range(9):
            soma += int(cpf[i]) * (10 - i)
        digito1 = 11 - (soma % 11)
        if digito1 > 9:
            digito1 = 0
        if digito1 != int(cpf[9]):
            return False

        # Validação do segundo dígão

        soma = 0
        for i in range(10):
            soma += int(cpf[i]) * (11 - i)
        digito2 = 11 - (soma % 11)
        if digito2 > 9:
            digito2 = 0
        if digito2 != int(cpf[10]):
            return False

        return True

    # --- Rotas da Aplicação ---

    @app.route('/')
    def index():
        """
        Rota principal da aplicação.
        Se o usuário não estiver logado, redireciona para a página de login.
        Caso contrário, renderiza a página inicial.
        """
        if 'usuario_id' not in session:
            return redirect(url_for('login'))
        total_funcionarios_ativos = db.session.query(ContratoTrabalho).filter_by(status=True).count()
        hoje = datetime.date.today()
        limite = hoje + datetime.timedelta(days=90)
        exames_proximos = ExameFuncionario.query.filter(ExameFuncionario.data_vencimento <= limite).all()
        vencidos = [e for e in exames_proximos if e.data_vencimento < hoje]
        alerta_amarelo = [e for e in exames_proximos if hoje <= e.data_vencimento <= hoje + datetime.timedelta(days=7)]
        return render_template(
            'index.html',
            total_funcionarios_ativos=total_funcionarios_ativos,
            exames_vencidos=len(vencidos),
            exames_proximos=len(alerta_amarelo)
        )

    @app.route('/login', methods=['GET', 'POST'])
    def login():
        """
        Rota para o login de usuários.
        Autentica o usuário e gerencia a sessão.
        """
        if 'usuario_id' in session:
            return redirect(url_for('index'))

        if request.method == 'POST':
            username = request.form['username']
            password = request.form['password']
            
            # Busca o usuário no banco de dados
            user = Usuario.query.filter_by(nome=username).first()

            if user and user.check_password(password):
                session['usuario_id'] = user.codigo
                session['tipo_usuario'] = user.tipo_usuario
                # Registra o login no log de auditoria
                log_entry = LogAuditoria(
                    usuario_id=user.codigo,
                    acao=f"Login realizado por {user.nome} ({user.tipo_usuario})",
                    tabela_afetada="usuarios",
                    registro_id=user.codigo
                )
                db.session.add(log_entry)
                db.session.commit()
                flash('Login realizado com sucesso!', 'success')
                return redirect(url_for('index'))
            else:
                flash('Usuário ou senha inválidos.', 'danger')
                return render_template('login.html')
        
        return render_template('login.html')

    @app.route('/logout')
    def logout():
        """
        Rota para deslogar o usuário.
        Limpa a sessão e registra o logout no log de auditoria.
        """
        if 'usuario_id' not in session: # Se não estiver logado, apenas redireciona
            return redirect(url_for('login'))
            
        user_id = session['usuario_id']
        user = Usuario.query.get(user_id) 
        user_name = user.nome if user else "Desconhecido"
        log_entry = LogAuditoria(
            usuario_id=user_id,
            acao=f"Logout realizado por {user_name}",
            tabela_afetada="usuarios",
            registro_id=user_id
        )
        db.session.add(log_entry)
        db.session.commit()
        flash('Logout realizado com sucesso!', 'success')

        session.pop('usuario_id', None)
        session.pop('tipo_usuario', None)
        return redirect(url_for('login'))

    # --- ROTA DE DIAGNÓSTICO: Listar todas as rotas registradas ---
    @app.route('/debug_routes')
    def debug_routes():
        output = []
        for rule in app.url_map.iter_rules():
            methods = ','.join(rule.methods) if rule.methods else 'ANY'
            output.append(f"{rule.endpoint}: {rule.rule} ({methods})")
        return "<br>".join(sorted(output))
    # --- FIM DA ROTA DE DIAGNÓSTICO ---


    # --- Módulo: Cadastro de Funcionários ---
    @app.route('/funcionarios')
    def listar_funcionarios():
        """
        Exibe a lista de todos os funcionários cadastrados.
        """
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        
        funcionarios = Funcionario.query.all()
        return render_template('funcionarios.html', funcionarios=funcionarios)

    @app.route('/funcionarios/add', methods=['GET', 'POST'])
    def adicionar_funcionario():
        """
        Adiciona um novo funcionário.
        Valida o CPF e os demais campos.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar funcionários.', 'danger')
            return redirect(url_for('login'))

        # Buscar estados do banco de dados para a combobox
        estados_uf = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
        estados_uf = [uf[0] for uf in estados_uf] # Extrai o UF da tupla

        if request.method == 'POST':
            cpf = request.form['cpf'].replace('.', '').replace('-', '') # Remove pontos e traço
            nome = request.form['nome']
            data_nascimento = datetime.datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
            sexo = request.form.get('sexo')
            pis = request.form['pis']
            id_face = request.form['id_face']
            endereco = request.form['endereco']
            bairro = request.form.get('bairro')
            cidade = request.form['cidade'] # Valor da combobox
            estado = request.form['estado'] # Valor da combobox
            cep = request.form['cep']
            telefone = request.form['telefone']
            grau_instrucao = request.form.get('grau_instrucao')
            codigo_banco = request.form.get('codigo_banco') # Usar .get() para evitar KeyError
            nome_banco = request.form.get('nome_banco')
            codigo_agencia = request.form.get('codigo_agencia')
            numero_conta = request.form.get('numero_conta')
            variacao_conta = request.form.get('variacao_conta')
            chave_pix = request.form.get('chave_pix') # Usar .get() para evitar KeyError
            observacao = request.form.get('observacao')
            
            # Captura a string Base64 diretamente do formulário.
            # O tipo ImageBase64 no modelo Funcionario cuidará da decodificação para BYTEA.
            foto_base64_str = request.form.get('foto_base64') 

            # DEBUG: Imprimir o que está sendo recebido
            print(f"DEBUG (ADD): Foto Base64 STR recebida (length): {len(foto_base64_str) if foto_base64_str else 0}")
            print(f"DEBUG (ADD): Foto Base64 STR (primeiros 50 chars): {foto_base64_str[:50] if foto_base64_str else 'N/A'}")


            # Validação do CPF
            if not valida_cpf(cpf):
                flash('CPF inválido. Verifique o número digitado.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

            # Verifica se o CPF já existe
            if Funcionario.query.get(cpf):
                flash('CPF já cadastrado.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

            # Validação condicional para campos bancários/PIX no backend
            if not (chave_pix or (codigo_banco and nome_banco and codigo_agencia and numero_conta)):
                flash('Por favor, preencha a Chave PIX OU todos os campos bancários.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

            novo_funcionario = Funcionario(
                cpf=cpf, nome=nome, data_nascimento=data_nascimento, sexo=sexo, pis=pis,
                id_face=id_face, endereco=endereco, bairro=bairro, cidade=cidade, estado=estado, # NOVO CAMPO: BAIRRO
                cep=cep, telefone=telefone, grau_instrucao=grau_instrucao,
                codigo_banco=codigo_banco,
                nome_banco=nome_banco, codigo_agencia=codigo_agencia,
                numero_conta=numero_conta, variacao_conta=variacao_conta,
                chave_pix=chave_pix, observacao=observacao,
                foto_base64=foto_base64_str, # Passa a string Base64, o CustomType cuidará da decodificação
                status=""
            )
            db.session.add(novo_funcionario)
            try:
                db.session.commit()
                print("DEBUG (ADD): Commit bem-sucedido.")
            except Exception as e:
                db.session.rollback()
                print(f"ERROR (ADD): Falha no commit: {e}")
                flash('Erro ao adicionar funcionário. Tente novamente.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Funcionário {nome} ({cpf}) adicionado.",
                tabela_afetada="funcionarios",
                registro_id=cpf # Usar o CPF como registro_id para funcionários
            )
            db.session.add(log_entry)
            db.session.commit()

            flash('Funcionário adicionado com sucesso!', 'success')
            return redirect(url_for('listar_funcionarios'))
        
        return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

    @app.route('/funcionarios/edit/<string:cpf>', methods=['GET', 'POST'])
    def editar_funcionario(cpf):
        """
        Edita um funcionário existente pelo CPF.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar funcionários.', 'danger')
            return redirect(url_for('login'))

        funcionario = Funcionario.query.get_or_404(cpf)

        estados_uf = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
        estados_uf = [uf[0] for uf in estados_uf] # Extrai o UF da tupla

        if request.method == 'POST':
            # Não permitir alteração do CPF para manter a chave primária
            nome = request.form['nome']
            data_nascimento = datetime.datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
            sexo = request.form.get('sexo')
            pis = request.form['pis']
            id_face = request.form['id_face']
            endereco = request.form['endereco']
            bairro = request.form.get('bairro') # NOVO CAMPO: BAIRRO
            cidade = request.form['cidade'] # Valor da combobox
            estado = request.form['estado'] # Valor da combobox
            cep = request.form['cep']
            telefone = request.form['telefone']
            grau_instrucao = request.form.get('grau_instrucao')
            codigo_banco = request.form.get('codigo_banco')
            nome_banco = request.form.get('nome_banco')
            codigo_agencia = request.form.get('codigo_agencia')
            numero_conta = request.form.get('numero_conta')
            variacao_conta = request.form.get('variacao_conta')
            chave_pix = request.form.get('chave_pix')
            observacao = request.form.get('observacao')
            
            # Captura a string Base64 diretamente do formulário.
            # O tipo ImageBase64 no modelo Funcionario cuidará da decodificação para BYTEA.
            foto_base64_str = request.form.get('foto_base64') 
            print(f"DEBUG (EDIT): Foto Base64 STR recebida (length): {len(foto_base64_str) if foto_base64_str else 0}")
            print(f"DEBUG (EDIT): Foto Base64 STR (primeiros 50 chars): {foto_base64_str[:50] if foto_base64_str else 'N/A'}")


            # Validação condicional para campos bancários/PIX no backend
            if not (chave_pix or (codigo_banco and nome_banco and codigo_agencia and numero_conta)):
                flash('Por favor, preencha a Chave PIX OU todos os campos bancários.', 'danger')
                return render_template('funcionario_form.html', funcionario=funcionario, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

            # Guarda os dados antigos para o log de auditoria
            dados_antigos = {
                'nome': funcionario.nome, 'data_nascimento': str(funcionario.data_nascimento),
                'sexo': funcionario.sexo,
                'pis': funcionario.pis, 'id_face': funcionario.id_face,
                'endereco': funcionario.endereco, 'bairro': funcionario.bairro, # NOVO CAMPO: BAIRRO
                'cidade': funcionario.cidade, 'estado': funcionario.estado,
                'cep': funcionario.cep, 'telefone': funcionario.telefone,
                'grau_instrucao': funcionario.grau_instrucao,
                'codigo_banco': funcionario.codigo_banco, 'nome_banco': funcionario.nome_banco,
                'codigo_agencia': funcionario.codigo_agencia, 'numero_conta': funcionario.numero_conta,
                'variacao_conta': funcionario.variacao_conta, 'chave_pix': funcionario.chave_pix,
                'observacao': funcionario.observacao,
                'foto_base64': funcionario.foto_base64 # O CustomType já retornou a string Base64 para o objeto
            }

            funcionario.nome = nome
            funcionario.data_nascimento = data_nascimento
            funcionario.pis = pis
            funcionario.sexo = sexo
            funcionario.id_face = id_face
            funcionario.endereco = endereco
            funcionario.bairro = bairro # NOVO CAMPO: BAIRRO
            funcionario.cidade = cidade
            funcionario.estado = estado
            funcionario.cep = cep
            funcionario.telefone = telefone
            funcionario.grau_instrucao = grau_instrucao
            funcionario.codigo_banco = codigo_banco
            funcionario.nome_banco = nome_banco
            funcionario.codigo_agencia = codigo_agencia
            funcionario.numero_conta = numero_conta
            funcionario.variacao_conta = variacao_conta
            funcionario.chave_pix = chave_pix
            funcionario.observacao = observacao
            funcionario.foto_base64 = foto_base64_str # Passa a string Base64, o CustomType cuidará da decodificação

            try:
                db.session.commit()
                print("DEBUG (EDIT): Commit bem-sucedido.")
            except Exception as e:
                db.session.rollback()
                print(f"ERROR (EDIT): Falha no commit: {e}")
                flash('Erro ao atualizar funcionário. Tente novamente.', 'danger')
                return render_template('funcionario_form.html', funcionario=funcionario, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

            dados_novos = {
                'nome': funcionario.nome, 'data_nascimento': str(funcionario.data_nascimento),
                'sexo': funcionario.sexo,
                'pis': funcionario.pis, 'id_face': funcionario.id_face,
                'endereco': funcionario.endereco, 'bairro': funcionario.bairro, # NOVO CAMPO: BAIRRO
                'cidade': funcionario.cidade, 'estado': funcionario.estado,
                'cep': funcionario.cep, 'telefone': funcionario.telefone,
                'grau_instrucao': funcionario.grau_instrucao,
                'codigo_banco': funcionario.codigo_banco, 'nome_banco': funcionario.nome_banco,
                'codigo_agencia': funcionario.codigo_agencia, 'numero_conta': funcionario.numero_conta,
                'variacao_conta': funcionario.variacao_conta, 'chave_pix': funcionario.chave_pix,
                'observacao': funcionario.observacao,
                'foto_base64': funcionario.foto_base64 # O CustomType já retornou a string Base64 para o objeto
            }

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Funcionário {funcionario.nome} ({cpf}) editado.",
                tabela_afetada="funcionarios",
                registro_id=cpf,
                dados_antigos=dados_antigos,
                dados_novos=dados_novos
            )
            db.session.add(log_entry)
            db.session.commit()

            flash('Funcionário atualizado com sucesso!', 'success')
            return redirect(url_for('listar_funcionarios'))

        return render_template('funcionario_form.html', funcionario=funcionario, estados_uf=estados_uf, graus_instrucao=GRAUS_INSTRUCAO, sexos=SEXOS)

    @app.route('/funcionarios/view/<string:cpf>')
    def ver_funcionario(cpf):
        """Exibe os detalhes de um funcionário sem permitir edição."""
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        funcionario = Funcionario.query.get_or_404(cpf)
        return render_template('funcionario_detalhes.html', funcionario=funcionario)

    @app.route('/funcionarios/delete/<string:cpf>', methods=['POST'])
    def deletar_funcionario(cpf):
        """
        Deleta um funcionário existente pelo CPF.
        Primeiro, verifica se há dependentes ou outros registros relacionados.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar funcionários.', 'danger')
            return redirect(url_for('login'))
        
        funcionario = Funcionario.query.get_or_404(cpf)

        if Dependente.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui dependentes cadastrados. Por favor, remova os dependentes primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        if ContratoTrabalho.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui contratos de trabalho. Por favor, remova os contratos primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        if ReajusteSalarial.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui reajustes salariais. Por favor, remova-os primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        if ControleFerias.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui registros de férias. Por favor, remova-os primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        if BancoHoras.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui registros de banco de horas. Por favor, remova-os primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        if HorasExtras.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui registros de horas extras. Por favor, remova-os primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        if RegistroPonto.query.filter_by(cpf_funcionario=cpf).first():
            flash(f'Não foi possível deletar o funcionário {funcionario.nome}. Ele possui registros de ponto. Por favor, remova-os primeiro.', 'danger')
            return redirect(url_for('listar_funcionarios'))
        
        dados_antigos = {
            'cpf': funcionario.cpf, 'nome': funcionario.nome,
            'data_nascimento': str(funcionario.data_nascimento), 'sexo': funcionario.sexo, 'pis': funcionario.pis,
            'id_face': funcionario.id_face, 'endereco': funcionario.endereco, 'bairro': funcionario.bairro, # NOVO CAMPO: BAIRRO
            'cidade': funcionario.cidade, 'estado': funcionario.estado,
            'cep': funcionario.cep, 'telefone': funcionario.telefone,
            'grau_instrucao': funcionario.grau_instrucao,
            'codigo_banco': funcionario.codigo_banco, 'nome_banco': funcionario.nome_banco,
            'codigo_agencia': funcionario.codigo_agencia, 'numero_conta': funcionario.numero_conta,
            'variacao_conta': funcionario.variacao_conta, 'chave_pix': funcionario.chave_pix,
            'observacao': funcionario.observacao,
            'foto_base64': funcionario.foto_base64
        }

        db.session.delete(funcionario)
        db.session.commit()
        log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Funcionário {funcionario.nome} ({cpf}) deletado.", tabela_afetada="funcionarios", registro_id=cpf, dados_antigos=dados_antigos, dados_novos=None)
        db.session.add(log_entry)
        db.session.commit()
        flash('Funcionário deletado com sucesso!', 'success')
        return redirect(url_for('listar_funcionarios'))

    # --- Módulo: Cadastro de Dependentes ---
    @app.route('/dependentes')
    def listar_dependentes():
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        dependentes = db.session.query(Dependente).join(Funcionario).all()
        return render_template('dependentes.html', dependentes=dependentes)

    @app.route('/dependentes/add', methods=['GET', 'POST'])
    def adicionar_dependente():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar dependentes.', 'danger')
            return redirect(url_for('login'))
        if request.method == 'POST':
            cpf_dependente = request.form['cpf_dependente'].replace('.', '').replace('-', '')
            nome_dependente = request.form['nome_dependente']
            data_nascimento = datetime.datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
            status = request.form['status'] == 'True'
            salario_familia = float(request.form['salario_familia']) if request.form['salario_familia'] else 0.00
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')

            if not valida_cpf(cpf_dependente):
                flash('CPF do dependente inválido. Verifique o número digitado.', 'danger')
                return render_template('dependente_form.html', dependente=None)
            if Dependente.query.get(cpf_dependente):
                flash('CPF do dependente já cadastrado.', 'danger')
                return render_template('dependente_form.html', dependente=None)
            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('dependentes_form.html', dependente=None)

            novo_dependente = Dependente(cpf_dependente=cpf_dependente, nome_dependente=nome_dependente, data_nascimento=data_nascimento, status=status, salario_familia=salario_familia, cpf_funcionario=cpf_funcionario)
            db.session.add(novo_dependente)
            db.session.commit()
            log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Dependente {nome_dependente} ({cpf_dependente}) adicionado para o funcionário {funcionario.nome} ({cpf_funcionario}).", tabela_afetada="dependentes", registro_id=cpf_dependente)
            db.session.add(log_entry)
            db.session.commit()
            flash('Dependente adicionado com sucesso!', 'success')
            return redirect(url_for('listar_dependentes'))
        return render_template('dependentes_form.html', dependente=None)

    @app.route('/dependentes/edit/<string:cpf_dependente>', methods=['GET', 'POST'])
    def editar_dependente(cpf_dependente):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar dependentes.', 'danger')
            return redirect(url_for('login'))
        dependente = Dependente.query.get_or_404(cpf_dependente)

        if request.method == 'POST':
            nome_dependente = request.form['nome_dependente']
            data_nascimento = datetime.datetime.strptime(request.form['data_nascimento'], '%Y-%m-%d').date()
            status = request.form['status'] == 'True'
            salario_familia = float(request.form['salario_familia']) if request.form['salario_familia'] else 0.00
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('dependentes_form.html', dependente=dependente)

            dados_antigos = {
                'nome_dependente': dependente.nome_dependente, 'data_nascimento': str(dependente.data_nascimento),
                'status': dependente.status, 'cpf_funcionario': dependente.cpf_funcionario,
                'salario_familia': float(dependente.salario_familia)
            }

            dependente.nome_dependente = nome_dependente
            dependente.data_nascimento = data_nascimento
            dependente.status = status
            dependente.salario_familia = salario_familia
            dependente.cpf_funcionario = cpf_funcionario

            db.session.commit()

            dados_novos = {
                'nome_dependente': dependente.nome_dependente, 'data_nascimento': str(dependente.data_nascimento),
                'status': dependente.status, 'cpf_funcionario': dependente.cpf_funcionario,
                'salario_familia': float(dependente.salario_familia)
            }

            log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Dependente {nome_dependente} ({cpf_dependente}) editado. Relacionado ao funcionário {funcionario.nome} ({cpf_funcionario}).", tabela_afetada="dependentes", registro_id=cpf_dependente, dados_antigos=dados_antigos, dados_novos=dados_novos)
            db.session.add(log_entry)
            db.session.commit()
            flash('Dependente atualizado com sucesso!', 'success')
            return redirect(url_for('listar_dependentes'))
        return render_template('dependentes_form.html', dependente=dependente)

    @app.route('/dependentes/delete/<string:cpf_dependente>', methods=['POST'])
    def deletar_dependente(cpf_dependente):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar dependentes.', 'danger')
            return redirect(url_for('login'))
        dependente = Dependente.query.get_or_404(cpf_dependente)

        dados_antigos = {
            'cpf_dependente': dependente.cpf_dependente, 'nome_dependente': dependente.nome_dependente,
            'data_nascimento': str(dependente.data_nascimento), 'status': dependente.status,
            'cpf_funcionario': dependente.cpf_funcionario,
            'salario_familia': float(dependente.salario_familia)
        }
        
        db.session.delete(dependente)
        db.session.commit()

        log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Dependente {dependente.nome_dependente} ({cpf_dependente}) deletado.", tabela_afetada="dependentes", registro_id=cpf_dependente, dados_antigos=dados_antigos, dados_novos=None)
        db.session.add(log_entry)
        db.session.commit()
        flash('Dependente deletado com sucesso!', 'success')
        return redirect(url_for('listar_dependentes'))

    # --- API para buscar nome do funcionário ---
    @app.route('/api/funcionarios/<string:cpf>', methods=['GET'])
    def api_buscar_funcionario(cpf):
        funcionario = Funcionario.query.get(cpf)
        if funcionario:
            return jsonify({'nome': funcionario.nome})
        else:
            return jsonify({'nome': None}), 404

# --- API para buscar funcionário por CPF, PIS ou IDFace ---
    @app.route('/api/buscar_funcionario_identificador/<string:identifier>', methods=['GET'])
    def api_buscar_funcionario_identificador(identifier):
        ident_clean = identifier.replace('.', '').replace('-', '').strip()
        funcionario = Funcionario.query.filter(
            or_(
                Funcionario.cpf == ident_clean,
                Funcionario.cpf == identifier,
                Funcionario.pis == ident_clean,
                Funcionario.pis == identifier,
                Funcionario.id_face == ident_clean,
                Funcionario.id_face == identifier,
            )
        ).first()
        if funcionario:
            return jsonify({
                'nome': funcionario.nome,
                'cpf': funcionario.cpf,
                'pis': funcionario.pis,
                'id_face': funcionario.id_face,
            })
        else:
            return jsonify({'nome': None}), 404

    # --- API para buscar contrato ativo do funcionário ---
    @app.route('/api/contratos_ativos/<string:cpf>', methods=['GET'])
    def api_buscar_contrato_ativo(cpf):
        # Primeiro, verifica se o funcionário existe
        funcionario = Funcionario.query.get(cpf)
        if not funcionario:
            return jsonify({
                'nome': None,
                'cargo': None,
                'setor': None,
                'data_admissao': None,
                'message': 'Funcionário não encontrado.'
            }), 404

        # Busca o contrato mais recente (pela data de admissão) e que esteja ativo
        contrato_ativo = ContratoTrabalho.query.filter_by(
            cpf_funcionario=cpf,
            status=True
        ).order_by(ContratoTrabalho.data_admissao.desc()).first()

        if contrato_ativo:
            return jsonify({
                'nome': funcionario.nome,
                'cargo': contrato_ativo.funcao,
                'setor': contrato_ativo.setor,
                'data_admissao': contrato_ativo.data_admissao.strftime('%Y-%m-%d')
            })
        else:
            return jsonify({
                'nome': funcionario.nome,
                'cargo': 'N/A',
                'setor': 'N/A',
                'data_admissao': 'N/A',
                'message': 'Nenhum contrato ativo encontrado para este funcionário.'
            }), 200  # Retorna 200 porque o funcionário foi encontrado, apenas sem contrato ativo

    # --- API para buscar salário base do contrato ativo ---
    @app.route('/api/salario_base/<string:cpf>', methods=['GET'])
    def api_salario_base(cpf):
        contrato = (
            ContratoTrabalho.query
            .filter_by(cpf_funcionario=cpf, status=True)
            .order_by(ContratoTrabalho.data_admissao.desc())
            .first()
        )
        if contrato:
            return jsonify({'salario_base': float(contrato.salario_inicial)})
        return jsonify({'salario_base': None}), 404

    # --- API para buscar cidades por estado ---
    @app.route('/api/cities_by_state/<string:state_uf>', methods=['GET'])
    def api_cities_by_state(state_uf):
        # Busca cidades no banco de dados para o estado selecionado
        cities = [c.nome_cidade for c in db.session.query(Cidade).filter_by(estado_uf=state_uf.upper()).order_by(Cidade.nome_cidade).all()]
        return jsonify(cities)


    # --- Módulo: Cadastro de Cidades ---
    @app.route('/cidades')
    def listar_cidades():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar cidades.', 'danger')
            return redirect(url_for('login'))
        
        cidades = []
        try:
            cidades = Cidade.query.order_by(Cidade.nome_cidade).all()
        except Exception as e:
            # Captura o erro específico para depuração
            print(f"ERROR: Falha ao carregar cidades (listar_cidades): {e}")
            flash(f"Erro ao carregar lista de cidades: {e}", 'danger')
            db.session.rollback() # Garante que a sessão esteja limpa
            
        return render_template('cidades.html', cidades=cidades)

    @app.route('/cidades/add', methods=['GET', 'POST'])
    def adicionar_cidade():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar cidades.', 'danger')
            return redirect(url_for('login'))
        
        if request.method == 'POST':
            cidibge = request.form['cidibge']
            nome_cidade = request.form['nome_cidade']
            estado_uf = request.form['estado_uf'].upper()
            
            longitude_str = request.form.get('longitude')
            latitude_str = request.form.get('latitude')

            # Tenta converter para float, substituindo ',' por '.'
            try:
                longitude = float(longitude_str.replace(',', '.')) if longitude_str else None
                latitude = float(latitude_str.replace(',', '.')) if latitude_str else None
            except ValueError:
                flash('Longitude ou Latitude inválida. Use um formato numérico válido (ex: -10.123456).', 'danger')
                estados_uf_para_form = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
                estados_uf_para_form = [uf[0] for uf in estados_uf_para_form] if estados_uf_para_form else []
                return render_template('cidade_form.html', cidade=None, estados_uf=estados_uf_para_form)


            regiao = request.form.get('regiao')
            mesorregiao = request.form.get('mesorregiao')
            microrregiao = request.form.get('microrregiao')

            if Cidade.query.get(cidibge):
                flash('CID IBGE já cadastrado.', 'danger')
                estados_uf_para_form = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
                estados_uf_para_form = [uf[0] for uf in estados_uf_para_form] if estados_uf_para_form else []
                return render_template('cidade_form.html', cidade=None, estados_uf=estados_uf_para_form)

            nova_cidade = Cidade(
                cidibge=cidibge, nome_cidade=nome_cidade, estado_uf=estado_uf,
                longitude=longitude, # Agora já é um float com ponto
                latitude=latitude,   # Agora já é um float com ponto
                regiao=regiao, mesorregiao=mesorregiao, microrregiao=microrregiao
            )
            db.session.add(nova_cidade)
            try:
                db.session.commit()
                flash('Cidade adicionada com sucesso!', 'success')
                return redirect(url_for('listar_cidades'))
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao adicionar cidade: {e}', 'danger')
                estados_uf_para_form = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
                estados_uf_para_form = [uf[0] for uf in estados_uf_para_form] if estados_uf_para_form else []
                return render_template('cidade_form.html', cidade=None, estados_uf=estados_uf_para_form)
        
        estados_uf = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
        estados_uf = [uf[0] for uf in estados_uf] if estados_uf else []

        return render_template('cidade_form.html', cidade=None, estados_uf=estados_uf)

    @app.route('/cidades/edit/<string:cidibge>', methods=['GET', 'POST'])
    def editar_cidade(cidibge):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar cidades.', 'danger')
            return redirect(url_for('login'))
        
        cidade = Cidade.query.get_or_404(cidibge)
        estados_uf = sorted(list(db.session.query(Cidade.estado_uf).distinct().all()))
        estados_uf = [uf[0] for uf in estados_uf] if estados_uf else []

        if request.method == 'POST':
            cidade.nome_cidade = request.form['nome_cidade']
            cidade.estado_uf = request.form['estado_uf'].upper()
            
            longitude_str = request.form.get('longitude')
            latitude_str = request.form.get('latitude')

            # Tenta converter para float, substituindo ',' por '.'
            try:
                cidade.longitude = float(longitude_str.replace(',', '.')) if longitude_str else None
                latitude = float(latitude_str.replace(',', '.')) if latitude_str else None
            except ValueError:
                flash('Longitude ou Latitude inválida. Use um formato numérico válido (ex: -10.123456).', 'danger')
                return render_template('cidade_form.html', cidade=cidade, estados_uf=estados_uf)


            cidade.regiao = request.form.get('regiao')
            cidade.mesorregiao = request.form.get('mesorregiao')
            cidade.microrregiao = request.form.get('microrregiao')

            try:
                db.session.commit()
                flash('Cidade atualizada com sucesso!', 'success')
                return redirect(url_for('listar_cidades'))
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao atualizar cidade: {e}', 'danger')
                return render_template('cidade_form.html', cidade=cidade, estados_uf=estados_uf)
        
        return render_template('cidade_form.html', cidade=cidade, estados_uf=estados_uf)

    @app.route('/cidades/delete/<string:cidibge>', methods=['POST'])
    def deletar_cidade(cidibge):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar cidades.', 'danger')
            return redirect(url_for('login'))
        
        cidade = Cidade.query.get_or_404(cidibge)

        if Funcionario.query.filter_by(cidade=cidade.nome_cidade, estado=cidade.estado_uf).first():
             flash(f'Não foi possível deletar a cidade {cidade.nome_cidade} ({cidade.estado_uf}). Existem funcionários cadastrados nela. Remova os funcionários primeiro.', 'danger')
             return redirect(url_for('listar_cidades'))

        try:
            db.session.delete(cidade)
            db.session.commit()
            flash('Cidade deletada com sucesso!', 'success')
            return redirect(url_for('listar_cidades'))
        except Exception as e:
            db.session.rollback()
            flash(f'Erro ao deletar cidade: {e}', 'danger')
            return redirect(url_for('listar_cidades'))

    # --- Módulo: Contrato de Trabalho ---
    @app.route('/contratos')
    def listar_contratos():
        """
        Exibe a lista de todos os contratos de trabalho.
        """
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))
        
        # Para trazer o nome do funcionário junto, fazemos um join
        contratos = db.session.query(ContratoTrabalho).join(Funcionario).order_by(ContratoTrabalho.data_admissao.desc()).all()
        return render_template('contratos.html', contratos=contratos)

    @app.route('/contratos/add', methods=['GET', 'POST'])
    def adicionar_contrato():
        """
        Adiciona um novo contrato de trabalho.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar contratos.', 'danger')
            return redirect(url_for('login'))

        # Obter listas de setores e funções para o dropdown
        setores = Setor.query.order_by(Setor.nome).all()
        funcoes = Funcao.query.order_by(Funcao.nome).all()
        jornadas = Jornada.query.order_by(Jornada.id).all()

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            setor_nome = request.form['setor'] # Pega o nome do setor do formulário
            funcao_nome = request.form['funcao'] # Pega o nome da função do formulário
            salario_inicial = float(request.form['salario_inicial'])
            bonus = float(request.form['bonus']) if request.form['bonus'] else 0.00
            regime_contratacao = request.form['regime_contratacao']
            jornada_id = int(request.form['jornada_id']) if request.form.get('jornada_id') else None
            data_admissao = datetime.datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            data_demissao_str = request.form.get('data_demissao')
            data_demissao = datetime.datetime.strptime(data_demissao_str, '%Y-%m-%d').date() if data_demissao_str else None
            status = request.form['status'] == 'True'

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes, jornadas=jornadas)
            
            # Validação: Um funcionário pode ter apenas um contrato ATIVO
            if ContratoTrabalho.query.filter_by(cpf_funcionario=cpf_funcionario, status=True).first():
                flash('Este funcionário já possui um contrato ATIVO. Por favor, inative o contrato anterior antes de criar um novo.', 'danger')
                return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes, jornadas=jornadas)


            novo_contrato = ContratoTrabalho(
                cpf_funcionario=cpf_funcionario, setor=setor_nome, funcao=funcao_nome, # Usando o nome direto
                jornada_id=jornada_id,
                salario_inicial=salario_inicial, bonus=bonus,
                regime_contratacao=regime_contratacao, data_admissao=data_admissao,
                data_demissao=data_demissao, status=status
            )
            db.session.add(novo_contrato)
            try:
                db.session.commit()
                if status:
                    funcionario.status = "Ativo"
                    db.session.add(funcionario)
                    db.session.commit()
                flash('Contrato adicionado com sucesso!', 'success')
                
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Contrato de Trabalho adicionado para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="contratos_trabalho",
                    registro_id=novo_contrato.id # ID do contrato
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_contratos'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao adicionar contrato: {e}") # Print para depuração
                flash(f'Erro ao adicionar contrato: {e}', 'danger')
                return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes, jornadas=jornadas)
        
        return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes, jornadas=jornadas)

    @app.route('/contratos/edit/<int:id>', methods=['GET', 'POST'])
    def editar_contrato(id):
        """
        Edita um contrato de trabalho existente.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar contratos.', 'danger')
            return redirect(url_for('login'))
        
        contrato = ContratoTrabalho.query.get_or_404(id)

        # Obter listas de setores e funções para o dropdown
        setores = Setor.query.order_by(Setor.nome).all()
        funcoes = Funcao.query.order_by(Funcao.nome).all()
        jornadas = Jornada.query.order_by(Jornada.id).all()

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            setor_nome = request.form['setor'] # Pega o nome do setor do formulário
            funcao_nome = request.form['funcao'] # Pega o nome da função do formulário
            salario_inicial = float(request.form['salario_inicial'])
            bonus = float(request.form['bonus']) if request.form['bonus'] else 0.00
            regime_contratacao = request.form['regime_contratacao']
            jornada_id = int(request.form['jornada_id']) if request.form.get('jornada_id') else None
            data_admissao = datetime.datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            data_demissao_str = request.form.get('data_demissao')
            data_demissao = datetime.datetime.strptime(data_demissao_str, '%Y-%m-%d').date() if data_demissao_str else None
            status = request.form['status'] == 'True'

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes, jornadas=jornadas)
            
            # Validação: Se está alterando o status para ATIVO, verifica se já existe outro contrato ATIVO
            if status and ContratoTrabalho.query.filter(ContratoTrabalho.cpf_funcionario == cpf_funcionario, ContratoTrabalho.status == True, ContratoTrabalho.id != contrato.id).first():
                flash('Este funcionário já possui outro contrato ATIVO. Por favor, inative o contrato anterior ou selecione "Inativo" para este contrato.', 'danger')
                return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes, jornadas=jornadas)


            # Guarda dados antigos para log
            dados_antigos = {
                'cpf_funcionario': contrato.cpf_funcionario, 'setor': contrato.setor,
                'funcao': contrato.funcao, 'salario_inicial': float(contrato.salario_inicial),
                'bonus': float(contrato.bonus), 'regime_contratacao': contrato.regime_contratacao,
                'data_admissao': str(contrato.data_admissao), 'data_demissao': str(contrato.data_demissao) if contrato.data_demissao else None,
                'status': contrato.status,
                'jornada_id': contrato.jornada_id
            }

            contrato.cpf_funcionario = cpf_funcionario
            contrato.setor = setor_nome # Usando o nome
            contrato.funcao = funcao_nome # Usando o nome
            contrato.salario_inicial = salario_inicial
            contrato.bonus = bonus
            contrato.regime_contratacao = regime_contratacao
            contrato.jornada_id = jornada_id
            contrato.data_admissao = data_admissao
            contrato.data_demissao = data_demissao
            contrato.status = status

            try:
                db.session.commit()
                flash('Contrato atualizado com sucesso!', 'success')

                # Guarda dados novos para log
                dados_novos = {
                    'cpf_funcionario': contrato.cpf_funcionario, 'setor': contrato.setor,
                    'funcao': contrato.funcao, 'salario_inicial': float(contrato.salario_inicial),
                    'bonus': float(contrato.bonus), 'regime_contratacao': contrato.regime_contratacao,
                    'data_admissao': str(contrato.data_admissao), 'data_demissao': str(contrato.data_demissao) if contrato.data_demissao else None,
                    'status': contrato.status,
                    'jornada_id': contrato.jornada_id
                }

                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Contrato de Trabalho ID {id} editado para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="contratos_trabalho",
                    registro_id=id,
                    dados_antigos=dados_antigos,
                    dados_novos=dados_novos
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_contratos'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao atualizar contrato: {e}") # Print para depuração
                flash(f'Erro ao atualizar contrato: {e}', 'danger')
                return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes, jornadas=jornadas)
        
        return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes, jornadas=jornadas)

    @app.route('/contratos/delete/<int:id>', methods=['POST'])
    def deletar_contrato(id):
        """
        Deleta um contrato de trabalho existente.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar contratos.', 'danger')
            return redirect(url_for('login'))
        
        contrato = ContratoTrabalho.query.get_or_404(id)

        # Guarda dados antigos para log
        dados_antigos = {
            'id': contrato.id, 'cpf_funcionario': contrato.cpf_funcionario,
            'setor': contrato.setor, 'funcao': contrato.funcao,
            'salario_inicial': float(contrato.salario_inicial), 'bonus': float(contrato.bonus),
            'regime_contratacao': contrato.regime_contratacao,
            'data_admissao': str(contrato.data_admissao), 'data_demissao': str(contrato.data_demissao) if contrato.data_demissao else None,
            'status': contrato.status,
            'jornada_id': contrato.jornada_id
        }
        
        db.session.delete(contrato)
        try:
            db.session.commit()
            flash('Contrato deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Contrato de Trabalho ID {id} deletado. Funcionário: {contrato.funcionario.nome if contrato.funcionario else contrato.cpf_funcionario}.",
                tabela_afetada="contratos_trabalho",
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()

            return redirect(url_for('listar_contratos'))
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar contrato: {e}") # Print para depuração
            flash(f'Erro ao deletar contrato: {e}', 'danger')
            return redirect(url_for('listar_contratos'))

    # --- Módulo: Cadastro de Setores ---
    @app.route('/setores')
    def listar_setores():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar setores.', 'danger')
            return redirect(url_for('login'))
        setores = Setor.query.order_by(Setor.nome).all()
        return render_template('setores.html', setores=setores)

    @app.route('/setores/add', methods=['GET', 'POST'])
    def adicionar_setor():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar setores.', 'danger')
            return redirect(url_for('login'))
        
        if request.method == 'POST':
            nome = request.form['nome'].strip()
            if not nome:
                flash('O nome do setor não pode ser vazio.', 'danger')
                return render_template('setor_form.html', setor=None)
            
            if Setor.query.filter_by(nome=nome).first():
                flash(f'O setor "{nome}" já existe.', 'danger')
                return render_template('setor_form.html', setor=None)
            
            novo_setor = Setor(nome=nome)
            db.session.add(novo_setor)
            try:
                db.session.commit()
                flash('Setor adicionado com sucesso!', 'success')
                log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Setor '{nome}' adicionado.", tabela_afetada="setores", registro_id=novo_setor.id)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('listar_setores'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao adicionar setor: {e}") # Print para depuração
                flash(f'Erro ao adicionar setor: {e}', 'danger')
                return render_template('setor_form.html', setor=None)
        
        return render_template('setor_form.html', setor=None)

    @app.route('/setores/edit/<int:id>', methods=['GET', 'POST'])
    def editar_setor(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar setores.', 'danger')
            return redirect(url_for('login'))
        
        setor = Setor.query.get_or_404(id)

        if request.method == 'POST':
            novo_nome = request.form['nome'].strip()
            if not novo_nome:
                flash('O nome do setor não pode ser vazio.', 'danger')
                return render_template('funcao_form.html', funcao=funcao)
            
            if Funcao.query.filter(Funcao.nome == novo_nome, Funcao.id != id).first():
                flash(f'A função "{novo_nome}" já existe.', 'danger')
                return render_template('funcao_form.html', funcao=funcao)
            
            dados_antigos = {'nome': setor.nome}
            setor.nome = novo_nome
            try:
                db.session.commit()
                flash('Setor atualizado com sucesso!', 'success')
                dados_novos = {'nome': setor.nome}
                log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Setor ID {id} editado para '{novo_nome}'.", tabela_afetada="setores", registro_id=id, dados_antigos=dados_antigos, dados_novos=dados_novos)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('listar_setores'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao atualizar setor: {e}") # Print para depuração
                flash(f'Erro ao atualizar setor: {e}', 'danger')
                return render_template('setor_form.html', setor=setor)
        
        return render_template('setor_form.html', setor=setor)

    @app.route('/setores/delete/<int:id>', methods=['POST'])
    def deletar_setor(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar setores.', 'danger')
            return redirect(url_for('login'))
        
        setor = Setor.query.get_or_404(id)

        # Verificação de hierarquia: não permitir deletar setor se há contratos associados
        if ContratoTrabalho.query.filter_by(funcao=funcao.nome).first():
            flash(f'Não foi possível deletar a função "{funcao.nome}". Existem contratos de trabalho associados a ela. Remova ou atualize os contratos primeiro.', 'danger')
            return redirect(url_for('listar_funcoes'))

        dados_antigos = {'id': setor.id, 'nome': setor.nome}
        try:
            db.session.delete(setor)
            db.session.commit()
            flash('Setor deletado com sucesso!', 'success')
            log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Setor '{setor.nome}' ID {id} deletado.", tabela_afetada="setores", registro_id=id, dados_antigos=dados_antigos, dados_novos=None)
            db.session.add(log_entry)
            db.session.commit()
            return redirect(url_for('listar_funcoes'))
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar função: {e}") # Print para depuração
            flash(f'Erro ao deletar função: {e}', 'danger')
            return redirect(url_for('listar_funcoes'))


    # --- Módulo: Cadastro de Funções ---
    @app.route('/funcoes')
    def listar_funcoes():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar funções.', 'danger')
            return redirect(url_for('login'))
        funcoes = Funcao.query.order_by(Funcao.nome).all()
        return render_template('funcoes.html', funcoes=funcoes)

    @app.route('/funcoes/add', methods=['GET', 'POST'])
    def adicionar_funcao():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar funções.', 'danger')
            return redirect(url_for('login'))
        
        if request.method == 'POST':
            nome = request.form['nome'].strip()
            if not nome:
                flash('O nome da função não pode ser vazio.', 'danger')
                return render_template('funcao_form.html', funcao=None)
            
            if Funcao.query.filter_by(nome=nome).first():
                flash(f'A função "{nome}" já existe.', 'danger')
                return render_template('funcao_form.html', funcao=None)
            
            nova_funcao = Funcao(nome=nome)
            db.session.add(nova_funcao)
            try:
                db.session.commit()
                flash('Função adicionada com sucesso!', 'success')
                log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Função '{nome}' adicionada.", tabela_afetada="funcoes", registro_id=nova_funcao.id)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('listar_funcoes'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao adicionar função: {e}") # Print para depuração
                flash(f'Erro ao adicionar função: {e}', 'danger')
                return render_template('funcao_form.html', funcao=None)
        
        return render_template('funcao_form.html', funcao=None)

    @app.route('/funcoes/edit/<int:id>', methods=['GET', 'POST'])
    def editar_funcao(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar funções.', 'danger')
            return redirect(url_for('login'))
        
        funcao = Funcao.query.get_or_404(id)

        if request.method == 'POST':
            novo_nome = request.form['nome'].strip()
            if not novo_nome:
                flash('O nome da função não pode ser vazio.', 'danger')
                return render_template('funcao_form.html', funcao=funcao)
            
            if Funcao.query.filter(Funcao.nome == novo_nome, Funcao.id != id).first():
                flash(f'A função "{novo_nome}" já existe.', 'danger')
                return render_template('funcao_form.html', funcao=funcao)
            
            dados_antigos = {'nome': funcao.nome}
            funcao.nome = novo_nome
            try:
                db.session.commit()
                flash('Função atualizada com sucesso!', 'success')
                dados_novos = {'nome': funcao.nome}
                log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Função ID {id} editada para '{novo_nome}'.", tabela_afetada="funcoes", registro_id=id, dados_antigos=dados_antigos, dados_novos=dados_novos)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('listar_funcoes'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao atualizar função: {e}") # Print para depuração
                flash(f'Erro ao atualizar função: {e}', 'danger')
                return render_template('funcao_form.html', funcao=funcao)
        
        return render_template('funcao_form.html', funcao=funcao)

    @app.route('/funcoes/delete/<int:id>', methods=['POST'])
    def deletar_funcao(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar funções.', 'danger')
            return redirect(url_for('login'))
        
        funcao = Funcao.query.get_or_404(id)

        # Verificação de hierarquia: não permitir deletar função se há contratos associados
        if ContratoTrabalho.query.filter_by(funcao=funcao.nome).first():
            flash(f'Não foi possível deletar a função "{funcao.nome}". Existem contratos de trabalho associados a ela. Remova ou atualize os contratos primeiro.', 'danger')
            return redirect(url_for('listar_funcoes'))

        dados_antigos = {'id': funcao.id, 'nome': funcao.nome}
        try:
            db.session.delete(funcao)
            db.session.commit()
            flash('Função deletada com sucesso!', 'success')
            log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f"Função '{funcao.nome}' ID {id} deletada.", tabela_afetada="funcoes", registro_id=id, dados_antigos=dados_antigos, dados_novos=None)
            db.session.add(log_entry)
            db.session.commit()
            return redirect(url_for('listar_funcoes'))
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar função: {e}") # Print para depuração
            flash(f'Erro ao deletar função: {e}', 'danger')
            return redirect(url_for('listar_funcoes'))

    # --- Módulo: Cadastro de Jornadas ---
    @app.route('/jornadas')
    def listar_jornadas():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar jornadas.', 'danger')
            return redirect(url_for('login'))
        jornadas = Jornada.query.order_by(Jornada.id).all()
        return render_template('jornadas.html', jornadas=jornadas)

    @app.route('/jornadas/add', methods=['GET', 'POST'])
    def adicionar_jornada():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar jornadas.', 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            pt_inicio = request.form['primeiro_turno_inicio']
            pt_fim = request.form['primeiro_turno_fim']
            st_inicio = request.form['segundo_turno_inicio']
            st_fim = request.form['segundo_turno_fim']
            te_inicio = request.form.get('turno_extra_inicio')
            te_fim = request.form.get('turno_extra_fim')

            nova_jornada = Jornada(
                primeiro_turno_inicio=datetime.datetime.strptime(pt_inicio, '%H:%M').time(),
                primeiro_turno_fim=datetime.datetime.strptime(pt_fim, '%H:%M').time(),
                segundo_turno_inicio=datetime.datetime.strptime(st_inicio, '%H:%M').time(),
                segundo_turno_fim=datetime.datetime.strptime(st_fim, '%H:%M').time(),
                turno_extra_inicio=datetime.datetime.strptime(te_inicio, '%H:%M').time() if te_inicio else None,
                turno_extra_fim=datetime.datetime.strptime(te_fim, '%H:%M').time() if te_fim else None
            )
            db.session.add(nova_jornada)
            try:
                db.session.commit()
                flash('Jornada adicionada com sucesso!', 'success')
                log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao='Jornada adicionada.', tabela_afetada='jornadas', registro_id=nova_jornada.id)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('listar_jornadas'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao adicionar jornada: {e}")
                flash(f'Erro ao adicionar jornada: {e}', 'danger')
                return render_template('jornada_form.html', jornada=None)

        return render_template('jornada_form.html', jornada=None)

    @app.route('/jornadas/edit/<int:id>', methods=['GET', 'POST'])
    def editar_jornada(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar jornadas.', 'danger')
            return redirect(url_for('login'))

        jornada = Jornada.query.get_or_404(id)

        if request.method == 'POST':
            pt_inicio = request.form['primeiro_turno_inicio']
            pt_fim = request.form['primeiro_turno_fim']
            st_inicio = request.form['segundo_turno_inicio']
            st_fim = request.form['segundo_turno_fim']
            te_inicio = request.form.get('turno_extra_inicio')
            te_fim = request.form.get('turno_extra_fim')

            dados_antigos = {
                'primeiro_turno_inicio': str(jornada.primeiro_turno_inicio),
                'primeiro_turno_fim': str(jornada.primeiro_turno_fim),
                'segundo_turno_inicio': str(jornada.segundo_turno_inicio),
                'segundo_turno_fim': str(jornada.segundo_turno_fim),
                'turno_extra_inicio': str(jornada.turno_extra_inicio) if jornada.turno_extra_inicio else None,
                'turno_extra_fim': str(jornada.turno_extra_fim) if jornada.turno_extra_fim else None
            }

            jornada.primeiro_turno_inicio = datetime.datetime.strptime(pt_inicio, '%H:%M').time()
            jornada.primeiro_turno_fim = datetime.datetime.strptime(pt_fim, '%H:%M').time()
            jornada.segundo_turno_inicio = datetime.datetime.strptime(st_inicio, '%H:%M').time()
            jornada.segundo_turno_fim = datetime.datetime.strptime(st_fim, '%H:%M').time()
            jornada.turno_extra_inicio = datetime.datetime.strptime(te_inicio, '%H:%M').time() if te_inicio else None
            jornada.turno_extra_fim = datetime.datetime.strptime(te_fim, '%H:%M').time() if te_fim else None

            try:
                db.session.commit()
                flash('Jornada atualizada com sucesso!', 'success')
                dados_novos = {
                    'primeiro_turno_inicio': str(jornada.primeiro_turno_inicio),
                    'primeiro_turno_fim': str(jornada.primeiro_turno_fim),
                    'segundo_turno_inicio': str(jornada.segundo_turno_inicio),
                    'segundo_turno_fim': str(jornada.segundo_turno_fim),
                    'turno_extra_inicio': str(jornada.turno_extra_inicio) if jornada.turno_extra_inicio else None,
                    'turno_extra_fim': str(jornada.turno_extra_fim) if jornada.turno_extra_fim else None
                }
                log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f'Jornada ID {id} editada.', tabela_afetada='jornadas', registro_id=id, dados_antigos=dados_antigos, dados_novos=dados_novos)
                db.session.add(log_entry)
                db.session.commit()
                return redirect(url_for('listar_jornadas'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao atualizar jornada: {e}")
                flash(f'Erro ao atualizar jornada: {e}', 'danger')
                return render_template('jornada_form.html', jornada=jornada)

        return render_template('jornada_form.html', jornada=jornada)

    @app.route('/jornadas/delete/<int:id>', methods=['POST'])
    def deletar_jornada(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar jornadas.', 'danger')
            return redirect(url_for('login'))

        jornada = Jornada.query.get_or_404(id)

        dados_antigos = {
            'id': jornada.id,
            'primeiro_turno_inicio': str(jornada.primeiro_turno_inicio),
            'primeiro_turno_fim': str(jornada.primeiro_turno_fim),
            'segundo_turno_inicio': str(jornada.segundo_turno_inicio),
            'segundo_turno_fim': str(jornada.segundo_turno_fim),
            'turno_extra_inicio': str(jornada.turno_extra_inicio) if jornada.turno_extra_inicio else None,
            'turno_extra_fim': str(jornada.turno_extra_fim) if jornada.turno_extra_fim else None
        }

        db.session.delete(jornada)
        try:
            db.session.commit()
            flash('Jornada deletada com sucesso!', 'success')
            log_entry = LogAuditoria(usuario_id=session['usuario_id'], acao=f'Jornada ID {id} deletada.', tabela_afetada='jornadas', registro_id=id, dados_antigos=dados_antigos, dados_novos=None)
            db.session.add(log_entry)
            db.session.commit()
            return redirect(url_for('listar_jornadas'))
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar jornada: {e}")
            flash(f'Erro ao deletar jornada: {e}', 'danger')
            return redirect(url_for('listar_jornadas'))

    # --- Módulo: Reajuste Salarial ---
    @app.route('/reajustes')
    def listar_reajustes():
        """
        Exibe a lista de todos os reajustes salariais.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar reajustes.', 'danger')
            return redirect(url_for('login'))
        
        reajustes = []
        try:
            # Para trazer o nome do funcionário junto, fazemos um join
            reajustes = db.session.query(ReajusteSalarial).join(Funcionario).order_by(ReajusteSalarial.data_alteracao.desc()).all()
        except Exception as e:
            print(f"ERROR: Falha ao carregar reajustes (listar_reajustes): {e}")
            flash(f"Erro ao carregar lista de reajustes salariais: {e}", 'danger')
            db.session.rollback() # Garante que a sessão esteja limpa
            
        return render_template('reajustes.html', reajustes=reajustes)

    @app.route('/reajustes/add', methods=['GET', 'POST'])
    def adicionar_reajuste():
        """
        Adiciona um novo reajuste salarial.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar reajustes.', 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            data_alteracao = datetime.datetime.strptime(request.form['data_alteracao'], '%Y-%m-%d').date()
            percentual_reajuste_salario = float(request.form['percentual_reajuste_salario'])
            percentual_reajuste_bonus = float(request.form['percentual_reajuste_bonus'])

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('reajuste_form.html', reajuste=None)

            novo_reajuste = ReajusteSalarial(
                cpf_funcionario=cpf_funcionario,
                data_alteracao=data_alteracao,
                percentual_reajuste_salario=percentual_reajuste_salario,
                percentual_reajuste_bonus=percentual_reajuste_bonus
            )
            db.session.add(novo_reajuste)
            try:
                db.session.commit()
                flash('Reajuste salarial adicionado com sucesso!', 'success')
                
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Reajuste Salarial adicionado para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="reajustes_salariais",
                    registro_id=novo_reajuste.id # ID do reajuste
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_reajustes'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao adicionar reajuste: {e}") # Print para depuração
                flash(f'Erro ao adicionar reajuste: {e}', 'danger')
                return render_template('reajuste_form.html', reajuste=None)
        
        return render_template('reajuste_form.html', reajuste=None)

    @app.route('/reajustes/edit/<int:id>', methods=['GET', 'POST'])
    def editar_reajuste(id):
        """
        Edita um reajuste salarial existente.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar reajustes.', 'danger')
            return redirect(url_for('login'))
        
        reajuste = ReajusteSalarial.query.get_or_404(id)

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            data_alteracao = datetime.datetime.strptime(request.form['data_alteracao'], '%Y-%m-%d').date()
            percentual_reajuste_salario = float(request.form['percentual_reajuste_salario'])
            percentual_reajuste_bonus = float(request.form['percentual_reajuste_bonus'])

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('reajuste_form.html', reajuste=reajuste)

            dados_antigos = {
                'cpf_funcionario': reajuste.cpf_funcionario,
                'data_alteracao': str(reajuste.data_alteracao),
                'percentual_reajuste_salario': float(reajuste.percentual_reajuste_salario),
                'percentual_reajuste_bonus': float(reajuste.percentual_reajuste_bonus)
            }

            reajuste.cpf_funcionario = cpf_funcionario
            reajuste.data_alteracao = data_alteracao
            reajuste.percentual_reajuste_salario = percentual_reajuste_salario
            reajuste.percentual_reajuste_bonus = percentual_reajuste_bonus

            try:
                db.session.commit()
                flash('Reajuste salarial atualizado com sucesso!', 'success')
                
                dados_novos = {
                    'cpf_funcionario': reajuste.cpf_funcionario,
                    'data_alteracao': str(reajuste.data_alteracao),
                    'percentual_reajuste_salario': float(reajuste.percentual_reajuste_salario),
                    'percentual_reajuste_bonus': float(reajuste.percentual_reajuste_bonus)
                }

                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Reajuste Salarial ID {id} editado para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="reajustes_salariais",
                    registro_id=id,
                    dados_antigos=dados_antigos,
                    dados_novos=dados_novos
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_reajustes'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao atualizar reajuste: {e}") # Print para depuração
                flash(f'Erro ao atualizar reajuste: {e}', 'danger')
                return render_template('reajuste_form.html', reajuste=reajuste)
        
        return render_template('reajuste_form.html', reajuste=reajuste)

    @app.route('/reajustes/delete/<int:id>', methods=['POST'])
    def deletar_reajuste(id):
        """
        Deleta um reajuste salarial existente.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar reajustes.', 'danger')
            return redirect(url_for('login'))
        
        reajuste = ReajusteSalarial.query.get_or_404(id)

        dados_antigos = {
            'id': reajuste.id, 'cpf_funcionario': reajuste.cpf_funcionario,
            'data_alteracao': str(reajuste.data_alteracao),
            'percentual_reajuste_salario': float(reajuste.percentual_reajuste_salario),
            'percentual_reajuste_bonus': float(reajuste.percentual_reajuste_bonus)
        }
        
        db.session.delete(reajuste)
        try:
            db.session.commit()
            flash('Reajuste salarial deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Reajuste Salarial ID {id} deletado. Funcionário: {reajuste.funcionario.nome if reajuste.funcionario else reajuste.cpf_funcionario}.",
                tabela_afetada="reajustes_salariais",
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()

            return redirect(url_for('listar_reajustes'))
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar reajuste: {e}") # Print para depuração
            flash(f'Erro ao deletar reajuste: {e}', 'danger')
            return redirect(url_for('listar_reajustes'))

    # --- Módulo: Demissões ---
    
    def calcular_dias_aviso(data_admissao, data_demissao):
        """Calcula a quantidade de dias de aviso prévio."""
        anos = (data_demissao - data_admissao).days // 365
        dias = 30 + max(anos - 1, 0) * 3
        return dias if dias <= 90 else 90
    @app.route('/demissoes')
    def listar_demissoes():
        """
        Exibe a lista de todos os registros de demissão.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar demissões.', 'danger')
            return redirect(url_for('login'))
        
        demissoes = []
        try:
            demissoes = db.session.query(Demissao).join(Funcionario).order_by(Demissao.data_demissao.desc()).all()
        except Exception as e:
            print(f"ERROR: Falha ao carregar demissões (listar_demissoes): {e}")
            flash(f"Erro ao carregar lista de demissões: {e}", 'danger')
            db.session.rollback() # Garante que a sessão esteja limpa
            
        return render_template('demissoes.html', demissoes=demissoes)

    @app.route('/demissoes/add', methods=['GET', 'POST'])
    def adicionar_demissao():
        """
        Registra uma nova demissão.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem registrar demissões.', 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            data_demissao = datetime.datetime.strptime(request.form['data_demissao'], '%Y-%m-%d').date()
            ultimo_dia_trabalhado = datetime.datetime.strptime(request.form['ultimo_dia_trabalhado'], '%Y-%m-%d').date()
            tipo_desligamento = request.form['tipo_desligamento']
            classificacao = request.form['classificacao']
            motivo_demissao = request.form.get('motivo_demissao')
            aviso_previo = request.form['aviso_previo']
            data_aviso_previo_str = request.form.get('data_aviso_previo')
            data_aviso_previo = datetime.datetime.strptime(data_aviso_previo_str, '%Y-%m-%d').date() if data_aviso_previo_str else None
            quantidade_dias_aviso_str = request.form.get('quantidade_dias_aviso')
            quantidade_dias_aviso = int(quantidade_dias_aviso_str) if quantidade_dias_aviso_str else None
            data_termino_aviso_str = request.form.get('data_termino_aviso')
            data_termino_aviso = datetime.datetime.strptime(data_termino_aviso_str, '%Y-%m-%d').date() if data_termino_aviso_str else None

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('demissao_form.html', demissao=None)

            # Validação: Motivo da Demissão obrigatório e com mínimo de 50 caracteres
            if not motivo_demissao or len(motivo_demissao.strip()) < 50:
                flash('O Motivo da Demissão é obrigatório e deve ter no mínimo 50 caracteres.', 'danger')
                return render_template('demissao_form.html', demissao=None)

            # Validações adicionais (ex: data de demissão não pode ser antes da admissão)
            # Você pode adicionar validações mais complexas aqui.
            contrato_ativo = ContratoTrabalho.query.filter_by(cpf_funcionario=cpf_funcionario, status=True).first()
            if contrato_ativo and data_demissao < contrato_ativo.data_admissao:
                flash('A data de demissão não pode ser anterior à data de admissão.', 'danger')
                return render_template('demissao_form.html', demissao=None)

            if contrato_ativo:
                if quantidade_dias_aviso is None:
                    quantidade_dias_aviso = calcular_dias_aviso(contrato_ativo.data_admissao, data_demissao)
                if data_aviso_previo is None and quantidade_dias_aviso is not None:
                    data_aviso_previo = data_demissao - datetime.timedelta(days=quantidade_dias_aviso)
                if data_termino_aviso is None and quantidade_dias_aviso is not None:
                    data_termino_aviso = data_demissao - datetime.timedelta(days=1)

            # Criar o registro de demissão
            nova_demissao = Demissao(
                cpf_funcionario=cpf_funcionario,
                data_demissao=data_demissao,
                ultimo_dia_trabalhado=ultimo_dia_trabalhado,
                tipo_desligamento=tipo_desligamento,
                classificacao=classificacao,
                motivo_demissao=motivo_demissao,
                aviso_previo=aviso_previo,
                data_aviso_previo=data_aviso_previo,
                quantidade_dias_aviso=quantidade_dias_aviso,
                data_termino_aviso=data_termino_aviso
            )
            db.session.add(nova_demissao)
            try:
                db.session.commit()

                # Opcional: Atualizar o status do contrato para inativo
                if contrato_ativo:
                    contrato_ativo.status = False
                    contrato_ativo.data_demissao = data_demissao # Atualiza a data de demissão no contrato
                    db.session.add(contrato_ativo) # Adiciona a mudança na sessão
                    db.session.commit() # Salva a mudança no contrato

                funcionario.status = "Inativo"
                db.session.add(funcionario)
                db.session.commit()
                
                flash('Registro de demissão adicionado com sucesso!', 'success')
                
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Demissão registrada para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="demissoes",
                    registro_id=nova_demissao.id
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_demissoes'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao registrar demissão: {e}") # Print para depuração
                flash(f'Erro ao registrar demissão: {e}', 'danger')
                return render_template('demissao_form.html', demissao=None)
        
        return render_template('demissao_form.html', demissao=None)

    @app.route('/demissoes/edit/<int:id>', methods=['GET', 'POST'])
    def editar_demissao(id):
        """
        Edita um registro de demissão existente.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar demissões.', 'danger')
            return redirect(url_for('login'))
        
        demissao = Demissao.query.get_or_404(id)

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            data_demissao = datetime.datetime.strptime(request.form['data_demissao'], '%Y-%m-%d').date()
            ultimo_dia_trabalhado = datetime.datetime.strptime(request.form['ultimo_dia_trabalhado'], '%Y-%m-%d').date()
            tipo_desligamento = request.form['tipo_desligamento']
            classificacao = request.form['classificacao']
            motivo_demissao = request.form.get('motivo_demissao')
            aviso_previo = request.form['aviso_previo']
            data_aviso_previo_str = request.form.get('data_aviso_previo')
            data_aviso_previo = datetime.datetime.strptime(data_aviso_previo_str, '%Y-%m-%d').date() if data_aviso_previo_str else None
            quantidade_dias_aviso_str = request.form.get('quantidade_dias_aviso')
            quantidade_dias_aviso = int(quantidade_dias_aviso_str) if quantidade_dias_aviso_str else None
            data_termino_aviso_str = request.form.get('data_termino_aviso')
            data_termino_aviso = datetime.datetime.strptime(data_termino_aviso_str, '%Y-%m-%d').date() if data_termino_aviso_str else None

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('demissao_form.html', demissao=demissao)

            # Validação: Motivo da Demissão obrigatório e com mínimo de 50 caracteres
            if not motivo_demissao or len(motivo_demissao.strip()) < 50:
                flash('O Motivo da Demissão é obrigatório e deve ter no mínimo 50 caracteres.', 'danger')
                return render_template('demissao_form.html', demissao=demissao)

            # Validação: data de demissão não pode ser anterior à data de admissão
            contrato_do_funcionario = ContratoTrabalho.query.filter_by(cpf_funcionario=cpf_funcionario).order_by(ContratoTrabalho.data_admissao.desc()).first()
            if contrato_do_funcionario and data_demissao < contrato_do_funcionario.data_admissao:
                flash('A data de demissão não pode ser anterior à data de admissão.', 'danger')
                return render_template('demissao_form.html', demissao=demissao)

            if contrato_do_funcionario:
                if quantidade_dias_aviso is None:
                    quantidade_dias_aviso = calcular_dias_aviso(contrato_do_funcionario.data_admissao, data_demissao)
                if data_aviso_previo is None and quantidade_dias_aviso is not None:
                    data_aviso_previo = data_demissao - datetime.timedelta(days=quantidade_dias_aviso)
                if data_termino_aviso is None and quantidade_dias_aviso is not None:
                    data_termino_aviso = data_demissao - datetime.timedelta(days=1)

            dados_antigos = {
                'cpf_funcionario': demissao.cpf_funcionario,
                'data_demissao': str(demissao.data_demissao),
                'ultimo_dia_trabalhado': str(demissao.ultimo_dia_trabalhado),
                'tipo_desligamento': demissao.tipo_desligamento,
                'classificacao': demissao.classificacao,
                'motivo_demissao': demissao.motivo_demissao,
                'aviso_previo': demissao.aviso_previo,
                'data_aviso_previo': str(demissao.data_aviso_previo) if demissao.data_aviso_previo else None,
                'quantidade_dias_aviso': demissao.quantidade_dias_aviso,
                'data_termino_aviso': str(demissao.data_termino_aviso) if demissao.data_termino_aviso else None
            }
 
            demissao.cpf_funcionario = cpf_funcionario
            demissao.data_demissao = data_demissao
            demissao.ultimo_dia_trabalhado = ultimo_dia_trabalhado
            demissao.tipo_desligamento = tipo_desligamento
            demissao.classificacao = classificacao
            demissao.motivo_demissao = motivo_demissao
            demissao.aviso_previo = aviso_previo
            demissao.data_aviso_previo = data_aviso_previo
            demissao.quantidade_dias_aviso = quantidade_dias_aviso
            demissao.data_termino_aviso = data_termino_aviso

            try:
                db.session.commit()
                
                # Opcional: Atualizar o status do contrato para inativo se este for o desligamento mais recente
                # E o contrato estava ativo.
                if contrato_do_funcionario and demissao.data_demissao == contrato_do_funcionario.data_demissao:
                    # Se a demissão sendo editada corresponde ao último contrato encerrado,
                    # e o tipo de desligamento sugere inatividade, certifique-se de que o contrato esteja inativo.
                    # Se o tipo de desligamento for reversão (ex: demissão cancelada, recontratação),
                    # isso exigiria lógica mais complexa. Por simplicidade, assumimos que demissões inativam.
                    contrato_ativo_ou_recente = ContratoTrabalho.query.filter_by(cpf_funcionario=cpf_funcionario).order_by(ContratoTrabalho.data_admissao.desc()).first()
                    if contrato_ativo_ou_recente:
                        contrato_ativo_ou_recente.status = False
                        contrato_ativo_ou_recente.data_demissao = demissao.data_demissao
                        db.session.add(contrato_ativo_ou_recente)
                        db.session.commit()

                flash('Registro de demissão atualizado com sucesso!', 'success')

                dados_novos = {
                    'cpf_funcionario': demissao.cpf_funcionario,
                    'data_demissao': str(demissao.data_demissao),
                    'ultimo_dia_trabalhado': str(demissao.ultimo_dia_trabalhado),
                    'tipo_desligamento': demissao.tipo_desligamento,
                    'motivo_demissao': demissao.motivo_demissao,
                    'classificacao': demissao.classificacao,
                    'aviso_previo': demissao.aviso_previo,
                    'data_aviso_previo': str(demissao.data_aviso_previo) if demissao.data_aviso_previo else None,
                    'quantidade_dias_aviso': demissao.quantidade_dias_aviso,
                    'data_termino_aviso': str(demissao.data_termino_aviso) if demissao.data_termino_aviso else None
                }
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Demissão ID {id} editada para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="demissoes",
                    registro_id=id,
                    dados_antigos=dados_antigos,
                    dados_novos=dados_novos
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_demissoes'))
            except Exception as e:
                db.session.rollback()
                print(f"Erro ao atualizar demissão: {e}") # Print para depuração
                flash(f'Erro ao atualizar demissão: {e}', 'danger')
                return render_template('demissao_form.html', demissao=demissao)
        
        return render_template('demissao_form.html', demissao=demissao)

    @app.route('/demissoes/delete/<int:id>', methods=['POST'])
    def deletar_demissao(id):
        """
        Deleta um registro de demissão existente.
        """
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar demissões.', 'danger')
            return redirect(url_for('login'))
        
        demissao = Demissao.query.get_or_404(id)

        dados_antigos = {
            'id': demissao.id, 'cpf_funcionario': demissao.cpf_funcionario,
            'data_demissao': str(demissao.data_demissao),
            'ultimo_dia_trabalhado': str(demissao.ultimo_dia_trabalhado),
            'tipo_desligamento': demissao.tipo_desligamento,
            'motivo_demissao': demissao.motivo_demissao,
            'aviso_previo': demissao.aviso_previo,
            'data_aviso_previo': str(demissao.data_aviso_previo) if demissao.data_aviso_previo else None,
            'quantidade_dias_aviso': demissao.quantidade_dias_aviso,
            'data_termino_aviso': str(demissao.data_termino_aviso) if demissao.data_termino_aviso else None
        }
        
        db.session.delete(demissao)
        try:
            db.session.commit()
            flash('Registro de demissão deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Demissão ID {id} deletada. Funcionário: {demissao.funcionario.nome if demissao.funcionario else demissao.cpf_funcionario}.",
                tabela_afetada="demissoes",
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()

            return redirect(url_for('listar_demissoes'))
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar demissão: {e}") # Print para depuração
            flash(f'Erro ao deletar demissão: {e}', 'danger')
            return redirect(url_for('listar_demissoes'))

    # --- Módulo de Controle de Férias ---
    @app.route('/ferias')
    def listar_ferias():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerenciar férias.', 'danger')
            return redirect(url_for('login'))
        
        ferias_registros = []
        try:
            # Join com Funcionario para exibir o nome
            ferias_registros = db.session.query(ControleFerias).join(Funcionario).order_by(ControleFerias.periodo_aquisitivo_inicio.desc()).all()
        except Exception as e:
            print(f"ERROR: Falha ao carregar registros de férias (listar_ferias): {e}")
            flash(f"Erro ao carregar lista de férias: {e}", 'danger')
            db.session.rollback()
            
        return render_template('ferias.html', ferias_registros=ferias_registros)

    @app.route('/ferias/add', methods=['GET', 'POST'])
    def adicionar_ferias():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar registros de férias.', 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            periodo_aquisitivo_inicio = datetime.datetime.strptime(request.form['periodo_aquisitivo_inicio'], '%Y-%m-%d').date()
            periodo_aquisitivo_fim = datetime.datetime.strptime(request.form['periodo_aquisitivo_fim'], '%Y-%m-%d').date()
            
            ferias_gozadas_inicio_str = request.form.get('ferias_gozadas_inicio')
            ferias_gozadas_inicio = datetime.datetime.strptime(ferias_gozadas_inicio_str, '%Y-%m-%d').date() if ferias_gozadas_inicio_str else None
            
            ferias_gozadas_fim_str = request.form.get('ferias_gozadas_fim')
            ferias_gozadas_fim = datetime.datetime.strptime(ferias_gozadas_fim_str, '%Y-%m-%d').date() if ferias_gozadas_fim_str else None

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('ferias_form.html', ferias=None)

            # Validações de data
            if periodo_aquisitivo_inicio >= periodo_aquisitivo_fim:
                flash('A data de início do período aquisitivo deve ser anterior à data de fim.', 'danger')
                return render_template('ferias_form.html', ferias=None)
            
            if ferias_gozadas_inicio and ferias_gozadas_fim and ferias_gozadas_inicio >= ferias_gozadas_fim:
                flash('A data de início das férias gozadas deve ser anterior à data de fim.', 'danger')
                return render_template('ferias_form.html', ferias=None)

            if ferias_gozadas_inicio and (ferias_gozadas_inicio < periodo_aquisitivo_inicio or ferias_gozadas_inicio > periodo_aquisitivo_fim):
                flash('As férias gozadas devem estar dentro do período aquisitivo.', 'danger')
                return render_template('ferias_form.html', ferias=None)


            novo_registro_ferias = ControleFerias(
                cpf_funcionario=cpf_funcionario,
                periodo_aquisitivo_inicio=periodo_aquisitivo_inicio,
                periodo_aquisitivo_fim=periodo_aquisitivo_fim,
                ferias_gozadas_inicio=ferias_gozadas_inicio,
                ferias_gozadas_fim=ferias_gozadas_fim
            )
            db.session.add(novo_registro_ferias)
            try:
                db.session.commit()
                flash('Registro de férias adicionado com sucesso!', 'success')
                
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Registro de férias adicionado para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="controle_ferias",
                    registro_id=novo_registro_ferias.id
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_ferias'))
            except Exception as e:
                db.session.rollback()
                print(f"ERROR: Erro ao adicionar registro de férias: {e}")
                flash(f'Erro ao adicionar registro de férias: {e}', 'danger')
                return render_template('ferias_form.html', ferias=None)
        
        return render_template('ferias_form.html', ferias=None)

    @app.route('/ferias/edit/<int:id>', methods=['GET', 'POST'])
    def editar_ferias(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem editar registros de férias.', 'danger')
            return redirect(url_for('login'))
        
        ferias = ControleFerias.query.get_or_404(id)

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            periodo_aquisitivo_inicio = datetime.datetime.strptime(request.form['periodo_aquisitivo_inicio'], '%Y-%m-%d').date()
            periodo_aquisitivo_fim = datetime.datetime.strptime(request.form['periodo_aquisitivo_fim'], '%Y-%m-%d').date()
            
            ferias_gozadas_inicio_str = request.form.get('ferias_gozadas_inicio')
            ferias_gozadas_inicio = datetime.datetime.strptime(ferias_gozadas_inicio_str, '%Y-%m-%d').date() if ferias_gozadas_inicio_str else None
            
            ferias_gozadas_fim_str = request.form.get('ferias_gozadas_fim')
            ferias_gozadas_fim = datetime.datetime.strptime(ferias_gozadas_fim_str, '%Y-%m-%d').date() if ferias_gozadas_fim_str else None

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('ferias_form.html', ferias=ferias)

            # Validações de data
            if periodo_aquisitivo_inicio >= periodo_aquisitivo_fim:
                flash('A data de início do período aquisitivo deve ser anterior à data de fim.', 'danger')
                return render_template('ferias_form.html', ferias=ferias)
            
            if ferias_gozadas_inicio and ferias_gozadas_fim and ferias_gozadas_inicio >= ferias_gozadas_fim:
                flash('A data de início das férias gozadas deve ser anterior à data de fim.', 'danger')
                return render_template('ferias_form.html', ferias=ferias)

            if ferias_gozadas_inicio and (ferias_gozadas_inicio < periodo_aquisitivo_inicio or ferias_gozadas_inicio > periodo_aquisitivo_fim):
                flash('As férias gozadas devem estar dentro do período aquisitivo.', 'danger')
                return render_template('ferias_form.html', ferias=ferias)

            dados_antigos = {
                'cpf_funcionario': ferias.cpf_funcionario,
                'periodo_aquisitivo_inicio': str(ferias.periodo_aquisitivo_inicio),
                'periodo_aquisitivo_fim': str(ferias.periodo_aquisitivo_fim),
                'ferias_gozadas_inicio': str(ferias.ferias_gozadas_inicio) if ferias.ferias_gozadas_inicio else None,
                'ferias_gozadas_fim': str(ferias.ferias_gozadas_fim) if ferias.ferias_gozadas_fim else None
            }

            ferias.cpf_funcionario = cpf_funcionario
            ferias.periodo_aquisitivo_inicio = periodo_aquisitivo_inicio
            ferias.periodo_aquisitivo_fim = periodo_aquisitivo_fim
            ferias.ferias_gozadas_inicio = ferias_gozadas_inicio
            ferias.ferias_gozadas_fim = ferias_gozadas_fim

            try:
                db.session.commit()
                flash('Registro de férias atualizado com sucesso!', 'success')

                dados_novos = {
                    'cpf_funcionario': ferias.cpf_funcionario,
                    'periodo_aquisitivo_inicio': str(ferias.periodo_aquisitivo_inicio),
                    'periodo_aquisitivo_fim': str(ferias.periodo_aquisitivo_fim),
                    'ferias_gozadas_inicio': str(ferias.ferias_gozadas_inicio) if ferias.ferias_gozadas_inicio else None,
                    'ferias_gozadas_fim': str(ferias.ferias_gozadas_fim) if ferias.ferias_gozadas_fim else None
                }
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Registro de férias ID {id} editado para {funcionario.nome} ({cpf_funcionario}).",
                    tabela_afetada="controle_ferias",
                    registro_id=id,
                    dados_antigos=dados_antigos,
                    dados_novos=dados_novos
                )
                db.session.add(log_entry)
                db.session.commit()

                return redirect(url_for('listar_ferias'))
            except Exception as e:
                db.session.rollback()
                print(f"ERROR: Erro ao atualizar registro de férias: {e}")
                flash(f'Erro ao atualizar registro de férias: {e}', 'danger')
                return render_template('ferias_form.html', ferias=ferias)
        
        return render_template('ferias_form.html', ferias=ferias)

    @app.route('/ferias/delete/<int:id>', methods=['POST'])
    def deletar_ferias(id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar registros de férias.', 'danger')
            return redirect(url_for('login'))
        
        ferias = ControleFerias.query.get_or_404(id)

        dados_antigos = {
            'id': ferias.id,
            'cpf_funcionario': ferias.cpf_funcionario,
            'periodo_aquisitivo_inicio': str(ferias.periodo_aquisitivo_inicio),
            'periodo_aquisitivo_fim': str(ferias.periodo_aquisitivo_fim),
            'ferias_gozadas_inicio': str(ferias.ferias_gozadas_inicio) if ferias.ferias_gozadas_inicio else None,
            'ferias_gozadas_fim': str(ferias.ferias_gozadas_fim) if ferias.ferias_gozadas_fim else None
        }
        
        db.session.delete(ferias)
        try:
            db.session.commit()
            flash('Registro de férias deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Registro de férias ID {id} deletado. Funcionário: {ferias.funcionario.nome if ferias.funcionario else ferias.cpf_funcionario}.",
                tabela_afetada="controle_ferias",
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()

            return redirect(url_for('listar_ferias'))
        except Exception as e:
            db.session.rollback()
            print(f"ERROR: Erro ao deletar registro de férias: {e}")
            flash(f'Erro ao deletar registro de férias: {e}', 'danger')
            return redirect(url_for('listar_ferias'))


    # --- Módulo de Adiantamentos Salariais ---

    @app.route('/adiantamentos')
    def listar_adiantamentos():
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))

        registros = db.session.query(Adiantamento).join(Funcionario).order_by(Adiantamento.data_adiantamento.desc()).all()
        return render_template('adiantamentos.html', adiantamentos=registros)

    @app.route('/adiantamentos/add', methods=['GET', 'POST'])
    def adicionar_adiantamento():
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem adicionar adiantamentos.', 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            valor_total = float(request.form['valor_total'])
            numero_parcelas = int(request.form['numero_parcelas'])
            data_adiantamento = datetime.datetime.strptime(request.form['data_adiantamento'], '%Y-%m-%d').date()
            observacoes = request.form.get('observacoes')

            contrato = (
                ContratoTrabalho.query
                .filter_by(cpf_funcionario=cpf_funcionario, status=True)
                .order_by(ContratoTrabalho.data_admissao.desc())
                .first()
            )
            if not contrato:
                flash('Funcionário sem contrato ativo.', 'danger')
                return render_template('adiantamento_form.html', adiantamento=None)

            novo = Adiantamento(
                cpf_funcionario=cpf_funcionario,
                salario_base=contrato.salario_inicial,
                valor_total=valor_total,
                numero_parcelas=numero_parcelas,
                data_adiantamento=data_adiantamento,
                observacoes=observacoes,
            )
            db.session.add(novo)
            db.session.flush()

            valor_parcela = round(valor_total / numero_parcelas, 2)
            for i in range(numero_parcelas):
                data_prevista = data_adiantamento + datetime.timedelta(days=30 * i)
                parcela = ParcelaAdiantamento(
                    adiantamento_id=novo.id,
                    numero=i + 1,
                    data_prevista=data_prevista,
                    valor=valor_parcela,
                )
                db.session.add(parcela)

            db.session.commit()
            flash('Adiantamento cadastrado com sucesso!', 'success')
            return redirect(url_for('listar_adiantamentos'))

        return render_template('adiantamento_form.html', adiantamento=None)

    @app.route('/adiantamentos/<int:adiantamento_id>')
    def visualizar_adiantamento(adiantamento_id):
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))

        adiantamento = Adiantamento.query.get_or_404(adiantamento_id)
        return render_template('parcelas_adiantamento.html', adiantamento=adiantamento)

    @app.route('/adiantamentos/parcela/<int:parcela_id>/descontar', methods=['POST'])
    def descontar_parcela(parcela_id):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado.', 'danger')
            return redirect(url_for('login'))

        parcela = ParcelaAdiantamento.query.get_or_404(parcela_id)
        parcela.situacao = 'Descontada'
        db.session.commit()
        flash('Parcela marcada como descontada.', 'success')
        return redirect(url_for('visualizar_adiantamento', adiantamento_id=parcela.adiantamento_id))

    @app.route('/integracao/adiantamentos/<int:ano>/<int:mes>')
    def integracao_adiantamentos(ano, mes):
        inicio = datetime.date(ano, mes, 1)
        fim = inicio + datetime.timedelta(days=31)
        parcelas = ParcelaAdiantamento.query.filter(
            ParcelaAdiantamento.data_prevista >= inicio,
            ParcelaAdiantamento.data_prevista < fim,
        ).all()
        resultado = [
            {
                'cpf_funcionario': p.adiantamento.cpf_funcionario,
                'valor': float(p.valor),
                'numero_parcela': p.numero,
                'total_parcelas': p.adiantamento.numero_parcelas,
            }
            for p in parcelas if p.situacao != 'Descontada'
        ]
        return jsonify(resultado)

    # --- Módulo de Relatórios ---
    @app.route('/relatorios/ficha_cadastral/<string:cpf>')
    def relatorios_ficha_cadastral(cpf):
        if 'usuario_id' not in session or session['tipo_usuario'] not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem gerar relatórios.', 'danger')
            return redirect(url_for('login'))

        funcionario = Funcionario.query.get_or_404(cpf)
        
        # Obter o contrato mais recente (pode ser ativo ou inativo)
        contrato = ContratoTrabalho.query.filter_by(cpf_funcionario=cpf).order_by(ContratoTrabalho.data_admissao.desc()).first()
        
        # Obter dependentes
        dependentes = Dependente.query.filter_by(cpf_funcionario=cpf).all()

        # Obter reajustes salariais
        reajustes = ReajusteSalarial.query.filter_by(cpf_funcionario=cpf).order_by(ReajusteSalarial.data_alteracao.asc()).all()

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=A4,
                                rightMargin=inch/2, leftMargin=inch/2,
                                topMargin=inch/2, bottomMargin=inch/2)
        
        styles = getSampleStyleSheet()
        
        # Estilos personalizados (criados a partir de estilos padrão para evitar KeyError)
        # Título
        style_title = ParagraphStyle(name='CustomTitle', fontSize=18, leading=22, alignment=TA_CENTER,
                                  fontName='Helvetica-Bold', textColor=HexColor('#1F3A5F'))

        # Subtítulos
        style_heading2 = ParagraphStyle(name='CustomHeading2', fontSize=14, leading=18, alignment=TA_LEFT,
                                  fontName='Helvetica-Bold', textColor=HexColor('#4F83CC'))

        # Texto Normal
        style_normal = ParagraphStyle(name='CustomNormal', fontSize=10, leading=12, alignment=TA_LEFT,
                                  fontName='Helvetica', textColor=HexColor('#1F3A5F'))

        # Texto Pequeno e Negrito
        style_small_bold = ParagraphStyle(name='CustomSmallBold', fontSize=9, leading=11, alignment=TA_LEFT,
                                  fontName='Helvetica-Bold', textColor=HexColor('#1F3A5F'))

        # Texto Pequeno
        style_small = ParagraphStyle(name='CustomSmall', fontSize=9, leading=11, alignment=TA_LEFT,
                                  fontName='Helvetica', textColor=HexColor('#1F3A5F'))

        table_style = TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), HexColor('#4F83CC')),
            ('TEXTCOLOR', (0, 0), (-1, 0), white),
            ('GRID', (0, 0), (-1, -1), 0.25, black),
            ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [white, lightgrey]),
        ])


        story = []

        # Título
        story.append(Paragraph("FICHA CADASTRAL DO FUNCIONÁRIO", style_title))
        story.append(Spacer(1, 0.2 * inch))

        # Foto do Funcionário (se houver)
        if funcionario.foto_base64:
            try:
                # Verifica se a string Base64 contém o prefixo antes de dividi-la
                if funcionario.foto_base64.startswith('data:image'):
                    img_data_base64 = funcionario.foto_base64.split(',', 1)[1]
                else:
                    img_data_base64 = funcionario.foto_base64
                
                # Aumenta o try-except para a decodificação e criação da imagem
                img_data = BytesIO(base64.b64decode(img_data_base64))
                img = Image(img_data)
                img._restrictSize(1.5 * inch, 1.5 * inch) # Ajusta o tamanho da imagem
                img.hAlign = 'CENTER'
                story.append(img)
                story.append(Spacer(1, 0.1 * inch))
            except Exception as e:
                # Logar o erro completo para depuração
                print(f"ERROR: Falha ao carregar ou processar foto Base64 para PDF: {e}")
                story.append(Paragraph(f"Falha ao carregar foto: {e}", style_small))
                story.append(Spacer(1, 0.1 * inch))
        else:
            story.append(Paragraph("Foto: Não disponível", style_small))
            story.append(Spacer(1, 0.1 * inch))


        # Dados Pessoais
        dados_pessoais = [
            [Paragraph("Nome Completo", style_small_bold), Paragraph(funcionario.nome, style_small)],
            [Paragraph("CPF", style_small_bold), Paragraph(funcionario.cpf, style_small)],
            [Paragraph("PIS", style_small_bold), Paragraph(funcionario.pis, style_small)],
            [Paragraph("IDFace", style_small_bold), Paragraph(funcionario.id_face, style_small)],
            [Paragraph("Data de Nascimento", style_small_bold), Paragraph(funcionario.data_nascimento.strftime('%d/%m/%Y'), style_small)],
            [Paragraph("Sexo", style_small_bold), Paragraph(funcionario.sexo or 'N/A', style_small)],
            [Paragraph("Telefone", style_small_bold), Paragraph(funcionario.telefone, style_small)],
            [Paragraph("Grau de Instrução", style_small_bold), Paragraph(funcionario.grau_instrucao or 'N/A', style_small)],
            [Paragraph("Endereço", style_small_bold), Paragraph(funcionario.endereco, style_small)],
            [Paragraph("Bairro", style_small_bold), Paragraph(funcionario.bairro or 'N/A', style_small)],
            [Paragraph("Cidade", style_small_bold), Paragraph(funcionario.cidade, style_small)],
            [Paragraph("Estado", style_small_bold), Paragraph(funcionario.estado, style_small)],
            [Paragraph("CEP", style_small_bold), Paragraph(funcionario.cep, style_small)],
        ]
        tabela_pessoais = Table([[Paragraph('DADOS PESSOAIS', style_heading2), '']] + dados_pessoais, colWidths=[2.2*inch, 3.8*inch])
        tabela_pessoais.setStyle(table_style)
        story.append(tabela_pessoais)
        story.append(Spacer(1, 0.2 * inch))

        # Dados Bancários
        
        if funcionario.chave_pix:
            dados_bancarios = [[Paragraph("Chave PIX", style_small_bold), Paragraph(funcionario.chave_pix, style_small)]]
        else:
            dados_bancarios = [
                [Paragraph("Código Banco", style_small_bold), Paragraph(funcionario.codigo_banco or 'N/A', style_small)],
                [Paragraph("Nome Banco", style_small_bold), Paragraph(funcionario.nome_banco or 'N/A', style_small)],
                [Paragraph("Código Agência", style_small_bold), Paragraph(funcionario.codigo_agencia or 'N/A', style_small)],
                [Paragraph("Número Conta", style_small_bold), Paragraph(funcionario.numero_conta or 'N/A', style_small)],
                [Paragraph("Variação Conta", style_small_bold), Paragraph(funcionario.variacao_conta or 'N/A', style_small)],
            ]
        tabela_bancaria = Table([[Paragraph('DADOS BANCÁRIOS', style_heading2), '']] + dados_bancarios, colWidths=[2.2*inch, 3.8*inch])
        tabela_bancaria.setStyle(table_style)
        story.append(tabela_bancaria)
        story.append(Spacer(1, 0.2 * inch))

        # Informações de Contrato (do último contrato, se houver)
        
        if contrato:
            dados_contrato = [
                [Paragraph("Setor", style_small_bold), Paragraph(contrato.setor, style_small)],
                [Paragraph("Função", style_small_bold), Paragraph(contrato.funcao, style_small)],
                [Paragraph("Salário Inicial", style_small_bold), Paragraph(f"R$ {contrato.salario_inicial:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), style_small)],
                [Paragraph("Bônus", style_small_bold), Paragraph(f"R$ {contrato.bonus:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), style_small)],
                [Paragraph("Regime de Contratação", style_small_bold), Paragraph(contrato.regime_contratacao, style_small)],
                [Paragraph("Data de Admissão", style_small_bold), Paragraph(contrato.data_admissao.strftime('%d/%m/%Y'), style_small)],
                [Paragraph("Data de Demissão", style_small_bold), Paragraph(contrato.data_demissao.strftime('%d/%m/%Y') if contrato.data_demissao else 'N/A', style_small)],
                [Paragraph("Status Contrato", style_small_bold), Paragraph('Ativo' if contrato.status else 'Inativo', style_small)],
            ]
            tabela_contrato = Table([[Paragraph('INFORMAÇÕES DE CONTRATO (Último Contrato)', style_heading2), '']] + dados_contrato, colWidths=[2.2*inch, 3.8*inch])
            tabela_contrato.setStyle(table_style)
            story.append(tabela_contrato)
        else:
            story.append(Paragraph("Nenhum contrato de trabalho encontrado.", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Dependentes
        
        if dependentes:
            dados_dep = [[Paragraph("Nome", style_small_bold), Paragraph("CPF", style_small_bold), Paragraph("Data Nasc.", style_small_bold), Paragraph("Salário Família", style_small_bold)]]
            for dep in dependentes:
                dados_dep.append([
                    Paragraph(dep.nome_dependente, style_small),
                    Paragraph(dep.cpf_dependente, style_small),
                    Paragraph(dep.data_nascimento.strftime('%d/%m/%Y'), style_small),
                    Paragraph(f"R$ {dep.salario_familia:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), style_small),
                ])
            tabela_dep = Table([[Paragraph('DEPENDENTES', style_heading2), '', '', '']] + dados_dep, colWidths=[2.0*inch, 1.5*inch, 1.2*inch, 1.3*inch])
            tabela_dep.setStyle(table_style)
            story.append(tabela_dep)
        else:
            story.append(Paragraph("Nenhum dependente cadastrado.", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Reajustes Salariais
        
        if reajustes:
            dados_reaj = [[Paragraph("Data", style_small_bold), Paragraph("% Salário", style_small_bold), Paragraph("% Bônus", style_small_bold)]]
            for reaj in reajustes:
                dados_reaj.append([
                    Paragraph(reaj.data_alteracao.strftime('%d/%m/%Y'), style_small),
                    Paragraph(f"{reaj.percentual_reajuste_salario:,.2f}%".replace(",", "X").replace(".", ",").replace("X", "."), style_small),
                    Paragraph(f"{reaj.percentual_reajuste_bonus:,.2f}%".replace(",", "X").replace(".", ",").replace("X", "."), style_small),
                ])
            tabela_reaj = Table([[Paragraph('REAJUSTES SALARIAIS', style_heading2), '', '']] + dados_reaj, colWidths=[1.8*inch, 1.6*inch, 2.6*inch])
            tabela_reaj.setStyle(table_style)
            story.append(tabela_reaj)
        else:
            story.append(Paragraph("Nenhum reajuste salarial cadastrado.", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Observações (do funcionário)
        story.append(Paragraph("OBSERVATIONS", style_heading2)) # Changed "Observações" to "OBSERVATIONS"
        story.append(Paragraph(f"{funcionario.observacao or 'N/A'}", style_normal))
        story.append(Spacer(1, 0.2 * inch))


        # Gerar o PDF
        doc.build(story)
        
        # Reset buffer's position to the beginning
        buffer.seek(0)

        return Response(buffer.getvalue(), mimetype='application/pdf',
                        headers={'Content-Disposition': f'attachment;filename=ficha_cadastral_{funcionario.cpf}.pdf'})

# Rota para listar registros de ponto
    @app.route('/registros_ponto')
    def listar_registros_ponto():
        if 'usuario_id' not in session:
            flash('Você precisa estar logado para acessar esta página.', 'warning')
            return redirect(url_for('login'))

        start_str = request.args.get('start_date')
        end_str = request.args.get('end_date')
        try:
            if start_str and end_str:
                start_date = datetime.datetime.strptime(start_str, '%Y-%m-%d')
                end_date = datetime.datetime.strptime(end_str, '%Y-%m-%d')
            else:
                today = datetime.date.today()
                start_date = today.replace(day=1)
                next_month = (start_date + datetime.timedelta(days=32)).replace(day=1)
                end_date = next_month - datetime.timedelta(days=1)
        except ValueError:
            flash('Formato de data inválido.', 'danger')
            today = datetime.date.today()
            start_date = today.replace(day=1)
            next_month = (start_date + datetime.timedelta(days=32)).replace(day=1)
            end_date = next_month - datetime.timedelta(days=1)

        query = db.session.query(RegistroPonto).outerjoin(Funcionario)
        query = query.filter(RegistroPonto.data_hora >= datetime.datetime.combine(start_date, datetime.time.min))
        query = query.filter(RegistroPonto.data_hora <= datetime.datetime.combine(end_date, datetime.time.max))
        registros = query.order_by(RegistroPonto.data_hora.desc()).all()

        return render_template(
            'registro_ponto.html',
            registros_ponto=registros,
            start_date=start_date.strftime('%Y-%m-%d'),
            end_date=end_date.strftime('%Y-%m-%d')
        )

    @app.route('/registros_ponto/importar', methods=['GET', 'POST'])
    def importar_registros_ponto():
        if 'usuario_id' not in session:
            flash('Você precisa fazer login primeiro.', 'warning')
            return redirect(url_for('login'))

        if request.method == 'POST':
            arquivo = request.files.get('arquivo_afd')
            if not arquivo or arquivo.filename == '':
                flash('Nenhum arquivo selecionado.', 'danger')
                return redirect(request.url)

            linhas = arquivo.read().decode('latin-1').splitlines()
            inseridos = 0
            
            for linha in linhas:
                linha = linha.strip()
                if len(linha) < 34:
                    continue

                tipo = linha[9]
                if tipo not in ('3', '7'):
                    continue

                # AFD: DDMMYYYYHHMM a partir da posição 10
                data_str = linha[10:22]
                pis_campo = linha[22:34].strip()
                # Alguns relógios registram o CPF no campo PIS com zeros
                # à esquerda. Removemos esses zeros para comparar com o CPF
                # salvo no banco, mas mantemos o valor original para salvar
                # no registro de ponto.
                cpf_possivel = pis_campo.lstrip('0')
                # Alguns relógios inserem um zero extra no início do CPF para
                # preencher 12 dígitos no campo PIS. Para garantir a busca,
                # também consideramos os 11 últimos dígitos (CPF completo)
                cpf_completo = pis_campo[-11:]

                try:
                    data_hora = datetime.datetime.strptime(data_str, '%d%m%Y%H%M')
                except ValueError:
                    continue

                # Busca funcionário utilizando o PIS informado no arquivo.
                # Caso não encontre, tenta localizar usando o CPF, pois muitos
                # registros antigos usam o CPF no campo destinado ao PIS.
                funcionario = Funcionario.query.filter(
                    or_(
                        Funcionario.pis == pis_campo,
                        Funcionario.cpf == cpf_possivel,
                        Funcionario.cpf == cpf_completo,
                    )
                ).first()
                if not funcionario:
                    # pulamos registros sem funcionário correspondente
                    continue

                if RegistroPonto.query.filter_by(cpf_funcionario=funcionario.cpf, data_hora=data_hora).first():
                    continue
                
                novo = RegistroPonto(
                    cpf_funcionario=funcionario.cpf,
                    pis=pis_campo,
                    id_face=funcionario.id_face,
                    data_hora=data_hora,
                    tipo_lancamento='Importação PIS'
                )
                db.session.add(novo)
                inseridos += 1

            db.session.commit()
            flash(f'{inseridos} registros importados com sucesso!', 'success')
            return redirect(url_for('listar_registros_ponto'))

        return render_template('importar_registro_ponto.html')
    
    # Rota para registro de ponto:
    @app.route('/registro_ponto', methods=['GET', 'POST'])
    def registro_ponto():
        # Validação de login
        if 'usuario_id' not in session:
            flash('Você precisa fazer login primeiro.', 'warning')
            return redirect(url_for('login'))

        if request.method == 'POST':
            cpf = request.form.get('cpf_funcionario', '').replace('.', '').replace('-', '')
            pis = request.form.get('pis')
            id_face = request.form.get('id_face')
            data_hora_str = request.form.get('data_hora')
            tipo_lancamento = request.form.get('tipo_lancamento')
            observacao = request.form.get('observacao')

            try:
                data_hora = datetime.datetime.strptime(data_hora_str, '%Y-%m-%dT%H:%M') if data_hora_str else datetime.datetime.now()
            except ValueError:
                flash('Data e hora inválidas.', 'danger')
                return render_template('registro_ponto_form.html', registro_ponto=None)

            novo_ponto = RegistroPonto(
                cpf_funcionario=cpf,
                pis=pis,
                id_face=id_face,
                data_hora=data_hora,
                tipo_lancamento=tipo_lancamento,
                observacao=observacao
            )
            db.session.add(novo_ponto)
            db.session.commit()

            flash('Ponto registrado com sucesso!', 'success')
            return redirect(url_for('listar_registros_ponto'))

        # GET: exibe o formulário
        return render_template('registro_ponto_form.html', registro_ponto=None)

    @app.route('/registros_ponto/delete/<int:id>', methods=['POST'])
    def deletar_registro_ponto(id):
        if 'usuario_id' not in session or session.get('tipo_usuario') not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar registros de ponto.', 'danger')
            return redirect(url_for('login'))

        registro = RegistroPonto.query.get_or_404(id)
        dados_antigos = {
            'id': registro.id,
            'cpf_funcionario': registro.cpf_funcionario,
            'pis': registro.pis,
            'id_face': registro.id_face,
            'data_hora': registro.data_hora.strftime('%Y-%m-%d %H:%M:%S'),
            'tipo_lancamento': registro.tipo_lancamento,
            'observacao': registro.observacao,
        }

        db.session.delete(registro)
        try:
            db.session.commit()
            flash('Registro de ponto deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Registro de ponto ID {id} deletado.",
                tabela_afetada='registro_ponto',
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()

        except Exception as e:
            db.session.rollback()
            print(f"ERROR: Erro ao deletar registro de ponto: {e}")
            flash(f'Erro ao deletar registro de ponto: {e}', 'danger')

        return redirect(url_for('listar_registros_ponto'))

    @app.route('/registros_ponto/edit/<int:id>', methods=['GET', 'POST'])
    def editar_registro_ponto(id):
        if 'usuario_id' not in session:
            flash('Você precisa fazer login primeiro.', 'warning')
            return redirect(url_for('login'))

        registro = RegistroPonto.query.get_or_404(id)

        if request.method == 'POST':
            cpf = request.form.get('cpf_funcionario', '').replace('.', '').replace('-', '')
            pis = request.form.get('pis')
            id_face = request.form.get('id_face')
            data_hora_str = request.form.get('data_hora')
            tipo_lancamento = request.form.get('tipo_lancamento')
            observacao = request.form.get('observacao')

            try:
                data_hora = datetime.datetime.strptime(data_hora_str, '%Y-%m-%dT%H:%M')
            except ValueError:
                flash('Data e hora inválidas.', 'danger')
                return render_template('registro_ponto_form.html', registro_ponto=registro)

            dados_antigos = {
                'cpf_funcionario': registro.cpf_funcionario,
                'pis': registro.pis,
                'id_face': registro.id_face,
                'data_hora': registro.data_hora.strftime('%Y-%m-%dT%H:%M'),
                'tipo_lancamento': registro.tipo_lancamento,
                'observacao': registro.observacao,
            }

            registro.cpf_funcionario = cpf
            registro.pis = pis
            registro.id_face = id_face
            registro.data_hora = data_hora
            registro.tipo_lancamento = tipo_lancamento
            registro.observacao = observacao

            try:
                db.session.commit()
                dados_novos = {
                    'cpf_funcionario': registro.cpf_funcionario,
                    'pis': registro.pis,
                    'id_face': registro.id_face,
                    'data_hora': registro.data_hora.strftime('%Y-%m-%dT%H:%M'),
                    'tipo_lancamento': registro.tipo_lancamento,
                    'observacao': registro.observacao,
                }
                log_entry = LogAuditoria(
                    usuario_id=session['usuario_id'],
                    acao=f"Registro de ponto ID {id} editado.",
                    tabela_afetada='registro_ponto',
                    registro_id=id,
                    dados_antigos=dados_antigos,
                    dados_novos=dados_novos
                )
                db.session.add(log_entry)
                db.session.commit()
                flash('Registro de ponto atualizado com sucesso!', 'success')
            except Exception as e:
                db.session.rollback()
                print(f"ERROR: Erro ao atualizar registro de ponto: {e}")
                flash(f'Erro ao atualizar registro de ponto: {e}', 'danger')
                return render_template('registro_ponto_form.html', registro_ponto=registro)

            return redirect(url_for('listar_registros_ponto'))

        return render_template('registro_ponto_form.html', registro_ponto=registro)

    # --- Módulo: Cadastro de Usuários ---
    @app.route('/usuarios')
    def listar_usuarios():
        if 'usuario_id' not in session or session['tipo_usuario'] != 'Master':
            flash('Acesso negado. Apenas usuários Master podem gerenciar usuários.', 'danger')
            return redirect(url_for('login'))
        usuarios = Usuario.query.order_by(Usuario.nome).all()
        return render_template('usuarios.html', usuarios=usuarios)

    @app.route('/usuarios/add', methods=['GET', 'POST'])
    def adicionar_usuario():
        if 'usuario_id' not in session or session['tipo_usuario'] != 'Master':
            flash('Acesso negado. Apenas usuários Master podem adicionar usuários.', 'danger')
            return redirect(url_for('login'))

        if request.method == 'POST':
            nome_completo = request.form['nome_completo']
            nome = request.form['nome']
            senha = request.form['senha']
            tipo_usuario = request.form['tipo_usuario']

            if Usuario.query.filter_by(nome=nome).first():
                flash('Usuário já existe.', 'danger')
                return render_template('usuario_form.html', usuario=None)

            novo_usuario = Usuario(nome=nome, nome_completo=nome_completo, tipo_usuario=tipo_usuario)
            novo_usuario.set_password(senha)
            db.session.add(novo_usuario)
            db.session.commit()
            flash('Usuário adicionado com sucesso!', 'success')
            return redirect(url_for('listar_usuarios'))

        return render_template('usuario_form.html', usuario=None)

    @app.route('/usuarios/edit/<int:id>', methods=['GET', 'POST'])
    def editar_usuario(id):
        if 'usuario_id' not in session or session['tipo_usuario'] != 'Master':
            flash('Acesso negado. Apenas usuários Master podem editar usuários.', 'danger')
            return redirect(url_for('login'))

        usuario = Usuario.query.get_or_404(id)

        if request.method == 'POST':
            usuario.nome_completo = request.form['nome_completo']
            usuario.nome = request.form['nome']
            senha = request.form.get('senha')
            usuario.tipo_usuario = request.form['tipo_usuario']
            if senha:
                usuario.set_password(senha)
            db.session.commit()
            flash('Usuário atualizado com sucesso!', 'success')
            return redirect(url_for('listar_usuarios'))

        return render_template('usuario_form.html', usuario=usuario)

    @app.route('/usuarios/delete/<int:id>', methods=['POST'])
    def deletar_usuario(id):
        if 'usuario_id' not in session or session['tipo_usuario'] != 'Master':
            flash('Acesso negado. Apenas usuários Master podem deletar usuários.', 'danger')
            return redirect(url_for('login'))

        usuario = Usuario.query.get_or_404(id)
        db.session.delete(usuario)
        db.session.commit()
        flash('Usuário deletado com sucesso!', 'success')
        return redirect(url_for('listar_usuarios'))

    # --- Módulo: Entidades de Saúde Ocupacional ---
    @app.route('/entidades_saude')
    def listar_entidades_saude():
        entidades = EntidadeSaudeOcupacional.query.order_by(EntidadeSaudeOcupacional.nome).all()
        return render_template('entidades_saude.html', entidades=entidades)

    @app.route('/entidades_saude/add', methods=['GET', 'POST'])
    def adicionar_entidade_saude():
        if request.method == 'POST':
            entidade = EntidadeSaudeOcupacional(
                nome=request.form['nome'],
                crm_cnpj=request.form.get('crm_cnpj'),
                telefone=request.form.get('telefone'),
                email=request.form.get('email')
            )
            db.session.add(entidade)
            try:
                db.session.commit()
                flash('Entidade de saúde ocupacional adicionada com sucesso!', 'success')
                return redirect(url_for('listar_entidades_saude'))
            except IntegrityError:
                db.session.rollback()
                flash('E-mail já cadastrado. Escolha outro.', 'danger')
                return render_template('entidade_saude_form.html', entidade=entidade)
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao adicionar entidade: {e}', 'danger')
                return render_template('entidade_saude_form.html', entidade=entidade)
        return render_template('entidade_saude_form.html', entidade=None)

    @app.route('/entidades_saude/edit/<int:id>', methods=['GET', 'POST'])
    def editar_entidade_saude(id):
        entidade = EntidadeSaudeOcupacional.query.get_or_404(id)
        if request.method == 'POST':
            entidade.nome = request.form['nome']
            entidade.crm_cnpj = request.form.get('crm_cnpj')
            entidade.telefone = request.form.get('telefone')
            entidade.email = request.form.get('email')
            db.session.commit()
            return redirect(url_for('listar_entidades_saude'))
        return render_template('entidade_saude_form.html', entidade=entidade)

    @app.route('/entidades_saude/delete/<int:id>', methods=['POST'])
    def deletar_entidade_saude(id):
        if 'usuario_id' not in session or session.get('tipo_usuario') not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar entidades de saúde.', 'danger')
            return redirect(url_for('login'))

        entidade = EntidadeSaudeOcupacional.query.get_or_404(id)

        dados_antigos = {
            'id': entidade.id,
            'nome': entidade.nome,
            'crm_cnpj': entidade.crm_cnpj,
            'telefone': entidade.telefone,
            'email': entidade.email
        }

        db.session.delete(entidade)
        try:
            db.session.commit()
            flash('Entidade de saúde ocupacional deletada com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Entidade de saúde ocupacional '{entidade.nome}' ID {id} deletada.",
                tabela_afetada='entidades_saude_ocupacional',
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar entidade de saúde ocupacional: {e}")
            flash(f'Erro ao deletar entidade: {e}', 'danger')

        return redirect(url_for('listar_entidades_saude'))

    # --- Módulo: Tipos de Exames ---
    @app.route('/tipos_exames')
    def listar_tipos_exames():
        tipos = TipoExame.query.order_by(TipoExame.nome).all()
        return render_template('tipos_exames.html', tipos=tipos)

    @app.route('/tipos_exames/add', methods=['GET', 'POST'])
    def adicionar_tipo_exame():
        if request.method == 'POST':
            nome = request.form['nome'].strip()
            if TipoExame.query.filter_by(nome=nome).first():
                flash(f'O tipo de exame "{nome}" já existe.', 'danger')
                return render_template('tipo_exame_form.html', tipo_exame=None)

            tipo = TipoExame(
                nome=nome,
                periodicidade_dias=int(request.form['periodicidade_dias']),
                observacoes=request.form.get('observacoes')
            )
            db.session.add(tipo)
            try:
                db.session.commit()
                flash('Tipo de exame adicionado com sucesso!', 'success')
                return redirect(url_for('listar_tipos_exames'))
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao adicionar tipo de exame: {e}', 'danger')
                return render_template('tipo_exame_form.html', tipo_exame=None)
        return render_template('tipo_exame_form.html', tipo_exame=None)

    @app.route('/tipos_exames/edit/<int:id>', methods=['GET', 'POST'])
    def editar_tipo_exame(id):
        tipo = TipoExame.query.get_or_404(id)
        if request.method == 'POST':
            novo_nome = request.form['nome'].strip()
            if TipoExame.query.filter(TipoExame.nome == novo_nome, TipoExame.id != id).first():
                flash(f'O tipo de exame "{novo_nome}" já existe.', 'danger')
                return render_template('tipo_exame_form.html', tipo_exame=tipo)

            tipo.nome = novo_nome
            tipo.periodicidade_dias = int(request.form['periodicidade_dias'])
            tipo.observacoes = request.form.get('observacoes')
            try:
                db.session.commit()
                flash('Tipo de exame atualizado com sucesso!', 'success')
                return redirect(url_for('listar_tipos_exames'))
            except Exception as e:
                db.session.rollback()
                flash(f'Erro ao atualizar tipo de exame: {e}', 'danger')
                return render_template('tipo_exame_form.html', tipo_exame=tipo)
        return render_template('tipo_exame_form.html', tipo_exame=tipo)

    @app.route('/tipos_exames/delete/<int:id>', methods=['POST'])
    def deletar_tipo_exame(id):
        if 'usuario_id' not in session or session.get('tipo_usuario') not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar tipos de exames.', 'danger')
            return redirect(url_for('login'))

        tipo = TipoExame.query.get_or_404(id)

        if ExameFuncionario.query.filter_by(tipo_exame_id=id).first() or ExameFuncao.query.filter_by(tipo_exame_id=id).first():
            flash('Não foi possível deletar o tipo de exame. Existem registros associados a ele.', 'danger')
            return redirect(url_for('listar_tipos_exames'))

        dados_antigos = {
            'id': tipo.id,
            'nome': tipo.nome,
            'periodicidade_dias': tipo.periodicidade_dias,
            'observacoes': tipo.observacoes
        }

        db.session.delete(tipo)
        try:
            db.session.commit()
            flash('Tipo de exame deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Tipo de Exame '{tipo.nome}' ID {id} deletado.",
                tabela_afetada='tipos_exames',
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar tipo de exame: {e}")
            flash(f'Erro ao deletar tipo de exame: {e}', 'danger')

        return redirect(url_for('listar_tipos_exames'))

    # --- Módulo: Exames de Funcionários ---
    @app.route('/exames_funcionarios')
    def listar_exames_funcionarios():
        exames = ExameFuncionario.query.join(Funcionario).join(TipoExame).all()
        return render_template('exames_funcionarios.html', exames=exames)

    @app.route('/exames_funcionarios/add', methods=['GET', 'POST'])
    def adicionar_exame_funcionario():
        tipos = TipoExame.query.order_by(TipoExame.nome).all()
        entidades = EntidadeSaudeOcupacional.query.order_by(EntidadeSaudeOcupacional.nome).all()
        if request.method == 'POST':
            cpf = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            tipo_id = int(request.form['tipo_exame_id'])
            data_realizacao = datetime.datetime.strptime(request.form['data_realizacao'], '%Y-%m-%d').date()
            tipo = TipoExame.query.get(tipo_id)
            data_vencimento = data_realizacao + datetime.timedelta(days=tipo.periodicidade_dias)
            ent = request.form.get('entidade_id')
            entidade_id = int(ent) if ent else None
            observacoes = request.form.get('observacoes')
            exame = ExameFuncionario(
                cpf_funcionario=cpf,
                tipo_exame_id=tipo_id,
                data_realizacao=data_realizacao,
                data_vencimento=data_vencimento,
                entidade_id=entidade_id,
                observacoes=observacoes
            )
            db.session.add(exame)
            db.session.commit()
            return redirect(url_for('listar_exames_funcionarios'))
        return render_template('exame_funcionario_form.html', exame=None, tipos=tipos, entidades=entidades)

    @app.route('/exames_funcionarios/edit/<int:id>', methods=['GET', 'POST'])
    def editar_exame_funcionario(id):
        exame = ExameFuncionario.query.get_or_404(id)
        tipos = TipoExame.query.order_by(TipoExame.nome).all()
        entidades = EntidadeSaudeOcupacional.query.order_by(EntidadeSaudeOcupacional.nome).all()
        if request.method == 'POST':
            exame.cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            exame.tipo_exame_id = int(request.form['tipo_exame_id'])
            exame.data_realizacao = datetime.datetime.strptime(request.form['data_realizacao'], '%Y-%m-%d').date()
            tipo = TipoExame.query.get(exame.tipo_exame_id)
            exame.data_vencimento = exame.data_realizacao + datetime.timedelta(days=tipo.periodicidade_dias)
            ent = request.form.get('entidade_id')
            exame.entidade_id = int(ent) if ent else None
            exame.observacoes = request.form.get('observacoes')
            db.session.commit()
            return redirect(url_for('listar_exames_funcionarios'))
        return render_template('exame_funcionario_form.html', exame=exame, tipos=tipos, entidades=entidades)

    @app.route('/exames_funcionarios/delete/<int:id>', methods=['POST'])
    def deletar_exame_funcionario(id):
        if 'usuario_id' not in session or session.get('tipo_usuario') not in ['Master']:
            flash('Acesso negado. Apenas usuários Master podem deletar exames.', 'danger')
            return redirect(url_for('login'))

        exame = ExameFuncionario.query.get_or_404(id)
        dados_antigos = {
            'id': exame.id,
            'cpf_funcionario': exame.cpf_funcionario,
            'tipo_exame_id': exame.tipo_exame_id,
            'data_realizacao': exame.data_realizacao.strftime('%Y-%m-%d'),
            'data_vencimento': exame.data_vencimento.strftime('%Y-%m-%d'),
            'entidade_id': exame.entidade_id,
            'observacoes': exame.observacoes
        }

        db.session.delete(exame)
        try:
            db.session.commit()
            flash('Exame do funcionário deletado com sucesso!', 'success')

            log_entry = LogAuditoria(
                usuario_id=session['usuario_id'],
                acao=f"Exame de funcionário ID {id} deletado.",
                tabela_afetada='exames_funcionarios',
                registro_id=id,
                dados_antigos=dados_antigos,
                dados_novos=None
            )
            db.session.add(log_entry)
            db.session.commit()
        except Exception as e:
            db.session.rollback()
            print(f"Erro ao deletar exame do funcionário: {e}")
            flash(f'Erro ao deletar exame do funcionário: {e}', 'danger')

        return redirect(url_for('listar_exames_funcionarios'))

    # --- Menu Exames ---
    @app.route('/exames')
    def exames_menu():
        """Página inicial do módulo de Exames"""
        return render_template('exames_menu.html')

    # --- Menu Fardas e EPIs ---
    @app.route('/fardas_epi')
    def fardas_epi_menu():
        """Página inicial do módulo de Fardas e EPIs"""
        return render_template('fardas_epi_menu.html')

    # --- Módulo: Itens de Fardas e EPIs ---
    @app.route('/itens_epi')
    def listar_itens_epi():
        itens = ItemEPI.query.order_by(ItemEPI.descricao).all()
        return render_template('itens_epi.html', itens=itens)

    @app.route('/itens_epi/add', methods=['GET', 'POST'])
    def adicionar_item_epi():
        if request.method == 'POST':
            item = ItemEPI(
                tipo_item=request.form['tipo_item'],
                descricao=request.form['descricao'],
                codigo=request.form['codigo'],
                funcoes_permitidas=request.form.get('funcoes_permitidas'),
                periodicidade_dias=request.form.get('periodicidade_dias') or None,
                fornecedor=request.form.get('fornecedor'),
                observacoes=request.form.get('observacoes'),
            )
            db.session.add(item)
            db.session.commit()
            return redirect(url_for('listar_itens_epi'))
        return render_template('item_epi_form.html', item=None)

    @app.route('/itens_epi/edit/<int:id>', methods=['GET', 'POST'])
    def editar_item_epi(id):
        item = ItemEPI.query.get_or_404(id)
        if request.method == 'POST':
            item.tipo_item = request.form['tipo_item']
            item.descricao = request.form['descricao']
            item.codigo = request.form['codigo']
            item.funcoes_permitidas = request.form.get('funcoes_permitidas')
            item.periodicidade_dias = request.form.get('periodicidade_dias') or None
            item.fornecedor = request.form.get('fornecedor')
            item.observacoes = request.form.get('observacoes')
            db.session.commit()
            return redirect(url_for('listar_itens_epi'))
        return render_template('item_epi_form.html', item=item)

    # --- Rotina de Distribuição ---
    @app.route('/distribuicoes_itens')
    def listar_distribuicoes_itens():
        distribs = DistribuicaoItem.query.join(ItemEPI).all()
        return render_template('distribuicoes_itens.html', distribs=distribs)

    @app.route('/distribuicoes_itens/add', methods=['GET', 'POST'])
    def adicionar_distribuicao_item():
        itens = ItemEPI.query.order_by(ItemEPI.descricao).all()
        if request.method == 'POST':
            dist = DistribuicaoItem(
                item_id=int(request.form['item_id']),
                cpf_funcionario=request.form['cpf_funcionario'],
                quantidade=int(request.form.get('quantidade', 1)),
                certificado_aprovacao=request.form.get('certificado_aprovacao'),
                responsavel=request.form.get('responsavel')
            )
            db.session.add(dist)
            db.session.commit()
            return redirect(url_for('listar_distribuicoes_itens'))
        return render_template('distribuicao_item_form.html', itens=itens)

    @app.route('/distribuicoes_itens/devolver/<int:distrib_id>', methods=['GET', 'POST'])
    def registrar_devolucao_item(distrib_id):
        distrib = DistribuicaoItem.query.get_or_404(distrib_id)
        if request.method == 'POST':
            dev = DevolucaoItem(
                distribuicao_id=distrib_id,
                motivo=request.form.get('motivo'),
                estado_item=request.form.get('estado_item'),
                observacoes=request.form.get('observacoes'),
            )
            db.session.add(dev)
            db.session.commit()
            return redirect(url_for('listar_distribuicoes_itens'))
        return render_template('devolucao_item_form.html', distrib=distrib)

    @app.route('/itens_epi/alertas')
    def alertas_itens_epi():
        hoje = datetime.date.today()
        limite = hoje + datetime.timedelta(days=30)
        distribs = DistribuicaoItem.query.join(ItemEPI).all()
        vencendo = [d for d in distribs if d.data_vencimento and hoje <= d.data_vencimento <= limite]
        return render_template('alertas_epi.html', itens_vencendo=vencendo)

    return app # Fim da função create_app


# --- Funções de Inicialização do Banco de Dados ---
def initialize_database(app_instance):
    with app_instance.app_context():
        db.create_all()
        # Garantir que a coluna "status" exista em funcionarios
        engine = db.engine
        inspector = inspect(engine)
        funcionario_cols = [c['name'] for c in inspector.get_columns('funcionarios')]
        if 'status' not in funcionario_cols:
            try:
                with engine.connect() as conn:
                    conn.execute(text('ALTER TABLE funcionarios ADD COLUMN status VARCHAR(20)'))
                    conn.commit()
                print("Coluna 'status' adicionada à tabela funcionarios.")
            except Exception as e:
                print(f"Erro ao adicionar coluna status: {e}")

        usuario_cols = [c['name'] for c in inspector.get_columns('usuarios')]
        if 'nome_completo' not in usuario_cols:
            try:
                with engine.connect() as conn:
                    conn.execute(text('ALTER TABLE usuarios ADD COLUMN nome_completo VARCHAR(255)'))
                    conn.commit()
                print("Coluna 'nome_completo' adicionada à tabela usuarios.")
            except Exception as e:
                print(f"Erro ao adicionar coluna nome_completo: {e}")

        contrato_cols = [c['name'] for c in inspector.get_columns('contratos_trabalho')]
        if 'jornada_id' not in contrato_cols:
            try:
                with engine.connect() as conn:
                    conn.execute(text('ALTER TABLE contratos_trabalho ADD COLUMN jornada_id INTEGER REFERENCES jornadas(id)'))
                    conn.commit()
                print("Coluna 'jornada_id' adicionada à tabela contratos_trabalho.")
            except Exception as e:
                print(f"Erro ao adicionar coluna jornada_id: {e}")
        from models import (
            Usuario,
            LogAuditoria,
            Cidade,
            Funcionario,
            ContratoTrabalho,
            Setor,
            Funcao,
            ReajusteSalarial,
            Demissao,
            ControleFerias,
            EntidadeSaudeOcupacional,
            TipoExame,
            ExameFuncao,
            ExameFuncionario,
            ItemEPI,
        )
        try:
            master_user_exists = db.session.query(Usuario).filter_by(nome='master').first()
            if not master_user_exists:
                master_user = Usuario(nome='master', nome_completo='Administrador', tipo_usuario='Master')
                master_user.set_password('master123')
                db.session.add(master_user)
                db.session.commit()
                print("Usuário 'master' criado com sucesso!")
            
            if Cidade.query.count() == 0:
                print("Populando cidades de exemplo...")
                cidades_exemplo = [
                    Cidade(cidibge="2927408", nome_cidade="Salvador", estado_uf="BA", longitude=-38.5098, latitude=-12.9718, regiao="Nordeste", mesorregiao="Metropolitana de Salvador", microrregiao="Salvador"),
                    Cidade(cidibge="2304400", nome_cidade="Fortaleza", estado_uf="CE", longitude=-38.5247, latitude=-3.7319, regiao="Nordeste", mesorregiao="Metropolitana de Fortaleza", microrregiao="Fortaleza"),
                    Cidade(cidibge="3550308", nome_cidade="São Paulo", estado_uf="SP", longitude=-46.6333, latitude=-23.5505, regiao="Sudeste", mesorregiao="Metropolitana de São Paulo", microrregiao="São Paulo"),
                    Cidade(cidibge="3304557", nome_cidade="Rio de Janeiro", estado_uf="RJ", longitude=-43.1729, latitude=-22.9068, regiao="Sudeste", mesorregiao="Metropolitana do Rio de Janeiro", microrregiao="Rio de Janeiro"),
                    Cidade(cidibge="4106902", nome_cidade="Curitiba", estado_uf="PR", longitude=-49.2719, latitude=-25.4284, regiao="Sul", mesorregiao="Metropolitana de Curitiba", microrregiao="Curitiba")
                ]
                db.session.bulk_save_objects(cidades_exemplo)
                db.session.commit()
                print("Cidades de exemplo populadas com sucesso!")

            if Funcionario.query.count() == 0:
                print("Adicionando funcionário de exemplo para testes de contrato...")
                funcionario_exemplo = Funcionario(
                    cpf="11122233344", nome="Funcionario Teste", data_nascimento=datetime.date(1980, 1, 1),
                    pis="123.45678.90-1", id_face="TESTFACE123", endereco="Rua Exemplo, 123",
                    cidade="São Paulo", estado="SP", cep="01000-000", telefone="11987654321",
                    codigo_banco="001", nome_banco="Banco do Brasil", codigo_agencia="1234",
                    numero_conta="56789-0", variacao_conta="01", chave_pix="teste@pix.com"
                )
                db.session.add(funcionario_exemplo)
                db.session.commit()
                print("Funcionário de exemplo adicionado.")

            if Setor.query.count() == 0:
                print("Populando setores de exemplo...")
                setores_exemplo = [
                    Setor(nome="Produção"),
                    Setor(nome="Administrativo"),
                    Setor(nome="Vendas"),
                    Setor(nome="Logística"),
                    Setor(nome="RH")
                ]
                db.session.bulk_save_objects(setores_exemplo)
                db.session.commit()
                print("Setores de exemplo populados com sucesso!")

            if Funcao.query.count() == 0:
                print("Populando funções de exemplo...")
                funcoes_exemplo = [
                    Funcao(nome="Operador de Máquina"),
                    Funcao(nome="Montador"),
                    Funcao(nome="Analista de RH"),
                    Funcao(nome="Vendedor"),
                    Funcao(nome="Gerente de Produção"),
                    Funcao(nome="Auxiliar Administrativo")
                ]
                db.session.bulk_save_objects(funcoes_exemplo)
                db.session.commit()
                print("Funções de exemplo populadas com sucesso!")

            if ReajusteSalarial.query.count() == 0:
                print("Populando reajustes salariais de exemplo...")
                funcionario_existente = Funcionario.query.first() 
                if funcionario_existente:
                    reajustes_exemplo = [
                        ReajusteSalarial(
                            cpf_funcionario=funcionario_existente.cpf,
                            data_alteracao=datetime.date(2023, 1, 1),
                            percentual_reajuste_salario=5.00,
                            percentual_reajuste_bonus=0.00
                        ),
                        ReajusteSalarial(
                            cpf_funcionario=funcionario_existente.cpf,
                            data_alteracao=datetime.date(2024, 1, 1),
                            percentual_reajuste_salario=7.50,
                            percentual_reajuste_bonus=2.00
                        )
                    ]
                    db.session.bulk_save_objects(reajustes_exemplo)
                    db.session.commit()
                    print("Reajustes salariais de exemplo populados com sucesso!")
                else:
                    print("Nenhum funcionário encontrado para popular reajustes de exemplo.")

            if Demissao.query.count() == 0:
                print("Populando demissões de exemplo...")
                funcionario_existente = Funcionario.query.first()
                if funcionario_existente:
                    demissoes_exemplo = [
                        Demissao(
                            cpf_funcionario=funcionario_existente.cpf,
                            data_demissao=datetime.date(2024, 5, 15),
                            ultimo_dia_trabalhado=datetime.date(2024, 5, 14),
                            tipo_desligamento="Pedido de demissão",
                            motivo_demissao="Busca por novas oportunidades.",
                            aviso_previo="Cumprido",
                            data_aviso_previo=datetime.date(2024, 4, 15),
                            quantidade_dias_aviso=30,
                            data_termino_aviso=datetime.date(2024, 5, 14)
                        ),
                        Demissao(
                            cpf_funcionario=funcionario_existente.cpf, # Reutilizando para exemplo
                            data_demissao=datetime.date(2023, 10, 20),
                            ultimo_dia_trabalhado=datetime.date(2023, 10, 20),
                            tipo_desligamento="Dispensa sem justa causa",
                            motivo_demissao="Reestruturação da equipe.",
                            aviso_previo="Indenizado",
                            data_aviso_previo=None,
                            quantidade_dias_aviso=None,
                            data_termino_aviso=None
                        )
                    ]
                    db.session.bulk_save_objects(demissoes_exemplo)
                    db.session.commit()
                    print("Demissões de exemplo populadas com sucesso!")
                else:
                    print("Nenhum funcionário encontrado para popular demissões de exemplo.")

            if ControleFerias.query.count() == 0:
                print("Populando registros de férias de exemplo...")
                funcionario_existente = Funcionario.query.first()
                if funcionario_existente:
                    ferias_exemplo = [
                        ControleFerias(
                            cpf_funcionario=funcionario_existente.cpf,
                            periodo_aquisitivo_inicio=datetime.date(2023, 1, 1),
                            periodo_aquisitivo_fim=datetime.date(2023, 12, 31),
                            ferias_gozadas_inicio=datetime.date(2024, 3, 1),
                            ferias_gozadas_fim=datetime.date(2024, 3, 30)
                        ),
                        ControleFerias(
                            cpf_funcionario=funcionario_existente.cpf,
                            periodo_aquisitivo_inicio=datetime.date(2024, 1, 1),
                            periodo_aquisitivo_fim=datetime.date(2024, 12, 31),
                            ferias_gozadas_inicio=None,
                            ferias_gozadas_fim=None
                        )
                    ]
                    db.session.bulk_save_objects(ferias_exemplo)
                    db.session.commit()
                    print("Registros de férias de exemplo populados com sucesso!")
                else:
                    print("Nenhum funcionário encontrado para popular férias de exemplo.")
            
            if ItemEPI.query.count() == 0:
                print("Populando itens de EPI de exemplo...")
                itens_exemplo = [
                    ItemEPI(tipo_item='EPI', descricao='Capacete de Segurança', codigo='EPI001', periodicidade_dias=365),
                    ItemEPI(tipo_item='Farda', descricao='Camisa Padrão', codigo='FARDA001', periodicidade_dias=180)
                ]
                db.session.bulk_save_objects(itens_exemplo)
                db.session.commit()
                print("Itens de exemplo populados com sucesso!")


        except Exception as e:
            print(f"Erro ao inicializar o banco de dados ou criar usuário master/cidades/funcionário/setores/funções/reajustes/demissões/férias: {e}")
            print("Isso pode ocorrer se o banco de dados não estiver acessível ou já estiver populado.")
            print("O aplicativo tentará rodar mesmo assim.")



# ------------------------------------------------------------------
# Observação: não replique esta definição de 'index' aqui, pois já existe
# no seu app.py original. Caso precise editar o template inicial, atualize
# somente o arquivo 'index.html' e mantenha apenas UMA rota @app.route('/')
app = create_app()
# no seu projeto.

if __name__ == '__main__':
    app.run(debug=True)
