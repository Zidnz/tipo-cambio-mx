"""Pipeline ETL del tipo de cambio FIX (pesos por dólar) publicado por Banxico.

Flujo:
    extract   -> descarga la serie SF43718 de la API SIE de Banxico
    transform -> limpia (valores N/E, tipos, duplicados) y valida
    merge     -> combina con el histórico guardado (lo nuevo reemplaza revisiones)
    metrics   -> calcula indicadores para el dashboard
    load      -> escribe CSV y JSON solo si los datos cambiaron

Uso:
    BANXICO_TOKEN=xxxx python src/pipeline.py
"""

from __future__ import annotations

import json
import math
import os
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests

SERIE_FIX = "SF43718"
API_URL = "https://www.banxico.org.mx/SieAPIRest/service/v1/series/{serie}/datos/{desde}/{hasta}"
INICIO_HISTORICO = date(2015, 1, 1)
DIAS_REVISION = 10  # se vuelven a pedir los últimos días por si Banxico corrige algún dato
TZ_MX = ZoneInfo("America/Mexico_City")

RAIZ = Path(__file__).resolve().parents[1]
RUTA_CSV = RAIZ / "docs" / "data" / "tipo_cambio.csv"
RUTA_JSON = RAIZ / "docs" / "data" / "resumen.json"

# Rango de valores plausibles para el FIX; fuera de él asumimos error de datos.
FIX_MIN, FIX_MAX = 5.0, 50.0


class DatosInvalidos(ValueError):
    """Los datos no pasaron las validaciones; no se escribe nada."""


# --------------------------------------------------------------------------- #
# Extract
# --------------------------------------------------------------------------- #
def extract(token: str, desde: date, hasta: date, serie: str = SERIE_FIX) -> list[dict]:
    """Devuelve la lista cruda de observaciones [{'fecha': 'dd/mm/aaaa', 'dato': '17.1234'}, ...]."""
    url = API_URL.format(serie=serie, desde=desde.isoformat(), hasta=hasta.isoformat())
    resp = requests.get(url, headers={"Bmx-Token": token, "Accept": "application/json"}, timeout=30)
    resp.raise_for_status()
    return parse_respuesta(resp.json())


def parse_respuesta(payload: dict) -> list[dict]:
    try:
        series = payload["bmx"]["series"]
    except (KeyError, TypeError) as exc:
        raise DatosInvalidos(f"Respuesta inesperada de Banxico: {str(payload)[:200]}") from exc
    if not series:
        return []
    return series[0].get("datos", []) or []


# --------------------------------------------------------------------------- #
# Transform
# --------------------------------------------------------------------------- #
def transform(crudos: list[dict]) -> pd.DataFrame:
    """Convierte las observaciones crudas en un DataFrame limpio (fecha, fix)."""
    if not crudos:
        return pd.DataFrame({"fecha": pd.Series(dtype="datetime64[ns]"), "fix": pd.Series(dtype="float64")})

    df = pd.DataFrame(crudos).rename(columns={"dato": "fix"})
    df["fecha"] = pd.to_datetime(df["fecha"], format="%d/%m/%Y", errors="coerce")
    # Banxico marca días sin dato como "N/E"; también pueden venir comas de miles.
    df["fix"] = pd.to_numeric(df["fix"].astype(str).str.replace(",", "", regex=False), errors="coerce")
    df = (
        df.dropna(subset=["fecha", "fix"])
        .drop_duplicates(subset="fecha", keep="last")
        .sort_values("fecha")
        .reset_index(drop=True)[["fecha", "fix"]]
    )
    validate(df)
    return df


def validate(df: pd.DataFrame, hoy: date | None = None) -> None:
    hoy = hoy or datetime.now(TZ_MX).date()
    if df.empty:
        return
    fuera = df[(df["fix"] < FIX_MIN) | (df["fix"] > FIX_MAX)]
    if not fuera.empty:
        raise DatosInvalidos(f"Valores fuera de rango [{FIX_MIN}, {FIX_MAX}]:\n{fuera.head()}")
    if df["fecha"].duplicated().any():
        raise DatosInvalidos("Fechas duplicadas en la serie")
    if df["fecha"].max().date() > hoy:
        raise DatosInvalidos(f"Hay fechas futuras: {df['fecha'].max().date()}")


def merge(historico: pd.DataFrame, nuevos: pd.DataFrame) -> pd.DataFrame:
    """Une histórico y datos nuevos; ante la misma fecha gana el dato nuevo (revisión)."""
    combinado = pd.concat([historico, nuevos], ignore_index=True)
    combinado = (
        combinado.drop_duplicates(subset="fecha", keep="last")
        .sort_values("fecha")
        .reset_index(drop=True)
    )
    validate(combinado)
    return combinado


# --------------------------------------------------------------------------- #
# Métricas para el dashboard
# --------------------------------------------------------------------------- #
def metrics(df: pd.DataFrame) -> dict:
    if df.empty:
        raise DatosInvalidos("No hay datos para calcular métricas")

    s = df.set_index("fecha")["fix"]
    ultimo, fecha = float(s.iloc[-1]), s.index[-1]
    previo = float(s.iloc[-2]) if len(s) > 1 else math.nan

    rend_log = (s / s.shift(1)).apply(math.log).dropna()
    vol_30 = float(rend_log.tail(30).std() * math.sqrt(252) * 100) if len(rend_log) >= 30 else None

    ult_52s = s[s.index > fecha - pd.Timedelta(days=365)]
    inicio_anio = s[s.index.year == fecha.year]
    base_anio = float(s[s.index.year < fecha.year].iloc[-1]) if (s.index.year < fecha.year).any() else float(inicio_anio.iloc[0])

    def redondea(x, n=4):
        return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(x, n)

    return {
        "serie": SERIE_FIX,
        "descripcion": "Tipo de cambio FIX, pesos por dólar (Banxico)",
        "fecha": fecha.date().isoformat(),
        "ultimo": redondea(ultimo),
        "variacion_diaria": redondea(ultimo - previo),
        "variacion_diaria_pct": redondea((ultimo / previo - 1) * 100, 3),
        "media_movil_20": redondea(float(s.tail(20).mean())),
        "volatilidad_30d_anualizada_pct": redondea(vol_30, 2),
        "min_52s": {"valor": redondea(float(ult_52s.min())), "fecha": ult_52s.idxmin().date().isoformat()},
        "max_52s": {"valor": redondea(float(ult_52s.max())), "fecha": ult_52s.idxmax().date().isoformat()},
        "variacion_anio_pct": redondea((ultimo / base_anio - 1) * 100, 2),
        "observaciones": int(len(s)),
        "desde": s.index[0].date().isoformat(),
        "actualizado": datetime.now(TZ_MX).isoformat(timespec="minutes"),
    }


# --------------------------------------------------------------------------- #
# Load
# --------------------------------------------------------------------------- #
def leer_historico(ruta: Path = RUTA_CSV) -> pd.DataFrame:
    if not ruta.exists():
        return pd.DataFrame({"fecha": pd.Series(dtype="datetime64[ns]"), "fix": pd.Series(dtype="float64")})
    return pd.read_csv(ruta, parse_dates=["fecha"])


def load(df: pd.DataFrame, resumen: dict, ruta_csv: Path = RUTA_CSV, ruta_json: Path = RUTA_JSON) -> bool:
    """Escribe CSV y JSON. Devuelve True si la serie cambió."""
    ruta_csv.parent.mkdir(parents=True, exist_ok=True)
    nuevo_csv = df.assign(fecha=df["fecha"].dt.strftime("%Y-%m-%d")).to_csv(index=False, float_format="%.4f")
    anterior = ruta_csv.read_text() if ruta_csv.exists() else None
    if nuevo_csv == anterior:
        return False
    ruta_csv.write_text(nuevo_csv)
    ruta_json.write_text(json.dumps(resumen, ensure_ascii=False, indent=2) + "\n")
    return True


def main() -> int:
    token = os.environ.get("BANXICO_TOKEN")
    if not token:
        print("Falta la variable de entorno BANXICO_TOKEN", file=sys.stderr)
        return 1

    historico = leer_historico()
    hoy = datetime.now(TZ_MX).date()
    desde = INICIO_HISTORICO if historico.empty else historico["fecha"].max().date() - timedelta(days=DIAS_REVISION)

    nuevos = transform(extract(token, desde, hoy))
    serie = merge(historico, nuevos)
    resumen = metrics(serie)
    cambio = load(serie, resumen)

    estado = "actualizado" if cambio else "sin cambios"
    print(f"{estado}: FIX {resumen['fecha']} = {resumen['ultimo']} ({resumen['observaciones']} observaciones)")

    # Expone datos al workflow de GitHub Actions para el mensaje del commit.
    salida = os.environ.get("GITHUB_OUTPUT")
    if salida:
        with open(salida, "a") as f:
            f.write(f"cambio={'true' if cambio else 'false'}\n")
            f.write(f"fecha={resumen['fecha']}\n")
            f.write(f"fix={resumen['ultimo']}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
