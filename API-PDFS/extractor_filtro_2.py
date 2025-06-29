import re
import fitz
import camelot
from extractor import AdvancedTableExtractor

columnas_clave = [
    "PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO",
    "SERIE", "COSTO", "TIPO ADQ.", "DESC. TIPO ADQ.",
    "NO. INVENTARIO"
]

EXCLUDE = {"Declaro","protesta","NOMBRE","FIRMA","TOTAL","CEDULA","CVE.","CODI","000","0000","00","SELLO","NOMBRE Y FIRMA DEL TITULAR"}

COLUMN_AREA_CONFIG = {
    "PROG": {
        "x_min_offset": 18,
        "x_max_offset": 20,
        "tolerance": 5
    },
    "DESCRIPCION": {
        "x_min_offset": 20,
        "x_max_offset": 100,
        "tolerance": 20
    },
    "OBSERVACIONES": {
        "x_min_offset": 110,
        "x_max_offset": 200,
        "tolerance": 20
    },
    "MARCA": {
        "x_min_offset": 210,
        "x_max_offset": 350,
        "tolerance": 10
    },
    "MODELO": {
        "x_min_offset": 360,
        "x_max_offset": 400,
        "tolerance": 10
    },
    "SERIE": {
        "x_min_offset": 400,
        "x_max_offset": 460,
        "tolerance": 10
    },
    "COSTO": {
        "x_min_offset": 470,
        "x_max_offset": 560,
        "tolerance": 10
    },
    "TIPO ADQ.": {
        "x_min_offset": 580,
        "x_max_offset": 600,
        "tolerance": 15
    },
    "DESC. TIPO ADQ.": {
        "x_min_offset": 630,
        "x_max_offset": 700,
        "tolerance": 25
    },
    "NO. INVENTARIO": {
        "x_min_offset": 780,
        "x_max_offset": 800,
        "tolerance": 15
    }
}

column_patterns = {
    "NO. INVENTARIO": re.compile(r'^\d{5,}-\d{4}-\d{4,6}-\d{1,2}$'),
    "COSTO":          re.compile(r'^[\d\.,]+\s*$'),
    "PROG":           re.compile(r'^\d{1,3}\s*$'),
    "TIPO_ADQ":       re.compile(r'^([A-Z]\d{1,2}-\d{1,2}|\d{1,3})$'),
    "SERIE_NUM":      re.compile(r'^\d{4,}$'),
    "MODELO_SLASH":   re.compile(r'.*/.*'),
    "DESC_TIPO_ADQ":  re.compile(r'C\.A\.P\.C\.E\.Q|I\.L\.C\.E|CONAFE|Muebles|Instrumental|Equipos|P\.A\.R\.E\.I\.B|P\.E\.C\.|U\.S\.E\.B\.E\.Q'),
    "DESCRIPCION_VALIDA": re.compile(r'^.*[A-Za-z].*$'),
}

class AreaMappedExtractor:
    def __init__(self):
        self.column_areas = {}
        self.ultimo_prog = 0
        self.progs_usados = set()
        self.header_y_positions = set()
        self.known_brands = {
            "OLYMPIA", "NOKIA", "CISCO", "SAMSUNG", "HP", "DELL", "CANON", 
            "EPSON", "BROTHER", "LEXMARK", "XEROX", "PANASONIC", "SONY"
        }
        
    def detect_and_exclude_headers(self, page):
        print("🚫 Detectando headers...")
        
        text_dict = page.get_text("dict")
        
        for block in text_dict.get("blocks", []):
            if "lines" not in block:
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    text = span["text"].strip().upper()
                    y_pos = span["bbox"][1]
                    
                    if text in ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", 
                              "SERIE", "COSTO", "TIPO ADQ", "DESC. TIPO ADQ", "NO. INVENTARIO",
                              "COSTO DEL BIEN", "PROG DESCRIPCION"]:
                        self.header_y_positions.add(round(y_pos, 1))
                        print(f"  🚫 Header: '{text}' Y={y_pos}")
                    
                    elif any(titulo in text for titulo in [
                        "UNIDAD DE SERVICIOS", "DIRECCION DE ADMINISTRACION", 
                        "DEPARTAMENTO", "SUBJEFATURA", "CEDULA", "BIENES DE TIPO",
                        "PATRIMONIO", "AREA O PLANTEL"
                    ]):
                        self.header_y_positions.add(round(y_pos, 1))
                        print(f"  🚫 Título: '{text[:20]}...' Y={y_pos}")
                    
                    elif y_pos < 120:
                        self.header_y_positions.add(round(y_pos, 1))
                        print(f"  🚫 Header superior: '{text[:15]}...' Y={y_pos}")
    
    def is_header_position(self, y_pos, tolerance=4):
        for header_y in self.header_y_positions:
            if abs(y_pos - header_y) <= tolerance:
                return True
        return False
    
    def setup_default_areas(self):
        print("🔧 Configurando áreas corregidas...")
        
        for col_name, config in COLUMN_AREA_CONFIG.items():
            self.column_areas[col_name] = {
                "x_min": config["x_min_offset"],
                "x_max": config["x_max_offset"], 
                "tolerance": config["tolerance"]
            }
            print(f"  📏 {col_name}: X={self.column_areas[col_name]['x_min']}-{self.column_areas[col_name]['x_max']} (±{config['tolerance']})")
    
    def group_multiline_elements(self, elementos_filtrados):
        print("🔗 Agrupando elementos multilínea...")
        
        filas_raw = {}
        for elem in elementos_filtrados:
            y_key = round(elem["y0"] / 6) * 6
            if y_key not in filas_raw:
                filas_raw[y_key] = []
            filas_raw[y_key].append(elem)
        
        filas_fusionadas = {}
        y_positions = sorted(filas_raw.keys())
        
        for i, y_pos in enumerate(y_positions):
            elementos_actuales = filas_raw[y_pos]
            
            debe_fusionar = False
            if i > 0:
                y_anterior = y_positions[i-1]
                elementos_anteriores = filas_raw[y_anterior]
                
                distancia_y = abs(y_pos - y_anterior)
                tiene_prog_actual = any(re.match(column_patterns["PROG"], elem["texto"].strip()) and elem["x0"] < 55 
                                      for elem in elementos_actuales)
                tiene_inventario_actual = any(re.match(column_patterns["NO. INVENTARIO"], elem["texto"].strip()) 
                                            for elem in elementos_actuales)
                tiene_prog_anterior = any(re.match(column_patterns["PROG"], elem["texto"].strip()) and elem["x0"] < 55 
                                        for elem in elementos_anteriores)
                
                if (distancia_y < 18 and
                    not tiene_prog_actual and 
                    not tiene_inventario_actual and 
                    tiene_prog_anterior):
                    debe_fusionar = True
                    print(f"  🔗 Fusionando Y={y_pos} con Y={y_anterior} (multilínea)")
            
            if debe_fusionar and i > 0:
                y_anterior = y_positions[i-1]
                if y_anterior in filas_fusionadas:
                    filas_fusionadas[y_anterior].extend(elementos_actuales)
                else:
                    filas_fusionadas[y_anterior] = filas_raw[y_anterior] + elementos_actuales
            else:
                filas_fusionadas[y_pos] = elementos_actuales
        
        return filas_fusionadas
    
    def extract_by_area_mapping_corrected(self, elementos_texto, page_num):
        print(f"\n🎯 EXTRACCIÓN CORREGIDA - Página {page_num}")
        
        elementos_filtrados = []
        for elem in elementos_texto:
            texto = elem["texto"].strip()
            y_pos = elem["y0"]
            
            if self.is_header_position(y_pos, tolerance=3):
                continue
                
            if (not texto or 
                texto.upper() in EXCLUDE or 
                len(texto) < 1 or
                texto in [".", ",", ":", ";", "-", "_", "|"]):
                continue
                
            elementos_filtrados.append(elem)
        
        print(f"  📊 Elementos válidos: {len(elementos_filtrados)}")
        
        filas = self.group_multiline_elements(elementos_filtrados)
        
        registros_extraidos = []
        
        for y_pos, elementos_fila in sorted(filas.items()):
            if len(elementos_fila) < 1:
                continue
                
            registro = {col: "" for col in columnas_clave}
            elementos_asignados = 0
            
            print(f"\n📋 Fila Y={y_pos} ({len(elementos_fila)} elementos)")
            
            for elem in elementos_fila:
                texto = elem["texto"].strip()
                x_pos = elem["x0"]
                columna_asignada = None
                
                if re.match(column_patterns["NO. INVENTARIO"], texto) and x_pos > 820:
                    columna_asignada = "NO. INVENTARIO"
                    print(f"    🎯 INVENTARIO: '{texto}' → NO. INVENTARIO (X={x_pos})")
                
                elif re.match(column_patterns["PROG"], texto) and 15 <= x_pos <= 55:
                    prog_num = int(texto)
                    if prog_num not in self.progs_usados:
                        columna_asignada = "PROG"
                        self.progs_usados.add(prog_num)
                        print(f"    🎯 PROG: '{texto}' → PROG (X={x_pos})")
                    else:
                        print(f"    ❌ PROG DUPLICADO: '{texto}' ya usado")
                        continue
                
                elif re.match(column_patterns["TIPO_ADQ"], texto) and 615 <= x_pos <= 630:
                    columna_asignada = "TIPO ADQ."
                    print(f"    🎯 TIPO ADQ: '{texto}' → TIPO ADQ. (X={x_pos})")
                
                elif re.match(column_patterns["COSTO"], texto.replace(',', '').replace('$', '')) and 570 <= x_pos <= 625:
                    columna_asignada = "COSTO"
                    print(f"    🎯 COSTO: '{texto}' → COSTO (X={x_pos})")
                
                elif re.search(column_patterns["DESC_TIPO_ADQ"], texto) and x_pos >= 665:
                    columna_asignada = "DESC. TIPO ADQ."
                    print(f"    🎯 DESC TIPO: '{texto}' → DESC. TIPO ADQ. (X={x_pos})")
                
                elif re.match(column_patterns["SERIE_NUM"], texto) and 520 <= x_pos <= 580:
                    columna_asignada = "SERIE"
                    print(f"    🎯 SERIE: '{texto}' → SERIE (X={x_pos})")
                
                elif "/" in texto and len(texto) < 25 and 480 <= x_pos <= 530:
                    columna_asignada = "MODELO"
                    print(f"    🎯 MODELO: '{texto}' → MODELO (X={x_pos})")
                
                elif any(brand in texto.upper() for brand in self.known_brands) and 440 <= x_pos <= 490:
                    columna_asignada = "MARCA"
                    print(f"    🎯 MARCA: '{texto}' → MARCA (X={x_pos})")
                
                if columna_asignada and not registro[columna_asignada]:
                    registro[columna_asignada] = texto
                    elementos_asignados += 1
            
            for elem in elementos_fila:
                texto = elem["texto"].strip()
                x_pos = elem["x0"]
                
                if any(texto == valor for valor in registro.values() if valor):
                    continue
                
                columna_asignada = self.find_column_by_position_corrected(x_pos, texto)
                
                if columna_asignada:
                    if columna_asignada in ["OBSERVACIONES", "DESC. TIPO ADQ."]:
                        if registro[columna_asignada]:
                            registro[columna_asignada] += " " + texto
                        else:
                            registro[columna_asignada] = texto
                    elif columna_asignada == "DESCRIPCION":
                        if not registro[columna_asignada] or len(texto) < len(registro[columna_asignada]):
                            registro[columna_asignada] = texto
                    else:
                        if not registro[columna_asignada]:
                            registro[columna_asignada] = texto
                    
                    elementos_asignados += 1
                    print(f"    🎯 ÁREA: '{texto}' → {columna_asignada} (X={x_pos})")
                else:
                    print(f"    ❌ FUERA DE ÁREA: '{texto}' (X={x_pos})")
            
            registro_corregido = self.validate_and_fix_record_corrected(registro, elementos_fila)
            
            if self.is_valid_record_corrected(registro_corregido):
                registros_extraidos.append(registro_corregido)
                print(f"    ✅ REGISTRO VÁLIDO: PROG={registro_corregido.get('PROG')}")
            else:
                print(f"    ❌ REGISTRO INVÁLIDO")
        
        return registros_extraidos
    
    def find_column_by_position_corrected(self, x_pos, texto):
        for col_name, area in self.column_areas.items():
            if area["x_min"] <= x_pos <= area["x_max"]:
                return col_name
        
        mejor_columna = None
        menor_distancia = float('inf')
        
        for col_name, area in self.column_areas.items():
            if x_pos < area["x_min"]:
                distancia = area["x_min"] - x_pos
            elif x_pos > area["x_max"]:
                distancia = x_pos - area["x_max"]
            else:
                distancia = 0
            
            tolerance = area["tolerance"]
            
            if distancia <= tolerance and distancia < menor_distancia:
                menor_distancia = distancia
                mejor_columna = col_name
        
        return mejor_columna
    
    def validate_and_fix_record_corrected(self, registro, elementos_fila):
        prog_actual = registro.get("PROG", "").strip()
        if prog_actual and prog_actual.isdigit():
            prog_num = int(prog_actual)
            self.ultimo_prog = max(self.ultimo_prog, prog_num)
        else:
            if (registro.get("DESCRIPCION") or 
                registro.get("NO. INVENTARIO") or 
                registro.get("COSTO")):
                siguiente_prog = self.ultimo_prog + 1
                while siguiente_prog in self.progs_usados:
                    siguiente_prog += 1
                
                registro["PROG"] = str(siguiente_prog)
                self.progs_usados.add(siguiente_prog)
                self.ultimo_prog = siguiente_prog
                print(f"    🔢 PROG asignado: {siguiente_prog}")
        
        descripcion = registro.get("DESCRIPCION", "").strip()
        if descripcion and not re.match(column_patterns["DESCRIPCION_VALIDA"], descripcion):
            print(f"    ❌ DESCRIPCION INVÁLIDA: '{descripcion}' (solo números)")
            for elem in elementos_fila:
                texto = elem["texto"].strip()
                x_pos = elem["x0"]
                
                if (not any(texto == valor for valor in registro.values() if valor) and
                    re.match(column_patterns["DESCRIPCION_VALIDA"], texto) and
                    40 <= x_pos <= 290 and
                    len(texto) > 3):
                    registro["DESCRIPCION"] = texto
                    print(f"    🔄 DESCRIPCION CORREGIDA: '{texto}'")
                    break
        
        observaciones = registro.get("OBSERVACIONES", "").strip()
        if observaciones and "/" in observaciones and len(observaciones) < 25:
            if not registro.get("MODELO"):
                registro["MODELO"] = observaciones
                registro["OBSERVACIONES"] = ""
                print(f"    🔄 MODELO corregido: '{observaciones}' (de OBSERVACIONES)")
        
        marca = registro.get("MARCA", "").strip()
        if marca and re.match(column_patterns["SERIE_NUM"], marca):
            if not registro.get("SERIE"):
                registro["SERIE"] = marca
                registro["MARCA"] = ""
                print(f"    🔄 SERIE corregida: '{marca}' (de MARCA)")
        
        desc_tipo = registro.get("DESC. TIPO ADQ.", "").strip()
        if desc_tipo and re.match(column_patterns["NO. INVENTARIO"], desc_tipo):
            registro["NO. INVENTARIO"] = desc_tipo
            registro["DESC. TIPO ADQ."] = ""
            for elem in elementos_fila:
                txt = elem["texto"].strip()
                x0 = elem["x0"]
                if re.search(column_patterns["DESC_TIPO_ADQ"], txt) and 665 <= x0 <= 825:
                    registro["DESC. TIPO ADQ."] = txt
                    break

        if not registro.get("TIPO ADQ.", "").strip():
            for elem in elementos_fila:
                txt = elem["texto"].strip()
                x0 = elem["x0"]
                if 620 <= x0 <= 665 and re.match(r'^([A-Z]\d{1,2}-\d{1,2}|\d+)$', txt):
                    registro["TIPO ADQ."] = txt
                    break

        desc = registro.get("DESCRIPCION", "").strip()
        if desc and ("SERIE:" in desc or "MCA." in desc):
            registro["OBSERVACIONES"] = desc
            registro["DESCRIPCION"] = ""
            for elem in elementos_fila:
                txt = elem["texto"].strip()
                x0 = elem["x0"]
                if re.match(column_patterns["DESCRIPCION_VALIDA"], txt) and 45 <= x0 <= 285:
                    registro["DESCRIPCION"] = txt
                    break

        return registro
    
    def is_valid_record_corrected(self, registro):
        prog = registro.get("PROG", "").strip()
        if not prog or not prog.isdigit():
            return False
        
        descripcion = registro.get("DESCRIPCION", "").strip()
        if not descripcion or not re.match(column_patterns["DESCRIPCION_VALIDA"], descripcion):
            return False
        
        campos_importantes = ["NO. INVENTARIO", "COSTO", "TIPO ADQ."]
        campos_importantes_llenos = sum(1 for campo in campos_importantes if registro.get(campo, "").strip())
        
        return campos_importantes_llenos >= 1

def assign_by_area_mapping(elementos, page_num, pdf_page=None):
    extractor = AreaMappedExtractor()
    
    if pdf_page:
        extractor.detect_and_exclude_headers(pdf_page)
    
    extractor.setup_default_areas()
    
    return extractor.extract_by_area_mapping_corrected(elementos, page_num)

def extraer_datos_por_celdas(pdf_bytes: bytes):
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    resultados_totales = []

    for page_num in range(1, len(doc)+1):
        page = doc.load_page(page_num-1)
        
        print(f"\n📄 ===== PÁGINA {page_num} =====")
        
        td = page.get_text("dict")
        elementos = []
        for block in td.get("blocks", []):
            if "lines" not in block: 
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    txt = span["text"].strip()
                    
                    if not txt or len(txt.strip()) < 1:
                        continue
                        
                    elementos.append({
                        "texto": txt,
                        "x0": round(span["bbox"][0], 1),
                        "y0": round(span["bbox"][1], 1),
                        "x1": round(span["bbox"][2], 1),
                        "y1": round(span["bbox"][3], 1),
                    })
        
        print(f"  📊 Elementos extraídos: {len(elementos)}")
        
        page_results = assign_by_area_mapping(elementos, page_num, page)
        print(f"  🎯 Registros válidos: {len(page_results)}")

        for rec in page_results:
            resultados_totales.append(rec)

    doc.close()
    
    print(f"\n🏁 EXTRACCIÓN COMPLETADA: {len(resultados_totales)} registros")
    
    return resultados_totales
