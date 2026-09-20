"""
Capa de datos: toma lo crudo de WHOOP y lo deja usable.

Equivalente a una capa silver: sin persistencia, todo en memoria.
Cada corrida sale a buscar el historico completo a la API.

Uso:
    from datos import cargar_todo, estado_de_hoy

    d = cargar_todo()          # dict con los DataFrames
    print(estado_de_hoy(d))    # el veredicto del dia
"""

from datetime import datetime, timedelta, timezone

import pandas as pd

from whoop_auth import api_get

# --- Configuracion ----------------------------------------------------------

# Dia del torneo y dia de partido semanal (0=lunes ... 5=sabado)
FECHA_TORNEO = "2026-09-26"
DIA_DE_PARTIDO = 5

# Ventana movil para la linea base del HRV.
#
# El estandar en monitoreo de deportistas son 7 dias para sensibilidad y 30 para
# tendencia de fondo. Aca la ventana se adapta sola por un motivo concreto:
# el 14/09/2026 hubo un quiebre en la serie. El HRV salto de un rango de 50-85
# a uno de 95-156 sin que el pulso en reposo se moviera, lo que descarta un
# cambio fisiologico y apunta a una mejora en la calidad de la senal del sensor.
#
# Promediar a traves de un quiebre estructural mezcla dos escalas y da
# comparaciones infladas. Asi que hasta acumular VENTANA_LARGA dias posteriores
# al quiebre se usa una ventana corta con datos homogeneos; despues, el estandar
# de 30 dias. Cuando la fecha del quiebre quede fuera del alcance util, se puede
# poner FECHA_QUIEBRE = None y queda la ventana larga siempre.
FECHA_QUIEBRE = "2026-09-14"
VENTANA_CORTA = 10
VENTANA_LARGA = 30

# Cortes de recovery (los mismos que usa WHOOP para sus colores)
CORTE_ROJO = 34
CORTE_VERDE = 67

# Strain arriba de esto = venis cargado
STRAIN_ALTO = 14.0


# --- Utilidades -------------------------------------------------------------

def _ms_a_horas(ms):
    if ms is None:
        return None
    return round(ms / 3_600_000, 2)


def _fecha_local(iso_utc, offset_str):
    """Convierte la marca UTC de WHOOP al dia calendario real del usuario.

    Sin esto, un entrenamiento de las 19:00 en Ecuador (00:00 UTC del dia
    siguiente) queda contado en el dia equivocado.
    """
    if not iso_utc:
        return None
    t = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if offset_str:
        signo = -1 if offset_str.startswith("-") else 1
        h, m = offset_str.lstrip("+-").split(":")
        t = t + signo * timedelta(hours=int(h), minutes=int(m))
    return t.date()


def _traer_todo(ruta, desde=None):
    """Pagina la API hasta agotar los registros.

    WHOOP devuelve 25 por pagina y un next_token para pedir la siguiente.
    """
    registros = []
    params = {"limit": 25}
    if desde:
        params["start"] = desde

    token = None
    while True:
        if token:
            params["nextToken"] = token
        datos = api_get(ruta, **params)
        registros.extend(datos.get("records", []))

        token = datos.get("next_token")
        if not token:
            break
        # freno de seguridad por si algo sale mal con la paginacion
        if len(registros) > 2000:
            break

    return registros


# --- Carga y limpieza -------------------------------------------------------

def cargar_recovery():
    filas = []
    for r in _traer_todo("/recovery"):
        # PENDING_SCORE = WHOOP todavia no termino de procesar. No sirve.
        if r.get("score_state") != "SCORED":
            continue
        s = r.get("score") or {}
        filas.append({
            "fecha": _fecha_local(r.get("created_at"), r.get("timezone_offset")),
            # apunta al ciclo que este recovery cierra: es la unica forma
            # confiable de emparejarlo con su strain
            "cycle_id": r.get("cycle_id"),
            "recovery": s.get("recovery_score"),
            "hrv": s.get("hrv_rmssd_milli"),
            "pulso_reposo": s.get("resting_heart_rate"),
        })
    df = pd.DataFrame(filas).dropna(subset=["fecha", "recovery"])
    return df.drop_duplicates("fecha").sort_values("fecha").reset_index(drop=True)


def cargar_ciclos():
    filas = []
    ahora = datetime.now(timezone.utc)
    for c in _traer_todo("/cycle"):
        if c.get("score_state") != "SCORED":
            continue
        s = c.get("score") or {}
        inicio = datetime.fromisoformat(c["start"].replace("Z", "+00:00"))
        # el ciclo en curso todavia no tiene end
        fin = (datetime.fromisoformat(c["end"].replace("Z", "+00:00"))
               if c.get("end") else ahora)
        filas.append({
            "cycle_id": c.get("id"),
            "fecha": _fecha_local(c.get("start"), c.get("timezone_offset")),
            "duracion_h": round((fin - inicio).total_seconds() / 3600, 1),
            "strain": s.get("strain"),
            "pulso_promedio": s.get("average_heart_rate"),
            "pulso_maximo": s.get("max_heart_rate"),
        })
    df = pd.DataFrame(filas).dropna(subset=["fecha"])
    # acostarse pasada la medianoche parte el dia en dos ciclos:
    # el real es el mas largo
    df = df.sort_values("duracion_h", ascending=False).drop_duplicates("fecha")
    return df.sort_values("fecha").reset_index(drop=True)


def cargar_sueno():
    filas = []
    for s in _traer_todo("/activity/sleep"):
        if s.get("score_state") != "SCORED":
            continue
        # Las siestas NO son el sueno principal. Si no se filtran, una siesta
        # de 2 horas se lee como una noche pesima y arruina la recomendacion.
        if s.get("nap"):
            continue

        sc = s.get("score") or {}
        et = sc.get("stage_summary") or {}
        en_cama = et.get("total_in_bed_time_milli")
        despierto = et.get("total_awake_time_milli") or 0

        filas.append({
            "fecha": _fecha_local(s.get("end"), s.get("timezone_offset")),
            "horas_dormidas": _ms_a_horas((en_cama or 0) - despierto),
            "horas_profundo": _ms_a_horas(et.get("total_slow_wave_sleep_time_milli")),
            "horas_rem": _ms_a_horas(et.get("total_rem_sleep_time_milli")),
            "rendimiento": sc.get("sleep_performance_percentage"),
            "despertares": et.get("disturbance_count"),
        })
    df = pd.DataFrame(filas).dropna(subset=["fecha"])
    return df.drop_duplicates("fecha").sort_values("fecha").reset_index(drop=True)


def cargar_entrenamientos():
    filas = []
    for w in _traer_todo("/activity/workout"):
        if w.get("score_state") != "SCORED":
            continue
        s = w.get("score") or {}
        z = s.get("zone_durations") or {}
        inicio = datetime.fromisoformat(w["start"].replace("Z", "+00:00"))
        fin = datetime.fromisoformat(w["end"].replace("Z", "+00:00"))

        filas.append({
            "fecha": _fecha_local(w.get("start"), w.get("timezone_offset")),
            "deporte": w.get("sport_name"),
            "minutos": round((fin - inicio).total_seconds() / 60, 1),
            "strain": s.get("strain"),
            "pulso_promedio": s.get("average_heart_rate"),
            # zonas 4 y 5 son el trabajo realmente duro
            "minutos_altos": round(
                ((z.get("zone_four_milli") or 0) + (z.get("zone_five_milli") or 0)) / 60000, 1
            ),
        })
    df = pd.DataFrame(filas).dropna(subset=["fecha"])
    return df.sort_values("fecha").reset_index(drop=True)


def cargar_todo():
    return {
        "recovery": cargar_recovery(),
        "ciclos": cargar_ciclos(),
        "sueno": cargar_sueno(),
        "entrenamientos": cargar_entrenamientos(),
    }


# --- Calculo ----------------------------------------------------------------

def _dias_al_partido(hoy):
    """Dias hasta el proximo sabado, o hasta el torneo si esta mas cerca."""
    faltan_semana = (DIA_DE_PARTIDO - hoy.weekday()) % 7
    torneo = datetime.strptime(FECHA_TORNEO, "%Y-%m-%d").date()
    faltan_torneo = (torneo - hoy).days
    if 0 <= faltan_torneo < faltan_semana:
        return faltan_torneo
    return faltan_semana


def _ventana_base(rec, fecha):
    """Elige el largo de la ventana segun cuantos dias hay tras el quiebre.

    Devuelve VENTANA_CORTA mientras no se acumulen VENTANA_LARGA dias de datos
    posteriores al quiebre; despues, VENTANA_LARGA. El cambio es automatico:
    no hay que acordarse de volver a tocar el codigo.
    """
    if not FECHA_QUIEBRE:
        return VENTANA_LARGA

    quiebre = datetime.strptime(FECHA_QUIEBRE, "%Y-%m-%d").date()
    if fecha < quiebre:
        # dias anteriores al quiebre: su propia escala, ventana estandar
        return VENTANA_LARGA

    posteriores = len(rec[(rec["fecha"] >= quiebre) & (rec["fecha"] < fecha)])
    return VENTANA_LARGA if posteriores >= VENTANA_LARGA else VENTANA_CORTA


def estado_de_hoy(d=None):
    """Devuelve el veredicto del dia: nivel de carga y por que."""
    if d is None:
        d = cargar_todo()

    rec = d["recovery"]
    if rec.empty:
        raise RuntimeError("No hay datos de recovery. Sincronizaste el WHOOP?")

    hoy = rec.iloc[-1]
    fecha = hoy["fecha"]

    # Linea base movil, excluyendo hoy para no contaminar la comparacion.
    # Mediana y no promedio: el HRV tiene picos sueltos por mala lectura del
    # sensor, y un solo valor raro corre el promedio varios puntos.
    ventana = _ventana_base(rec, fecha)
    base = rec[rec["fecha"] < fecha].tail(ventana)
    hrv_base = base["hrv"].median() if len(base) >= 5 else None
    desvio_hrv = None
    if hrv_base:
        desvio_hrv = round((hoy["hrv"] - hrv_base) / hrv_base * 100, 1)

    # Nivel base segun recovery
    if hoy["recovery"] < CORTE_ROJO:
        nivel, razon = 1, f"recovery en {hoy['recovery']:.0f} (rojo)"
    elif hoy["recovery"] < CORTE_VERDE:
        nivel, razon = 2, f"recovery en {hoy['recovery']:.0f} (amarillo)"
    else:
        nivel, razon = 3, f"recovery en {hoy['recovery']:.0f} (verde)"

    ajustes = []

    # Ajuste por la carga del ciclo que este recovery cierra.
    # Se empareja por cycle_id, NO por fecha: el recovery se calcula la manana
    # siguiente al ciclo, asi que sus fechas nunca coinciden aunque describan
    # el mismo periodo.
    ciclos = d["ciclos"]
    previo = ciclos[ciclos["cycle_id"] == hoy["cycle_id"]]
    strain_previo = float(previo["strain"].iloc[0]) if not previo.empty else None

    if strain_previo and strain_previo > STRAIN_ALTO and nivel > 1:
        nivel -= 1
        ajustes.append(f"carga previa alta (strain {strain_previo:.1f})")

    # Ajuste por cercania al partido
    faltan = _dias_al_partido(fecha)
    if faltan <= 2 and nivel > 1:
        nivel -= 1
        ajustes.append(f"faltan {faltan} dias para el partido")

    etiqueta = {1: "suave", 2: "moderado", 3: "fuerte"}[nivel]

    return {
        "fecha": str(fecha),
        "nivel": nivel,
        "carga": etiqueta,
        "recovery": hoy["recovery"],
        "hrv": round(hoy["hrv"], 1),
        "hrv_base": round(hrv_base, 1) if hrv_base else None,
        "ventana_base": ventana,
        "desvio_hrv_pct": desvio_hrv,
        "pulso_reposo": hoy["pulso_reposo"],
        "strain_previo": strain_previo,
        "dias_al_partido": faltan,
        "razon": razon,
        "ajustes": ajustes,
    }


if __name__ == "__main__":
    datos = cargar_todo()

    print("Registros cargados:")
    for nombre, df in datos.items():
        print(f"  {nombre}: {len(df)}")

    e = estado_de_hoy(datos)
    print(f"\n--- {e['fecha']} ---")
    print(f"Carga sugerida: {e['carga'].upper()}")
    print(f"Motivo: {e['razon']}")
    for a in e["ajustes"]:
        print(f"  ajuste: {a}")
    if e["desvio_hrv_pct"] is not None:
        print(f"\nHRV hoy: {e['hrv']} (base {e['hrv_base']}, {e['desvio_hrv_pct']:+}%)")
    print(f"Pulso en reposo: {e['pulso_reposo']}")
    print(f"Strain del ciclo previo: {e['strain_previo']}")
    print(f"Dias al partido: {e['dias_al_partido']}")
