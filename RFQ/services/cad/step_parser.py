
def analyze_step(file_path):
    from cadquery import importers

    model = importers.importStep(file_path)

    solid = model.val()

    volume_cm3 = solid.Volume() / 1000

    return {
        "volume_cm3": round(volume_cm3, 2)
    }