from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import io
import fitz
import re
from extractor_filtro_2 import extraer_datos_por_celdas as extraer_con_codi
from extractor import extraer_datos_por_celdas as extraer_sin_codi

app = FastAPI(
    title="PDF Table Extractor API",
    description="API para extraer datos de tablas PDF usando mapeo por áreas",
    version="2.0.0"
)

def detect_codi_column(pdf_bytes: bytes) -> bool:
    """
    Detecta si el PDF tiene columna CODI analizando las primeras páginas
    """
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        
        # Analizar solo las primeras 2 páginas para eficiencia
        max_pages = min(2, len(doc))
        
        for page_num in range(max_pages):
            page = doc.load_page(page_num)
            text_dict = page.get_text("dict")
            
            # Buscar headers de columnas
            header_elements = []
            data_elements = []
            
            for block in text_dict.get("blocks", []):
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            texto = span["text"].strip().upper()
                            y_pos = span["bbox"][1]
                            x_pos = span["bbox"][0]
                            
                            # Detectar headers (posición Y superior)
                            if y_pos < 150:  # Área de headers
                                if any(header in texto for header in ["PROG", "DESCRIPCION", "OBSERVACIONES", "MARCA", "MODELO"]):
                                    header_elements.append((texto, x_pos, y_pos))
                                elif "CODI" in texto and len(texto) <= 6:
                                    print(f"🎯 CODI detectado en header: '{texto}' en posición X={x_pos}, Y={y_pos}")
                                    doc.close()
                                    return True
                            
                            # Detectar datos (posición Y media/baja)
                            elif y_pos > 150:
                                data_elements.append((texto, x_pos, y_pos))
            
            # Si encontramos headers, buscar CODI en la misma línea Y
            if header_elements:
                header_y_positions = set(round(elem[2]) for elem in header_elements)
                
                for texto, x_pos, y_pos in data_elements:
                    if any(abs(y_pos - header_y) <= 10 for header_y in header_y_positions):
                        if "CODI" in texto and len(texto) <= 6:
                            print(f"🎯 CODI detectado en línea de headers: '{texto}' en X={x_pos}, Y={y_pos}")
                            doc.close()
                            return True
            
            # Buscar patrones de datos CODI (valores 000, 0000, 00)
            codi_pattern_count = 0
            for texto, x_pos, y_pos in data_elements:
                if texto in ["000", "0000", "00"] and y_pos > 150:
                    codi_pattern_count += 1
            
            # Si hay muchos valores 000/0000/00 en posiciones similares, probablemente hay columna CODI
            if codi_pattern_count >= 3:
                print(f"🎯 Patrones CODI detectados: {codi_pattern_count} valores '000'/'0000'/'00'")
                
                # Verificar que estén en la misma columna X (agrupados)
                codi_x_positions = []
                for texto, x_pos, y_pos in data_elements:
                    if texto in ["000", "0000", "00"] and y_pos > 150:
                        codi_x_positions.append(x_pos)
                
                if codi_x_positions:
                    # Si la mayoría están en posiciones X similares, es una columna
                    x_groups = {}
                    for x in codi_x_positions:
                        key = round(x / 20) * 20  # Agrupar por cada 20px
                        x_groups[key] = x_groups.get(key, 0) + 1
                    
                    max_group_count = max(x_groups.values())
                    if max_group_count >= 2:  # Al menos 2 en la misma columna
                        print(f"🎯 Columna CODI confirmada: {max_group_count} valores agrupados")
                        doc.close()
                        return True
        
        doc.close()
        print("❌ No se detectó columna CODI")
        return False
        
    except Exception as e:
        print(f"❌ Error detectando CODI: {str(e)}")
        return False  # En caso de error, asumir que no hay CODI

@app.post("/extract-table")
async def extract_table(file: UploadFile = File(...)):
    """
    Extrae datos de tabla de un archivo PDF usando el extractor apropiado según la presencia de columna CODI
    """
    try:
        # Validar tipo de archivo
        if not file.filename.lower().endswith('.pdf'):
            raise HTTPException(
                status_code=400, 
                detail="Solo se permiten archivos PDF"
            )
        
        # Leer el archivo PDF
        pdf_bytes = await file.read()
        
        if not pdf_bytes:
            raise HTTPException(
                status_code=400,
                detail="El archivo está vacío"
            )
        
        print(f"🚀 Iniciando análisis de: {file.filename}")
        
        # 🎯 DETECTAR COLUMNA CODI
        tiene_codi = detect_codi_column(pdf_bytes)
        
        if tiene_codi:
            print("📊 Columna CODI detectada → Usando extractor_filtro_2.py")
            extraction_method = "Area Mapping + CODI Filter (extractor_filtro_2.py)"
            resultados = extraer_con_codi(pdf_bytes)
        else:
            print("📊 Sin columna CODI → Usando extractor.py")
            extraction_method = "Area Mapping Standard (extractor.py)"
            resultados = extraer_sin_codi(pdf_bytes)
        
        # Estadísticas de extracción
        stats = {
            "total_registros": len(resultados),
            "registros_con_inventario": len([r for r in resultados if r.get("NO. INVENTARIO")]),
            "registros_con_prog": len([r for r in resultados if r.get("PROG")]),
            "columnas_extraidas": list(set().union(*[r.keys() for r in resultados])) if resultados else [],
            "tiene_columna_codi": tiene_codi,
            "archivo_extractor": "extractor_filtro_2.py" if tiene_codi else "extractor.py"
        }
        
        return {
            "success": True,
            "filename": file.filename,
            "extraction_method": extraction_method,
            "statistics": stats,
            "data": resultados
        }
        
    except Exception as e:
        print(f"❌ Error procesando {file.filename}: {str(e)}")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": str(e),
                "message": "Error al procesar el archivo PDF",
                "filename": file.filename if file else "unknown"
            }
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)