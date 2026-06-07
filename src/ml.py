"""ML.py - Entrypoint CLI para o pipeline de ML.

Substitui a versao antiga da Nadia (que dependia de Prophet e tinha score
hardcoded). Agora orquestra treino, avaliacao e inferencia via o pacote
`ml_model`.

Uso:
    python -m ml.py train                          # classificador houve_evento
    python -m ml.py evaluate
    python -m ml.py all
    python -m ml.py predict SP "Sao Paulo" 2025-06-04

    python -m ml.py impacto train                  # regressor impacto_total (R$)
    python -m ml.py impacto evaluate
    python -m ml.py impacto predict SP "Sao Paulo"
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

import pandas as pd

SRC_DIR = Path(__file__).resolve().parent
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from ml_model.config import BEST_MODEL_PATH
from ml_model.data import load_dataset
from ml_model.evaluate import (
    build_correlation_report,
    build_model_comparison,
    evaluate_model,
    top_correlations_with_label,
)
from ml_model.predict import predict_impacto_range, predict_score
from ml_model.train import load_best_model, train_and_save_best
from ml_model.train_impacto import (
    load_best_impacto_model,
    train_and_save_best_impacto,
)


def cmd_train(_args) -> int:
    print(f"=== TRAIN (classificador) ===")
    print(f"Dataset: {load_dataset.__name__} -> Oracle")
    summary = train_and_save_best()
    print(f"\nVencedor: {summary['winner']}")
    for k, v in summary["metrics"].items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")
    return 0


def cmd_evaluate(_args) -> int:
    print(f"=== EVALUATE (classificador) ===")
    df = load_dataset()

    print("\n[1/2] Relatorio de correlacao")
    corr = build_correlation_report(df)
    top = top_correlations_with_label(corr, top_k=10)
    print("Top-10 features mais correlacionadas com 'houve_evento':")
    print(top.to_string(index=False))

    print("\n[2/2] Avaliacao do modelo vencedor")
    if not BEST_MODEL_PATH.exists():
        print(f"Modelo nao encontrado em {BEST_MODEL_PATH}. Rode: python -m ml.py train")
        return 1
    bundle = load_best_model()
    metrics = evaluate_model(bundle, df)
    print(f"\nMetricas: {metrics}")
    return 0


def cmd_predict(args) -> int:
    print(f"=== PREDICT (classificador) ===")
    try:
        data = date.fromisoformat(args.data) if args.data else None
    except ValueError:
        print(f"Data invalida: {args.data}. Use YYYY-MM-DD.")
        return 1
    res = predict_score(args.uf, args.municipio, data)
    print(f"UF={args.uf}  Municipio={args.municipio}  Data={data or 'hoje'}")
    print(f"  Score 0-100 : {res['score']}")
    print(f"  Rotulo      : {res['rotulo']}")
    print(f"  P(evento)   : {res['probabilidade']:.4f}")
    return 0


def cmd_all(_args) -> int:
    cmd_train(_args)
    print()
    return cmd_evaluate(_args)


def cmd_impacto_train(_args) -> int:
    print(f"=== TRAIN (regressor de impacto R$) ===")
    summary = train_and_save_best_impacto()
    print(f"\nVencedor: {summary['winner']}")
    for k, v in summary["metrics"].items():
        if isinstance(v, float):
            print(f"  {k}: {v:.4f}")
        else:
            print(f"  {k}: {v}")
    return 0


def cmd_impacto_evaluate(_args) -> int:
    print(f"=== EVALUATE (regressor de impacto) ===")
    from ml_model.config import BEST_IMPACTO_MODEL_PATH
    if not BEST_IMPACTO_MODEL_PATH.exists():
        print(f"Modelo nao encontrado em {BEST_IMPACTO_MODEL_PATH}. Rode: python -m ml.py impacto train")
        return 1
    bundle = load_best_impacto_model()
    print(f"Modelo vencedor: {bundle.get('model_name', '?')}")
    print(f"Metricas de treino: {bundle.get('metrics', {})}")
    return 0


def cmd_impacto_predict(args) -> int:
    print(f"=== PREDICT (regressor de impacto) ===")
    df = predict_impacto_range(args.uf, args.municipio, days=args.days)
    if df.empty:
        print(f"Sem projecao para {args.uf}/{args.municipio} (sem fazenda, Open-Meteo offline, ou modelo ausente).")
        return 1
    print(f"UF={args.uf}  Municipio={args.municipio}  Horizonte={args.days} dias")
    print(f"  Fazenda: {df['fazenda'].iloc[0]}  Cultura: {df['cultura'].iloc[0]}")
    for _, row in df.iterrows():
        ts = pd.Timestamp(row['date']).strftime('%d/%m/%Y') if hasattr(row['date'], 'strftime') else str(row['date'])
        print(
            f"  {ts}  impacto_previsto=R$ {row['impacto_total_previsto']:>12,.0f}  "
            f"temp_max={row['temp_max']:.0f}C  umi_min={row['umi_min']:.0f}%"
        )
    return 0


def cmd_impacto(_args) -> int:
    parser = argparse.ArgumentParser(prog="ml.py impacto", description="Regressor de IMPACTO_TOTAL (R$)")
    sub = parser.add_subparsers(dest="sub")
    sub.add_parser("train", help="Treina Tweedie/Forest/Ridge e salva vencedor")
    sub.add_parser("evaluate", help="Mostra metricas do regressor vencedor")
    p_pred = sub.add_parser("predict", help="Prediz impacto diario para (uf, municipio)")
    p_pred.add_argument("uf", help="Sigla da UF (ex: SP)")
    p_pred.add_argument("municipio", help="Nome do municipio")
    p_pred.add_argument("--days", type=int, default=14, help="Horizonte em dias (max 14)")
    a = parser.parse_args(_args.sub_args)
    if a.sub == "train":
        return cmd_impacto_train(a)
    if a.sub == "evaluate":
        return cmd_impacto_evaluate(a)
    if a.sub == "predict":
        return cmd_impacto_predict(a)
    parser.print_help()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Campo Seguro - pipeline ML")
    sub = parser.add_subparsers(dest="cmd")

    sub.add_parser("train", help="Treina classificador (4 modelos) e salva vencedor")
    sub.add_parser("evaluate", help="Avalia classificador vencedor + gera relatorios")
    sub.add_parser("all", help="train + evaluate do classificador")

    p_pred = sub.add_parser("predict", help="Prediz score para (uf, municipio, data)")
    p_pred.add_argument("uf", help="Sigla da UF (ex: SP)")
    p_pred.add_argument("municipio", help="Nome do municipio")
    p_pred.add_argument("data", nargs="?", help="Data no formato YYYY-MM-DD (opcional)")

    p_imp = sub.add_parser("impacto", help="Pipeline do regressor de IMPACTO_TOTAL (R$)")
    p_imp.add_argument(
        "sub_args", nargs=argparse.REMAINDER,
        help="Sub-comando: train | evaluate | predict UF MUNICIPIO [--days 14]",
    )

    args = parser.parse_args(argv)
    if args.cmd == "train":
        return cmd_train(args)
    if args.cmd == "evaluate":
        return cmd_evaluate(args)
    if args.cmd == "all":
        return cmd_all(args)
    if args.cmd == "predict":
        return cmd_predict(args)
    if args.cmd == "impacto":
        return cmd_impacto(args)
    parser.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
