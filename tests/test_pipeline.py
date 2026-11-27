import sys
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
import pipeline as p  # noqa: E402

RESPUESTA = {
    "bmx": {
        "series": [
            {
                "idSerie": "SF43718",
                "titulo": "Tipo de cambio pesos por dólar E.U.A. FIX",
                "datos": [
                    {"fecha": "02/01/2025", "dato": "20.6640"},
                    {"fecha": "03/01/2025", "dato": "20.5122"},
                    {"fecha": "04/01/2025", "dato": "N/E"},
                    {"fecha": "06/01/2025", "dato": "20.4313"},
                    {"fecha": "06/01/2025", "dato": "20.4400"},
                ],
            }
        ]
    }
}


def test_parse_y_transform_limpia_ne_y_duplicados():
    df = p.transform(p.parse_respuesta(RESPUESTA))
    assert list(df["fecha"].dt.date) == [date(2025, 1, 2), date(2025, 1, 3), date(2025, 1, 6)]
    # ante fecha duplicada se queda el último valor
    assert df["fix"].iloc[-1] == pytest.approx(20.44)


def test_respuesta_malformada():
    with pytest.raises(p.DatosInvalidos):
        p.parse_respuesta({"error": "token inválido"})


def test_valor_fuera_de_rango():
    with pytest.raises(p.DatosInvalidos):
        p.transform([{"fecha": "02/01/2025", "dato": "2064.0"}])


def test_merge_reemplaza_revisiones():
    hist = pd.DataFrame({"fecha": pd.to_datetime(["2025-01-02", "2025-01-03"]), "fix": [20.0, 20.1]})
    nuevos = pd.DataFrame({"fecha": pd.to_datetime(["2025-01-03", "2025-01-06"]), "fix": [20.2, 20.3]})
    out = p.merge(hist, nuevos)
    assert out["fix"].tolist() == [20.0, 20.2, 20.3]


def test_metrics_y_load_idempotente(tmp_path):
    fechas = pd.bdate_range("2024-01-01", "2025-03-31")
    df = pd.DataFrame({"fecha": fechas, "fix": [17 + (i % 50) / 25 for i in range(len(fechas))]})
    res = p.metrics(df)
    assert res["fecha"] == "2025-03-31"
    assert res["volatilidad_30d_anualizada_pct"] is not None
    assert res["min_52s"]["valor"] <= res["ultimo"] <= res["max_52s"]["valor"]

    csv, js = tmp_path / "d.csv", tmp_path / "r.json"
    assert p.load(df, res, csv, js) is True
    assert p.load(df, res, csv, js) is False  # sin cambios -> no hay commit
