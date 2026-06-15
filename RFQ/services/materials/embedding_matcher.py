import gc
import numpy as np
from sentence_transformers import SentenceTransformer
from sklearn.metrics.pairwise import cosine_similarity
from RFQ.models import Material

# =====================================================================
# OPTIMIZACIÓN GLOBAL DE MEMORIA PARA RAILWAY
# Reemplazamos la lógica pesada por estructuras primitivas ligeras.
# No guardamos instancias completas de Modelos de Django en caché.
# =====================================================================
_MODEL_INSTANCE = None
_MATERIAL_IDS = []       # Guardamos solo IDs (enteros), consume 99% menos RAM
_MATERIAL_VECTORS = None


def get_model():
    """Carga el transformador semántico una sola vez de forma estricta en CPU."""
    global _MODEL_INSTANCE
    if _MODEL_INSTANCE is None:
        # Forzar explícitamente el uso de CPU disminuye el footprint de memoria inicial
        _MODEL_INSTANCE = SentenceTransformer('all-MiniLM-L6-v2', device='cpu')
    return _MODEL_INSTANCE


def build_material_index():
    """Genera la matriz de vectores optimizando el uso de la base de datos."""
    global _MATERIAL_IDS
    global _MATERIAL_VECTORS

    # Traemos EXCLUSIVAMENTE los campos necesarios mediante un Query directo y rápido
    materials_data = Material.objects.values_list('id', 'family', 'commercial_name', 'color', 'material_code')

    if not materials_data:
        _MATERIAL_IDS = []
        _MATERIAL_VECTORS = None
        return

    local_ids = []
    texts = []

    for m_id, family, commercial_name, color, material_code in materials_data:
        # Mantener el mismo formato semántico e idéntico peso industrial que ya tenías
        text = f"FAMILIA: {family or ''} | NOMBRE: {commercial_name or ''} | COLOR: {color or ''} | CODIGO: {material_code or ''}".upper()
        local_ids.append(m_id)
        texts.append(text)

    try:
        current_model = get_model()
        # Generación de la matriz vectorial
        _MATERIAL_VECTORS = current_model.encode(
            texts,
            show_progress_bar=False,
            convert_to_numpy=True  # Forzar salida en NumPy directo para no acumular tensores pesados
        )
        _MATERIAL_IDS = local_ids
        print(f"Embeddings creados con éxito: {len(texts)}")
    except Exception as e:
        print(f"Error crítico al construir el índice de materiales: {e}")
        _MATERIAL_VECTORS = None
        _MATERIAL_IDS = []
    finally:
        # Forzar la limpieza de los textos crudos de la memoria inmediatamente
        del texts
        gc.collect()


def match_material(candidate_text):
    global _MATERIAL_VECTORS
    global _MATERIAL_IDS

    if not candidate_text or str(candidate_text).strip() == "":
        return None

    # Si la matriz global no está lista en este hilo/proceso, la construimos de forma limpia
    if _MATERIAL_VECTORS is None or len(_MATERIAL_IDS) == 0:
        build_material_index()

    if _MATERIAL_VECTORS is None or len(_MATERIAL_IDS) == 0:
        return None

    try:
        current_model = get_model()

        # Construcción exacta del string de búsqueda candidato
        search_query = f"FAMILIA: {candidate_text} | NOMBRE: {candidate_text}".upper()

        candidate_vector = current_model.encode(
            [search_query],
            show_progress_bar=False,
            convert_to_numpy=True
        )

        # Medición matemática de similitud de cosenos
        similarities = cosine_similarity(candidate_vector, _MATERIAL_VECTORS)[0]

        best_idx = np.argmax(similarities)
        best_score = similarities[best_idx]
        confidence = round(float(best_score) * 100, 2)

        # Liberación inmediata de variables temporales para mitigar SIGKILL en Railway
        del candidate_vector
        gc.collect()

        # Filtro de Confianza Estricto (70%)
        if confidence < 70:
            return None

        # Como solo guardamos los enteros IDs en RAM, hacemos el fetch directo a la base de datos
        # justo a tiempo únicamente para el material ganador.
        target_id = _MATERIAL_IDS[best_idx]
        matched_material_obj = Material.objects.get(id=target_id)

        return {
            "material": matched_material_obj,
            "confidence": confidence
        }

    except Exception as run_error:
        print(f"Error durante la ejecución del match de materiales: {run_error}")
        return None