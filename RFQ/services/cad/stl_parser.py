import trimesh

def analyze_stl(file_path):

    mesh = trimesh.load(file_path)

    volume_cm3 = mesh.volume / 1000

    return {
        "volume_cm3": round(volume_cm3, 2),
        "bodies": 1
    }