import os
import sys
import json
from datetime import datetime
import pytz
import requests
import pandas as pd

ESIOS_URL = "https://api.esios.ree.es/indicators/1001"
GEO_ID_PENINSULA = 8741
TZ_MADRID = pytz.timezone("Europe/Madrid")


def obtener_anio_objetivo() -> int:
    """Devuelve el año pasado por variable de entorno o, por defecto, el año anterior al actual."""
    env_year = os.getenv("TARGET_YEAR", "").strip()
    if env_year.isdigit():
        return int(env_year)
    return datetime.now(TZ_MADRID).year - 1


def descargar_pvpc_anual(token: str, year: int) -> pd.DataFrame:
    """Consulta el indicador 1001 de ESIOS en bloques trimestrales para evitar timeouts o truncados."""
    headers = {
        "Accept": "application/json; application/vnd.esios-api-v1+json",
        "Content-Type": "application/json",
        "x-api-key": token,
    }

    # Tramos en hora local peninsular convertidos a UTC para no perder ni duplicar horas en cambios DST
    tramos_locales = [
        (f"{year}-01-01 00:00:00", f"{year}-03-31 23:59:59"),
        (f"{year}-04-01 00:00:00", f"{year}-06-30 23:59:59"),
        (f"{year}-07-01 00:00:00", f"{year}-09-30 23:59:59"),
        (f"{year}-10-01 00:00:00", f"{year}-12-31 23:59:59"),
    ]

    registros = []

    for inicio_str, fin_str in tramos_locales:
        dt_inicio = TZ_MADRID.localize(datetime.strptime(inicio_str, "%Y-%m-%d %H:%M:%S")).astimezone(pytz.UTC)
        dt_fin = TZ_MADRID.localize(datetime.strptime(fin_str, "%Y-%m-%d %H:%M:%S")).astimezone(pytz.UTC)

        params = {
            "start_date": dt_inicio.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end_date": dt_fin.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "time_trunc": "hour",
        }

        resp = requests.get(ESIOS_URL, headers=headers, params=params, timeout=45)
        resp.raise_for_status()

        values = resp.json().get("indicator", {}).get("values", [])
        for v in values:
            if v.get("geo_id") == GEO_ID_PENINSULA:
                registros.append({
                    "datetime_utc": v["datetime"],
                    "precio_eur_kwh": round(float(v["value"]) / 1000.0, 6),
                })

    if not registros:
        raise RuntimeError(f"ESIOS devolvió 0 registros para Península en {year}.")

    df = pd.DataFrame(registros)
    df["datetime"] = pd.to_datetime(df["datetime_utc"], utc=True).dt.tz_convert("Europe/Madrid")
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)

    # Columnas auxiliares útiles para análisis posteriores
    df["fecha"] = df["datetime"].dt.strftime("%Y-%m-%d")
    df["hora"] = df["datetime"].dt.hour
    df["mes"] = df["datetime"].dt.month
    df["dia_semana"] = df["datetime"].dt.dayofweek  # 0=Lunes ... 6=Domingo

    return df[["datetime", "fecha", "mes", "dia_semana", "hora", "precio_eur_kwh"]]


def generar_resumen(df: pd.DataFrame, year: int) -> dict:
    """Calcula métricas agregadas anuales y medias mensuales."""
    medias_mensuales = (
        df.groupby("mes")["precio_eur_kwh"]
        .agg(["mean", "min", "max"])
        .round(5)
        .to_dict(orient="index")
    )

    return {
        "anio": year,
        "zona": "Península (geo_id=8741)",
        "total_horas": int(len(df)),
        "media_anual_eur_kwh": round(float(df["precio_eur_kwh"].mean()), 5),
        "mediana_anual_eur_kwh": round(float(df["precio_eur_kwh"].median()), 5),
        "minimo_anual_eur_kwh": round(float(df["precio_eur_kwh"].min()), 5),
        "maximo_anual_eur_kwh": round(float(df["precio_eur_kwh"].max()), 5),
        "desglose_mensual": medias_mensuales,
        "actualizado_utc": datetime.now(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def main():
    token = os.getenv("ESIOS_TOKEN", "").strip()
    if not token:
        print("ERROR: Falta la variable de entorno ESIOS_TOKEN.", file=sys.stderr)
        sys.exit(1)

    year = obtener_anio_objetivo()
    print(f"Descargando histórico PVPC para el año {year}...")

    df = descargar_pvpc_anual(token, year)

    # Validación de integridad horaria (8760 h año normal, 8784 h bisiesto)
    es_bisiesto = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
    horas_esperadas = 8784 if es_bisiesto else 8760
    if len(df) != horas_esperadas:
        print(f"AVISO: Se esperaban {horas_esperadas} horas y se obtuvieron {len(df)}.", file=sys.stderr)

    os.makedirs("data", exist_ok=True)
    csv_path = f"data/pvpc_{year}_peninsula.csv"
    json_path = f"data/resumen_{year}.json"

    df.to_csv(csv_path, index=False)

    resumen = generar_resumen(df, year)
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)

    print(f"Guardado: {csv_path} ({len(df)} registros)")
    print(f"Media anual {year}: {resumen['media_anual_eur_kwh']} €/kWh")


if __name__ == "__main__":
    main()
