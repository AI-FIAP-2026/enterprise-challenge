-- =====================================================================
-- Consulta Preditiva Otimizada - Foco em Incêndios (Ano de 2025)
-- =====================================================================

WITH municipios_queimadas AS (
    SELECT DISTINCT CODIGO_IBGE, UF, INC_DATA_EVENTO
    FROM RM568906.CS_EVENTOS
    WHERE INC_DATA_EVENTO IS NOT NULL
      AND SUBSTR(COBRADE, 1, 5) IN ('14131', '14132')
      AND INC_DATA_EVENTO >= TO_DATE('2025-01-01', 'YYYY-MM-DD')
      AND INC_DATA_EVENTO < TO_DATE('2026-01-01', 'YYYY-MM-DD')
)
SELECT 
    c.DATA_HORA_BR,
    c.TEMPERATURA, 
    c.UMIDADE, 
    c.VELOCIDADE_VENTO, 
    c.PRECIPITACAO,
    e.ID_EVENTO,
    c.CODIGO_IBGE,
    c.UF,
    e.COBRADE,
    e.INC_GRUPO_DESASTRE,
    e.INC_PROTOCOLO,
    e.INC_DATA_EVENTO,
    CASE 
        WHEN mi.INC_DATA_EVENTO IS NOT NULL 
             AND TRUNC(c.DATA_HORA_BR) = TRUNC(mi.INC_DATA_EVENTO) THEN 1 
        ELSE 0 
    END AS ALERTA_DESASTRE
FROM RM568906.CS_EVENTOS_CLIMA c
LEFT JOIN RM568906.CS_EVENTOS e ON c.ID_EVENTO = e.ID_EVENTO
LEFT JOIN municipios_queimadas mi ON c.CODIGO_IBGE = mi.CODIGO_IBGE
WHERE c.DATA_HORA_BR >= TO_DATE('2025-01-01', 'YYYY-MM-DD')
  AND c.DATA_HORA_BR < TO_DATE('2026-01-01', 'YYYY-MM-DD')
  AND EXISTS (
      SELECT 1 FROM municipios_queimadas m2 
      WHERE m2.CODIGO_IBGE = c.CODIGO_IBGE 
        AND c.DATA_HORA_BR >= (m2.INC_DATA_EVENTO - 90)
        AND c.DATA_HORA_BR <= m2.INC_DATA_EVENTO
  )
  AND ROWNUM <= 75000;