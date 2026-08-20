import oracledb
from oracle_config import USER, PASSWORD, DSN

def inserir_cliente_sompo():
    try:
        connection = oracledb.connect(user=USER, password=PASSWORD, dsn=DSN)
        cursor = connection.cursor()

        # Inserindo a Sompo Seguradora na tabela CS_CLIENTES
        # CNPJ fictício/exemplo (substitua pelo oficial se necessário)
        sql = """
        INSERT INTO CS_CLIENTES (RAZAO_SOCIAL, CNPJ, FIDELIDADE)
        VALUES (:razao_social, :cnpj, :fidelidade)
        """
        
        dados = {
            "razao_social": "Sompo Seguradora S.A.",
            "cnpj": "61384782000124", # Exemplo de CNPJ da Sompo (apenas números)
            "fidelidade": "Não se aplica"
        }

        cursor.execute(sql, dados)
        connection.commit()
        print("Cliente inserido com sucesso!")
        
        cursor.close()
        connection.close()
    except oracledb.DatabaseError as e:
        error, = e.args
        if error.code == 1: # Erro ORA-00001: restrição exclusiva violada (CNPJ duplicado)
            print("Aviso: Este CNPJ já está cadastrado na tabela CS_CLIENTES.")
        else:
            print(f"Erro ao inserir cliente: {e}")

if __name__ == "__main__":
    inserir_cliente_sompo()