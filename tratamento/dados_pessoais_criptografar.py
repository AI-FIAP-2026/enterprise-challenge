# -*- coding: utf-8 -*-
"""
Protege os dados pessoais que já estão gravados no banco (rodar à mão, uma vez, depois do sql/estrutura_banco.sql).

1. CS_USUARIOS: NOME, EMAIL e CELULAR passam a ficar criptografados (criptografia.py, Fernet) e EMAIL_HASH recebe o
   hash do e-mail (HMAC-SHA256), usado pelo login. A SENHA já é hash bcrypt (irreversível) e não muda.
2. CS_CLIENTES: TELEFONE, EMAIL e os dados do responsável (nome, CPF, telefone e e-mail) gravados abertos (cargas
   antigas, antes da criptografia do Cadastro) são criptografados.
3. Colunas de "quem fez" (CS_LOG_ACOES, CS_ANEXOS, CS_MANUTENCOES_REALIZADAS, CS_LEITURAS_MEDIDOR, CS_NLP_REVISOES,
   CS_NLP_USO_IA): o nome do usuário é trocado pela referência "USR-<ID>" (as telas mostram o nome). Nome que não é
   de nenhum usuário é criptografado; rótulos do sistema ("Carga de teste", "Sompo (carga de teste)" etc.) ficam.

Pode rodar mais de uma vez: o que já está criptografado ou já é referência fica como está.
Nada de dado pessoal aparece na tela: o script mostra só as quantidades.

Uso:
    python tratamento/dados_pessoais_criptografar.py --simular   (conta o que seria alterado, sem gravar)
    python tratamento/dados_pessoais_criptografar.py             (grava)
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import argparse
import oracledb
from auth import USER, PASSWORD, DSN
import criptografia
from requisitos import usuarios

CAMPOS_USUARIOS = ("NOME", "EMAIL", "CELULAR")
CAMPOS_CLIENTES = ("TELEFONE", "EMAIL", "RESPONSAVEL_NOME", "RESPONSAVEL_CPF", "RESPONSAVEL_TELEFONE",
                   "RESPONSAVEL_EMAIL")
COLUNAS_QUEM_FEZ = [("CS_LOG_ACOES", "USUARIO"), ("CS_ANEXOS", "USUARIO_ENVIO"),
                    ("CS_MANUTENCOES_REALIZADAS", "AVALIADO_POR"), ("CS_LEITURAS_MEDIDOR", "USUARIO"),
                    ("CS_NLP_REVISOES", "USUARIO"), ("CS_NLP_USO_IA", "USUARIO")]
ROTULOS_DO_SISTEMA = ("carga de teste", "exemplo", "sistema", "pipeline")
TAMANHOS_MINIMOS = {("CS_USUARIOS", "NOME"): 400, ("CS_USUARIOS", "EMAIL"): 400, ("CS_USUARIOS", "CELULAR"): 200,
                    ("CS_CLIENTES", "EMAIL"): 400, ("CS_CLIENTES", "RESPONSAVEL_EMAIL"): 400}


def colunas(cursor, tabela):
    """{COLUNA: tamanho em caracteres} da tabela ({} se ela não existe)."""
    cursor.execute("SELECT COLUMN_NAME, CHAR_LENGTH FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1", [tabela])
    return {c: t for c, t in cursor.fetchall()}


def aberto(valor):
    """Tem texto e ainda não está criptografado."""
    return valor is not None and str(valor).strip() != "" and not criptografia.esta_criptografado(str(valor))


def proteger_usuarios(cursor):
    col = colunas(cursor, "CS_USUARIOS")
    campos = [c for c in CAMPOS_USUARIOS if c in col]
    cursor.execute(f"SELECT ID, {', '.join(campos)}, EMAIL_HASH FROM CS_USUARIOS")
    alterados, nomes = 0, {}
    for linha in cursor.fetchall():
        id_usuario, valores, hash_atual = linha[0], dict(zip(campos, linha[1:-1])), linha[-1]
        abertos = {c: criptografia.descriptografar(v) for c, v in valores.items()}
        if abertos.get("NOME"):
            nomes[str(abertos["NOME"]).strip().lower()] = usuarios.referencia(id_usuario)
        novos = {c: criptografia.criptografar(v) for c, v in valores.items() if aberto(v)}
        if "EMAIL" in novos:
            novos["EMAIL"] = criptografia.criptografar(criptografia.normalizar_email(abertos["EMAIL"]))
        email_hash = criptografia.hash_busca(abertos["EMAIL"]) if abertos.get("EMAIL") else None
        if email_hash and email_hash != hash_atual:
            novos["EMAIL_HASH"] = email_hash
        if novos:
            cursor.execute(f"UPDATE CS_USUARIOS SET {', '.join(f'{c} = :{c}' for c in novos)} WHERE ID = :id_usuario",
                           dict(novos, id_usuario=id_usuario))
            alterados += 1
    return alterados, nomes


def proteger_clientes(cursor):
    col = colunas(cursor, "CS_CLIENTES")
    campos = [c for c in CAMPOS_CLIENTES if c in col]
    if not campos:
        return 0, {}
    cursor.execute(f"SELECT ID, {', '.join(campos)} FROM CS_CLIENTES")
    alterados, por_campo = 0, {}
    for linha in cursor.fetchall():
        novos = {c: criptografia.criptografar(v) for c, v in zip(campos, linha[1:]) if aberto(v)}
        if novos:
            cursor.execute(f"UPDATE CS_CLIENTES SET {', '.join(f'{c} = :{c}' for c in novos)} WHERE ID = :id_cliente",
                           dict(novos, id_cliente=linha[0]))
            alterados += 1
            for c in novos:
                por_campo[c] = por_campo.get(c, 0) + 1
    return alterados, por_campo


def trocar_quem_fez(cursor, nomes):
    """Nome -> referência do usuário em cada coluna de "quem fez". Devolve [(tabela.coluna, trocados, cripto, fica)]."""
    resumo = []
    for tabela, coluna in COLUNAS_QUEM_FEZ:
        col = colunas(cursor, tabela)
        if coluna not in col:
            continue
        cursor.execute(f"SELECT {coluna}, COUNT(*) FROM {tabela} WHERE {coluna} IS NOT NULL GROUP BY {coluna}")
        trocados = cripto = ficam = 0
        for valor, quantidade in cursor.fetchall():
            texto = str(valor).strip()
            if usuarios.eh_referencia(texto) or criptografia.esta_criptografado(texto):
                continue
            if texto.lower() in nomes:
                novo = nomes[texto.lower()]
                trocados += quantidade
            elif any(r in texto.lower() for r in ROTULOS_DO_SISTEMA):
                ficam += quantidade
                continue
            else:
                novo = criptografia.criptografar(texto)
                if len(novo) > (col[coluna] or 0):
                    novo = usuarios.PREFIXO_REFERENCIA + "?"      # não cabe criptografado: fica sem o nome
                cripto += quantidade
            cursor.execute(f"UPDATE {tabela} SET {coluna} = :novo WHERE {coluna} = :antigo",
                           {"novo": novo, "antigo": valor})
        resumo.append((f"{tabela}.{coluna}", trocados, cripto, ficam))
    return resumo


def main():
    parser = argparse.ArgumentParser(description="Criptografa os dados pessoais já gravados no banco.")
    parser.add_argument("--simular", action="store_true", help="conta o que seria alterado, sem gravar")
    args = parser.parse_args()

    problema = criptografia.diagnostico_chave()
    if problema:
        print(f"[ERRO] Chave de criptografia: {problema}")
        return 1
    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    try:
        faltando = []
        for (tabela, coluna), tamanho in TAMANHOS_MINIMOS.items():
            atual = colunas(cursor, tabela).get(coluna)
            if atual is not None and atual < tamanho:
                faltando.append(f"{tabela}.{coluna}")
        if "EMAIL_HASH" not in colunas(cursor, "CS_USUARIOS") or faltando:
            print("[ERRO] Rode antes o sql/estrutura_banco.sql (coluna EMAIL_HASH e colunas ampliadas"
                  + (": " + ", ".join(faltando) if faltando else "") + ").")
            return 1

        qtd_usuarios, nomes = proteger_usuarios(cursor)
        qtd_clientes, por_campo = proteger_clientes(cursor)
        resumo = trocar_quem_fez(cursor, nomes)

        print(f"\nCS_USUARIOS: {qtd_usuarios} usuário(s) com nome, e-mail ou celular criptografados agora "
              f"({len(nomes)} usuário(s) no total).")
        print(f"CS_CLIENTES: {qtd_clientes} cliente(s) com dados pessoais criptografados agora"
              + (" (" + ", ".join(f"{c}: {n}" for c, n in sorted(por_campo.items())) + ")" if por_campo else "") + ".")
        for nome_coluna, trocados, cripto, ficam in resumo:
            print(f"{nome_coluna}: {trocados} linha(s) com a referência do usuário, {cripto} criptografada(s), "
                  f"{ficam} com rótulo do sistema (sem dado pessoal).")

        if args.simular:
            connection.rollback()
            print("\nSimulação: nada foi gravado. Rode sem --simular para gravar.")
        else:
            connection.commit()
            print("\nPronto. O login passa a usar o hash do e-mail; as telas mostram os nomes normalmente.")
        return 0
    except Exception:
        connection.rollback()
        print("\n[ERRO] Nada foi gravado.")
        raise
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
