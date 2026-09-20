"""
Generador de la rutina del dia.

Junta las cuatro piezas:
    datos.py           -> como estas hoy (recovery, carga, dias al partido)
    metricas.py        -> tendencias, ratio de carga, VO2 estimado, insights
    planificacion.json -> que bloque toca segun el dia de la semana
    ejercicios.json    -> que ejercicios, para que sirven y como se ejecutan

y los vuelca en plantilla.html (Jinja2).

Uso:
    python generar.py                 rutina de hoy
    python generar.py 2026-09-22      simular otra fecha, para probar
    python generar.py --papel         version clara para imprimir
    python generar.py --pdf           ademas del HTML, exporta PDF
    python generar.py --local         no sube nada a GitHub
"""

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from datos import cargar_todo, estado_de_hoy
from metricas import panel

AQUI = Path(__file__).parent
CARPETA_SALIDA = AQUI / "rutinas"

DIAS = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
DIAS_LARGO = ["Lunes", "Martes", "Miercoles", "Jueves", "Viernes", "Sabado", "Domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio",
         "agosto", "septiembre", "octubre", "noviembre", "diciembre"]

# Los mismos acentos definidos en el CSS. Se repiten aqui porque el SVG del
# anillo necesita el color como atributo: las variables CSS no siempre
# cascadean dentro de SVG inline.
ACENTOS = {
    "fuerte":   {"oscuro": "#16E08B", "papel": "#0B7A45"},
    "moderado": {"oscuro": "#FFB224", "papel": "#8A5A00"},
    "suave":    {"oscuro": "#FF4D4F", "papel": "#B3241B"},
}
TRACK = {"oscuro": "#1E2327", "papel": "#E4E4DE"}

# Titulos legibles de cada bloque. Explicitos y no derivados del id:
# derivarlos con replace daba resultados como "Upper Y Body".
TITULOS = {
    "lower_body": "Lower Body",
    "upper_body": "Upper Body",
    "potencia": "Potencia",
    "movilidad_core": "Movilidad y Core",
    "activacion": "Activacion",
    "tecnica": "Tecnica",
    "partido": "Dia de Partido",
}

# Calentamiento fijo del dia de partido. No sale de la biblioteca porque no es
# entrenamiento: es protocolo previo a competir.
CALENTAMIENTO = [
    {"nombre": "Trote progresivo", "prescripcion": "5 min",
     "para_que": "Sube pulso y temperatura: el musculo frio se rompe mas facil."},
    {"nombre": "Movilidad de cadera", "prescripcion": "4 min",
     "para_que": "Rango para el primer paso y para frenar sin perder el apoyo."},
    {"nombre": "Activacion de gluteo", "prescripcion": "3 min",
     "para_que": "Enciende el gluteo para que el isquio no cargue solo en cada esprint."},
    {"nombre": "Progresivos", "prescripcion": "4x40 m",
     "para_que": "Prepara el sistema nervioso para la velocidad real del juego."},
    {"nombre": "Toques y cambios de direccion", "prescripcion": "5 min - balon",
     "para_que": "Lleva el calentamiento al gesto del juego: girar, recibir y salir."},
]


def _leer_json(nombre):
    with open(AQUI / nombre, encoding="utf-8") as f:
        return json.load(f)


# --- Seleccion de la sesion -------------------------------------------------

def armar_sesion(estado, plan, biblioteca, fecha):
    """Decide que bloque toca y que ejercicios entran."""
    dia = DIAS[fecha.weekday()]
    cfg = plan["semana_tipo"][dia]
    bloque = cfg["bloque"]
    nivel = estado["nivel"]
    avisos = []

    if bloque == "partido":
        return {
            "dia": dia, "bloque": bloque, "titulo": "Dia de Partido",
            "lugar": cfg.get("lugar", "cancha"),
            "duracion": cfg.get("duracion_min", 90),
            "ejercicios": CALENTAMIENTO, "avisos": [], "es_partido": True,
        }

    # Con recovery bajo, la pliometria no se hace a media intensidad: se cambia.
    # Es la sesion que mas lesiona cuando el cuerpo no responde.
    if bloque == "potencia" and nivel == 1:
        bloque = "movilidad_core"
        avisos.append(
            "Sesion de potencia sustituida por movilidad: el estado de hoy no "
            "acompana y la pliometria cansada es la via mas rapida a una lesion."
        )

    nivel_txt = {1: "suave", 2: "moderado", 3: "fuerte"}[nivel]

    # Filtro por restriccion de tobillo activa
    tobillo = plan.get("restricciones", {}).get("tobillo")
    descartar_alto = bool(tobillo) and nivel <= 2

    elegidos = []
    for ej in biblioteca.get(bloque, []):
        reps = ej["series_reps"].get(nivel_txt, "")
        if reps == "omitir":
            continue
        if descartar_alto and ej.get("impacto_tobillo") == "alto":
            avisos.append(f"{ej['nombre']} omitido por la restriccion de tobillo.")
            continue
        elegidos.append({**ej, "prescripcion": reps})

    return {
        "dia": dia,
        "bloque": bloque,
        "titulo": TITULOS.get(bloque, bloque.replace("_", " ").title()),
        "lugar": cfg.get("lugar", "-"),
        "duracion": cfg.get("duracion_min", 45),
        "ejercicios": elegidos,
        "avisos": avisos,
        "es_partido": False,
    }


# --- Render -----------------------------------------------------------------

def render(estado, p, sesion, plan, fecha, papel=False):
    env = Environment(loader=FileSystemLoader(str(AQUI)), autoescape=False)
    plantilla = env.get_template("plantilla.html")

    # Arco del anillo: el perimetro es 2*pi*50 = 314.16.
    # El offset es la parte NO dibujada, de ahi el (1 - pct/100).
    pct = max(0.0, min(100.0, float(estado["recovery"])))
    offset = round(314.16 * (1 - pct / 100), 2)

    clave = f"{estado['nivel']}_{estado['carga']}"
    intensidad = plan["reglas_de_carga"][clave]["intensidad_pct"]

    criterio = estado["razon"].capitalize()
    if estado["ajustes"]:
        criterio += ", ajustado por " + " y ".join(estado["ajustes"])
    criterio += "."

    tema = "papel" if papel else "oscuro"
    torneo = datetime.strptime(plan["objetivo"]["fecha"], "%Y-%m-%d").date()

    return plantilla.render(
        estado=estado,
        panel=p,
        sesion=sesion,
        plan=plan,
        criterio=criterio,
        intensidad=intensidad,
        segmentos=round(intensidad / 10),
        anillo_offset=offset,
        color_acento=ACENTOS[estado["carga"]][tema],
        color_track=TRACK[tema],
        tema_papel=papel,
        fecha_larga=f"{DIAS_LARGO[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]}",
        generado=datetime.now().strftime("%d/%m/%Y a las %H:%M"),
        dias_torneo=(torneo - fecha).days,
        nombre_evento=plan["objetivo"].get("nombre", "el torneo"),
    )


def exportar_pdf(html, ruta_pdf):
    """Opcional: requiere weasyprint, que en Windows pide librerias extra."""
    try:
        from weasyprint import HTML
    except Exception as e:
        print(f"\n(PDF no generado: weasyprint no disponible - {e})")
        return None
    HTML(string=html, base_url=str(AQUI)).write_pdf(str(ruta_pdf))
    return ruta_pdf


# --- Publicacion ------------------------------------------------------------

def publicar(fecha_txt):
    """Sube index.html al repo para que GitHub Pages sirva la rutina del dia.

    Solo se toca index.html: nunca `git add .`, para que un archivo suelto en
    la carpeta no termine en un repositorio publico por descuido.
    """
    def _git(*args):
        return subprocess.run(
            ["git", *args], cwd=str(AQUI),
            capture_output=True, text=True,
        )

    if not (AQUI / ".git").exists():
        print("\n(sin publicar: la carpeta no es un repositorio git)")
        return False

    r = _git("add", "index.html")
    if r.returncode != 0:
        print(f"\nNo se pudo preparar el archivo: {r.stderr.strip()}")
        return False

    # Sin cambios que subir no es un error: la rutina de hoy ya estaba publicada
    estado = _git("diff", "--cached", "--quiet", "index.html")
    if estado.returncode == 0:
        print("\nLa pagina ya estaba al dia, no habia nada que subir.")
        return True

    r = _git("commit", "-m", f"Rutina del {fecha_txt}")
    if r.returncode != 0:
        print(f"\nNo se pudo guardar el cambio: {r.stderr.strip() or r.stdout.strip()}")
        return False

    r = _git("push")
    if r.returncode != 0:
        print(f"\nNo se pudo subir a GitHub: {r.stderr.strip()}")
        print("El commit quedo hecho: reintenta con `git push` cuando haya conexion.")
        return False

    return True


# --- Main -------------------------------------------------------------------

def main():
    args = sys.argv[1:]
    papel = "--papel" in args
    con_pdf = "--pdf" in args
    sin_subir = "--local" in args
    fechas = [a for a in args if not a.startswith("--")]

    plan = _leer_json("planificacion.json")
    biblioteca = _leer_json("ejercicios.json")

    print("Consultando WHOOP...")
    d = cargar_todo()
    estado = estado_de_hoy(d)
    p = panel(d)

    if fechas:
        estado["fecha"] = fechas[0]
        print(f"(simulando la fecha {fechas[0]})")

    fecha = datetime.strptime(estado["fecha"], "%Y-%m-%d").date()
    sesion = armar_sesion(estado, plan, biblioteca, fecha)

    html = render(estado, p, sesion, plan, fecha, papel=papel)

    CARPETA_SALIDA.mkdir(exist_ok=True)
    sufijo = "_papel" if papel else ""
    ruta = CARPETA_SALIDA / f"{estado['fecha']}_{sesion['bloque']}{sufijo}.html"
    ruta.write_text(html, encoding="utf-8")

    # Copia en la raiz con nombre fijo: es lo que sirve GitHub Pages en la
    # direccion corta, para abrir siempre el mismo link desde el celular.
    # La version papel no pisa la principal.
    if not papel:
        (AQUI / "index.html").write_text(html, encoding="utf-8")

    print(f"\n{sesion['dia'].capitalize()} - {sesion['titulo']}")
    print(f"Carga: {estado['carga'].upper()} (recovery {estado['recovery']:.0f})")
    print(f"{len(sesion['ejercicios'])} ejercicios")
    for a in sesion["avisos"]:
        print(f"  ! {a}")
    print(f"\nHTML: {ruta}")
    if not papel:
        print(f"      {AQUI / 'index.html'}  (subir este a GitHub)")

    if con_pdf:
        pdf = exportar_pdf(html, ruta.with_suffix(".pdf"))
        if pdf:
            print(f"PDF:  {pdf}")

    if not papel and not sin_subir:
        if publicar(estado["fecha"]):
            print("\nPublicado: https://gianlucapendola.github.io/Entrenador-IA/")


if __name__ == "__main__":
    main()
