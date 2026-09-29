# -*- coding: utf-8 -*-
import sys
import time

# Primeira mensagem antes de qualquer import pesado: se ela não aparecer, o Python nem começou.
print(f"[{time.strftime('%H:%M:%S')}] Pipeline iniciado. Carregando bibliotecas e conexão com o Oracle...", flush=True)

import os
import threading
import importlib
import logging
import oracledb
from auth import USER, PASSWORD, DSN

# --- AJUSTE DE DIRETÓRIO PARA O PIPELINE E EXECUÇÃO ISOLADA ---
current_dir = os.path.dirname(os.path.abspath(__file__))
if current_dir not in sys.path:
    sys.path.append(current_dir)
# -------------------------------------------------------------

# ==========================================
# MONITOR DE ATIVIDADE
# ==========================================
# Tudo o que os serviços escrevem na tela (print ou logging) conta como "atividade".
# Um vigia em segundo plano avisa quando o serviço fica muito tempo em silêncio.
INTERVALO_AVISO_SEG = 60          # sem mensagens há 1 min -> "ainda trabalhando..."
LIMITE_TRAVAMENTO_SEG = 10 * 60   # sem mensagens há 10 min -> "possível travamento"


class MonitorAtividade:
    def __init__(self):
        self.ultima_atividade = time.time()
        self.modulo_atual = None
        self.inicio_modulo = None
        self.ultima_mensagem = ""
        self._ativo = False

    def registrar(self, texto):
        texto = texto.strip()
        if texto:
            self.ultima_atividade = time.time()
            self.ultima_mensagem = texto[:120]

    def iniciar_modulo(self, nome):
        self.modulo_atual = nome
        self.inicio_modulo = time.time()
        self.ultima_atividade = time.time()

    def finalizar_modulo(self):
        self.modulo_atual = None

    def iniciar_vigia(self):
        self._ativo = True
        threading.Thread(target=self._vigiar, daemon=True).start()

    def parar_vigia(self):
        self._ativo = False

    def _vigiar(self):
        proximo_aviso = INTERVALO_AVISO_SEG
        while self._ativo:
            time.sleep(5)
            if not self.modulo_atual:
                proximo_aviso = INTERVALO_AVISO_SEG
                continue
            silencio = time.time() - self.ultima_atividade
            if silencio < INTERVALO_AVISO_SEG:
                proximo_aviso = INTERVALO_AVISO_SEG
                continue
            if silencio < proximo_aviso:
                continue
            proximo_aviso += INTERVALO_AVISO_SEG

            # O próprio aviso não conta como atividade nem substitui a última mensagem do serviço
            ultima_atividade, ultima_mensagem = self.ultima_atividade, self.ultima_mensagem
            decorrido = formatar_duracao(time.time() - self.inicio_modulo)
            if silencio >= LIMITE_TRAVAMENTO_SEG:
                logging.warning(
                    f"   ⚠️  POSSÍVEL TRAVAMENTO: '{self.modulo_atual}' está sem nenhuma mensagem há "
                    f"{formatar_duracao(silencio)} (rodando há {decorrido}). Última mensagem: \"{self.ultima_mensagem}\". "
                    f"Se continuar assim, pare com Ctrl+C: a próxima execução retoma de onde parou.")
            else:
                logging.info(
                    f"   ⏳ Ainda trabalhando em '{self.modulo_atual}' (rodando há {decorrido}; sem mensagens novas há "
                    f"{formatar_duracao(silencio)} — normal durante downloads e gravações grandes). Não precisa parar.")
            self.ultima_atividade, self.ultima_mensagem = ultima_atividade, ultima_mensagem


class SaidaMonitorada:
    """Repassa tudo para a tela original e avisa o monitor a cada escrita."""

    def __init__(self, destino, monitor):
        self.destino = destino
        self.monitor = monitor

    def write(self, texto):
        self.monitor.registrar(texto)
        return self.destino.write(texto)

    def flush(self):
        return self.destino.flush()

    def __getattr__(self, nome):
        return getattr(self.destino, nome)


def formatar_duracao(segundos):
    segundos = int(segundos)
    if segundos < 60:
        return f"{segundos}s"
    minutos, seg = divmod(segundos, 60)
    if minutos < 60:
        return f"{minutos}min{seg:02d}s"
    horas, minutos = divmod(minutos, 60)
    return f"{horas}h{minutos:02d}min"


monitor = MonitorAtividade()
sys.stdout = SaidaMonitorada(sys.stdout, monitor)

# ==========================================
# CONFIGURAÇÃO DO MÓDULO NATIVO DE LOGGING
# ==========================================
os.makedirs(os.path.join(current_dir, 'logs'), exist_ok=True)
caminho_arquivo_log = os.path.join(current_dir, 'logs', 'pipeline.log')

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(caminho_arquivo_log, encoding='utf-8'),
        logging.StreamHandler(sys.stdout)
    ]
)

def registrar_log_pipeline(exec_id, script_nome, tentativa, status, mensagem_retorno, qtd_registros):
    """Grava o registro de auditoria na tabela CS_PIPELINE_LOGS do Oracle. Retorna True se gravou."""
    try:
        msg_limpa = str(mensagem_retorno) if mensagem_retorno is not None else ""

        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        sql = """
            INSERT INTO CS_PIPELINE_LOGS
            (PIPELINE_EXEC_ID, SCRIPT_NOME, TENTATIVA, STATUS, MENSAGEM_RETORNO, REGISTROS_PROCESSADOS)
            VALUES (:1, :2, :3, :4, :5, :6)
        """
        # MENSAGEM_RETORNO é CLOB: declarar o tipo evita erro com mensagens acima de 4000 bytes (acentos contam em dobro)
        cursor.setinputsizes(None, None, None, None, oracledb.DB_TYPE_CLOB, None)
        cursor.execute(sql, [exec_id, script_nome, tentativa, str(status)[:20], msg_limpa, int(qtd_registros or 0)])
        connection.commit()
        cursor.close()
        connection.close()
        return True
    except Exception as e:
        logging.error(f"[ERRO DE LOG ORACLE] Falha ao gravar auditoria no banco para '{script_nome}': {e}")
        return False

def testar_conexao_oracle():
    """Confirma logo no início que o Oracle responde, em vez de descobrir só no meio do pipeline."""
    logging.info("Testando conexão com o Oracle...")
    inicio = time.time()
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN, tcp_connect_timeout=20)
        connection.close()
        logging.info(f"   -> Oracle OK ({formatar_duracao(time.time() - inicio)}).")
        return True
    except Exception as e:
        logging.error(f"   -> Não foi possível conectar ao Oracle: {e}")
        return False

def job_pipeline():
    """Varre dinamicamente a pasta 'servicos' e executa a função 'executar' de cada módulo."""
    exec_id = "EXEC_" + os.urandom(4).hex().upper()
    inicio_pipeline = time.time()
    logging.info(f"==================================================")
    logging.info(f"--- INICIANDO PIPELINE DINÂMICO [{exec_id}] ---")
    logging.info(f"==================================================")
    logging.info(f"Log completo em: {caminho_arquivo_log}")
    logging.info(f"Acompanhamento: aviso a cada {INTERVALO_AVISO_SEG}s sem mensagens; "
                 f"alerta de possível travamento após {LIMITE_TRAVAMENTO_SEG // 60} min sem mensagens.")

    if not testar_conexao_oracle():
        logging.error("Pipeline cancelado: sem conexão com o Oracle.")
        return

    pasta_servicos = os.path.join(current_dir, 'servicos')

    if not os.path.exists(pasta_servicos):
        logging.error(f"A pasta '{pasta_servicos}' não foi encontrada.")
        return

    arquivos = [f[:-3] for f in os.listdir(pasta_servicos) if f.endswith('.py') and not f.startswith('__')]

    total_scripts = len(arquivos)
    logging.info(f"Encontrados {total_scripts} módulo(s) na pasta 'servicos': {', '.join(sorted(arquivos))}")

    resumo = []
    monitor.iniciar_vigia()
    try:
        for index, nome_modulo in enumerate(sorted(arquivos), start=1):
            script_nome_arquivo = f"{nome_modulo}.py"
            logging.info(f"--------------------------------------------------")
            logging.info(f"[{index}/{total_scripts}] ▶ Iniciando módulo: {script_nome_arquivo}")
            monitor.iniciar_modulo(script_nome_arquivo)
            inicio_modulo = time.time()
            status = "ERRO"

            try:
                # Importa o script dinamicamente da pasta servicos
                modulo = importlib.import_module(f"servicos.{nome_modulo}")

                # Como padronizamos todos os scripts para usarem 'executar', a verificação fica limpa:
                if hasattr(modulo, 'executar'):
                    status, mensagem, qtd = modulo.executar()
                    if registrar_log_pipeline(exec_id, script_nome_arquivo, 1, status, mensagem, qtd):
                        logging.info(f"   -> Log registrado para '{script_nome_arquivo}' [Status: {status} | Registros: {qtd}]")
                    else:
                        logging.error(f"   -> '{script_nome_arquivo}' executou [Status: {status}], mas o log NÃO foi gravado no Oracle (veja o erro acima).")
                else:
                    status = "ALERTA"
                    msg_aviso = "A função padrão 'executar' não foi encontrada no módulo."
                    registrar_log_pipeline(exec_id, script_nome_arquivo, 1, "ALERTA", msg_aviso, 0)
                    logging.warning(f"   -> [AVISO] {msg_aviso}")

            except KeyboardInterrupt:
                # Execução interrompida manualmente (Ctrl+C): registra antes de encerrar
                registrar_log_pipeline(exec_id, script_nome_arquivo, 1, "INTERROMPIDO", "Execução interrompida manualmente (Ctrl+C).", 0)
                logging.warning(f"   -> Execução de '{script_nome_arquivo}' interrompida manualmente. Pipeline encerrado.")
                raise
            except Exception as e:
                msg_erro = f"Exceção não tratada: {str(e)}"
                registrar_log_pipeline(exec_id, script_nome_arquivo, 1, "ERRO", msg_erro, 0)
                logging.error(f"   -> Falha crítica registrada para '{script_nome_arquivo}': {e}")
            finally:
                monitor.finalizar_modulo()

            duracao = formatar_duracao(time.time() - inicio_modulo)
            resumo.append((script_nome_arquivo, status, duracao))
            logging.info(f"[{index}/{total_scripts}] ✔ {script_nome_arquivo} finalizado em {duracao} [Status: {status}]")
    finally:
        monitor.parar_vigia()

    logging.info(f"==================================================")
    logging.info(f"--- PIPELINE DINÂMICO FINALIZADO [{exec_id}] em {formatar_duracao(time.time() - inicio_pipeline)} ---")
    for nome, status, duracao in resumo:
        logging.info(f"   {status:<12} {duracao:>10}   {nome}")
    logging.info(f"==================================================\n")

if __name__ == "__main__":
    job_pipeline()
