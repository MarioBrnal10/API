from fastapi import FastAPI, UploadFile, File
import fitz  # PyMuPDF

app = FastAPI()

def extraer_datos_por_celdas(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    resultados_totales = []

    columnas_clave = [
        "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO",
        "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.",
        "NO. INVENTARIO", "UBICACION"
    ]

    for page in doc:
        blocks = page.get_text("dict")["blocks"]

        # Paso 1: localizar encabezados
        coords_columnas = {}
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    texto = span["text"].strip().upper()
                    if texto in columnas_clave:
                        coords_columnas[texto] = (span["bbox"][0], span["bbox"][2])

        if "DESCRIPCION" not in coords_columnas:
            continue  # sin encabezados, saltar página

        # Paso 2: detectar filas por coordenadas verticales
        filas = []
        x_inicio, x_fin = coords_columnas["DESCRIPCION"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    if x_inicio <= span["bbox"][0] <= x_fin:
                        y0 = round(span["bbox"][1], 1)
                        y1 = round(span["bbox"][3], 1)
                        if 100 < y0 < 730:  # evitar encabezado/pie
                            filas.append((y0, y1))
                        break

        filas = sorted(set(filas))  # quitar duplicados

        # Paso 3: extraer contenido celda por celda
        for y0, y1 in filas:
            fila_data = {}
            for col, (x0, x1) in coords_columnas.items():
                texto = ""
                for block in blocks:
                    for line in block.get("lines", []):
                        for span in line.get("spans", []):
                            xb0, yb0, xb1, yb1 = span["bbox"]
                            if x0 <= xb0 <= x1 and y0 <= yb0 <= y1:
                                texto += span["text"].strip() + " "
                fila_data[col] = texto.strip()
            if fila_data.get("NO. INVENTARIO") or fila_data.get("DESCRIPCION"):
                resultados_totales.append(fila_data)

    return resultados_totales

@app.post("/procesar-pdf")
async def procesar_pdf(file: UploadFile = File(...)):
    try:
        contenido = await file.read()
        datos = extraer_datos_por_celdas(contenido)
        return datos
    except Exception as e:
        return {"error": str(e)}
