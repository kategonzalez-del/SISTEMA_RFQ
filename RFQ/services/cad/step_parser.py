# step_parser.py
import multiprocessing as mp


def _step_worker(file_path, result_queue):
    try:
        from cadquery import importers
        model = importers.importStep(file_path)
        solid = model.val()
        volume_cm3 = solid.Volume() / 1000
        result_queue.put({"ok": True, "volume_cm3": round(volume_cm3, 2)})
    except Exception as e:
        result_queue.put({"ok": False, "error": str(e)})


def analyze_step(file_path, timeout=120):
    """
    Ejecuta el parseo de STEP en un proceso aislado. Si cadquery/OCP
    truena (segfault) o se cuelga procesando geometría compleja,
    solo muere ese proceso hijo, no el worker de Celery.
    """
    result_queue = mp.Queue()
    p = mp.Process(target=_step_worker, args=(file_path, result_queue))
    p.start()
    p.join(timeout)

    if p.is_alive():
        p.terminate()
        p.join()
        return {"ok": False, "volume_cm3": None,
                "error": f"Timeout de {timeout}s procesando STEP (geometría muy compleja o colgada)."}

    if not result_queue.empty():
        result = result_queue.get()
        return {"ok": result["ok"], "volume_cm3": result.get("volume_cm3"),
                "error": result.get("error")}

    # La cola quedó vacía y el proceso ya no está vivo -> probablemente segfault/OOM
    return {"ok": False, "volume_cm3": None,
            "error": "El proceso de análisis STEP terminó sin resultado (posible crash nativo o memoria insuficiente)."}