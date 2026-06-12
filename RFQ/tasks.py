import os
import json
import re
import base64
from django.conf import settings
from celery import shared_task
from django.db.models import Q
from .models import DrawingAnalysis, DrawingDetectedMaterial, PartComponent, Material

# Importaciones diferidas dentro de la tarea para que no pesen al arrancar pip listDjango
from RFQ.services.parsers.pdf_parser import extract_text_from_pdf, extract_volume, extract_weight
from RFQ.services.ai.structured_extractor import extract_rfq_data
from RFQ.services.materials.embedding_matcher import match_material
from RFQ.services.cad.step_parser import analyze_step
from RFQ.services.cad.stl_parser import analyze_stl

@shared_task
def process_file_in_background(analysis_id, file_name, file_base64, ext, is_subcomponent_manual):
    try:
        analysis = DrawingAnalysis.objects.get(id=analysis_id)
        local_dir = os.path.join(settings.MEDIA_ROOT, 'tmp')
        os.makedirs(local_dir, exist_ok=True) # Si no existe, Django la crea automáticamente
        
        file_path = os.path.join(local_dir, file_name)
        
        # RECONSTRUIMOS EL ARCHIVO ORIGINAL A PARTIR DEL TEXTO ENVIADO
        with open(file_path, 'wb') as f:
            f.write(base64.b64decode(file_base64.encode('utf-8')))
        
        
        classification_string = f"{file_name}: 🧩 Subcomponente 2D" if is_subcomponent_manual else f"{file_name}: 🏢 Plano de Ensamble Maestro"

        # CASO 1: ARCHIVOS 3D
        if ext in ['.step', '.stp', '.stl']:
            if ext in ['.step', '.stp']:
                threed_data = analyze_step(file_path)
            else:
                threed_data = analyze_stl(file_path)
            
            volume_cm3 = threed_data.get('volume_cm3', 0)
            classification_string = f"{file_name}: 📐 Geometría 3D Indexada ({volume_cm3} cm³)"
            
            # Puedes actualizar estados en la base de datos para que el usuario lo vea
            return {'success': True, 'type': '3d', 'file_name': file_name, 'classification': classification_string}

        # CASO 2: PLANOS TÉCNICOS 2D (.PDF)
        elif ext == '.pdf':
            raw_text = extract_text_from_pdf(file_path)
            analysis.raw_text += f"\n--- ORIGEN: {file_name} ---\n" + raw_text
            
            clean_text_for_regex = re.sub(r'MASSE\s*:\s*WEIGHT', 'WEIGHT', raw_text, flags=re.IGNORECASE)
            local_volume = extract_volume(raw_text)
            local_weight = extract_weight(clean_text_for_regex) or extract_weight(raw_text)
            
            if local_volume and not analysis.estimated_volume:
                analysis.estimated_volume = local_volume
            if local_weight and not analysis.estimated_weight:
                analysis.estimated_weight = local_weight
            analysis.save()

            try:
                gemini_result = extract_rfq_data(raw_text)
                raw_json = gemini_result.get('raw_response', '{}') if 'raw_response' in gemini_result else json.dumps(gemini_result)
                clean_json = re.sub(r'^```json\s*|```$', '', raw_json, flags=re.MULTILINE).strip()
                raw_data = json.loads(clean_json)
            except Exception:
                raw_data = {}

            parts_list = []
            if not raw_data:
                file_pure_name = os.path.splitext(file_name)[0]
                parts_list = [{"part_number": file_pure_name, "description": "Componente extraído"}]
            elif isinstance(raw_data, list):
                parts_list = raw_data
            elif isinstance(raw_data, dict):
                parts_list = raw_data.get('parts', raw_data.get('part_numbers', [raw_data] if 'part_number' in raw_data else []))
                
            parts_found_payload = []
            for part in parts_list:
                part_num = part.get('part_number') or part.get('part_number_base')
                part_desc = part.get('name') or part.get('description') or ''
                clean_part_key = str(part_num).strip().upper() if part_num else ""
                
                material_data = part.get('materials', [{}])[0] if part.get('materials') else part.get('material', {})
                if not isinstance(material_data, dict):
                    material_data = {"name": str(material_data)}
                    
                commercial_name_ia = material_data.get('material_name') or material_data.get('name') or ''
                resin_family_ia = material_data.get('resin_family') or material_data.get('family') or ''
                color_ia = material_data.get('color') or ''
                supplier_ia = material_data.get('supplier') or 'No especificado'
                
                alt_material_data = part.get('alternative_material_suggestions', [{}])[0] if part.get('alternative_material_suggestions') else {}
                alt_resin_name = alt_material_data.get('name', '')
                if not alt_resin_name or alt_resin_name.upper() == 'NULL':
                    alt_resin_name = 'Ninguna registrada'

                matched_material_db = None
                is_matched_via_alternate = False
                
                if isinstance(commercial_name_ia, str) and commercial_name_ia.strip():
                    match_result = match_material(commercial_name_ia)
                    if match_result and match_result["confidence"] >= 45:
                        matched_material_db = match_result["material"]
                
                if not matched_material_db and alt_resin_name and alt_resin_name != 'Ninguna registrada':
                    alt_match_result = match_material(alt_resin_name)
                    if alt_match_result and alt_match_result["confidence"] >= 45:
                        matched_material_db = alt_match_result["material"]
                        is_matched_via_alternate = True

                volume_val = part.get('volume_cm3') or part.get('volume')
                
                if not volume_val and clean_part_key:
                    threed_geom = DrawingDetectedMaterial.objects.filter(
                        Q(analysis=analysis) & 
                        Q(bom_reference__icontains="Geometría 3D Indexada") & 
                        Q(bom_reference__icontains=clean_part_key)
                    ).first()
                    if threed_geom:
                        volume_val = threed_geom.component_volumen

                if not volume_val:
                    volume_val = local_volume
                        
                weight_val = part.get('weight_grams') or part.get('weight') or local_weight
                data_source_flag = "Extraído de Plano PDF"

                if not weight_val and local_weight:
                    weight_val = local_weight
                    data_source_flag = "Extraído vía Regex PDF (Masse/Weight)"

                if volume_val and not weight_val:
                    density = matched_material_db.density if matched_material_db else 1.05
                    try:
                        weight_val = float(volume_val) * float(density)
                        data_source_flag = f"Peso Estimado ({volume_val} cm³ x {density} g/cm³)"
                    except:
                        weight_val = None

                secondary_components = part.get('secondary_embedded_components', [])
                secondary_text_list = []
                if secondary_components and weight_val:
                    for sub_item in secondary_components:
                        s_name = sub_item.get('component_name', '')
                        m_type = sub_item.get('material_type', '')
                        qty = sub_item.get('quantity', 1) or 1
                        added_weight = (2.5 * qty) if m_type == 'METAL' else (0.5 * qty)
                        weight_val = float(weight_val) + added_weight
                        secondary_text_list.append(f"{qty}x {s_name}")
                        data_source_flag = "Peso Compuesto (Resina + Insertos)"

                ref_secundarios = f" | Lleva: {', '.join(secondary_text_list)}" if secondary_text_list else ""

                db_component = DrawingDetectedMaterial.objects.create(
                    analysis=analysis,
                    part_number=part_num,
                    raw_material_text=commercial_name_ia if commercial_name_ia else part_desc,
                    detected_material=matched_material_db,
                    detected_family=resin_family_ia if resin_family_ia else "N/D",
                    detected_color=color_ia if color_ia else "N/D",
                    component_weight=round(float(weight_val), 2) if weight_val else None,
                    component_volumen=round(float(volume_val), 2) if volume_val else None,
                    bom_reference=f"{part_desc}{ref_secundarios} | [Alt: {alt_resin_name}] | [{data_source_flag}] | Origen: {file_name}"
                )

                if part_num:
                    PartComponent.objects.create(
                        parent=analysis,
                        child_part_number=part_num,
                        estimated_weight=weight_val if weight_val else 0,
                        quantity=part.get('quantity', 1)
                    )

                # CÁLCULO DUAL DE PESO EN LIBRAS (1 gramo = 0.00220462 libras)
                weight_lbs = round(float(weight_val) * 0.00220462, 4) if weight_val else None

                parts_found_payload.append({
                    'part_number': part_num or "Insumo / Adicional",
                    'description': part_desc,
                    'bom_reference': db_component.bom_reference,
                    'alternative_resin': alt_resin_name,
                    'data_source': data_source_flag,
                    'detected_material': matched_material_db.commercial_name if matched_material_db else None,
                    'material_code': matched_material_db.material_code if matched_material_db else None,
                    'raw_material_text': commercial_name_ia if commercial_name_ia else "No especificado",
                    'detected_family': db_component.detected_family,
                    'detected_color': db_component.detected_color,
                    'component_weight': db_component.component_weight,
                    'component_weight_lbs': weight_lbs, 
                    'component_volumen': db_component.component_volumen,
                    'color_completo': color_ia,
                    'is_matched_via_alternate': is_matched_via_alternate,
                    'secondary_elements': secondary_text_list,
                    'sugerencias': []
                })

            analysis.status = 'completed'
            analysis.save()
            return {'success': True, 'type': 'pdf', 'file_name': file_name}

    except Exception as e:
        if 'analysis' in locals():
            analysis.status = 'failed'
            analysis.save()
        return {'success': False, 'error': str(e)}