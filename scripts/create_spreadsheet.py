"""
Script para gerar a planilha modelo de empresas autorizadas.

Uso:
    python scripts/create_spreadsheet.py
    python scripts/create_spreadsheet.py --sem-exemplos
    python scripts/create_spreadsheet.py --saida /caminho/customizado.xlsx
"""
import argparse
import sys
from pathlib import Path

# Adiciona o diretório raiz ao path para importar o pacote src/
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.spreadsheet import criar_planilha_modelo


def main():
    parser = argparse.ArgumentParser(
        description="Cria a planilha modelo de empresas autorizadas para NFS-e automática."
    )
    parser.add_argument(
        "--saida",
        type=Path,
        default=None,
        help="Caminho do arquivo de saída (padrão: ./empresas_autorizadas.xlsx)",
    )
    parser.add_argument(
        "--sem-exemplos",
        action="store_true",
        help="Cria a planilha sem linhas de exemplo",
    )
    args = parser.parse_args()

    caminho = criar_planilha_modelo(
        caminho=args.saida,
        com_exemplos=not args.sem_exemplos,
    )
    print(f"✅ Planilha criada com sucesso: {caminho}")


if __name__ == "__main__":
    main()
