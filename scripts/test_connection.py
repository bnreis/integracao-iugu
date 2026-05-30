"""
Script para validar se as credenciais e a planilha estão funcionando.

Uso:
    python scripts/test_connection.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def testar_planilha() -> bool:
    print("\n🗂  Testando planilha de empresas...")
    try:
        from src.spreadsheet import EmpresasRepository

        repo = EmpresasRepository()
        repo.carregar()
        empresas = repo.listar_ativas()
        print(f"   ✅ Planilha carregada com {len(empresas)} empresa(s) ativa(s)")
        for e in empresas[:5]:
            print(f"      • {e.razao_social} ({e.cnpj})")
        return True
    except Exception as exc:
        print(f"   ❌ Erro: {exc}")
        return False


def testar_iugu() -> bool:
    print("\n💰 Testando conexão com a Iugu...")
    try:
        from src.iugu_client import IuguClient

        with IuguClient() as client:
            # Lista 1 fatura só pra validar autenticação
            result = client.list_invoices(limit=1)
            total = result.get("totalItems", 0)
            print(f"   ✅ Autenticação OK — {total} fatura(s) na conta")
            items = result.get("items", [])
            if items:
                inv = items[0]
                print(f"      Exemplo: {inv.get('id')[:8]}... status={inv.get('status')}")
        return True
    except Exception as exc:
        print(f"   ❌ Erro: {exc}")
        return False


def testar_config_nfse() -> bool:
    print("\n📄 Verificando configuração NFS-e DF...")
    from src.config import settings

    problemas = []
    if not settings.nfse_inscricao_municipal:
        problemas.append("NFSE_INSCRICAO_MUNICIPAL não preenchida")
    if not settings.nfse_cnpj_prestador:
        problemas.append("NFSE_CNPJ_PRESTADOR não preenchido")
    if not settings.nfse_certificado_path.exists():
        problemas.append(f"Certificado não encontrado: {settings.nfse_certificado_path}")
    if not settings.nfse_certificado_senha:
        problemas.append("NFSE_CERTIFICADO_SENHA não preenchida")

    if problemas:
        print("   ⚠️  Configuração incompleta (esperado nesta fase):")
        for p in problemas:
            print(f"      • {p}")
        return False
    print(f"   ✅ Configuração OK — ambiente: {settings.nfse_ambiente}")
    return True


def main():
    print("=" * 60)
    print("🔧 Teste de Configuração — Integração Iugu + NFS-e DF")
    print("=" * 60)

    resultados = {
        "Planilha": testar_planilha(),
        "Iugu": testar_iugu(),
        "NFS-e DF": testar_config_nfse(),
    }

    print("\n" + "=" * 60)
    print("📊 Resumo")
    print("=" * 60)
    for nome, ok in resultados.items():
        icon = "✅" if ok else "❌"
        print(f"   {icon} {nome}")
    print()

    if all(resultados.values()):
        print("🎉 Tudo pronto! Você pode iniciar o servidor de webhook.")
    else:
        print("⚠️  Corrija os itens marcados com ❌ antes de seguir.")


if __name__ == "__main__":
    main()
