from functools import lru_cache
import numpy as np
from sklearn.metrics.pairwise import cosine_similarity
from RFQ.models import Material

material_cache = []
material_vectors = None


@lru_cache(maxsize=1)
def get_model():
    from sentence_transformers import SentenceTransformer
    return SentenceTransformer('all-MiniLM-L6-v2')


def build_material_index():
    global material_cache
    global material_vectors

    materials = Material.objects.all()

    material_cache = []
    texts = []

    for material in materials:
        # MEJORA CRUCIAL: Añadimos etiquetas semánticas claras para que el modelo
        # entienda qué es una familia, qué es el nombre comercial y qué es el código.
        text = f"FAMILIA: {material.family or ''} | NOMBRE: {material.commercial_name or ''} | COLOR: {material.color or ''} | CODIGO: {material.material_code or ''}".upper()

        material_cache.append(material)
        texts.append(text)

    if texts:
        current_model = get_model()
        material_vectors = current_model.encode(
            texts,
            show_progress_bar=False
        )
        print(f"Embeddings creados con éxito: {len(texts)}")


def match_material(candidate_text):
    global material_vectors

    if not candidate_text or str(candidate_text).strip() == "":
        return None

    if material_vectors is None or len(material_cache) == 0:
        build_material_index()

    # Si la base de datos está vacía, evitamos que truene sklearn
    if material_vectors is None:
        return None

    current_model = get_model()

    # Formateamos la búsqueda del candidato con el mismo peso semántico
    search_query = f"FAMILIA: {candidate_text} | NOMBRE: {candidate_text}".upper()

    candidate_vector = current_model.encode(
        [search_query],
        show_progress_bar=False
    )

    similarities = cosine_similarity(
        candidate_vector,
        material_vectors
    )[0]

    best_idx = np.argmax(similarities)
    best_score = similarities[best_idx]

    confidence = round(float(best_score) * 100, 2)

    # REGLA DE INGENIERÍA: Ajustamos un filtro estricto directamente en el buscador.
    # Un 45% permitía que entrara cualquier plástico con el mismo color. Subimos a 70% mínimo.
    if confidence < 70:
        return None

    return {
        "material": material_cache[best_idx],
        "confidence": confidence
    }