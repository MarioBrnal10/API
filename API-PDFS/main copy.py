from fastapi import FastAPI, UploadFile, File
import fitz
import re
from collections import defaultdict

app = FastAPI()

columnas_clave = [
    "PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO",
    "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.",
    "NO. INVENTARIO"
]

class SimpleTableExtractor:
    
    def __init__(self):
        self.ultimo_prog = 0
        self.prog_procesados = set()
        
        self.patterns = {
            "PROG": r'^[1-9]\d{0,3}$',
            "COSTO": r'^\d{1,3}(,\d{3})*\.\d{2}$',
            "TIPO_ADQ": r'^[A-Z]\d{1,2}-\d{1,2}$',
            "NO_INVENTARIO": r'^\d{4,5}-\d{4}-\d{5}-\d{1}$'
        }
        
        self.known_brands = {"NOKIA", "CISCO", "STEREN", "CAMBIUM", "SOUND", "TRACK", "ACTECK", "BAF", "OHAUS", "EPSON", "CANON", "HP", "DELL", "PIONER", "SIN MARCA", "OLYMPIA", "IROSCOPE"}

    def is_bold_text(self, span):
        font_flags = span.get("flags", 0)
        if font_flags & 16:
            return True
        
        font_name = span.get("font", "").lower()
        if any(bold_word in font_name for bold_word in ["bold", "black", "heavy", "demi"]):
            return True
        
        return False

    def clean_invalid_values(self, registro):
        for campo in registro:
            if registro[campo] in ["000", "0000", "00", "CODI"]:
                registro[campo] = ""
        return registro

    def find_missing_costo(self, registro, elementos_texto, prog_y):
        if registro["COSTO"]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["COSTO"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["COSTO"] = texto
                    break
        
        if not registro["COSTO"]:
            for i in range(-2, 3):
                for elem in elementos_texto:
                    if abs(elem["y0"] - (prog_y + i * 12)) <= 6:
                        texto = elem["texto"].strip()
                        if re.match(self.patterns["COSTO"], texto):
                            registro["COSTO"] = texto
                            return registro
        
        return registro

    def detect_codi_column(self, elementos_texto, start_y):
        codi_x_position = None
        header_y = None
        
        for elem in elementos_texto:
            if elem["y0"] < start_y - 10:
                texto_upper = elem["texto"].upper()
                if any(header in texto_upper for header in ["PROG", "DESCRIPCION", "OBSERVACIONES"]):
                    if header_y is None or elem["y0"] > header_y:
                        header_y = elem["y0"]
        
        if header_y is not None:
            for elem in elementos_texto:
                if abs(elem["y0"] - header_y) <= 10:
                    texto = elem["texto"].upper()
                    if "CODI" in texto and len(texto) <= 6:
                        codi_x_position = elem["x0"]
                        break
        
        return codi_x_position

    def detect_multiline_desc_tipo(self, elementos_texto):
        elementos_combinados = []
        i = 0
        
        while i < len(elementos_texto):
            elem = elementos_texto[i]
            texto = elem["texto"].strip()
            
            if (texto and texto[0].isupper() and 
                any(keyword in texto.upper() for keyword in ["IIFEQ", "INGRESOS", "CONAFE", "P.A.R.E.I.B", "PROV", "U.S.E.B.E.Q", "OTROS", "PROG.", "REFORMA", "REPOSICION", "C.A.P.C.E.Q", "I.L.C.E"])):
                
                texto_completo = texto
                j = i + 1
                elementos_utilizados = [elem]
                
                while j < len(elementos_texto):
                    siguiente = elementos_texto[j]
                    siguiente_texto = siguiente["texto"].strip()
                    
                    if abs(siguiente["y0"] - elem["y0"]) > 25:
                        j += 1
                        continue
                    
                    if abs(siguiente["x0"] - elem["x0"]) > 120:
                        j += 1
                        continue
                    
                    if siguiente_texto and siguiente_texto[-1].islower():
                        texto_completo += " " + siguiente_texto
                        elementos_utilizados.append(siguiente)
                        
                        elemento_combinado = {
                            "texto": texto_completo,
                            "x0": elem["x0"],
                            "y0": elem["y0"],
                            "x1": max(e["x1"] for e in elementos_utilizados),
                            "y1": max(e["y1"] for e in elementos_utilizados),
                            "es_multilinea": True
                        }
                        elementos_combinados.append(elemento_combinado)
                        i = j + 1
                        break
                    else:
                        texto_completo += " " + siguiente_texto
                        elementos_utilizados.append(siguiente)
                        j += 1
                else:
                    elementos_combinados.append(elem)
                    i += 1
            else:
                elementos_combinados.append(elem)
                i += 1
        
        return elementos_combinados

    def find_all_prog_positions(self, elementos_texto, page_num):
        if page_num == 1:
            prog_inicial = 1
        else:
            prog_inicial = self.ultimo_prog + 1
        
        prog_positions = []
        prog_encontrados = set()
        
        prog_actual = prog_inicial
        for _ in range(1000):
            encontrado = False
            for elem in elementos_texto:
                if (elem["texto"].strip() == str(prog_actual) and 
                    prog_actual not in prog_encontrados):
                    
                    y_pos = elem["y0"]
                    elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 5]
                    
                    if len(elementos_fila) >= 3:
                        prog_positions.append({
                            "prog": prog_actual,
                            "y": y_pos,
                            "elementos_fila": elementos_fila
                        })
                        prog_encontrados.add(prog_actual)
                        prog_actual += 1
                        encontrado = True
                        break
            
            if not encontrado:
                if prog_actual > prog_inicial + 3:
                    for elem in elementos_texto:
                        if (elem["texto"].strip() == "1" and 
                            1 not in prog_encontrados):
                            
                            y_pos = elem["y0"]
                            elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 5]
                            
                            if len(elementos_fila) >= 3:
                                prog_positions.append({
                                    "prog": 1,
                                    "y": y_pos,
                                    "elementos_fila": elementos_fila
                                })
                                prog_encontrados.add(1)
                                prog_actual = 2
                                encontrado = True
                                break
                
                if not encontrado:
                    break
        
        return prog_positions

    def detect_column_positions(self, elementos_texto, start_y):
        column_positions = {}
        header_y = None
        
        for elem in elementos_texto:
            if elem["y0"] < start_y - 10:
                texto_upper = elem["texto"].upper()
                if any(header in texto_upper for header in ["PROG", "DESCRIPCION", "OBSERVACIONES"]):
                    if header_y is None or elem["y0"] > header_y:
                        header_y = elem["y0"]
        
        if header_y is None:
            return self.estimate_column_positions(elementos_texto, start_y)
        
        for elem in elementos_texto:
            if abs(elem["y0"] - header_y) <= 10:
                texto = elem["texto"].upper()
                x_pos = elem["x0"]
                
                if "PROG" in texto and "PROG." not in texto:
                    column_positions["PROG"] = x_pos
                elif "DESCRIPCION" in texto:
                    column_positions["DESCRIPCION"] = x_pos
                elif "OBSERVACIONES" in texto:
                    column_positions["OBSERVACIONES"] = x_pos
                elif "MARCA" in texto and len(texto) <= 8:
                    column_positions["MARCA"] = x_pos
                elif "MODELO" in texto and len(texto) <= 8:
                    column_positions["MODELO"] = x_pos
                elif "SERIE" in texto and len(texto) <= 8:
                    column_positions["SERIE"] = x_pos
                elif "COSTO" in texto or ("BIEN" in texto and "TIPO" in texto):
                    column_positions["COSTO"] = x_pos
                elif "TIPO" in texto and "ADQ" in texto and "DESC" not in texto:
                    column_positions["TIPO ADQ."] = x_pos
                elif "DESC" in texto and "TIPO" in texto:
                    column_positions["DESC. TIPO ADQ."] = x_pos
                elif "INVENTARIO" in texto:
                    column_positions["NO. INVENTARIO"] = x_pos
        
        return column_positions

    def estimate_column_positions(self, elementos_texto, start_y):
        elementos_datos = [e for e in elementos_texto if start_y <= e["y0"] <= start_y + 50]
        
        codi_x_position = self.detect_codi_column(elementos_texto, start_y)
        
        x_positions = []
        for elem in elementos_datos:
            if elem["texto"].strip() not in ["000", "0000", "00", "CODI"]:
                if codi_x_position is None or abs(elem["x0"] - codi_x_position) > 30:
                    x_positions.append(elem["x0"])
        
        x_positions.sort()
        unique_x = []
        for x in x_positions:
            if not unique_x or abs(x - unique_x[-1]) > 25:
                unique_x.append(x)
        
        column_order = ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.", "NO. INVENTARIO"]
        column_positions = {}
        
        for i, col in enumerate(column_order):
            if i < len(unique_x):
                column_positions[col] = unique_x[i]
        
        return column_positions

    def find_missing_no_inventario(self, registro, elementos_texto, prog_y):
        if registro["NO. INVENTARIO"]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["NO_INVENTARIO"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["NO. INVENTARIO"] = texto
                    break
        
        if not registro["NO. INVENTARIO"]:
            for i in range(-3, 4):
                for elem in elementos_texto:
                    if abs(elem["y0"] - (prog_y + i * 15)) <= 8:
                        texto = elem["texto"].strip()
                        if re.match(self.patterns["NO_INVENTARIO"], texto):
                            registro["NO. INVENTARIO"] = texto
                            return registro
        
        return registro

    def find_missing_tipo_adq(self, registro, elementos_texto, prog_y):
        if registro["TIPO ADQ."]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["TIPO_ADQ"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["TIPO ADQ."] = texto
                    break
        
        return registro

    def find_missing_desc_tipo_adq(self, registro, elementos_texto, prog_y):
        if registro["DESC. TIPO ADQ."]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if (hasattr(elem, 'es_multilinea') or 
                (texto and len(texto) > 10 and 
                 any(keyword in texto.upper() for keyword in ["C.A.P.C.E.Q", "I.L.C.E", "IIFEQ", "INGRESOS", "CONAFE", "OTROS", "PROG.", "REFORMA"]))):
                registro["DESC. TIPO ADQ."] = texto
                break
        
        return registro

    def assign_by_position(self, elementos_fila, column_positions, prog_num):
        registro = {col: "" for col in columnas_clave}
        registro["PROG"] = str(prog_num)
        
        elementos_fila.sort(key=lambda x: x["x0"])
        
        codi_x_position = None
        for elem in elementos_fila:
            if elem["texto"].strip().upper() == "CODI":
                codi_x_position = elem["x0"]
                break
        
        elementos_restantes = []
        
        for elem in elementos_fila:
            texto = elem["texto"].strip()
            asignado = False
            
            if texto == str(prog_num) or texto in ["000", "0000", "00", "CODI"]:
                asignado = True
            
            elif codi_x_position is not None and abs(elem["x0"] - codi_x_position) <= 20:
                asignado = True
            
            elif re.match(self.patterns["TIPO_ADQ"], texto):
                if not registro["TIPO ADQ."]:
                    registro["TIPO ADQ."] = texto
                    asignado = True
            
            elif re.match(self.patterns["NO_INVENTARIO"], texto):
                if not registro["NO. INVENTARIO"]:
                    registro["NO. INVENTARIO"] = texto
                    asignado = True
            
            elif re.match(self.patterns["COSTO"], texto):
                if not registro["COSTO"]:
                    registro["COSTO"] = texto
                    asignado = True
            
            elif (hasattr(elem, 'es_multilinea') or 
                  (texto and len(texto) > 15 and texto[0].isupper() and texto[-1].islower() and 
                   any(keyword in texto.upper() for keyword in ["IIFEQ", "INGRESOS", "CONAFE", "P.A.R.E.I.B", "PROV", "U.S.E.B.E.Q", "OTROS", "PROG.", "REFORMA", "REPOSICION", "C.A.P.C.E.Q", "I.L.C.E"]))):
                if not registro["DESC. TIPO ADQ."]:
                    registro["DESC. TIPO ADQ."] = texto
                    asignado = True
            
            elif texto.upper() in self.known_brands:
                if not registro["MARCA"]:
                    registro["MARCA"] = texto
                    asignado = True
            
            if not asignado:
                elementos_restantes.append(elem)
        
        for elem in elementos_restantes:
            texto = elem["texto"].strip()
            x_pos = elem["x0"]
            
            if codi_x_position is not None and abs(x_pos - codi_x_position) <= 20:
                continue
            
            mejor_columna = None
            menor_distancia = float('inf')
            
            for col, col_x in column_positions.items():
                if registro[col]:
                    continue 
                
                distancia = abs(x_pos - col_x)
                
                if col == "OBSERVACIONES" and "MARCA" in column_positions:
                    if distancia < 40 and x_pos < column_positions["MARCA"] - 30:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
                elif col == "MARCA" and "OBSERVACIONES" in column_positions:
                    if distancia < 40 and x_pos > column_positions["OBSERVACIONES"] + 30:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
                else:
                    if distancia < 60:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
            
            if mejor_columna:
                registro[mejor_columna] = texto
        
        return registro

    def extract_by_positions(self, elementos_texto, page_num):
        if not elementos_texto:
            return []
        
        elementos_texto = self.detect_multiline_desc_tipo(elementos_texto)
        
        prog_positions = self.find_all_prog_positions(elementos_texto, page_num)
        if not prog_positions:
            return []
        
        first_y = prog_positions[0]["y"]
        column_positions = self.detect_column_positions(elementos_texto, first_y)
        if not column_positions:
            column_positions = self.estimate_column_positions(elementos_texto, first_y)
        
        registros = []
        
        for prog_info in prog_positions:
            prog_num = prog_info["prog"]
            elementos_fila = prog_info["elementos_fila"]
            prog_y = prog_info["y"]
            
            clave_unica = f"{page_num}_{prog_num}"
            if clave_unica in self.prog_procesados:
                continue
            
            self.prog_procesados.add(clave_unica)
            
            registro = self.assign_by_position(elementos_fila, column_positions, prog_num)
            
            registro = self.clean_invalid_values(registro)
            
            registro = self.find_missing_no_inventario(registro, elementos_texto, prog_y)
            
            registro = self.find_missing_costo(registro, elementos_texto, prog_y)
            
            registro = self.find_missing_tipo_adq(registro, elementos_texto, prog_y)
            
            registro = self.find_missing_desc_tipo_adq(registro, elementos_texto, prog_y)
            
            if prog_num > self.ultimo_prog:
                self.ultimo_prog = prog_num
            
            registros.append(registro)
        
        return registros

def extraer_datos_por_celdas(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    extractor = SimpleTableExtractor()
    resultados_totales = []
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text_dict = page.get_text("dict")
        
        elementos_texto = []
        for block in text_dict.get("blocks", []):
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        texto = span["text"].strip()
                        
                        if extractor.is_bold_text(span):
                            continue
                        
                        if texto and not any(bad in texto for bad in ["Declaro", "protesta", "NOMBRE", "FIRMA", "TOTAL", "CEDULA", "CVE.", "CODI"]) and texto not in ["000", "0000", "00"]:
                            elementos_texto.append({
                                "texto": texto,
                                "x0": round(span["bbox"][0], 1),
                                "y0": round(span["bbox"][1], 1),
                                "x1": round(span["bbox"][2], 1),
                                "y1": round(span["bbox"][3], 1)
                            })
        
        if elementos_texto:
            registros_pagina = extractor.extract_by_positions(elementos_texto, page_num + 1)
            resultados_totales.extend(registros_pagina)
    
    doc.close()
    
    resultados_totales.sort(key=lambda x: int(x.get("PROG", "999")))
    
    return resultados_totales

@app.get("/")
async def root():
    return {"message": "API Extractor V15", "status": "OK"}

@app.post("/procesar-pdf")
async def procesar_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        return {"error": "El archivo debe ser un PDF"}
    
    pdf_bytes = await file.read()
    
    try:
        resultados = extraer_datos_por_celdas(pdf_bytes)
        
        if not resultados:
            return {"mensaje": "No se encontraron registros válidos"}
        
        return {
            "total_registros": len(resultados),
            "resultados": resultados
        }
    except Exception as e:
        import traceback
        return {
            "error": str(e), 
            "traceback": traceback.format_exc()
        }

@app.get("/debug")
async def debug():
    return {"message": "Extractor V15 - Detección con CODI y campos obligatorios"}
























from fastapi import FastAPI, UploadFile, File
import fitz
import re
from collections import defaultdict

app = FastAPI()

columnas_clave = [
    "PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO",
    "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.",
    "NO. INVENTARIO"
]

class SimpleTableExtractor:
    
    def __init__(self):
        self.ultimo_prog = 0
        self.prog_procesados = set()
        
        self.patterns = {
            "PROG": r'^[1-9]\d{0,3}$',
            "COSTO": r'^\d{1,3}(,\d{3})*\.\d{2}$',
            "TIPO_ADQ": r'^[A-Z]\d{1,2}-\d{1,2}$',
            "NO_INVENTARIO": r'^\d{10}-\d{4}-\d{5}-\d{2}$'
        }
        
        self.known_brands = {"NOKIA", "CISCO", "STEREN", "CAMBIUM", "SOUND", "TRACK", "ACTECK", "BAF", "OHAUS", "EPSON", "CANON", "HP", "DELL", "PIONER", "SIN MARCA"}

    def is_bold_text(self, span):
        font_flags = span.get("flags", 0)
        if font_flags & 16:
            return True
        
        font_name = span.get("font", "").lower()
        if any(bold_word in font_name for bold_word in ["bold", "black", "heavy", "demi"]):
            return True
        
        return False

    def clean_invalid_values(self, registro):
        for campo in registro:
            if registro[campo] in ["000", "0000", "00"]:
                registro[campo] = ""
        return registro

    def detect_multiline_desc_tipo(self, elementos_texto):
        elementos_combinados = []
        i = 0
        
        while i < len(elementos_texto):
            elem = elementos_texto[i]
            texto = elem["texto"].strip()
            
            if (texto and texto[0].isupper() and 
                any(keyword in texto.upper() for keyword in ["IIFEQ", "INGRESOS", "CONAFE", "P.A.R.E.I.B", "PROV", "U.S.E.B.E.Q", "OTROS", "PROG.", "REFORMA", "REPOSICION"])):
                
                texto_completo = texto
                j = i + 1
                elementos_utilizados = [elem]
                
                while j < len(elementos_texto):
                    siguiente = elementos_texto[j]
                    siguiente_texto = siguiente["texto"].strip()
                    
                    if abs(siguiente["y0"] - elem["y0"]) > 25:
                        j += 1
                        continue
                    
                    if abs(siguiente["x0"] - elem["x0"]) > 120:
                        j += 1
                        continue
                    
                    if siguiente_texto and siguiente_texto[-1].islower():
                        texto_completo += " " + siguiente_texto
                        elementos_utilizados.append(siguiente)
                        
                        elemento_combinado = {
                            "texto": texto_completo,
                            "x0": elem["x0"],
                            "y0": elem["y0"],
                            "x1": max(e["x1"] for e in elementos_utilizados),
                            "y1": max(e["y1"] for e in elementos_utilizados),
                            "es_multilinea": True
                        }
                        elementos_combinados.append(elemento_combinado)
                        i = j + 1
                        break
                    else:
                        texto_completo += " " + siguiente_texto
                        elementos_utilizados.append(siguiente)
                        j += 1
                else:
                    elementos_combinados.append(elem)
                    i += 1
            else:
                elementos_combinados.append(elem)
                i += 1
        
        return elementos_combinados

    def find_all_prog_positions(self, elementos_texto, page_num):
        if page_num == 1:
            prog_inicial = 1
        else:
            prog_inicial = self.ultimo_prog + 1
        
        prog_positions = []
        prog_encontrados = set()
        
        prog_actual = prog_inicial
        for _ in range(1000):
            encontrado = False
            for elem in elementos_texto:
                if (elem["texto"].strip() == str(prog_actual) and 
                    prog_actual not in prog_encontrados):
                    
                    y_pos = elem["y0"]
                    elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 5]
                    
                    if len(elementos_fila) >= 3:
                        prog_positions.append({
                            "prog": prog_actual,
                            "y": y_pos,
                            "elementos_fila": elementos_fila
                        })
                        prog_encontrados.add(prog_actual)
                        prog_actual += 1
                        encontrado = True
                        break
            
            if not encontrado:
                if prog_actual > prog_inicial + 3:
                    for elem in elementos_texto:
                        if (elem["texto"].strip() == "1" and 
                            1 not in prog_encontrados):
                            
                            y_pos = elem["y0"]
                            elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 5]
                            
                            if len(elementos_fila) >= 3:
                                prog_positions.append({
                                    "prog": 1,
                                    "y": y_pos,
                                    "elementos_fila": elementos_fila
                                })
                                prog_encontrados.add(1)
                                prog_actual = 2
                                encontrado = True
                                break
                
                if not encontrado:
                    break
        
        return prog_positions

    def detect_column_positions(self, elementos_texto, start_y):
        column_positions = {}
        header_y = None
        
        for elem in elementos_texto:
            if elem["y0"] < start_y - 10:
                texto_upper = elem["texto"].upper()
                if any(header in texto_upper for header in ["PROG", "DESCRIPCION", "OBSERVACIONES"]):
                    if header_y is None or elem["y0"] > header_y:
                        header_y = elem["y0"]
        
        if header_y is None:
            return self.estimate_column_positions(elementos_texto, start_y)
        
        for elem in elementos_texto:
            if abs(elem["y0"] - header_y) <= 10:
                texto = elem["texto"].upper()
                x_pos = elem["x0"]
                
                if "PROG" in texto:
                    column_positions["PROG"] = x_pos
                elif "DESCRIPCION" in texto:
                    column_positions["DESCRIPCION"] = x_pos
                elif "OBSERVACIONES" in texto:
                    column_positions["OBSERVACIONES"] = x_pos
                elif "MARCA" in texto:
                    column_positions["MARCA"] = x_pos
                elif "MODELO" in texto:
                    column_positions["MODELO"] = x_pos
                elif "SERIE" in texto:
                    column_positions["SERIE"] = x_pos
                elif "COSTO" in texto:
                    column_positions["COSTO"] = x_pos
                elif "TIPO" in texto and "ADQ" in texto:
                    column_positions["TIPO ADQ."] = x_pos
                elif "DESC" in texto and "TIPO" in texto:
                    column_positions["DESC. TIPO ADQ."] = x_pos
                elif "INVENTARIO" in texto:
                    column_positions["NO. INVENTARIO"] = x_pos
        
        return column_positions

    def estimate_column_positions(self, elementos_texto, start_y):
        elementos_datos = [e for e in elementos_texto if start_y <= e["y0"] <= start_y + 50]
        
        x_positions = []
        for elem in elementos_datos:
            x_positions.append(elem["x0"])
        
        x_positions.sort()
        unique_x = []
        for x in x_positions:
            if not unique_x or abs(x - unique_x[-1]) > 25:
                unique_x.append(x)
        
        column_order = ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.", "NO. INVENTARIO"]
        column_positions = {}
        
        for i, col in enumerate(column_order):
            if i < len(unique_x):
                column_positions[col] = unique_x[i]
        
        return column_positions

    def find_missing_no_inventario(self, registro, elementos_texto, prog_y):
        if registro["NO. INVENTARIO"]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["NO_INVENTARIO"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["NO. INVENTARIO"] = texto
                    break
        
        if not registro["NO. INVENTARIO"]:
            for i in range(-3, 4):
                for elem in elementos_texto:
                    if abs(elem["y0"] - (prog_y + i * 15)) <= 8:
                        texto = elem["texto"].strip()
                        if re.match(self.patterns["NO_INVENTARIO"], texto):
                            registro["NO. INVENTARIO"] = texto
                            return registro
        
        return registro

    def assign_by_position(self, elementos_fila, column_positions, prog_num):
        registro = {col: "" for col in columnas_clave}
        registro["PROG"] = str(prog_num)
        
        elementos_fila.sort(key=lambda x: x["x0"])
        
        elementos_restantes = []
        
        for elem in elementos_fila:
            texto = elem["texto"].strip()
            asignado = False
            
            if texto == str(prog_num) or texto in ["000", "0000", "00"]:
                asignado = True
            
            elif re.match(self.patterns["TIPO_ADQ"], texto):
                if not registro["TIPO ADQ."]:
                    registro["TIPO ADQ."] = texto
                    asignado = True
            
            elif re.match(self.patterns["NO_INVENTARIO"], texto):
                if not registro["NO. INVENTARIO"]:
                    registro["NO. INVENTARIO"] = texto
                    asignado = True
            
            elif re.match(self.patterns["COSTO"], texto):
                if not registro["COSTO"]:
                    registro["COSTO"] = texto
                    asignado = True
            
            elif (hasattr(elem, 'es_multilinea') or 
                  (texto and len(texto) > 15 and texto[0].isupper() and texto[-1].islower() and 
                   any(keyword in texto.upper() for keyword in ["IIFEQ", "INGRESOS", "CONAFE", "P.A.R.E.I.B", "PROV", "U.S.E.B.E.Q", "OTROS", "PROG.", "REFORMA", "REPOSICION"]))):
                if not registro["DESC. TIPO ADQ."]:
                    registro["DESC. TIPO ADQ."] = texto
                    asignado = True
            
            elif texto.upper() in self.known_brands:
                if not registro["MARCA"]:
                    registro["MARCA"] = texto
                    asignado = True
            
            if not asignado:
                elementos_restantes.append(elem)
        
        for elem in elementos_restantes:
            texto = elem["texto"].strip()
            x_pos = elem["x0"]
            
            mejor_columna = None
            menor_distancia = float('inf')
            
            for col, col_x in column_positions.items():
                if registro[col]:
                    continue 
                
                distancia = abs(x_pos - col_x)
                
                if col == "OBSERVACIONES" and "MARCA" in column_positions:
                    if distancia < 40 and x_pos < column_positions["MARCA"] - 30:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
                elif col == "MARCA" and "OBSERVACIONES" in column_positions:
                    if distancia < 40 and x_pos > column_positions["OBSERVACIONES"] + 30:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
                else:
                    if distancia < 60:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
            
            if mejor_columna:
                registro[mejor_columna] = texto
        
        return registro

    def extract_by_positions(self, elementos_texto, page_num):
        if not elementos_texto:
            return []
        
        elementos_texto = self.detect_multiline_desc_tipo(elementos_texto)
        
        prog_positions = self.find_all_prog_positions(elementos_texto, page_num)
        if not prog_positions:
            return []
        
        first_y = prog_positions[0]["y"]
        column_positions = self.detect_column_positions(elementos_texto, first_y)
        if not column_positions:
            column_positions = self.estimate_column_positions(elementos_texto, first_y)
        
        registros = []
        
        for prog_info in prog_positions:
            prog_num = prog_info["prog"]
            elementos_fila = prog_info["elementos_fila"]
            prog_y = prog_info["y"]
            
            clave_unica = f"{page_num}_{prog_num}"
            if clave_unica in self.prog_procesados:
                continue
            
            self.prog_procesados.add(clave_unica)
            
            registro = self.assign_by_position(elementos_fila, column_positions, prog_num)
            
            registro = self.clean_invalid_values(registro)
            
            registro = self.find_missing_no_inventario(registro, elementos_texto, prog_y)
            
            if prog_num > self.ultimo_prog:
                self.ultimo_prog = prog_num
            
            registros.append(registro)
        
        return registros

def extraer_datos_por_celdas(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    extractor = SimpleTableExtractor()
    resultados_totales = []
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text_dict = page.get_text("dict")
        
        elementos_texto = []
        for block in text_dict.get("blocks", []):
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        texto = span["text"].strip()
                        
                        if extractor.is_bold_text(span):
                            continue
                        
                        if texto and not any(bad in texto for bad in ["Declaro", "protesta", "NOMBRE", "FIRMA", "TOTAL", "CEDULA", "CVE.", "000", "0000", "00"]):
                            elementos_texto.append({
                                "texto": texto,
                                "x0": round(span["bbox"][0], 1),
                                "y0": round(span["bbox"][1], 1),
                                "x1": round(span["bbox"][2], 1),
                                "y1": round(span["bbox"][3], 1)
                            })
        
        if elementos_texto:
            registros_pagina = extractor.extract_by_positions(elementos_texto, page_num + 1)
            resultados_totales.extend(registros_pagina)
    
    doc.close()
    
    resultados_totales.sort(key=lambda x: int(x.get("PROG", "999")))
    
    return resultados_totales

@app.get("/")
async def root():
    return {"message": "API Extractor V13", "status": "OK"}

@app.post("/procesar-pdf")
async def procesar_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        return {"error": "El archivo debe ser un PDF"}
    
    pdf_bytes = await file.read()
    
    try:
        resultados = extraer_datos_por_celdas(pdf_bytes)
        
        if not resultados:
            return {"mensaje": "No se encontraron registros válidos"}
        
        return {
            "total_registros": len(resultados),
            "resultados": resultados
        }
    except Exception as e:
        import traceback
        return {
            "error": str(e), 
            "traceback": traceback.format_exc()
        }

@app.get("/debug")
async def debug():
    return {"message": "Extractor V13 - Detección mejorada"}































from fastapi import FastAPI, UploadFile, File
import fitz
import re
from collections import defaultdict

app = FastAPI()

columnas_clave = [
    "PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO",
    "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.",
    "NO. INVENTARIO"
]

class SimpleTableExtractor:
    
    def __init__(self):
        self.ultimo_prog = 0
        self.prog_procesados = set()
        
        self.patterns = {
            "PROG": r'^[1-9]\d{0,3}$',
            "COSTO": r'^\d{1,3}(,\d{3})*\.\d{2}$',
            "TIPO_ADQ": r'^[A-Z]\d{1,2}-\d{1,2}$',
            "NO_INVENTARIO": r'^\d{4,5}-\d{4}-\d{5}-\d{1}$'
        }
        
        self.known_brands = {"NOKIA", "CISCO", "STEREN", "CAMBIUM", "SOUND", "TRACK", "ACTECK", "BAF", "OHAUS", "EPSON", "CANON", "HP", "DELL", "PIONER", "SIN MARCA", "OLYMPIA", "IROSCOPE"}

    def is_bold_text(self, span):
        font_flags = span.get("flags", 0)
        if font_flags & 16:
            return True
        
        font_name = span.get("font", "").lower()
        if any(bold_word in font_name for bold_word in ["bold", "black", "heavy", "demi"]):
            return True
        
        return False

    def clean_invalid_values(self, registro):
        for campo in registro:
            if registro[campo] in ["000", "0000", "00", "CODI"]:
                registro[campo] = ""
        return registro

    def find_missing_costo(self, registro, elementos_texto, prog_y):
        if registro["COSTO"]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["COSTO"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["COSTO"] = texto
                    break
        
        if not registro["COSTO"]:
            for i in range(-2, 3):
                for elem in elementos_texto:
                    if abs(elem["y0"] - (prog_y + i * 12)) <= 6:
                        texto = elem["texto"].strip()
                        if re.match(self.patterns["COSTO"], texto):
                            registro["COSTO"] = texto
                            return registro
        
        return registro

    def detect_codi_column(self, elementos_texto, start_y):
        codi_x_position = None
        header_y = None
        
        for elem in elementos_texto:
            if elem["y0"] < start_y - 10:
                texto_upper = elem["texto"].upper()
                if any(header in texto_upper for header in ["PROG", "DESCRIPCION", "OBSERVACIONES"]):
                    if header_y is None or elem["y0"] > header_y:
                        header_y = elem["y0"]
        
        if header_y is not None:
            for elem in elementos_texto:
                if abs(elem["y0"] - header_y) <= 10:
                    texto = elem["texto"].upper()
                    if "CODI" in texto and len(texto) <= 6:
                        codi_x_position = elem["x0"]
                        break
        
        return codi_x_position

    def detect_multiline_desc_tipo(self, elementos_texto):
        elementos_combinados = []
        i = 0
        
        while i < len(elementos_texto):
            elem = elementos_texto[i]
            texto = elem["texto"].strip()
            
            if (texto and texto[0].isupper() and 
                any(keyword in texto.upper() for keyword in ["IIFEQ", "INGRESOS", "CONAFE", "P.A.R.E.I.B", "PROV", "U.S.E.B.E.Q", "OTROS", "PROG.", "REFORMA", "REPOSICION", "C.A.P.C.E.Q", "I.L.C.E"])):
                
                texto_completo = texto
                j = i + 1
                elementos_utilizados = [elem]
                
                while j < len(elementos_texto):
                    siguiente = elementos_texto[j]
                    siguiente_texto = siguiente["texto"].strip()
                    
                    if abs(siguiente["y0"] - elem["y0"]) > 25:
                        j += 1
                        continue
                    
                    if abs(siguiente["x0"] - elem["x0"]) > 120:
                        j += 1
                        continue
                    
                    if siguiente_texto and siguiente_texto[-1].islower():
                        texto_completo += " " + siguiente_texto
                        elementos_utilizados.append(siguiente)
                        
                        elemento_combinado = {
                            "texto": texto_completo,
                            "x0": elem["x0"],
                            "y0": elem["y0"],
                            "x1": max(e["x1"] for e in elementos_utilizados),
                            "y1": max(e["y1"] for e in elementos_utilizados),
                            "es_multilinea": True
                        }
                        elementos_combinados.append(elemento_combinado)
                        i = j + 1
                        break
                    else:
                        texto_completo += " " + siguiente_texto
                        elementos_utilizados.append(siguiente)
                        j += 1
                else:
                    elementos_combinados.append(elem)
                    i += 1
            else:
                elementos_combinados.append(elem)
                i += 1
        
        return elementos_combinados

    def find_all_prog_positions(self, elementos_texto, page_num):
        if page_num == 1:
            prog_inicial = 1
        else:
            prog_inicial = self.ultimo_prog + 1
        
        prog_positions = []
        prog_encontrados = set()
        
        prog_actual = prog_inicial
        for _ in range(1000):
            encontrado = False
            for elem in elementos_texto:
                if (elem["texto"].strip() == str(prog_actual) and 
                    prog_actual not in prog_encontrados):
                    
                    y_pos = elem["y0"]
                    elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 5]
                    
                    if len(elementos_fila) >= 3:
                        prog_positions.append({
                            "prog": prog_actual,
                            "y": y_pos,
                            "elementos_fila": elementos_fila
                        })
                        prog_encontrados.add(prog_actual)
                        prog_actual += 1
                        encontrado = True
                        break
            
            if not encontrado:
                if prog_actual > prog_inicial + 3:
                    for elem in elementos_texto:
                        if (elem["texto"].strip() == "1" and 
                            1 not in prog_encontrados):
                            
                            y_pos = elem["y0"]
                            elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 5]
                            
                            if len(elementos_fila) >= 3:
                                prog_positions.append({
                                    "prog": 1,
                                    "y": y_pos,
                                    "elementos_fila": elementos_fila
                                })
                                prog_encontrados.add(1)
                                prog_actual = 2
                                encontrado = True
                                break
                
                if not encontrado:
                    break
        
        return prog_positions

    def detect_column_positions(self, elementos_texto, start_y):
        column_positions = {}
        header_y = None
        
        for elem in elementos_texto:
            if elem["y0"] < start_y - 10:
                texto_upper = elem["texto"].upper()
                if any(header in texto_upper for header in ["PROG", "DESCRIPCION", "OBSERVACIONES"]):
                    if header_y is None or elem["y0"] > header_y:
                        header_y = elem["y0"]
        
        if header_y is None:
            return self.estimate_column_positions(elementos_texto, start_y)
        
        for elem in elementos_texto:
            if abs(elem["y0"] - header_y) <= 10:
                texto = elem["texto"].upper()
                x_pos = elem["x0"]
                
                if "PROG" in texto and "PROG." not in texto:
                    column_positions["PROG"] = x_pos
                elif "DESCRIPCION" in texto:
                    column_positions["DESCRIPCION"] = x_pos
                elif "OBSERVACIONES" in texto:
                    column_positions["OBSERVACIONES"] = x_pos
                elif "MARCA" in texto and len(texto) <= 8:
                    column_positions["MARCA"] = x_pos
                elif "MODELO" in texto and len(texto) <= 8:
                    column_positions["MODELO"] = x_pos
                elif "SERIE" in texto and len(texto) <= 8:
                    column_positions["SERIE"] = x_pos
                elif "COSTO" in texto or ("BIEN" in texto and "TIPO" in texto):
                    column_positions["COSTO"] = x_pos
                elif "TIPO" in texto and "ADQ" in texto and "DESC" not in texto:
                    column_positions["TIPO ADQ."] = x_pos
                elif "DESC" in texto and "TIPO" in texto:
                    column_positions["DESC. TIPO ADQ."] = x_pos
                elif "INVENTARIO" in texto:
                    column_positions["NO. INVENTARIO"] = x_pos
        
        return column_positions

    def estimate_column_positions(self, elementos_texto, start_y):
        elementos_datos = [e for e in elementos_texto if start_y <= e["y0"] <= start_y + 50]
        
        codi_x_position = self.detect_codi_column(elementos_texto, start_y)
        
        x_positions = []
        for elem in elementos_datos:
            if elem["texto"].strip() not in ["000", "0000", "00", "CODI"]:
                if codi_x_position is None or abs(elem["x0"] - codi_x_position) > 30:
                    x_positions.append(elem["x0"])
        
        x_positions.sort()
        unique_x = []
        for x in x_positions:
            if not unique_x or abs(x - unique_x[-1]) > 25:
                unique_x.append(x)
        
        column_order = ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.", "NO. INVENTARIO"]
        column_positions = {}
        
        for i, col in enumerate(column_order):
            if i < len(unique_x):
                column_positions[col] = unique_x[i]
        
        return column_positions

    def find_missing_no_inventario_advanced(self, registro, elementos_texto, prog_y, page_num):
        """
        Método avanzado para encontrar NO. INVENTARIO faltante
        Busca con múltiples estrategias y patrones flexibles
        """
        if registro["NO. INVENTARIO"]:
            return registro
        
        # Patrón original más flexible
        pattern_flexible = r'^\d{10}-\d{4}-\d{5}-\d{2}$'
        
        # Estrategia 1: Búsqueda en área extendida (más amplia)
        for radio in [30, 50, 80]:
            elementos_area = []
            for elem in elementos_texto:
                if abs(elem["y0"] - prog_y) <= radio:
                    elementos_area.append(elem)
            
            for elem in elementos_area:
                texto = elem["texto"].strip()
                if re.match(pattern_flexible, texto):
                    if texto not in [registro[k] for k in registro.keys() if registro[k]]:
                        registro["NO. INVENTARIO"] = texto
                        return registro
        
        # Estrategia 2: Búsqueda vertical (arriba y abajo)
        for desplazamiento in range(-10, 11):
            y_busqueda = prog_y + (desplazamiento * 20)
            for elem in elementos_texto:
                if abs(elem["y0"] - y_busqueda) <= 8:
                    texto = elem["texto"].strip()
                    if re.match(pattern_flexible, texto):
                        if texto not in [registro[k] for k in registro.keys() if registro[k]]:
                            registro["NO. INVENTARIO"] = texto
                            return registro
        
        # Estrategia 3: Búsqueda por posición X (derecha de la página)
        elementos_derecha = []
        for elem in elementos_texto:
            if elem["x0"] > 400:  # Lado derecho típico del NO. INVENTARIO
                elementos_derecha.append(elem)
        
        # Buscar en elementos de la derecha cerca del PROG actual
        for elem in elementos_derecha:
            if abs(elem["y0"] - prog_y) <= 40:
                texto = elem["texto"].strip()
                if re.match(pattern_flexible, texto):
                    if texto not in [registro[k] for k in registro.keys() if registro[k]]:
                        registro["NO. INVENTARIO"] = texto
                        return registro
        
        # Estrategia 4: Búsqueda secuencial por página
        # Buscar el siguiente número de inventario disponible en la página
        inventarios_en_pagina = []
        for elem in elementos_texto:
            texto = elem["texto"].strip()
            if re.match(pattern_flexible, texto):
                inventarios_en_pagina.append((elem["y0"], texto))
        
        # Ordenar por posición Y
        inventarios_en_pagina.sort(key=lambda x: x[0])
        
        # Encontrar inventarios ya asignados
        inventarios_asignados = set()
        for elem in elementos_texto:
            # Simular otros registros en la misma página
            y_elem = elem["y0"]
            if abs(y_elem - prog_y) > 15:  # Diferentes filas
                elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_elem) <= 5]
                for elem_fila in elementos_fila:
                    texto_fila = elem_fila["texto"].strip()
                    if re.match(pattern_flexible, texto_fila):
                        inventarios_asignados.add(texto_fila)
        
        # Asignar el primer inventario no asignado cercano al PROG actual
        for y_inv, inventario in inventarios_en_pagina:
            if inventario not in inventarios_asignados:
                if abs(y_inv - prog_y) <= 60:  # Cercanía razonable
                    registro["NO. INVENTARIO"] = inventario
                    return registro
        
        # Estrategia 5: Búsqueda por patrón de secuencia
        # Si hay un patrón secuencial, intentar predecir el siguiente
        try:
            # Buscar inventarios previos para detectar patrón
            inventarios_previos = []
            for elem in elementos_texto:
                if elem["y0"] < prog_y:
                    texto = elem["texto"].strip()
                    if re.match(pattern_flexible, texto):
                        inventarios_previos.append(texto)
            
            if len(inventarios_previos) >= 2:
                # Analizar últimos dos inventarios para detectar secuencia
                ultimo = inventarios_previos[-1]
                penultimo = inventarios_previos[-2]
                
                # Extraer números secuenciales (últimos 5 dígitos antes del final)
                match_ultimo = re.search(r'-(\d{5})-\d{2}$', ultimo)
                match_penultimo = re.search(r'-(\d{5})-\d{2}$', penultimo)
                
                if match_ultimo and match_penultimo:
                    num_ultimo = int(match_ultimo.group(1))
                    num_penultimo = int(match_penultimo.group(1))
                    
                    if num_ultimo == num_penultimo + 1:
                        # Secuencia detectada, predecir siguiente
                        siguiente_num = num_ultimo + 1
                        base_inventario = ultimo.rsplit('-', 2)[0]
                        ultimo_digito = ultimo.split('-')[-1]
                        
                        inventario_predicho = f"{base_inventario}-{siguiente_num:05d}-{ultimo_digito}"
                        
                        # Verificar si existe en el texto
                        for elem in elementos_texto:
                            if elem["texto"].strip() == inventario_predicho:
                                registro["NO. INVENTARIO"] = inventario_predicho
                                return registro
        
        except:
            pass  # Si falla la predicción, continuar con otras estrategias
        
        # Estrategia 6: Búsqueda con tolerancia en el patrón
        # Relajar el patrón para casos especiales
        pattern_relaxed = r'^\d{8,12}-\d{4}-\d{4,6}-\d{1,2}$'
        
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 50:
                texto = elem["texto"].strip()
                if re.match(pattern_relaxed, texto):
                    if texto not in [registro[k] for k in registro.keys() if registro[k]]:
                        registro["NO. INVENTARIO"] = texto
                        return registro
        
        return registro

    def find_missing_no_inventario(self, registro, elementos_texto, prog_y):
        """
        Método original mejorado que llama al método avanzado si no encuentra
        """
        if registro["NO. INVENTARIO"]:
            return registro
        
        # Método original
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["NO_INVENTARIO"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["NO. INVENTARIO"] = texto
                    return registro
        
        if not registro["NO. INVENTARIO"]:
            for i in range(-3, 4):
                for elem in elementos_texto:
                    if abs(elem["y0"] - (prog_y + i * 15)) <= 8:
                        texto = elem["texto"].strip()
                        if re.match(self.patterns["NO_INVENTARIO"], texto):
                            registro["NO. INVENTARIO"] = texto
                            return registro
        
        # Si el método original no encontró nada, usar método avanzado
        if not registro["NO. INVENTARIO"]:
            registro = self.find_missing_no_inventario_advanced(registro, elementos_texto, prog_y, 1)
        
        return registro

    def find_missing_tipo_adq(self, registro, elementos_texto, prog_y):
        if registro["TIPO ADQ."]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if re.match(self.patterns["TIPO_ADQ"], texto):
                if texto not in [registro[k] for k in registro.keys()]:
                    registro["TIPO ADQ."] = texto
                    break
        
        return registro

    def find_missing_desc_tipo_adq(self, registro, elementos_texto, prog_y):
        if registro["DESC. TIPO ADQ."]:
            return registro
        
        elementos_extendidos = []
        for elem in elementos_texto:
            if abs(elem["y0"] - prog_y) <= 20:
                elementos_extendidos.append(elem)
        
        for elem in elementos_extendidos:
            texto = elem["texto"].strip()
            if (hasattr(elem, 'es_multilinea') or 
                (texto and len(texto) > 10 and 
                 any(keyword in texto.upper() for keyword in ["C.A.P.C.E.Q", "I.L.C.E", "IIFEQ", "INGRESOS", "CONAFE", "OTROS", "PROG.", "REFORMA"]))):
                registro["DESC. TIPO ADQ."] = texto
                break
        
        return registro

    def assign_by_position(self, elementos_fila, column_positions, prog_num):
        registro = {col: "" for col in columnas_clave}
        registro["PROG"] = str(prog_num)
        
        elementos_fila.sort(key=lambda x: x["x0"])
        
        codi_x_position = None
        for elem in elementos_fila:
            if elem["texto"].strip().upper() == "CODI":
                codi_x_position = elem["x0"]
                break
        
        elementos_restantes = []
        
        for elem in elementos_fila:
            texto = elem["texto"].strip()
            asignado = False
            
            if texto == str(prog_num) or texto in ["000", "0000", "00", "CODI"]:
                asignado = True
            
            elif codi_x_position is not None and abs(elem["x0"] - codi_x_position) <= 20:
                asignado = True
            
            elif re.match(self.patterns["TIPO_ADQ"], texto):
                if not registro["TIPO ADQ."]:
                    registro["TIPO ADQ."] = texto
                    asignado = True
            
            elif re.match(self.patterns["NO_INVENTARIO"], texto):
                if not registro["NO. INVENTARIO"]:
                    registro["NO. INVENTARIO"] = texto
                    asignado = True
            
            elif re.match(self.patterns["COSTO"], texto):
                if not registro["COSTO"]:
                    registro["COSTO"] = texto
                    asignado = True
            
            elif (hasattr(elem, 'es_multilinea') or 
                  (texto and len(texto) > 15 and texto[0].isupper() and texto[-1].islower() and 
                   any(keyword in texto.upper() for keyword in ["IIFEQ", "INGRESOS", "CONAFE", "P.A.R.E.I.B", "PROV", "U.S.E.B.E.Q", "OTROS", "PROG.", "REFORMA", "REPOSICION", "C.A.P.C.E.Q", "I.L.C.E"]))):
                if not registro["DESC. TIPO ADQ."]:
                    registro["DESC. TIPO ADQ."] = texto
                    asignado = True
            
            elif texto.upper() in self.known_brands:
                if not registro["MARCA"]:
                    registro["MARCA"] = texto
                    asignado = True
            
            if not asignado:
                elementos_restantes.append(elem)
        
        for elem in elementos_restantes:
            texto = elem["texto"].strip()
            x_pos = elem["x0"]
            
            if codi_x_position is not None and abs(x_pos - codi_x_position) <= 20:
                continue
            
            mejor_columna = None
            menor_distancia = float('inf')
            
            for col, col_x in column_positions.items():
                if registro[col]:
                    continue 
                
                distancia = abs(x_pos - col_x)
                
                if col == "OBSERVACIONES" and "MARCA" in column_positions:
                    if distancia < 40 and x_pos < column_positions["MARCA"] - 30:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
                elif col == "MARCA" and "OBSERVACIONES" in column_positions:
                    if distancia < 40 and x_pos > column_positions["OBSERVACIONES"] + 30:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
                else:
                    if distancia < 60:
                        if distancia < menor_distancia:
                            menor_distancia = distancia
                            mejor_columna = col
            
            if mejor_columna:
                registro[mejor_columna] = texto
        
        return registro

    def validate_and_correct_inventarios(self, registros):
        """
        Valida y corrige números de inventario duplicados
        Asigna inventarios únicos manteniendo secuencia lógica
        """
        if not registros:
            return registros
        
        # Detectar duplicados y crear mapa de corrección
        inventarios_usados = set()
        registros_corregidos = []
        
        for i, registro in enumerate(registros):
            inventario_original = registro.get("NO. INVENTARIO", "")
            
            if inventario_original and inventario_original in inventarios_usados:
                # Buscar inventario alternativo
                nuevo_inventario = self.find_alternative_inventario(
                    inventario_original, inventarios_usados, i
                )
                if nuevo_inventario:
                    registro["NO. INVENTARIO"] = nuevo_inventario
                    inventarios_usados.add(nuevo_inventario)
                else:
                    # Generar inventario secuencial si no se encuentra alternativo
                    inventario_generado = self.generate_sequential_inventario(
                        inventario_original, inventarios_usados
                    )
                    registro["NO. INVENTARIO"] = inventario_generado
                    inventarios_usados.add(inventario_generado)
            elif inventario_original:
                inventarios_usados.add(inventario_original)
            
            registros_corregidos.append(registro)
        
        return registros_corregidos

    def find_alternative_inventario(self, inventario_base, inventarios_usados, registro_index):
        """
        Busca inventario alternativo basado en el patrón del inventario base
        """
        if not inventario_base:
            return None
        
        try:
            # Extraer partes del inventario: XXXXXXXXXX-YYYY-ZZZZZ-WW
            partes = inventario_base.split('-')
            if len(partes) != 4:
                return None
            
            base, año, secuencia, final = partes
            secuencia_num = int(secuencia)
            
            # Buscar secuencia alternativa (incrementar/decrementar)
            for offset in range(1, 20):  # Probar hasta 20 números adelante/atrás
                for direction in [1, -1]:  # Adelante y atrás
                    nueva_secuencia = secuencia_num + (offset * direction)
                    
                    if nueva_secuencia > 0:  # Solo números positivos
                        inventario_alternativo = f"{base}-{año}-{nueva_secuencia:05d}-{final}"
                        
                        if inventario_alternativo not in inventarios_usados:
                            return inventario_alternativo
            
            return None
            
        except (ValueError, IndexError):
            return None

    def generate_sequential_inventario(self, inventario_base, inventarios_usados):
        """
        Genera inventario secuencial cuando no se encuentra alternativo
        """
        if not inventario_base:
            return f"GENERATED-{len(inventarios_usados):04d}-00001-01"
        
        try:
            partes = inventario_base.split('-')
            if len(partes) != 4:
                return f"GENERATED-{len(inventarios_usados):04d}-00001-01"
            
            base, año, secuencia, final = partes
            secuencia_num = int(secuencia)
            
            # Generar secuencia incremental hasta encontrar disponible
            contador = 1
            while contador <= 9999:
                nueva_secuencia = secuencia_num + contador
                inventario_generado = f"{base}-{año}-{nueva_secuencia:05d}-{final}"
                
                if inventario_generado not in inventarios_usados:
                    return inventario_generado
                
                contador += 1
            
            # Si no se encuentra, usar timestamp como respaldo
            import time
            timestamp = int(time.time()) % 99999
            return f"{base}-{año}-{timestamp:05d}-{final}"
            
        except (ValueError, IndexError):
            return f"GENERATED-{len(inventarios_usados):04d}-00001-01"

    def validate_and_correct_data_integrity(self, registros):
        """
        Valida y corrige la integridad general de los datos
        """
        registros_corregidos = []
        
        for registro in registros:
            registro_corregido = registro.copy()
            
            # Validar y corregir PROG secuencial
            registro_corregido = self.validate_prog_sequence(registro_corregido, registros)
            
            # Validar y corregir campos obligatorios vacíos
            registro_corregido = self.validate_required_fields(registro_corregido)
            
            # Validar y corregir patrones de datos
            registro_corregido = self.validate_data_patterns(registro_corregido)
            
            registros_corregidos.append(registro_corregido)
        
        return registros_corregidos

    def validate_prog_sequence(self, registro, todos_registros):
        """
        Valida que la secuencia PROG sea correcta
        """
        prog_actual = registro.get("PROG", "")
        
        if not prog_actual or not prog_actual.isdigit():
            # Asignar PROG basado en posición en la lista
            progs_existentes = [int(r.get("PROG", "0")) for r in todos_registros if r.get("PROG", "").isdigit()]
            if progs_existentes:
                registro["PROG"] = str(max(progs_existentes) + 1)
            else:
                registro["PROG"] = "1"
        
        return registro

    def validate_required_fields(self, registro):
        """
        Valida que los campos obligatorios no estén vacíos
        """
        # Si DESCRIPCION está vacía, marcar como pendiente
        if not registro.get("DESCRIPCION", "").strip():
            registro["DESCRIPCION"] = "DESCRIPCION PENDIENTE"
        
        # Si NO. INVENTARIO está vacío, generar temporal
        if not registro.get("NO. INVENTARIO", "").strip():
            prog = registro.get("PROG", "0")
            registro["NO. INVENTARIO"] = f"TEMP-{prog:04s}-00000-00"
        
        return registro

    def validate_data_patterns(self, registro):
        """
        Valida que los patrones de datos sean correctos
        """
        # Validar patrón de COSTO
        costo = registro.get("COSTO", "")
        if costo and not re.match(self.patterns["COSTO"], costo):
            # Intentar corregir formato de costo
            try:
                # Eliminar caracteres no numéricos excepto punto y coma
                costo_limpio = re.sub(r'[^\d.,]', '', costo)
                if '.' in costo_limpio:
                    partes = costo_limpio.split('.')
                    if len(partes) == 2 and len(partes[1]) <= 2:
                        registro["COSTO"] = f"{partes[0]}.{partes[1]:0<2}"
            except:
                pass  # Mantener valor original si falla corrección
        
        # Validar patrón de TIPO ADQ.
        tipo_adq = registro.get("TIPO ADQ.", "")
        if tipo_adq and not re.match(self.patterns["TIPO_ADQ"], tipo_adq):
            # Intentar corregir formato
            if re.match(r'^[A-Z]\d{1,2}[-_]\d{1,2}$', tipo_adq):
                registro["TIPO ADQ."] = tipo_adq.replace('_', '-')
        
        return registro

    def comprehensive_inventory_validation(self, registros):
        """
        Validación comprehensiva de inventarios con múltiples estrategias
        """
        if not registros:
            return registros
        
        # Paso 1: Identificar todos los inventarios únicos disponibles
        todos_inventarios_disponibles = set()
        
        # Buscar en todo el documento inventarios con patrón correcto
        for registro in registros:
            inventario = registro.get("NO. INVENTARIO", "")
            if inventario and re.match(r'^\d{10}-\d{4}-\d{5}-\d{2}$', inventario):
                todos_inventarios_disponibles.add(inventario)
        
        # Paso 2: Asignación inteligente evitando duplicados
        inventarios_asignados = set()
        registros_procesados = []
        
        for i, registro in enumerate(registros):
            inventario_original = registro.get("NO. INVENTARIO", "")
            
            if not inventario_original:
                # Buscar inventario no asignado cercano al índice actual
                inventario_disponible = self.find_available_inventory_by_index(
                    todos_inventarios_disponibles, inventarios_asignados, i
                )
                if inventario_disponible:
                    registro["NO. INVENTARIO"] = inventario_disponible
                    inventarios_asignados.add(inventario_disponible)
            
            elif inventario_original in inventarios_asignados:
                # Duplicado detectado, buscar alternativo
                inventario_alternativo = self.find_next_available_inventory(
                    inventario_original, todos_inventarios_disponibles, inventarios_asignados
                )
                if inventario_alternativo:
                    registro["NO. INVENTARIO"] = inventario_alternativo
                    inventarios_asignados.add(inventario_alternativo)
                else:
                    # Generar nuevo inventario
                    inventario_generado = self.generate_unique_inventory(
                        inventario_original, inventarios_asignados
                    )
                    registro["NO. INVENTARIO"] = inventario_generado
                    inventarios_asignados.add(inventario_generado)
            
            else:
                # Inventario único, marcar como asignado
                inventarios_asignados.add(inventario_original)
            
            registros_procesados.append(registro)
        
        return registros_procesados

    def find_available_inventory_by_index(self, todos_inventarios, inventarios_asignados, index):
        """
        Encuentra inventario disponible basado en el índice del registro
        """
        inventarios_ordenados = sorted(list(todos_inventarios))
        
        # Buscar inventario cercano al índice
        for offset in range(len(inventarios_ordenados)):
            for direction in [0, 1, -1]:  # Mismo índice, adelante, atrás
                idx_busqueda = index + (offset * direction)
                
                if 0 <= idx_busqueda < len(inventarios_ordenados):
                    inventario_candidato = inventarios_ordenados[idx_busqueda]
                    if inventario_candidato not in inventarios_asignados:
                        return inventario_candidato
        
        return None

    def find_next_available_inventory(self, inventario_base, todos_inventarios, inventarios_asignados):
        """
        Encuentra el siguiente inventario disponible basado en el inventario base
        """
        inventarios_ordenados = sorted(list(todos_inventarios))
        
        try:
            indice_base = inventarios_ordenados.index(inventario_base)
            
            # Buscar hacia adelante
            for i in range(indice_base + 1, len(inventarios_ordenados)):
                if inventarios_ordenados[i] not in inventarios_asignados:
                    return inventarios_ordenados[i]
            
            # Buscar hacia atrás
            for i in range(indice_base - 1, -1, -1):
                if inventarios_ordenados[i] not in inventarios_asignados:
                    return inventarios_ordenados[i]
        
        except ValueError:
            # Si el inventario base no está en la lista, buscar el más cercano
            for inventario in inventarios_ordenados:
                if inventario not in inventarios_asignados:
                    return inventario
        
        return None

    def generate_unique_inventory(self, inventario_base, inventarios_asignados):
        """
        Genera inventario único cuando no hay disponibles
        """
        if not inventario_base:
            base_generado = "9999999999-2024-00001-01"
        else:
            try:
                partes = inventario_base.split('-')
                base, año, secuencia, final = partes
                secuencia_num = int(secuencia)
                
                # Incrementar secuencia hasta encontrar único
                contador = 1
                while contador <= 9999:
                    nueva_secuencia = secuencia_num + contador
                    inventario_candidato = f"{base}-{año}-{nueva_secuencia:05d}-{final}"
                    
                    if inventario_candidato not in inventarios_asignados:
                        return inventario_candidato
                    
                    contador += 1
                
                # Si no se encuentra, usar timestamp
                import time
                timestamp = int(time.time()) % 99999
                base_generado = f"{base}-{año}-{timestamp:05d}-{final}"
            
            except:
                base_generado = "9999999999-2024-00001-01"
        
        # Asegurar que sea único
        contador = 1
        inventario_final = base_generado
        while inventario_final in inventarios_asignados:
            try:
                partes = base_generado.split('-')
                base, año, secuencia, final = partes
                secuencia_num = int(secuencia) + contador
                inventario_final = f"{base}-{año}-{secuencia_num:05d}-{final}"
                contador += 1
            except:
                inventario_final = f"GEN{contador:06d}-2024-00001-01"
                contador += 1
        
        return inventario_final

    def extract_by_positions(self, elementos_texto, page_num):
        if not elementos_texto:
            return []
        
        elementos_texto = self.detect_multiline_desc_tipo(elementos_texto)
        
        prog_positions = self.find_all_prog_positions(elementos_texto, page_num)
        if not prog_positions:
            return []
        
        first_y = prog_positions[0]["y"]
        column_positions = self.detect_column_positions(elementos_texto, first_y)
        if not column_positions:
            column_positions = self.estimate_column_positions(elementos_texto, first_y)
        
        registros = []
        
        for prog_info in prog_positions:
            prog_num = prog_info["prog"]
            elementos_fila = prog_info["elementos_fila"]
            prog_y = prog_info["y"]
            
            clave_unica = f"{page_num}_{prog_num}"
            if clave_unica in self.prog_procesados:
                continue
            
            self.prog_procesados.add(clave_unica)
            
            registro = self.assign_by_position(elementos_fila, column_positions, prog_num)
            
            registro = self.clean_invalid_values(registro)
            
            registro = self.find_missing_no_inventario(registro, elementos_texto, prog_y)
            
            registro = self.find_missing_costo(registro, elementos_texto, prog_y)
            
            registro = self.find_missing_tipo_adq(registro, elementos_texto, prog_y)
            
            registro = self.find_missing_desc_tipo_adq(registro, elementos_texto, prog_y)
            
            if prog_num > self.ultimo_prog:
                self.ultimo_prog = prog_num
            
            registros.append(registro)
        
        return registros

def extraer_datos_por_celdas(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    extractor = SimpleTableExtractor()
    resultados_totales = []
    
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text_dict = page.get_text("dict")
        
        elementos_texto = []
        for block in text_dict.get("blocks", []):
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        texto = span["text"].strip()
                        
                        if extractor.is_bold_text(span):
                            continue
                        
                        if texto and not any(bad in texto for bad in ["Declaro", "protesta", "NOMBRE", "FIRMA", "TOTAL", "CEDULA", "CVE.", "CODI"]) and texto not in ["000", "0000", "00"]:
                            elementos_texto.append({
                                "texto": texto,
                                "x0": round(span["bbox"][0], 1),
                                "y0": round(span["bbox"][1], 1),
                                "x1": round(span["bbox"][2], 1),
                                "y1": round(span["bbox"][3], 1)
                            })
        
        if elementos_texto:
            registros_pagina = extractor.extract_by_positions(elementos_texto, page_num + 1)
            resultados_totales.extend(registros_pagina)
    
    doc.close()
    
    resultados_totales.sort(key=lambda x: int(x.get("PROG", "999")))
    
    return resultados_totales
