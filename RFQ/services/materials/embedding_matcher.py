from functools import lru_cache

from RFQ.models import Material

material_cache = []
material_vectors = None


@lru_cache(maxsize=1)
def get_model():

    from sentence_transformers import (
        SentenceTransformer
    )

    return SentenceTransformer(
        'all-MiniLM-L6-v2'
    )


def build_material_index():

    global material_cache
    global material_vectors

    materials = Material.objects.all()

    material_cache = []
    texts = []

    for material in materials:

        text = " ".join([
            material.family or '',
            material.commercial_name or '',
            material.color or '',
            material.material_code or ''
        ]).upper()

        material_cache.append(material)

        texts.append(text)

    if texts:

        current_model = get_model()

        material_vectors = current_model.encode(
                texts,
                show_progress_bar=False
            )

        print(
            f"Embeddings creados: {len(texts)}"
        )


def match_material(candidate_text):

    global material_vectors

    if material_vectors is None:

        build_material_index()

    import numpy as np

    from sklearn.metrics.pairwise import (
        cosine_similarity
    )

    current_model = get_model()

    candidate_vector = current_model.encode(
            [candidate_text.upper()],
            show_progress_bar=False
        )

    similarities = cosine_similarity(
        candidate_vector,
        material_vectors
    )[0]

    best_idx = np.argmax(similarities)

    best_score = similarities[best_idx]

    confidence = round(
            float(best_score) * 100,
            2
        )

    if confidence < 45:

        return None

    return {
        "material": material_cache[best_idx],
        "confidence": confidence
    }