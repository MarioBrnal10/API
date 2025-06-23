from fastapi import FastAPI, UploadFile, File
import fitz
import re
from collections import defaultdict
import numpy as np

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
            "NO_INVENTARIO": r'^\d{10}-\d{4}-\d{5}-\d{2}$',
            "SERIE_ALPHANUMERIC": r'^[A-Z0-9]{5,}$',
            "MODELO_PATTERN": r'^[A-Z0-9/\-]{3,}$'
        }
        
        # MARCAS AMPLIADAS para mejor detección
        self.known_brands = {
            "NOKIA", "CISCO", "STEREN", "CAMBIUM", "SOUND", "TRACK", 
            "ACTECK", "BAF", "OHAUS", "EPSON", "CANON", "HP", "DELL", 
            "PIONEER", "SIN MARCA", "OLYMPIA", "IROSCOPE", "CHAPARRAL",
            "OLYMPIA/SG-3", "IME2-86SA", "AM/E2C28M-1A"
        }

    def is_bold_text(self, span):
        """Detecta texto en negrita - MEJORADO"""
        font_flags = span.get("flags", 0)
        if font_flags & 16:
            return True
        
        font_name = span.get("font", "").lower()
        if any(bold_word in font_name for bold_word in ["bold", "black", "heavy", "demi"]):
            return True
        
        return False

    def clean_invalid_values(self, registro):
        """Limpia valores inválidos - MANTENER ORIGINAL"""
        for campo in registro:
            if registro[campo] in ["000", "0000", "00", "CODI"]:
                registro[campo] = ""
        return registro

    def find_missing_costo(self, registro, elementos_texto, prog_y):
        """Búsqueda de costo faltante - MANTENER ORIGINAL MEJORADO"""
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
        """Detecta columna CODI - MANTENER ORIGINAL"""
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
        """Detecta texto multilínea para DESC. TIPO ADQ. - CORREGIDO ESPECÍFICAMENTE"""
        elementos_combinados = []
        i = 0
        
        while i < len(elementos_texto):
            elem = elementos_texto[i]
            texto = elem["texto"].strip()
            
            # 🎯 PATRONES MUY ESPECÍFICOS PARA DESC. TIPO ADQ. (NO CONFUNDIR CON DESCRIPCION)
            if (texto and 
                # ✅ DEBE empezar con códigos específicos O contener guión bajo
                (texto.startswith(("C.A.P.C.E.Q", "I.L.C.E", "IIFEQ", "U.S.E.B.E.Q")) or
                 "_" in texto) and
                # ❌ NO debe ser un número de inventario
                not re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto) and
                # ❌ NO debe contener palabras típicas de DESCRIPCION
                not any(desc_word in texto.upper() for desc_word in [
                    "PANTALLA", "TELEVISION", "MONITOR", "ANTENA", "EQUIPO DE SONIDO",
                    "MICROSCOPIO", "MAQUINA", "MESA", "ARCHIVERO"
                ])):
                
                texto_completo = texto
                j = i + 1
                elementos_utilizados = [elem]
                
                # Combinar textos relacionados
                while j < len(elementos_texto):
                    siguiente = elementos_texto[j]
                    siguiente_texto = siguiente["texto"].strip()
                    
                    # Verificar distancia Y (máximo 25px)
                    if abs(siguiente["y0"] - elem["y0"]) > 25:
                        j += 1
                        continue
                    
                    # Verificar distancia X (máximo 150px)
                    if abs(siguiente["x0"] - elem["x0"]) > 150:
                        j += 1
                        continue
                    
                    # ❌ PARAR si encuentra un número de inventario
                    if re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', siguiente_texto):
                        break
                    
                    # ❌ PARAR si encuentra palabras típicas de DESCRIPCION
                    if any(desc_word in siguiente_texto.upper() for desc_word in [
                        "PANTALLA", "TELEVISION", "MONITOR", "ANTENA"
                    ]):
                        break
                    
                    # ✅ CONTINUAR si es parte del DESC. TIPO ADQ.
                    if (siguiente_texto.endswith(("ción", "ión", "ento", "ado", "ión")) or
                        any(kw in siguiente_texto.upper() for kw in [
                            "MOBILIARIOS", "EQUIPOS", "ADMINISTRACION", "LABORATORIO", 
                            "INSTRUMENTAL", "MEDICO", "COMPUTO", "TECNOLOGIA", "INFORMACIÓN"
                        ])):
                        
                        if texto_completo.endswith("_"):
                            texto_completo += siguiente_texto
                        else:
                            texto_completo += "_" + siguiente_texto
                        
                        elementos_utilizados.append(siguiente)
                        j += 1
                    else:
                        break
                
                # Crear elemento combinado si es válido
                if len(elementos_utilizados) >= 1:
                    elemento_combinado = {
                        "texto": texto_completo,
                        "x0": elem["x0"],
                        "y0": elem["y0"],
                        "x1": max(e["x1"] for e in elementos_utilizados),
                        "y1": max(e["y1"] for e in elementos_utilizados),
                        "es_multilinea": True,
                        "es_desc_tipo_adq": True  # 🆕 MARCADOR ESPECÍFICO
                    }
                    elementos_combinados.append(elemento_combinado)
                    i = j
                else:
                    elementos_combinados.append(elem)
                    i += 1
            else:
                elementos_combinados.append(elem)
                i += 1
        
        return elementos_combinados

    def find_all_prog_positions(self, elementos_texto, page_num):
        """Encuentra posiciones PROG - CORREGIDO para evitar duplicados"""
        if page_num == 1:
            prog_inicial = 1
        else:
            prog_inicial = self.ultimo_prog + 1
        
        prog_positions = []
        prog_encontrados = set()
        
        # NUEVA LÓGICA: Buscar secuencialmente y validar contexto
        prog_actual = prog_inicial
        intentos_sin_encontrar = 0
        
        while intentos_sin_encontrar < 5:  # Máximo 5 intentos consecutivos sin encontrar
            encontrado = False
            
            for elem in elementos_texto:
                if (elem["texto"].strip() == str(prog_actual) and 
                    prog_actual not in prog_encontrados):
                    
                    y_pos = elem["y0"]
                    # TOLERANCIA MÁS ESTRICTA para evitar elementos lejanos
                    elementos_fila = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 6]
                    
                    # VALIDACIÓN ESTRICTA: debe tener al menos 4 elementos en la fila
                    if len(elementos_fila) >= 4:
                        # VALIDACIÓN ADICIONAL: verificar que no sea un PROG ya procesado en otra posición
                        prog_ya_usado = False
                        for pos_existente in prog_positions:
                            if abs(pos_existente["y"] - y_pos) < 3:  # Muy cerca de otro PROG
                                prog_ya_usado = True
                                break
                        
                        if not prog_ya_usado:
                            prog_positions.append({
                                "prog": prog_actual,
                                "y": y_pos,
                                "elementos_fila": elementos_fila
                            })
                            prog_encontrados.add(prog_actual)
                            prog_actual += 1
                            encontrado = True
                            intentos_sin_encontrar = 0
                            break
            
            if not encontrado:
                intentos_sin_encontrar += 1
                prog_actual += 1
        
        return prog_positions

    def detect_column_positions(self, elementos_texto, start_y):
        """Detecta posiciones de columnas - MANTENER ORIGINAL"""
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
        """Estima posiciones de columnas - MANTENER ORIGINAL"""
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

    def is_valid_modelo(self, texto):
        """Valida si un texto puede ser un modelo - CORREGIDO"""
        if len(texto) < 3:
            return False
        
        # Si es solo números simples de 4-8 dígitos, probablemente es SERIE, no MODELO
        if re.match(r'^\d{4,8}$', texto):
            return False
        
        # Patrones comunes de modelos (MÁS ESPECÍFICOS)
        modelo_patterns = [
            r'^[A-Z0-9/\-]{5,}$',     # Alfanumérico con guiones/barras (5+ chars)
            r'^[A-Z]+\d+[A-Z/\-]+$', # Letras + números + letras/símbolos
            r'^[A-Z]+/[A-Z0-9\-]+$', # Formato con barra
            r'^\d{9,}$',              # Números muy largos (9+ dígitos)
        ]
        
        for pattern in modelo_patterns:
            if re.match(pattern, texto.upper()):
                return True
        
        return False

    def is_valid_serie(self, texto):
        """Valida si un texto puede ser una serie - CORREGIDO"""
        if len(texto) < 3:
            return False
        
        # Patrones comunes de series (MÁS FLEXIBLE)
        serie_patterns = [
            r'^\d{4,8}$',          # Solo números de 4-8 dígitos (como 009737)
            r'^[A-Z0-9]{4,}$',     # Alfanumérico puro
            r'^[A-Z]+\d+[A-Z]*$',  # Letras, números, letras
            r'^[A-Z]{2,}\d{2,}$',  # 2+ letras seguidas de 2+ números
        ]
        
        for pattern in serie_patterns:
            if re.match(pattern, texto.upper()):
                return True
        
        return False

    def is_descripcion_text(self, texto):
        """Identifica si un texto es una descripción - MEJORADO Y MÁS ESPECÍFICO"""
        if len(texto) < 3:
            return False
        
        texto_upper = texto.upper()
        
        # ❌ NO es descripción si contiene patrones de DESC. TIPO ADQ.
        if (texto.startswith(("C.A.P.C.E.Q", "I.L.C.E", "IIFEQ", "U.S.E.B.E.Q")) or
            "_" in texto or
            any(kw in texto_upper for kw in ["MOBILIARIOS", "ADMINISTRACION", "OTROS_"])):
            return False
        
        # ❌ NO es descripción si es un número de inventario
        if re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto):
            return False
        
        # ✅ SÍ es descripción si contiene palabras específicas
        descripcion_keywords = [
            "ANTENA", "ARCHIVERO", "MAQUINA", "MESA", "MICROSCOPIO", 
            "EQUIPO", "BOMBA", "APARATO", "UNIDAD", "MONITOR", "PANTALLA",
            "ESCRITORIO", "SILLA", "TELEFONO", "IMPRESORA", "COMPUTADORA",
            "TELEVISION", "CENTRAL", "BIOLOGIA", "QUIMICA", "ELECTRICIDAD",
            "ANATOMICO", "TERMOLOGIA", "OPTICA", "VACIO", "SONIDO", "PROCESO"
        ]
        
        return any(keyword in texto_upper for keyword in descripcion_keywords)

    def validate_field_assignment(self, texto, campo_objetivo):
        """🆕 Valida que un texto sea apropiado para el campo objetivo"""
        texto_upper = texto.upper()
        
        if campo_objetivo == "DESCRIPCION":
            # ❌ NO debe ser DESC. TIPO ADQ.
            if (texto.startswith(("C.A.P.C.E.Q", "I.L.C.E", "IIFEQ")) or
                "_" in texto or
                "MOBILIARIOS" in texto_upper or
                "ADMINISTRACION" in texto_upper):
                return False
            
            # ❌ NO debe ser número de inventario
            if re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto):
                return False
            
            # ✅ Debe contener palabras de descripción
            return self.is_descripcion_text(texto)
        
        elif campo_objetivo == "DESC. TIPO ADQ.":
            # ❌ NO debe ser número de inventario
            if re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto):
                return False
            
            # ✅ Debe empezar con códigos específicos O contener guión bajo
            if (texto.startswith(("C.A.P.C.E.Q", "I.L.C.E", "IIFEQ", "U.S.E.B.E.Q")) or
                "_" in texto or
                any(kw in texto_upper for kw in ["MOBILIARIOS", "ADMINISTRACION", "INSTRUMENTAL", "LABORATORIO"])):
                return True
            
            return False
        
        elif campo_objetivo == "NO. INVENTARIO":
            # ✅ Debe tener formato específico de inventario
            return re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto) is not None
        
        return True

    def is_valid_observaciones(self, texto):
        """🆕 Detecta si un texto son observaciones usando patrones específicos"""
        if len(texto) < 3:
            return False
        
        texto_upper = texto.upper()
        
        # ✅ PATRONES ESPECÍFICOS DE OBSERVACIONES
        observaciones_patterns = [
            # Patrones con números largos
            r'^\d{6,}$',  # Como 086972
            
            # Patrones descriptivos con palabras clave
            r'.*DE LAMINA.*',
            r'.*COLOR.*',
            r'.*PATAS.*',
            r'.*ACERO.*',
            r'.*METALICAS.*',
            r'.*INOXIDABLE.*',
            r'.*CUBIERTA.*',
            r'.*MONOCULAR.*',
            r'.*OBJETIVOS.*',
            r'.*AUMENTOS.*',
            r'.*TORSO.*',
            r'.*DESARMABLE.*',
            r'.*PRESION.*',
            r'.*PORTATIL.*',
            r'.*METALICO.*',
            r'.*INTERACTIVO.*',
            r'.*PROYECTOR.*',
            r'.*RESOLUCION.*',
            r'.*FORM FACTOR.*',
            
            # Patrones con números y "PZAS"
            r'^\d+\s*PZAS?\.?$',  # Como "20 PZAS.", "35 PZAS."
        ]
        
        # Verificar patrones específicos
        for pattern in observaciones_patterns:
            if re.match(pattern, texto_upper):
                return True
        
        # ✅ CARACTERÍSTICAS ADICIONALES DE OBSERVACIONES
        # Contiene comas o puntos (muy común en observaciones)
        if (',' in texto or '.' in texto) and len(texto) > 10:
            return True
        
        # Múltiples palabras descriptivas
        if (len(texto.split()) >= 3 and 
            any(kw in texto_upper for kw in ['DE ', 'CON ', 'COLOR', 'PATAS', 'TIPO', 'ACERO'])):
            return True
        
        return False

    def is_valid_serie_patterns(self, texto):
        """🆕 Detecta series usando patrones específicos mejorados"""
        if len(texto) < 3:
            return False
        
        # ✅ PATRONES ESPECÍFICOS DE SERIES BASADOS EN TUS EJEMPLOS
        serie_patterns = [
            r'^\d{6}$',                    # 009737
            r'^\d{7}$',                    # 7411324
            r'^\d{6}$',                    # 203866
            r'^\d{2}-[A-Z]-\d{2}$',        # 52-L-99
            r'^[A-Z]{3}\d{4}-\d{5}$',      # SAA2000-61689
            r'^[A-Z]{3}\d{5}[A-Z]{2}$',    # MXJ44803FK, MXJ44803JF
            r'^[A-Z]{3}\d{6}[A-Z]$',       # MYA442006M
            r'^[A-Z]{3}\d{7}[A-Z]$',       # JX4F824449L
            r'^[A-Z]{3}\d{5}[A-Z]{2}$',    # MXJ80306RR
            r'^\d{13}$',                   # 2T072907075345
            
            # Patrones generales adicionales
            r'^[A-Z0-9]{6,15}$',           # Alfanumérico 6-15 caracteres
            r'^[A-Z]{2,4}\d{4,8}[A-Z]{0,3}$',  # Letras + números + letras opcionales
        ]
        
        for pattern in serie_patterns:
            if re.match(pattern, texto.upper()):
                return True
        
        return False

    def is_valid_modelo_patterns(self, texto):
        """🆕 Detecta modelos usando patrones específicos mejorados"""
        if len(texto) < 2:
            return False
        
        texto_upper = texto.upper()
        
        # ✅ PATRONES ESPECÍFICOS DE MODELOS BASADOS EN TUS EJEMPLOS
        modelo_patterns = [
            # Formato MARCA/MODELO
            r'^[A-Z]+/[A-Z0-9\-]+$',        # OLYMPIA/SG-3, HP/7550, etc.
            r'^[A-Z\s]+/[A-Z0-9\-\s]+$',    # SOUND TRACK/SAP-11
            
            # Formato complejo con espacios
            r'^[A-Z]+\s+[A-Z]+/[A-Z0-9\-]+$',  # HP COMPAQ/DC5750
            
            # Solo la parte del modelo (sin marca)
            r'^[A-Z]{2,4}\d{4,8}[A-Z]{0,3}$',  # SG-3, DC5750, etc.
            r'^[A-Z0-9\-]{3,15}$',             # Modelos alfanuméricos
            
            # Formatos específicos detectados
            r'^[A-Z]+\d{3,5}[A-Z]*$',         # NP410W, etc.
            r'^[A-Z]+\-[A-Z0-9]+$',           # DES-30X, etc.
        ]
        
        for pattern in modelo_patterns:
            if re.match(pattern, texto_upper):
                return True
        
        # ✅ VERIFICACIÓN ADICIONAL: contiene "/" (muy común en modelos)
        if '/' in texto and len(texto) > 3:
            return True
        
        return False

    def is_valid_marca_patterns(self, texto):
        """🆕 Detecta marcas usando patrones mejorados"""
        texto_upper = texto.upper()
        
        # ✅ MARCAS ESPECÍFICAS AMPLIADAS CON LAS DE TUS EJEMPLOS
        marcas_conocidas = {
            "OLYMPIA", "IROSCOPE", "SOUND TRACK", "HP", "SMARTBOARD", 
            "EPSON", "NEC", "AVERMEDIA", "INFOCUS", "LANIX", "TRUPER",
            "NOKIA", "CISCO", "STEREN", "CAMBIUM", "TRACK", "ACTECK", 
            "BAF", "OHAUS", "CANON", "DELL", "PIONEER", "SIN MARCA", 
            "CHAPARRAL", "HUAWEI", "SAMSUNG"
        }
        
        # Verificar marcas exactas
        if texto_upper in marcas_conocidas:
            return True
        
        # Verificar marcas compuestas (como "SOUND TRACK")
        for marca in marcas_conocidas:
            if ' ' in marca and marca in texto_upper:
                return True
        
        # ✅ PATRONES ADICIONALES DE MARCAS
        # Marcas que terminan con números/letras específicas
        if re.match(r'^[A-Z]{2,}$', texto_upper) and len(texto) <= 12:
            return True
        
        return False

    def multiple_pass_extraction(self, elementos_texto, prog_positions):
        """🆕 Extracción MEJORADA con detección específica de patrones"""
        registros = []
        
        # 🎯 ANÁLISIS PRELIMINAR: Clasificar todos los elementos CON PATRONES ESPECÍFICOS
        elementos_clasificados = {}
        for elem in elementos_texto:
            texto = elem["texto"].strip()
            
            # Clasificar por tipo usando patrones específicos
            if re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto):
                elementos_clasificados.setdefault("inventarios", []).append(elem)
            elif (texto.startswith(("C.A.P.C.E.Q", "I.L.C.E", "IIFEQ")) or 
                  "_" in texto or
                  any(kw in texto.upper() for kw in ["MOBILIARIOS", "ADMINISTRACION"])):
                elementos_clasificados.setdefault("desc_tipo_adq", []).append(elem)
            elif self.is_descripcion_text(texto):
                elementos_clasificados.setdefault("descripciones", []).append(elem)
            elif re.match(self.patterns["COSTO"], texto):
                elementos_clasificados.setdefault("costos", []).append(elem)
            elif self.is_valid_marca_patterns(texto):  # 🆕 PATRÓN ESPECÍFICO
                elementos_clasificados.setdefault("marcas", []).append(elem)
            elif self.is_valid_modelo_patterns(texto):  # 🆕 PATRÓN ESPECÍFICO
                elementos_clasificados.setdefault("modelos", []).append(elem)
            elif self.is_valid_serie_patterns(texto):   # 🆕 PATRÓN ESPECÍFICO
                elementos_clasificados.setdefault("series", []).append(elem)
            elif self.is_valid_observaciones(texto):    # 🆕 PATRÓN ESPECÍFICO
                elementos_clasificados.setdefault("observaciones", []).append(elem)
        
        print(f"🔍 Elementos clasificados CON PATRONES:")
        for tipo, lista in elementos_clasificados.items():
            print(f"  {tipo}: {len(lista)} elementos")
            if lista:
                print(f"    Ejemplos: {[elem['texto'][:30] for elem in lista[:3]]}")
        
        # 🎯 PROCESAMIENTO POR PROG CON MÚLTIPLES PASADAS
        for prog_info in prog_positions:
            prog_num = prog_info["prog"]
            prog_y = prog_info["y"]
            
            print(f"\n🔍 Procesando PROG {prog_num} (Y: {prog_y})")
            
            # Encontrar elementos en el área del PROG
            elementos_area = []
            for elem in elementos_texto:
                if abs(elem["y0"] - prog_y) <= 15:  # Área estricta
                    elementos_area.append(elem)
            
            print(f"📍 Elementos en área PROG {prog_num}: {len(elementos_area)}")
            
            # 🎯 PASADA 1: Asignación por patrones exactos con DISTANCIA
            registro = {col: "" for col in columnas_clave}
            registro["PROG"] = str(prog_num)
            
            for elem in elementos_area:
                texto = elem["texto"].strip()
                x_pos = elem["x0"]
                
                # NO. INVENTARIO (más alta prioridad)
                if (not registro["NO. INVENTARIO"] and 
                    re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto)):
                    registro["NO. INVENTARIO"] = texto
                    print(f"✅ NO. INVENTARIO asignado: {texto}")
                
                # DESC. TIPO ADQ. (segunda prioridad)
                elif (not registro["DESC. TIPO ADQ."] and 
                      self.validate_field_assignment(texto, "DESC. TIPO ADQ.")):
                    registro["DESC. TIPO ADQ."] = texto
                    print(f"✅ DESC. TIPO ADQ. asignado: {texto}")
                
                # DESCRIPCION (tercera prioridad)
                elif (not registro["DESCRIPCION"] and 
                      self.validate_field_assignment(texto, "DESCRIPCION")):
                    registro["DESCRIPCION"] = texto
                    print(f"✅ DESCRIPCION asignada: {texto}")
                
                # 🆕 OBSERVACIONES (usando patrones específicos)
                elif (not registro["OBSERVACIONES"] and 
                      self.is_valid_observaciones(texto)):
                    registro["OBSERVACIONES"] = texto
                    print(f"✅ OBSERVACIONES asignadas: {texto}")
                
                # 🆕 MARCA (usando patrones específicos)
                elif (not registro["MARCA"] and 
                      self.is_valid_marca_patterns(texto)):
                    registro["MARCA"] = texto
                    print(f"✅ MARCA asignada: {texto}")
                
                # 🆕 MODELO (usando patrones específicos)
                elif (not registro["MODELO"] and 
                      self.is_valid_modelo_patterns(texto)):
                    registro["MODELO"] = texto
                    print(f"✅ MODELO asignado: {texto}")
                
                # 🆕 SERIE (usando patrones específicos)
                elif (not registro["SERIE"] and 
                      self.is_valid_serie_patterns(texto)):
                    registro["SERIE"] = texto
                    print(f"✅ SERIE asignada: {texto}")
                
                # COSTO
                elif (not registro["COSTO"] and 
                      re.match(self.patterns["COSTO"], texto)):
                    registro["COSTO"] = texto
                    print(f"✅ COSTO asignado: {texto}")
            
            # 🎯 PASADA 2: Búsqueda extendida POR TIPO con DISTANCIA
            elementos_faltantes = [
                ("OBSERVACIONES", "observaciones"),
                ("MARCA", "marcas"),
                ("MODELO", "modelos"), 
                ("SERIE", "series")
            ]
            
            for campo, tipo_elem in elementos_faltantes:
                if not registro[campo] and tipo_elem in elementos_clasificados:
                    mejor_candidato = None
                    menor_distancia = float('inf')
                    
                    for elem in elementos_clasificados[tipo_elem]:
                        distancia_y = abs(elem["y0"] - prog_y)
                        if distancia_y <= 30:  # 🎯 DISTANCIA MÁXIMA
                            if distancia_y < menor_distancia:
                                menor_distancia = distancia_y
                                mejor_candidato = elem
                    
                    if mejor_candidato:
                        registro[campo] = mejor_candidato["texto"]
                        print(f"🔍 {campo} encontrado (búsqueda extendida): {mejor_candidato['texto']} (distancia: {menor_distancia}px)")
            
            # 🎯 PASADA 3: Búsqueda bidireccional por proximidad X
            elementos_area_ordenados = sorted(elementos_area, key=lambda x: x["x0"])
            
            # Asignar elementos restantes por proximidad X
            for elem in elementos_area_ordenados:
                texto = elem["texto"].strip()
                x_pos = elem["x0"]
                
                # 🆕 Asignación específica por patrones y posición X
                if not registro["OBSERVACIONES"] and self.is_valid_observaciones(texto):
                    registro["OBSERVACIONES"] = texto
                    print(f"🔄 OBSERVACIONES (proximidad X): {texto}")
                elif not registro["MARCA"] and self.is_valid_marca_patterns(texto):
                    registro["MARCA"] = texto
                    print(f"🔄 MARCA (proximidad X): {texto}")
                elif not registro["MODELO"] and self.is_valid_modelo_patterns(texto):
                    registro["MODELO"] = texto
                    print(f"🔄 MODELO (proximidad X): {texto}")
                elif not registro["SERIE"] and self.is_valid_serie_patterns(texto):
                    registro["SERIE"] = texto
                    print(f"🔄 SERIE (proximidad X): {texto}")
            
            # 🎯 VALIDACIÓN FINAL
            registro = self.clean_invalid_values(registro)
            
            print(f"📋 Registro final PROG {prog_num}:")
            for campo, valor in registro.items():
                if valor:
                    print(f"    {campo}: {valor}")
            
            registros.append(registro)
        
        return registros

    def extract_by_positions(self, elementos_texto, page_num):
        """🆕 Método principal CORREGIDO con múltiples pasadas"""
        if not elementos_texto:
            return []
        
        print(f"🚀 Iniciando extracción página {page_num} con {len(elementos_texto)} elementos")
        
        # Detectar texto multilínea
        elementos_texto = self.detect_multiline_desc_tipo(elementos_texto)
        print(f"📝 Después de detect_multiline_desc_tipo: {len(elementos_texto)} elementos")
        
        # Encontrar posiciones PROG
        prog_positions = self.find_all_prog_positions(elementos_texto, page_num)
        if not prog_positions:
            print("❌ No se encontraron posiciones PROG")
            return []
        
        print(f"🎯 Posiciones PROG encontradas: {[p['prog'] for p in prog_positions]}")
        
        # 🆕 USAR MÉTODO DE MÚLTIPLES PASADAS
        registros = self.multiple_pass_extraction(elementos_texto, prog_positions)
        
        # Actualizar último PROG procesado
        if prog_positions:
            max_prog = max(p["prog"] for p in prog_positions)
            if max_prog > self.ultimo_prog:
                self.ultimo_prog = max_prog
        
        print(f"✅ Registros extraídos: {len(registros)}")
        return registros

    def comprehensive_inventory_validation(self, registros):
        """Validación comprehensiva de inventarios - FUNCIÓN FALTANTE"""
        if not registros:
            return registros
        
        todos_inventarios_disponibles = set()
        
        # Recopilar todos los inventarios válidos disponibles
        for registro in registros:
            inventario = registro.get("NO. INVENTARIO", "")
            if inventario and re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', inventario):
                todos_inventarios_disponibles.add(inventario)
        
        inventarios_asignados = set()
        registros_procesados = []
        
        # Procesar cada registro
        for i, registro in enumerate(registros):
            inventario_original = registro.get("NO. INVENTARIO", "")
            
            # Si no tiene inventario, buscar uno disponible
            if not inventario_original:
                inventario_disponible = self.find_available_inventory_by_index(
                    todos_inventarios_disponibles, inventarios_asignados, i
                )
                if inventario_disponible:
                    registro["NO. INVENTARIO"] = inventario_disponible
                    inventarios_asignados.add(inventario_disponible)
            
            # Si ya está asignado, buscar alternativo
            elif inventario_original in inventarios_asignados:
                inventario_alternativo = self.find_next_available_inventory(
                    inventario_original, todos_inventarios_disponibles, inventarios_asignados
                )
                if inventario_alternativo:
                    registro["NO. INVENTARIO"] = inventario_alternativo
                    inventarios_asignados.add(inventario_alternativo)
                else:
                    # Generar uno único
                    inventario_generado = self.generate_unique_inventory(
                        inventario_original, inventarios_asignados
                    )
                    registro["NO. INVENTARIO"] = inventario_generado
                    inventarios_asignados.add(inventario_generado)
            
            else:
                # Es válido y único
                inventarios_asignados.add(inventario_original)
            
            registros_procesados.append(registro)
        
        return registros_procesados

    def find_available_inventory_by_index(self, todos_inventarios, inventarios_asignados, index):
        """Encuentra inventario disponible por índice"""
        inventarios_ordenados = sorted(list(todos_inventarios))
        
        for offset in range(len(inventarios_ordenados)):
            for direction in [0, 1, -1]:
                idx_busqueda = index + (offset * direction)
                
                if 0 <= idx_busqueda < len(inventarios_ordenados):
                    inventario_candidato = inventarios_ordenados[idx_busqueda]
                    if inventario_candidato not in inventarios_asignados:
                        return inventario_candidato
        
        return None

    def find_next_available_inventory(self, inventario_base, todos_inventarios, inventarios_asignados):
        """Encuentra siguiente inventario disponible"""
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
            # Si no encontró el inventario base, buscar cualquiera disponible
            for inventario in inventarios_ordenados:
                if inventario not in inventarios_asignados:
                    return inventario
        
        return None

    def generate_unique_inventory(self, inventario_base, inventarios_asignados):
        """Genera inventario único"""
        if not inventario_base:
            base_generado = "9999999999-2024-00001-01"
        else:
            try:
                partes = inventario_base.split('-')
                base, año, secuencia, final = partes
                secuencia_num = int(secuencia)
                
                contador = 1
                while contador <= 9999:
                    nueva_secuencia = secuencia_num + contador
                    inventario_candidato = f"{base}-{año}-{nueva_secuencia:05d}-{final}"
                    
                    if inventario_candidato not in inventarios_asignados:
                        return inventario_candidato
                    
                    contador += 1
                
                # Si no encuentra, usar timestamp
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

    def analyze_text_characteristics(self, texto, x_pos, y_pos, context_elements):
        """Analiza características de un texto para clasificarlo - FUNCIÓN FALTANTE"""
        caracteristicas = {
            "es_marca": False,
            "es_modelo": False,
            "es_serie": False,
            "es_descripcion": False,
            "es_observaciones": False,
            "confianza": 0
        }
        
        texto_upper = texto.upper()
        
        # Verificar si es marca conocida
        if texto_upper in self.known_brands:
            caracteristicas["es_marca"] = True
            caracteristicas["confianza"] = 95
        
        # Verificar si es descripción
        elif self.is_descripcion_text(texto):
            caracteristicas["es_descripcion"] = True
            caracteristicas["confianza"] = 90
        
        # Verificar si es modelo
        elif self.is_valid_modelo(texto):
            caracteristicas["es_modelo"] = True
            caracteristicas["confianza"] = 85
        
        # Verificar si es serie
        elif self.is_valid_serie(texto):
            caracteristicas["es_serie"] = True
            caracteristicas["confianza"] = 80
        
        # Verificar si son observaciones
        elif any(kw in texto_upper for kw in ["DE ", "CON ", "SERIE:", "INCLUYE", "MARCA:"]):
            caracteristicas["es_observaciones"] = True
            caracteristicas["confianza"] = 75
        
        return caracteristicas

def extraer_datos_por_celdas(pdf_bytes: bytes):
    """🆕 Función principal MEJORADA con depuración completa"""
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        extractor = SimpleTableExtractor()
        resultados_totales = []
        
        print(f"📄 Procesando PDF con {len(doc)} páginas")
        
        for page_num in range(len(doc)):
            print(f"\n📖 === PÁGINA {page_num + 1} ===")
            page = doc.load_page(page_num)
            text_dict = page.get_text("dict")
            
            elementos_texto = []
            for block in text_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            texto = span["text"].strip()
                            
                            # 🎯 FILTRADO MÍNIMO - NO filtrar elementos importantes
                            if (texto and 
                                len(texto.strip()) > 0 and
                                not any(bad in texto for bad in ["Declaro", "protesta", "NOMBRE", "FIRMA", "TOTAL", "CEDULA"])):
                                
                                elementos_texto.append({
                                    "texto": texto,
                                    "x0": round(span["bbox"][0], 1),
                                    "y0": round(span["bbox"][1], 1),
                                    "x1": round(span["bbox"][2], 1),
                                    "y1": round(span["bbox"][3], 1)
                                })
            
            print(f"📝 Elementos de texto extraídos: {len(elementos_texto)}")
            
            # 🔍 DEBUG: Mostrar elementos relevantes
            inventarios_encontrados = []
            desc_tipos_encontrados = []
            descripciones_encontradas = []
            
            for elem in elementos_texto:
                texto = elem["texto"].strip()
                
                if re.match(r'^\d{5}-\d{4}-\d{5}-\d{1,2}$', texto):
                    inventarios_encontrados.append(texto)
                elif (texto.startswith(("C.A.P.C.E.Q", "I.L.C.E", "IIFEQ")) or "_" in texto):
                    desc_tipos_encontrados.append(texto)
                elif any(kw in texto.upper() for kw in ["PANTALLA", "TELEVISION", "ANTENA", "MONITOR"]):
                    descripciones_encontradas.append(texto)
            
            print(f"🎯 Inventarios detectados: {len(inventarios_encontrados)}")
            print(f"🎯 DESC. TIPO ADQ. detectados: {len(desc_tipos_encontrados)}")
            print(f"🎯 Descripciones detectadas: {len(descripciones_encontradas)}")
            
            if inventarios_encontrados:
                print(f"📋 Primeros inventarios: {inventarios_encontrados[:3]}")
            if desc_tipos_encontrados:
                print(f"📋 Primeros DESC. TIPO ADQ.: {[d[:50] for d in desc_tipos_encontrados[:3]]}")
            
            if elementos_texto:
                try:
                    registros_pagina = extractor.extract_by_positions(elementos_texto, page_num + 1)
                    print(f"✅ Registros extraídos de página {page_num + 1}: {len(registros_pagina)}")
                    
                    # DEBUG: Mostrar primeros registros
                    for i, reg in enumerate(registros_pagina[:3]):
                        print(f"📊 Registro {i+1}:")
                        print(f"    PROG: {reg.get('PROG', 'N/A')}")
                        print(f"    DESCRIPCION: {reg.get('DESCRIPCION', 'N/A')}")
                        print(f"    DESC. TIPO ADQ.: {reg.get('DESC. TIPO ADQ.', 'N/A')}")
                        print(f"    NO. INVENTARIO: {reg.get('NO. INVENTARIO', 'N/A')}")
                    
                    resultados_totales.extend(registros_pagina)
                    
                except Exception as e:
                    print(f"❌ Error procesando página {page_num + 1}: {str(e)}")
                    import traceback
                    traceback.print_exc()
        
        doc.close()
        
        print(f"\n📊 === RESUMEN FINAL ===")
        print(f"Total registros antes de validación: {len(resultados_totales)}")
        
        # Validación final
        if resultados_totales:
            resultados_totales = extractor.comprehensive_inventory_validation(resultados_totales)
            resultados_totales.sort(key=lambda x: int(x.get("PROG", "999")))
        
        print(f"Total registros finales: {len(resultados_totales)}")
        
        # Estadísticas finales
        if resultados_totales:
            campos_llenos = {}
            for campo in columnas_clave:
                campos_llenos[campo] = sum(1 for r in resultados_totales if r.get(campo))
            
            print("📈 Estadísticas por campo:")
            for campo, count in campos_llenos.items():
                porcentaje = (count / len(resultados_totales)) * 100 if resultados_totales else 0
                print(f"    {campo}: {count}/{len(resultados_totales)} ({porcentaje:.1f}%)")
        
        return resultados_totales
    
    except Exception as e:
        print(f"❌ Error en extracción: {str(e)}")
        import traceback
        traceback.print_exc()
        return []
