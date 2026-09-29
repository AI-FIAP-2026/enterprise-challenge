# -*- coding: utf-8 -*-
"""
Usuários do sistema (CS_USUARIOS) com os dados pessoais protegidos.

- NOME, EMAIL e CELULAR ficam criptografados (criptografia.py, Fernet). A SENHA é hash bcrypt (irreversível).
- O login procura o usuário pela coluna EMAIL_HASH (HMAC-SHA256 do e-mail em minúsculas), porque o texto
  criptografado muda a cada gravação. Usuário ainda não migrado (e-mail aberto) continua entrando pelo e-mail.
- Nas tabelas que registram quem fez algo (CS_LOG_ACOES, CS_ANEXOS, CS_MANUTENCOES_REALIZADAS,
  CS_LEITURAS_MEDIDOR, CS_NLP_REVISOES, CS_NLP_USO_IA) grava-se só a referência do usuário ("USR-5"), sem nome.
  As telas trocam a referência pelo nome na hora de mostrar (nomes_por_referencia).

Migração do que já está gravado: tratamento/dados_pessoais_criptografar.py.
"""
import re

import criptografia

PREFIXO_REFERENCIA = "USR-"
_REFERENCIA = re.compile(r"^USR-(\d+)$")


def referencia(id_usuario):
    """Referência do usuário gravada no lugar do nome: 'USR-<ID>'."""
    return f"{PREFIXO_REFERENCIA}{int(id_usuario)}"


def eh_referencia(valor):
    return bool(_REFERENCIA.match(str(valor or "").strip()))


def _colunas(cursor):
    cursor.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = 'CS_USUARIOS'")
    return {linha[0] for linha in cursor.fetchall()}


def buscar_para_login(cursor, email):
    """Usuário do e-mail informado: dict com ID, NOME (aberto), SENHA (hash), ROLE e CLIENTE_CNPJ; None se não achar.
    Procura primeiro pelo hash do e-mail; se não achar, pelo e-mail aberto (usuário ainda não migrado)."""
    email = criptografia.normalizar_email(email)
    if not email:
        return None
    campos = "ID, NOME, SENHA, ROLE, CLIENTE_CNPJ"
    linha = None
    if "EMAIL_HASH" in _colunas(cursor):
        try:
            cursor.execute(f"SELECT {campos} FROM CS_USUARIOS WHERE EMAIL_HASH = :1", [criptografia.hash_busca(email)])
            linha = cursor.fetchone()
        except criptografia.ChaveAusente:
            linha = None
    if linha is None:
        cursor.execute(f"SELECT {campos} FROM CS_USUARIOS WHERE LOWER(TRIM(EMAIL)) = :1", [email])
        linha = cursor.fetchone()
    if linha is None:
        return None
    id_usuario, nome, senha, role, cnpj = linha
    return {"ID": id_usuario, "NOME": criptografia.descriptografar_seguro(nome, "Usuário"), "SENHA": senha,
            "ROLE": role, "CLIENTE_CNPJ": cnpj}


def campos_protegidos(nome=None, email=None, celular=None):
    """Valores prontos para gravar em CS_USUARIOS: NOME, EMAIL e CELULAR criptografados e EMAIL_HASH.
    Só devolve as chaves informadas (None = não mexer)."""
    saida = {}
    if nome is not None:
        saida["NOME"] = criptografia.criptografar(nome)
    if email is not None:
        email = criptografia.normalizar_email(email)
        saida["EMAIL"] = criptografia.criptografar(email)
        saida["EMAIL_HASH"] = criptografia.hash_busca(email)
    if celular is not None:
        saida["CELULAR"] = criptografia.criptografar(celular)
    return saida


def nomes_por_referencia(cursor):
    """{'USR-5': 'Nome aberto'} de todos os usuários (para mostrar quem fez cada ação)."""
    try:
        cursor.execute("SELECT ID, NOME FROM CS_USUARIOS")
        linhas = cursor.fetchall()
    except Exception:
        return {}
    return {referencia(i): criptografia.descriptografar_seguro(nome, referencia(i)) for i, nome in linhas}


def nome_para_exibir(valor, nomes):
    """Texto de 'quem fez' para a tela: referência -> nome; valor criptografado -> aberto; o resto como está."""
    if valor is None or str(valor).strip() == "":
        return ""
    texto = str(valor).strip()
    if eh_referencia(texto):
        return nomes.get(texto, texto)
    if criptografia.esta_criptografado(texto):
        return criptografia.descriptografar_seguro(texto)
    return texto
