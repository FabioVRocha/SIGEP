from app import create_app
from extensions import db
from models import ItemEPI, DistribuicaoItem, DevolucaoItem

app = create_app()

with app.app_context():
    db.create_all(tables=[ItemEPI.__table__, DistribuicaoItem.__table__, DevolucaoItem.__table__])
    print("Tabelas de Fardas e EPI criadas com sucesso.")