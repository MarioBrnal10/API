from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import io
from extractor_filtro_2 import extraer_datos_por_celdas

app = FastAPI(
    title="PDF Table Extractor API",
    description="API para extraer datos de tablas PDF usando mapeo por áreas",
    version="2.0.0"
)

@app.get("/")
async def root():
    return {
        "message": "PDF Table Extractor API con mapeo por áreas está funcionando",
        "version": "2.0.0",
        "features": [
            "Mapeo por áreas geográficas",
            "Detección automática de headers",
            "Configuración ajustable de áreas",
            "Fallback múltiple (Camelot + método avanzado)"
        ]
    }

@app.post("/extract-table")
async def extract_table(file: UploadFile = File(...)):
    """
    Extrae datos de tabla de un archivo PDF usando mapeo por áreas
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
        
        # Extraer datos usando mapeo por áreas
        print(f"🚀 Iniciando extracción de: {file.filename}")
        resultados = extraer_datos_por_celdas(pdf_bytes)
        
        # Estadísticas de extracción
        stats = {
            "total_registros": len(resultados),
            "registros_con_inventario": len([r for r in resultados if r.get("NO. INVENTARIO")]),
            "registros_con_prog": len([r for r in resultados if r.get("PROG")]),
            "columnas_extraidas": list(set().union(*[r.keys() for r in resultados])) if resultados else []
        }
        
        return {
            "success": True,
            "filename": file.filename,
            "extraction_method": "Area Mapping + Camelot Fallback",
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

@app.get("/config/areas")
async def get_area_config():
    """
    Devuelve la configuración actual de áreas de mapeo
    """
    from extractor_filtro_2 import COLUMN_AREA_CONFIG
    
    return {
        "message": "Configuración de áreas de mapeo",
        "note": "Ajusta estos valores en COLUMN_AREA_CONFIG para modificar las áreas de captura",
        "areas": COLUMN_AREA_CONFIG
    }

@app.get("/health")
async def health_check():
    """
    Endpoint de verificación de salud del servicio
    """
    try:
        # Verificar importaciones
        from extractor_filtro_2 import AreaMappedExtractor
        import fitz
        import camelot
        
        return {
            "status": "healthy",
            "services": {
                "area_mapper": "✅ OK",
                "pymupdf": "✅ OK", 
                "camelot": "✅ OK"
            }
        }
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unhealthy",
                "error": str(e)
            }
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)