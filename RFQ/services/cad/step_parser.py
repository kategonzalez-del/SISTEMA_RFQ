def analyze_step(file_path):
    # Movemos CadQuery aquí adentro para que no sature la RAM al arrancar Django
    from cadquery import importers

    model = importers.importStep(file_path)
    solid = model.val()
    
    # Asegurar conversión correcta de unidades según tu escala CAD
    volume_cm3 = solid.Volume() / 1000

    return {
        "volume_cm3": round(volume_cm3, 2)
    }