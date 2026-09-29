# -*- coding: utf-8 -*-
"""
Gera a chave de criptografia dos dados pessoais do cadastro (nome completo, CPF e telefones).

Uso:
    python tratamento/db_gerar_chave_criptografia.py

Copie a linha mostrada para o arquivo auth.py (que não vai para o GitHub). Rode UMA vez só:
uma chave nova não abre os dados gravados com a chave anterior. Guarde uma cópia da chave em local seguro.
"""
from cryptography.fernet import Fernet

if __name__ == "__main__":
    print("Inclua esta linha no arquivo auth.py (e guarde uma cópia em local seguro):\n")
    print(f'CHAVE_CRIPTOGRAFIA = "{Fernet.generate_key().decode()}"')
