# stl_parser.py — mismo patrón por seguridad, aunque trimesh es mucho menos propenso a crashear
import multiprocessing as mp


def _stl_worker(file_path, result_queue):
    try:
        import trimesh
        mesh = trimesh.load(file_path)
        volume_cm3 = mesh.volume / 1000
        result_queue.put({"ok": True, "volume_cm3": round(volume_cm3, 2)})
    except Exception as e:
        result_queue.put({"ok": False, "error": str(e)})


def analyze_stl(file_path, timeout=60):
    result_queue = mp.Queue()
    p = mp.Process(target=_stl_worker, args=(file_path, result_queue))
    p.start()
    p.join(timeout)

    if p.is_alive():
        p.terminate()
        p.join()
        return {"ok": False, "volume_cm3": None,
                "error": f"Timeout de {timeout}s procesando STL."}

    if not result_queue.empty():
        result = result_queue.get()
        return {"ok": result["ok"], "volume_cm3": result.get("volume_cm3"),
                "error": result.get("error"), "bodies": 1}

    return {"ok": False, "volume_cm3": None,
            "error": "El proceso de análisis STL terminó sin resultado (posible crash nativo)."}