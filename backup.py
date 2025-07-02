import os
import subprocess
from datetime import datetime

from config import Config


def create_backup():
    """Gera um arquivo .backup usando pg_dump."""
    db_url = os.environ.get('DATABASE_URL', Config.SQLALCHEMY_DATABASE_URI)
    pg_dump = os.environ.get('PG_DUMP_PATH', os.path.join(os.path.dirname(__file__), 'pg_dump'))

    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_dir = os.path.join(os.path.dirname(__file__), 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    backup_file = os.path.join(backup_dir, f'sigep_{timestamp}.backup')

    try:
        result = subprocess.run(
            [pg_dump, f"--dbname={db_url}", "-f", backup_file],
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            print(result.stdout)
        if result.stderr:
            print(result.stderr)
        print(f"Backup salvo em {backup_file}")
    except FileNotFoundError:
        print(f"pg_dump não encontrado em {pg_dump}")
    except subprocess.CalledProcessError as exc:
        msg = exc.stderr or str(exc)
        print(f"Falha ao executar pg_dump: {msg}")


if __name__ == '__main__':
    create_backup()