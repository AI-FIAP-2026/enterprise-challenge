# -*- coding: utf-8 -*-
import sys
import os
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
    """Grava o registro de auditoria na tabela CS_PIPELINE_LOGS do Oracle convertendo LOBs/textos com segurança."""
    try:
        msg_limpa = str(mensagem_retorno) if mensagem_retorno is not None else ""
        
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()
        
        sql = """
            INSERT INTO CS_PIPELINE_LOGS 
            (PIPELINE_EXEC_ID, SCRIPT_NOME, TENTATIVA, STATUS, MENSAGEM_RETORNO, REGISTROS_PROCESSADOS)
            VALUES (:1, :2, :3, :4, :5, :6)
        """
        cursor.execute(sql, [exec_id, script_nome, tentativa, status, msg_limpa, int(qtd_registros)])
        connection.commit()
        cursor.close()
        connection.close()
    except Exception as e:
        logging.error(f"[ERRO DE LOG ORACLE] Falha ao gravar auditoria no banco: {e}")

def job_pipeline():
    """Varre dinamicamente a pasta 'servicos' e executa qualquer script que possua uma função de sincronização."""
    exec_id = "EXEC_" + os.urandom(4).hex().upper()
    logging.info(f"==================================================")
    logging.info(f"--- INICIANDO PIPELINE DINÂMICO [{exec_id}] ---")
    logging.info(f"==================================================")
    
    pasta_servicos = os.path.join(current_dir, 'servicos')
    
    if not os.path.exists(pasta_servicos):
        logging.error(f"A pasta '{pasta_servicos}' não foi encontrada.")
        return

    arquivos = [f[:-3] for f in os.listdir(pasta_servicos) if f.endswith('.py') and not f.startswith('__')]
    
    total_scripts = len(arquivos)
    logging.info(f"Encontrados {total_scripts} módulo(s) na pasta 'servicos'. Iniciando execução...")

    for index, nome_modulo in enumerate(sorted(arquivos), start=1):
        script_nome_arquivo = f"{nome_modulo}.py"
        logging.info(f"[{index}/{total_scripts}] Executando módulo: {script_nome_arquivo}")
        
        try:
            modulo = importlib.import_module(f"servicos.{nome_modulo}")
            
            funcao_execucao = None
            for nome_func in ['sincronizar_escalavel_cemaden', 'atualizar_dados_clima_fazendas', 'sincronizar_focos_queimadas', 'executar', 'sincronizar']:
                if hasattr(modulo, nome_func):
                    funcao_execucao = getattr(modulo, nome_func)
                    break
            
            if funcao_execucao:
                status, mensagem, qtd = funcao_execucao()
                registrar_log_pipeline(exec_id, script_nome_arquivo, 1, status, mensagem, qtd)
                logging.info(f"   -> Sucesso! Log registrado para '{script_nome_arquivo}' [Status: {status} | Registros: {qtd}]")
            else:
                msg_aviso = "Nenhuma função de execução padrão encontrada no módulo."
                registrar_log_pipeline(exec_id, script_nome_arquivo, 1, "ALERTA", msg_aviso, 0)
                logging.warning(f"   -> [AVISO] {msg_aviso}")

        except Exception as e:
            msg_erro = f"Exceção não tratada: {str(e)}"
            registrar_log_pipeline(exec_id, script_nome_arquivo, 1, "ERRO", msg_erro, 0)
            logging.error(f"   -> Falha crítica registrada para '{script_nome_arquivo}': {e}")

    logging.info(f"==================================================")
    logging.info(f"--- PIPELINE DINÂMICO FINALIZADO [{exec_id}] ---")
    logging.info(f"==================================================\n")

if __name__ == "__main__":
    job_pipeline()