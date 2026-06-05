def analyze_stl(file_path):
    # Movemos trimesh aquí adentro para que se cargue bajo demanda
    import trimesh

    mesh = trimesh.load(file_path)
    volume_cm3 = mesh.volume / 1000

    return {
        "volume_cm3": round(volume_cm3, 2),
        "bodies": 1
    }