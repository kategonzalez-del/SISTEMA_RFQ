from RFQ.models import Material

# Variables globales vacías al inicio (No consumen RAM)
model = None
material_cache = []
material_vectors = None

def get_model():
    """Carga el modelo de IA solo cuando se solicita por primera vez"""
    global model
    if model is None:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer('all-MiniLM-L6-v2')
    return model

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
        # Llamamos al modelo perezoso
        current_model = get_model()
        material_vectors = current_model.encode(texts)

def match_material(candidate_text):
    global material_vectors

    if material_vectors is None:
        build_material_index()

    # Importaciones diferidas (Lazy Imports) para no saturar Django al inicio
    import numpy as np
    from sklearn.metrics.pairwise import cosine_similarity

    current_model = get_model()
    candidate_vector = current_model.encode([candidate_text.upper()])

    similarities = cosine_similarity(
        candidate_vector,
        material_vectors
    )[0]

    best_idx = np.argmax(similarities)
    best_score = similarities[best_idx]
    best_material = material_cache[best_idx]

    confidence = round(float(best_score) * 100, 2)

    if confidence < 45:
        return None

    return {
        "material": best_material,
        "confidence": confidence
    }