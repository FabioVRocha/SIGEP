# SIGEP

Este repositório contém uma aplicação Flask para gerenciamento de funcionários. 

## Rotina de backup

O script `backup.py` utiliza o utilitário `pg_dump` para gerar um arquivo `.backup` do banco de dados configurado em `config.py`.
Para evitar instalar o PostgreSQL completo na máquina local, copie o executável `pg_dump` para a raiz do projeto ou defina a variável `PG_DUMP_PATH` com o caminho para ele.

> **Atenção**: no Windows é necessário copiar também as bibliotecas `libpq.dll` e
> `libintl-8.dll` (presentes na pasta `bin` da instalação do PostgreSQL) para o
> mesmo diretório do `pg_dump`. Sem essas dependências o programa exibirá o erro
> `exit status 3221225781` ao tentar iniciar.

### Resolvendo erro 3221225781 no Windows

Esse código indica que alguma biblioteca exigida não foi encontrada. Copie as
DLLs `libpq.dll` e `libintl-8.dll` (da pasta `bin` de uma instalação do
PostgreSQL) para o mesmo diretório do `pg_dump` e verifique se elas possuem a
mesma arquitetura (32 ou 64 bits) do executável.

1. Opcionalmente defina a variável `DATABASE_URL` com a string de conexão para o
   banco de dados. Caso não seja definida, será utilizada a URL padrão presente
   em `config.py`.
2. Execute:

```bash
python backup.py
```

O arquivo será salvo na pasta `backups/` com a data e hora no nome.