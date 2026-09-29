# -*- coding: utf-8 -*-
"""
Registro de ações (auditoria) em CS_LOG_ACOES: quem fez, quando e o quê.

Cada ação relevante grava uma linha nova (o histórico nunca é sobrescrito): envio de comprovante, comprovante a mais
(regra ideal), validação da Sompo, gravação das recomendações de um manual. A linha vai na mesma transação da ação
(sem commit aqui): se a ação falhar, o registro também não fica.

Registro de uso (registrar_avulso): login (com sucesso ou recusado), saída, acesso às páginas, execução do pipeline,
treino e gravação dos modelos, cálculo do score e gravações do cadastro. Usa uma conexão própria e grava na hora
(commit), e qualquer falha é ignorada: o registro nunca impede o uso do sistema.

Quem fez fica gravado como referência do usuário ("USR-5", requisitos/usuarios.py), sem nome nem e-mail.

Sem a tabela (sql/estrutura_banco.sql ainda não rodado), as funções não fazem nada, e a aplicação segue funcionando.

Chave: identifica o que foi alterado, para listar o histórico depois. Ex.: revisão = "equipamento|data|dias"
(ver chave_revisao).
"""

ENTIDADE_REVISAO = "REVISAO"
ENTIDADE_MANUAL = "MANUAL"
ENTIDADE_ACESSO = "ACESSO"
ACOES_USO = {
    "LOGIN": "Entrada no sistema", "LOGIN_RECUSADO": "Tentativa de entrada recusada", "LOGOUT": "Saída do sistema",
    "ACESSO_PAGINA": "Acesso à página", "PIPELINE": "Execução do pipeline", "TREINO_MODELO": "Treino de modelo",
    "GRAVACAO_MODELO": "Gravação de regras do modelo", "CALCULO_SCORE": "Cálculo do score",
    "CADASTRO": "Gravação no cadastro", "ENVIO_COMPROVANTE": "Envio de comprovante", "COMPROVANTE_A_MAIS":
    "Comprovante a mais (regra ideal)", "ACESSO_NEGADO": "Acesso negado à página", "VALIDACAO": "Validação da Sompo", "GRAVACAO_MANUAL": "Gravação das "
    "recomendações do manual", "LEITURA": "Leitura de horímetro/hodômetro",
    "AJUSTE_PREMIO": "Ajuste do prêmio (subscrição)",
}
_TABELA_EXISTE = {}


def tabela_existe(conn):
    """A tabela CS_LOG_ACOES existe? (consulta uma vez por conexão)"""
    chave = id(conn)
    if chave not in _TABELA_EXISTE:
        with conn.cursor() as cursor:
            cursor.execute("SELECT TABLE_NAME FROM USER_TABLES WHERE TABLE_NAME = 'CS_LOG_ACOES'")
            _TABELA_EXISTE[chave] = bool(cursor.fetchall())
    return _TABELA_EXISTE[chave]


def chave_revisao(id_equipamento, data_prevista, dias):
    """Chave de uma revisão do cronograma: equipamento, data prevista e intervalo em dias da periodicidade."""
    return f"{int(id_equipamento)}|{data_prevista:%Y-%m-%d}|{int(round(dias or 0))}"


def registrar(conn, acao, entidade=None, chave=None, detalhe=None, usuario=None, perfil=None, pagina=None):
    """Grava uma linha em CS_LOG_ACOES (sem commit). Devolve True se gravou."""
    if not tabela_existe(conn):
        return False
    with conn.cursor() as cursor:
        cursor.execute("""
            INSERT INTO CS_LOG_ACOES (USUARIO, PERFIL, PAGINA, ACAO, ENTIDADE, CHAVE, DETALHE)
            VALUES (:usuario, :perfil, :pagina, :acao, :entidade, :chave, :detalhe)
        """, {"usuario": (usuario or "")[:150] or None, "perfil": (perfil or "")[:30] or None,
              "pagina": (pagina or "")[:50] or None, "acao": acao[:50], "entidade": (entidade or "")[:50] or None,
              "chave": (chave or "")[:200] or None, "detalhe": (detalhe or "")[:1000] or None})
    return True


def historico(conn, entidade, chave):
    """Ações registradas para a entidade e chave, da mais recente para a mais antiga."""
    if not tabela_existe(conn):
        return []
    with conn.cursor() as cursor:
        cursor.execute("""
            SELECT DATA_HORA, USUARIO, PERFIL, ACAO, DETALHE FROM CS_LOG_ACOES
            WHERE ENTIDADE = :entidade AND CHAVE = :chave ORDER BY DATA_HORA DESC, ID DESC
        """, {"entidade": entidade, "chave": chave})
        colunas = [c[0] for c in cursor.description]
        return [dict(zip(colunas, linha)) for linha in cursor.fetchall()]


def registrar_avulso(acao, entidade=None, chave=None, detalhe=None, usuario=None, perfil=None, pagina=None):
    """Registro de uso fora de uma transação: abre a conexão, grava, faz commit e fecha. Nunca levanta erro."""
    try:
        import oracledb
        from auth import USER, PASSWORD, DSN
        conn = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
    except Exception:
        return False
    try:
        gravou = registrar(conn, acao, entidade, chave, detalhe, usuario, perfil, pagina)
        if gravou:
            conn.commit()
        return gravou
    except Exception:
        return False
    finally:
        _TABELA_EXISTE.pop(id(conn), None)       # o id da conexão pode ser reaproveitado por outra depois
        try:
            conn.close()
        except Exception:
            pass


def uso(conn, inicio, fim, acoes=None, limite=5000):
    """Linhas de CS_LOG_ACOES entre inicio e fim (datetime), da mais recente para a mais antiga."""
    if not tabela_existe(conn):
        return []
    filtro, parametros = "", {"inicio": inicio, "fim": fim}
    if acoes:
        nomes = [f":a{i}" for i in range(len(acoes))]
        filtro = f" AND ACAO IN ({', '.join(nomes)})"
        parametros.update({f"a{i}": a for i, a in enumerate(acoes)})
    with conn.cursor() as cursor:
        cursor.execute(f"""
            SELECT DATA_HORA, USUARIO, PERFIL, PAGINA, ACAO, ENTIDADE, CHAVE, DETALHE FROM CS_LOG_ACOES
            WHERE DATA_HORA >= :inicio AND DATA_HORA < :fim{filtro}
            ORDER BY DATA_HORA DESC, ID DESC
        """, parametros)
        colunas = [c[0] for c in cursor.description]
        return [dict(zip(colunas, linha)) for linha in cursor.fetchall()[:int(limite)]]
