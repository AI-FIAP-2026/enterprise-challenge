# -*- coding: utf-8 -*-
"""
Cria (ou atualiza) usuários OPERADORES de teste, um para cada cliente informado, para testar a
Central de Alertas com o acesso restrito às fazendas do cliente.

- O cliente é encontrado pelo nome em CS_CLIENTES; o CNPJ completo vem do banco.
- Antes de gravar, mostra as fazendas do cliente e quantos focos de queimada (INPE) houve a até ~50 km
  delas nos últimos 7 dias, por nível de risco.
- A senha é digitada no terminal (não aparece na tela nem fica em arquivo) e é gravada com bcrypt.
- Se o e-mail já existir em CS_USUARIOS, o usuário é atualizado (não duplica).
- Nome e e-mail são gravados criptografados, com o hash do e-mail para o login (requisitos/usuarios.py).

Uso:
    python tratamento/criar_operadores_teste.py
    python tratamento/criar_operadores_teste.py --clientes "Agro Pastoril Canezin" "Agro Santa Margarida"
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import re
import argparse
import getpass
import unicodedata
import bcrypt
import oracledb
from auth import USER, PASSWORD, DSN
from requisitos import usuarios

CLIENTES_PADRAO = ["Agro Pastoril Canezin", "Agro Santa Margarida"]
COLUNAS_NOME_CLIENTE = ["NOME", "NOME_CLIENTE", "RAZAO_SOCIAL", "NOME_FANTASIA", "NOME_EMPRESA"]
ROLE_OPERADOR = "Operador"
TAMANHO_MINIMO_SENHA = 8
ORA_VALOR_NULO = 1400  # ORA-01400: não é possível inserir NULL (ex.: coluna ID sem sequência)


def sem_acento(texto):
    return "".join(c for c in unicodedata.normalize("NFD", str(texto)) if unicodedata.category(c) != "Mn")


def slug_email(nome_cliente):
    """Nome simplificado do cliente para o e-mail: 'Agro Santa Margarida' -> 'santamargarida'
    (tira 'Agro', 'Pastoril', 'Ltda', acentos, espaços e símbolos)."""
    texto = sem_acento(nome_cliente).lower()
    texto = re.sub(r"\b(agro|agropecuaria|pastoril|ltda|s/?a|me|eireli)\b", " ", texto)
    return re.sub(r"[^a-z0-9]", "", texto) or "cliente"


def pedir_senha(titulo):
    while True:
        senha = getpass.getpass(f"{titulo}: ")
        if len(senha) < TAMANHO_MINIMO_SENHA:
            print(f"   A senha precisa ter pelo menos {TAMANHO_MINIMO_SENHA} caracteres. Tente de novo.")
            continue
        if getpass.getpass("Repita a senha: ") != senha:
            print("   As senhas não conferem. Tente de novo.")
            continue
        return senha


def consultar(cursor, sql, parametros=None):
    cursor.execute(sql, parametros or [])
    colunas = [c[0] for c in cursor.description]
    return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def encontrar_cliente(clientes, trecho):
    """Cliente cujo nome contém o trecho informado (sem diferenciar maiúsculas nem acentos)."""
    alvo = sem_acento(trecho).lower().strip()
    achados = []
    for c in clientes:
        coluna = next((col for col in COLUNAS_NOME_CLIENTE if c.get(col)), None)
        nome = str(c[coluna]).strip() if coluna else ""
        if alvo and alvo in sem_acento(nome).lower():
            achados.append((nome, c))
    return achados


def resumo_risco(cursor, id_cliente):
    """Fazendas do cliente e focos do INPE nos últimos 7 dias a até ~50 km de cada uma."""
    return consultar(cursor, """
        SELECT f.ID, f.NOME_FAZENDA, f.MUNICIPIO, f.ESTADO,
               COUNT(a.ID) AS FOCOS,
               SUM(CASE WHEN a.CATEGORIA_RISCO = 'Crítico' THEN 1 ELSE 0 END) AS CRITICO,
               SUM(CASE WHEN a.CATEGORIA_RISCO = 'Alto' THEN 1 ELSE 0 END) AS ALTO,
               SUM(CASE WHEN a.CATEGORIA_RISCO = 'Médio' THEN 1 ELSE 0 END) AS MEDIO,
               SUM(CASE WHEN a.CATEGORIA_RISCO = 'Aguardando risco' THEN 1 ELSE 0 END) AS AGUARDANDO
        FROM CS_FAZENDAS f
        LEFT JOIN CS_ALERTAS a
          ON a.ORIGEM_ALERTA = 'INPE'
         AND a.DATA_HORA >= SYSDATE - 7
         AND a.LATITUDE  BETWEEN f.LATITUDE  - 0.45 AND f.LATITUDE  + 0.45
         AND a.LONGITUDE BETWEEN f.LONGITUDE - 0.50 AND f.LONGITUDE + 0.50
        WHERE f.ID_CLIENTE = :1
        GROUP BY f.ID, f.NOME_FAZENDA, f.MUNICIPIO, f.ESTADO
        ORDER BY FOCOS DESC, f.ID
    """, [id_cliente])


def gravar_usuario(cursor, nome, email, hash_senha, cnpj):
    """Atualiza se o e-mail já existe; senão insere. Devolve 'atualizado' ou 'criado'.
    Nome e e-mail vão criptografados e o e-mail também como hash (busca do login)."""
    existente = usuarios.buscar_para_login(cursor, email)
    protegidos = usuarios.campos_protegidos(nome=nome, email=email)
    dados = dict(protegidos, SENHA=hash_senha, ROLE=ROLE_OPERADOR, CLIENTE_CNPJ=cnpj)
    if existente:
        cursor.execute(f"UPDATE CS_USUARIOS SET {', '.join(f'{c} = :{c}' for c in dados)} WHERE ID = :id_usuario",
                       dict(dados, id_usuario=existente["ID"]))
        return "atualizado"
    colunas = list(dados)
    try:
        cursor.execute(f"INSERT INTO CS_USUARIOS ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)})",
                       dados)
    except oracledb.DatabaseError as e:
        erro = e.args[0]
        if getattr(erro, "code", None) != ORA_VALOR_NULO:
            raise
        # A coluna ID não é gerada automaticamente: usa o próximo número livre
        cursor.execute(f"""
            INSERT INTO CS_USUARIOS (ID, {', '.join(colunas)})
            SELECT NVL(MAX(ID), 0) + 1, {', '.join(':' + c for c in colunas)} FROM CS_USUARIOS
        """, dados)
    return "criado"


def main():
    parser = argparse.ArgumentParser(description="Cria operadores de teste para clientes de CS_CLIENTES.")
    parser.add_argument("--clientes", nargs="+", default=CLIENTES_PADRAO,
                        help="nomes (ou parte do nome) dos clientes")
    args = parser.parse_args()

    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CS_USUARIOS' "
                       "AND COLUMN_NAME = 'EMAIL_HASH'")
        if not cursor.fetchone()[0]:
            print("[ERRO] Rode antes o sql/estrutura_banco.sql (coluna CS_USUARIOS.EMAIL_HASH).")
            return 1
        clientes = consultar(cursor, "SELECT * FROM CS_CLIENTES")
        planejados = []
        for trecho in args.clientes:
            achados = encontrar_cliente(clientes, trecho)
            if len(achados) != 1:
                opcoes = ", ".join(n for n, _ in achados) or "nenhum"
                print(f"[ERRO] '{trecho}' encontrou {len(achados)} cliente(s): {opcoes}. Informe um nome mais exato.")
                return 1
            nome_cliente, cliente = achados[0]
            email = f"operador@{slug_email(nome_cliente)}.com.br"
            planejados.append({"cliente": nome_cliente, "id": cliente["ID"], "cnpj": cliente.get("CNPJ"),
                               "email": email, "nome": f"Operador {nome_cliente}"})

        for p in planejados:
            fazendas = resumo_risco(cursor, p["id"])
            focos = sum(int(f["FOCOS"] or 0) for f in fazendas)
            print(f"\n=== {p['cliente']} (CNPJ {p['cnpj']}) — {len(fazendas)} fazenda(s), "
                  f"{focos} foco(s) nos últimos 7 dias a até ~50 km")
            for f in fazendas:
                print(f"   {f['NOME_FAZENDA']} ({f['MUNICIPIO']}/{f['ESTADO']}): {f['FOCOS']} foco(s) | "
                      f"Crítico {f['CRITICO'] or 0}, Alto {f['ALTO'] or 0}, Médio {f['MEDIO'] or 0}, "
                      f"Aguardando {f['AGUARDANDO'] or 0}")
            if not p["cnpj"]:
                print("   [ERRO] Cliente sem CNPJ em CS_CLIENTES: o operador não teria acesso a nenhuma fazenda.")
                return 1
            if focos == 0:
                print("   [AVISO] Nenhum foco perto das fazendas deste cliente nos últimos 7 dias.")

        print("\nUsuários que serão criados (ou atualizados):")
        for p in planejados:
            print(f"   - {p['email']}  | perfil {ROLE_OPERADOR} | CNPJ {p['cnpj']}")
        if input("\nConfirma? Digite SIM para continuar: ").strip().upper() != "SIM":
            print("Cancelado. Nada foi gravado.")
            return 1

        senha = pedir_senha("\nSenha para os operadores de teste")
        for p in planejados:
            hash_senha = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            p["resultado"] = gravar_usuario(cursor, p["nome"], p["email"], hash_senha, p["cnpj"])
        connection.commit()

        print("\n✔ Pronto. Faça login no app com:")
        for p in planejados:
            print(f"   {p['email']}  ({p['resultado']}) -> vê só as fazendas de {p['cliente']}")
        return 0
    except KeyboardInterrupt:
        connection.rollback()
        print("\nInterrompido. Nada foi gravado.")
        return 1
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
