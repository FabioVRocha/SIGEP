from flask import Flask, render_template, session, redirect, url_for, request, flash, jsonify, Response
from sqlalchemy import or_
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
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image, PageBreak
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.colors import HexColor # Para usar as cores da paleta no PDF
import base64 # Importar base64 para decodificar a foto

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
    from models import Funcionario, Dependente, ContratoTrabalho, ReajusteSalarial, \
                       ControleFerias, FolhaPagamento, BancoHoras, HorasExtras, \
                       RegistroPonto, LogAuditoria, Usuario, Cidade, Setor, Funcao, Demissao

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
        return render_template('index.html', total_funcionarios_ativos=total_funcionarios_ativos)

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
            pis = request.form['pis']
            id_face = request.form['id_face']
            endereco = request.form['endereco']
            bairro = request.form.get('bairro')
            cidade = request.form['cidade'] # Valor da combobox
            estado = request.form['estado'] # Valor da combobox
            cep = request.form['cep']
            telefone = request.form['telefone']
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
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf)

            # Verifica se o CPF já existe
            if Funcionario.query.get(cpf):
                flash('CPF já cadastrado.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf)

            # Validação condicional para campos bancários/PIX no backend
            if not (chave_pix or (codigo_banco and nome_banco and codigo_agencia and numero_conta)):
                flash('Por favor, preencha a Chave PIX OU todos os campos bancários.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf)

            novo_funcionario = Funcionario(
                cpf=cpf, nome=nome, data_nascimento=data_nascimento, pis=pis,
                id_face=id_face, endereco=endereco, bairro=bairro, cidade=cidade, estado=estado, # NOVO CAMPO: BAIRRO
                cep=cep, telefone=telefone, codigo_banco=codigo_banco,
                nome_banco=nome_banco, codigo_agencia=codigo_agencia,
                numero_conta=numero_conta, variacao_conta=variacao_conta,
                chave_pix=chave_pix, observacao=observacao,
                foto_base64=foto_base64_str # Passa a string Base64, o CustomType cuidará da decodificação
            )
            db.session.add(novo_funcionario)
            try:
                db.session.commit()
                print("DEBUG (ADD): Commit bem-sucedido.")
            except Exception as e:
                db.session.rollback()
                print(f"ERROR (ADD): Falha no commit: {e}")
                flash('Erro ao adicionar funcionário. Tente novamente.', 'danger')
                return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf)

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
        
        return render_template('funcionario_form.html', funcionario=None, estados_uf=estados_uf)

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
            pis = request.form['pis']
            id_face = request.form['id_face']
            endereco = request.form['endereco']
            bairro = request.form.get('bairro') # NOVO CAMPO: BAIRRO
            cidade = request.form['cidade'] # Valor da combobox
            estado = request.form['estado'] # Valor da combobox
            cep = request.form['cep']
            telefone = request.form['telefone']
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
                return render_template('funcionario_form.html', funcionario=funcionario, estados_uf=estados_uf)

            # Guarda os dados antigos para o log de auditoria
            dados_antigos = {
                'nome': funcionario.nome, 'data_nascimento': str(funcionario.data_nascimento),
                'pis': funcionario.pis, 'id_face': funcionario.id_face,
                'endereco': funcionario.endereco, 'bairro': funcionario.bairro, # NOVO CAMPO: BAIRRO
                'cidade': funcionario.cidade, 'estado': funcionario.estado,
                'cep': funcionario.cep, 'telefone': funcionario.telefone,
                'codigo_banco': funcionario.codigo_banco, 'nome_banco': funcionario.nome_banco,
                'codigo_agencia': funcionario.codigo_agencia, 'numero_conta': funcionario.numero_conta,
                'variacao_conta': funcionario.variacao_conta, 'chave_pix': funcionario.chave_pix,
                'observacao': funcionario.observacao,
                'foto_base64': funcionario.foto_base64 # O CustomType já retornou a string Base64 para o objeto
            }

            funcionario.nome = nome
            funcionario.data_nascimento = data_nascimento
            funcionario.pis = pis
            funcionario.id_face = id_face
            funcionario.endereco = endereco
            funcionario.bairro = bairro # NOVO CAMPO: BAIRRO
            funcionario.cidade = cidade
            funcionario.estado = estado
            funcionario.cep = cep
            funcionario.telefone = telefone
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
                return render_template('funcionario_form.html', funcionario=funcionario, estados_uf=estados_uf)

            dados_novos = {
                'nome': funcionario.nome, 'data_nascimento': str(funcionario.data_nascimento),
                'pis': funcionario.pis, 'id_face': funcionario.id_face,
                'endereco': funcionario.endereco, 'bairro': funcionario.bairro, # NOVO CAMPO: BAIRRO
                'cidade': funcionario.cidade, 'estado': funcionario.estado,
                'cep': funcionario.cep, 'telefone': funcionario.telefone,
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

        return render_template('funcionario_form.html', funcionario=funcionario, estados_uf=estados_uf)

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
            'data_nascimento': str(funcionario.data_nascimento), 'pis': funcionario.pis,
            'id_face': funcionario.id_face, 'endereco': funcionario.endereco, 'bairro': funcionario.bairro, # NOVO CAMPO: BAIRRO
            'cidade': funcionario.cidade, 'estado': funcionario.estado,
            'cep': funcionario.cep, 'telefone': funcionario.telefone,
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
            }), 200 # Retorna 200 porque o funcionário foi encontrado, apenas sem contrato ativo

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

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            setor_nome = request.form['setor'] # Pega o nome do setor do formulário
            funcao_nome = request.form['funcao'] # Pega o nome da função do formulário
            salario_inicial = float(request.form['salario_inicial'])
            bonus = float(request.form['bonus']) if request.form['bonus'] else 0.00
            regime_contratacao = request.form['regime_contratacao']
            data_admissao = datetime.datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            data_demissao_str = request.form.get('data_demissao')
            data_demissao = datetime.datetime.strptime(data_demissao_str, '%Y-%m-%d').date() if data_demissao_str else None
            status = request.form['status'] == 'True'

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes)
            
            # Validação: Um funcionário pode ter apenas um contrato ATIVO
            if ContratoTrabalho.query.filter_by(cpf_funcionario=cpf_funcionario, status=True).first():
                flash('Este funcionário já possui um contrato ATIVO. Por favor, inative o contrato anterior antes de criar um novo.', 'danger')
                return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes)


            novo_contrato = ContratoTrabalho(
                cpf_funcionario=cpf_funcionario, setor=setor_nome, funcao=funcao_nome, # Usando o nome direto
                salario_inicial=salario_inicial, bonus=bonus,
                regime_contratacao=regime_contratacao, data_admissao=data_admissao,
                data_demissao=data_demissao, status=status
            )
            db.session.add(novo_contrato)
            try:
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
                return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes)
        
        return render_template('contrato_form.html', contrato=None, setores=setores, funcoes=funcoes)

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

        if request.method == 'POST':
            cpf_funcionario = request.form['cpf_funcionario'].replace('.', '').replace('-', '')
            setor_nome = request.form['setor'] # Pega o nome do setor do formulário
            funcao_nome = request.form['funcao'] # Pega o nome da função do formulário
            salario_inicial = float(request.form['salario_inicial'])
            bonus = float(request.form['bonus']) if request.form['bonus'] else 0.00
            regime_contratacao = request.form['regime_contratacao']
            data_admissao = datetime.datetime.strptime(request.form['data_admissao'], '%Y-%m-%d').date()
            data_demissao_str = request.form.get('data_demissao')
            data_demissao = datetime.datetime.strptime(data_demissao_str, '%Y-%m-%d').date() if data_demissao_str else None
            status = request.form['status'] == 'True'

            funcionario = Funcionario.query.get(cpf_funcionario)
            if not funcionario:
                flash('Funcionário com o CPF informado não encontrado.', 'danger')
                return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes)
            
            # Validação: Se está alterando o status para ATIVO, verifica se já existe outro contrato ATIVO
            if status and ContratoTrabalho.query.filter(ContratoTrabalho.cpf_funcionario == cpf_funcionario, ContratoTrabalho.status == True, ContratoTrabalho.id != contrato.id).first():
                flash('Este funcionário já possui outro contrato ATIVO. Por favor, inative o contrato anterior ou selecione "Inativo" para este contrato.', 'danger')
                return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes)


            # Guarda dados antigos para log
            dados_antigos = {
                'cpf_funcionario': contrato.cpf_funcionario, 'setor': contrato.setor,
                'funcao': contrato.funcao, 'salario_inicial': float(contrato.salario_inicial),
                'bonus': float(contrato.bonus), 'regime_contratacao': contrato.regime_contratacao,
                'data_admissao': str(contrato.data_admissao), 'data_demissao': str(contrato.data_demissao) if contrato.data_demissao else None,
                'status': contrato.status
            }

            contrato.cpf_funcionario = cpf_funcionario
            contrato.setor = setor_nome # Usando o nome
            contrato.funcao = funcao_nome # Usando o nome
            contrato.salario_inicial = salario_inicial
            contrato.bonus = bonus
            contrato.regime_contratacao = regime_contratacao
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
                    'status': contrato.status
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
                return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes)
        
        return render_template('contrato_form.html', contrato=contrato, setores=setores, funcoes=funcoes)

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
            'status': contrato.status
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

            # Criar o registro de demissão
            nova_demissao = Demissao(
                cpf_funcionario=cpf_funcionario,
                data_demissao=data_demissao,
                ultimo_dia_trabalhado=ultimo_dia_trabalhado,
                tipo_desligamento=tipo_desligamento,
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

            dados_antigos = {
                'cpf_funcionario': demissao.cpf_funcionario,
                'data_demissao': str(demissao.data_demissao),
                'ultimo_dia_trabalhado': str(demissao.ultimo_dia_trabalhado),
                'tipo_desligamento': demissao.tipo_desligamento,
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
        story.append(Paragraph("DADOS PESSOAIS", style_heading2))
        story.append(Paragraph(f"<b>Nome Completo:</b> {funcionario.nome}", style_normal))
        story.append(Paragraph(f"<b>CPF:</b> {funcionario.cpf}", style_normal))
        story.append(Paragraph(f"<b>PIS:</b> {funcionario.pis}", style_normal))
        story.append(Paragraph(f"<b>IDFace:</b> {funcionario.id_face}", style_normal))
        story.append(Paragraph(f"<b>Data de Nascimento:</b> {funcionario.data_nascimento.strftime('%d/%m/%Y')}", style_normal))
        story.append(Paragraph(f"<b>Telefone:</b> {funcionario.telefone}", style_normal))
        story.append(Paragraph(f"<b>Endereço:</b> {funcionario.endereco}", style_normal))
        story.append(Paragraph(f"<b>Bairro:</b> {funcionario.bairro or 'N/A'}", style_normal)) # Incluindo o Bairro no PDF
        story.append(Paragraph(f"<b>Cidade:</b> {funcionario.cidade}", style_normal))
        story.append(Paragraph(f"<b>Estado:</b> {funcionario.estado}", style_normal))
        story.append(Paragraph(f"<b>CEP:</b> {funcionario.cep}", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Dados Bancários
        story.append(Paragraph("DADOS BANCÁRIOS", style_heading2))
        if funcionario.chave_pix:
            story.append(Paragraph(f"<b>Chave PIX:</b> {funcionario.chave_pix}", style_normal))
        else:
            story.append(Paragraph(f"<b>Código Banco:</b> {funcionario.codigo_banco or 'N/A'}", style_normal))
            story.append(Paragraph(f"<b>Nome Banco:</b> {funcionario.nome_banco or 'N/A'}", style_normal))
            story.append(Paragraph(f"<b>Código Agência:</b> {funcionario.codigo_agencia or 'N/A'}", style_normal))
            story.append(Paragraph(f"<b>Número Conta:</b> {funcionario.numero_conta or 'N/A'}", style_normal))
            story.append(Paragraph(f"<b>Variação Conta:</b> {funcionario.variacao_conta or 'N/A'}", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Informações de Contrato (do último contrato, se houver)
        story.append(Paragraph("INFORMAÇÕES DE CONTRATO (Último Contrato)", style_heading2))
        if contrato:
            story.append(Paragraph(f"<b>Setor:</b> {contrato.setor}", style_normal))
            story.append(Paragraph(f"<b>Função:</b> {contrato.funcao}", style_normal))
            story.append(Paragraph(f"<b>Salário Inicial:</b> R$ {contrato.salario_inicial:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), style_normal))
            story.append(Paragraph(f"<b>Bônus:</b> R$ {contrato.bonus:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), style_normal))
            story.append(Paragraph(f"<b>Regime de Contratação:</b> {contrato.regime_contratacao}", style_normal))
            story.append(Paragraph(f"<b>Data de Admissão:</b> {contrato.data_admissao.strftime('%d/%m/%Y')}", style_normal))
            story.append(Paragraph(f"<b>Data de Demissão:</b> {contrato.data_demissao.strftime('%d/%m/%Y') if contrato.data_demissao else 'N/A'}", style_normal))
            story.append(Paragraph(f"<b>Status Contrato:</b> {'Ativo' if contrato.status else 'Inativo'}", style_normal))
        else:
            story.append(Paragraph("Nenhum contrato de trabalho encontrado.", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Dependentes
        story.append(Paragraph("DEPENDENTES", style_heading2))
        if dependentes:
            for dep in dependentes:
                story.append(Paragraph(f"- <b>Nome:</b> {dep.nome_dependente}, <b>CPF:</b> {dep.cpf_dependente}, <b>Data Nasc.:</b> {dep.data_nascimento.strftime('%d/%m/%Y')}, <b>Salário Família:</b> R$ {dep.salario_familia:,.2f}".replace(",", "X").replace(".", ",").replace("X", "."), style_normal))
        else:
            story.append(Paragraph("Nenhum dependente cadastrado.", style_normal))
        story.append(Spacer(1, 0.2 * inch))

        # Reajustes Salariais
        story.append(Paragraph("REAJUSTES SALARIAIS", style_heading2))
        if reajustes:
            for reaj in reajustes:
                story.append(Paragraph(f"- <b>Data:</b> {reaj.data_alteracao.strftime('%d/%m/%Y')}, <b>% Salário:</b> {reaj.percentual_reajuste_salario:,.2f}%, <b>% Bônus:</b> {reaj.percentual_reajuste_bonus:,.2f}%".replace(",", "X").replace(".", ",").replace("X", "."), style_normal))
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

        registros = db.session.query(RegistroPonto).outerjoin(Funcionario).order_by(RegistroPonto.data_hora.desc()).all()
        return render_template('registro_ponto.html', registros_ponto=registros)

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
                cpf = linha[22:34].strip()

                try:
                    data_hora = datetime.datetime.strptime(data_str, '%d%m%Y%H%M')
                except ValueError:
                    continue

                if RegistroPonto.query.filter_by(cpf_funcionario=cpf, data_hora=data_hora).first():
                    continue

                funcionario = Funcionario.query.filter_by(cpf=cpf).first()
                novo = RegistroPonto(
                    cpf_funcionario=cpf,
                    pis=funcionario.pis if funcionario else None,
                    id_face=funcionario.id_face if funcionario else None,
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

    return app # Fim da função create_app


# --- Funções de Inicialização do Banco de Dados ---
def initialize_database(app_instance):
    with app_instance.app_context():
        db.create_all()
        from models import Usuario, LogAuditoria, Cidade, Funcionario, ContratoTrabalho, Setor, Funcao, ReajusteSalarial, Demissao, ControleFerias
        try:
            master_user_exists = db.session.query(Usuario).filter_by(nome='master').first()
            if not master_user_exists:
                master_user = Usuario(nome='master', tipo_usuario='Master')
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
