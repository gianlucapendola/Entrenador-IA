"""
Metricas derivadas e insights.

Toma los DataFrames limpios de datos.py y calcula lo que WHOOP no te da masticado:
tendencias, ratios de carga, deuda de sueno y una estimacion de VO2 max.

Uso:
    from datos import cargar_todo
    from metricas import panel

    p = panel(cargar_todo())
"""

from datetime import timedelta

# Edad y pulso maximo se leen de planificacion.json en el generador;
# aqui se pasan como argumento para no acoplar los modulos.


# --- VO2 max ----------------------------------------------------------------

def vo2max_estimado(pulso_maximo, pulso_reposo):
    """Estimacion por la formula de Uth-Sorensen: 15.3 * (FCmax / FCreposo).

    OJO: es una ESTIMACION, no la medicion que muestra la app de WHOOP.
    La API no expone el VO2 max real. El valor absoluto puede diferir, pero
    como la formula es constante, la TENDENCIA si es informativa: si tu pulso
    en reposo baja, este numero sube, y eso refleja una mejora real.
    """
    if not pulso_maximo or not pulso_reposo:
        return None
    return round(15.3 * (pulso_maximo / pulso_reposo), 1)


# --- Tendencias -------------------------------------------------------------

def _tendencia(serie_reciente, serie_previa, umbral_pct=3.0):
    """Compara dos medianas y devuelve (direccion, cambio_pct).

    Mediana y no promedio: las series de HRV traen picos por mala lectura del
    sensor y un solo valor extremo distorsiona el promedio de una ventana corta.
    """
    if serie_reciente.empty or serie_previa.empty:
        return None, None
    a, b = serie_reciente.median(), serie_previa.median()
    if not b:
        return None, None
    cambio = (a - b) / b * 100
    if cambio > umbral_pct:
        return "subiendo", round(cambio, 1)
    if cambio < -umbral_pct:
        return "bajando", round(cambio, 1)
    return "estable", round(cambio, 1)


# --- Carga -----------------------------------------------------------------

def ratio_carga(ciclos, fecha):
    """Ratio agudo-cronico: carga de 7 dias contra el promedio de 28.

    Es el indicador de riesgo de lesion mas usado en deporte de equipo.
    Por encima de 1.5 el riesgo sube de forma marcada: significa que estas
    entrenando mucho mas de lo que tu cuerpo tiene asimilado.
    Por debajo de 0.8 estas desentrenando.
    """
    agudo = ciclos[ciclos["fecha"] > fecha - timedelta(days=7)]["strain"]
    cronico = ciclos[ciclos["fecha"] > fecha - timedelta(days=28)]["strain"]
    if len(agudo) < 3 or len(cronico) < 10:
        return None, None, None
    m_agudo, m_cronico = agudo.mean(), cronico.mean()
    if not m_cronico:
        return None, None, None
    return round(m_agudo / m_cronico, 2), round(m_agudo, 1), round(m_cronico, 1)


# --- Sueno ------------------------------------------------------------------

def deuda_sueno(sueno, fecha, objetivo_h=8.0, dias=7):
    """Horas que faltaron respecto al objetivo en los ultimos dias."""
    ventana = sueno[sueno["fecha"] > fecha - timedelta(days=dias)]
    if ventana.empty:
        return None, None
    horas = ventana["horas_dormidas"].dropna()
    if horas.empty:
        return None, None
    deficit = sum(max(0, objetivo_h - h) for h in horas)
    return round(deficit, 1), round(horas.mean(), 1)


# --- Intensidad -------------------------------------------------------------

def minutos_zona_alta(entrenamientos, fecha, dias=7):
    ventana = entrenamientos[entrenamientos["fecha"] > fecha - timedelta(days=dias)]
    if ventana.empty:
        return 0.0, 0
    return round(ventana["minutos_altos"].sum(), 1), len(ventana)


# --- Panel completo ---------------------------------------------------------

def panel(d, pulso_maximo=None):
    """Devuelve todas las metricas derivadas mas una lista de insights."""
    rec, ciclos = d["recovery"], d["ciclos"]
    sueno, entren = d["sueno"], d["entrenamientos"]

    hoy = rec.iloc[-1]
    fecha = hoy["fecha"]

    # Pulso maximo: percentil 95, NO el maximo absoluto.
    # Un mal contacto del sensor genera picos espurios de un segundo (se vieron
    # valores de 203 lpm a los 23 anos, por encima del maximo teorico). Como el
    # VO2 max se calcula directo de este numero, un solo artefacto lo infla
    # varios puntos. El percentil 95 ignora los picos aislados.
    if pulso_maximo is None and not ciclos.empty:
        serie = ciclos["pulso_maximo"].dropna()
        if not serie.empty:
            pulso_maximo = round(serie.quantile(0.95))

    # --- Tendencias de 7 dias contra los 21 anteriores
    r7 = rec[rec["fecha"] > fecha - timedelta(days=7)]
    r28 = rec[(rec["fecha"] <= fecha - timedelta(days=7))
              & (rec["fecha"] > fecha - timedelta(days=28))]

    hrv_dir, hrv_pct = _tendencia(r7["hrv"], r28["hrv"])
    # En el pulso en reposo, bajar es mejorar: se invierte el umbral
    rhr_dir, rhr_pct = _tendencia(r7["pulso_reposo"], r28["pulso_reposo"], umbral_pct=2.0)
    rec_dir, rec_pct = _tendencia(r7["recovery"], r28["recovery"])

    ratio, agudo, cronico = ratio_carga(ciclos, fecha)
    deficit, promedio_sueno = deuda_sueno(sueno, fecha)
    min_altos, n_sesiones = minutos_zona_alta(entren, fecha)

    # Sueno de anoche
    anoche = sueno[sueno["fecha"] == fecha]
    if anoche.empty and not sueno.empty:
        anoche = sueno.tail(1)
    horas_anoche = float(anoche["horas_dormidas"].iloc[0]) if not anoche.empty else None
    rendimiento_anoche = float(anoche["rendimiento"].iloc[0]) if not anoche.empty else None

    # Se usa la mediana del pulso en reposo de los ultimos 7 dias y no el valor
    # de hoy: un solo dia malo movia el VO2 estimado varios puntos.
    rhr_estable = r7["pulso_reposo"].median() if not r7.empty else hoy["pulso_reposo"]
    vo2 = vo2max_estimado(pulso_maximo, rhr_estable)

    # --- Insights: observaciones cortas, marcadas como alerta o neutra
    insights = []

    if ratio is not None:
        if ratio > 1.5:
            insights.append({
                "alerta": True,
                "texto": f"Carga aguda {ratio} veces tu promedio de 4 semanas. "
                         f"Arriba de 1.5 el riesgo de lesion sube: conviene bajar volumen.",
            })
        elif ratio < 0.8:
            insights.append({
                "alerta": False,
                "texto": f"Carga en {ratio} de tu promedio. Vienes descargando; "
                         f"hay margen para exigir mas si el recovery acompana.",
            })
        else:
            insights.append({
                "alerta": False,
                "texto": f"Carga en {ratio} de tu promedio de 4 semanas: zona optima.",
            })

    if hrv_dir and hrv_dir != "estable":
        # Un salto grande de HRV sin que el pulso en reposo acompane no es
        # fisiologico: apunta a un cambio en la medicion (sensor, ajuste, posicion).
        # Se avisa en vez de celebrarlo como mejora.
        # HRV y pulso en reposo se mueven juntos en fisiologia real, y en
        # proporciones comparables. Un salto de HRV varias veces mayor que el
        # movimiento del reposo apunta a la medicion, no al cuerpo.
        mov_rhr = abs(rhr_pct) if rhr_pct is not None else 0
        sospechoso = abs(hrv_pct) > 25 and abs(hrv_pct) > 4 * max(mov_rhr, 1)
        if sospechoso:
            insights.append({
                "alerta": True,
                "texto": f"HRV {hrv_dir} un {abs(hrv_pct):.0f}% pero el pulso en reposo "
                         f"no acompana. Suele ser un cambio en la medicion, no en el cuerpo: "
                         f"revisa ajuste y limpieza del sensor antes de leerlo como mejora.",
            })
        else:
            insights.append({
                "alerta": hrv_dir == "bajando",
                "texto": f"HRV {hrv_dir} un {abs(hrv_pct):.0f}% en los ultimos 7 dias"
                         + (". Suele anticipar fatiga acumulada." if hrv_dir == "bajando"
                            else ". Buena senal de adaptacion."),
            })

    if rhr_dir and rhr_dir != "estable":
        subio = rhr_dir == "subiendo"
        insights.append({
            "alerta": subio,
            "texto": f"Pulso en reposo {rhr_dir} un {abs(rhr_pct):.0f}%"
                     + (". Puede indicar fatiga, estres o algo incubando."
                        if subio else ". Señal de mejor condicion."),
        })

    if deficit is not None and deficit >= 4:
        insights.append({
            "alerta": True,
            "texto": f"Deuda de sueno de {deficit} horas en la semana "
                     f"(promedio {promedio_sueno} h por noche). Es lo que mas frena la recuperacion.",
        })
    elif promedio_sueno is not None:
        insights.append({
            "alerta": False,
            "texto": f"Promedio de sueno de {promedio_sueno} h por noche esta semana.",
        })

    if n_sesiones:
        insights.append({
            "alerta": False,
            "texto": f"{min_altos:.0f} minutos en zona alta en {n_sesiones} sesiones esta semana.",
        })

    return {
        "vo2max_estimado": vo2,
        "pulso_maximo": int(pulso_maximo) if pulso_maximo else None,
        "ratio_carga": ratio,
        "strain_agudo": agudo,
        "strain_cronico": cronico,
        "horas_anoche": horas_anoche,
        "rendimiento_sueno": rendimiento_anoche,
        "promedio_sueno_7d": promedio_sueno,
        "deuda_sueno_7d": deficit,
        "minutos_zona_alta_7d": min_altos,
        "sesiones_7d": n_sesiones,
        "hrv_tendencia": hrv_dir,
        "hrv_cambio_pct": hrv_pct,
        "rhr_tendencia": rhr_dir,
        "rhr_cambio_pct": rhr_pct,
        "recovery_tendencia": rec_dir,
        "recovery_cambio_pct": rec_pct,
        "insights": insights,
    }


if __name__ == "__main__":
    from datos import cargar_todo

    p = panel(cargar_todo())

    print("--- Panel ---")
    print(f"VO2 max estimado:   {p['vo2max_estimado']} (pulso max {p['pulso_maximo']})")
    print(f"Ratio de carga:     {p['ratio_carga']}  (agudo {p['strain_agudo']} / cronico {p['strain_cronico']})")
    print(f"Sueno anoche:       {p['horas_anoche']} h  (rendimiento {p['rendimiento_sueno']}%)")
    print(f"Promedio 7d:        {p['promedio_sueno_7d']} h   deuda: {p['deuda_sueno_7d']} h")
    print(f"Zona alta 7d:       {p['minutos_zona_alta_7d']} min en {p['sesiones_7d']} sesiones")
    print(f"HRV:                {p['hrv_tendencia']} ({p['hrv_cambio_pct']:+}%)")
    print(f"Pulso reposo:       {p['rhr_tendencia']} ({p['rhr_cambio_pct']:+}%)")

    print("\n--- Insights ---")
    for i in p["insights"]:
        marca = "!" if i["alerta"] else "-"
        print(f" {marca} {i['texto']}")
