# -*- coding: utf-8 -*-
"""
Criptografia dos dados pessoais gravados no banco: nome, CPF, e-mail e telefones do responsável pelo cliente
(CS_CLIENTES) e nome, e-mail e celular dos usuários (CS_USUARIOS). A senha dos usuários não passa por aqui: é
guardada como hash bcrypt, que é irreversível.

Usa Fernet (AES-128 em modo CBC + HMAC-SHA256, da biblioteca cryptography, que já é instalada junto com o
python-oracledb). É reversível: a página de cadastro mostra os dados abertos para quem tem acesso, mas no banco
eles ficam ilegíveis (ex.: "enc:v1:gAAAAAB...").

A chave fica FORA do repositório, em auth.py (que está no .gitignore) ou na variável de ambiente
CAMPO_SEGURO_CHAVE. Para gerar uma chave:  python tratamento/db_gerar_chave_criptografia.py
ATENÇÃO: sem a chave não há como ler os dados gravados. Guarde uma cópia em local seguro; trocar a chave
exige recriptografar os dados.
"""
import os

PREFIXO = "enc:v1:"


class ChaveAusente(Exception):
    """A chave de criptografia não foi configurada."""


def _obter_chave():
    chave = os.environ.get("CAMPO_SEGURO_CHAVE")
    if not chave:
        try:
            from auth import CHAVE_CRIPTOGRAFIA as chave
        except ImportError:
            chave = None
    if not chave:
        raise ChaveAusente("Chave de criptografia não configurada. Gere uma com "
                           "'python tratamento/db_gerar_chave_criptografia.py' e inclua em auth.py a linha "
                           "CHAVE_CRIPTOGRAFIA = \"...\".")
    return chave.encode() if isinstance(chave, str) else chave


def _fernet():
    from cryptography.fernet import Fernet
    return Fernet(_obter_chave())


def chave_configurada():
    return diagnostico_chave() is None


def diagnostico_chave():
    """None se a chave está pronta para uso; senão, o motivo (para mostrar na tela)."""
    try:
        import auth
    except ImportError:
        auth = None
    if not os.environ.get("CAMPO_SEGURO_CHAVE") and not getattr(auth, "CHAVE_CRIPTOGRAFIA", None):
        return ("a linha CHAVE_CRIPTOGRAFIA não foi encontrada no auth.py. Confira se ela está no auth.py da pasta "
                "principal do projeto (ao lado do app.py) e reinicie o Streamlit (Control + C e streamlit run app.py): "
                "ele só lê o auth.py quando é iniciado.")
    try:
        _fernet()
    except Exception:
        return ("a chave do auth.py é inválida (deve ter 44 caracteres e terminar com '='). Copie de novo a linha "
                "inteira mostrada por 'python tratamento/db_gerar_chave_criptografia.py', com as aspas.")
    return None


def criptografar(texto):
    """Texto -> 'enc:v1:<token>'. Vazio continua vazio (None)."""
    if texto is None or str(texto).strip() == "":
        return None
    return PREFIXO + _fernet().encrypt(str(texto).encode("utf-8")).decode("ascii")


def descriptografar(valor):
    """'enc:v1:<token>' -> texto. Valor antigo, gravado sem criptografia, volta como está."""
    if valor is None:
        return None
    valor = str(valor)
    if not valor.startswith(PREFIXO):
        return valor
    return _fernet().decrypt(valor[len(PREFIXO):].encode("ascii")).decode("utf-8")


def descriptografar_seguro(valor, padrao="(dado protegido)"):
    """Como descriptografar, mas sem erro para a tela: chave ausente ou dado ilegível devolvem `padrao`."""
    try:
        return descriptografar(valor)
    except Exception:
        return padrao


def normalizar_email(email):
    return str(email or "").strip().lower()


def hash_busca(texto):
    """Hash fixo (HMAC-SHA256, 64 caracteres) de um dado criptografado que precisa ser procurado no banco, como o
    e-mail do login: o Fernet gera um texto diferente a cada gravação, então a busca é feita por este hash. A chave do
    HMAC é derivada da chave de criptografia, e o hash não permite recuperar o e-mail."""
    import hashlib
    import hmac
    chave = hashlib.sha256(b"campo-seguro-busca:" + _obter_chave()).digest()
    return hmac.new(chave, normalizar_email(texto).encode("utf-8"), hashlib.sha256).hexdigest()


def esta_criptografado(valor):
    return isinstance(valor, str) and valor.startswith(PREFIXO)
