from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import fitz
import json
from extractor import extraer_datos_por_celdas as extraer_formato_estandar
from extractor_filtro_2 import extraer_datos_por_celdas as extraer_formato_codi

app = FastAPI(title="API Extractor de PDFs", version="1.0.0")

class PDFFormatDetector:
    
    def __init__(self):
        self.format_indicators = {
            "CODI": {
                "required_headers": ["CODI", "PROG", "DESCRIPCION"],  # CODI debe estar como encabezado
                "min_header_matches": 3,  # Mínimo 3 encabezados encontrados
                "codi_column_required": True,  # CODI debe existir como columna
                "typical_codi_values": ["000", "0000", "00"]  # Valores típicos en columna CODI
            },
            "ESTANDAR": {
                "required_headers": ["PROG", "DESCRIPCION", "NO. INVENTARIO"],
                "min_header_matches": 3,
                "codi_column_required": False,  # NO debe tener columna CODI
                "forbidden_headers": ["CODI"]  # Si encuentra CODI, no es estándar
            }
        }
    
    def detect_pdf_format(self, pdf_bytes: bytes) -> str:
        """
        Detecta automáticamente el formato del PDF
        Retorna: 'CODI' o 'ESTANDAR'
        """
        try:
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Analizar las primeras 2 páginas para determinar formato
            pages_to_analyze = min(2, len(doc))
            detection_results = []
            
            for page_num in range(pages_to_analyze):
                page = doc.load_page(page_num)
                text_dict = page.get_text("dict")
                
                page_result = self._analyze_page_for_format(text_dict)
                detection_results.append(page_result)
                
                # Si encontramos evidencia clara en la primera página, usar eso
                if page_result["confidence"] > 0.8:
                    break
            
            doc.close()
            
            # Determinar formato basado en el análisis
            codi_score = sum(r["codi_score"] for r in detection_results)
            estandar_score = sum(r["estandar_score"] for r in detection_results)
            
            print(f"🔍 Puntuaciones: CODI={codi_score}, ESTANDAR={estandar_score}")
            
            if codi_score > estandar_score:
                return "CODI"
            else:
                return "ESTANDAR"
                
        except Exception as e:
            print(f"Error detectando formato: {e}")
            return "ESTANDAR"
    
    def _analyze_page_for_format(self, text_dict: dict) -> dict:
        """
        Analiza una página específica para determinar formato
        """
        elementos_texto = self._extract_text_elements(text_dict)
        
        # Buscar encabezados de tabla (texto en negritas o posiciones específicas)
        headers_found = self._find_table_headers(elementos_texto)
        
        print(f"📋 Encabezados encontrados: {headers_found}")
        
        # Calcular puntuaciones para cada formato
        codi_score = self._calculate_codi_score(headers_found, elementos_texto)
        estandar_score = self._calculate_estandar_score(headers_found, elementos_texto)
        
        confidence = max(codi_score, estandar_score) / 100.0 if max(codi_score, estandar_score) > 0 else 0
        
        return {
            "headers_found": headers_found,
            "codi_score": codi_score,
            "estandar_score": estandar_score,
            "confidence": min(confidence, 1.0)
        }
    
    def _extract_text_elements(self, text_dict: dict) -> list:
        """
        Extrae elementos de texto con información de formato
        """
        elementos_texto = []
        
        for block in text_dict.get("blocks", []):
            if "lines" in block:
                for line in block["lines"]:
                    for span in line["spans"]:
                        texto = span["text"].strip()
                        if texto:
                            # Detectar si es texto en negrita (probable encabezado)
                            is_bold = span.get("flags", 0) & 2**4  # Flag de negrita
                            font_size = span.get("size", 0)
                            
                            elementos_texto.append({
                                "texto": texto.upper(),
                                "x0": round(span["bbox"][0], 1),
                                "y0": round(span["bbox"][1], 1),
                                "x1": round(span["bbox"][2], 1),
                                "y1": round(span["bbox"][3], 1),
                                "is_bold": is_bold,
                                "font_size": font_size
                            })
        
        return elementos_texto
    
    def _find_table_headers(self, elementos_texto: list) -> list:
        """
        Encuentra los encabezados de la tabla (típicamente en negrita o primera fila)
        """
        headers_found = []
        
        # Buscar texto en negrita que parezca encabezado
        bold_texts = [e for e in elementos_texto if e.get("is_bold")]
        
        header_keywords = [
            "PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", 
            "SERIE", "COSTO", "TIPO", "ADQ", "INVENTARIO", "CODI", "CODIGO"
        ]
        
        for elem in bold_texts:
            texto = elem["texto"]
            for keyword in header_keywords:
                if keyword in texto:
                    headers_found.append(texto)
                    break
        
        # Si no hay negritas, buscar en las primeras filas
        if not headers_found:
            # Agrupar por posición Y (filas)
            y_positions = sorted(set(e["y0"] for e in elementos_texto))
            
            # Revisar las primeras 3 filas
            for y_pos in y_positions[:3]:
                fila_elementos = [e for e in elementos_texto if abs(e["y0"] - y_pos) <= 2]
                
                for elem in fila_elementos:
                    texto = elem["texto"]
                    for keyword in header_keywords:
                        if keyword in texto and texto not in headers_found:
                            headers_found.append(texto)
        
        return headers_found
    
    def _calculate_codi_score(self, headers_found: list, elementos_texto: list) -> int:
        """
        Calcula puntuación para formato CODI
        """
        score = 0
        
        # CODI debe tener la columna CODI como encabezado
        has_codi_header = any("CODI" in header for header in headers_found)
        
        if not has_codi_header:
            return 0  # Sin columna CODI, no puede ser formato CODI
        
        score += 50  # Bonus por tener columna CODI
        
        # Buscar encabezados requeridos
        required_headers = ["PROG", "DESCRIPCION", "CODI"]
        for required in required_headers:
            if any(required in header for header in headers_found):
                score += 20
        
        # Buscar valores típicos de CODI
        codi_values = ["000", "0000", "00"]
        codi_value_count = 0
        
        for elem in elementos_texto:
            if elem["texto"] in codi_values:
                codi_value_count += 1
        
        if codi_value_count >= 3:  # Al menos 3 valores CODI típicos
            score += 30
        
        # Bonus por estructura completa de CODI
        expected_codi_headers = ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", "SERIE", "COSTO", "TIPO", "ADQ", "INVENTARIO", "CODI"]
        matches = sum(1 for expected in expected_codi_headers if any(expected in header for header in headers_found))
        
        if matches >= 8:  # La mayoría de columnas presentes
            score += 40
        
        print(f"🔵 CODI Score: {score} (CODI header: {has_codi_header}, values: {codi_value_count})")
        return score
    
    def _calculate_estandar_score(self, headers_found: list, elementos_texto: list) -> int:
        """
        Calcula puntuación para formato ESTÁNDAR
        """
        score = 0
        
        # Si tiene columna CODI, NO puede ser estándar
        has_codi_header = any("CODI" in header for header in headers_found)
        
        if has_codi_header:
            return 0  # Formato estándar no debe tener columna CODI
        
        score += 30  # Bonus por NO tener CODI
        
        # Buscar encabezados requeridos para formato estándar
        required_headers = ["PROG", "DESCRIPCION", "INVENTARIO"]
        for required in required_headers:
            if any(required in header for header in headers_found):
                score += 25
        
        # Bonus por estructura típica estándar
        expected_standard_headers = ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO", "SERIE", "COSTO", "TIPO"]
        matches = sum(1 for expected in expected_standard_headers if any(expected in header for header in headers_found))
        
        if matches >= 6:  # La mayoría de columnas estándar presentes
            score += 35
        
        print(f"🟠 ESTANDAR Score: {score} (No CODI: {not has_codi_header})")
        return score

# Instancia global del detector
format_detector = PDFFormatDetector()

@app.get("/")
async def root():
    return {
        "message": "API Extractor de PDFs - Detección Automática",
        "version": "1.0.0",
        "description": "Sube tu PDF y se procesará automáticamente detectando el formato correcto",
        "endpoint": "POST /procesar-pdf"
    }

@app.post("/procesar-pdf")
async def procesar_pdf(file: UploadFile = File(...)):
    """
    🎯 ENDPOINT ÚNICO - Procesa PDF con detección automática de formato
    
    - Detecta automáticamente si es formato CODI o ESTÁNDAR
    - Usa el extractor apropiado (extractor.py o extractor_filtro_2.py)
    - Retorna datos procesados con estadísticas completas
    """
    try:
        # Validar archivo
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(status_code=400, detail="❌ El archivo debe ser un PDF")
        
        # Leer contenido del archivo
        print(f"📄 Procesando archivo: {file.filename}")
        pdf_content = await file.read()
        
        if len(pdf_content) == 0:
            raise HTTPException(status_code=400, detail="❌ El archivo PDF está vacío")
        
        # 🔍 PASO 1: Detectar formato automáticamente
        print("🔍 Detectando formato del PDF...")
        formato_detectado = format_detector.detect_pdf_format(pdf_content)
        print(f"📋 Formato detectado: {formato_detectado}")
        
        # 🚀 PASO 2: Procesar con el extractor apropiado
        if formato_detectado == "CODI":
            print("🔄 Procesando con extractor CODI (extractor_filtro_2.py)...")
            resultados = extraer_formato_codi(pdf_content)
            extractor_usado = "extractor_filtro_2.py"
        else:
            print("🔄 Procesando con extractor ESTÁNDAR (extractor.py)...")
            resultados = extraer_formato_estandar(pdf_content)
            extractor_usado = "extractor.py"
        
        # 📊 PASO 3: Generar estadísticas
        total_registros = len(resultados)
        print(f"✅ Procesamiento completado: {total_registros} registros extraídos")
        
        # Estadísticas adicionales
        registros_con_inventario = sum(1 for r in resultados if r.get("NO. INVENTARIO"))
        registros_con_costo = sum(1 for r in resultados if r.get("COSTO"))
        registros_con_descripcion = sum(1 for r in resultados if r.get("DESCRIPCION"))
        
        stats = {
            "archivo_procesado": file.filename,
            "formato_detectado": formato_detectado,
            "extractor_utilizado": extractor_usado,
            "total_registros": total_registros,
            "registros_con_inventario": registros_con_inventario,
            "registros_con_costo": registros_con_costo,
            "registros_con_descripcion": registros_con_descripcion,
            "completitud": {
                "inventario": f"{(registros_con_inventario/total_registros*100):.1f}%" if total_registros > 0 else "0%",
                "costo": f"{(registros_con_costo/total_registros*100):.1f}%" if total_registros > 0 else "0%",
                "descripcion": f"{(registros_con_descripcion/total_registros*100):.1f}%" if total_registros > 0 else "0%"
            }
        }
        
        # 📤 RESPUESTA FINAL
        return JSONResponse(content={
            "success": True,
            "mensaje": f"✅ PDF procesado exitosamente con formato {formato_detectado}",
            "estadisticas": stats,
            "datos": resultados
        })
        
    except HTTPException as he:
        # Re-lanzar HTTPExceptions tal como están
        raise he
    except Exception as e:
        error_msg = f"❌ Error procesando PDF: {str(e)}"
        print(error_msg)
        raise HTTPException(status_code=500, detail=error_msg)

@app.get("/health")
async def health_check():
    """
    Endpoint de salud para verificar que la API está funcionando
    """
    return {
        "status": "🟢 healthy",
        "message": "API funcionando correctamente",
        "extractors_available": {
            "estandar": "extractor.py ✅",
            "codi": "extractor_filtro_2.py ✅"
        },
        "auto_detection": "✅ enabled"
    }

if __name__ == "__main__":
    import uvicorn
    print("🚀 Iniciando API Extractor de PDFs...")
    print("📍 Endpoint principal: POST /procesar-pdf")
    print("🔍 Detección automática de formato habilitada")
    uvicorn.run(app, host="0.0.0.0", port=8000)