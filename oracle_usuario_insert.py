# -*- coding: utf-8 -*-
import oracledb
import bcrypt
from oracle_config import USER, PASSWORD, DSN

def inserir_usuario_com_seguranca():
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        # Senha em texto puro que a usuária digitou
        senha_pura = "123456"
        
        # TRANSFORMA EM HASH (Criptografia segura)
        salt = bcrypt.gensalt()
        senha_criptografada = bcrypt.hashpw(senha_pura.encode('utf-8'), salt).decode('utf-8')

        # SQL para inserção com o campo NOME incluído
        sql = """
        INSERT INTO CS_USUARIOS (email, senha, nome, celular, role, cliente_cnpj)
        VALUES (:email, :senha, :nome, :celular, :role, :cnpj)
        """
        
        dados = {
            "email": "mariana.torres@sompo.com",
            "senha": senha_criptografada, # Salvando o hash e não a senha pura!
            "nome": "Mariana Torres",
            "celular": "11943933233",
            "role": "Analista de Subscrição",
            "cnpj": "61384782000124" # CNPJ da Sompo que cadastramos antes
        }

        cursor.execute(sql, dados)
        connection.commit()
        print("Usuário Mariana Torres cadastrado com senha protegida (hash) com sucesso!")
        
        cursor.close()
        connection.close()
    except Exception as e:
        print(f"Erro ao inserir usuário: {e}")

if __name__ == "__main__":
    inserir_usuario_com_seguranca()