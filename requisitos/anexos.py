# -*- coding: utf-8 -*-
"""
Anexos do sistema (comprovantes de manutenção, notas fiscais e manuais dos equipamentos).

- Comprovantes, notas fiscais e fotos do horímetro: CS_ANEXOS (sql/estrutura_banco.sql), ligados a uma manutenção, a um
  equipamento segurado ou a uma leitura mensal do horímetro.
- Manuais: CS_EQUIPAMENTOS_MANUAIS, ligados ao MODELO do equipamento (ID_EQUIPAMENTO = CS_EQUIPAMENTOS_MODELOS.ID),
  valem para todas as máquinas do mesmo modelo. COD_FONTE_MANUAL liga o manual às orientações de manutenção
  (CS_EQUIPAMENTOS_ORIENTACOES).
Cada arquivo guarda o SHA-256 (integridade), quem enviou e quando (rastreabilidade).

As funções recebem uma conexão aberta (oracledb) e não fazem commit: quem chama decide quando confirmar.
"""
import hashlib
import re
import unicodedata
from pathlib import Path

import oracledb

# Cópia de trabalho dos manuais para a leitura automática e com IA (campo_seguro/nlp). A cópia oficial fica no banco
# (CS_EQUIPAMENTOS_MANUAIS): se o app rodar na nuvem, esta pasta é apagada a cada reinício.
PASTA_MANUAIS = Path(__file__).resolve().parents[1] / "data" / "raw"

ENTIDADE_MANUTENCAO = "MANUTENCAO"
ENTIDADE_EQUIPAMENTO = "EQUIPAMENTO"
ENTIDADE_LEITURA = "LEITURA"          # leitura mensal do horímetro (CS_LEITURAS_MEDIDOR)
ANEXO_COMPROVANTE = "COMPROVANTE"
ANEXO_NOTA_FISCAL = "NOTA_FISCAL"
ANEXO_HORIMETRO = "HORIMETRO"          # foto ou PDF do horímetro/hodômetro

TIPOS_DOCUMENTO = ["pdf", "jpg", "jpeg", "png"]      # comprovantes e notas fiscais
TIPOS_MANUAL = ["pdf"]
TAMANHO_MAXIMO_MB = {ANEXO_COMPROVANTE: 10, ANEXO_NOTA_FISCAL: 10, ANEXO_HORIMETRO: 10, "MANUAL": 70}
ORA_VALOR_NULO = 1400


class AnexoInvalido(ValueError):
    """Arquivo vazio, grande demais ou de tipo não aceito."""


def validar_arquivo(nome, conteudo, tipos, tamanho_maximo_mb):
    extensao = nome.rsplit(".", 1)[-1].lower() if "." in nome else ""
    if extensao not in tipos:
        raise AnexoInvalido(f"{nome}: tipo de arquivo não aceito (use {', '.join(tipos).upper()}).")
    if not conteudo:
        raise AnexoInvalido(f"{nome}: arquivo vazio.")
    if len(conteudo) > tamanho_maximo_mb * 1024 * 1024:
        raise AnexoInvalido(f"{nome}: arquivo maior que {tamanho_maximo_mb} MB.")


def nome_seguro(nome):
    """Remove caminhos e caracteres estranhos do nome enviado."""
    nome = re.split(r"[\\/]", str(nome or "arquivo"))[-1]
    return re.sub(r"[^\w.\- ()]", "_", nome)[:200] or "arquivo"


def _inserir_com_id(cursor, tabela, dados):
    """INSERT devolvendo o ID; se a tabela não gera ID sozinha, usa MAX(ID) + 1."""
    colunas = list(dados)
    novo_id = cursor.var(oracledb.NUMBER)
    try:
        cursor.setinputsizes(**{c: oracledb.DB_TYPE_BLOB for c in colunas if c in ("ARQUIVO", "ARQUIVO_PDF")})
        cursor.execute(f"INSERT INTO {tabela} ({', '.join(colunas)}) VALUES ({', '.join(':' + c for c in colunas)}) "
                       f"RETURNING ID INTO :novo_id", dict(dados, novo_id=novo_id))
        valor = novo_id.getvalue()
        return int(valor[0] if isinstance(valor, list) else valor)
    except oracledb.DatabaseError as e:
        if getattr(e.args[0], "code", None) != ORA_VALOR_NULO:
            raise
    cursor.execute(f"LOCK TABLE {tabela} IN EXCLUSIVE MODE")
    cursor.execute(f"SELECT NVL(MAX(ID), 0) + 1 FROM {tabela}")
    proximo = int(cursor.fetchone()[0])
    cursor.setinputsizes(**{c: oracledb.DB_TYPE_BLOB for c in colunas if c in ("ARQUIVO", "ARQUIVO_PDF")})
    cursor.execute(f"INSERT INTO {tabela} (ID, {', '.join(colunas)}) VALUES (:id_novo, "
                   f"{', '.join(':' + c for c in colunas)})", dict(dados, id_novo=proximo))
    return proximo


def salvar_anexo(conn, tipo_entidade, id_entidade, tipo_anexo, nome, conteudo, tipo_conteudo=None, usuario=None):
    """Grava um comprovante ou nota fiscal em CS_ANEXOS. Devolve o ID do anexo."""
    nome = nome_seguro(nome)
    validar_arquivo(nome, conteudo, TIPOS_DOCUMENTO, TAMANHO_MAXIMO_MB.get(tipo_anexo, 10))
    with conn.cursor() as cursor:
        return _inserir_com_id(cursor, "CS_ANEXOS", {
            "TIPO_ENTIDADE": tipo_entidade, "ID_ENTIDADE": int(id_entidade), "TIPO_ANEXO": tipo_anexo,
            "NOME_ARQUIVO": nome, "TIPO_CONTEUDO": tipo_conteudo, "TAMANHO_BYTES": len(conteudo),
            "HASH_SHA256": hashlib.sha256(conteudo).hexdigest(), "ARQUIVO": conteudo,
            "USUARIO_ENVIO": (usuario or "")[:150] or None})


def listar_anexos(conn, tipo_entidade, ids_entidade):
    """{id_entidade: [anexos sem o conteúdo]} dos registros informados."""
    ids = [int(i) for i in ids_entidade if i is not None]
    if not ids:
        return {}
    resultado = {}
    with conn.cursor() as cursor:
        for inicio in range(0, len(ids), 900):
            lote = ids[inicio:inicio + 900]
            binds = {f"i{n}": v for n, v in enumerate(lote)}
            cursor.execute(f"""
                SELECT ID, ID_ENTIDADE, TIPO_ANEXO, NOME_ARQUIVO, TIPO_CONTEUDO, TAMANHO_BYTES, HASH_SHA256,
                       USUARIO_ENVIO, DATA_ENVIO
                FROM CS_ANEXOS WHERE TIPO_ENTIDADE = :tipo AND ID_ENTIDADE IN ({', '.join(':' + b for b in binds)})
                ORDER BY DATA_ENVIO DESC
            """, dict(binds, tipo=tipo_entidade))
            colunas = [c[0] for c in cursor.description]
            for linha in cursor.fetchall():
                anexo = dict(zip(colunas, linha))
                resultado.setdefault(anexo["ID_ENTIDADE"], []).append(anexo)
    return resultado


def conteudo_anexo(conn, id_anexo):
    """(nome, bytes, tipo) de um anexo, conferindo a integridade pelo SHA-256."""
    with conn.cursor() as cursor:
        cursor.execute("SELECT NOME_ARQUIVO, ARQUIVO, TIPO_CONTEUDO, HASH_SHA256 FROM CS_ANEXOS WHERE ID = :id",
                       {"id": int(id_anexo)})
        linha = cursor.fetchone()
    if not linha:
        return None
    nome, blob, tipo, hash_gravado = linha
    conteudo = blob.read() if hasattr(blob, "read") else bytes(blob)
    if hashlib.sha256(conteudo).hexdigest() != hash_gravado:
        raise AnexoInvalido(f"{nome}: o arquivo guardado não confere com o original (integridade violada).")
    return nome, conteudo, tipo


def nome_padrao_manual(fabricante, modelo):
    """'John Deere', 'CH950' -> 'manual_john_deere_ch950.pdf' (minúsculas, sem espaço nem acento)."""
    texto = f"manual {fabricante or ''} {modelo or ''}"
    texto = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", texto.lower()).strip("_") + ".pdf"


def copiar_manual_para_data_raw(conn, id_modelo, conteudo):
    """Grava uma cópia do PDF em data/raw com o nome padrão. Devolve o caminho, ou None se não foi possível."""
    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT FABRICANTE, MODELO FROM CS_EQUIPAMENTOS_MODELOS WHERE ID = :m", {"m": int(id_modelo)})
            linha = cursor.fetchone()
        if not linha:
            return None
        PASTA_MANUAIS.mkdir(parents=True, exist_ok=True)
        caminho = PASTA_MANUAIS / nome_padrao_manual(*linha)
        caminho.write_bytes(conteudo)
        return caminho
    except (OSError, oracledb.Error):
        return None


def salvar_manual(conn, id_modelo, nome, conteudo):
    """Grava o PDF do manual do modelo em CS_EQUIPAMENTOS_MANUAIS. Se o modelo já tem manual vinculado (com o código
    das recomendações, ex.: JD-CH950-OMCXT31163-B3), o PDF entra nesse registro; senão, cria um registro novo.
    Também grava uma cópia em data/raw (nome padrão, ex.: manual_john_deere_ch950.pdf) para a leitura dos manuais.
    Devolve o COD_FONTE_MANUAL."""
    nome = nome_seguro(nome)
    validar_arquivo(nome, conteudo, TIPOS_MANUAL, TAMANHO_MAXIMO_MB["MANUAL"])
    copiar_manual_para_data_raw(conn, id_modelo, conteudo)
    with conn.cursor() as cursor:
        cursor.execute("SELECT MIN(COD_FONTE_MANUAL) FROM CS_EQUIPAMENTOS_MANUAIS WHERE ID_EQUIPAMENTO = :m",
                       {"m": int(id_modelo)})
        existente = cursor.fetchone()[0]
        if existente:
            cursor.setinputsizes(arquivo=oracledb.DB_TYPE_BLOB)
            cursor.execute("""
                UPDATE CS_EQUIPAMENTOS_MANUAIS SET ARQUIVO_PDF = :arquivo, NOME_ARQUIVO = :nome, DATA_UPLOAD = SYSTIMESTAMP
                WHERE COD_FONTE_MANUAL = :codigo
            """, {"arquivo": conteudo, "nome": nome, "codigo": existente})
            return existente
        codigo = f"MAN-{int(id_modelo)}-{hashlib.sha256(conteudo).hexdigest()[:10].upper()}"
        _inserir_com_id(cursor, "CS_EQUIPAMENTOS_MANUAIS", {
            "ID_EQUIPAMENTO": int(id_modelo), "COD_FONTE_MANUAL": codigo, "NOME_ARQUIVO": nome,
            "ARQUIVO_PDF": conteudo})
        cursor.execute("UPDATE CS_EQUIPAMENTOS_MANUAIS SET DATA_UPLOAD = SYSTIMESTAMP WHERE COD_FONTE_MANUAL = :c",
                       {"c": codigo})
    return codigo


def listar_manuais(conn, ids_modelos):
    """{id_modelo: [manuais sem o conteúdo]}."""
    ids = [int(i) for i in ids_modelos if i is not None]
    if not ids:
        return {}
    binds = {f"m{n}": v for n, v in enumerate(ids[:900])}
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT ID, ID_EQUIPAMENTO, COD_FONTE_MANUAL, NOME_ARQUIVO, DATA_UPLOAD FROM CS_EQUIPAMENTOS_MANUAIS
            WHERE ID_EQUIPAMENTO IN ({', '.join(':' + b for b in binds)}) ORDER BY DATA_UPLOAD DESC
        """, binds)
        colunas = [c[0] for c in cursor.description]
        resultado = {}
        for linha in cursor.fetchall():
            manual = dict(zip(colunas, linha))
            resultado.setdefault(manual["ID_EQUIPAMENTO"], []).append(manual)
    return resultado


def conteudo_manual(conn, id_manual):
    with conn.cursor() as cursor:
        cursor.execute("SELECT NOME_ARQUIVO, ARQUIVO_PDF FROM CS_EQUIPAMENTOS_MANUAIS WHERE ID = :id",
                       {"id": int(id_manual)})
        linha = cursor.fetchone()
    if not linha or linha[1] is None:
        return None
    nome, blob = linha
    return nome, (blob.read() if hasattr(blob, "read") else bytes(blob)), "application/pdf"
