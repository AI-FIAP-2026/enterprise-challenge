-- =====================================================================
-- Consulta Preditiva Otimizada — Risco Hidrológico
-- =====================================================================

WITH eventos_hidrologicos AS (
    SELECT ID_EVENTO, CODIGO_IBGE, UF, INC_DATA_EVENTO, INICIO_ANALISE_CLIMATICA, FIM_ANALISE_CLIMATICA
    FROM RM568906.CS_EVENTOS
    WHERE INC_DATA_EVENTO IS NOT NULL
      AND (SUBSTR(COBRADE, 1, 3) IN ('122', '123', '132') 
           OR LOWER(INC_GRUPO_DESASTRE) LIKE '%inunda%' 
           OR LOWER(INC_GRUPO_DESASTRE) LIKE '%alagamento%' 
           OR LOWER(INC_GRUPO_DESASTRE) LIKE '%enxurrada%')
      AND INICIO_ANALISE_CLIMATICA IS NOT NULL
      AND FIM_ANALISE_CLIMATICA IS NOT NULL
      AND INC_DATA_EVENTO >= TO_DATE('2024-09-01', 'YYYY-MM-DD')
      AND INC_DATA_EVENTO < TO_DATE('2025-04-01', 'YYYY-MM-DD')
)
SELECT 
    c.DATA_HORA_BR,
    c.TEMPERATURA, 
    c.UMIDADE, 
    c.VELOCIDADE_VENTO, 
    c.PRECIPITACAO,
    c.ID_EVENTO,
    c.CODIGO_IBGE,
    c.UF,
    e.INC_DATA_EVENTO,
    CASE 
        WHEN TRUNC(c.DATA_HORA_BR) = TRUNC(e.INC_DATA_EVENTO) THEN 1 
        ELSE 0 
    END AS ALERTA_DESASTRE
FROM RM568906.CS_EVENTOS_CLIMA c
JOIN eventos_hidrologicos e ON c.ID_EVENTO = e.ID_EVENTO
WHERE c.DATA_HORA_BR >= e.INICIO_ANALISE_CLIMATICA
  AND c.DATA_HORA_BR <= e.FIM_ANALISE_CLIMATICA
  AND ROWNUM <= 100000;