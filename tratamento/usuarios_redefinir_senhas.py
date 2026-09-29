# -*- coding: utf-8 -*-
"""
Redefine as senhas dos usuários de CS_USUARIOS (rodar à mão).

A senha é digitada no terminal (não aparece na tela nem fica gravada em arquivo) e vai para o banco
criptografada com bcrypt, o mesmo formato que o login do app.py confere. Cada usuário recebe um hash
diferente, mesmo com a mesma senha (o bcrypt usa um "sal" aleatório).

Uso:
    python tratamento/redefinir_senhaSIMs.py                 (mesma senha para todos os usuários)
    python tratamento/redefinir_senhas.py --cada-um       (pede uma senha para cada usuário)
    python tratamento/redefinir_senhas.py --email admin@camposeguro.com   (só um usuário)
"""
import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))
root_dir = os.path.abspath(os.path.join(current_dir, '..'))
if root_dir not in sys.path:
    sys.path.append(root_dir)

import argparse
import getpass
import bcrypt
import oracledb
from auth import USER, PASSWORD, DSN

TAMANHO_MINIMO = 8


def pedir_senha(titulo):
    """Pede a senha duas vezes (sem mostrar na tela) até as duas baterem e ter o tamanho mínimo."""
    while True:
        senha = getpass.getpass(f"{titulo}: ")
        if len(senha) < TAMANHO_MINIMO:
            print(f"   A senha precisa ter pelo menos {TAMANHO_MINIMO} caracteres. Tente de novo.")
            continue
        if getpass.getpass("Repita a senha: ") != senha:
            print("   As senhas não conferem. Tente de novo.")
            continue
        return senha


def main():
    parser = argparse.ArgumentParser(description="Redefine as senhas de CS_USUARIOS (bcrypt).")
    parser.add_argument("--cada-um", action="store_true", help="pede uma senha diferente para cada usuário")
    parser.add_argument("--email", help="redefine só o usuário com este e-mail")
    args = parser.parse_args()

    connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    cursor = connection.cursor()
    try:
        sql = "SELECT EMAIL, NOME, ROLE FROM CS_USUARIOS"
        parametros = []
        if args.email:
            sql += " WHERE LOWER(TRIM(EMAIL)) = LOWER(TRIM(:1))"
            parametros = [args.email]
        cursor.execute(sql + " ORDER BY ROLE, EMAIL", parametros)
        usuarios = cursor.fetchall()
        if not usuarios:
            print("Nenhum usuário encontrado.")
            return 1

        print(f"\nUsuários que terão a senha redefinida ({len(usuarios)}):")
        for email, nome, role in usuarios:
            print(f"   - {email}  ({nome} | {role})")
        if input("\nConfirma? Digite SIM para continuar: ").strip().upper() != "SIM":
            print("Cancelado. Nenhuma senha foi alterada.")
            return 1

        senha_comum = None if args.cada_um else pedir_senha("\nNova senha para TODOS os usuários")
        alteracoes = []
        for email, nome, role in usuarios:
            senha = senha_comum or pedir_senha(f"\nNova senha para {email}")
            hash_senha = bcrypt.hashpw(senha.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
            alteracoes.append((hash_senha, email, senha))

        cursor.executemany("UPDATE CS_USUARIOS SET SENHA = :1 WHERE EMAIL = :2", [a[:2] for a in alteracoes])
        connection.commit()

        # Conferência: lê de volta e testa cada senha do mesmo jeito que o login do app.py
        falhas = 0
        for _, email, senha in alteracoes:
            cursor.execute("SELECT SENHA FROM CS_USUARIOS WHERE EMAIL = :1", [email])
            linha = cursor.fetchone()
            if not linha or not bcrypt.checkpw(senha.encode("utf-8"), str(linha[0]).encode("utf-8")):
                falhas += 1
                print(f"   [ERRO] {email}: a senha não foi gravada.")
        if falhas:
            return 1
        print(f"\n✔ {len(alteracoes)} senha(s) redefinida(s) e criptografada(s) com bcrypt.")
        return 0
    except KeyboardInterrupt:
        connection.rollback()
        print("\nInterrompido. Nenhuma senha foi alterada.")
        return 1
    finally:
        cursor.close()
        connection.close()


if __name__ == "__main__":
    sys.exit(main())
